"""Real resolver and bounded backends for literal prose footnote boundaries."""
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import Edit, plan
from footnote_rules import GAP, analyze as footnotes
from package_loader import load_packages
from paragraph_rules import CAPITALIZATION, analyze as paragraphs
from prose_marker_rules import RULE_ID as MARKER, analyze as markers
from source_analysis import analyze
from static_resolver import ParagraphInsertionProof, TEXT_UTILS, resolve
from syntax_client import EXECUTABLE, parse
from typst_lint import digest

IMPORT = b'#import "@local/text-utils:0.1.2": paragraph_tab, apply-paragraph-tabs\n'
SHOW = b'#show: apply-paragraph-tabs\n'
LANG = b'#set text(lang: "en")\n'
PREFIX = IMPORT + SHOW + LANG
NOTE = b'First idea.#footnote[Reason.]'


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class FootnoteCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packages = load_packages(REPO, (TEXT_UTILS,)).sources

    def direct(self, source, *, packages=None, language=None):
        tree = parse(source)
        resolution = resolve(tree, packages=self.packages if packages is None else packages,
                             fallback_language=language)
        return tree, resolution

    def backend(self, source):
        tree, resolution = self.direct(source)
        return markers(tree, 'main.typ', resolution, allow_followup_casing=True), footnotes(tree, 'main.typ', resolution)[GAP]

    def composed(self, source, ignored=frozenset()):
        results = analyze(parse(source), 'main.typ', pack='pde', packages=self.packages, ignored=ignored)
        candidate = plan(source, [edit for result in results.values() for edit in result.edits])
        self.assertFalse(candidate.deferred)
        return results, candidate.candidate()

    def test_existing_marked_owns_only_one_break_insertion(self):
        source = PREFIX + NOTE + b'\n\n#paragraph_tab\nThis holds.\n'
        marker, gap = self.backend(source)
        at = source.index(b'\n\n') + 1
        self.assertEqual(gap.edits, (Edit(at, at, b'#parbreak()\n'),))
        self.assertFalse(marker.edits or marker.diagnostics)
        results, candidate = self.composed(source)
        self.assertEqual(candidate, source[:at] + b'#parbreak()\n' + source[at:])
        self.assertEqual({rid for r in results.values() for e in r.attributed_edits for rid in e.rule_ids}, {GAP})
        self.assertEqual(results[GAP].repair_passes, 1)
        self.assertFalse(any(r.edits for r in self.composed(candidate)[0].values()))

    def test_missing_marker_policy_owns_both_insertions_and_real_later_case(self):
        source = PREFIX + NOTE + b'\n\nthis consequence follows.\n'
        marker, gap = self.backend(source)
        start = source.index(b'this consequence')
        at = source.index(b'\n\n') + 1
        self.assertEqual(marker.edits, (Edit(at, at, b'#parbreak()\n'), Edit(start, start, b'#paragraph_tab\n')))
        self.assertTrue(gap.diagnostics)
        self.assertFalse(gap.edits)
        middle = plan(source, marker.edits).candidate()
        self.assertIn(b'#paragraph_tab\nthis consequence', middle)
        self.assertNotIn(b'This consequence', middle)
        tree, resolution = self.direct(middle)
        casing = paragraphs(tree, 'main.typ', resolution)[CAPITALIZATION]
        self.assertEqual(casing.edits, (Edit(middle.index(b'this'), middle.index(b'this') + 1, b'T'),))
        results, final = self.composed(source)
        self.assertEqual(final, middle.replace(b'this consequence', b'This consequence'))
        first, second = results[MARKER].repair_steps
        self.assertEqual((first.before, first.after, second.before, second.after), (source, middle, middle, final))
        self.assertEqual({rid for e in first.applied for rid in e.rule_ids}, {MARKER})
        self.assertEqual({rid for e in second.applied for rid in e.rule_ids}, {CAPITALIZATION})
        self.assertFalse(results[GAP].attributed_edits)
        self.assertFalse(any(r.edits or r.diagnostics for r in self.composed(final)[0].values()))

    def test_default_backend_does_not_opt_into_casing(self):
        source = PREFIX + NOTE + b'\n\nthis holds.\n'
        tree, resolution = self.direct(source)
        result = markers(tree, 'main.typ', resolution)
        self.assertTrue(result.diagnostics)
        self.assertFalse(result.edits)
        self.assertTrue(any('later capitalization repair' in s for s in result.skips))

    def test_rule_ignores_cannot_leave_half_boundary_repairs(self):
        source = PREFIX + NOTE + b'\n\nthis holds.\n'
        middle = source.replace(b'\n\nthis', b'\n#parbreak()\n\n#paragraph_tab\nthis')
        for ignored, expected in (({MARKER}, source), ({GAP}, middle.replace(b'this holds', b'This holds')),
                                   ({CAPITALIZATION}, middle), ({MARKER, GAP}, source)):
            with self.subTest(ignored=ignored):
                results, candidate = self.composed(source, frozenset(ignored))
                self.assertEqual(candidate, expected)
                self.assertTrue(all(not results[rid].edits for rid in ignored))

    def test_lf_crlf_bom_indentation_and_all_blank_bytes_are_preserved(self):
        for newline in (b'\n', b'\r\n'):
            for blank in (newline * 2, newline + b' \t' + newline * 3):
                for marked in (False, True):
                    source = b'\xef\xbb\xbf' + PREFIX.replace(b'\n', newline) + NOTE + blank
                    source += (b'#paragraph_tab' + newline if marked else b'') + b' \tThis holds.' + newline
                    at = source.index(blank) + len(newline)
                    expected = source[:at] + b'#parbreak()' + newline + source[at:]
                    if not marked:
                        expected = expected.replace(b' \tThis', b'#paragraph_tab' + newline + b' \tThis')
                    self.assertEqual(self.composed(source)[1], expected)

    def test_existing_marker_does_not_add_language_or_initial_word_requirements(self):
        for language in (b'', LANG, b'#set text(lang: "ko")\n'):
            for text in (b'Second idea.', b'iPhone remains.', '따라서 성립.'.encode(), b'this holds.'):
                with self.subTest(language=language, text=text):
                    source = IMPORT + SHOW + language + NOTE + b'\n\n#paragraph_tab\n' + text + b'\n'
                    marker, gap = self.backend(source)
                    self.assertEqual(len(gap.edits), 1)
                    self.assertFalse(marker.edits)
                    results, candidate = self.composed(source, frozenset({CAPITALIZATION}))
                    self.assertEqual(candidate, source.replace(b'\n\n#paragraph_tab', b'\n#parbreak()\n\n#paragraph_tab'))

    def test_new_marker_reuses_exact_editorial_and_language_refusals(self):
        for text in (b'Second idea.', b'iPhone remains.', b'eBay remains.', b'tHis holds.', b'x holds.',
                     b'This, follows.', b'this-like holds.', '따라서 성립.'.encode(), b'This'):
            source = PREFIX + NOTE + b'\n\n' + text
            marker, gap = self.backend(source)
            self.assertTrue(marker.diagnostics)
            self.assertFalse(marker.edits or gap.edits)
            self.assertEqual(self.composed(source)[1], source)
        for lang in (b'', b'#set text(lang: "ko")\n', b'#set text(lang: variable)\n'):
            source = IMPORT + SHOW + lang + NOTE + b'\n\nthis holds.\n'
            self.assertFalse(self.backend(source)[0].edits)
            self.assertEqual(self.composed(source)[1], source)

    def test_missing_or_shadowed_proofs_never_manufacture_dependencies(self):
        body = NOTE + b'\n\nThis holds.\n'
        for prefix, packages in ((IMPORT + LANG, self.packages), (SHOW + LANG, self.packages),
                                 (PREFIX, ()), (PREFIX + b'#let parbreak = none\n', self.packages),
                                 (PREFIX + b'#let paragraph_tab = none\n', self.packages),
                                 (PREFIX + b'#(parbreak = none)\n', self.packages),
                                 (IMPORT + b'#show: unknown\n' + SHOW + LANG, self.packages),
                                 (PREFIX + b'#show parbreak: [BREAK]\n', self.packages)):
            with self.subTest(prefix=prefix, packages=bool(packages)):
                tree, resolution = self.direct(prefix + body, packages=packages)
                self.assertFalse(markers(tree, 'main.typ', resolution, allow_followup_casing=True).edits)
                self.assertFalse(footnotes(tree, 'main.typ', resolution)[GAP].edits)

    def test_stale_absent_or_invalid_break_proof_refuses_both_paths(self):
        for marked in (False, True):
            source = PREFIX + NOTE + b'\n\n' + (b'#paragraph_tab\n' if marked else b'') + b'This holds.\n'
            tree, resolution = self.direct(source)
            proof = resolution.paragraph_insertion_proof
            self.assertIs(type(proof.parbreak_available), frozenset)
            with self.assertRaises(FrozenInstanceError):
                proof.parbreak_available = frozenset()
            for invalid in (None, replace(proof, source_sha256='stale'), replace(proof, parbreak_available=frozenset()),
                            replace(proof, parbreak_available=frozenset({True})),
                            replace(proof, parbreak_available=frozenset({len(tree.nodes)})),
                            replace(proof, parbreak_available=set(proof.parbreak_available))):
                changed = replace(resolution, paragraph_insertion_proof=invalid)
                self.assertFalse(markers(tree, 'main.typ', changed, allow_followup_casing=True).edits)
                self.assertFalse(footnotes(tree, 'main.typ', changed)[GAP].edits)
            self.assertEqual(ParagraphInsertionProof(digest(source)).parbreak_available, frozenset())

    def test_existing_one_break_gets_only_missing_marker_and_duplicates_are_not_collapsed(self):
        for marked in (False, True):
            source = PREFIX + NOTE + b'\n#parbreak()\n\n' + (b'#paragraph_tab\n' if marked else b'') + b'This holds.\n'
            marker, gap = self.backend(source)
            self.assertFalse(gap.diagnostics or gap.edits)
            self.assertEqual(len(marker.edits), 0 if marked else 1)
            self.assertEqual(self.composed(source)[1].count(b'#parbreak()'), 1)
        for breaks in (b'#parbreak()\n#parbreak()', b'#parbreak()#parbreak()', b'#parbreak( )', b'#br()'):
            source = PREFIX + b'#let br = parbreak\n' + NOTE + b'\n' + breaks + b'\n\nThis holds.\n'
            self.assertFalse(self.backend(source)[0].edits)
            self.assertEqual(self.composed(source)[1], source)

    def test_comments_directives_and_structural_content_are_not_crossed(self):
        for body in (b'First $x$ tail.#footnote[Reason.]\n\nThis holds.',
                     b'= Heading#footnote[Reason.]\n\nThis holds.',
                     b'#block[First.#footnote[Reason.]\n\nThis holds.]',
                     b'First.#footnote[*Reason*]\n\nThis holds.',
                     b'First.#footnote[// comment\nReason.]\n\nThis holds.',
                     NOTE + b'\n// comment\n\nThis holds.',
                     NOTE + b'\n\n// comment\nThis holds.',
                     NOTE + b'\n\nThis *strong* paragraph.',
                     NOTE + b'\n\n#paragraph_tab // comment\nThis holds.',
                     NOTE + b'\n\n#let x = 1\nThis holds.',
                     NOTE + b'\n\n$ A = B $',
                     NOTE + b'\n\n- List',
                     NOTE + b'\n\n// typst-lint: ignore-next pde.prose-paragraph-marker -- keep\nThis holds.'):
            with self.subTest(body=body):
                marker, gap = self.backend(PREFIX + body)
                self.assertFalse(marker.edits or gap.edits)

    def test_canonical_root_break_preserves_but_never_restores_safe_facts(self):
        for line in (b'#parbreak()', b' \t#parbreak() \t'):
            source = PREFIX + line + b'\nThis holds.\n'
            tree, resolution = self.direct(source)
            target = next(i for i in tree.nodes[0].children if tree.nodes[i].kind == 'Text')
            self.assertTrue(resolution.facts[target].prose_safe)
            self.assertIn(target, resolution.paragraph_insertion_proof.parbreak_available)
        for line in (b'#parbreak( )', b'#parbreak(/* comment */)', b'#parbreak(weak: true)',
                     b'#br()', b'#(parbreak)()', b'inline #parbreak()', b'#parbreak() inline',
                     b'#parbreak() // comment'):
            source = PREFIX + b'#let br = parbreak\n' + line + b'\nThis holds.\n'
            tree, resolution = self.direct(source)
            target = next(i for i in tree.nodes[0].children if tree.source[tree.nodes[i].start:tree.nodes[i].end] == b'This holds.')
            self.assertFalse(resolution.facts[target].prose_safe, line)
        source = IMPORT + b'#show: unknown\n' + SHOW + LANG + b'#parbreak()\nThis holds.\n'
        tree, resolution = self.direct(source)
        self.assertFalse(resolution.paragraph_insertion_proof.available)
        self.assertFalse(resolution.paragraph_insertion_proof.parbreak_available)
        self.assertFalse(resolution.complete)

    def test_large_batch_reparses_typed_breaks_and_markers_only_once(self):
        source = PREFIX + (NOTE + b'\n\nThis holds.\n\n') * 200
        tree, resolution = self.direct(source)
        with patch('prose_marker_rules.parse', wraps=parse) as parser:
            result = markers(tree, 'main.typ', resolution, allow_followup_casing=True)
        self.assertEqual(parser.call_count, 1)
        self.assertEqual(len(result.edits), 400)
        candidate = plan(source, result.edits).candidate()
        self.assertEqual(candidate.count(b'#parbreak()'), 200)
        self.assertEqual(candidate.count(b'#paragraph_tab'), 200)

    def test_typed_candidate_verification_rejects_reinterpreted_break(self):
        source = PREFIX + NOTE + b'\n\nThis holds.\n'
        tree, resolution = self.direct(source)
        def changed(content, mode):
            candidate = parse(content, mode)
            target = next(i for i in candidate.nodes[0].children
                          if content[candidate.nodes[i].start:candidate.nodes[i].end] == b'parbreak()')
            nodes = list(candidate.nodes)
            nodes[target] = replace(nodes[target], kind='Ident')
            return replace(candidate, nodes=tuple(nodes))
        with patch('prose_marker_rules.parse', side_effect=changed):
            result = markers(tree, 'main.typ', resolution, allow_followup_casing=True)
        self.assertFalse(result.edits)
        self.assertTrue(any('standalone root expression' in s for s in result.skips))


if __name__ == '__main__':
    unittest.main()
