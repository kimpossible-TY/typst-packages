"""Read-only literal-include inventory and source-style checks for one document.

Math scripts have verified in-memory proposals; display source layout rules
have source-level diagnostics. Neither grants source or compilation authority.
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


def report_document(root, entrypoint, *, rule_set='core', package_path=None):
    """Inspect retained source bytes without reading consumer configuration.

    Literal includes inside unknown containers are inventory entries, not proof
    of execution. Each unique captured source is checked once, while repeated
    include occurrences retain their separate site chains in the report.
    """
    require(type(rule_set) is str and rule_set in ('core', 'pde'),
            'Document rule-set must be core or pde')
    require(package_path is None or rule_set == 'pde',
            'Conditional package namespace inspection requires explicit --rule-set pde')
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
            rule_ids=CONDITIONAL_RULES)
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
    items = len(captured.sources) + len(captured.edges) + len(captured.occurrences) + len(captured.unresolved)
    for item in captured.sources:
        try:
            result = analyze(item.tree, item.path, catalog=catalog['rules'], _verify=False)
            displays = (analyze_display_source(item.tree, item.path, catalog=catalog['rules'])
                        if rule_set == 'pde' else None)
            conditional = (conditional_session.analyze(item.tree, item.path)
                           if conditional_session is not None else None)
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
        if conditional is not None:
            items += sum(1 + len(conditional.reasons_for(rid)) + len(conditional.parse_errors)
                         for rid in CONDITIONAL_RULES)
            for finding in (*conditional.diagnostics, *conditional.suppressed):
                items += (1 + len(finding.source_hashes) + len(finding.package_evidence)
                          + len(finding.package_requests) + len(finding.route) + len(finding.conditions)
                          + sum(1 + len(route) for route in finding.content_context))
        require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
        if conditional is not None:
            metadata = {rule['id']: rule for rule in catalog['rules']}
            def conditional_record(finding):
                rule = metadata[finding.rule_id]
                return dict(**asdict(finding), grade=rule['grade'], source=rule['source'])
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
                final = analyze(candidate_tree, item.path, catalog=catalog['rules'], _verify=False)
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
                files.append(dict(path=item.path, source_sha256=item.identity.source_sha256,
                    tree_sha256=item.identity.tree_sha256, bytes=len(item.source), nodes=len(item.tree.nodes),
                    rule_id=rid, status='partial' if value.skips else 'evaluated',
                    analysis_scope='literal-markup-source-pattern', reasons=list(value.skips),
                    diagnostics=len(value.diagnostics), suppressed=len(value.suppressed),
                    proposed_edits=0, candidate_sha256=None,
                    candidate_verification='diagnostic-only', candidate_verification_rules=[]))
    findings.sort(key=lambda row: (row['path'], row['line'], row['column'], row['rule_id']))
    suppressed.sort(key=lambda row: (row['path'], row['line'], row['column'], row['rule_id']))
    conditional_findings.sort(key=lambda row: (row['path'], row['line'], row['column']))
    conditional_suppressed.sort(key=lambda row: (row['path'], row['line'], row['column']))
    edges = []
    for edge in captured.edges:
        record = asdict(edge)
        record['raw_request'] = edge.raw_request.decode('utf-8')
        edges.append(record)
    return dict(schema=1, tool_version=VERSION, mode='inspect-document', status=2, phase='analysis',
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
            files=files,
            unresolved_obligations=['other-style-rules', 'import-and-package-resolution',
                'include-runtime-execution', 'render-context-and-resources',
                'protected-source-transaction', 'compiler-snapshot']),
        blockers=[('Only math scripts and source display layout are inspected; full rule coverage is incomplete'
                   if rule_set == 'pde' else
                   'Only core.parenthesized-scripts is inspected; full document rule coverage is incomplete'),
                  'Literal include reachability does not establish runtime execution or imported dependencies',
                  'Proposals are read-only; no protected source transaction or compiler is invoked'])


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
    parser.add_argument('--format', choices=('json', 'text'), default='text')
    args = parser.parse_args(sys.argv[1:] if arguments is None else arguments)
    try:
        result = report_document(args.root, args.entrypoint, rule_set=args.rule_set,
                                 package_path=args.package_path)
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
        if result['namespace_evidence'] is not None:
            unresolved = sum(len(row['reasons']) + len(row['parse_errors'])
                             for row in result['conditional_coverage'])
            print(f"Conditional helper inspection: {len(result['conditional_diagnostics'])} hints, "
                  f"{unresolved} unresolved rule contexts; no edit permission")
        if result['conditional_diagnostics']:
            print('Helper hints require successful module initialization and exact runtime package selection; no edit permission')
        for blocker in result['blockers']:
            print(blocker)
        print('Global coverage incomplete (exit 2); compiler not invoked')
    return result['status']


if __name__ == '__main__':
    raise SystemExit(main())
