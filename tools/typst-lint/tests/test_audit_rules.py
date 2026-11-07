"""The audit harness must not equate missing analysis with idempotence."""
import copy
from pathlib import Path
import sys
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from audit_rules import candidate_verdict

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


if __name__ == '__main__':
    unittest.main()
