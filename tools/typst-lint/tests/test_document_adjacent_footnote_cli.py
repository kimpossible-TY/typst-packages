"""Opt-in read-only detail reporting for existing footnote hints."""
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
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class AdjacentFootnoteDocumentCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def run_cli(self, *args, format='json'):
        run = subprocess.run([sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
                              '--root', str(self.root), '--entrypoint', 'main.typ',
                              '--format', format, *args], capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertFalse(run.stderr)
        return json.loads(run.stdout) if format == 'json' else run.stdout

    def test_opt_in_reports_details_without_counting_them_as_new_hints(self):
        main = (b'#include "child.typ"\n#block[Text#footnote[Reason.]. Next.]\n'
                b'// typst-lint: ignore-next pde.prose-footnote-punctuation -- intentional\n'
                b'#block[$x$#footnote[Suppressed.], next.]')
        child = b'#let footnote = none\n#block[Text#footnote[Other.]. Next.]'
        (self.root / 'main.typ').write_bytes(main)
        (self.root / 'child.typ').write_bytes(child)
        options = ('--rule-set', 'pde', '--local-rule-hints')
        baseline = self.run_cli(*options)
        self.assertNotIn('conditional_punctuation_details', baseline)
        self.assertNotIn('conditional_punctuation_previews', baseline)
        report = self.run_cli(*options, '--preview-conditional-punctuation')
        details = report['conditional_punctuation_details']
        self.assertTrue(details['duplicates_existing_hints'])
        self.assertFalse(details['edit_permission'] or details['compiler_permission']
                         or report['compiler_invoked'] or report['applied_fixes'])
        self.assertEqual(report['coverage']['uninspected'], baseline['coverage']['uninspected'])
        self.assertEqual(report['local_rule_hints'], baseline['local_rule_hints'])
        rows = {row['path']: row for row in details['by_file']}
        self.assertEqual(rows['main.typ']['source_sha256'], digest(main))
        self.assertEqual(len(rows['main.typ']['diagnostics']), 1)
        self.assertEqual(len(rows['main.typ']['suppressed']), 1)
        self.assertTrue(rows['main.typ']['conditions'])
        item, = rows['main.typ']['diagnostics']
        self.assertEqual(main[item['punctuation_start']:item['punctuation_end']], b'.')
        self.assertEqual(item['preceding_kind'], 'Text')
        self.assertTrue(rows['child.typ']['unresolved'])
        self.assertFalse(rows['child.typ']['diagnostics'])
        text = self.run_cli(*options, '--preview-conditional-punctuation', format='text')
        for phrase in ('Conditional adjacent footnote punctuation details:', 'classify existing hints',
                       'conditional: footnote bytes', 'suppressed: footnote bytes',
                       'unresolved:', 'condition:', 'no edit or compiler permission'):
            self.assertIn(phrase, text)
        self.assertEqual((self.root / 'main.typ').read_bytes(), main)
        self.assertEqual((self.root / 'child.typ').read_bytes(), child)

    def test_existing_option_still_requires_pde(self):
        (self.root / 'main.typ').write_bytes(b'#block[Text#footnote[Reason.]. Next.]')
        report = self.run_cli('--preview-conditional-punctuation')
        self.assertEqual(report['phase'], 'input')
        self.assertFalse(report['compiler_invoked'] or report['applied_fixes'])

    def test_details_obey_shared_report_item_limit(self):
        (self.root / 'main.typ').write_bytes(b'#block[Text#footnote[Reason.]. Next.]')
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 3):
            with self.assertRaisesRegex(InputError, 'report item limit'):
                document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                             preview_conditional_punctuation=True)
