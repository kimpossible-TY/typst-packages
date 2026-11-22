"""Conditional raw annot placement in literal local scoped callbacks."""
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
import document_conditional_local_raw_annot as local
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalLocalRawAnnotTests(unittest.TestCase):
    def inspect(self, source):
        result = local.analyze(source, 'fixture.typ')
        self.assertEqual(result['source_sha256'], digest(source))
        for key in ('runtime_binding_proved', 'execution_proved', 'coverage_complete',
                    'edit_permission', 'compiler_permission'):
            self.assertFalse(result[key], (key, result))
        self.assertTrue(result['conditions'])
        return result

    def test_display_calls_counted_and_outside_calls_are_conditional(self):
        source = '#local-scope-annotations(s => [$ x #annot(<x>, [note]) $ 한글 #annot(<x>, [note])])'.encode()
        result = self.inspect(source)
        self.assertEqual((result['wrappers_examined'], result['lexical_display_calls']), (1, 1))
        finding, = result['diagnostics']
        self.assertEqual(finding['rule_id'], 'core.annotation-in-display')
        self.assertEqual(source[finding['start']:finding['start'] + len(b'#annot')], b'#annot')
        self.assertFalse(finding['outer_dynamic_context'])
        self.assertFalse(result['unresolved'])

    def test_inline_math_is_not_a_display(self):
        result = self.inspect(b'#local-scope-annotations(s => [$x #annot(<x>, [note])$])')
        self.assertEqual(len(result['diagnostics']), 1)
        self.assertEqual(result['lexical_display_calls'], 0)

    def test_suppression_and_dynamic_outer_context(self):
        source = (b'#let later() = [#local-scope-annotations(s => [\n'
                  b'// typst-lint: ignore-next core.annotation-in-display -- intentional\n'
                  b'#annot(<x>, [note])\n])\n]')
        result = self.inspect(source)
        self.assertFalse(result['diagnostics'])
        finding, = result['suppressed']
        self.assertTrue(finding['outer_dynamic_context'])
        self.assertEqual(result['wrappers_in_dynamic_context'], 1)

    def test_nested_wrappers_are_not_double_counted(self):
        source = (b'#local-scope-annotations(s => [#local-scope-annotations(t => '
                  b'[$ x #annot(<x>, [note]) $])])')
        result = self.inspect(source)
        self.assertEqual(result['wrappers_examined'], 2)
        self.assertEqual(result['lexical_display_calls'], 1)
        self.assertFalse(result['diagnostics'])
        # A separate mannot-scope report owns the nested mannot wrapper.
        source = (b'#local-scope-annotations(s => [#mannot-scope(t => '
                  b'[$ x #annot(<x>, [note]) $])])')
        result = self.inspect(source)
        self.assertEqual(result['wrappers_examined'], 1)
        self.assertEqual(result['lexical_display_calls'], 0)

    def test_unsupported_callbacks_nested_dynamic_and_equation_constructor(self):
        cases = (b'#local-scope-annotations(callback)',
                 b'#local-scope-annotations(s => [], prefix: make-name())',
                 b'#local-scope-annotations(s => [#let later() = [#annot(<x>, [note])]])',
                 b'#local-scope-annotations(s => [#math.equation([#annot(<x>, [note])], block: true)])',
                 b'#local-scope-annotations(s => [$ annot(x, y) $])')
        for source in cases:
            with self.subTest(source=source):
                result = self.inspect(source)
                self.assertFalse(result['diagnostics'] or result['lexical_display_calls'])
                self.assertTrue(result['unresolved'])

    def test_explicit_wrapper_and_annot_shadows_are_refused(self):
        call = b'#local-scope-annotations(s => [#annot(<x>, [note])])'
        prefixes = (b'#let local-scope-annotations = none\n', b'#let annot = none\n',
                    b'#let f(local-scope-annotations) = []\n',
                    b'#import "other.typ": x as local-scope-annotations\n')
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                result = self.inspect(prefix + call)
                self.assertFalse(result['diagnostics'] or result['lexical_display_calls'])
                self.assertTrue(result['unresolved'])

    def test_invalid_metadata_and_item_limit_refuse(self):
        with self.assertRaises(InputError):
            local.analyze(b'', 'fixture.typ', catalog=[])
        with patch.object(local, 'MAX_ITEMS', 1):
            with self.assertRaisesRegex(InputError, 'item limit'):
                self.inspect(b'#local-scope-annotations(s => [#annot(<x>, [note])])')

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1 and Typst')
    def test_same_spelling_can_compile_with_unreviewed_display_wrapper(self):
        # This intentionally unreviewed wrapper puts its callback inside a
        # display, illustrating why lexical hints are not runtime violations.
        source = b'#import "helpers.typ": *\n#local-scope-annotations(s => [#annot(<x>, [note])])'
        result = self.inspect(source)
        self.assertEqual(len(result['diagnostics']), 1)
        self.assertEqual(result['lexical_display_calls'], 0)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'main.typ').write_bytes(source)
            (root / 'helpers.typ').write_bytes(
                b'#let annot(target, note) = note\n'
                b'#let local-scope-annotations(f) = math.equation(f((:)), block: true)\n')
            process = subprocess.run(['typst', 'compile', '--root', str(root), '--creation-timestamp', '0',
                                      str(root / 'main.typ'), str(root / 'out.pdf')],
                                     capture_output=True, timeout=30)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertTrue((root / 'out.pdf').read_bytes().startswith(b'%PDF'))
            self.assertEqual((root / 'main.typ').read_bytes(), source)
