"""Partial preview retains unsupported sources and never grants write authority."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_footnote_binding
import document_footnote_evidence
import document_preview_cli
import document_resources
import document_sequence_preview
import include_context
import namespace_multiroot
from syntax_client import EXECUTABLE
from typst_lint import SafetyError, load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class PartialDocumentPreviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.project, self.packages, self.cache = (root / x for x in ('project', 'packages', 'cache'))
        for directory in (self.project, self.packages, self.cache):
            directory.mkdir()
        destination = self.packages / 'local/text-utils/0.1.2'
        destination.parent.mkdir(parents=True)
        shutil.copytree(TOOL / 'tests/fixtures/reviewed-packages/local/text-utils/0.1.2', destination)
        self.main = b'#include "good.typ"\n#include "bad.typ"\n'
        self.good = b'#block[$ x #dots_space #footnote[Good] $]\n'
        self.bad = (b'#import "@local/text-utils:0.1.2": *\n'
                    b'Thus $x^i$.\n#let f(x) = [$ x #dots_space #footnote[Note] $]\n')
        for name, content in (('main.typ', self.main), ('good.typ', self.good), ('bad.typ', self.bad)):
            (self.project / name).write_bytes(content)
        self.roots = dict(project=str(self.project), packages=str(self.packages), cache=str(self.cache))
        self.rows = [('project', name, 'source') for name in ('main.typ', 'good.typ', 'bad.typ')]
        self.rows += [('packages', 'local/text-utils/0.1.2/' + name, 'package')
                      for name in ('typst.toml', 'lib.typ')]
        self.manifest = root / 'manifest.json'
        self.manifest.write_text(json.dumps(dict(roots=self.roots,
            files=[dict(root=a, path=b, role=c) for a, b, c in self.rows])))
        self.catalog = load_rules()[0]['rules']
        self.profile = 'conditional-gaps-and-spacers'

    def build(self):
        context = include_context.load_include_context(self.project, (('main.typ', self.main),))
        namespaces = namespace_multiroot.load_multiroot_capture(self.packages, self.cache)
        session = conditional_footnote_binding.ConditionalFootnoteBinding(context, namespaces)
        snapshot = document_resources.capture(self.roots, [document_resources.ResourceRequest(*row) for row in self.rows])
        evidence = document_footnote_evidence.PreviewFootnoteEvidence(session, snapshot)
        records, withheld = [], []
        for source in context.sources:
            result = document_sequence_preview.preview(source.tree, source.path, self.catalog, profile=self.profile,
                footnote_evidence=evidence, classify_deferred=True)
            if result is not None:
                (withheld if result['verification'] == 'withheld' else records).append(result)
        return context, snapshot, evidence, records, withheld

    def command(self, *extra):
        return ['--root', str(self.project), '--entrypoint', 'main.typ', '--manifest', str(self.manifest),
                '--conditional-gaps', '--conditional-spacers', '--conditional-facade-footnotes', *extra]

    def test_replay_keeps_all_original_bytes_for_withheld_file(self):
        context, snapshot, evidence, records, withheld = self.build()
        self.assertEqual([row['path'] for row in withheld], ['bad.typ'])
        self.assertEqual(withheld[0]['reason_code'], 'unsupported-deferred-footnote-binding')
        staged = document_resources.stage_document(context, records, self.catalog, snapshot, profile=self.profile,
            footnote_evidence=evidence, partial_unsupported=True, withheld_records=withheld)
        self.assertEqual(staged['project', 'bad.typ'], self.bad)
        self.assertIn(b'$x^i$', staged['project', 'bad.typ'])  # Earlier script edit was discarded too.
        self.assertNotIn(b'#dots_space', staged['project', 'good.typ'])
        self.assertEqual((self.project / 'bad.typ').read_bytes(), self.bad)
        self.assertEqual((self.project / 'good.typ').read_bytes(), self.good)

    def test_omitted_forged_duplicate_and_unopted_records_are_refused(self):
        context, snapshot, evidence, records, withheld = self.build()
        for values in ([], [dict(withheld[0], baseline_sha256='0' * 64)],
                       [dict(withheld[0], reason_code='other')], [*withheld, *withheld]):
            with self.subTest(values=values), self.assertRaises(SafetyError):
                document_resources.stage_document(context, records, self.catalog, snapshot, profile=self.profile,
                    footnote_evidence=evidence, partial_unsupported=True, withheld_records=values)
        altered = copy.deepcopy(records)
        altered[0]['candidate_sha256'] = '0' * 64
        with self.assertRaises(SafetyError):
            document_resources.stage_document(context, altered, self.catalog, snapshot, profile=self.profile,
                footnote_evidence=evidence, partial_unsupported=True, withheld_records=withheld)
        with self.assertRaises(SafetyError):
            document_resources.stage_document(context, records, self.catalog, snapshot, profile=self.profile,
                footnote_evidence=evidence, withheld_records=withheld)

    def test_namespace_integrity_failure_and_unrecognized_withholding_do_not_skip(self):
        context, snapshot, evidence, records, withheld = self.build()
        with patch.object(document_footnote_evidence, '_verify_package', side_effect=SafetyError('broken evidence')):
            result = document_sequence_preview.preview(context.source_for('bad.typ').tree, 'bad.typ', self.catalog,
                profile=self.profile, footnote_evidence=evidence, classify_deferred=True)
        self.assertEqual(result['verification'], 'withheld')
        self.assertNotIn('reason_code', result)
        with self.assertRaises(SafetyError):
            document_resources.stage_document(context, records, self.catalog, snapshot, profile=self.profile,
                footnote_evidence=evidence, partial_unsupported=True, withheld_records=[result])
        # An unknown wildcard is separate from an unsupported closure.
        changed = b'#import "@preview/unknown:1.0.0": *\n' + self.bad
        (self.project / 'bad.typ').write_bytes(changed)
        context, _, evidence, _, withheld = self.build()
        self.assertEqual(len(withheld), 1)
        self.assertNotIn('reason_code', withheld[0])

    def test_explicit_shadow_and_assignment_are_not_partial_exclusions(self):
        wildcard = b'#import "@local/text-utils:0.1.2": *\n'
        note = b'$ x #dots_space #footnote[Note] $'
        scenarios = (b'#use(footnote => [' + note + b'])',
                     b'#use(x => [#let footnote = x\n' + note + b'])',
                     b'#use(x => [#{footnote = x}\n' + note + b'])',
                     b'#let footnote = none\n#let f(x) = [' + note + b']',
                     b'#let f(x) = [' + note + b']\n#let footnote = none\n#block[' + note + b']')
        for source in scenarios:
            with self.subTest(source=source):
                self.bad = wildcard + source
                (self.project / 'bad.typ').write_bytes(self.bad)
                _, _, _, _, withheld = self.build()
                self.assertEqual(len(withheld), 1)
                self.assertNotIn('reason_code', withheld[0])
                with patch.object(document_preview_cli.document_preview_compile, 'compile_preview') as compile_preview, \
                     contextlib.redirect_stdout(io.StringIO()) as output:
                    status = document_preview_cli.main(self.command('--partial-unsupported-preview', '--format', 'json'))
                self.assertEqual(status, 2, output.getvalue())
                compile_preview.assert_not_called()

    def test_nested_callbacks_keep_the_whole_unsupported_source_unchanged(self):
        self.bad = (b'#import "@local/text-utils:0.1.2": *\n'
                    b'Thus $x^i$.\n#let f(x) = [#g(y => [Text]) #g(z => [Text]) '
                    b'$ x #dots_space #footnote[Note] $]\n')
        (self.project / 'bad.typ').write_bytes(self.bad)
        context, snapshot, evidence, records, withheld = self.build()
        self.assertEqual([row['reason_code'] for row in withheld], ['unsupported-deferred-footnote-binding'])
        staged = document_resources.stage_document(context, records, self.catalog, snapshot, profile=self.profile,
            footnote_evidence=evidence, partial_unsupported=True, withheld_records=withheld)
        self.assertEqual(staged['project', 'bad.typ'], self.bad)
        self.assertIn(b'$x^i$', staged['project', 'bad.typ'])
        self.assertNotIn(b'#dots_space', staged['project', 'good.typ'])
        self.assertEqual((self.project / 'bad.typ').read_bytes(), self.bad)

    def test_nested_shadows_mutations_and_unsupported_target_remain_fatal(self):
        wildcard = b'#import "@local/text-utils:0.1.2": *\n'
        note = b'$ x #dots_space #footnote[Note] $'
        sources = (
            b'#let f(x) = [#g(footnote => [Text]) ' + note + b']',
            b'#let f(x) = [#g((y: none) => [Text]) ' + note + b']',
            b'#let f(x) = [#g((..y) => [Text]) ' + note + b']',
            b'#let f(x) = [#g(y => [#g(footnote => [Text])]) ' + note + b']',
            b'#let f(x) = [#g(y => [#let footnote = y]) ' + note + b']',
            b'#let f(x) = [#g(y => [#{footnote = y}]) ' + note + b']',
            b'#let f(x) = [#g(y => [#import "unknown.typ": *]) ' + note + b']',
            b'#let f(x) = [#g(y => [' + note + b'])]',
        )
        for source in sources:
            with self.subTest(source=source):
                self.bad = wildcard + source
                (self.project / 'bad.typ').write_bytes(self.bad)
                _, _, _, _, withheld = self.build()
                self.assertEqual(len(withheld), 1)
                self.assertNotIn('reason_code', withheld[0])
                with patch.object(document_preview_cli.document_preview_compile, 'compile_preview') as compile_preview, \
                     contextlib.redirect_stdout(io.StringIO()):
                    status = document_preview_cli.main(self.command('--partial-unsupported-preview', '--format', 'json'))
                self.assertEqual(status, 2)
                compile_preview.assert_not_called()

    def test_forged_candidate_evidence_is_not_partial_exclusion(self):
        context, _, evidence, _, _ = self.build()
        fake = SimpleNamespace(source_sha256='0' * 64, calls=(), unresolved=())
        with patch.object(document_footnote_evidence.PreviewFootnoteEvidence, 'candidate_calls', return_value=fake):
            record = document_sequence_preview.preview(context.source_for('bad.typ').tree, 'bad.typ', self.catalog,
                profile=self.profile, footnote_evidence=evidence, classify_deferred=True)
        self.assertEqual(record['verification'], 'withheld')
        self.assertNotIn('reason_code', record)
        self.assertIn('stale source hash', record['reason'])

    def test_cli_requires_opt_in_and_reports_partial_without_source_writes(self):
        calls = []
        def compile_fixture(context, records, catalog, snapshot, **kwargs):
            staged = document_resources.stage_document(context, records, catalog, snapshot,
                profile=kwargs['profile'], footnote_evidence=kwargs['footnote_evidence'],
                partial_unsupported=kwargs['partial_unsupported'], withheld_records=kwargs['withheld_records'])
            self.assertEqual(staged['project', 'bad.typ'], self.bad)
            self.assertNotIn(b'#dots_space', staged['project', 'good.typ'])
            calls.append(True)
            return SimpleNamespace(baseline_sha256='a' * 64, candidate_sha256='a' * 64,
                pdfs_identical=True, dependency_count=1, unobserved_capture_count=0,
                system_font_files=0, compiler_sha256='b' * 64, candidate_pdf=b'%PDF-fixture')
        output = io.StringIO()
        with patch.object(document_preview_cli.document_preview_compile, 'compile_preview', side_effect=compile_fixture), \
             contextlib.redirect_stdout(output):
            status = document_preview_cli.main(self.command('--format', 'json'))
        self.assertEqual(status, 2)
        self.assertFalse(calls)
        output = io.StringIO()
        with patch.object(document_preview_cli.document_preview_compile, 'compile_preview', side_effect=compile_fixture), \
             contextlib.redirect_stdout(output):
            status = document_preview_cli.main(self.command('--partial-unsupported-preview', '--review-edits', '--format', 'json'))
        report = json.loads(output.getvalue())
        self.assertEqual(status, 2)
        self.assertTrue(report['partial_unsupported_preview'])
        self.assertFalse(report['source_files_modified'] or report['publication_authority']
                         or report['global_coverage_complete'] or report['source_apply_gate']['eligible'])
        self.assertIn('partial_preview_withheld_files', report['source_apply_gate']['blockers'])
        self.assertEqual([row['path'] for row in report['withheld_sources']], ['bad.typ'])
        bad = next(row for row in report['source_review']['sources'] if row['path'] == 'bad.typ')
        self.assertEqual(bad['status'], 'withheld')
        self.assertEqual(bad['baseline_sha256'], bad['candidate_sha256'])
        self.assertTrue(calls)
        self.assertEqual((self.project / 'bad.typ').read_bytes(), self.bad)

    def test_cli_partial_text_and_pdf_stdout_remain_separate(self):
        fake = SimpleNamespace(baseline_sha256='a' * 64, candidate_sha256='b' * 64,
            pdfs_identical=False, dependency_count=1, unobserved_capture_count=0,
            system_font_files=0, compiler_sha256='c' * 64, candidate_pdf=b'%PDF-fixture')
        with patch.object(document_preview_cli.document_preview_compile, 'compile_preview', return_value=fake), \
             contextlib.redirect_stdout(io.StringIO()) as text:
            status = document_preview_cli.main(self.command('--partial-unsupported-preview'))
        self.assertEqual(status, 2)
        self.assertIn('bad.typ: pde.unaligned-footnote-no-spacer', text.getvalue())
        self.assertIn('global rule coverage remains incomplete', text.getvalue())
        stdout, stderr = io.StringIO(), io.StringIO()
        stdout.buffer = io.BytesIO()
        with patch.object(document_preview_cli.document_preview_compile, 'compile_preview', return_value=fake), \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = document_preview_cli.main(self.command('--partial-unsupported-preview', '--pdf-stdout'))
        self.assertEqual(status, 2)
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(stdout.buffer.getvalue(), b'%PDF-fixture')
        report = json.loads(stderr.getvalue())
        self.assertEqual([row['path'] for row in report['withheld_sources']], ['bad.typ'])
        self.assertIn('preview_pdfs_differ', report['source_apply_gate']['blockers'])

    def test_no_candidate_reports_withheld_sources_without_compiling(self):
        (self.project / 'good.typ').write_bytes(b'No candidate here.\n')
        output = io.StringIO()
        with patch.object(document_preview_cli.document_preview_compile, 'compile_preview') as compile_preview, \
             contextlib.redirect_stdout(output):
            status = document_preview_cli.main(self.command('--partial-unsupported-preview', '--format', 'json'))
        report = json.loads(output.getvalue())
        self.assertEqual(status, 2)
        compile_preview.assert_not_called()
        self.assertFalse(report['pdf_generated'] or report['source_apply_gate']['eligible'])
        self.assertEqual([row['path'] for row in report['withheld_sources']], ['bad.typ'])
        self.assertIn('no_verified_candidate_edits', report['source_apply_gate']['blockers'])

    def test_partial_option_requires_explicit_facade_evidence(self):
        args = self.command('--partial-unsupported-preview')
        args.remove('--conditional-facade-footnotes')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            document_preview_cli.main(args)
        self.assertEqual(raised.exception.code, 2)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and sys.platform == 'darwin',
                         'Opt in on macOS with the reviewed Typst compiler')
    def test_real_partial_preview_compiles_without_writing_sources(self):
        self.good = b'#let dots_space = h(1pt)\n' + self.good
        self.bad = self.bad.replace(b'Thus $x^i$.\n', b'#let dots_space = h(1pt)\nThus $x^i$.\n')
        (self.project / 'good.typ').write_bytes(self.good)
        (self.project / 'bad.typ').write_bytes(self.bad)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            status = document_preview_cli.main(self.command('--partial-unsupported-preview', '--format', 'json'))
        report = json.loads(output.getvalue())
        self.assertEqual(status, 2, report)
        self.assertTrue(report['pdf_generated'], report)
        self.assertEqual([row['path'] for row in report['withheld_sources']], ['bad.typ'])
        self.assertFalse(report['source_apply_gate']['eligible'])
        self.assertEqual((self.project / 'bad.typ').read_bytes(), self.bad)
        self.assertEqual((self.project / 'good.typ').read_bytes(), self.good)


if __name__ == '__main__':
    unittest.main()
