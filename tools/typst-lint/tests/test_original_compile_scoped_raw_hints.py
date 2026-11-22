"""Captured original lint includes bounded conditional local-scope raw hints."""
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
import document_cli
import document_conditional_local_raw_annot as local
import document_original_compile_cli as cli
import document_preview_compile
import document_resources
import include_context
from syntax_client import EXECUTABLE
import test_document_preview_cli as fixtures


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class OriginalCompileScopedRawHintsTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DocumentPreviewCliTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.fake = SimpleNamespace(pdf=b'%PDF-1.7\n%%EOF\n', pdf_sha256='a' * 64,
            dependency_count=1, unobserved_capture_count=0, system_font_files=0, compiler_sha256='b' * 64)

    def command(self, *extra):
        return ['--root', str(self.f.project), '--entrypoint', 'main.typ', '--manifest', str(self.f.manifest),
                '--pdf-stdout', *extra]

    def run_mock(self, source, *extra):
        (self.f.project / 'main.typ').write_bytes(source)
        out, err = io.StringIO(), io.StringIO()
        out.buffer = io.BytesIO()
        with patch.object(document_preview_compile, 'compile_original', return_value=self.fake) as compiler, \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cli.main(self.command(*extra))
        return status, out.buffer.getvalue(), json.loads(err.getvalue()), compiler

    def test_matches_document_inspection_counts_conditions_and_suppression(self):
        source = (b'#let later() = [#local-scope-annotations(s => [$ x #annot(<x>, [inside]) $\n'
                  b'#annot(<y>, [outside])])]\n'
                  b'#local-scope-annotations(s => [\n'
                  b'// typst-lint: ignore-next core.annotation-in-display -- intentional\n'
                  b'#annot(<z>, [note])\n])')
        (self.f.project / 'main.typ').write_bytes(source)
        context = include_context.load_include_context(self.f.project, (('main.typ', source),), budget='book-v1')
        requests = [document_resources.ResourceRequest(**row) for row in self.f.value['files']]
        snapshot = document_resources.capture(self.f.value['roots'], requests)
        result = cli.lint_captured_document(context, snapshot, rule_set='pde', local_hints=True)
        expected = document_cli.report_document(self.f.project, 'main.typ', rule_set='pde', local_rule_hints=True)
        actual = result['conditional_local_scope_raw_annotations']
        rows = expected['conditional_local_scope_raw_annotations']['by_file']
        expected_findings = sorted((finding for row in rows for finding in row['diagnostics']),
                                   key=lambda row: (row['path'], row['line'], row['column'], row['rule_id']))
        expected_suppressed = sorted((finding for row in rows for finding in row['suppressed']),
                                     key=lambda row: (row['path'], row['line'], row['column'], row['rule_id']))
        self.assertEqual(actual['diagnostics'], expected_findings)
        self.assertEqual(actual['suppressed'], expected_suppressed)
        self.assertEqual((actual['diagnostics_count'], actual['suppressed_count']), (1, 1))
        self.assertEqual((actual['wrappers_examined'], actual['wrappers_in_dynamic_context'],
                          actual['lexical_display_calls']), (2, 1, 1))
        self.assertEqual(actual['conditions'], list(local.CONDITIONS))
        self.assertFalse(actual['runtime_binding_proved'] or actual['execution_proved']
                         or actual['coverage_complete'] or actual['edit_permission'] or actual['compiler_permission'])
        self.assertIn(local.RULE, result['coverage']['uninspected'])
        self.assertEqual((self.f.project / 'main.typ').read_bytes(), source)

    def test_default_and_details_samples_preserve_totals(self):
        source = b'\n'.join([b'#local-scope-annotations(s => [#annot(<x>, [note])])'] * 65)
        flags = ('--lint-document', '--rule-set', 'pde', '--lint-local-hints')
        status, pdf, report, _ = self.run_mock(source, *flags)
        self.assertEqual(status, 0, report)
        self.assertTrue(pdf.startswith(b'%PDF-'))
        section = report['lint']['conditional_local_scope_raw_annotations']
        self.assertEqual((section['diagnostics_count'], section['diagnostics_omitted']), (65, 15))
        self.assertEqual(len(section['diagnostics']), 50)
        status, _, report, _ = self.run_mock(source, *flags, '--lint-details')
        self.assertEqual(status, 0, report)
        section = report['lint']['conditional_local_scope_raw_annotations']
        self.assertEqual((len(section['diagnostics']), section['diagnostics_omitted']), (65, 0))
        self.assertEqual((self.f.project / 'main.typ').read_bytes(), source)

    def test_no_local_option_does_not_run_new_analysis(self):
        with patch.object(local, 'analyze', side_effect=AssertionError('unexpected scoped analysis')):
            status, _, report, _ = self.run_mock(b'Plain source.', '--lint-document', '--rule-set', 'pde')
        self.assertEqual(status, 0, report)
        self.assertNotIn('conditional_local_scope_raw_annotations', report['lint'])

    def test_forged_source_metadata_or_positions_abort_before_compile(self):
        source = b'#local-scope-annotations(s => [#annot(<x>, [outside])])'
        flags = ('--lint-document', '--rule-set', 'pde', '--lint-local-hints')
        original = local.analyze
        for field, value in (('source_sha256', '0' * 64), ('line', 999), ('column', 999),
                             ('start', 0), ('end', 0), ('wrapper_start', 0),
                             ('outer_dynamic_context', True), ('grade', 'wrong'),
                             ('explanation', 'Runtime binding and execution have been proven.'),
                             ('runtime_binding_proved', True)):
            def forged(data, path, **kwargs):
                row = original(data, path, **kwargs)
                if field == 'source_sha256':
                    row[field] = value
                else:
                    row['diagnostics'][0][field] = value
                return row
            with self.subTest(field=field), patch.object(local, 'analyze', side_effect=forged):
                status, pdf, report, compiler = self.run_mock(source, *flags)
            self.assertEqual(status, 2, report)
            self.assertEqual(pdf, b'')
            self.assertFalse(report['lint_completed'])
            compiler.assert_not_called()

    def test_lexical_display_call_cannot_be_forged_as_outside(self):
        source = b'#local-scope-annotations(s => [$ x #annot(<x>, [inside]) $])'
        original = local.analyze
        def forged(data, path, **kwargs):
            row = original(data, path, **kwargs)
            sample = original(b'#local-scope-annotations(s => [#annot(<x>, [inside])])', path, **kwargs)['diagnostics'][0]
            start = data.index(b'#annot')
            sample.update(start=start, end=start + len(b'#annot(<x>, [inside])'),
                          line=1, column=start + 1, wrapper_start=1)
            row['diagnostics'] = [sample]
            return row
        with patch.object(local, 'analyze', side_effect=forged):
            status, pdf, report, compiler = self.run_mock(source, '--lint-document', '--rule-set', 'pde',
                                                           '--lint-local-hints')
        self.assertEqual(status, 2, report)
        self.assertEqual(pdf, b'')
        compiler.assert_not_called()

    def test_suppression_classification_and_wrapper_counts_cannot_be_forged(self):
        ordinary = b'#local-scope-annotations(s => [#annot(<x>, [outside])])'
        suppressed = (b'#local-scope-annotations(s => [\n'
                      b'// typst-lint: ignore-next core.annotation-in-display -- intentional\n'
                      b'#annot(<x>, [outside])\n])')
        flags = ('--lint-document', '--rule-set', 'pde', '--lint-local-hints')
        original = local.analyze
        def move_to_suppressed(data, path, **kwargs):
            row = original(data, path, **kwargs)
            row['suppressed'].append(row['diagnostics'].pop())
            return row
        def move_to_diagnostic(data, path, **kwargs):
            row = original(data, path, **kwargs)
            row['diagnostics'].append(row['suppressed'].pop())
            return row
        def remove_wrapper_count(data, path, **kwargs):
            row = original(data, path, **kwargs)
            row['wrappers_examined'] = 0
            return row
        def inflate_wrapper_count(data, path, **kwargs):
            row = original(data, path, **kwargs)
            row['wrappers_examined'] += 1
            return row
        def inflate_display_count(data, path, **kwargs):
            row = original(data, path, **kwargs)
            row['lexical_display_calls'] += 1
            return row
        for source, forged in ((ordinary, move_to_suppressed), (suppressed, move_to_diagnostic),
                               (ordinary, remove_wrapper_count), (ordinary, inflate_wrapper_count),
                               (ordinary, inflate_display_count)):
            with self.subTest(forgery=forged.__name__), patch.object(local, 'analyze', side_effect=forged):
                status, pdf, report, compiler = self.run_mock(source, *flags)
            self.assertEqual(status, 2, report)
            self.assertEqual(pdf, b'')
            self.assertFalse(report['lint_completed'])
            compiler.assert_not_called()

    def test_dynamic_wrapper_count_cannot_be_smaller_than_findings(self):
        source = b'#let later() = [#local-scope-annotations(s => [#annot(<x>, [outside])])]'
        original = local.analyze
        def forged(data, path, **kwargs):
            row = original(data, path, **kwargs)
            row['wrappers_in_dynamic_context'] = 0
            return row
        with patch.object(local, 'analyze', side_effect=forged):
            status, pdf, report, compiler = self.run_mock(source, '--lint-document', '--rule-set', 'pde',
                                                           '--lint-local-hints')
        self.assertEqual(status, 2, report)
        self.assertEqual(pdf, b'')
        compiler.assert_not_called()

    def test_work_budget_and_unavailable_source(self):
        source = b'#local-scope-annotations(s => [#annot(<x>, [outside])])'
        flags = ('--lint-document', '--rule-set', 'pde', '--lint-local-hints')
        with patch.object(cli, '_LOCAL_RAW_OWNER_WORK', 0):
            status, pdf, report, compiler = self.run_mock(source, *flags)
        self.assertEqual(status, 2, report)
        self.assertEqual(pdf, b'')
        compiler.assert_not_called()
        original = local.analyze
        def unavailable(data, path, **kwargs):
            row = original(data, path, **kwargs)
            return dict(row, status='UNAVAILABLE', diagnostics=[], suppressed=[], unresolved=['fixture unavailable'],
                        wrappers_examined=0, wrappers_in_dynamic_context=0, lexical_display_calls=0)
        with patch.object(local, 'analyze', side_effect=unavailable):
            status, _, report, _ = self.run_mock(source, *flags)
        self.assertEqual(status, 0, report)
        section = report['lint']['conditional_local_scope_raw_annotations']
        self.assertEqual(section['unavailable_files'], 1)
        self.assertEqual(section['by_file'][0]['unresolved'], ['fixture unavailable'])

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and sys.platform == 'darwin',
                         'Opt in on macOS with reviewed Typst')
    def test_real_compiler_keeps_conditional_hints_and_original_bytes(self):
        source = (b'#import "helpers.typ": *\n'
                  b'#local-scope-annotations(s => [#annot(<x>, [note])])')
        helpers = (b'#let annot(target, note) = note\n'
                   b'#let local-scope-annotations(f) = f((:))\n')
        (self.f.project / 'main.typ').write_bytes(source)
        (self.f.project / 'helpers.typ').write_bytes(helpers)
        self.f.value['files'].append(dict(root='project', path='helpers.typ', role='resource'))
        self.f.manifest.write_text(json.dumps(self.f.value), encoding='utf-8')
        command = [str(TOOL / 'typst-style'), 'compile', '--compile-original',
                   *self.command('--lint-document', '--rule-set', 'pde', '--lint-local-hints')]
        run = subprocess.run(command, capture_output=True, timeout=60)
        report = json.loads(run.stderr)
        self.assertEqual(run.returncode, 0, report)
        self.assertTrue(run.stdout.startswith(b'%PDF-'))
        section = report['lint']['conditional_local_scope_raw_annotations']
        self.assertEqual(section['diagnostics_count'], 1)
        self.assertFalse(section['coverage_complete'] or section['runtime_binding_proved']
                         or section['execution_proved'] or section['edit_permission'])
        self.assertEqual((self.f.project / 'main.typ').read_bytes(), source)
