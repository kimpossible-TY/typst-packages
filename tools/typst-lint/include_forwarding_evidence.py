"""Conditional include ownership for reviewed book-part and direct context bodies.

The original include capture and its unresolved codes remain untouched. This
source-level evidence neither establishes runtime execution nor grants compile,
source-edit or publication authority.
"""
import os
import json
import unicodedata

import document_resources
import import_graph
import include_context
from static_resolver import MATH_BOOK, MATH_BOOK_SOURCE_SHA256
from typst_lint import InputError, digest, require

MATH_BOOK_MANIFEST_SHA256 = 'a4c36fcc5862d20f69a29f60550bca92d4305954335eafa3e0a1c5ba87c93be9'
MAX_EVIDENCE_ITEMS = 4096
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
MAX_WORK = 5_000_000
_ASSIGNMENTS = frozenset(('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq'))
_CONTEXT_CONDITIONS = (
    'The enclosing direct context expression executes and reaches the literal content expression.',
    'The containing source and its include occurrence execute in the captured document context.',
    'Earlier code and runtime show rules do not discard or replace the content.',
)
_BOOK_CONDITIONS = (
    'Runtime selects and initializes the exact captured math-book package and its dependencies.',
    'The direct book-part call resolves to that package helper and successfully evaluates its options.',
    'The enclosing document renders the helper result and does not discard or replace its content.',
)


def _text(tree, index):
    node = tree.nodes[index]
    return tree.source[node.start:node.end].decode('utf-8')


def _charge(budget, amount):
    budget[0] -= amount
    require(budget[0] >= 0, 'Include forwarding work budget exceeded')


def _book_name_flags(tree, budget):
    """Compute subtree name presence once, including nested mutation targets."""
    nodes = tree.nodes
    _charge(budget, len(nodes) + len(tree.source))
    order, pending = [], [0]
    while pending:
        current = pending.pop()
        _charge(budget, 1 + len(nodes[current].children))
        order.append(current)
        pending.extend(nodes[current].children)
    flags = [False] * len(nodes)
    for i in reversed(order):
        node = nodes[i]
        flags[i] = ((node.kind in ('Ident', 'MathIdent')
                     and tree.source[node.start:node.end] == b'book-part')
                    or any(flags[child] for child in node.children))
    return flags


def _may_rebind_book_part(tree, index, flags, budget):
    """Reject nested mutations/imports that can change the tracked name."""
    nodes, pending = tree.nodes, [index]
    while pending:
        current = pending.pop()
        node = nodes[current]
        _charge(budget, 1 + len(node.children))
        parts = import_graph.significant(tree, current)
        if node.kind == 'ModuleImport':
            shape = import_graph.import_shape(tree, current)
            if shape is None or shape.items is None or any(local == 'book-part' for _, local in shape.items):
                return True
        elif node.kind == 'LetBinding' and len(parts) >= 2:
            target = parts[1]
            if nodes[target].kind == 'Closure':
                closure = import_graph.significant(tree, target)
                target = closure[0] if closure else target
            if flags[target]:
                return True
        elif node.kind in ('Binary', 'DestructAssignment') and parts:
            if ((node.kind == 'DestructAssignment' or any(nodes[p].kind in _ASSIGNMENTS for p in parts))
                    and flags[parts[0]]):
                return True
        pending.extend(node.children)
    return False


def _book_calls(tree, *, budget=None):
    """Prove a narrow root named-import route and exact literal call shapes."""
    budget = [MAX_WORK] if budget is None else budget
    nodes, bound, calls = tree.nodes, False, {}
    flags = _book_name_flags(tree, budget)
    for index in nodes[0].children:
        node = nodes[index]
        parts = import_graph.significant(tree, index)
        if node.kind == 'ModuleImport':
            shape = import_graph.import_shape(tree, index)
            if shape is None or shape.items is None:
                bound = False
            elif any(local == 'book-part' for _, local in shape.items):
                bound = shape.request == MATH_BOOK and ('book-part', 'book-part') in shape.items
            continue
        if node.kind == 'LetBinding' and len(parts) >= 2:
            target = parts[1]
            if nodes[target].kind == 'Closure':
                closure = import_graph.significant(tree, target)
                target = closure[0] if closure else target
            if bound and (flags[target] or _may_rebind_book_part(tree, index, flags, budget)):
                bound = False
            continue
        if node.kind in ('Binary', 'DestructAssignment') and parts:
            if ((node.kind == 'DestructAssignment' or any(nodes[p].kind in _ASSIGNMENTS for p in parts))
                    and flags[parts[0]]):
                bound = False
            continue
        if node.kind in ('CodeBlock', 'Conditional', 'ForLoop', 'WhileLoop', 'Contextual'):
            bound = False
            continue
        if bound and _may_rebind_book_part(tree, index, flags, budget):
            bound = False
            continue
        if (not bound or node.kind != 'FuncCall' or len(parts) != 2
                or nodes[parts[0]].kind != 'Ident' or _text(tree, parts[0]) != 'book-part'
                or nodes[parts[1]].kind != 'Args'):
            continue
        bodies, seen, valid = [], set(), True
        for argument in import_graph.significant(tree, parts[1]):
            arg = nodes[argument]
            if arg.kind in ('LeftParen', 'RightParen', 'Comma'):
                continue
            if arg.kind == 'ContentBlock':
                bodies.append(argument)
                continue
            fields = import_graph.significant(tree, argument) if arg.kind == 'Named' else ()
            if len(fields) != 3 or nodes[fields[0]].kind != 'Ident':
                valid = False
                break
            name = _text(tree, fields[0])
            if name in seen or name not in ('prefix', 'single-chapter', 'center-sections', 'reset-heading'):
                valid = False
                break
            seen.add(name)
            value = nodes[fields[2]]
            if (name == 'prefix' and value.kind not in ('Str', 'None')) or (
                    name != 'prefix' and value.kind != 'Bool'):
                valid = False
                break
        if valid and len(bodies) == 1:
            calls[index] = bodies[0]
    return calls


def _matches(edge, source, book_calls):
    """Return a bounded syntactic owner, not an assertion of execution."""
    ancestry = edge.ancestry
    kinds = tuple(node.kind for node in ancestry)
    if kinds == ('Markup', 'FuncCall', 'Args', 'ContentBlock', 'Markup'):
        call = ancestry[1].node_id
        if book_calls.get(call) == ancestry[3].node_id and edge.unresolved_codes == ('unknown-containment',):
            return 'reviewed-book-part-literal-body', _BOOK_CONDITIONS, call
    if kinds == ('Markup', 'Contextual', 'CodeBlock', 'Code', 'ContentBlock', 'Markup'):
        tree = source.tree
        root, context, block, code, body, _ = (entry.node_id for entry in ancestry)
        if (root == 0 and context in tree.nodes[0].children and block in tree.nodes[context].children
                and code in tree.nodes[block].children and body in tree.nodes[code].children
                and edge.unresolved_codes == ('deferred-containment',)):
            return 'direct-context-literal-content', _CONTEXT_CONDITIONS, context
    return None


def _match_work(edge, source):
    """Bound the context ancestry membership checks before scanning them."""
    if tuple(node.kind for node in edge.ancestry) != (
            'Markup', 'Contextual', 'CodeBlock', 'Code', 'ContentBlock', 'Markup'):
        return 1
    nodes = source.tree.nodes
    return 1 + sum(len(nodes[node.node_id].children) for node in edge.ancestry[:4])


def _unavailable(context, reason):
    return dict(status='UNAVAILABLE', reason=reason, sites=[], recognized_site_count=0,
                conditionally_explained_occurrences=0, unresolved_sites_before=len(context.unresolved),
                unresolved_occurrences_before=sum(bool(item.unresolved_codes) for item in context.occurrences),
                original_unresolved_retained=True, resource_capture_bound=False,
                runtime_execution_proved=False, runtime_package_selection_proved=False,
                live_package_freshness_proved=False, coverage_complete=False,
                edit_permission=False, compiler_permission=False, publication_authority=False)


def analyze(context, package_path, *, snapshot=None, max_work=MAX_WORK):
    """Bind conditional include ownership to the exact issued compiler resource snapshot."""
    include_context.assert_include_context(context)
    require(type(max_work) is int and 0 < max_work <= MAX_WORK, 'Invalid include forwarding work budget')
    if snapshot is None:
        return _unavailable(context, 'Issued compiler resource capture is unavailable')
    document_resources.assert_capture(snapshot)
    def unavailable(reason):
        include_context.assert_include_context(context)
        document_resources.assert_capture(snapshot)
        return _unavailable(context, reason)
    roots = dict(snapshot.roots)
    try:
        selected = os.fspath(package_path)
    except TypeError as error:
        raise InputError('Expected an explicit package root') from error
    if type(selected) is not str:
        raise InputError('Expected an explicit package root')
    if (roots.get('project') != context.project_root or 'packages' not in roots or 'cache' not in roots
            or selected not in (roots['packages'], roots['cache']) or roots['packages'] == roots['cache']):
        return unavailable('Resource roots do not match the selected document and package roots')
    root_id = 'packages' if selected == roots['packages'] else 'cache'
    resources = {(item.request.root, item.request.path): item for item in snapshot.files}
    for item in context.sources:
        resource = resources.get(('project', item.path))
        if resource is None or resource.request.role != 'source' or resource.data != item.source:
            return unavailable('A captured include source is missing or differs from the resource snapshot')
    prefix = 'local/math-book/0.2.0'
    if root_id == 'cache':
        canonical = unicodedata.normalize('NFC', prefix).casefold()
        def interferes(key):
            if key[0] != 'packages':
                return False
            path = unicodedata.normalize('NFC', key[1]).casefold()
            return path == canonical or path.startswith(canonical + '/') or canonical.startswith(path + '/')
        if any(interferes(key) for key in resources):
            return unavailable('A higher-priority staged package may shadow the selected cache package')
    manifest = resources.get((root_id, prefix + '/typst.toml'))
    source = resources.get((root_id, prefix + '/lib.typ'))
    if (manifest is None or source is None or manifest.request.role != 'package' or source.request.role != 'package'
            or digest(manifest.data) != MATH_BOOK_MANIFEST_SHA256
            or digest(source.data) != MATH_BOOK_SOURCE_SHA256):
        return unavailable('The exact reviewed math-book package is missing or differs in the capture')
    evidence = dict(import_path=MATH_BOOK, root_id=root_id,
                    manifest_identity=manifest.evidence.path, manifest_sha256=manifest.sha256,
                    source_identity=source.evidence.path, source_sha256=source.sha256)
    budget = [max_work]
    calls = {item.path: _book_calls(item.tree, budget=budget) for item in context.sources}
    rows, identified, size = [], set(), 0
    for edge in context.edges:
        if edge.target is None or edge.literal is None:
            _charge(budget, 1)
            continue
        item = context.source_for(edge.node.source.path)
        _charge(budget, _match_work(edge, item))
        match = _matches(edge, item, calls[item.path])
        if match is None:
            continue
        kind, conditions, owner = match
        _charge(budget, edge.node.start)
        row = dict(path=item.path, source_sha256=item.identity.source_sha256,
            line=item.source[:edge.node.start].count(b'\n') + 1,
            start=edge.node.start, end=edge.node.end, target=edge.target.path,
            target_sha256=edge.target.source_sha256, owner_start=item.tree.nodes[owner].start,
            owner_end=item.tree.nodes[owner].end, evidence_kind=kind,
            retained_unresolved_codes=list(edge.unresolved_codes), conditions=conditions)
        encoded = json.dumps(row, ensure_ascii=False, sort_keys=True, indent=2)
        size += len(encoded.encode('utf-8')) + 4 * encoded.count('\n')
        require(size <= MAX_EVIDENCE_BYTES, 'Include forwarding report byte budget exceeded')
        rows.append(row)
        identified.add((edge.node.source.path, edge.node.node_id))
        require(len(rows) <= MAX_EVIDENCE_ITEMS, 'Include forwarding evidence limit exceeded')
    by_node = {(edge.node.source.path, edge.node.node_id): edge for edge in context.edges}
    explained = 0
    for occurrence in context.occurrences:
        _charge(budget, 1 + len(occurrence.edge_chain))
        if not occurrence.unresolved_codes or 'include-cycle' in occurrence.unresolved_codes:
            continue
        unresolved = [node for node in occurrence.edge_chain
                      if by_node[(node.source.path, node.node_id)].unresolved_codes]
        if unresolved and all((node.source.path, node.node_id) in identified for node in unresolved):
            explained += 1
    include_context.assert_include_context(context)
    document_resources.assert_capture(snapshot)
    return dict(status='CONDITIONAL', package_evidence=evidence, sites=rows,
                recognized_site_count=len(rows), conditionally_explained_occurrences=explained,
                unresolved_sites_before=len(context.unresolved),
                unresolved_occurrences_before=sum(bool(item.unresolved_codes) for item in context.occurrences),
                original_unresolved_retained=True, resource_capture_bound=True, runtime_execution_proved=False,
                runtime_package_selection_proved=False, live_package_freshness_proved=False, coverage_complete=False,
                edit_permission=False, compiler_permission=False, publication_authority=False)
