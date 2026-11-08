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
import document_cli
import paragraph_rules
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

    def test_suppressed_unknown_boundary_does_not_block_other_candidate(self):
        source = (b'#let n = footnote\n#block[Text.#n[X]\n'
                  b'// typst-lint: ignore-next pde.paragraph-marker-gap -- intended\n'
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

    def test_first_content_marker_has_unresolved_preceding_ownership(self):
        for source in (b'#block[\n#paragraph_tab\nNext.]',
                       b'Text outside.\n#block[\n#paragraph_tab\nNext.]'):
            with self.subTest(source=source):
                tree = parse(source)
                hints = paragraph_rules.analyze_conditional_nested_gap(tree, 'main.typ')
                self.assertFalse(hints.diagnostics)
                self.assertTrue(any(reason.startswith(paragraph_rules.NESTED_GAP_UNRESOLVED)
                                    for reason in hints.skips))
                result = self.preview(source)
                self.assertEqual(result['verification'], 'withheld')
                self.assertFalse(result['edits'])

    def test_unknown_prior_call_wrapper_or_label_never_gets_gap_only_fix(self):
        cases = (b'#let n = footnote\n#block[Text.#n[X]\n#paragraph_tab\nNext.]',
                 b'#block[Text.#footnote([X])\n#paragraph_tab\nNext.]',
                 b'#block[*Text.#footnote[X]*\n#paragraph_tab\nNext.]',
                 b'#block[_Text.#footnote[X]_\n#paragraph_tab\nNext.]',
                 b'#block[Text.#footnote[X] <tag>\n#paragraph_tab\nNext.]',
                 b'#block[Text.#unknown[X].\n#paragraph_tab\nNext.]',
                 b'#block[Text.#unknown\n#paragraph_tab\nNext.]')
        for source in cases:
            with self.subTest(source=source):
                hints = paragraph_rules.analyze_conditional_nested_gap(parse(source), 'main.typ')
                self.assertFalse(hints.diagnostics)
                self.assertTrue(any(reason.startswith(paragraph_rules.NESTED_GAP_UNRESOLVED)
                                    for reason in hints.skips))
                result = self.preview(source)
                self.assertEqual(result['verification'], 'withheld')
                self.assertFalse(result['edits'])

    def test_existing_gap_after_unknown_or_first_marker_needs_no_fix(self):
        for source in (b'#block[\n\n#paragraph_tab\nNext.]',
                       b'#block[Text.#footnote[X]\n\n#paragraph_tab\nNext.]'):
            with self.subTest(source=source):
                result = self.preview(source)
                self.assertTrue(result is None or not result['edits'], result)

    def test_inspection_report_exposes_unresolved_and_withholds_source(self):
        source = (b'#let n = footnote\n#block[Text.#n[X]\n#paragraph_tab\nNext.]\n'
                  b'#block[\n#paragraph_tab\nNext.]')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'main.typ').write_bytes(source)
            report = document_cli.report_document(root, 'main.typ', rule_set='pde',
                local_rule_hints=True, preview_conditional_paragraph_gaps=True)
            rows = [row for row in report['local_rule_hints']['coverage']
                    if row['path'] == 'main.typ' and row['rule_id'] == paragraph_rules.MARKER_GAP]
            self.assertEqual(len(rows), 1)
            reasons = [reason for reason in rows[0]['reasons']
                       if reason.startswith(paragraph_rules.NESTED_GAP_UNRESOLVED)]
            self.assertEqual(len(reasons), 2)
            preview, = report['conditional_paragraph_gap_previews']['by_file']
            self.assertEqual(preview['verification'], 'withheld')
            self.assertEqual(preview['edits'], [])
            self.assertEqual((root / 'main.typ').read_bytes(), source)

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

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and shutil.which('typst') and shutil.which('pdftoppm'),
                         'Opt in with Typst and pdftoppm')
    def test_first_nested_marker_blank_line_does_not_separate_outer_prose(self):
        source = b'#let paragraph_tab = h(1em)\nText outside.\n#block[\n#paragraph_tab\nNext.]'
        changed = source.replace(b'#block[\n#paragraph_tab', b'#block[\n\n#paragraph_tab')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            images = []
            for name, data in (('before', source), ('after', changed)):
                src, pdf = root / (name + '.typ'), root / (name + '.pdf')
                src.write_bytes(data)
                subprocess.run(['typst', 'compile', '--root', str(root), '--creation-timestamp', '0',
                                str(src), str(pdf)], capture_output=True, timeout=30, check=True)
                subprocess.run(['pdftoppm', '-f', '1', '-l', '1', '-r', '72', '-png', '-singlefile',
                                str(pdf), str(root / name)], capture_output=True, timeout=30, check=True)
                images.append((root / (name + '.png')).read_bytes())
            self.assertEqual(images[0], images[1])
            self.assertEqual((root / 'before.typ').read_bytes(), source)
