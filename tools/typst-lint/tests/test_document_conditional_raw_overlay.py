"""Detached raw overlay hints remain source-only and explicitly conditional."""
from pathlib import Path
import sys
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_conditional_raw_overlay as check
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalRawOverlayTests(unittest.TestCase):
    call = b'#annot-cetz((<x>,), cetz, {draw})'

    def test_direct_nested_raw_overlay_outside_display(self):
        source = b'#import "styles.typ": *\n#proof[$ x $\n' + self.call + b'\n]'
        result = check.analyze(source, 'main.typ')
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual(result['source_sha256'], digest(source))
        finding, = result['diagnostics']
        self.assertEqual(finding['rule_id'], 'core.annotation-in-display')
        self.assertEqual(source[finding['start']:finding['end']], self.call)
        self.assertTrue(result['conditions'])
        self.assertFalse(result['runtime_binding_proved'] or result['execution_proved']
                         or result['coverage_complete'] or result['edit_permission'] or result['compiler_permission'])
        self.assertNotIn('edits', result)

    def test_display_deferred_and_dynamic_contexts_are_not_reported(self):
        cases = (b'$ x ' + self.call + b' $',
                 b'#let f(x) = [' + self.call + b']',
                 b'#for x in (1,) [' + self.call + b']',
                 b'#if true [' + self.call + b']',
                 b'#context [' + self.call + b']',
                 b'#let x = [' + self.call + b']')
        for source in cases:
            with self.subTest(source=source):
                result = check.analyze(source, 'main.typ')
                self.assertFalse(result['diagnostics'])
                self.assertTrue(result['unresolved'])

    def test_explicit_shadows_and_module_imports_are_never_promoted(self):
        cases = (b'#let annot-cetz = none', b'#let annot-cetz(a, b, c) = none',
                 b'#let f(annot-cetz) = []', b'#{annot-cetz = none}',
                 b'#for annot-cetz in (none,) []', b'#import "annot-cetz.typ"',
                 b'#import "@local/annot-cetz:0.1.0"', b'#import "other.typ" as annot-cetz',
                 b'#import "other.typ": other as annot-cetz', b'#import "other.typ": annot-cetz',
                 b'#import "other.typ" as tools: other as annot-cetz',
                 b'#import "other.typ" as tools: annot-cetz',
                 b'#import "other.typ" as annot-cetz: other')
        for prefix in cases:
            with self.subTest(prefix=prefix):
                result = check.analyze(prefix + b'\n#proof[' + self.call + b']', 'main.typ')
                self.assertFalse(result['diagnostics'])
                self.assertTrue(result['unresolved'])

    def test_unrelated_import_and_literal_content_do_not_create_shadows(self):
        cases = (b'#import "annot-cetz.typ" as other\n', b'#import "other.typ": helper\n',
                 b'#import "other.typ" as tools: annot-cetz as other\n',
                 b'#import "other.typ" as tools: *\n',
                 b'#let f = "annot-cetz"\n')
        for prefix in cases:
            with self.subTest(prefix=prefix):
                result = check.analyze(prefix + b'#proof[' + self.call + b']', 'main.typ')
                self.assertEqual(len(result['diagnostics']), 1, result['unresolved'])

    def test_suppression_and_unicode_crlf_positions(self):
        source = ('#proof[한글\r\n'
                  '// typst-lint: ignore-next core.annotation-in-display -- intentional\r\n'
                  '#annot-cetz((<x>,), cetz, {draw})\r\n]').encode()
        result = check.analyze(source, 'main.typ')
        self.assertFalse(result['diagnostics'])
        finding, = result['suppressed']
        self.assertEqual((finding['line'], finding['column']), (3, 1))

    def test_strings_unsupported_arguments_and_malformed_source(self):
        for source in (b'#str("#annot-cetz(x, y, z)")', b'`#annot-cetz(x, y, z)`',
                       b'#proof[#annot-cetz(x, y)]', b'#proof[#annot-cetz(x, y, z, ..args)]'):
            with self.subTest(source=source):
                self.assertFalse(check.analyze(source, 'main.typ')['diagnostics'])
        result = check.analyze(b'#proof[#annot-cetz(', 'main.typ')
        self.assertEqual(result['status'], 'UNAVAILABLE')
        self.assertFalse(result['diagnostics'])
        with self.assertRaises(InputError):
            check.analyze('not bytes', 'main.typ')
        with self.assertRaises(InputError):
            check.analyze(b'', 'main.typ', catalog=[])


if __name__ == '__main__':
    unittest.main()
