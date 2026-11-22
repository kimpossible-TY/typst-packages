#!/usr/bin/env python3
"""Audit all normative rule IDs through the real, read-only CLI.

Exit 2 means incomplete implementation/acceptance, NOT a successful lint run.
Exit 1 means audit invariant failure or a mismatch in a claimed evaluated subset.
No Typst source, imports or compiler arguments are evaluated by this harness.
Proposed edits are reproduced only in a NEW temporary consumer for reanalysis.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import syntax_client

TOOL = Path(__file__).resolve().parent
REPO = TOOL.parents[1]
SPEC = REPO / 'openspec/changes/typst-style-linter'
ADAPTER = syntax_client.EXECUTABLE
ADAPTER_WORKER = TOOL / 'audit_adapter_worker.py'
INVENTORY = TOOL / 'audit/fixtures.json'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def git(*args):
    return subprocess.check_output(['git', '-C', str(REPO), *args], text=True).strip()


def snapshot():
    paths = set(TOOL.glob('*.py')) | set((TOOL / 'tests').rglob('*.py'))
    paths |= set((TOOL / 'tests/fixtures').rglob('*'))
    paths |= set((TOOL / 'rules').glob('*.json')) | set(SPEC.rglob('*.md'))
    paths |= set((TOOL / 'syntax-adapter/src').glob('*.rs'))
    paths |= {TOOL / 'typst-lint', TOOL / 'typst-style',
              TOOL / 'syntax-adapter/Cargo.toml', TOOL / 'syntax-adapter/Cargo.lock',
              INVENTORY}
    # Hash the repository package source/manifest inventory, not a hardcoded
    # singleton. This covers every currently reviewed package and future additions
    # without executing its imports or reading runtime JSON catalogs.
    paths |= set((REPO / 'local').rglob('*.typ'))
    paths |= set((REPO / 'local').rglob('typst.toml'))
    files = {p.relative_to(REPO).as_posix(): digest(p.read_bytes()) for p in sorted(paths) if p.is_file()}
    adapter = syntax_client.audit_adapter_inventory()
    public_candidates = {name: (None if state is None else
                                {'kind': state['kind'], 'sha256': state['sha256']})
                         for name, state in adapter['candidates'].items()}
    return {'commit': git('rev-parse', 'HEAD'), 'branch': git('branch', '--show-current'),
            'files': files, 'working_tree_sha256': digest(json.dumps(files, sort_keys=True).encode()),
            'adapter_path': adapter['path'], 'adapter_sha256': adapter['sha256'],
            'adapter_candidates': public_candidates}


def normalize(value, root, package_root=None):
    if isinstance(value, dict):
        return {key: normalize(item, root, package_root) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item, root, package_root) for item in value]
    if isinstance(value, str):
        normalized = value.replace(str(root), '<fixture>').replace(str(REPO), '<repo>')
        return normalized.replace(str(package_root), '<package-fixture>') if package_root else normalized
    return value


def spec_index():
    requirements = {}
    for pack in ('core', 'pde'):
        path = SPEC / f'specs/typst-{pack}-style/spec.md'
        rid = None
        for line_no, line in enumerate(path.read_text().splitlines(), 1):
            if line.startswith('### Requirement: '):
                rid = line.split(': ', 1)[1]
                requirements[rid] = {'spec': path.relative_to(REPO).as_posix(), 'line': line_no, 'scenarios': []}
            elif line.startswith('#### Scenario: '):
                requirements[rid]['scenarios'].append({'heading': line.split(': ', 1)[1], 'line': line_no})
    repair_lines = (SPEC / 'fix-policy.md').read_text().splitlines()
    for rid, item in requirements.items():
        item['repair_line'] = next(i for i, line in enumerate(repair_lines, 1) if line.startswith(f'| `{rid}` |'))
    return requirements


def expected_input_refusal(fixture, report):
    expected = fixture.get('expected_input_error')
    return bool(expected and report.get('phase') == 'input' and
                expected in report.get('error', ''))


def run_cli(fixture, mode='dry-run', source_files=None, *, _analysis_contexts=None):
    with tempfile.TemporaryDirectory(prefix='typst-rule-audit-') as directory, \
            tempfile.TemporaryDirectory(prefix='typst-rule-package-snapshot-') as package_directory:
        root = Path(directory).resolve()
        package_root = None
        package_before = {}
        if fixture.get('scoped_namespace_evidence') or fixture.get('supplemental_evidence'):
            package_root = Path(package_directory).resolve()
            fixtures = TOOL / 'tests/fixtures/scoped-reference-packages'
            artifacts = [
                (REPO, 'local/scoped-annotations/0.3.0/typst.toml'),
                (REPO, 'local/scoped-annotations/0.3.0/lib.typ'),
                # Facade inventory fixtures select their local text-utils
                # imports too. These exact copies supply only requested
                # source evidence, never an evaluated dependency graph.
                ((TOOL / 'tests/fixtures/reviewed-packages'), 'local/text-utils/0.1.2/typst.toml'),
                ((TOOL / 'tests/fixtures/reviewed-packages'), 'local/text-utils/0.1.2/lib.typ'),
                (fixtures, 'preview/mannot/0.4.0/typst.toml'),
                (fixtures, 'preview/mannot/0.4.0/src/lib.typ'),
            ]
            if fixture.get('supplemental_evidence'):
                artifacts = [(base, relative) for base, relative in artifacts
                             if not relative.startswith('preview/')]
                for family in ('marker-bounds-packages', 'anchor-overlay-packages'):
                    base = TOOL / 'tests/fixtures' / family
                    artifacts.extend((base, path.relative_to(base).as_posix())
                                     for path in base.rglob('*') if path.is_file())
            for base, relative in artifacts:
                raw = (base / relative).read_bytes()
                target = package_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                package_before[relative] = digest(raw)
        files = dict(fixture['files'] if source_files is None else source_files)
        files['.typst-lint.toml'] = fixture['config']
        for name, source in files.items():
            path = root / name
            if path.is_absolute() and not path.is_relative_to(root):
                raise ValueError('Fixture path escapes its temporary consumer')
            if '..' in Path(name).parts:
                raise ValueError('Fixture path contains traversal')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(source.encode('utf-8'))
        before = {name: digest((root / name).read_bytes()) for name in files}
        # A safety sentinel, not a real compiler. Existing output must stay intact.
        (root / 'output.pdf').write_bytes(b'AUDIT OUTPUT SENTINEL\n')
        tripwire = root / '.audit-bin'
        tripwire.mkdir()
        compiler = tripwire / 'typst'
        compiler.write_text('#!/bin/sh\nprintf called > "$AUDIT_COMPILER_MARKER"\nexit 97\n')
        compiler.chmod(0o700)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
                   PATH=str(tripwire) + os.pathsep + os.environ.get('PATH', ''),
                   AUDIT_COMPILER_MARKER=str(root / '.compiler-called'))
        launcher = TOOL / ('typst-style' if mode == 'compile' else 'typst-lint')
        command = 'compile' if mode == 'compile' else 'check' if mode == 'check' else 'fix'
        args = [sys.executable, str(launcher), command]
        if mode == 'dry-run':
            args += ['--dry-run']
        args += ['--root', str(root)]
        if fixture['kind'] != 'missing-package-path':
            args += ['--package-path', str(package_root or REPO)]
        args += ['--format', 'json']
        if mode == 'compile':
            args += ['--', 'main.typ', 'output.pdf']
        adapter = syntax_client.audit_adapter_inventory()
        worker_args = [sys.executable, str(ADAPTER_WORKER), json.dumps(adapter, sort_keys=True), *args[1:]]
        process = subprocess.run(worker_args, capture_output=True, text=True, timeout=30, env=env)
        if syntax_client.audit_adapter_inventory() != adapter:
            raise syntax_client.AdapterError('Syntax adapter changed during audit child execution')
        after = {name: digest((root / name).read_bytes()) if (root / name).is_file() else None for name in files}
        try:
            report = json.loads(process.stdout)
        except json.JSONDecodeError:
            report = None
        record = {'mode': mode, 'command': ['python3', *normalize(args[1:], root, package_root)],
                  'exit_code': process.returncode, 'stderr': normalize(process.stderr, root, package_root),
                  'report': normalize(report, root, package_root), 'source_hashes_before': before, 'source_hashes_after': after,
                  'source_unchanged': before == after,
                  'package_fixture_hashes': package_before,
                  'package_fixture_unchanged': all((package_root / name).is_file() and
                      digest((package_root / name).read_bytes()) == expected
                      for name, expected in package_before.items()),
                  'compiler_tripwire_untouched': not (root / '.compiler-called').exists(),
                  'output_sentinel_unchanged': (root / 'output.pdf').read_bytes() == b'AUDIT OUTPUT SENTINEL\n'}
        issues = []
        if report is None:
            issues.append('CLI did not emit valid JSON')
            record['non_json_stdout'] = normalize(process.stdout[:4096], root, package_root)
        else:
            if process.returncode != 2 or report.get('status') != 2:
                issues.append('Expected current partial runtime status/exit 2')
            expected_refusal = expected_input_refusal(fixture, report)
            record['expected_input_refusal_matched'] = expected_refusal
            if fixture.get('expected_input_error') and not expected_refusal:
                issues.append('Expected explicit input refusal was not observed')
            if report.get('compiler_invoked') is not False or report.get('applied_fixes', [] if expected_refusal else None) != []:
                issues.append('CLI did not affirm no compiler/no source applications')
            if not expected_refusal and report.get('coverage', {}).get('complete') is not False:
                issues.append('Incomplete runtime incorrectly reports complete coverage')
            if report.get('phase') == 'analysis' and 'catalog.json' in before:
                if report.get('hashes', {}).get('reference_catalog') != before['catalog.json']:
                    issues.append('Validated catalog hash differs from independent fixture hash')
            for item in report.get('inputs', []):
                if before.get(item['path']) != item['sha256']:
                    issues.append('Input hash differs from independently read fixture hash')
            for edit in report.get('proposed_edits', []):
                if edit.get('baseline_sha256') != before.get(edit['path']):
                    issues.append('Proposed edit baseline hash mismatch')
        for field in ('source_unchanged', 'package_fixture_unchanged', 'compiler_tripwire_untouched', 'output_sentinel_unchanged'):
            if not record[field]:
                issues.append(field + ' invariant failed')
        record['invariant_issues'] = issues
        if _analysis_contexts is not None and report is not None and report.get('phase') == 'analysis':
            # Capture an independent raw-analysis context while exact temporary
            # fixture resources still exist. Its subsequent candidate analyses
            # use immutable bytes only; contexts never enter serialized reports.
            from typst_lint import report as capture_report
            captured = capture_report(root, 'check',
                package_path=None if fixture['kind'] == 'missing-package-path' else package_root or REPO,
                _verify=False, _analysis_observer=_analysis_contexts.__setitem__)
            if (captured.get('hashes') != report.get('hashes')
                    or captured.get('inputs') != report.get('inputs')
                    or json.loads(json.dumps(captured.get('package_evidence'))) != report.get('package_evidence')
                    or any(digest((root / name).read_bytes()) != expected for name, expected in before.items())
                    or any(digest((package_root / name).read_bytes()) != expected
                           for name, expected in package_before.items())):
                issues.append('Independent analysis capture differs from actual CLI resource snapshot')
                _analysis_contexts.clear()
        return record


def apply_proposals(fixture, report):
    """Apply reported edits to memory only; another new temp consumer checks it."""
    files = dict(fixture['files'])
    by_path = {}
    for edit in report.get('proposed_edits', []):
        by_path.setdefault(edit['path'], []).append(edit)
    for name, edits in by_path.items():
        raw = files[name].encode('utf-8')
        previous = len(raw) + 1
        for edit in sorted(edits, key=lambda e: (e['start'], e['end']), reverse=True):
            start, end = edit['start'], edit['end']
            if not (0 <= start <= end <= len(raw) and end <= previous):
                raise ValueError('Invalid or overlapping proposal bounds')
            raw = raw[:start] + edit['replacement'].encode('utf-8') + raw[end:]
            previous = start
        files[name] = raw.decode('utf-8')
    return files


def candidate_verdict(baseline, candidate, source_files, candidate_files, *, analysis_contexts=None):
    """Verify zero edits only when all repaired analysis remains supported.

    Globally incomplete coverage is expected. Existing bounded partial skips can
    remain, but every baseline-supported pair and every repaired pair must retain
    its proof. Byte offsets in skip reasons and surviving findings are mapped
    through the proposed edits instead of mistaking shifts for new refusals.
    """
    blocked, mismatches = [], []
    edits, repaired, replays = [], set(), {}
    from syntax_client import AdapterError
    from typst_lint import InputError, SafetyError, load_rules
    from edit_plan import Edit, plan
    from convergence_evidence import authenticate_candidate
    try:
        proposals = baseline.get('proposed_edits', [])
        if type(proposals) is not list:
            raise SafetyError('Proposals must be a list')
        known = {r['id'] for r in load_rules()[0]['rules']}
        for edit in proposals:
            if (type(edit) is not dict or type(edit.get('path')) is not str
                    or type(edit.get('rule_id')) is not str or edit['rule_id'] not in known):
                raise SafetyError('Malformed proposal identity')
            ids = edit.get('rule_ids', [edit['rule_id']])
            if (type(ids) is not list or not ids
                    or any(type(rid) is not str or rid not in known for rid in ids)
                    or ids != sorted(set(ids)) or edit['rule_id'] not in ids):
                raise SafetyError('Malformed proposal attribution')
            if ('repair_passes' in edit and (type(edit['repair_passes']) is not int
                    or not 1 <= edit['repair_passes'] <= 8)):
                raise SafetyError('Invalid proposal pass count')
            raw = source_files[edit['path']].encode()
            if edit.get('baseline_sha256') != digest(raw):
                raise SafetyError('Proposal baseline hash is missing or mismatched')
            plan(raw, (Edit(edit['start'], edit['end'], edit['replacement'].encode()),))
            edits.append(edit)
            repaired.update((edit['path'], rid) for rid in ids)
        evidence = baseline.get('convergence_evidence', [])
        if type(evidence) is not list:
            raise SafetyError('Convergence evidence must be a list')
        records = {}
        for record in evidence:
            if type(record) is not dict or type(record.get('path')) is not str or record['path'] in records:
                raise SafetyError('Malformed or duplicate convergence evidence path')
            records[record['path']] = record
        paths = {edit['path'] for edit in edits}
        if set(records) - paths:
            raise SafetyError('Convergence evidence has no repaired input')
        if paths and not isinstance(analysis_contexts, dict):
            raise SafetyError('Independent captured raw-analysis context is required')
        for path in paths:
            original, final = source_files[path].encode(), candidate_files[path].encode()
            selected = [e for e in edits if e['path'] == path]
            result = plan(original, (Edit(e['start'], e['end'], e['replacement'].encode()) for e in selected))
            if result.deferred or result.candidate() != final:
                raise SafetyError('Reported baseline plan differs from candidate')
            context = analysis_contexts.get(path)
            if getattr(context, 'path', None) != path:
                raise SafetyError('Independent context path does not match repaired input')
            proof = authenticate_candidate(context, original, final, records.get(path))
            actual = sorted((e['start'], e['end'], e['replacement'].encode(), e['rule_id'],
                             tuple(e.get('rule_ids', [e['rule_id']]))) for e in selected)
            expected = sorted((a.edit.start, a.edit.end, a.edit.replacement, a.primary_rule_id, a.rule_ids)
                              for a in proof.edits)
            if actual != expected or any(e.get('repair_passes', 1) != proof.passes for e in selected):
                raise SafetyError('Proposal attribution/pass count differs from independently recomputed lineage')
            replays[path] = proof
    except (AdapterError, InputError, SafetyError, KeyError, TypeError, ValueError, AttributeError) as error:
        blocked.append(f'Cannot verify candidate repair ownership: {error}')

    def mapped(path, position):
        if path in replays:
            return replays[path].map_offset(position)
        # Failure above permanently blocks certification. Keep collecting other
        # report issues without inventing an ownership map for the missing proof.
        return position

    def coverage_rows(report):
        rows = {}
        for row in report.get('coverage', {}).get('files', []):
            pair = (row['path'], row['rule_id'])
            if pair in rows:
                blocked.append(f'Duplicate per-file coverage row: {pair}')
            rows[pair] = row
        return rows

    before_rows = coverage_rows(baseline)
    after_rows = coverage_rows(candidate)
    checked = repaired | {pair for pair, row in before_rows.items()
                          if row['status'] in ('evaluated', 'partial')}
    if candidate.get('phase') != 'analysis' or candidate.get('parse_failures') or candidate.get('configuration_failures'):
        blocked.append('Candidate analysis is absent or has parse/configuration failures')
    for pair in sorted(checked):
        before, after = before_rows.get(pair), after_rows.get(pair)
        if not before or before['status'] not in ('evaluated', 'partial'):
            blocked.append(f'No supported baseline proof for repaired pair: {pair}')
            continue
        if not after or after['status'] not in ('evaluated', 'partial'):
            blocked.append(f'Candidate lost supported per-file coverage: {pair}')
            continue
        if pair[1] not in candidate.get('coverage', {}).get('enabled', []):
            blocked.append(f'Candidate no longer enables analyzed rule: {pair}')
        if before['status'] == 'evaluated' and after['status'] != 'evaluated':
            blocked.append(f'Candidate coverage downgraded from evaluated: {pair}')
        previous_reasons = Counter(re.sub(r'at byte (\d+)',
            lambda match: f"at byte {mapped(pair[0], int(match.group(1)))}", reason)
            for reason in before.get('reasons', []))
        new_reasons = Counter(after.get('reasons', [])) - previous_reasons
        if new_reasons:
            blocked.append(f'Candidate introduced analysis refusal for {pair}: {list(new_reasons.elements())}')
    if not repaired:
        blocked.append('No repaired rule/file pair to verify')
    if candidate.get('proposed_edits'):
        mismatches.append('Candidate still proposes edits')

    def findings(report, files, transform):
        found = Counter()
        for item in report.get('diagnostics', []):
            path = item['path']
            raw = files[path].encode('utf-8')
            lines = raw.split(b'\n')
            line, column = item['line'], item['column']
            if not (1 <= line <= len(lines)) or column < 1:
                raise ValueError('Invalid diagnostic source location')
            text = lines[line - 1].decode('utf-8')
            if column > len(text) + 1:
                raise ValueError('Diagnostic column is outside source')
            offset = sum(len(part) + 1 for part in lines[:line - 1]) + len(text[:column - 1].encode('utf-8'))
            explanation = item['explanation']
            related = re.fullmatch(r'Duplicate literal scope namespace; first declaration at byte (\d+)', explanation)
            if item['rule_id'] == 'core.unique-scope-namespace' and related:
                explanation = f'Duplicate literal scope namespace; first declaration at byte {transform(path, int(related.group(1)))}'
            found[(item['rule_id'], item['grade'], path, transform(path, offset),
                   explanation, item.get('source'))] += 1
        return found

    try:
        previous = findings(baseline, source_files, mapped)
        current = findings(candidate, candidate_files, lambda path, position: position)
        if current - previous:
            mismatches.append('Candidate introduces unexpected active findings')
    except (KeyError, TypeError, ValueError, UnicodeError) as error:
        blocked.append(f'Cannot verify candidate finding ownership: {error}')
    status = 'BLOCKED_ANALYSIS' if blocked else 'MISMATCH' if mismatches else 'VERIFIED_SUBSET'
    return {'status': status, 'verified': status == 'VERIFIED_SUBSET',
            'reasons': blocked + mismatches,
            'repaired_pairs': [{'path': path, 'rule_id': rid} for path, rid in sorted(repaired)],
            'checked_pairs': [{'path': path, 'rule_id': rid} for path, rid in sorted(checked)]}


def observe(fixture):
    analysis_contexts = {}
    result = {'fixture_id': fixture['id'], 'rule_id': fixture['rule_id'], 'kind': fixture['kind'],
              'normative_diagnostics': fixture['normative_diagnostics'],
              'run': run_cli(fixture, _analysis_contexts=analysis_contexts)}
    run = result['run']
    report = run['report'] or {}
    rid = fixture['rule_id']
    rows = [r for r in report.get('coverage', {}).get('files', []) if r['rule_id'] == rid]
    findings = [r for r in report.get('diagnostics', []) if r['rule_id'] == rid]
    edits = [r for r in report.get('proposed_edits', []) if rid in r.get('rule_ids', [r['rule_id']])]
    result.update(target_findings=len(findings), target_edits=len(edits), target_coverage=rows,
                  backend_observed=bool(rows), expected_candidate_verified=None,
                  candidate_idempotence_verified=None)
    if report.get('parse_failures'):
        result['observation'] = 'EXPECTED_PARSE_REFUSAL' if fixture['intentional_parse_error'] else 'PARSE_BLOCKED'
    elif expected_input_refusal(fixture, report):
        result['observation'] = 'EXPECTED_INPUT_REFUSAL'
    elif report.get('configuration_failures') or report.get('phase') != 'analysis':
        result['observation'] = 'INPUT_OR_CONFIG_BLOCKED'
    elif not rows:
        result['observation'] = 'NOT_IMPLEMENTED'
    elif any(row['status'] in ('unsupported', 'configuration-failure') for row in rows):
        result['observation'] = 'UNSUPPORTED_CASE'
    elif any(row['status'] == 'partial' for row in rows):
        result['observation'] = 'PARTIAL_CASE'
    elif fixture['normative_diagnostics'] is not None and len(findings) != fixture['normative_diagnostics']:
        result['observation'] = 'MISMATCH'
    else:
        result['observation'] = 'OBSERVED_SUBSET'
    expected = fixture['normative_diagnostics']
    result['diagnostic_count_comparison'] = ('NOT_ASSERTED' if expected is None else
                                            'MATCH' if len(findings) == expected else 'DIFFERENT')
    if expected == 0 and findings:
        result['observation'] = 'MISMATCH'
    if fixture.get('assert_normative_count') and expected is not None and rows and len(findings) != expected:
        result['observation'] = 'MISMATCH'
    if fixture.get('expected_no_target_edits') and edits:
        result['observation'] = 'MISMATCH'
    if fixture.get('expected_input_error') and not expected_input_refusal(fixture, report):
        result['observation'] = 'MISMATCH'
    if fixture.get('expected_candidate') is not None:
        candidate = apply_proposals(fixture, report)
        result['candidate_hashes'] = {name: digest(source.encode()) for name, source in candidate.items()}
        result['expected_candidate_verified'] = all(candidate[name] == text for name, text in fixture['expected_candidate'].items())
        candidate_run = run_cli(fixture, source_files=candidate)
        result['candidate_reanalysis'] = candidate_run
        candidate_report = candidate_run['report'] or {}
        verdict = candidate_verdict(report, candidate_report, fixture['files'], candidate,
                                    analysis_contexts=analysis_contexts)
        if candidate_run['invariant_issues']:
            verdict.update(status='BLOCKED_ANALYSIS', verified=False,
                           reasons=verdict['reasons'] + candidate_run['invariant_issues'])
        result['candidate_verification'] = verdict
        result['candidate_idempotence_verified'] = verdict['verified']
        if verdict['status'] == 'BLOCKED_ANALYSIS':
            result['observation'] = 'CANDIDATE_ANALYSIS_BLOCKED'
        elif not result['expected_candidate_verified'] or not verdict['verified']:
            result['observation'] = 'MISMATCH'
    return result


MODULES = {
    'core.scoped-marker-tags': ['marker_bounds_rules.py', 'scoped_provenance.py', 'package_artifacts.py', 'package_loader.py', 'static_resolver.py', 'source_analysis.py'],
    'core.annotation-marker-bounds': ['marker_bounds_rules.py', 'scoped_provenance.py', 'package_artifacts.py', 'package_loader.py', 'static_resolver.py', 'source_analysis.py'],
    'core.scoped-anchor-access': ['anchor_overlay_rules.py', 'marker_bounds_rules.py', 'scoped_provenance.py', 'package_artifacts.py', 'package_loader.py', 'static_resolver.py', 'source_analysis.py'],
    'core.local-overlay-api': ['anchor_overlay_rules.py', 'scoped_provenance.py', 'package_artifacts.py', 'package_loader.py', 'static_resolver.py', 'source_analysis.py'],
    'pde.import-order': ['import_rules.py', 'import_graph.py', 'source_analysis.py'],
    'pde.no-redundant-facade-import': ['import_rules.py', 'import_graph.py', 'source_analysis.py'],
    'pde.flowbox-colored-border': ['flowbox_rules.py', 'source_analysis.py'],

    'pde.theme-cancelmark': ['cancel_rules.py', 'static_resolver.py', 'source_analysis.py'],
    'core.single-book-show': ['book_rules.py', 'book_provenance.py', 'static_resolver.py', 'source_analysis.py'],
    'core.no-duplicate-book-reset': ['book_rules.py', 'book_provenance.py', 'static_resolver.py', 'source_analysis.py'],
    'pde.prose-paragraph-marker': ['prose_marker_rules.py', 'capitalization.py', 'static_resolver.py', 'source_analysis.py'],
    'pde.proof-trailing-gap': ['proof_rules.py', 'static_resolver.py', 'source_analysis.py'],
    'core.scoped-function-call': ['scoped_rules.py', 'scoped_provenance.py', 'source_analysis.py'],
    'core.scoped-reference-access': ['scoped_rules.py', 'scoped_provenance.py', 'source_analysis.py'],
    'core.annotation-in-display': ['scoped_rules.py', 'scoped_provenance.py', 'source_analysis.py'],
    'core.unique-scope-namespace': ['scoped_rules.py', 'scoped_provenance.py', 'source_analysis.py'],
    'core.no-set-theme': ['theme_rules.py', 'static_resolver.py', 'package_loader.py', 'source_analysis.py'],
    'core.theme-in-context': ['theme_rules.py', 'static_resolver.py', 'package_loader.py', 'source_analysis.py'],
    'pde.text-only-highlight': ['highlight_rules.py', 'static_resolver.py', 'source_analysis.py'],
    'pde.paragraph-marker-target': ['paragraph_rules.py', 'static_resolver.py', 'source_analysis.py'],
    'core.parenthesized-scripts': ['script_rule.py', 'markdown_scripts.py', 'syntax_client.py', 'source_analysis.py', 'convergence_evidence.py'],
    'pde.paragraph-marker-gap': ['paragraph_rules.py', 'static_resolver.py', 'package_loader.py', 'source_analysis.py'],
    'core.project-ref-arguments': ['reference_rules.py', 'reference_catalog.py', 'static_resolver.py', 'package_loader.py', 'source_analysis.py'],
    'core.project-ref-catalog-target': ['reference_rules.py', 'reference_catalog.py', 'static_resolver.py', 'package_loader.py', 'source_analysis.py'],
    'pde.standalone-display': ['display_rules.py', 'syntax_client.py', 'static_resolver.py', 'source_analysis.py'],
    'pde.display-continuation-gap': ['display_rules.py', 'syntax_client.py', 'static_resolver.py', 'source_analysis.py'],
    'pde.continuation-line': ['continuation_rules.py', 'syntax_client.py', 'static_resolver.py', 'source_analysis.py'],
    'pde.footnote-trailing-gap': ['footnote_rules.py', 'syntax_client.py', 'static_resolver.py', 'source_analysis.py'],
    'pde.prose-footnote-punctuation': ['footnote_rules.py', 'syntax_client.py', 'static_resolver.py', 'source_analysis.py'],
    'pde.aligned-footnote-spacer': ['footnote_rules.py', 'syntax_client.py', 'static_resolver.py', 'source_analysis.py'],
    'pde.unaligned-footnote-no-spacer': ['footnote_rules.py', 'static_resolver.py', 'source_analysis.py'],
    'pde.prose-footnote-no-spacer': ['footnote_rules.py', 'static_resolver.py', 'source_analysis.py'],
    'pde.paragraph-capitalization': ['capitalization.py', 'paragraph_rules.py', 'static_resolver.py', 'package_loader.py', 'source_analysis.py'],
}
TESTS = {
    'core.scoped-marker-tags': ['tests/test_marker_bounds_rules.py', 'tests/test_supplemental_artifacts.py'],
    'core.annotation-marker-bounds': ['tests/test_marker_bounds_rules.py', 'tests/test_supplemental_artifacts.py'],
    'core.scoped-anchor-access': ['tests/test_anchor_overlay_rules.py', 'tests/test_cetz_provenance.py'],
    'core.local-overlay-api': ['tests/test_anchor_overlay_rules.py', 'tests/test_cetz_provenance.py'],
    'pde.import-order': ['tests/test_import_graph.py', 'tests/test_import_rules.py', 'tests/test_import_integration.py', 'tests/test_import_render.py'],
    'pde.no-redundant-facade-import': ['tests/test_import_graph.py', 'tests/test_import_rules.py', 'tests/test_import_integration.py', 'tests/test_import_render.py'],
    'pde.flowbox-colored-border': ['tests/test_flowbox_rules.py', 'tests/test_flowbox_integration.py', 'tests/test_flowbox_render.py'],

    'pde.theme-cancelmark': ['tests/test_cancel_rules.py', 'tests/test_cancel_integration.py', 'tests/test_cancel_render.py'],
    'core.single-book-show': ['tests/test_book_rules.py', 'tests/test_book_integration.py', 'tests/test_book_render.py'],
    'core.no-duplicate-book-reset': ['tests/test_book_rules.py', 'tests/test_book_integration.py', 'tests/test_book_render.py'],
    'pde.prose-paragraph-marker': ['tests/test_prose_marker_rules.py', 'tests/test_prose_marker_integration.py', 'tests/test_prose_marker_render.py'],
    'pde.proof-trailing-gap': ['tests/test_proof_rules.py', 'tests/test_proof_integration.py', 'tests/test_proof_render.py'],
    'core.scoped-function-call': ['tests/test_scoped_rules.py', 'tests/test_new_rule_integration.py'],
    'core.scoped-reference-access': ['tests/test_scoped_rules.py', 'tests/test_scoped_reference_repairs.py', 'tests/test_scoped_namespace_loader.py', 'tests/test_new_rule_integration.py'],
    'core.annotation-in-display': ['tests/test_scoped_rules.py', 'tests/test_new_rule_integration.py'],
    'core.unique-scope-namespace': ['tests/test_scoped_rules.py', 'tests/test_new_rule_integration.py'],
    'core.no-set-theme': ['tests/test_theme_rules.py', 'tests/test_new_rule_integration.py'],
    'core.theme-in-context': ['tests/test_theme_rules.py', 'tests/test_new_rule_integration.py'],
    'pde.text-only-highlight': ['tests/test_highlight_rules.py', 'tests/test_highlight_render.py', 'tests/test_new_rule_integration.py'],
    'pde.paragraph-marker-target': ['tests/test_paragraph_target.py'],
    'core.project-ref-arguments': ['tests/test_reference_rules.py', 'tests/test_reference_catalog.py'],
    'core.project-ref-catalog-target': ['tests/test_reference_rules.py', 'tests/test_reference_catalog.py'],
    'pde.standalone-display': ['tests/test_display_rules.py'],
    'pde.display-continuation-gap': ['tests/test_display_rules.py'],
    'pde.continuation-line': ['tests/test_continuation_rules.py', 'tests/test_continuation_integration.py'],
    'pde.footnote-trailing-gap': ['tests/test_footnote_rules.py', 'tests/test_continuation_integration.py'],
    'pde.prose-footnote-punctuation': ['tests/test_footnote_rules.py', 'tests/test_continuation_integration.py'],
    'pde.aligned-footnote-spacer': ['tests/test_footnote_rules.py', 'tests/test_continuation_integration.py', 'tests/test_spacer_provenance.py', 'tests/test_footnote_render.py'],
    'pde.unaligned-footnote-no-spacer': ['tests/test_footnote_rules.py', 'tests/test_spacer_provenance.py', 'tests/test_footnote_render.py'],
    'pde.prose-footnote-no-spacer': ['tests/test_footnote_rules.py', 'tests/test_spacer_provenance.py', 'tests/test_footnote_render.py'],
    'core.parenthesized-scripts': ['tests/test_syntax.py', 'tests/test_suppression.py', 'tests/test_source_rules.py', 'tests/test_markdown_scripts.py', 'tests/test_markdown_integration.py'],
    'pde.paragraph-marker-gap': ['tests/test_source_rules.py', 'tests/test_source_rules_render.py'],
    'pde.paragraph-capitalization': ['tests/test_capitalization.py', 'tests/test_source_rules.py', 'tests/test_source_rules_render.py'],
}
LIMITS = {
    'core.scoped-marker-tags': 'Actual reviewed raw mark/annotation calls under recognized literal local callbacks only. Exact wrapper, namespace and definition artifacts required. Diagnostic only; no generated-label repair or general capture proof.',
    'core.annotation-marker-bounds': 'Complete direct display and ordered same-scope target events only. Diagnose the nearest preceding known plain label on a nonmetadata atom; never infer missing markers from global absence. Opaque output, argument effects, aliases losing target identity and collisions refuse. Diagnostic only.',
    'core.scoped-anchor-access': 'Genuine complete fixed CeTZ artifact evidence, direct line positional coordinate strings, same-display unique preceding applied marker and target ownership, safe dot-free scope prefix and eight compass suffixes only. Conditional scope.node repair; no opaque drawing or rendering-equivalence claim.',
    'core.local-overlay-api': 'Genuine raw mannot annot/annot-cetz calls under recognized local annotation callbacks only. Diagnostic only; local wrappers retained. General closures, facades and dynamic execution unresolved.',
    'pde.import-order': 'Immutable explicit relative import graph and pure literal/alias modules only. Reorder disjoint immediate styles/figures groups with exact statement identity and no comments/directives/dependencies. Package initializers, includes and arbitrary calls remain unsupported.',
    'pde.no-redundant-facade-import': 'Exact graph-derived original leaf binding identity must already be retained from a pure styles facade. Remove only standalone redundant compatibility import with no effects/comments/directive reassignment. No name-only facade proof.',
    'pde.flowbox-colored-border': 'Whole reviewed math-blocks module AST/token fingerprint with one permitted border slot only. Add its proven existing theme.rule to a literal width-only stroke. Fragments, changed bodies/themes/selectors and dynamic strokes refuse; light/dark isolated renders verify width and geometry.',

    'pde.theme-cancelmark': 'Proven builtin math cancellation in real mathematical source only, with distinct math/code namespace lookup and verified lexical/module aliases. Shadowed, unknown wildcard, dynamic/deferred and higher-order cases remain unresolved. No cancelmark replacement, facade identity or palette effect is certified.',
    'core.single-book-show': 'Explicit immutable configured root and paired reviewed book/blocks sources only. Direct root book/reset transform identity with bounded literal metadata/reset options; duplicate owners and adjacent same-reset ownership only. No missing-owner, included-file/graph claims, broad wildcard proofs or reset deletion without full body transformation equivalence.',
    'core.no-duplicate-book-reset': 'Explicit immutable configured root and paired reviewed book/blocks sources only. Direct root book/reset transform identity with bounded literal metadata/reset options; duplicate owners and adjacent same-reset ownership only. No missing-owner, included-file/graph claims, broad wildcard proofs or reset deletion without full body transformation equivalence.',
    'pde.prose-paragraph-marker': 'Existing whitespace-only boundaries between literal root prose paragraphs only. Insertions require exact canonical text-utils marker availability and an already installed reviewed show, unchanged directive targets, preserved prose and exact casing preflight; only eligible lowercase starters can obtain a separately parsed one-byte followup under eight-pass ownership checks, with no new final findings/refusals. Exact literal terminal-footnote boundaries can receive the complete marker-owned break-plus-marker insertion with canonical builtin availability; existing breaks remain untouched. Nested bodies, general break/call contexts, inline content, structural boundaries and unsupported starters remain partial.',
    'pde.proof-trailing-gap': 'Exact reviewed math-blocks proof helper at standalone root output only, with incoming safe style facts and a proven next sibling. No synthetic EOF paragraph, nested/deferred/facade ownership or ambiguous comment/label attachment. Candidate non-whitespace syntax must remain identical.',
    'core.scoped-function-call': 'Exact reviewed scoped-annotations wrapper imports and literal one-parameter callbacks only. No raw mannot, facade or graph-wide namespace coverage; reference repair requires one literal tag target, its exact scope receiver and explicit reviewed mannot namespace evidence. Mutations, includes and dynamic branches withhold scope proofs.',
    'core.scoped-reference-access': 'Exact reviewed scoped-annotations wrapper imports and literal one-parameter callbacks only. No raw mannot, facade or graph-wide namespace coverage; reference repair requires one literal tag target, its exact scope receiver and explicit reviewed mannot namespace evidence. Mutations, includes and dynamic branches withhold scope proofs.',
    'core.annotation-in-display': 'Exact reviewed scoped-annotations wrapper imports and literal one-parameter callbacks only. No raw mannot, facade or graph-wide namespace coverage; reference repair requires one literal tag target, its exact scope receiver and explicit reviewed mannot namespace evidence. Mutations, includes and dynamic branches withhold scope proofs.',
    'core.unique-scope-namespace': 'Exact reviewed scoped-annotations wrapper imports and literal one-parameter callbacks only. No raw mannot, facade or graph-wide namespace coverage; reference repair requires one literal tag target, its exact scope receiver and explicit reviewed mannot namespace evidence. Mutations, includes and dynamic branches withhold scope proofs.',
    'core.no-set-theme': 'Exact reviewed math-blocks 0.2.0 palette/helper values and aliases only; local facades and other versions unresolved. Diagnostic only.',
    'core.theme-in-context': 'Exact theme helper within reusable closure bodies, proven immutable theme captures and lexical context only. No context insertion without complete content-result and call-site proof. Local facades and arbitrary colors are not inferred.',
    'pde.text-only-highlight': 'Builtin highlight and reviewed text-utils highlighted with literal content. Wrapper-only extraction preserves direct equations and prose bytes. Options, nested styles/calls, comments/directives, labels and ambiguous arguments refuse repair; bounded later display passes require exact original-source composition and independently authenticated ownership.',
    'pde.paragraph-marker-target': 'Proven canonical standalone root markers before heading/display siblings, across whitespace/comments. Nested, captured, general facade and noncanonical marker forms remain partial; certified named text-utils forwarding preserves existing identity. Directives targeting a removed marker refuse repair rather than acquire another target.',
    'core.project-ref-arguments': 'Only proven direct root calls, reviewed project-references package/aliases and bounded literal types. Exact [@label] repair needs an explicit unique catalog target. Facade graphs, nested/captured calls, dynamic types and generated labels remain unsupported.',
    'core.project-ref-catalog-target': 'Immutable explicit catalogs and direct literal project/label calls only. No catalog is an optional skip, not a clean target check. Facades, dynamic/global identity proofs and broader document graphs remain unsupported.',
    'pde.standalone-display': 'Typed Equation.display proof and root ordinary prose only. Nested/captured bodies, unknown inline wrappers, ambiguous labels, comments and general content graphs remain partial.',
    'pde.display-continuation-gap': 'Root standalone displays, one known attached label, whitespace-only gap and direct unmarked prose only. Nested contexts, comments and unknown wrappers remain incomplete; verified standalone-display followup passes retain exact source ownership.',
    'pde.continuation-line': 'Direct root Text/Space/Text prose only; periods require a small word allowlist, questions and exclamations use bounded literal forms. Nested bodies, inline content, other vocabulary and ambiguous boundaries remain unsupported.',
    'pde.footnote-trailing-gap': 'Only proven builtin literal-form root prose or direct root display footnotes. A proven literal root footnote boundary with an existing reviewed marker permits only next-line canonical builtin break insertion. Missing markers use the complete marker-owned repair; unknown shows/break bindings, comments and nested bodies remain unsupported.',
    'pde.prose-footnote-punctuation': 'Only direct root prose and proven builtin literal-form calls, with bounded punctuation repairs. Inline wrappers, nested bodies and ambiguous punctuation are unsupported.',
    'pde.aligned-footnote-spacer': 'Direct display-level footnotes and typed alignment only. Insertion requires the exact reviewed math-blocks 0.2.0 canonical dots_space binding; aliases prove removal only. Nested contexts, facades, unknown wildcards and ambiguous preceding expressions remain unsupported.',
    'pde.unaligned-footnote-no-spacer': 'Direct root display footnotes and actual source-bound spacer value proof, including aliases/module access. No spelling-only, facade, deferred or unknown preceding-expression repair. Nested ownership and richer math contexts remain incomplete.',
    'pde.prose-footnote-no-spacer': 'Direct root prose footnotes with actual spacer identity and pre-spacer prose safety. Removal also preserves existing punctuation adjacency; ambiguous punctuation requires unsupported composition. Facades and nested bodies remain incomplete.',
    'core.parenthesized-scripts': 'Explicitly included bounded Markdown fences and same-line math examples use actual parser/source mappings and the exact teaching exemption. General Markdown contexts, multiline examples and ambiguous escaped/commented operands remain unsupported. Ordinary CLI plans remain read-only and compilation unavailable; the explicit closed-core offline profile has separate per-input proof and does not change general rule coverage.',
    'pde.paragraph-marker-gap': 'Only proven root prose with reviewed direct text-utils binding. Nested/body-start contexts and footnote-combined repair unsupported; comments can make repair unavailable.',
    'pde.paragraph-capitalization': 'Only proven root prose, canonical marker spelling, bounded starter/language policy. Nested/dynamic contexts remain unsupported; eligible lowercase starters can follow actual marker insertion under bounded convergence and exact ownership checks.',
}


for _rule_id in ('pde.prose-paragraph-marker', 'pde.paragraph-capitalization',
                 'pde.paragraph-marker-target', 'pde.paragraph-marker-gap',
                 'pde.text-only-highlight', 'pde.footnote-trailing-gap'):
    MODULES[_rule_id] = [*MODULES[_rule_id], 'facade_provenance.py']
    TESTS[_rule_id] = [*TESTS[_rule_id], 'tests/test_facade_provenance.py',
        'tests/test_facade_integration.py', 'tests/test_facade_render.py']

for _rule_id in ('pde.prose-paragraph-marker', 'pde.footnote-trailing-gap'):
    MODULES[_rule_id] = [*MODULES[_rule_id], 'prose_boundary.py']
    TESTS[_rule_id] = [*TESTS[_rule_id], 'tests/test_footnote_composition.py',
        'tests/test_footnote_composition_render.py', 'tests/test_footnote_paragraph_integration.py']

for _rule_id in MODULES:
    MODULES[_rule_id] = list(dict.fromkeys([*MODULES[_rule_id], 'candidate_convergence.py']))
for _rule_id in ('pde.text-only-highlight', 'pde.standalone-display', 'pde.display-continuation-gap',
                 'core.scoped-function-call', 'core.scoped-reference-access'):
    TESTS[_rule_id] = [*TESTS[_rule_id], 'tests/test_candidate_convergence.py', 'tests/test_multi_pass_integration.py']
for _rule_id in ('pde.prose-paragraph-marker', 'pde.paragraph-capitalization'):
    TESTS[_rule_id] = [*TESTS[_rule_id], 'tests/test_marker_followup.py', 'tests/test_marker_followup_render.py']


def summarize(fixtures, results, specs):
    rules = json.loads((TOOL / 'rules/catalog.json').read_text())['rules']
    inventory = {f['id']: f for f in fixtures}
    summary = []
    for rule in rules:
        rid = rule['id']
        cases = [r for r in results if r['rule_id'] == rid]
        status = ('MISMATCH' if any(r['observation'] == 'MISMATCH' for r in cases) else
                  'PARTIAL' if any(r['backend_observed'] for r in cases) else 'NOT_IMPLEMENTED')
        scenarios = []
        for scenario in specs[rid]['scenarios']:
            selected = [r for r in cases if inventory[r['fixture_id']]['scenario_heading'] == scenario['heading']]
            scenarios.append({**scenario, 'fixture_ids': [r['fixture_id'] for r in selected],
                              'execution': 'SMOKE_ONLY' if selected else 'NOT_EXECUTED',
                              'acceptance': 'NOT_COMPLETE',
                              'observations': dict(Counter(r['observation'] for r in selected))})
        summary.append({'rule_id': rid, 'status': status, 'repair_policy': rule['repair'],
            'spec': specs[rid]['spec'], 'spec_line': specs[rid]['line'],
            'repair_line': specs[rid]['repair_line'],
            'implementation_modules': MODULES.get(rid, []), 'existing_test_sources_not_rerun_here': TESTS.get(rid, []),
            'fixture_ids': [r['fixture_id'] for r in cases], 'scenarios': scenarios,
            'acceptance_dimensions': {
                'positive_negative': 'REPRESENTATIVE_SMOKE_ONLY',
                'exact_repair': 'OBSERVED_SUBSET' if any(r['expected_candidate_verified'] for r in cases) else 'NOT_VERIFIED',
                'idempotence': 'OBSERVED_SUBSET' if any(r['candidate_idempotence_verified'] for r in cases) else 'NOT_VERIFIED',
                'refusal_and_preservation': 'SELECTED_PROBES_ONLY' if rid in MODULES else 'NOT_VERIFIED',
                'render_effect': 'NOT_RUN_BY_THIS_AUDIT', 'protected_source_apply': 'NOT_IMPLEMENTED'},
            'remaining': LIMITS.get(rid, 'Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified.')})
    return summary


def write_matrix(evidence, output):
    counts = evidence['summary']['rule_status_counts']
    lines = ['# All-rule coverage audit', '',
        '**This is an incomplete implementation, not 33 passing rules.**', '',
        f"- Tested commit: `{evidence['revision_before']['commit']}`.",
        f"- Working-tree content fingerprint: `{evidence['revision_before']['working_tree_sha256']}` (exact file hashes in the JSON).",
        f"- Generated: {evidence['generated_utc']}.",
        f"- Rule status: {counts}. No rule is classified COMPLETE.",
        f"- {len(evidence['fixtures'])} fixture executions; {evidence['summary']['cli_invocations']} real CLI invocations including isolated candidate reanalysis and mode probes.",
        f"- Observation counts: {evidence['summary']['observation_counts']}.",
        f"- Audit issues: {evidence['summary']['audit_issues']}. Revision stable during run: {evidence['revision_stable']}.", '',
        '## Reproduce', '', '```sh',
        'python3 tools/typst-lint/audit_rules.py', '```', '',
        'The official syntax adapter must already be built as documented in the tool README. This runner never builds, evaluates Typst imports, invokes a real compiler, or edits a consumer. Exit **2** means the audit finished with incomplete rule implementation/acceptance; it is not a success code. Exit 1 indicates an audit invariant failure, parse-blocked representative, or mismatch in claimed supported behavior.', '',
        'The runner creates isolated temporary consumers from [fixtures.json](audit/fixtures.json), supplies the explicit repository package path except in its missing-path refusal case. Explicitly flagged scoped-reference and direct-mannot fixtures instead copy the exact wrapper, text-utils and reviewed mannot namespace artifacts into a separate temporary package root. Namespace-only fixtures do not grant raw APIs. Explicit supplemental-evidence fixtures additionally copy genuine pinned mannot and CeTZ definition artifacts, without evaluating or fetching them. The runner captures the full normalized CLI reports in [latest-evidence.json](audit/latest-evidence.json). `<repo>`, `<fixture>` and `<package-fixture>` are portable path placeholders, not missing evidence. Every input/config/catalog hash, proposed edit baseline, package hash, runtime/spec fingerprint and report is retained. Negative fixtures with absent/partial coverage are **not semantic passes**.', '',
        'Only proposed-edit bytes are composed in memory and copied into a different temporary consumer for candidate reanalysis. Idempotence is verified only when all repaired rule/file pairs retain supported coverage, baseline-supported pairs have no downgrade or new refusal after mapping byte offsets, no unexpected active findings appear, and no edits remain. Missing/unsupported reanalysis is explicitly CANDIDATE_ANALYSIS_BLOCKED, never verified. This does not exercise protected source application, editor leases, convergence of all 33 rules, or the authoritative compiler gate. A PATH tripwire plus the reported compiler flag/output sentinel checks that these CLI invocations remain inert; it is not a proof about arbitrary external compiler paths.', '',
        'The fixture inventory contains representative snippets of each normative positive/negative scenario. Reviewed direct text-utils, project-references, bounded math-blocks theme/spacer/proof and scoped-annotations wrappers are supported. Exact named text-utils facade forwarding has a separate initializer/export-access certificate without import-reorder authority; other facade helper provenance remains unresolved, and reports retain the exact unavailable-package reasons. Pure captured relative re-export graphs and a whole-module flowbox fingerprint have bounded proofs; arbitrary facade names and flowbox fragments still do not establish semantics. No unresolved helper is promoted to a semantic pass.', '',
        'The separately selected closed-core offline CLI profile proves per-input applicability for all 15 core rules over a strict literal-prose/math grammar and connects actual script analysis to protected replacement. Its unit, actual CLI, inventory and process-crash tests are not rerun or counted as passes by this ordinary-mode audit. It does not make a global rule COMPLETE, authorize general input writes or establish a compiler snapshot. See [OFFLINE_CLI_CONTRACT.md](OFFLINE_CLI_CONTRACT.md) and [PROTECTED_CORE_CONTRACT.md](PROTECTED_CORE_CONTRACT.md).', '',
        '## Historical baseline and corrected reference contract', '',
        'The prior 3-PARTIAL / 30-NOT_IMPLEMENTED audit is preserved in Git commit `58111d8c2cd956bfe019869a76571945b22b3671` at `tools/typst-lint/audit/latest-evidence.json` and its matrix. Current counts are derived from this run, never copied forward. That baseline identified a malformed bare-reference repair example.', '',
        'The current normative contract permits only the exact syntax-valid `project-ref("pde", [@energy])` content-to-label repair with a unique explicit catalog match. Bare `@energy` in code arguments is now an intentional parse-refusal scenario, not an unresolved SPEC_CONFLICT. Invalid duplicate-key/nonfinite catalogs are reported as EXPECTED_INPUT_REFUSAL and never count as semantic rule passes. An absent optional catalog likewise does not establish target validity.', '',
        '## 33-rule matrix', '',
        '| Rule and normative spec | Repair contract | Status / implementation | Actual CLI smoke evidence | Remaining acceptance work |',
        '| --- | --- | --- | --- | --- |']
    for rule in evidence['rules']:
        rid = rule['rule_id']
        spec = '../../' + rule['spec']
        repair = f"../../openspec/changes/typst-style-linter/fix-policy.md#L{rule['repair_line']}"
        modules = ', '.join(f'[{p}]({p})' for p in rule['implementation_modules']) or 'No backend'
        cases = [r for r in evidence['fixtures'] if r['rule_id'] == rid]
        smoke = '; '.join(f"{r['kind']}: {r['observation']}, findings={r['target_findings']}, edits={r['target_edits']}" for r in cases if r['kind'] in ('positive','negative','direct-reviewed-repair'))
        lines.append(f"| [{rid}]({spec}#L{rule['spec_line']}) | [{rule['repair_policy']}]({repair}) | **{rule['status']}**. {modules} | {smoke}. Full records keyed by `{rid}--…` in JSON. | {rule['remaining']} |")
    lines += ['', '## Normative scenario inventory', '',
        'All scenario headings are indexed below and in the JSON with exact source lines. SMOKE_ONLY means at least one variant was run, **not** that all alternatives/preconditions in that scenario were exercised. Every scenario acceptance remains NOT_COMPLETE. Required exact-edit/refusal/idempotence/preservation/render dimensions are separately reported per rule; none is inferred from a zero-diagnostic run.', '']
    for rule in evidence['rules']:
        lines.append(f"### {rule['rule_id']}")
        for scenario in rule['scenarios']:
            ids = ', '.join(f'`{i}`' for i in scenario['fixture_ids']) or 'none'
            lines.append(f"- **{scenario['heading']}**: {scenario['execution']}; {scenario['observations']}; fixtures {ids}. Acceptance NOT_COMPLETE.")
        tests = ', '.join(f'[{p}]({p})' for p in rule['existing_test_sources_not_rerun_here']) or 'No implementation test module yet.'
        lines += [f'- Existing test sources (not rerun or counted as passes by this audit): {tests}', '']
    lines += ['## Work still required, in priority order', '',
        f"1. Keep fail-closed coverage reporting while implementing the missing {counts.get('NOT_IMPLEMENTED', 0)} detection backends. Establish reviewed facade/import/include, builtin/shadowing, content-context and document-graph proofs before rules that depend on them. Do not count registry metadata or catalog validation as rule execution.",
        '2. Complete interacting paragraph/display/proof/footnote rules, including marker insertion, body-start gaps, explicit prose parbreak composition and source-language/context proof. Verify all lowercase/name/math/non-English preservation and multi-pass convergence cases.',
        '3. Implement annotation scope ownership/marker bounds, namespace graphs, remaining project-reference/catalog provenance, book/reset ownership and theme rules. Diagnostic-only contracts must remain nonmutating; unavailable targets/names must never be invented.',
        '4. Add Markdown extraction/source mapping and exact teaching-line exemption; complete every listed branch, refusal, suppression, exact edit, byte/newline/BOM/comment preservation and idempotence fixture for each repair row. Add the required combined highlight/display/paragraph and light/dark palette render assertions.',
        '5. Extend the separately tested explicit offline closed-core route only with complete per-input analysis and writer coordination. General project coverage, editor generations and immutable compiler snapshots remain unimplemented. The ordinary check, dry-run, fix and compile invocations exercised here remain exit 2, source unchanged, compiler not invoked.', '',
        'No consumer documentation, editor settings, actual document sources or real compilation outputs were changed by this audit.', '']
    output.write_text('\n'.join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=TOOL / 'audit/latest-evidence.json')
    parser.add_argument('--matrix', type=Path, default=TOOL / 'coverage-matrix.md')
    args = parser.parse_args()
    fixtures = json.loads(INVENTORY.read_text())['fixtures']
    specs = spec_index()
    if len(specs) != 33 or {f['rule_id'] for f in fixtures} != set(specs):
        raise ValueError('Fixture/spec inventory must cover exactly the normative 33 rule IDs')
    if len({f['id'] for f in fixtures}) != len(fixtures):
        raise ValueError('Duplicate fixture ID')
    before = snapshot()
    results = [observe(fixture) for fixture in fixtures]
    combined = next(f for f in fixtures if f['kind'] == 'three-rule-composition')
    modes = [run_cli(combined, mode) for mode in ('check', 'fix', 'compile')]
    after = snapshot()
    rules = summarize(fixtures, results, specs)
    all_runs = [r['run'] for r in results] + [r['candidate_reanalysis'] for r in results if 'candidate_reanalysis' in r] + modes
    issues = [issue for run in all_runs for issue in run['invariant_issues']]
    issues += [r['fixture_id'] + ': ' + r['observation'] for r in results if r['observation'] in ('MISMATCH', 'PARSE_BLOCKED', 'INPUT_OR_CONFIG_BLOCKED', 'CANDIDATE_ANALYSIS_BLOCKED')]
    if before != after:
        issues.append('Runtime/spec/fixture revision drifted during audit; rerun required')
    evidence = {'schema': 1, 'generated_utc': datetime.now(timezone.utc).isoformat(),
                'scope': 'Incomplete 33-rule acceptance audit. No unsupported zero-finding result is a pass.',
                'revision_before': before, 'revision_after': after, 'revision_stable': before == after,
                'rules': rules, 'fixtures': results, 'mode_probes': modes,
                'summary': {'rule_status_counts': dict(Counter(r['status'] for r in rules)),
                    'observation_counts': dict(Counter(r['observation'] for r in results)),
                    'normative_scenarios': sum(len(r['scenarios']) for r in rules),
                    'complete_scenarios': 0, 'cli_invocations': len(all_runs), 'audit_issues': issues,
                    'expected_input_refusals': sum(r['observation'] == 'EXPECTED_INPUT_REFUSAL' for r in results),
                    'exit_code': 1 if issues else 2}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    write_matrix(evidence, args.matrix)
    print(json.dumps(evidence['summary'], sort_keys=True))
    return evidence['summary']['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
