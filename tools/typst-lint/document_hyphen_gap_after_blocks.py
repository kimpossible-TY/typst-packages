"""Optional source-only hyphen-marker gaps after bounded block-like calls.

The callee identities are conditions, not proven package or builtin bindings.
This independent plan never applies changes or grants compilation authority.
"""
from conditional_highlight import _combined_import, _module_alias
from document_conditional_marker_tags import _has_name
from document_conditional_hyphen_marker import analyze
from document_conditional_hyphen_marker_gap import _keys
from document_display import analyze_all as analyze_displays
from document_gap_preview import _scopes, _syntax_signature
from document_sequence_preview import _same_findings
from edit_plan import Edit, plan
import continuation_rules
import footnote_rules
import import_graph
import paragraph_rules
import proof_rules
import prose_marker_rules
from script_rule import analyze as analyze_scripts
from static_resolver import resolve
from syntax_client import parse
from typst_lint import InputError, SafetyError, digest, load_rules

RULE = paragraph_rules.MARKER_GAP
CONDITIONS = (
    'The paragraph-tab spelling resolves to the intended marker and its show rule is active.',
    'The surrounding source actually executes in the intended prose context; rendering may change.',
    'Package selection, runtime bindings and source-write or compiler authority are not proved.',
)
PRECEDING_CONDITIONS = {
    'pagebreak': ('This preceding pagebreak resolves to the builtin pagebreak with its stated literal argument.',),
    'flowbox': ('This preceding flowbox resolves to the intended complete block-output helper, containing its own effects.',),
}
_ASSIGNMENTS = frozenset(('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq'))


def _explicit_shadow(tree, name):
    """Refuse obvious bindings/mutations globally; wildcard imports stay conditional."""
    nodes = tree.nodes
    def named(index):
        return _has_name(tree, index, name)
    for index, node in enumerate(nodes):
        if node.kind == 'ModuleImport':
            module = _module_alias(tree, index)
            if module is not None and module[1] == name:
                return True
            shape = import_graph.import_shape(tree, index)
            combined = _combined_import(tree, index) if shape is None else None
            if combined:
                shape, alias = combined
                if alias == name:
                    return True
            if shape and shape.items is not None:
                if any(local == name and (name == 'pagebreak' or original != local)
                       for original, local in shape.items):
                    return True
            elif shape is None:
                parts = import_graph.significant(tree, index)
                if any(nodes[p].kind == 'ImportItems' and named(p) for p in parts):
                    return True
            continue
        parts = import_graph.significant(tree, index) if node.kind in (
            'LetBinding', 'Closure', 'ForLoop', 'Binary', 'DestructAssignment') else ()
        if node.kind == 'LetBinding' and len(parts) >= 2:
            target = parts[1]
            if nodes[target].kind == 'Closure':
                nested = import_graph.significant(tree, target)
                target = nested[0] if nested else target
            if named(target):
                return True
        elif node.kind == 'Closure':
            if any(nodes[p].kind == 'Params' and named(p) for p in parts):
                return True
        elif node.kind == 'ForLoop':
            for p in parts[1:]:
                if nodes[p].kind == 'In':
                    break
                if named(p):
                    return True
        elif node.kind == 'Binary' and parts and any(nodes[p].kind in _ASSIGNMENTS for p in parts):
            if named(parts[0]):
                return True
        elif node.kind == 'DestructAssignment' and parts and named(parts[0]):
            return True
    return False


def _callee(tree, index):
    nodes, source = tree.nodes, tree.source
    call = nodes[index]
    if call.kind != 'FuncCall' or len(call.children) != 2:
        return None
    ident, args = (nodes[i] for i in call.children)
    if ident.kind != 'Ident' or args.kind != 'Args':
        return None
    name = source[ident.start:ident.end].decode('utf-8')
    parts = import_graph.significant(tree, call.children[1])
    kinds = tuple(nodes[p].kind for p in parts)
    if name == 'flowbox' and kinds == ('LeftParen', 'RightParen', 'ContentBlock'):
        return name
    if name == 'pagebreak' and kinds == ('LeftParen', 'Named', 'RightParen'):
        named = import_graph.significant(tree, parts[1])
        if (len(named) == 3 and nodes[named[0]].kind == 'Ident'
                and source[nodes[named[0]].start:nodes[named[0]].end] == b'weak'
                and nodes[named[1]].kind == 'Colon' and nodes[named[2]].kind == 'Bool'
                and source[nodes[named[2]].start:nodes[named[2]].end] in (b'true', b'false')):
            return name
    return None


def _select(tree, diagnostics):
    nodes, source = tree.nodes, tree.source
    targets = {row['marker_start']: row for row in diagnostics if row['rule_id'] == RULE}
    parents = {child: (index, position) for index, node in enumerate(nodes)
               for position, child in enumerate(node.children)}
    edits, selected, names = [], [], []
    shadows = {}
    for index, mark in enumerate(nodes):
        if mark.kind != 'Hash' or mark.start not in targets or index not in parents:
            continue
        parent, position = parents[index]
        body = nodes[parent]
        if body.kind != 'Markup' or position + 1 >= len(body.children):
            raise SafetyError('Marker is not in the expected markup body')
        marker = nodes[body.children[position + 1]]
        if (marker.kind != 'Ident' or mark.end != marker.start
                or source[mark.start:marker.end] != b'#paragraph-tab'):
            raise SafetyError('Marker syntax no longer matches the hint')
        prev = position - 1
        while prev >= 0 and nodes[body.children[prev]].kind in ('Space', 'Parbreak'):
            prev -= 1
        if prev < 1:
            continue
        call_id = body.children[prev]
        call, callmark = nodes[call_id], nodes[body.children[prev - 1]]
        if callmark.kind != 'Hash' or callmark.end != call.start:
            continue
        name = _callee(tree, call_id)
        if name is None:
            continue
        if name not in shadows:
            shadows[name] = _explicit_shadow(tree, name)
        if shadows[name]:
            continue
        line_start = source.rfind(b'\n', 0, mark.start) + 1
        if source[line_start:mark.start].strip(b' \t'):
            continue
        separator = source[call.end:line_start]
        if separator.strip(b' \t\r\n') or b'\r' in separator.replace(b'\r\n', b''):
            continue
        newline = b'\r\n' if source[max(0, line_start - 2):line_start] == b'\r\n' else b'\n'
        edits.append(Edit(line_start, line_start, newline))
        selected.append(targets[mark.start])
        names.append(name)
    return tuple(edits), tuple(selected), tuple(names)


def preview(tree, path, *, catalog=None):
    """Preview literal block-adjacent cases under explicit binding assumptions."""
    records = load_rules()[0]['rules'] if catalog is None else catalog
    source = tree.source
    empty = dict(path=path, baseline_sha256=digest(source), candidate_sha256=None,
                 conditions=CONDITIONS, runtime_binding_proved=False, execution_proved=False,
                 render_equivalence_proved=False, edit_permission=False, compiler_permission=False, edits=[])
    try:
        if tree.mode != 'markup' or tree.nodes[0].errors:
            raise SafetyError('Requires well-formed markup source')
        before = analyze(source, path, catalog=records)
        if before['status'] == 'UNAVAILABLE':
            raise SafetyError('Hyphen marker analysis is unavailable')
        edits, selected, names = _select(tree, before['diagnostics'])
        if not edits:
            return None
        proposed = plan(source, edits)
        if proposed.deferred or len(proposed.edits) != len(edits) or len(set(names)) > 2:
            raise SafetyError('Conflicting conditional marker gap edits')
        candidate = proposed.candidate()
        parsed = parse(candidate, tree.mode)
        if (parsed.nodes[0].errors or _syntax_signature(tree) != _syntax_signature(parsed)
                or _scopes(tree, records, proposed.edits) != _scopes(parsed, records)):
            raise SafetyError('Blank line changes other syntax or suppression targets')
        after = analyze(candidate, path, catalog=records)
        expected = _keys(before['diagnostics'], proposed.edits)
        expected.subtract(_keys(selected, proposed.edits))
        if (after['status'] == 'UNAVAILABLE' or any(count < 0 for count in expected.values())
                or +expected != _keys(after['diagnostics'])
                or _keys(before['suppressed'], proposed.edits) != _keys(after['suppressed'])):
            raise SafetyError('Blank line changed other hyphen marker hints or suppressions')
        displays_before, displays_after = analyze_displays(tree, path, catalog=records), analyze_displays(parsed, path, catalog=records)
        if any(not _same_findings(tree, displays_before[rid], parsed, displays_after[rid], proposed.edits)
               for rid in displays_before):
            raise SafetyError('Blank line changed display findings')
        if not _same_findings(tree, analyze_scripts(tree, path, catalog=records, _verify=False, _allow_code_call=True),
                              parsed, analyze_scripts(parsed, path, catalog=records, _verify=False, _allow_code_call=True), proposed.edits):
            raise SafetyError('Blank line changed script findings')
        before_resolution, after_resolution = resolve(tree, packages=()), resolve(parsed, packages=())
        a = footnote_rules.analyze_conditional_nested(tree, path, before_resolution, catalog=records)
        b = footnote_rules.analyze_conditional_nested(parsed, path, after_resolution, catalog=records)
        if any(not _same_findings(tree, a[rid], parsed, b[rid], proposed.edits) for rid in footnote_rules.RULE_IDS):
            raise SafetyError('Blank line changed footnote findings')
        for analyzer in (paragraph_rules.analyze_conditional_nested_gap, paragraph_rules.analyze_conditional_nested_target,
                         paragraph_rules.analyze_conditional_nested_capitalization, prose_marker_rules.analyze_conditional,
                         continuation_rules.analyze_conditional):
            if not _same_findings(tree, analyzer(tree, path, catalog=records), parsed,
                                  analyzer(parsed, path, catalog=records), proposed.edits):
                raise SafetyError('Blank line changed paragraph findings')
        if not _same_findings(tree, proof_rules.analyze_conditional(tree, path, before_resolution, catalog=records),
                              parsed, proof_rules.analyze_conditional(parsed, path, after_resolution, catalog=records), proposed.edits):
            raise SafetyError('Blank line changed proof findings')
        if _select(parsed, after['diagnostics'])[0]:
            raise SafetyError('Block-adjacent marker gap preview did not converge')
        by_start = {edit.start: name for edit, name in zip(edits, names)}
        return dict(empty, candidate_sha256=digest(candidate),
                    verification='conditional-block-adjacent-marker-gap-reanalysis',
                    edits=[dict(start=edit.start, end=edit.end, replacement=edit.replacement.decode(),
                                rule_id=RULE, preceding_kind=by_start[edit.start],
                                preceding_conditions=PRECEDING_CONDITIONS[by_start[edit.start]])
                           for edit in proposed.edits])
    except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
        return dict(empty, verification='withheld', reason=str(error))
