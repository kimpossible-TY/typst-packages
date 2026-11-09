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

    def test_standalone_block_then_marker_gets_conditional_blank_line(self):
        for newline in (b'\n', b'\r\n'):
            for block in (b'#block[Proof.]', b'#block(breakable: false)[Proof.]'):
                source = b'#block[Before.' + newline + newline + block + newline + b'  #paragraph_tab' + newline + b'Next.]'
                with self.subTest(source=source):
                    tree = parse(source)
                    old = paragraph_rules.analyze_conditional_nested_gap(tree, 'main.typ')
                    self.assertTrue(any(x.startswith(paragraph_rules.NESTED_GAP_UNRESOLVED) for x in old.skips))
                    result, candidate = self.candidate(source)
                    self.assertEqual(len(result['edits']), 1)
                    self.assertEqual(candidate, source.replace(block + newline, block + newline + newline))
                    self.assertTrue(any('preceding standalone block' in x for x in result['conditions']))
                    self.assertFalse(result['runtime_binding_proved'] or result['execution_proved'])

    def test_explicit_block_shadow_and_directive_are_withheld(self):
        prefix_cases = (b'#let block(body, ..args) = body\n', b'#let block = none\n',
                        b'#import "other.typ": f as block\n', b'#(block = none)\n')
        for prefix in prefix_cases:
            source = prefix + b'#outer[Before.\n\n#block(breakable: false)[Proof.]\n#paragraph_tab\nNext.]'
            with self.subTest(prefix=prefix):
                result = self.preview(source)
                self.assertEqual(result['verification'], 'withheld', result)
                self.assertFalse(result['edits'])
        for source in (b'#f(block => [Before.\n\n#block[Proof.]\n#paragraph_tab\nNext.])',
                       b'#for block in (x => x,) [\n#block[Proof.]\n#paragraph_tab\nNext.]',
                       b'#for (i, block) in ((1, none),) [\n#block[Proof.]\n#paragraph_tab\nNext.]'):
            with self.subTest(source=source):
                result = self.preview(source)
                self.assertEqual(result['verification'], 'withheld')
                self.assertFalse(result['edits'])
        suppressed = (b'// typst-lint: ignore-next pde.paragraph-marker-gap -- intentional\n'
                      b'#block[Before.\n\n#block[Proof.]\n#paragraph_tab\nNext.]')
        hints = paragraph_rules.analyze_conditional_nested_gap(parse(suppressed), 'main.typ',
                    _allow_first_content=True, _allow_direct_block=True)
        self.assertFalse(hints.diagnostics)
        self.assertEqual(len(hints.suppressed), 1)
        self.assertIsNone(self.preview(suppressed))

    def test_nested_default_parameter_shadow_screen(self):
        value = b'0'
        for _ in range(16):
            value = b'(a: ' + value + b') => 0'
        tree = parse(b'#let f = ' + value)
        self.assertFalse(paragraph_rules._explicit_block_shadow(tree))
        value = b'block'
        for _ in range(16):
            value = b'(a: ' + value + b') => 0'
        tree = parse(b'#let f = ' + value)
        self.assertTrue(paragraph_rules._explicit_block_shadow(tree))

    def test_block_dynamic_options_comments_aliases_and_attached_labels_withhold(self):
        blocks = (b'#block(breakable: flag)[Proof.]', b'#block(breakable: true)[Proof.]',
                  b'#block(breakable: false, inset: 1pt)[Proof.]', b'#block(/*comment*/breakable: false)[Proof.]',
                  b'#other[Proof.]', b'#block[Proof.] <label>', b'#block[Proof.] // comment',
                  b'Inline #block[Proof.]')
        for block in blocks:
            source = b'#block[Before.\n\n' + block + b'\n#paragraph_tab\nNext.]'
            with self.subTest(block=block):
                result = self.preview(source)
                self.assertEqual(result['verification'], 'withheld', result)
                self.assertFalse(result['edits'])

    def test_block_gap_is_not_composed_into_compiler_path(self):
        import document_sequence_preview as sequence
        from typst_lint import load_rules
        source = (b'#block[Before.\n\n#block[Proof.]\n#paragraph_tab\n'
                  b'This holds#footnote[Reason.].]')
        tree = parse(source)
        result, _ = self.candidate(source)
        self.assertEqual(len(result['edits']), 1)
        record = sequence.preview(tree, 'main.typ', load_rules()[0]['rules'],
                                  conditional_adjacent_punctuation=True)
        self.assertEqual(record['verification'], 'withheld')
        self.assertFalse(record['steps'])

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and shutil.which('typst') and shutil.which('pdftoppm'),
                         'Opt in with Typst and pdftoppm')
    def test_standalone_block_then_marker_owned_fixture_raster(self):
        if not subprocess.check_output(['typst', '--version'], text=True).startswith('typst 0.15.1 '):
            self.skipTest('Fixture pins Typst 0.15.1')
        source = (b'#let paragraph_tab = "\\u{F000}"\n#show regex("\\u{F000}"): h(1.5em)\n'
                  b'#block[Before.\n\n#block(breakable: false)[Proof.]\n#paragraph_tab\nAfter.]')
        _, candidate = self.candidate(source)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            images = []
            for name, data in (('before', source), ('after', candidate)):
                src, pdf = root / (name + '.typ'), root / (name + '.pdf')
                src.write_bytes(data)
                subprocess.run(['typst', 'compile', '--root', str(root), '--creation-timestamp', '0',
                                str(src), str(pdf)], capture_output=True, timeout=30, check=True)
                subprocess.run(['pdftoppm', '-f', '1', '-l', '1', '-r', '72', '-png', '-singlefile',
                                str(pdf), str(root / name)], capture_output=True, timeout=30, check=True)
                images.append((root / (name + '.png')).read_bytes())
            self.assertEqual(images[0], images[1])
            self.assertEqual((root / 'before.typ').read_bytes(), source)

    def test_first_content_marker_gets_conditional_blank_line(self):
        for newline in (b'\n', b'\r\n'):
            for outside in (b'', b'Text outside.' + newline):
                source = outside + b'#block[' + newline + b'  #paragraph_tab' + newline + b'Next.]'
                with self.subTest(source=source):
                    hints = paragraph_rules.analyze_conditional_nested_gap(parse(source), 'main.typ', _allow_first_content=True)
                    self.assertEqual(len(hints.diagnostics), 1)
                    self.assertFalse(any(reason.startswith(paragraph_rules.NESTED_GAP_UNRESOLVED)
                                         for reason in hints.skips))
                    result, candidate = self.candidate(source)
                    self.assertEqual(len(result['edits']), 1)
                    self.assertEqual(candidate, source.replace(b'#block[' + newline,
                                                              b'#block[' + newline + newline))

    def test_first_marker_stays_unresolved_in_composed_compile_path(self):
        import document_sequence_preview as sequence
        from typst_lint import load_rules
        source = b'#block[\n#paragraph_tab\nThis holds#footnote[Reason.].]'
        tree = parse(source)
        baseline = paragraph_rules.analyze_conditional_nested_gap(tree, 'main.typ')
        self.assertTrue(any(reason.startswith(paragraph_rules.NESTED_GAP_UNRESOLVED)
                            for reason in baseline.skips))
        result, _ = self.candidate(source)
        self.assertEqual(len(result['edits']), 1)
        record = sequence.preview(tree, 'main.typ', load_rules()[0]['rules'],
                                  conditional_adjacent_punctuation=True)
        self.assertEqual(record['verification'], 'withheld')
        self.assertFalse(record['steps'])

    def test_first_marker_suppression_and_unknown_binding_remain_conditional(self):
        suppressed = (b'// typst-lint: ignore-next pde.paragraph-marker-gap -- intentional\n'
                      b'#block[\n#paragraph_tab\nNext.]')
        hints = paragraph_rules.analyze_conditional_nested_gap(parse(suppressed), 'main.typ', _allow_first_content=True)
        self.assertFalse(hints.diagnostics)
        self.assertEqual(len(hints.suppressed), 1)
        self.assertIsNone(self.preview(suppressed))
        shadow = b'#let paragraph_tab = []\n#block[\n#paragraph_tab\nNext.]'
        result, _ = self.candidate(shadow)
        self.assertFalse(result['runtime_binding_proved'] or result['execution_proved'])
        self.assertTrue(any('resolves' in condition for condition in result['conditions']))

    def test_first_marker_with_comment_or_dynamic_prefix_stays_unproved(self):
        cases = (b'#block[\n// ordinary comment\n#paragraph_tab\nNext.]',
                 b'#block[\n#unknown()\n#paragraph_tab\nNext.]',
                 b'#block[\n#let paragraph_tab = []\n#paragraph_tab\nNext.]')
        for source in cases:
            with self.subTest(source=source):
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
            self.assertEqual(len(reasons), 1)
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
        if not subprocess.check_output(['typst', '--version'], text=True).startswith('typst 0.15.1 '):
            self.skipTest('Fixture pins Typst 0.15.1')
        _, changed = self.candidate(source)
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
