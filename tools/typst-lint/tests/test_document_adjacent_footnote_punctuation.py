"""Conditional detail for adjacent prose footnotes with trailing punctuation."""
from pathlib import Path
import sys
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_adjacent_footnote_punctuation as adjacent
from syntax_client import EXECUTABLE, parse
from typst_lint import digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class AdjacentFootnotePunctuationTests(unittest.TestCase):
    def test_actual_source_shapes_are_classified_without_edits(self):
        cases = (
            (b'#block[Text#footnote[Reason.]. Next.]', 'Text', '.'),
            (b'#block[$x$#footnote[Reason.], next.]', 'Equation', ','),
            (b'#block[@label#footnote[Reason.]. Next.]', 'Ref', '.'),
            ('#block[한글#footnote[#emph[이유]].\r\n다음.]'.encode(), 'Text', '.'),
        )
        for source, previous, punctuation in cases:
            with self.subTest(source=source):
                result = adjacent.analyze(parse(source), 'main.typ')
                self.assertEqual(result['status'], 'CONDITIONAL', result)
                self.assertEqual(result['source_sha256'], digest(source))
                self.assertFalse(result['runtime_binding_proved'] or result['execution_proved']
                                 or result['render_equivalence_proved'] or result['edit_permission']
                                 or result['compiler_permission'] or result['edits'])
                item, = result['diagnostics']
                self.assertEqual(item['preceding_kind'], previous)
                self.assertEqual(item['punctuation'], punctuation)
                self.assertEqual(source[item['punctuation_start']:item['punctuation_end']], punctuation.encode())
                self.assertTrue(source[item['start']:item['end']].startswith(b'#footnote['))

    def test_ambiguous_or_other_contexts_do_not_emit_classification(self):
        cases = (
            b'Root#footnote[Reason.]. Next.',
            b'#block[Text #footnote[Reason.]. Next.]',
            b'#block[Text.#footnote[Reason.] Next.]',
            b'#block[Text#footnote[Reason.].. Next.]',
            b'#block[Text#footnote[Reason.]; Next.]',
            b'#block[Text#footnote[Reason.].Next.]',
            b'#block[$ x #footnote[Reason.]. $]',
            b'#block[Text/* comment */#footnote[Reason.]. Next.]',
        )
        for source in cases:
            with self.subTest(source=source):
                result = adjacent.analyze(parse(source), 'main.typ')
                self.assertFalse(result['diagnostics'] or result['edits'], result)

    def test_explicit_shadow_assignments_and_named_imports_are_withheld(self):
        suffix = b'\n#block[Text#footnote[Reason.]. Next.]'
        prefixes = (b'#let footnote = none', b'#let footnote(x) = x',
                    b'#let f(footnote) = []', b'#{footnote = none}',
                    b'#import "other.typ": other as footnote',
                    b'#import "other.typ" as footnote',
                    b'#import "footnote.typ"',
                    b'#import "@local/footnote:0.1.0"',
                    b'#import "other.typ": footnote',
                    b'#for footnote in (none,) []')
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                result = adjacent.analyze(parse(prefix + suffix), 'main.typ')
                self.assertFalse(result['diagnostics'] or result['edits'], result)
                self.assertTrue(result['unresolved'])

    def test_conditional_wildcard_and_suppression_are_explicit(self):
        source = (b'#import "styles.typ": *\n'
                  b'// typst-lint: ignore-next pde.prose-footnote-punctuation -- intentional\n'
                  b'#block[Text#footnote[Reason.]. Next.]')
        result = adjacent.analyze(parse(source), 'main.typ')
        self.assertFalse(result['diagnostics'] or result['edits'])
        self.assertEqual(len(result['suppressed']), 1)
        self.assertTrue(result['conditions'])
        self.assertFalse(result['runtime_binding_proved'])

    def test_malformed_source_fails_closed(self):
        result = adjacent.analyze(parse(b'#block[Text#footnote['), 'main.typ')
        self.assertEqual(result['status'], 'WITHHELD')
        self.assertFalse(result['diagnostics'] or result['edits'] or result['edit_permission'])
