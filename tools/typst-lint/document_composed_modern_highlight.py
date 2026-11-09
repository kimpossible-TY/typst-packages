"""Recompute a conditional modern-highlight stage after a verified sequence.

The returned private byte map is analysis output, not source-write or
publication authority. The caller must bind those exact bytes to compiler
staging and apply its independent dependency and workspace checks.
"""
import json
import time
from types import MappingProxyType

import conditional_modern_highlight as modern
import document_footnote_evidence as footnote
import document_intermediate_context as intermediate
import document_modern_highlight_evidence as modern_evidence
import document_modern_highlight_preview as highlight
import document_sequence_preview as sequence
from edit_plan import Edit, plan
from typst_lint import SafetyError, digest


def stage(proof, records, *, profile='standard', partial_unsupported=False, withheld_records=None,
          partial_unproven_binding=False, explicit_withheld_sources=(), conditional_continuations=False,
          conditional_adjacent_punctuation=False, sequence_facade_evidence=False,
          conditional_deferred_footnotes=False, reviewed_math=False, phase_timings=None):
    """Derive both stages from issued inputs; never trust a caller's candidate."""
    if type(reviewed_math) is not bool:
        raise SafetyError('Reviewed math selection must be Boolean')
    if sequence_facade_evidence is True and profile not in ('conditional-gaps-and-spacers',
                                                            'conditional-gaps-punctuation-and-spacers'):
        raise SafetyError('Facade-footnote evidence requires a spacer profile')
    if partial_unsupported is True and sequence_facade_evidence is not True:
        raise SafetyError('Partial unsupported preview requires facade-footnote evidence')
    if phase_timings is not None and type(phase_timings) is not dict:
        raise SafetyError('Phase timing sink must be a dictionary')
    stable_records = intermediate._copy_records(records)
    stable_withheld = intermediate._copy_records(withheld_records)
    started = time.monotonic() if phase_timings is not None else None
    view = intermediate.derive(proof, stable_records, profile=profile, partial_unsupported=partial_unsupported,
        withheld_records=stable_withheld, partial_unproven_binding=partial_unproven_binding,
        explicit_withheld_sources=explicit_withheld_sources, conditional_continuations=conditional_continuations,
        conditional_adjacent_punctuation=conditional_adjacent_punctuation,
        sequence_facade_evidence=sequence_facade_evidence,
        conditional_deferred_footnotes=conditional_deferred_footnotes)
    if phase_timings is not None:
        phase_timings['replay_and_intermediate_seconds'] = time.monotonic() - started
    session = intermediate.issue_binding(view)
    analyzer = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)
    preview_options = {'reviewed_math': True} if reviewed_math else {}
    started = time.monotonic() if phase_timings is not None else None
    diagnostics, proposed = highlight.preview_all(analyzer, return_evidence=True, **preview_options)
    if phase_timings is not None:
        phase_timings['highlight_analysis_seconds'] = time.monotonic() - started
    modern_evidence._verify_routes(analyzer, view.sources, diagnostics)
    conditional_calls = {record['path']: sequence._final_conditional_footnote_calls(
                         proof._session._context.source_for(record['path']).tree, record)
                         for record in stable_records}
    conditional_calls = {path: offsets for path, offsets in conditional_calls.items() if offsets}
    excluded = set(explicit_withheld_sources)
    excluded.update(row['path'] for row in (stable_withheld or ()))
    baseline = footnote._valid(proof)
    final = dict(view.sources)
    edits_by_path = {}
    modern_withheld = []
    imported_sources = {edge.target for edge in view.graph.edges} if reviewed_math else set()
    for row in proposed['by_file']:
        path = row['path']
        if path in excluded or row['verification'] == 'withheld':
            continue
        # Any edited imported module could change another source's wildcard
        # namespace. Until candidate-graph math replay exists, withhold it.
        if path in imported_sources:
            modern_withheld.append(dict(path=path, reason_code='math-import-dependency',
                reason='A highlight edit would change an imported source used by the math binding graph'))
            continue
        key = ('project', path)
        if key not in final:
            raise SafetyError('Modern highlight source is missing from the intermediate capture')
        edits = tuple(Edit(item['start'], item['end'], item['replacement'].encode('utf-8'))
                      for item in row['edits'])
        before = final[key]
        proposal = plan(before, edits)
        if proposal.deferred or len(proposal.edits) != len(edits):
            raise SafetyError('Composed modern highlight edits conflict')
        after = proposal.candidate()
        if digest(before) != row['baseline_sha256'] or digest(after) != row['candidate_sha256']:
            raise SafetyError('Composed modern highlight hash chain changed')
        if path in conditional_calls:
            try:
                conditional_calls[path] = sequence._map_conditional_calls_through_edits(
                    view.context.source_for(path).tree, conditional_calls[path], proposal.edits)
            except sequence.ConditionalFootnoteEditOverlap:
                modern_withheld.append(dict(path=path, reason_code='conditional-footnote-overlap',
                    reason='A modern highlight edit touches a conditionally bound footnote call'))
                continue
        final[key] = after
        edits_by_path[path] = edits

    # Every captured route, including suppressed and unselected findings, was
    # established on the intermediate graph. A second-stage edit in any
    # other route source invalidates that evidence; do not guess its binding.
    for row in diagnostics['by_file']:
        for finding in (*row['diagnostics'], *row['suppressed']):
            dependencies = {item['path'] for item in finding['source_hashes']}
            for route in (finding['route'], *finding['content_context']):
                dependencies.update(item['path'] for item in route if not item['path'].startswith('@'))
            if (dependencies - {row['path']}) & edits_by_path.keys():
                raise SafetyError('A composed highlight changes another finding route source')

    if conditional_calls:
        project = {source.path: final['project', source.path] for source in view.context.sources}
        options = {'allow_deferred': True} if conditional_deferred_footnotes else {}
        footnote.PreviewFootnoteEvidence.validate_staged_facades(proof, project, conditional_calls, **options)

    # Recompute independently before authorizing even a private staging map.
    started = time.monotonic() if phase_timings is not None else None
    repeated_diagnostics, repeated_proposed = highlight.preview_all(analyzer, return_evidence=True, **preview_options)
    if phase_timings is not None:
        phase_timings['highlight_replay_seconds'] = time.monotonic() - started
    if repeated_diagnostics != diagnostics or repeated_proposed != proposed:
        raise SafetyError('Composed highlight replay changed')
    modern_evidence._verify_routes(analyzer, view.sources, repeated_diagnostics)
    if (not sequence._same_data(stable_records, records)
            or not sequence._same_data(stable_withheld, withheld_records)):
        raise SafetyError('Composed sequence records changed during staging')
    intermediate.assert_intermediate(view)
    footnote._valid(proof)
    changed = []
    record_by_path = {record['path']: record for record in stable_records}
    for source in view.context.sources:
        key = ('project', source.path)
        before, middle, after = baseline[key], view.sources[key], final[key]
        if before != middle or middle != after:
            stages = [dict(rule_id=step['rule_id'], baseline_sha256=step['baseline_sha256'],
                           candidate_sha256=step['candidate_sha256'],
                           edit_spans=[dict(start=edit['start'], end=edit['end']) for edit in step['edits']])
                      for step in record_by_path.get(source.path, {}).get('steps', ())]
            if middle != after:
                stages.append(dict(rule_id='pde.text-only-highlight', baseline_sha256=digest(middle),
                    candidate_sha256=digest(after), edit_spans=[dict(start=edit.start, end=edit.end)
                        for edit in edits_by_path[source.path]]))
            changed.append(dict(path=source.path, baseline_sha256=digest(before),
                intermediate_sha256=digest(middle), candidate_sha256=digest(after),
                highlight_edits=len(edits_by_path.get(source.path, ())), stages=stages))
    accepted = sum(map(len, edits_by_path.values()))
    unpreviewed = proposed['diagnostics_count'] - accepted
    result = dict(status='INCOMPLETE' if unpreviewed or proposed['unresolved_reasons_count']
                  or proposed['withheld_source_count'] or modern_withheld or excluded else 'CONDITIONAL',
                  source_count=len(changed), candidate_count=accepted, sources=changed,
                  unpreviewed_diagnostics=unpreviewed,
                  unresolved_reasons_count=proposed['unresolved_reasons_count'],
                  withheld_source_count=proposed['withheld_source_count'] + len(modern_withheld),
                  modern_withheld_sources=modern_withheld,
                  sequence_withheld_source_count=len(excluded),
                  runtime_binding_proved=False, execution_proved=False, coverage_complete=False,
                  render_equivalence_proved=False, edit_permission=False, compiler_permission=False,
                  staged_workspace_proved=False)
    if reviewed_math:
        result['reviewed_math'] = True
        result['math_conditions'] = highlight.MATH_CONDITIONS
    encoded = [json.dumps(x, ensure_ascii=False, sort_keys=True, indent=2) for x in (diagnostics, proposed, result)]
    size = sum(len(raw.encode('utf-8')) + 2 * raw.count('\n') for raw in encoded)
    stage_items = (len(modern_withheld)
                   + sum(1 + sum(1 + len(step['edit_spans']) for step in row['stages']) for row in changed))
    if (size > highlight.MAX_PREVIEW_BYTES
            or diagnostics['report_items'] + proposed['report_items'] + stage_items > highlight.MAX_PREVIEW_ITEMS):
        raise SafetyError('Composed highlight report budget exceeded')
    return MappingProxyType(final), result, view
