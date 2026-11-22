"""Bounded conditional composition of prose paragraph marker insertions.

The source proof is conditional: insertion can change visible indentation and
never proves runtime binding, show behavior, execution or write authority.
"""
from document_composed_prose_footnote_gap import _within_budget
from document_composed_capitalization import _marker_shadow
from document_sequence_preview import _FindingMapper, _keys, _preserve_unresolved_paragraph_boundaries, _same_findings
from edit_plan import Edit, plan
import continuation_rules
import document_conditional_prose_marker as marker
import document_display
import footnote_rules
import paragraph_rules
import proof_rules
import prose_marker_rules
from script_rule import analyze as analyze_scripts
from static_resolver import resolve
from suppression import resolve as resolve_suppressions
from syntax_client import parse
from typst_lint import SafetyError, digest


def _literal_targets(tree, catalog):
    """Independently recompute exact unsuppressed direct prose boundaries."""
    nodes, source = tree.nodes, tree.source
    parents = {child: index for index, node in enumerate(nodes) for child in node.children}
    excluded = {}
    for index in range(len(nodes)):
        parent = parents.get(index)
        excluded[index] = parent is not None and (excluded[parent]
            or nodes[parent].kind in prose_marker_rules._CONDITIONAL_EXCLUDED)
    scopes = resolve_suppressions(tree, {row['id'] for row in catalog})
    targets = []
    def literal(segment):
        return (bool(segment) and any(nodes[i].kind == 'Text' and source[nodes[i].start:nodes[i].end].strip()
                                      for i in segment)
                and all(nodes[i].kind in ('Text', 'Space')
                        or (nodes[i].kind == 'Equation' and not nodes[i].display) for i in segment))
    for body_id, body in enumerate(nodes):
        if body.kind != 'Markup' or excluded[body_id]:
            continue
        siblings = body.children
        segments, breaks, start = [], [], 0
        for position, index in enumerate(siblings):
            if nodes[index].kind == 'Parbreak':
                segments.append(siblings[start:position]); breaks.append(index); start = position + 1
        segments.append(siblings[start:])
        for pos, gap_id in enumerate(breaks):
            gap = nodes[gap_id]
            if (not prose_marker_rules._GAP.fullmatch(source[gap.start:gap.end])
                    or not literal(segments[pos]) or not literal(segments[pos + 1])):
                continue
            target = next(nodes[i] for i in segments[pos + 1]
                          if nodes[i].kind == 'Equation' or (nodes[i].kind == 'Text'
                             and source[nodes[i].start:nodes[i].end].strip()))
            if (target.kind != 'Text' or target.start >= len(source)
                    or not 65 <= source[target.start] <= 90
                    or any(scope.covers(prose_marker_rules.RULE_ID, target.start) for scope in scopes)):
                continue
            line_start = source.rfind(b'\n', 0, target.start) + 1
            indent = source[line_start:target.start]
            if any(byte not in b' \t' for byte in indent):
                continue
            newline = b'\r\n' if line_start >= 2 and source[line_start-2:line_start] == b'\r\n' else b'\n'
            targets.append(Edit(target.start, target.start, b'#paragraph_tab' + newline + indent))
    return tuple(targets)


def select(tree, path, catalog):
    """Independently select syntax-owned, unsuppressed literal targets."""
    if not _within_budget(tree) or _marker_shadow(tree, 'paragraph_tab'):
        return None
    hint = prose_marker_rules.analyze_conditional(tree, path, catalog=catalog)
    if hint.parse_errors:
        return None
    selected, _ = marker._select(tree, hint)
    exact = _literal_targets(tree, catalog)
    if selected != exact:
        raise SafetyError('Prose marker hints differ from literal prose boundary ownership')
    if not exact:
        return None
    proposed = plan(tree.source, exact)
    if proposed.deferred or len(proposed.edits) != len(exact):
        raise SafetyError('Prose marker insertions conflict')
    return proposed


def preview(tree, path, catalog):
    """Obtain the existing source proof and independent bounds for this stage."""
    expected = select(tree, path, catalog)
    if expected is None:
        return None
    after = parse(expected.candidate(), tree.mode)
    if after.nodes[0].errors or not _within_budget(after) or _marker_shadow(after, 'paragraph_tab'):
        return None
    try:
        _preserve_unresolved_paragraph_boundaries(tree, after, path, catalog, expected.edits)
    except SafetyError:
        return None
    proof = marker.preview(tree, path, catalog=catalog)
    if proof is None:
        raise SafetyError('Selected prose marker source proof was omitted')
    if proof.get('verification') == 'withheld':
        return None
    if (proof.get('verification') != 'conditional-prose-marker-syntax-directives-and-reanalysis'
            or proof.get('conditions') != marker.CONDITIONS
            or any(proof.get(key) is not False for key in ('runtime_binding_proved', 'execution_proved',
                'render_equivalence_proved', 'edit_permission', 'compiler_permission'))):
        raise SafetyError('Prose marker source proof invalid')
    rows = [dict(start=e.start, end=e.end, replacement=e.replacement.decode('ascii'),
                 rule_id=prose_marker_rules.RULE_ID) for e in expected.edits]
    if (proof.get('edits') != rows or proof.get('baseline_sha256') != digest(tree.source)
            or proof.get('candidate_sha256') != digest(after.source)):
        raise SafetyError('Prose marker source proof changed')
    return dict(rule_id=prose_marker_rules.RULE_ID, baseline_sha256=digest(tree.source),
                candidate_sha256=digest(after.source), edits=[dict(row, conditions=list(marker.CONDITIONS),
                    runtime_binding_proved=False, execution_proved=False, render_equivalence_proved=False,
                    edit_permission=False, compiler_permission=False) for row in rows])


def verify_transition(before, after, path, catalog, edits):
    """Recheck candidate syntax, directives and all relevant old diagnostics."""
    from document_gap_preview import _scopes
    shift, positions = 0, []
    for edit in edits:
        positions.append(edit.start + shift); shift += len(edit.replacement)
    if (not _within_budget(before) or not _within_budget(after)
            or after.nodes[0].errors or _marker_shadow(after, 'paragraph_tab')
            or marker._signature_without_markers(before, ()) != marker._signature_without_markers(after, tuple(positions))
            or _scopes(before, catalog, edits) != _scopes(after, catalog)):
        raise SafetyError('Prose marker insertion changed syntax, binding shape or suppression ownership')
    original = prose_marker_rules.analyze_conditional(before, path, catalog=catalog)
    final = prose_marker_rules.analyze_conditional(after, path, catalog=catalog)
    mapper = _FindingMapper(before.source)
    selected_offsets = {e.start for e in edits}
    selected = [row for row in original.diagnostics if mapper.offset(row) in selected_offsets]
    expected = _keys(before.source, original.diagnostics, edits)
    expected.subtract(_keys(before.source, selected, edits))
    if (len(selected) != len(edits) or any(count < 0 for count in expected.values())
            or +expected != _keys(after.source, final.diagnostics)
            or _keys(before.source, original.suppressed, edits) != _keys(after.source, final.suppressed)
            or final.parse_errors or select(after, path, catalog) is not None):
        raise SafetyError('Prose marker insertion changed findings or suppression')
    old, new = document_display.analyze_all(before, path, catalog=catalog), document_display.analyze_all(after, path, catalog=catalog)
    if any(not _same_findings(before, old[r], after, new[r], edits) for r in old):
        raise SafetyError('Prose marker insertion changed display findings')
    if not _same_findings(before, analyze_scripts(before, path, catalog=catalog, _verify=False, _allow_code_call=True),
                          after, analyze_scripts(after, path, catalog=catalog, _verify=False, _allow_code_call=True), edits):
        raise SafetyError('Prose marker insertion changed script findings')
    for analyze in (paragraph_rules.analyze_conditional_nested_gap, paragraph_rules.analyze_conditional_nested_target,
                    paragraph_rules.analyze_conditional_nested_capitalization, continuation_rules.analyze_conditional):
        if not _same_findings(before, analyze(before, path, catalog=catalog),
                              after, analyze(after, path, catalog=catalog), edits):
            raise SafetyError('Prose marker insertion changed another paragraph finding')
    old_res, new_res = resolve(before, packages=()), resolve(after, packages=())
    old_notes = footnote_rules.analyze_conditional_nested(before, path, old_res, catalog=catalog)
    new_notes = footnote_rules.analyze_conditional_nested(after, path, new_res, catalog=catalog)
    if any(not _same_findings(before, old_notes[r], after, new_notes[r], edits) for r in footnote_rules.RULE_IDS):
        raise SafetyError('Prose marker insertion changed footnote findings')
    if not _same_findings(before, proof_rules.analyze_conditional(before, path, old_res, catalog=catalog),
                          after, proof_rules.analyze_conditional(after, path, new_res, catalog=catalog), edits):
        raise SafetyError('Prose marker insertion changed proof findings')
    _preserve_unresolved_paragraph_boundaries(before, after, path, catalog, edits)
