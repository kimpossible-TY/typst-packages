"""Independent, conditional scoped-field source preview."""
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
import document_conditional_scoped_calls as scoped
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE, parse
from typst_lint import digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalScopedCallTests(unittest.TestCase):
    def analyze(self, source):
        return scoped.analyze(parse(source), 'main.typ')

    def candidate(self, source):
        report = self.analyze(source)
        self.assertEqual(report['status'], 'CONDITIONAL', report)
        self.assertFalse(report['runtime_binding_proved'] or report['execution_proved']
                         or report['edit_permission'] or report['compiler_permission'])
        self.assertTrue(report['conditions'])
        edits = [Edit(e['start'], e['end'], e['replacement'].encode()) for e in report['edits']]
        candidate = plan(source, edits).candidate()
        self.assertEqual(report['source_sha256'], digest(source))
        self.assertEqual(report['candidate_sha256'], digest(candidate))
        self.assertFalse(self.analyze(candidate)['edits'])
        return report, candidate

    def test_scoped_calls_are_conditional_and_keep_arguments_and_trivia(self):
        for source, expected in (
                (b'#local-tag-scope(s => [#s.tag(/*keep*/ "x")])',
                 b'#local-tag-scope(s => [#(s.tag)(/*keep*/ "x")])'),
                (b'#local-scope-annotations(t => [$ x #t.mark("x") $])',
                 b'#local-scope-annotations(t => [$ x #(t.mark)("x") $])'),
                ('#local-tag-scope(s => [한글 #s.ref("x")])'.encode(),
                 '#local-tag-scope(s => [한글 #(s.ref)("x")])'.encode())):
            with self.subTest(source=source):
                result, candidate = self.candidate(source)
                self.assertEqual(candidate, expected)
                self.assertEqual(len(result['diagnostics']), 1)

    def test_multiple_edits_and_existing_suppression_are_preserved(self):
        source = (b'// typst-lint: ignore-next core.scoped-function-call -- deliberate\n'
                  b'#local-tag-scope(s => [#s.tag("ignored")])\n'
                  b'#local-tag-scope(t => [#t.tag("x") #t.ref("y")])')
        report, candidate = self.candidate(source)
        self.assertEqual(len(report['suppressed']), 1)
        self.assertEqual(len(report['edits']), 2)
        self.assertIn(b'#s.tag("ignored")', candidate)
        self.assertIn(b'#(t.tag)("x") #(t.ref)("y")', candidate)

    def test_literal_name_parent_identifier_and_already_fixed_calls(self):
        source = (b'#local-scope-annotations(s => [#(s.tag)("done") #s.ref("x")], '
                  b'parent: outer, name: "child")')
        result, candidate = self.candidate(source)
        self.assertEqual(len(result['edits']), 1)
        self.assertEqual(candidate, source.replace(b'#s.ref', b'#(s.ref)'))

    def test_ambiguous_options_and_already_fixed_call_with_foreign_receiver(self):
        for source in (b'#local-scope-annotations(s => [#s.tag("x")], parent: build())',
                       b'#local-scope-annotations(s => [#s.tag("x")], name: dynamic)',
                       b'#local-tag-scope(s => [#(other.tag)("x") #s.ref("y")])'):
            with self.subTest(source=source):
                self.assertFalse(self.analyze(source)['edits'])

    def test_wrapper_parameter_shadow_and_outer_deferred_context_are_refused(self):
        cases = (
            b'#let run(local-tag-scope) = [#local-tag-scope(s => [#s.tag("x")])]\n#run(none)',
            b'#for local-tag-scope in (none,) [#local-tag-scope(s => [#s.tag("x")])]',
            b'#let run() = [#local-tag-scope(s => [#s.tag("x")])]',
            b'#context [#local-tag-scope(s => [#s.tag("x")])]',
            b'#if flag [#local-tag-scope(s => [#s.tag("x")])]',
        )
        for source in cases:
            with self.subTest(source=source):
                report = self.analyze(source)
                self.assertEqual(report['status'], 'CONDITIONAL', report)
                self.assertFalse(report['diagnostics'] or report['edits'])
                self.assertTrue(any('unsupported deferred or dynamic scope' in reason
                                    for reason in report['unresolved']))

    def test_ambiguous_or_shadowed_callbacks_are_skipped(self):
        examples = (
            b'#let local-tag-scope(f) = f((:))\n#local-tag-scope(s => [#s.tag("x")])',
            b'#local-tag-scope((s, t) => [#s.tag("x")])',
            b'#local-tag-scope(s => [#s.mark("x")])',
            b'#local-tag-scope(s => [#unknown(s) #s.tag("x")])',
            b'#local-tag-scope(s => [#let s = (:)\n#s.tag("x")])',
            b'#local-tag-scope(s => [#context s.tag("x")])',
            b'#local-tag-scope(s => [#s.tag("x")], prefix: dynamic)',
            b'#s.tag("x")',
        )
        for source in examples:
            with self.subTest(source=source):
                result = self.analyze(source)
                self.assertFalse(result['diagnostics'] or result['edits'], result)

    def test_parse_and_candidate_failures_withhold_all_edits(self):
        result = self.analyze(b'#local-tag-scope(s => [#s.tag("x")')
        self.assertEqual(result['status'], 'WITHHELD')
        self.assertFalse(result['edits'])
        source = b'#local-tag-scope(s => [#s.tag("x")])'
        with patch.object(scoped, 'parse', side_effect=ValueError('fixture failure')):
            result = self.analyze(source)
        self.assertEqual(result['status'], 'WITHHELD')
        self.assertFalse(result['edits'])
        self.assertIn('fixture failure', result['unresolved'][0])

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1 and Typst')
    def test_owned_scope_fixture_candidate_compiles_without_source_write(self):
        source = b'#import "wrapper.typ": local-tag-scope\n#local-tag-scope(s => [#s.tag("x")])'
        _, candidate = self.candidate(source)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'wrapper.typ').write_bytes(b'#let local-tag-scope(f) = f((tag: x => label(x)))')
            original, fixed = root / 'original.typ', root / 'candidate.typ'
            original.write_bytes(source)
            fixed.write_bytes(candidate)
            before = subprocess.run(['typst', 'compile', '--root', str(root), str(original),
                                     str(root / 'original.pdf')], capture_output=True, timeout=30)
            after = subprocess.run(['typst', 'compile', '--root', str(root), str(fixed),
                                    str(root / 'candidate.pdf')], capture_output=True, timeout=30)
            self.assertNotEqual(before.returncode, 0)
            self.assertEqual(after.returncode, 0, after.stderr)
            self.assertEqual(original.read_bytes(), source)
