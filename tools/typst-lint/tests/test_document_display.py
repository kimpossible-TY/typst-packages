"""Source-only display style over actual pinned parser output."""
from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import document_display
from document_display import RULE_ID, STANDALONE, analyze, analyze_all
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

    def test_punctuation_only_or_numeric_text_does_not_establish_prose_continuation(self):
        for suffix in ('.', '42', '\uff01', '\u0661\u0662'):
            with self.subTest(suffix=suffix):
                result = self.check('$ A=B $\n\n' + suffix)
                self.assertFalse(result.diagnostics)
                self.assertTrue(any('no Unicode letter' in reason for reason in result.skips))

    def test_analyzer_is_pure_and_result_is_immutable(self):
        tree = parse(b'$ A=B $\n\nHence.')
        with patch('builtins.open', side_effect=AssertionError('No source I/O')):
            # Supply the immutable catalog rows so this analysis reads no file.
            catalog = ({'id': RULE_ID, 'grade': 'warning', 'source': 'test'},)
            result = analyze(tree, 'chapter.typ', catalog=catalog)
        self.assertEqual(len(result.diagnostics), 1)
        with self.assertRaises(FrozenInstanceError):
            result.edits = ()


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class DocumentStandaloneDisplayTests(unittest.TestCase):
    def check(self, source):
        raw = source.encode() if isinstance(source, str) else source
        tree = parse(raw)
        results = analyze_all(tree, 'chapter.typ')
        self.assertEqual(set(results), {RULE_ID, STANDALONE})
        for result in results.values():
            self.assertEqual(result.edits, (), 'Source observations never authorize edits')
        self.assertEqual(tree.source, raw)
        return results

    def test_root_and_nested_callback_report_once_for_both_sides(self):
        for source in ('Before $ A=B $ after.',
                       '#local-scope-annotations(s => [Before $ A=B $ after.])',
                       '#let saved = [Before $ A=B $ after.]',
                       '#context [Before $ A=B $ after.]'):
            with self.subTest(source=source):
                results = self.check(source)
                finding, = results[STANDALONE].diagnostics
                self.assertEqual(finding.rule_id, STANDALONE)
                self.assertFalse(results[RULE_ID].diagnostics)

    def test_either_side_is_a_violation_but_a_physical_newline_is_accepted(self):
        for source in ('Before $ A=B $', '$ A=B $ after.',
                       'Before $ A=B $\nafter.', 'Before\n$ A=B $ after.'):
            with self.subTest(source=source):
                self.assertEqual(len(self.check(source)[STANDALONE].diagnostics), 1)
        for source in ('$ A=B $', 'Before\n$ A=B $\nafter.',
                       'Before\r\n  $ A=B $\r\nafter.'):
            with self.subTest(source=source):
                result = self.check(source)[STANDALONE]
                self.assertFalse(result.diagnostics)
                self.assertFalse(result.skips)

    def test_typed_inline_equations_are_ignored(self):
        for equation in ('$A=B$', '$ A=B$', '$A=B $'):
            source = '#unknown[Before ' + equation + ' after.]'
            with self.subTest(source=source):
                tree = parse(source.encode())
                self.assertEqual([node.display for node in tree.nodes if node.kind == 'Equation'], [False])
                for result in self.check(source).values():
                    self.assertFalse(result.diagnostics)
                    self.assertFalse(result.skips)

    def test_unicode_bom_and_lf_crlf_locations(self):
        for newline in ('\n', '\r\n'):
            with self.subTest(newline=newline):
                source = '\ufeff#unknown[' + newline + '가나다 $ α=β $ <에너지> 따라서.' + newline + ']'
                finding, = self.check(source)[STANDALONE].diagnostics
                self.assertEqual((finding.path, finding.line, finding.column), ('chapter.typ', 2, 5))
        finding, = self.check('\ufeff앞글 $ α=β $ 따라서.')[STANDALONE].diagnostics
        self.assertEqual((finding.line, finding.column), (1, 5))
        for source in ('\ufeff$ α=β $\n따라서.', '\ufeff \t$ α=β $'):
            with self.subTest(source=source):
                result = self.check(source)[STANDALONE]
                self.assertFalse(result.diagnostics)
                self.assertFalse(result.skips)

    def test_single_actual_label_attaches_to_the_closing_line(self):
        for source in ('Before $ A=B $ <energy>', '$ A=B $ <energy> after.',
                       '$ A=B $<energy>after.',
                       '#unknown[Before $\nA=B\n$\t<energy> \tafter.]'):
            with self.subTest(source=source):
                result = self.check(source)[STANDALONE]
                self.assertEqual(len(result.diagnostics), 1)
                self.assertFalse(result.skips)

    def test_detached_and_multiple_labels_leave_ownership_unresolved(self):
        for source in ('Before $ A=B $\n<energy> after.',
                       'Before $ A=B $ <one> <two> after.',
                       'Before $ A=B $ <one>\n<two> after.'):
            with self.subTest(source=source):
                result = self.check(source)[STANDALONE]
                self.assertFalse(result.diagnostics)
                self.assertTrue(result.skips)

    def test_punctuation_only_text_does_not_establish_adjacent_prose(self):
        for source in ('$ A=B $ <energy>.', '. $ A=B $',
                       '$ A=B $ <energy> 42', '#unknown[$ A=B $ <energy>.]'):
            with self.subTest(source=source):
                result = self.check(source)[STANDALONE]
                self.assertFalse(result.diagnostics)
                self.assertTrue(result.skips)
        result = self.check('$ A=B $ <energy>\n.')[STANDALONE]
        self.assertFalse(result.diagnostics)
        self.assertFalse(result.skips)

    def test_adjacent_display_chain_reports_one_finding_per_equation(self):
        results = self.check('First $ A=B $ middle $ C=D $ last.')
        self.assertEqual([(f.line, f.column) for f in results[STANDALONE].diagnostics],
                         [(1, 7), (1, 22)])
        self.assertFalse(results[RULE_ID].diagnostics)
        result = self.check('Before $ A=B $ $ C=D $ after.')[STANDALONE]
        self.assertEqual(len(result.diagnostics), 2)
        self.assertFalse(result.skips)

    def test_unknown_neighbor_does_not_hide_literal_prose_on_the_other_side(self):
        for source in ('#text[Before] $ A=B $ after.',
                       'Before $ A=B $ #text[after]',
                       'Before $ A=B $ // comment\nafter.',
                       '/* comment */ $ A=B $ after.'):
            with self.subTest(source=source):
                result = self.check(source)[STANDALONE]
                self.assertEqual(len(result.diagnostics), 1)
                self.assertTrue(result.skips)

    def test_comments_hash_wrappers_and_dynamic_labels_are_never_crossed(self):
        for source in ('Before /* comment */ $ A=B $ /* comment */ after.',
                       'Before #text[] $ A=B $ #text[] after.',
                       'Before *bold* $ A=B $ _italic_ after.',
                       '$ A=B $ #(s.tag)("energy") after.',
                       '$ A=B $ // comment\nafter.',
                       '// comment\n$ A=B $\n#text[after]'):
            with self.subTest(source=source):
                result = self.check(source)[STANDALONE]
                self.assertFalse(result.diagnostics)
        result = self.check('$ A=B $\n\n#text[after]')
        self.assertFalse(result[STANDALONE].skips, 'A following physical newline needs no standalone decision')
        self.assertTrue(result[RULE_ID].skips, 'Gap coverage independently records the wrapper boundary')

    def test_markup_bodies_never_supply_neighbors_to_each_other(self):
        for source in ('Before #unknown[$ A=B $] after.',
                       '#unknown[Before]\n#unknown[$ A=B $]\nafter.',
                       '#unknown[Before]\n#unknown[$ A=B $]\n#unknown[after]'):
            with self.subTest(source=source):
                result = self.check(source)[STANDALONE]
                self.assertFalse(result.diagnostics)
                self.assertFalse(result.skips)

    def test_code_list_heading_and_math_ancestors_remain_excluded(self):
        for source in ('#let f() = { [Before $ A=B $ after.] }',
                       '- Before $ A=B $ after.',
                       '+ Before $ A=B $ after.',
                       '/ Term: Before $ A=B $ after.',
                       '= Before $ A=B $ after.',
                       '$ x + #box[Before $ A=B $ after.] $',
                       '$ x #footnote[Before $ A=B $ after.] $',
                       '$ x #[Before $ A=B $ after.] $'):
            with self.subTest(source=source):
                result = self.check(source)[STANDALONE]
                self.assertFalse(result.diagnostics)
                self.assertTrue(result.skips)
        result = analyze_all(parse(b'[Before $ A=B $ after.]', 'code'), 'code.typ')[STANDALONE]
        self.assertFalse(result.diagnostics)
        self.assertTrue(result.skips)

    def test_non_ascii_and_bare_cr_separators_are_not_literal_boundaries(self):
        for source in ('Before\r$ A=B $', '$ A=B $\rafter.',
                       'Before\u00a0$ A=B $', '$ A=B $\u00a0after.'):
            with self.subTest(source=source):
                result = self.check(source)[STANDALONE]
                self.assertFalse(result.diagnostics)
                self.assertTrue(result.skips)
        result = self.check('Before\u00a0\n$ A=B $\n\u00a0after.')[STANDALONE]
        self.assertFalse(result.diagnostics)
        self.assertFalse(result.skips)

    def test_equation_and_enclosing_scope_suppressions_belong_to_the_equation(self):
        for source in ('// typst-lint: ignore-next ' + STANDALONE + ' -- intentional\n'
                       '$ A=B $ after.',
                       '// typst-lint: ignore-next ' + STANDALONE + ' -- intentional\n'
                       '#unknown[Before $ A=B $ after.]'):
            with self.subTest(source=source):
                result = self.check(source)[STANDALONE]
                self.assertFalse(result.diagnostics)
                self.assertEqual(len(result.suppressed), 1)

    def test_following_directive_does_not_suppress_preceding_equation(self):
        results = self.check('Before $ A=B $\n// typst-lint: ignore-next ' + STANDALONE
                             + ' -- following prose\nafter.')
        result = results[STANDALONE]
        self.assertEqual(len(result.diagnostics), 1)
        self.assertFalse(result.suppressed)
        self.assertFalse(result.skips)
        self.assertTrue(results[RULE_ID].skips)

    def test_suppression_is_specific_to_its_rule_and_markup_body(self):
        source = ('#unknown[\n// typst-lint: ignore-next ' + STANDALONE + ' -- here\n'
                  '$ A=B $ after.\n]\n#unknown[$ C=D $ after.]\n'
                  '#unknown[\n// typst-lint: ignore-next ' + RULE_ID + ' -- gap only\n'
                  '$ E=F $\n\nHence.\n]')
        results = self.check(source)
        self.assertEqual(len(results[STANDALONE].diagnostics), 1)
        self.assertEqual(len(results[STANDALONE].suppressed), 1)
        self.assertFalse(results[RULE_ID].diagnostics)
        self.assertEqual(len(results[RULE_ID].suppressed), 1)
        result = self.check('// typst-lint: ignore-next ' + RULE_ID
                            + ' -- other rule\n$ A=B $ after.')[STANDALONE]
        self.assertEqual(len(result.diagnostics), 1)
        self.assertFalse(result.suppressed)

    def test_malformed_source_and_invalid_suppression_are_not_clean(self):
        for result in self.check('Before $ A=B').values():
            self.assertFalse(result.diagnostics)
            self.assertTrue(result.parse_errors)
            self.assertTrue(result.skips)
        with self.assertRaises(SuppressionError):
            self.check('// typst-lint: ignore-next ' + STANDALONE + '\n$ A=B $ after.')

    def test_catalog_metadata_purity_and_gap_only_api_compatibility(self):
        tree = parse(b'Before $ A=B $ after.\n\n$ C=D $\n\nHence.')
        catalog = ({'id': RULE_ID, 'grade': 'warning', 'source': 'gap-test'},
                   {'id': STANDALONE, 'grade': 'error', 'source': 'standalone-test'})
        with patch('builtins.open', side_effect=AssertionError('No source I/O')):
            results = analyze_all(tree, 'chapter.typ', catalog=catalog)
            gap_only = analyze(tree, 'chapter.typ', catalog=catalog[:1])
        self.assertEqual(gap_only, results[RULE_ID])
        gap_finding, = gap_only.diagnostics
        standalone_finding, = results[STANDALONE].diagnostics
        self.assertEqual((gap_finding.grade, gap_finding.source), ('warning', 'gap-test'))
        self.assertEqual((standalone_finding.grade, standalone_finding.source),
                         ('error', 'standalone-test'))
        with self.assertRaises(FrozenInstanceError):
            results[STANDALONE].edits = ()

    def test_standalone_normalization_can_reveal_a_distinct_gap_obligation(self):
        # Independently owned example states model a future two-pass formatter;
        # this diagnostic backend never constructs or applies these changes.
        before = '#scope(s => [\nThus $ A=B $ <energy>\n\nHence the claim follows.\n])'
        separated = '#scope(s => [\nThus\n$ A=B $ <energy>\n\nHence the claim follows.\n])'
        continued = '#scope(s => [\nThus\n$ A=B $ <energy>\nHence the claim follows.\n])'
        initial = self.check(before)
        self.assertEqual(len(initial[STANDALONE].diagnostics), 1)
        self.assertFalse(initial[RULE_ID].diagnostics)
        middle = self.check(separated)
        self.assertFalse(middle[STANDALONE].diagnostics)
        self.assertEqual(len(middle[RULE_ID].diagnostics), 1)
        final = self.check(continued)
        self.assertFalse(any(value.diagnostics or value.edits for value in final.values()))


if __name__ == '__main__':
    unittest.main()
