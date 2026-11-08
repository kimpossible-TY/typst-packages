"""Independent conditional preview of one nested prose-footnote gap pattern.

No helper binding, execution, PDF equivalence, source write or compiler
authority follows from this source-only proposal.
"""
from conditional_highlight import _module_alias
from document_adjacent_footnote_punctuation import _explicit_footnote_shadow
from document_gap_preview import _scopes
from document_sequence_preview import _FindingMapper, _keys, _same_findings
from edit_plan import Edit, plan
import footnote_rules
import import_graph
import paragraph_rules
import proof_rules
import prose_marker_rules
from static_resolver import resolve
from syntax_client import parse
from typst_lint import InputError, SafetyError, digest, load_rules

RULE = footnote_rules.GAP
CONDITIONS = (
    'The footnote and parbreak calls resolve to the intended built-in functions.',
    'The existing paragraph_tab resolves to the intended marker and the paragraph show rule applies.',
    'This included source executes as prose with the analyzed bytes and intended paragraph boundary.',
    'The inserted paragraph break can change rendering; equivalence and source-write authority are not proved.',
)


def _has_explicit_shadow(tree):
    names = {b'footnote', b'parbreak', b'paragraph_tab'}
    nodes, source = tree.nodes, tree.source
    def names_in(root):
        pending, found = [root], set()
        while pending:
            index = pending.pop()
            node = nodes[index]
            if node.kind == 'Ident':
                found.add(source[node.start:node.end])
            pending.extend(node.children)
        return found
    for index, node in enumerate(nodes):
        if node.kind not in ('LetBinding', 'Binary', 'DestructAssignment', 'Closure', 'ForLoop',
                             'ModuleImport', 'RenamedImportItem'):
            continue
        parts = import_graph.significant(tree, index)
        if node.kind == 'LetBinding' and len(parts) >= 2:
            target = parts[1]
            if nodes[target].kind == 'Closure':
                closure = import_graph.significant(tree, target)
                if closure and names_in(closure[0]) & names:
                    return True
            elif names_in(target) & names:
                return True
        elif node.kind == 'Closure':
            if any(nodes[child].kind == 'Params' and names_in(child) & names for child in parts):
                return True
        elif node.kind == 'Binary' and parts and any(nodes[x].kind in
                ('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq') for x in parts):
            if names_in(parts[0]) & names:
                return True
        elif node.kind == 'DestructAssignment' and parts and names_in(parts[0]) & names:
            return True
        elif node.kind == 'ForLoop':
            for child in parts[1:]:
                if nodes[child].kind == 'In':
                    break
                if names_in(child) & names:
                    return True
        elif node.kind == 'ModuleImport':
            module = _module_alias(tree, index)
            if module is not None and module[1].encode() in names:
                return True
            shape = import_graph.import_shape(tree, index)
            if shape is not None and shape.items is not None:
                for original, local in shape.items:
                    if local in ('footnote', 'parbreak') or (local == 'paragraph_tab' and original != local):
                        return True
            for position, part in enumerate(parts[:-1]):
                target = parts[position + 1]
                if nodes[part].kind == 'As' and nodes[target].kind == 'Ident' and names_in(target) & names:
                    return True
            # Combined imports have an ImportItems subtree even though the
            # ordinary import_shape cannot classify the module-plus-items form.
            for part in parts:
                if nodes[part].kind != 'ImportItems':
                    continue
                for item in import_graph.significant(tree, part):
                    if nodes[item].kind != 'ImportItemPath':
                        continue  # Renamed items are checked separately below.
                    components = import_graph.significant(tree, item)
                    identifiers = [child for child in components if nodes[child].kind == 'Ident']
                    if identifiers and names_in(identifiers[-1]) & {b'footnote', b'parbreak'}:
                        return True
                    if not identifiers and names_in(item) & names:
                        return True
        elif node.kind == 'RenamedImportItem':
            for position, part in enumerate(parts[:-1]):
                target = parts[position + 1]
                if nodes[part].kind == 'As' and nodes[target].kind == 'Ident' and names_in(target) & names:
                    return True
    return _explicit_footnote_shadow(tree)


def _select(tree, path, records, resolution, notes):
    source, nodes = tree.source, tree.nodes
    mapper = _FindingMapper(source)
    diagnosed = {mapper.offset(item): item for item in notes[RULE].diagnostics}
    parents = {child: (index, position) for index, node in enumerate(nodes)
               for position, child in enumerate(node.children)}
    edits, targets = [], []
    for index, call in enumerate(nodes):
        if call.kind != 'FuncCall' or not call.start or source[call.start - 1:call.start] != b'#':
            continue
        start = call.start - 1
        if start not in diagnosed or index not in parents:
            continue
        parent, position = parents[index]
        container = nodes[parent]
        if container.kind != 'Markup':
            continue
        ancestor = parents.get(parent)
        if any_kind_ancestor(nodes, parents, ancestor, {'Equation', 'Math'}):
            continue
        gap = footnote_rules._BLANK_AFTER_FOOTNOTE.match(source, call.end, container.end)
        if gap is None:
            continue
        next_start = gap.end()
        while source[next_start:next_start + 1] in (b' ', b'\t'):
            next_start += 1
        siblings = container.children
        next_pos = position + 1
        while next_pos < len(siblings) and nodes[siblings[next_pos]].end <= next_start:
            next_pos += 1
        if next_pos + 2 >= len(siblings):
            continue
        mark, ident = nodes[siblings[next_pos]], nodes[siblings[next_pos + 1]]
        if (mark.kind != 'Hash' or mark.start != next_start or ident.kind != 'Ident'
                or mark.end != ident.start or source[ident.start:ident.end] != b'paragraph_tab'):
            continue
        line_end = source.find(b'\n', ident.end, container.end)
        if line_end < 0:
            continue
        suffix = source[ident.end:line_end]
        if suffix.endswith(b'\r'):
            suffix = suffix[:-1]
        if suffix.strip(b' \t'):
            continue
        next_after = next_pos + 2
        if nodes[siblings[next_after]].kind != 'Space':
            continue
        next_after += 1
        if next_after >= len(siblings) or nodes[siblings[next_after]].kind != 'Text':
            continue
        newline = gap.group(1)
        edits.append(Edit(call.end + len(newline), call.end + len(newline), b'#parbreak()' + newline))
        targets.append(diagnosed[start])
    return tuple(edits), tuple(targets)


def any_kind_ancestor(nodes, parents, ancestor, forbidden):
    while ancestor is not None:
        index, _ = ancestor
        if nodes[index].kind in forbidden:
            return True
        ancestor = parents.get(index)
    return False


def _signature(tree, removed=()):
    nodes, source = tree.nodes, tree.source
    expected = set(removed)
    parents = {child: index for index, node in enumerate(nodes) for child in node.children}
    ignored = set()
    for index, node in enumerate(nodes):
        if node.kind != 'FuncCall' or (node.start - 1, node.end) not in expected:
            continue
        parts = import_graph.significant(tree, index)
        hash_index = index - 1
        if (len(parts) != 2 or nodes[parts[0]].kind != 'Ident' or nodes[parts[1]].kind != 'Args'
                or source[node.start:node.end] != b'parbreak()' or hash_index < 0
                or nodes[hash_index].kind != 'Hash' or nodes[hash_index].end != node.start):
            raise SafetyError('Inserted parbreak syntax is not exact')
        pending = [index]
        while pending:
            child = pending.pop()
            ignored.add(child)
            pending.extend(nodes[child].children)
        ignored.add(hash_index)
        expected.remove((node.start - 1, node.end))
    if expected:
        raise SafetyError('Inserted parbreak is missing from parsed candidate')
    kept = [index for index, node in enumerate(nodes)
            if index not in ignored and node.kind not in ('Space', 'Parbreak')]
    remap = {old: new for new, old in enumerate(kept)}
    result = []
    for index in kept:
        node = nodes[index]
        parent = parents.get(index)
        if parent is not None and parent not in remap:
            raise SafetyError('Unexpected descendant of removed whitespace or parbreak')
        if any(target not in remap for _, target in node.operands):
            raise SafetyError('Unexpected operand in removed whitespace or parbreak')
        result.append((node.kind, remap.get(parent), node.display, node.errors,
                       tuple((name, remap[target]) for name, target in node.operands),
                       source[node.start:node.end] if not node.children else None))
    return tuple(result)


def preview(tree, path, *, catalog=None):
    """Preview only direct footnotes before existing literal paragraph markers."""
    records = load_rules()[0]['rules'] if catalog is None else catalog
    source = tree.source
    empty = dict(path=path, baseline_sha256=digest(source), candidate_sha256=None,
                 conditions=CONDITIONS, runtime_binding_proved=False, render_equivalence_proved=False,
                 edit_permission=False, compiler_permission=False, edits=[])
    try:
        if tree.mode != 'markup' or tree.nodes[0].errors:
            raise SafetyError('Requires well-formed markup source')
        if _has_explicit_shadow(tree):
            return dict(empty, verification='withheld', reason='Explicit footnote, marker or parbreak shadow')
        before_resolution = resolve(tree, packages=())
        before = footnote_rules.analyze_conditional_nested(tree, path, before_resolution, catalog=records)
        edits, targets = _select(tree, path, records, before_resolution, before)
        if not edits:
            return None
        proposal = plan(source, edits)
        if proposal.deferred or len(proposal.edits) != len(edits) or len(targets) != len(edits):
            raise SafetyError('Conflicting prose footnote gap edits')
        candidate = proposal.candidate()
        parsed = parse(candidate, tree.mode)
        shifts = [0]
        for edit in proposal.edits:
            shifts.append(shifts[-1] + len(edit.replacement))
        inserted = [(edit.start + shifts[index], edit.start + shifts[index] + len(b'#parbreak()'))
                    for index, edit in enumerate(proposal.edits)]
        if (parsed.nodes[0].errors or _signature(tree) != _signature(parsed, inserted)
                or _scopes(tree, records, proposal.edits) != _scopes(parsed, records)):
            raise SafetyError('Prose footnote break changed other syntax or suppression targets')
        after_resolution = resolve(parsed, packages=())
        after = footnote_rules.analyze_conditional_nested(parsed, path, after_resolution, catalog=records)
        for rule_id in footnote_rules.RULE_IDS:
            expected = _keys(source, before[rule_id].diagnostics, proposal.edits)
            if rule_id == RULE:
                expected.subtract(_keys(source, targets, proposal.edits))
            if (any(count < 0 for count in expected.values())
                    or +expected != _keys(candidate, after[rule_id].diagnostics)
                    or _keys(source, before[rule_id].suppressed, proposal.edits)
                       != _keys(candidate, after[rule_id].suppressed)):
                raise SafetyError('Prose footnote break changed other footnote findings or suppressions')
        for analyzer in (paragraph_rules.analyze_conditional_nested_gap,
                         paragraph_rules.analyze_conditional_nested_target,
                         paragraph_rules.analyze_conditional_nested_capitalization,
                         prose_marker_rules.analyze_conditional):
            original = analyzer(tree, path, catalog=records)
            final = analyzer(parsed, path, catalog=records)
            if not _same_findings(tree, original, parsed, final, proposal.edits):
                raise SafetyError('Prose footnote break changed paragraph findings or suppressions')
        original_proof = proof_rules.analyze_conditional(tree, path, before_resolution, catalog=records)
        final_proof = proof_rules.analyze_conditional(parsed, path, after_resolution, catalog=records)
        if not _same_findings(tree, original_proof, parsed, final_proof, proposal.edits):
            raise SafetyError('Prose footnote break changed proof findings or suppressions')
        if _select(parsed, path, records, after_resolution, after)[0]:
            raise SafetyError('Prose footnote break did not converge')
        return dict(empty, candidate_sha256=digest(candidate),
                    verification='conditional-prose-footnote-gap-syntax-directives-and-reanalysis',
                    edits=[dict(start=edit.start, end=edit.end, replacement=edit.replacement.decode('utf-8'),
                                rule_id=RULE) for edit in proposal.edits])
    except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
        return dict(empty, verification='withheld', reason=str(error))
