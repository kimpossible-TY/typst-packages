"""Actual pure facade graphs, exact edits, refusals and candidate reanalysis."""
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import plan
from import_graph import load_import_graph, rebase_graph
from import_rules import ORDER, REDUNDANT, analyze
from syntax_client import EXECUTABLE, parse

STYLES = b'#import "Styles/styles.typ": *\n'
FIGURES = b'#import "figures.typ": *\n'
COMPAT = b'#import "Styles/local_tags.typ": *\n'


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ImportRulesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='typst-import-rules-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.put('leaf.typ', b'#let tag = 1\n')
        self.put('Styles/styles.typ', b'#import "../leaf.typ": *\n')
        self.put('Styles/local_tags.typ', b'#import "../leaf.typ": *\n')
        self.put('Styles/mannot_utils.typ', b'#import "../leaf.typ": tag\n')
        self.put('figures.typ', b'#let diagram = 2\n')

    def put(self, path, source):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source)
        return target

    def run_source(self, source):
        self.put('main.typ', source)
        graph = load_import_graph(self.root, (('main.typ', source),))
        results = analyze(parse(source), 'main.typ', graph=graph)
        candidate = plan(source, (edit for result in results.values() for edit in result.edits)).candidate()
        return results, candidate, graph

    def assert_no_edits(self, source):
        result, candidate, _ = self.run_source(source)
        self.assertEqual(candidate, source)
        self.assertFalse(any(r.edits for r in result.values()))
        return result

    def test_order_and_redundancy_positive_exact_candidate_and_idempotence(self):
        source = FIGURES + STYLES + COMPAT + b'#tag + #diagram\n'
        result, candidate, graph = self.run_source(source)
        self.assertEqual(candidate, STYLES + FIGURES + b'#tag + #diagram\n')
        self.assertEqual(len(result[ORDER].diagnostics), 1)
        self.assertEqual(len(result[REDUNDANT].diagnostics), 1)
        final_graph = rebase_graph(graph, 'main.typ', source, candidate)
        final = analyze(parse(candidate), 'main.typ', graph=final_graph)
        self.assertFalse(any(r.diagnostics or r.edits for r in final.values()))
        self.assertEqual(final, analyze(parse(candidate), 'main.typ', graph=final_graph))
        self.assertEqual((self.root / 'main.typ').read_bytes(), source)

    def test_order_preserves_exact_statement_data_and_separator_slots(self):
        source = (b'  #import "figures.typ": diagram as d\r\n\r\n\t'
                  b'#import "Styles/styles.typ": tag as t\r\n#t + #d\r\n')
        expected = (b'  #import "Styles/styles.typ": tag as t\r\n\r\n\t'
                    b'#import "figures.typ": diagram as d\r\n#t + #d\r\n')
        result, candidate, _ = self.run_source(source)
        self.assertEqual(candidate, expected)
        self.assertTrue(result[ORDER].edits)

    def test_order_is_stable_within_category_and_does_not_require_missing_categories(self):
        self.put('nested/figures.typ', b'#let other = 3\n')
        extra = b'#import "nested/figures.typ": *\n'
        result, candidate, _ = self.run_source(FIGURES + extra + STYLES)
        self.assertEqual(candidate, STYLES + FIGURES + extra)
        for source in (STYLES, FIGURES, STYLES + FIGURES, b'Ordinary prose.\n'):
            with self.subTest(source=source):
                self.assertFalse(self.assert_no_edits(source)[ORDER].diagnostics)

    def test_facade_spelling_alone_does_not_establish_identity(self):
        for value in (b'#let unrelated = 1\n', b'Ordinary output\n', b'#import "missing.typ": *\n'):
            self.put('Styles/styles.typ', value)
            result = self.assert_no_edits(FIGURES + STYLES)
            self.assertFalse(result[ORDER].diagnostics)

    def test_compatibility_alone_and_missing_requested_bindings_are_accepted(self):
        result = self.assert_no_edits(COMPAT)
        self.assertFalse(result[REDUNDANT].diagnostics)
        self.put('leaf.typ', b'#let tag = 1\n#let other = 2\n')
        self.put('Styles/styles.typ', b'#import "../leaf.typ": tag\n')
        result = self.assert_no_edits(STYLES + b'#import "Styles/local_tags.typ": other\n')
        self.assertFalse(result[REDUNDANT].diagnostics)

    def test_same_spelling_and_same_literal_value_are_not_identity(self):
        self.put('different.typ', b'#let tag = 1\n')
        self.put('Styles/local_tags.typ', b'#import "../different.typ": *\n')
        result = self.assert_no_edits(STYLES + COMPAT)
        self.assertFalse(result[REDUNDANT].diagnostics)

    def test_renamed_matching_bindings_and_repeated_compat_imports(self):
        source = (b'#import "Styles/styles.typ": tag as local\n'
                  b'#import "Styles/local_tags.typ": tag as local\n'
                  b'#import "Styles/mannot_utils.typ": tag as local\n#local\n')
        result, candidate, _ = self.run_source(source)
        self.assertEqual(candidate, b'#import "Styles/styles.typ": tag as local\n#local\n')
        self.assertEqual(len(result[REDUNDANT].diagnostics), 2)
        source = STYLES + b'#import "Styles/local_tags.typ": tag as different\n#different\n'
        self.assertFalse(self.assert_no_edits(source)[REDUNDANT].diagnostics)

    def test_compat_namespace_alias_is_retained(self):
        source = STYLES + b'#import "Styles/local_tags.typ" as compat\n#compat.tag\n'
        result = self.assert_no_edits(source)
        self.assertFalse(result[REDUNDANT].diagnostics)

    def test_redundant_import_deletion_preserves_other_gaps_and_eof(self):
        for tail in (b'', b'\r\n', b'\r\n\r\n#tag\r\n'):
            source = STYLES.replace(b'\n', b'\r\n') + b'  ' + COMPAT.rstrip(b'\n') + tail
            _, candidate, _ = self.run_source(source)
            expected_tail = b'' if tail in (b'', b'\r\n') else b'\r\n#tag\r\n'
            self.assertEqual(candidate, STYLES.replace(b'\n', b'\r\n') + expected_tail)

    def test_unknown_dynamic_initializers_cycles_and_shadowing_never_justify_edits(self):
        for value in (b'#let tag = counter("x")\n', b'#include "../leaf.typ"\n',
                      b'#let tag = 1\n#let tag = 2\n', b'#import ("../leaf" + ".typ"): *\n',
                      b'#import "styles.typ": *\n'):
            self.put('Styles/styles.typ', value)
            result = self.assert_no_edits(FIGURES + STYLES + COMPAT)
            self.assertFalse(result[REDUNDANT].diagnostics)
        self.put('Styles/styles.typ', b'#import "../leaf.typ": *\n')
        for prefix in (b'#let prior = 1\n', b'#import "missing.typ": *\n', b'#include "leaf.typ"\n'):
            result = self.assert_no_edits(prefix + FIGURES + STYLES + COMPAT)
            self.assertTrue(result[ORDER].diagnostics)

    def test_actual_namespace_collision_blocks_reorder(self):
        self.put('figures.typ', b'#let tag = 2\n')
        result = self.assert_no_edits(FIGURES + STYLES)
        self.assertTrue(result[ORDER].diagnostics)
        self.assertTrue(any('collide' in reason for reason in result[ORDER].skips))
        result = self.assert_no_edits(STYLES + FIGURES + COMPAT)
        self.assertFalse(result[REDUNDANT].diagnostics)

    def test_relative_import_dependency_blocks_reorder_even_with_disjoint_aliases(self):
        self.put('figures.typ', b'#import "Styles/styles.typ": tag as diagram\n')
        result = self.assert_no_edits(FIGURES + STYLES)
        self.assertTrue(result[ORDER].diagnostics)
        self.assertTrue(any('depend on each other' in reason for reason in result[ORDER].skips))

    def test_unrelated_import_and_executable_construct_are_not_crossed(self):
        self.put('other.typ', b'#let other = 3\n')
        for barrier in (b'#import "other.typ": *\n', b'#let executed = 1\n', b'Visible content.\n'):
            result = self.assert_no_edits(FIGURES + barrier + STYLES)
            self.assertTrue(result[ORDER].diagnostics)
        self.assertFalse(self.assert_no_edits(STYLES + b'#let tag = 4\n' + COMPAT)[REDUNDANT].diagnostics)

    def test_comments_preserve_diagnostics_but_block_ambiguous_attachments(self):
        for source in (b'// Header\n' + FIGURES + STYLES,
                       FIGURES + b'// Between\n' + STYLES,
                       STYLES + b'// Compatibility\n' + COMPAT,
                       STYLES + COMPAT.rstrip(b'\n') + b' // trailing\n'):
            result = self.assert_no_edits(source)
            self.assertTrue(any(r.diagnostics for r in result.values()))
            self.assertTrue(any('Comment/directive' in reason for r in result.values() for reason in r.skips))

    def test_directives_suppress_exact_import_and_never_move_or_reassign(self):
        directive = b'// typst-lint: ignore-next pde.import-order -- intentional\n'
        result = self.assert_no_edits(directive + FIGURES + STYLES)
        self.assertFalse(result[ORDER].diagnostics)
        self.assertEqual(len(result[ORDER].suppressed), 1)
        directive = b'// typst-lint: ignore-next pde.no-redundant-facade-import -- public compatibility\n'
        result = self.assert_no_edits(STYLES + directive + COMPAT)
        self.assertFalse(result[REDUNDANT].diagnostics)
        self.assertEqual(len(result[REDUNDANT].suppressed), 1)

    def test_annotation_order_is_diagnostic_only_without_package_effect_proof(self):
        annotation = b'#import "@preview/mannot:0.4.0": *\n'
        result = self.assert_no_edits(annotation + FIGURES + STYLES)
        self.assertTrue(result[ORDER].diagnostics)
        self.assertTrue(any('module initialization' in reason for reason in result[ORDER].skips))
        self.assertFalse(self.assert_no_edits(STYLES + FIGURES + annotation)[ORDER].diagnostics)

    def test_nested_and_inline_imports_are_not_rewritten(self):
        result = self.assert_no_edits(b'#{\nimport "figures.typ": *\nimport "Styles/styles.typ": *\n}\n')
        self.assertTrue(any('Nested' in reason for reason in result[ORDER].skips))
        result = self.assert_no_edits(FIGURES.rstrip(b'\n') + b'; ' + STYLES)
        self.assertTrue(result[ORDER].diagnostics)

    def test_graph_bytes_and_path_must_match_and_not_extend_selection(self):
        source = FIGURES + STYLES
        _, _, graph = self.run_source(source)
        for tree, path, context in ((parse(source + b' '), 'main.typ', graph),
                                    (parse(source), 'else.typ', graph),
                                    (parse(source), 'main.typ', None),
                                    (parse((self.root / 'Styles/styles.typ').read_bytes()), 'Styles/styles.typ', graph)):
            result = analyze(tree, path, graph=context)
            self.assertFalse(any(r.edits or r.diagnostics for r in result.values()))
            self.assertTrue(all(r.skips for r in result.values()))

    def test_candidate_cannot_claim_success_after_losing_graph_support(self):
        source = FIGURES + STYLES
        _, _, graph = self.run_source(source)
        with patch('import_rules.rebase_graph', return_value=graph):
            result = analyze(parse(source), 'main.typ', graph=graph)
        self.assertFalse(any(r.edits for r in result.values()))
        self.assertTrue(all(any('lost its source-bound' in reason for reason in r.skips) for r in result.values()))

    def test_malformed_source_and_invalid_candidate_reanalysis_refuse(self):
        result, candidate, _ = self.run_source(FIGURES + STYLES + b'#let =')
        self.assertTrue(all(r.parse_errors for r in result.values()))
        self.assertFalse(any(r.edits for r in result.values()))
        source = FIGURES + STYLES
        _, _, context = self.run_source(source)
        real_parse = parse
        with patch('import_rules.parse', side_effect=lambda *args: real_parse(b'#let =')):
            result = analyze(parse(source), 'main.typ', graph=context)
        self.assertFalse(any(r.edits for r in result.values()))
        self.assertTrue(all(any('did not parse' in reason for reason in r.skips) for r in result.values()))


if __name__ == '__main__':
    unittest.main()
