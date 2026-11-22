"""Conditional composition of prose-footnote breaks before displays, figures or hyphenated markers.

No runtime binding, execution, render equivalence, or source-write authority is
implied. Complex or ambiguous sources retain their existing sequence unchanged.
"""
import document_composed_prose_footnote_gap as budget
import document_prose_footnote_display_gap as display
import document_prose_footnote_extra_gaps as extra
import footnote_rules
import paragraph_rules
from document_sequence_preview import _preserve_unresolved_paragraph_boundaries, _same_data
from edit_plan import Edit, plan
from static_resolver import resolve
from syntax_client import parse
from typst_lint import SafetyError, digest


def _prove(tree, path, catalog, kind):
    if kind not in ('display', 'figure', 'hyphen'):
        raise SafetyError('Invalid conditional prose block selection')
    if (b'#footnote' not in tree.source or (kind == 'display' and b'$' not in tree.source)
            or (kind == 'figure' and b'#figure' not in tree.source)
            or (kind == 'hyphen' and b'#paragraph-tab' not in tree.source) or not budget._within_budget(tree)):
        return None
    boundaries = paragraph_rules.analyze_conditional_nested_gap(tree, path, catalog=catalog)
    if any(reason.startswith(paragraph_rules.NESTED_GAP_UNRESOLVED) for reason in boundaries.skips):
        return None
    before_resolution = resolve(tree, packages=())
    notes = footnote_rules.analyze_conditional_nested(tree, path, before_resolution, catalog=catalog)
    source = display if kind == 'display' else extra
    selection = source._select(tree, notes)
    expected, targets = selection[:2]
    # The existing extra proof covers figures and hyphenated markers together.
    # Do not select or compose either on a mixed source without a separate proof.
    expected_kind = 'figure' if kind == 'figure' else 'hyphenated-paragraph-marker'
    if kind != 'display' and any(item != expected_kind for item in selection[2]):
        return None
    if not expected:
        return None
    preliminary = plan(tree.source, expected)
    if preliminary.deferred or len(preliminary.edits) != len(expected) or len(expected) != len(targets):
        raise SafetyError('Conditional prose block candidates conflict')
    candidate = preliminary.candidate()
    parsed = parse(candidate, tree.mode)
    if parsed.nodes[0].errors or not budget._within_budget(parsed):
        return None
    proof = source.preview(tree, path, catalog=catalog)
    if proof is None:
        raise SafetyError('Conditional prose block candidates were omitted')
    if type(proof) is not dict:
        raise SafetyError('Conditional prose block proof is invalid')
    if proof.get('verification') == 'withheld':
        return None
    verification = ('conditional-prose-display-gap-syntax-directives-and-reanalysis' if kind == 'display'
                    else 'conditional-prose-extra-gap-syntax-directives-and-reanalysis')
    if (proof.get('verification') != verification or proof.get('conditions') != source.CONDITIONS
            or any(proof.get(key) is not False for key in ('runtime_binding_proved', 'execution_proved',
                'render_equivalence_proved', 'edit_permission', 'compiler_permission'))):
        raise SafetyError('Conditional prose block source proof is invalid')
    expected_rows = [dict(start=e.start, end=e.end, replacement=e.replacement.decode('ascii'), rule_id=source.RULE)
                     for e in expected]
    if kind != 'display':
        # Retain the literal target-specific runtime condition in every row.
        for row in expected_rows:
            row.update(following_kind=expected_kind, following_conditions=(
                ['The literal figure binding is the intended block-producing figure and '
                 'the preceding prose owns the intended paragraph boundary.'] if kind == 'figure' else
                ['The literal paragraph-tab binding and paragraph show rule are the intended ones.']))
    if (not _same_data(proof.get('edits'), expected_rows)
            or proof.get('baseline_sha256') != digest(tree.source)
            or proof.get('candidate_sha256') != digest(candidate)):
        raise SafetyError('Conditional prose block source selection or hash changed')
    try:
        _preserve_unresolved_paragraph_boundaries(tree, parsed, path, catalog, preliminary.edits)
    except SafetyError:
        return None
    rows = [dict(row, conditions=list(source.CONDITIONS), runtime_binding_proved=False,
                 execution_proved=False, render_equivalence_proved=False,
                 edit_permission=False, compiler_permission=False) for row in expected_rows]
    return dict(rule_id=source.RULE, baseline_sha256=digest(tree.source), candidate_sha256=digest(candidate), edits=rows)


def preview(tree, path, catalog, kind):
    return _prove(tree, path, catalog, kind)
