"""Captured compilation reports bounded conditional scoped annotation hints."""
from unittest.mock import patch
import document_conditional_detached_annotations as hints
import document_original_compile_cli as cli
import test_original_compile_scoped_raw_hints as fixtures


class DetachedCompileTests(fixtures.OriginalCompileScopedRawHintsTests):
    def test_dynamic_callback_and_suppression_reuse_capture(self):
        source = (b'#proof[#local-scope-annotations(s => [$ x $ #(s.annot)(("x",), none, none)])]\n'
                  b'#proof[#local-scope-annotations(s => [\n'
                  b'// typst-lint: ignore-next core.annotation-in-display -- intentional\n'
                  b'#(s.annot)(("x",), none, none)\n])]')
        with patch.object(hints, 'parse', side_effect=AssertionError('unexpected parse')):
            status, pdf, report, _ = self.run_mock(source, '--lint-document', '--rule-set', 'pde',
                                                   '--lint-local-hints')
        self.assertEqual(status, 0, report)
        section = report['lint']['conditional_detached_annotations']
        self.assertEqual((section['diagnostics_count'], section['suppressed_count']), (1, 1))
        self.assertTrue(section['diagnostics'][0]['outer_dynamic_context'])
        self.assertFalse(section['coverage_complete'] or section['runtime_binding_proved']
                         or section['execution_proved'] or section['edit_permission'])
        self.assertIn(hints.RULE, report['lint']['coverage']['uninspected'])
        self.assertEqual((self.f.project / 'main.typ').read_bytes(), source)

    def test_forged_findings_and_suppression_are_rejected(self):
        source = b'#proof[#local-scope-annotations(s => [$ x $ #(s.annot)(("x",), none, none)])]'
        flags = ('--lint-document', '--rule-set', 'pde', '--lint-local-hints')
        original = hints._analyze_captured_tree
        for field, value in (('start', 0), ('line', 999), ('wrapper_start', 0),
                             ('outer_dynamic_context', False), ('explanation', 'proven'),
                             ('runtime_binding_proved', True), ('source_sha256', '0'*64), ('suppression', True)):
            def forged(tree, path, **kw):
                result = original(tree, path, **kw)
                if field == 'suppression': result['suppressed'].append(result['diagnostics'].pop())
                elif field == 'source_sha256': result[field] = value
                else: result['diagnostics'][0][field] = value
                return result
            with self.subTest(field=field), patch.object(hints, '_analyze_captured_tree', side_effect=forged):
                status, pdf, report, compiler = self.run_mock(source, *flags)
                self.assertEqual(status, 2, report); self.assertEqual(pdf, b''); compiler.assert_not_called()

    def test_forged_unsafe_argument_is_rejected(self):
        source = b'#local-scope-annotations(s => [#(s.annot)(mutate(s), none, none)])'
        sample = hints.analyze(b'#local-scope-annotations(s => [#(s.annot)(none, none, none)])',
                               'main.typ')['diagnostics'][0]
        original = hints._analyze_captured_tree
        def forged(tree, path, **kw):
            result = original(tree, path, **kw)
            start = tree.source.index(b'#(s.annot)')
            end = len(tree.source)-2
            result['diagnostics'] = [dict(sample, start=start, end=end, line=1, column=start+1)]
            return result
        with patch.object(hints, '_analyze_captured_tree', side_effect=forged):
            status, pdf, report, compiler = self.run_mock(source, '--lint-document', '--rule-set', 'pde',
                                                           '--lint-local-hints')
        self.assertEqual(status, 2, report); self.assertEqual(pdf, b''); compiler.assert_not_called()

    def test_sampling_and_work_budget(self):
        source = b'\n'.join([b'#proof[#local-scope-annotations(s => [#(s.annot)(("x",), none, none)])]'] * 55)
        flags = ('--lint-document', '--rule-set', 'pde', '--lint-local-hints')
        status, _, report, _ = self.run_mock(source, *flags)
        self.assertEqual(status, 0, report)
        section = report['lint']['conditional_detached_annotations']
        self.assertEqual((section['diagnostics_count'], len(section['diagnostics']),
                          section['diagnostics_omitted']), (55, 50, 5))
        with patch.object(cli, '_LOCAL_RAW_OWNER_WORK', 0):
            status, pdf, report, compiler = self.run_mock(source, *flags)
        self.assertEqual(status, 2, report); self.assertEqual(pdf, b''); compiler.assert_not_called()
