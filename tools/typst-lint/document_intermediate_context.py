"""Issue a bounded in-memory context from replayed, captured source sequences.

This is analysis evidence only. It never represents live file fingerprints,
compiler resources, publication or permission to apply candidate bytes.
Include and import sites must remain structurally unchanged; uncertain changes
are refused rather than inferred or read from the live filesystem.
"""
from dataclasses import dataclass, field, replace
from types import MappingProxyType
import json
import weakref

import conditional_footnote_binding as binding
import document_footnote_evidence as footnote
import document_resources
import document_sequence_preview as sequence
from document_gap_preview import _MappedEdits
from edit_plan import Edit, plan
import include_context as inc
import import_graph
from syntax_client import AdapterError, parse
from typst_lint import InputError, SafetyError, digest, load_rules

_ISSUED = {}
MAX_SEQUENCE_BYTES = 16 * 1024 * 1024
MAX_SEQUENCE_CHUNKS = 1_000_000
MAX_SEQUENCE_DEPTH = 128


def _preflight_records(value):
    """Bound the JSON representation before the encoder materializes chunks."""
    total = nodes = 0
    active = set()

    def spend(amount):
        nonlocal total
        total += amount
        if total > MAX_SEQUENCE_BYTES:
            raise InputError('Intermediate sequence record budget exceeded')

    def visit(item, depth):
        nonlocal nodes
        nodes += 1
        if nodes > MAX_SEQUENCE_CHUNKS or depth > MAX_SEQUENCE_DEPTH:
            raise InputError('Intermediate sequence record budget exceeded')
        if type(item) is str:
            # Every character needs at least one byte in JSON. Check this
            # before slicing or encoding to reject giant individual strings.
            if len(item) > MAX_SEQUENCE_BYTES - total - 2:
                raise InputError('Intermediate sequence record budget exceeded')
            spend(2)  # quotes
            for offset in range(0, len(item), 4096):
                part = item[offset:offset + 4096]
                try:
                    size = len(part.encode('utf-8'))
                except UnicodeError as error:
                    raise InputError('Invalid intermediate sequence string') from error
                # JSON escapes quotation marks, backslashes, and C0 controls.
                for character in part:
                    code = ord(character)
                    if character in ('"', '\\', '\b', '\f', '\n', '\r', '\t'):
                        size += 1
                    elif code < 32:
                        size += 5
                spend(size)
            return
        if item is None:
            spend(4); return
        if type(item) is bool:
            spend(4 if item else 5); return
        if type(item) is int:
            if item.bit_length() > 4 * max(0, MAX_SEQUENCE_BYTES - total):
                raise InputError('Intermediate sequence record budget exceeded')
            try:
                length = len(str(item))
            except ValueError as error:
                raise InputError('Invalid intermediate sequence integer') from error
            spend(length)
            return
        if type(item) not in (list, dict):
            raise InputError('Invalid intermediate sequence record types')
        identity = id(item)
        if identity in active:
            raise InputError('Intermediate sequence records contain a cycle')
        count = len(item)
        # Default JSON separators are comma-space and colon-space.
        spend(2 + 2 * max(0, count - 1) + (2 * count if type(item) is dict else 0))
        active.add(identity)
        try:
            if type(item) is list:
                for child in item:
                    visit(child, depth + 1)
            else:
                for key, child in item.items():
                    if type(key) is not str:
                        raise InputError('Intermediate sequence object keys must be strings')
                    visit(key, depth + 1)
                    visit(child, depth + 1)
        finally:
            active.remove(identity)

    visit(value, 0)


def _copy_records(value):
    if value is None:
        return None
    if type(value) is not list:
        raise InputError('Intermediate sequence records must be a list')
    try:
        _preflight_records(value)
        pieces, size = [], 0
        for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(value):
            if len(pieces) >= MAX_SEQUENCE_CHUNKS or len(chunk) > MAX_SEQUENCE_BYTES - size:
                raise InputError('Intermediate sequence record budget exceeded')
            raw = chunk.encode('utf-8')
            size += len(raw)
            if size > MAX_SEQUENCE_BYTES:
                raise InputError('Intermediate sequence record budget exceeded')
            pieces.append(raw)
        result = json.loads(b''.join(pieces))
    except InputError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise InputError('Invalid intermediate sequence records') from error
    if not sequence._same_data(result, value):
        raise InputError('Intermediate sequence record types are invalid')
    return result


@dataclass(frozen=True, slots=True, weakref_slot=True)
class IntermediateContext:
    context: inc.IncludeContext
    graph: import_graph.ImportGraph
    sources: object = field(repr=False)
    _proof: object = field(repr=False)
    runtime_binding_proved: bool = field(default=False, init=False)
    compiler_permission: bool = field(default=False, init=False)
    edit_permission: bool = field(default=False, init=False)


def _imports(tree):
    return tuple(tree.source[n.start:n.end] for n in tree.nodes if n.kind == 'ModuleImport')


def _mappers(original, candidate, record):
    """Replay each supplied stage again and retain its exact offset mapping."""
    if record is None:
        if original != candidate:
            raise SafetyError('Intermediate source has no replayed sequence')
        return ()
    current, mappers = original, []
    for step in record['steps']:
        if digest(current) != step['baseline_sha256']:
            raise SafetyError('Intermediate sequence baseline changed')
        edits = tuple(Edit(row['start'], row['end'], row['replacement'].encode('utf-8'))
                      for row in step['edits'])
        proposal = plan(current, edits)
        if proposal.deferred or len(proposal.edits) != len(edits):
            raise SafetyError('Intermediate sequence edits conflict')
        mappers.append(_MappedEdits(proposal.edits))
        current = proposal.candidate()
        if digest(current) != step['candidate_sha256']:
            raise SafetyError('Intermediate sequence step changed')
    if current != candidate or digest(current) != record['candidate_sha256']:
        raise SafetyError('Intermediate source differs from the replayed sequence')
    return tuple(mappers)


_OWNERS = frozenset(('ContentBlock', 'CodeBlock', 'LetBinding', 'FuncCall', 'Closure',
                     'Conditional', 'ForLoop', 'WhileLoop', 'Contextual', 'ShowRule'))


def _check_sites(old, new, kind, mappers):
    old_nodes = [i for i, n in enumerate(old.nodes) if n.kind == kind]
    new_nodes = [i for i, n in enumerate(new.nodes) if n.kind == kind]
    if len(old_nodes) != len(new_nodes):
        raise SafetyError(f'Intermediate changes the {kind} site inventory')
    old_parents = {child: index for index, node in enumerate(old.nodes) for child in node.children}
    new_parents = {child: index for index, node in enumerate(new.nodes) for child in node.children}
    def owners(tree, parents, index):
        result = []
        while index in parents:
            index = parents[index]
            if tree.nodes[index].kind in _OWNERS:
                node = tree.nodes[index]
                result.append((node.kind, node.start, node.end))
        return tuple(reversed(result))
    def mapped(pos):
        for mapper in mappers:
            pos = mapper.mapped(pos)
        return pos
    for a, b in zip(old_nodes, new_nodes):
        left, right = old.nodes[a], new.nodes[b]
        if (old.source[left.start:left.end] != new.source[right.start:right.end]
                or (mapped(left.start), mapped(left.end)) != (right.start, right.end)):
            raise SafetyError(f'Intermediate changes a {kind} site or location')
        before, after = owners(old, old_parents, a), owners(new, new_parents, b)
        if tuple((kind, mapped(start), mapped(end)) for kind, start, end in before) != after:
            raise SafetyError(f'Intermediate changes {kind} ownership')


def _derived_include(original, candidates, records=None):
    max_files, max_nodes = inc._capture_bounds(original.budget)
    if len(original.sources) > max_files:
        raise SafetyError('Intermediate include file limit exceeded')
    sources, total_bytes, total_nodes = {}, 0, 0
    for item in original.sources:
        raw = candidates[item.path]
        if type(raw) is not bytes or len(raw) > inc.MAX_SOURCE_BYTES:
            raise SafetyError('Intermediate include source exceeds its bound')
        total_bytes += len(raw)
        try:
            tree = parse(raw)
        except AdapterError as error:
            raise SafetyError('Intermediate include syntax is unavailable') from error
        if tree.nodes[0].errors or tree.mode != 'markup':
            raise SafetyError('Intermediate include source is malformed')
        total_nodes += len(tree.nodes)
        if total_bytes > inc.MAX_CONTEXT_BYTES or total_nodes > max_nodes or len(tree.nodes) > 100_000:
            raise SafetyError('Intermediate include context exceeds its bounds')
        record = (records or {}).get(item.path)
        mappers = _mappers(item.source, raw, record)
        _check_sites(item.tree, tree, 'ModuleInclude', mappers)
        _check_sites(item.tree, tree, 'ModuleImport', mappers)
        identity = inc.IncludeSourceIdentity(item.path, digest(raw), inc._tree_sha256(tree))
        # A changed in-memory source has no filesystem fingerprint. The zero
        # sentinel is not a live-file or protected-transaction assertion.
        fingerprint = item.fingerprint if raw == item.source else (0, 0, 0, 0, 0, 0, len(raw), 0, 0)
        sources[item.path] = inc.IncludeSource(identity, raw, tree, fingerprint)

    edges = []
    for old in original.sources:
        current = sources[old.path]
        tree = current.tree
        parents = {child: index for index, node in enumerate(tree.nodes) for child in node.children}
        old_edges = [edge for edge in original.edges if edge.node.source.path == old.path]
        new_nodes = [index for index, node in enumerate(tree.nodes) if node.kind == 'ModuleInclude']
        if len(old_edges) != len(new_nodes):
            raise SafetyError('Intermediate changes the include site inventory')
        def identity(index):
            node = tree.nodes[index]
            return inc.IncludeNodeIdentity(current.identity, index, node.kind, node.start, node.end)
        for previous, index in zip(old_edges, new_nodes):
            node = tree.nodes[index]
            ancestry = []
            parent = parents.get(index)
            while parent is not None:
                ancestry.append(identity(parent)); parent = parents.get(parent)
            ancestry = tuple(reversed(ancestry))
            if tuple(x.kind for x in ancestry) != tuple(x.kind for x in previous.ancestry):
                raise SafetyError('Intermediate changes include containment')
            parts = tuple(x for x in node.children if tree.nodes[x].kind not in inc._TRIVIA)
            operand = parts[1] if len(parts) == 2 and tree.nodes[parts[0]].kind == 'Include' else None
            literal = identity(operand) if operand is not None and tree.nodes[operand].kind == 'Str' else None
            raw = tree.source[tree.nodes[operand].start:tree.nodes[operand].end] if operand is not None else b''
            if raw != previous.raw_request or (literal is None) != (previous.literal is None):
                raise SafetyError('Intermediate changes an include request')
            target = sources[previous.target.path].identity if previous.target is not None else None
            edges.append(inc.IncludeEdge(identity(index), literal, previous.request, raw, target, ancestry,
                tuple(x for x in ancestry if x.kind == 'ContentBlock'),
                tuple(x for x in ancestry if x.kind in inc._CALLS), previous.unresolved_codes))
    edges = tuple(sorted(edges, key=lambda x: (x.node.source.path, x.node.node_id)))
    entrypoints = tuple(sources[x.path].identity for x in original.entrypoints)
    occurrences, cycles = inc._occurrences(entrypoints, edges)
    unresolved = tuple(inc.IncludeUnresolved(edge.node, edge.unresolved_codes)
                       for edge in edges if edge.unresolved_codes)
    return inc.IncludeContext(original.project_root, entrypoints,
        tuple(sources[k] for k in sorted(sources)), edges, occurrences, (*unresolved, *cycles), original.budget)


def _derived_import(original_graph, old_context, new_context):
    old_sources = {x.path: x for x in old_context.sources}
    new_sources = {x.path: x for x in new_context.sources}
    graph_sources = []
    for item in original_graph.sources:
        if item.path not in new_sources:
            graph_sources.append(item)
            continue
        old, new = old_sources[item.path], new_sources[item.path]
        if item.source != old.source or _imports(old.tree) != _imports(new.tree):
            raise SafetyError('Intermediate changes an imported source or import statement')
        graph_sources.append(replace(item, source=new.source))
    graph = replace(original_graph, sources=tuple(graph_sources))
    binding._graph_signature(graph)
    return graph


def _assert_backing(value, context):
    issued = _ISSUED.get(id(value))
    if (type(value) is not IntermediateContext or issued is None or issued[0]() is not value
            or value._proof is not issued[1] or value.context is not issued[2]
            or context is not value.context
            or value.graph is not issued[3] or value.sources is not issued[4]
            or value.runtime_binding_proved is not False or value.compiler_permission is not False
            or value.edit_permission is not False):
        raise SafetyError('Intermediate context is unissued or changed')
    footnote._valid(value._proof)
    if binding._graph_signature(value.graph) != issued[5]:
        raise SafetyError('Intermediate import graph changed')
    if tuple((x.path, digest(x.source)) for x in value.context.sources) != issued[6]:
        raise SafetyError('Intermediate source bytes changed')
    if tuple((key, digest(raw)) for key, raw in value.sources.items()) != issued[7]:
        raise SafetyError('Intermediate resource bytes changed')
    # As with other read-only evidence this is not a concurrent-write lock.
    footnote._valid(value._proof)
    if binding._graph_signature(value.graph) != issued[5]:
        raise SafetyError('Intermediate import graph changed during validation')
    return value


def assert_intermediate(value):
    if type(value) is not IntermediateContext:
        raise SafetyError('Intermediate context is unissued or changed')
    issued = _ISSUED.get(id(value))
    guard = inc._DERIVED.get(id(value.context))
    if (issued is None or issued[0]() is not value or guard is None
            or guard[0]() is not value.context or guard[1]() is not value):
        raise SafetyError('Intermediate context is unissued or changed')
    # The include guard checks both parser facts and this owner's backing.
    inc.assert_include_context(value.context)
    return value


def derive(proof, records, *, profile='standard', partial_unsupported=False, withheld_records=None,
           partial_unproven_binding=False, explicit_withheld_sources=(), conditional_continuations=False, conditional_capitalization=False,
           conditional_adjacent_punctuation=False, sequence_facade_evidence=True,
           conditional_deferred_footnotes=False):
    """Recompute/replay source sequences and issue a restricted in-memory view."""
    if type(proof) is not footnote.PreviewFootnoteEvidence:
        raise InputError('Issued preview resource evidence is required')
    if type(sequence_facade_evidence) is not bool:
        raise InputError('Sequence facade evidence selection must be a Boolean')
    resources = footnote._valid(proof)
    issued_session = binding._ISSUED.get(id(proof._session))
    if issued_session is None or len(issued_session) != 4:
        raise SafetyError('A baseline, non-derived binding is required')
    baseline = proof._session._context
    document_resources.stage_original(baseline, proof._snapshot)
    for package in proof._session._namespaces.packages:
        footnote._verify_package(proof._session, resources, package.evidence)
    catalog = load_rules()[0]['rules']
    stable_records = _copy_records(records)
    stable_withheld = _copy_records(withheld_records)
    candidates = sequence.replay_document(baseline, stable_records, catalog, profile=profile,
        footnote_evidence=proof if sequence_facade_evidence else None,
        partial_unsupported=partial_unsupported, withheld_records=stable_withheld,
        partial_unproven_binding=partial_unproven_binding, explicit_withheld_sources=explicit_withheld_sources,
        conditional_continuations=conditional_continuations, conditional_capitalization=conditional_capitalization,
        conditional_adjacent_punctuation=conditional_adjacent_punctuation,
        conditional_deferred_footnotes=conditional_deferred_footnotes)
    context = _derived_include(baseline, candidates,
        {item['path']: item for item in stable_records})
    graph = _derived_import(proof._session._graph, baseline, context)
    output = dict(resources)
    for path, raw in candidates.items():
        if ('project', path) not in output:
            raise SafetyError('Intermediate source is absent from the resource capture')
        output['project', path] = raw
    output = MappingProxyType(output)
    if (not sequence._same_data(stable_records, records)
            or not sequence._same_data(stable_withheld, withheld_records)):
        raise SafetyError('Intermediate sequence records changed during derivation')
    footnote._valid(proof)
    context = inc._issue(context)
    result = IntermediateContext(context, graph, output, proof)
    key = id(result)
    def release(reference):
        retained = _ISSUED.get(key)
        if retained is not None and retained[0] is reference:
            _ISSUED.pop(key, None)
    _ISSUED[key] = (weakref.ref(result, release), proof, context, graph, output,
                    binding._graph_signature(graph), tuple((x.path, digest(x.source)) for x in context.sources),
                    tuple((key, digest(raw)) for key, raw in output.items()))
    inc._bind_derived(context, result)
    return assert_intermediate(result)


def issue_binding(value):
    """Issue a source-only binding whose lifetime remains tied to this view."""
    assert_intermediate(value)
    session = object.__new__(binding.ConditionalFootnoteBinding)
    session._context, session._namespaces, session._graph = (
        value.context, value._proof._session._namespaces, value.graph)
    identity = id(session)
    def release(reference):
        retained = binding._ISSUED.get(identity)
        if retained is not None and retained[0] is reference:
            binding._ISSUED.pop(identity, None)
    binding._ISSUED[identity] = (weakref.ref(session, release), session._context, session._namespaces,
                                 binding._graph_signature(session._graph), value)
    if not binding._valid(session):
        raise SafetyError('Cannot issue intermediate binding')
    return session
