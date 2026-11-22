"""Explicit composed references retain and independently replay prior plans."""
from copy import deepcopy
from pathlib import Path
import os
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
import json

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_composed_modern_highlight as composed
import document_composed_reference_routes as routes
import document_footnote_evidence as footnote
import document_sequence_preview as sequence
import test_document_modern_highlight_evidence as fixtures
from edit_plan import Edit, plan
from typst_lint import InputError, SafetyError, digest, load_rules

IMPORT = fixtures.fixtures.IMPORT
SCOPE = b'#import "@local/scoped-annotations:0.3.0": local-tag-scope, local-scope-annotations\n'


@unittest.skipUnless(fixtures.fixtures.EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ComposedHighlightReferencesTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.CapturedModernHighlightTests('runTest'); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        src = TOOL / 'tests/fixtures/reviewed-packages/local/scoped-annotations/0.3.0'
        dst = self.f.packages / 'local/scoped-annotations/0.3.0'; dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)
        src = TOOL / 'tests/fixtures/scoped-reference-packages/preview/mannot/0.4.0'
        dst = self.f.packages / 'preview/mannot/0.4.0'; dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)

    def capture(self, body):
        source = IMPORT + SCOPE + body
        extra = [('packages', 'local/scoped-annotations/0.3.0/' + name, 'package')
                 for name in ('typst.toml', 'lib.typ')]
        extra += [('packages', 'preview/mannot/0.4.0/' + name, 'package')
                  for name in ('typst.toml', 'src/lib.typ')]
        _, analyzer, snapshot, _ = self.f.issue(source, extra=extra)
        proof = footnote.PreviewFootnoteEvidence(analyzer._session, snapshot)
        catalog = load_rules()[0]['rules']
        records = [row for src in analyzer._session._context.sources
                   if (row := sequence.preview(src.tree, src.path, catalog)) is not None]
        self.assertTrue(all(row['verification'] != 'withheld' for row in records))
        return source, proof, records

    def test_direct_and_scoped_preserve_baseline_and_candidate_routes(self):
        source, proof, records = self.capture(b'#highlighted[Plain $a$ end.]\n'
            b'#highlighted[See @thing and $x$ end.]\n'
            b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") and $y$ end.]])')
        old, old_report, _ = composed.stage(proof, records, reviewed_math=True)
        final, report, _ = composed.stage(proof, records, reviewed_math=True, composed_references=True)
        self.assertEqual(old_report['candidate_count'], 1)
        self.assertEqual(report['candidate_count'], 3, report)
        self.assertEqual(report['reference_candidate_routes']['paths'], ('main.typ',))
        self.assertGreaterEqual(report['reference_candidate_routes']['generated_calls'], 2)
        self.assertIn(b'@thing', final['project','main.typ'])
        self.assertIn(b'#(s.ref)("one")', final['project','main.typ'])
        self.assertNotEqual(final['project','main.typ'], old['project','main.typ'])
        self.assertFalse(report['runtime_binding_proved'] or report['render_equivalence_proved']
                         or report['edit_permission'] or report['compiler_permission'])
        self.assertEqual((self.f.project/'main.typ').read_bytes(), source)

    def test_default_never_requests_reference_proof(self):
        _, proof, records = self.capture(b'#highlighted[Plain $a$ end.]')
        original = composed.highlight.preview_all
        options = []
        def trace(*a, **kw):
            options.append(kw)
            return original(*a, **kw)
        with patch.object(composed.highlight, 'preview_all', side_effect=trace), \
             patch.object(routes, 'verify', side_effect=AssertionError('reference route proof requested')):
            _, report, _ = composed.stage(proof, records, reviewed_math=True)
        self.assertEqual(len(options), 2)
        self.assertTrue(all('allow_direct_references' not in kw and 'allow_scoped_references' not in kw for kw in options))
        self.assertNotIn('composed_references', report)

    def test_shadow_and_suppression_never_generate_reference_edits(self):
        body = (b'// typst-lint: ignore-next pde.text-only-highlight -- intended\n'
                b'#highlighted[See @thing and $x$.]\n'
                b'#let highlighted = none\n#highlighted[See @other and $y$.]')
        _, proof, records = self.capture(body)
        final, report, view = composed.stage(proof, records, reviewed_math=True, composed_references=True)
        self.assertEqual(report['candidate_count'], 0)
        self.assertEqual(final['project','main.typ'], view.sources['project','main.typ'])

    def test_candidate_route_exhaustion_preserves_existing_plan(self):
        _, proof, records = self.capture(b'#highlighted[Plain $a$ end.]\n#highlighted[See @thing $x$ end.]')
        old, old_report, _ = composed.stage(proof, records, reviewed_math=True)
        with patch.object(routes, 'MAX_FINAL_ROUTE_WORK', 1):
            final, report, _ = composed.stage(proof, records, reviewed_math=True, composed_references=True)
        self.assertEqual(final, old)
        self.assertEqual(report['candidate_count'], old_report['candidate_count'])
        self.assertTrue(report['reference_budget_fallback'])
        self.assertEqual(report['reference_budget_fallback_stage'], 'candidate-route')

    def test_reference_overlap_retains_nonoverlapping_facade_baseline(self):
        _, proof, _ = self.capture(b'#highlighted[Plain $a$ end.]\n'
            b'#highlighted[See @thing $x$ end.]\n#block[$ y #dots_space #footnote[Note] $]')
        profile = 'conditional-gaps-and-spacers'
        catalog = load_rules()[0]['rules']
        records = [row for src in proof._session._context.sources
                   if (row := sequence.preview(src.tree, src.path, catalog, profile=profile,
                                               footnote_evidence=proof)) is not None]
        self.assertTrue(any(step.get('conditional_footnote_calls') for row in records for step in row['steps']))
        old, old_report, _ = composed.stage(proof, records, profile=profile,
            sequence_facade_evidence=True, reviewed_math=True)
        original = sequence._map_conditional_calls_through_edits
        def overlap(tree, calls, edits):
            if any(b'@thing' in edit.replacement for edit in edits):
                raise sequence.ConditionalFootnoteEditOverlap('fixture overlap')
            return original(tree, calls, edits)
        with patch.object(sequence, '_map_conditional_calls_through_edits', side_effect=overlap):
            final, report, _ = composed.stage(proof, records, profile=profile,
                sequence_facade_evidence=True, reviewed_math=True, composed_references=True)
        self.assertEqual(final, old)
        self.assertEqual(report['candidate_count'], old_report['candidate_count'])
        self.assertEqual(report['reference_fallback_sources'], ('main.typ',))

    def test_old_edits_are_preserved_when_expanded_batch_omits_them(self):
        _, proof, records = self.capture(b'#highlighted[Plain $a$ end.]\n#highlighted[See @thing $x$ end.]')
        old, _, _ = composed.stage(proof, records, reviewed_math=True)
        original = composed.highlight.preview_all
        def omitted(*args, **kwargs):
            diagnostics, result = original(*args, **kwargs)
            if kwargs.get('allow_scoped_references'):
                result = deepcopy(result)
                result['by_file'][0]['edits'] = result['by_file'][0]['edits'][1:]
            return diagnostics, result
        with patch.object(composed.highlight, 'preview_all', side_effect=omitted):
            final, report, _ = composed.stage(proof, records, reviewed_math=True, composed_references=True)
        self.assertEqual(final, old)
        self.assertEqual(report['reference_fallback_sources'], ('main.typ',))

    def test_final_route_mismatch_refuses_instead_of_fallback(self):
        _, proof, records = self.capture(b'#highlighted[See @thing $x$ end.]')
        with patch.object(routes._Tracer, 'body_math', return_value=(False, None)):
            with self.assertRaisesRegex(SafetyError, 'routes are unavailable'):
                composed.stage(proof, records, reviewed_math=True, composed_references=True)

    def test_reference_edit_in_imported_source_is_withheld(self):
        styles = IMPORT + b'#highlighted[See @thing and $x$ end.]'
        (self.f.project/'styles.typ').write_bytes(styles)
        source = b'#include "styles.typ"\n#import "styles.typ": *\n#highlighted[Plain $a$ end.]'
        extra = [('packages', 'local/scoped-annotations/0.3.0/' + name, 'package')
                 for name in ('typst.toml', 'lib.typ')]
        extra += [('packages', 'preview/mannot/0.4.0/' + name, 'package')
                  for name in ('typst.toml', 'src/lib.typ')]
        _, analyzer, snapshot, _ = self.f.issue(source, extra=extra)
        proof = footnote.PreviewFootnoteEvidence(analyzer._session, snapshot)
        final, report, view = composed.stage(proof, [], reviewed_math=True, composed_references=True)
        self.assertEqual(final['project','styles.typ'], view.sources['project','styles.typ'])
        self.assertTrue(any(x['path']=='styles.typ' and x['reason_code']=='math-import-dependency'
                            for x in report['modern_withheld_sources']))
        self.assertEqual((self.f.project/'styles.typ').read_bytes(), styles)

    def test_consistent_forged_batch_candidate_is_independently_rejected(self):
        _, proof, records = self.capture(b'#highlighted[Plain $a$ end.]\n#highlighted[See @thing $x$ end.]')
        original = composed.highlight.preview_all
        def forge(analyzer, **kwargs):
            diagnostic, result = original(analyzer, **kwargs)
            if kwargs.get('allow_scoped_references'):
                result = deepcopy(result)
                row = result['by_file'][0]
                row['edits'][-1]['replacement'] = row['edits'][-1]['replacement'].replace('See', 'Evil')
                src = analyzer._session._context.source_for(row['path']).source
                edits = tuple(Edit(e['start'], e['end'], e['replacement'].encode()) for e in row['edits'])
                row['candidate_sha256'] = digest(plan(src, edits).candidate())
            return diagnostic, result
        with patch.object(composed.highlight, 'preview_all', side_effect=forge):
            with self.assertRaisesRegex(SafetyError, 'independent source replay'):
                composed.stage(proof, records, reviewed_math=True, composed_references=True)

    def test_consistent_forged_diagnostic_is_independently_rejected(self):
        _, proof, records = self.capture(b'#highlighted[See @thing $x$ end.]')
        original = composed.highlight.preview_all
        def forge(*args, **kwargs):
            diagnostic, result = original(*args, **kwargs)
            diagnostic = deepcopy(diagnostic)
            diagnostic['by_file'][0]['diagnostics'][0]['line'] += 1
            return diagnostic, result
        with patch.object(composed.highlight, 'preview_all', side_effect=forge):
            with self.assertRaisesRegex(SafetyError, 'source diagnostics or suppressions'):
                composed.stage(proof, records, reviewed_math=True, composed_references=True)

    def test_expanded_internal_report_budget_refusal_replays_baseline(self):
        _, proof, records = self.capture(b'#highlighted[Plain $a$ end.]\n#highlighted[See @thing $x$ end.]')
        old, old_report, _ = composed.stage(proof, records, reviewed_math=True)
        original = composed.highlight.preview_all
        def limited(*args, **kwargs):
            if kwargs.get('allow_scoped_references'):
                raise InputError('Modern highlight preview report budget exceeded')
            return original(*args, **kwargs)
        with patch.object(composed.highlight, 'preview_all', side_effect=limited):
            final, report, _ = composed.stage(proof, records, reviewed_math=True, composed_references=True)
        self.assertEqual(final, old)
        self.assertEqual(report['candidate_count'], old_report['candidate_count'])
        self.assertTrue(report['reference_budget_fallback'])

    def test_aggregate_bytes_and_items_budget_fall_back_to_baseline(self):
        _, proof, records = self.capture(b'#highlighted[Plain $a$ end.]\n#highlighted[See @thing $x$ end.]')
        old, old_report, view = composed.stage(proof, records, reviewed_math=True)
        import conditional_modern_highlight as modern
        import document_intermediate_context as intermediate
        analyzer = modern.ModernHighlightAnalyzer(intermediate.issue_binding(view), scoped_callbacks=True)
        diagnostic, proposal = composed.highlight.preview_all(analyzer, return_evidence=True, reviewed_math=True)
        def encoded(value):
            raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
            return len(raw.encode()) + 2 * raw.count('\n')
        base_size = sum(map(encoded, (diagnostic, proposal, old_report)))
        stage_items = (len(old_report['modern_withheld_sources'])
            + sum(1 + sum(1 + len(s['edit_spans']) for s in row['stages']) for row in old_report['sources']))
        base_items = diagnostic['report_items'] + proposal['report_items'] + stage_items
        original = composed.highlight.preview_all
        def bounded(*args, **kwargs):
            kwargs['max_bytes'] = composed.highlight.MAX_PREVIEW_BYTES
            kwargs['max_items'] = composed.highlight.MAX_PREVIEW_ITEMS
            return original(*args, **kwargs)
        for key, limit in (('MAX_PREVIEW_BYTES', base_size + 4096), ('MAX_PREVIEW_ITEMS', base_items + 20)):
            with self.subTest(key=key), patch.object(composed.highlight, key, limit), \
                    patch.object(composed.highlight, 'preview_all', side_effect=bounded):
                final, report, _ = composed.stage(proof, records, reviewed_math=True, composed_references=True)
                self.assertEqual(final, old)
                self.assertEqual(report['candidate_count'], old_report['candidate_count'])
                self.assertEqual(report['reference_budget_fallback_stage'], 'combined-report')

    def test_line_and_directive_costs_are_charged_before_independent_reanalysis(self):
        from syntax_client import parse
        for source in (b'#block[' + b'#paragraph_tab' * 1000 + b']',
                       b'// typst-lint: ignore-next ' + b' ' * 1500 + b'X\n'):
            with self.subTest(size=len(source)), self.assertRaises(routes.RouteBudgetExceeded):
                routes._reanalysis_work(parse(source))

    def test_cli_requires_explicit_composed_reviewed_math(self):
        command = [sys.executable, str(TOOL / 'typst-lint'), 'preview-compile', '--root', str(self.f.project),
                   '--entrypoint', 'main.typ', '--manifest', str(self.f.project.parent/'missing.json'),
                   '--compose-highlight-references']
        for extra in ([], ['--compose-modern-highlights']):
            run = subprocess.run(command + extra, capture_output=True, timeout=20)
            self.assertEqual(run.returncode, 2)
            self.assertIn(b'requires --compose-reviewed-math-highlights', run.stderr)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and sys.platform == 'darwin',
                         'Opt in on macOS with reviewed Typst')
    def test_cli_compiles_reference_without_source_authority(self):
        source = IMPORT + b'#set heading(numbering: "1.")\n= Target <thing>\n#highlighted[See @thing and $x$ end.]'
        _, analyzer, snapshot, _ = self.f.issue(source)
        manifest = self.f.project.parent / 'reference-manifest.json'
        manifest.write_text(json.dumps(dict(roots=dict(snapshot.roots), files=[
            dict(root=x.request.root, path=x.request.path, role=x.request.role) for x in snapshot.files])))
        command = [sys.executable, str(TOOL / 'typst-lint'), 'preview-compile', '--root', str(self.f.project),
                   '--entrypoint', 'main.typ', '--manifest', str(manifest), '--format', 'json',
                   '--compose-modern-highlights', '--compose-reviewed-math-highlights',
                   '--compose-highlight-references']
        run = subprocess.run(command, capture_output=True, timeout=120)
        report = json.loads(run.stdout)
        self.assertEqual(run.returncode, 2, report)
        self.assertTrue(report['compiler_invoked'] and report['pdf_generated'])
        self.assertTrue(report['composed_highlight_references'])
        self.assertEqual(report['conditional_modern_highlight_preview']['candidate_count'], 1)
        self.assertFalse(report['source_apply_gate']['eligible'] or report['publication_authority'])
        self.assertEqual((self.f.project/'main.typ').read_bytes(), source)
