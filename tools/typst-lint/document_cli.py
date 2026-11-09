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
MAX_MODERN_REPORT_BYTES = 16 * 1024 * 1024
CONDITIONAL_RULES = ('pde.text-only-highlight', 'pde.paragraph-marker-gap')


def report_document(root, entrypoint, *, rule_set='core', package_path=None,
                    preview_display_gaps=False, preview_standalone_displays=False,
                    preview_sequence=False, local_rule_hints=False, preview_conditional_gaps=False,
                    inspect_capitalization_review=False,
                    preview_conditional_punctuation=False, preview_conditional_spacers=False,
                    preview_conditional_scoped_calls=False,
                    preview_conditional_continuations=False,
                    preview_conditional_capitalization=False,
                    preview_conditional_paragraph_gaps=False,
                    preview_conditional_block_marker_gaps=False,
                    preview_conditional_prose_spacers=False,
                    preview_conditional_aligned_spacers=False,
                    preview_conditional_prose_markers=False,
                    import_obligations=False,
                    inspect_include_forwarding=False,
                    footnote_cache_path=None,
                    footnote_punctuation_evidence=False,
                    modern_highlight_evidence=False,
                    cetz_annotation_manifest=None,
                    preview_conditional_modern_highlights=False,
                    inspect_scoped_parent_patterns=False,
                    preview_conditional_modern_scoped_references=False,
                    preview_conditional_modern_math_highlights=False,
                    preview_conditional_modern_combined_highlights=False,
                    preview_conditional_modern_literal_footnotes=False,
                    preview_conditional_modern_scoped_math=None,
                    preview_conditional_modern_scoped_union=None,
                    inspect_scoped_parent_transfer=None,
                    preview_conditional_modern_parent_reference=None,
                    preview_conditional_modern_parent_union=None,
                    attest_parent_mannot=False,
                    reference_package_path=None, reference_facade=None, reference_cache_path=None,
                    reference_calls=False, reference_arguments=False, reference_catalog=None):
    """Inspect retained source bytes without reading consumer configuration.

    Literal includes inside unknown containers are inventory entries, not proof
    of execution. Each unique captured source is checked once, while repeated
    include occurrences retain their separate site chains in the report.
    """
    require(type(rule_set) is str and rule_set in ('core', 'pde'),
            'Document rule-set must be core or pde')
    require(type(import_obligations) is bool, 'Import obligations must be explicitly requested')
    require(type(inspect_include_forwarding) is bool and (not inspect_include_forwarding
            or (rule_set == 'pde' and package_path is not None and footnote_cache_path is not None)),
            'Include forwarding inspection requires PDE and both explicit namespace roots')
    require(type(inspect_capitalization_review) is bool and (not inspect_capitalization_review or rule_set == 'pde'),
            'Capitalization review requires explicit --rule-set pde')
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
    require(type(preview_conditional_continuations) is bool
            and (not preview_conditional_continuations or rule_set == 'pde'),
            'Conditional continuation previews require explicit --rule-set pde')
    require(type(preview_conditional_capitalization) is bool
            and (not preview_conditional_capitalization or rule_set == 'pde'),
            'Conditional capitalization previews require explicit --rule-set pde')
    require(type(preview_conditional_paragraph_gaps) is bool
            and (not preview_conditional_paragraph_gaps or rule_set == 'pde'),
            'Conditional paragraph gap previews require explicit --rule-set pde')
    require(type(preview_conditional_block_marker_gaps) is bool
            and (not preview_conditional_block_marker_gaps or rule_set == 'pde'),
            'Conditional block marker gap previews require explicit --rule-set pde')
    require(type(preview_conditional_prose_spacers) is bool
            and (not preview_conditional_prose_spacers or rule_set == 'pde'),
            'Conditional prose spacer previews require explicit --rule-set pde')
    require(type(preview_conditional_aligned_spacers) is bool
            and (not preview_conditional_aligned_spacers or rule_set == 'pde'),
            'Conditional aligned spacer previews require explicit --rule-set pde')
    require(type(preview_conditional_prose_markers) is bool
            and (not preview_conditional_prose_markers or rule_set == 'pde'),
            'Conditional prose marker previews require explicit --rule-set pde')
    require(footnote_cache_path is None or (type(footnote_cache_path) is str
            and rule_set == 'pde' and package_path is not None),
            'Conditional footnote bindings require --rule-set pde, --package-path and --footnote-cache-path')
    require(type(footnote_punctuation_evidence) is bool
            and (not footnote_punctuation_evidence or footnote_cache_path is not None),
            'Footnote punctuation evidence requires --footnote-cache-path')
    require(type(modern_highlight_evidence) is bool
            and (not modern_highlight_evidence or footnote_cache_path is not None),
            'Modern highlight evidence requires --rule-set pde, --package-path and --footnote-cache-path')
    require(cetz_annotation_manifest is None or (type(cetz_annotation_manifest) is str
            and preview_conditional_scoped_calls and footnote_cache_path is not None),
            'CeTZ annotation manifest requires --preview-conditional-scoped-calls, --package-path and --footnote-cache-path')
    require(type(inspect_scoped_parent_patterns) is bool
            and (not inspect_scoped_parent_patterns or modern_highlight_evidence),
            'Scoped parent pattern inspection requires --modern-highlight-evidence')
    require(type(preview_conditional_modern_highlights) is bool
            and (not preview_conditional_modern_highlights or modern_highlight_evidence),
            'Modern highlight previews require --modern-highlight-evidence')
    require(type(preview_conditional_modern_scoped_references) is bool
            and (not preview_conditional_modern_scoped_references or preview_conditional_modern_highlights),
            'Scoped reference previews require --preview-conditional-modern-highlights')
    require(type(preview_conditional_modern_math_highlights) is bool
            and (not preview_conditional_modern_math_highlights or modern_highlight_evidence),
            'Modern math highlight previews require --modern-highlight-evidence')
    require(type(preview_conditional_modern_combined_highlights) is bool
            and (not preview_conditional_modern_combined_highlights or modern_highlight_evidence),
            'Combined modern highlight previews require --modern-highlight-evidence')
    require(not preview_conditional_modern_combined_highlights
            or not (preview_conditional_modern_highlights or preview_conditional_modern_math_highlights),
            'Combined modern highlight previews cannot be mixed with separate modern highlight previews')
    require(type(preview_conditional_modern_literal_footnotes) is bool
            and (not preview_conditional_modern_literal_footnotes or preview_conditional_modern_combined_highlights),
            'Literal footnote highlight previews require --preview-conditional-modern-combined-highlights')
    scoped_math_location = None
    if preview_conditional_modern_scoped_math is not None:
        require(preview_conditional_modern_combined_highlights
                and type(preview_conditional_modern_scoped_math) in (tuple, list)
                and len(preview_conditional_modern_scoped_math) == 2,
                'Scoped math preview requires combined highlights and PATH START')
        scoped_path, scoped_start = preview_conditional_modern_scoped_math
        require(type(scoped_path) is str and type(scoped_start) in (str, int)
                and type(scoped_start) is not bool and str(scoped_start).isascii()
                and str(scoped_start).isdigit() and len(str(scoped_start)) <= 12,
                'Scoped math START must be a nonnegative source byte offset')
        scoped_math_location = (scoped_path, int(scoped_start))
    scoped_union_targets = None
    if preview_conditional_modern_scoped_union is not None:
        require(preview_conditional_modern_combined_highlights
                and type(preview_conditional_modern_scoped_union) in (tuple, list)
                and 0 < len(preview_conditional_modern_scoped_union) <= 4,
                'Scoped union preview requires combined highlights and up to four PATH START pairs')
        grouped = {}
        for pair in preview_conditional_modern_scoped_union:
            require(type(pair) in (tuple, list) and len(pair) == 2,
                    'Each scoped union target requires PATH START')
            path, start = pair
            require(type(path) is str and len(path.encode('utf-8')) <= include_context.MAX_PATH_BYTES
                    and type(start) in (str, int) and type(start) is not bool and str(start).isascii()
                    and str(start).isdigit() and len(str(start)) <= 12,
                    'Scoped union START must be a nonnegative source byte offset')
            grouped.setdefault(path, []).append(int(start))
        require(len(grouped) <= 2 and all(len(v) == len(set(v)) for v in grouped.values()),
                'Scoped union allows at most two sources and no duplicate locations')
        scoped_union_targets = tuple((path, tuple(starts)) for path, starts in grouped.items())
    require(inspect_scoped_parent_transfer is None or preview_conditional_modern_parent_reference is None,
            'Parent transfer inspection and preview cannot be requested together')
    parent_location = None
    parent_preview = preview_conditional_modern_parent_reference is not None
    require(type(attest_parent_mannot) is bool and (not attest_parent_mannot or parent_preview),
            'Mannot binding evidence requires a parent reference preview')
    parent_request = preview_conditional_modern_parent_reference if parent_preview else inspect_scoped_parent_transfer
    if parent_request is not None:
        require(modern_highlight_evidence and (not parent_preview or preview_conditional_modern_combined_highlights)
                and type(parent_request) in (tuple, list) and len(parent_request) == 2,
                'Parent transfer requires modern evidence, PATH START, and combined highlights for preview')
        path, start = parent_request
        require(type(path) is str and len(path.encode('utf-8')) <= include_context.MAX_PATH_BYTES
                and type(start) in (str, int) and type(start) is not bool and str(start).isascii()
                and str(start).isdigit() and len(str(start)) <= 12,
                'Parent transfer START must be a nonnegative source byte offset')
        parent_location = (path, int(start))
    parent_union_target = None
    if preview_conditional_modern_parent_union is not None:
        values = preview_conditional_modern_parent_union
        require(preview_conditional_modern_combined_highlights and type(values) in (tuple, list)
                and 2 <= len(values) <= 4, 'Parent union requires combined highlights and two to four PATH START pairs')
        pairs = []
        for value in values:
            require(type(value) in (tuple, list) and len(value) == 2, 'Parent union requires PATH START pairs')
            target_path, target_start = value
            require(type(target_path) is str and len(target_path.encode('utf-8')) <= include_context.MAX_PATH_BYTES
                    and type(target_start) in (str, int) and type(target_start) is not bool
                    and str(target_start).isascii() and str(target_start).isdigit()
                    and len(str(target_start)) <= 12, 'Invalid parent union path or source byte offset')
            pairs.append((target_path, int(target_start)))
        require(len({p for p, _ in pairs}) == 1 and len({offset for _, offset in pairs}) == len(pairs),
                'Parent union requires one source and distinct locations')
        parent_union_target = (pairs[0][0], tuple(offset for _, offset in pairs))
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
    conditional_prose_footnote_display_gap_previews = []
    conditional_prose_footnote_extra_gap_previews = []
    conditional_punctuation_previews = []
    conditional_punctuation_move_previews = []
    conditional_adjacent_punctuation_move_previews = []
    conditional_punctuation_details = []
    conditional_spacer_previews = []
    conditional_scoped_previews = []
    conditional_continuation_previews = []
    conditional_capitalization_previews = []
    conditional_hyphen_capitalization_previews = []
    conditional_paragraph_gap_previews = []
    conditional_hyphen_marker_gap_previews = []
    conditional_hyphen_block_gap_previews = []
    conditional_prose_spacer_previews = []
    conditional_aligned_spacer_previews = []
    conditional_prose_marker_previews = []
    conditional_detached_annotations = []
    conditional_raw_overlays = []
    conditional_raw_annotations = []
    conditional_mannot_scope = []
    conditional_marker_tags = []
    local_findings, local_suppressed, local_files, local_contexts = [], [], [], []
    conditional_hyphen_markers = []
    conditional_local_scope_raw_annotations = []
    items = len(captured.sources) + len(captured.edges) + len(captured.occurrences) + len(captured.unresolved)
    capitalization_review = None
    if inspect_capitalization_review:
        from document_capitalization_review import analyze as review_capitalization
        capitalization_review = review_capitalization(captured, catalog['rules'], max_items=MAX_REPORT_ITEMS - items)
        items += capitalization_review['report_items']
        require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
    import_obligation_report = None
    if import_obligations:
        from document_import_obligations import analyze as analyze_import_obligations
        import_obligation_report = analyze_import_obligations(captured)
        items += len(import_obligation_report['relative_sources'])
        for row in import_obligation_report['by_source']:
            items += 1 + len(row['obligations']) + sum(len(item['relative_import_path'])
                                                       for item in row['obligations'])
            require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
    footnote_report = None
    modern_highlight_report = None
    modern_analyzer = None
    scoped_parent_pattern_report = None
    modern_highlight_previews = None
    modern_combined_previews = None
    modern_literal_footnote_previews = None
    modern_scoped_math_preview = None
    modern_scoped_union_previews = None
    parent_transfer_result = None
    parent_union_result = None
    parent_mannot_result = None
    modern_math_report = None
    modern_math_previews = None
    if footnote_cache_path is not None:
        from conditional_footnote_binding import ConditionalFootnoteBinding, _valid as valid_footnote_session
        from namespace_multiroot import load_multiroot_capture
        footnote_roots = load_multiroot_capture(package_path, footnote_cache_path,
                                               capture_mannot_artifacts=attest_parent_mannot)
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
        if modern_highlight_evidence:
            from conditional_modern_highlight import ModernHighlightAnalyzer, ModernVariantUnavailable
            try:
                modern_analyzer = ModernHighlightAnalyzer(footnote_session, scoped_callbacks=True)
                if preview_conditional_modern_math_highlights:
                    from document_modern_highlight_preview import preview_reviewed_math_all
                    modern_highlight_report, modern_math_report, modern_math_previews = preview_reviewed_math_all(
                        modern_analyzer, max_items=MAX_REPORT_ITEMS - items, return_evidence=True)
                    items += modern_math_previews['report_items']
                elif not preview_conditional_modern_highlights and not preview_conditional_modern_combined_highlights:
                    modern_highlight_report = modern_analyzer.analyze_all(max_items=MAX_REPORT_ITEMS - items)
                if preview_conditional_modern_highlights:
                    from document_modern_highlight_preview import preview_all as preview_modern_highlights
                    repeated_report, modern_highlight_previews = preview_modern_highlights(
                        modern_analyzer, max_items=MAX_REPORT_ITEMS - items, return_evidence=True,
                        allow_direct_references=True,
                        allow_scoped_references=preview_conditional_modern_scoped_references)
                    if modern_highlight_report is not None:
                        require(modern_highlight_report == repeated_report, 'Modern highlight evidence changed between previews')
                    modern_highlight_report = repeated_report
                    items += modern_highlight_previews['report_items']
                if preview_conditional_modern_combined_highlights:
                    if scoped_union_targets is None:
                        from document_modern_highlight_preview import preview_all as preview_combined_highlights
                        repeated_report, modern_combined_previews = preview_combined_highlights(
                            modern_analyzer, max_items=MAX_REPORT_ITEMS - items, return_evidence=True,
                            allow_direct_references=True, allow_scoped_references=True, reviewed_math=True)
                        items += modern_combined_previews['report_items']
                    else:
                        from scoped_math_union_prototype import preview_batch as preview_scoped_union
                        repeated_report, modern_combined_previews, modern_scoped_union_previews = preview_scoped_union(
                            modern_analyzer, scoped_union_targets, max_items=MAX_REPORT_ITEMS - items,
                            return_evidence=True)
                        items += modern_scoped_union_previews['report_items'] - repeated_report['report_items']
                    if modern_highlight_report is not None:
                        require(modern_highlight_report == repeated_report, 'Modern highlight evidence changed between previews')
                    modern_highlight_report = repeated_report
                if preview_conditional_modern_literal_footnotes:
                    from document_literal_footnote_preview import preview_all as preview_literal_footnotes
                    repeated_report, modern_literal_footnote_previews = preview_literal_footnotes(
                        modern_analyzer, max_items=MAX_REPORT_ITEMS - items, return_evidence=True)
                    require(modern_highlight_report == repeated_report, 'Modern highlight evidence changed between previews')
                    items += modern_literal_footnote_previews['report_items']
                if scoped_math_location is not None:
                    import scoped_math_capture_prototype as scoped_math
                    try:
                        modern_scoped_math_preview = scoped_math.preview_one(modern_analyzer, *scoped_math_location)
                    except ValueError as error:
                        ModernHighlightAnalyzer._check(modern_analyzer)
                        modern_scoped_math_preview = dict(path=scoped_math_location[0], start=scoped_math_location[1],
                            verification='withheld', reason=str(error), edits=[], conditions=scoped_math.CONDITIONS,
                            runtime_binding_proved=False, execution_proved=False, coverage_complete=False,
                            render_equivalence_proved=False, edit_permission=False, compiler_permission=False)
                    items += (1 + len(modern_scoped_math_preview['edits']) + len(modern_scoped_math_preview['conditions'])
                              + int('reason' in modern_scoped_math_preview))
                if parent_location is not None:
                    import scoped_parent_transfer_prototype as parent_scope
                    try:
                        if attest_parent_mannot:
                            import scoped_parent_mannot_evidence as mannot_evidence
                            parent_transfer_result, parent_mannot_result = mannot_evidence.preview_with_evidence(
                                modern_analyzer, *parent_location)
                        else:
                            parent_transfer_result = (parent_scope.preview_one(modern_analyzer, *parent_location)
                                if parent_preview else parent_scope.analyze_one(modern_analyzer, *parent_location))
                    except ValueError as error:
                        ModernHighlightAnalyzer._check(modern_analyzer)
                        parent_transfer_result = dict(path=parent_location[0], start=parent_location[1],
                            status='WITHHELD', verification='withheld', reason=str(error), parents=[], edits=[],
                            conditions=(parent_scope.PREVIEW_CONDITIONS if parent_preview else parent_scope.CONDITIONS),
                            independent_source_only=parent_preview, composition_proved=False,
                            runtime_binding_proved=False, execution_proved=False,
                            coverage_complete=False, render_equivalence_proved=False,
                            edit_permission=False, compiler_permission=False)
                        if attest_parent_mannot:
                            parent_mannot_result = dict(path=parent_location[0], start=parent_location[1],
                                status='WITHHELD', reason=str(error), conditions=mannot_evidence.CONDITIONS,
                                runtime_binding_proved=False, execution_proved=False, coverage_complete=False,
                                render_equivalence_proved=False, edit_permission=False, compiler_permission=False)
                    items += (1 + len(parent_transfer_result.get('conditions', ()))
                              + len(parent_transfer_result.get('edits', ()))
                              + int('reason' in parent_transfer_result)
                              + len(parent_transfer_result.get('outer_scope_fields', {}))
                              + len(parent_transfer_result.get('child_scope_fields', {}))
                              + sum(1 + len(row['route']) for row in parent_transfer_result.get('parents', ())))
                    if parent_mannot_result is not None:
                        items += (1 + len(parent_mannot_result['conditions'])
                                  + len(parent_mannot_result.get('route', ()))
                                  + len(parent_mannot_result.get('artifacts', ()))
                                  + int('reason' in parent_mannot_result))
                if parent_union_target is not None:
                    import scoped_parent_union_prototype as parent_union
                    parent_union_result = parent_union.preview_union(modern_analyzer, *parent_union_target)
                    items += (1 + len(parent_union_result['conditions']) + int('reason' in parent_union_result)
                              + sum(1 + len(row['conditions']) + len(row['edits'])
                                    for row in parent_union_result['baseline']))
                    if parent_union_result['union'] is not None:
                        items += (1 + len(parent_union_result['union']['conditions'])
                                  + len(parent_union_result['union']['edits']))
                items += modern_highlight_report['report_items']
            except ModernVariantUnavailable as error:
                modern_highlight_report = dict(status='UNAVAILABLE', reason=str(error), by_file=[],
                    runtime_binding_proved=False, execution_proved=False, coverage_complete=False,
                    edit_permission=False, compiler_permission=False, render_equivalence_proved=False)
                items += 1
                if inspect_scoped_parent_patterns:
                    scoped_parent_pattern_report = dict(status='UNAVAILABLE', reason=str(error), findings=[],
                        report_items=0, truncated=True, runtime_binding_proved=False, execution_proved=False,
                        coverage_complete=False, edit_permission=False, compiler_permission=False,
                        render_equivalence_proved=False)
                if preview_conditional_modern_highlights:
                    modern_highlight_previews = dict(status='UNAVAILABLE', reason=str(error), by_file=[],
                        runtime_binding_proved=False, execution_proved=False, coverage_complete=False,
                        edit_permission=False, compiler_permission=False, render_equivalence_proved=False)
                    items += 1
                if preview_conditional_modern_math_highlights:
                    modern_math_report = dict(status='UNAVAILABLE', reason=str(error), by_file=[],
                                              edit_permission=False, compiler_permission=False)
                    modern_math_previews = dict(status='UNAVAILABLE', reason=str(error), by_file=[],
                                                edit_permission=False, compiler_permission=False)
                    items += 2
                if preview_conditional_modern_combined_highlights:
                    modern_combined_previews = dict(status='UNAVAILABLE', reason=str(error), by_file=[],
                        runtime_binding_proved=False, execution_proved=False, coverage_complete=False,
                        edit_permission=False, compiler_permission=False, render_equivalence_proved=False)
                    items += 1
                if preview_conditional_modern_literal_footnotes:
                    modern_literal_footnote_previews = dict(status='UNAVAILABLE', reason=str(error), plans=[], withheld=[],
                        runtime_binding_proved=False, execution_proved=False, coverage_complete=False,
                        edit_permission=False, compiler_permission=False, render_equivalence_proved=False)
                    items += 1
                if scoped_math_location is not None:
                    modern_scoped_math_preview = dict(path=scoped_math_location[0], start=scoped_math_location[1],
                        verification='withheld', reason=str(error), edits=[], conditions=(),
                        runtime_binding_proved=False, execution_proved=False, coverage_complete=False,
                        edit_permission=False, compiler_permission=False, render_equivalence_proved=False)
                    items += 1
                if scoped_union_targets is not None:
                    modern_scoped_union_previews = dict(status='UNAVAILABLE', reason=str(error), unions=[],
                        runtime_binding_proved=False, execution_proved=False, coverage_complete=False,
                        edit_permission=False, compiler_permission=False, render_equivalence_proved=False)
                    items += 1
                if parent_location is not None:
                    parent_transfer_result = dict(path=parent_location[0], start=parent_location[1],
                        status='UNAVAILABLE', verification='withheld', reason=str(error), parents=[], edits=[],
                        runtime_binding_proved=False, execution_proved=False, coverage_complete=False,
                        edit_permission=False, compiler_permission=False, render_equivalence_proved=False)
                    items += 1
                    if attest_parent_mannot:
                        parent_mannot_result = dict(path=parent_location[0], start=parent_location[1],
                            status='UNAVAILABLE', reason=str(error), conditions=(),
                            runtime_binding_proved=False, execution_proved=False, coverage_complete=False,
                            render_equivalence_proved=False, edit_permission=False, compiler_permission=False)
                        items += 1
                if parent_union_target is not None:
                    parent_union_result = dict(path=parent_union_target[0], status='UNAVAILABLE',
                        reason=str(error), baseline=[], union=None, source_union_revalidated=False,
                        composition_proved=False, runtime_binding_proved=False, execution_proved=False,
                        coverage_complete=False, render_equivalence_proved=False, edit_permission=False,
                        compiler_permission=False)
                    items += 1
            require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
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
                from document_conditional_hyphen_marker import analyze as analyze_hyphen_marker
                hyphen_markers = analyze_hyphen_marker(item.source, item.path, catalog=catalog['rules'])
                items += (1 + len(hyphen_markers['diagnostics']) + len(hyphen_markers['suppressed'])
                          + len(hyphen_markers['unresolved']) + len(hyphen_markers['conditions']))
                require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                conditional_hyphen_markers.append(hyphen_markers)
                from document_conditional_local_raw_annot import analyze as analyze_local_raw_annot
                local_raw = analyze_local_raw_annot(item.source, item.path, catalog=catalog['rules'])
                items += (1 + len(local_raw['diagnostics']) + len(local_raw['suppressed'])
                          + len(local_raw['unresolved']) + len(local_raw['conditions'])
                          + local_raw['wrappers_examined'] + local_raw['lexical_display_calls'])
                require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                conditional_local_scope_raw_annotations.append(local_raw)
            else:
                local_results = None
            if preview_conditional_gaps:
                from footnote_rules import preview_conditional_math_gaps
                from document_nested_prose_footnote_gap import preview as preview_prose_footnote_gap
                from document_prose_footnote_display_gap import preview as preview_prose_footnote_display_gap
                from document_prose_footnote_extra_gaps import preview as preview_prose_footnote_extra_gaps
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
                prose_display_gap = preview_prose_footnote_display_gap(item.tree, item.path, catalog=catalog['rules'])
                if prose_display_gap is not None:
                    items += (1 + len(prose_display_gap['edits']) + len(prose_display_gap['conditions'])
                              + int(bool(prose_display_gap.get('reason'))))
                    require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                    conditional_prose_footnote_display_gap_previews.append(prose_display_gap)
                prose_extra_gap = preview_prose_footnote_extra_gaps(item.tree, item.path, catalog=catalog['rules'])
                if prose_extra_gap is not None:
                    items += (1 + len(prose_extra_gap['edits']) + len(prose_extra_gap['conditions'])
                              + int(bool(prose_extra_gap.get('reason')))
                              + sum(len(edit['following_conditions']) for edit in prose_extra_gap['edits']))
                    require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                    conditional_prose_footnote_extra_gap_previews.append(prose_extra_gap)
            if preview_conditional_punctuation:
                from footnote_rules import preview_conditional_prose_punctuation
                from document_nested_footnote_punctuation import preview as preview_punctuation_move
                from document_adjacent_footnote_punctuation import analyze as analyze_punctuation_detail
                from document_adjacent_punctuation_preview import preview as preview_adjacent_punctuation_move
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
                adjacent_move = preview_adjacent_punctuation_move(item.tree, item.path, catalog=catalog['rules'])
                if adjacent_move is not None:
                    items += (1 + len(adjacent_move['edits']) + len(adjacent_move['conditions'])
                              + int(bool(adjacent_move.get('reason'))))
                    require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                    conditional_adjacent_punctuation_move_previews.append(adjacent_move)
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
                    items += 1 + len(spacer['edits']) + len(spacer['applicability_conditions'])
                    require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                    conditional_spacer_previews.append(spacer)
            if preview_conditional_scoped_calls:
                from document_conditional_scoped_calls import analyze as analyze_scoped_calls
                from document_conditional_detached_annotations import analyze as analyze_detached_annotations
                from document_conditional_raw_overlay import analyze as analyze_raw_overlay
                from document_conditional_raw_annot import analyze as analyze_raw_annot
                from document_conditional_mannot_scope import analyze as analyze_mannot_scope
                from document_conditional_marker_tags import analyze as analyze_marker_tags
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
                          + len(raw_overlay['conditions']) + len(raw_overlay['unresolved'])
                          + raw_overlay['lexical_display_calls'])
                require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                conditional_raw_overlays.append(raw_overlay)
                raw_annot = analyze_raw_annot(item.source, item.path, catalog=catalog['rules'])
                items += (1 + len(raw_annot['diagnostics']) + len(raw_annot['suppressed'])
                          + len(raw_annot['conditions']) + len(raw_annot['unresolved'])
                          + raw_annot['lexical_display_calls'])
                require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                conditional_raw_annotations.append(raw_annot)
                mannot_scope = analyze_mannot_scope(item.source, item.path, catalog=catalog['rules'])
                items += (1 + len(mannot_scope['diagnostics']) + len(mannot_scope['suppressed'])
                          + len(mannot_scope['conditions']) + len(mannot_scope['unresolved'])
                          + mannot_scope['wrappers_examined'] + mannot_scope['lexical_display_calls'])
                require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                conditional_mannot_scope.append(mannot_scope)
                marker_tags = analyze_marker_tags(item.source, item.path, catalog=catalog['rules'])
                items += (1 + len(marker_tags['diagnostics']) + len(marker_tags['suppressed'])
                          + len(marker_tags['conditions']) + len(marker_tags['unresolved']))
                require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                conditional_marker_tags.append(marker_tags)
            if preview_conditional_continuations:
                from document_conditional_continuation import CONDITIONS, preview as preview_continuation
                continuation = preview_continuation(item.tree, item.path, catalog=catalog['rules'])
                if continuation is None:
                    continuation = dict(path=item.path, baseline_sha256=digest(item.source),
                        candidate_sha256=None, verification='no-candidate', conditions=CONDITIONS,
                        execution_proved=False, render_equivalence_proved=False,
                        edit_permission=False, compiler_permission=False, edits=[])
                items += (1 + len(continuation['edits']) + len(continuation['conditions'])
                          + int('reason' in continuation))
                require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                conditional_continuation_previews.append(continuation)
            if preview_conditional_capitalization:
                from document_conditional_capitalization import CONDITIONS, preview as preview_capitalization
                from document_hyphen_marker_capitalization import CONDITIONS as HYPHEN_CAPITALIZATION_CONDITIONS
                from document_hyphen_marker_capitalization import preview as preview_hyphen_capitalization
                capitalization = preview_capitalization(item.tree, item.path, catalog=catalog['rules'])
                if capitalization is None:
                    capitalization = dict(path=item.path, baseline_sha256=digest(item.source),
                        candidate_sha256=None, verification='no-candidate', conditions=CONDITIONS,
                        execution_proved=False, runtime_binding_proved=False,
                        render_equivalence_proved=False, edit_permission=False, compiler_permission=False, edits=[])
                items += (1 + len(capitalization['edits']) + len(capitalization['conditions'])
                          + int('reason' in capitalization))
                require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                conditional_capitalization_previews.append(capitalization)
                hyphen_capitalization = preview_hyphen_capitalization(item.tree, item.path, catalog=catalog['rules'])
                if hyphen_capitalization is None:
                    hyphen_capitalization = dict(path=item.path, baseline_sha256=digest(item.source),
                        candidate_sha256=None, verification='no-candidate', conditions=HYPHEN_CAPITALIZATION_CONDITIONS,
                        execution_proved=False, runtime_binding_proved=False,
                        render_equivalence_proved=False, edit_permission=False, compiler_permission=False, edits=[])
                items += (1 + len(hyphen_capitalization['edits']) + len(hyphen_capitalization['conditions'])
                          + int('reason' in hyphen_capitalization))
                require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                conditional_hyphen_capitalization_previews.append(hyphen_capitalization)
            if preview_conditional_paragraph_gaps:
                from document_conditional_paragraph_gap import CONDITIONS, preview as preview_paragraph_gap
                paragraph_gap = preview_paragraph_gap(item.tree, item.path, catalog=catalog['rules'])
                if paragraph_gap is None:
                    paragraph_gap = dict(path=item.path, baseline_sha256=digest(item.source),
                        candidate_sha256=None, verification='no-candidate', conditions=CONDITIONS,
                        execution_proved=False, runtime_binding_proved=False,
                        render_equivalence_proved=False, edit_permission=False, compiler_permission=False, edits=[])
                items += (1 + len(paragraph_gap['edits']) + len(paragraph_gap['conditions'])
                          + int('reason' in paragraph_gap))
                require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                conditional_paragraph_gap_previews.append(paragraph_gap)
                from document_conditional_hyphen_marker_gap import CONDITIONS, preview as preview_hyphen_marker_gap
                hyphen_gap = preview_hyphen_marker_gap(item.tree, item.path, catalog=catalog['rules'])
                if hyphen_gap is None:
                    hyphen_gap = dict(path=item.path, baseline_sha256=digest(item.source),
                        candidate_sha256=None, verification='no-candidate', conditions=CONDITIONS,
                        execution_proved=False, runtime_binding_proved=False,
                        render_equivalence_proved=False, edit_permission=False, compiler_permission=False,
                        unresolved=[], edits=[])
                items += (1 + len(hyphen_gap['edits']) + len(hyphen_gap['conditions'])
                          + len(hyphen_gap['unresolved']) + int('reason' in hyphen_gap))
                require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                conditional_hyphen_marker_gap_previews.append(hyphen_gap)
            if preview_conditional_block_marker_gaps:
                from document_hyphen_gap_after_blocks import CONDITIONS, preview as preview_hyphen_block_gap
                block_gap = preview_hyphen_block_gap(item.tree, item.path, catalog=catalog['rules'])
                if block_gap is None:
                    block_gap = dict(path=item.path, baseline_sha256=digest(item.source),
                        candidate_sha256=None, verification='no-candidate', conditions=CONDITIONS,
                        execution_proved=False, runtime_binding_proved=False,
                        render_equivalence_proved=False, edit_permission=False, compiler_permission=False, edits=[])
                items += (1 + len(block_gap['edits']) + len(block_gap['conditions'])
                          + int('reason' in block_gap)
                          + sum(len(edit['preceding_conditions']) for edit in block_gap['edits']))
                require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                conditional_hyphen_block_gap_previews.append(block_gap)
            if preview_conditional_prose_spacers:
                from document_conditional_prose_spacer import CONDITIONS, preview as preview_prose_spacer
                prose_spacer = preview_prose_spacer(item.tree, item.path, catalog=catalog['rules'])
                if prose_spacer is None:
                    prose_spacer = dict(path=item.path, baseline_sha256=digest(item.source),
                        candidate_sha256=None, verification='no-candidate', conditions=CONDITIONS,
                        runtime_binding_proved=False, execution_proved=False,
                        render_equivalence_proved=False, edit_permission=False, compiler_permission=False, edits=[])
                items += (1 + len(prose_spacer['edits']) + len(prose_spacer['conditions'])
                          + int('reason' in prose_spacer))
                require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                conditional_prose_spacer_previews.append(prose_spacer)
            if preview_conditional_aligned_spacers:
                from document_conditional_aligned_spacer import CONDITIONS, preview as preview_aligned_spacer
                aligned_spacer = preview_aligned_spacer(item.tree, item.path, catalog=catalog['rules'])
                if aligned_spacer is None:
                    aligned_spacer = dict(path=item.path, baseline_sha256=digest(item.source),
                        candidate_sha256=None, verification='no-candidate', conditions=CONDITIONS,
                        runtime_binding_proved=False, execution_proved=False,
                        render_equivalence_proved=False, edit_permission=False, compiler_permission=False, edits=[])
                items += (1 + len(aligned_spacer['edits']) + len(aligned_spacer['conditions'])
                          + int('reason' in aligned_spacer))
                require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                conditional_aligned_spacer_previews.append(aligned_spacer)
            if preview_conditional_prose_markers:
                from document_conditional_prose_marker import CONDITIONS, preview as preview_prose_marker
                prose_marker = preview_prose_marker(item.tree, item.path, catalog=catalog['rules'])
                if prose_marker is None:
                    prose_marker = dict(path=item.path, baseline_sha256=digest(item.source),
                        candidate_sha256=None, verification='no-candidate', conditions=CONDITIONS,
                        runtime_binding_proved=False, execution_proved=False,
                        render_equivalence_proved=False, edit_permission=False, compiler_permission=False, edits=[])
                items += (1 + len(prose_marker['edits']) + len(prose_marker['conditions'])
                          + int('reason' in prose_marker))
                require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
                conditional_prose_marker_previews.append(prose_marker)
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
    cetz_annotations = None
    if cetz_annotation_manifest is not None:
        from document_preview_cli import load_manifest
        import document_resources
        from document_footnote_evidence import PreviewFootnoteEvidence, _valid as valid_preview_evidence
        import document_cetz_detached_annotations as cetz_detached
        roots, requests, allow = load_manifest(cetz_annotation_manifest, root)
        require(roots.get('packages') == footnote_roots.roots[0]
                and roots.get('cache') == footnote_roots.roots[1],
                'Manifest package and cache roots must match the selected namespace roots')
        snapshot = document_resources.capture(roots, requests, allow_apple_asset_fonts=allow)
        proof = PreviewFootnoteEvidence(footnote_session, snapshot)
        prefix = 'Callback or annotation argument effects are unsupported at byte '
        candidates = [row for row in conditional_detached_annotations
                      if any(reason.startswith(prefix) for reason in row['unresolved'])]
        by_path = {item.path: item for item in captured.sources}
        work = sum(len(by_path[row['path']].tree.nodes)
                   * sum(reason.startswith(prefix) for reason in row['unresolved']) for row in candidates)
        require(work <= cetz_detached.MAX_NODE_WORK, 'CeTZ scoped annotation total work limit exceeded')
        inspected = []
        for row in candidates:
            finding = cetz_detached.analyze(proof, row['path'], catalog=catalog['rules'])
            items += (1 + len(finding['diagnostics']) + len(finding['suppressed'])
                      + len(finding['unresolved']) + len(finding['conditions']))
            require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
            inspected.append(finding)
        valid_preview_evidence(proof)
        cetz_annotations = dict(by_file=inspected, captured_sources=len(captured.sources),
                                candidate_sources=len(candidates), status='PARTIAL',
                                runtime_binding_proved=False, execution_proved=False,
                                coverage_complete=False, edit_permission=False, compiler_permission=False)
        require(len(json.dumps(cetz_annotations, ensure_ascii=False).encode('utf-8')) <= MAX_MODERN_REPORT_BYTES,
                'CeTZ scoped annotation report size limit exceeded')
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
        report['conditional_prose_footnote_display_gap_previews'] = conditional_prose_footnote_display_gap_previews
        report['conditional_prose_footnote_extra_gap_previews'] = conditional_prose_footnote_extra_gap_previews
        report['conditional_gap_preview_status'] = 'independent-source-only-not-composed-or-render-verified'
    if preview_conditional_punctuation:
        report['conditional_punctuation_previews'] = conditional_punctuation_previews
        report['conditional_punctuation_move_previews'] = conditional_punctuation_move_previews
        report['conditional_adjacent_punctuation_move_previews'] = conditional_adjacent_punctuation_move_previews
        report['conditional_punctuation_details'] = dict(by_file=conditional_punctuation_details,
            duplicates_existing_hints=True, edit_permission=False, compiler_permission=False)
        report['conditional_punctuation_preview_status'] = 'independent-source-only-not-composed-or-render-verified'
    if preview_conditional_spacers:
        report['conditional_spacer_previews'] = conditional_spacer_previews
        report['conditional_spacer_preview_status'] = 'partial-independent-source-only-not-composed-or-render-verified'
    if preview_conditional_scoped_calls:
        from document_conditional_scope_namespaces import analyze as analyze_scope_namespaces
        conditional_scope_namespaces = analyze_scope_namespaces(captured, catalog=catalog['rules'])
        items += (1 + len(conditional_scope_namespaces['diagnostics'])
                  + len(conditional_scope_namespaces['suppressed'])
                  + len(conditional_scope_namespaces['unresolved'])
                  + len(conditional_scope_namespaces['conditions']))
        items += sum(len(row['include_chain']) + len(row['include_unresolved'])
                     + len(row['comparison_site']['include_chain'])
                     + len(row['comparison_site']['include_unresolved'])
                     + sum(1 + len(scope['rule_ids']) for scope in row['suppressions'])
                     + sum(1 + len(scope['rule_ids']) for scope in row['comparison_site']['suppressions'])
                     for row in (*conditional_scope_namespaces['diagnostics'],
                                 *conditional_scope_namespaces['suppressed']))
        require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
        report['conditional_scoped_calls'] = dict(by_file=conditional_scoped_previews,
            edit_permission=False, compiler_permission=False,
            scope='independent-source-pattern-not-composed-or-render-verified')
        report['conditional_detached_annotations'] = dict(by_file=conditional_detached_annotations,
            edit_permission=False, compiler_permission=False, coverage_complete=False)
        if cetz_annotations is not None:
            report['conditional_cetz_detached_annotations'] = cetz_annotations
        report['conditional_raw_overlays'] = dict(by_file=conditional_raw_overlays,
            edit_permission=False, compiler_permission=False, coverage_complete=False)
        report['conditional_raw_annotations'] = dict(by_file=conditional_raw_annotations,
            edit_permission=False, compiler_permission=False, coverage_complete=False)
        report['conditional_mannot_scope_annotations'] = dict(by_file=conditional_mannot_scope,
            duplicates_existing_hints_possible=True, edit_permission=False, compiler_permission=False,
            coverage_complete=False)
        report['conditional_marker_tags'] = dict(by_file=conditional_marker_tags,
            edit_permission=False, compiler_permission=False, coverage_complete=False)
        report['conditional_scope_namespaces'] = conditional_scope_namespaces
    if preview_conditional_continuations:
        report['conditional_continuation_previews'] = dict(by_file=conditional_continuation_previews,
            edit_permission=False, compiler_permission=False,
            scope='independent-source-pattern-not-composed-or-render-verified')
    if preview_conditional_capitalization:
        report['conditional_capitalization_previews'] = dict(by_file=conditional_capitalization_previews,
            edit_permission=False, compiler_permission=False,
            scope='independent-source-pattern-not-composed-or-render-verified')
        report['conditional_hyphen_capitalization_previews'] = dict(by_file=conditional_hyphen_capitalization_previews,
            edit_permission=False, compiler_permission=False,
            scope='independent-source-pattern-not-composed-or-render-verified')
    if preview_conditional_paragraph_gaps:
        report['conditional_paragraph_gap_previews'] = dict(by_file=conditional_paragraph_gap_previews,
            edit_permission=False, compiler_permission=False,
            scope='independent-source-pattern-not-composed-or-render-verified')
        report['conditional_hyphen_marker_gap_previews'] = dict(by_file=conditional_hyphen_marker_gap_previews,
            edit_permission=False, compiler_permission=False,
            scope='independent-source-pattern-not-composed-or-render-verified')
    if preview_conditional_block_marker_gaps:
        report['conditional_hyphen_block_gap_previews'] = dict(by_file=conditional_hyphen_block_gap_previews,
            edit_permission=False, compiler_permission=False,
            scope='independent-source-pattern-not-composed-or-render-verified')
    if preview_conditional_prose_spacers:
        report['conditional_prose_spacer_previews'] = dict(by_file=conditional_prose_spacer_previews,
            edit_permission=False, compiler_permission=False,
            scope='independent-source-pattern-not-composed-or-render-verified')
    if preview_conditional_aligned_spacers:
        report['conditional_aligned_spacer_previews'] = dict(by_file=conditional_aligned_spacer_previews,
            edit_permission=False, compiler_permission=False,
            scope='independent-source-pattern-not-composed-or-render-verified')
    if preview_conditional_prose_markers:
        report['conditional_prose_marker_previews'] = dict(by_file=conditional_prose_marker_previews,
            edit_permission=False, compiler_permission=False,
            scope='independent-source-pattern-not-composed-or-render-verified')
    if local_rule_hints:
        from document_local_rules import RULE_IDS as LOCAL_RULE_IDS
        report['local_rule_hints'] = dict(rule_ids=list(LOCAL_RULE_IDS),
            conditions=['Each file is analyzed in a standalone lexical environment; actual include-site bindings, '
                        'styles and execution are not established',
                        'This hint analysis uses no package resolution, imported namespace or consumer configuration'],
            diagnostics=sorted(local_findings, key=lambda row: (row['path'], row['line'], row['column'], row['rule_id'])),
            suppressed=sorted(local_suppressed, key=lambda row: (row['path'], row['line'], row['column'], row['rule_id'])),
            source_contexts=local_contexts, coverage=local_files, edit_permission=False)
        report['conditional_hyphen_markers'] = dict(by_file=conditional_hyphen_markers,
            edit_permission=False, compiler_permission=False, coverage_complete=False)
        report['conditional_local_scope_raw_annotations'] = dict(by_file=conditional_local_scope_raw_annotations,
            duplicates_existing_hints_possible=True, edit_permission=False, compiler_permission=False,
            coverage_complete=False)
    if reference_report is not None:
        report['reference_provenance'] = reference_report
    if footnote_report is not None:
        report['conditional_footnote_bindings'] = footnote_report
    if capitalization_review is not None:
        report['capitalization_starter_review'] = capitalization_review
    if import_obligation_report is not None:
        report['import_obligations'] = import_obligation_report
    if modern_highlight_report is not None:
        report['conditional_modern_highlight'] = modern_highlight_report
    if modern_highlight_previews is not None:
        report['conditional_modern_highlight_previews'] = modern_highlight_previews
    if modern_combined_previews is not None:
        report['conditional_modern_combined_previews'] = modern_combined_previews
    if modern_literal_footnote_previews is not None:
        report['conditional_modern_literal_footnote_previews'] = modern_literal_footnote_previews
    if modern_scoped_math_preview is not None:
        report['conditional_modern_scoped_math_preview'] = modern_scoped_math_preview
    if modern_scoped_union_previews is not None:
        report['conditional_modern_scoped_union_previews'] = modern_scoped_union_previews
    if parent_transfer_result is not None:
        key = ('conditional_modern_parent_reference_preview' if parent_preview else 'conditional_scoped_parent_transfer')
        report[key] = parent_transfer_result
    if parent_mannot_result is not None:
        report['conditional_parent_mannot_binding'] = parent_mannot_result
    if parent_union_result is not None:
        report['conditional_modern_parent_union'] = parent_union_result
    if modern_math_report is not None:
        report['conditional_modern_math_evidence'] = modern_math_report
    if modern_math_previews is not None:
        report['conditional_modern_math_previews'] = modern_math_previews
    if modern_highlight_report is not None:
        require(valid_footnote_session(footnote_session), 'Conditional highlight capture changed during inspection')
    if (modern_highlight_report is not None or import_obligation_report is not None
            or cetz_annotations is not None or capitalization_review is not None):
        require(len(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8'))
                <= MAX_MODERN_REPORT_BYTES,
                'Document inspection report byte limit exceeded')
    # Optional inventory runs last. It can use only the item/byte headroom left
    # after the complete existing report, including scoped namespace sections.
    # Its truncation must never remove already verified proposals.
    if inspect_scoped_parent_patterns:
        from scoped_parent_patterns import analyze as inspect_parent_patterns, MAX_ITEMS as PARENT_MAX_ITEMS, MAX_BYTES as PARENT_MAX_BYTES
        base_size = len(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8'))
        byte_room = MAX_MODERN_REPORT_BYTES - base_size
        available = min(PARENT_MAX_ITEMS, MAX_REPORT_ITEMS - items)
        if scoped_parent_pattern_report is None:
            if available < 7 or byte_room < 4096:
                scoped_parent_pattern_report = dict(status='INCOMPLETE', findings=[], truncated=True,
                    reason='Remaining document report budget cannot hold the scoped parent inventory',
                    report_items=0, runtime_binding_proved=False, execution_proved=False,
                    coverage_complete=False, edit_permission=False, compiler_permission=False,
                    render_equivalence_proved=False)
            else:
                scoped_parent_pattern_report = inspect_parent_patterns(modern_analyzer, max_items=available,
                    max_bytes=min(PARENT_MAX_BYTES, byte_room - 512))
        trial = dict(report, scoped_parent_patterns=scoped_parent_pattern_report)
        if len(json.dumps(trial, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')) > MAX_MODERN_REPORT_BYTES:
            # The small fallback itself is the only additional content. If even
            # that cannot fit, leave the pre-existing report intact; the CLI
            # reports the missing optional section on stderr.
            scoped_parent_pattern_report = dict(status='INCOMPLETE', findings=[], truncated=True,
                reason='Remaining document report byte budget cannot hold the scoped parent inventory',
                report_items=0, runtime_binding_proved=False, execution_proved=False,
                coverage_complete=False, edit_permission=False, compiler_permission=False,
                render_equivalence_proved=False)
            trial = dict(report, scoped_parent_patterns=scoped_parent_pattern_report)
        if len(json.dumps(trial, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')) <= MAX_MODERN_REPORT_BYTES:
            report = trial
            items += scoped_parent_pattern_report.get('report_items', 0)
    if inspect_include_forwarding:
        # Run after all existing reports so the optional evidence never
        # consumes their item/byte budgets or alters their proposals.
        available = MAX_REPORT_ITEMS - items
        if available > 0:
            from document_include_forwarding_inspect import inspect as inspect_forwarding
            section, _ = inspect_forwarding(captured, os.fspath(package_path),
                                            os.fspath(footnote_cache_path), max_items=available)
            trial = dict(report, conditional_include_forwarding=section)
            if len(json.dumps(trial, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')) > MAX_MODERN_REPORT_BYTES:
                import include_forwarding_evidence as forwarding
                section = forwarding._unavailable(captured, 'Remaining document report byte budget is insufficient')
                trial = dict(report, conditional_include_forwarding=section)
            if len(json.dumps(trial, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')) <= MAX_MODERN_REPORT_BYTES:
                report = trial
    return report


def main(arguments=None):
    parser = argparse.ArgumentParser(prog='typst-lint inspect-document', description=__doc__,
        epilog='Uses the fixed book-v1 include budget (64 files, 400,000 syntax nodes). '
               'Consumer configuration is not loaded; package namespace hints require explicit '
               '--rule-set pde and --package-path. Inline suppressions are honored. '
               'Incomplete global coverage returns 2 even with zero findings.')
    parser.add_argument('--root', required=True, help='Canonical absolute consumer root')
    parser.add_argument('--entrypoint', required=True, help='Canonical project-relative .typ entrypoint')
    parser.add_argument('--import-obligations', action='store_true',
                        help='Report unresolved static relative-import graph requests by included source')
    parser.add_argument('--inspect-include-forwarding', action='store_true',
                        help='Report conditional include ownership using math-book from --package-path (no cache fallback); execution remains unproved')
    parser.add_argument('--rule-set', choices=('core', 'pde'), default='core',
                        help='Explicit source checks: core (scripts) or pde (also display layout); default core')
    parser.add_argument('--package-path',
                        help='Explicit canonical package root for conditional PDE helper namespace hints')
    parser.add_argument('--footnote-cache-path',
                        help='Explicit second namespace root for conditional footnote bindings (requires --rule-set pde and --package-path)')
    parser.add_argument('--footnote-punctuation-evidence', action='store_true',
                        help='Report separately sourced conditional prose footnote punctuation hints (requires --footnote-cache-path)')
    parser.add_argument('--modern-highlight-evidence', action='store_true',
                        help='Report the exact modern text-utils highlighted variant (requires --package-path and --footnote-cache-path)')
    parser.add_argument('--cetz-annotation-manifest',
                        help='Capture an explicit resource manifest for conditional CeTZ annotation hints (requires scoped-call previews and both namespace roots)')
    parser.add_argument('--inspect-capitalization-review', action='store_true',
                        help='Review lowercase paragraph starters outside the versioned policy (not rule violations or edits)')
    parser.add_argument('--inspect-scoped-parent-patterns', action='store_true',
                        help='Inventory nested scoped-parent source patterns (requires --modern-highlight-evidence)')
    parser.add_argument('--inspect-scoped-parent-transfer', nargs=2, metavar=('PATH', 'START'),
                        help='Inspect parent/child scope identities at one diagnostic location (requires modern evidence)')
    parser.add_argument('--preview-conditional-modern-parent-reference', nargs=2, metavar=('PATH', 'START'),
                        help='Preview one independent parent-scoped reference plan; do not merge plans (requires combined highlights)')
    parser.add_argument('--attest-parent-mannot', action='store_true',
                        help='Inspect selected mannot source binding for the parent preview; runtime and rendering remain unproved')
    parser.add_argument('--preview-conditional-modern-parent-union', nargs=2, action='append', metavar=('PATH', 'START'),
                        help='Revalidate two to four parent reference sites as one source-only plan (requires combined highlights)')
    parser.add_argument('--preview-conditional-modern-highlights', action='store_true',
                        help='Preview simple modern highlight splits (requires --modern-highlight-evidence)')
    parser.add_argument('--preview-conditional-modern-scoped-references', action='store_true',
                        help='Opt in to source-only scoped reference highlight splits (requires --preview-conditional-modern-highlights)')
    parser.add_argument('--preview-conditional-modern-math-highlights', action='store_true',
                        help='Preview reviewed builtin math highlight splits (requires --modern-highlight-evidence)')
    parser.add_argument('--preview-conditional-modern-combined-highlights', action='store_true',
                        help='Combine separate supported reviewed-math and scoped-reference highlight edits per source (requires --modern-highlight-evidence)')
    parser.add_argument('--preview-conditional-modern-literal-footnotes', action='store_true',
                        help='Preview independent literal footnote highlight plans; do not merge with other plans (requires combined highlights)')
    parser.add_argument('--preview-conditional-modern-scoped-math', nargs=2, metavar=('PATH', 'START'),
                        help='Preview one independent callback math plan at diagnostic byte offset; do not merge with other plans (requires combined highlights)')
    parser.add_argument('--preview-conditional-modern-scoped-union', nargs=2, action='append', metavar=('PATH', 'START'),
                        help='Preview a separately verified per-source scoped/math union; repeat up to four locations (requires combined highlights)')
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
    parser.add_argument('--preview-conditional-continuations', action='store_true',
                        help='Preview conditional prose continuation joins (requires --rule-set pde)')
    parser.add_argument('--preview-conditional-capitalization', action='store_true',
                        help='Preview conditional nested paragraph capitalization (requires --rule-set pde)')
    parser.add_argument('--preview-conditional-paragraph-gaps', action='store_true',
                        help='Preview conditional nested marker blank lines (requires --rule-set pde)')
    parser.add_argument('--preview-conditional-block-marker-gaps', action='store_true',
                        help='Preview extra marker gaps after conditional block-like calls (requires --rule-set pde)')
    parser.add_argument('--preview-conditional-prose-spacers', action='store_true',
                        help='Preview conditional prose footnote spacer removal (requires --rule-set pde)')
    parser.add_argument('--preview-conditional-aligned-spacers', action='store_true',
                        help='Preview conditional aligned footnote spacers (requires --rule-set pde)')
    parser.add_argument('--preview-conditional-prose-markers', action='store_true',
                        help='Preview conditional prose paragraph markers (requires --rule-set pde)')
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
                                 import_obligations=args.import_obligations,
                                 inspect_include_forwarding=args.inspect_include_forwarding,
                                 package_path=args.package_path,
                                 footnote_cache_path=args.footnote_cache_path,
                                 footnote_punctuation_evidence=args.footnote_punctuation_evidence,
                                 modern_highlight_evidence=args.modern_highlight_evidence,
                                 cetz_annotation_manifest=args.cetz_annotation_manifest,
                                 preview_conditional_modern_highlights=args.preview_conditional_modern_highlights,
                                 inspect_scoped_parent_patterns=args.inspect_scoped_parent_patterns,
                                 inspect_capitalization_review=args.inspect_capitalization_review,
                                 inspect_scoped_parent_transfer=args.inspect_scoped_parent_transfer,
                                 preview_conditional_modern_parent_reference=args.preview_conditional_modern_parent_reference,
                                 attest_parent_mannot=args.attest_parent_mannot,
                                 preview_conditional_modern_parent_union=args.preview_conditional_modern_parent_union,
                                 preview_conditional_modern_scoped_references=args.preview_conditional_modern_scoped_references,
                                 preview_conditional_modern_math_highlights=args.preview_conditional_modern_math_highlights,
                                 preview_conditional_modern_combined_highlights=args.preview_conditional_modern_combined_highlights,
                                 preview_conditional_modern_literal_footnotes=args.preview_conditional_modern_literal_footnotes,
                                 preview_conditional_modern_scoped_math=args.preview_conditional_modern_scoped_math,
                                 preview_conditional_modern_scoped_union=args.preview_conditional_modern_scoped_union,
                                 preview_display_gaps=args.preview_display_gaps,
                                 preview_standalone_displays=args.preview_standalone_displays,
                                 preview_sequence=args.preview_sequence,
                                 local_rule_hints=args.local_rule_hints,
                                 preview_conditional_gaps=args.preview_conditional_gaps,
                                 preview_conditional_punctuation=args.preview_conditional_punctuation,
                                 preview_conditional_spacers=args.preview_conditional_spacers,
                                 preview_conditional_scoped_calls=args.preview_conditional_scoped_calls,
                                 preview_conditional_continuations=args.preview_conditional_continuations,
                                 preview_conditional_capitalization=args.preview_conditional_capitalization,
                                 preview_conditional_paragraph_gaps=args.preview_conditional_paragraph_gaps,
                                 preview_conditional_block_marker_gaps=args.preview_conditional_block_marker_gaps,
                                 preview_conditional_prose_spacers=args.preview_conditional_prose_spacers,
                                 preview_conditional_aligned_spacers=args.preview_conditional_aligned_spacers,
                                 preview_conditional_prose_markers=args.preview_conditional_prose_markers,
                                 reference_package_path=args.reference_package_path,
                                 reference_facade=args.reference_facade,
                                 reference_cache_path=args.reference_cache_path,
                                 reference_calls=args.reference_calls,
                                 reference_arguments=args.reference_arguments,
                                 reference_catalog=args.reference_catalog)
    except (InputError, PackageInputError, OSError, ValueError) as error:
        result = dict(schema=1, tool_version=VERSION, mode='inspect-document', status=2,
                      phase='input', compiler_invoked=False, applied_fixes=[], error=str(error))
    if (args.inspect_scoped_parent_patterns and 'error' not in result
            and 'scoped_parent_patterns' not in result):
        print('Scoped parent pattern inventory omitted: document report byte budget is full', file=sys.stderr)
    if (args.inspect_include_forwarding and 'error' not in result
            and 'conditional_include_forwarding' not in result):
        print('Include forwarding evidence omitted: document report budget is full', file=sys.stderr)
    if args.format == 'json':
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    elif 'error' in result:
        print(f"typst-lint inspect-document: blocked (exit 2): {result['error']}")
    else:
        context = result['include_context']
        print(f"typst-lint inspect-document: {len(context['sources'])} sources, "
              f"{len(context['occurrences'])} include occurrences, "
              f"{len(result['diagnostics'])} source-style findings")
        if 'conditional_include_forwarding' in result:
            section = result['conditional_include_forwarding']
            print(f"Conditional include ownership: {section['status']}; "
                  f"{section['recognized_site_count']} source sites; execution is not proved")
            print('  Original unresolved include records remain unchanged.')
            if section.get('reason'):
                print(f"  unavailable: {section['reason']}")
            for site in section.get('sites', ()):
                print(f"  {site['path']}:{site['line']}: {site['target']} ({site['evidence_kind']})")
        if 'import_obligations' in result:
            inventory = result['import_obligations']
            print('Static import obligations (distinct requests; execution and package evaluation not proved):')
            for row in inventory['by_source']:
                if row['obligations']:
                    print(f"  {row['path']!r}: {len(row['obligations'])} reachable unresolved requests")
                    for item in row['obligations']:
                        print(f"    {item['importer']!r}: {item['category']}: {item['request']!r}")
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
            from cancel_rules import RULE_ID as CANCEL_RULE, DEFERRED_REASON, UNRESOLVED_REASON
            cancel_reasons = (reason for row in result['local_rule_hints']['coverage']
                              if row['rule_id'] == CANCEL_RULE for reason in row['reasons'])
            deferred = unresolved = 0
            for reason in cancel_reasons:
                deferred += reason.startswith(DEFERRED_REASON)
                unresolved += reason.startswith(UNRESOLVED_REASON)
            if deferred or unresolved:
                print(f'Cancellation coverage is incomplete across captured unique source files: '
                      f'{deferred} deferred/dynamic and '
                      f'{unresolved} unresolved/shadowed mathematical call sites; these are not '
                      'violation counts and execution is unproved. See JSON for source positions.')
            from paragraph_rules import NESTED_GAP_UNRESOLVED
            unresolved_gaps = [(row['path'], reason) for row in result['local_rule_hints']['coverage']
                               if row['rule_id'] == 'pde.paragraph-marker-gap'
                               for reason in row['reasons'] if reason.startswith(NESTED_GAP_UNRESOLVED)]
            for path, reason in unresolved_gaps[:20]:
                print(f"  {path}: {reason}")
            if len(unresolved_gaps) > 20:
                print(f"  {len(unresolved_gaps) - 20} additional nested paragraph marker boundaries omitted; see JSON")
        if 'conditional_local_scope_raw_annotations' in result:
            local_raw = result['conditional_local_scope_raw_annotations']
            print('Conditional local-scope raw annotations: diagnostic-only, no edit or compiler permission')
            for row in local_raw['by_file']:
                if not (row['wrappers_examined'] or row['diagnostics'] or row['suppressed'] or row['unresolved']):
                    continue
                print(f"  {row['path']}: {row['wrappers_examined']} callbacks; "
                      f"{row['lexical_display_calls']} calls lexically in display; "
                      f"{len(row['diagnostics'])} outside hints; {len(row['suppressed'])} suppressed")
                for reason in row['unresolved']:
                    print(f"    unresolved: {reason}")
                for finding in row['diagnostics']:
                    print(f"    {finding['line']}:{finding['column']}: {finding['rule_id']}: {finding['explanation']}")
            if local_raw['by_file']:
                for condition in local_raw['by_file'][0]['conditions']:
                    print(f"  condition: {condition}")
        if 'conditional_hyphen_markers' in result:
            markers = result['conditional_hyphen_markers']
            print('Conditional hyphenated paragraph markers: diagnostic-only, no edit or compiler permission')
            for row in markers['by_file']:
                if not (row['diagnostics'] or row['suppressed'] or row['unresolved']):
                    continue
                print(f"  {row['path']}: {row['status']}; {len(row['diagnostics'])} hints; "
                      f"{len(row['suppressed'])} suppressed; source sha256 {row['source_sha256']}")
                for label, entries in (('hint', row['diagnostics']), ('suppressed', row['suppressed'])):
                    for entry in entries:
                        print(f"    {label} {entry['path']}:{entry['line']}:{entry['column']}: "
                              f"{entry['rule_id']}: {entry['explanation']}")
                for reason in row['unresolved']:
                    print(f"    unresolved: {reason}")
            if markers['by_file']:
                for condition in markers['by_file'][0]['conditions']:
                    print(f"  condition: {condition}")
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
        if 'conditional_modern_highlight' in result:
            modern = result['conditional_modern_highlight']
            print(f"Modern highlight evidence: {modern['status']}; diagnostic-only, no edit or compiler permission")
            if modern.get('reason'):
                print(f"  unresolved: {modern['reason']}")
            for row in modern['by_file']:
                if not (row['diagnostics'] or row['suppressed'] or row['unresolved']):
                    continue
                print(f"  {row['path']}: {len(row['diagnostics'])} hints; {len(row['suppressed'])} suppressed")
                for label, entries in (('hint', row['diagnostics']), ('suppressed', row['suppressed'])):
                    for entry in entries:
                        print(f"    {label} {entry['path']}:{entry['line']}:{entry['column']}: "
                              f"{entry['rule_id']}; mode {entry['mode']} ({entry['mode_source']})")
                        for condition in entry['conditions']:
                            print(f"      condition: {condition}")
                for reason in row['unresolved']:
                    print(f"    unresolved: {reason}")
        if 'scoped_parent_patterns' in result:
            parent_inventory = result['scoped_parent_patterns']
            print(f"Scoped parent source patterns: {parent_inventory['status']}; syntax only, no edit or compiler permission")
            for item in parent_inventory['findings']:
                print(f"  {item['path']}:{item['line']}:{item['column']}: {item['wrapper_syntax']}; "
                      + ', '.join(item['reasons']))
                for child in item['child_calls']:
                    print(f"    {child['line']}:{child['column']}: unreviewed call syntax {child['callee_syntax']}")
                if item['child_calls_truncated']:
                    print(f"    {item['child_calls_truncated']} additional call sites omitted")
            if parent_inventory.get('reason'):
                print('  ' + parent_inventory['reason'])
        if 'conditional_modern_highlight_previews' in result:
            previews = result['conditional_modern_highlight_previews']
            print(f"Conditional modern highlight previews: {previews['status']}; "
                  'source-only, no edit or compiler permission')
            if 'diagnostics_count' in previews:
                print(f"  {previews['diagnostics_count']} diagnostics; {previews['previewed_diagnostics']} previewed; "
                      f"{previews['unpreviewed_diagnostics']} not previewed; "
                      f"{previews['withheld_source_count']} sources withheld; "
                      f"{previews['unresolved_reasons_count']} unresolved reasons")
            if previews.get('reason'):
                print(f"  unresolved: {previews['reason']}")
            for row in previews['by_file']:
                print(f"  {row['path']}: {row['verification']}; {len(row['edits'])} edits; "
                      f"source sha256 {row['baseline_sha256']}; candidate sha256 {row['candidate_sha256']}")
                if row.get('reason'):
                    print(f"    unresolved: {row['reason']}")
                for edit in row['edits']:
                    print(f"    conditional edit bytes {edit['start']}..{edit['end']}; replacement in JSON")
            for condition in previews.get('conditions', ()):
                print(f"  condition: {condition}")
        if 'conditional_modern_combined_previews' in result:
            previews = result['conditional_modern_combined_previews']
            print(f"Conditional combined modern highlight previews: {previews['status']}; "
                  'source-only, no edit or compiler permission')
            if 'diagnostics_count' in previews:
                print(f"  {previews['diagnostics_count']} diagnostics; {previews['previewed_diagnostics']} previewed; "
                      f"{previews['unpreviewed_diagnostics']} not previewed; "
                      f"{previews['withheld_source_count']} sources withheld; "
                      f"{previews['unresolved_reasons_count']} unresolved reasons")
            if previews.get('reason'):
                print(f"  unresolved: {previews['reason']}")
            for row in previews['by_file']:
                print(f"  {row['path']}: {row['verification']}; {len(row['edits'])} edits; "
                      f"source sha256 {row['baseline_sha256']}; candidate sha256 {row['candidate_sha256']}")
                if row.get('reason'):
                    print(f"    unresolved: {row['reason']}")
                if row.get('scoped_reference_withheld'):
                    print(f"    scoped reference withheld: {row['scoped_reference_withheld']['reason']}")
                for edit in row['edits']:
                    print(f"    conditional edit bytes {edit['start']}..{edit['end']}; replacement in JSON")
            for condition in previews.get('conditions', ()):
                print(f"  condition: {condition}")
        if 'conditional_modern_parent_union' in result:
            row = result['conditional_modern_parent_union']
            count = len(row['union']['edits']) if row.get('union') else 0
            print(f"Conditional parent union: {row['status']}; {count} edits; source-only")
            print('  Do not merge with other plans; execution, side effects and rendering are not proved.')
            if row.get('reason'):
                print(f"  withheld: {row['reason']}")
        if 'capitalization_starter_review' in result:
            review = result['capitalization_starter_review']
            print(f"Capitalization starter review: {review['status']}; {len(review['findings'])} review-only patterns")
            print('  These are not proven rule violations; language, marker binding and intent are unknown.')
            for row in review['findings']:
                print(f"  {row['path']}:{row['line']}:{row['column']}: review starter {row['starter']!r}")
        if 'conditional_scoped_parent_transfer' in result:
            row = result['conditional_scoped_parent_transfer']
            count = sum(item['parent_transfer'] for item in row.get('parents', ()))
            print(f"Conditional parent scope evidence: {row['status']}; {count} transfers; no execution or edit permission")
            if row.get('reason'):
                print(f"  withheld: {row['reason']}")
        if 'conditional_modern_parent_reference_preview' in result:
            row = result['conditional_modern_parent_reference_preview']
            print(f"Conditional parent reference preview: {row['verification']}; independent source-only plan")
            print('  Do not merge with other plans; execution, side effects and rendering are not proved.')
            if row.get('reason'):
                print(f"  withheld: {row['reason']}")
        if 'conditional_parent_mannot_binding' in result:
            row = result['conditional_parent_mannot_binding']
            print(f"Conditional parent mannot source binding: {row['status']}; runtime and effects are not proved")
            if row.get('reason'):
                print(f"  withheld: {row['reason']}")
        if 'conditional_modern_scoped_union_previews' in result:
            section = result['conditional_modern_scoped_union_previews']
            print(f"Conditional scoped/math union previews: {section['status']}; independent per-source plans")
            print('  Do not merge with other plans; runtime execution and rendering are not proved.')
            for row in section['unions']:
                print(f"  {row['path']}: {row['verification']}; {len(row['edits'])} edits")
                if row.get('reason'):
                    print(f"    withheld: {row['reason']}")
        if 'conditional_modern_scoped_math_preview' in result:
            row = result['conditional_modern_scoped_math_preview']
            print(f"Conditional callback math preview: {row['verification']}; independent source-only plan")
            print('  Do not merge this plan with other previews; execution, side effects and rendering are not proved.')
            print(f"  {row['path']}: {len(row['edits'])} edits")
            if row.get('reason'):
                print(f"    withheld: {row['reason']}")
        if 'conditional_modern_literal_footnote_previews' in result:
            previews = result['conditional_modern_literal_footnote_previews']
            print(f"Conditional literal footnote highlight previews: {previews['status']}; independent source-only plans")
            print('  Do not merge these plans with other previews; execution, numbering and rendering are not proved.')
            for row in previews['plans']:
                print(f"  {row['path']}: {row['verification']}; source sha256 {row['baseline_sha256']}")
            for row in previews['withheld']:
                print(f"  {row['path']}: withheld: {row['reason']}")
        if 'conditional_modern_math_previews' in result:
            previews = result['conditional_modern_math_previews']
            math = result['conditional_modern_math_evidence']
            print(f"Conditional modern math previews: {previews['status']}; source-only, no edit or compiler permission")
            if 'diagnostics_count' in previews:
                print(f"  {previews['diagnostics_count']} reviewed; {previews['previewed_diagnostics']} previewed; "
                      f"{previews['unpreviewed_diagnostics']} not previewed; "
                      f"{previews['withheld_source_count']} sources withheld; "
                      f"{previews['unresolved_reasons_count']} unresolved reasons")
            if previews.get('reason'):
                print(f"  unresolved: {previews['reason']}")
            for row in math['by_file']:
                for entry in row.get('unresolved', ()):
                    print(f"  {row['path']} byte {entry['start']}: unresolved: {entry['reason']}")
                for reason in row.get('upstream_unresolved', ()):
                    print(f"  {row['path']}: unresolved: {reason}")
                for reason in row.get('parse_errors', ()):
                    print(f"  {row['path']}: parse error: {reason}")
            for row in previews['by_file']:
                print(f"  {row['path']}: {row['verification']}; {len(row['edits'])} edits; "
                      f"source sha256 {row['baseline_sha256']}; candidate sha256 {row['candidate_sha256']}")
                if row.get('reason'):
                    print(f"    unresolved: {row['reason']}")
                for edit in row['edits']:
                    print(f"    conditional edit bytes {edit['start']}..{edit['end']}; replacement in JSON")
            for condition in previews.get('conditions', ()):
                print(f"  condition: {condition}")
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
        if 'conditional_continuation_previews' in result:
            continuations = result['conditional_continuation_previews']
            print('Conditional continuation previews: source-only, no edit or compiler permission')
            for row in continuations['by_file']:
                print(f"  {row['path']}: {row['verification']}; {len(row['edits'])} proposed edits; "
                      f"source sha256 {row['baseline_sha256']}; candidate sha256 {row['candidate_sha256']}")
                if 'reason' in row:
                    print(f"    unresolved: {row['reason']}")
                for entry in row['edits']:
                    print(f"    conditional edit bytes {entry['start']}..{entry['end']}: replace with one space")
            if continuations['by_file']:
                for condition in continuations['by_file'][0]['conditions']:
                    print(f"  condition: {condition}")
        if 'conditional_capitalization_previews' in result:
            capitalizations = result['conditional_capitalization_previews']
            print('Conditional capitalization previews: source-only, no edit or compiler permission')
            for row in capitalizations['by_file']:
                print(f"  {row['path']}: {row['verification']}; {len(row['edits'])} proposed edits; "
                      f"source sha256 {row['baseline_sha256']}; candidate sha256 {row['candidate_sha256']}")
                if 'reason' in row:
                    print(f"    unresolved: {row['reason']}")
                for entry in row['edits']:
                    print(f"    conditional edit bytes {entry['start']}..{entry['end']}: "
                          f"replace with {entry['replacement']}")
            if capitalizations['by_file']:
                for condition in capitalizations['by_file'][0]['conditions']:
                    print(f"  condition: {condition}")
        if 'conditional_hyphen_capitalization_previews' in result:
            capitalizations = result['conditional_hyphen_capitalization_previews']
            print('Conditional hyphen-marker capitalization previews: source-only, no edit or compiler permission')
            for row in capitalizations['by_file']:
                print(f"  {row['path']}: {row['verification']}; {len(row['edits'])} proposed edits; "
                      f"source sha256 {row['baseline_sha256']}; candidate sha256 {row['candidate_sha256']}")
                if 'reason' in row:
                    print(f"    unresolved: {row['reason']}")
                for entry in row['edits']:
                    print(f"    conditional edit bytes {entry['start']}..{entry['end']}: "
                          f"replace with {entry['replacement']}")
            if capitalizations['by_file']:
                for condition in capitalizations['by_file'][0]['conditions']:
                    print(f"  condition: {condition}")
        if 'conditional_paragraph_gap_previews' in result:
            paragraph_gaps = result['conditional_paragraph_gap_previews']
            print('Conditional paragraph gap previews: source-only, no edit or compiler permission')
            for row in paragraph_gaps['by_file']:
                print(f"  {row['path']}: {row['verification']}; {len(row['edits'])} proposed edits; "
                      f"source sha256 {row['baseline_sha256']}; candidate sha256 {row['candidate_sha256']}")
                if 'reason' in row:
                    print(f"    unresolved: {row['reason']}")
                for entry in row['edits']:
                    newline = 'CRLF' if entry['replacement'] == '\r\n' else 'LF'
                    print(f"    conditional edit bytes {entry['start']}..{entry['end']}: insert blank {newline} line")
            if paragraph_gaps['by_file']:
                for condition in paragraph_gaps['by_file'][0]['conditions']:
                    print(f"  condition: {condition}")
        if 'conditional_hyphen_marker_gap_previews' in result:
            hyphen_gaps = result['conditional_hyphen_marker_gap_previews']
            print('Conditional hyphen marker gap previews: source-only, no edit or compiler permission')
            for row in hyphen_gaps['by_file']:
                print(f"  {row['path']}: {row['verification']}; {len(row['edits'])} proposed edits; "
                      f"source sha256 {row['baseline_sha256']}; candidate sha256 {row['candidate_sha256']}")
                if 'reason' in row:
                    print(f"    unresolved: {row['reason']}")
                for entry in row['unresolved']:
                    print(f"    unresolved marker at byte {entry['marker_start']}: {entry['reason']}")
                for entry in row['edits']:
                    print(f"    conditional edit bytes {entry['start']}..{entry['end']}: insert blank line")
            if hyphen_gaps['by_file']:
                for condition in hyphen_gaps['by_file'][0]['conditions']:
                    print(f"  condition: {condition}")
        if 'conditional_hyphen_block_gap_previews' in result:
            block_gaps = result['conditional_hyphen_block_gap_previews']
            print('Conditional block-adjacent marker gap previews: source-only, no edit or compiler permission')
            for row in block_gaps['by_file']:
                print(f"  {row['path']}: {row['verification']}; {len(row['edits'])} proposed edits; "
                      f"source sha256 {row['baseline_sha256']}; candidate sha256 {row['candidate_sha256']}")
                if 'reason' in row:
                    print(f"    unresolved: {row['reason']}")
                for entry in row['edits']:
                    print(f"    conditional edit at byte {entry['start']} after {entry['preceding_kind']}: insert blank line")
                    for condition in entry['preceding_conditions']:
                        print(f"      condition: {condition}")
            if block_gaps['by_file']:
                for condition in block_gaps['by_file'][0]['conditions']:
                    print(f"  condition: {condition}")
        if 'conditional_prose_spacer_previews' in result:
            prose_spacers = result['conditional_prose_spacer_previews']
            print('Conditional prose spacer previews: source-only, no edit or compiler permission')
            for row in prose_spacers['by_file']:
                print(f"  {row['path']}: {row['verification']}; {len(row['edits'])} proposed edits; "
                      f"source sha256 {row['baseline_sha256']}; candidate sha256 {row['candidate_sha256']}")
                if 'reason' in row:
                    print(f"    unresolved: {row['reason']}")
                for entry in row['edits']:
                    print(f"    conditional edit bytes {entry['start']}..{entry['end']}: remove spacer and separator")
            if prose_spacers['by_file']:
                for condition in prose_spacers['by_file'][0]['conditions']:
                    print(f"  condition: {condition}")
        if 'conditional_aligned_spacer_previews' in result:
            aligned_spacers = result['conditional_aligned_spacer_previews']
            print('Conditional aligned spacer previews: source-only, no edit or compiler permission')
            for row in aligned_spacers['by_file']:
                print(f"  {row['path']}: {row['verification']}; {len(row['edits'])} proposed edits; "
                      f"source sha256 {row['baseline_sha256']}; candidate sha256 {row['candidate_sha256']}")
                if 'reason' in row:
                    print(f"    unresolved: {row['reason']}")
                for entry in row['edits']:
                    print(f"    conditional edit bytes {entry['start']}..{entry['end']}: insert #dots_space")
            if aligned_spacers['by_file']:
                for condition in aligned_spacers['by_file'][0]['conditions']:
                    print(f"  condition: {condition}")
        if 'conditional_prose_marker_previews' in result:
            prose_markers = result['conditional_prose_marker_previews']
            print('Conditional prose marker previews: source-only, no edit or compiler permission')
            for row in prose_markers['by_file']:
                print(f"  {row['path']}: {row['verification']}; {len(row['edits'])} proposed edits; "
                      f"source sha256 {row['baseline_sha256']}; candidate sha256 {row['candidate_sha256']}")
                if 'reason' in row:
                    print(f"    unresolved: {row['reason']}")
                for entry in row['edits']:
                    print(f"    conditional edit bytes {entry['start']}..{entry['end']}: insert #paragraph_tab")
            if prose_markers['by_file']:
                for condition in prose_markers['by_file'][0]['conditions']:
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
        if 'conditional_cetz_detached_annotations' in result:
            cetz = result['conditional_cetz_detached_annotations']
            print('Conditional captured CeTZ annotations: diagnostic-only, no edit or compiler permission')
            for row in cetz['by_file']:
                print(f"  {row['path']}: {row['status']}; source sha256 {row['source_sha256']}")
                for label, entries in (('hint', row['diagnostics']), ('suppressed', row['suppressed'])):
                    for entry in entries:
                        print(f"    {label} {entry['path']}:{entry['line']}:{entry['column']}: "
                              f"{entry['rule_id']}: {entry['explanation']}")
                for reason in row['unresolved']:
                    print(f"    unresolved: {reason}")
            if cetz['by_file']:
                for condition in cetz['by_file'][0]['conditions']:
                    print(f"  condition: {condition}")
        if 'conditional_raw_overlays' in result:
            raw_overlays = result['conditional_raw_overlays']
            print('Conditional raw overlays: diagnostic-only, no edit or compiler permission')
            for row in raw_overlays['by_file']:
                print(f"  {row['path']}: {row['status']}; {len(row['diagnostics'])} hints; "
                      f"{len(row['suppressed'])} suppressed; {row['lexical_display_calls']} direct lexical "
                      f"display calls; source sha256 {row['source_sha256']}")
                for label, entries in (('hint', row['diagnostics']), ('suppressed', row['suppressed'])):
                    for entry in entries:
                        print(f"    {label} {entry['path']}:{entry['line']}:{entry['column']}: "
                              f"{entry['rule_id']}: {entry['explanation']}")
                for reason in row['unresolved']:
                    print(f"    unresolved: {reason}")
            if raw_overlays['by_file']:
                for condition in raw_overlays['by_file'][0]['conditions']:
                    print(f"  condition: {condition}")
        if 'conditional_raw_annotations' in result:
            raw_annotations = result['conditional_raw_annotations']
            print('Conditional raw annotations: diagnostic-only, no edit or compiler permission')
            for row in raw_annotations['by_file']:
                print(f"  {row['path']}: {row['status']}; {len(row['diagnostics'])} hints; "
                      f"{len(row['suppressed'])} suppressed; {row['lexical_display_calls']} direct lexical "
                      f"display calls; source sha256 {row['source_sha256']}")
                for label, entries in (('hint', row['diagnostics']), ('suppressed', row['suppressed'])):
                    for entry in entries:
                        print(f"    {label} {entry['path']}:{entry['line']}:{entry['column']}: "
                              f"{entry['rule_id']}: {entry['explanation']}")
                for reason in row['unresolved']:
                    print(f"    unresolved: {reason}")
            if raw_annotations['by_file']:
                for condition in raw_annotations['by_file'][0]['conditions']:
                    print(f"  condition: {condition}")
        if 'conditional_mannot_scope_annotations' in result:
            annotations = result['conditional_mannot_scope_annotations']
            print('Conditional mannot-scope annotations: diagnostic-only, no edit or compiler permission; '
                  'hints may duplicate raw annotation hints')
            for row in annotations['by_file']:
                if not (row['wrappers_examined'] or row['unresolved'] or row['diagnostics'] or row['suppressed']):
                    continue
                print(f"  {row['path']}: {row['status']}; {row['wrappers_examined']} wrappers; "
                      f"{row['lexical_display_calls']} calls lexically in display; "
                      f"{len(row['diagnostics'])} hints; {len(row['suppressed'])} suppressed")
                for label, entries in (('hint', row['diagnostics']), ('suppressed', row['suppressed'])):
                    for entry in entries:
                        print(f"    {label} {entry['path']}:{entry['line']}:{entry['column']}: "
                              f"{entry['rule_id']}: {entry['explanation']}")
                for reason in row['unresolved']:
                    print(f"    unresolved: {reason}")
            if annotations['by_file']:
                for condition in annotations['by_file'][0]['conditions']:
                    print(f"  condition: {condition}")
        if 'conditional_marker_tags' in result:
            marker_tags = result['conditional_marker_tags']
            print('Conditional marker tags: diagnostic-only, no edit or compiler permission')
            for row in marker_tags['by_file']:
                print(f"  {row['path']}: {row['status']}; {len(row['diagnostics'])} hints; "
                      f"{len(row['suppressed'])} suppressed; source sha256 {row['source_sha256']}")
                for label, entries in (('hint', row['diagnostics']), ('suppressed', row['suppressed'])):
                    for entry in entries:
                        print(f"    {label} {entry['path']}:{entry['line']}:{entry['column']}: "
                              f"{entry['rule_id']}: {entry['explanation']}")
                for reason in row['unresolved']:
                    print(f"    unresolved: {reason}")
            if marker_tags['by_file']:
                for condition in marker_tags['by_file'][0]['conditions']:
                    print(f"  condition: {condition}")
        if 'conditional_scope_namespaces' in result:
            namespaces = result['conditional_scope_namespaces']
            print('Conditional scope namespaces: diagnostic-only, no edit or compiler permission')
            for label, entries in (('hint', namespaces['diagnostics']), ('suppressed', namespaces['suppressed'])):
                for entry in entries:
                    other = entry['comparison_site']
                    print(f"  {label} {entry['path']}:{entry['line']}:{entry['column']}: "
                          f"{entry['rule_id']}: {entry['explanation']} "
                          f"Comparison: {other['path']}:{other['line']}:{other['column']}.")
            for reason in namespaces['unresolved']:
                print(f"  unresolved: {reason}")
            for condition in namespaces['conditions']:
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
                                 (args.preview_conditional_gaps, 'conditional_prose_footnote_display_gap_previews',
                                  'Conditional prose footnote display gap'),
                                 (args.preview_conditional_gaps, 'conditional_prose_footnote_extra_gap_previews',
                                  'Conditional prose footnote marker/figure gap'),
                                 (args.preview_conditional_punctuation, 'conditional_punctuation_previews',
                                  'Conditional punctuation'),
                                 (args.preview_conditional_punctuation, 'conditional_punctuation_move_previews',
                                  'Conditional punctuation move'),
                                 (args.preview_conditional_punctuation, 'conditional_adjacent_punctuation_move_previews',
                                  'Conditional adjacent punctuation move'),
                                 (args.preview_conditional_spacers, 'conditional_spacer_previews',
                                  'Conditional spacer')):
            if flag:
                previews = result[key]
                print(f"{label} previews: {sum(len(row['edits']) for row in previews)} edits; "
                      f"{sum(row['verification'] == 'withheld' for row in previews)} sources withheld; "
                      'partial, not composed, not render-verified and not applied')
                if key in ('conditional_prose_footnote_gap_previews',
                           'conditional_prose_footnote_display_gap_previews',
                           'conditional_prose_footnote_extra_gap_previews',
                           'conditional_adjacent_punctuation_move_previews'):
                    for row in previews:
                        if row.get('reason'):
                            print(f"  {row['path']}: withheld: {row['reason']}")
                        if key == 'conditional_prose_footnote_extra_gap_previews':
                            for edit in row['edits']:
                                print(f"  {row['path']}: conditional {edit['following_kind']} at byte {edit['start']}")
                                for condition in edit['following_conditions']:
                                    print(f"    condition: {condition}")
                    if previews:
                        for condition in previews[0]['conditions']:
                            print(f"  condition: {condition}")
                    if key == 'conditional_adjacent_punctuation_move_previews':
                        print(f"  {sum(len(row['edits']) // 2 for row in previews)} inseparable punctuation moves")
                if key == 'conditional_spacer_previews':
                    from footnote_rules import UNALIGNED_PREVIEW_CONDITIONS, UNALIGNED_RENDER_EFFECT
                    for condition in UNALIGNED_PREVIEW_CONDITIONS:
                        print(f"  applicability: {condition}")
                    print(f"  possible render effect: {UNALIGNED_RENDER_EFFECT}")
        for blocker in result['blockers']:
            print(blocker)
        print('Global coverage incomplete (exit 2); compiler not invoked')
    return result['status']


if __name__ == '__main__':
    raise SystemExit(main())
