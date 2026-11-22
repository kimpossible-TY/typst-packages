"""Source-only conditional proposals for spacers before prose footnotes."""
from dataclasses import replace
from pathlib import Path
import sys
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_conditional_prose_spacer as spacer
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE, parse
from typst_lint import digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalProseSpacerTests(unittest.TestCase):
    def preview(self, source):
        return spacer.preview(parse(source), 'main.typ')

    def candidate(self, source):
        result = self.preview(source)
        self.assertIsNotNone(result)
        self.assertEqual(result['verification'], 'conditional-prose-spacer-syntax-directives-and-reanalysis', result)
        self.assertFalse(result['runtime_binding_proved'] or result['execution_proved']
                         or result['render_equivalence_proved'] or result['edit_permission']
                         or result['compiler_permission'])
        self.assertTrue(result['conditions'])
        edits = [Edit(item['start'], item['end'], item['replacement'].encode()) for item in result['edits']]
        candidate = plan(source, edits).candidate()
        self.assertEqual(result['baseline_sha256'], digest(source))
        self.assertEqual(result['candidate_sha256'], digest(candidate))
        self.assertIsNone(self.preview(candidate))
        return result, candidate

    def test_direct_punctuation_utf8_and_horizontal_space(self):
        for separator in (b' ', b'\t', b'   '):
            source = '#block[한글 $x$,'.encode() + b'#dots_space' + separator + b'#footnote[Note.]]'
            with self.subTest(separator=separator):
                result, candidate = self.candidate(source)
                self.assertEqual(len(result['edits']), 1)
                self.assertEqual(candidate, source.replace(b'#dots_space' + separator, b''))

    def test_multiple_edits_preserve_unrelated_findings(self):
        source = (b'#block[First.#dots_space #footnote[A.] '
                  b'Second,#dots_space\t#footnote[B.] Claim #footnote[C.]]')
        result, candidate = self.candidate(source)
        self.assertEqual(len(result['edits']), 2)
        self.assertIn(b'First.#footnote[A.]', candidate)
        self.assertIn(b'Second,#footnote[B.]', candidate)
        self.assertIn(b'Claim #footnote[C.]', candidate)

    def test_unsupported_and_ambiguous_contexts_are_not_edited(self):
        cases = (b'This.#dots_space #footnote[X]',  # root belongs to single-source analysis
                 b'#block[This #dots_space #footnote[X].]',
                 b'#block[This;#dots_space #footnote[X]]',
                 b'#block[This. #dots_space #footnote[X]]',
                 b'#block[This.#dots_space\n#footnote[X]]',
                 b'#block[This.#dots_space /* comment */ #footnote[X]]',
                 b'#block[$ A #dots_space #footnote[X] $]',
                 b'#block[This.#other #footnote[X]]',
                 b'#block[`This.#dots_space #footnote[X]`]')
        for source in cases:
            with self.subTest(source=source):
                result = self.preview(source)
                self.assertTrue(result is None or not result['edits'], result)

    def test_suppressed_spacer_and_punctuation_are_not_erased(self):
        for rule in (b'pde.prose-footnote-no-spacer', b'pde.prose-footnote-punctuation'):
            source = (b'// typst-lint: ignore-next ' + rule + b' -- intentional\n'
                      b'#block[This.#dots_space #footnote[X]]')
            with self.subTest(rule=rule):
                result = self.preview(source)
                self.assertTrue(result is None or not result['edits'], result)

    def test_parse_or_unrelated_finding_change_withholds(self):
        source = b'#block[First.#dots_space #footnote[A.] Claim #footnote[C.]]'
        original = spacer.footnote_rules.analyze_conditional_nested
        def changed(tree, *args, **kwargs):
            values = original(tree, *args, **kwargs)
            if b'First.#footnote' in tree.source:
                finding = values[spacer.footnote_rules.PUNCT]
                if finding.diagnostics:
                    values[spacer.footnote_rules.PUNCT] = replace(
                        finding, diagnostics=(replace(finding.diagnostics[0], explanation='altered'),))
            return values
        with patch.object(spacer.footnote_rules, 'analyze_conditional_nested', side_effect=changed):
            result = self.preview(source)
        self.assertEqual(result['verification'], 'withheld')
        self.assertFalse(result['edits'])
        with patch.object(spacer, 'parse', side_effect=ValueError('parser failed')):
            result = self.preview(source)
        self.assertEqual(result['verification'], 'withheld')
        self.assertFalse(result['edits'])

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1 and Typst')
    def test_positive_spacer_changes_rendered_pdf_in_owned_fixture(self):
        source = (b'#set page(width: 300pt, height: 250pt, margin: 30pt)\n'
                  b'#let dots_space = h(8pt)\n#block[This holds.#dots_space #footnote[Note.]]')
        _, candidate = self.candidate(source)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outputs = []
            for label, contents in (('before', source), ('after', candidate)):
                target = root / label / 'main.typ'
                target.parent.mkdir()
                target.write_bytes(contents)
                output = target.parent / 'output.pdf'
                run = subprocess.run(['typst', 'compile', '--root', str(target.parent),
                                      '--creation-timestamp', '0', str(target), str(output)],
                                     capture_output=True, timeout=30)
                self.assertEqual(run.returncode, 0, run.stderr)
                outputs.append(output.read_bytes())
            self.assertNotEqual(outputs[0], outputs[1])
            self.assertEqual((root / 'before/main.typ').read_bytes(), source)
