"""Nested marker-gap proposals remain independent and conditional."""
from dataclasses import replace
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import document_conditional_paragraph_gap as paragraph_gap
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE, parse
from typst_lint import digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalParagraphGapTests(unittest.TestCase):
    def preview(self, source):
        return paragraph_gap.preview(parse(source), 'main.typ')

    def candidate(self, source):
        result = self.preview(source)
        self.assertIsNotNone(result)
        self.assertEqual(result['verification'], 'conditional-paragraph-gap-syntax-directives-and-reanalysis', result)
        self.assertFalse(result['execution_proved'] or result['runtime_binding_proved']
                         or result['render_equivalence_proved'] or result['edit_permission']
                         or result['compiler_permission'])
        edits = [Edit(row['start'], row['end'], row['replacement'].encode()) for row in result['edits']]
        candidate = plan(source, edits).candidate()
        self.assertEqual(result['baseline_sha256'], digest(source))
        self.assertEqual(result['candidate_sha256'], digest(candidate))
        self.assertIsNone(self.preview(candidate))
        return result, candidate

    def test_lf_crlf_unicode_and_indentation(self):
        for newline in (b'\n', b'\r\n'):
            source = '#block[한글.'.encode() + newline + b'  #paragraph_tab' + newline + b'Text.]'
            result, candidate = self.candidate(source)
            self.assertEqual(len(result['edits']), 1)
            self.assertEqual(candidate, source.replace(newline + b'  #paragraph_tab',
                                                        newline + newline + b'  #paragraph_tab'))

    def test_multiple_gaps(self):
        source = b'#block[First.\n#paragraph_tab\nNext.\n#paragraph_tab\nFinal.]'
        result, candidate = self.candidate(source)
        self.assertEqual(len(result['edits']), 2)
        self.assertEqual(candidate.count(b'\n\n#paragraph_tab'), 2)

    def test_reasoned_suppression_is_preserved(self):
        source = (b'#block[First.\n// typst-lint: ignore-next pde.paragraph-marker-gap -- intended\n'
                  b'#paragraph_tab\nNext.\n#paragraph_tab\nFinal.]')
        result, candidate = self.candidate(source)
        self.assertEqual(len(result['edits']), 1)
        self.assertIn(b'intended\n#paragraph_tab', candidate)
        self.assertIn(b'Next.\n\n#paragraph_tab', candidate)

    def test_existing_gap_and_marker_before_display_are_not_changed(self):
        for source in (b'#block[First.\n\n#paragraph_tab\nText.]',
                       b'#block[First.\n#paragraph_tab\n$ x $]',
                       b'#block[First.\n#paragraph_tab\n= Heading]'):
            with self.subTest(source=source):
                result = self.preview(source)
                self.assertTrue(result is None or not result['edits'], result)

    def test_cross_rule_change_and_parse_failure_withhold(self):
        source = b'#block[First.\n#paragraph_tab\nText.]'
        original = paragraph_gap.continuation_rules.analyze_conditional
        def changed(tree, *args, **kwargs):
            result = original(tree, *args, **kwargs)
            if b'First.\n\n#paragraph_tab' in tree.source:
                from typst_lint import Diagnostic
                finding = Diagnostic('pde.continuation-line', 'warning', 'main.typ', 1, 1,
                                     'Fixture finding', 'fixture')
                return replace(result, diagnostics=(finding,))
            return result
        with patch.object(paragraph_gap.continuation_rules, 'analyze_conditional', side_effect=changed):
            result = self.preview(source)
        self.assertEqual(result['verification'], 'withheld')
        self.assertFalse(result['edits'])
        with patch.object(paragraph_gap, 'parse', side_effect=ValueError('fixture parse failure')):
            result = self.preview(source)
        self.assertEqual(result['verification'], 'withheld')
        self.assertFalse(result['edits'])

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1 and Typst')
    def test_owned_fixture_render_changes_without_source_write(self):
        source = b'#let paragraph_tab = h(1em)\n#block[First.\n#paragraph_tab\nNext.]'
        _, candidate = self.candidate(source)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outputs = []
            for name, contents in (('before', source), ('after', candidate)):
                path, output = root / (name + '.typ'), root / (name + '.pdf')
                path.write_bytes(contents)
                run = subprocess.run(['typst', 'compile', '--root', str(root), '--creation-timestamp', '0',
                                      str(path), str(output)], capture_output=True, timeout=30)
                self.assertEqual(run.returncode, 0, run.stderr)
                outputs.append(output.read_bytes())
            self.assertNotEqual(outputs[0], outputs[1])
            self.assertEqual((root / 'before.typ').read_bytes(), source)
