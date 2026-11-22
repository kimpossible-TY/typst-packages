"""Independent prose footnote break previews before direct displays."""
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
import document_prose_footnote_display_gap as gap
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE, parse
from typst_lint import digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ProseFootnoteDisplayGapTests(unittest.TestCase):
    def preview(self, source):
        return gap.preview(parse(source), 'main.typ')

    def candidate(self, source):
        result = self.preview(source)
        self.assertIsNotNone(result)
        self.assertEqual(result['verification'], 'conditional-prose-display-gap-syntax-directives-and-reanalysis', result)
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

    def test_lf_crlf_indentation_and_utf8_are_preserved(self):
        for newline in (b'\n', b'\r\n'):
            source = '#block[문장.'.encode() + b'#footnote[Note.]' + newline * 2 + b'  $ x = y $]'
            with self.subTest(newline=newline):
                result, candidate = self.candidate(source)
                self.assertEqual(len(result['edits']), 1)
                self.assertEqual(candidate, source.replace(newline * 2, newline + b'#parbreak()' + newline * 2))

    def test_multiple_edits_and_unrelated_finding_preserved(self):
        source = (b'#block[First.#footnote[A]\n\n$ x = y $]\n'
                  b'#block[Second.#footnote[B]\n\n$ a = b $]\n'
                  b'#block[Claim #footnote[C]]')
        result, candidate = self.candidate(source)
        self.assertEqual(len(result['edits']), 2)
        self.assertEqual(candidate.count(b'#parbreak()'), 2)
        self.assertIn(b'Claim #footnote[C]', candidate)

    def test_inline_math_comments_prose_and_math_footnotes_are_skipped(self):
        cases = (b'#block[Text.#footnote[X]\n\n$x$]',
                 b'#block[Text.#footnote[X]\n\n// comment\n$ A = B $]',
                 b'#block[Text.#footnote[X]\n\nNext.]',
                 b'#block[Text.#footnote[X]\n\n#paragraph_tab\nNext.]',
                 b'#block[$ A #footnote[X]\n\n B $]',
                 b'#block[Text.#footnote[X]\n\n= Heading]',
                 b'#block[`Text.#footnote[X]\n\n$ A = B $`]')
        for source in cases:
            with self.subTest(source=source):
                result = self.preview(source)
                self.assertTrue(result is None or not result['edits'], result)

    def test_suppression_and_explicit_shadow_withhold(self):
        source = (b'// typst-lint: ignore-next pde.footnote-trailing-gap -- intended\n'
                  b'#block[Text.#footnote[X]\n\n$ A = B $]')
        result = self.preview(source)
        self.assertTrue(result is None or not result['edits'], result)
        for name in (b'footnote', b'parbreak'):
            source = b'#let ' + name + b' = none\n#block[Text.#footnote[X]\n\n$ A = B $]'
            result = self.preview(source)
            self.assertTrue(result is None or not result['edits'], result)

    def test_parser_and_other_finding_change_withhold(self):
        source = b'#block[First.#footnote[X]\n\n$ A = B $] #block[Claim #footnote[Y]]'
        original = gap.footnote_rules.analyze_conditional_nested
        def changed(tree, *args, **kwargs):
            values = original(tree, *args, **kwargs)
            if b'#parbreak()' in tree.source:
                finding = values[gap.footnote_rules.PUNCT]
                if finding.diagnostics:
                    values[gap.footnote_rules.PUNCT] = replace(
                        finding, diagnostics=(replace(finding.diagnostics[0], explanation='altered'),))
            return values
        with patch.object(gap.footnote_rules, 'analyze_conditional_nested', side_effect=changed):
            result = self.preview(source)
        self.assertEqual(result['verification'], 'withheld')
        self.assertFalse(result['edits'])
        with patch.object(gap, 'parse', side_effect=ValueError('parser failed')):
            result = self.preview(source)
        self.assertEqual(result['verification'], 'withheld')
        self.assertFalse(result['edits'])

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1 and Typst')
    def test_owned_fixture_compiles_both_versions_without_source_write(self):
        source = b'#block[Text.#footnote[Reason.]\n\n$ x = y $]'
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
                self.assertTrue(rendered[-1].startswith(b'%PDF'))
            self.assertEqual(rendered[0], rendered[1])  # This owned fixture only.
            self.assertEqual((root / 'before/main.typ').read_bytes(), source)
