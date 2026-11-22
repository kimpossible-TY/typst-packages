"""Deferred scoped callback placement is reported only with explicit conditions."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import document_conditional_detached_annotations as check
from syntax_client import EXECUTABLE


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class DeferredDetachedAnnotations(unittest.TestCase):
    def test_nested_wrapper_reports_dynamic_and_root_stays_distinct(self):
        call = b'#local-scope-annotations(s => [$ x $ #(s.annot)(("x",), none, none)])'
        root = check.analyze(call, 'main.typ')
        deferred = check.analyze(b'#proof[' + call + b']', 'main.typ')
        self.assertEqual(len(root['diagnostics']), 1)
        self.assertEqual(len(deferred['diagnostics']), 1)
        self.assertFalse(root['diagnostics'][0]['outer_dynamic_context'])
        self.assertTrue(deferred['diagnostics'][0]['outer_dynamic_context'])
        self.assertTrue(deferred['conditions'])
        self.assertFalse(deferred['runtime_binding_proved'] or deferred['execution_proved']
                         or deferred['coverage_complete'] or deferred['edit_permission'])

    def test_shadow_and_display_constructor_are_unresolved(self):
        call = b'#local-scope-annotations(s => [$ x $ #(s.annot)(("x",), none, none)])'
        variants = (b'#let f(local-scope-annotations) = [' + call + b']',
                    b'#for local-scope-annotations in (1,) [' + call + b']',
                    b'$ ' + call + b' $', b'#equation([' + call + b'])',
                    b'#math.equation([' + call + b'])')
        for source in variants:
            with self.subTest(source=source):
                result = check.analyze(source, 'main.typ')
                self.assertFalse(result['diagnostics'], result)
                self.assertTrue(result['unresolved'], result)

    def test_parameter_escape_before_call_and_suppression(self):
        bad = (b'#proof[#local-scope-annotations(s => ['
               b'#unknown(s) #(s.annot)(("x",), none, none)])]')
        result = check.analyze(bad, 'main.typ')
        self.assertFalse(result['diagnostics']); self.assertTrue(result['unresolved'])
        suppressed = (b'#proof[#local-scope-annotations(s => [\n'
                      b'// typst-lint: ignore-next core.annotation-in-display -- intentional\n'
                      b'#(s.annot)(("x",), none, none)\n])]')
        result = check.analyze(suppressed, 'main.typ')
        self.assertFalse(result['diagnostics']); self.assertEqual(len(result['suppressed']), 1)

    def test_annotation_arguments_cannot_escape_or_mutate_receiver(self):
        args = (b'mutate(s), none, none', b's.insert("x", 1), none, none',
                b's.remove("annot"), none, none', b's, none, none',
                b'none, none, { mutate(s) }', b'none, none, { import cetz.draw: *; (s.node)("x") }',
                b'apply((s.node)), none, none')
        for arg in args:
            with self.subTest(argument=arg):
                source = b'#proof[#local-scope-annotations(s => [#(s.annot)(' + arg + b')])]'
                result = check.analyze(source, 'main.typ')
                self.assertFalse(result['diagnostics'], result)
                self.assertTrue(result['unresolved'], result)
        safe = b'#proof[#local-scope-annotations(s => [#(s.annot)((s.node)("x"), none, { draw })])]'
        self.assertEqual(len(check.analyze(safe, 'main.typ')['diagnostics']), 1)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with reviewed Typst')
    def test_real_typst_mutating_argument_is_withheld(self):
        source = (b'#import "helpers.typ": *\n'
                  b'#proof[#local-scope-annotations(s => [#(s.annot)(s.insert("new", 1), none, none)])]')
        helper = (b'#let local-scope-annotations(f) = f((annot: (a, b, c) => [note]))\n'
                  b'#let proof(body) = block(body)\n')
        result = check.analyze(source, 'main.typ')
        self.assertFalse(result['diagnostics']); self.assertTrue(result['unresolved'])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'main.typ').write_bytes(source); (root / 'helpers.typ').write_bytes(helper)
            run = subprocess.run(['typst', 'compile', '--root', str(root), str(root / 'main.typ'),
                                  str(root / 'out.pdf')], capture_output=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr.decode('utf-8', 'replace'))
            self.assertTrue((root / 'out.pdf').read_bytes().startswith(b'%PDF-'))
            self.assertEqual((root / 'main.typ').read_bytes(), source)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with reviewed Typst')
    def test_actual_typst_deferred_wrapper_runs_without_mutating_source(self):
        source = (b'#import "helpers.typ": *\n'
                  b'#proof[#local-scope-annotations(s => [$ x $ #(s.annot)(("x",), none, none)])]')
        helper = (b'#let local-scope-annotations(f) = f((annot: (targets, c, d) => [note]))\n'
                  b'#let proof(body) = block(body)\n')
        result = check.analyze(source, 'main.typ')
        self.assertEqual(len(result['diagnostics']), 1)
        self.assertFalse(result['runtime_binding_proved'])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'main.typ').write_bytes(source)
            (root / 'helpers.typ').write_bytes(helper)
            run = subprocess.run(['typst', 'compile', '--root', str(root), str(root / 'main.typ'),
                                  str(root / 'out.pdf')], capture_output=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stderr.decode('utf-8', 'replace'))
            self.assertTrue((root / 'out.pdf').read_bytes().startswith(b'%PDF-'))
            self.assertEqual((root / 'main.typ').read_bytes(), source)
