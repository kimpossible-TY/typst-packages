"""Bounded continuation proposals against the pinned official parser."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from continuation_rules import RULE_ID, analyze, analyze_conditional
from edit_plan import plan
from static_resolver import resolve
from syntax_client import EXECUTABLE, parse


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build the pinned syntax adapter first')
class ContinuationTests(unittest.TestCase):
    def run_source(self, source):
        tree = parse(source)
        return analyze(tree, 'main.typ', resolve(tree))

    def test_normative_case_and_idempotence(self):
        source = b'It is smooth and compactly supported.\nThe calculation can therefore be written as follows.\n'
        result = self.run_source(source)
        self.assertEqual(len(result.diagnostics), 1)
        self.assertEqual(result.diagnostics[0].rule_id, RULE_ID)
        candidate = plan(source, result.edits).candidate()
        self.assertEqual(candidate, source.replace(b'supported.\nThe', b'supported. The'))
        self.assertFalse(parse(candidate).nodes[0].errors)
        self.assertFalse(self.run_source(candidate).edits)

    def test_line_endings_indent_and_multiple_sentences(self):
        source = b'This statement holds. \t\r\n  Another statement follows.\r\nFinal statement converges.\r\n'
        result = self.run_source(source)
        self.assertEqual(len(result.edits), 2)
        candidate = plan(source, result.edits).candidate()
        self.assertEqual(candidate, b'This statement holds. Another statement follows. Final statement converges.\r\n')
        self.assertFalse(self.run_source(candidate).edits)

    def test_structures_and_ambiguous_punctuation_are_never_rewritten(self):
        examples = (
            b'See Prof.\nSmith arrived.', b'See e.g.\nAnother example follows.',
            b'It is supported.\n\nAnother sentence follows.',
            b'It is supported.\n// Comment\nAnother sentence follows.',
            b'= It is supported.\nAnother sentence follows.',
            b'- It is supported.\n  Another sentence follows.',
            b'`It is supported.\nAnother sentence follows.`',
            b'#block[It is supported.\nAnother sentence follows.]',
            b'It is supported.\\\nAnother sentence follows.',
            b'It is supported.\n$ x $ Another sentence follows.',
            b'It is supported.\n#paragraph_tab\nAnother sentence follows.',
            b'It is supported.\ntherefore it follows.',
            b'It is supported.\nAnother',
        )
        for source in examples:
            with self.subTest(source=source):
                self.assertFalse(self.run_source(source).edits)

    def test_questions_and_exclamations(self):
        for punctuation in (b'?', b'!'):
            source = b'Is this correct' + punctuation + b'\nAnother calculation follows.'
            self.assertEqual(len(self.run_source(source).edits), 1)

    def test_common_math_endings_and_short_sentence_starters(self):
        endings = (b'choice', b'definition', b'exist', b'understandable', b'useless',
                   b'equation', b'solution', b'continuous', b'bounded', b'finite', b'unique')
        for ending in endings:
            for starter in (b'We now', b'It follows', b'If this', b'A solution', b'I assume'):
                with self.subTest(ending=ending, starter=starter):
                    source = b'This is the ' + ending + b'.\n' + starter + b' holds.\n'
                    result = self.run_source(source)
                    self.assertEqual(len(result.edits), 1)
                    candidate = plan(source, result.edits).candidate()
                    self.assertEqual(candidate, source.replace(b'.\n', b'. ', 1))
                    self.assertFalse(self.run_source(candidate).edits)

    def test_abbreviations_initials_and_incomplete_starters_remain_unresolved(self):
        for ending in (b'Prof', b'approx', b'fig', b'vol', b'ref', b'max', b'etc', b'e.g'):
            source = b'This is ' + ending + b'.\nWe now continue.\n'
            with self.subTest(ending=ending):
                self.assertFalse(self.run_source(source).edits)
        for starter in (b'J. Smith', b'X other', b'It', b'A'):
            source = b'This statement holds.\n' + starter
            with self.subTest(starter=starter):
                self.assertFalse(self.run_source(source).edits)

    def test_conditional_source_patterns_include_root_and_nested_without_edits(self):
        source = ('한글 This statement holds.\nWe now continue.\n\n'
                  '#block[This is the definition.\nIt follows.\n]\n').encode()
        tree = parse(source)
        result = analyze_conditional(tree, 'main.typ')
        self.assertEqual(len(result.diagnostics), 2)
        self.assertFalse(result.edits)
        self.assertEqual((result.diagnostics[0].line, result.diagnostics[0].column), (1, 24))
        self.assertTrue(all('if this markup is rendered' in item.explanation for item in result.diagnostics))
        self.assertTrue(any('unproved' in reason for reason in result.skips))
        self.assertEqual(len(analyze(tree, 'main.typ', resolve(tree)).diagnostics), 1)

    def test_conditional_excludes_raw_code_math_and_structural_list_contexts(self):
        examples = (
            b'`This statement holds.\nWe now continue.`',
            b'#let thing = { [This statement holds.\nWe now continue.] }',
            b'$ x^#footnote[This statement holds.\nWe now continue.] $',
            b'- This statement holds.\n  We now continue.',
            b'= This statement holds.\nWe now continue.',
        )
        for source in examples:
            with self.subTest(source=source):
                result = analyze_conditional(parse(source), 'main.typ')
                self.assertFalse(result.diagnostics or result.edits)

    def test_conditional_honors_suppression_and_explicit_catalog(self):
        source = (b'#block[\n// typst-lint: ignore-next pde.continuation-line -- intentional\n'
                  b'This statement holds.\nWe now continue.\n]')
        tree = parse(source)
        result = analyze_conditional(tree, 'main.typ')
        self.assertEqual(len(result.suppressed), 1)
        self.assertFalse(result.diagnostics or result.edits)
        from typst_lint import load_rules
        catalog = load_rules()[0]['rules']
        with patch('continuation_rules.load_rules', side_effect=AssertionError('ambient catalog')):
            result = analyze_conditional(tree, 'main.typ', catalog=catalog)
        self.assertEqual(len(result.suppressed), 1)

    def test_conditional_refuses_malformed_source(self):
        result = analyze_conditional(parse(b'#block[This statement holds.\nWe now continue.\n#let =]'), 'main.typ')
        self.assertTrue(result.parse_errors)
        self.assertFalse(result.diagnostics or result.edits)

    def test_conditional_many_nested_boundaries_are_reported_without_edits(self):
        source = b'#block[' + b'This statement holds.\nWe now continue.\n\n' * 1000 + b']'
        result = analyze_conditional(parse(source), 'main.typ')
        self.assertEqual(len(result.diagnostics), 1000)
        self.assertFalse(result.edits)
        self.assertEqual(result.diagnostics[-1].line, 2998)

    def test_suppression_and_stale_proof(self):
        source = (b'// typst-lint: ignore-next pde.continuation-line -- intentional\n'
                  b'This statement holds.\nAnother statement follows.')
        result = self.run_source(source)
        self.assertFalse(result.edits or result.diagnostics)
        self.assertEqual(len(result.suppressed), 1)
        tree = parse(source)
        stale = replace(resolve(tree), source_sha256='0' * 64)
        self.assertFalse(analyze(tree, 'main.typ', stale).edits)

    def test_malformed_input_has_no_edits(self):
        source = b'This statement holds.\nAnother statement follows.\n#let x = ('
        result = self.run_source(source)
        self.assertFalse(result.edits)
        self.assertTrue(result.parse_errors)


if __name__ == '__main__':
    unittest.main()
