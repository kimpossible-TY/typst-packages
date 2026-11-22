"""Actual CLI multi-pass plans and independent serialized ownership replay."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from audit_rules import candidate_verdict
from convergence_evidence import replay_evidence
from edit_plan import Edit, plan
from source_analysis import analyze
from syntax_client import AdapterError, parse
from typst_lint import SafetyError, digest, report

HIGHLIGHT = 'pde.text-only-highlight'
DISPLAY = 'pde.standalone-display'
GAP = 'pde.display-continuation-gap'
SOURCE = b'Thus #highlight[$ X^i=Y $] and more.\n'
EXPECTED = b'Thus\n$ X^(i)=Y $\nand more.\n'


def proposed(source, value):
    return plan(source, (Edit(e['start'], e['end'], e['replacement'].encode())
                         for e in value['proposed_edits'])).candidate()


class MultiPassIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / '.typst-lint.toml').write_text('rule-set="pde"\ninclude=["main.typ"]\n')
        self.contexts = {}
        self.target = self.root / 'main.typ'
        self.target.write_bytes(SOURCE)

    def pair(self, source=SOURCE):
        self.target.write_bytes(source)
        before = report(self.root, 'dry-run', _analysis_observer=self.contexts.__setitem__)
        candidate = proposed(source, before)
        self.target.write_bytes(candidate)  # Isolated test-owned consumer only.
        after = report(self.root, 'dry-run')
        return before, after, candidate

    def test_exact_multi_owner_plan_and_independent_zero_edit_check(self):
        before, after, candidate = self.pair()
        self.assertEqual(candidate, EXPECTED)
        self.assertFalse(after['proposed_edits'])
        self.assertTrue(all(e['baseline_sha256'] == digest(SOURCE) for e in before['proposed_edits']))
        self.assertTrue(any(set(e.get('rule_ids', ())) == {HIGHLIGHT, DISPLAY}
                            for e in before['proposed_edits']))
        self.assertTrue(all(e['repair_passes'] == 2 for e in before['proposed_edits']))
        record, = before['convergence_evidence']
        replay = replay_evidence(record, SOURCE, candidate, context=self.contexts['main.typ'])
        x = SOURCE.index(b'X')
        self.assertEqual(candidate[replay.map_offset(x):][:1], b'X')
        self.assertIsNone(replay.map_offset(SOURCE.index(b'highlight')))
        verdict = candidate_verdict(before, after, {'main.typ': SOURCE.decode()}, {'main.typ': candidate.decode()}, analysis_contexts=self.contexts)
        self.assertTrue(verdict['verified'], verdict)
        self.assertIn({'path': 'main.typ', 'rule_id': DISPLAY}, verdict['repaired_pairs'])

    def test_followup_rule_without_original_diagnostic_is_not_lost(self):
        source = b'Thus $ A=B $\n\nHence the claim.\n'
        before, after, candidate = self.pair(source)
        self.assertEqual(candidate, b'Thus\n$ A=B $\nHence the claim.\n')
        self.assertNotIn(GAP, {d['rule_id'] for d in before['diagnostics']})
        self.assertIn(GAP, {e['rule_id'] for e in before['proposed_edits']})
        self.assertFalse(after['proposed_edits'])
        verdict = candidate_verdict(before, after, {'main.typ': source.decode()}, {'main.typ': candidate.decode()}, analysis_contexts=self.contexts)
        self.assertTrue(verdict['verified'], verdict)

    def test_bom_crlf_unicode_and_later_directive_owner_survive(self):
        source = b'\xef\xbb\xbf' + SOURCE.replace(b'\n', b'\r\n') + (
            '// typst-lint: ignore-next core.parenthesized-scripts -- preserve notation\r\n'
            '$ z^j $\r\n한국어 remains.\r\n').encode()
        before, after, candidate = self.pair(source)
        self.assertEqual(candidate, source.replace(SOURCE.rstrip(b'\n'), EXPECTED.rstrip(b'\n').replace(b'\n', b'\r\n')))
        self.assertEqual(before['suppressed_by_rule'], after['suppressed_by_rule'])
        self.assertEqual(before['suppressed_count'], 1)
        verdict = candidate_verdict(before, after, {'main.typ': source.decode()}, {'main.typ': candidate.decode()}, analysis_contexts=self.contexts)
        self.assertTrue(verdict['verified'], verdict)

    def test_all_four_cli_modes_stay_read_only_and_never_compile(self):
        output = self.root / 'previous.pdf'
        output.write_bytes(b'previous output')
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        compiler = bin_dir / 'typst'
        compiler.write_text('#!/bin/sh\nprintf called > "$MARKER"\nexit 91\n')
        compiler.chmod(0o700)
        marker = self.root / 'compiler-called'
        env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ.get('PATH', ''), MARKER=str(marker))
        for mode in ('check', 'dry-run', 'fix', 'compile'):
            launcher = TOOL / ('typst-style' if mode == 'compile' else 'typst-lint')
            command = [sys.executable, str(launcher), 'fix' if mode == 'dry-run' else mode,
                       '--root', str(self.root), '--format', 'json']
            if mode == 'dry-run': command.append('--dry-run')
            if mode == 'compile': command.extend(['--', 'main.typ', 'previous.pdf'])
            run = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 2, run.stderr)
            value = json.loads(run.stdout)
            self.assertEqual(proposed(SOURCE, value), EXPECTED)
            self.assertFalse(value['compiler_invoked'] or value['applied_fixes'] or value['coverage']['complete'])
            self.assertEqual(self.target.read_bytes(), SOURCE)
            self.assertEqual(output.read_bytes(), b'previous output')
            self.assertFalse(marker.exists())

    def test_config_ignored_followup_and_core_pack_are_not_reenabled(self):
        (self.root / '.typst-lint.toml').write_text('rule-set="pde"\n[[ignore]]\npaths=["main.typ"]\n'
            f'rules=["{DISPLAY}"]\nreason="intentional"\n')
        value = report(self.root, 'dry-run')
        self.assertEqual(proposed(SOURCE, value), b'Thus $ X^(i)=Y $ and more.\n')
        self.assertNotIn(DISPLAY, {rid for e in value['proposed_edits'] for rid in e.get('rule_ids', [e['rule_id']])})
        core = analyze(parse(SOURCE), 'main.typ', pack='core')
        self.assertNotIn(DISPLAY, core)
        self.assertEqual(plan(SOURCE, [e for r in core.values() for e in r.edits]).candidate(),
                         SOURCE.replace(b'^i', b'^(i)'))

    def test_tampered_missing_and_duplicate_history_never_certify(self):
        before, after, candidate = self.pair()
        for kind in ('missing', 'stripped', 'duplicate', 'hash', 'move', 'source', 'pass', 'attribution', 'edit',
                     'valid-false-step', 'valid-false-plan', 'plan-pass', 'null-ids', 'nested-ids', 'cycle', 'float-pass', 'float-offset', 'missing-baseline', 'wrong-baseline'):
            changed = copy.deepcopy(before)
            record = changed['convergence_evidence'][0]
            if kind == 'missing': changed.pop('convergence_evidence')
            elif kind == 'stripped':
                changed.pop('convergence_evidence')
                for e in changed['proposed_edits']:
                    e.pop('repair_passes', None)
                    e.pop('rule_ids', None)
            elif kind == 'valid-false-step':
                for step in record['steps']:
                    for e in step['applied']:
                        e['rule_id'], e['rule_ids'] = 'pde.import-order', ['pde.import-order']
            elif kind == 'valid-false-plan':
                for e in changed['proposed_edits']:
                    e['rule_id'], e['rule_ids'] = 'pde.import-order', ['pde.import-order']
            elif kind == 'plan-pass': changed['proposed_edits'][0]['repair_passes'] = 9
            elif kind == 'float-pass': record['passes'] = float(record['passes'])
            elif kind == 'float-offset': record['steps'][0]['applied'][0]['start'] = float(record['steps'][0]['applied'][0]['start'])
            elif kind == 'missing-baseline': changed['proposed_edits'][0].pop('baseline_sha256')
            elif kind == 'wrong-baseline': changed['proposed_edits'][0]['baseline_sha256'] = '0' * 64
            elif kind == 'null-ids': changed['proposed_edits'][0]['rule_ids'] = None
            elif kind == 'nested-ids': changed['proposed_edits'][0]['rule_ids'] = [[]]
            elif kind == 'cycle':
                record['steps'] = [record['steps'][0], record['steps'][0], record['steps'][1]]
                record['passes'] = 3
            elif kind == 'duplicate': changed['convergence_evidence'].append(copy.deepcopy(record))
            elif kind == 'hash': record['steps'][0]['before_sha256'] = '0' * 64
            elif kind == 'move': record['steps'][0]['moves'] = [[0, 4, 0]]
            elif kind == 'source': record['candidate_sha256'] = '0' * 64
            elif kind == 'pass': record['passes'] = 9
            elif kind == 'attribution': record['steps'][0]['applied'][0]['rule_ids'] = ['invented.rule']
            else: record['steps'][0]['applied'][0]['replacement'] += 'x'
            with self.subTest(kind=kind):
                value = candidate_verdict(changed, after, {'main.typ': SOURCE.decode()}, {'main.typ': candidate.decode()}, analysis_contexts=self.contexts)
                self.assertEqual(value['status'], 'BLOCKED_ANALYSIS', value)
                self.assertFalse(value['verified'])
        with patch('candidate_convergence.parse', side_effect=AdapterError('missing parser')):
            value = candidate_verdict(before, after, {'main.typ': SOURCE.decode()}, {'main.typ': candidate.decode()}, analysis_contexts=self.contexts)
            self.assertEqual(value['status'], 'BLOCKED_ANALYSIS')

    def test_directive_retarget_and_wrong_source_hash_are_refused(self):
        before, _, candidate = self.pair()
        record = before['convergence_evidence'][0]
        with self.assertRaises(SafetyError):
            replay_evidence(record, SOURCE + b' ', candidate, context=self.contexts['main.typ'])
        with patch('source_analysis._scope_owners', side_effect=[(), ('wrong target',)]):
            with self.assertRaisesRegex(SafetyError, 'directive.*ownership'):
                replay_evidence(record, SOURCE, candidate, context=self.contexts['main.typ'])

    def test_missing_independent_context_cannot_authenticate_even_stripped_history(self):
        before, after, candidate = self.pair()
        before.pop('convergence_evidence')
        for edit in before['proposed_edits']:
            edit.pop('repair_passes', None)
            edit.pop('rule_ids', None)
        value = candidate_verdict(before, after, {'main.typ': SOURCE.decode()}, {'main.typ': candidate.decode()})
        self.assertEqual(value['status'], 'BLOCKED_ANALYSIS')

    def test_full_same_kind_suppressed_target_replacement_loses_source_origin(self):
        from candidate_convergence import AttributedEdit, Contribution, Step
        from source_analysis import _validate_step
        source = b'// typst-lint: ignore-next core.parenthesized-scripts -- retain target\n#highlight[old]'
        candidate = source.replace(b'#highlight[old]', b'#highlight[new]')
        before, after = parse(source), parse(candidate)
        start = source.index(b'highlight')
        edit = Edit(start, len(source), b'highlight[new]')
        # Deliberate adversarial proposal, never a claimed production-rule fix.
        attributed = AttributedEdit(edit, HIGHLIGHT, (HIGHLIGHT,), (Contribution(1, HIGHLIGHT, edit),))
        step = Step(source, candidate, (attributed,), ())
        with self.assertRaisesRegex(SafetyError, 'target origin'):
            _validate_step(before, analyze(before, 'main.typ', pack='pde', _verify=False),
                           after, analyze(after, 'main.typ', pack='pde', _verify=False), step)
