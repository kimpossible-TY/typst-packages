"""Direct callback annotations are conditional, diagnostic-only hints."""
from pathlib import Path
import sys
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_conditional_detached_annotations as check
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class DetachedAnnotationHintsTests(unittest.TestCase):
    def analyze(self, body, wrapper=b'local-scope-annotations'):
        return check.analyze(b'#' + wrapper + b'(s => [\n' + body + b'\n])', 'main.typ')

    def test_direct_annotation_after_display_is_conditional_and_never_an_edit(self):
        source = (b'#local-scope-annotations(s => [\n'
                  b'$ x #(s.tag)("x") $\n\n#(s.annot)(("x",), cetz, { draw })\n])')
        result = check.analyze(source, 'main.typ')
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual(result['source_sha256'], digest(source))
        finding, = result['diagnostics']
        self.assertEqual(finding['rule_id'], 'core.annotation-in-display')
        self.assertTrue(source[finding['start']:finding['end']].startswith(b'#(s.annot)('))
        self.assertTrue(result['conditions'])
        self.assertFalse(result['runtime_binding_proved'] or result['execution_proved']
                         or result['coverage_complete'] or result['edit_permission'] or result['compiler_permission'])
        self.assertNotIn('edits', result)

    def test_display_ancestor_and_nonmatching_calls_are_not_reported(self):
        for body in (b'$ x #(s.annot)(("x",), cetz, {draw}) $',
                     b'#(other.annot)(("x",), cetz, {draw})',
                     b'#(s.annot)("x")',
                     b'#annot-cetz("x", cetz, {draw})'):
            with self.subTest(body=body):
                self.assertFalse(self.analyze(body)['diagnostics'])

        outer_display = (b'$ #local-scope-annotations(s => '
                         b'[#(s.annot)(("x",), cetz, {draw})]) $')
        result = check.analyze(outer_display, 'main.typ')
        self.assertFalse(result['diagnostics'])
        self.assertTrue(result['unresolved'])

    def test_suppression_respected_with_unicode_and_crlf(self):
        source = ('#local-scope-annotations(s => [\r\n한글.\r\n'
                  '// typst-lint: ignore-next core.annotation-in-display -- intentional\r\n'
                  '#(s.annot)(("x",), cetz, {draw})\r\n])').encode()
        result = check.analyze(source, 'main.typ')
        self.assertFalse(result['diagnostics'])
        finding, = result['suppressed']
        self.assertEqual((finding['line'], finding['column']), (4, 1))

    def test_receiver_shadow_mutation_or_escape_before_call_are_unresolved(self):
        annotation = b'#(s.annot)(("x",), cetz, {draw})'
        for before in (b'#let s = (:)', b'#s.insert("annot", other)', b'#unknown(s)',
                       b'#{s = other}', b'#if true [x]'):
            with self.subTest(before=before):
                result = self.analyze(before + b'\n' + annotation)
                self.assertFalse(result['diagnostics'])
                self.assertTrue(result['unresolved'], result)

    def test_unproved_wrapper_or_callback_are_never_promoted(self):
        source = b'#local-scope-annotations(s => [#(s.annot)(("x",), cetz, {draw})])'
        for prefix in (b'#let local-scope-annotations = other\n',
                       b'#local-scope-annotations(s => [#(s.annot)(("x",), cetz, {draw})], prefix: dynamic)\n'):
            with self.subTest(prefix=prefix):
                result = check.analyze(prefix if b'prefix:' in prefix else prefix + source, 'main.typ')
                self.assertFalse(result['diagnostics'])
                self.assertTrue(result['unresolved'])
        deferred = b'#let run(local-scope-annotations) = [#local-scope-annotations(s => [#(s.annot)(("x",), cetz, {draw})])]'
        result = check.analyze(deferred, 'main.typ')
        self.assertFalse(result['diagnostics'])
        self.assertTrue(result['unresolved'])

    def test_module_and_named_import_aliases_cannot_shadow_wrapper(self):
        body = b'\n#local-scope-annotations(s => [#(s.annot)(("x",), cetz, {draw})])'
        for prefix in (b'#import "other.typ" as local-scope-annotations',
                       b'#import "local-scope-annotations.typ"',
                       b'#import "other.typ": other as local-scope-annotations',
                       b'#import "other.typ" as local-scope-annotations: *',
                       b'#import dynamic as local-scope-annotations'):
            with self.subTest(prefix=prefix):
                result = check.analyze(prefix + body, 'main.typ')
                self.assertFalse(result['diagnostics'], result)
                self.assertTrue(result['unresolved'])
        direct = check.analyze(b'#import "styles.typ": local-scope-annotations' + body, 'main.typ')
        self.assertEqual(len(direct['diagnostics']), 1, direct)

    def test_nonliteral_or_malformed_inputs_fail_closed(self):
        result = check.analyze(b'#local-scope-annotations(s => [', 'main.typ')
        self.assertEqual(result['status'], 'UNAVAILABLE')
        self.assertFalse(result['diagnostics'])
        with self.assertRaises(InputError):
            check.analyze('#not bytes', 'main.typ')
        with self.assertRaises(InputError):
            check.analyze(b'', 'main.typ', catalog=[])


if __name__ == '__main__':
    unittest.main()
