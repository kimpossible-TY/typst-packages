"""Opt-in modern highlight splits are separate, conditional source proposals."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_cli
import document_modern_highlight_preview as preview
from edit_plan import Edit, plan
import test_conditional_modern_highlight as fixtures
from typst_lint import InputError, digest


@unittest.skipUnless(fixtures.EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ModernHighlightPreviewCliTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ConditionalModernHighlightTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project, self.packages, self.cache = self.fixture.project, self.fixture.packages, self.fixture.cache

    def run_cli(self, *args, format='json'):
        run = subprocess.run([sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
            '--root', str(self.project), '--entrypoint', 'main.typ', '--format', format, *args],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertFalse(run.stderr)
        return json.loads(run.stdout) if format == 'json' else run.stdout

    def options(self):
        return ('--rule-set', 'pde', '--package-path', str(self.packages),
                '--footnote-cache-path', str(self.cache), '--modern-highlight-evidence')

    def test_preview_is_opt_in_independent_and_preserves_source(self):
        source = fixtures.IMPORT + '#highlighted[한글 $x$ 다음]'.encode()
        (self.project / 'main.typ').write_bytes(source)
        base = self.run_cli(*self.options())
        self.assertNotIn('conditional_modern_highlight_previews', base)
        result = self.run_cli(*self.options(), '--preview-conditional-modern-highlights')
        self.assertEqual(result['conditional_modern_highlight'], base['conditional_modern_highlight'])
        self.assertEqual(result['coverage'], base['coverage'])
        self.assertFalse(result['applied_fixes'] or result['compiler_invoked'])
        section = result['conditional_modern_highlight_previews']
        self.assertFalse(section['edit_permission'] or section['compiler_permission']
                         or section['runtime_binding_proved'] or section['coverage_complete'])
        row, = section['by_file']
        edit, = row['edits']
        candidate = plan(source, [Edit(edit['start'], edit['end'], edit['replacement'].encode())]).candidate()
        self.assertEqual(row['baseline_sha256'], digest(source))
        self.assertEqual(row['candidate_sha256'], digest(candidate))
        self.assertIn('#highlighted[한글 ]$x$#highlighted[ 다음]'.encode(), candidate)
        text = self.run_cli(*self.options(), '--preview-conditional-modern-highlights', format='text')
        for phrase in ('Conditional modern highlight previews:', 'conditional edit bytes',
                       'replacement in JSON', 'condition:', 'no edit or compiler permission'):
            self.assertIn(phrase, text)
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)

    def test_missing_or_wrong_evidence_is_explicitly_unavailable(self):
        source = fixtures.IMPORT + b'#highlighted[Text $x$ end.]'
        (self.project / 'main.typ').write_bytes(source)
        missing = self.run_cli('--rule-set', 'pde', '--preview-conditional-modern-highlights')
        self.assertEqual(missing['phase'], 'input')
        shutil.rmtree(self.fixture.target)
        shutil.copytree(fixtures.OLD, self.fixture.target)
        unavailable = self.run_cli(*self.options(), '--preview-conditional-modern-highlights')
        section = unavailable['conditional_modern_highlight_previews']
        self.assertEqual(section['status'], 'UNAVAILABLE')
        self.assertTrue(section['reason'])
        self.assertFalse(section['by_file'] or section['edit_permission'] or section['compiler_permission'])

    def test_empty_parentheses_and_smart_quotes_reach_cli_preview(self):
        source = fixtures.IMPORT + b'#highlighted()["Before $x$ after"]'
        (self.project / 'main.typ').write_bytes(source)
        report = self.run_cli(*self.options(), '--preview-conditional-modern-highlights')
        section = report['conditional_modern_highlight_previews']
        row, = section['by_file']
        edit, = row['edits']
        candidate = plan(source, [Edit(edit['start'], edit['end'], edit['replacement'].encode())]).candidate()
        self.assertIn(b'#highlighted["Before ]$x$#highlighted[ after"]', candidate)
        self.assertTrue(any('smart-quote direction' in condition for condition in section['conditions']))
        self.assertFalse(report['applied_fixes'] or report['compiler_invoked'])
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)

    def test_opt_out_does_not_call_preview_and_shared_budget_is_enforced(self):
        source = fixtures.IMPORT + b'#highlighted[Text $x$ end.]'
        (self.project / 'main.typ').write_bytes(source)
        with patch.object(preview, 'preview_all', side_effect=AssertionError('ran without option')):
            report = document_cli.report_document(str(self.project), 'main.typ', rule_set='pde')
        self.assertNotIn('conditional_modern_highlight_previews', report)
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 1):
            with self.assertRaises(InputError):
                document_cli.report_document(str(self.project), 'main.typ', rule_set='pde',
                    package_path=str(self.packages), footnote_cache_path=str(self.cache),
                    modern_highlight_evidence=True, preview_conditional_modern_highlights=True)
