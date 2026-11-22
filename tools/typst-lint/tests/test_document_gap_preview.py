"""Independent previews over real parser trees; consumers are never modified."""
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import document_gap_preview
from document_display import RULE_ID, analyze, preview_gap_edits
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE, parse
from suppression import Scope
from typst_lint import SafetyError, digest, load_rules


class ScopeMappingTests(unittest.TestCase):
    def test_index_matches_original_for_overlapping_unsorted_and_insertion_edits(self):
        rng = random.Random(4813)
        explicit = [
            (Edit(2, 8, b''), Edit(4, 5, b'a'), Edit(8, 8, b'xy')),
            (Edit(3, 3, b'xy'), Edit(3, 3, b'z'), Edit(1, 6, b'')),
            (Edit(7, 2, b''), Edit(2, 2, b''), Edit(6, 6, b'a')),
        ]
        cases = explicit + [tuple(Edit(rng.randrange(-2, 15), rng.randrange(-2, 15),
                                      b'x' * rng.randrange(4)) for _ in range(rng.randrange(10)))
                            for _ in range(500)]
        for edits in cases:
            index = document_gap_preview._MappedEdits(edits)
            for position in range(-3, 18):
                try:
                    expected = document_gap_preview._mapped(position, edits)
                except SafetyError as error:
                    with self.subTest(edits=edits, position=position), self.assertRaisesRegex(
                            SafetyError, str(error)):
                        index.mapped(position)
                else:
                    with self.subTest(edits=edits, position=position):
                        self.assertEqual(index.mapped(position), expected)

    def test_scopes_preserve_complete_mapping_and_custom_iteration_behavior(self):
        scopes = (Scope(frozenset({'rule'}), 'reason', 3, 8, 1),
                  Scope(frozenset({'other'}), 'why', 12, 14, 10))
        edits = (Edit(8, 8, b'xy'), Edit(0, 1, b''), Edit(6, 7, b'z'))
        expected = tuple((scope.rules, scope.reason,
                          document_gap_preview._mapped(scope.start, edits),
                          document_gap_preview._mapped(scope.end, edits),
                          document_gap_preview._mapped(scope.directive_start, edits)) for scope in scopes)
        with patch.object(document_gap_preview, 'resolve', return_value=scopes):
            self.assertEqual(document_gap_preview._scopes(None, [], edits), expected)
            # Iterator inputs historically get consumed by the first _mapped
            # call, including its own first traversal. Keep that behavior.
            actual = document_gap_preview._scopes(None, [], iter(edits))
        self.assertEqual(actual, tuple((scope.rules, scope.reason, scope.start,
                                       scope.end, scope.directive_start) for scope in scopes))

    def test_large_independent_scopes_are_indexed_once(self):
        edits = tuple(Edit(i * 3, i * 3 + 1, b'') for i in range(6000))
        scopes = tuple(Scope(frozenset({'rule'}), 'reason', i * 3, i * 3 + 1, i * 3 + 2)
                       for i in range(6000))
        with patch.object(document_gap_preview, 'resolve', return_value=scopes), \
             patch.object(document_gap_preview, '_mapped', side_effect=AssertionError('quadratic fallback')):
            actual = document_gap_preview._scopes(None, [], edits)
        self.assertEqual(len(actual), 6000)
        self.assertEqual(actual[0][2:], (0, 0, 1))
        self.assertEqual(actual[-1][2:], (11998, 11998, 11999))


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class DocumentGapPreviewTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_rules()[0]['rules']

    def test_nested_label_and_crlf_preview_replays_and_is_idempotent(self):
        original = b'#outer[\r\n$ A=B $ <label>\r\n \t\r\n  Hence here.\r\n]'
        tree = parse(original)
        self.assertEqual(analyze(tree, 'a.typ').edits, ())
        report = document_gap_preview.preview(tree, 'a.typ', self.catalog)
        self.assertEqual(report['verification'], 'parse-syntax-directives-and-display-reanalysis')
        edit, = report['edits']
        self.assertEqual((edit['rule_id'], edit['replacement']), (RULE_ID, '\r\n  '))
        candidate = plan(original, (Edit(edit['start'], edit['end'], b'\r\n  '),)).candidate()
        self.assertEqual(candidate, b'#outer[\r\n$ A=B $ <label>\r\n  Hence here.\r\n]')
        self.assertEqual(report['candidate_sha256'], digest(candidate))
        self.assertIsNone(document_gap_preview.preview(parse(candidate), 'a.typ', self.catalog))

    def test_suppressed_gap_is_not_proposed_and_other_directives_survive(self):
        source = (b'// typst-lint: ignore-next pde.display-continuation-gap -- intentional\n'
                  b'$ A=B $\n\nFirst.\n\n$ C=D $\n\nSecond.\n'
                  b'// typst-lint: ignore-next core.parenthesized-scripts -- example\n$x^a$\n')
        tree = parse(source)
        result = preview_gap_edits(tree, 'a.typ', catalog=self.catalog)
        self.assertEqual((len(result.diagnostics), len(result.suppressed), len(result.edits)), (1, 1, 1))
        report = document_gap_preview.preview(tree, 'a.typ', self.catalog)
        self.assertEqual(len(report['edits']), 1)
        self.assertEqual(report['verification'], 'parse-syntax-directives-and-display-reanalysis')

    def test_candidate_with_changed_syntax_is_withheld(self):
        source = b'$ A=B $\n\nHence.\n'
        tree = parse(source)
        altered = parse(b'$ A=C $\nHence.\n')
        with patch.object(document_gap_preview, 'parse', return_value=altered):
            report = document_gap_preview.preview(tree, 'a.typ', self.catalog)
        self.assertEqual(report['verification'], 'withheld')
        self.assertFalse(report['edits'])
        self.assertIn('syntax', report['reason'])

    def test_changed_directive_targets_are_withheld(self):
        source = (b'$ A=B $\n\nHence.\n'
                  b'// typst-lint: ignore-next core.parenthesized-scripts -- example\n$x^a$\n')
        with patch.object(document_gap_preview, '_scopes', side_effect=[('before',), ('after',)]):
            report = document_gap_preview.preview(parse(source), 'a.typ', self.catalog)
        self.assertEqual(report['verification'], 'withheld')
        self.assertIn('directive target', report['reason'])

    def test_unknown_neighbor_does_not_create_a_preview(self):
        tree = parse(b'$ A=B $\n\n#text[Following.]\n')
        self.assertIsNone(document_gap_preview.preview(tree, 'a.typ', self.catalog))
