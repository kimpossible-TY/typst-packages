"""Partial rule tests; synthetic spacer proofs exercise the resolver contract."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import plan
from footnote_rules import (GAP, PUNCT, ALIGNED, UNALIGNED, PROSE, SpacerProof,
                            analyze, analyze_conditional_nested, preview_conditional_math_gaps,
                            preview_conditional_prose_punctuation)
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

    def test_conditional_nested_prose_hints_never_offer_edits(self):
        source = b'#block[This. #footnote[X]\n\nNext.]'
        tree = parse(source)
        result = analyze_conditional_nested(tree, 'x.typ', resolve(tree))
        self.assertEqual(len(result[PUNCT].diagnostics), 1)
        self.assertEqual(len(result[GAP].diagnostics), 1)
        self.assertTrue(all(not item.edits and item.skips for item in result.values()))
        self.assertFalse(self.run_source(source)[PUNCT].diagnostics)

    def test_conditional_nested_math_and_prose_spacers(self):
        examples = (
            (b'#block[$\n A &= B #footnote[X]\n$]', ALIGNED),
            (b'#block[$\n A = B #dots_space #footnote[X]\n$]', UNALIGNED),
            (b'#block[This.#dots_space #footnote[X]]', PROSE),
        )
        for source, expected in examples:
            with self.subTest(source=source):
                tree = parse(source)
                result = analyze_conditional_nested(tree, 'x.typ', resolve(tree))
                self.assertEqual(len(result[expected].diagnostics), 1)
                self.assertTrue(all(not item.edits for item in result.values()))
        source = b'#block[$\n A = B #footnote[$x & y$]\n$]'
        tree = parse(source)
        self.assertFalse(analyze_conditional_nested(tree, 'x.typ', resolve(tree))[ALIGNED].diagnostics)

    def test_conditional_nested_refuses_unrelated_and_ambiguous_sources(self):
        for source in (
            b'This. #footnote[X]',
            b'#block[$x #footnote[X]$]',
            b'`#block[This. #footnote[X]]`',
            b'#block[$\n A &= B #unknown #footnote[X]\n$]',
        ):
            with self.subTest(source=source):
                tree = parse(source)
                result = analyze_conditional_nested(tree, 'x.typ', resolve(tree))
                self.assertFalse(any(item.diagnostics or item.edits for item in result.values()))

    def test_conditional_nested_binding_is_explicitly_unproved(self):
        source = b'#let footnote(body) = body\n#block[This. #footnote[X]]'
        tree = parse(source)
        result = analyze_conditional_nested(tree, 'x.typ', resolve(tree))
        self.assertEqual(len(result[PUNCT].diagnostics), 1)
        self.assertFalse(result[PUNCT].edits)
        self.assertTrue(any('bindings' in reason and 'unproved' in reason for reason in result[PUNCT].skips))

    def test_conditional_nested_honors_suppression_and_stale_resolution(self):
        source = (b'#block[This.\n// typst-lint: ignore-next pde.prose-footnote-punctuation -- intentional\n'
                  b'#footnote[X]\n]')
        tree = parse(source)
        result = analyze_conditional_nested(tree, 'x.typ', resolve(tree))
        self.assertFalse(result[PUNCT].diagnostics)
        self.assertEqual(len(result[PUNCT].suppressed), 1)
        from dataclasses import replace
        stale = replace(resolve(tree), source_sha256='0' * 64)
        result = analyze_conditional_nested(tree, 'x.typ', stale)
        self.assertTrue(all(item.skips and not item.diagnostics for item in result.values()))

    def test_conditional_nested_large_and_unicode_positions(self):
        source = ('한글\n' + '#block[This. #footnote[X]]\n' * 1000).encode()
        tree = parse(source)
        result = analyze_conditional_nested(tree, 'x.typ', resolve(tree))
        self.assertEqual(len(result[PUNCT].diagnostics), 1000)
        self.assertEqual((result[PUNCT].diagnostics[0].line, result[PUNCT].diagnostics[0].column), (2, 14))
        self.assertFalse(any(item.edits for item in result.values()))

    def test_conditional_math_gap_preview_reanalyzes_and_preserves_crlf(self):
        source = '#block[$\r\n 한글 #footnote[Reason.]\r\n \t\r\n B\r\n$]'.encode()
        tree = parse(source)
        result = preview_conditional_math_gaps(tree, 'fixture.typ', resolve(tree))
        self.assertEqual(result['verification'], 'conditional-parse-syntax-directives-and-footnote-reanalysis')
        self.assertFalse(result['runtime_binding_proved'] or result['render_equivalence_proved'] or result['edit_permission'])
        self.assertEqual(len(result['edits']), 1)
        edit = result['edits'][0]
        candidate = source[:edit['start']] + edit['replacement'].encode() + source[edit['end']:]
        self.assertEqual(candidate, source.replace(b'\r\n \t\r\n', b'\r\n'))
        self.assertEqual(result['candidate_sha256'], digest(candidate))
        parsed = parse(candidate)
        self.assertIsNone(preview_conditional_math_gaps(parsed, 'fixture.typ', resolve(parsed)))

    def test_conditional_math_gap_preview_refuses_prose_comment_and_suppression(self):
        for source in (b'#block[Text.#footnote[X]\n\nNext.]',
                       b'#block[$\n A #footnote[X]\n\n // comment\n B\n$]',
                       b'#block[$\n A #footnote[X]\n\n /* comment */ B\n$]',
                       b'#block[\n// typst-lint: ignore-next pde.footnote-trailing-gap -- deliberate\n'
                       b'$\n A #footnote[X]\n\n B\n$]'):
            with self.subTest(source=source):
                tree = parse(source)
                self.assertIsNone(preview_conditional_math_gaps(tree, 'fixture.typ', resolve(tree)))

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1 and Typst 0.15.1')
    def test_conditional_math_gap_fixture_pdf_matches(self):
        version = subprocess.check_output(['typst', '--version'], text=True)
        if not version.startswith('typst 0.15.1 '):
            self.skipTest('Fixture comparison pins Typst 0.15.1')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'packages').mkdir()
            (root / 'cache').mkdir()
            source = b'#block[$\n A #footnote[Reason.]\n\n B\n$]'
            tree = parse(source)
            result = preview_conditional_math_gaps(tree, 'fixture.typ', resolve(tree))
            self.assertTrue(result and result['edits'])
            edit = result['edits'][0]
            candidate = source[:edit['start']] + edit['replacement'].encode() + source[edit['end']:]
            rendered = []
            for label, contents in (('before', source), ('after', candidate)):
                entry = root / 'main.typ'
                entry.write_bytes(contents)
                output = root / f'{label}.pdf'
                subprocess.run(['typst', 'compile', '--root', str(root), '--creation-timestamp', '0',
                                '--package-path', str(root / 'packages'), '--package-cache-path', str(root / 'cache'),
                                str(entry), str(output)], check=True, capture_output=True, timeout=30)
                rendered.append(output.read_bytes())
            self.assertEqual(rendered[0], rendered[1])

    def test_conditional_prose_punctuation_preview_removes_only_horizontal_space(self):
        source = '#block[한글. \t#footnote[Why?] next, #footnote[Also.]]'.encode()
        tree = parse(source)
        result = preview_conditional_prose_punctuation(tree, 'fixture.typ', resolve(tree))
        self.assertEqual(result['verification'], 'conditional-parse-syntax-directives-and-footnote-reanalysis')
        self.assertFalse(result['runtime_binding_proved'] or result['render_equivalence_proved'] or result['edit_permission'])
        self.assertEqual(len(result['edits']), 2)
        proposal = plan(source, [__import__('edit_plan').Edit(row['start'], row['end'], row['replacement'].encode())
                                 for row in result['edits']])
        candidate = proposal.candidate()
        self.assertEqual(candidate, '#block[한글.#footnote[Why?] next,#footnote[Also.]]'.encode())
        self.assertEqual(result['candidate_sha256'], digest(candidate))
        parsed = parse(candidate)
        self.assertIsNone(preview_conditional_prose_punctuation(parsed, 'fixture.typ', resolve(parsed)))

    def test_conditional_prose_punctuation_refuses_ambiguous_and_suppressed(self):
        for source in (b'#block[This #footnote[X].]', b'#block[This; #footnote[X]]',
                       b'#block[This.\n#footnote[X]]', b'#block[This. /* comment */ #footnote[X]]',
                       b'#block[This.#dots_space #footnote[X]]',
                       b'// typst-lint: ignore-next pde.prose-footnote-punctuation -- deliberate\n'
                       b'#block[This. #footnote[X]]'):
            with self.subTest(source=source):
                tree = parse(source)
                self.assertIsNone(preview_conditional_prose_punctuation(tree, 'fixture.typ', resolve(tree)))

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and shutil.which('typst') and shutil.which('pdftotext'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1, Typst 0.15.1 and pdftotext')
    def test_conditional_prose_punctuation_fixture_preserves_text_and_note(self):
        if not subprocess.check_output(['typst', '--version'], text=True).startswith('typst 0.15.1 '):
            self.skipTest('Fixture comparison pins Typst 0.15.1')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'packages').mkdir()
            (root / 'cache').mkdir()
            source = (b'#set page(width: 300pt, height: 250pt, margin: 30pt)\n'
                      b'#block[This holds. #footnote[Reason.] Next sentence.]')
            tree = parse(source)
            result = preview_conditional_prose_punctuation(tree, 'fixture.typ', resolve(tree))
            self.assertTrue(result and result['edits'])
            edit = result['edits'][0]
            candidate = source[:edit['start']] + edit['replacement'].encode() + source[edit['end']:]
            words = []
            for label, contents in (('before', source), ('after', candidate)):
                entry = root / 'main.typ'
                entry.write_bytes(contents)
                output = root / f'{label}.pdf'
                subprocess.run(['typst', 'compile', '--root', str(root), '--creation-timestamp', '0',
                                '--package-path', str(root / 'packages'), '--package-cache-path', str(root / 'cache'),
                                str(entry), str(output)], check=True, capture_output=True, timeout=30)
                xml = subprocess.check_output(['pdftotext', '-bbox', str(output), '-'], timeout=30)
                words.append([(node.text or '', tuple(node.attrib.items()))
                              for node in ET.fromstring(xml).iter() if node.tag.endswith('}word')])
            self.assertEqual(words[0], words[1])
            self.assertIn('Reason.', ''.join(word for word, _ in words[1]))


if __name__ == '__main__':
    unittest.main()
