"""Source-only blank-line previews for exact hyphenated markers."""
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
import document_conditional_hyphen_marker_gap as gap
from document_conditional_hyphen_marker import analyze
from document_conditional_paragraph_gap import preview as underscore_preview
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE, parse
from typst_lint import digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalHyphenMarkerGapTests(unittest.TestCase):
    def preview(self, source):
        return gap.preview(parse(source), 'main.typ')

    def candidate(self, source):
        result = self.preview(source)
        self.assertIsNotNone(result)
        self.assertEqual(result['verification'], 'conditional-hyphen-marker-gap-syntax-directives-and-reanalysis', result)
        self.assertFalse(result['runtime_binding_proved'] or result['execution_proved']
                         or result['render_equivalence_proved'] or result['edit_permission']
                         or result['compiler_permission'])
        self.assertTrue(result['conditions'])
        edits = [Edit(item['start'], item['end'], item['replacement'].encode()) for item in result['edits']]
        candidate = plan(source, edits).candidate()
        self.assertEqual(result['baseline_sha256'], digest(source))
        self.assertEqual(result['candidate_sha256'], digest(candidate))
        later = self.preview(candidate)
        self.assertTrue(later is None or not later['edits'], later)
        return result, candidate

    def test_root_nested_body_start_crlf_indent_and_utf8(self):
        for newline in (b'\n', b'\r\n'):
            for prefix in (b'= Heading', '#block[한글.'.encode(), b'#block['):
                suffix = b'Second.]' if prefix.startswith(b'#block[') else b'Second.'
                source = prefix + newline + b'  #paragraph-tab' + newline + b'  ' + suffix
                with self.subTest(newline=newline, prefix=prefix):
                    result, candidate = self.candidate(source)
                    self.assertEqual(len(result['edits']), 1)
                    self.assertEqual(candidate, source.replace(newline + b'  #paragraph-tab',
                                                               newline * 2 + b'  #paragraph-tab'))

    def test_multiple_and_other_rule_findings_remain(self):
        source = (b'#block[First.\n#paragraph-tab\nSecond.\n#paragraph-tab\nThird.]\n'
                  b'#block[Claim #footnote[X]]')
        result, candidate = self.candidate(source)
        self.assertEqual(len(result['edits']), 2)
        self.assertEqual(candidate.count(b'\n\n#paragraph-tab'), 2)
        self.assertIn(b'Claim #footnote[X]', candidate)

    def test_suppression_and_underscore_selection_are_independent(self):
        source = (b'// typst-lint: ignore-next pde.paragraph-marker-gap -- intentional\n'
                  b'#block[One.\n#paragraph-tab\nTwo.]\n'
                  b'#block[Three.\n#paragraph-tab\nFour.]\n'
                  b'#block[Five.\n#paragraph_tab\nSix.]')
        result, candidate = self.candidate(source)
        self.assertEqual(len(result['edits']), 1)
        self.assertIn(b'One.\n#paragraph-tab', candidate)
        self.assertIn(b'Three.\n\n#paragraph-tab', candidate)
        self.assertIn(b'Five.\n#paragraph_tab', candidate)
        old = underscore_preview(parse(source), 'main.typ')
        self.assertEqual(len(old['edits']), 1)
        self.assertNotEqual(old['edits'][0]['start'], result['edits'][0]['start'])

    def test_calls_aliases_comments_and_math_are_not_repaired_without_proof(self):
        cases = (b'#block[Before.#footnote[X]\n#paragraph-tab\nSecond.]',
                 b'#block[#unknown[X]\n#paragraph-tab\nSecond.]',
                 b'#block[First. // comment\n#paragraph-tab\nSecond.]',
                 b'#block[$ x #paragraph-tab y $]',
                 b'#block[First.\n\n#paragraph-tab\nSecond.]',
                 b'#block[First.\n#paragraph-tab\n= Heading]')
        for source in cases:
            with self.subTest(source=source):
                result = self.preview(source)
                self.assertTrue(result is None or not result['edits'], result)

    def test_mixed_safe_and_ambiguous_markers_report_both(self):
        source = (b'#block[First.\n#paragraph-tab\nSecond.]\n'
                  b'#block[Before.#footnote[X]\n#paragraph-tab\nSecond.]')
        result, candidate = self.candidate(source)
        self.assertEqual(len(result['edits']), 1)
        self.assertEqual(len(result['unresolved']), 1)
        self.assertIn(b'Before.#footnote[X]\n#paragraph-tab', candidate)

    def test_malformed_or_changed_other_findings_withhold(self):
        source = b'#block[First.\n#paragraph-tab\nSecond.] #block[Claim #footnote[Y]]'
        original = gap.footnote_rules.analyze_conditional_nested
        def changed(tree, *args, **kwargs):
            values = original(tree, *args, **kwargs)
            if b'First.\n\n#paragraph-tab' in tree.source:
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
    def test_owned_fixture_can_change_pdf_without_source_write(self):
        source = (b'#import "marker.typ": paragraph-tab\n'
                  b'#block[First paragraph.\n#paragraph-tab\nSecond paragraph.]')
        _, candidate = self.candidate(source)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rendered = []
            for label, contents in (('before', source), ('after', candidate)):
                target = root / label / 'main.typ'
                target.parent.mkdir()
                (target.parent / 'marker.typ').write_bytes(b'#let paragraph-tab = h(2em)')
                target.write_bytes(contents)
                output = target.parent / 'output.pdf'
                run = subprocess.run(['typst', 'compile', '--root', str(target.parent),
                                      '--creation-timestamp', '0', str(target), str(output)],
                                     capture_output=True, timeout=30)
                self.assertEqual(run.returncode, 0, run.stderr)
                rendered.append(output.read_bytes())
            self.assertNotEqual(rendered[0], rendered[1])
            self.assertEqual((root / 'before/main.typ').read_bytes(), source)
