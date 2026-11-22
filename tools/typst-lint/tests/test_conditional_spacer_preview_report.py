"""Report selected spacer proposals without attributing rendered changes."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_preview_cli
from syntax_client import EXECUTABLE
import test_document_preview_cli as fixtures
import test_partial_document_preview as partial_fixtures


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalSpacerPreviewReportTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DocumentPreviewCliTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.fake = SimpleNamespace(baseline_sha256='a' * 64, candidate_sha256='b' * 64,
            pdfs_identical=False, dependency_count=1, unobserved_capture_count=0,
            system_font_files=0, compiler_sha256='c' * 64, candidate_pdf=b'%PDF-fixture')

    def report(self, source, *flags):
        (self.f.project / 'main.typ').write_bytes(source)
        with patch.object(document_preview_cli.document_preview_compile, 'compile_preview', return_value=self.fake), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            status = document_preview_cli.main(self.f.command()[3:] + list(flags) + ['--format', 'json'])
        report = json.loads(output.getvalue())
        self.assertEqual(status, 2, report)
        self.assertEqual((self.f.project / 'main.typ').read_bytes(), source)
        return report

    def test_counts_only_actual_spacer_edits(self):
        flags = ('--conditional-gaps', '--conditional-spacers')
        note = b'$ x #dots_space #footnote[Note] $'
        for count in (0, 1, 2):
            with self.subTest(count=count):
                source = b'#block[' + b'\n'.join([note] * count) + b']'
                report = self.report(source, *flags)
                self.assertEqual(report['conditional_spacer_preview']['planned_edits'], count, report)
                self.assertIn('unproved', report['conditional_spacer_preview']['eligibility'])
                self.assertIn('visible content or layout', report['conditional_spacer_preview']['render_effect'])
                self.assertIn('does not attribute', report['conditional_spacer_preview']['pdf_attribution'])
                self.assertFalse(report['source_apply_gate']['eligible'] or report['publication_authority'])

    def test_other_rule_pdf_difference_does_not_imply_spacer_cause(self):
        report = self.report(b'Thus $ x^i $.\n', '--conditional-gaps', '--conditional-spacers')
        self.assertGreater(report['planned_edits'], 0)
        self.assertEqual(report['conditional_spacer_preview']['planned_edits'], 0)
        self.assertFalse(report['pdfs_identical'])
        self.assertIn('preview_pdfs_differ', report['source_apply_gate']['blockers'])
        self.assertIn('does not attribute', report['conditional_spacer_preview']['pdf_attribution'])
        plain = self.report(b'Thus $ x^i $.\n')
        self.assertNotIn('conditional_spacer_preview', plain)

    def test_unresolved_binding_does_not_claim_a_zero_candidate_plan(self):
        source = b'#let footnote(x) = x\n#block[$ x #dots_space #footnote[Note] $]'
        report = self.report(source, '--conditional-gaps', '--conditional-spacers')
        self.assertEqual(report['phase'], 'planning')
        self.assertFalse(report['pdf_generated'])
        self.assertNotIn('conditional_spacer_preview', report)

    def test_text_output_includes_actual_count_and_render_conditions(self):
        source = b'#block[$ x #dots_space #footnote[Note] $]'
        (self.f.project / 'main.typ').write_bytes(source)
        with patch.object(document_preview_cli.document_preview_compile, 'compile_preview', return_value=self.fake), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            status = document_preview_cli.main(self.f.command()[3:] + ['--conditional-gaps', '--conditional-spacers'])
        self.assertEqual(status, 2)
        self.assertIn('Conditional spacer removals planned: 1.', output.getvalue())
        self.assertIn('visible content or layout', output.getvalue())
        self.assertIn('does not attribute', output.getvalue())

    def test_caller_withheld_sources_are_not_counted(self):
        f = partial_fixtures.PartialDocumentPreviewTests('runTest')
        f.setUp()
        self.addCleanup(f.doCleanups)
        flags = ['--partial-unsupported-preview', '--withhold-source', 'bad.typ', '--format', 'json']
        with patch.object(document_preview_cli.document_preview_compile, 'compile_preview', return_value=self.fake), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            status = document_preview_cli.main(f.command(*flags))
        report = json.loads(output.getvalue())
        self.assertEqual(status, 2, report)
        self.assertEqual(report['conditional_spacer_preview']['planned_edits'], 1)
        self.assertEqual(report['withheld_sources'][0]['path'], 'bad.typ')
        self.assertFalse(report['source_apply_gate']['eligible'])
        flags = ['--partial-unsupported-preview', '--withhold-source', 'bad.typ',
                 '--withhold-source', 'good.typ', '--format', 'json']
        with patch.object(document_preview_cli.document_preview_compile, 'compile_preview') as compiler, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            status = document_preview_cli.main(f.command(*flags))
        report = json.loads(output.getvalue())
        self.assertEqual(status, 2, report)
        self.assertEqual(report['conditional_spacer_preview']['planned_edits'], 0)
        self.assertFalse(report['pdf_generated'])
        compiler.assert_not_called()

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and sys.platform == 'darwin',
                         'Opt in on macOS with the reviewed Typst compiler')
    def test_real_compiler_reports_spacer_without_claiming_visual_attribution(self):
        source = (b'#set page(width: 300pt, height: 250pt, margin: 30pt)\n'
                  b'#let dots_space = h(8pt)\n#block[$ A = B #dots_space #footnote[Reason.] $]')
        (self.f.project / 'main.typ').write_bytes(source)
        run = subprocess.run(self.f.command('--conditional-gaps', '--conditional-spacers', '--format', 'json'),
                             capture_output=True, timeout=60)
        report = json.loads(run.stdout)
        self.assertEqual(run.returncode, 2, report)
        self.assertTrue(report['pdf_generated'], report)
        self.assertEqual(report['conditional_spacer_preview']['planned_edits'], 1)
        self.assertFalse(report['pdfs_identical'])
        self.assertIn('does not attribute', report['conditional_spacer_preview']['pdf_attribution'])
        self.assertFalse(report['source_files_modified'] or report['source_apply_gate']['eligible'])
        self.assertEqual((self.f.project / 'main.typ').read_bytes(), source)
