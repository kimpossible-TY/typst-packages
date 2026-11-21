"""Mode D shared graph/module cache alignment and fail-closed refusal tests."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import conditional_footnote_binding as binding
import conditional_highlight as high
import conditional_modern_highlight as modern
import document_composed_modern_highlight as composed
import document_composed_reference_routes as routes
import document_footnote_evidence as footnote
import document_intermediate_context as intermediate
import document_math_builtin_evidence as math_evidence
import document_modern_highlight_preview as preview
import document_sequence_preview as sequence
from edit_plan import Edit
from syntax_client import EXECUTABLE
import test_document_modern_highlight_evidence as fixtures
from typst_lint import InputError, SafetyError, load_rules

IMPORT = fixtures.fixtures.IMPORT
SCOPE = b'#import "@local/scoped-annotations:0.3.0": local-tag-scope, local-scope-annotations\n'


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ComposedReferenceCacheAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.CapturedModernHighlightTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        src = TOOL / 'tests/fixtures/reviewed-packages/local/scoped-annotations/0.3.0'
        dst = self.f.packages / 'local/scoped-annotations/0.3.0'
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)
        src = TOOL / 'tests/fixtures/scoped-reference-packages/preview/mannot/0.4.0'
        dst = self.f.packages / 'preview/mannot/0.4.0'
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)

    def _capture_multifile(self, chapter_count=3):
        helpers = b'#let helper_tag = "ok"\n'
        (self.f.project / 'helpers.typ').write_bytes(helpers)
        styles = IMPORT + SCOPE + b'#import "helpers.typ": *\n#let shared_note = "' + (b'x' * 4000) + b'"\n'
        (self.f.project / 'styles.typ').write_bytes(styles)
        includes = []
        extra = [
            *[('packages', 'local/scoped-annotations/0.3.0/' + n, 'package') for n in ('typst.toml', 'lib.typ')],
            *[('packages', 'preview/mannot/0.4.0/' + n, 'package') for n in ('typst.toml', 'src/lib.typ')],
        ]
        for idx in range(1, chapter_count + 1):
            name = f'ch{idx}.typ'
            body = (
                b'#import "styles.typ": *\n'
                + f'#highlighted[Plain $cal(a_{idx})$ end.]\n'.encode()
                + f'#highlighted[See @thing{idx} and $frac(x_{idx}, y_{idx})$ end.]\n'.encode()
                + f'#local-tag-scope(s => [#highlighted[See #(s.ref)("ref{idx}") and $hat(z_{idx})$ end.]])\n'.encode()
            )
            if idx == 2:
                body += (
                    b'// typst-lint: ignore-next pde.text-only-highlight -- kept\n'
                    b'#highlighted[Suppressed $cal(s)$ end.]\n'
                )
            (self.f.project / name).write_bytes(body)
            includes.append(f'#include "{name}"\n'.encode())
        main_source = b'#import "styles.typ": *\n' + b''.join(includes)
        _, analyzer, snapshot, _ = self.f.issue(main_source, extra=extra)
        proof = footnote.PreviewFootnoteEvidence(analyzer._session, snapshot)
        catalog = load_rules()[0]['rules']
        records = [
            row for src in analyzer._session._context.sources
            if (row := sequence.preview(src.tree, src.path, catalog)) is not None
        ]
        return proof, records

    def _prepare_verify_inputs(self, proof, records):
        final, _, view = composed.stage(proof, records, reviewed_math=True, composed_references=True)
        session = intermediate.issue_binding(view)
        analyzer = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)
        _, baseline_proposed = preview.preview_all(analyzer, return_evidence=True, reviewed_math=True)
        diagnostics, proposed = preview.preview_all(
            analyzer,
            return_evidence=True,
            reviewed_math=True,
            allow_direct_references=True,
            allow_scoped_references=True,
        )
        edits_by_path = {
            row['path']: tuple(
                Edit(item['start'], item['end'], item['replacement'].encode('utf-8'))
                for item in row['edits']
            )
            for row in proposed['by_file']
            if row['verification'] != 'withheld' and row['edits']
        }
        return view, analyzer, final, edits_by_path, baseline_proposed, diagnostics

    def test_shared_cache_matches_uncached_single_file_bit_for_bit(self):
        proof, records = self._capture_multifile(chapter_count=3)
        view, analyzer, final, edits_by_path, baseline_proposed, diagnostics = self._prepare_verify_inputs(
            proof, records)
        diag_by_path = {row['path']: row for row in diagnostics['by_file']}

        shared_modern = analyzer.new_shared_analyzer()
        shared_math = math_evidence._engine(analyzer._session)
        for path in ('ch1.typ', 'ch2.typ', 'ch3.typ'):
            tree = view.context.source_for(path).tree
            cached_modern = analyzer.analyze(tree, path, _shared_analyzer=shared_modern)
            uncached_modern = analyzer.analyze(tree, path)
            self.assertEqual(
                json.dumps(cached_modern, ensure_ascii=False, sort_keys=True),
                json.dumps(uncached_modern, ensure_ascii=False, sort_keys=True),
            )
            cached_math = math_evidence._analyze_report(
                analyzer, path, diag_by_path[path], engine=shared_math)
            uncached_math = math_evidence._analyze_report(analyzer, path, diag_by_path[path])
            self.assertEqual(
                json.dumps(cached_math, ensure_ascii=False, sort_keys=True),
                json.dumps(uncached_math, ensure_ascii=False, sort_keys=True),
            )

        cached_verify = routes.verify(view, analyzer, final, edits_by_path, baseline_proposed, diagnostics)
        orig_analyze = modern.ModernHighlightAnalyzer.analyze
        orig_math = math_evidence._analyze_report

        def uncached_analyze(self_analyzer, tree, path, *, _shared_analyzer=None):
            return orig_analyze(self_analyzer, tree, path)

        def uncached_math_report(self_analyzer, path, report, *, engine=None):
            return orig_math(self_analyzer, path, report)

        with patch.object(modern.ModernHighlightAnalyzer, 'analyze', uncached_analyze), \
                patch.object(math_evidence, '_analyze_report', uncached_math_report):
            uncached_verify = routes.verify(view, analyzer, final, edits_by_path, baseline_proposed, diagnostics)

        self.assertEqual(cached_verify, uncached_verify)
        self.assertEqual(cached_verify['paths'], ('ch1.typ', 'ch2.typ', 'ch3.typ'))
        self.assertEqual(cached_verify['generated_calls'], 6)

    def test_verify_reuses_shared_analyzers_and_graph_module_cache_across_targets(self):
        proof, records = self._capture_multifile(chapter_count=3)
        view, analyzer, final, edits_by_path, baseline_proposed, diagnostics = self._prepare_verify_inputs(
            proof, records)

        modern_inits = 0
        seen_shared_moderns = []
        seen_shared_engines = []
        modern_module_evals = []
        math_module_evals = []
        modern_cache_hits = 0
        math_cache_hits = 0

        orig_modern_init = modern._ModernAnalyzer.__init__
        orig_analyze = modern.ModernHighlightAnalyzer.analyze
        orig_analyze_report = math_evidence._analyze_report
        orig_module = high._Analyzer.module

        def count_modern_init(self_obj, *args, **kwargs):
            nonlocal modern_inits
            if type(self_obj) is modern._ModernAnalyzer:
                modern_inits += 1
            return orig_modern_init(self_obj, *args, **kwargs)

        def trace_analyze(self_analyzer, tree, path, *, _shared_analyzer=None):
            seen_shared_moderns.append(_shared_analyzer)
            return orig_analyze(self_analyzer, tree, path, _shared_analyzer=_shared_analyzer)

        def trace_analyze_report(self_analyzer, path, report, *, engine=None):
            seen_shared_engines.append(engine)
            return orig_analyze_report(self_analyzer, path, report, engine=engine)

        def trace_module(self_obj, path, active):
            nonlocal modern_cache_hits, math_cache_hits
            was_cached = path in self_obj.modules
            result = orig_module(self_obj, path, active)
            if self_obj in seen_shared_moderns:
                if was_cached:
                    modern_cache_hits += 1
                elif path in self_obj.modules:
                    modern_module_evals.append(path)
            elif self_obj in seen_shared_engines:
                if was_cached:
                    math_cache_hits += 1
                elif path in self_obj.modules:
                    math_module_evals.append(path)
            return result

        with patch.object(modern._ModernAnalyzer, '__init__', count_modern_init), \
                patch.object(modern.ModernHighlightAnalyzer, 'analyze', trace_analyze), \
                patch.object(math_evidence, '_analyze_report', trace_analyze_report), \
                patch.object(high._Analyzer, 'module', trace_module):
            result = routes.verify(view, analyzer, final, edits_by_path, baseline_proposed, diagnostics)

        self.assertEqual(result['paths'], ('ch1.typ', 'ch2.typ', 'ch3.typ'))
        self.assertEqual(modern_inits, 1)
        self.assertEqual(len(seen_shared_moderns), 3)
        self.assertEqual(len({id(x) for x in seen_shared_moderns}), 1)
        self.assertIsNotNone(seen_shared_moderns[0])
        self.assertEqual(len(seen_shared_engines), 3)
        self.assertEqual(len({id(x) for x in seen_shared_engines}), 1)
        self.assertIsNotNone(seen_shared_engines[0])
        # Each shared imported module ('styles.typ' and 'helpers.typ') is evaluated
        # exactly once across all 3 target files, with cache hits on targets 2 and 3.
        self.assertEqual(sorted(modern_module_evals), ['helpers.typ', 'styles.typ'])
        self.assertEqual(sorted(math_module_evals), ['helpers.typ', 'styles.typ'])
        self.assertEqual(modern_cache_hits, 2)
        self.assertEqual(math_cache_hits, 2)
        self.assertEqual(sorted(seen_shared_moderns[0].modules), ['helpers.typ', 'styles.typ'])
        self.assertEqual(sorted(seen_shared_engines[0].modules), ['helpers.typ', 'styles.typ'])

    def test_mismatched_and_mutated_shared_analyzers_refuse_fail_closed(self):
        proof, records = self._capture_multifile(chapter_count=2)
        view, analyzer, _, _, _, diagnostics = self._prepare_verify_inputs(proof, records)
        diag_row = next(row for row in diagnostics['by_file'] if row['path'] == 'ch1.typ')
        tree = view.context.source_for('ch1.typ').tree

        other_analyzer_same_session = modern.ModernHighlightAnalyzer(analyzer._session, scoped_callbacks=True)
        other_scoped_analyzer = modern.ModernHighlightAnalyzer(analyzer._session, scoped_callbacks=False)
        other_session = intermediate.issue_binding(view)
        other_session_analyzer = modern.ModernHighlightAnalyzer(other_session, scoped_callbacks=True)

        # 1. Shared _ModernAnalyzer from another ModernHighlightAnalyzer instance or unissued instance.
        for bad_shared in (
            other_analyzer_same_session.new_shared_analyzer(),
            other_scoped_analyzer.new_shared_analyzer(),
            other_session_analyzer.new_shared_analyzer(),
            modern._ModernAnalyzer(analyzer._session, analyzer._variant, scoped_callbacks=True),
        ):
            with self.subTest(bad_shared=type(bad_shared).__name__):
                with self.assertRaises(InputError):
                    analyzer.analyze(tree, 'ch1.typ', _shared_analyzer=bad_shared)

        # 2. Mutating a validly issued shared _ModernAnalyzer must fail closed.
        mutated_shared = analyzer.new_shared_analyzer()
        mutated_shared.packages['@local/evil:0.0.1'] = None
        with self.assertRaises(InputError):
            analyzer.analyze(tree, 'ch1.typ', _shared_analyzer=mutated_shared)

        mutated_flag = analyzer.new_shared_analyzer()
        mutated_flag.scoped_callbacks = False
        with self.assertRaises(InputError):
            analyzer.analyze(tree, 'ch1.typ', _shared_analyzer=mutated_flag)

        # 3. Shared math _NamespaceAnalyzer from another session or unissued instance.
        unissued_engine = binding._NamespaceAnalyzer(
            analyzer._session._graph, analyzer._session._namespaces, (), False)
        other_engine = math_evidence._engine(other_session)
        for bad_engine in (unissued_engine, other_engine):
            with self.subTest(bad_engine=id(bad_engine)):
                with self.assertRaises(InputError):
                    math_evidence._analyze_report(analyzer, 'ch1.typ', diag_row, engine=bad_engine)

        # 4. Mutating a validly issued shared math engine must fail closed.
        mutated_engine = math_evidence._engine(analyzer._session)
        mutated_engine.packages['@local/evil:0.0.1'] = None
        with self.assertRaises(InputError):
            math_evidence._analyze_report(analyzer, 'ch1.typ', diag_row, engine=mutated_engine)

    def test_tight_budget_and_mid_verify_session_mutation_refuse_fail_closed(self):
        proof, records = self._capture_multifile(chapter_count=2)
        view, analyzer, final, edits_by_path, baseline_proposed, diagnostics = self._prepare_verify_inputs(
            proof, records)

        full = routes.verify(view, analyzer, final, edits_by_path, baseline_proposed, diagnostics)
        with patch.object(routes, 'MAX_FINAL_ROUTE_WORK', max(1, full['work'] - 1)):
            with self.assertRaises(routes.RouteBudgetExceeded):
                routes.verify(view, analyzer, final, edits_by_path, baseline_proposed, diagnostics)

        # Mutate the issued session between target 1 and target 2 during verify().
        orig_analyze = modern.ModernHighlightAnalyzer.analyze
        calls = 0

        def mutate_after_first(self_analyzer, tree, path, *, _shared_analyzer=None):
            nonlocal calls
            result = orig_analyze(self_analyzer, tree, path, _shared_analyzer=_shared_analyzer)
            calls += 1
            if calls == 1:
                object.__setattr__(self_analyzer._session._graph.sources[0], 'source', b'// tampered')
            return result

        with patch.object(modern.ModernHighlightAnalyzer, 'analyze', mutate_after_first):
            with self.assertRaises((InputError, SafetyError)):
                routes.verify(view, analyzer, final, edits_by_path, baseline_proposed, diagnostics)

    def test_forged_diagnostic_still_rejected_with_shared_cache(self):
        proof, records = self._capture_multifile(chapter_count=2)
        view, analyzer, final, edits_by_path, baseline_proposed, diagnostics = self._prepare_verify_inputs(
            proof, records)
        forged = deepcopy(diagnostics)
        for row in forged['by_file']:
            if row['path'] == 'ch2.typ' and row['diagnostics']:
                row['diagnostics'][0]['line'] += 1
                break
        with self.assertRaisesRegex(SafetyError, 'source diagnostics or suppressions'):
            routes.verify(view, analyzer, final, edits_by_path, baseline_proposed, forged)


if __name__ == '__main__':
    unittest.main()
