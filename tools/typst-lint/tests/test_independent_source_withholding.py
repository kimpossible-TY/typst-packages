"""Caller-selected source withholding works without changing strict rule profiles."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_preview_cli as cli
import document_resources
import document_sequence_preview as sequence
import include_context
from syntax_client import EXECUTABLE
from typst_lint import InputError, SafetyError
import test_partial_document_preview as fixtures


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class IndependentSourceWithholdingTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PartialDocumentPreviewTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.good = b'Thus $ x^i $.\n'
        self.f.bad = b'This is $ a^j $.\n'
        (self.f.project / 'good.typ').write_bytes(self.f.good)
        (self.f.project / 'bad.typ').write_bytes(self.f.bad)
        self.fake = SimpleNamespace(baseline_sha256='a' * 64, candidate_sha256='a' * 64,
            pdfs_identical=True, dependency_count=1, unobserved_capture_count=0,
            system_font_files=0, compiler_sha256='b' * 64, candidate_pdf=b'%PDF-fixture')

    def command(self, *flags):
        return ['--root', str(self.f.project), '--entrypoint', 'main.typ',
                '--manifest', str(self.f.manifest), *flags]

    def test_standard_and_gap_profiles_replay_only_selected_original(self):
        for flags, profile in (((), 'standard'), (('--conditional-gaps',), 'conditional-gaps')):
            with self.subTest(profile=profile):
                def compile_preview(context, records, catalog, snapshot, **options):
                    self.assertFalse(options['partial_unsupported'])
                    self.assertEqual(options['explicit_withheld_sources'], ('bad.typ',))
                    staged = document_resources.stage_document(context, records, catalog, snapshot,
                        profile=options['profile'], partial_unsupported=options['partial_unsupported'],
                        withheld_records=options['withheld_records'],
                        explicit_withheld_sources=options['explicit_withheld_sources'])
                    self.assertEqual(staged['project', 'bad.typ'], self.f.bad)
                    self.assertNotEqual(staged['project', 'good.typ'], self.f.good)
                    return self.fake
                with patch.object(cli.document_preview_compile, 'compile_preview', side_effect=compile_preview), \
                     contextlib.redirect_stdout(io.StringIO()) as output:
                    status = cli.main(self.command(*flags, '--withhold-source', 'bad.typ',
                                                   '--review-edits', '--format', 'json'))
                report = json.loads(output.getvalue())
                self.assertEqual(status, 2, report)
                self.assertEqual(report['sequence_profile'], profile)
                self.assertTrue(report['explicit_source_withholding'])
                self.assertNotIn('partial_unsupported_preview', report)
                self.assertEqual(report['withheld_sources'][0]['path'], 'bad.typ')
                self.assertEqual(report['withheld_sources'][0]['reason_code'], 'caller-excluded-source')
                self.assertEqual(next(row['status'] for row in report['source_review']['sources']
                                      if row['path'] == 'bad.typ'), 'withheld')
                self.assertIn('partial_preview_withheld_files', report['source_apply_gate']['blockers'])
                self.assertFalse(report['source_apply_gate']['eligible'] or report['publication_authority']
                                 or report['source_files_modified'] or report['global_coverage_complete'])

    def test_no_candidates_after_explicit_withholding_never_compile(self):
        flags = [item for value in ('main.typ', 'good.typ', 'bad.typ') for item in ('--withhold-source', value)]
        for profile in ((), ('--conditional-gaps',)):
            with self.subTest(profile=profile), \
                 patch.object(cli.document_preview_compile, 'compile_preview') as compiler, \
                 patch.object(cli.document_preview_discover, 'discover') as discover, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                status = cli.main(self.command(*profile, *flags, '--discover-dependencies', '--format', 'json'))
            report = json.loads(output.getvalue())
            self.assertEqual(status, 2, report)
            self.assertTrue(report['explicit_source_withholding'])
            self.assertFalse(report['pdf_generated'] or report['source_apply_gate']['eligible'])
            self.assertIn('no_verified_candidate_edits', report['source_apply_gate']['blockers'])
            self.assertEqual(len(report['withheld_sources']), 3)
            compiler.assert_not_called()
            discover.assert_not_called()
        with patch.object(cli.document_preview_compile, 'compile_preview') as compiler, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            status = cli.main(self.command(*flags))
        self.assertEqual(status, 2)
        self.assertIn('caller-excluded-source', output.getvalue())
        self.assertIn('bad.typ', output.getvalue())
        compiler.assert_not_called()

    def test_unselected_error_remains_fatal_with_or_without_a_selection(self):
        original = sequence.preview
        def refuse_other(tree, path, *args, **kwargs):
            if path == 'good.typ':
                return dict(path=path, verification='withheld', reason='unsupported other input')
            return original(tree, path, *args, **kwargs)
        for flags in ((), ('--withhold-source', 'bad.typ')):
            with self.subTest(flags=flags), patch.object(sequence, 'preview', side_effect=refuse_other), \
                 patch.object(cli.document_preview_compile, 'compile_preview') as compiler, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                status = cli.main(self.command(*flags, '--format', 'json'))
            report = json.loads(output.getvalue())
            self.assertEqual(status, 2, report)
            self.assertFalse(report['pdf_generated'])
            self.assertIn('unsupported other input', report['error'])
            compiler.assert_not_called()

    def test_replay_rejects_unselected_automatic_or_forged_withholding(self):
        context = include_context.load_include_context(self.f.project, (('main.typ', self.f.main),))
        snapshot = document_resources.capture(self.f.roots, [document_resources.ResourceRequest(*row) for row in self.f.rows])
        selected = sequence.explicit_withheld_record(context.source_for('bad.typ'))
        records = [row for item in context.sources if item.path != 'bad.typ'
                   if (row := sequence.preview(item.tree, item.path, self.f.catalog)) is not None]
        for withheld, paths in (([], ('bad.typ',)), ([dict(selected, baseline_sha256='0' * 64)], ('bad.typ',)),
                                ([selected, selected], ('bad.typ',)), ([selected], ()),
                                ([selected], ('../bad.typ',)), ([selected], ('bad.typ', 'bad.typ'))):
            with self.subTest(paths=paths), self.assertRaises((InputError, SafetyError)):
                document_resources.stage_document(context, records, self.f.catalog, snapshot,
                    withheld_records=withheld, explicit_withheld_sources=paths)
        forged = dict(path='good.typ', verification='withheld', reason_code='unsupported-deferred-footnote-binding')
        with self.assertRaises(SafetyError):
            document_resources.stage_document(context, [], self.f.catalog, snapshot,
                withheld_records=[selected, forged], explicit_withheld_sources=('bad.typ',))

    def test_text_output_names_the_withheld_source_and_no_spacer_requirement(self):
        with patch.object(cli.document_preview_compile, 'compile_preview', return_value=self.fake), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            status = cli.main(self.command('--withhold-source', 'bad.typ'))
        self.assertEqual(status, 2)
        self.assertIn('bad.typ', output.getvalue())
        self.assertIn('caller-excluded-source', output.getvalue())

    def test_pdf_stdout_keeps_explicit_report_on_stderr(self):
        out, err = io.StringIO(), io.StringIO()
        out.buffer = io.BytesIO()
        with patch.object(cli.document_preview_compile, 'compile_preview', return_value=self.fake), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cli.main(self.command('--withhold-source', 'bad.typ', '--pdf-stdout'))
        report = json.loads(err.getvalue())
        self.assertEqual(status, 2, report)
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(out.buffer.getvalue(), self.fake.candidate_pdf)
        self.assertTrue(report['explicit_source_withholding'])
        self.assertEqual(report['withheld_sources'][0]['path'], 'bad.typ')
        self.assertFalse(report['source_apply_gate']['eligible'] or report['publication_authority'])

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and sys.platform == 'darwin',
                         'Opt in on macOS with reviewed Typst')
    def test_actual_typst_compares_no_spacer_partial_profiles(self):
        for flags in ((), ('--conditional-gaps',)):
            with self.subTest(flags=flags), contextlib.redirect_stdout(io.StringIO()) as output:
                status = cli.main(self.command(*flags, '--withhold-source', 'bad.typ', '--format', 'json'))
            report = json.loads(output.getvalue())
            self.assertEqual(status, 2, report)
            self.assertTrue(report['pdf_generated'], report)
            self.assertTrue(report['pdfs_identical'], report)
            self.assertEqual(report['withheld_sources'][0]['path'], 'bad.typ')
            self.assertFalse(report['source_apply_gate']['eligible'] or report['publication_authority'])
            self.assertEqual((self.f.project / 'bad.typ').read_bytes(), self.f.bad)
            self.assertEqual((self.f.project / 'good.typ').read_bytes(), self.f.good)
