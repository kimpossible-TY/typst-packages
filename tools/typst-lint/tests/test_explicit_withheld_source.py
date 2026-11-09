"""Caller-selected files are withheld without claiming rule inspection."""
import contextlib
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_preview_cli
import document_resources
import document_sequence_preview as sequence
from syntax_client import EXECUTABLE
from typst_lint import InputError, SafetyError
import test_partial_document_preview as fixtures


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ExplicitWithheldSourceTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PartialDocumentPreviewTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.context, self.snapshot, self.evidence, self.records, _ = self.f.build()
        self.record = sequence.explicit_withheld_record(self.context.source_for('bad.typ'))

    def stage(self, withheld=None, selected=('bad.typ',), records=None):
        return document_resources.stage_document(self.context, self.records if records is None else records,
            self.f.catalog, self.snapshot, profile=self.f.profile, footnote_evidence=self.evidence,
            partial_unsupported=True, withheld_records=[self.record] if withheld is None else withheld,
            explicit_withheld_sources=selected)

    def test_explicit_source_keeps_original_but_other_candidates_are_replayed(self):
        actual = sequence.preview
        def assert_not_excluded(tree, path, *args, **kwargs):
            self.assertNotEqual(path, 'bad.typ')
            return actual(tree, path, *args, **kwargs)
        with patch.object(sequence, 'preview', side_effect=assert_not_excluded):
            staged = self.stage()
        self.assertEqual(staged['project', 'bad.typ'], self.f.bad)
        self.assertNotIn(b'#dots_space', staged['project', 'good.typ'])
        self.assertEqual((self.f.project / 'bad.typ').read_bytes(), self.f.bad)

    def test_forged_missing_duplicated_or_overlapping_selections_fail(self):
        for withheld, selected, records in (
                ([], ('bad.typ',), None),
                ([dict(self.record, baseline_sha256='0' * 64)], ('bad.typ',), None),
                ([dict(self.record, reason_code='other')], ('bad.typ',), None),
                ([self.record, self.record], ('bad.typ',), None),
                ([self.record], (), None),
                ([self.record], ('bad.typ', 'bad.typ'), None),
                ([self.record], ('missing.typ',), None),
                ([self.record], ('../bad.typ',), None),
                ([self.record], ('bad.typ',), self.records + [dict(path='bad.typ')])):
            with self.subTest(selected=selected, withheld=withheld), self.assertRaises((SafetyError, InputError)):
                self.stage(withheld, selected, records)

    def test_invalid_other_file_and_changed_snapshot_are_not_hidden(self):
        actual = sequence.preview
        def fail_other(tree, path, *args, **kwargs):
            if path == 'good.typ':
                raise SafetyError('unrelated failure')
            return actual(tree, path, *args, **kwargs)
        with patch.object(sequence, 'preview', side_effect=fail_other), self.assertRaises(SafetyError):
            self.stage()
        object.__setattr__(self.snapshot.files[0], 'data', b'changed')
        with self.assertRaises(SafetyError):
            self.stage()

    def test_cli_reports_all_rules_uninspected_for_explicit_selection(self):
        fake = SimpleNamespace(baseline_sha256='a' * 64, candidate_sha256='b' * 64,
            pdfs_identical=False, dependency_count=1, unobserved_capture_count=0,
            system_font_files=0, compiler_sha256='c' * 64, candidate_pdf=b'%PDF-fixture')
        actual = sequence.preview
        def assert_not_excluded(tree, path, *args, **kwargs):
            self.assertNotEqual(path, 'bad.typ')
            return actual(tree, path, *args, **kwargs)
        def compile_preview(context, records, catalog, snapshot, **options):
            staged = document_resources.stage_document(context, records, catalog, snapshot,
                profile=options['profile'], footnote_evidence=options['footnote_evidence'],
                partial_unsupported=options['partial_unsupported'], withheld_records=options['withheld_records'],
                partial_unproven_binding=options['partial_unproven_binding'],
                explicit_withheld_sources=options['explicit_withheld_sources'])
            self.assertEqual(staged['project', 'bad.typ'], self.f.bad)
            return fake
        with patch.object(sequence, 'preview', side_effect=assert_not_excluded), \
             patch.object(document_preview_cli.document_preview_compile, 'compile_preview', side_effect=compile_preview), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            status = document_preview_cli.main(self.f.command('--partial-unsupported-preview',
                '--withhold-source', 'bad.typ', '--format', 'json'))
        report = json.loads(output.getvalue())
        self.assertEqual(status, 2, report)
        row = report['withheld_sources'][0]
        self.assertEqual(row['reason_code'], 'caller-excluded-source')
        self.assertEqual(set(row['uninspected_rule_ids']), {rule['id'] for rule in self.f.catalog})
        self.assertFalse(report['source_apply_gate']['eligible'] or report['source_files_modified']
                         or report['publication_authority'] or report['global_coverage_complete'])

    def test_preexisting_withheld_reasons_do_not_claim_complete_inspection(self):
        previous = dict(self.record, reason_code='unsupported-deferred-footnote-binding',
                        rule_id='pde.unaligned-footnote-no-spacer')
        report = document_preview_cli.describe_withheld([previous], self.f.catalog)
        self.assertNotIn('uninspected_rule_ids', report[0])

    def test_cli_rejects_unknown_duplicate_or_noncanonical_paths(self):
        for values in (('missing.typ',), ('bad.typ', 'bad.typ'), ('../bad.typ',), ('./bad.typ',)):
            flags = [item for value in values for item in ('--withhold-source', value)]
            with self.subTest(values=values), contextlib.redirect_stdout(io.StringIO()) as output:
                status = document_preview_cli.main(self.f.command('--partial-unsupported-preview', *flags, '--format', 'json'))
            self.assertEqual(status, 2)
            self.assertFalse(json.loads(output.getvalue())['pdf_generated'])

    def test_all_sources_withheld_stops_before_compilation(self):
        flags = [item for value in ('main.typ', 'good.typ', 'bad.typ') for item in ('--withhold-source', value)]
        with patch.object(document_preview_cli.document_preview_compile, 'compile_preview') as compile_preview, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            status = document_preview_cli.main(self.f.command('--partial-unsupported-preview', *flags, '--format', 'json'))
        report = json.loads(output.getvalue())
        self.assertEqual(status, 2, report)
        self.assertFalse(report['pdf_generated'])
        self.assertEqual(len(report['withheld_sources']), 3)
        self.assertIn('no_verified_candidate_edits', report['source_apply_gate']['blockers'])
        compile_preview.assert_not_called()

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and sys.platform == 'darwin',
                         'Opt in on macOS with the reviewed Typst compiler')
    def test_actual_pdf_uses_skipped_source_and_other_candidate(self):
        good = b'#let dots_space = h(1pt)\n' + self.f.good
        bad = (b'#let dots_space = h(1pt)\n#let footnote = none\n'
               b'#let f(x) = [$ x #dots_space #footnote[Note] $]\n')
        (self.f.project / 'good.typ').write_bytes(good)
        (self.f.project / 'bad.typ').write_bytes(bad)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            status = document_preview_cli.main(self.f.command('--partial-unsupported-preview',
                '--withhold-source', 'bad.typ', '--review-edits', '--format', 'json'))
        report = json.loads(output.getvalue())
        self.assertEqual(status, 2, report)
        self.assertTrue(report['pdf_generated'], report)
        self.assertEqual(report['withheld_sources'][0]['reason_code'], 'caller-excluded-source')
        self.assertGreater(report['planned_edits'], 0)
        self.assertFalse(report['source_apply_gate']['eligible'])
        self.assertEqual((self.f.project / 'bad.typ').read_bytes(), bad)
        self.assertEqual((self.f.project / 'good.typ').read_bytes(), good)
