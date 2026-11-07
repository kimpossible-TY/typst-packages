"""Bounded continuation proposals against the pinned official parser."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from continuation_rules import RULE_ID, analyze
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
