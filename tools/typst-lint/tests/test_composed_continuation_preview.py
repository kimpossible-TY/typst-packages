"""Opt-in continuation edits compose without changing the strict preview gate."""
from copy import deepcopy
from pathlib import Path
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_conditional_continuation as continuation
import document_sequence_preview as sequence
from syntax_client import EXECUTABLE, parse
from typst_lint import SafetyError, digest, load_rules
import test_document_preview_cli as fixtures


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ComposedContinuationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_rules()[0]['rules']

    def test_all_profiles_replay_selection_and_crlf(self):
        source = b'#block[This solution holds.\r\n  We continue.]'
        for profile in sequence._VERIFICATIONS:
            with self.subTest(profile=profile):
                tree = parse(source)
                self.assertIsNone(sequence.preview(tree, 'main.typ', self.catalog, profile=profile))
                record = sequence.preview(tree, 'main.typ', self.catalog, profile=profile,
                                          conditional_continuations=True)
                self.assertEqual(record['steps'][-1]['rule_id'], 'pde.continuation-line', record)
                self.assertEqual(sequence.replay(tree, 'main.typ', self.catalog, record, profile=profile,
                                                 conditional_continuations=True),
                                 b'#block[This solution holds. We continue.]')
                with self.assertRaises(SafetyError):
                    sequence.replay(tree, 'main.typ', self.catalog, record, profile=profile)
                for key, value in (('conditions', ['forged']), ('execution_proved', True),
                                   ('replacement', '\n')):
                    changed = deepcopy(record)
                    changed['steps'][-1]['edits'][0][key] = value
                    with self.assertRaises(SafetyError):
                        sequence.replay(tree, 'main.typ', self.catalog, changed, profile=profile,
                                        conditional_continuations=True)

    def test_suppression_comments_and_unresolved_marker_boundary(self):
        cases = (b'#block[This was Fig.\nWe continue.]',
                 b'#block[This solution holds. // attached\nWe continue.]',
                 b'// typst-lint: ignore-next pde.continuation-line -- intended\n'
                 b'#block[This solution holds.\nWe continue.]')
        for source in cases:
            with self.subTest(source=source):
                record = sequence.preview(parse(source), 'main.typ', self.catalog, conditional_continuations=True)
                self.assertTrue(record is None or not record['steps'], record)
        source = b'#block[\n#paragraph_tab\nNext.]\n#block[This solution holds.\nWe continue.]'
        record = sequence.preview(parse(source), 'main.typ', self.catalog, conditional_continuations=True)
        self.assertEqual(record['verification'], 'withheld')
        self.assertIn('paragraph marker boundary', record['reason'])

    def test_forged_source_proof_withholds_all_steps(self):
        source = b'#block[This solution holds.\nWe continue.]'
        original = continuation.preview
        def forged(tree, path, **kwargs):
            value = original(tree, path, **kwargs)
            if value is not None:
                value['conditions'] = ('forged',)
            return value
        with patch.object(continuation, 'preview', side_effect=forged):
            result = sequence.preview(parse(source), 'main.typ', self.catalog, conditional_continuations=True)
        self.assertEqual(result['verification'], 'withheld')
        self.assertEqual(result['steps'], [])

    def test_self_consistent_forged_undetected_boundary_is_rejected(self):
        source = b'#block[This solution holds.\nWe continue.\nThis was Fig.\nWe proceed.]'
        original = continuation.preview
        def forged(tree, path, **kwargs):
            result = original(tree, path, **kwargs)
            if result is not None and tree.source == source:
                start = source.index(b'\nWe proceed.')
                result['edits'][0].update(start=start, end=start + 1)
                result['candidate_sha256'] = digest(source[:start] + b' ' + source[start + 1:])
            return result
        with patch.object(continuation, 'preview', side_effect=forged):
            record = sequence.preview(parse(source), 'main.typ', self.catalog, conditional_continuations=True)
        self.assertEqual(record['verification'], 'withheld')
        self.assertFalse(record['steps'])

    def test_candidate_omission_is_withheld(self):
        source = b'#block[This solution holds.\nWe continue.]'
        with patch.object(continuation, 'preview', return_value=None):
            record = sequence.preview(parse(source), 'main.typ', self.catalog, conditional_continuations=True)
        self.assertEqual(record['verification'], 'withheld')
        self.assertIn('omitted', record['reason'])
        self.assertFalse(record['steps'])


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ComposedContinuationCliTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DocumentPreviewCliTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and sys.platform == 'darwin',
                         'Opt in on macOS with reviewed Typst')
    def test_real_compile_reports_conditional_step_without_source_write(self):
        source = b'#block[This solution holds.\nWe continue.]'
        path = self.f.project / 'main.typ'
        path.write_bytes(source)
        run = subprocess.run(self.f.command('--conditional-continuations', '--review-edits', '--format', 'json'),
                             capture_output=True, timeout=60)
        report = json.loads(run.stdout)
        self.assertEqual(run.returncode, 2, report)
        self.assertTrue(report['compiler_invoked'] and report['pdf_generated'])
        self.assertEqual(report['conditional_continuation_preview']['planned_edits'], 1)
        self.assertIn('pde.continuation-line', report['conditionally_previewed_rule_ids'])
        self.assertFalse(report['source_apply_gate']['eligible'])
        self.assertEqual(path.read_bytes(), source)
