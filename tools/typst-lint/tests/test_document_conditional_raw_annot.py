"""Raw annot source hints do not infer bindings, execution or edit authority."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_conditional_raw_annot as check
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalRawAnnotTests(unittest.TestCase):
    call = b'#annot(<x>, pos: top, dx: 1pt)[Note.]'

    def test_nested_raw_annot_is_source_bound_conditional_and_nonmutating(self):
        source = b'#import "styles.typ": *\n#proof[$ x $\n' + self.call + b'\n]'
        result = check.analyze(source, 'main.typ')
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual(result['source_sha256'], digest(source))
        finding, = result['diagnostics']
        self.assertEqual(finding['rule_id'], 'core.annotation-in-display')
        self.assertEqual(source[finding['start']:finding['end']], self.call)
        self.assertTrue(result['conditions'])
        self.assertFalse(result['runtime_binding_proved'] or result['execution_proved']
                         or result['coverage_complete'] or result['edit_permission'] or result['compiler_permission'])
        self.assertNotIn('edits', result)

    def test_display_deferred_and_dynamic_contexts_are_not_reported(self):
        cases = (b'$ x ' + self.call + b' $', b'#let f(x) = [' + self.call + b']',
                 b'#for x in (1,) [' + self.call + b']', b'#if true [' + self.call + b']',
                 b'#context [' + self.call + b']', b'#let x = [' + self.call + b']')
        for source in cases:
            with self.subTest(source=source):
                result = check.analyze(source, 'main.typ')
                self.assertFalse(result['diagnostics'])
                self.assertTrue(result['unresolved'])

    def test_equation_constructor_content_is_unresolved_without_binding_guess(self):
        for callee in (b'math.equation', b'equation', b'(math.equation)', b'other.equation'):
            source = b'#' + callee + b'([' + self.call + b'], block: true)'
            with self.subTest(callee=callee):
                result = check.analyze(source, 'main.typ')
                self.assertFalse(result['diagnostics'])
                self.assertTrue(result['unresolved'], result)

    def test_unknown_wrapper_and_show_rule_limits_are_explicit_conditions(self):
        for source in (b'#wrapper([' + self.call + b'])',
                       b'#show: it => math.equation(it, block: true)\n' + self.call):
            with self.subTest(source=source):
                result = check.analyze(source, 'main.typ')
                finding, = result['diagnostics']
                self.assertIn('source call', finding['explanation'])
                self.assertTrue(any('wrapper or show rule' in item for item in result['conditions']))
                self.assertFalse(result['runtime_binding_proved'] or result['edit_permission'])

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1 and Typst')
    def test_owned_equation_constructor_compiles_but_is_not_promoted(self):
        source = (b'#import "fixture.typ": *\n'
                  b'#math.equation([#annot(<x>)[Note.]], block: true)')
        result = check.analyze(source, 'main.typ')
        self.assertFalse(result['diagnostics'])
        self.assertTrue(result['unresolved'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'main.typ'
            target.write_bytes(source)
            (root / 'fixture.typ').write_bytes(b'#let annot(tag, body) = body')
            run = subprocess.run(['typst', 'compile', '--root', str(root), str(target), str(root / 'out.pdf')],
                                 capture_output=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(target.read_bytes(), source)

    def test_explicit_shadow_including_combined_import_is_withheld(self):
        cases = (b'#let annot = none', b'#let annot(a, b) = none', b'#let f(annot) = []',
                 b'#{annot = none}', b'#for annot in (none,) []', b'#import "annot.typ"',
                 b'#import "@local/annot:0.1.0"', b'#import "other.typ" as annot',
                 b'#import "other.typ": other as annot', b'#import "other.typ": annot',
                 b'#import "other.typ" as tools: other as annot',
                 b'#import "other.typ" as tools: annot', b'#import "other.typ" as annot: other')
        for prefix in cases:
            with self.subTest(prefix=prefix):
                result = check.analyze(prefix + b'\n#proof[' + self.call + b']', 'main.typ')
                self.assertFalse(result['diagnostics'])
                self.assertTrue(result['unresolved'])

    def test_unrelated_import_and_expected_named_options_are_permitted(self):
        cases = (b'#import "annot.typ" as other\n', b'#import "other.typ": helper\n',
                 b'#import "other.typ" as tools: annot as other\n',
                 b'#import "other.typ" as tools: *\n', b'#let x = "annot"\n')
        for prefix in cases:
            with self.subTest(prefix=prefix):
                result = check.analyze(prefix + b'#proof[' + self.call + b']', 'main.typ')
                self.assertEqual(len(result['diagnostics']), 1, result['unresolved'])

    def test_suppression_and_unicode_crlf_offsets(self):
        source = ('#proof[한글\r\n'
                  '// typst-lint: ignore-next core.annotation-in-display -- intentional\r\n'
                  '#annot(<x>, pos: top)[Note.]\r\n]').encode()
        result = check.analyze(source, 'main.typ')
        self.assertFalse(result['diagnostics'])
        finding, = result['suppressed']
        self.assertEqual((finding['line'], finding['column']), (3, 1))

    def test_strings_ambiguous_arguments_and_parse_failures_are_not_reported(self):
        for source in (b'#str("#annot(x)[y]")', b'`#annot(x)[y]`', b'#proof[#annot(x)]',
                       b'#proof[#annot(x, y, ..args)]', b'#proof[#annot(x, unexpected: 1)[y]]',
                       b'#proof[#annot(x, pos: top, pos: bottom)[y]]'):
            with self.subTest(source=source):
                self.assertFalse(check.analyze(source, 'main.typ')['diagnostics'])
        result = check.analyze(b'#proof[#annot(', 'main.typ')
        self.assertEqual(result['status'], 'UNAVAILABLE')
        self.assertFalse(result['diagnostics'])
        with self.assertRaises(InputError):
            check.analyze('not bytes', 'main.typ')
        with self.assertRaises(InputError):
            check.analyze(b'', 'main.typ', catalog=[])


if __name__ == '__main__':
    unittest.main()
