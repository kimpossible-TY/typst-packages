"""Independent paragraph marker source previews and safety boundaries."""
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
import document_conditional_prose_marker as marker
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE, parse
from typst_lint import digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalProseMarkerTests(unittest.TestCase):
    def preview(self, source):
        return marker.preview(parse(source), 'main.typ')

    def candidate(self, source):
        result = self.preview(source)
        self.assertIsNotNone(result)
        self.assertEqual(result['verification'], 'conditional-prose-marker-syntax-directives-and-reanalysis', result)
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

    def test_lf_crlf_indent_and_utf8_are_preserved(self):
        for newline in (b'\n', b'\r\n'):
            source = '#block[첫 문단.'.encode() + newline * 2 + b'  Second paragraph.]'
            with self.subTest(newline=newline):
                result, candidate = self.candidate(source)
                self.assertEqual(len(result['edits']), 1)
                self.assertEqual(candidate, source.replace(b'Second', b'#paragraph_tab' + newline + b'  Second'))

    def test_multiple_edits_with_other_findings(self):
        source = (b'#block[First paragraph.\n\nSecond paragraph.\n\nThird paragraph.] '
                  b'#block[Claim #footnote[Reason.]]')
        result, candidate = self.candidate(source)
        self.assertEqual(len(result['edits']), 2)
        self.assertEqual(candidate.count(b'#paragraph_tab'), 2)
        self.assertIn(b'Claim #footnote[Reason.]', candidate)

    def test_unsupported_or_ambiguous_contexts_are_skipped(self):
        cases = (b'#block[First paragraph.\n\nsecond paragraph.]',
                 b'#block[First paragraph.\n\n$ x $ follows.]',
                 b'#block[First paragraph.\n\n#footnote[X] Second.]',
                 b'#block[First paragraph.\n\n// comment\nSecond paragraph.]',
                 b'#block[$ x $\n\nSecond paragraph.]',
                 b'#block[First.\n\n= Heading]',
                 b'#block[`First.\n\nSecond.`]')
        for source in cases:
            with self.subTest(source=source):
                result = self.preview(source)
                self.assertTrue(result is None or not result['edits'], result)

    def test_suppressed_boundary_untouched(self):
        source = (b'// typst-lint: ignore-next pde.prose-paragraph-marker -- intended\n'
                  b'#block[First paragraph.\n\nSecond paragraph.]')
        result = self.preview(source)
        self.assertTrue(result is None or not result['edits'], result)

    def test_parser_and_other_finding_change_withhold(self):
        source = b'#block[First paragraph.\n\nSecond paragraph.] #block[Claim #footnote[X]]'
        original = marker.footnote_rules.analyze_conditional_nested
        def changed(tree, *args, **kwargs):
            values = original(tree, *args, **kwargs)
            if b'#paragraph_tab' in tree.source:
                finding = values[marker.footnote_rules.PUNCT]
                if finding.diagnostics:
                    values[marker.footnote_rules.PUNCT] = replace(
                        finding, diagnostics=(replace(finding.diagnostics[0], explanation='altered'),))
            return values
        with patch.object(marker.footnote_rules, 'analyze_conditional_nested', side_effect=changed):
            result = self.preview(source)
        self.assertEqual(result['verification'], 'withheld')
        self.assertFalse(result['edits'])
        with patch.object(marker, 'parse', side_effect=ValueError('parser failed')):
            result = self.preview(source)
        self.assertEqual(result['verification'], 'withheld')
        self.assertFalse(result['edits'])

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1 and Typst')
    def test_marker_can_change_rendered_pdf_in_owned_fixture(self):
        source = (b'#set page(width: 300pt, height: 250pt, margin: 30pt)\n'
                  b'#let paragraph_tab = h(1.5em)\n#block[First paragraph.\n\nSecond paragraph.]')
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
