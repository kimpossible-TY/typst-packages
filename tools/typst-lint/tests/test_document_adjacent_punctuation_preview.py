"""Conditional, independent previews for adjacent prose footnote punctuation."""
from dataclasses import replace
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_adjacent_punctuation_preview as punctuation
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE, parse
from typst_lint import digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class AdjacentPunctuationPreviewTests(unittest.TestCase):
    def preview(self, source):
        return punctuation.preview(parse(source), 'main.typ')

    def candidate(self, source):
        result = self.preview(source)
        self.assertIsNotNone(result)
        self.assertEqual(result['verification'], 'conditional-adjacent-punctuation-syntax-directives-and-reanalysis', result)
        self.assertFalse(result['runtime_binding_proved'] or result['execution_proved']
                         or result['render_equivalence_proved'] or result['edit_permission']
                         or result['compiler_permission'])
        self.assertTrue(result['conditions'])
        self.assertTrue(result['atomic_edits'])
        edits = [Edit(item['start'], item['end'], item['replacement'].encode()) for item in result['edits']]
        candidate = plan(source, edits).candidate()
        self.assertEqual(result['baseline_sha256'], digest(source))
        self.assertEqual(result['candidate_sha256'], digest(candidate))
        self.assertIsNone(self.preview(candidate))
        return result, candidate

    def test_complex_bodies_and_different_preceding_kinds(self):
        for previous in ('한글', '$x$', '@target'):
            for point in ('.', ','):
                body = b'#footnote[#emph[Reason.] $x^(2)$]'
                source = b'#block[' + previous.encode() + body + point.encode() + b' Next.]'
                with self.subTest(previous=previous, point=point):
                    result, candidate = self.candidate(source)
                    self.assertEqual(len(result['edits']), 2)
                    self.assertEqual(candidate, b'#block[' + previous.encode() + point.encode() + body + b' Next.]')

    def test_multiple_moves_and_unrelated_finding_preserved(self):
        source = (b'#block[First#footnote[X]. Second#footnote[Y], Next.]\n'
                  b'#block[Claim #footnote[Reason.]]')
        result, candidate = self.candidate(source)
        self.assertEqual(len(result['edits']), 4)
        self.assertIn(b'First.#footnote[X] Second,#footnote[Y]', candidate)
        self.assertIn(b'Claim #footnote[Reason.]', candidate)

    def test_ambiguous_and_already_correct_forms_are_not_changed(self):
        cases = (b'#block[Text.#footnote[X]]',
                 b'#block[Text #footnote[X].]',
                 b'#block[Text#footnote[X]...]',
                 b'#block[Text#footnote[X].Next]',
                 b'#block[Text;#footnote[X].]',
                 b'#block[Text#footnote[X]!]',
                 b'#block[Text#footnote[X]/* comment */.]',
                 b'#block[$ A #footnote[X]. $]',
                 b'#block[`Text#footnote[X].`]')
        for source in cases:
            with self.subTest(source=source):
                result = self.preview(source)
                self.assertTrue(result is None or not result['edits'], result)

    def test_suppressed_or_shadowed_footnote_is_not_changed(self):
        prefixes = (b'// typst-lint: ignore-next pde.prose-footnote-punctuation -- intended\n',
                    b'#let footnote = none\n', b'#let f(footnote) = []\n',
                    b'#import "other.typ": footnote\n')
        for prefix in prefixes:
            source = prefix + b'#block[Text#footnote[X].]'
            with self.subTest(prefix=prefix):
                result = self.preview(source)
                self.assertTrue(result is None or not result['edits'], result)

    def test_parse_or_unrelated_finding_changes_withhold(self):
        source = b'#block[First#footnote[X].] #block[Claim #footnote[Y]]'
        original = punctuation.footnote_rules.analyze_conditional_nested
        def changed(tree, *args, **kwargs):
            values = original(tree, *args, **kwargs)
            if b'First.#footnote' in tree.source:
                finding = values[punctuation.RULE]
                if finding.diagnostics:
                    values[punctuation.RULE] = replace(
                        finding, diagnostics=(replace(finding.diagnostics[0], explanation='altered'),))
            return values
        with patch.object(punctuation.footnote_rules, 'analyze_conditional_nested', side_effect=changed):
            result = self.preview(source)
        self.assertEqual(result['verification'], 'withheld')
        self.assertFalse(result['edits'])
        with patch.object(punctuation, 'parse', side_effect=ValueError('parser failed')):
            result = self.preview(source)
        self.assertEqual(result['verification'], 'withheld')
        self.assertFalse(result['edits'])

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1 and Typst')
    def test_owned_fixture_has_different_pdf_without_source_write(self):
        source = b'#block[Text#footnote[Reason.].]'
        _, candidate = self.candidate(source)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rendered = []
            for label, contents in (('before', source), ('after', candidate)):
                target = root / label / 'main.typ'
                target.parent.mkdir()
                target.write_bytes(contents)
                output = target.parent / 'output.pdf'
                run = subprocess.run(['typst', 'compile', '--root', str(target.parent),
                                      '--creation-timestamp', '0', str(target), str(output)],
                                     capture_output=True, timeout=30)
                self.assertEqual(run.returncode, 0, run.stderr)
                rendered.append(output.read_bytes())
            self.assertNotEqual(rendered[0], rendered[1])
            self.assertEqual((root / 'before/main.typ').read_bytes(), source)
