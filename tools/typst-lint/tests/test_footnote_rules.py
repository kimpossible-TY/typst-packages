"""Partial rule tests; synthetic spacer proofs exercise the resolver contract."""
from pathlib import Path
import sys
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import plan
from footnote_rules import GAP, PUNCT, ALIGNED, UNALIGNED, PROSE, SpacerProof, analyze
from static_resolver import resolve
from syntax_client import EXECUTABLE, parse
from typst_lint import digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class FootnoteRules(unittest.TestCase):
    def run_source(self, source, *, proof=False, available=False):
        tree = parse(source)
        existing = frozenset(i for i, n in enumerate(tree.nodes)
                             if proof and n.kind == 'Ident' and source[n.start:n.end] == b'dots_space')
        calls = frozenset(i for i, n in enumerate(tree.nodes)
                          if available and n.kind == 'FuncCall' and source[n.start:n.end].startswith(b'footnote['))
        return analyze(tree, 'fixture.typ', resolve(tree), SpacerProof(digest(source), existing, calls))

    def candidate(self, source, **kw):
        results = self.run_source(source, **kw)
        p = plan(source, [e for r in results.values() for e in r.edits])
        self.assertFalse(p.deferred)
        return results, p.candidate()

    def test_prose_punctuation_repairs_and_idempotence(self):
        for source, expected in ((b'This. #footnote[Reason.]', b'This.#footnote[Reason.]'),
                                 (b'This \t#footnote[Reason.], next', b'This,#footnote[Reason.] next'),
                                 ('한글. #footnote[이유]'.encode(), '한글.#footnote[이유]'.encode())):
            with self.subTest(source=source):
                result, candidate = self.candidate(source)
                self.assertEqual(candidate, expected)
                self.assertEqual(len(result[PUNCT].diagnostics), 1)
                self.assertFalse(self.run_source(candidate)[PUNCT].diagnostics)
                self.assertFalse(self.run_source(candidate)[PUNCT].edits)

    def test_punctuation_refuses_ambiguous_cases(self):
        for source in (b'This;#footnote[X]', b'This #footnote[X]!', b'This #footnote[X]..',
                       b'This.\n#footnote[X]', b'This. // comment\n#footnote[X]', b'This.#footnote[X] #footnote[Y].',
                       b'This #footnote[#strong[X]].'):
            with self.subTest(source=source):
                result = self.run_source(source)[PUNCT]
                self.assertTrue(result.diagnostics)
                self.assertFalse(result.edits)
        for source in (b'This,#footnote[X]', b'This.#footnote[X]', b'For $x$, this.#footnote[X]'):
            self.assertFalse(self.run_source(source)[PUNCT].diagnostics)

    def test_alignment_detected_from_tokens_not_nested_content_or_source_lines(self):
        for source in (b'$ A &= B #footnote[X] $', b'$\n A=B \\\n C=D #footnote[X]\n$'):
            result, candidate = self.candidate(source, available=True)
            self.assertTrue(result[ALIGNED].diagnostics)
            self.assertIn(b'#dots_space #footnote', candidate)
            self.assertFalse(self.run_source(candidate, proof=True, available=True)[ALIGNED].diagnostics)
        for source in (b'$\n A=B\n C=D #footnote[$x & y$]\n$',
                       b'$ A=B #footnote[Reason.] $'):
            self.assertFalse(self.run_source(source, available=True)[ALIGNED].diagnostics)

    def test_proof_required_for_spacer_and_shadowing(self):
        source = b'$ A &= B #footnote[X] $'
        result = self.run_source(source)[ALIGNED]
        self.assertTrue(result.diagnostics)
        self.assertFalse(result.edits)
        self.assertTrue(result.skips)
        source = b'$ A = B #dots_space #footnote[X] $'
        self.assertFalse(self.run_source(source)[UNALIGNED].edits)
        self.assertFalse(self.run_source(source)[UNALIGNED].diagnostics)
        self.assertTrue(self.run_source(source)[UNALIGNED].skips)
        for expression in (b'#alias', b'#facade.dots_space', b'#helper()'):
            source = b'$ A &= B ' + expression + b' #footnote[X] $'
            result = self.run_source(source, available=True)[ALIGNED]
            self.assertFalse(result.diagnostics or result.edits)
            self.assertTrue(result.skips)
        source = b'#let footnote = unknown\nThis. #footnote[X]'
        self.assertFalse(self.run_source(source)[PUNCT].diagnostics)

    def test_spacer_removals_preserve_formula_and_prose(self):
        for source, expected, rule in ((b'This.#dots_space #footnote[X]', b'This.#footnote[X]', PROSE),
            (b'$ A = B #dots_space #footnote[X] $', b'$ A = B #footnote[X] $', UNALIGNED)):
            result, candidate = self.candidate(source, proof=True)
            self.assertEqual(candidate, expected)
            self.assertTrue(result[rule].diagnostics)
            self.assertFalse(self.run_source(candidate, proof=True)[rule].diagnostics)

    def test_prose_spacer_combines_gap_or_defers_ambiguous_punctuation(self):
        source = b'This.  #dots_space #footnote[X]'
        result, candidate = self.candidate(source, proof=True)
        self.assertEqual(candidate, b'This.#footnote[X]')
        self.assertTrue(result[PUNCT].diagnostics)
        self.assertFalse(self.run_source(candidate)[PUNCT].diagnostics)
        for source in (b'This;#dots_space #footnote[X]', b'This #dots_space #footnote[X].'):
            result = self.run_source(source, proof=True)
            self.assertTrue(result[PROSE].diagnostics)
            self.assertTrue(result[PUNCT].diagnostics)
            self.assertFalse(result[PROSE].edits or result[PUNCT].edits)

    def test_footnote_gap_and_eof(self):
        source = b'$\n A #footnote[X]\r\n \t\r\n B\n$'
        result, candidate = self.candidate(source)
        self.assertTrue(result[GAP].diagnostics)
        self.assertEqual(candidate, b'$\n A #footnote[X]\r\n B\n$')
        self.assertFalse(self.run_source(candidate)[GAP].diagnostics)
        source = b'This.#footnote[X]\n\nNext.'
        result = self.run_source(source)[GAP]
        self.assertTrue(result.diagnostics)
        self.assertFalse(result.edits)
        for source in (b'This.#footnote[X]\n\n', b'This.#footnote[X]\n#parbreak()\n\nNext.',
                       b'This.#footnote[X]\nNext.'):
            self.assertFalse(self.run_source(source)[GAP].diagnostics)

    def test_math_gap_before_comment_is_diagnostic_only(self):
        for comment in (b'// explanation', b'/* explanation */'):
            source = b'$\n A #footnote[X]\n\n' + comment + b'\n B\n$'
            result = self.run_source(source)[GAP]
            self.assertTrue(result.diagnostics)
            self.assertFalse(result.edits)
            self.assertTrue(any('comment attachment' in s for s in result.skips))

    def test_proof_is_source_bound_and_unknown_contexts_skipped(self):
        source = b'This. #footnote[X]'
        tree = parse(source)
        self.assertTrue(all(r.skips and not r.edits for r in analyze(tree, 'x', resolve(tree), SpacerProof('stale')).values()))
        for source in (b'#block[This. #footnote[X]]', b'`This. #footnote[X]`',
                       b'#let f() = [This. #footnote[X]]', b'$x #footnote[X]$'):
            self.assertFalse(self.run_source(source)[PUNCT].diagnostics)


if __name__ == '__main__':
    unittest.main()
