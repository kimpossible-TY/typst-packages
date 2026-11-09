"""Experimental union rechecks one callback without authorizing writes."""
from copy import deepcopy
import json
import subprocess
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_modern_highlight as modern
import scoped_math_union_prototype as union
import scoped_math_capture_prototype as math
import document_modern_highlight_preview as preview
import document_cli
from edit_plan import Edit, plan
from typst_lint import InputError
from test_document_modern_highlight_scoped_preview import ScopedReferencePreviewTests, SCOPE
import test_conditional_modern_highlight as fixtures


@unittest.skipUnless(fixtures.EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ScopedMathUnionPrototypeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ScopedReferencePreviewTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def issue(self, body):
        source, context, analyzer, _ = self.fixture.run_source(body)
        diagnostics = analyzer.analyze(context.source_for('main.typ').tree, 'main.typ')['diagnostics']
        return source, analyzer, diagnostics

    def test_same_callback_union_preserves_baseline_and_offsets(self):
        body = ('#local-tag-scope(s => [\n'
                '#highlighted[한글 #(s.ref)("one") $x_1$ end.]\n'
                '#highlighted[Math $cal(X)$ end.]\n'
                '#highlighted[Other #(s.ref)("two") $y$ end.]\n'
                '#highlighted[More $op("L")$ end.]\n])').encode()
        source, analyzer, ds = self.issue(body)
        starts = tuple(d['start'] for d in ds if b'cal(' in source[d['start']:d['end']]
                       or b'op(' in source[d['start']:d['end']])
        result = union.preview_batch(analyzer, [('main.typ', starts)])
        expected = preview.preview_all(analyzer, allow_direct_references=True, allow_scoped_references=True,
                                       reviewed_math=True)
        self.assertEqual(result['baseline'], expected)
        row, = result['unions']
        self.assertEqual(row['verification'], 'conditional-scoped-math-union-source-only')
        baseline, = expected['by_file']
        self.assertEqual(len(baseline['edits']), 2)
        self.assertEqual(len(row['edits']), 4)
        as_tuple = lambda r: {(e['start'], e['end'], e['replacement']) for e in r['edits']}
        self.assertTrue(as_tuple(baseline) < as_tuple(row))
        candidate = plan(source, [Edit(e['start'], e['end'], e['replacement'].encode()) for e in row['edits']]).candidate()
        self.assertLess(candidate.index(b'#(s.ref)("one")'), candidate.index(b'#(s.ref)("two")'))
        for name in ('runtime_binding_proved', 'execution_proved', 'render_equivalence_proved',
                     'edit_permission', 'compiler_permission'):
            self.assertFalse(row[name])
        self.assertEqual((self.fixture.fixture.project / 'main.typ').read_bytes(), source)

    def test_failed_proof_and_work_budget_keep_original_baseline(self):
        body = b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") $x$ end.]\n#highlighted[Math $cal(X)$ end.]])'
        _, analyzer, ds = self.issue(body)
        start = ds[-1]['start']
        baseline = preview.preview_all(analyzer, allow_direct_references=True, allow_scoped_references=True,
                                       reviewed_math=True)
        with patch.object(union, 'MAX_UNION_SCOPE_WORK', 1):
            result = union.preview_batch(analyzer, [('main.typ', (start,))])
        self.assertEqual(result['baseline'], baseline)
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertEqual(result['unions'][0]['verification'], 'withheld')
        self.assertFalse(result['unions'][0]['edits'])
        result = union.preview_batch(analyzer, [('main.typ', (ds[0]['start'],))])
        self.assertEqual(result['baseline'], baseline)
        self.assertEqual(result['unions'][0]['verification'], 'withheld')
        with self.assertRaises(InputError):
            union.preview_batch(analyzer, [('main.typ', (start, start))])

    def test_same_highlight_and_distinct_callbacks_do_not_implicitly_combine(self):
        bodies = (
            b'#local-tag-scope(s => [#highlighted[One #(s.ref)("one") $x$ end.]\n'
            b'#highlighted[Both #(s.ref)("two") $cal(X)$ end.]])',
            b'#local-tag-scope(s => [#highlighted[One #(s.ref)("one") $x$ end.]])\n'
            b'#local-tag-scope(s => [#highlighted[Math $cal(X)$ end.]])',
        )
        for body in bodies:
            with self.subTest(body=body):
                _, analyzer, ds = self.issue(body)
                base = preview.preview_all(analyzer, allow_direct_references=True, allow_scoped_references=True,
                                           reviewed_math=True)
                result = union.preview_batch(analyzer, [('main.typ', (ds[-1]['start'],))])
                self.assertEqual(result['baseline'], base)
                self.assertEqual(result['unions'][0]['verification'], 'withheld')
                self.assertFalse(result['unions'][0]['edits'])

    def test_tampered_candidate_and_forged_diagnostics_keep_baseline(self):
        body = b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") $x$ end.]\n#highlighted[Math $cal(X)$ end.]])'
        _, analyzer, ds = self.issue(body)
        baseline = preview.preview_all(analyzer, allow_direct_references=True, allow_scoped_references=True,
                                       reviewed_math=True)
        original_parse = union.parse
        def tamper(data, *args, **kwargs):
            return original_parse(data.replace(b'cal(X)', b'op(X)'), *args, **kwargs)
        with patch.object(union, 'parse', side_effect=tamper):
            result = union.preview_batch(analyzer, [('main.typ', (ds[-1]['start'],))])
        self.assertEqual(result['baseline'], baseline)
        self.assertEqual(result['unions'][0]['verification'], 'withheld')
        original = preview.preview_all
        def forged(*args, **kwargs):
            diagnostics, report = original(*args, **kwargs)
            diagnostics = deepcopy(diagnostics)
            diagnostics['by_file'][0]['diagnostics'][-1]['callback_context'][0]['call_start'] += 1
            return diagnostics, report
        with patch.object(preview, 'preview_all', side_effect=forged):
            result = union.preview_batch(analyzer, [('main.typ', (ds[-1]['start'],))])
        self.assertEqual(result['baseline'], baseline)
        self.assertIn('diagnostics changed', result['unions'][0]['reason'])

    def test_source_change_during_batch_refuses_result(self):
        body = b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") $x$ end.]\n#highlighted[Math $cal(X)$ end.]])'
        _, analyzer, ds = self.issue(body)
        root = analyzer._session._context.source_for('main.typ').tree.nodes[0]
        original_end = root.end
        original = math.preview_one
        def mutate(*args, **kwargs):
            value = original(*args, **kwargs)
            object.__setattr__(root, 'end', original_end - 1)
            return value
        try:
            with patch.object(math, 'preview_one', side_effect=mutate):
                with self.assertRaises(InputError):
                    union.preview_batch(analyzer, [('main.typ', (ds[-1]['start'],))])
        finally:
            object.__setattr__(root, 'end', original_end)

    def test_cli_reuses_baseline_and_enforces_global_budgets(self):
        body = b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") $x$ end.]\n' \
               b'#highlighted[Math $cal(X)$ end.]\n#highlighted[More $op("L")$ end.]])'
        source, _, ds = self.issue(body)
        targets = [('main.typ', ds[1]['start']), ('main.typ', ds[2]['start'])]
        common = dict(root=str(self.fixture.fixture.project), entrypoint='main.typ', rule_set='pde',
                      package_path=str(self.fixture.fixture.packages), footnote_cache_path=str(self.fixture.fixture.cache),
                      modern_highlight_evidence=True, preview_conditional_modern_combined_highlights=True)
        baseline = document_cli.report_document(**common)
        self.assertNotIn('conditional_modern_scoped_union_previews', baseline)
        original = preview.preview_all
        with patch.object(preview, 'preview_all', wraps=original) as count:
            result = document_cli.report_document(**common, preview_conditional_modern_scoped_union=targets)
        self.assertEqual(count.call_count, 1)
        self.assertEqual(result['conditional_modern_combined_previews'], baseline['conditional_modern_combined_previews'])
        row, = result['conditional_modern_scoped_union_previews']['unions']
        self.assertEqual(row['verification'], 'conditional-scoped-math-union-source-only')
        self.assertEqual(len(row['edits']), 3)
        self.assertFalse(result['compiler_invoked'] or result['applied_fixes'])
        with patch.object(union, 'MAX_UNION_SCOPE_WORK', 1):
            limited = document_cli.report_document(**common, preview_conditional_modern_scoped_union=targets)
        self.assertEqual(limited['conditional_modern_combined_previews'], baseline['conditional_modern_combined_previews'])
        self.assertEqual(limited['conditional_modern_scoped_union_previews']['unions'][0]['verification'], 'withheld')
        size = len(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8'))
        with patch.object(document_cli, 'MAX_MODERN_REPORT_BYTES', size):
            self.assertEqual(result, document_cli.report_document(**common, preview_conditional_modern_scoped_union=targets))
        with patch.object(document_cli, 'MAX_MODERN_REPORT_BYTES', size - 1):
            with self.assertRaises(InputError):
                document_cli.report_document(**common, preview_conditional_modern_scoped_union=targets)
        low, high = 1, 2048
        while low < high:
            middle = (low + high) // 2
            try:
                with patch.object(document_cli, 'MAX_REPORT_ITEMS', middle):
                    document_cli.report_document(**common, preview_conditional_modern_scoped_union=targets)
            except InputError:
                low = middle + 1
            else:
                high = middle
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', low):
            self.assertEqual(result, document_cli.report_document(**common, preview_conditional_modern_scoped_union=targets))
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', low - 1):
            with self.assertRaises(InputError):
                document_cli.report_document(**common, preview_conditional_modern_scoped_union=targets)
        for bad in ([('main.typ', -1)], [('main.typ', True)], [('main.typ', '１２')],
                    [('main.typ', '1.2')], [('main.typ', '1234567890123')], [targets[0], targets[0]]):
            with self.subTest(bad=bad), self.assertRaises(InputError):
                document_cli.report_document(**common, preview_conditional_modern_scoped_union=bad)
        self.assertEqual((self.fixture.fixture.project / 'main.typ').read_bytes(), source)

    def test_cli_two_sources_and_outside_capture_paths(self):
        body = b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") $x$ end.]\n' \
               b'#highlighted[Math $cal(X)$ end.]])'
        for name in ('a.typ', 'b.typ'):
            (self.fixture.fixture.project / name).write_bytes(fixtures.IMPORT + SCOPE + body)
        source = b'#include "a.typ"\n#include "b.typ"\n'
        session, context = self.fixture.fixture.session(source)
        analyzer = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)
        targets = []
        for name in ('a.typ', 'b.typ'):
            tree = context.source_for(name).tree
            ds = analyzer.analyze(tree, name)['diagnostics']
            targets.append((name, ds[-1]['start']))
        common = dict(root=str(self.fixture.fixture.project), entrypoint='main.typ', rule_set='pde',
                      package_path=str(self.fixture.fixture.packages), footnote_cache_path=str(self.fixture.fixture.cache),
                      modern_highlight_evidence=True, preview_conditional_modern_combined_highlights=True)
        base = document_cli.report_document(**common)
        result = document_cli.report_document(**common, preview_conditional_modern_scoped_union=targets)
        self.assertEqual(result['conditional_modern_combined_previews'], base['conditional_modern_combined_previews'])
        self.assertEqual([len(row['edits']) for row in result['conditional_modern_scoped_union_previews']['unions']], [2, 2])
        for path in ('../a.typ', '/etc/passwd'):
            with self.subTest(path=path):
                withheld = document_cli.report_document(**common,
                    preview_conditional_modern_scoped_union=[(path, targets[0][1])])
                row, = withheld['conditional_modern_scoped_union_previews']['unions']
                self.assertEqual(row['verification'], 'withheld')
                self.assertFalse(row['edits'])
                self.assertEqual(withheld['conditional_modern_combined_previews'], base['conditional_modern_combined_previews'])
        self.assertEqual((self.fixture.fixture.project / 'main.typ').read_bytes(), source)

    def test_cli_help_and_text_mark_independent_plans(self):
        source, _, ds = self.issue(b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") $x$ end.]\n'
                                   b'#highlighted[Math $cal(X)$ end.]])')
        command = [sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
                   '--root', str(self.fixture.fixture.project), '--entrypoint', 'main.typ',
                   '--rule-set', 'pde', '--package-path', str(self.fixture.fixture.packages),
                   '--footnote-cache-path', str(self.fixture.fixture.cache), '--modern-highlight-evidence',
                   '--preview-conditional-modern-scoped-union', 'main.typ', str(ds[-1]['start'])]
        missing = subprocess.run(command + ['--format', 'json'], capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(missing.stdout)['phase'], 'input')
        run = subprocess.run(command + ['--preview-conditional-modern-combined-highlights', '--format', 'text'],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertIn('Do not merge with other plans', run.stdout)
        self.assertEqual((self.fixture.fixture.project / 'main.typ').read_bytes(), source)

    def test_report_budget_exact_boundaries(self):
        body = b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") $x$ end.]\n#highlighted[Math $cal(X)$ end.]])'
        _, analyzer, ds = self.issue(body)
        target = [('main.typ', (ds[-1]['start'],))]
        result = union.preview_batch(analyzer, target)
        evidence = analyzer.analyze_all()
        a = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
        b = json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2)
        size = len(a.encode()) + 2 * a.count('\n') + len(b.encode()) + 2 * b.count('\n')
        self.assertEqual(union.preview_batch(analyzer, target, max_items=result['report_items'], max_bytes=size), result)
        with self.assertRaises(InputError):
            union.preview_batch(analyzer, target, max_items=result['report_items'] - 1)
        with self.assertRaises(InputError):
            union.preview_batch(analyzer, target, max_bytes=size - 1)


if __name__ == '__main__':
    unittest.main()
