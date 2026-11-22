"""Scoped marker-tag hints stay separate from document coverage and edits."""
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
import document_conditional_marker_tags as marker_tags
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalMarkerTagsCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def run_cli(self, *args, format='json'):
        result = subprocess.run([sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
            '--root', str(self.root), '--entrypoint', 'main.typ', '--format', format, *args],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(result.stderr)
        return json.loads(result.stdout) if format == 'json' else result.stdout

    def test_opt_in_json_text_suppression_and_coverage(self):
        main = b'#include "child.typ"\n#local-scope-annotations(s => [$ mark(x, tag: #<x>) $])'
        child = (b'// typst-lint: ignore-next core.scoped-marker-tags -- external label\n'
                 b'#local-scope-annotations(s => [$ mark(y, tag: #<y>) $])')
        (self.root / 'main.typ').write_bytes(main)
        (self.root / 'child.typ').write_bytes(child)
        ordinary = self.run_cli('--rule-set', 'pde')
        self.assertNotIn('conditional_marker_tags', ordinary)
        result = self.run_cli('--rule-set', 'pde', '--preview-conditional-scoped-calls')
        self.assertEqual(result['coverage'], ordinary['coverage'])
        self.assertEqual(result['diagnostics'], ordinary['diagnostics'])
        self.assertFalse(result['applied_fixes'] or result['compiler_invoked'])
        section = result['conditional_marker_tags']
        self.assertFalse(section['edit_permission'] or section['compiler_permission'] or section['coverage_complete'])
        rows = {row['path']: row for row in section['by_file']}
        for path, source, key in (('main.typ', main, 'diagnostics'), ('child.typ', child, 'suppressed')):
            row = rows[path]
            finding, = row[key]
            self.assertEqual(row['source_sha256'], digest(source))
            self.assertEqual(finding['rule_id'], 'core.scoped-marker-tags')
            self.assertTrue(row['conditions'])
            self.assertFalse(row['runtime_binding_proved'] or row['execution_proved'])
        text = self.run_cli('--rule-set', 'pde', '--preview-conditional-scoped-calls', format='text')
        for phrase in ('Conditional marker tags:', 'hint main.typ:', 'suppressed child.typ:',
                       'source sha256', 'condition:', 'no edit or compiler permission'):
            self.assertIn(phrase, text)
        self.assertEqual((self.root / 'main.typ').read_bytes(), main)
        self.assertEqual((self.root / 'child.typ').read_bytes(), child)

    def test_opt_out_does_not_run_analyzer_and_limit_is_enforced(self):
        (self.root / 'main.typ').write_bytes(b'#local-scope-annotations(s => [$ mark(x, tag: #<x>) $])')
        with patch.object(marker_tags, 'analyze', side_effect=AssertionError('unexpected opt-in')):
            report = document_cli.report_document(str(self.root), 'main.typ', rule_set='pde')
        self.assertNotIn('conditional_marker_tags', report)
        with patch.object(marker_tags, 'analyze', wraps=marker_tags.analyze) as analyzed:
            report = document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                                  preview_conditional_scoped_calls=True)
        analyzed.assert_called_once()
        large = dict(report['conditional_marker_tags']['by_file'][0])
        large['diagnostics'] = [{}] * 1001
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 1000), \
             patch.object(marker_tags, 'analyze', return_value=large) as analyzed:
            with self.assertRaisesRegex(InputError, 'report item limit'):
                document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                             preview_conditional_scoped_calls=True)
            analyzed.assert_called_once()
