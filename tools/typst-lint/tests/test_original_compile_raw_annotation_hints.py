"""Conditional raw annotation reports in captured original compilation."""
import json
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
        with patch.object(cli, '_LINT_ITEM_LIMIT', 1):
            status, _, report, compiler = self.run_mock(source, *flags)
            self.assertEqual(status, 2, report)
            compiler.assert_not_called()
