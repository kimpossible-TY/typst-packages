"""The explicit conditional profile composes only reviewed source patterns."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
import json
import os
import subprocess
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import document_conditional_paragraph_gap as gap
import document_sequence_preview as sequence
from syntax_client import EXECUTABLE, parse
from typst_lint import SafetyError, load_rules
import test_document_preview_cli as fixtures


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ComposedParagraphMarkerGapTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_rules()[0]['rules']

    def preview(self, source, **kwargs):
        return sequence.preview(parse(source), 'main.typ', self.catalog, **kwargs)

    def test_supported_forms_replay_and_default_is_unchanged(self):
        cases = (b'#block[\n#paragraph_tab\nNext.]',
                 b'#block[\r\n  #paragraph_tab\r\nNext.]',
                 b'#block[Before.\n\n#block[Proof.]\n#paragraph_tab\nNext.]',
                 b'#block[Before.\n\n#block(breakable: false)[Proof.]\n#paragraph_tab\nNext.]')
        for source in cases:
            with self.subTest(source=source):
                self.assertIsNone(self.preview(source))
                record = self.preview(source, profile='conditional-gaps')
                self.assertEqual(record['steps'][0]['rule_id'], gap.RULE, record)
                row = record['steps'][0]['edits'][0]
                self.assertFalse(any(row[k] for k in ('execution_proved', 'runtime_binding_proved',
                    'render_equivalence_proved', 'edit_permission', 'compiler_permission')))
                candidate = sequence.replay(parse(source), 'main.typ', self.catalog, record, profile='conditional-gaps')
                self.assertEqual(candidate, source[:row['start']] + row['replacement'].encode() + source[row['end']:])
                bad = deepcopy(record)
                bad['steps'][0]['edits'][0]['execution_proved'] = True
                with self.assertRaises(SafetyError):
                    sequence.replay(parse(source), 'main.typ', self.catalog, bad, profile='conditional-gaps')
                with self.assertRaises(SafetyError):
                    sequence.replay(parse(source), 'main.typ', self.catalog, record)

    def test_original_prose_hint_suppression_and_other_unresolved_are_preserved(self):
        source = (b'#block[\n#paragraph_tab\nNext.\n\nText.\n#paragraph_tab\nAgain.]')
        selected = gap.preview(parse(source), 'main.typ', catalog=self.catalog, _special_only=True)
        self.assertEqual(len(selected['edits']), 1, selected)
        record = self.preview(source, profile='conditional-gaps')
        self.assertEqual(len(record['steps'][0]['edits']), 1, record)
        suppressed = (b'// typst-lint: ignore-next pde.paragraph-marker-gap -- intentional\n'
                      b'#block[\n#paragraph_tab\nNext.]')
        self.assertIsNone(self.preview(suppressed, profile='conditional-gaps'))
        unresolved = (b'#block[\n#paragraph_tab\nNext.\n\n#unknown()\n#paragraph_tab\nAgain.]\n'
                      b'Thus $ x^i $\n\nNext.')
        baseline = self.preview(unresolved)
        conditional = self.preview(unresolved, profile='conditional-gaps')
        self.assertEqual(baseline['steps'], conditional['steps'])
        self.assertTrue(all(step['rule_id'] != gap.RULE for step in conditional['steps']))

    def test_shadow_dynamic_comments_do_not_enter_special_plan(self):
        for prefix in (b'#let block = x => x\n', b'#for block in (x => x,) [ignored]\n'):
            source = prefix + b'#block[Before.\n\n#block[Proof.]\n#paragraph_tab\nNext.]'
            result = self.preview(source, profile='conditional-gaps')
            self.assertTrue(result is None or all(step['rule_id'] != gap.RULE for step in result['steps']), result)
        for previous in (b'#block(breakable: flag)[Proof.]', b'#block[Proof.] // note', b'#unknown()'):
            source = b'#block[Before.\n\n' + previous + b'\n#paragraph_tab\nNext.]'
            result = self.preview(source, profile='conditional-gaps')
            self.assertTrue(result is None or all(step['rule_id'] != gap.RULE for step in result['steps']), result)

    def test_forged_or_missing_proof_withholds_complete_sequence(self):
        source = b'#block[\n#paragraph_tab\nNext.]\nThus $ x^i $\n\nNext.'
        original = gap.preview
        def fake(tree, path, **kwargs):
            result = original(tree, path, **kwargs)
            if result is not None:
                result['conditions'] = ('forged',)
            return result
        for replacement in (lambda *a, **k: None, fake):
            with patch.object(gap, 'preview', side_effect=replacement):
                result = self.preview(source, profile='conditional-gaps')
            self.assertEqual(result['verification'], 'withheld')
            self.assertFalse(result['steps'])


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ComposedParagraphMarkerCliTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and sys.platform == 'darwin',
                         'Opt in on macOS with reviewed Typst')
    def test_compile_reports_conditional_plan_without_source_write(self):
        fixture = fixtures.DocumentPreviewCliTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        source = b'#let paragraph_tab = []\n#block[\n#paragraph_tab\nNext.]\n'
        (fixture.project / 'main.typ').write_bytes(source)
        run = subprocess.run(fixture.command('--conditional-gaps', '--review-edits', '--format', 'json'),
                             capture_output=True, timeout=120)
        report = json.loads(run.stdout)
        self.assertEqual(run.returncode, 2, report)
        self.assertTrue(report['compiler_invoked'] and report['pdf_generated'])
        self.assertEqual(report['planned_edits'], 1)
        self.assertIn(gap.RULE, report['conditionally_previewed_rule_ids'])
        self.assertFalse(report['source_apply_gate']['eligible'])
        self.assertEqual((fixture.project / 'main.typ').read_bytes(), source)
