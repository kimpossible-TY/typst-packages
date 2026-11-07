"""Actual parser and exact repository package snapshots; no evaluation."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
from book_provenance import EntrypointSnapshot
from book_rules import SINGLE, RESET, analyze
from package_loader import load_packages
from static_resolver import MATH_BOOK, MATH_BLOCKS, PackageSource, resolve
from syntax_client import EXECUTABLE, parse

IMPORT = (b'#import "@local/math-book:0.2.0": apply-math-book\n'
          b'#import "@local/math-blocks:0.2.0": apply-math-block-reset\n')
BOOK = b'#show: apply-math-book.with(title: "Notes")\n'
RESET_SHOW = b'#show: apply-math-block-reset\n'


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class BookRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packages = load_packages(REPO, (MATH_BOOK, MATH_BLOCKS)).sources

    def check(self, source, *, packages=None, path='main.typ', entrypoint=True):
        tree = parse(source)
        self.assertFalse(tree.nodes[0].errors, source)
        resolution = resolve(tree, packages=self.packages if packages is None else packages)
        context = EntrypointSnapshot(path, source) if entrypoint is True else entrypoint
        return tree, resolution, analyze(tree, path, resolution, entrypoint=context)

    def test_duplicates_and_enabled_reset_are_diagnostics_only(self):
        _, resolution, result = self.check(IMPORT + BOOK + BOOK + RESET_SHOW)
        self.assertEqual(len(resolution.book_proof.applications), 3)
        self.assertEqual(len(result[SINGLE].diagnostics), 1)
        self.assertEqual(len(result[RESET].diagnostics), 1)
        self.assertFalse(any(r.edits for r in result.values()))
        self.assertTrue(any('fix_unavailable' in s for s in result[RESET].skips))

    def test_one_owner_and_explicit_transfer(self):
        for book in (BOOK, b'#show: apply-math-book\n', b'#show: apply-math-book.with()\n'):
            _, _, result = self.check(IMPORT + book)
            self.assertFalse(any(r.diagnostics for r in result.values()))
        for enabled, count in ((b'true', 1), (b'false', 0)):
            _, _, result = self.check(IMPORT + b'#show: apply-math-book.with(reset-math-blocks: '
                                      + enabled + b')\n' + RESET_SHOW)
            self.assertEqual(len(result[RESET].diagnostics), count)

    def test_named_renames_module_fields_parentheses_and_value_aliases(self):
        cases = (
            (b'#import "@local/math-book:0.2.0": apply-math-book as b\n'
             b'#import "@local/math-blocks:0.2.0": apply-math-block-reset as r\n',
             b'#show: (b).with(title: "Notes")\n#show: (b)\n#show: (r)\n'),
            (b'#import "@local/math-book:0.2.0" as b\n'
             b'#import "@local/math-blocks:0.2.0" as m\n',
             b'#let owner = (b.apply-math-book)\n#let reset = m.apply-math-block-reset\n'
             b'#show: owner.with(title: "Notes")\n#show: b.apply-math-book\n#show: reset\n'),
        )
        for prefix, body in cases:
            with self.subTest(body=body):
                _, _, result = self.check(prefix + body)
                self.assertEqual(len(result[SINGLE].diagnostics), 1)
                self.assertEqual(len(result[RESET].diagnostics), 1)

    def test_rebinding_does_not_uninstall_a_captured_show(self):
        _, resolution, result = self.check(IMPORT + BOOK + b'#let saved = apply-math-block-reset\n'
                                 b'#{apply-math-book = none; apply-math-block-reset = none}\n#show: saved\n')
        self.assertFalse(result[RESET].diagnostics)
        self.assertEqual(len(resolution.book_proof.applications), 2)
        self.assertTrue(any('intervening code or output' in s for s in result[RESET].skips))
        _, _, result = self.check(IMPORT + b'#let saved = apply-math-book\n'
                                 b'#{apply-math-book = none}\n#show: saved\n#show: saved\n')
        self.assertEqual(len(result[SINGLE].diagnostics), 1)

    def test_shadowing_and_mutation_never_invent_an_owner(self):
        for change in (
            b'#let apply-math-book(body) = body\n',
            b'#{apply-math-book = none}\n', b'#{apply-math-book += []}\n',
            b'#let (apply-math-book, x) = (none, 0)\n',
            b'#if maybe { apply-math-book = none }\n',
            b'#import "unknown.typ": *\n',
        ):
            with self.subTest(change=change):
                _, _, result = self.check(IMPORT + change + BOOK + BOOK + RESET_SHOW)
                self.assertFalse(any(r.diagnostics for r in result.values()))
        prefix = (b'#import "@local/math-book:0.2.0" as b\n'
                  b'#import "@local/math-blocks:0.2.0": apply-math-block-reset\n')
        _, _, result = self.check(prefix + b'#{b.apply-math-book = none}\n'
                                 b'#show: b.apply-math-book\n#show: b.apply-math-book\n' + RESET_SHOW)
        self.assertFalse(any(r.diagnostics for r in result.values()))

    def test_dynamic_customized_or_body_bound_options_are_not_transform_proofs(self):
        for expression in (
            b'apply-math-book.with(title: title)',
            b'apply-math-book.with(title: 17)', b'apply-math-book.with(size: "large")',
            b'apply-math-book.with(theme: 1)', b'apply-math-book.with(running-header: auto)',
            b'apply-math-book.with(size: 12pt)', b'apply-math-book.with(theme: none)',
            b'apply-math-book.with(reset-math-blocks: maybe)',
            b'apply-math-book.with(reset-math-blocks: "true")',
            b'apply-math-book.with(..options)', b'apply-math-book.with([Body])',
            b'apply-math-book.with(body: "Body")', b'apply-math-book.with(unknown: true)',
            b'apply-math-book.with(title: "A", title: "B")',
            b'apply-math-book.with(reset-math-blocks: true, reset-math-blocks: false)',
            b'apply-math-book.with(title: { apply-math-book = none; "X" })',
            b'factory()', b'apply-math-book.with().with()',
        ):
            with self.subTest(expression=expression):
                source = IMPORT + b'#show: ' + expression + b'\n#show: ' + expression + b'\n' + RESET_SHOW
                tree = parse(source)
                if tree.nodes[0].errors:
                    # The official parser rejects duplicate named keys before
                    # a transform proof can be issued.
                    result = analyze(tree, 'main.typ', resolve(tree, packages=self.packages),
                                     entrypoint=EntrypointSnapshot('main.typ', source))
                    self.assertTrue(all(r.parse_errors for r in result.values()))
                    self.assertFalse(any(r.diagnostics or r.edits for r in result.values()))
                    continue
                _, resolution, result = self.check(source)
                self.assertFalse(any(r.diagnostics for r in result.values()))
                self.assertFalse(any(a.binding.package == MATH_BOOK for a in resolution.book_proof.applications))

    def test_preconfigured_call_results_and_local_fake_with_are_unresolved(self):
        for binding in (b'#let owner = apply-math-book.with(title: "X")\n',
                        b'#let owner = (with: (..args) => apply-math-book)\n'):
            _, _, result = self.check(IMPORT + binding + b'#show: owner\n#show: owner\n' + RESET_SHOW)
            self.assertFalse(any(r.diagnostics for r in result.values()))

    def test_only_string_or_none_metadata_options_are_certified(self):
        for value in (b'"Notes"', b'none'):
            _, _, result = self.check(IMPORT + b'#show: apply-math-book.with(title: ' + value
                + b', author: ' + value + b', description: ' + value + b')\n' + RESET_SHOW)
            self.assertEqual(len(result[RESET].diagnostics), 1)

    def test_reset_with_empty_args_diagnoses_but_customized_forms_do_not(self):
        _, _, result = self.check(IMPORT + BOOK + b'#show: apply-math-block-reset.with()\n')
        self.assertEqual(len(result[RESET].diagnostics), 1)
        self.assertFalse(result[RESET].edits)
        for expression in (b'apply-math-block-reset.with(body: [])', b'apply-math-block-reset.with(..options)'):
            _, _, result = self.check(IMPORT + BOOK + b'#show: ' + expression + b'\n')
            self.assertFalse(result[RESET].diagnostics)

    def test_no_graph_or_deferred_or_missing_owner_claims(self):
        for body in (
            b'#if true { show: apply-math-book }\n',
            b'#let f() = { show: apply-math-book; [X] }\n',
            b'#context { show: apply-math-book; [X] }\n',
            b'#[#show: apply-math-book\nX]\n',
            b'#include "chapter.typ"\n', b'#let unused = apply-math-book\n',
        ):
            _, _, result = self.check(IMPORT + body + RESET_SHOW)
            self.assertFalse(any(r.diagnostics for r in result.values()))
        _, _, result = self.check(IMPORT + BOOK + b'#include "chapter.typ"\n')
        self.assertFalse(result[SINGLE].diagnostics)
        self.assertTrue(any('include graphs' in s for s in result[SINGLE].skips))

    def test_exact_paired_sources_and_explicit_named_root_requests_required(self):
        for packages in ((), self.packages[:1], self.packages[1:],
                         tuple(replace(p, source=p.source + b'\n') if p.import_path == MATH_BOOK else p
                               for p in self.packages),
                         tuple(replace(p, source=p.source + b'\n') if p.import_path == MATH_BLOCKS else p
                               for p in self.packages)):
            _, _, result = self.check(IMPORT + BOOK + BOOK + RESET_SHOW, packages=packages)
            self.assertFalse(any(r.diagnostics for r in result.values()))
        for prefix in (
            IMPORT.replace(b'@local/math-book:0.2.0', b'facade.typ'),
            IMPORT.replace(b'@local/math-blocks:0.2.0', b'@local/math-blocks:0.1.0'),
            IMPORT.replace(b': apply-math-block-reset', b': *'),
            IMPORT.replace(b': apply-math-book', b': *'),
            IMPORT.splitlines(keepends=True)[0],
        ):
            _, _, result = self.check(prefix + BOOK + BOOK + RESET_SHOW)
            self.assertFalse(any(r.diagnostics for r in result.values()))

    def test_existing_wildcard_uncertainty_is_not_broadened(self):
        prefix = IMPORT.replace(b': apply-math-block-reset', b': *')
        _, resolution, result = self.check(prefix + BOOK + BOOK + RESET_SHOW)
        self.assertFalse(any(r.diagnostics for r in result.values()))
        self.assertTrue(any('partial export namespace' in s for s in resolution.skips))

    def test_explicit_entrypoint_bytes_and_path_are_required(self):
        source = IMPORT + BOOK + BOOK + RESET_SHOW
        for context in (None, 'main.typ', EntrypointSnapshot('other.typ', source),
                        EntrypointSnapshot('main.typ', source + b' '),
                        EntrypointSnapshot('main.typ', bytearray(source))):
            _, _, result = self.check(source, entrypoint=context)
            self.assertFalse(any(r.diagnostics for r in result.values()))

    def test_suppression_unicode_and_crlf_positions(self):
        source = (b'\xef\xbb\xbf' + IMPORT + BOOK
                  + b'// typst-lint: ignore-next core.single-book-show -- intentional example\n'
                  + BOOK + RESET_SHOW)
        _, _, result = self.check(source.replace(b'\n', b'\r\n'))
        self.assertEqual(len(result[SINGLE].suppressed), 1)
        self.assertFalse(result[SINGLE].diagnostics)
        self.assertEqual((result[RESET].diagnostics[0].line, result[RESET].diagnostics[0].column), (6, 2))
        _, _, result = self.check(IMPORT + BOOK + '한글 '.encode() + BOOK + RESET_SHOW)
        self.assertEqual((result[SINGLE].diagnostics[0].line, result[SINGLE].diagnostics[0].column), (4, 5))
        _, _, result = self.check(IMPORT + BOOK
            + b'// typst-lint: ignore-next core.no-duplicate-book-reset -- intentional\n' + RESET_SHOW)
        self.assertEqual(len(result[RESET].suppressed), 1)
        self.assertFalse(result[RESET].diagnostics or result[RESET].edits)

    def test_order_comments_labels_and_other_transforms_never_get_an_edit(self):
        for body in (RESET_SHOW + BOOK, BOOK + b'// explain\n' + RESET_SHOW,
                     BOOK + RESET_SHOW.rstrip() + b' // explain\n',
                     BOOK + RESET_SHOW):
            _, _, result = self.check(IMPORT + body)
            self.assertEqual(len(result[RESET].diagnostics), 1)
            self.assertFalse(any(r.edits for r in result.values()))

    def test_intervening_unknown_transform_or_output_withholds_shared_body_claim(self):
        for gap in (b'#show: body => []\n', b'#show heading: it => it\n',
                    b'#unknown[]\n', b'Earlier content.\n', b'#let x = 0\n'):
            _, _, result = self.check(IMPORT + BOOK + gap + RESET_SHOW)
            self.assertFalse(result[RESET].diagnostics or result[RESET].edits)
            self.assertTrue(any('intervening code or output' in s for s in result[RESET].skips))

    def test_malformed_and_stale_proofs(self):
        tree = parse(b'#show: apply-math-book.with(')
        result = analyze(tree, 'main.typ', resolve(tree), entrypoint=EntrypointSnapshot('main.typ', tree.source))
        self.assertTrue(all(r.parse_errors for r in result.values()))
        tree, resolution, _ = self.check(IMPORT + BOOK + BOOK)
        context = EntrypointSnapshot('main.typ', tree.source)
        for stale in (replace(resolution, source_sha256='stale'),
                      replace(resolution, book_proof=replace(resolution.book_proof, source_sha256='stale')),
                      replace(resolution, book_proof=None)):
            result = analyze(tree, 'main.typ', stale, entrypoint=context)
            self.assertFalse(any(r.diagnostics or r.edits for r in result.values()))


if __name__ == '__main__':
    unittest.main()
