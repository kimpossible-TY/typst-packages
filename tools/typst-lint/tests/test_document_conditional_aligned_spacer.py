"""Independent aligned footnote source previews and safety boundaries."""
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
import document_conditional_aligned_spacer as spacer
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE, parse
from typst_lint import digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalAlignedSpacerTests(unittest.TestCase):
    def preview(self, source):
        return spacer.preview(parse(source), 'main.typ')

    def candidate(self, source):
        result = self.preview(source)
        self.assertIsNotNone(result)
        self.assertEqual(result['verification'], 'conditional-aligned-spacer-syntax-directives-and-reanalysis', result)
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

    def test_aligned_display_and_utf8_offsets(self):
        for newline in (b'\n', b'\r\n'):
            source = b'#block[$' + newline + ' 한글 &='.encode() + b' B #footnote[Note.]' + newline + b'$]'
            with self.subTest(newline=newline):
                result, candidate = self.candidate(source)
                self.assertEqual(len(result['edits']), 1)
                self.assertEqual(candidate, source.replace(b'#footnote', b'#dots_space #footnote'))

    def test_multiple_edits_and_unrelated_diagnostics_preserved(self):
        source = (b'#block[$\n A &= B #footnote[X] + C #footnote[Y]\n$ '
                  b'Claim #footnote[Z]]')
        result, candidate = self.candidate(source)
        self.assertEqual(len(result['edits']), 2)
        self.assertEqual(candidate.count(b'#dots_space'), 2)
        self.assertIn(b'Claim #footnote[Z]', candidate)

    def test_unaligned_nested_content_and_ambiguous_expressions_skipped(self):
        cases = (b'$ A &= B #footnote[X] $',  # root belongs to single-source analysis
                 b'#block[$ A = B #footnote[X] $]',
                 b'#block[$ A &= B #dots_space #footnote[X] $]',
                 b'#block[$ A &= B #unknown #footnote[X] $]',
                 b'#block[Prose #footnote[X] and $ A &= B $]',
                 b'#block[$ A = B #footnote[inside $a & b$] $]',
                 b'#block[`$ A &= B #footnote[X] $`]')
        for source in cases:
            with self.subTest(source=source):
                result = self.preview(source)
                self.assertTrue(result is None or not result['edits'], result)

    def test_suppressed_calls_untouched(self):
        source = (b'// typst-lint: ignore-next pde.aligned-footnote-spacer -- intended\n'
                  b'#block[$ A &= B #footnote[X] $]')
        result = self.preview(source)
        self.assertTrue(result is None or not result['edits'], result)

    def test_parser_and_other_finding_change_withhold(self):
        source = b'#block[$ A &= B #footnote[A] $ Claim #footnote[C.]]'
        original = spacer.footnote_rules.analyze_conditional_nested
        def changed(tree, *args, **kwargs):
            values = original(tree, *args, **kwargs)
            if b'#dots_space' in tree.source:
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
    def test_nonzero_spacer_changes_rendered_pdf_in_owned_fixture(self):
        source = (b'#set page(width: 300pt, height: 250pt, margin: 30pt)\n'
                  b'#let dots_space = h(8pt)\n#block[$ A &= B #footnote[Note.] $]')
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
