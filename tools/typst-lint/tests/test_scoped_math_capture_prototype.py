"""Experimental callback math capture remains one-site and conditional."""
import json
import subprocess
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import scoped_math_capture_prototype as proof
import document_cli
import document_modern_highlight_preview as preview
from edit_plan import Edit, plan
from typst_lint import InputError, SafetyError
from test_document_modern_highlight_scoped_preview import ScopedReferencePreviewTests
import test_conditional_modern_highlight as fixtures


@unittest.skipUnless(fixtures.EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ScopedMathCapturePrototypeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ScopedReferencePreviewTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def issue(self, body):
        source, context, analyzer, _ = self.fixture.run_source(body)
        tree = context.source_for('main.typ').tree
        findings = analyzer.analyze(tree, 'main.typ')['diagnostics']
        return source, analyzer, findings

    def test_reviewed_callback_math_is_one_conditional_source_plan(self):
        body = b'#local-tag-scope(s => [#highlighted[Before $cal(tilde(X))$ after.]])'
        source, analyzer, findings = self.issue(body)
        finding, = findings
        result = proof.preview_one(analyzer, 'main.typ', finding['start'])
        self.assertEqual(result['verification'], 'conditional-modern-highlight-syntax-directives-and-reanalysis')
        edit, = result['edits']
        candidate = plan(source, [Edit(edit['start'], edit['end'], edit['replacement'].encode())]).candidate()
        self.assertIn(b'#highlighted[Before ]$cal(tilde(X))$#highlighted[ after.]', candidate)
        for name in ('runtime_binding_proved', 'execution_proved', 'render_equivalence_proved',
                     'edit_permission', 'compiler_permission'):
            self.assertFalse(result[name])
        self.assertEqual((self.fixture.fixture.project / 'main.typ').read_bytes(), source)
        self.assertFalse(preview.preview_all(analyzer, reviewed_math=True)['by_file'])

    def test_shadow_unknown_math_and_unsafe_body_are_withheld(self):
        highlight = b'#highlighted[Text $cal(X)$ end.]'
        cases = (
            b'#let cal(x) = panic("shadow")\n#local-tag-scope(s => [' + highlight + b'])',
            b'#local-tag-scope(s => [#let cal(x) = x\n' + highlight + b'])',
            b'#local-tag-scope(s => [' + highlight + b'\n#let later = 1])',
            b'#local-tag-scope(s => [#if true [hello]\n' + highlight + b'])',
            b'#local-tag-scope(s => [#unknown(x => [x])\n' + highlight + b'])',
            b'#local-tag-scope(s => [#import "missing.typ": *\n' + highlight + b'])',
            b'#local-tag-scope(s => [#highlighted[Text $cal(#panic("x"))$ end.]])',
            b'#local-tag-scope(s => [#highlighted[Text $unknown(X)$ end.]])',
            b'#local-tag-scope(s => [#highlighted[Text #(s.ref)("one") $cal(X)$ end.]])',
        )
        for body in cases:
            with self.subTest(body=body):
                _, analyzer, findings = self.issue(body)
                for finding in findings:
                    try:
                        result = proof.preview_one(analyzer, 'main.typ', finding['start'])
                    except (InputError, SafetyError):
                        continue
                    self.assertEqual(result['verification'], 'withheld')
                    self.assertFalse(result['edits'])

    def test_modified_wrapper_and_forged_analyzer_findings_are_refused(self):
        wrapper = self.fixture.fixture.packages / 'local/scoped-annotations/0.3.0/lib.typ'
        wrapper.write_bytes(wrapper.read_bytes() + b'\n// different package bytes\n')
        source, analyzer, _ = self.issue(b'#local-tag-scope(s => [#highlighted[Text $cal(X)$ end.]])')
        with self.assertRaisesRegex(SafetyError, 'reviewed callback wrapper'):
            proof.preview_one(analyzer, 'main.typ', source.rindex(b'highlighted'))

    def test_forged_instance_analyzer_methods_are_not_trusted(self):
        _, analyzer, findings = self.issue(b'#local-tag-scope(s => [#highlighted[Text $cal(X)$ end.]])')
        real = findings[0]
        forged = dict(real, start=real['start'] + 1)
        report = {'diagnostics': [forged]}
        with patch.object(analyzer, 'analyze', return_value=report), patch.object(analyzer, '_analyze_one', return_value=report):
            with self.assertRaisesRegex(InputError, 'No unique active'):
                proof.preview_one(analyzer, 'main.typ', forged['start'])

    def test_common_preview_does_not_accept_arbitrary_resolver_factory(self):
        _, analyzer, findings = self.issue(b'#local-tag-scope(s => [#highlighted[Text $unknown(X)$ end.]])')
        tree = analyzer._session._context.source_for('main.typ').tree
        with self.assertRaises(TypeError):
            preview._preview_source(analyzer, tree, 'main.typ', findings, [],
                                    candidate_math_resolver=lambda *args: object())

    def test_budget_missing_location_and_candidate_tamper_fail_closed(self):
        source, analyzer, findings = self.issue(b'#local-tag-scope(s => [#highlighted[Text $cal(X)$ end.]])')
        start = findings[0]['start']
        with patch.object(proof, 'MAX_BODY_NODES', 1):
            with self.assertRaisesRegex(SafetyError, 'work budget'):
                proof.preview_one(analyzer, 'main.typ', start)
        with self.assertRaises(InputError):
            proof.preview_one(analyzer, 'main.typ', start + 1)
        original = proof.parse
        def tamper(data, *args, **kwargs):
            return original(data.replace(b'cal(X)', b'op(X)'), *args, **kwargs)
        with patch.object(proof, 'parse', side_effect=tamper):
            result = proof.preview_one(analyzer, 'main.typ', start)
        self.assertEqual(result['verification'], 'withheld')
        self.assertFalse(result['edits'])
        self.assertEqual((self.fixture.fixture.project / 'main.typ').read_bytes(), source)

    def test_cli_is_explicit_independent_bounded_and_preserves_baseline(self):
        source, _, findings = self.issue(b'#local-tag-scope(s => [#highlighted[Text $cal(X)$ end.]])')
        start = findings[0]['start']
        common = dict(root=str(self.fixture.fixture.project), entrypoint='main.typ', rule_set='pde',
                      package_path=str(self.fixture.fixture.packages), footnote_cache_path=str(self.fixture.fixture.cache),
                      modern_highlight_evidence=True, preview_conditional_modern_combined_highlights=True)
        baseline = document_cli.report_document(**common)
        self.assertNotIn('conditional_modern_scoped_math_preview', baseline)
        result = document_cli.report_document(**common, preview_conditional_modern_scoped_math=('main.typ', start))
        self.assertEqual(baseline['conditional_modern_combined_previews'], result['conditional_modern_combined_previews'])
        row = result['conditional_modern_scoped_math_preview']
        self.assertEqual(row['verification'], 'conditional-modern-highlight-syntax-directives-and-reanalysis')
        self.assertEqual(len(row['edits']), 1)
        self.assertFalse(result['compiler_invoked'] or result['applied_fixes'])
        withheld = document_cli.report_document(**common, preview_conditional_modern_scoped_math=('main.typ', start + 1))
        self.assertEqual(withheld['conditional_modern_scoped_math_preview']['verification'], 'withheld')
        self.assertEqual(withheld['conditional_modern_combined_previews'], baseline['conditional_modern_combined_previews'])
        for path in ('../main.typ', '/etc/passwd'):
            with self.subTest(path=path):
                other = document_cli.report_document(**common, preview_conditional_modern_scoped_math=(path, start))
                self.assertEqual(other['conditional_modern_scoped_math_preview']['verification'], 'withheld')
                self.assertFalse(other['conditional_modern_scoped_math_preview']['edits'])
                self.assertEqual(other['conditional_modern_combined_previews'], baseline['conditional_modern_combined_previews'])
        for offset in (-1, True, '１２', '1.2', '1234567890123'):
            with self.subTest(offset=offset), self.assertRaises(InputError):
                document_cli.report_document(**common, preview_conditional_modern_scoped_math=('main.typ', offset))
        size = len(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8'))
        with patch.object(document_cli, 'MAX_MODERN_REPORT_BYTES', size):
            self.assertEqual(result, document_cli.report_document(**common,
                    preview_conditional_modern_scoped_math=('main.typ', start)))
        with patch.object(document_cli, 'MAX_MODERN_REPORT_BYTES', size - 1):
            with self.assertRaises(InputError):
                document_cli.report_document(**common, preview_conditional_modern_scoped_math=('main.typ', start))
        low, high = 1, 2048
        while low < high:
            middle = (low + high) // 2
            try:
                with patch.object(document_cli, 'MAX_REPORT_ITEMS', middle):
                    document_cli.report_document(**common, preview_conditional_modern_scoped_math=('main.typ', start))
            except InputError:
                low = middle + 1
            else:
                high = middle
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', low):
            self.assertEqual(result, document_cli.report_document(**common,
                    preview_conditional_modern_scoped_math=('main.typ', start)))
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', low - 1):
            with self.assertRaises(InputError):
                document_cli.report_document(**common, preview_conditional_modern_scoped_math=('main.typ', start))
        self.assertEqual((self.fixture.fixture.project / 'main.typ').read_bytes(), source)

    def test_cli_flag_requires_combined_and_displays_independence(self):
        source, _, findings = self.issue(b'#local-tag-scope(s => [#highlighted[Text $cal(X)$ end.]])')
        command = [sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
                   '--root', str(self.fixture.fixture.project), '--entrypoint', 'main.typ',
                   '--rule-set', 'pde', '--package-path', str(self.fixture.fixture.packages),
                   '--footnote-cache-path', str(self.fixture.fixture.cache), '--modern-highlight-evidence',
                   '--preview-conditional-modern-scoped-math', 'main.typ', str(findings[0]['start'])]
        missing = subprocess.run(command + ['--format', 'json'], capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(missing.stdout)['phase'], 'input')
        run = subprocess.run(command + ['--preview-conditional-modern-combined-highlights', '--format', 'text'],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertIn('Do not merge this plan with other previews', run.stdout)
        self.assertEqual((self.fixture.fixture.project / 'main.typ').read_bytes(), source)

    @unittest.skipUnless(shutil.which('typst'), 'Requires Typst')
    def test_pinned_typst_captures_and_protects_outer_binding(self):
        if not subprocess.check_output(['typst', '--version'], text=True).startswith('typst 0.15.1 '):
            self.skipTest('Prototype pins Typst 0.15.1')
        frozen = subprocess.run(['typst', 'eval', '{ let cal = math.cal; let f(s) = [$cal(X)$]; '
                                 'cal = x => panic("shadow"); f(none) }'], capture_output=True, text=True)
        self.assertEqual(frozen.returncode, 0, frozen.stderr)
        write = subprocess.run(['typst', 'eval', '{ let cal = math.cal; let f() = { cal = x => x }; f() }'],
                               capture_output=True, text=True)
        self.assertNotEqual(write.returncode, 0)
        self.assertIn('read-only', write.stderr)


if __name__ == '__main__':
    unittest.main()
