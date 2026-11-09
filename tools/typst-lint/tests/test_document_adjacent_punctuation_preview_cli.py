"""CLI coverage of atomic adjacent punctuation moves on owned sources."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_cli
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class AdjacentPunctuationPreviewCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def run_cli(self, *extra, format='json'):
        command = [sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
                   '--root', str(self.root), '--entrypoint', 'main.typ', '--format', format, *extra]
        run = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertFalse(run.stderr)
        return json.loads(run.stdout) if format == 'json' else run.stdout

    def test_opt_in_reports_atomic_candidate_without_changing_sources_or_coverage(self):
        main = b'#include "child.typ"\n#block[Text#footnote[#emph[Reason.]].]'
        child = (b'// typst-lint: ignore-next pde.prose-footnote-punctuation -- intended\n'
                 b'#block[Other#footnote[X].]')
        (self.root / 'main.typ').write_bytes(main)
        (self.root / 'child.typ').write_bytes(child)
        baseline = self.run_cli('--rule-set', 'pde', '--local-rule-hints')
        self.assertNotIn('conditional_adjacent_punctuation_move_previews', baseline)
        report = self.run_cli('--rule-set', 'pde', '--local-rule-hints', '--preview-conditional-punctuation')
        rows = report['conditional_adjacent_punctuation_move_previews']
        self.assertFalse(report['compiler_invoked'] or report['applied_fixes'])
        self.assertEqual(report['coverage']['uninspected'], baseline['coverage']['uninspected'])
        self.assertEqual(report['local_rule_hints'], baseline['local_rule_hints'])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['path'], 'main.typ')
        self.assertTrue(row['atomic_edits'])
        self.assertFalse(row['runtime_binding_proved'] or row['execution_proved']
                         or row['edit_permission'] or row['compiler_permission'])
        self.assertEqual(len(row['edits']), 2)
        candidate = plan(main, [Edit(e['start'], e['end'], e['replacement'].encode())
                                for e in row['edits']]).candidate()
        self.assertEqual(row['baseline_sha256'], digest(main))
        self.assertEqual(row['candidate_sha256'], digest(candidate))
        self.assertIn(b'Text.#footnote[#emph[Reason.]]', candidate)
        text = self.run_cli('--rule-set', 'pde', '--preview-conditional-punctuation', format='text')
        for phrase in ('Conditional adjacent punctuation move previews:', 'condition:',
                       'not render-verified and not applied', 'inseparable'):
            self.assertIn(phrase, text)
        self.assertEqual((self.root / 'main.typ').read_bytes(), main)
        self.assertEqual((self.root / 'child.typ').read_bytes(), child)

    def test_whitespace_and_adjacent_moves_are_distinct_and_item_limit_applies(self):
        source = b'#block[First#footnote[X]. Second #footnote[Y].]'
        (self.root / 'main.typ').write_bytes(source)
        report = self.run_cli('--rule-set', 'pde', '--preview-conditional-punctuation')
        adjacent, = report['conditional_adjacent_punctuation_move_previews']
        spaced, = report['conditional_punctuation_move_previews']
        self.assertEqual(len(adjacent['edits']), 2)
        self.assertEqual(len(spaced['edits']), 1)
        self.assertLess(adjacent['edits'][-1]['end'], spaced['edits'][0]['start'])
        self.assertEqual((self.root / 'main.typ').read_bytes(), source)
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 3):
            with self.assertRaisesRegex(InputError, 'report item limit'):
                document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                             preview_conditional_punctuation=True)
