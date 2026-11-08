"""Document-only script proposals for selected syntax-embedded code operands."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import Edit, plan
import document_sequence_preview
from script_rule import analyze
from syntax_client import EXECUTABLE, parse
from typst_lint import load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class DocumentCodeScriptTests(unittest.TestCase):
    def test_code_call_is_grouped_with_hash_and_idempotent(self):
        for source, expected in (
            (b'$x^#footnote[Text]$', b'$x^(#footnote[Text])$'),
            (b'$x^ #f(2)$', b'$x^ (#f(2))$'),
            (b'$x_#f(2)$', b'$x_(#f(2))$'),
        ):
            with self.subTest(source=source):
                tree = parse(source)
                self.assertTrue(analyze(tree, 'x.typ').skips, 'Existing default stays conservative')
                result = analyze(tree, 'x.typ', _allow_code_call=True)
                self.assertEqual(len(result.diagnostics), 1)
                self.assertFalse(result.skips)
                candidate = plan(source, result.edits).candidate()
                self.assertEqual(candidate, expected)
                final = analyze(parse(candidate), 'x.typ', _allow_code_call=True)
                self.assertFalse(final.diagnostics or final.edits or final.skips)

    def test_direct_code_values_and_fields_are_grouped_with_hash(self):
        for source, expected in (
            (b'$x^#value$', b'$x^(#value)$'),
            (b'$x_ #value_2$', b'$x_ (#value_2)$'),
            (b'$x^#record.value$', b'$x^(#record.value)$'),
            (b'$x_#record.value$', b'$x_(#record.value)$'),
            (b'$x^#record.inner.value$', b'$x^(#record.inner.value)$'),
            (b'$x^#value_2 + y^i$', b'$x^(#value_2) + y^(i)$'),
        ):
            with self.subTest(source=source):
                tree = parse(source)
                self.assertTrue(analyze(tree, 'x.typ').skips, 'Default profiles remain conservative')
                result = analyze(tree, 'x.typ', _allow_code_call=True)
                self.assertFalse(result.skips)
                candidate = plan(source, result.edits).candidate()
                self.assertEqual(candidate, expected)
                final = analyze(parse(candidate), 'x.typ', _allow_code_call=True)
                self.assertFalse(final.diagnostics or final.edits or final.skips)

    def test_comments_and_other_code_expressions_remain_unsupported(self):
        for source in (b'$x^/* comment */#f(2)$', b'$x^/* comment */#value$',
                       b'$x^#(value + 1)$', b'$x^#{value}$', b'$x^#f().value$',
                       b'$x^#a.at(0).value$', b'$x^/* comment */i$'):
            with self.subTest(source=source):
                result = analyze(parse(source), 'x.typ', _allow_code_call=True)
                self.assertTrue(result.skips)
                self.assertFalse(result.edits)

    def test_suppression_prevents_the_code_call_edit(self):
        source = (b'// typst-lint: ignore-next core.parenthesized-scripts -- intentional\n'
                  b'$x^#footnote[Text]$')
        result = analyze(parse(source), 'x.typ', _allow_code_call=True)
        self.assertEqual(len(result.suppressed), 1)
        self.assertFalse(result.edits or result.skips)

    def test_suppression_prevents_the_code_value_edit(self):
        source = (b'// typst-lint: ignore-next core.parenthesized-scripts -- intentional\n'
                  b'$x^#value$')
        result = analyze(parse(source), 'x.typ', _allow_code_call=True)
        self.assertEqual(len(result.suppressed), 1)
        self.assertFalse(result.edits or result.skips)

    def test_document_cli_reports_and_plans_value_and_field_operands(self):
        source = b'$x^#value + y_#record.field$\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'main.typ').write_bytes(source)
            run = subprocess.run([sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
                                  '--root', str(root), '--entrypoint', 'main.typ', '--format', 'json'],
                                 capture_output=True, timeout=30)
            self.assertEqual(run.returncode, 2, run.stderr)
            report = json.loads(run.stdout)
            self.assertEqual(len(report['diagnostics']), 2)
            edits = tuple(Edit(e['start'], e['end'], e['replacement'].encode())
                          for e in report['proposed_edits'])
            self.assertEqual(plan(source, edits).candidate(), b'$x^(#value) + y_(#record.field)$\n')
            self.assertEqual((root / 'main.typ').read_bytes(), source)

    def test_combined_document_preview_replays_the_new_operands(self):
        source = b'$x^#value + y_#record.field + z^i$\n'
        tree = parse(source)
        catalog = load_rules()[0]['rules']
        report = document_sequence_preview.preview(tree, 'main.typ', catalog)
        self.assertEqual(report['verification'], 'hash-chained-parse-and-three-rule-reanalysis')
        candidate = document_sequence_preview.replay(tree, 'main.typ', catalog, report)
        self.assertEqual(candidate, b'$x^(#value) + y_(#record.field) + z^(i)$\n')
        self.assertIsNone(document_sequence_preview.preview(parse(candidate), 'main.typ', catalog))

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1 and Typst')
    def test_generated_footnote_candidate_renders_the_same_owned_fixture(self):
        source = b'The assumption follows: $"by assuming"^#footnote[Example.]$\n'
        result = analyze(parse(source), 'main.typ', _allow_code_call=True)
        candidate = plan(source, result.edits).candidate()
        self.assertEqual(candidate, b'The assumption follows: $"by assuming"^(#footnote[Example.])$\n')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = root / 'main.typ'
            output = root / 'output.pdf'
            (root / 'packages').mkdir()
            (root / 'cache').mkdir()
            def compile(data):
                entry.write_bytes(data)
                subprocess.run(['typst', 'compile', '--root', str(root),
                                '--package-path', str(root / 'packages'),
                                '--package-cache-path', str(root / 'cache'),
                                '--creation-timestamp', '1700000000', str(entry), str(output)],
                               check=True, capture_output=True, timeout=30)
                return output.read_bytes()
            self.assertEqual(compile(source), compile(candidate))

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1 and Typst')
    def test_generated_value_and_field_candidate_renders_the_same_owned_fixture(self):
        source = (b'#let value = [alpha]\n#let record = (inner: (value: [beta]))\n'
                  b'$x^#value + y_#record.inner.value + z^#value_2$\n')
        # All variables are local to this temporary fixture; no consumer files change.
        source = b'#let value_2 = [two]\n' + source
        result = analyze(parse(source), 'main.typ', _allow_code_call=True)
        self.assertEqual(len(result.diagnostics), 3)
        self.assertFalse(result.skips)
        candidate = plan(source, result.edits).candidate()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = root / 'main.typ'
            output = root / 'output.pdf'
            (root / 'packages').mkdir()
            (root / 'cache').mkdir()
            def compile(data):
                entry.write_bytes(data)
                subprocess.run(['typst', 'compile', '--root', str(root),
                                '--package-path', str(root / 'packages'),
                                '--package-cache-path', str(root / 'cache'),
                                '--creation-timestamp', '1700000000', str(entry), str(output)],
                               check=True, capture_output=True, timeout=30)
                return output.read_bytes()
            self.assertEqual(compile(source), compile(candidate))
