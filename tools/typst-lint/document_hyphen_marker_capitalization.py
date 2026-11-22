"""Source-only capitalization previews after literal hyphenated markers.

No marker identity, prose language, actual execution, rendering equivalence,
source-write or compiler authority is inferred from this conditional preview.
"""
from collections import Counter

import document_conditional_hyphen_marker as marker
from document_conditional_continuation import _nontext_signature
from document_display import analyze_all as analyze_displays
from document_gap_preview import _scopes
from document_sequence_preview import _same_findings
from edit_plan import Edit, plan
import continuation_rules
import footnote_rules
import paragraph_rules
import proof_rules
import prose_marker_rules
from script_rule import analyze as analyze_scripts
from static_resolver import resolve
from syntax_client import parse
from typst_lint import InputError, SafetyError, digest, load_rules

RULE = paragraph_rules.CAPITALIZATION
CONDITIONS = (
    'The literal paragraph-tab resolves to the intended paragraph marker at runtime.',
    'The following paragraph is ordinary English prose and its content executes.',
    'Source capitalization changes; rendered text may change depending on marker and show behavior.',
)


def _keys(rows):
    return Counter((r['rule_id'], r['path'], r['line'], r['column'], r['start'], r['end'],
                    r['marker_start'], r['marker_spelling'], r['grade'], r['source'], r['explanation']) for r in rows)


def preview(tree, path, *, catalog=None):
    """Preview exact ASCII casing edits, independently from other source plans."""
    records = load_rules()[0]['rules'] if catalog is None else catalog
    source = tree.source
    empty = dict(path=path, baseline_sha256=digest(source), candidate_sha256=None,
                 conditions=CONDITIONS, runtime_binding_proved=False, execution_proved=False,
                 render_equivalence_proved=False, edit_permission=False, compiler_permission=False, edits=[])
    try:
        if tree.mode != 'markup' or tree.nodes[0].errors:
            raise SafetyError('Requires well-formed markup source')
        before = marker.analyze(source, path, catalog=records)
        if before['status'] != 'PARTIAL':
            raise SafetyError('Hyphen marker hints are unavailable')
        if any('explicit paragraph-tab shadow' in reason for reason in before['unresolved']):
            raise SafetyError('Hyphen marker binding has an explicit shadow or mutation')
        selected = [row for row in before['diagnostics'] if row['rule_id'] == RULE]
        if not selected:
            return None
        edits = []
        for finding in selected:
            start, end, marker_start = finding['start'], finding['end'], finding['marker_start']
            initial = source[start:end]
            if (end != start + 1 or len(initial) != 1 or not (b'a' <= initial <= b'z')
                    or source[marker_start:marker_start + len(b'#paragraph-tab')] != b'#paragraph-tab'):
                raise SafetyError('Conditional marker or capitalization location is invalid')
            edits.append(Edit(start, end, initial.upper()))
        proposal = plan(source, edits)
        if proposal.deferred or len(proposal.edits) != len(edits):
            raise SafetyError('Conflicting capitalization edits')
        candidate = proposal.candidate()
        parsed = parse(candidate, tree.mode)
        if (parsed.nodes[0].errors or _nontext_signature(tree) != _nontext_signature(parsed)
                or _scopes(tree, records, proposal.edits) != _scopes(parsed, records)):
            raise SafetyError('Capitalization preview changed non-text syntax or suppression targets')
        after = marker.analyze(candidate, path, catalog=records)
        expected = _keys(before['diagnostics'])
        expected.subtract(_keys(selected))
        if (after['status'] != 'PARTIAL' or after['unresolved'] != before['unresolved']
                or any(n < 0 for n in expected.values()) or +expected != _keys(after['diagnostics'])
                or _keys(before['suppressed']) != _keys(after['suppressed'])):
            raise SafetyError('Capitalization preview changed other marker hints or suppressions')
        before_resolution, after_resolution = resolve(tree, packages=()), resolve(parsed, packages=())
        displays_before = analyze_displays(tree, path, catalog=records)
        displays_after = analyze_displays(parsed, path, catalog=records)
        if any(not _same_findings(tree, displays_before[rid], parsed, displays_after[rid], proposal.edits)
               for rid in displays_before):
            raise SafetyError('Capitalization preview changed display findings')
        if not _same_findings(tree, analyze_scripts(tree, path, catalog=records, _verify=False, _allow_code_call=True),
                              parsed, analyze_scripts(parsed, path, catalog=records, _verify=False, _allow_code_call=True),
                              proposal.edits):
            raise SafetyError('Capitalization preview changed script findings')
        notes_before = footnote_rules.analyze_conditional_nested(tree, path, before_resolution, catalog=records)
        notes_after = footnote_rules.analyze_conditional_nested(parsed, path, after_resolution, catalog=records)
        if any(not _same_findings(tree, notes_before[rid], parsed, notes_after[rid], proposal.edits)
               for rid in footnote_rules.RULE_IDS):
            raise SafetyError('Capitalization preview changed footnote findings')
        for analyzer in (paragraph_rules.analyze_conditional_nested_gap, paragraph_rules.analyze_conditional_nested_target,
                         paragraph_rules.analyze_conditional_nested_capitalization, prose_marker_rules.analyze_conditional,
                         continuation_rules.analyze_conditional):
            if not _same_findings(tree, analyzer(tree, path, catalog=records),
                                  parsed, analyzer(parsed, path, catalog=records), proposal.edits):
                raise SafetyError('Capitalization preview changed other paragraph findings')
        if not _same_findings(tree, proof_rules.analyze_conditional(tree, path, before_resolution, catalog=records),
                              parsed, proof_rules.analyze_conditional(parsed, path, after_resolution, catalog=records),
                              proposal.edits):
            raise SafetyError('Capitalization preview changed proof findings')
        return dict(empty, candidate_sha256=digest(candidate),
                    verification='conditional-hyphen-capitalization-syntax-directives-and-reanalysis',
                    edits=[dict(start=edit.start, end=edit.end,
                                replacement=edit.replacement.decode('ascii'), rule_id=RULE)
                           for edit in proposal.edits])
    except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
        return dict(empty, verification='withheld', reason=str(error))
