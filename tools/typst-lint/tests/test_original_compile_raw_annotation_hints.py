"""Conditional raw annotation reports in captured original compilation."""
import json
import os
import subprocess
import sys
from unittest.mock import patch
import unittest

import document_conditional_raw_annot as direct
import document_conditional_mannot_scope as scoped
import document_original_compile_cli as cli
import test_original_compile_scoped_raw_hints as fixtures


class RawAnnotationCompileTests(fixtures.OriginalCompileScopedRawHintsTests):
    def test_separate_sections_suppression_and_tree_reuse(self):
        source = (b'#annot(<a>, [outside])\n'
                  b'// typst-lint: ignore-next core.annotation-in-display -- intentional\n'
                  b'#annot(<b>, [suppressed])\n'
                  b'#mannot-scope(s => [$ x #annot(<c>, [inside]) $ #annot(<d>, [outside])])\n'
                  b'#local-scope-annotations(s => [#annot(<e>, [outside])])')
        flags = ('--lint-document', '--rule-set', 'pde', '--lint-local-hints')
        with patch.object(direct, 'parse', side_effect=AssertionError('direct reparsed')), \
             patch.object(scoped, 'parse', side_effect=AssertionError('scoped reparsed')):
            status, pdf, report, _ = self.run_mock(source, *flags)
        self.assertEqual(status, 0, report)
        lint = report['lint']
        first = lint['conditional_raw_annotations']
        second = lint['conditional_mannot_scope_raw_annotations']
        third = lint['conditional_local_scope_raw_annotations']
        self.assertEqual((first['diagnostics_count'], first['suppressed_count']), (1, 1))
        self.assertEqual(first['lexical_display_calls'], 1)
        self.assertEqual(first['by_file'][0]['lexical_display_calls'], 1)
        self.assertEqual((second['diagnostics_count'], second['lexical_display_calls']), (1, 1))
        self.assertEqual(third['diagnostics_count'], 1)
        self.assertEqual(len({x['diagnostics'][0]['start'] for x in (first, second, third)}), 3)
        self.assertIn(direct.RULE, lint['coverage']['uninspected'])
        self.assertTrue(all(not x['coverage_complete'] and not x['runtime_binding_proved'] for x in (first, second)))
        self.assertEqual((self.f.project / 'main.typ').read_bytes(), source)

    def test_captured_tree_analysis_matches_public_source_api(self):
        from syntax_client import parse
        samples = (b'#annot(<a>, [note])',
                   b'#mannot-scope(s => [$ x #annot(<x>, [inside]) $ #annot(<y>, [outside])])',
                   b'#let annot = 1\n#annot(<a>, [note])', b'#let bad = [')
        for module in (direct, scoped):
            for source in samples:
                with self.subTest(module=module.__name__, source=source):
                    self.assertEqual(module._analyze_captured_tree(parse(source), 'main.typ'),
                                     module.analyze(source, 'main.typ'))

    def test_scoped_suppression_is_preserved_and_cannot_be_forged(self):
        source = (b'#mannot-scope(s => [\n'
                  b'// typst-lint: ignore-next core.annotation-in-display -- intentional\n'
                  b'#annot(<x>, [note])\n])')
        flags = ('--lint-document', '--rule-set', 'pde', '--lint-local-hints')
        status, _, report, _ = self.run_mock(source, *flags)
        self.assertEqual(status, 0, report)
        self.assertEqual(report['lint']['conditional_mannot_scope_raw_annotations']['suppressed_count'], 1)
        original = scoped._analyze_captured_tree
        def forged(tree, path, **kw):
            value = original(tree, path, **kw)
            value['diagnostics'].append(value['suppressed'].pop())
            return value
        with patch.object(scoped, '_analyze_captured_tree', side_effect=forged):
            status, pdf, report, compiler = self.run_mock(source, *flags)
        self.assertEqual(status, 2, report)
        self.assertEqual(pdf, b'')
        compiler.assert_not_called()

    def test_sample_and_per_file_unresolved_limits(self):
        source = b'\n'.join([b'#annot(<x>, [outside])'] * 65 + [b'#other(annot(<x>, [unsupported]))'] * 25)
        status, _, report, _ = self.run_mock(source, '--lint-document', '--rule-set', 'pde', '--lint-local-hints')
        self.assertEqual(status, 0, report)
        section = report['lint']['conditional_raw_annotations']
        self.assertEqual((section['diagnostics_count'], len(section['diagnostics']), section['diagnostics_omitted']),
                         (65, 50, 15))
        self.assertEqual((section['by_file'][0]['unresolved_count'], len(section['by_file'][0]['unresolved']),
                          section['by_file'][0]['unresolved_omitted']), (25, 20, 5))

    def test_forged_findings_and_suppression_are_rejected(self):
        flags = ('--lint-document', '--rule-set', 'pde', '--lint-local-hints')
        for module, source in ((direct, b'#annot(<x>, [outside])'),
                               (scoped, b'#mannot-scope(s => [#annot(<x>, [outside])])')):
            original = module._analyze_captured_tree
            for field, val in (('runtime_binding_proved', True), ('explanation', 'proven'),
                               ('start', 1), ('line', 999), ('suppression', True), ('source_sha256', '0'*64)):
                def forged(tree, path, **kw):
                    result = original(tree, path, **kw)
                    if field == 'source_sha256': result[field] = val
                    elif field == 'suppression': result['suppressed'].append(result['diagnostics'].pop())
                    else: result['diagnostics'][0][field] = val
                    return result
                with self.subTest(module=module.__name__, field=field), \
                     patch.object(module, '_analyze_captured_tree', side_effect=forged):
                    status, pdf, report, compiler = self.run_mock(source, *flags)
                    self.assertEqual(status, 2, report)
                    self.assertEqual(pdf, b'')
                    compiler.assert_not_called()

    def test_unavailable_and_report_byte_limit(self):
        source = b'#annot(<x>, [outside])'
        flags = ('--lint-document', '--rule-set', 'pde', '--lint-local-hints')
        original = direct._analyze_captured_tree
        def unavailable(tree, path, **kw):
            value = original(tree, path, **kw)
            return dict(value, status='UNAVAILABLE', diagnostics=[], suppressed=[], unresolved=['fixture unavailable'])
        with patch.object(direct, '_analyze_captured_tree', side_effect=unavailable):
            status, _, report, _ = self.run_mock(source, *flags)
        self.assertEqual(status, 0, report)
        self.assertEqual(report['lint']['conditional_raw_annotations']['unavailable_files'], 1)
        self.assertEqual(report['lint']['conditional_raw_annotations']['by_file'][0]['unresolved'],
                         ['fixture unavailable'])
        with patch.object(cli, '_LINT_REPORT_BYTES', 100):
            status, pdf, report, compiler = self.run_mock(source, *flags)
        self.assertEqual(status, 2, report)
        self.assertEqual(pdf, b'')
        compiler.assert_not_called()

    def test_wrapper_counts_and_item_budget_are_enforced(self):
        source = b'#mannot-scope(s => [#annot(<x>, [outside])])'
        flags = ('--lint-document', '--rule-set', 'pde', '--lint-local-hints')
        original = scoped._analyze_captured_tree
        def forged(tree, path, **kw):
            result = original(tree, path, **kw)
            result['wrappers_examined'] = 0
            return result
        with patch.object(scoped, '_analyze_captured_tree', side_effect=forged):
            status, _, report, compiler = self.run_mock(source, *flags)
            self.assertEqual(status, 2, report)
            compiler.assert_not_called()

    def test_forged_display_counts_are_rejected_before_compile(self):
        source = b'$ x #annot(<x>, [inside]) $'
        flags = ('--lint-document', '--rule-set', 'pde', '--lint-local-hints')
        status, _, report, _ = self.run_mock(source, *flags)
        self.assertEqual(status, 0, report)
        self.assertEqual(report['lint']['conditional_raw_annotations']['lexical_display_calls'], 1)
        original = direct._analyze_captured_tree
        for count in (0, 2, -1, True):
            def forged(tree, path, **kw):
                value = original(tree, path, **kw)
                value['lexical_display_calls'] = count
                return value
            with self.subTest(count=count), patch.object(direct, '_analyze_captured_tree', side_effect=forged):
                status, pdf, report, compiler = self.run_mock(source, *flags)
                self.assertEqual(status, 2, report)
                self.assertEqual(pdf, b'')
                compiler.assert_not_called()
        with patch.object(cli, '_LINT_ITEM_LIMIT', 1):
            status, _, report, compiler = self.run_mock(source, *flags)
            self.assertEqual(status, 2, report)
            compiler.assert_not_called()
        with patch.object(cli, '_LINT_REPORT_BYTES', 100):
            status, _, report, compiler = self.run_mock(source, *flags)
            self.assertEqual(status, 2, report)
            compiler.assert_not_called()

    def test_display_ownership_work_is_bounded(self):
        from syntax_client import parse
        from typst_lint import InputError
        tree = parse(b'$ x #annot(<x>, [inside]) $')
        with self.assertRaisesRegex(InputError, 'ownership work limit'):
            cli._direct_raw_display_count(tree, max_work=len(tree.nodes) - 1)
        self.assertEqual(cli._direct_raw_display_count(tree, max_work=len(tree.nodes)), (1, len(tree.nodes)))

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and sys.platform == 'darwin',
                         'Opt in on macOS with reviewed Typst')
    def test_real_compiler_reports_lexical_display_and_outside_separately(self):
        source = (b'#import "helpers.typ": *\n'
                  b'$ x #annot(<x>, [inside]) $\n#annot(<y>, [outside])')
        (self.f.project / 'main.typ').write_bytes(source)
        (self.f.project / 'helpers.typ').write_bytes(b'#let annot(target, note) = note\n')
        self.f.value['files'].append(dict(root='project', path='helpers.typ', role='resource'))
        self.f.manifest.write_text(json.dumps(self.f.value), encoding='utf-8')
        command = [str(fixtures.TOOL / 'typst-style'), 'compile', '--compile-original',
                   *self.command('--lint-document', '--rule-set', 'pde', '--lint-local-hints')]
        run = subprocess.run(command, capture_output=True, timeout=60)
        report = json.loads(run.stderr)
        self.assertEqual(run.returncode, 0, report)
        self.assertTrue(run.stdout.startswith(b'%PDF-'))
        section = report['lint']['conditional_raw_annotations']
        self.assertEqual((section['lexical_display_calls'], section['diagnostics_count']), (1, 1))
        self.assertFalse(section['execution_proved'] or section['coverage_complete'])
        self.assertEqual((self.f.project / 'main.typ').read_bytes(), source)
