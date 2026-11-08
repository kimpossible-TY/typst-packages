"""Explicit source-binding partial preview remains original-source-only."""
import contextlib
import copy
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
import document_footnote_evidence
import document_cetz_draw_evidence
import document_preview_cli
import document_resources
import document_sequence_preview as sequence
import test_partial_document_preview as fixtures
import test_document_cetz_draw_evidence as cetz_fixtures
from syntax_client import EXECUTABLE, parse
from typst_lint import SafetyError


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class PartialUnprovenBindingTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PartialDocumentPreviewTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.wildcard = b'#import "@local/text-utils:0.1.2": *\n'
        self.note = b'$ x #dots_space #footnote[Note] $'

    def plan(self, body):
        self.f.bad = self.wildcard + body
        (self.f.project / 'bad.typ').write_bytes(self.f.bad)
        context, snapshot, evidence, _, _ = self.f.build()
        records, withheld = [], []
        for source in context.sources:
            result = sequence.preview(source.tree, source.path, self.f.catalog, profile=self.f.profile,
                footnote_evidence=evidence, classify_deferred=True, classify_unproven_binding=True)
            if result is not None:
                (withheld if result['verification'] == 'withheld' else records).append(result)
        return context, snapshot, evidence, records, withheld

    def stage(self, context, snapshot, evidence, records, withheld, **overrides):
        options = dict(profile=self.f.profile, footnote_evidence=evidence, partial_unsupported=True,
                       withheld_records=withheld, partial_unproven_binding=True)
        options.update(overrides)
        return document_resources.stage_document(context, records, self.f.catalog, snapshot, **options)

    def test_explicit_shadow_and_local_functions_withhold_whole_source(self):
        bodies = (b'#use(footnote => [' + self.note + b'])',
                  b'#use(x => [#let footnote = x\n' + self.note + b'])',
                  b'#let footnote = none\n#let f(x) = [' + self.note + b']',
                  b'#let f(x) = [#let local(y) = { other(y) }\n' + self.note + b']')
        for body in bodies:
            with self.subTest(body=body):
                context, snapshot, evidence, records, withheld = self.plan(body)
                self.assertEqual([row.get('reason_code') for row in withheld], ['unproven-source-footnote-binding'])
                original = context.source_for('bad.typ').source
                staged = self.stage(context, snapshot, evidence, records, withheld)
                self.assertEqual(staged['project', 'bad.typ'], original)
                self.assertNotIn(b'#dots_space', staged['project', 'good.typ'])
                self.assertEqual((self.f.project / 'bad.typ').read_bytes(), original)
                strict = sequence.preview(context.source_for('bad.typ').tree, 'bad.typ', self.f.catalog,
                    profile=self.f.profile, footnote_evidence=evidence, classify_deferred=True)
                self.assertEqual(strict['verification'], 'withheld')
                self.assertNotIn('reason_code', strict)

    def test_unknown_literal_or_dynamic_import_cannot_be_hidden(self):
        cases = (b'#import "@preview/unknown:1.0.0": thing as footnote\n#let f(x) = [' + self.note + b']',
                 b'#import "unknown.typ": missing\n#let f(x) = [' + self.note + b']',
                 b'#let f(x) = [#import ("unknown" + ".typ"): *\n' + self.note + b']')
        for body in cases:
            with self.subTest(body=body):
                _, _, _, _, withheld = self.plan(body)
                self.assertEqual(len(withheld), 1)
                self.assertNotIn('reason_code', withheld[0])

    def test_transitive_unknown_imports_cannot_be_hidden_by_named_import(self):
        self.f.rows.append(('project', 'other.typ', 'source'))
        cases = ((b'#import unknown: *\n#let unrelated = none\n', False),
                 (b'#import "@preview/unreviewed:1.0.0": unrelated\n', False),
                 (b'#import "missing.typ": unrelated\n', False),
                 (b'#import "@local/text-utils:0.1.2": highlighted\n#let unrelated = none\n', True))
        for other, accepted in cases:
            with self.subTest(other=other):
                (self.f.project / 'other.typ').write_bytes(other)
                context, snapshot, evidence, records, withheld = self.plan(
                    b'#import "other.typ": unrelated\n#use(footnote => [' + self.note + b'])')
                self.assertEqual(len(withheld), 1)
                if accepted:
                    self.assertEqual(withheld[0].get('reason_code'), 'unproven-source-footnote-binding')
                    staged = self.stage(context, snapshot, evidence, records, withheld)
                    self.assertEqual(staged['project', 'bad.typ'], self.f.bad)
                else:
                    self.assertNotIn('reason_code', withheld[0])
                    self.assertIn("source='other.typ'", withheld[0]['reason'])
                    self.assertIn('reason=', withheld[0]['reason'])
                    if b'@preview/unreviewed:1.0.0' in other:
                        self.assertIn("request='@preview/unreviewed:1.0.0'", withheld[0]['reason'])
                    elif b'unknown: *' in other:
                        self.assertIn('Dynamic or escaped import is unsupported', withheld[0]['reason'])
                    with self.assertRaises(SafetyError):
                        self.stage(context, snapshot, evidence, records, withheld)

    def test_reviewed_computed_import_is_checked_once_for_all_unproven_calls(self):
        fixture = cetz_fixtures.CetzDrawEvidenceTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        source = (b'#import "@local/text-utils:0.1.2": *\n#import "@preview/cetz:0.4.2"\n'
                  b'#let f(x) = [#{import cetz.draw: *}\n$ x #dots_space #footnote[One] $\n'
                  b'$ y #dots_space #footnote[Two] $]')
        proof = fixture.proof(source)
        real_check = document_cetz_draw_evidence.supports_imports
        with patch.object(document_cetz_draw_evidence, 'supports_imports', wraps=real_check) as check:
            result = sequence.preview(parse(source), 'main.typ', self.f.catalog, profile=self.f.profile,
                footnote_evidence=proof, classify_deferred=True, classify_unproven_binding=True)
        self.assertEqual(result.get('reason_code'), 'unproven-source-footnote-binding', result)
        self.assertEqual(check.call_count, 1)

        # A different computed namespace cannot be classified merely by shape.
        changed = source.replace(b'cetz.draw: *', b'other.draw: *')
        result = sequence.preview(parse(changed), 'main.typ', self.f.catalog, profile=self.f.profile,
            footnote_evidence=proof, classify_deferred=True, classify_unproven_binding=True)
        self.assertEqual(result['verification'], 'withheld')
        self.assertNotIn('reason_code', result)

    def test_transitive_import_failure_is_actionable_in_json_without_source_content(self):
        self.f.rows.append(('project', 'other.typ', 'source'))
        (self.f.project / 'other.typ').write_bytes(
            b'#import "@preview/unreviewed:1.0.0": unrelated\n#let secret = "private content"\n')
        self.plan(b'#import "other.typ": unrelated\n#use(footnote => [' + self.note + b'])')
        with patch.object(document_preview_cli.document_preview_compile, 'compile_preview') as compiler, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            status = document_preview_cli.main(self.f.command('--partial-unsupported-preview',
                '--partial-unproven-binding-preview', '--format', 'json'))
        result = json.loads(output.getvalue())
        self.assertEqual(status, 2)
        compiler.assert_not_called()
        self.assertFalse(result['pdf_generated'] or result['source_files_modified'])
        self.assertIn("source='other.typ'", result['error'])
        self.assertIn("request='@preview/unreviewed:1.0.0'", result['error'])
        self.assertNotIn('private content', result['error'])

    def test_integrity_or_later_reanalysis_failure_is_never_a_skip(self):
        context, _, evidence, _, _ = self.plan(b'#use(footnote => [' + self.note + b'])')
        item = context.source_for('bad.typ')
        for target, attribute, failure in ((document_footnote_evidence, '_verify_package', SafetyError('invalid package')),
                                           (sequence, 'analyze_all', SafetyError('reanalysis failed'))):
            with self.subTest(attribute=attribute), patch.object(target, attribute, side_effect=failure):
                result = sequence.preview(item.tree, item.path, self.f.catalog, profile=self.f.profile,
                    footnote_evidence=evidence, classify_deferred=True, classify_unproven_binding=True)
                self.assertEqual(result['verification'], 'withheld')
                self.assertNotIn('reason_code', result)

    def test_replay_recomputes_type_and_rejects_forgery_or_missing_optin(self):
        context, snapshot, evidence, records, withheld = self.plan(b'#use(footnote => [' + self.note + b'])')
        self.assertEqual(withheld[0]['reason_code'], 'unproven-source-footnote-binding')
        for replacement in ([], [dict(withheld[0], reason_code='unsupported-deferred-footnote-binding')],
                            [dict(withheld[0], baseline_sha256='0' * 64)], [*withheld, *withheld]):
            with self.subTest(replacement=replacement), self.assertRaises(SafetyError):
                self.stage(context, snapshot, evidence, records, replacement)
        altered = copy.deepcopy(records)
        altered[0]['candidate_sha256'] = '0' * 64
        with self.assertRaises(SafetyError):
            self.stage(context, snapshot, evidence, altered, withheld)
        with self.assertRaises(SafetyError):
            self.stage(context, snapshot, evidence, records, withheld, partial_unproven_binding=False)

    def test_cli_requires_second_optin_and_reports_binding_refusal(self):
        self.plan(b'#use(footnote => [' + self.note + b'])')
        fake = SimpleNamespace(baseline_sha256='a' * 64, candidate_sha256='b' * 64,
            pdfs_identical=False, dependency_count=1, unobserved_capture_count=0,
            system_font_files=0, compiler_sha256='c' * 64, candidate_pdf=b'%PDF-fixture')
        def compile_preview(context, records, catalog, snapshot, **options):
            staged = document_resources.stage_document(context, records, catalog, snapshot,
                profile=options['profile'], footnote_evidence=options['footnote_evidence'],
                partial_unsupported=options['partial_unsupported'], withheld_records=options['withheld_records'],
                partial_unproven_binding=options['partial_unproven_binding'])
            self.assertEqual(staged['project', 'bad.typ'], self.f.bad)
            return fake
        with patch.object(document_preview_cli.document_preview_compile, 'compile_preview', side_effect=compile_preview) as compiler, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            status = document_preview_cli.main(self.f.command('--partial-unsupported-preview', '--format', 'json'))
        self.assertEqual(status, 2)
        compiler.assert_not_called()
        with patch.object(document_preview_cli.document_preview_compile, 'compile_preview', side_effect=compile_preview), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            status = document_preview_cli.main(self.f.command('--partial-unsupported-preview',
                '--partial-unproven-binding-preview', '--review-edits', '--format', 'json'))
        result = json.loads(output.getvalue())
        self.assertEqual(status, 2, result)
        self.assertTrue(result['partial_unproven_binding_preview'])
        self.assertEqual(result['withheld_sources'][0]['reason_code'], 'unproven-source-footnote-binding')
        self.assertFalse(result['source_apply_gate']['eligible'] or result['source_files_modified']
                         or result['publication_authority'] or result['global_coverage_complete'])
        self.assertEqual(next(row['status'] for row in result['source_review']['sources']
                              if row['path'] == 'bad.typ'), 'withheld')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            document_preview_cli.main(self.f.command('--partial-unproven-binding-preview'))

    def test_no_candidate_reports_original_unproven_source_without_compile(self):
        self.plan(b'#use(footnote => [' + self.note + b'])')
        (self.f.project / 'good.typ').write_bytes(b'No candidate.\n')
        with patch.object(document_preview_cli.document_preview_compile, 'compile_preview') as compiler, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            status = document_preview_cli.main(self.f.command('--partial-unsupported-preview',
                '--partial-unproven-binding-preview', '--format', 'json'))
        result = json.loads(output.getvalue())
        self.assertEqual(status, 2)
        compiler.assert_not_called()
        self.assertFalse(result['pdf_generated'] or result['source_apply_gate']['eligible'])
        self.assertEqual(result['withheld_sources'][0]['reason_code'], 'unproven-source-footnote-binding')
        self.assertIn('no_verified_candidate_edits', result['source_apply_gate']['blockers'])

    def test_pdf_stream_does_not_mix_partial_report_into_stdout(self):
        self.plan(b'#use(footnote => [' + self.note + b'])')
        fake = SimpleNamespace(baseline_sha256='a' * 64, candidate_sha256='b' * 64,
            pdfs_identical=False, dependency_count=1, unobserved_capture_count=0,
            system_font_files=0, compiler_sha256='c' * 64, candidate_pdf=b'%PDF-fixture')
        stdout, stderr = io.StringIO(), io.StringIO()
        stdout.buffer = io.BytesIO()
        with patch.object(document_preview_cli.document_preview_compile, 'compile_preview', return_value=fake), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = document_preview_cli.main(self.f.command('--partial-unsupported-preview',
                '--partial-unproven-binding-preview', '--pdf-stdout'))
        self.assertEqual(status, 2)
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(stdout.buffer.getvalue(), b'%PDF-fixture')
        report = json.loads(stderr.getvalue())
        self.assertEqual(report['withheld_sources'][0]['reason_code'], 'unproven-source-footnote-binding')
        self.assertFalse(report['source_apply_gate']['eligible'])

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and sys.platform == 'darwin',
                         'Opt in on macOS with the reviewed Typst compiler')
    def test_real_preview_uses_original_shadowed_source(self):
        self.f.good = b'#let dots_space = h(1pt)\n' + self.f.good
        (self.f.project / 'good.typ').write_bytes(self.f.good)
        self.f.bad = (self.wildcard + b'#let dots_space = h(1pt)\n#let footnote = none\n'
                      b'#let f(x) = [#let local(y) = { y }\n' + self.note + b']\n')
        (self.f.project / 'bad.typ').write_bytes(self.f.bad)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            status = document_preview_cli.main(self.f.command('--partial-unsupported-preview',
                '--partial-unproven-binding-preview', '--format', 'json'))
        report = json.loads(output.getvalue())
        self.assertEqual(status, 2, report)
        self.assertTrue(report['pdf_generated'], report)
        self.assertEqual(report['withheld_sources'][0]['reason_code'], 'unproven-source-footnote-binding')
        self.assertFalse(report['source_apply_gate']['eligible'])
        self.assertEqual((self.f.project / 'bad.typ').read_bytes(), self.f.bad)
        self.assertEqual((self.f.project / 'good.typ').read_bytes(), self.f.good)


if __name__ == '__main__':
    unittest.main()
