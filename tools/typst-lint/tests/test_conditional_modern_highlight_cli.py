"""The modern highlight artifact has a separate explicit document option."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_modern_highlight as modern
import document_cli
from syntax_client import EXECUTABLE
from typst_lint import InputError

MODERN = TOOL / 'tests/fixtures/reviewed-text-utils-main/local/text-utils/0.1.2'
OLD = TOOL / 'tests/fixtures/reviewed-packages/local/text-utils/0.1.2'


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalModernHighlightCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='modern-highlight-cli-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.project, self.packages, self.cache = (root / name for name in ('project', 'packages', 'cache'))
        for folder in (self.project, self.packages, self.cache):
            folder.mkdir()
        self.target = self.packages / 'local/text-utils/0.1.2'
        self.target.parent.mkdir(parents=True)
        shutil.copytree(MODERN, self.target)
        self.source = (b'#import "@local/text-utils:0.1.2": *\n'
                       b'#highlighted(mode: "phrase")[Before $x$ after]')
        (self.project / 'main.typ').write_bytes(self.source)

    def command(self, *flags, format='json'):
        process = subprocess.run([sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
            '--root', str(self.project), '--entrypoint', 'main.typ', '--rule-set', 'pde',
            '--package-path', str(self.packages), '--footnote-cache-path', str(self.cache),
            '--format', format, *flags], capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertFalse(process.stderr)
        return json.loads(process.stdout) if format == 'json' else process.stdout

    def test_explicit_option_adds_separate_json_and_text_hints(self):
        ordinary = self.command()
        self.assertNotIn('conditional_modern_highlight', ordinary)
        report = self.command('--modern-highlight-evidence')
        self.assertEqual(report['diagnostics'], ordinary['diagnostics'])
        self.assertEqual(report['conditional_diagnostics'], ordinary['conditional_diagnostics'])
        self.assertEqual(report['coverage'], ordinary['coverage'])
        section = report['conditional_modern_highlight']
        self.assertEqual(section['status'], 'CONDITIONAL')
        row, = section['by_file']
        finding, = row['diagnostics']
        self.assertEqual((finding['mode'], finding['mode_source']), ('phrase', 'explicit'))
        self.assertEqual(row['package_evidence']['source_sha256'], modern.MODERN_SOURCE_SHA256)
        self.assertFalse(section['edit_permission'] or section['compiler_permission'] or section['coverage_complete'])
        self.assertFalse(report['compiler_invoked'] or report['applied_fixes'])
        text = self.command('--modern-highlight-evidence', format='text')
        for expected in ('Modern highlight evidence: CONDITIONAL', 'mode phrase (explicit)',
                         'condition:', 'diagnostic-only, no edit or compiler permission'):
            self.assertIn(expected, text)
        self.assertEqual((self.project / 'main.typ').read_bytes(), self.source)

    def test_older_first_root_cannot_fall_back_to_modern_cache(self):
        cached = self.cache / 'local/text-utils/0.1.2'
        cached.parent.mkdir(parents=True)
        shutil.copytree(self.target, cached)
        shutil.rmtree(self.target)
        cache_only = self.command('--modern-highlight-evidence')['conditional_modern_highlight']
        self.assertEqual(cache_only['status'], 'CONDITIONAL')
        self.assertTrue(cache_only['by_file'][0]['package_evidence']['source_identity'].startswith(
            str(self.cache) + '/'))
        shutil.copytree(OLD, self.target)
        report = self.command('--modern-highlight-evidence')
        self.assertEqual(report['conditional_modern_highlight']['status'], 'UNAVAILABLE')
        self.assertFalse(report['conditional_modern_highlight']['by_file'])
        self.assertIn('unresolved:', self.command('--modern-highlight-evidence', format='text'))

    def test_option_requires_roots_and_full_document_byte_budget(self):
        with self.assertRaisesRegex(InputError, 'Modern highlight evidence requires'):
            document_cli.report_document(str(self.project), 'main.typ', rule_set='pde',
                                         modern_highlight_evidence=True)
        with patch.object(document_cli, 'MAX_MODERN_REPORT_BYTES', 1):
            with self.assertRaisesRegex(InputError, 'report byte limit'):
                document_cli.report_document(str(self.project), 'main.typ', rule_set='pde',
                    package_path=str(self.packages), footnote_cache_path=str(self.cache),
                    modern_highlight_evidence=True)


if __name__ == '__main__':
    unittest.main()
