"""Conditional mannot-scope coverage never certifies runtime or writes."""
from pathlib import Path
import sys
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_conditional_mannot_scope as mannot
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ConditionalMannotScopeTests(unittest.TestCase):
    def run_source(self, source):
        result = mannot.analyze(source, 'fixture.typ')
        self.assertEqual(result['source_sha256'], digest(source))
        for key in ('runtime_binding_proved', 'execution_proved', 'coverage_complete',
                    'edit_permission', 'compiler_permission'):
            self.assertFalse(result[key], (key, result))
        self.assertTrue(result['conditions'])
        return result

    def test_display_annotation_is_counted_without_diagnostic(self):
        source = b'#mannot-scope(s => [$ x #annot(<x>, [note]) $])'
        result = self.run_source(source)
        self.assertEqual((result['wrappers_examined'], result['lexical_display_calls']), (1, 1))
        self.assertFalse(result['diagnostics'] or result['suppressed'] or result['unresolved'])

    def test_outside_display_is_conditional_and_offset_is_source_bound(self):
        source = '#mannot-scope(s => [한글 #annot(<x>, [note])])'.encode()
        result = self.run_source(source)
        finding, = result['diagnostics']
        self.assertEqual(finding['rule_id'], 'core.annotation-in-display')
        self.assertEqual(source[finding['start']:finding['start'] + len(b'#annot')], b'#annot')
        self.assertEqual(finding['line'], 1)
        self.assertFalse(finding['outer_dynamic_context'])

    def test_inline_math_is_not_a_display(self):
        result = self.run_source(b'#mannot-scope(s => [$x #annot(<x>, [note])$])')
        self.assertEqual(len(result['diagnostics']), 1)
        self.assertEqual(result['lexical_display_calls'], 0)

    def test_directive_is_preserved_as_suppressed(self):
        source = (b'#mannot-scope(s => [\n'
                  b'// typst-lint: ignore-next core.annotation-in-display -- intentional\n'
                  b'#annot(<x>, [note])\n])')
        result = self.run_source(source)
        self.assertFalse(result['diagnostics'])
        self.assertEqual(len(result['suppressed']), 1)

    def test_outer_deferred_wrapper_remains_explicitly_conditional(self):
        for source in (b'#let later() = [#mannot-scope(s => [#annot(<x>, [note])])]',
                       b'#let later = [#mannot-scope(s => [#annot(<x>, [note])])]',
                       b'#box[#mannot-scope(s => [#annot(<x>, [note])])]',
                       b'#show: body => [#mannot-scope(s => [#annot(<x>, [note])])]'):
            with self.subTest(source=source):
                result = self.run_source(source)
                finding, = result['diagnostics']
                self.assertTrue(finding['outer_dynamic_context'])
                self.assertEqual(result['wrappers_in_dynamic_context'], 1)
                self.assertTrue(any('execute' in condition for condition in result['conditions']))

    def test_nested_wrapper_annotation_is_not_counted_twice(self):
        result = self.run_source(b'#mannot-scope(s => [#mannot-scope(t => [$ x #annot(<x>, [note]) $])])')
        self.assertEqual(result['wrappers_examined'], 2)
        self.assertEqual(result['lexical_display_calls'], 1)
        self.assertFalse(result['diagnostics'])

    def test_unknown_callback_options_and_nested_deferred_are_unresolved(self):
        for source in (b'#mannot-scope(callback)',
                       b'#mannot-scope(s => [], namespace: "other")',
                       b'#mannot-scope(s => [#let later() = [#annot(<x>, [note])]])'):
            with self.subTest(source=source):
                result = self.run_source(source)
                self.assertFalse(result['diagnostics'] or result['lexical_display_calls'])
                self.assertTrue(result['unresolved'])

    def test_explicit_wrapper_or_annot_shadow_is_unresolved(self):
        call = b'#mannot-scope(s => [#annot(<x>, [note])])'
        for declaration in (b'#let mannot-scope = none\n', b'#let annot = none\n',
                            b'#import "other.typ": something as mannot-scope\n'):
            with self.subTest(declaration=declaration):
                result = self.run_source(declaration + call)
                self.assertFalse(result['diagnostics'] or result['lexical_display_calls'])
                self.assertTrue(result['unresolved'])

    def test_equation_constructor_is_not_guessed(self):
        result = self.run_source(b'#mannot-scope(s => [#math.equation([#annot(<x>, [note])], block: true)])')
        self.assertFalse(result['diagnostics'] or result['lexical_display_calls'])
        self.assertTrue(result['unresolved'])

    def test_bare_math_annot_is_unresolved_even_inside_deferred_content(self):
        for source in (b'#mannot-scope(s => [$ annot(x, y) $])',
                       b'#mannot-scope(s => [#let later() = [$ annot(x, y) $]])'):
            with self.subTest(source=source):
                result = self.run_source(source)
                self.assertFalse(result['diagnostics'] or result['lexical_display_calls'])
                self.assertTrue(result['unresolved'])

    def test_invalid_metadata_and_item_limit_refuse(self):
        with self.assertRaises(InputError):
            mannot.analyze(b'', 'fixture.typ', catalog=[])
        from unittest.mock import patch
        with patch.object(mannot, 'MAX_ITEMS', 1):
            with self.assertRaisesRegex(InputError, 'item limit'):
                self.run_source(b'#mannot-scope(s => [#annot(<x>, [note])])')


if __name__ == '__main__':
    unittest.main()
