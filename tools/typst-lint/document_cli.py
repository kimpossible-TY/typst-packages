"""Read-only literal-include inventory and script proposals for one document.

This route deliberately evaluates just the syntax-local script rule. Its
in-memory candidates never authorize a source transaction or compilation.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
import sys

from edit_plan import plan
import include_context
from package_loader import PackageInputError, _absolute_root, read_snapshot_file
from script_rule import RULE_ID, analyze
from syntax_client import parse
from typst_lint import (InputError, VERSION, checked_path, digest, load_rules,
                        relative, require)

INCLUDE_BUDGET = 'book-v1'
MAX_REPORT_ITEMS = 100_000


def report_document(root, entrypoint):
    """Inspect retained source bytes without reading consumer configuration.

    Literal includes inside unknown containers are inventory entries, not proof
    of execution. Each unique captured source is checked once, while repeated
    include occurrences retain their separate site chains in the report.
    """
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
    findings, proposed, suppressed, files = [], [], [], []
    items = len(captured.sources) + len(captured.edges) + len(captured.occurrences) + len(captured.unresolved)
    for item in captured.sources:
        try:
            result = analyze(item.tree, item.path, catalog=catalog['rules'], _verify=False)
        except (InputError, OSError, ValueError) as error:
            raise InputError(f'{item.path}: {error}') from error
        # Keep unsupported baseline operands diagnostic-only, then independently
        # require complete script coverage from the candidate's fresh parse.
        edits = () if result.skips else result.edits
        candidate_plan = plan(item.source, edits)
        require(not candidate_plan.deferred, 'Document script proposals contain conflicting edits')
        items += len(result.diagnostics) + len(result.edits) + len(result.suppressed)
        require(items <= MAX_REPORT_ITEMS, 'Document inspection report item limit exceeded')
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
            candidate_verification='parse-and-rule-idempotence-checked' if verified else 'no-candidate'))
    findings.sort(key=lambda row: (row['path'], row['line'], row['column'], row['rule_id']))
    suppressed.sort(key=lambda row: (row['path'], row['line'], row['column'], row['rule_id']))
    edges = []
    for edge in captured.edges:
        record = asdict(edge)
        record['raw_request'] = edge.raw_request.decode('utf-8')
        edges.append(record)
    return dict(schema=1, tool_version=VERSION, mode='inspect-document', status=2, phase='analysis',
        root=str(root), entrypoint=entrypoint, compiler_invoked=False, applied_fixes=[],
        consumer_config_loaded=False, package_resolution_performed=False,
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
        coverage=dict(global_complete=False, available_backends=[RULE_ID], files=files,
            unresolved_obligations=['other-style-rules', 'import-and-package-resolution',
                'include-runtime-execution', 'render-context-and-resources',
                'protected-source-transaction', 'compiler-snapshot']),
        blockers=['Only core.parenthesized-scripts is inspected; full document rule coverage is incomplete',
                  'Literal include reachability does not establish runtime execution or imported dependencies',
                  'Proposals are read-only; no protected source transaction or compiler is invoked'])


def main(arguments=None):
    parser = argparse.ArgumentParser(prog='typst-lint inspect-document', description=__doc__,
        epilog='Uses the fixed book-v1 include budget (64 files, 400,000 syntax nodes). '
               'Consumer configuration and packages are not loaded; inline suppressions are honored. '
               'Incomplete global coverage returns 2 even with zero findings.')
    parser.add_argument('--root', required=True, help='Canonical absolute consumer root')
    parser.add_argument('--entrypoint', required=True, help='Canonical project-relative .typ entrypoint')
    parser.add_argument('--format', choices=('json', 'text'), default='text')
    args = parser.parse_args(sys.argv[1:] if arguments is None else arguments)
    try:
        result = report_document(args.root, args.entrypoint)
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
              f"{len(result['diagnostics'])} script findings")
        for finding in result['diagnostics']:
            print(f"{finding['path']}:{finding['line']}:{finding['column']}: "
                  f"{finding['rule_id']}: {finding['explanation']}")
        for blocker in result['blockers']:
            print(blocker)
        print('Global coverage incomplete (exit 2); compiler not invoked')
    return result['status']


if __name__ == '__main__':
    raise SystemExit(main())
