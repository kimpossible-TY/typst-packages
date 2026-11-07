"""Source-only nested display gaps over actual pinned parser output."""
from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import document_display
from document_display import RULE_ID, analyze
from suppression import SuppressionError
from syntax_client import EXECUTABLE, parse
from typst_lint import load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class DocumentDisplayTests(unittest.TestCase):
    def check(self, source):
        raw = source.encode() if isinstance(source, str) else source
        tree = parse(raw)
        result = analyze(tree, 'chapter.typ')
        self.assertEqual(result.edits, (), 'Source observations never authorize edits')
        self.assertEqual(tree.source, raw)
        self.assertEqual(result, analyze(tree, 'chapter.typ'))
        return result

    def test_root_multiline_display_and_plain_prose(self):
        result = self.check('$\n A=B\n$\n\nHence it follows.\n')
        finding, = result.diagnostics
        rule = next(rule for rule in load_rules()[0]['rules'] if rule['id'] == RULE_ID)
        self.assertEqual((finding.rule_id, finding.grade, finding.source),
                         (RULE_ID, rule['grade'], rule['source']))
        self.assertEqual((finding.path, finding.line, finding.column), ('chapter.typ', 1, 1))
        self.assertFalse(result.skips)

    def test_nested_callback_with_later_inline_calls_needs_no_package_resolution(self):
        source = ('#import "styles.typ": *\n#local-scope-annotations(s => [\n'
                  ' $\n A=B\n $\n\n Can we #(s.ref)("target") use it?\n])')
        finding, = self.check(source).diagnostics
        self.assertEqual((finding.line, finding.column), (3, 2))

    def test_literal_stored_or_context_content_is_source_style(self):
        for source in ('#let saved = [\n$ A=B $\n\nHence.\n]',
                       '#let f() = [\n$ A=B $\n\nHence.\n]',
                       '#context [\n$ A=B $\n\nHence.\n]',
                       '#unknown[\n$ A=B $\n\nHence.\n]'):
            with self.subTest(source=source):
                self.assertEqual(len(self.check(source).diagnostics), 1)

    def test_immediate_next_line_and_inline_math_are_accepted(self):
        for source in ('$ A=B $\nHence.', '$ A=B $\r\n  Hence.',
                       '$A=B$\n\nHence.', '$ A=B$\n\nHence.', '$A=B $\n\nHence.'):
            with self.subTest(source=source):
                result = self.check(source)
                self.assertFalse(result.diagnostics)
                self.assertFalse(result.skips)

    def test_single_same_line_label_is_preserved(self):
        for source in ('$ A=B $ <energy>\n\nHence.',
                       '$ A=B $\t<energy> \t\r\n\r\nHence.'):
            with self.subTest(source=source):
                self.assertEqual(len(self.check(source).diagnostics), 1)

    def test_labels_across_newline_and_multiple_labels_are_unresolved(self):
        for source in ('$ A=B $\n<energy>\n\nHence.',
                       '$ A=B $ <one> <two>\n\nHence.'):
            with self.subTest(source=source):
                result = self.check(source)
                self.assertFalse(result.diagnostics)
                self.assertTrue(result.skips)

    def test_bom_unicode_and_crlf_locations(self):
        result = self.check('\ufeff$ α=β $ <에너지>\r\n \t\r\n  따라서 성립한다.\r\n')
        finding, = result.diagnostics
        self.assertEqual((finding.line, finding.column), (1, 2))
        self.assertFalse(result.skips)

    def test_nonstandalone_equation_never_requests_only_a_gap(self):
        for source in ('Thus $ A=B $\n\nHence.',
                       '#unknown[$ A=B $\n\nHence.\n]',
                       '$ A=B $ trailing\n\nHence.'):
            with self.subTest(source=source):
                result = self.check(source)
                self.assertFalse(result.diagnostics)

    def test_comments_markers_wrappers_and_dynamic_labels_are_not_crossed(self):
        for suffix in ('// comment\n\nHence.', '/* comment */\n\nHence.',
                       '#paragraph_tab\nHence.', '#paragraph-tab\nHence.',
                       '#parbreak()\nHence.', '#text[Hence.]', '*Hence.*',
                       '_Hence._', '@energy Hence.', '$x$ Hence.',
                       '#(s.tag)("label")\n\nHence.'):
            with self.subTest(suffix=suffix):
                result = self.check('$ A=B $\n\n' + suffix)
                self.assertFalse(result.diagnostics)
                self.assertTrue(result.skips)

    def test_structural_siblings_are_not_prose(self):
        for suffix in ('= Heading', '- Item', '+ Item', '/ Term: Meaning',
                       '`raw`', '\\\nText.', '$ C=D $'):
            with self.subTest(suffix=suffix):
                self.assertFalse(self.check('$ A=B $\n\n' + suffix).diagnostics)

    def test_other_markup_bodies_never_supply_the_next_prose(self):
        for source in ('#unknown[\n$ A=B $\n]\n\nHence.',
                       '#unknown[\n$ A=B $\n]\n#unknown[\nHence.\n]'):
            with self.subTest(source=source):
                self.assertFalse(self.check(source).diagnostics)

    def test_code_list_heading_and_math_ancestors_are_excluded(self):
        for source in ('#let f() = { [\n$ A=B $\n\nHence.\n] }',
                       '- $ A=B $\n\n  Hence.',
                       '= $ A=B $\n\nHence.',
                       '$ x + #box[\n$ A=B $\n\nHence.\n] $',
                       '$ x #footnote[\n$ A=B $\n\nHence.\n] $',
                       '$ x #[\n$ A=B $\n\nHence.\n] $'):
            with self.subTest(source=source):
                result = self.check(source)
                self.assertFalse(result.diagnostics)
                self.assertTrue(result.skips)
        result = analyze(parse(b'[\n$ A=B $\n\nHence.\n]', 'code'), 'code.typ')
        self.assertFalse(result.diagnostics)
        self.assertTrue(result.skips)

    def test_raw_strings_and_comments_are_data(self):
        source = '#let value = "$ A=B $\\n\\nHence."\n// $ A=B $\n`$ A=B $`'
        result = self.check(source)
        self.assertFalse(result.diagnostics)
        self.assertFalse(result.skips)

    def test_equation_and_enclosing_call_suppressions_are_honored(self):
        for source in ('// typst-lint: ignore-next ' + RULE_ID + ' -- intentional\n'
                       '$ A=B $\n\nHence.',
                       '// typst-lint: ignore-next ' + RULE_ID + ' -- intentional\n'
                       '#unknown[\n$ A=B $\n\nHence.\n]'):
            with self.subTest(source=source):
                result = self.check(source)
                self.assertFalse(result.diagnostics)
                self.assertEqual(len(result.suppressed), 1)

    def test_following_directive_does_not_acquire_equation_ownership(self):
        result = self.check('$ A=B $\n\n// typst-lint: ignore-next ' + RULE_ID
                            + ' -- following prose\nHence.')
        self.assertFalse(result.diagnostics)
        self.assertFalse(result.suppressed)
        self.assertTrue(result.skips)

    def test_suppression_does_not_leak_to_a_sibling_body(self):
        source = ('#unknown[\n// typst-lint: ignore-next ' + RULE_ID + ' -- here\n'
                  '$ A=B $\n\nHence.\n]\n#unknown[\n$ C=D $\n\nTherefore.\n]')
        result = self.check(source)
        self.assertEqual(len(result.diagnostics), 1)
        self.assertEqual(len(result.suppressed), 1)

    def test_invalid_directive_and_parse_errors_are_not_clean_results(self):
        with self.assertRaises(SuppressionError):
            self.check('// typst-lint: ignore-next ' + RULE_ID + '\n$ A=B $\n\nHence.')
        result = self.check('$ A=B')
        self.assertFalse(result.diagnostics)
        self.assertTrue(result.parse_errors)
        self.assertTrue(result.skips)

    def test_bare_cr_and_non_ascii_gap_whitespace_are_unsupported(self):
        for source in ('$ A=B $\r\r\nHence.', '$ A=B $\n\u00a0\nHence.'):
            with self.subTest(source=source):
                result = self.check(source)
                self.assertFalse(result.diagnostics)
                self.assertTrue(result.skips)

    def test_analyzer_is_pure_and_result_is_immutable(self):
        tree = parse(b'$ A=B $\n\nHence.')
        with patch('builtins.open', side_effect=AssertionError('No source I/O')):
            # Supply the immutable catalog rows so this analysis reads no file.
            catalog = ({'id': RULE_ID, 'grade': 'warning', 'source': 'test'},)
            result = analyze(tree, 'chapter.typ', catalog=catalog)
        self.assertEqual(len(result.diagnostics), 1)
        with self.assertRaises(FrozenInstanceError):
            result.edits = ()


if __name__ == '__main__':
    unittest.main()
