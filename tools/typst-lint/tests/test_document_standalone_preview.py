"""Standalone source previews against actual pinned syntax trees."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_standalone_preview
from document_display import STANDALONE, analyze_all
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE, parse
from typst_lint import digest, load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class DocumentStandalonePreviewTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_rules()[0]['rules']

    def test_nested_both_sides_and_label_preserve_indentation_and_crlf(self):
        source = b'#wrap[\r\n  Thus $ A=B $ <label> hence.\r\n]'
        tree = parse(source)
        self.assertFalse(analyze_all(tree, 'x.typ')[STANDALONE].edits)
        result = document_standalone_preview.preview(tree, 'x.typ', self.catalog)
        self.assertEqual(result['verification'], 'parse-syntax-directives-and-display-reanalysis')
        edits = tuple(Edit(e['start'], e['end'], e['replacement'].encode()) for e in result['edits'])
        self.assertEqual(len(edits), 2)
        candidate = plan(source, edits).candidate()
        self.assertEqual(candidate, b'#wrap[\r\n  Thus\r\n  $ A=B $ <label>\r\n  hence.\r\n]')
        self.assertEqual(result['candidate_sha256'], digest(candidate))
        self.assertIsNone(document_standalone_preview.preview(parse(candidate), 'x.typ', self.catalog))

    def test_suppression_preserves_equation(self):
        source = (b'// typst-lint: ignore-next pde.standalone-display -- intentional\n'
                  b'$ A=B $ hence.\n')
        self.assertIsNone(document_standalone_preview.preview(parse(source), 'x.typ', self.catalog))

    def test_changed_syntax_is_withheld(self):
        source = b'Thus $ A=B $ hence.\n'
        altered = parse(b'Thus\n$ A=C $\nhence.\n')
        with patch.object(document_standalone_preview, 'parse', return_value=altered):
            result = document_standalone_preview.preview(parse(source), 'x.typ', self.catalog)
        self.assertEqual(result['verification'], 'withheld')
        self.assertFalse(result['edits'])
        self.assertIn('syntax', result['reason'])

    def test_changed_directive_targets_are_withheld(self):
        source = b'Thus $ A=B $ hence.\n'
        with patch.object(document_standalone_preview, '_scopes', side_effect=[('before',), ('after',)]):
            result = document_standalone_preview.preview(parse(source), 'x.typ', self.catalog)
        self.assertEqual(result['verification'], 'withheld')
        self.assertIn('directive target', result['reason'])

    def test_newly_standalone_display_exposes_gap_for_a_later_pass(self):
        source = b'Thus $ A=B $\n\nHence.\n'
        result = document_standalone_preview.preview(parse(source), 'x.typ', self.catalog)
        self.assertEqual(result['verification'], 'parse-syntax-directives-and-display-reanalysis')
        self.assertEqual(result['candidate_gap_diagnostics'], 1)
