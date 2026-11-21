"""Extended preview-compile tests: Mode D budget, 9 captured proposals, 33/33 inspection, and CLI wiring."""
import ast
import contextlib
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))

import closed_core_compile as runtime
import document_captured_preview_stage as stage
import document_composed_modern_highlight as composed
import document_composed_reference_routes as routes
import document_dependencies
import document_display
import document_footnote_evidence as footnote
import document_modern_highlight_evidence as modern_evidence
import document_preview_cli
import document_preview_compile as preview_compile
import document_resources
import document_sequence_preview as sequence
from edit_plan import Edit, plan
import include_context
from reference_catalog import parse_reference_catalog
import script_rule
from syntax_client import EXECUTABLE, parse
import test_document_modern_highlight_evidence as highlight_fixtures
import typst_lint
from typst_lint import SafetyError, digest, load_rules

FLOWBOX_PACKAGE = TOOL / 'tests/fixtures/reviewed-packages/local/math-blocks/0.2.0/lib.typ'
FLOWBOX_STROKE = b'stroke: 1pt + theme.rule'

IMPORT_MODERN = highlight_fixtures.fixtures.IMPORT
IMPORT_SCOPED = b'#import "@local/scoped-annotations:0.3.0": local-tag-scope, local-scope-annotations\n'

REFERENCE_CATALOG = parse_reference_catalog(
    json.dumps({
        'schema': 1,
        'projects': {
            'pde': {
                'references': {'energy': {'page': 1, 'text': 'Theorem 1'}},
                'ambiguous_labels': [],
            }
        },
    }).encode('utf-8'),
    'explicit-extended-test-catalog',
)


def _materialize_reviewed_packages(packages_root: Path):
    """Copy reviewed local and preview packages into a temporary package root."""
    reviewed_local = TOOL / 'tests/fixtures/reviewed-packages/local'
    for name, version in (('scoped-annotations', '0.3.0'),
                          ('math-blocks', '0.2.0'),
                          ('math-book', '0.2.0'),
                          ('project-references', '0.1.0'),
                          ('text-utils', '0.1.2')):
        shutil.copytree(reviewed_local / name / version, packages_root / 'local' / name / version)
    shutil.copytree(TOOL / 'tests/fixtures/marker-bounds-packages/preview/mannot/0.4.0',
                    packages_root / 'preview/mannot/0.4.0')
    shutil.copytree(TOOL / 'tests/fixtures/anchor-overlay-packages/preview/cetz/0.4.2',
                    packages_root / 'preview/cetz/0.4.2')


def _package_resource_requests(packages_root: Path) -> list[dict]:
    """Enumerate explicit resource manifest entries for all files under packages_root."""
    entries = []
    for path in sorted(packages_root.rglob('*')):
        if path.is_file():
            entries.append(dict(root='packages', path=path.relative_to(packages_root).as_posix(), role='package'))
    return entries


def _fake_preview_result(baseline_sha='a' * 64, candidate_sha='b' * 64, *,
                         pdfs_identical=False, modern_highlight_evidence=None):
    return SimpleNamespace(
        baseline_sha256=baseline_sha,
        candidate_sha256=candidate_sha,
        pdfs_identical=pdfs_identical,
        dependency_count=1,
        unobserved_capture_count=0,
        system_font_files=0,
        compiler_sha256='c' * 64,
        candidate_pdf=b'%PDF-1.7\n%%EOF\n',
        modern_highlight_evidence=modern_highlight_evidence,
    )


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ModeDComposedReferenceRouteBudgetTests(unittest.TestCase):
    def setUp(self):
        self.f = highlight_fixtures.CapturedModernHighlightTests('runTest')
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

    def _capture_multifile(self, chapter_count: int = 3):
        # Shared imported module with substantial content so 2 * graph_work is significant.
        padding = b'#let shared_note = "' + (b'x' * 8000) + b'"\n'
        styles = IMPORT_MODERN + IMPORT_SCOPED + padding
        (self.f.project / 'styles.typ').write_bytes(styles)
        includes = []
        extra = [
            *[('packages', 'local/scoped-annotations/0.3.0/' + n, 'package') for n in ('typst.toml', 'lib.typ')],
            *[('packages', 'preview/mannot/0.4.0/' + n, 'package') for n in ('typst.toml', 'src/lib.typ')],
        ]
        chapter_bytes = {}
        for idx in range(1, chapter_count + 1):
            name = f'ch{idx}.typ'
            body = (
                b'#import "styles.typ": *\n'
                + f'#highlighted[Plain $a_{idx}$ end.]\n'.encode()
                + f'#highlighted[See @thing{idx} and $x_{idx}$ end.]\n'.encode()
                + f'#local-tag-scope(s => [#highlighted[See #(s.ref)("ref{idx}") and $y_{idx}$ end.]])\n'.encode()
            )
            (self.f.project / name).write_bytes(body)
            chapter_bytes[name] = body
            includes.append(f'#include "{name}"\n'.encode())
        main_source = b'#import "styles.typ": *\n' + b''.join(includes)
        _, analyzer, snapshot, _ = self.f.issue(main_source, extra=extra)
        proof = footnote.PreviewFootnoteEvidence(analyzer._session, snapshot)
        catalog = load_rules()[0]['rules']
        records = [
            row for src in analyzer._session._context.sources
            if (row := sequence.preview(src.tree, src.path, catalog)) is not None
        ]
        return main_source, chapter_bytes, styles, proof, records

    @staticmethod
    def _compute_graph_work(view, analyzer):
        graph_work = (
            len(view.graph.sources) * len(view.context.sources)
            + sum(len(item.source) + len(item.manifest) for item in analyzer._session._namespaces.packages)
        )
        for item in view.graph.sources:
            captured = view.context.source_for(item.path)
            graph_tree = captured.tree if captured is not None and captured.source == item.source else parse(item.source)
            depths = [0] * len(graph_tree.nodes)
            graph_work += 4 * len(graph_tree.nodes) + len(graph_tree.source)
            for index, node in enumerate(graph_tree.nodes):
                for child in node.children:
                    depths[child] = depths[index] + 1
                if node.kind in ('ModuleImport', 'ModuleInclude'):
                    graph_work += 4 * depths[index] + 2 * (node.end - node.start)
        return graph_work

    def test_multifile_shared_graph_work_charged_once(self):
        main_source, chapter_bytes, styles, proof, records = self._capture_multifile(chapter_count=3)
        final, report, view = composed.stage(proof, records, reviewed_math=True, composed_references=True)
        route_info = report['reference_candidate_routes']
        self.assertEqual(route_info['paths'], ('ch1.typ', 'ch2.typ', 'ch3.typ'))
        self.assertGreaterEqual(route_info['generated_calls'], 6)
        self.assertEqual(report['candidate_count'], 9)

        import conditional_modern_highlight as modern
        import document_intermediate_context as intermediate
        analyzer = modern.ModernHighlightAnalyzer(intermediate.issue_binding(view), scoped_callbacks=True)
        graph_work = self._compute_graph_work(view, analyzer)
        actual_work = route_info['work']
        # Shared 2 * graph_work is deducted once per verify() call, NOT once per target file.
        self.assertGreater(actual_work, 2 * graph_work)
        # Verify a budget strictly below 3 * (2 * graph_work) still succeeds when above actual_work.
        tight_passing_budget = actual_work + 50_000
        self.assertLess(tight_passing_budget, 3 * (2 * graph_work) + (actual_work - 2 * graph_work))
        with patch.object(routes, 'MAX_FINAL_ROUTE_WORK', tight_passing_budget):
            final2, report2, _ = composed.stage(proof, records, reviewed_math=True, composed_references=True)
        self.assertEqual(final2, final)
        self.assertEqual(report2['reference_candidate_routes']['paths'], ('ch1.typ', 'ch2.typ', 'ch3.typ'))
        self.assertFalse(report2['reference_budget_fallback'])
        # Real files on disk remain completely untouched.
        self.assertEqual((self.f.project / 'main.typ').read_bytes(), main_source)
        self.assertEqual((self.f.project / 'styles.typ').read_bytes(), styles)
        for name, raw in chapter_bytes.items():
            self.assertEqual((self.f.project / name).read_bytes(), raw)

    def test_tight_budget_refuses_with_route_budget_exceeded_and_falls_back(self):
        _, _, _, proof, records = self._capture_multifile(chapter_count=2)
        baseline_final, baseline_report, view = composed.stage(proof, records, reviewed_math=True)
        import conditional_modern_highlight as modern
        import document_intermediate_context as intermediate
        analyzer = modern.ModernHighlightAnalyzer(intermediate.issue_binding(view), scoped_callbacks=True)
        graph_work = self._compute_graph_work(view, analyzer)

        # Budget strictly below 2 * graph_work refuses via RouteBudgetExceeded and falls back cleanly.
        with patch.object(routes, 'MAX_FINAL_ROUTE_WORK', max(1, 2 * graph_work - 10)):
            final, report, _ = composed.stage(proof, records, reviewed_math=True, composed_references=True)
        self.assertEqual(final, baseline_final)
        self.assertEqual(report['candidate_count'], baseline_report['candidate_count'])
        self.assertTrue(report['reference_budget_fallback'])
        self.assertEqual(report['reference_budget_fallback_stage'], 'candidate-route')

        # Direct routes.verify() raises RouteBudgetExceeded under a tight per-file budget too.
        _, full_report, full_view = composed.stage(proof, records, reviewed_math=True, composed_references=True)
        full_work = full_report['reference_candidate_routes']['work']
        with patch.object(routes, 'MAX_FINAL_ROUTE_WORK', max(1, full_work - 1)):
            fallback_final, fallback_report, _ = composed.stage(proof, records, reviewed_math=True, composed_references=True)
        self.assertEqual(fallback_final, baseline_final)
        self.assertTrue(fallback_report['reference_budget_fallback'])
        self.assertEqual(fallback_report['reference_budget_fallback_stage'], 'candidate-route')

    def test_safety_refusal_checks_preserved_on_multifile_candidates(self):
        _, _, _, proof, records = self._capture_multifile(chapter_count=2)
        # 1. Final helper route mismatch must refuse with SafetyError (never fall back).
        with patch.object(routes._Tracer, 'body_math', return_value=(False, None)):
            with self.assertRaisesRegex(SafetyError, 'routes are unavailable'):
                composed.stage(proof, records, reviewed_math=True, composed_references=True)

        # 2. Forged diagnostic must be independently rejected with SafetyError.
        original_preview = composed.highlight.preview_all
        def forge_diag(*args, **kwargs):
            diag, prop = original_preview(*args, **kwargs)
            diag = deepcopy(diag)
            for row in diag['by_file']:
                if row['diagnostics']:
                    row['diagnostics'][0]['line'] += 1
                    break
            return diag, prop
        with patch.object(composed.highlight, 'preview_all', side_effect=forge_diag):
            with self.assertRaisesRegex(SafetyError, 'source diagnostics or suppressions'):
                composed.stage(proof, records, reviewed_math=True, composed_references=True)


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class CapturedProposalsAndInspectAllTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='typst-preview-ext-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / 'project'
        self.packages = self.root / 'packages'
        self.cache = self.root / 'cache'
        for d in (self.project, self.packages, self.cache):
            d.mkdir()
        _materialize_reviewed_packages(self.packages)
        self.catalog = load_rules()[0]['rules']

    def write(self, relpath: str, data: bytes) -> Path:
        target = self.project / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def _capture(self, entrypoint: str = 'main.typ', extra_project_files: tuple[str, ...] = ()):
        entry_bytes = (self.project / entrypoint).read_bytes()
        context = include_context.load_include_context(self.project, ((entrypoint, entry_bytes),), budget='book-v1')
        requests = []
        seen = set()
        for item in context.sources:
            seen.add(('project', item.path))
            requests.append(document_resources.ResourceRequest('project', item.path, 'source'))
        for path in extra_project_files:
            if ('project', path) not in seen:
                seen.add(('project', path))
                requests.append(document_resources.ResourceRequest('project', path, 'source'))
        for row in _package_resource_requests(self.packages):
            requests.append(document_resources.ResourceRequest(row['root'], row['path'], row['role']))
        roots = dict(project=str(self.project), packages=str(self.packages), cache=str(self.cache))
        snapshot = document_resources.capture(roots, requests)
        base_candidates = document_resources.stage_original(context, snapshot)
        return context, snapshot, base_candidates, roots

    def test_all_nine_captured_proposal_rules_verified_in_memory(self):
        # Leaf, figures, and facade modules for import-order and no-redundant-facade-import
        self.write('leaf.typ', b'#let alpha = 1\n')
        self.write('figures.typ', b'#let fig_val = 2\n')
        self.write('Styles/styles.typ', b'#import "../leaf.typ": *\n')
        self.write('Styles/local_tags.typ', b'#import "../leaf.typ": *\n')

        files = {
            # 1. core.scoped-function-call
            'r1_scoped_call.typ': (
                b'#import "@local/scoped-annotations:0.3.0": local-tag-scope\n'
                b'#local-tag-scope(s => [#s.tag("alpha")])\n'
            ),
            # 2. core.scoped-anchor-access
            'r2_anchor.typ': (
                b'#import "@local/scoped-annotations:0.3.0": mannot-scope\n'
                b'#import "@preview/mannot:0.4.0": mark, annot-cetz\n'
                b'#import "@preview/cetz:0.4.2"\n'
                b'#mannot-scope(s => [$ #(s.mark)("x")(1) #(s.annot)("x", cetz, { cetz.draw.line("x.north", "x.south") }) $])\n'
            ),
            # 3. core.scoped-reference-access
            'r3_scoped_ref.typ': (
                b'#import "@local/scoped-annotations:0.3.0": local-tag-scope\n'
                b'#local-tag-scope(s => [#ref((s.tag)("energy"))])\n'
            ),
            # 4. core.project-ref-arguments (verified when reference_catalog supplied)
            'r4_proj_ref.typ': (
                b'#import "@local/project-references:0.1.0": project-ref\n'
                b'#project-ref("pde", [@energy])\n'
            ),
            # 5. pde.import-order (figures.typ before Styles/styles.typ facade -> reordered)
            'r5_import_order.typ': (
                b'#import "figures.typ": fig_val\n'
                b'#import "Styles/styles.typ": *\n'
                b'#alpha #fig_val\n'
            ),
            # 6. pde.no-redundant-facade-import
            'r6_redundant.typ': (
                b'#import "Styles/styles.typ": *\n'
                b'#import "Styles/local_tags.typ": *\n'
                b'#alpha\n'
            ),
            # 7. pde.flowbox-colored-border
            'r7_flowbox.typ': FLOWBOX_PACKAGE.read_bytes().replace(FLOWBOX_STROKE, b'stroke: 1pt'),
            # 8. pde.paragraph-marker-target
            'r8_para_target.typ': (
                b'#import "@local/text-utils:0.1.2": paragraph_tab\n'
                b'#paragraph_tab\n'
                b'= Heading\n'
            ),
            # 9. pde.prose-footnote-no-spacer (conditional nested prose spacer)
            'r9_prose_spacer.typ': (
                b'#block[Alpha.#dots_space #footnote[Note.]\n]\n'
            ),
        }
        for name, data in files.items():
            self.write(name, data)
        main_src = b''.join(f'#include "{name}"\n'.encode() for name in sorted(files))
        self.write('main.typ', main_src)

        context, snapshot, base_candidates, _ = self._capture(
            'main.typ',
            extra_project_files=('leaf.typ', 'figures.typ', 'Styles/styles.typ', 'Styles/local_tags.typ'),
        )
        staged, summary = stage.stage_captured_proposals(
            context, snapshot, base_candidates, self.catalog,
            entrypoint='main.typ',
            reference_catalog=REFERENCE_CATALOG,
        )
        self.assertIsInstance(staged, MappingProxyType)
        self.assertEqual(summary['rule_count'], 9)
        self.assertEqual(tuple(summary['rule_ids']), stage.CAPTURED_PROPOSAL_RULE_IDS)
        self.assertEqual(summary['withheld_count'], 0, summary['withheld'])
        self.assertEqual(summary['status'], 'CONDITIONAL')
        self.assertEqual(summary['candidate_count'], 9)
        for rid in stage.CAPTURED_PROPOSAL_RULE_IDS:
            self.assertEqual(summary['candidate_count_by_rule'][rid], 1, f'Missing candidate for {rid}')
        for row in summary['sources']:
            self.assertEqual(row['verification'], stage.VERIFICATION_LABEL)
            self.assertNotEqual(row['baseline_sha256'], row['candidate_sha256'])
            self.assertFalse(row['runtime_binding_proved'] or row['execution_proved']
                             or row['render_equivalence_proved']
                             or row['edit_permission'] or row['compiler_permission'])
            cand_bytes = staged[('project', row['path'])]
            self.assertEqual(digest(cand_bytes), row['candidate_sha256'])
            # Fresh parse check: candidate must have zero syntax errors.
            self.assertFalse(parse(cand_bytes).nodes[0].errors)
        # Spot-check key candidate transformations.
        self.assertIn(b'#(s.tag)("alpha")', staged[('project', 'r1_scoped_call.typ')])
        self.assertIn(b'#(s.ref)("energy")', staged[('project', 'r3_scoped_ref.typ')])
        self.assertIn(b'<energy>', staged[('project', 'r4_proj_ref.typ')])
        self.assertNotIn(b'local_tags.typ', staged[('project', 'r6_redundant.typ')])
        self.assertIn(FLOWBOX_STROKE, staged[('project', 'r7_flowbox.typ')])
        self.assertNotIn(b'#paragraph_tab', staged[('project', 'r8_para_target.typ')])
        self.assertNotIn(b'#dots_space', staged[('project', 'r9_prose_spacer.typ')])
        # Real files on disk remain completely unmodified.
        for name, data in files.items():
            self.assertEqual((self.project / name).read_bytes(), data)
        self.assertEqual((self.project / 'main.typ').read_bytes(), main_src)

    def test_directive_preservation_and_unproven_or_overlap_withholding(self):
        self.write('leaf.typ', b'#let alpha = 1\n')
        self.write('Styles/styles.typ', b'#import "../leaf.typ": *\n')
        self.write('Styles/local_tags.typ', b'#import "../leaf.typ": *\n')
        # 1. Suppressed finding preserved, unsuppressed finding fixed.
        suppressed_src = (
            b'#import "@local/scoped-annotations:0.3.0": local-tag-scope\n'
            b'#local-tag-scope(s => [\n'
            b'  // typst-lint: ignore-next core.scoped-reference-access -- intentional\n'
            b'  #ref((s.tag)("keep"))\n'
            b'  #ref((s.tag)("fixme"))\n'
            b'])\n'
        )
        # 2. Unproven finding without catalog (core.project-ref-arguments -> diagnostic without edit -> withheld)
        unproven_src = (
            b'#import "@local/project-references:0.1.0": project-ref\n'
            b'#project-ref("pde", [@energy])\n'
        )
        # 3. Imported module that also has a captured proposal -> withheld fail-closed
        imported_mod_src = (
            b'#import "@local/scoped-annotations:0.3.0": local-tag-scope\n'
            b'#let helper = 1\n'
            b'#local-tag-scope(s => [#ref((s.tag)("energy"))])\n'
        )
        # 4. Prior-stage overlap target
        overlap_prior_src = (
            b'#import "@local/scoped-annotations:0.3.0": local-tag-scope\n'
            b'#local-tag-scope(s => [#ref((s.tag)("overlap"))])\n'
        )
        self.write('suppressed.typ', suppressed_src)
        self.write('unproven.typ', unproven_src)
        self.write('imported_mod.typ', imported_mod_src)
        self.write('overlap_prior.typ', overlap_prior_src)
        main_src = (
            b'#import "imported_mod.typ": helper\n'
            b'#include "suppressed.typ"\n'
            b'#include "unproven.typ"\n'
            b'#include "imported_mod.typ"\n'
            b'#include "overlap_prior.typ"\n'
        )
        self.write('main.typ', main_src)

        context, snapshot, base_candidates, _ = self._capture(
            'main.typ', extra_project_files=('leaf.typ', 'Styles/styles.typ', 'Styles/local_tags.typ'))

        # Simulate a prior sequence step touching the exact byte range of overlap_prior.typ's #ref(...) call.
        overlap_start = overlap_prior_src.index(b'#ref(')
        overlap_end = overlap_prior_src.index(b'))') + 2
        prior_records = [dict(
            path='overlap_prior.typ',
            steps=[dict(
                rule_id='core.parenthesized-scripts',
                edits=[dict(start=overlap_start, end=overlap_end,
                            replacement=overlap_prior_src[overlap_start:overlap_end].decode('utf-8'))],
            )],
        )]

        staged, summary = stage.stage_captured_proposals(
            context, snapshot, base_candidates, self.catalog,
            entrypoint='main.typ',
            prior_records=prior_records,
            reference_catalog=None,
        )
        self.assertEqual(summary['status'], 'INCOMPLETE')
        # Suppressed file verified: kept suppressed ref, fixed unsuppressed ref, preserved directive.
        cand_sup = staged[('project', 'suppressed.typ')]
        self.assertIn(b'// typst-lint: ignore-next core.scoped-reference-access -- intentional', cand_sup)
        self.assertIn(b'#ref((s.tag)("keep"))', cand_sup)
        self.assertIn(b'#(s.ref)("fixme")', cand_sup)
        # Unproven, imported, and overlapping sources remain completely unchanged fail-closed.
        self.assertEqual(staged[('project', 'unproven.typ')], unproven_src)
        self.assertEqual(staged[('project', 'imported_mod.typ')], imported_mod_src)
        self.assertEqual(staged[('project', 'overlap_prior.typ')], overlap_prior_src)
        withheld_by_path = {(row['path'], row['reason_code']) for row in summary['withheld']}
        self.assertIn(('unproven.typ', 'unproven-captured-proposal'), withheld_by_path)
        self.assertIn(('imported_mod.typ', 'imported-source-dependency'), withheld_by_path)
        self.assertIn(('overlap_prior.typ', 'prior-stage-edit-overlap'), withheld_by_path)

    def test_intra_captured_overlapping_proposals_withheld_fail_closed(self):
        src = (
            b'#import "@local/scoped-annotations:0.3.0": local-tag-scope\n'
            b'#local-tag-scope(s => [#ref((s.tag)("energy"))])\n'
        )
        self.write('main.typ', src)
        context, snapshot, base_candidates, _ = self._capture('main.typ')

        real_analyze = stage._analyze_nine_proposal_rules
        def overlapping_analyze(*args, **kwargs):
            results, extra = real_analyze(*args, **kwargs)
            ref_edits = results['core.scoped-reference-access'].edits
            if ref_edits:
                ed = ref_edits[0]
                # Inject a conflicting edit from another captured rule on the exact same byte span.
                results['core.scoped-function-call'] = stage.CapturedRuleResult(
                    edits=(Edit(ed.start, ed.end, b'#(s.tag)("conflict")'),)
                )
            return results, extra

        with patch.object(stage, '_analyze_nine_proposal_rules', side_effect=overlapping_analyze):
            staged, summary = stage.stage_captured_proposals(
                context, snapshot, base_candidates, self.catalog, entrypoint='main.typ')
        self.assertEqual(staged[('project', 'main.typ')], src)
        self.assertEqual(summary['candidate_count'], 0)
        self.assertEqual(summary['status'], 'INCOMPLETE')
        self.assertTrue(any(
            row['path'] == 'main.typ' and row['reason_code'] == 'overlapping-captured-proposals'
            for row in summary['withheld']
        ))

    def test_inspect_all_33_captured_rules_summary_and_fail_closed_invariants(self):
        self.write('leaf.typ', b'#let alpha = 1\n')
        self.write('Styles/styles.typ', b'#import "../leaf.typ": *\n')
        self.write('Styles/local_tags.typ', b'#import "../leaf.typ": *\n')
        main_src = (
            b'#import "@local/math-book:0.2.0": apply-math-book\n'
            b'#import "@local/math-blocks:0.2.0": apply-math-block-reset, light-theme\n'
            b'#show: apply-math-book.with(title: "Book")\n'
            b'#show: apply-math-book\n'
            b'#show: apply-math-block-reset\n'
            b'#set light-theme(fill: red)\n'
            b'Thus $ x^i $.\n'
            b'#include "ref.typ"\n'
        )
        ref_src = (
            b'#import "@local/scoped-annotations:0.3.0": local-tag-scope\n'
            b'#local-tag-scope(s => [#ref((s.tag)("energy"))])\n'
        )
        self.write('main.typ', main_src)
        self.write('ref.typ', ref_src)

        context, snapshot, _, _ = self._capture(
            'main.typ', extra_project_files=('leaf.typ', 'Styles/styles.typ', 'Styles/local_tags.typ'))
        report = stage.inspect_all_captured_rules(context, snapshot, self.catalog, entrypoint='main.typ')
        self.assertEqual(report['catalog_rule_count'], 33)
        self.assertEqual(report['inspected_rule_count'], 33)
        self.assertEqual(len(report['rule_ids']), 33)
        self.assertEqual(report['diagnostic_only_rule_count'], 11)
        self.assertEqual(set(report['diagnostic_only_rule_ids']), set(stage.DIAGNOSTIC_ONLY_RULE_IDS))
        self.assertEqual(len(report['captured_proposal_rule_ids']), 9)
        self.assertEqual(len(report['proposal_capable_rule_ids']), 22)
        self.assertEqual(report['source_count'], 2)
        self.assertEqual(report['coverage_entries'], 66)
        self.assertFalse(report['global_coverage_complete'])
        self.assertFalse(report['coverage_complete'])
        self.assertFalse(report['runtime_binding_proved'] or report['execution_proved']
                         or report['render_equivalence_proved']
                         or report['edit_permission'] or report['compiler_permission'])
        # Diagnostic-only rules (book show, duplicate reset, no-set-theme) and proposal rules both reported.
        found_rules = {d['rule_id'] for d in report['diagnostics']}
        self.assertTrue({
            'core.single-book-show',
            'core.no-duplicate-book-reset',
            'core.no-set-theme',
            'core.scoped-reference-access',
        }.issubset(found_rules), found_rules)
        # Originals untouched.
        self.assertEqual((self.project / 'main.typ').read_bytes(), main_src)
        self.assertEqual((self.project / 'ref.typ').read_bytes(), ref_src)

    def _issue_modern_spacer_scoped_fixture(self):
        modern_fixture = highlight_fixtures.CapturedModernHighlightTests('runTest')
        modern_fixture.setUp()
        self.addCleanup(modern_fixture.doCleanups)
        scoped_src = TOOL / 'tests/fixtures/reviewed-packages/local/scoped-annotations/0.3.0'
        scoped_dst = modern_fixture.packages / 'local/scoped-annotations/0.3.0'
        scoped_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(scoped_src, scoped_dst)
        main_src = (
            IMPORT_MODERN
            + IMPORT_SCOPED
            + b'#highlighted[Leading text $x$ end.]\n'
            + b'#block[$ x #dots_space #footnote[Reason.] $]\n'
            + b'#local-tag-scope(s => [#s.tag("alpha")])\n'
        )
        extra = [
            ('packages', 'local/scoped-annotations/0.3.0/typst.toml', 'package'),
            ('packages', 'local/scoped-annotations/0.3.0/lib.typ', 'package'),
        ]
        _, analyzer, snapshot, _ = modern_fixture.issue(main_src, extra=extra)
        session = analyzer._session
        context = session._context
        proof = footnote.PreviewFootnoteEvidence(session, snapshot)
        records = [
            row for src in context.sources
            if (row := sequence.preview(
                src.tree, src.path, self.catalog,
                profile='conditional-gaps-and-spacers',
                footnote_evidence=proof,
            )) is not None
        ]
        return modern_fixture, main_src, context, snapshot, proof, records

    def test_composed_modern_highlight_shifts_offsets_before_conditional_footnote_and_captured_proposal(self):
        # Combined --conditional-spacers --conditional-facade-footnotes --compose-modern-highlights
        # --conditional-captured-proposals: a leading #highlighted[...] edit shifts byte offsets before
        # a trailing #footnote[...] and a non-overlapping core.scoped-function-call candidate.
        modern_fixture, main_src, context, snapshot, proof, records = self._issue_modern_spacer_scoped_fixture()
        self.assertEqual(len(records), 1)
        self.assertTrue(any(step.get('conditional_footnote_calls') for step in records[0]['steps']))

        base_candidates, modern_summary, composed_view = composed.stage(
            proof, records,
            profile='conditional-gaps-and-spacers',
            sequence_facade_evidence=True,
        )
        pre_modern = composed_view.sources[('project', 'main.typ')]
        post_modern = base_candidates[('project', 'main.typ')]
        # Confirm the leading modern highlight edit actually shifted byte offsets before #footnote.
        self.assertNotEqual(pre_modern, post_modern)
        self.assertNotEqual(pre_modern.index(b'#footnote['), post_modern.index(b'#footnote['))

        staged, summary = stage.stage_captured_proposals(
            context, snapshot, base_candidates, self.catalog,
            entrypoint='main.typ',
            prior_records=records,
            prior_modern_summary=modern_summary,
            pre_modern_sources=composed_view.sources,
        )
        self.assertEqual(summary['status'], 'CONDITIONAL', summary['withheld'])
        self.assertEqual(summary['withheld_count'], 0, summary['withheld'])
        self.assertEqual(summary['candidate_count'], 1)
        self.assertEqual(summary['candidate_count_by_rule']['core.scoped-function-call'], 1)
        final_main = staged[('project', 'main.typ')]
        self.assertIn(b'#highlighted[Leading text ]$x$#highlighted[ end.]', final_main)
        self.assertNotIn(b'#dots_space', final_main)
        self.assertIn(b'#(s.tag)("alpha")', final_main)
        self.assertFalse(parse(final_main).nodes[0].errors)

        # Also exercise the full CLI flag combination with mocked sandboxed compile.
        roots = dict(snapshot.roots)
        manifest_path = self.root / 'combined-manifest.json'
        manifest_path.write_text(json.dumps(dict(
            roots=roots,
            files=[
                dict(root=item.request.root, path=item.request.path, role=item.request.role)
                for item in snapshot.files
            ],
        )), encoding='utf-8')
        cli_candidates = []
        def capture_compile(ctx, snap, cands, **kwargs):
            cli_candidates.append((cands, kwargs))
            return _fake_preview_result(
                pdfs_identical=False,
                modern_highlight_evidence=kwargs.get('modern_summary'),
            )
        stdout = io.StringIO()
        with patch.object(stage, 'compile_staged_preview', side_effect=capture_compile), \
             contextlib.redirect_stdout(stdout):
            exit_code = document_preview_cli.main([
                '--root', str(modern_fixture.project),
                '--entrypoint', 'main.typ',
                '--manifest', str(manifest_path),
                '--conditional-gaps',
                '--conditional-spacers',
                '--conditional-facade-footnotes',
                '--compose-modern-highlights',
                '--conditional-captured-proposals',
                '--format', 'json',
            ])
        self.assertEqual(exit_code, 2)
        report = json.loads(stdout.getvalue())
        self.assertEqual(report['conditional_captured_proposals']['status'], 'CONDITIONAL')
        self.assertEqual(report['conditional_captured_proposals']['withheld_count'], 0)
        self.assertEqual(report['conditional_captured_proposals']['candidate_count'], 1)
        self.assertEqual(len(cli_candidates), 1)
        self.assertIn(b'#(s.tag)("alpha")', cli_candidates[0][0][('project', 'main.typ')])
        self.assertIsNotNone(cli_candidates[0][1]['composed_view'])
        self.assertIsNotNone(cli_candidates[0][1]['composed_modern_evidence'])
        self.assertEqual((modern_fixture.project / 'main.typ').read_bytes(), main_src)

    def test_modern_highlight_overlap_with_conditional_footnote_withholds_fail_closed(self):
        _, _, context, snapshot, proof, records = self._issue_modern_spacer_scoped_fixture()
        base_candidates, modern_summary, composed_view = composed.stage(
            proof, records,
            profile='conditional-gaps-and-spacers',
            sequence_facade_evidence=True,
        )
        # Mutate post-modern bytes inside the #footnote[...] call span so the pre-modern
        # conditional footnote call cannot map cleanly without overlap.
        corrupted = dict(base_candidates)
        corrupted[('project', 'main.typ')] = base_candidates[('project', 'main.typ')].replace(
            b'#footnote[Reason.]', b'#footnote[Altered.]'
        )
        staged, summary = stage.stage_captured_proposals(
            context, snapshot, MappingProxyType(corrupted), self.catalog,
            entrypoint='main.typ',
            prior_records=records,
            prior_modern_summary=modern_summary,
            pre_modern_sources=composed_view.sources,
        )
        self.assertEqual(summary['status'], 'INCOMPLETE')
        self.assertEqual(summary['candidate_count'], 0)
        self.assertEqual(summary['withheld_count'], 1)
        self.assertEqual(staged[('project', 'main.typ')], corrupted[('project', 'main.typ')])
        self.assertIn('footnote', summary['withheld'][0]['reason'].lower())

    def test_overlapping_deferred_and_aligned_conditional_footnote_calls_withheld_fail_closed(self):
        # A core.scoped-function-call edit inside #footnote[...] touches a conditionally bound
        # footnote call recorded via conditional_deferred_footnote_calls or conditional_aligned_spacer_calls.
        main_src = (
            b'#import "@local/scoped-annotations:0.3.0": local-tag-scope\n'
            b'#local-tag-scope(s => [$\n  A #footnote[#s.tag("alpha")]\n$])\n'
        )
        self.write('main.typ', main_src)
        context, snapshot, base_candidates, _ = self._capture('main.typ')
        fn_offset = main_src.index(b'#footnote[')
        src_sha = digest(main_src)

        for field_name in ('conditional_deferred_footnote_calls', 'conditional_aligned_spacer_calls'):
            with self.subTest(field=field_name):
                prior_records = [dict(
                    path='main.typ',
                    baseline_sha256=src_sha,
                    candidate_sha256=src_sha,
                    steps=[dict(
                        rule_id='pde.unaligned-footnote-no-spacer'
                                if field_name == 'conditional_deferred_footnote_calls'
                                else 'pde.aligned-footnote-spacer',
                        baseline_sha256=src_sha,
                        candidate_sha256=src_sha,
                        edits=[],
                        **{field_name: [fn_offset]},
                    )],
                )]
                staged, summary = stage.stage_captured_proposals(
                    context, snapshot, base_candidates, self.catalog,
                    entrypoint='main.typ',
                    prior_records=prior_records,
                )
                self.assertEqual(summary['status'], 'INCOMPLETE')
                self.assertEqual(summary['candidate_count'], 0)
                self.assertEqual(summary['withheld_count'], 1)
                self.assertEqual(staged[('project', 'main.typ')], main_src)
                self.assertIn('footnote', summary['withheld'][0]['reason'].lower())


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class CompileStagedPreviewParityTests(unittest.TestCase):
    def setUp(self):
        self.f = highlight_fixtures.CapturedModernHighlightTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.project.parent
        self.project, self.packages, self.cache = self.f.project, self.f.packages, self.f.cache
        self.catalog = load_rules()[0]['rules']
        self.main_src = (
            IMPORT_MODERN
            + b'#highlighted[Alpha $x$ end.]\n'
            + b'Thus $ x^i $\n\nHence.\n'
        )
        self.hl_evidence, self.analyzer, self.snapshot, self.candidates = self.f.issue(self.main_src)
        self.session = self.analyzer._session
        self.context = self.session._context
        self.roots = dict(self.snapshot.roots)

    def _mock_compile_patches(self, *, dep_sets=None, inject_unexpected=False, recorded_commands=None):
        fake_compiler_path = self.root / 'fake-typst'
        if not fake_compiler_path.exists():
            fake_compiler_path.write_bytes(b'#!/bin/sh\n')
            fake_compiler_path.chmod(0o500)
        compiler_bytes = fake_compiler_path.read_bytes()
        compiler_evidence = SimpleNamespace(sha256=digest(compiler_bytes), size=len(compiler_bytes))
        compiler_record = SimpleNamespace(path=fake_compiler_path, version=b'typst 0.14.2\n')
        sandbox_evidence = SimpleNamespace(sha256='d' * 64, size=1024)
        real_capture = runtime._capture
        pdf_header = b'%PDF-1.7\n1 0 obj\n<<>>\nendobj\n'
        pdf_bytes = (
            pdf_header
            + b'xref\n0 1\n0000000000 65535 f \ntrailer\n<< /Size 1 >>\nstartxref\n'
            + f'{len(pdf_header)}\n%%EOF\n'.encode('ascii')
        )

        def fake_capture(path, max_bytes, *, executable=False):
            if Path(path) == preview_compile.SANDBOX:
                return b'sandbox', sandbox_evidence
            return real_capture(path, max_bytes, executable=executable)

        def fake_run(argv, *, purpose, cwd, environment, deadline, cancel_event, check_workspace, _ledger=None):
            if recorded_commands is not None:
                recorded_commands.append((purpose, list(argv)))
            check_workspace()
            if purpose == 'document-preview-version':
                return runtime.Invocation(purpose, tuple(argv), str(cwd), (), 0, compiler_record.version, b'', started=True)
            label = 'baseline' if purpose.endswith('baseline') else 'candidate'
            workspace = Path(cwd).parent
            if inject_unexpected and label == 'candidate':
                rogue = workspace / 'unexpected-file'
                rogue.write_bytes(b'rogue')
                rogue.chmod(0o600)
            (workspace / 'output.pdf').write_bytes(pdf_bytes)
            (workspace / 'dependencies').write_bytes(b'main.typ\0')
            return runtime.Invocation(purpose, tuple(argv), str(cwd), (), 0, b'', b'', started=True)

        call_index = [0]
        def fake_compare(snapshot, dep_bytes, *, cwd, roots):
            idx = call_index[0]
            call_index[0] += 1
            observed = (dep_sets[idx] if dep_sets is not None else (('project', 'main.typ'),))
            return SimpleNamespace(observed=observed, unobserved=(), missing=())

        return (
            patch.object(runtime, '_capture_compiler', return_value=(compiler_record, compiler_bytes, compiler_evidence)),
            patch.object(runtime, '_capture', side_effect=fake_capture),
            patch.object(runtime, '_run', side_effect=fake_run),
            patch.object(runtime, '_same', return_value=None),
            patch.object(document_dependencies, 'compare', side_effect=fake_compare),
        )

    def test_no_private_cross_module_imports_in_captured_preview_stage(self):
        source = (TOOL / 'document_captured_preview_stage.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        forbidden_preview = {'_sandbox_profile', '_mkdir', '_workspace_guard', '_run_tracked'}
        forbidden_runtime = {'_capture_compiler', '_write_private', '_bind_output', '_environment',
                             '_output_identity', '_same', '_pdf', '_MAX_EXECUTABLE'}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotEqual(alias.name, 'closed_core_compile')
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, 'closed_core_compile')
                if node.module == 'document_preview_compile':
                    for alias in node.names:
                        self.assertNotIn(alias.name, forbidden_preview)
            elif isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name) and node.value.id in ('preview_compile', 'document_preview_compile'):
                    self.assertNotIn(node.attr, forbidden_preview)
                if isinstance(node.value, ast.Name) and node.value.id in ('runtime', 'closed_core_compile'):
                    self.assertNotIn(node.attr, forbidden_runtime)

    def test_workspace_budget_and_sandbox_workspace_and_dependency_mismatch_parity(self):
        # 1. Shared MAX_PREVIEW_BYTES budget refuses before compiler capture in both paths.
        with patch.object(preview_compile, 'MAX_PREVIEW_BYTES', 1), \
             patch.object(runtime, '_capture_compiler') as capture:
            with self.assertRaisesRegex(SafetyError, 'workspace budget'):
                stage.compile_staged_preview(
                    self.context, self.snapshot, self.candidates, entrypoint='main.typ')
            capture.assert_not_called()

        # 2. Sandbox profile invocation and successful mocked staged compile.
        commands = []
        with contextlib.ExitStack() as stack:
            for cm in self._mock_compile_patches(recorded_commands=commands):
                stack.enter_context(cm)
            res = stage.compile_staged_preview(
                self.context, self.snapshot, self.candidates, entrypoint='main.typ')
        self.assertTrue(res.pdfs_identical)
        self.assertEqual(len(commands), 3)
        for _, argv in commands:
            self.assertEqual(argv[0], str(preview_compile.SANDBOX))
            self.assertEqual(argv[1], '-p')
            self.assertIn('(deny network*)', argv[2])
            self.assertIn('(deny file-write*)', argv[2])

        # 3. Unexpected workspace file triggers _workspace_guard refusal.
        with contextlib.ExitStack() as stack:
            for cm in self._mock_compile_patches(inject_unexpected=True):
                stack.enter_context(cm)
            with self.assertRaisesRegex(SafetyError, 'Unexpected or oversized private preview file'):
                stage.compile_staged_preview(
                    self.context, self.snapshot, self.candidates, entrypoint='main.typ')

        # 4. Dependency membership mismatch between baseline and candidate is refused.
        with contextlib.ExitStack() as stack:
            for cm in self._mock_compile_patches(
                dep_sets=[(('project', 'main.typ'),), (('project', 'main.typ'), ('project', 'extra.typ'))]
            ):
                stack.enter_context(cm)
            with self.assertRaisesRegex(SafetyError, 'dependency membership changed'):
                stage.compile_staged_preview(
                    self.context, self.snapshot, self.candidates, entrypoint='main.typ')

    def test_composed_view_and_modern_evidence_validation_parity(self):
        proof = footnote.PreviewFootnoteEvidence(self.session, self.snapshot)
        records = [
            row for src in self.context.sources
            if (row := sequence.preview(src.tree, src.path, self.catalog)) is not None
        ]
        staged_cands, modern_summary, composed_view = composed.stage(proof, records)

        # Missing composed_modern_evidence when composed_view is passed -> refused!
        with patch.object(runtime, '_capture_compiler') as capture:
            with self.assertRaisesRegex(SafetyError, 'matching issued resource capture'):
                stage.compile_staged_preview(
                    self.context, self.snapshot, staged_cands, entrypoint='main.typ',
                    modern_summary=modern_summary, composed_view=composed_view,
                    composed_modern_evidence=None)
            capture.assert_not_called()

        # Missing composed_view when composed_modern_evidence is passed -> refused!
        with patch.object(runtime, '_capture_compiler') as capture:
            with self.assertRaisesRegex(SafetyError, 'matching issued resource capture'):
                stage.compile_staged_preview(
                    self.context, self.snapshot, staged_cands, entrypoint='main.typ',
                    modern_summary=modern_summary, composed_view=None,
                    composed_modern_evidence=proof)
            capture.assert_not_called()

        # Valid composed_view + composed_modern_evidence runs _valid before and after compile.
        with contextlib.ExitStack() as stack:
            for cm in self._mock_compile_patches():
                stack.enter_context(cm)
            valid_mock = stack.enter_context(patch.object(footnote, '_valid', wraps=footnote._valid))
            res = stage.compile_staged_preview(
                self.context, self.snapshot, staged_cands, entrypoint='main.typ',
                modern_summary=modern_summary, composed_view=composed_view,
                composed_modern_evidence=proof)
        self.assertEqual(res.modern_highlight_evidence, modern_summary)
        self.assertGreaterEqual(valid_mock.call_count, 2)

        # modern_highlight_evidence (CapturedModernHighlightEvidence) runs modern_evidence._valid before & after compile.
        hl_evidence = self.hl_evidence
        hl_cands, hl_summary = modern_evidence.stage_candidates(hl_evidence)
        with contextlib.ExitStack() as stack:
            for cm in self._mock_compile_patches():
                stack.enter_context(cm)
            hl_valid_mock = stack.enter_context(
                patch.object(modern_evidence, '_valid', wraps=modern_evidence._valid))
            res = stage.compile_staged_preview(
                self.context, self.snapshot, hl_cands, entrypoint='main.typ',
                modern_highlight_evidence=hl_evidence, modern_summary=hl_summary)
        self.assertEqual(res.modern_highlight_evidence, hl_summary)
        self.assertGreaterEqual(hl_valid_mock.call_count, 2)

        # Failing modern_evidence._valid refuses before compiler launch.
        with patch.object(modern_evidence, '_valid', side_effect=SafetyError('tampered modern evidence')), \
             patch.object(runtime, '_capture_compiler') as capture:
            with self.assertRaisesRegex(SafetyError, 'tampered modern evidence'):
                stage.compile_staged_preview(
                    self.context, self.snapshot, hl_cands, entrypoint='main.typ',
                    modern_highlight_evidence=hl_evidence, modern_summary=hl_summary)
            capture.assert_not_called()


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class PreviewCliExtendedWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='typst-preview-cli-ext-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / 'project'
        self.packages = self.root / 'packages'
        self.cache = self.root / 'cache'
        for d in (self.project, self.packages, self.cache):
            d.mkdir()
        _materialize_reviewed_packages(self.packages)

        self.main_src = (
            b'Thus $ x^i $\n\nHence.\n'
            b'#include "ref.typ"\n'
            b'#include "unproven.typ"\n'
        )
        self.ref_src = (
            b'#import "@local/scoped-annotations:0.3.0": local-tag-scope\n'
            b'#local-tag-scope(s => [#ref((s.tag)("energy"))])\n'
        )
        self.unproven_src = (
            b'#import "@local/project-references:0.1.0": project-ref\n'
            b'#project-ref("pde", [@energy])\n'
        )
        (self.project / 'main.typ').write_bytes(self.main_src)
        (self.project / 'ref.typ').write_bytes(self.ref_src)
        (self.project / 'unproven.typ').write_bytes(self.unproven_src)

        self.manifest = self.root / 'manifest.json'
        files = [
            dict(root='project', path='main.typ', role='source'),
            dict(root='project', path='ref.typ', role='source'),
            dict(root='project', path='unproven.typ', role='source'),
            *_package_resource_requests(self.packages),
        ]
        self.manifest.write_text(json.dumps(dict(
            roots=dict(project=str(self.project), packages=str(self.packages), cache=str(self.cache)),
            files=files,
        )), encoding='utf-8')

    def _base_args(self, *extra):
        return ['--root', str(self.project), '--entrypoint', 'main.typ',
                '--manifest', str(self.manifest), *extra]

    def test_help_exposes_new_flags_via_both_typst_lint_and_typst_style(self):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        for cmd in (
            [sys.executable, '-B', str(TOOL / 'typst-lint'), 'preview-compile', '--help'],
            [sys.executable, '-B', str(TOOL / 'typst-style'), 'compile', '--preview', '--help'],
        ):
            with self.subTest(cmd=cmd[2:5]):
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20, env=env)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn('--conditional-captured-proposals', proc.stdout)
                self.assertIn('--inspect-all-captured-rules', proc.stdout)

    def test_conditional_captured_proposals_reachable_via_both_entrypoints(self):
        captured_mappings = []

        def fake_staged_compile(context, snapshot, candidates, **kwargs):
            captured_mappings.append(candidates)
            return _fake_preview_result(pdfs_identical=False)

        for label, invoker in (
            ('typst-lint preview-compile', lambda extra: typst_lint.main(['preview-compile', *self._base_args(*extra)])),
            ('typst-style compile --preview', lambda extra: typst_lint.main(['compile', '--preview', *self._base_args(*extra)], gate=True)),
        ):
            with self.subTest(entrypoint=label):
                captured_mappings.clear()
                stdout = io.StringIO()
                with patch.object(stage, 'compile_staged_preview', side_effect=fake_staged_compile), \
                     contextlib.redirect_stdout(stdout):
                    status = invoker(['--conditional-captured-proposals', '--format', 'json'])
                self.assertEqual(status, 2)
                report = json.loads(stdout.getvalue())
                self.assertEqual(report['status'], 2)
                self.assertFalse(report['global_coverage_complete'])
                self.assertFalse(report['source_files_modified'])
                self.assertFalse(report['publication_authority'])
                self.assertFalse(report['source_apply_gate']['eligible'])
                self.assertIn('captured_proposals_runtime_binding_unproved', report['source_apply_gate']['blockers'])
                self.assertIn('captured_proposals_withheld', report['source_apply_gate']['blockers'])
                # Verified candidate composed into the in-memory snapshot passed to sandboxed compile!
                self.assertEqual(len(captured_mappings), 1)
                staged = captured_mappings[0]
                self.assertIsInstance(staged, MappingProxyType)
                self.assertIn(b'#(s.ref)("energy")', staged[('project', 'ref.typ')])
                # Unproven source withheld fail-closed without mutating bytes!
                self.assertEqual(staged[('project', 'unproven.typ')], self.unproven_src)
                # Baseline sequence edit on main.typ also preserved!
                self.assertNotEqual(staged[('project', 'main.typ')], self.main_src)
                cp = report['conditional_captured_proposals']
                self.assertEqual(cp['rule_count'], 9)
                self.assertEqual(cp['candidate_count'], 1)
                self.assertEqual(cp['withheld_count'], 1)
                self.assertEqual(cp['status'], 'INCOMPLETE')
                for rid in stage.CAPTURED_PROPOSAL_RULE_IDS:
                    self.assertIn(rid, report['conditionally_previewed_rule_ids'])
                # Real files on disk untouched!
                self.assertEqual((self.project / 'main.typ').read_bytes(), self.main_src)
                self.assertEqual((self.project / 'ref.typ').read_bytes(), self.ref_src)
                self.assertEqual((self.project / 'unproven.typ').read_bytes(), self.unproven_src)

    def test_inspect_all_captured_rules_via_both_entrypoints_keeps_fail_closed_gate(self):
        fake = _fake_preview_result(pdfs_identical=True)
        for label, invoker in (
            ('typst-lint preview-compile', lambda extra: typst_lint.main(['preview-compile', *self._base_args(*extra)])),
            ('typst-style compile --preview', lambda extra: typst_lint.main(['compile', '--preview', *self._base_args(*extra)], gate=True)),
        ):
            with self.subTest(entrypoint=label):
                stdout = io.StringIO()
                with patch.object(document_preview_cli.document_preview_compile, 'compile_preview', return_value=fake), \
                     contextlib.redirect_stdout(stdout):
                    status = invoker(['--inspect-all-captured-rules', '--format', 'json'])
                self.assertEqual(status, 2)
                report = json.loads(stdout.getvalue())
                self.assertEqual(report['status'], 2)
                self.assertFalse(report['global_coverage_complete'])
                self.assertFalse(report['source_apply_gate']['eligible'])
                self.assertIn('global_rule_coverage_incomplete', report['source_apply_gate']['blockers'])
                self.assertIn('captured_all_rules_inspection_bounded', report['source_apply_gate']['blockers'])
                self.assertEqual(len(report['partially_inspected_rule_ids']), 33)
                self.assertEqual(report['uninspected_rule_ids'], [])
                insp = report['all_captured_rules_inspection']
                self.assertEqual(insp['catalog_rule_count'], 33)
                self.assertEqual(insp['inspected_rule_count'], 33)
                self.assertEqual(insp['diagnostic_only_rule_count'], 11)
                self.assertFalse(insp['global_coverage_complete'])
                self.assertGreaterEqual(insp['diagnostics_count'], 2)

    def test_both_new_flags_together_and_text_output(self):
        fake = _fake_preview_result(pdfs_identical=True)
        for fmt in ('json', 'text'):
            with self.subTest(format=fmt):
                stdout = io.StringIO()
                with patch.object(stage, 'compile_staged_preview', return_value=fake), \
                     contextlib.redirect_stdout(stdout):
                    status = document_preview_cli.main(self._base_args(
                        '--conditional-captured-proposals', '--inspect-all-captured-rules', '--format', fmt))
                self.assertEqual(status, 2)
                if fmt == 'json':
                    report = json.loads(stdout.getvalue())
                    self.assertIn('conditional_captured_proposals', report)
                    self.assertIn('all_captured_rules_inspection', report)
                    self.assertEqual(len(report['partially_inspected_rule_ids']), 33)
                    self.assertEqual(report['uninspected_rule_ids'], [])
                    self.assertFalse(report['global_coverage_complete'])
                    self.assertFalse(report['source_apply_gate']['eligible'])
                else:
                    text = stdout.getvalue()
                    self.assertIn('Conditional captured proposals (9 rules):', text)
                    self.assertIn('Captured-rule inspection: 33/33 rules', text)
                    self.assertIn('source_apply_gate.eligible=False', text)

    def test_conditional_modern_highlights_rejects_captured_proposals(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as ctx:
            document_preview_cli.main(self._base_args(
                '--conditional-modern-highlights', '--conditional-captured-proposals'))
        self.assertEqual(ctx.exception.code, 2)
        error_line = stderr.getvalue().strip().splitlines()[-1]
        self.assertIn('exclusive preview', error_line)
        self.assertNotIn('--review-edits', error_line)

    def test_conditional_captured_proposals_rejects_review_edits(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as ctx:
            document_preview_cli.main(self._base_args(
                '--conditional-captured-proposals', '--review-edits', '--format', 'json'))
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn('--conditional-captured-proposals does not yet support --review-edits',
                      stderr.getvalue())

    def test_backward_compatibility_when_new_flags_omitted(self):
        fake = _fake_preview_result(pdfs_identical=True)
        stdout = io.StringIO()
        with patch.object(document_preview_cli.document_preview_compile, 'compile_preview', return_value=fake) as cp, \
             patch.object(stage, 'stage_captured_proposals', side_effect=AssertionError('must not run')), \
             patch.object(stage, 'inspect_all_captured_rules', side_effect=AssertionError('must not run')), \
             contextlib.redirect_stdout(stdout):
            status = document_preview_cli.main(self._base_args('--format', 'json'))
        self.assertEqual(status, 2)
        cp.assert_called_once()
        report = json.loads(stdout.getvalue())
        self.assertNotIn('conditional_captured_proposals', report)
        self.assertNotIn('all_captured_rules_inspection', report)
        self.assertEqual(
            report['partially_inspected_rule_ids'],
            [document_display.STANDALONE, script_rule.RULE_ID, document_display.RULE_ID],
        )
        self.assertEqual(len(report['uninspected_rule_ids']), 30)
        self.assertFalse(report['global_coverage_complete'])
        self.assertFalse(report['source_apply_gate']['eligible'])
        self.assertEqual(
            report['source_apply_gate']['blockers'],
            ['global_rule_coverage_incomplete', 'dependency_closure_unproved',
             'live_source_freshness_unproved', 'protected_source_transaction_unavailable'],
        )
        self.assertEqual((self.project / 'main.typ').read_bytes(), self.main_src)
