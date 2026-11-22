"""Opt-in scoped-call document reporting over owned source fixtures."""
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
class ConditionalScopedDocumentCliTests(unittest.TestCase):
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

    def test_opt_in_per_file_reports_edit_suppression_and_unresolved(self):
        main = b'#include "child.typ"\n#local-tag-scope(s => [#s.tag("x")])'
        child = (b'// typst-lint: ignore-next core.scoped-function-call -- intentional\n'
                 b'#local-tag-scope(t => [#t.tag("ignored")])\n'
                 b'#local-tag-scope(t => [#unknown(t) #t.tag("x")])')
        (self.root / 'main.typ').write_bytes(main)
        (self.root / 'child.typ').write_bytes(child)
        baseline = self.run_cli('--rule-set', 'pde')
        self.assertNotIn('conditional_scoped_calls', baseline)
        options = ('--rule-set', 'pde', '--preview-conditional-scoped-calls')
        report = self.run_cli(*options)
        data = report['conditional_scoped_calls']
        self.assertFalse(data['edit_permission'] or data['compiler_permission']
                         or report['compiler_invoked'] or report['applied_fixes'])
        self.assertEqual(report['coverage']['uninspected'], baseline['coverage']['uninspected'])
        rows = {item['path']: item for item in data['by_file']}
        self.assertEqual(rows['main.typ']['source_sha256'], digest(main))
        self.assertEqual(len(rows['main.typ']['diagnostics']), 1)
        edit, = rows['main.typ']['edits']
        self.assertEqual(main[edit['start']:edit['end']], b's.tag')
        self.assertEqual(edit['replacement'], '(s.tag)')
        self.assertEqual(len(rows['child.typ']['suppressed']), 1)
        self.assertTrue(rows['child.typ']['unresolved'])
        self.assertTrue(all(not row['edit_permission'] and not row['compiler_permission']
                            and not row['runtime_binding_proved'] for row in rows.values()))
        text = self.run_cli(*options, format='text')
        for phrase in ('Conditional scoped calls:', 'conditional edit bytes', '(s.tag)',
                       'unresolved:', 'condition:', 'no edit or compiler permission'):
            self.assertIn(phrase, text)
        self.assertEqual((self.root / 'main.typ').read_bytes(), main)
        self.assertEqual((self.root / 'child.typ').read_bytes(), child)

    def test_option_requires_pde_and_rejects_invalid_api_type(self):
        (self.root / 'main.typ').write_bytes(b'Hello.')
        report = self.run_cli('--preview-conditional-scoped-calls')
        self.assertEqual(report['phase'], 'input')
        self.assertFalse(report['compiler_invoked'] or report['applied_fixes'])
        with self.assertRaises(InputError):
            document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                         preview_conditional_scoped_calls=1)

    def test_item_limit_refuses_large_independent_report(self):
        (self.root / 'main.typ').write_bytes(b'#local-tag-scope(s => [#s.tag("x")])')
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 3):
            with self.assertRaisesRegex(InputError, 'report item limit'):
                document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                             preview_conditional_scoped_calls=True)
