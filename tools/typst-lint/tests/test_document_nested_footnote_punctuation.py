"""Read-only punctuation movement for a narrow nested prose source pattern."""
from pathlib import Path
from dataclasses import replace
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_nested_footnote_punctuation as nested
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE, parse
from typst_lint import digest, load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class NestedFootnotePunctuationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_rules()[0]['rules']

    def candidate(self, source):
        result = nested.preview(parse(source), 'main.typ', self.catalog)
        self.assertIsNotNone(result)
        self.assertEqual(result['verification'],
                         'conditional-nested-punctuation-syntax-directives-and-footnote-reanalysis', result)
        self.assertFalse(result['runtime_binding_proved'] or result['render_equivalence_proved']
                         or result['edit_permission'])
        edits = [Edit(row['start'], row['end'], row['replacement'].encode()) for row in result['edits']]
        candidate = plan(source, edits).candidate()
        self.assertEqual(result['baseline_sha256'], digest(source))
        self.assertEqual(result['candidate_sha256'], digest(candidate))
        self.assertIsNone(nested.preview(parse(candidate), 'main.typ', self.catalog))
        return candidate

    def test_existing_period_and_comma_move_with_literal_footnote(self):
        for source, expected in (
                (b'#block[Text #footnote[Reason.]. Next.]', b'#block[Text.#footnote[Reason.] Next.]'),
                (b'#block[Text \t#footnote[Reason.], next.]', b'#block[Text,#footnote[Reason.] next.]'),
                ('#block[한글 #footnote[이유].\r\n다음.]'.encode(),
                 '#block[한글.#footnote[이유]\r\n다음.]'.encode())):
            with self.subTest(source=source):
                self.assertEqual(self.candidate(source), expected)

    def test_multiple_edits_keep_other_diagnostics(self):
        source = b'#block[First #footnote[A]. Next #footnote[B], then. Other #footnote[C]]'
        expected = b'#block[First.#footnote[A] Next,#footnote[B] then. Other #footnote[C]]'
        self.assertEqual(self.candidate(source), expected)

    def test_unsupported_or_ambiguous_cases_are_unchanged(self):
        cases = (
            b'Root #footnote[Reason.].',
            b'#block[$ A #footnote[Reason.]. $]',
            b'#block[Text\n#footnote[Reason.].]',
            b'#block[Text /*comment*/ #footnote[Reason.].]',
            b'#block[Text #footnote[#strong[Reason.]].]',
            b'#block[Text #footnote[Reason.]..]',
            b'#block[Text #footnote[Reason.];]',
            b'#block[Text. #footnote[Reason.].]',
            b'#block[Text #footnote[Reason.].Next]',
            b'#let footnote(x) = x\n#block[Text #footnote[Reason.].]',
            b'// typst-lint: ignore-next pde.prose-footnote-punctuation -- intentional\n'
            b'#block[Text #footnote[Reason.].]',
        )
        for source in cases:
            with self.subTest(source=source):
                self.assertIsNone(nested.preview(parse(source), 'main.typ', self.catalog))

    def test_parse_error_or_cross_rule_change_withholds_every_edit(self):
        source = b'#block[Text #footnote[Reason.].]'
        with patch.object(nested, 'parse', side_effect=ValueError('fixture parser failure')):
            result = nested.preview(parse(source), 'main.typ', self.catalog)
        self.assertEqual(result['verification'], 'withheld')
        self.assertFalse(result['edits'] or result['edit_permission'])
        self.assertIn('fixture parser failure', result['reason'])
        source = b'#block[Text #footnote[Reason.]. Other #footnote[Other.]]'
        original = nested.footnote_rules.analyze_conditional_nested
        def alter_other_finding(tree, *args, **kwargs):
            findings = original(tree, *args, **kwargs)
            if b'Text.#footnote' in tree.source:
                rule = findings[nested.footnote_rules.PUNCT]
                item = rule.diagnostics[0]
                findings[nested.footnote_rules.PUNCT] = replace(
                    rule, diagnostics=(replace(item, explanation='altered finding'),))
            return findings
        with patch.object(nested.footnote_rules, 'analyze_conditional_nested', side_effect=alter_other_finding):
            result = nested.preview(parse(source), 'main.typ', self.catalog)
        self.assertEqual(result['verification'], 'withheld')
        self.assertFalse(result['edits'] or result['edit_permission'])
        self.assertIn('another footnote finding', result['reason'])

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1 and Typst')
    def test_owned_fixture_compiles_before_and_after_without_original_write(self):
        source = b'#block[Text #footnote[Reason.]. Next.]'
        candidate = self.candidate(source)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / 'original.typ'
            candidate_path = root / 'candidate.typ'
            original.write_bytes(source)
            candidate_path.write_bytes(candidate)
            for path in (original, candidate_path):
                subprocess.run(['typst', 'compile', '--root', str(root), '--creation-timestamp', '0',
                                str(path), str(path.with_suffix('.pdf'))], check=True,
                               capture_output=True, timeout=30)
            self.assertEqual(original.read_bytes(), source)
            self.assertTrue(original.with_suffix('.pdf').is_file())
            self.assertTrue(candidate_path.with_suffix('.pdf').is_file())
