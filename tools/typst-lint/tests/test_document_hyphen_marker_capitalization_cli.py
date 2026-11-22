"""Hyphen capitalization is a separate conditional source-only preview."""
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
import document_hyphen_marker_capitalization as capitalization
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class HyphenMarkerCapitalizationCliTests(unittest.TestCase):
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

    def test_opt_in_json_text_and_original_sources(self):
        main = b'#include "child.typ"\n#block[First.\n#paragraph-tab\ntherefore x.]'
        child = (b'#block[First.\n// typst-lint: ignore-next pde.paragraph-capitalization -- intended\n'
                 b'#paragraph-tab\ntherefore x.]')
        (self.root / 'main.typ').write_bytes(main)
        (self.root / 'child.typ').write_bytes(child)
        ordinary = self.run_cli('--rule-set', 'pde')
        self.assertNotIn('conditional_hyphen_capitalization_previews', ordinary)
        options = ('--rule-set', 'pde', '--preview-conditional-capitalization')
        report = self.run_cli(*options)
        self.assertEqual(report['coverage'], ordinary['coverage'])
        self.assertEqual(report['diagnostics'], ordinary['diagnostics'])
        self.assertFalse(report['applied_fixes'] or report['compiler_invoked'])
        section = report['conditional_hyphen_capitalization_previews']
        self.assertFalse(section['edit_permission'] or section['compiler_permission'])
        rows = {row['path']: row for row in section['by_file']}
        self.assertEqual(rows['child.typ']['verification'], 'no-candidate')
        edit, = rows['main.typ']['edits']
        candidate = plan(main, [Edit(edit['start'], edit['end'], edit['replacement'].encode())]).candidate()
        self.assertIn(b'Therefore x.', candidate)
        self.assertEqual(rows['main.typ']['baseline_sha256'], digest(main))
        self.assertEqual(rows['main.typ']['candidate_sha256'], digest(candidate))
        self.assertFalse(rows['main.typ']['runtime_binding_proved'] or rows['main.typ']['execution_proved'])
        text = self.run_cli(*options, format='text')
        for phrase in ('Conditional hyphen-marker capitalization previews:', 'conditional edit bytes',
                       'replace with T', 'source sha256', 'condition:', 'no edit or compiler permission'):
            self.assertIn(phrase, text)
        self.assertEqual((self.root / 'main.typ').read_bytes(), main)
        self.assertEqual((self.root / 'child.typ').read_bytes(), child)

    def test_opt_out_and_report_limit(self):
        (self.root / 'main.typ').write_bytes(b'#block[First.\n#paragraph-tab\ntherefore x.]')
        with patch.object(capitalization, 'preview', side_effect=AssertionError('unexpected opt-in')):
            report = document_cli.report_document(str(self.root), 'main.typ', rule_set='pde')
        self.assertNotIn('conditional_hyphen_capitalization_previews', report)
        with patch.object(capitalization, 'preview', wraps=capitalization.preview) as previewed:
            report = document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                                  preview_conditional_capitalization=True)
        previewed.assert_called_once()
        large = dict(report['conditional_hyphen_capitalization_previews']['by_file'][0])
        large['conditions'] = ['fixture'] * 1001
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 1000), \
             patch.object(capitalization, 'preview', return_value=large) as previewed:
            with self.assertRaisesRegex(InputError, 'report item limit'):
                document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                             preview_conditional_capitalization=True)
            previewed.assert_called_once()
