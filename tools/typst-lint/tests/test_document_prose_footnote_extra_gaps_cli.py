"""Conditional extra gap preview is an independent opt-in section."""
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
import document_prose_footnote_extra_gaps as gaps
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ProseFootnoteExtraGapsCliTests(unittest.TestCase):
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

    def test_opt_in_reports_distinct_patterns_and_preserves_sources(self):
        main = b'#include "child.typ"\n#block[Text.#footnote[Note.]\n\n#paragraph-tab\nNext.]'
        child = b'#block[Text.#footnote[Note.]\n\n#figure([x])]'
        (self.root / 'main.typ').write_bytes(main)
        (self.root / 'child.typ').write_bytes(child)
        ordinary = self.run_cli('--rule-set', 'pde')
        self.assertNotIn('conditional_prose_footnote_extra_gap_previews', ordinary)
        result = self.run_cli('--rule-set', 'pde', '--preview-conditional-gaps')
        self.assertEqual(result['coverage'], ordinary['coverage'])
        self.assertEqual(result['diagnostics'], ordinary['diagnostics'])
        self.assertFalse(result['applied_fixes'] or result['compiler_invoked'])
        rows = {row['path']: row for row in result['conditional_prose_footnote_extra_gap_previews']}
        for path, source, kind in (('main.typ', main, 'hyphenated-paragraph-marker'), ('child.typ', child, 'figure')):
            row = rows[path]
            edit, = row['edits']
            self.assertEqual(edit['following_kind'], kind)
            self.assertTrue(edit['following_conditions'])
            self.assertFalse(row['runtime_binding_proved'] or row['execution_proved']
                             or row['compiler_permission'] or row['edit_permission'])
            candidate = plan(source, [Edit(edit['start'], edit['end'], edit['replacement'].encode())]).candidate()
            self.assertEqual(row['baseline_sha256'], digest(source))
            self.assertEqual(row['candidate_sha256'], digest(candidate))
        text = self.run_cli('--rule-set', 'pde', '--preview-conditional-gaps', format='text')
        for phrase in ('Conditional prose footnote marker/figure gap', 'hyphenated-paragraph-marker',
                       'conditional figure', 'condition:', 'not applied'):
            self.assertIn(phrase, text)
        self.assertEqual((self.root / 'main.typ').read_bytes(), main)
        self.assertEqual((self.root / 'child.typ').read_bytes(), child)

    def test_opt_out_and_limit(self):
        (self.root / 'main.typ').write_bytes(b'#block[Text.#footnote[Note.]\n\n#figure([x])]')
        with patch.object(gaps, 'preview', side_effect=AssertionError('ran without option')):
            report = document_cli.report_document(str(self.root), 'main.typ', rule_set='pde')
        self.assertNotIn('conditional_prose_footnote_extra_gap_previews', report)
        with patch.object(gaps, 'preview', wraps=gaps.preview) as previewed:
            result = document_cli.report_document(str(self.root), 'main.typ', rule_set='pde', preview_conditional_gaps=True)
        previewed.assert_called_once()
        large = dict(result['conditional_prose_footnote_extra_gap_previews'][0])
        edit = dict(large['edits'][0])
        edit['following_conditions'] = ['fixture'] * 1001
        large['edits'] = [edit]
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 1000), \
             patch.object(gaps, 'preview', return_value=large) as previewed:
            with self.assertRaisesRegex(InputError, 'report item limit'):
                document_cli.report_document(str(self.root), 'main.typ', rule_set='pde', preview_conditional_gaps=True)
            previewed.assert_called_once()
