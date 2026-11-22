"""Real parser + explicit package snapshots, never fabricated spacer proofs."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import plan
from footnote_rules import ALIGNED, UNALIGNED, PROSE, PUNCT, analyze
from package_loader import load_packages
from static_resolver import MATH_BLOCKS, MATH_BLOCKS_SOURCE_SHA256, PackageSource, resolve
from syntax_client import EXECUTABLE, parse
from typst_lint import digest

IMPORT = b'#import "@local/math-blocks:0.2.0": dots_space\n'
MODULE = b'#import "@local/math-blocks:0.2.0" as m\n'


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class SpacerProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loaded = load_packages(REPO, (MATH_BLOCKS,))
        assert not loaded.unavailable
        cls.packages = loaded.sources

    def check(self, source, packages=None):
        tree = parse(source)
        self.assertFalse(tree.nodes[0].errors, source)
        resolution = resolve(tree, packages=self.packages if packages is None else packages)
        self.assertEqual(resolution.spacer_proof.source_sha256, digest(source))
        self.assertFalse(resolution.complete)
        return tree, resolution, analyze(tree, 'fixture.typ', resolution, resolution.spacer_proof)

    def candidate(self, source, packages=None):
        _, _, results = self.check(source, packages)
        planned = plan(source, [edit for result in results.values() for edit in result.edits])
        self.assertFalse(planned.deferred)
        return results, planned.candidate()

    def assert_stable(self, source, expected, rule):
        results, candidate = self.candidate(source)
        self.assertEqual(candidate, expected, results[rule].skips)
        self.assertTrue(results[rule].diagnostics)
        self.assertTrue(results[rule].edits)
        _, _, final = self.check(candidate)
        self.assertFalse(final[rule].diagnostics)
        self.assertFalse(any(result.edits for result in final.values()))

    def test_direct_exact_export_insertion_and_source_bytes(self):
        for body in (b'$ A &= B #footnote[Reason.] $',
                     b'$\n A=B \\\n C=D #footnote[Reason.]\n$',
                     '$\r\n α &= β #footnote[이유.]\r\n$'.encode()):
            with self.subTest(body=body):
                prefix = b'\xef\xbb\xbf' + IMPORT.replace(b'\n', b'\r\n')
                source = prefix + body
                self.assert_stable(source, source.replace(b'#footnote', b'#dots_space #footnote'), ALIGNED)
        self.assertEqual(digest(self.packages[0].source), MATH_BLOCKS_SOURCE_SHA256)

    def test_aliases_module_access_and_parentheses_prove_existing_value(self):
        for prefix, expression in (
            (IMPORT, b'dots_space'),
            (IMPORT, b'(dots_space)'),
            (IMPORT + b'#let saved = (dots_space)\n', b'saved'),
            (IMPORT.replace(b': dots_space', b': dots_space as spacer'), b'spacer'),
            (MODULE, b'm.dots_space'),
            (MODULE + b'#let alias = m\n', b'alias.dots_space'),
            (MODULE + b'#let spacer = (m.dots_space)\n', b'spacer'),
        ):
            with self.subTest(expression=expression):
                source = prefix + b'$ A = B #' + expression + b' \t#footnote[Reason.] $'
                self.assert_stable(source, prefix + b'$ A = B #footnote[Reason.] $', UNALIGNED)
                source = prefix + b'This.  #' + expression + b' #footnote[Reason.]'
                self.assert_stable(source, prefix + b'This.#footnote[Reason.]', PROSE)
                source = prefix + b'$ A &= B #' + expression + b' #footnote[Reason.] $'
                results, candidate = self.candidate(source)
                self.assertFalse(results[ALIGNED].diagnostics or results[ALIGNED].edits)
                self.assertEqual(candidate, source)

    def test_only_canonical_in_scope_name_can_be_inserted(self):
        for prefix in (MODULE, IMPORT.replace(b': dots_space', b': dots_space as spacer')):
            _, resolution, results = self.check(prefix + b'$ A &= B #footnote[X] $')
            self.assertFalse(resolution.spacer_proof.available)
            self.assertTrue(results[ALIGNED].diagnostics)
            self.assertFalse(results[ALIGNED].edits)
        for prefix in (MODULE + b'#let dots_space = m.dots_space\n',
                       IMPORT.replace(b': dots_space', b': dots_space as spacer')
                       + b'#let dots_space = (spacer)\n'):
            source = prefix + b'$ A &= B #footnote[X] $'
            self.assert_stable(source, source.replace(b'#footnote', b'#dots_space #footnote'), ALIGNED)

    def test_unreviewed_sources_versions_and_facades_never_prove_helper(self):
        stale = (replace(self.packages[0], source=self.packages[0].source + b'\n'),)
        forged = (PackageSource('@local/math-blocks:0.1.0', 'other', self.packages[0].source),)
        for prefix, packages in (
            (IMPORT, ()), (IMPORT, stale),
            (IMPORT.replace(b'0.2.0', b'0.1.0'), self.packages),
            (IMPORT.replace(b'0.2.0', b'0.1.0'), forged),
            (IMPORT.replace(b'@local/math-blocks:0.2.0', b'@local/math-book:0.2.0'), self.packages),
            (b'#import "facade.typ": dots_space\n', self.packages),
            (b'#let dots_space = [$& wide dots.h.c thin$]\n', self.packages),
            (b'', self.packages),
        ):
            with self.subTest(prefix=prefix, packages=packages):
                for body, rule in ((b'$ A = B #dots_space #footnote[X] $', UNALIGNED),
                                   (b'$ A &= B #footnote[X] $', ALIGNED),
                                   (b'This.#dots_space #footnote[X]', PROSE)):
                    _, resolution, results = self.check(prefix + body, packages)
                    self.assertFalse(resolution.spacer_proof.existing or resolution.spacer_proof.available)
                    self.assertFalse(results[rule].edits)

    def test_shadowing_mutation_destructuring_and_wildcards_invalidate(self):
        for change in (
            b'#let dots_space = []\n', b'#let (dots_space, other) = ([], 1)\n',
            b'#{dots_space = []}\n', b'#{dots_space += []}\n',
            b'#let other = 0\n#{(dots_space, other) = ([], 1)}\n',
            b'#if maybe { dots_space = [] }\n',
            b'#for x in (1,) { dots_space = [] }\n',
            b'#while maybe { dots_space = [] }\n',
            b'#import "unknown.typ": *\n', b'#import "unknown.typ": dots_space\n',
        ):
            with self.subTest(change=change):
                source = IMPORT + change + b'$ A = B #dots_space #footnote[X] $'
                tree, resolution, results = self.check(source)
                last = max(i for i, node in enumerate(tree.nodes)
                           if node.kind == 'Ident' and source[node.start:node.end] == b'dots_space')
                self.assertNotIn(last, resolution.spacer_proof.existing)
                self.assertFalse(resolution.spacer_proof.available)
                self.assertFalse(results[UNALIGNED].edits)
        source = MODULE + b'#{m.dots_space = []}\n$ A=B #m.dots_space #footnote[X] $'
        tree, resolution, results = self.check(source)
        self.assertFalse(results[UNALIGNED].edits)
        self.assertNotIn(max(i for i, n in enumerate(tree.nodes) if n.kind == 'FieldAccess'),
                         resolution.spacer_proof.existing)

    def test_source_order_immutable_alias_and_local_shadowing(self):
        source = IMPORT + b'#let saved = dots_space\n#{dots_space = []}\n#saved'
        tree, resolution, _ = self.check(source)
        self.assertIn(len(tree.nodes) - 1, resolution.spacer_proof.existing)
        source = IMPORT + b'$ A &= B #footnote[X] $\n#let dots_space = []'
        self.assertTrue(self.check(source)[1].spacer_proof.available)
        source = IMPORT + b'#let local = { let dots_space = []; dots_space }\n$ A &= B #footnote[X] $'
        self.assert_stable(source, source.replace(b'#footnote', b'#dots_space #footnote'), ALIGNED)

    def test_partial_wildcard_never_proves_all_exports_or_builtin_footnote(self):
        source = b'#import "@local/math-blocks:0.2.0": *\n$ A = B #dots_space #footnote[X] $'
        tree, resolution, results = self.check(source)
        self.assertTrue(resolution.spacer_proof.existing)
        self.assertFalse(resolution.spacer_proof.available)
        self.assertTrue(any('partial export namespace' in skip for skip in resolution.skips))
        self.assertFalse(any(result.diagnostics or result.edits for result in results.values()))
        call = next(i for i, node in enumerate(tree.nodes) if node.kind == 'FuncCall')
        self.assertIsNone(resolution.binding_for(call))

    def test_call_result_and_unresolved_preceding_expression_are_not_values(self):
        for expression in (b'dots_space()', b'unknown', b'facade.dots_space', b'helper()'):
            source = IMPORT + b'$ A &= B #' + expression + b' #footnote[X] $'
            tree, resolution, results = self.check(source)
            for i, node in enumerate(tree.nodes):
                if node.kind == 'FuncCall':
                    self.assertNotIn(i, resolution.spacer_proof.existing)
            self.assertFalse(results[ALIGNED].diagnostics or results[ALIGNED].edits)
            self.assertTrue(any('Unresolved possible spacer' in s for s in results[ALIGNED].skips))

    def test_deferred_nested_unknown_output_and_false_footnote_are_not_fixed(self):
        for body in (
            b'#let f() = [#dots_space #footnote[X]]',
            b'#let f() = { import "@local/math-blocks:0.2.0": dots_space; [#dots_space #footnote[X]] }',
            b'#context [$ A &= B #footnote[X] $]',
            b'#block[$ A &= B #footnote[X] $]',
            b'#show: unknown\n$ A &= B #footnote[X] $',
            b'#unknown[]\n$ A &= B #footnote[X] $',
            b'#let footnote(body) = body\n$ A &= B #footnote[X] $',
            b'$x #dots_space #footnote[X]$',
        ):
            with self.subTest(body=body):
                tree, resolution, results = self.check(IMPORT + body)
                self.assertFalse(any(result.edits for result in results.values()))
                for i in resolution.spacer_proof.existing | resolution.spacer_proof.available:
                    self.assertFalse(resolution.facts[i].deferred)
        # The theme-specific deferred module-capture exception cannot extend
        # spacer proofs even though a module field has known package identity.
        _, resolution, _ = self.check(MODULE + b'#let f() = [#m.dots_space #footnote[X]]')
        self.assertFalse(resolution.spacer_proof.existing or resolution.spacer_proof.available)

    def test_alignment_uses_outer_tokens_and_intervening_comments_are_preserved(self):
        source = IMPORT + b'$ A=B #dots_space #footnote[$a & b$] $'
        self.assert_stable(source, source.replace(b'#dots_space ', b''), UNALIGNED)
        for body in (b'$ A=B\n C=D #footnote[X] $', b'$ A=B #footnote[$a & b$] $'):
            self.assertFalse(self.check(IMPORT + body)[2][ALIGNED].diagnostics)
        source = IMPORT + b'$ A=B #dots_space /* retained */ #footnote[X] $'
        self.assertEqual(self.candidate(source)[1], source)

    def test_suppression_and_stale_evidence(self):
        source = (IMPORT + b'// typst-lint: ignore-next pde.aligned-footnote-spacer -- intentional\n'
                  b'$ A &= B #footnote[X] $\n$ C &= D #footnote[Y] $')
        tree, resolution, results = self.check(source)
        self.assertEqual(len(results[ALIGNED].suppressed), 1)
        self.assertEqual(len(results[ALIGNED].diagnostics), 1)
        self.assertEqual(len(results[ALIGNED].edits), 1)
        stale_tree = parse(source + b' ')
        stale_results = analyze(stale_tree, 'x', resolve(stale_tree, packages=self.packages), resolution.spacer_proof)
        self.assertTrue(all(not r.edits and 'Stale or invalid spacer proof' in r.skips for r in stale_results.values()))

    def test_ambiguous_prose_punctuation_remains_diagnostic_without_guess(self):
        for body in (b'This;#dots_space #footnote[X]', b'This #dots_space #footnote[X].'):
            _, _, results = self.check(IMPORT + body)
            self.assertTrue(results[PROSE].diagnostics and results[PUNCT].diagnostics)
            self.assertFalse(results[PROSE].edits or results[PUNCT].edits)


if __name__ == '__main__':
    unittest.main()
