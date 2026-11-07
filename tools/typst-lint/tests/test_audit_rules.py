"""The audit harness must not equate missing analysis with idempotence."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from audit_rules import candidate_verdict, expected_input_refusal, observe, run_cli, snapshot, spec_index
from static_resolver import (MANNOT_NAMESPACE, MANNOT_NAMESPACE_MANIFEST_SHA256,
                             MANNOT_NAMESPACE_SOURCE_SHA256, SCOPED_ANNOTATIONS, TEXT_UTILS)
from syntax_client import EXECUTABLE

SCRIPT = 'core.parenthesized-scripts'
CAP = 'pde.paragraph-capitalization'
SOURCE = {'main.typ': '$x^i$\niPhone remains.\n'}
CANDIDATE = {'main.typ': '$x^(i)$\niPhone remains.\n'}


def pair():
    baseline = {
        'phase': 'analysis', 'parse_failures': [], 'configuration_failures': [],
        'diagnostics': [], 'proposed_edits': [
            {'path': 'main.typ', 'rule_id': SCRIPT, 'start': 3, 'end': 3, 'replacement': '('},
            {'path': 'main.typ', 'rule_id': SCRIPT, 'start': 4, 'end': 4, 'replacement': ')'}],
        'coverage': {'complete': False, 'enabled': [SCRIPT, CAP], 'files': [
            {'path': 'main.typ', 'rule_id': SCRIPT, 'status': 'evaluated', 'reasons': []},
            {'path': 'main.typ', 'rule_id': CAP, 'status': 'partial',
             'reasons': ['initial-word-outside-editorial-allowlist at byte 6']}]}}
    candidate = copy.deepcopy(baseline)
    candidate['proposed_edits'] = []
    candidate['coverage']['files'][1]['reasons'] = ['initial-word-outside-editorial-allowlist at byte 8']
    return baseline, candidate


class CandidateCoverageTests(unittest.TestCase):
    def verdict(self, baseline, candidate):
        return candidate_verdict(baseline, candidate, SOURCE, CANDIDATE)

    def test_valid_partial_other_rule_and_global_incomplete_are_allowed(self):
        baseline, candidate = pair()
        value = self.verdict(baseline, candidate)
        self.assertEqual(value['status'], 'VERIFIED_SUBSET')
        self.assertTrue(value['verified'])

    def test_missing_adapter_unsupported_reanalysis_is_not_verified(self):
        baseline, candidate = pair()
        for row in candidate['coverage']['files']:
            row.update(status='unsupported', reasons=['Syntax adapter absent'])
        value = self.verdict(baseline, candidate)
        self.assertEqual(value['status'], 'BLOCKED_ANALYSIS')
        self.assertFalse(value['verified'])

    def test_missing_repaired_rule_coverage_is_not_verified(self):
        baseline, candidate = pair()
        candidate['coverage']['files'].pop(0)
        self.assertFalse(self.verdict(baseline, candidate)['verified'])

    def test_coverage_downgrade_and_new_partial_refusal_block(self):
        for which in (0, 1):
            with self.subTest(which=which):
                baseline, candidate = pair()
                candidate['coverage']['files'][which].update(status='partial', reasons=['new unresolved binding'])
                value = self.verdict(baseline, candidate)
                self.assertEqual(value['status'], 'BLOCKED_ANALYSIS')
                self.assertFalse(value['verified'])

    def test_unexpected_active_finding_is_mismatch(self):
        baseline, candidate = pair()
        candidate['diagnostics'] = [{'rule_id': CAP, 'grade': 'warning', 'path': 'main.typ',
                                    'line': 2, 'column': 1, 'explanation': 'New finding', 'source': 'fixture'}]
        value = self.verdict(baseline, candidate)
        self.assertEqual(value['status'], 'MISMATCH')
        self.assertFalse(value['verified'])

    def test_existing_active_finding_preserves_owner_through_byte_shifts(self):
        baseline, candidate = pair()
        finding = {'rule_id': CAP, 'grade': 'warning', 'path': 'main.typ',
                   'line': 2, 'column': 1, 'explanation': 'Existing unresolved finding', 'source': 'fixture'}
        baseline['diagnostics'] = [dict(finding)]
        candidate['diagnostics'] = [dict(finding)]
        self.assertTrue(self.verdict(baseline, candidate)['verified'])

    def test_related_diagnostic_byte_location_is_mapped_not_discarded(self):
        baseline, candidate = pair()
        finding = {'rule_id': 'core.unique-scope-namespace', 'grade': 'error', 'path': 'main.typ',
                   'line': 2, 'column': 1, 'explanation': 'Duplicate literal scope namespace; first declaration at byte 6', 'source': 'fixture'}
        baseline['diagnostics'] = [finding]
        candidate['diagnostics'] = [dict(finding, explanation='Duplicate literal scope namespace; first declaration at byte 8')]
        self.assertTrue(self.verdict(baseline, candidate)['verified'])
        candidate['diagnostics'][0]['explanation'] = 'Duplicate literal scope namespace; first declaration at byte 9'
        self.assertEqual(self.verdict(baseline, candidate)['status'], 'MISMATCH')

    def test_byte_text_inside_literal_diagnostic_data_is_never_mapped(self):
        baseline, candidate = pair()
        finding = {'rule_id': 'core.project-ref-catalog-target', 'grade': 'warning', 'path': 'main.typ',
                   'line': 2, 'column': 1, 'explanation': "Unknown catalog project 'at byte 100'.", 'source': 'fixture'}
        baseline['diagnostics'] = [dict(finding)]
        candidate['diagnostics'] = [dict(finding)]
        self.assertTrue(self.verdict(baseline, candidate)['verified'])

    def test_partial_repaired_rule_preserves_existing_bounded_skip(self):
        baseline, candidate = pair()
        for report, offset in ((baseline, 6), (candidate, 8)):
            report['coverage']['files'][0].update(status='partial', reasons=[f'protected operand at byte {offset}'])
        self.assertTrue(self.verdict(baseline, candidate)['verified'])

    def test_disabled_rule_duplicate_rows_and_parse_failure_block(self):
        for change in ('disabled', 'duplicate', 'parse', 'no-phase'):
            with self.subTest(change=change):
                baseline, candidate = pair()
                if change == 'disabled': candidate['coverage']['enabled'].remove(SCRIPT)
                elif change == 'duplicate': candidate['coverage']['files'].append(dict(candidate['coverage']['files'][0]))
                elif change == 'parse': candidate['parse_failures'] = [{'path': 'main.typ'}]
                else: candidate.pop('phase')
                self.assertEqual(self.verdict(baseline, candidate)['status'], 'BLOCKED_ANALYSIS')

    def test_remaining_edits_cannot_be_idempotent(self):
        baseline, candidate = pair()
        candidate['proposed_edits'] = baseline['proposed_edits']
        self.assertEqual(self.verdict(baseline, candidate)['status'], 'MISMATCH')

    def test_no_repairs_or_unsupported_baseline_cannot_prove_candidate(self):
        for change in ('no-repairs', 'unsupported-baseline'):
            with self.subTest(change=change):
                baseline, candidate = pair()
                if change == 'no-repairs': baseline['proposed_edits'] = []
                else: baseline['coverage']['files'][0]['status'] = 'unsupported'
                self.assertFalse(self.verdict(baseline, candidate)['verified'])


class AuditInventoryTests(unittest.TestCase):
    def test_direct_mannot_fixtures_explicitly_select_the_namespace_snapshot(self):
        fixtures = json.loads((TOOL / 'audit/fixtures.json').read_text())['fixtures']
        requested = [f for f in fixtures if any(MANNOT_NAMESPACE in source for source in f['files'].values())]
        self.assertTrue(requested)
        for fixture in requested:
            with self.subTest(fixture=fixture['id']):
                self.assertIs(fixture.get('scoped_namespace_evidence'), True)

    def test_expected_catalog_refusal_requires_matching_phase_and_reason(self):
        fixture = {'expected_input_error': 'Duplicate reference catalog key'}
        report = {'phase': 'input', 'error': 'Duplicate reference catalog key: energy'}
        self.assertTrue(expected_input_refusal(fixture, report))
        self.assertFalse(expected_input_refusal(fixture, dict(report, phase='analysis')))
        self.assertFalse(expected_input_refusal(fixture, dict(report, error='Unrelated failure')))
        self.assertFalse(expected_input_refusal({}, report))

    def test_expected_input_refusal_is_not_a_semantic_rule_result(self):
        fixture = {'id': 'core.project-ref-catalog-target--invalid',
                   'rule_id': 'core.project-ref-catalog-target', 'kind': 'invalid',
                   'normative_diagnostics': None, 'intentional_parse_error': False,
                   'expected_input_error': 'Duplicate reference catalog key'}
        report = {'phase': 'input', 'error': 'Duplicate reference catalog key: energy'}
        with patch('audit_rules.run_cli', return_value={'report': report}):
            result = observe(fixture)
        self.assertEqual(result['observation'], 'EXPECTED_INPUT_REFUSAL')
        self.assertFalse(result['backend_observed'])
        self.assertIsNone(result['candidate_idempotence_verified'])

    def test_wrong_input_failure_is_not_accepted_as_expected_refusal(self):
        fixture = {'id': 'core.project-ref-catalog-target--invalid',
                   'rule_id': 'core.project-ref-catalog-target', 'kind': 'invalid',
                   'normative_diagnostics': None, 'intentional_parse_error': False,
                   'expected_input_error': 'Duplicate reference catalog key'}
        with patch('audit_rules.run_cli', return_value={'report': {'phase': 'input', 'error': 'Unrelated failure'}}):
            result = observe(fixture)
        self.assertEqual(result['observation'], 'MISMATCH')

    def test_bounded_reference_diagnostic_expectation_is_enforced_despite_partial_coverage(self):
        rid = 'core.project-ref-arguments'
        fixture = {'id': rid + '--exact', 'rule_id': rid, 'kind': 'exact',
                   'normative_diagnostics': 1, 'intentional_parse_error': False,
                   'assert_normative_count': True}
        report = {'phase': 'analysis', 'coverage': {'files': [
            {'path': 'main.typ', 'rule_id': rid, 'status': 'partial', 'reasons': ['Bounded root subset']}]},
            'diagnostics': [], 'proposed_edits': []}
        with patch('audit_rules.run_cli', return_value={'report': report}):
            result = observe(fixture)
        self.assertEqual(result['observation'], 'MISMATCH')

    def test_snapshot_covers_all_current_reviewed_artifacts_and_catalog_type(self):
        files = snapshot()['files']
        for path in ('local/text-utils/0.1.2/lib.typ', 'local/text-utils/0.1.2/typst.toml',
                     'local/project-references/0.1.0/lib.typ', 'local/project-references/0.1.0/typst.toml',
                     'tools/typst-lint/reference_catalog.py'):
            self.assertIn(path, files)

    def test_current_normative_scenarios_are_mapped_without_completion_claims(self):
        fixtures = json.loads((TOOL / 'audit/fixtures.json').read_text())['fixtures']
        specs = spec_index()
        self.assertEqual(len(specs), 33)
        self.assertEqual(len({f['id'] for f in fixtures}), len(fixtures))
        for rid, definition in specs.items():
            for scenario in definition['scenarios']:
                with self.subTest(rule=rid, heading=scenario['heading']):
                    self.assertTrue(any(f['rule_id'] == rid and f['scenario_heading'] == scenario['heading']
                                        for f in fixtures))
        bare = next(f for f in fixtures if f['kind'] == 'malformed-bare-reference')
        self.assertTrue(bare['intentional_parse_error'])
        self.assertEqual(bare['scenario_heading'], 'Malformed bare reference is not repaired')


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class AuditPackageSnapshotTests(unittest.TestCase):
    def fixture(self, fixture_id):
        fixtures = json.loads((TOOL / 'audit/fixtures.json').read_text())['fixtures']
        return next(f for f in fixtures if f['id'] == fixture_id)

    def assert_snapshot_analysis(self, run):
        self.assertEqual(run['exit_code'], 2)
        self.assertFalse(run['invariant_issues'])
        self.assertTrue(run['source_unchanged'] and run['package_fixture_unchanged'])
        self.assertTrue(run['compiler_tripwire_untouched'] and run['output_sentinel_unchanged'])
        report = run['report']
        self.assertEqual(report['phase'], 'analysis')
        self.assertFalse(report['coverage']['complete'] or report['compiler_invoked'] or report['applied_fixes'])
        self.assertEqual(report['package_path'], '<package-fixture>')
        namespace = next(e for e in report['package_evidence'] if e['import_path'] == MANNOT_NAMESPACE)
        self.assertEqual(namespace['manifest_sha256'], MANNOT_NAMESPACE_MANIFEST_SHA256)
        self.assertEqual(namespace['source_sha256'], MANNOT_NAMESPACE_SOURCE_SHA256)
        self.assertEqual(namespace['source_identity'], '<package-fixture>/preview/mannot/0.4.0/src/lib.typ')
        return report

    def test_direct_normative_import_reaches_partial_analysis_without_ambient_packages(self):
        fixture = self.fixture('core.annotation-in-display--positive')
        result = observe(fixture)
        report = self.assert_snapshot_analysis(result['run'])
        self.assertEqual(result['observation'], 'PARTIAL_CASE')
        self.assertFalse(report['proposed_edits'])
        self.assertEqual({e['import_path'] for e in report['package_evidence']},
                         {SCOPED_ANNOTATIONS, MANNOT_NAMESPACE})

    def test_facade_fixture_gets_its_selected_text_utils_without_graph_proof(self):
        fixture = self.fixture('pde.import-order--positive')
        result = observe(fixture)
        report = self.assert_snapshot_analysis(result['run'])
        self.assertEqual(result['observation'], 'NOT_IMPLEMENTED')
        self.assertFalse(report['proposed_edits'])
        self.assertEqual({e['import_path'] for e in report['package_evidence']},
                         {SCOPED_ANNOTATIONS, MANNOT_NAMESPACE, TEXT_UTILS})
        self.assertEqual(set(result['run']['package_fixture_hashes']), {
            'local/scoped-annotations/0.3.0/typst.toml', 'local/scoped-annotations/0.3.0/lib.typ',
            'local/text-utils/0.1.2/typst.toml', 'local/text-utils/0.1.2/lib.typ',
            'preview/mannot/0.4.0/typst.toml', 'preview/mannot/0.4.0/src/lib.typ'})
        self.assertTrue(any('no verified direct-package contract' in reason
                            for row in report['coverage']['files'] if row['path'] == 'main.typ'
                            for reason in row['reasons']))

    def test_namespace_snapshot_never_invents_raw_api_or_module_bindings(self):
        fixture = self.fixture('core.annotation-in-display--positive')
        for source, reason in (
            ('#let mark = highlight\n#import "@preview/mannot:0.4.0": *\n#mark[$x$]\n',
             'Unknown wildcard may shadow'),
            ('#let mark = highlight\n#import "@preview/mannot:0.4.0": mark\n#mark[$x$]\n',
             'Imported item is unresolved'),
            ('#import "@preview/mannot:0.4.0" as m\n#m.mark[$x$]\n',
             'Import has no verified direct-package contract'),
        ):
            with self.subTest(source=source):
                report = self.assert_snapshot_analysis(run_cli(fixture, source_files={'main.typ': source}))
                self.assertFalse(report['diagnostics'] or report['proposed_edits'])
                self.assertTrue(any(reason in skip for row in report['coverage']['files']
                                    for skip in row['reasons']))


if __name__ == '__main__':
    unittest.main()
