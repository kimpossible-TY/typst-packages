"""Independent previews over real parser trees; consumers are never modified."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import document_gap_preview
from document_display import RULE_ID, analyze, preview_gap_edits
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE, parse
from typst_lint import digest, load_rules


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
