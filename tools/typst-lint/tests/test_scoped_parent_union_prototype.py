"""Parent source union preserves independent plans and rechecks the candidate."""
from pathlib import Path
import sys
import json
import subprocess
from dataclasses import replace
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import scoped_parent_union_prototype as union
import scoped_parent_transfer_prototype as parent
import document_cli
from typst_lint import InputError, SafetyError
import test_conditional_modern_highlight as fixtures
from test_scoped_parent_transfer_prototype import ParentTransferPrototypeTests


@unittest.skipUnless(fixtures.EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ParentUnionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ParentTransferPrototypeTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def issue(self):
        body = (b'#local-tag-scope(s => [#mannot-scope(m => [#(m.tag)("a")], parent: s, name: "one")\n'
                b'#highlighted[One #(s.ref)("a") $x$ end.]\n'
                b'#highlighted[Two #(s.ref)("b") $y_1$ end.]])')
        source, analyzer, diagnostics = self.fixture.issue(body)
        return source, analyzer, tuple(d['start'] for d in diagnostics)

    def test_union_rechecks_all_edits_and_preserves_original(self):
        source, analyzer, starts = self.issue()
        result = union.preview_union(analyzer, 'main.typ', starts)
        self.assertEqual(result['status'], 'CONDITIONAL', result)
        self.assertEqual(len(result['baseline']), 2)
        self.assertEqual(len(result['union']['edits']), 2)
        self.assertTrue(result['source_union_revalidated'])
        self.assertFalse(result['composition_proved'] or result['edit_permission'] or result['compiler_permission'])
        self.assertEqual((self.fixture.fixture.fixture.project / 'main.typ').read_bytes(), source)

    def test_union_reports_captured_helper_binding_condition(self):
        body = (b'#local-tag-scope(s => [#mannot-scope(m => [\n'
                b'#let markchild(body) = { set math.equation(numbering: none)\n'
                b'markub(body, tag: (m.tag)("x"), bracket: sym.brace.b) }\n'
                b'$markchild(x)$], parent: s, name: "one")\n'
                b'#highlighted[One #(s.ref)("a") $x$ end.]\n'
                b'#highlighted[Two #(s.ref)("b") $y$ end.]])')
        _, analyzer, diagnostics = self.fixture.issue(body)
        result = union.preview_union(analyzer, 'main.typ', tuple(d['start'] for d in diagnostics))
        self.assertEqual(result['status'], 'CONDITIONAL', result)
        for row in [result, result['union'], *result['baseline']]:
            self.assertIn(parent.CAPTURE_CONDITIONS[0], row['conditions'])
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
        byte_limit = len(encoded.encode('utf-8')) + 2 * encoded.count('\n')
        starts = tuple(d['start'] for d in diagnostics)
        self.assertEqual(union.preview_union(analyzer, 'main.typ', starts, max_bytes=byte_limit), result)
        with self.assertRaisesRegex(InputError, 'byte budget'):
            union.preview_union(analyzer, 'main.typ', starts, max_bytes=byte_limit - 1)
        original = parent._preview_selected
        def refuse(evidence, path, offsets, **kwargs):
            if len(offsets) > 1:
                raise SafetyError('forced union refusal')
            return original(evidence, path, offsets, **kwargs)
        with patch.object(parent, '_preview_selected', side_effect=refuse):
            withheld = union.preview_union(analyzer, 'main.typ', tuple(d['start'] for d in diagnostics))
        self.assertEqual(withheld['status'], 'INCOMPLETE')
        self.assertEqual(len(withheld['baseline']), 2)
        self.assertIn(parent.CAPTURE_CONDITIONS[0], withheld['conditions'])

    def test_union_failure_preserves_both_baselines(self):
        _, analyzer, starts = self.issue()
        original = parent._preview_selected
        def refuse(evidence, path, offsets, **kwargs):
            if len(offsets) > 1:
                raise SafetyError('forced candidate refusal')
            return original(evidence, path, offsets, **kwargs)
        with patch.object(parent, '_preview_selected', side_effect=refuse):
            result = union.preview_union(analyzer, 'main.typ', starts)
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertIsNone(result['union'])
        self.assertEqual(len(result['baseline']), 2)
        self.assertIn('forced candidate refusal', result['reason'])

    def test_overlapping_union_edits_withhold_only_union(self):
        import document_modern_highlight_preview as preview
        _, analyzer, starts = self.issue()
        original = preview._select
        def overlap(tree, diagnostics, **kwargs):
            edits, plans = original(tree, diagnostics, **kwargs)
            if len(diagnostics) > 1:
                edits = (edits[0], replace(edits[1], start=edits[0].start + 1))
            return edits, plans
        with patch.object(preview, '_select', side_effect=overlap):
            result = union.preview_union(analyzer, 'main.typ', starts)
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertEqual(len(result['baseline']), 2)
        self.assertIsNone(result['union'])
        self.assertIn('conflict', result['reason'].lower())

    def test_candidate_route_tamper_withholds_union_preserves_baseline(self):
        _, analyzer, starts = self.issue()
        original = parent._prove_tree
        candidate_calls = 0
        def tamper(*args, **kwargs):
            nonlocal candidate_calls
            result = original(*args, **kwargs)
            if kwargs.get('graph_override') is not None:
                candidate_calls += 1
                if candidate_calls == 3:
                    result['parents'][0]['route'][0]['source_sha256'] = '0' * 64
            return result
        with patch.object(parent, '_prove_tree', side_effect=tamper):
            result = union.preview_union(analyzer, 'main.typ', starts)
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertEqual(len(result['baseline']), 2)
        self.assertIn('route changed', result['reason'])

    def test_distinct_callbacks_are_withheld_after_independent_plans(self):
        body = (b'#local-tag-scope(s => [#mannot-scope(m => [#(m.tag)("a")], parent: s, name: "one")\n'
                b'#highlighted[One #(s.ref)("a") $x$ end.]])\n'
                b'#local-tag-scope(s => [#mannot-scope(m => [#(m.tag)("b")], parent: s, name: "two")\n'
                b'#highlighted[Two #(s.ref)("b") $y$ end.]])')
        _, analyzer, diagnostics = self.fixture.issue(body)
        result = union.preview_union(analyzer, 'main.typ', tuple(d['start'] for d in diagnostics))
        self.assertEqual(len(result['baseline']), 2)
        self.assertIsNone(result['union'])
        self.assertIn('share one issued callback', result['reason'])

    def test_cli_preserves_existing_reports_and_fails_closed_on_limits(self):
        source, _, starts = self.issue()
        f = self.fixture.fixture.fixture
        common = dict(root=str(f.project), entrypoint='main.typ', rule_set='pde',
                      package_path=str(f.packages), footnote_cache_path=str(f.cache),
                      modern_highlight_evidence=True, preview_conditional_modern_combined_highlights=True,
                      preview_conditional_modern_literal_footnotes=True,
                      preview_conditional_modern_parent_reference=('main.typ', starts[0]))
        baseline = document_cli.report_document(**common)
        pairs = [('main.typ', start) for start in starts]
        result = document_cli.report_document(**common, preview_conditional_modern_parent_union=pairs)
        for key in ('conditional_modern_combined_previews', 'conditional_modern_literal_footnote_previews',
                    'conditional_modern_parent_reference_preview'):
            self.assertEqual(baseline[key], result[key])
        row = result['conditional_modern_parent_union']
        self.assertTrue(row['source_union_revalidated'])
        self.assertEqual(len(row['union']['edits']), 2)
        self.assertFalse(result['compiler_invoked'] or result['applied_fixes'])
        original_selected = parent._preview_selected
        def refuse_union(evidence, path, offsets, **kwargs):
            if len(offsets) > 1:
                raise SafetyError('union withheld')
            return original_selected(evidence, path, offsets, **kwargs)
        with patch.object(parent, '_preview_selected', side_effect=refuse_union):
            refused = document_cli.report_document(**common, preview_conditional_modern_parent_union=pairs)
        for key in ('conditional_modern_combined_previews', 'conditional_modern_literal_footnote_previews',
                    'conditional_modern_parent_reference_preview'):
            self.assertEqual(baseline[key], refused[key])
        self.assertEqual(len(refused['conditional_modern_parent_union']['baseline']), 2)
        self.assertFalse(refused['conditional_modern_parent_union']['source_union_revalidated'])
        size = len(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8'))
        with patch.object(document_cli, 'MAX_MODERN_REPORT_BYTES', size):
            self.assertEqual(document_cli.report_document(**common, preview_conditional_modern_parent_union=pairs), result)
        with patch.object(document_cli, 'MAX_MODERN_REPORT_BYTES', size - 1):
            with self.assertRaises(InputError):
                document_cli.report_document(**common, preview_conditional_modern_parent_union=pairs)
        low, high = 1, 2048
        while low < high:
            middle = (low + high) // 2
            try:
                with patch.object(document_cli, 'MAX_REPORT_ITEMS', middle):
                    document_cli.report_document(**common, preview_conditional_modern_parent_union=pairs)
            except InputError:
                low = middle + 1
            else:
                high = middle
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', low):
            self.assertEqual(document_cli.report_document(**common, preview_conditional_modern_parent_union=pairs), result)
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', low - 1):
            with self.assertRaises(InputError):
                document_cli.report_document(**common, preview_conditional_modern_parent_union=pairs)
        for bad in ([pairs[0], pairs[0]], [('main.typ', -1), pairs[0]],
                    [('main.typ', True), pairs[0]], [('main.typ', '１２'), pairs[0]],
                    [('other.typ', starts[0]), pairs[1]]):
            with self.subTest(bad=bad), self.assertRaises(InputError):
                document_cli.report_document(**common, preview_conditional_modern_parent_union=bad)
        self.assertEqual((f.project / 'main.typ').read_bytes(), source)

    def test_cli_text_and_opt_in_are_explicit(self):
        _, _, starts = self.issue()
        f = self.fixture.fixture.fixture
        command = [sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
                   '--root', str(f.project), '--entrypoint', 'main.typ', '--rule-set', 'pde',
                   '--package-path', str(f.packages), '--footnote-cache-path', str(f.cache),
                   '--modern-highlight-evidence']
        for offset in starts:
            command.extend(('--preview-conditional-modern-parent-union', 'main.typ', str(offset)))
        missing = subprocess.run(command + ['--format', 'json'], capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(missing.stdout)['phase'], 'input')
        actual = subprocess.run(command + ['--preview-conditional-modern-combined-highlights', '--format', 'text'],
                                capture_output=True, text=True, timeout=30)
        self.assertIn('Conditional parent union:', actual.stdout)
        self.assertIn('Do not merge with other plans', actual.stdout)

    def test_report_budget_exact_boundary(self):
        _, analyzer, starts = self.issue()
        result = union.preview_union(analyzer, 'main.typ', starts)
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
        size = len(encoded.encode('utf-8')) + 2 * encoded.count('\n')
        self.assertEqual(union.preview_union(analyzer, 'main.typ', starts, max_bytes=size), result)
        with self.assertRaises(InputError):
            union.preview_union(analyzer, 'main.typ', starts, max_bytes=size - 1)

    def test_rejects_duplicates_and_fails_closed_on_low_work(self):
        _, analyzer, starts = self.issue()
        with self.assertRaises(InputError):
            union.preview_union(analyzer, 'main.typ', (starts[0], starts[0]))
        result = union.preview_union(analyzer, 'main.typ', starts, max_work=1)
        self.assertFalse(result['source_union_revalidated'])
        self.assertIsNone(result['union'])


if __name__ == '__main__':
    unittest.main()
