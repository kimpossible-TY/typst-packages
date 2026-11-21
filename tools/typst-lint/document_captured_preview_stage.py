"""Bounded captured-snapshot proposal stage and 33/33 rule inspection for preview-compile.

Operates strictly in memory over an issued IncludeContext and ResourceCapture
without filesystem rereads or mutating real files. Provides:
1. Bounded convergence, fresh parse, reanalysis, and directive-ownership
   verification for the 9 remaining proposal-capable rules, composing only
   non-overlapping verified candidates into the in-memory preview snapshot
   while withholding overlapping or unproven sources fail-closed.
2. Bounded 33/33 captured-rule inspection over captured context and resources
   while preserving global_coverage_complete=False, status=2, and
   source_apply_gate.eligible=False.
3. Sandboxed compilation of a verified in-memory candidate mapping reusing
   document_preview_compile's exact sandbox and workspace invariants.
"""
from __future__ import annotations

from dataclasses import asdict
import difflib
from types import MappingProxyType

import anchor_overlay_rules
from book_provenance import EntrypointSnapshot
import book_rules
import cancel_rules
from candidate_convergence import converge
import continuation_rules
import display_rules
import document_captured_rules
from document_captured_rules import (
    CapturedRuleResult,
    import_graph_from_resource_capture,
    normalize_rule_result,
    refusal_reasons,
)
import document_conditional_prose_spacer
import document_conditional_scoped_calls
import document_preview_compile as preview_compile
import document_resources
import document_sequence_preview
from edit_plan import Edit, conflict, plan
import flowbox_rules
import footnote_rules
import highlight_rules
import import_graph as import_graph_mod
from import_graph import ImportGraph, rebase_graph
import import_rules
import include_context
import marker_bounds_rules
import paragraph_rules
import proof_rules
import prose_marker_rules
import reference_rules
import scoped_rules
import script_rule
import source_analysis
from static_resolver import PackageSource, resolve as static_resolve
from syntax_client import AdapterError, Tree, parse
import theme_rules
from typst_lint import Diagnostic, InputError, SafetyError, digest, require

CAPTURED_PROPOSAL_RULE_IDS = (
    scoped_rules.CALL,
    anchor_overlay_rules.ANCHOR,
    scoped_rules.REFERENCE,
    reference_rules.ARGUMENTS,
    import_rules.ORDER,
    import_rules.REDUNDANT,
    flowbox_rules.RULE_ID,
    paragraph_rules.MARKER_TARGET,
    footnote_rules.PROSE,
)

DIAGNOSTIC_ONLY_RULE_IDS = (
    marker_bounds_rules.TAGS,
    anchor_overlay_rules.LOCAL,
    scoped_rules.DISPLAY,
    marker_bounds_rules.BOUNDS,
    scoped_rules.UNIQUE,
    reference_rules.CATALOG_TARGET,
    book_rules.SINGLE,
    book_rules.RESET,
    theme_rules.NO_SET,
    theme_rules.CONTEXT,
    cancel_rules.RULE_ID,
)

PREVIEW_BASELINE_RULE_IDS = (
    script_rule.RULE_ID,
    display_rules.STANDALONE,
    display_rules.CONTINUATION_GAP,
)

PREVIEW_CONDITIONAL_RULE_IDS = (
    proof_rules.RULE_ID,
    footnote_rules.GAP,
    paragraph_rules.MARKER_GAP,
    footnote_rules.PUNCT,
    footnote_rules.UNALIGNED,
    footnote_rules.ALIGNED,
    continuation_rules.RULE_ID,
    paragraph_rules.CAPITALIZATION,
    prose_marker_rules.RULE_ID,
    highlight_rules.RULE_ID,
)

VERIFICATION_LABEL = 'conditional-captured-convergence-parse-directives-and-reanalysis'
MAX_INSPECTION_DIAGNOSTICS = 10_000

PROPOSAL_CONDITIONS = (
    'Operates strictly over captured in-memory snapshots and resource captures without filesystem rereads or live source mutation.',
    'Runs bounded convergence (up to 8 passes), fresh parse, reanalysis, directive-ownership, and determinism verification per source.',
    'Overlapping, imported-dependency, or unproven sources are withheld fail-closed without touching real files.',
    'Runtime package selection, execution, and rendering equivalence remain unproved; no write or publication authority.',
)

INSPECT_ALL_CONDITIONS = (
    'Records bounded captured-snapshot static inspection across all 33 catalog rules without filesystem rereads or source mutation.',
    'The 11 diagnostic-only rules produce no source edits by design.',
    'Book ownership rules apply only to the explicit configured entrypoint snapshot; included files remain unproved as entrypoints.',
    'Bounded captured inspection does not prove global rule coverage, runtime binding, execution, or rendering equivalence; status remains 2 and source_apply_gate.eligible remains False.',
)


def _diagnostic_at(source: bytes, path: str, rule_meta: dict, offset: int, explanation: str) -> Diagnostic:
    line = source[:offset].count(b'\n') + 1
    line_start = source.rfind(b'\n', 0, offset) + 1
    column = len(source[line_start:offset].decode('utf-8')) + 1
    return Diagnostic(rule_meta['id'], rule_meta['grade'], path, line, column, explanation, rule_meta['source'])


def _extract_conditional_calls(tree: Tree, record: dict) -> tuple[int, ...]:
    """Extract all conditional footnote call offsets in post-sequence coordinates."""
    steps = record.get('steps', ())
    if not steps:
        return ()
    offsets: set[int] = set()
    offsets.update(document_sequence_preview._final_conditional_footnote_calls(
        tree, record, field='conditional_footnote_calls'))
    offsets.update(document_sequence_preview._final_conditional_footnote_calls(
        tree, record, field='conditional_aligned_spacer_calls'))
    if any(step.get('conditional_deferred_footnote_calls') for step in steps):
        deferred_record = dict(
            candidate_sha256=record['candidate_sha256'],
            steps=[
                dict(
                    baseline_sha256=step['baseline_sha256'],
                    candidate_sha256=step['candidate_sha256'],
                    edits=step['edits'],
                    **(
                        {'conditional_footnote_calls': step['conditional_deferred_footnote_calls']}
                        if step.get('conditional_deferred_footnote_calls')
                        else {}
                    ),
                )
                for step in steps
            ],
        )
        offsets.update(document_sequence_preview._final_conditional_footnote_calls(
            tree, deferred_record, field='conditional_footnote_calls'))
    return tuple(sorted(offsets))


def _footnote_call_spans(tree: Tree) -> dict[int, tuple[int, int, bytes]]:
    """Index unique literal #footnote(...) call spans by their leading '#' offset."""
    source = tree.source
    calls: dict[int, list] = {}
    for index, node in enumerate(tree.nodes):
        if node.kind != 'FuncCall' or node.start <= 0 or source[node.start - 1:node.start] != b'#':
            continue
        parts = import_graph_mod.significant(tree, index)
        if (len(parts) == 2 and tree.nodes[parts[0]].kind == 'Ident'
                and import_graph_mod.raw(tree, parts[0]) == b'footnote'):
            calls.setdefault(node.start - 1, []).append(node)
    return {
        offset: (offset, nodes[0].end, source[offset:nodes[0].end])
        for offset, nodes in calls.items()
        if len(nodes) == 1
    }


def _map_conditional_calls_from_pre_modern(
    middle_bytes: bytes,
    current_tree: Tree,
    offsets: tuple[int, ...],
    *,
    opcodes: list[tuple[str, int, int, int, int]],
    modern_edit_spans: tuple[tuple[int, int], ...] = (),
) -> tuple[int, ...]:
    """Map pre-modern conditional footnote offsets into post-modern current_tree coordinates.

    Fails closed if any modern highlight edit overlaps a conditional call span or
    changes its exact #footnote anchor in current_tree.
    """
    middle_tree = parse(middle_bytes, current_tree.mode)
    if middle_tree.nodes[0].errors:
        raise SafetyError('Pre-modern source failed parse while mapping conditional footnote calls')
    middle_calls = _footnote_call_spans(middle_tree)
    current_calls = _footnote_call_spans(current_tree)
    current_bytes = current_tree.source
    mapped: list[int] = []
    for offset in offsets:
        span = middle_calls.get(offset)
        if span is None:
            raise SafetyError('Conditional footnote call anchor is missing in pre-modern source')
        call_start, call_end, call_raw = span
        for sp_start, sp_end in modern_edit_spans:
            if (sp_start == sp_end and call_start <= sp_start <= call_end) or (sp_start < call_end and sp_end > call_start):
                raise SafetyError('Modern highlight edit touches a conditionally bound footnote call')
        new_start = None
        new_end = None
        for tag, i1, i2, j1, _j2 in opcodes:
            if tag == 'equal' and i1 <= call_start and call_end <= i2:
                new_start = j1 + (call_start - i1)
                new_end = j1 + (call_end - i1)
                break
        if new_start is None or new_end is None:
            raise SafetyError('Modern highlight edit touches a conditionally bound footnote call')
        if (new_start not in current_calls
                or current_calls[new_start] != (new_start, new_end, call_raw)
                or current_bytes[new_start:new_end] != call_raw):
            raise SafetyError('Mapped conditional footnote call anchor changed or is invalid')
        mapped.append(new_start)
    if len(set(mapped)) != len(mapped):
        raise SafetyError('Mapped conditional footnote call anchors overlap')
    return tuple(sorted(mapped))


def _analyze_nine_proposal_rules(tree: Tree, path: str, *,
                                 packages: tuple[PackageSource, ...],
                                 import_graph: ImportGraph,
                                 catalog: list | tuple,
                                 catalog_by_id: dict[str, dict],
                                 reference_catalog=None) -> tuple[dict[str, CapturedRuleResult], dict[str, tuple[str, ...]]]:
    """Analyze the 9 remaining proposal-capable rules on one parsed source without IO."""
    resolution = static_resolve(tree, packages=packages, import_graph=import_graph, source_path=path)
    scoped_res = scoped_rules.analyze(tree, path, packages=packages, import_graph=import_graph)
    anchor_res = anchor_overlay_rules.analyze(tree, path, packages=packages, import_graph=import_graph)
    ref_res = reference_rules.analyze(tree, path, resolution, reference_catalog)
    imp_res = import_rules.analyze(tree, path, graph=import_graph, _verify=False)
    fb_res = flowbox_rules.analyze(tree, path, resolution)
    para_res = paragraph_rules.analyze(tree, path, resolution, catalog=catalog)
    fn_res = footnote_rules.analyze(tree, path, resolution, resolution.spacer_proof)

    extra_withheld: dict[str, list[str]] = {}

    # 1. core.scoped-function-call: prefer direct reviewed scoped_rules, augment with
    # conditional literal scoped-call analyzer when scoped_rules produced no edits.
    call_base = normalize_rule_result(scoped_res[scoped_rules.CALL])
    if not call_base.edits and not call_base.diagnostics and not tree.nodes[0].errors:
        cond_call = document_conditional_scoped_calls.analyze(tree, path, catalog=catalog)
        if cond_call.get('status') == 'WITHHELD':
            reasons = tuple(cond_call.get('unresolved') or ('Conditional scoped-call preview withheld',))
            extra_withheld[scoped_rules.CALL] = list(reasons)
        elif cond_call.get('verification') == 'conditional-scoped-syntax-and-suppression-reanalysis' and cond_call.get('edits'):
            rule_meta = catalog_by_id[scoped_rules.CALL]
            cond_diags = tuple(
                _diagnostic_at(tree.source, path, rule_meta, entry['start'], entry['explanation'])
                for entry in cond_call.get('diagnostics', ())
            )
            cond_edits = tuple(
                Edit(row['start'], row['end'], row['replacement'].encode('utf-8'))
                for row in cond_call['edits']
            )
            call_base = CapturedRuleResult(
                diagnostics=call_base.diagnostics + cond_diags,
                edits=cond_edits,
                skips=call_base.skips,
                parse_errors=call_base.parse_errors,
                suppressed=call_base.suppressed,
            )

    # 9. pde.prose-footnote-no-spacer: prefer proven footnote_rules, augment with
    # conditional nested prose-spacer preview when footnote_rules produced no edits.
    prose_spacer_base = normalize_rule_result(fn_res[footnote_rules.PROSE])
    if not prose_spacer_base.edits and not prose_spacer_base.diagnostics and not tree.nodes[0].errors:
        cond_spacer = document_conditional_prose_spacer.preview(tree, path, catalog=catalog)
        if cond_spacer is not None:
            if cond_spacer.get('verification') == 'withheld':
                reason = str(cond_spacer.get('reason') or 'Conditional prose spacer preview withheld')
                extra_withheld[footnote_rules.PROSE] = [reason]
            elif (cond_spacer.get('verification') == 'conditional-prose-spacer-syntax-directives-and-reanalysis'
                  and cond_spacer.get('edits')):
                rule_meta = catalog_by_id[footnote_rules.PROSE]
                cond_diags = tuple(
                    _diagnostic_at(tree.source, path, rule_meta, row['start'],
                                   'Remove the forbidden spacer before a prose footnote.')
                    for row in cond_spacer['edits']
                )
                cond_edits = tuple(
                    Edit(row['start'], row['end'], row['replacement'].encode('utf-8'))
                    for row in cond_spacer['edits']
                )
                prose_spacer_base = CapturedRuleResult(
                    diagnostics=prose_spacer_base.diagnostics + cond_diags,
                    edits=cond_edits,
                    skips=prose_spacer_base.skips,
                    parse_errors=prose_spacer_base.parse_errors,
                    suppressed=prose_spacer_base.suppressed,
                )

    results = {
        scoped_rules.CALL: call_base,
        anchor_overlay_rules.ANCHOR: normalize_rule_result(anchor_res[anchor_overlay_rules.ANCHOR]),
        scoped_rules.REFERENCE: normalize_rule_result(scoped_res[scoped_rules.REFERENCE]),
        reference_rules.ARGUMENTS: normalize_rule_result(ref_res[reference_rules.ARGUMENTS]),
        import_rules.ORDER: normalize_rule_result(imp_res[import_rules.ORDER]),
        import_rules.REDUNDANT: normalize_rule_result(imp_res[import_rules.REDUNDANT]),
        flowbox_rules.RULE_ID: normalize_rule_result(fb_res),
        paragraph_rules.MARKER_TARGET: normalize_rule_result(para_res[paragraph_rules.MARKER_TARGET]),
        footnote_rules.PROSE: prose_spacer_base,
    }
    return results, {rid: tuple(reasons) for rid, reasons in extra_withheld.items()}


def _prior_result_spans(baseline_bytes: bytes, steps: list[dict]) -> tuple[tuple[int, int], ...]:
    """Compute final byte spans touched by prior sequence steps in post-sequence coordinates."""
    if not steps:
        return ()
    spans: list[tuple[int, int]] = []
    for step in steps:
        raw_edits = tuple(
            Edit(e['start'], e['end'], e['replacement'].encode('utf-8'))
            for e in step.get('edits', ())
        )
        if not raw_edits:
            continue
        # Map previously recorded spans forward through this step's edits.
        mapped: list[tuple[int, int]] = []
        for s, e in spans:
            delta_s = sum(len(ed.replacement) - (ed.end - ed.start) for ed in raw_edits if ed.end <= s)
            delta_e = sum(len(ed.replacement) - (ed.end - ed.start) for ed in raw_edits if ed.end <= e)
            mapped.append((s + delta_s, e + delta_e))
        shift = 0
        for ed in raw_edits:
            start = ed.start + shift
            end = start + len(ed.replacement)
            mapped.append((start, end))
            shift += len(ed.replacement) - (ed.end - ed.start)
        spans = mapped
    return tuple(sorted(set(spans)))


def _overlaps_spans(edits: tuple[Edit, ...], spans: tuple[tuple[int, int], ...]) -> bool:
    for ed in edits:
        for s, e in spans:
            if conflict(ed, Edit(s, e, b'')):
                return True
    return False


def stage_captured_proposals(context, snapshot, base_candidates, catalog, *,
                             entrypoint: str,
                             explicit_withheld_sources: tuple[str, ...] = (),
                             prior_withheld_paths: frozenset[str] = frozenset(),
                             prior_records: list[dict] | tuple[dict, ...] = (),
                             prior_modern_summary: dict | None = None,
                             pre_modern_sources=None,
                             reference_catalog=None,
                             max_passes: int = 8) -> tuple[MappingProxyType, dict]:
    """Verify and compose non-overlapping candidates for the 9 captured proposal rules.

    Purely in memory: never rereads the filesystem or mutates real files.
    Any overlapping, imported-dependency, or unproven source is withheld fail-closed.
    """
    include_context.assert_include_context(context)
    document_resources.assert_capture(snapshot)
    require(type(base_candidates) is MappingProxyType,
            'Captured proposals stage requires an immutable MappingProxyType base snapshot')
    expected_keys = {(item.request.root, item.request.path) for item in snapshot.files}
    require(set(base_candidates.keys()) == expected_keys,
            'Base candidate mapping does not match the issued resource capture')
    entry_item = context.source_for(entrypoint)
    require(entry_item is not None, 'Configured entrypoint is absent from captured include context')

    catalog_by_id = {row['id']: row for row in catalog}
    pkg_bundle = document_captured_rules.packages_from_resource_capture(snapshot)
    selected_paths = tuple(item.path for item in context.sources)
    working_graph = import_graph_from_resource_capture(
        snapshot, selected_paths, source_overrides=base_candidates)
    imported_targets = frozenset(edge.target for edge in working_graph.edges)

    excluded = frozenset(explicit_withheld_sources) | frozenset(prior_withheld_paths)
    record_by_path = {row['path']: row for row in (prior_records or ())}
    modern_by_path = {row['path']: row for row in (prior_modern_summary or {}).get('sources', ())}

    conditional_calls_by_path = {}
    for row in (prior_records or ()):
        src_item = context.source_for(row['path'])
        if src_item is not None:
            offsets = _extract_conditional_calls(src_item.tree, row)
            if offsets:
                conditional_calls_by_path[row['path']] = offsets

    final = dict(base_candidates)
    verified_sources = []
    withheld_sources = []
    candidate_by_rule = {rid: 0 for rid in CAPTURED_PROPOSAL_RULE_IDS}
    withheld_by_rule = {rid: 0 for rid in CAPTURED_PROPOSAL_RULE_IDS}
    planned_edits_total = 0

    for item in context.sources:
        path = item.path
        key = ('project', path)
        current_bytes = base_candidates[key]
        current_sha = digest(current_bytes)

        if path in excluded:
            withheld_sources.append(dict(
                path=path,
                baseline_sha256=current_sha,
                rule_id=None,
                reason_code='prior-withheld-source',
                reason='Source was excluded or withheld by an earlier preview stage',
            ))
            continue

        try:
            current_tree = item.tree if current_bytes == item.source else parse(current_bytes)
        except AdapterError as error:
            withheld_sources.append(dict(
                path=path,
                baseline_sha256=current_sha,
                rule_id=None,
                reason_code='parse-error',
                reason=f'Malformed source syntax; captured proposals withheld: {error}',
            ))
            continue

        if current_tree.nodes[0].errors:
            withheld_sources.append(dict(
                path=path,
                baseline_sha256=current_sha,
                rule_id=None,
                reason_code='parse-error',
                reason='Malformed source syntax; captured proposals withheld',
            ))
            continue

        initial_results, extra_withheld = _analyze_nine_proposal_rules(
            current_tree, path,
            packages=pkg_bundle.sources,
            import_graph=working_graph,
            catalog=catalog,
            catalog_by_id=catalog_by_id,
            reference_catalog=reference_catalog,
        )

        # Record any individual rule diagnostics that lack proven candidate edits.
        for rid in CAPTURED_PROPOSAL_RULE_IDS:
            res = initial_results[rid]
            if rid in extra_withheld:
                withheld_by_rule[rid] += 1
                withheld_sources.append(dict(
                    path=path,
                    baseline_sha256=current_sha,
                    rule_id=rid,
                    reason_code='unproven-captured-proposal',
                    reason=extra_withheld[rid][0],
                    reasons=list(extra_withheld[rid]),
                ))
            elif res.diagnostics and not res.edits:
                refusals = refusal_reasons(rid, res.skips)
                primary = refusals[0] if refusals else 'Rule reported findings without eligible candidate edits'
                withheld_by_rule[rid] += 1
                withheld_sources.append(dict(
                    path=path,
                    baseline_sha256=current_sha,
                    rule_id=rid,
                    reason_code='unproven-captured-proposal',
                    reason=primary,
                    reasons=list(refusals),
                ))

        initial_edits = tuple(
            edit for rid in CAPTURED_PROPOSAL_RULE_IDS for edit in initial_results[rid].edits
        )
        if not initial_edits:
            continue

        proposing_rules = tuple(rid for rid in CAPTURED_PROPOSAL_RULE_IDS if initial_results[rid].edits)

        # Fail closed if editing an imported module could invalidate other sources' binding graphs.
        if path in imported_targets:
            for rid in proposing_rules:
                withheld_by_rule[rid] += 1
            withheld_sources.append(dict(
                path=path,
                baseline_sha256=current_sha,
                rule_id=proposing_rules[0] if len(proposing_rules) == 1 else None,
                rule_ids=list(proposing_rules),
                reason_code='imported-source-dependency',
                reason='Captured proposal would modify an imported module used by another source',
            ))
            continue

        # Fail closed on overlapping edits among the 9 rules themselves.
        initial_plan = plan(current_bytes, initial_edits)
        if initial_plan.deferred or not initial_plan.edits:
            for rid in proposing_rules:
                withheld_by_rule[rid] += 1
            withheld_sources.append(dict(
                path=path,
                baseline_sha256=current_sha,
                rule_id=proposing_rules[0] if len(proposing_rules) == 1 else None,
                rule_ids=list(proposing_rules),
                reason_code='overlapping-captured-proposals',
                reason='Captured proposal edits overlap or conflict',
            ))
            continue

        # Fail closed on overlap with prior preview stage edits on this source.
        prior_spans: list[tuple[int, int]] = []
        if path in record_by_path:
            prior_spans.extend(_prior_result_spans(item.source, record_by_path[path].get('steps', ())))
        modern_edit_spans: list[tuple[int, int]] = []
        if path in modern_by_path:
            for stage_entry in modern_by_path[path].get('stages', ()):
                if stage_entry.get('rule_id') == highlight_rules.RULE_ID:
                    for sp in stage_entry.get('edit_spans', ()):
                        modern_edit_spans.append((sp['start'], sp['end']))
        middle_bytes = (pre_modern_sources[key]
                        if pre_modern_sources is not None and key in pre_modern_sources
                        else None)
        modern_opcodes = None
        if middle_bytes is not None and middle_bytes != current_bytes:
            modern_opcodes = difflib.SequenceMatcher(
                None, middle_bytes, current_bytes, autojunk=False).get_opcodes()
            mapped_prior: list[tuple[int, int]] = []
            for s, e in prior_spans:
                new_s, new_e = s, e
                for tag, i1, i2, j1, j2 in modern_opcodes:
                    if i1 <= s <= i2:
                        new_s = j1 + (s - i1) if tag == 'equal' else j1
                    if i1 <= e <= i2:
                        new_e = j1 + (e - i1) if tag == 'equal' else j2
                mapped_prior.append((new_s, new_e))
            prior_spans = mapped_prior
            for tag, _i1, _i2, j1, j2 in modern_opcodes:
                if tag != 'equal':
                    prior_spans.append((j1, j2))
        elif middle_bytes is None and modern_edit_spans:
            prior_spans.extend(modern_edit_spans)
        if prior_spans and _overlaps_spans(initial_plan.edits, tuple(prior_spans)):
            for rid in proposing_rules:
                withheld_by_rule[rid] += 1
            withheld_sources.append(dict(
                path=path,
                baseline_sha256=current_sha,
                rule_id=proposing_rules[0] if len(proposing_rules) == 1 else None,
                rule_ids=list(proposing_rules),
                reason_code='prior-stage-edit-overlap',
                reason='Captured proposal overlaps an edit produced by an earlier preview stage',
            ))
            continue

        # Run bounded convergence + fresh parse + reanalysis + directive ownership + determinism checks.
        def analyze_candidate(checked_tree: Tree):
            if checked_tree.mode != current_tree.mode:
                raise SafetyError('Candidate context changed parser mode')
            rebased_graph = rebase_graph(working_graph, path, current_bytes, checked_tree.source)
            if (type(rebased_graph) is not ImportGraph
                    or rebased_graph.source_for(path) is None
                    or rebased_graph.source_for(path).source != checked_tree.source):
                raise SafetyError('Candidate lost immutable relative import graph context')
            res_map, cond_withheld = _analyze_nine_proposal_rules(
                checked_tree, path,
                packages=pkg_bundle.sources,
                import_graph=rebased_graph,
                catalog=catalog,
                catalog_by_id=catalog_by_id,
                reference_catalog=reference_catalog,
            )
            if cond_withheld:
                raise SafetyError('Candidate reanalysis triggered a conditional withholding refusal')
            return res_map

        def validate_step(before_tree, before, after_tree, after, step):
            if step.deferred:
                raise SafetyError('Captured proposal edits overlap or conflict')
            source_analysis._validate_step(before_tree, before, after_tree, after, step)

        def validate_final(candidate):
            source_analysis._validate_final(current_tree, initial_results, frozenset(), candidate)

        try:
            candidate = converge(
                current_tree,
                initial_results,
                analyze_candidate,
                validate_step,
                validate_final,
                movement_proofs=source_analysis._movement_proofs,
                max_passes=max_passes,
            )
            if candidate.content == current_bytes or not candidate.edits:
                raise SafetyError('Captured proposal convergence produced no effective edits')
            final_edits = tuple(attr.edit for attr in candidate.edits)
            if prior_spans and _overlaps_spans(final_edits, tuple(prior_spans)):
                raise SafetyError('Converged captured proposal overlaps an earlier preview stage edit')
            if path in conditional_calls_by_path:
                mapped_calls = conditional_calls_by_path[path]
                if modern_opcodes is not None:
                    mapped_calls = _map_conditional_calls_from_pre_modern(
                        middle_bytes,
                        current_tree,
                        mapped_calls,
                        opcodes=modern_opcodes,
                        modern_edit_spans=tuple(modern_edit_spans),
                    )
                elif middle_bytes is None and modern_edit_spans:
                    raise SafetyError(
                        'Pre-modern source snapshot is required to map conditional footnote calls across modern highlight edits'
                    )
                try:
                    document_sequence_preview._map_conditional_calls_through_edits(
                        current_tree, mapped_calls, final_edits)
                except document_sequence_preview.ConditionalFootnoteEditOverlap as error:
                    raise SafetyError('Captured proposal touches a conditionally bound footnote call') from error
            for attr in candidate.edits:
                attr.edit.replacement.decode('utf-8')
        except (AdapterError, SafetyError, InputError, ValueError, UnicodeDecodeError) as error:
            for rid in proposing_rules:
                withheld_by_rule[rid] += 1
            reason_code = ('overlapping-captured-proposals'
                           if 'overlap' in str(error).lower() or 'conflict' in str(error).lower()
                           else 'verification-failed')
            withheld_sources.append(dict(
                path=path,
                baseline_sha256=current_sha,
                rule_id=proposing_rules[0] if len(proposing_rules) == 1 else None,
                rule_ids=list(proposing_rules),
                reason_code=reason_code,
                reason=str(error),
            ))
            continue

        applied_rules = tuple(sorted({rid for attr in candidate.edits for rid in attr.rule_ids}))
        for rid in applied_rules:
            candidate_by_rule[rid] += 1
        planned_edits_total += len(candidate.edits)
        final[key] = candidate.content
        verified_sources.append(dict(
            path=path,
            baseline_sha256=current_sha,
            candidate_sha256=digest(candidate.content),
            verification=VERIFICATION_LABEL,
            passes=candidate.passes,
            rule_ids=list(applied_rules),
            edits=[
                dict(
                    start=attr.edit.start,
                    end=attr.edit.end,
                    replacement=attr.edit.replacement.decode('utf-8'),
                    rule_id=attr.primary_rule_id,
                    rule_ids=list(attr.rule_ids),
                )
                for attr in candidate.edits
            ],
            runtime_binding_proved=False,
            execution_proved=False,
            render_equivalence_proved=False,
            edit_permission=False,
            compiler_permission=False,
        ))

    include_context.assert_include_context(context)
    document_resources.assert_capture(snapshot)

    summary = dict(
        status='INCOMPLETE' if withheld_sources else 'CONDITIONAL',
        rule_ids=list(CAPTURED_PROPOSAL_RULE_IDS),
        rule_count=len(CAPTURED_PROPOSAL_RULE_IDS),
        source_count=len(verified_sources),
        candidate_count=len(verified_sources),
        planned_edits=planned_edits_total,
        withheld_count=len(withheld_sources),
        candidate_count_by_rule=candidate_by_rule,
        withheld_count_by_rule=withheld_by_rule,
        sources=verified_sources,
        withheld=withheld_sources,
        package_roots=list(pkg_bundle.roots),
        package_evidence=[asdict(e) for e in pkg_bundle.evidence],
        package_unavailable=[asdict(u) for u in pkg_bundle.unavailable],
        import_graph_captured=True,
        conditions=list(PROPOSAL_CONDITIONS),
        runtime_binding_proved=False,
        execution_proved=False,
        render_equivalence_proved=False,
        coverage_complete=False,
        edit_permission=False,
        compiler_permission=False,
    )
    return MappingProxyType(final), summary


def _analyze_all_33_rules(tree: Tree, path: str, *,
                          packages: tuple[PackageSource, ...],
                          import_graph: ImportGraph,
                          entrypoint: EntrypointSnapshot,
                          catalog: list | tuple,
                          reference_catalog=None) -> dict[str, CapturedRuleResult]:
    """Run all 33 catalog rules on one parsed source purely in memory, stripping edits."""
    results: dict[str, CapturedRuleResult] = {}
    results[script_rule.RULE_ID] = normalize_rule_result(
        script_rule.analyze(tree, path, catalog=catalog, _verify=False, _allow_code_call=True), strip_edits=True)
    resolution = static_resolve(tree, packages=packages, import_graph=import_graph, source_path=path)
    for rid, res in reference_rules.analyze(tree, path, resolution, reference_catalog).items():
        results[rid] = normalize_rule_result(res, strip_edits=True)
    for rid, res in theme_rules.analyze(tree, path, resolution).items():
        results[rid] = normalize_rule_result(res, strip_edits=True)
    for rid, res in scoped_rules.analyze(tree, path, packages=packages, import_graph=import_graph).items():
        results[rid] = normalize_rule_result(res, strip_edits=True)
    for rid, res in marker_bounds_rules.analyze(tree, path, packages=packages, import_graph=import_graph).items():
        results[rid] = normalize_rule_result(res, strip_edits=True)
    for rid, res in anchor_overlay_rules.analyze(tree, path, packages=packages, import_graph=import_graph).items():
        results[rid] = normalize_rule_result(res, strip_edits=True)
    for rid, res in book_rules.analyze(tree, path, resolution, entrypoint=entrypoint).items():
        results[rid] = normalize_rule_result(res, strip_edits=True)
    for rid, res in import_rules.analyze(tree, path, graph=import_graph, _verify=False).items():
        results[rid] = normalize_rule_result(res, strip_edits=True)
    for rid, res in paragraph_rules.analyze(tree, path, resolution, catalog=catalog).items():
        results[rid] = normalize_rule_result(res, strip_edits=True)
    results[proof_rules.RULE_ID] = normalize_rule_result(proof_rules.analyze(tree, path, resolution), strip_edits=True)
    results[prose_marker_rules.RULE_ID] = normalize_rule_result(
        prose_marker_rules.analyze(tree, path, resolution, allow_followup_casing=True), strip_edits=True)
    results[highlight_rules.RULE_ID] = normalize_rule_result(highlight_rules.analyze(tree, path, resolution), strip_edits=True)
    results[cancel_rules.RULE_ID] = normalize_rule_result(cancel_rules.analyze(tree, path, resolution), strip_edits=True)
    results[flowbox_rules.RULE_ID] = normalize_rule_result(flowbox_rules.analyze(tree, path, resolution), strip_edits=True)
    for rid, res in display_rules.analyze(tree, path, resolution).items():
        results[rid] = normalize_rule_result(res, strip_edits=True)
    results[continuation_rules.RULE_ID] = normalize_rule_result(continuation_rules.analyze(tree, path, resolution), strip_edits=True)
    for rid, res in footnote_rules.analyze(tree, path, resolution, resolution.spacer_proof).items():
        results[rid] = normalize_rule_result(res, strip_edits=True)
    return results


def _rule_category(rule_id: str) -> str:
    if rule_id in PREVIEW_BASELINE_RULE_IDS:
        return 'preview-baseline'
    if rule_id in PREVIEW_CONDITIONAL_RULE_IDS:
        return 'preview-conditional'
    if rule_id in CAPTURED_PROPOSAL_RULE_IDS:
        return 'captured-proposal'
    if rule_id in DIAGNOSTIC_ONLY_RULE_IDS:
        return 'diagnostic-only'
    raise SafetyError(f'Unclassified catalog rule: {rule_id}')


def inspect_all_captured_rules(context, snapshot, catalog, *,
                               entrypoint: str,
                               reference_catalog=None,
                               max_diagnostics: int = MAX_INSPECTION_DIAGNOSTICS) -> dict:
    """Record bounded 33/33 captured-rule inspection over captured context without IO."""
    include_context.assert_include_context(context)
    document_resources.assert_capture(snapshot)
    entry_item = context.source_for(entrypoint)
    require(entry_item is not None, 'Configured entrypoint is absent from captured include context')
    entry_snapshot = EntrypointSnapshot(entry_item.path, entry_item.source)

    catalog_rule_ids = tuple(row['id'] for row in catalog)
    pkg_bundle = document_captured_rules.packages_from_resource_capture(snapshot)
    selected_paths = tuple(item.path for item in context.sources)
    import_graph = document_captured_rules.import_graph_from_resource_capture(snapshot, selected_paths)

    diagnostics_by_rule = {rid: 0 for rid in catalog_rule_ids}
    suppressed_by_rule = {rid: 0 for rid in catalog_rule_ids}
    parse_errors_by_rule = {rid: 0 for rid in catalog_rule_ids}
    sources_inspected = []
    diagnostics_rows = []
    suppressed_rows = []
    diagnostics_truncated = False
    suppressed_truncated = False

    for item in context.sources:
        results = _analyze_all_33_rules(
            item.tree, item.path,
            packages=pkg_bundle.sources,
            import_graph=import_graph,
            entrypoint=entry_snapshot,
            catalog=catalog,
            reference_catalog=reference_catalog,
        )
        require(set(results.keys()) == set(catalog_rule_ids),
                'Captured 33-rule inspection did not match catalog rule membership')
        source_diags = 0
        source_suppressed = 0
        for rid in catalog_rule_ids:
            val = results[rid]
            diagnostics_by_rule[rid] += len(val.diagnostics)
            suppressed_by_rule[rid] += len(val.suppressed)
            parse_errors_by_rule[rid] += len(val.parse_errors)
            source_diags += len(val.diagnostics)
            source_suppressed += len(val.suppressed)
            for finding in val.diagnostics:
                if len(diagnostics_rows) < max_diagnostics:
                    diagnostics_rows.append(asdict(finding))
                else:
                    diagnostics_truncated = True
            for finding in val.suppressed:
                if len(suppressed_rows) < max_diagnostics:
                    suppressed_rows.append(asdict(finding))
                else:
                    suppressed_truncated = True
        sources_inspected.append(dict(
            path=item.path,
            source_sha256=item.identity.source_sha256,
            diagnostics=source_diags,
            suppressed=source_suppressed,
        ))

    diagnostics_rows.sort(key=lambda row: (row['path'], row['line'], row['column'], row['rule_id']))
    suppressed_rows.sort(key=lambda row: (row['path'], row['line'], row['column'], row['rule_id']))
    total_diagnostics = sum(diagnostics_by_rule.values())
    total_suppressed = sum(suppressed_by_rule.values())
    categories = {rid: _rule_category(rid) for rid in catalog_rule_ids}

    include_context.assert_include_context(context)
    document_resources.assert_capture(snapshot)

    return dict(
        status='CONDITIONAL',
        catalog_rule_count=len(catalog_rule_ids),
        inspected_rule_count=len(catalog_rule_ids),
        rule_ids=list(catalog_rule_ids),
        rule_categories=categories,
        proposal_capable_rule_ids=list((*PREVIEW_BASELINE_RULE_IDS, *PREVIEW_CONDITIONAL_RULE_IDS,
                                        *CAPTURED_PROPOSAL_RULE_IDS)),
        captured_proposal_rule_ids=list(CAPTURED_PROPOSAL_RULE_IDS),
        diagnostic_only_rule_ids=list(DIAGNOSTIC_ONLY_RULE_IDS),
        diagnostic_only_rule_count=len(DIAGNOSTIC_ONLY_RULE_IDS),
        source_count=len(context.sources),
        coverage_entries=len(context.sources) * len(catalog_rule_ids),
        entrypoint=entry_item.path,
        entrypoint_sha256=entry_item.identity.source_sha256,
        import_graph_captured=True,
        package_roots=list(pkg_bundle.roots),
        package_evidence=[asdict(e) for e in pkg_bundle.evidence],
        package_unavailable=[asdict(u) for u in pkg_bundle.unavailable],
        diagnostics_count=total_diagnostics,
        suppressed_count=total_suppressed,
        diagnostics_by_rule=diagnostics_by_rule,
        suppressed_by_rule=suppressed_by_rule,
        parse_errors_by_rule=parse_errors_by_rule,
        diagnostics=diagnostics_rows,
        suppressed=suppressed_rows,
        diagnostics_truncated=diagnostics_truncated,
        suppressed_truncated=suppressed_truncated,
        sources=sources_inspected,
        conditions=list(INSPECT_ALL_CONDITIONS),
        global_coverage_complete=False,
        coverage_complete=False,
        runtime_binding_proved=False,
        execution_proved=False,
        render_equivalence_proved=False,
        edit_permission=False,
        compiler_permission=False,
    )


def compile_staged_preview(context, snapshot, candidates, *, entrypoint, deadline_seconds=120.0,
                           cancel_event=None, expected_dependencies=None, expected_baseline_sha256=None,
                           starts=None, phase_timings=None, modern_highlight_evidence=None,
                           modern_summary=None, composed_view=None,
                           composed_modern_evidence=None) -> preview_compile.PreviewCompileResult:
    """Compile baseline vs verified in-memory candidate mapping under macOS sandbox-exec.

    Delegates directly to document_preview_compile.compile_staged_candidates so
    sandbox profile, workspace guard, font checks, dependency checks, and
    evidence validation invariants stay single-sourced.
    """
    if modern_summary is None and type(modern_highlight_evidence) is dict:
        modern_summary = modern_highlight_evidence
        modern_highlight_evidence = None
    return preview_compile.compile_staged_candidates(
        context,
        snapshot,
        candidates,
        entrypoint=entrypoint,
        deadline_seconds=deadline_seconds,
        cancel_event=cancel_event,
        expected_dependencies=expected_dependencies,
        expected_baseline_sha256=expected_baseline_sha256,
        starts=starts,
        original_only=False,
        phase_timings=phase_timings,
        modern_highlight_evidence=modern_highlight_evidence,
        modern_summary=modern_summary,
        composed_view=composed_view,
        composed_modern_evidence=composed_modern_evidence,
    )
