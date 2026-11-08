"""Real syntax and explicit reviewed package snapshots; no fabricated proofs."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import Edit, plan
from package_loader import load_packages
from proof_rules import RULE_ID, analyze, analyze_conditional, preview_conditional
from script_rule import analyze as scripts
from static_resolver import MATH_BLOCKS, MATH_BLOCKS_SOURCE_SHA256, PackageSource, is_proof_binding, resolve
from suppression import resolve as suppressions
from syntax_client import AdapterError, EXECUTABLE, parse
from typst_lint import digest, load_rules

IMPORT = b'#import "@local/math-blocks:0.2.0": proof\n'
MODULE = b'#import "@local/math-blocks:0.2.0" as m\n'


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class ProofRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loaded = load_packages(REPO, (MATH_BLOCKS,))
        assert not loaded.unavailable
        cls.packages = loaded.sources

    def result(self, source, packages=None, mode='markup'):
        tree = parse(source, mode)
        self.assertFalse(tree.nodes[0].errors, source)
        resolution = resolve(tree, packages=self.packages if packages is None else packages)
        self.assertFalse(resolution.complete)
        return analyze(tree, 'fixture.typ', resolution)

    def check(self, source, expected, *, packages=None, active=True, mode='markup'):
        result = self.result(source, packages, mode)
        proposed = plan(source, result.edits)
        self.assertFalse(proposed.deferred)
        candidate = proposed.candidate()
        self.assertEqual(candidate, expected, result.skips)
        if active:
            self.assertEqual(len(result.diagnostics), 1)
            self.assertEqual(len(result.edits), 1)
            self.assertTrue(all(edit.start == edit.end for edit in result.edits))
        final = self.result(candidate, packages, mode)
        self.assertFalse(final.edits)
        return result

    def test_next_complete_structural_and_prose_siblings(self):
        for suffix in (b'Therefore the claim.', b'$ A=B $', b'= Heading', b'- item', b'+ item',
                       b'/ Term: definition', b'```typ\nraw\n```', b'#block[Content]',
                       b'#figure([Caption])', b'#heading[Heading]', b'#list[Item]',
                       b'#enum[Item]', b'#proof[Another proof]'):
            with self.subTest(suffix=suffix):
                source = IMPORT + b'#proof[The claim follows.]\n' + suffix
                self.check(source, source.replace(b']\n', b']\n\n', 1))

    def test_aliases_modules_and_parenthesized_callees(self):
        for prefix, expression in (
            (IMPORT, b'proof'), (IMPORT, b'(proof)'),
            (IMPORT.replace(b': proof', b': proof as demonstrate'), b'demonstrate'),
            (IMPORT + b'#let saved = (proof)\n', b'saved'),
            (MODULE, b'm.proof'), (MODULE + b'#let saved = m\n', b'saved.proof'),
            (MODULE + b'#let saved = (m.proof)\n', b'saved'),
        ):
            for args in (b'[Text]', b'([Text])'):
                with self.subTest(expression=expression, args=args):
                    source = prefix + b'#' + expression + args + b'\nNext.'
                    self.check(source, source[:-5] + b'\nNext.')

    def test_exact_artifact_and_partial_wildcard(self):
        self.assertEqual(digest(self.packages[0].source), MATH_BLOCKS_SOURCE_SHA256)
        source = IMPORT.replace(b': proof', b': *') + b'#proof[X]\nNext.'
        result = self.check(source, source.replace(b']\n', b']\n\n'))
        self.assertTrue(any('partial export namespace' in s for s in result.skips))
        # Wildcard establishes this export only; a same-spelled block builtin
        # cannot be inferred from the partially reviewed namespace.
        source = IMPORT.replace(b': proof', b': *') + b'#proof[X]\n#block[Y]'
        self.check(source, source, active=False)

    def test_single_same_line_label_stays_with_complete_call(self):
        for label in (b'<claim>', b' \t<claim>'):
            source = IMPORT + b'#proof[Line 1.\nLine 2.]' + label + b'\nNext.'
            self.check(source, source[:-5] + b'\nNext.')
        source = IMPORT + b'#proof[X] <claim> Next.'
        self.check(source, source.replace(b'<claim>', b'<claim>\n\n'))

    def test_unicode_bom_indentation_comments_and_original_newlines_preserved(self):
        source = (b'\xef\xbb\xbf' + IMPORT.replace(b'\n', b'\r\n')
                  + ' \t#proof[증명. /* keep */ $α=β$]\t <증명>  \r\n \t다음.'.encode())
        at = source.index(' \t다음.'.encode())
        result = self.check(source, source[:at] + b'\r\n' + source[at:])
        self.assertEqual(result.edits, (Edit(at, at, b'\r\n'),))
        self.assertEqual((result.diagnostics[0].line, result.diagnostics[0].column), (2, 3))
        # In a mixed-newline file the existing separator's CRLF is copied,
        # without normalizing the LF import or the body.
        source = IMPORT + b'#proof[Line 1.\nLine 2.]\r\nNext.'
        self.check(source, source[:-5] + b'\r\nNext.')

    def test_same_line_prose_gets_exactly_one_blank_line(self):
        source = IMPORT + b'#proof[X] \tNext.'
        self.check(source, source.replace(b']', b']\n\n'))
        source = IMPORT.replace(b'\n', b'\r\n') + b'#proof[X] Next.'
        self.check(source, source.replace(b']', b']\r\n\r\n'))

    def test_already_separated_eof_and_body_end_never_get_synthetic_paragraph(self):
        for suffix in (b'', b'\n', b'\r\n', b' \t', b' <claim>', b' <claim>\n',
                       b'\n\nNext.', b'\r\n \t\r\nNext.', b'\n\n\n$ A=B $'):
            source = IMPORT + b'#proof[X]' + suffix
            self.check(source, source, active=False)
        for body in (b'#block[#proof[X]]', b'#let body = [#proof[X]]',
                     b'#let f() = [#proof[X]]', b'#context [#proof[X]]'):
            source = IMPORT + body
            self.check(source, source, active=False)

    def test_unknown_versions_facades_local_names_and_stale_artifacts(self):
        for prefix, packages in (
            (IMPORT, ()), (IMPORT, (replace(self.packages[0], source=self.packages[0].source + b'\n'),)),
            (IMPORT.replace(b'0.2.0', b'0.1.0'), self.packages),
            (IMPORT.replace(b'0.2.0', b'0.1.0'),
             (PackageSource('@local/math-blocks:0.1.0', 'other', self.packages[0].source),)),
            (b'#import "facade.typ": proof\n', self.packages),
            (b'#import "@local/math-book:0.2.0": proof\n', self.packages),
            (b'#let proof(body) = body\n', self.packages), (b'', self.packages),
        ):
            with self.subTest(prefix=prefix):
                source = prefix + b'#proof[X]\nNext.'
                result = self.check(source, source, packages=packages, active=False)
                self.assertFalse(result.diagnostics)

    def test_shadow_mutation_destructuring_dynamic_and_wildcard_invalidate(self):
        for change in (
            b'#let proof(body) = body\n', b'#let (proof, other) = (none, 1)\n',
            b'#{proof = none}\n', b'#{proof += none}\n',
            b'#let other = 0\n#{(proof, other) = (none, 1)}\n',
            b'#if maybe { proof = none }\n', b'#for x in (1,) { proof = none }\n',
            b'#while maybe { proof = none }\n', b'#import "unknown.typ": *\n',
            b'#import "unknown.typ": proof\n', b'#let proof = maker()\n',
        ):
            with self.subTest(change=change):
                source = IMPORT + change + b'#proof[X]\nNext.'
                result = self.check(source, source, active=False)
                self.assertFalse(result.diagnostics)
        source = MODULE + b'#{m.proof = none}\n#m.proof[X]\nNext.'
        self.check(source, source, active=False)

    def test_capture_and_local_source_order_do_not_expand_ownership(self):
        source = (IMPORT + b'#let saved = proof\n#let ignored = { proof = none }\n'
                  b'#saved[X]\nNext.')
        self.check(source, source[:-5] + b'\nNext.')
        source = IMPORT + b'#proof[X]\nNext.\n#let proof = none'
        self.check(source, source.replace(b']\n', b']\n\n', 1))
        # Deferred module aliases may retain theme-only provenance, never root
        # output ownership for proof or certainty about when its body executes.
        for source in (
            MODULE + b'#let f() = [#m.proof[X]\nNext.]',
            IMPORT + b'#let f() = [#proof[X]\nNext.]',
            IMPORT + b'#block[#proof[X]\nNext.]',
            IMPORT + b'#context [#proof[X]\nNext.]',
            IMPORT + b'#unknown[]\n#proof[X]\nNext.',
            IMPORT + b'#show: unknown\n#proof[X]\nNext.',
            IMPORT + b'#proof[X]\n#proof[Y]\nNext.',
        ):
            with self.subTest(source=source):
                result = self.result(source)
                # The first independently safe root proof is eligible; the
                # second is deliberately not certified after unknown output.
                self.assertLessEqual(len(result.edits), 1)
                if source.endswith(b'#proof[Y]\nNext.'):
                    self.assertEqual(len(result.edits), 1)
                else:
                    self.assertFalse(result.edits)

    def test_ambiguous_labels_comments_directives_and_unknown_siblings_refuse(self):
        for tail in (
            b'\n<claim>\nNext.', b' <one> <two>\nNext.', b' <one>\n<two>\nNext.',
            b' /* attached */ <claim>\nNext.', b' // comment\nNext.',
            b'\n/* comment */\nNext.', b'\n// comment\nNext.',
            b'\n// typst-lint: ignore-next pde.proof-trailing-gap -- following\nNext.',
            b'\n#unknown[Next.]', b'\n#parbreak()', b'\n#let x = 1\nNext.',
            b'\n#text[Next.]', b'\n#block(width: 100%)[Next.]', b'\n$x$',
            b'\rNext.', '\u00a0Next.'.encode(),
        ):
            source = IMPORT + b'#proof[X]' + tail
            with self.subTest(tail=tail):
                result = self.check(source, source, active=False)
                self.assertFalse(result.diagnostics)
                self.assertTrue(result.skips)
        for body in (b'#proof(variable)\nNext.', b'#proof([X], [Y])\nNext.',
                     b'#proof(body: [X])\nNext.', b'#proof( /* comment */ [X])\nNext.',
                     b'#proof[X].func()\nNext.', b'Inline #proof[X]\nNext.'):
            source = IMPORT + body
            self.check(source, source, active=False)

    def test_newline_cannot_reinterpret_literal_text_as_structure(self):
        for tail in (b'= Heading', b'- item', b'+ item', b'1. item', b'/ term: definition'):
            source = IMPORT + b'#proof[X] ' + tail
            result = self.check(source, source, active=False)
            self.assertTrue(result.diagnostics)
            self.assertTrue(any('non-whitespace syntax' in s for s in result.skips))

    def test_suppression_removes_finding_and_edit_without_reassigning_other_rules(self):
        directive = b'// typst-lint: ignore-next pde.proof-trailing-gap -- intentional\n'
        source = IMPORT + directive + b'#proof[X] <claim>\nNext.'
        result = self.check(source, source, active=False)
        self.assertFalse(result.diagnostics or result.edits)
        self.assertEqual(len(result.suppressed), 1)
        directive = b'// typst-lint: ignore-next core.parenthesized-scripts -- intentional\n'
        source = IMPORT + directive + b'#proof[$ x^i $] <claim>\nNext.'
        result = self.result(source)
        candidate = plan(source, result.edits).candidate()
        known = {r['id'] for r in load_rules()[0]['rules']}
        for value in (source, candidate):
            tree = parse(value)
            scope, = suppressions(tree, known)
            self.assertEqual(value[scope.start:scope.end], b'#proof[$ x^i $]')
            self.assertEqual(len(scripts(tree, 'x').suppressed), 1)
        self.assertTrue(result.edits)

    def test_parse_stale_resolution_and_failed_reparse_are_fail_closed(self):
        tree = parse(IMPORT + b'#proof[Unclosed')
        result = analyze(tree, 'x', resolve(tree, packages=self.packages))
        self.assertTrue(result.parse_errors)
        self.assertFalse(result.edits)
        source = IMPORT + b'#proof[X]\nNext.'
        tree = parse(source)
        resolution = resolve(tree, packages=self.packages)
        for stale in (replace(resolution, source_sha256='0' * 64), replace(resolution, facts=())):
            result = analyze(tree, 'x', stale)
            self.assertFalse(result.edits)
            self.assertTrue(any('Stale' in s for s in result.skips))
        with patch('proof_rules.parse', side_effect=AdapterError('fixture unavailable')):
            result = analyze(tree, 'x', resolution)
        self.assertTrue(result.diagnostics)
        self.assertFalse(result.edits)
        self.assertTrue(any('fix_unavailable' in s for s in result.skips))
        # A callable binding alone does not make a code-mode call root output.
        source = b'import "@local/math-blocks:0.2.0": proof; proof([X]); [Next.]'
        self.check(source, source, mode='code', active=False)

    def test_conditional_patterns_include_nested_markup_without_edits(self):
        source = (b'#proof[Root.]\nNext.\n\n#block[\n'
                  b'#proof[Nested.] <claim>\nFollowing.\n]')
        tree = parse(source)
        result = analyze_conditional(tree, 'fixture.typ', resolve(tree))
        self.assertEqual(len(result.diagnostics), 2)
        self.assertEqual([item.line for item in result.diagnostics], [1, 5])
        self.assertFalse(result.edits)
        self.assertTrue(all('if this is the reviewed proof' in item.explanation for item in result.diagnostics))
        self.assertTrue(any('unproved' in item for item in result.skips))

    def test_conditional_omits_obvious_shadow_and_unsupported_contexts(self):
        examples = (
            b'#let proof(body) = body\n#proof[X]\nNext.',
            b'#let proof = none\n#proof[X]\nNext.',
            b'#proof[X]\n\nNext.', b'#proof[X]',
            b'#proof[X] <one> <two>\nNext.',
            b'#proof[X]\n// comment\nNext.', b'Inline #proof[X]\nNext.',
            b'#proof(variable)\nNext.', b'#proof[X]\n#unknown[Next.]',
            b'`#proof[X]\nNext.`', b'- #proof[X]\n  Next.',
            b'#let other = { [\n#proof[X]\nNext.\n] }',
        )
        for source in examples:
            with self.subTest(source=source):
                tree = parse(source)
                result = analyze_conditional(tree, 'fixture.typ', resolve(tree))
                self.assertFalse(result.diagnostics or result.edits)

    def test_conditional_suppression_utf8_metadata_and_stale_resolution(self):
        source = ('한글\n#block[\n// typst-lint: ignore-next pde.proof-trailing-gap -- intentional\n'
                  '#proof[X]\nNext.\n]').encode()
        tree = parse(source)
        resolution = resolve(tree)
        catalog = load_rules()[0]['rules']
        with patch('proof_rules.load_rules', side_effect=AssertionError('ambient catalog')):
            result = analyze_conditional(tree, 'fixture.typ', resolution, catalog=catalog)
        self.assertFalse(result.diagnostics or result.edits)
        self.assertEqual(len(result.suppressed), 1)
        self.assertEqual((result.suppressed[0].line, result.suppressed[0].column), (4, 1))
        stale = analyze_conditional(tree, 'fixture.typ', replace(resolution, source_sha256='0' * 64),
                                    catalog=catalog)
        self.assertFalse(stale.diagnostics or stale.edits)
        self.assertTrue(any('Stale' in reason for reason in stale.skips))

    def test_conditional_recognizes_direct_literal_following_blocks(self):
        for following in (b'#note[Body.]', b'#theorem(title: "Result")[Body.]',
                          b'#note(title: [Heading],)[Body.]'):
            with self.subTest(following=following):
                source = b'#block[\n#proof[Done.]\n' + following + b'\n]'
                tree = parse(source)
                result = analyze_conditional(tree, 'fixture.typ', resolve(tree))
                self.assertEqual(len(result.diagnostics), 1)
                self.assertIn('expected block', result.diagnostics[0].explanation)
                self.assertFalse(result.edits)
        for following in (b'#unknown[Body.]', b'#note(title: dynamic)[Body.]',
                          b'#theorem(extra: "x")[Body.]', b'#note(title: "x", extra: 1)[Body.]'):
            with self.subTest(following=following):
                tree = parse(b'#block[\n#proof[Done.]\n' + following + b'\n]')
                result = analyze_conditional(tree, 'fixture.typ', resolve(tree))
                self.assertFalse(result.diagnostics or result.edits)

    def test_conditional_preview_is_explicit_reparsed_and_idempotent(self):
        for body in (b'#block[\n#proof[Done.]\nNext.\n]',
                     b'#block[\r\n  #proof[Done.] <claim>\r\n  $ A = B $\r\n]',
                     b'#block[\n#proof[One.]\nNext.\n#proof[Two.]\n#note[Body.]\n]',
                     b'#block[\n#proof[Nested.]\nNext.\n]\n#proof[Root.]\nNext.'):
            with self.subTest(body=body):
                tree = parse(body)
                resolution = resolve(tree)
                plain = analyze_conditional(tree, 'fixture.typ', resolution)
                self.assertFalse(plain.edits)
                result = preview_conditional(tree, 'fixture.typ', resolution)
                self.assertEqual(len(result.edits), len(result.diagnostics), result.skips)
                self.assertTrue(result.edits)
                self.assertTrue(any('PDF equivalence' in reason for reason in result.skips))
                candidate = plan(body, result.edits).candidate()
                final_tree = parse(candidate)
                self.assertFalse(final_tree.nodes[0].errors)
                final = preview_conditional(final_tree, 'fixture.typ', resolve(final_tree))
                self.assertFalse(final.diagnostics or final.edits)

    def test_conditional_preview_fails_closed_when_syntax_changes(self):
        for tail in (b'= Heading', b'- item', b'+ item', b'1. item', b'/ term: definition'):
            with self.subTest(tail=tail):
                source = b'#block[\n#proof[X] ' + tail + b'\n]'
                tree = parse(source)
                result = preview_conditional(tree, 'fixture.typ', resolve(tree))
                self.assertTrue(result.diagnostics)
                self.assertFalse(result.edits)
                self.assertTrue(any('preview_unavailable' in reason for reason in result.skips))

    def test_conditional_preview_preserves_suppression_targets(self):
        source = (b'// typst-lint: ignore-next core.parenthesized-scripts -- intentional\n'
                  b'#block[\n#proof[$ x^i $]\nNext.\n]')
        tree = parse(source)
        result = preview_conditional(tree, 'fixture.typ', resolve(tree))
        self.assertTrue(result.edits, result.skips)
        candidate = plan(source, result.edits).candidate()
        ids = {r['id'] for r in load_rules()[0]['rules']}
        for text in (source, candidate):
            scopes = suppressions(parse(text), ids)
            self.assertEqual(len(scopes), 1)
            self.assertTrue(text[scopes[0].start:scopes[0].end].startswith(b'#block['))
        source = (b'#block[\n// typst-lint: ignore-next pde.proof-trailing-gap -- intentional\n'
                  b'#proof[X]\nNext.\n]')
        tree = parse(source)
        result = preview_conditional(tree, 'fixture.typ', resolve(tree))
        self.assertFalse(result.diagnostics or result.edits)
        self.assertEqual(len(result.suppressed), 1)

    def test_conditional_preview_parse_failure_keeps_diagnostic_only(self):
        source = b'#block[\n#proof[X]\nNext.\n]'
        tree = parse(source)
        with patch('proof_rules.parse', side_effect=AdapterError('unavailable')):
            result = preview_conditional(tree, 'fixture.typ', resolve(tree))
        self.assertTrue(result.diagnostics)
        self.assertFalse(result.edits)
        self.assertTrue(any('preview_unavailable' in reason for reason in result.skips))


if __name__ == '__main__':
    unittest.main()
