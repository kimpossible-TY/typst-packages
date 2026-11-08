"""Conditional raw overlay hints on the captured original compile path."""
from unittest.mock import patch
import document_conditional_raw_overlay as overlay
import document_original_compile_cli as cli
from syntax_client import parse
import test_original_compile_scoped_raw_hints as fixtures


class RawOverlayCompileTests(fixtures.OriginalCompileScopedRawHintsTests):
    def test_captured_tree_and_suppression(self):
        source = (b'#annot-cetz((<x>,), cetz, [draw])\n'
                  b'// typst-lint: ignore-next core.annotation-in-display -- intentional\n'
                  b'#annot-cetz((<y>,), cetz, [draw])')
        self.assertEqual(overlay.analyze(source, 'main.typ'),
                         overlay._analyze_captured_tree(parse(source), 'main.typ'))
        with patch.object(overlay, 'parse', side_effect=AssertionError('unexpected reparse')):
            status, pdf, report, _ = self.run_mock(source, '--lint-document', '--rule-set', 'pde',
                                                   '--lint-local-hints')
        self.assertEqual(status, 0, report)
        lint = report['lint']; section = lint['conditional_raw_overlays']
        self.assertEqual((section['diagnostics_count'], section['suppressed_count']), (1, 1))
        self.assertIn(overlay.RULE, lint['coverage']['uninspected'])
        self.assertFalse(section['coverage_complete'] or section['execution_proved']
                         or section['runtime_binding_proved'] or section['edit_permission'])
        self.assertEqual((self.f.project / 'main.typ').read_bytes(), source)

    def test_display_constructor_is_unresolved(self):
        for constructor in (b'equation', b'math.equation', b'(equation)'):
            source = b'#' + constructor + b'([#annot-cetz((<x>,), cetz, [draw])])'
            with self.subTest(constructor=constructor):
                result = overlay.analyze(source, 'main.typ')
                self.assertEqual(result['diagnostics'], [])
                self.assertEqual(len(result['unresolved']), 1)
                status, _, report, _ = self.run_mock(source, '--lint-document', '--rule-set', 'pde',
                                                     '--lint-local-hints')
                self.assertEqual(status, 0, report)
                self.assertEqual(report['lint']['conditional_raw_overlays']['diagnostics_count'], 0)

    def test_sample_and_unknown_bounds(self):
        source = b'\n'.join([b'#annot-cetz((<x>,), cetz, [draw])'] * 55)
        status, _, report, _ = self.run_mock(source, '--lint-document', '--rule-set', 'pde',
                                             '--lint-local-hints')
        self.assertEqual(status, 0, report)
        section = report['lint']['conditional_raw_overlays']
        self.assertEqual((section['diagnostics_count'], len(section['diagnostics']),
                          section['diagnostics_omitted']), (55, 50, 5))

    def test_forged_display_constructor_finding_is_rejected(self):
        source = b'#equation([#annot-cetz((<x>,), cetz, [draw])])'
        original = overlay._analyze_captured_tree
        sample = overlay.analyze(b'#annot-cetz((<x>,), cetz, [draw])', 'main.typ')['diagnostics'][0]
        def forged(tree, path, **kw):
            result = original(tree, path, **kw)
            start = tree.source.index(b'#annot-cetz')
            finding = dict(sample, start=start, end=start + len(b'#annot-cetz((<x>,), cetz, [draw])'),
                           line=1, column=start + 1)
            result['diagnostics'] = [finding]
            return result
        with patch.object(overlay, '_analyze_captured_tree', side_effect=forged):
            status, pdf, report, compiler = self.run_mock(source, '--lint-document', '--rule-set', 'pde',
                                                           '--lint-local-hints')
        self.assertEqual(status, 2, report)
        self.assertEqual(pdf, b'')
        compiler.assert_not_called()

    def test_forgery_and_budgets_abort_before_compiler(self):
        source = b'#annot-cetz((<x>,), cetz, [draw])'
        flags = ('--lint-document', '--rule-set', 'pde', '--lint-local-hints')
        original = overlay._analyze_captured_tree
        for field, value in (('start', 1), ('line', 99), ('explanation', 'proven'),
                             ('runtime_binding_proved', True), ('suppression', True), ('source_sha256', '0'*64)):
            def forged(tree, path, **kw):
                row = original(tree, path, **kw)
                if field == 'suppression': row['suppressed'].append(row['diagnostics'].pop())
                elif field == 'source_sha256': row[field] = value
                else: row['diagnostics'][0][field] = value
                return row
            with self.subTest(field=field), patch.object(overlay, '_analyze_captured_tree', side_effect=forged):
                status, pdf, report, compiler = self.run_mock(source, *flags)
                self.assertEqual(status, 2, report)
                self.assertEqual(pdf, b'')
                compiler.assert_not_called()
        with patch.object(cli, '_LINT_REPORT_BYTES', 100):
            status, pdf, report, compiler = self.run_mock(source, *flags)
            self.assertEqual(status, 2, report)
            self.assertEqual(pdf, b'')
            compiler.assert_not_called()
