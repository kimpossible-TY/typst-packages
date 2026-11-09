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
from typst_lint import InputError, SafetyError, digest


def stage(proof, records, *, profile='standard', partial_unsupported=False, withheld_records=None,
          partial_unproven_binding=False, explicit_withheld_sources=(), conditional_continuations=False,
          conditional_adjacent_punctuation=False, sequence_facade_evidence=False,
          conditional_deferred_footnotes=False, reviewed_math=False, composed_references=False, phase_timings=None):
    """Derive both stages from issued inputs; never trust a caller's candidate."""
    if type(reviewed_math) is not bool:
        raise SafetyError('Reviewed math selection must be Boolean')
    if type(composed_references) is not bool or (composed_references and not reviewed_math):
        raise SafetyError('Composed references require reviewed math')
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
    baseline_diagnostics = baseline_proposed = None
    budget_fallback = False
    started = time.monotonic() if phase_timings is not None else None
    if composed_references:
        # Reissue the pre-existing plan so expansion cannot drop prior edits.
        baseline_diagnostics, baseline_proposed = highlight.preview_all(
            analyzer, return_evidence=True, reviewed_math=True)
        preview_options.update(allow_direct_references=True, allow_scoped_references=True)
    try:
        diagnostics, proposed = highlight.preview_all(analyzer, return_evidence=True, **preview_options)
    except InputError as error:
        if not composed_references or str(error) != 'Modern highlight preview report budget exceeded':
            raise
        # The extra proof is optional. A report-budget refusal cannot remove
        # the independently issued existing plan.
        diagnostics, proposed = baseline_diagnostics, baseline_proposed
        budget_fallback = True
    if composed_references and diagnostics != baseline_diagnostics:
        raise SafetyError('Composed reference proof changed issued diagnostics')
    if phase_timings is not None:
        phase_timings['highlight_analysis_seconds'] = time.monotonic() - started
    modern_evidence._verify_routes(analyzer, view.sources, diagnostics)
    selected_rows = list(proposed['by_file'])
    reference_fallbacks = []
    if composed_references and not budget_fallback:
        expanded = {row['path']: row for row in selected_rows}
        for base_row in baseline_proposed['by_file']:
            if base_row['verification'] == 'withheld':
                continue
            old = {(e['start'], e['end'], e['replacement'], e['rule_id']) for e in base_row['edits']}
            row = expanded.get(base_row['path'])
            new = (set() if row is None or row['verification'] == 'withheld' else
                   {(e['start'], e['end'], e['replacement'], e['rule_id']) for e in row['edits']})
            if not old.issubset(new):
                expanded[base_row['path']] = base_row
                reference_fallbacks.append(base_row['path'])
        # Keep the issued diagnostic source order, including fallback rows.
        selected_rows = [expanded[row['path']] for row in diagnostics['by_file'] if row['path'] in expanded]
    conditional_calls = {record['path']: sequence._final_conditional_footnote_calls(
                         proof._session._context.source_for(record['path']).tree, record)
                         for record in stable_records}
    conditional_calls = {path: offsets for path, offsets in conditional_calls.items() if offsets}
    original_conditional_calls = dict(conditional_calls) if composed_references else {}
    excluded = set(explicit_withheld_sources)
    excluded.update(row['path'] for row in (stable_withheld or ()))
    baseline = footnote._valid(proof)
    final = dict(view.sources)
    edits_by_path = {}
    modern_withheld = []
    imported_sources = {edge.target for edge in view.graph.edges} if reviewed_math else set()
    base_rows = ({row['path']: row for row in baseline_proposed['by_file']}
                 if composed_references else {})
    for row in selected_rows:
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
                base_row = base_rows.get(path)
                if (base_row is None or base_row['verification'] == 'withheld'
                        or sequence._same_data(base_row, row)):
                    modern_withheld.append(dict(path=path, reason_code='conditional-footnote-overlap',
                        reason='A modern highlight edit touches a conditionally bound footnote call'))
                    continue
                base_edits = tuple(Edit(item['start'], item['end'], item['replacement'].encode('utf-8'))
                                   for item in base_row['edits'])
                base_plan = plan(before, base_edits)
                base_candidate = base_plan.candidate()
                if (base_plan.deferred or len(base_plan.edits) != len(base_edits)
                        or digest(before) != base_row['baseline_sha256']
                        or digest(base_candidate) != base_row['candidate_sha256']):
                    raise SafetyError('Composed highlight fallback hash chain changed')
                try:
                    conditional_calls[path] = sequence._map_conditional_calls_through_edits(
                        view.context.source_for(path).tree, conditional_calls[path], base_plan.edits)
                except sequence.ConditionalFootnoteEditOverlap:
                    modern_withheld.append(dict(path=path, reason_code='conditional-footnote-overlap',
                        reason='The existing highlight plan also touches a conditionally bound footnote call'))
                    continue
                edits, after = base_plan.edits, base_candidate
                if path not in reference_fallbacks:
                    reference_fallbacks.append(path)
        final[key] = after
        edits_by_path[path] = edits

    # Every captured route, including suppressed and unselected findings, was
    # established on the intermediate graph. A second-stage edit in any
    # other route source invalidates that evidence; do not guess its binding.
    reference_cross_route_fallback = False
    baseline_changed_sources = ({row['path'] for row in baseline_proposed['by_file']
                                 if row['verification'] != 'withheld' and row['edits']
                                 and row['path'] not in excluded and row['path'] not in imported_sources}
                                if composed_references else set())
    for row in diagnostics['by_file']:
        for finding in (*row['diagnostics'], *row['suppressed']):
            dependencies = {item['path'] for item in finding['source_hashes']}
            for route in (finding['route'], *finding['content_context']):
                dependencies.update(item['path'] for item in route if not item['path'].startswith('@'))
            conflicts = (dependencies - {row['path']}) & edits_by_path.keys()
            if conflicts:
                if composed_references and not (conflicts & baseline_changed_sources):
                    reference_cross_route_fallback = True
                else:
                    raise SafetyError('A composed highlight changes another finding route source')

    def fallback_to_baseline(reason):
        # The optional reference proof may exhaust its own or combined report
        # budget. Rebuild and replay the older plan rather than dropping it.
        prior_analysis = phase_timings.get('highlight_analysis_seconds', 0) if phase_timings is not None else 0
        prior_replay = phase_timings.get('highlight_replay_seconds', 0) if phase_timings is not None else 0
        prior_intermediate = phase_timings.get('replay_and_intermediate_seconds', 0) if phase_timings is not None else 0
        fallback, fallback_report, fallback_view = stage(proof, records, profile=profile,
            partial_unsupported=partial_unsupported, withheld_records=withheld_records,
            partial_unproven_binding=partial_unproven_binding, explicit_withheld_sources=explicit_withheld_sources,
            conditional_continuations=conditional_continuations,
            conditional_adjacent_punctuation=conditional_adjacent_punctuation,
            sequence_facade_evidence=sequence_facade_evidence,
            conditional_deferred_footnotes=conditional_deferred_footnotes,
            reviewed_math=True, phase_timings=phase_timings)
        budget_reason = reason != 'cross-route'
        fallback_report.update(status='INCOMPLETE', composed_references=True,
            reference_conditions=highlight.REFERENCE_CONDITION + highlight.SCOPED_REFERENCE_CONDITION,
            reference_budget_fallback=budget_reason, reference_fallback_stage=reason,
            reference_fallback_sources=(), reference_fallback_source_count=0,
            reference_scoped_withheld_sources=(), reference_scoped_withheld_source_count=0,
            reference_candidate_routes=None,
            reference_baseline_proposed_count=baseline_proposed['previewed_diagnostics'],
            reference_baseline_eligible_count=fallback_report['candidate_count'],
            reference_baseline_preserved_count=fallback_report['candidate_count'])
        if budget_reason:
            fallback_report['reference_budget_fallback_stage'] = reason
        fallback_encoded = [json.dumps(x, ensure_ascii=False, sort_keys=True, indent=2)
                            for x in (baseline_diagnostics, baseline_proposed, fallback_report)]
        fallback_size = sum(len(raw.encode('utf-8')) + 2 * raw.count('\n') for raw in fallback_encoded)
        fallback_items = (baseline_diagnostics['report_items'] + baseline_proposed['report_items'] + 13
            + len(fallback_report['modern_withheld_sources']) + len(fallback_report['reference_conditions'])
            + sum(1 + sum(1 + len(step['edit_spans']) for step in row['stages'])
                  for row in fallback_report['sources']))
        if fallback_size > highlight.MAX_PREVIEW_BYTES or fallback_items > highlight.MAX_PREVIEW_ITEMS:
            raise SafetyError('Baseline highlight report and fallback notice exceed the report budget')
        if phase_timings is not None:
            phase_timings['highlight_analysis_seconds'] += prior_analysis
            phase_timings['highlight_replay_seconds'] += prior_replay
            phase_timings['replay_and_intermediate_seconds'] += prior_intermediate
        return fallback, fallback_report, fallback_view

    if reference_cross_route_fallback:
        return fallback_to_baseline('cross-route')
    if conditional_calls:
        project = {source.path: final['project', source.path] for source in view.context.sources}
        options = {'allow_deferred': True} if conditional_deferred_footnotes else {}
        footnote.PreviewFootnoteEvidence.validate_staged_facades(proof, project, conditional_calls, **options)
    reference_routes = None
    if composed_references and not budget_fallback:
        import document_composed_reference_routes as final_routes
        route_started = time.monotonic() if phase_timings is not None else None
        try:
            reference_routes = final_routes.verify(view, analyzer, final, edits_by_path, baseline_proposed, diagnostics)
        except final_routes.RouteBudgetExceeded:
            if phase_timings is not None:
                phase_timings['highlight_analysis_seconds'] += time.monotonic() - route_started
            return fallback_to_baseline('candidate-route')
        if phase_timings is not None:
            phase_timings['highlight_analysis_seconds'] += time.monotonic() - route_started

    # Recompute independently before authorizing even a private staging map.
    started = time.monotonic() if phase_timings is not None else None
    replay_options = {'reviewed_math': True} if budget_fallback else preview_options
    repeated_diagnostics, repeated_proposed = highlight.preview_all(analyzer, return_evidence=True, **replay_options)
    if repeated_diagnostics != diagnostics or repeated_proposed != proposed:
        raise SafetyError('Composed highlight replay changed')
    if reference_fallbacks:
        repeated_base_diagnostics, repeated_base = highlight.preview_all(analyzer, return_evidence=True, reviewed_math=True)
        if repeated_base_diagnostics != baseline_diagnostics or repeated_base != baseline_proposed:
            raise SafetyError('Composed reference fallback replay changed')
    modern_evidence._verify_routes(analyzer, view.sources, repeated_diagnostics)
    if reference_routes is not None:
        try:
            repeated_routes = final_routes.verify(view, analyzer, final, edits_by_path,
                                                  baseline_proposed, repeated_diagnostics)
        except final_routes.RouteBudgetExceeded:
            if phase_timings is not None:
                phase_timings['highlight_replay_seconds'] = time.monotonic() - started
            return fallback_to_baseline('candidate-route-replay')
        if repeated_routes != reference_routes:
            raise SafetyError('Final reference candidate route replay changed')
    if phase_timings is not None:
        phase_timings['highlight_replay_seconds'] = time.monotonic() - started
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
    baseline_eligible = baseline_preserved = 0
    if composed_references:
        for row in baseline_proposed['by_file']:
            path = row['path']
            if row['verification'] == 'withheld' or path in excluded or path in imported_sources:
                continue
            old_edits = tuple(Edit(e['start'], e['end'], e['replacement'].encode('utf-8')) for e in row['edits'])
            if path in original_conditional_calls:
                try:
                    sequence._map_conditional_calls_through_edits(view.context.source_for(path).tree,
                        original_conditional_calls[path], old_edits)
                except sequence.ConditionalFootnoteEditOverlap:
                    continue
            baseline_eligible += len(old_edits)
            present = set(edits_by_path.get(path, ()))
            baseline_preserved += sum(edit in present for edit in old_edits)
        if baseline_preserved != baseline_eligible:
            raise SafetyError('Composed reference selection lost an eligible existing highlight edit')
    unpreviewed = proposed['diagnostics_count'] - accepted
    result = dict(status='INCOMPLETE' if unpreviewed or proposed['unresolved_reasons_count']
                  or proposed['withheld_source_count'] or modern_withheld or excluded
                  or proposed.get('scoped_reference_withheld_source_count', 0)
                  or reference_fallbacks or budget_fallback else 'CONDITIONAL',
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
    if composed_references:
        result['composed_references'] = True
        result['reference_conditions'] = highlight.REFERENCE_CONDITION + highlight.SCOPED_REFERENCE_CONDITION
        result['reference_budget_fallback'] = budget_fallback
        result['reference_fallback_sources'] = tuple(reference_fallbacks)
        result['reference_fallback_source_count'] = len(reference_fallbacks)
        result['reference_scoped_withheld_sources'] = tuple(
            dict(path=row['path'], reason=row['scoped_reference_withheld']['reason'])
            for row in proposed['by_file'] if 'scoped_reference_withheld' in row)
        result['reference_scoped_withheld_source_count'] = len(result['reference_scoped_withheld_sources'])
        result['reference_candidate_routes'] = reference_routes
        result['reference_baseline_proposed_count'] = baseline_proposed['previewed_diagnostics']
        result['reference_baseline_eligible_count'] = baseline_eligible
        result['reference_baseline_preserved_count'] = baseline_preserved
    retained = [diagnostics, proposed, result]
    if composed_references:
        retained.extend((baseline_diagnostics, baseline_proposed, repeated_diagnostics, repeated_proposed))
        if reference_fallbacks:
            retained.extend((repeated_base_diagnostics, repeated_base))
    encoded = [json.dumps(x, ensure_ascii=False, sort_keys=True, indent=2) for x in retained]
    size = sum(len(raw.encode('utf-8')) + 2 * raw.count('\n') for raw in encoded)
    stage_items = (len(modern_withheld)
                   + sum(1 + sum(1 + len(step['edit_spans']) for step in row['stages']) for row in changed))
    extra_items = 0
    if composed_references:
        extra_items = (baseline_diagnostics['report_items'] + baseline_proposed['report_items']
                       + repeated_diagnostics['report_items'] + repeated_proposed['report_items'])
        if reference_fallbacks:
            extra_items += repeated_base_diagnostics['report_items'] + repeated_base['report_items']
        extra_items += len(reference_fallbacks) + len(result['reference_conditions']) + 8
        extra_items += 3 * len(result['reference_scoped_withheld_sources'])
        if reference_routes is not None:
            extra_items += len(reference_routes) + len(reference_routes['paths'])
    if (size > highlight.MAX_PREVIEW_BYTES
            or diagnostics['report_items'] + proposed['report_items'] + stage_items + extra_items > highlight.MAX_PREVIEW_ITEMS):
        if not composed_references:
            raise SafetyError('Composed highlight report budget exceeded')
        return fallback_to_baseline('combined-report')
    return MappingProxyType(final), result, view
