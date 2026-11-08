"""CLI and resource-limit coverage for hyphenated marker gap previews."""
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
class ConditionalHyphenMarkerGapCliTests(unittest.TestCase):
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

    def test_opt_in_reports_candidate_and_unresolved_without_changing_sources(self):
        main = (b'#include "child.typ"\n#block[First.\n#paragraph-tab\nSecond.]\n'
                b'#block[Before.#unknown[X]\n#paragraph-tab\nNext.]')
        child = (b'// typst-lint: ignore-next pde.paragraph-marker-gap -- intended\n'
                 b'#block[Other.\n#paragraph-tab\nNext.]')
        (self.root / 'main.typ').write_bytes(main)
        (self.root / 'child.typ').write_bytes(child)
        baseline = self.run_cli('--rule-set', 'pde', '--local-rule-hints')
        self.assertNotIn('conditional_hyphen_marker_gap_previews', baseline)
        report = self.run_cli('--rule-set', 'pde', '--local-rule-hints', '--preview-conditional-paragraph-gaps')
        section = report['conditional_hyphen_marker_gap_previews']
        self.assertFalse(section['edit_permission'] or section['compiler_permission']
                         or report['compiler_invoked'] or report['applied_fixes'])
        self.assertEqual(report['coverage']['uninspected'], baseline['coverage']['uninspected'])
        self.assertEqual(report['local_rule_hints'], baseline['local_rule_hints'])
        rows = {row['path']: row for row in section['by_file']}
        self.assertEqual(rows['child.typ']['verification'], 'no-candidate')
        row = rows['main.typ']
        self.assertEqual(row['verification'], 'withheld')
        self.assertEqual(len(row['unresolved']), 1)
        self.assertFalse(row['runtime_binding_proved'] or row['execution_proved'])
        self.assertEqual(row['baseline_sha256'], digest(main))
        self.assertIsNone(row['candidate_sha256'])
        self.assertFalse(row['edits'])
        text = self.run_cli('--rule-set', 'pde', '--preview-conditional-paragraph-gaps', format='text')
        for phrase in ('Conditional hyphen marker gap previews:', 'withheld',
                       'unresolved marker at byte', 'condition:', 'no edit or compiler permission'):
            self.assertIn(phrase, text)
        self.assertEqual((self.root / 'main.typ').read_bytes(), main)
        self.assertEqual((self.root / 'child.typ').read_bytes(), child)

    def test_underscore_and_hyphen_candidates_are_distinct_and_limit_applies(self):
        source = (b'#block[First.\n#paragraph-tab\nSecond.]\n'
                  b'#block[Third.\n#paragraph_tab\nFourth.]')
        (self.root / 'main.typ').write_bytes(source)
        report = self.run_cli('--rule-set', 'pde', '--preview-conditional-paragraph-gaps')
        hyphen = report['conditional_hyphen_marker_gap_previews']['by_file'][0]
        underscore = report['conditional_paragraph_gap_previews']['by_file'][0]
        self.assertEqual(len(hyphen['edits']), 1)
        self.assertEqual(len(underscore['edits']), 1)
        self.assertNotEqual(hyphen['edits'][0]['start'], underscore['edits'][0]['start'])
        self.assertEqual((self.root / 'main.typ').read_bytes(), source)
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 3):
            with self.assertRaisesRegex(InputError, 'report item limit'):
                document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                             preview_conditional_paragraph_gaps=True)
