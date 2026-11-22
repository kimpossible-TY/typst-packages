"""Conditional nested paragraph capitalization is a source-only proposal."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import document_conditional_capitalization as capitalization
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE, parse
from typst_lint import digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalCapitalizationTests(unittest.TestCase):
    def preview(self, source):
        return capitalization.preview(parse(source), 'main.typ')

    def candidate(self, source):
        result = self.preview(source)
        self.assertIsNotNone(result)
        self.assertEqual(result['verification'], 'conditional-capitalization-syntax-directives-and-reanalysis', result)
        self.assertFalse(result['execution_proved'] or result['runtime_binding_proved']
                         or result['render_equivalence_proved'] or result['edit_permission']
                         or result['compiler_permission'])
        edits = [Edit(row['start'], row['end'], row['replacement'].encode()) for row in result['edits']]
        candidate = plan(source, edits).candidate()
        self.assertEqual(result['baseline_sha256'], digest(source))
        self.assertEqual(result['candidate_sha256'], digest(candidate))
        self.assertIsNone(self.preview(candidate))
        return result, candidate

    def test_exact_ascii_changes_with_unicode_and_crlf(self):
        for newline in (b'\n', b'\r\n'):
            source = '#block[한글'.encode() + newline + b'#paragraph_tab' + newline + b'therefore x holds.]'
            result, candidate = self.candidate(source)
            self.assertEqual(len(result['edits']), 1)
            self.assertEqual(candidate, source.replace(b'therefore', b'Therefore'))

    def test_multiple_independent_markers(self):
        source = b'#block[\n#paragraph_tab\ntherefore x.\n\n#paragraph_tab\nhence y.]'
        result, candidate = self.candidate(source)
        self.assertEqual(len(result['edits']), 2)
        self.assertIn(b'Therefore x.', candidate)
        self.assertIn(b'Hence y.', candidate)

    def test_reasoned_suppression_is_preserved(self):
        source = (b'#block[\n// typst-lint: ignore-next pde.paragraph-capitalization -- intended\n'
                  b'#paragraph_tab\ntherefore x.\n\n#paragraph_tab\nhence y.]')
        result, candidate = self.candidate(source)
        self.assertEqual(len(result['edits']), 1)
        self.assertIn(b'therefore x.', candidate)
        self.assertIn(b'Hence y.', candidate)

    def test_unknown_and_nonprose_starters_are_not_changed(self):
        for text in (b'x is a variable.', b'iPhone is a name.', b'$x$ is positive.',
                     b'customword is outside the vocabulary.', '한글 문장.'.encode(),
                     b'Therefore already uppercase.'):
            source = b'#block[\n#paragraph_tab\n' + text + b']'
            with self.subTest(text=text):
                result = self.preview(source)
                self.assertTrue(result is None or not result['edits'], result)

    def test_cross_rule_change_and_parse_failure_withhold(self):
        source = b'#block[\n#paragraph_tab\ntherefore x.]'
        original = capitalization.continuation_rules.analyze_conditional
        def changed(tree, *args, **kwargs):
            result = original(tree, *args, **kwargs)
            if b'Therefore' in tree.source:
                from typst_lint import Diagnostic
                finding = Diagnostic('pde.continuation-line', 'warning', 'main.typ', 1, 1,
                                     'Fixture finding', 'fixture')
                return replace(result, diagnostics=(finding,))
            return result
        with patch.object(capitalization.continuation_rules, 'analyze_conditional', side_effect=changed):
            result = self.preview(source)
        self.assertEqual(result['verification'], 'withheld')
        self.assertFalse(result['edits'])
        with patch.object(capitalization, 'parse', side_effect=ValueError('fixture parse failure')):
            result = self.preview(source)
        self.assertEqual(result['verification'], 'withheld')
        self.assertFalse(result['edits'])
