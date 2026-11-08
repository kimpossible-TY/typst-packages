"""Conditional math builtin identification never grants edit authority."""
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
import conditional_footnote_binding as binding
import conditional_modern_highlight as modern
import document_math_builtin_evidence as evidence
import include_context
import namespace_multiroot
from syntax_client import EXECUTABLE

MODERN = TOOL / 'tests/fixtures/reviewed-text-utils-main/local/text-utils/0.1.2'
IMPORT = b'#import "@local/text-utils:0.1.2": *\n'
CALL = b'#highlighted[Text $cal(X)$ text]'


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the syntax adapter')
class MathBuiltinEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='math-builtins-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project, self.packages, self.cache = (self.root / n for n in ('project', 'packages', 'cache'))
        for path in (self.project, self.packages, self.cache):
            path.mkdir()
        target = self.packages / 'local/text-utils/0.1.2'
        target.parent.mkdir(parents=True)
        shutil.copytree(MODERN, target)

    def analyze(self, body):
        source = IMPORT + body
        (self.project / 'main.typ').write_bytes(source)
        context = include_context.load_include_context(self.project, (('main.typ', source),))
        session = binding.ConditionalFootnoteBinding(context,
                    namespace_multiroot.load_multiroot_capture(self.packages, self.cache))
        return evidence.analyze(modern.ModernHighlightAnalyzer(session), 'main.typ')

    def test_builtin_under_reviewed_wildcard_is_conditional(self):
        result = self.analyze(CALL)
        self.assertEqual(len(result['findings']), 1)
        for key in ('runtime_binding_proved', 'edit_permission', 'compiler_permission'):
            self.assertFalse(result[key])
        self.assertFalse(result['findings'][0]['render_equivalence_proved'])

    def test_lexical_shadow_import_and_dynamic_scope_are_withheld(self):
        (self.project / 'shadow.typ').write_bytes(b'#let cal(x) = x\n')
        cases = (b'#let cal(x) = panic("shadow")\n' + CALL,
                 b'#import "shadow.typ": cal\n' + CALL,
                 b'#import "shadow.typ": *\n' + CALL,
                 b'#let f() = [' + CALL + b']\n#f()',
                 b'#if true [' + CALL + b']')
        for case in cases:
            with self.subTest(case=case):
                self.assertFalse(self.analyze(case)['findings'])

    def test_suppressed_other_calls_and_embedded_code_are_not_promoted(self):
        cases = (b'// typst-lint: ignore-next pde.text-only-highlight -- known\n' + CALL,
                 b'#highlighted[text $unknown(x, y)$ text]',
                 b'#highlighted[text $cal(#panic("side effect"))$ text]')
        for case in cases:
            with self.subTest(case=case):
                self.assertFalse(self.analyze(case)['findings'])

    def test_unknown_wildcard_cannot_be_repaired_by_later_known_import(self):
        (self.project / 'shadow.typ').write_bytes(b'#let cal(x) = panic("shadow")\n')
        result = self.analyze(b'#import "shadow.typ": *\n' + IMPORT + CALL)
        self.assertFalse(result['findings'])
        self.assertTrue(result['unresolved'])
        self.assertEqual(result['status'], 'INCOMPLETE')

    def test_modern_upstream_unresolved_is_not_lost(self):
        result = self.analyze(b'#import "missing.typ": *\n' + CALL)
        self.assertFalse(result['findings'])
        self.assertTrue(result['upstream_unresolved'])
        self.assertEqual(result['status'], 'INCOMPLETE')

    def test_nested_math_every_binding_and_code_are_checked(self):
        good = b'#highlighted[Text $cal(frac(x, y))$ text]'
        self.assertEqual(len(self.analyze(good)['findings']), 1)
        for prefix, call in ((b'#let frac(x, y) = panic("shadow")\n', good),
                             (b'', b'#highlighted[text $cal(unknown(x))$ text]'),
                             (b'', b'#highlighted[text $cal(frac(#panic("x"), y))$ text]')):
            with self.subTest(call=call, prefix=prefix):
                self.assertFalse(self.analyze(prefix + call)['findings'])

    def test_alias_shadow_and_limits(self):
        (self.project / 'shadow.typ').write_bytes(b'#let replacement(x) = x\n')
        self.assertFalse(self.analyze(b'#import "shadow.typ": replacement as cal\n' + CALL)['findings'])
        with patch.object(evidence, 'MAX_ITEMS', 1):
            with self.assertRaisesRegex(ValueError, 'budget'):
                self.analyze(CALL)
        with patch.object(evidence, 'MAX_BYTES', 8):
            with self.assertRaisesRegex(ValueError, 'budget'):
                self.analyze(CALL)

    @unittest.skipUnless(shutil.which('typst'), 'Requires Typst')
    def test_actual_typst_rebinding_can_have_side_effect(self):
        good = self.project / 'good.typ'
        bad = self.project / 'bad.typ'
        good.write_bytes(b'$cal(X) dot(x) frac(x,y) frak(X) overline(x) tilde(x) op("lim") bb(X) hat(x)$')
        bad.write_bytes(b'#let cal(x) = panic("shadow called")\n$cal(X)$')
        env = dict(os.environ, TYPST_PACKAGE_PATH=str(self.packages), TYPST_PACKAGE_CACHE_PATH=str(self.cache))
        for file, expected in ((good, 0), (bad, 1)):
            result = subprocess.run(['typst', 'compile', '--root', str(self.project), str(file),
                                     str(self.project / (file.stem + '.pdf'))],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            self.assertEqual(result.returncode == 0, expected == 0, result.stderr.decode())
        self.assertIn(b'shadow called', result.stderr)
