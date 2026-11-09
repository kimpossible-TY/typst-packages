"""Scoped-reference source preview remains conditional and fails closed."""
from pathlib import Path
from copy import deepcopy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_modern_highlight as modern
import document_modern_highlight_preview as preview
import document_cli
import scoped_reference_preview as scoped
from edit_plan import Edit, plan
import test_conditional_modern_highlight as fixtures

SCOPE = b'#import "@local/scoped-annotations:0.3.0": local-tag-scope, local-scope-annotations\n'


@unittest.skipUnless(fixtures.EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ScopedReferencePreviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ConditionalModernHighlightTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        for fixture, namespace, name, version in (
            ('reviewed-packages', 'local', 'scoped-annotations', '0.3.0'),
            ('scoped-reference-packages', 'preview', 'mannot', '0.4.0'),
        ):
            source = TOOL / 'tests/fixtures' / fixture / namespace / name / version
            dest = self.fixture.packages / namespace / name / version
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, dest)

    def run_source(self, body, **options):
        source = fixtures.IMPORT + SCOPE + body
        session, context = self.fixture.session(source)
        analyzer = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)
        result = preview.preview_all(analyzer, allow_scoped_references=True, **options)
        return source, context, analyzer, result

    def test_literal_reference_order_and_owner(self):
        body = (b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") and $x$ '
                b'then #(s.ref)("two") after.]])')
        source, _, analyzer, result = self.run_source(body)
        row, = result['by_file']
        self.assertEqual(row['verification'], 'conditional-modern-highlight-syntax-directives-and-reanalysis', row)
        edit, = row['edits']
        candidate = plan(source, [Edit(edit['start'], edit['end'], edit['replacement'].encode())]).candidate()
        self.assertIn(b'#highlighted[See #(s.ref)("one") and ]$x$'
                      b'#highlighted[ then #(s.ref)("two") after.]', candidate)
        self.assertIn(preview.SCOPED_REFERENCE_CONDITION[0], row['conditions'])
        self.assertFalse(result['runtime_binding_proved'] or result['execution_proved']
                         or result['render_equivalence_proved'] or result['edit_permission']
                         or result['compiler_permission'])
        self.assertFalse(preview.preview_all(analyzer)['by_file'])
        self.assertEqual((self.fixture.project / 'main.typ').read_bytes(), source)

    def test_shadow_mutation_dynamic_calls_and_dictionary_escape_are_refused(self):
        bodies = (
            b'#let s = (ref: x => x)\n#highlighted[See #(s.ref)("one") $x$]',
            b'#(s = (ref: x => x))\n#highlighted[See #(s.ref)("one") $x$]',
            b'#let mutate() = { s.ref = x => x }\n#mutate()\n#highlighted[See #(s.ref)("one") $x$]',
            b'#s.insert("ref", x => x)\n#highlighted[See #(s.ref)("one") $x$]',
            b'#unknown(parent: s)\n#highlighted[See #(s.ref)("one") $x$]',
            b'#let alias = s\n#highlighted[See #(s.ref)("one") $x$]',
            b'#import "unknown.typ": *\n#highlighted[See #(s.ref)("one") $x$]',
            b'#highlighted[See #(s.ref)(side-effect()) $x$]',
            b'#highlighted[See #(s . ref)("one") $x$]',
            b'#highlighted[See #(s.ref)("one", extra: true) $x$]',
            b'#highlighted[See #(s.ref)("one") and #unknown[] $x$]',
        )
        for body in bodies:
            with self.subTest(body=body):
                source, _, _, result = self.run_source(b'#local-tag-scope(s => [' + body + b'])')
                self.assertFalse(any(row['edits'] for row in result['by_file']), result)
                self.assertEqual((self.fixture.project / 'main.typ').read_bytes(), source)

    def test_nested_callback_shadow_and_suppression_are_refused(self):
        nested = b'#local-tag-scope(s => [#local-tag-scope(s => [#highlighted[See #(s.ref)("one") $x$]])])'
        _, _, _, nested_result = self.run_source(nested)
        self.assertEqual(nested_result['previewed_diagnostics'], 1)
        cases = (
            b'#local-tag-scope(s => [#local-tag-scope(s => [#let s = (ref: x => x)\n'
            b'#highlighted[See #(s.ref)("one") $x$]])])',
            b'#local-tag-scope(s => [\n// typst-lint: ignore-next pde.text-only-highlight -- intentional\n'
            b'#highlighted[See #(s.ref)("one") $x$]])',
        )
        for body in cases:
            with self.subTest(body=body):
                _, _, _, result = self.run_source(body)
                self.assertFalse(any(row['edits'] for row in result['by_file']), result)

    def test_forged_diagnostic_and_invalid_context_index_are_refused(self):
        body = b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") $x$]])'
        _, context, analyzer, _ = self.run_source(body)
        tree = context.source_for('main.typ').tree
        diagnostic, = analyzer.analyze(tree, 'main.typ')['diagnostics']
        captured = scoped.CapturedScopedReferences(analyzer, tree, 'main.typ', budget=[scoped.MAX_SCOPE_WORK])
        self.assertTrue(captured.selected(diagnostic, budget=[scoped.MAX_SCOPE_WORK]))
        for ordinal in (-1, -20, False, len(diagnostic['content_context'])):
            with self.subTest(ordinal=ordinal):
                forged = deepcopy(diagnostic)
                forged['callback_context'][-1]['content_context_index'] = ordinal
                self.assertFalse(scoped._scope(tree, forged))
                self.assertFalse(captured.selected(forged, budget=[scoped.MAX_SCOPE_WORK]))
        forged = deepcopy(diagnostic)
        forged['callback_context'][-1]['body_start'] += 1
        self.assertFalse(captured.selected(forged, budget=[scoped.MAX_SCOPE_WORK]))
        self.assertFalse(scoped.selected(analyzer, tree, forged))

    def test_changed_candidate_reference_is_withheld(self):
        body = b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") $x$]])'
        original = scoped._scope
        def changed(tree, diagnostic, *, override=None, budget=None):
            if override is not None:
                return frozenset()
            return original(tree, diagnostic, budget=budget)
        with patch.object(scoped, '_scope', side_effect=changed):
            _, _, _, result = self.run_source(body)
        row, = result['by_file']
        self.assertEqual(row['verification'], 'withheld')
        self.assertIn('Scoped reference binding, order or call count changed', row['reason'])
        self.assertFalse(row['edits'])

    def test_ast_budget_is_explicitly_withheld(self):
        body = b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") $x$]])'
        with patch.object(scoped, 'MAX_SCOPE_WORK', 1):
            _, _, _, result = self.run_source(body)
        row, = result['by_file']
        self.assertEqual(row['verification'], 'withheld')
        self.assertIn('AST work limit exceeded', row['reason'])
        self.assertFalse(row['edits'])

    def test_budget_fallback_preserves_baseline_partial_subset_and_conflicts(self):
        body = (b'#highlighted[Bad $x_1$ end.]\n#highlighted[Good $x$ end.]\n'
                b'#highlighted[More $y$ end.]\n'
                b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") $z$]])')
        _, _, analyzer, _ = self.run_source(body)
        baseline = preview.preview_all(analyzer)
        with patch.object(scoped, 'MAX_SCOPE_WORK', 1):
            result = preview.preview_all(analyzer, allow_scoped_references=True)
        base, = baseline['by_file']
        row, = result['by_file']
        self.assertEqual(base['verification'], row['verification'])
        self.assertEqual(base['edits'], row['edits'])
        self.assertEqual(len(row['edits']), 2)
        self.assertEqual(row['scoped_reference_withheld']['status'], 'WITHHELD')
        self.assertIn('AST work limit', row['scoped_reference_withheld']['reason'])
        self.assertEqual(result['scoped_reference_withheld_source_count'], 1)
        self.assertEqual(result['status'], 'INCOMPLETE')

    def test_budget_fallback_preserves_suppression_and_existing_withholding(self):
        body = (b'// typst-lint: ignore-next core.parenthesized-scripts -- deliberate\n'
                b'#highlighted[Bad $x_1$ end.]\n#highlighted[Good $x$ end.]\n'
                b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") $z$]])')
        _, _, analyzer, _ = self.run_source(body)
        baseline = preview.preview_all(analyzer)
        with patch.object(scoped, 'MAX_SCOPE_WORK', 1):
            result = preview.preview_all(analyzer, allow_scoped_references=True)
        base, = baseline['by_file']
        row, = result['by_file']
        self.assertEqual(base['verification'], 'withheld')
        self.assertEqual(row['verification'], 'withheld')
        self.assertEqual(base['reason'], row['reason'])
        self.assertFalse(row['edits'])
        self.assertEqual(row['scoped_reference_withheld']['status'], 'WITHHELD')
        self.assertEqual(result['status'], 'INCOMPLETE')

    def test_candidate_recheck_budget_falls_back_to_baseline(self):
        body = (b'#highlighted[Good $x$ end.]\n'
                b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") $z$]])')
        _, _, analyzer, _ = self.run_source(body)
        baseline = preview.preview_all(analyzer)
        original = scoped._scope
        def exhausted(tree, diagnostic, *, override=None, budget=None):
            if override is not None:
                raise scoped.ScopedReferenceBudgetExceeded(scoped.BUDGET_REASON)
            return original(tree, diagnostic, budget=budget)
        with patch.object(scoped, '_scope', side_effect=exhausted):
            result = preview.preview_all(analyzer, allow_scoped_references=True)
        self.assertEqual(result['by_file'][0]['edits'], baseline['by_file'][0]['edits'])
        self.assertEqual(result['by_file'][0]['scoped_reference_withheld']['status'], 'WITHHELD')
        self.assertEqual(result['status'], 'INCOMPLETE')

    def test_subset_recheck_budget_propagates_and_preserves_baseline(self):
        body = (b'#highlighted[Bad $x_1$ end.]\n#highlighted[Good $x$ end.]\n'
                b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") $z$]])')
        _, _, analyzer, _ = self.run_source(body)
        baseline = preview.preview_all(analyzer)
        original, count = scoped._scope, [0]
        def exhausted(tree, diagnostic, *, override=None, budget=None):
            if override is not None:
                count[0] += 1
                if count[0] >= 2:
                    raise scoped.ScopedReferenceBudgetExceeded(scoped.BUDGET_REASON)
            return original(tree, diagnostic, override=override, budget=budget)
        with patch.object(scoped, '_scope', side_effect=exhausted):
            result = preview.preview_all(analyzer, allow_scoped_references=True)
        self.assertGreaterEqual(count[0], 2)
        self.assertEqual(result['by_file'][0]['edits'], baseline['by_file'][0]['edits'])
        self.assertEqual(result['by_file'][0]['scoped_reference_withheld']['status'], 'WITHHELD')
        self.assertIn('AST work limit', result['by_file'][0]['scoped_reference_withheld']['reason'])
        self.assertEqual(result['status'], 'INCOMPLETE')

    def test_combined_subset_budget_reason_is_not_lost(self):
        body = (b'#highlighted[Bad $x_1$ end.]\n#highlighted[Good $x$ end.]\n'
                b'#highlighted[More $y$ end.]\n'
                b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") $z$]])')
        _, _, analyzer, _ = self.run_source(body)
        baseline = preview.preview_all(analyzer)
        original, single_success = preview._preview_source, [0]
        def exhausted(evidence, tree, path, diagnostics, records, **kwargs):
            result = original(evidence, tree, path, diagnostics, records, **kwargs)
            if kwargs.get('allow_scoped_references') and len(diagnostics) == 1 and result and result['verification'] != 'withheld':
                single_success[0] += 1
            if (kwargs.get('allow_scoped_references') and single_success[0] >= 2
                    and len(diagnostics) >= 2 and result):
                return dict(result, verification='withheld', edits=[], reason=scoped.BUDGET_REASON)
            return result
        with patch.object(preview, '_preview_source', side_effect=exhausted):
            result = preview.preview_all(analyzer, allow_scoped_references=True)
        self.assertGreaterEqual(single_success[0], 2)
        self.assertEqual(result['by_file'][0]['edits'], baseline['by_file'][0]['edits'])
        self.assertEqual(result['by_file'][0]['scoped_reference_withheld']['status'], 'WITHHELD')
        self.assertEqual(result['status'], 'INCOMPLETE')

    def test_inspect_requires_explicit_opt_in_and_never_compiles(self):
        body = b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") $x$]])'
        source, _, _, _ = self.run_source(body)
        kwargs = dict(rule_set='pde', package_path=str(self.fixture.packages),
                      footnote_cache_path=str(self.fixture.cache), modern_highlight_evidence=True,
                      preview_conditional_modern_highlights=True)
        default = document_cli.report_document(str(self.fixture.project), 'main.typ', **kwargs)
        selected = document_cli.report_document(str(self.fixture.project), 'main.typ',
            preview_conditional_modern_scoped_references=True, **kwargs)
        self.assertFalse(default['conditional_modern_highlight_previews']['by_file'])
        self.assertEqual(selected['conditional_modern_highlight_previews']['previewed_diagnostics'], 1)
        self.assertFalse(selected['compiler_invoked'] or selected['applied_fixes'])
        self.assertEqual((self.fixture.project / 'main.typ').read_bytes(), source)
        command = [sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
                   '--root', str(self.fixture.project), '--entrypoint', 'main.typ',
                   '--rule-set', 'pde', '--package-path', str(self.fixture.packages),
                   '--footnote-cache-path', str(self.fixture.cache), '--modern-highlight-evidence',
                   '--preview-conditional-modern-highlights',
                   '--preview-conditional-modern-scoped-references', '--format', 'json']
        run = subprocess.run(command, capture_output=True, text=True, timeout=30)
        cli = json.loads(run.stdout)
        self.assertEqual(run.returncode, cli['status'])
        self.assertEqual(cli['conditional_modern_highlight_previews']['previewed_diagnostics'], 1)
        self.assertFalse(cli['compiler_invoked'] or cli['applied_fixes'])
        with self.assertRaisesRegex(ValueError, 'Scoped reference previews require'):
            document_cli.report_document(str(self.fixture.project), 'main.typ', rule_set='pde',
                                         preview_conditional_modern_scoped_references=True)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst')
                         and shutil.which('pdftotext'), 'Opt in with Typst and pdftotext')
    def test_typst_fixture_reference_and_render_are_not_claimed_equivalent(self):
        body = (b'#show ref: it => [CUSTOMREF]\n#local-tag-scope(s => [\n#figure([Target], kind: "test", supplement: [Test])#(s.tag)("one")\n'
                b'#highlighted[See #(s.ref)("one") before $x$ after.]\n])')
        source, _, _, result = self.run_source(body)
        row, = result['by_file']
        edit, = row['edits']
        candidate = plan(source, [Edit(edit['start'], edit['end'], edit['replacement'].encode())]).candidate()
        # The pinned m annot entrypoint is real. Its uncalled transitive modules
        # are explicit local stubs; scoped reference does not call these APIs.
        src = self.fixture.packages / 'preview/mannot/0.4.0/src'
        (src / 'mark.typ').write_text('\n'.join('#let ' + n + ' = none' for n in
            ('core-mark', 'mark', 'markhl', 'markrect', 'markub', 'markul', 'markuw')))
        (src / 'annot.typ').write_text('\n'.join('#let ' + n + ' = none' for n in
            ('annot', 'annot-cetz', 'core-annot', 'tiptoe')))
        with tempfile.TemporaryDirectory(prefix='scoped-highlight-render-') as folder:
            root = Path(folder)
            for name, data in (('before', source), ('after', candidate)):
                typ, pdf = root / (name + '.typ'), root / (name + '.pdf')
                typ.write_bytes(data)
                run = subprocess.run(['typst', 'compile', '--root', str(root), '--package-path',
                    str(self.fixture.packages), '--package-cache-path', str(self.fixture.cache),
                    '--creation-timestamp', '0', str(typ), str(pdf)], capture_output=True, timeout=30)
                self.assertEqual(run.returncode, 0, run.stderr.decode())
                text = subprocess.run(['pdftotext', str(pdf), '-'], check=True, capture_output=True).stdout
                self.assertIn(b'Target', text)
                self.assertEqual(text.count(b'CUSTOMREF'), 1, text)
        self.assertFalse(row['render_equivalence_proved'])
