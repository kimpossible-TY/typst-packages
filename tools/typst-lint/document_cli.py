"""Read-only literal-include inventory and source-style checks for one document.

Math scripts have verified in-memory proposals; display source layout rules
have diagnostics and separately requested previews. None grants source or
compilation authority.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
import sys

from edit_plan import plan
from document_display import (RULE_ID as DISPLAY_GAP, STANDALONE,
                              analyze_all as analyze_display_source)
import include_context
from package_loader import PackageInputError, _absolute_root, read_snapshot_file
from script_rule import RULE_ID, analyze
from syntax_client import parse
from typst_lint import (InputError, VERSION, checked_path, digest, load_rules,
                        relative, require)

INCLUDE_BUDGET = 'book-v1'
MAX_REPORT_ITEMS = 100_000
CONDITIONAL_RULES = ('pde.text-only-highlight', 'pde.paragraph-marker-gap')


def report_document(root, entrypoint, *, rule_set='core', package_path=None,
                    preview_display_gaps=False, preview_standalone_displays=False,
                    preview_sequence=False, local_rule_hints=False, preview_conditional_gaps=False,
                    preview_conditional_punctuation=False, preview_conditional_spacers=False,
                    preview_conditional_scoped_calls=False,
                    footnote_cache_path=None,
                    footnote_punctuation_evidence=False,
                    reference_package_path=None, reference_facade=None, reference_cache_path=None,
                    reference_calls=False, reference_arguments=False, reference_catalog=None):
    """Inspect retained source bytes without reading consumer configuration.

    Literal includes inside unknown containers are inventory entries, not proof
    of execution. Each unique captured source is checked once, while repeated
    include occurrences retain their separate site chains in the report.
    """
    require(type(rule_set) is str and rule_set in ('core', 'pde'),
            'Document rule-set must be core or pde')
    require(package_path is None or rule_set == 'pde',
            'Conditional package namespace inspection requires explicit --rule-set pde')
    require(type(preview_display_gaps) is bool and (not preview_display_gaps or rule_set == 'pde'),
            'Display gap previews require explicit --rule-set pde')
    require(type(preview_standalone_displays) is bool
            and (not preview_standalone_displays or rule_set == 'pde'),
            'Standalone display previews require explicit --rule-set pde')
    require(type(preview_sequence) is bool and (not preview_sequence or rule_set == 'pde'),
            'Sequence previews require explicit --rule-set pde')
    require(type(local_rule_hints) is bool and (not local_rule_hints or rule_set == 'pde'),
            'Source-local rule hints require explicit --rule-set pde')
    require(type(preview_conditional_gaps) is bool and (not preview_conditional_gaps or rule_set == 'pde'),
            'Conditional gap previews require explicit --rule-set pde')
    require(type(preview_conditional_punctuation) is bool
            and (not preview_conditional_punctuation or rule_set == 'pde'),
            'Conditional punctuation previews require explicit --rule-set pde')
    require(type(preview_conditional_spacers) is bool
            and (not preview_conditional_spacers or rule_set == 'pde'),
            'Conditional spacer previews require explicit --rule-set pde')
    require(type(preview_conditional_scoped_calls) is bool
            and (not preview_conditional_scoped_calls or rule_set == 'pde'),
            'Conditional scoped-call previews require explicit --rule-set pde')
    require(footnote_cache_path is None or (type(footnote_cache_path) is str
            and rule_set == 'pde' and package_path is not None),
            'Conditional footnote bindings require --rule-set pde, --package-path and --footnote-cache-path')
    require(type(footnote_punctuation_evidence) is bool
            and (not footnote_punctuation_evidence or footnote_cache_path is not None),
            'Footnote punctuation evidence requires --footnote-cache-path')
    reference_requested = (reference_package_path is not None or reference_facade is not None
                           or reference_cache_path is not None)
    require(type(reference_calls) is bool and (not reference_calls or reference_requested),
            'Reference call tracing requires explicit reference provenance options')
    require(type(reference_arguments) is bool and (not reference_arguments or reference_requested),
            'Reference argument hints require explicit reference provenance options')
    require(reference_catalog is None or (type(reference_catalog) is str and reference_requested),
            'Reference catalog hints require explicit reference provenance options and a catalog path')
    require(not reference_requested or (rule_set == 'pde' and package_path is not None
            and reference_package_path is not None and type(reference_facade) is str),
            'Reference provenance requires --rule-set pde, --package-path, '
            '--reference-package-path and --reference-facade')
    if reference_requested:
        relative(reference_facade)
        require(reference_facade.endswith('.typ')
                and len(reference_facade.encode('utf-8')) <= include_context.MAX_PATH_BYTES
                and not any(ord(char) < 32 or ord(char) == 127 for char in reference_facade),
                'Reference facade must be a bounded project-relative Typst path')
    raw_root = os.fspath(root)
    require(type(raw_root) is str and len(raw_root) <= include_context.MAX_ROOT_BYTES,
            'Document root must be a bounded canonical absolute path')
    root = _absolute_root(raw_root)
    require(raw_root == str(root), 'Document root must be a canonical absolute path')
    relative(entrypoint)
    require(entrypoint.endswith('.typ')
            and len(entrypoint.encode('utf-8')) <= include_context.MAX_PATH_BYTES
            and not any(ord(char) < 32 or ord(char) == 127 for char in entrypoint),
            'Entrypoint must be a bounded canonical project-relative Typst path')
    _, source = read_snapshot_file(checked_path(root, entrypoint), include_context.MAX_SOURCE_BYTES)
    captured = include_context.load_include_context(root, ((entrypoint, source),),
                                                    budget=INCLUDE_BUDGET)
    catalog, catalog_hash = load_rules()
    enabled = [rule['id'] for rule in catalog['rules']
               if rule['id'].split('.')[0] in catalog['packs'][rule_set]]
    backends = [RULE_ID, STANDALONE, DISPLAY_GAP] if rule_set == 'pde' else [RULE_ID]
    conditional_session = None
    namespace_report = None
    if package_path is not None:
        from conditional_highlight import ConditionalHighlightAnalyzer
        from import_graph import load_import_graph
        from namespace_packages import load_namespace_packages
        namespaces = load_namespace_packages(package_path)
        imports = load_import_graph(root, ((item.path, item.source) for item in captured.sources))
        retained = {item.path: item.source for item in imports.sources}
        require(all(retained.get(item.path) == item.source for item in captured.sources),
                'Conditional namespace graph differs from the included source snapshot')
        conditional_session = ConditionalHighlightAnalyzer(imports, namespaces,
            known_rule_ids=(rule['id'] for rule in catalog['rules']),
            rule_ids=CONDITIONAL_RULES, scoped_callbacks=True)
        namespace_report = dict(package_path=namespaces.package_path,
            packages=[dict(**asdict(item.evidence), exports=list(item.exports),
                           origins=[list(origin) for origin in item.origins]) for item in namespaces.packages],
            unavailable=[asdict(item) for item in namespaces.unavailable],
            relative_sources=[dict(path=item.path, source_sha256=digest(item.source)) for item in imports.sources],
            relative_unavailable=[asdict(item) for item in imports.unavailable],
            initializer_purity_proved=False, runtime_package_selection_proved=False,
            edit_permission=False)
    findings, proposed, suppressed, files = [], [], [], []
    conditional_findings, conditional_suppressed, conditional_files = [], [], []
    gap_previews = []
    standalone_previews = []
    sequence_previews = []
    conditional_gap_previews = []
    conditional_prose_footnote_gap_previews = []
    conditional_punctuation_previews = []
    conditional_punctuation_move_previews = []
    conditional_punctuation_details = []
    conditional_spacer_previews = []
    conditional_scoped_previews = []
    conditional_detached_annotations = []
    conditional_raw_overlays = []
    local_findings, local_suppressed, local_files, local_contexts = [], [], [], []
    items = len(captured.sources) + len(captured.edges) + len(captured.occurrences) + len(captured.unresolved)
    footnote_report = None
    if footnote_cache_path is not None:
        from conditional_footnote_binding import ConditionalFootnoteBinding, _valid as valid_footnote_session
        from namespace_multiroot import load_multiroot_capture
        footnote_roots = load_multiroot_capture(package_path, footnote_cache_path)
        footnote_session = ConditionalFootnoteBinding(captured, footnote_roots)
        footnote_report = dict(package_path=footnote_roots.roots[0], cache_path=footnote_roots.roots[1],
                               by_file=[], edit_permission=False, compiler_permission=False)
        binding_batch = footnote_session.analyze_all(max_items=MAX_REPORT_ITEMS - items)
        require(binding_batch.status != 'UNAVAILABLE', 'Conditional footnote capture changed during inspection')
        for source_path, bindings in binding_batch.results:
            items += 1 + len(bindings.unresolved)
            for call in bindings.calls:
                items += (1 + len(call.facade_sources) + len(call.package_evidence)
                          + len(call.package_requests) + len(call.conditions))
            require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
            footnote_report['by_file'].append(dict(path=source_path, status=bindings.status,
                calls=[asdict(call) for call in bindings.calls], unresolved=list(bindings.unresolved),
                edit_permission=False, compiler_permission=False))
        if footnote_punctuation_evidence:
            from conditional_footnote_punctuation import analyze_all as analyze_footnote_punctuation
            punctuation_batch = analyze_footnote_punctuation(footnote_session, max_items=MAX_REPORT_ITEMS - items)
            require(punctuation_batch.status != 'UNAVAILABLE', 'Conditional footnote capture changed during inspection')
            footnote_report['punctuation_by_file'] = []
            for source_path, punctuation in punctuation_batch.results:
                items += 1 + len(punctuation.unresolved)
                for finding in (*punctuation.diagnostics, *punctuation.suppressed):
                    items += (1 + len(finding.conditions) + len(finding.call.conditions)
                              + len(finding.call.facade_sources) + len(finding.call.package_evidence)
                              + len(finding.call.package_requests))
                require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                footnote_report['punctuation_by_file'].append(dict(path=source_path, **asdict(punctuation)))
        require(valid_footnote_session(footnote_session), 'Conditional footnote capture changed during inspection')
    reference_report = None
    if reference_requested:
        from conditional_reference_provenance import ConditionalReferenceProvenance
        reference_namespaces = namespaces
        if reference_cache_path is not None:
            from namespace_multiroot import load_multiroot_capture
            reference_namespaces = load_multiroot_capture(package_path, reference_cache_path)
        analyzer = ConditionalReferenceProvenance(imports, reference_namespaces, reference_package_path)
        export = analyzer.export(reference_facade)
        catalog_capture = None
        if reference_catalog is not None:
            from conditional_reference_catalog import capture_catalog_path
            catalog_capture = capture_catalog_path(reference_catalog)
        reference_report = dict(reference_package_path=analyzer.package_path,
            reference_package_evidence=[asdict(evidence) for evidence in analyzer.package_evidence],
            facade=reference_facade, export=asdict(export), call_binding_proved=False,
            runtime_selection_proved=False, catalog_checked=False, catalog_check_complete=False,
            edit_permission=False)
        if catalog_capture is not None:
            reference_report['catalog_snapshot'] = dict(
                source_identity=catalog_capture.catalog.source_identity, sha256=catalog_capture.catalog.sha256,
                freshness_proved=False, runtime_selection_proved=False, scope='conditional-captured-calls-only')
        if reference_cache_path is not None:
            reference_report['namespace_roots'] = list(reference_namespaces.roots)
            reference_report['namespace_selections'] = [asdict(item) for item in reference_namespaces.selections]
            reference_report['namespace_unavailable'] = [asdict(item) for item in reference_namespaces.unavailable]
        items += 1 + len(export.route) + len(export.source_hashes) + len(export.package_evidence)
        if reference_calls or reference_arguments or catalog_capture is not None:
            if reference_calls:
                reference_report['calls_by_file'] = []
            if reference_arguments:
                from conditional_reference_arguments import analyze as analyze_reference_arguments
                reference_report['arguments_by_file'] = []
            if catalog_capture is not None:
                from conditional_reference_catalog import analyze as analyze_reference_catalog
                reference_report['catalog_by_file'] = []
            for selected in captured.sources:
                membership = (analyze_reference_catalog(analyzer, selected.path, catalog_capture,
                                                        catalog=catalog['rules'])
                              if catalog_capture is not None else None)
                arguments = (membership.argument_result if membership is not None else
                             analyze_reference_arguments(analyzer, selected.path, catalog=catalog['rules'])
                             if reference_arguments else None)
                traced = (membership.trace if membership is not None else
                          arguments.trace if arguments is not None else analyzer.calls(selected.path))
                if reference_calls:
                    unavailable_reasons = ((membership.unresolved if membership is not None else arguments.unresolved)
                                           if traced is None else ())
                    items += 1 + (len(unavailable_reasons) if traced is None else len(traced.unresolved))
                    if traced is not None:
                        for call in traced.calls:
                            items += (1 + len(call.route) + len(call.source_hashes) + len(call.package_evidence)
                                      + len(call.package_requests) + len(call.conditions))
                            require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                    require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                    if traced is None:
                        row = dict(path=selected.path, status='UNAVAILABLE', calls=[],
                                   unresolved=list(unavailable_reasons), edit_permission=False)
                    else:
                        row = dict(path=selected.path, **asdict(traced))
                    reference_report['calls_by_file'].append(row)
                if reference_arguments and arguments is not None:
                    items += 1 + len(arguments.unresolved)
                    for group in (arguments.diagnostics, arguments.suppressed):
                        for finding in group:
                            items += (1 + len(finding.route) + len(finding.source_hashes)
                                      + len(finding.package_evidence) + len(finding.package_requests)
                                      + len(finding.conditions))
                            require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                    require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                    row = dict(path=selected.path, status=arguments.status,
                               diagnostics=[asdict(item) for item in arguments.diagnostics],
                               suppressed=[asdict(item) for item in arguments.suppressed],
                               unresolved=list(arguments.unresolved), edit_permission=arguments.edit_permission,
                               catalog_checked=arguments.catalog_checked)
                    reference_report['arguments_by_file'].append(row)
                elif reference_arguments:
                    items += 1 + len(membership.unresolved)
                    require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                    reference_report['arguments_by_file'].append(dict(
                        path=selected.path, status='UNAVAILABLE', diagnostics=[], suppressed=[],
                        unresolved=list(membership.unresolved), edit_permission=False, catalog_checked=False))
                if membership is not None:
                    if membership.checked_calls:
                        reference_report['catalog_checked'] = True
                    items += 1 + len(membership.unresolved)
                    for group in (membership.diagnostics, membership.suppressed):
                        for finding in group:
                            items += (1 + len(finding.route) + len(finding.source_hashes)
                                      + len(finding.package_evidence) + len(finding.package_requests)
                                      + len(finding.conditions))
                            require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                    require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                    reference_report['catalog_by_file'].append(dict(
                        path=selected.path, status=membership.status,
                        diagnostics=[asdict(item) for item in membership.diagnostics],
                        suppressed=[asdict(item) for item in membership.suppressed],
                        unresolved=list(membership.unresolved), checked_calls=membership.checked_calls,
                        catalog_identity=membership.catalog_identity, catalog_sha256=membership.catalog_sha256,
                        edit_permission=False, freshness_proved=False, runtime_selection_proved=False))
                require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
        require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
    for item in captured.sources:
        gap_preview = None
        standalone_preview = None
        try:
            result = analyze(item.tree, item.path, catalog=catalog['rules'], _verify=False,
                             _allow_code_call=True)
            displays = (analyze_display_source(item.tree, item.path, catalog=catalog['rules'])
                        if rule_set == 'pde' else None)
            conditional = (conditional_session.analyze(item.tree, item.path)
                           if conditional_session is not None else None)
            if local_rule_hints:
                from document_local_rules import analyze as analyze_local
                local_reasons, local_results = analyze_local(item.tree, item.path, catalog['rules'])
            else:
                local_results = None
            if preview_conditional_gaps:
                from footnote_rules import preview_conditional_math_gaps
                from document_nested_prose_footnote_gap import preview as preview_prose_footnote_gap
                from proof_rules import preview_conditional as preview_proof_gaps
                from static_resolver import resolve
                local_resolution = resolve(item.tree, packages=())
                footnote_preview = preview_conditional_math_gaps(item.tree, item.path, local_resolution,
                                                                 catalog=catalog['rules'])
                proof_preview = preview_proof_gaps(item.tree, item.path, local_resolution, catalog=catalog['rules'])
                previews = [] if footnote_preview is None else [footnote_preview]
                if proof_preview.edits:
                    proof_plan = plan(item.source, proof_preview.edits)
                    previews.append(dict(path=item.path, rule_id='pde.proof-trailing-gap',
                        baseline_sha256=digest(item.source), candidate_sha256=digest(proof_plan.candidate()),
                        verification='conditional-parse-syntax-directives-and-proof-reanalysis',
                        runtime_binding_proved=False, render_equivalence_proved=False, edit_permission=False,
                        edits=[dict(start=edit.start, end=edit.end, replacement=edit.replacement.decode('utf-8'),
                                    rule_id='pde.proof-trailing-gap') for edit in proof_plan.edits]))
                elif any(reason.startswith('preview_unavailable:') for reason in proof_preview.skips):
                    previews.append(dict(path=item.path, rule_id='pde.proof-trailing-gap',
                        baseline_sha256=digest(item.source), candidate_sha256=None, verification='withheld',
                        runtime_binding_proved=False, render_equivalence_proved=False, edit_permission=False,
                        edits=[], reasons=[reason for reason in proof_preview.skips
                                           if reason.startswith('preview_unavailable:')]))
                for preview in previews:
                    items += 1 + len(preview['edits']) + len(preview.get('reasons', ()))
                    require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                    conditional_gap_previews.append(preview)
                prose_gap = preview_prose_footnote_gap(item.tree, item.path, catalog=catalog['rules'])
                if prose_gap is not None:
                    items += (1 + len(prose_gap['edits']) + len(prose_gap['conditions'])
                              + int(bool(prose_gap.get('reason'))))
                    require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                    conditional_prose_footnote_gap_previews.append(prose_gap)
            if preview_conditional_punctuation:
                from footnote_rules import preview_conditional_prose_punctuation
                from document_nested_footnote_punctuation import preview as preview_punctuation_move
                from document_adjacent_footnote_punctuation import analyze as analyze_punctuation_detail
                from static_resolver import resolve
                punctuation = preview_conditional_prose_punctuation(
                    item.tree, item.path, resolve(item.tree, packages=()), catalog=catalog['rules'])
                if punctuation is not None:
                    items += 1 + len(punctuation['edits'])
                    require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                    conditional_punctuation_previews.append(punctuation)
                punctuation_move = preview_punctuation_move(item.tree, item.path, catalog=catalog['rules'])
                if punctuation_move is not None:
                    items += 1 + len(punctuation_move['edits'])
                    require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                    conditional_punctuation_move_previews.append(punctuation_move)
                detail = analyze_punctuation_detail(item.tree, item.path, catalog=catalog['rules'])
                items += (1 + len(detail['diagnostics']) + len(detail['suppressed'])
                          + len(detail['unresolved']) + len(detail['conditions']))
                require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                conditional_punctuation_details.append(detail)
            if preview_conditional_spacers:
                from footnote_rules import preview_conditional_unaligned_spacers
                from static_resolver import resolve
                spacer = preview_conditional_unaligned_spacers(
                    item.tree, item.path, resolve(item.tree, packages=()), catalog=catalog['rules'])
                if spacer is not None:
                    items += 1 + len(spacer['edits'])
                    require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                    conditional_spacer_previews.append(spacer)
            if preview_conditional_scoped_calls:
                from document_conditional_scoped_calls import analyze as analyze_scoped_calls
                from document_conditional_detached_annotations import analyze as analyze_detached_annotations
                from document_conditional_raw_overlay import analyze as analyze_raw_overlay
                scoped_preview = analyze_scoped_calls(item.tree, item.path, catalog=catalog['rules'])
                items += (1 + len(scoped_preview['diagnostics']) + len(scoped_preview['suppressed'])
                          + len(scoped_preview['edits']) + len(scoped_preview['conditions'])
                          + len(scoped_preview.get('unresolved', ())))
                require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                conditional_scoped_previews.append(scoped_preview)
                detached = analyze_detached_annotations(item.source, item.path, catalog=catalog['rules'])
                items += (1 + len(detached['diagnostics']) + len(detached['suppressed'])
                          + len(detached['conditions']) + len(detached['unresolved']))
                require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                conditional_detached_annotations.append(detached)
                raw_overlay = analyze_raw_overlay(item.source, item.path, catalog=catalog['rules'])
                items += (1 + len(raw_overlay['diagnostics']) + len(raw_overlay['suppressed'])
                          + len(raw_overlay['conditions']) + len(raw_overlay['unresolved']))
                require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                conditional_raw_overlays.append(raw_overlay)
        except (InputError, OSError, ValueError) as error:
            raise InputError(f'{item.path}: {error}') from error
        # Keep unsupported baseline operands diagnostic-only, then independently
        # require complete script coverage from the candidate's fresh parse.
        edits = () if result.skips else result.edits
        candidate_plan = plan(item.source, edits)
        require(not candidate_plan.deferred, 'Document script proposals contain conflicting edits')
        items += (len(result.diagnostics) + len(result.edits) + len(result.suppressed)
                  + len(result.skips))
        if displays is not None:
            items += sum(len(value.diagnostics) + len(value.suppressed) + len(value.skips)
                         for value in displays.values())
            if preview_display_gaps and displays[DISPLAY_GAP].diagnostics:
                from document_gap_preview import preview
                gap_preview = preview(item.tree, item.path, catalog['rules'], baseline_displays=displays)
                if gap_preview is not None:
                    items += 1 + len(gap_preview['edits'])
                    gap_previews.append(gap_preview)
            if preview_standalone_displays and displays[STANDALONE].diagnostics:
                from document_standalone_preview import preview
                standalone_preview = preview(item.tree, item.path, catalog['rules'], baseline_displays=displays)
                if standalone_preview is not None:
                    items += 1 + len(standalone_preview['edits'])
                    standalone_previews.append(standalone_preview)
            if preview_sequence and (result.diagnostics or result.skips
                                     or displays[STANDALONE].diagnostics or displays[DISPLAY_GAP].diagnostics):
                from document_sequence_preview import preview
                sequence = preview(item.tree, item.path, catalog['rules'])
                if sequence is not None:
                    items += 1 + len(sequence['steps']) + sum(len(step['edits']) for step in sequence['steps'])
                    sequence_previews.append(sequence)
        if conditional is not None:
            items += sum(1 + len(conditional.reasons_for(rid)) + len(conditional.parse_errors)
                         for rid in CONDITIONAL_RULES)
            for finding in (*conditional.diagnostics, *conditional.suppressed):
                items += (1 + len(finding.source_hashes) + len(finding.package_evidence)
                          + len(finding.package_requests) + len(finding.route) + len(finding.conditions)
                          + sum(1 + len(route) for route in finding.content_context)
                          + sum(1 + len(context.parameters) for context in finding.callback_context))
        if local_results is not None:
            shared = set(local_reasons)
            items += 1 + len(local_reasons)
            items += sum(1 + len(value.diagnostics) + len(value.suppressed) + len(value.skips)
                         - sum(reason in shared for reason in value.skips)
                         + len(value.parse_errors) for value in local_results.values())
        require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
        if local_results is not None:
            local_contexts.append(dict(path=item.path, source_sha256=item.identity.source_sha256,
                                       shared_resolution_reasons=list(local_reasons)))
            for rid, value in local_results.items():
                local_findings.extend(asdict(finding) for finding in value.diagnostics)
                local_suppressed.extend(asdict(finding) for finding in value.suppressed)
                local_files.append(dict(path=item.path, source_sha256=item.identity.source_sha256,
                    rule_id=rid, status='conditional', diagnostics=len(value.diagnostics),
                    suppressed=len(value.suppressed),
                    reasons=[reason for reason in value.skips if reason not in shared],
                    parse_errors=[list(span) for span in value.parse_errors], edit_permission=False))
        if conditional is not None:
            metadata = {rule['id']: rule for rule in catalog['rules']}
            def conditional_record(finding):
                rule = metadata[finding.rule_id]
                record = asdict(finding)
                if not finding.callback_context:
                    record.pop('callback_context')
                return dict(**record, grade=rule['grade'], source=rule['source'])
            conditional_findings.extend(conditional_record(finding) for finding in conditional.diagnostics)
            conditional_suppressed.extend(conditional_record(finding) for finding in conditional.suppressed)
            for rid in CONDITIONAL_RULES:
                conditional_files.append(dict(path=item.path, source_sha256=item.identity.source_sha256,
                    rule_id=rid, status='conditional',
                    diagnostics=sum(finding.rule_id == rid for finding in conditional.diagnostics),
                    suppressed=sum(finding.rule_id == rid for finding in conditional.suppressed),
                    reasons=list(conditional.reasons_for(rid)),
                    parse_errors=[list(span) for span in conditional.parse_errors], edit_permission=False))
        reasons = list(result.skips)
        if result.skips and result.edits:
            reasons.append('Incomplete script coverage; all proposals for this source are withheld')
        if edits:
            try:
                candidate_tree = parse(candidate_plan.candidate(), item.tree.mode)
                final = analyze(candidate_tree, item.path, catalog=catalog['rules'], _verify=False,
                                _allow_code_call=True)
                verified = (not candidate_tree.nodes[0].errors and not final.parse_errors
                            and not final.diagnostics and not final.edits and not final.skips
                            and len(final.suppressed) == len(result.suppressed))
                if not verified:
                    reasons.append('Candidate script verification failed; all proposals for this source are withheld')
                    reasons.extend(f'Candidate coverage: {reason}' for reason in final.skips)
                    edits = ()
            except (InputError, OSError, ValueError) as error:
                reasons.append(f'{item.path}: candidate verification failed: {error}; all proposals are withheld')
                edits = ()
        findings.extend(asdict(finding) for finding in result.diagnostics)
        suppressed.extend(asdict(finding) for finding in result.suppressed)
        if displays is not None:
            for value in displays.values():
                findings.extend(asdict(finding) for finding in value.diagnostics)
                suppressed.extend(asdict(finding) for finding in value.suppressed)
        proposed.extend(dict(path=item.path, start=edit.start, end=edit.end,
                             replacement=edit.replacement.decode('utf-8'), rule_id=RULE_ID,
                             baseline_sha256=item.identity.source_sha256)
                        for edit in edits)
        # Export only candidates that passed the explicit fresh-parse checks.
        verified = bool(edits)
        files.append(dict(path=item.path, source_sha256=item.identity.source_sha256,
            tree_sha256=item.identity.tree_sha256, bytes=len(item.source), nodes=len(item.tree.nodes),
            rule_id=RULE_ID, status='partial' if reasons else 'evaluated',
            reasons=reasons, diagnostics=len(result.diagnostics),
            suppressed=len(result.suppressed), proposed_edits=len(edits),
            candidate_sha256=digest(candidate_plan.candidate()) if verified else None,
            candidate_verification='parse-and-rule-idempotence-checked' if verified else 'no-candidate',
            candidate_verification_rules=[RULE_ID] if verified else []))
        if displays is not None:
            for rid, value in displays.items():
                coverage = dict(path=item.path, source_sha256=item.identity.source_sha256,
                    tree_sha256=item.identity.tree_sha256, bytes=len(item.source), nodes=len(item.tree.nodes),
                    rule_id=rid, status='partial' if value.skips else 'evaluated',
                    analysis_scope='literal-markup-source-pattern', reasons=list(value.skips),
                    diagnostics=len(value.diagnostics), suppressed=len(value.suppressed),
                    proposed_edits=0, candidate_sha256=None,
                    candidate_verification='diagnostic-only', candidate_verification_rules=[])
                if preview_display_gaps and rid == DISPLAY_GAP:
                    coverage['independent_preview_edits'] = len(gap_preview['edits']) if gap_preview else 0
                    coverage['independent_preview_candidate_sha256'] = (gap_preview['candidate_sha256']
                                                                         if gap_preview else None)
                if preview_standalone_displays and rid == STANDALONE:
                    coverage['independent_preview_edits'] = len(standalone_preview['edits']) if standalone_preview else 0
                    coverage['independent_preview_candidate_sha256'] = (standalone_preview['candidate_sha256']
                                                                         if standalone_preview else None)
                files.append(coverage)
    findings.sort(key=lambda row: (row['path'], row['line'], row['column'], row['rule_id']))
    suppressed.sort(key=lambda row: (row['path'], row['line'], row['column'], row['rule_id']))
    conditional_findings.sort(key=lambda row: (row['path'], row['line'], row['column']))
    conditional_suppressed.sort(key=lambda row: (row['path'], row['line'], row['column']))
    edges = []
    for edge in captured.edges:
        record = asdict(edge)
        record['raw_request'] = edge.raw_request.decode('utf-8')
        edges.append(record)
    report = dict(schema=1, tool_version=VERSION, mode='inspect-document', status=2, phase='analysis',
        root=str(root), entrypoint=entrypoint, rule_set=rule_set, compiler_invoked=False, applied_fixes=[],
        consumer_config_loaded=False, package_resolution_performed=namespace_report is not None,
        package_resolution_scope='namespace-roots-only' if namespace_report is not None else 'not-requested',
        namespace_evidence=namespace_report,
        conditional_diagnostics=conditional_findings, conditional_suppressed=conditional_suppressed,
        conditional_suppressed_count=len(conditional_suppressed), conditional_coverage=conditional_files,
        hashes=dict(rules=catalog_hash),
        include_context=dict(budget=captured.budget,
            entrypoints=[asdict(identity) for identity in captured.entrypoints],
            sources=[dict(path=item.path, source_sha256=item.identity.source_sha256,
                          tree_sha256=item.identity.tree_sha256,
                          bytes=len(item.source), nodes=len(item.tree.nodes)) for item in captured.sources],
            edges=edges, occurrences=[asdict(occurrence) for occurrence in captured.occurrences],
            unresolved=[asdict(record) for record in captured.unresolved]),
        diagnostics=findings, suppressed=suppressed, suppressed_count=len(suppressed),
        proposed_edits=proposed, proposal_status='partial-not-applicable-transaction',
        coverage=dict(global_complete=False, enabled=enabled,
            available_backends=backends, uninspected=[rid for rid in enabled if rid not in backends],
            conditional_hint_backends=list(CONDITIONAL_RULES) if conditional_session is not None else [],
            conditional_contexts=(['root-markup', 'literal-wrapper-body', 'scoped-callback-content']
                                  if conditional_session is not None else []),
            files=files,
            unresolved_obligations=['other-style-rules', 'import-and-package-resolution',
                'include-runtime-execution', 'render-context-and-resources',
                'protected-source-transaction', 'compiler-snapshot']),
        blockers=[('Only math scripts and source display layout are inspected; full rule coverage is incomplete'
                   if rule_set == 'pde' else
                   'Only core.parenthesized-scripts is inspected; full document rule coverage is incomplete'),
                  'Literal include reachability does not establish runtime execution or imported dependencies',
                  'Proposals are read-only; no protected source transaction or compiler is invoked'])
    if preview_display_gaps:
        report['display_gap_previews'] = gap_previews
        report['display_gap_preview_status'] = 'independent-read-only-not-composed-with-script-proposals'
    if preview_standalone_displays:
        report['standalone_display_previews'] = standalone_previews
        report['standalone_display_preview_status'] = 'independent-read-only-not-composed-with-other-proposals'
    if preview_sequence:
        report['sequence_previews'] = sequence_previews
        report['sequence_preview_status'] = 'hash-chained-read-only-not-a-write-transaction'
    if preview_conditional_gaps:
        report['conditional_gap_previews'] = conditional_gap_previews
        report['conditional_prose_footnote_gap_previews'] = conditional_prose_footnote_gap_previews
        report['conditional_gap_preview_status'] = 'independent-source-only-not-composed-or-render-verified'
    if preview_conditional_punctuation:
        report['conditional_punctuation_previews'] = conditional_punctuation_previews
        report['conditional_punctuation_move_previews'] = conditional_punctuation_move_previews
        report['conditional_punctuation_details'] = dict(by_file=conditional_punctuation_details,
            duplicates_existing_hints=True, edit_permission=False, compiler_permission=False)
        report['conditional_punctuation_preview_status'] = 'independent-source-only-not-composed-or-render-verified'
    if preview_conditional_spacers:
        report['conditional_spacer_previews'] = conditional_spacer_previews
        report['conditional_spacer_preview_status'] = 'partial-independent-source-only-not-composed-or-render-verified'
    if preview_conditional_scoped_calls:
        report['conditional_scoped_calls'] = dict(by_file=conditional_scoped_previews,
            edit_permission=False, compiler_permission=False,
            scope='independent-source-pattern-not-composed-or-render-verified')
        report['conditional_detached_annotations'] = dict(by_file=conditional_detached_annotations,
            edit_permission=False, compiler_permission=False, coverage_complete=False)
        report['conditional_raw_overlays'] = dict(by_file=conditional_raw_overlays,
            edit_permission=False, compiler_permission=False, coverage_complete=False)
    if local_rule_hints:
        from document_local_rules import RULE_IDS as LOCAL_RULE_IDS
        report['local_rule_hints'] = dict(rule_ids=list(LOCAL_RULE_IDS),
            conditions=['Each file is analyzed in a standalone lexical environment; actual include-site bindings, '
                        'styles and execution are not established',
                        'This hint analysis uses no package resolution, imported namespace or consumer configuration'],
            diagnostics=sorted(local_findings, key=lambda row: (row['path'], row['line'], row['column'], row['rule_id'])),
            suppressed=sorted(local_suppressed, key=lambda row: (row['path'], row['line'], row['column'], row['rule_id'])),
            source_contexts=local_contexts, coverage=local_files, edit_permission=False)
    if reference_report is not None:
        report['reference_provenance'] = reference_report
    if footnote_report is not None:
        report['conditional_footnote_bindings'] = footnote_report
    return report


def main(arguments=None):
    parser = argparse.ArgumentParser(prog='typst-lint inspect-document', description=__doc__,
        epilog='Uses the fixed book-v1 include budget (64 files, 400,000 syntax nodes). '
               'Consumer configuration is not loaded; package namespace hints require explicit '
               '--rule-set pde and --package-path. Inline suppressions are honored. '
               'Incomplete global coverage returns 2 even with zero findings.')
    parser.add_argument('--root', required=True, help='Canonical absolute consumer root')
    parser.add_argument('--entrypoint', required=True, help='Canonical project-relative .typ entrypoint')
    parser.add_argument('--rule-set', choices=('core', 'pde'), default='core',
                        help='Explicit source checks: core (scripts) or pde (also display layout); default core')
    parser.add_argument('--package-path',
                        help='Explicit canonical package root for conditional PDE helper namespace hints')
    parser.add_argument('--footnote-cache-path',
                        help='Explicit second namespace root for conditional footnote bindings (requires --rule-set pde and --package-path)')
    parser.add_argument('--footnote-punctuation-evidence', action='store_true',
                        help='Report separately sourced conditional prose footnote punctuation hints (requires --footnote-cache-path)')
    parser.add_argument('--preview-display-gaps', action='store_true',
                        help='Preview independently verified display-gap edits (requires --rule-set pde)')
    parser.add_argument('--preview-standalone-displays', action='store_true',
                        help='Preview standalone display edits (requires --rule-set pde)')
    parser.add_argument('--preview-sequence', action='store_true',
                        help='Preview hash-chained standalone, script and gap passes (requires --rule-set pde)')
    parser.add_argument('--local-rule-hints', action='store_true',
                        help='Inspect included files independently for additional conditional PDE hints')
    parser.add_argument('--preview-conditional-gaps', action='store_true',
                        help='Preview independent proof and nested math-footnote gaps (requires --rule-set pde)')
    parser.add_argument('--preview-conditional-punctuation', action='store_true',
                        help='Preview independent prose footnote punctuation spacing and moves (requires --rule-set pde)')
    parser.add_argument('--preview-conditional-spacers', action='store_true',
                        help='Preview conditional unaligned footnote spacer removals (requires --rule-set pde)')
    parser.add_argument('--preview-conditional-scoped-calls', action='store_true',
                        help='Preview conditional scoped dictionary calls (requires --rule-set pde)')
    parser.add_argument('--reference-package-path',
                        help='Explicit canonical root containing the reviewed project-references package')
    parser.add_argument('--reference-facade',
                        help='Graph-captured project-relative .typ file whose project-ref export is traced')
    parser.add_argument('--reference-cache-path',
                        help='Explicit second namespace root, consulted after --package-path for reference tracing')
    parser.add_argument('--reference-calls', action='store_true',
                        help='Trace direct project-reference calls in each captured file (requires reference provenance)')
    parser.add_argument('--reference-arguments', action='store_true',
                        help='Inspect arguments of conditionally traced project-reference calls')
    parser.add_argument('--reference-catalog',
                        help='Explicit absolute JSON catalog snapshot for conditional reference membership')
    parser.add_argument('--format', choices=('json', 'text'), default='text')
    args = parser.parse_args(sys.argv[1:] if arguments is None else arguments)
    try:
        result = report_document(args.root, args.entrypoint, rule_set=args.rule_set,
                                 package_path=args.package_path,
                                 footnote_cache_path=args.footnote_cache_path,
                                 footnote_punctuation_evidence=args.footnote_punctuation_evidence,
                                 preview_display_gaps=args.preview_display_gaps,
                                 preview_standalone_displays=args.preview_standalone_displays,
                                 preview_sequence=args.preview_sequence,
                                 local_rule_hints=args.local_rule_hints,
                                 preview_conditional_gaps=args.preview_conditional_gaps,
                                 preview_conditional_punctuation=args.preview_conditional_punctuation,
                                 preview_conditional_spacers=args.preview_conditional_spacers,
                                 preview_conditional_scoped_calls=args.preview_conditional_scoped_calls,
                                 reference_package_path=args.reference_package_path,
                                 reference_facade=args.reference_facade,
                                 reference_cache_path=args.reference_cache_path,
                                 reference_calls=args.reference_calls,
                                 reference_arguments=args.reference_arguments,
                                 reference_catalog=args.reference_catalog)
    except (InputError, PackageInputError, OSError, ValueError) as error:
        result = dict(schema=1, tool_version=VERSION, mode='inspect-document', status=2,
                      phase='input', compiler_invoked=False, applied_fixes=[], error=str(error))
    if args.format == 'json':
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    elif 'error' in result:
        print(f"typst-lint inspect-document: blocked (exit 2): {result['error']}")
    else:
        context = result['include_context']
        print(f"typst-lint inspect-document: {len(context['sources'])} sources, "
              f"{len(context['occurrences'])} include occurrences, "
              f"{len(result['diagnostics'])} source-style findings")
        for finding in result['diagnostics']:
            print(f"{finding['path']}:{finding['line']}:{finding['column']}: "
                  f"{finding['rule_id']}: {finding['explanation']}")
        for finding in result['conditional_diagnostics']:
            print(f"{finding['path']}:{finding['line']}:{finding['column']}: "
                  f"conditional {finding['rule_id']}: {finding['message']}")
        if 'local_rule_hints' in result:
            for finding in result['local_rule_hints']['diagnostics']:
                print(f"{finding['path']}:{finding['line']}:{finding['column']}: "
                      f"source-local conditional {finding['rule_id']}: {finding['explanation']}")
        if 'reference_provenance' in result:
            reference = result['reference_provenance']
            detail = reference['export']['reason'] or 'runtime binding and initialization remain unproved'
            print(f"Reference export {reference['facade']}: {reference['export']['status']} ({detail})")
            if 'calls_by_file' in reference:
                rows = reference['calls_by_file']
                print(f"Conditional reference calls: {sum(len(row['calls']) for row in rows)} traced; "
                      f"{sum(row['status'] == 'PARTIAL' for row in rows)} partial and "
                      f"{sum(row['status'] == 'UNAVAILABLE' for row in rows)} unavailable sources")
            for name, label in (('arguments_by_file', 'Reference arguments'),
                                ('catalog_by_file', 'Reference catalog')):
                if name not in reference:
                    continue
                rows = reference[name]
                for row in rows:
                    for finding in row['diagnostics']:
                        print(f"{finding['path']}:{finding['line']}:{finding['column']}: "
                              f"conditional {finding['rule_id']}: {finding['explanation']}")
                checked = (f"; {sum(row['checked_calls'] for row in rows)} pairs checked"
                           if name == 'catalog_by_file' else '')
                print(f"{label}: {sum(len(row['diagnostics']) for row in rows)} hints{checked}; "
                      f"{sum(row['status'] == 'PARTIAL' for row in rows)} partial and "
                      f"{sum(row['status'] == 'UNAVAILABLE' for row in rows)} unavailable sources; no edit permission")
        if 'conditional_footnote_bindings' in result:
            footnotes = result['conditional_footnote_bindings']
            print(f"Conditional footnote bindings: package root {footnotes['package_path']}; "
                  f"cache root {footnotes['cache_path']}; no edit or compiler permission")
            for row in footnotes['by_file']:
                print(f"  {row['path']}: {row['status']}; {len(row['calls'])} calls")
                for reason in row['unresolved']:
                    print(f"    unresolved: {reason}")
                for call in row['calls']:
                    print(f"    conditional call bytes {call['start']}..{call['end']}; "
                          f"source sha256 {call['source_sha256']}")
                    for source in call['facade_sources']:
                        print(f"      facade {source['path']}: {source['source_sha256']}")
                    for package in call['package_evidence']:
                        print(f"      package {package['import_path']}: {package['source_identity']} "
                              f"sha256 {package['source_sha256']}")
                    for request in call['package_requests']:
                        print(f"      package request: {request}")
                    for condition in call['conditions']:
                        print(f"      condition: {condition}")
            if 'punctuation_by_file' in footnotes:
                print('Conditional footnote punctuation evidence: separate hints; no edit or compiler permission')
                for row in footnotes['punctuation_by_file']:
                    print(f"  {row['path']}: {row['status']}; {len(row['diagnostics'])} hints; "
                          f"{len(row['suppressed'])} suppressed; source sha256 {row['source_sha256']}")
                    for reason in row['unresolved']:
                        print(f"    unresolved: {reason}")
                    for label, entries in (('hint', row['diagnostics']), ('suppressed', row['suppressed'])):
                        for entry in entries:
                            finding, call = entry['diagnostic'], entry['call']
                            print(f"    {label} {finding['path']}:{finding['line']}:{finding['column']}: "
                                  f"{finding['rule_id']}: {finding['explanation']}")
                            print(f"      conditional call bytes {call['start']}..{call['end']}; "
                                  f"source sha256 {call['source_sha256']}")
                            for source in call['facade_sources']:
                                print(f"      facade {source['path']}: {source['source_sha256']}")
                            for package in call['package_evidence']:
                                print(f"      package {package['import_path']}: {package['source_identity']} "
                                      f"sha256 {package['source_sha256']}")
                            for request in call['package_requests']:
                                print(f"      package request: {request}")
                            for condition in entry['conditions']:
                                print(f"      condition: {condition}")
        if 'conditional_scoped_calls' in result:
            scoped_calls = result['conditional_scoped_calls']
            print('Conditional scoped calls: source-only, no edit or compiler permission')
            for row in scoped_calls['by_file']:
                print(f"  {row['path']}: {row['status']}; {len(row['diagnostics'])} findings; "
                      f"{len(row['suppressed'])} suppressed; {len(row['edits'])} proposed edits; "
                      f"source sha256 {row['source_sha256']}")
                for reason in row['unresolved']:
                    print(f"    unresolved: {reason}")
                for entry in row['edits']:
                    print(f"    conditional edit bytes {entry['start']}..{entry['end']}: {entry['replacement']}")
            if scoped_calls['by_file']:
                for condition in scoped_calls['by_file'][0]['conditions']:
                    print(f"  condition: {condition}")
        if 'conditional_detached_annotations' in result:
            detached = result['conditional_detached_annotations']
            print('Conditional detached annotations: diagnostic-only, no edit or compiler permission')
            for row in detached['by_file']:
                print(f"  {row['path']}: {row['status']}; {len(row['diagnostics'])} hints; "
                      f"{len(row['suppressed'])} suppressed")
                for label, entries in (('hint', row['diagnostics']), ('suppressed', row['suppressed'])):
                    for entry in entries:
                        print(f"  {label} {entry['path']}:{entry['line']}:{entry['column']}: "
                              f"{entry['rule_id']}: {entry['explanation']}")
                for reason in row['unresolved']:
                    print(f"  {row['path']}: unresolved: {reason}")
            if detached['by_file']:
                for condition in detached['by_file'][0]['conditions']:
                    print(f"  condition: {condition}")
        if 'conditional_raw_overlays' in result:
            raw_overlays = result['conditional_raw_overlays']
            print('Conditional raw overlays: diagnostic-only, no edit or compiler permission')
            for row in raw_overlays['by_file']:
                print(f"  {row['path']}: {row['status']}; {len(row['diagnostics'])} hints; "
                      f"{len(row['suppressed'])} suppressed; source sha256 {row['source_sha256']}")
                for label, entries in (('hint', row['diagnostics']), ('suppressed', row['suppressed'])):
                    for entry in entries:
                        print(f"    {label} {entry['path']}:{entry['line']}:{entry['column']}: "
                              f"{entry['rule_id']}: {entry['explanation']}")
                for reason in row['unresolved']:
                    print(f"    unresolved: {reason}")
            if raw_overlays['by_file']:
                for condition in raw_overlays['by_file'][0]['conditions']:
                    print(f"  condition: {condition}")
        if 'conditional_punctuation_details' in result:
            details = result['conditional_punctuation_details']
            print('Conditional adjacent footnote punctuation details: classify existing hints; '
                  'no edit or compiler permission')
            for row in details['by_file']:
                print(f"  {row['path']}: {row['status']}; {len(row['diagnostics'])} details; "
                      f"{len(row['suppressed'])} suppressed; source sha256 {row['source_sha256']}")
                for reason in row['unresolved']:
                    print(f"    unresolved: {reason}")
                for label, entries in (('conditional', row['diagnostics']), ('suppressed', row['suppressed'])):
                    for item in entries:
                        print(f"    {label}: footnote bytes {item['start']}..{item['end']}; "
                              f"punctuation {item['punctuation']} at byte {item['punctuation_start']}; "
                              f"preceding {item['preceding_kind']}")
            if details['by_file']:
                for condition in details['by_file'][0]['conditions']:
                    print(f"  condition: {condition}")
        if result['namespace_evidence'] is not None:
            unresolved = sum(len(row['reasons']) + len(row['parse_errors'])
                             for row in result['conditional_coverage'])
            print(f"Conditional helper inspection: {len(result['conditional_diagnostics'])} hints, "
                  f"{unresolved} unresolved rule contexts; no edit permission")
        if result['conditional_diagnostics']:
            print('Helper hints require successful module initialization and exact runtime package selection; no edit permission')
        if args.preview_display_gaps:
            previews = result['display_gap_previews']
            print(f"Display-gap previews: {sum(len(row['edits']) for row in previews)} edits; "
                  f"{sum(row['verification'] == 'withheld' for row in previews)} sources withheld; "
                  'independent of script proposals and not applied')
        if args.preview_standalone_displays:
            previews = result['standalone_display_previews']
            print(f"Standalone display previews: {sum(len(row['edits']) for row in previews)} edits; "
                  f"{sum(row['verification'] == 'withheld' for row in previews)} sources withheld; "
                  'independent of other proposals and not applied')
        if args.preview_sequence:
            previews = result['sequence_previews']
            print(f"Sequence previews: {sum(len(row['steps']) for row in previews)} passes; "
                  f"{sum(row['verification'] == 'withheld' for row in previews)} sources withheld; not applied")
        for flag, key, label in ((args.preview_conditional_gaps, 'conditional_gap_previews', 'Conditional gap'),
                                 (args.preview_conditional_gaps, 'conditional_prose_footnote_gap_previews',
                                  'Conditional prose footnote gap'),
                                 (args.preview_conditional_punctuation, 'conditional_punctuation_previews',
                                  'Conditional punctuation'),
                                 (args.preview_conditional_punctuation, 'conditional_punctuation_move_previews',
                                  'Conditional punctuation move'),
                                 (args.preview_conditional_spacers, 'conditional_spacer_previews',
                                  'Conditional spacer')):
            if flag:
                previews = result[key]
                print(f"{label} previews: {sum(len(row['edits']) for row in previews)} edits; "
                      f"{sum(row['verification'] == 'withheld' for row in previews)} sources withheld; "
                      'partial, not composed, not render-verified and not applied')
                if key == 'conditional_prose_footnote_gap_previews':
                    for row in previews:
                        if row.get('reason'):
                            print(f"  {row['path']}: withheld: {row['reason']}")
                    if previews:
                        for condition in previews[0]['conditions']:
                            print(f"  condition: {condition}")
        for blocker in result['blockers']:
            print(blocker)
        print('Global coverage incomplete (exit 2); compiler not invoked')
    return result['status']


if __name__ == '__main__':
    raise SystemExit(main())
