"""Independent nested paragraph-gap previews on owned documents."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_cli
import document_conditional_paragraph_gap as paragraph_gap
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalParagraphGapCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def run_cli(self, *args, format='json'):
        run = subprocess.run([sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
            '--root', str(self.root), '--entrypoint', 'main.typ', '--format', format, *args],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertFalse(run.stderr)
        return json.loads(run.stdout) if format == 'json' else run.stdout

    def test_json_text_conditions_and_unchanged_sources(self):
        main = b'#include "child.typ"\n#block[First.\r\n#paragraph_tab\r\nNext.]'
        child = (b'#block[First.\n// typst-lint: ignore-next pde.paragraph-marker-gap -- intentional\n'
                 b'#paragraph_tab\nNext.]')
        (self.root / 'main.typ').write_bytes(main)
        (self.root / 'child.typ').write_bytes(child)
        ordinary = self.run_cli('--rule-set', 'pde', '--local-rule-hints')
        self.assertNotIn('conditional_paragraph_gap_previews', ordinary)
        options = ('--rule-set', 'pde', '--local-rule-hints', '--preview-conditional-paragraph-gaps')
        report = self.run_cli(*options)
        self.assertEqual(report['coverage'], ordinary['coverage'])
        self.assertEqual(report['local_rule_hints'], ordinary['local_rule_hints'])
        section = report['conditional_paragraph_gap_previews']
        self.assertFalse(section['edit_permission'] or section['compiler_permission']
                         or report['applied_fixes'] or report['compiler_invoked'])
        rows = {row['path']: row for row in section['by_file']}
        self.assertEqual(rows['child.typ']['verification'], 'no-candidate')
        edit, = rows['main.typ']['edits']
        self.assertEqual(edit['replacement'], '\r\n')
        candidate = plan(main, [Edit(edit['start'], edit['end'], edit['replacement'].encode())]).candidate()
        self.assertEqual(rows['main.typ']['baseline_sha256'], digest(main))
        self.assertEqual(rows['main.typ']['candidate_sha256'], digest(candidate))
        self.assertIn(b'First.\r\n\r\n#paragraph_tab', candidate)
        self.assertFalse(rows['main.typ']['runtime_binding_proved'] or rows['main.typ']['execution_proved'])
        text = self.run_cli(*options, format='text')
        for phrase in ('Conditional paragraph gap previews:', 'conditional edit bytes', 'insert blank CRLF line',
                       'source sha256', 'candidate sha256', 'condition:', 'no edit or compiler permission'):
            self.assertIn(phrase, text)
        self.assertEqual((self.root / 'main.typ').read_bytes(), main)
        self.assertEqual((self.root / 'child.typ').read_bytes(), child)

    def test_opt_out_type_and_budget(self):
        (self.root / 'main.typ').write_bytes(b'#block[First.\n#paragraph_tab\nNext.]')
        with patch.object(paragraph_gap, 'preview', side_effect=AssertionError('unexpected opt-in')):
            report = document_cli.report_document(str(self.root), 'main.typ', rule_set='pde')
        self.assertNotIn('conditional_paragraph_gap_previews', report)
        self.assertEqual(self.run_cli('--preview-conditional-paragraph-gaps')['phase'], 'input')
        with self.assertRaises(InputError):
            document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                         preview_conditional_paragraph_gaps=1)
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 3):
            with self.assertRaisesRegex(InputError, 'report item limit'):
                document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                             preview_conditional_paragraph_gaps=True)
