"""End-to-end source-only prose-footnote/display-gap previews."""
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
class ProseFootnoteDisplayGapCliTests(unittest.TestCase):
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

    def test_opt_in_reports_candidate_without_changing_files_or_coverage(self):
        main = b'#include "child.typ"\n#block[Text.#footnote[A.]\n\n$ A = B $]'
        child = (b'// typst-lint: ignore-next pde.footnote-trailing-gap -- intended\n'
                 b'#block[Other.#footnote[B.]\n\n$ x = y $]')
        (self.root / 'main.typ').write_bytes(main)
        (self.root / 'child.typ').write_bytes(child)
        baseline = self.run_cli('--rule-set', 'pde', '--local-rule-hints')
        self.assertNotIn('conditional_prose_footnote_display_gap_previews', baseline)
        report = self.run_cli('--rule-set', 'pde', '--local-rule-hints', '--preview-conditional-gaps')
        rows = report['conditional_prose_footnote_display_gap_previews']
        self.assertFalse(report['compiler_invoked'] or report['applied_fixes'])
        self.assertEqual(report['coverage']['uninspected'], baseline['coverage']['uninspected'])
        self.assertEqual(report['local_rule_hints'], baseline['local_rule_hints'])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['path'], 'main.typ')
        self.assertFalse(row['runtime_binding_proved'] or row['execution_proved']
                         or row['edit_permission'] or row['compiler_permission'])
        edit, = row['edits']
        candidate = plan(main, [Edit(edit['start'], edit['end'], edit['replacement'].encode())]).candidate()
        self.assertEqual(row['baseline_sha256'], digest(main))
        self.assertEqual(row['candidate_sha256'], digest(candidate))
        self.assertIn(b'#footnote[A.]\n#parbreak()\n\n$ A = B $', candidate)
        text = self.run_cli('--rule-set', 'pde', '--preview-conditional-gaps', format='text')
        for phrase in ('Conditional prose footnote display gap previews: 1 edits',
                       'condition:', 'not render-verified and not applied'):
            self.assertIn(phrase, text)
        self.assertEqual((self.root / 'main.typ').read_bytes(), main)
        self.assertEqual((self.root / 'child.typ').read_bytes(), child)

    def test_other_gap_previews_are_separate_and_report_limit_applies(self):
        source = (b'#block[Text.#footnote[X]\n\n$ A = B $]\n'
                  b'#block[Text.#footnote[Y]\n\n#paragraph_tab\nNext.]')
        (self.root / 'main.typ').write_bytes(source)
        report = self.run_cli('--rule-set', 'pde', '--preview-conditional-gaps')
        self.assertEqual(len(report['conditional_prose_footnote_display_gap_previews']), 1)
        self.assertEqual(len(report['conditional_prose_footnote_gap_previews']), 1)
        self.assertEqual((self.root / 'main.typ').read_bytes(), source)
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 3):
            with self.assertRaisesRegex(InputError, 'report item limit'):
                document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                             preview_conditional_gaps=True)
