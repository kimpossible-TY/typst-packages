"""Real syntax and reviewed source proofs for bounded paragraph insertion."""
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
from capitalization import WORDS
from edit_plan import Edit, plan
from package_loader import load_packages
from prose_marker_rules import RULE_ID, analyze
from static_resolver import (PackageSource, ParagraphInsertionProof, TEXT_UTILS,
                             TEXT_UTILS_SOURCE_SHA256, resolve)
from suppression import resolve as suppressions
from syntax_client import EXECUTABLE, parse
from typst_lint import digest, load_rules

IMPORT = b'#import "@local/text-utils:0.1.2": paragraph_tab, apply-paragraph-tabs\n'
SHOW = b'#show: apply-paragraph-tabs\n'
LANG = b'#set text(lang: "en")\n'
PREFIX = IMPORT + SHOW + LANG
BODY = b'First idea.\n\nThis consequence follows.\n'


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class ProseMarkerRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packages = load_packages(REPO, (TEXT_UTILS,)).sources
        assert len(cls.packages) == 1

    def run_source(self, source, *, packages=None, language=None, mode='markup'):
        tree = parse(source, mode)
        resolution = resolve(tree, packages=self.packages if packages is None else packages,
                             fallback_language=language)
        return tree, resolution, analyze(tree, 'fixture.typ', resolution)

    def test_exact_root_insertion_and_repeat_analysis(self):
        source = PREFIX + BODY
        tree, resolution, result = self.run_source(source)
        at = source.index(b'This consequence')
        self.assertEqual(result.edits, (Edit(at, at, b'#paragraph_tab\n'),))
        self.assertEqual(len(result.diagnostics), 1)
        self.assertEqual((result.diagnostics[0].line, result.diagnostics[0].column), (6, 1))
        candidate = plan(source, result.edits).candidate()
        self.assertEqual(candidate, source[:at] + b'#paragraph_tab\n' + source[at:])
        final = self.run_source(candidate)[2]
        self.assertFalse(final.diagnostics or final.edits)
        self.assertFalse(resolution.complete)
        self.assertEqual(resolution.paragraph_insertion_proof.source_sha256, digest(source))
        with self.assertRaises(FrozenInstanceError):
            resolution.paragraph_insertion_proof.source_sha256 = 'changed'

    def test_all_editorial_uppercase_starters_reuse_casing_policy(self):
        for word in WORDS:
            for first in (word.capitalize(), word.upper()):
                with self.subTest(first=first):
                    source = PREFIX + b'First idea.\n\n' + first + b' holds.\n'
                    result = self.run_source(source)[2]
                    self.assertEqual(len(result.edits), 1, result.skips)

    def test_lowercase_starters_remain_explicit_diagnostic_only(self):
        for word in WORDS:
            with self.subTest(word=word):
                source = PREFIX + b'First idea.\n\n' + word + b' holds.\n'
                result = self.run_source(source)[2]
                self.assertEqual(len(result.diagnostics), 1)
                self.assertFalse(result.edits)
                self.assertTrue(any('later capitalization repair' in reason for reason in result.skips))

    def test_unsupported_initials_never_broaden_capitalization_or_change_prose(self):
        for first in (b'Second idea.', b'iPhone remains.', b'eBay remains.', b'tHis holds.',
                      b'x holds.', b'This, follows.', b'This-like holds.',
                      'Élan holds.'.encode(), '따라서 성립.'.encode()):
            with self.subTest(first=first):
                source = PREFIX + b'First idea.\n\n' + first + b'\n'
                result = self.run_source(source)[2]
                self.assertEqual(len(result.diagnostics), 1)
                self.assertFalse(result.edits)
                self.assertTrue(any('fix_unavailable:' in reason for reason in result.skips))
        source = PREFIX + b'First idea.\n\nThis'
        self.assertFalse(self.run_source(source)[2].edits)

    def test_source_language_and_config_fallback_are_exact(self):
        for lang, fallback, repaired in ((b'', None, False), (b'', 'en', True),
                (b'#set text(lang: "en-US")\n', None, True),
                (b'#set text(lang: "ko")\n', 'en', False),
                (b'#set text(lang: variable)\n', 'en', False),
                (b'#set text(lang: "en") if condition\n', 'en', False)):
            with self.subTest(lang=lang, fallback=fallback):
                result = self.run_source(IMPORT + SHOW + lang + BODY, language=fallback)[2]
                self.assertEqual(len(result.diagnostics), 1)
                self.assertEqual(bool(result.edits), repaired, result.skips)

    def test_lf_crlf_bom_indentation_and_original_gap_are_preserved(self):
        for newline in (b'\n', b'\r\n'):
            for gap in (newline * 2, b' \t' + newline + b' \t' + newline,
                        newline * 4):
                with self.subTest(newline=newline, gap=gap):
                    prefix = b'\xef\xbb\xbf' + PREFIX.replace(b'\n', newline)
                    source = prefix + '이전 idea.'.encode() + gap + b' \tThis consequence follows.' + newline
                    result = self.run_source(source)[2]
                    at = source.index(b' \tThis')
                    self.assertEqual(result.edits, (Edit(at, at, b'#paragraph_tab' + newline),))
                    candidate = plan(source, result.edits).candidate()
                    self.assertEqual(candidate, source[:at] + b'#paragraph_tab' + newline + source[at:])
                    self.assertIn(gap + b'#paragraph_tab' + newline + b' \tThis', candidate)
                    self.assertFalse(self.run_source(candidate)[2].edits)

    def test_multiple_independent_boundaries_and_single_line_continuations(self):
        source = PREFIX + b'First idea.\nIts consequence follows.\n\nThis holds.\n\nTherefore it holds.\n'
        result = self.run_source(source)[2]
        self.assertEqual(len(result.diagnostics), 2)
        self.assertEqual(len(result.edits), 2)
        candidate = plan(source, result.edits).candidate()
        self.assertIn(b'First idea.\nIts consequence follows.', candidate)
        self.assertEqual(candidate.count(b'#paragraph_tab\n'), 2)
        self.assertFalse(self.run_source(candidate)[2].diagnostics)

    def test_large_eligible_batch_reparses_only_once(self):
        import prose_marker_rules
        source = PREFIX + b'First idea.\n\n' + b'\n\n'.join([b'This consequence follows.'] * 200) + b'\n'
        tree = parse(source)
        resolution = resolve(tree, packages=self.packages)
        with patch.object(prose_marker_rules, 'parse', wraps=parse) as parser:
            result = analyze(tree, 'fixture.typ', resolution)
        self.assertEqual(parser.call_count, 1)
        self.assertEqual(len(result.edits), 200)

    def test_exact_standalone_reviewed_marker_prefix_on_preceding_prose_is_supported(self):
        source = PREFIX + b'\n#paragraph_tab\nFirst idea.\n\nThis consequence follows.\n'
        result = self.run_source(source)[2]
        self.assertEqual(len(result.edits), 1, result.skips)
        for marker in (b'#paragraph_tab inline\n', b'#paragraph_tab\n// comment\n',
                       b'#paragraph_tab\n#highlight[First] '):
            source = PREFIX + b'\n' + marker + b'idea.\n\nThis consequence follows.\n'
            result = self.run_source(source)[2]
            self.assertFalse(result.diagnostics or result.edits)

    def test_no_document_start_structural_or_unknown_boundary_inference(self):
        for body in (b'This holds.\n', b'First idea. This holds.\n',
                     b'First idea.\nThis holds.\n',
                     b'\n\nThis holds.\n', b'= Heading\n\nThis holds.\n',
                     b'- List\n\nThis holds.\n', b'+ List\n\nThis holds.\n',
                     b'/ Term: definition\n\nThis holds.\n',
                     b'First idea.\n\n$ A = B $\n\nThis holds.\n',
                     b'First idea.\n\n#figure[Caption]\n\nThis holds.\n',
                     b'First idea.\n#block[This holds.]\n',
                     b'First idea.\n#parbreak()\nThis holds.\n',
                     b'First idea.\n\n#paragraph_tab\nThis holds.\n',
                     b'#let body = [First idea.\n\nThis holds.]\n',
                     b'#context [First idea.\n\nThis holds.]\n',
                     b'First idea.\n\n// comment\nThis holds.\n',
                     b'First idea.\n// comment\n\nThis holds.\n'):
            with self.subTest(body=body):
                result = self.run_source(PREFIX + body)[2]
                self.assertFalse(result.diagnostics or result.edits, result)

    def test_missing_marker_show_and_package_evidence_never_create_imports(self):
        for prefix, packages in ((IMPORT + LANG, self.packages),
                                 (IMPORT.replace(b'paragraph_tab, ', b'') + SHOW + LANG, self.packages),
                                 (PREFIX, ()), (LANG, self.packages)):
            with self.subTest(prefix=prefix, packages=bool(packages)):
                source = prefix + BODY
                result = self.run_source(source, packages=packages)[2]
                self.assertEqual(len(result.diagnostics), 1 if packages else 0)
                self.assertFalse(result.edits)
                if packages:
                    self.assertTrue(any('availability and active reviewed paragraph show are unproven' in s for s in result.skips))

    def test_alias_spelling_and_show_capture_have_separate_lifetimes(self):
        for prefix in (
            IMPORT.replace(b'apply-paragraph-tabs', b'apply-paragraph-tabs as tabs')
                + b'#show: tabs\n' + LANG,
            b'#import "@local/text-utils:0.1.2" as t\n'
                b'#let paragraph_tab = t.paragraph_tab\n#show: t.apply-paragraph-tabs\n' + LANG,
            IMPORT + b'#let tabs = apply-paragraph-tabs\n#show: tabs\n#let tabs = none\n' + LANG,
            PREFIX + b'#let apply-paragraph-tabs = none\n',
            IMPORT + b'#let saved = paragraph_tab\n#let paragraph_tab = none\n'
                b'#let paragraph_tab = saved\n' + SHOW + LANG,
        ):
            with self.subTest(prefix=prefix):
                result = self.run_source(prefix + BODY)[2]
                self.assertEqual(len(result.edits), 1, result.skips)
        prefix = IMPORT.replace(b'paragraph_tab, ', b'paragraph_tab as tab, ') + SHOW + LANG
        result = self.run_source(prefix + BODY)[2]
        self.assertEqual(len(result.diagnostics), 1)
        self.assertFalse(result.edits)

    def test_shadowing_mutation_and_show_after_boundary_do_not_grant_proof(self):
        for before in (b'#let paragraph_tab = "wrong"\n', b'#(paragraph_tab = "wrong")\n',
                       b'#let (paragraph_tab,) = ("wrong",)\n',
                       b'#import "unknown.typ": *\n',
                       b'#if flag { paragraph_tab = "wrong" }\n'):
            with self.subTest(before=before):
                source = PREFIX + before + BODY
                tree, resolution, result = self.run_source(source)
                right = next(i for i in tree.nodes[0].children
                             if source[tree.nodes[i].start:tree.nodes[i].end].startswith(b'This consequence'))
                self.assertNotIn(right, resolution.paragraph_insertion_proof.available)
                self.assertFalse(result.edits)
        source = IMPORT + LANG + BODY + SHOW
        self.assertFalse(self.run_source(source)[2].edits)

    def test_unknown_show_effects_and_nested_show_cannot_be_recovered_by_known_show(self):
        for prefix in (IMPORT + b'#show: unknown\n' + SHOW + LANG,
                       IMPORT + b'#show text: it => it\n' + SHOW + LANG,
                       IMPORT + b'#show: apply-paragraph-tabs.with()\n' + LANG,
                       IMPORT + b'#let f() = { show: apply-paragraph-tabs; [X] }\n' + LANG):
            with self.subTest(prefix=prefix):
                tree, resolution, result = self.run_source(prefix + BODY)
                self.assertFalse(resolution.paragraph_insertion_proof.available)
                self.assertFalse(result.edits)

    def test_wrong_versions_facades_and_replaced_sources_are_not_certified(self):
        for prefix, packages in (
            (PREFIX.replace(b'0.1.2', b'0.1.1'), self.packages),
            (PREFIX.replace(b'@local/text-utils:0.1.2', b'facade.typ'), self.packages),
            (PREFIX, (replace(self.packages[0], source=self.packages[0].source + b'\n'),)),
            (PREFIX.replace(b'0.1.2', b'0.1.1'),
             (PackageSource('@local/text-utils:0.1.1', 'other', self.packages[0].source),)),
        ):
            with self.subTest(prefix=prefix):
                resolution = self.run_source(prefix + BODY, packages=packages)[1]
                self.assertFalse(resolution.paragraph_insertion_proof.available)
        self.assertEqual(digest(self.packages[0].source), TEXT_UTILS_SOURCE_SHA256)

    def test_stale_absent_and_invalid_proofs_are_explicit_refusals(self):
        source = PREFIX + BODY
        tree, resolution, _ = self.run_source(source)
        for proof in (None, ParagraphInsertionProof('stale', resolution.paragraph_insertion_proof.available),
                      ParagraphInsertionProof(digest(source), frozenset({len(tree.nodes)})),
                      ParagraphInsertionProof(digest(source), frozenset({True}))):
            result = analyze(tree, 'fixture.typ', replace(resolution, paragraph_insertion_proof=proof))
            self.assertEqual(len(result.diagnostics), 1)
            self.assertFalse(result.edits)
        result = analyze(tree, 'fixture.typ', replace(resolution, source_sha256='stale'))
        self.assertFalse(result.diagnostics or result.edits)
        self.assertTrue(any('Stale or mismatched' in reason for reason in result.skips))

    def test_suppression_targets_and_comments_are_never_crossed(self):
        for rid in (RULE_ID, 'core.parenthesized-scripts', 'pde.paragraph-capitalization'):
            source = PREFIX + b'First idea.\n\n// typst-lint: ignore-next ' + rid.encode() + b' -- keep target\nThis holds.\n'
            tree, _, result = self.run_source(source)
            scopes = suppressions(tree, {rule['id'] for rule in load_rules()[0]['rules']})
            self.assertEqual(len(scopes), 1)
            self.assertEqual(source[scopes[0].start:scopes[0].end], b'This holds.')
            self.assertFalse(result.diagnostics or result.edits)
        source = (PREFIX + b'// typst-lint: ignore-next core.parenthesized-scripts -- prior target\n'
                  b'$x^i$\n\n' + BODY)
        result = self.run_source(source)[2]
        self.assertEqual(len(result.edits), 1, result.skips)
        source = PREFIX + BODY + b'\n// typst-lint: ignore-next core.parenthesized-scripts -- later target\n$x^i$\n'
        self.assertEqual(len(self.run_source(source)[2].edits), 1)

    def test_entire_adjacent_paragraphs_must_be_literal_not_just_their_edge_text(self):
        for paragraph in (b'First $x$ tail.', b'First.#footnote[Reason.] tail.',
                          b'First#footnote[Reason.].', b'First #highlight[important] tail.',
                          b'First *strong* tail.', b'First _emphasis_ tail.',
                          b'First @claim tail.', b'First `raw` tail.',
                          b'First.\n// keep\ntail.'):
            for body in (paragraph + b'\n\nThis holds.\n',
                         b'First idea.\n\nThis holds. ' + paragraph + b'\n'):
                with self.subTest(body=body):
                    result = self.run_source(PREFIX + body)[2]
                    self.assertFalse(result.diagnostics or result.edits)
                    self.assertTrue(any('Adjacent paragraph contains nonliteral' in s for s in result.skips))

    def test_reparse_structural_reinterpretation_and_target_changes_refuse_edits(self):
        source = PREFIX + BODY
        tree, resolution, _ = self.run_source(source)
        import prose_marker_rules
        actual_parse = prose_marker_rules.parse
        def changed(content, mode):
            candidate = actual_parse(content, mode)
            target = next(i for i in candidate.nodes[0].children
                          if candidate.nodes[i].kind == 'Text'
                          and content[candidate.nodes[i].start:candidate.nodes[i].end].startswith(b'This'))
            nodes = list(candidate.nodes)
            nodes[target] = replace(nodes[target], kind='Heading')
            return replace(candidate, nodes=tuple(nodes))
        with patch.object(prose_marker_rules, 'parse', side_effect=changed):
            result = analyze(tree, 'fixture.typ', resolution)
        self.assertFalse(result.edits)
        self.assertTrue(any('reinterpret original literal prose' in reason for reason in result.skips))

    def test_malformed_and_nonmarkup_inputs_never_propose_repairs(self):
        result = self.run_source(PREFIX + BODY + b'#let =')[2]
        self.assertTrue(result.parse_errors)
        self.assertFalse(result.edits)
        result = self.run_source(b'1 + 2', mode='code')[2]
        self.assertFalse(result.diagnostics or result.edits)


if __name__ == '__main__':
    unittest.main()
