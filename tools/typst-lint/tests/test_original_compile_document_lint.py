"""Opt-in lint reports only supported diagnostics from the compile snapshot."""
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
import document_original_compile_cli as cli
import document_preview_compile
import document_resources
import include_context
from syntax_client import EXECUTABLE
from typst_lint import Diagnostic, SafetyError
import test_document_preview_cli as fixtures


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class OriginalCompileDocumentLintTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DocumentPreviewCliTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.existing = self.f.project / 'existing.pdf'
        self.existing.write_bytes(b'existing PDF')
        self.fake = SimpleNamespace(pdf=b'%PDF-1.7\n%%EOF\n', pdf_sha256='a' * 64,
            dependency_count=1, unobserved_capture_count=0, system_font_files=0, compiler_sha256='b' * 64)

    def command(self, *extra):
        return ['--root', str(self.f.project), '--entrypoint', 'main.typ',
                '--manifest', str(self.f.manifest), '--pdf-stdout', *extra]

    def run_mock(self, source, *extra):
        (self.f.project / 'main.typ').write_bytes(source)
        out, err = io.StringIO(), io.StringIO()
        out.buffer = io.BytesIO()
        with patch.object(document_preview_compile, 'compile_original', return_value=self.fake) as compiler, \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cli.main(self.command(*extra))
        return status, out.buffer.getvalue(), json.loads(err.getvalue()), compiler

    def test_diagnostics_suppressions_and_skip_counts_match_document_inspection(self):
        source = (b'// typst-lint: ignore-next core.parenthesized-scripts -- fixture\n'
                  b'$ x^i $\n\n$ y^j $\n\n#include "child.typ"\n')
        child = b'#block[$ a^#(b+1) $]\n'
        (self.f.project / 'main.typ').write_bytes(source)
        (self.f.project / 'child.typ').write_bytes(child)
        self.f.value['files'].append({'root': 'project', 'path': 'child.typ', 'role': 'source'})
        self.f.manifest.write_text(json.dumps(self.f.value))
        context = include_context.load_include_context(self.f.project, (('main.typ', source),), budget='book-v1')
        requests = [document_resources.ResourceRequest(row['root'], row['path'], row['role'])
                    for row in self.f.value['files']]
        snapshot = document_resources.capture(self.f.value['roots'], requests)
        actual = cli.lint_captured_document(context, snapshot, rule_set='pde')
        expected = document_cli.report_document(self.f.project, 'main.typ', rule_set='pde')
        self.assertEqual(actual['diagnostics'], expected['diagnostics'])
        self.assertEqual(actual['suppressed'], expected['suppressed'])
        self.assertEqual(actual['suppressed_count'], expected['suppressed_count'])
        self.assertEqual(actual['coverage']['enabled'], expected['coverage']['enabled'])
        self.assertEqual(actual['coverage']['uninspected'], expected['coverage']['uninspected'])
        self.assertGreater(actual['suppressed_count'], 0)
        self.assertGreater(actual['coverage']['skips_by_rule']['core.parenthesized-scripts'], 0)
        expected_rows = {(row['path'], row['rule_id']): row for row in expected['coverage']['files']}
        for row in actual['coverage']['files']:
            other = expected_rows[row['path'], row['rule_id']]
            self.assertEqual(row['status'], 'partial')
            self.assertEqual(row['diagnostics'], other['diagnostics'])
            self.assertEqual(row['suppressed'], other['suppressed'])
            self.assertEqual(row['skip_reasons'], other['reasons'][:20])
        self.assertFalse(actual['coverage']['global_complete'] or actual['coverage']['fixes_applied'])

    def test_default_sample_and_detailed_limit_preserve_exact_totals(self):
        source = b'\n'.join([b'$ x^i $'] * 120) + b'\n'
        for details in (False, True):
            with self.subTest(details=details):
                flags = ['--lint-document', '--rule-set', 'core'] + (['--lint-details'] if details else [])
                status, pdf, report, _ = self.run_mock(source, *flags)
                self.assertEqual(status, 0, report)
                self.assertTrue(pdf.startswith(b'%PDF-'))
                lint = report['lint']
                self.assertEqual(lint['diagnostics_count'], 120)
                self.assertEqual(len(lint['diagnostics']), 120 if details else 50)
                self.assertEqual(lint['diagnostics_omitted'], 0 if details else 70)
                expected = document_cli.report_document(self.f.project, 'main.typ', rule_set='core')
                self.assertEqual(lint['diagnostics'], expected['diagnostics'][:len(lint['diagnostics'])])
                self.assertTrue(lint['diagnostics_present'])
                self.assertFalse(lint['coverage']['global_complete'])
                self.assertEqual(len(lint['coverage']['uninspected']), 14)

    def test_bounded_sample_orders_findings_and_preserves_ties(self):
        rows = [Diagnostic('core.parenthesized-scripts', 'error', path, line, 1, str(index), 'fixture')
                for index, (path, line) in enumerate((('z.typ', 2), ('a.typ', 2), ('a.typ', 1),
                                                       ('a.typ', 1), ('b.typ', 1), ('a.typ', 0)))]
        heap = []
        for index, row in enumerate(rows):
            cli._retain_sample(heap, row, index, 3)
        expected = sorted(rows, key=lambda row: (row.path, row.line, row.column, row.rule_id))[:3]
        self.assertEqual([row['explanation'] for row in cli._finish_sample(heap)],
                         [row.explanation for row in expected])

    def test_analyzer_failure_blocks_compile_and_pdf_stream(self):
        source = b'// typst-lint: ignore-next core.parenthesized-scripts\n$x^i$\n'
        status, pdf, report, compiler = self.run_mock(source, '--lint-document')
        self.assertEqual(status, 2, report)
        self.assertEqual(pdf, b'')
        self.assertEqual(report['phase'], 'lint')
        self.assertTrue(report['lint_attempted'])
        self.assertFalse(report['lint_completed'] or report['lint_performed'])
        self.assertFalse(report['global_coverage_complete'] or report['pdf_streamed'])
        compiler.assert_not_called()

    def test_unresolved_include_inventory_matches_document_inspection(self):
        source = b'#include selected\n$x^i$\n'
        (self.f.project / 'main.typ').write_bytes(source)
        context = include_context.load_include_context(self.f.project, (('main.typ', source),), budget='book-v1')
        requests = [document_resources.ResourceRequest(row['root'], row['path'], row['role'])
                    for row in self.f.value['files']]
        snapshot = document_resources.capture(self.f.value['roots'], requests)
        report = cli.lint_captured_document(context, snapshot, rule_set='pde')
        expected = document_cli.report_document(self.f.project, 'main.typ', rule_set='pde')
        normalized = lambda value: json.loads(json.dumps(value))
        self.assertEqual(normalized(report['coverage']['unresolved_includes']),
                         normalized(expected['include_context']['unresolved']))
        self.assertTrue(report['coverage']['unresolved_includes'])
        self.assertFalse(report['coverage']['global_complete'])

    def test_malformed_source_refuses_before_lint_and_pdf(self):
        status, pdf, report, compiler = self.run_mock(b'$x^(\n', '--lint-document')
        self.assertEqual(status, 2, report)
        self.assertEqual(pdf, b'')
        self.assertFalse(report['lint_attempted'] or report['lint_completed'] or report['pdf_streamed'])
        compiler.assert_not_called()

    def test_changed_snapshot_and_item_limit_block_before_compilation(self):
        source = b'$ x^i $\n'
        for target, failure in ((document_resources, SafetyError('capture changed')),):
            with patch.object(target, 'stage_original', side_effect=failure):
                status, pdf, report, compiler = self.run_mock(source, '--lint-document')
            self.assertEqual(status, 2, report)
            self.assertEqual(pdf, b'')
            compiler.assert_not_called()
        with patch.object(cli, '_LINT_ITEM_LIMIT', 1):
            status, pdf, report, compiler = self.run_mock(source, '--lint-document')
        self.assertEqual(status, 2, report)
        self.assertEqual(pdf, b'')
        self.assertIn('limit', report['error'])
        compiler.assert_not_called()

    def test_report_byte_budget_uses_real_indented_unicode_encoding(self):
        child = self.f.project / '한글.typ'
        child.write_bytes(b'$ y^j $\n')
        self.f.value['files'].append({'root': 'project', 'path': child.name, 'role': 'source'})
        self.f.manifest.write_text(json.dumps(self.f.value))
        for source in (b'\n'.join([b'$ x^i $'] * 20),
                       '#include "한글.typ"\n'.encode('utf-8') + b'\n'.join([b'$ x^i $'] * 20)):
            with self.subTest(unicode=b'include' in source):
                status, _, report, _ = self.run_mock(source, '--lint-document')
                self.assertEqual(status, 0, report)
                section = {'lint': report['lint']}
                actual = len(json.dumps(section, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8'))
                compact = len(json.dumps(section, ensure_ascii=True).encode('utf-8'))
                self.assertGreater(actual, compact)
                with patch.object(cli, '_LINT_REPORT_BYTES', actual - 200):
                    status, pdf, limited, compiler = self.run_mock(source, '--lint-document')
                self.assertEqual(status, 2, limited)
                self.assertEqual(pdf, b'')
                self.assertIn('byte limit', limited['error'])
                self.assertFalse(limited['lint_completed'])
                compiler.assert_not_called()

    def test_extra_options_require_explicit_document_lint(self):
        out, err = io.StringIO(), io.StringIO()
        out.buffer = io.BytesIO()
        for options in (('--rule-set', 'pde'), ('--lint-details',)):
            with self.subTest(options=options), contextlib.redirect_stdout(out), \
                 contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                cli.main(self.command(*options))
        self.assertEqual(out.buffer.getvalue(), b'')

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and sys.platform == 'darwin',
                         'Opt in on macOS with reviewed Typst')
    def test_actual_compile_reports_findings_without_modifying_pdf_or_source(self):
        source = b'Thus $ x^i $.\n'
        (self.f.project / 'main.typ').write_bytes(source)
        command = [str(TOOL / 'typst-style'), 'compile', '--compile-original', *self.command()]
        original = subprocess.run(command, capture_output=True, timeout=60)
        checked = subprocess.run([*command, '--lint-document', '--rule-set', 'pde'],
                                 capture_output=True, timeout=60)
        base, report = json.loads(original.stderr), json.loads(checked.stderr)
        self.assertEqual((original.returncode, checked.returncode), (0, 0), report)
        self.assertEqual(original.stdout, checked.stdout)
        self.assertTrue(report['lint_performed'])
        self.assertTrue(report['lint']['diagnostics_present'])
        self.assertFalse(report['global_coverage_complete'] or report['lint']['coverage']['global_complete'])
        self.assertFalse(base['lint_performed'])
        self.assertEqual((self.f.project / 'main.typ').read_bytes(), source)
        self.assertEqual(self.existing.read_bytes(), b'existing PDF')
