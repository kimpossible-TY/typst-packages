"""Conditional local hints share the issued compile inputs, never fix authority."""
import contextlib
from dataclasses import replace
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
import document_conditional_hyphen_marker
import document_local_rules
import document_original_compile_cli as cli
import document_preview_compile
import document_resources
import include_context
from syntax_client import EXECUTABLE
from typst_lint import SafetyError
import test_document_preview_cli as fixtures


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class OriginalCompileLocalHintsTests(unittest.TestCase):
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

    def test_conditional_hints_match_existing_inspection_and_stay_separate(self):
        source = (b'// typst-lint: ignore-next pde.continuation-line -- fixture\n'
                  b'This statement holds.\nAnother statement follows.\n\n'
                  b'#block[Claim#footnote[Text]\n\nNext.\n]\n')
        (self.f.project / 'main.typ').write_bytes(source)
        context = include_context.load_include_context(self.f.project, (('main.typ', source),), budget='book-v1')
        requests = [document_resources.ResourceRequest(**row) for row in self.f.value['files']]
        snapshot = document_resources.capture(self.f.value['roots'], requests)
        actual = cli.lint_captured_document(context, snapshot, rule_set='pde', local_hints=True)
        expected = document_cli.report_document(self.f.project, 'main.typ', rule_set='pde', local_rule_hints=True)
        hints, old = actual['conditional_local_hints'], expected['local_rule_hints']
        self.assertEqual(hints['rule_ids'], old['rule_ids'])
        self.assertEqual(hints['diagnostics'], old['diagnostics'])
        self.assertEqual(hints['suppressed'], old['suppressed'])
        self.assertGreater(hints['diagnostics_count'], 0)
        self.assertGreater(hints['suppressed_count'], 0)
        old_rows = {(row['path'], row['rule_id']): row for row in old['coverage']}
        for row in hints['coverage']:
            original = old_rows[row['path'], row['rule_id']]
            self.assertEqual(row['diagnostics'], original['diagnostics'])
            self.assertEqual(row['suppressed'], original['suppressed'])
            self.assertEqual(row['skip_reasons'], original['reasons'][:20])
            self.assertEqual(row['parse_errors'], original['parse_errors'][:20])
            self.assertEqual(row['status'], 'conditional')
        self.assertFalse(hints['coverage_complete'] or hints['runtime_binding_proved']
                         or hints['execution_proved'] or hints['edit_permission'] or hints['compiler_permission'])
        self.assertTrue(set(hints['rule_ids']).issubset(set(actual['coverage']['uninspected'])))
        self.assertIn('primary backends', actual['coverage']['uninspected_scope'])
        self.assertEqual(actual['diagnostics'], expected['diagnostics'])
        self.assertEqual((self.f.project / 'main.typ').read_bytes(), source)

    def test_sample_and_omitted_counts_match_inspection(self):
        source = b'\n\n'.join([b'This statement holds.\nAnother statement follows.'] * 100) + b'\n'
        flags = ('--lint-document', '--rule-set', 'pde', '--lint-local-hints')
        status, _, report, _ = self.run_mock(source, *flags)
        self.assertEqual(status, 0, report)
        hints = report['lint']['conditional_local_hints']
        expected = document_cli.report_document(self.f.project, 'main.typ', rule_set='pde', local_rule_hints=True)
        all_rows = expected['local_rule_hints']['diagnostics']
        self.assertGreater(len(all_rows), 50)
        self.assertEqual(hints['diagnostics_count'], len(all_rows))
        self.assertEqual(hints['diagnostics'], all_rows[:50])
        self.assertEqual(hints['diagnostics_omitted'], len(all_rows) - 50)
        status, _, report, _ = self.run_mock(source, *flags, '--lint-details')
        self.assertEqual(status, 0, report)
        self.assertEqual(report['lint']['conditional_local_hints']['diagnostics'], all_rows)
        self.assertEqual(report['lint']['conditional_local_hints']['diagnostics_omitted'], 0)

    def test_hints_require_explicit_lint_and_pde_selection(self):
        out, err = io.StringIO(), io.StringIO()
        out.buffer = io.BytesIO()
        for options in (('--lint-local-hints',), ('--lint-document', '--lint-local-hints'),
                        ('--lint-document', '--rule-set', 'core', '--lint-local-hints')):
            with self.subTest(options=options), contextlib.redirect_stdout(out), \
                 contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                cli.main(self.command(*options))
        self.assertEqual(out.buffer.getvalue(), b'')

    def test_hint_failure_blocks_compile_and_discards_partial_results(self):
        with patch.object(document_local_rules, 'analyze', side_effect=SafetyError('conditional failure')):
            status, pdf, report, compiler = self.run_mock(b'Plain source.\n', '--lint-document',
                '--rule-set', 'pde', '--lint-local-hints')
        self.assertEqual(status, 2, report)
        self.assertEqual(pdf, b'')
        self.assertEqual(report['phase'], 'lint')
        self.assertFalse(report['lint_completed'] or report['global_coverage_complete'])
        self.assertNotIn('lint', report)
        compiler.assert_not_called()

    def test_conditional_reason_limits_preserve_omitted_counts(self):
        real = document_local_rules.analyze
        def many_reasons(tree, path, catalog):
            _, results = real(tree, path, catalog)
            rid = document_local_rules.RULE_IDS[0]
            results[rid] = replace(results[rid], skips=tuple(f'rule reason {i}' for i in range(25)))
            return tuple(f'shared reason {i}' for i in range(25)), results
        with patch.object(document_local_rules, 'analyze', side_effect=many_reasons):
            status, _, report, _ = self.run_mock(b'Plain source.\n', '--lint-document',
                '--rule-set', 'pde', '--lint-local-hints')
        self.assertEqual(status, 0, report)
        hints = report['lint']['conditional_local_hints']
        context = hints['source_contexts'][0]
        row = hints['coverage'][0]
        self.assertEqual((context['shared_reason_count'], len(context['shared_resolution_reasons']),
                          context['shared_reasons_omitted']), (25, 20, 5))
        self.assertEqual((row['skip_count'], len(row['skip_reasons']), row['skip_reasons_omitted']), (25, 20, 5))

    def test_conditional_section_is_included_in_byte_budget(self):
        source = b'This statement holds.\nAnother statement follows.\n'
        base = ('--lint-document', '--rule-set', 'pde')
        status, _, without, _ = self.run_mock(source, *base)
        self.assertEqual(status, 0, without)
        status, _, with_hints, _ = self.run_mock(source, *base, '--lint-local-hints')
        self.assertEqual(status, 0, with_hints)
        size = lambda value: len(json.dumps({'lint': value['lint']}, ensure_ascii=False,
                                           sort_keys=True, indent=2).encode('utf-8'))
        base_size, hint_size = size(without), size(with_hints)
        self.assertGreater(hint_size, base_size)
        with patch.object(cli, '_LINT_REPORT_BYTES', (base_size + hint_size) // 2):
            status, pdf, report, compiler = self.run_mock(source, *base, '--lint-local-hints')
        self.assertEqual(status, 2, report)
        self.assertEqual(pdf, b'')
        self.assertIn('byte limit', report['error'])
        compiler.assert_not_called()

    def test_hyphen_marker_hints_match_inspection_in_separate_section(self):
        source = b'This paragraph starts.\n#paragraph-tab\nNext paragraph.\n'
        flags = ('--lint-document', '--rule-set', 'pde', '--lint-local-hints')
        status, _, report, _ = self.run_mock(source, *flags)
        self.assertEqual(status, 0, report)
        hyphen = report['lint']['conditional_hyphen_markers']
        old = document_cli.report_document(self.f.project, 'main.typ', rule_set='pde',
                                           local_rule_hints=True)['conditional_hyphen_markers']['by_file']
        expected = sorted((row for item in old for row in item['diagnostics']),
                          key=lambda row: (row['path'], row['line'], row['column'], row['rule_id']))
        self.assertGreater(len(expected), 0)
        self.assertEqual(hyphen['diagnostics'], expected)
        self.assertEqual(hyphen['diagnostics_count'], len(expected))
        self.assertEqual(hyphen['unavailable_files'], 0)
        self.assertFalse(hyphen['coverage_complete'] or hyphen['runtime_binding_proved']
                         or hyphen['execution_proved'] or hyphen['edit_permission'] or hyphen['compiler_permission'])
        self.assertIn('conditional_local_hints', report['lint'])

    def test_hyphen_unavailable_is_reported_and_forged_capture_refuses(self):
        real = document_conditional_hyphen_marker.analyze
        def unavailable(source, path, **kwargs):
            row = real(source, path, **kwargs)
            return dict(row, status='UNAVAILABLE', diagnostics=[], suppressed=[], unresolved=['fixture unavailable'])
        flags = ('--lint-document', '--rule-set', 'pde', '--lint-local-hints')
        with patch.object(document_conditional_hyphen_marker, 'analyze', side_effect=unavailable):
            status, _, report, _ = self.run_mock(b'Plain source.\n', *flags)
        self.assertEqual(status, 0, report)
        hyphen = report['lint']['conditional_hyphen_markers']
        self.assertEqual(hyphen['unavailable_files'], 1)
        self.assertEqual(hyphen['by_file'][0]['unresolved'], ['fixture unavailable'])
        def forged(source, path, **kwargs):
            return dict(real(source, path, **kwargs), source_sha256='0' * 64)
        with patch.object(document_conditional_hyphen_marker, 'analyze', side_effect=forged):
            status, pdf, report, compiler = self.run_mock(b'Plain source.\n', *flags)
        self.assertEqual(status, 2, report)
        self.assertEqual(pdf, b'')
        self.assertFalse(report['lint_completed'])
        compiler.assert_not_called()

    def test_hyphen_sample_and_detail_omissions_match_inspection(self):
        source = b''.join(b'Text.\n#paragraph-tab\nNext.\n\n' for _ in range(70))
        flags = ('--lint-document', '--rule-set', 'pde', '--lint-local-hints')
        status, _, report, _ = self.run_mock(source, *flags)
        self.assertEqual(status, 0, report)
        hints = report['lint']['conditional_hyphen_markers']
        old = document_cli.report_document(self.f.project, 'main.typ', rule_set='pde',
                                           local_rule_hints=True)['conditional_hyphen_markers']['by_file']
        expected = sorted((row for item in old for row in item['diagnostics']),
                          key=lambda row: (row['path'], row['line'], row['column'], row['rule_id']))
        self.assertEqual(hints['diagnostics_count'], len(expected))
        self.assertEqual(hints['diagnostics'], expected[:50])
        self.assertEqual(hints['diagnostics_omitted'], len(expected) - 50)
        status, _, report, _ = self.run_mock(source, *flags, '--lint-details')
        self.assertEqual(status, 0, report)
        self.assertEqual(report['lint']['conditional_hyphen_markers']['diagnostics'], expected)
        self.assertEqual(report['lint']['conditional_hyphen_markers']['diagnostics_omitted'], 0)

    def test_hyphen_shadow_is_visible_as_unresolved(self):
        source = b'#let paragraph-tab = none\nText.\n#paragraph-tab\nNext.\n'
        status, _, report, _ = self.run_mock(source, '--lint-document', '--rule-set', 'pde', '--lint-local-hints')
        self.assertEqual(status, 0, report)
        row = report['lint']['conditional_hyphen_markers']['by_file'][0]
        self.assertEqual(row['diagnostics'], 0)
        self.assertTrue(any('shadow' in reason for reason in row['unresolved']))

    def test_hyphen_utf8_capitalization_and_forged_positions(self):
        source = '#block[한글.\n  #paragraph-tab\ntherefore it follows.]'.encode()
        flags = ('--lint-document', '--rule-set', 'pde', '--lint-local-hints')
        status, _, report, _ = self.run_mock(source, *flags)
        self.assertEqual(status, 0, report)
        rows = report['lint']['conditional_hyphen_markers']['diagnostics']
        self.assertEqual({row['rule_id'] for row in rows},
                         {'pde.paragraph-marker-gap', 'pde.paragraph-capitalization'})
        real = document_conditional_hyphen_marker.analyze
        for field, value in (('line', 9999), ('column', 9999), ('start', 0), ('end', 0),
                             ('marker_start', 0), ('grade', 'incorrect')):
            def forged(data, path, **kwargs):
                result = real(data, path, **kwargs)
                result['diagnostics'][0][field] = value
                return result
            with self.subTest(field=field), patch.object(document_conditional_hyphen_marker, 'analyze', side_effect=forged):
                status, pdf, report, compiler = self.run_mock(source, *flags)
            self.assertEqual(status, 2, report)
            self.assertEqual(pdf, b'')
            self.assertFalse(report['lint_completed'])
            compiler.assert_not_called()

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and sys.platform == 'darwin',
                         'Opt in on macOS with reviewed Typst')
    def test_real_compiler_remains_separate_from_conditional_hints(self):
        source = b'This statement holds.\nAnother statement follows.\n'
        (self.f.project / 'main.typ').write_bytes(source)
        command = [str(TOOL / 'typst-style'), 'compile', '--compile-original',
                   *self.command('--lint-document', '--rule-set', 'pde', '--lint-local-hints')]
        run = subprocess.run(command, capture_output=True, timeout=60)
        report = json.loads(run.stderr)
        self.assertEqual(run.returncode, 0, report)
        self.assertTrue(run.stdout.startswith(b'%PDF-'))
        hints = report['lint']['conditional_local_hints']
        self.assertGreater(hints['diagnostics_count'], 0)
        self.assertFalse(hints['coverage_complete'] or hints['runtime_binding_proved']
                         or hints['execution_proved'] or hints['edit_permission'])
        self.assertEqual((self.f.project / 'main.typ').read_bytes(), source)
