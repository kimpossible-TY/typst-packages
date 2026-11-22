"""Opt-in mannot binding evidence must preserve the parent plan."""
from pathlib import Path
import json
import shutil
import sys
import unittest
from unittest.mock import patch
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_footnote_binding as binding
import conditional_modern_highlight as modern
import document_cli
import namespace_multiroot as multi
import scoped_parent_mannot_evidence as evidence
import scoped_parent_transfer_prototype as parent
import test_conditional_modern_highlight as fixtures
from test_scoped_parent_transfer_prototype import ParentTransferPrototypeTests

@unittest.skipUnless(fixtures.EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class MannotEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.parent = ParentTransferPrototypeTests('runTest')
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.f = self.parent.fixture.fixture

    def issue(self, *, shadow=b'', install=True, appendix=b''):
        body = (b'#import "@preview/mannot:0.4.0": *\n' + shadow
                + b'#local-tag-scope(s => [#mannot-scope(m => [\n'
                b'#let markchild(body) = { set math.equation(numbering: none)\n'
                b'markub(body, tag: (m.tag)("x"), bracket: sym.brace.b) }\n'
                b'$markchild(x)$], parent: s, name: "one")\n'
                b'#highlighted[See #(s.ref)("a") $x$ end.]])') + appendix
        source, _, _ = self.parent.issue(body)
        if install:
            origin = TOOL / 'tests/fixtures/marker-bounds-packages/preview/mannot/0.4.0/src'
            dest = self.f.packages / 'preview/mannot/0.4.0/src'
            for name in ('mark.typ', 'util.typ'):
                shutil.copyfile(origin / name, dest / name)
        capture = multi.load_multiroot_capture(self.f.packages, self.f.cache, capture_mannot_artifacts=True)
        _, context = self.f.session(source)
        analyzer = modern.ModernHighlightAnalyzer(binding.ConditionalFootnoteBinding(context, capture), scoped_callbacks=True)
        diagnostic = analyzer.analyze(context.source_for('main.typ').tree, 'main.typ')['diagnostics'][0]
        return source, analyzer, diagnostic['start']

    def test_binding_replayed_for_candidate_without_changing_parent_plan(self):
        source, analyzer, start = self.issue()
        expected = parent.preview_one(analyzer, 'main.typ', start)
        baseline, result = evidence.preview_with_evidence(analyzer, 'main.typ', start)
        self.assertEqual(baseline, expected)
        self.assertEqual(result['status'], 'CONDITIONAL', result)
        self.assertEqual(result['route'][0]['imported_name'], 'markub')
        self.assertEqual(result['captured_helpers'], 1)
        self.assertEqual(len(result['artifacts']), 2)
        self.assertEqual(result['candidate_sha256'], baseline['candidate_sha256'])
        self.assertFalse(result['runtime_binding_proved'] or result['execution_proved'] or result['render_equivalence_proved'] or result['edit_permission'])
        self.assertEqual((self.f.project / 'main.typ').read_bytes(), source)

    def test_missing_shadow_and_tampering_withhold_only_evidence(self):
        for shadow, install in ((b'', False), (b'#let markub(body) = body\n', True)):
            with self.subTest(shadow=shadow, install=install):
                _, analyzer, start = self.issue(shadow=shadow, install=install)
                expected = parent.preview_one(analyzer, 'main.typ', start)
                baseline, result = evidence.preview_with_evidence(analyzer, 'main.typ', start)
                self.assertEqual(baseline, expected)
                self.assertEqual(result['status'], 'WITHHELD')
        _, analyzer, start = self.issue()
        original = evidence._route
        def tamper(*args, **kwargs):
            route, remaining = original(*args, **kwargs)
            if kwargs.get('graph_override') is not None:
                route[0]['source_sha256'] = '0' * 64
            return route, remaining
        with patch.object(evidence, '_route', side_effect=tamper):
            baseline, result = evidence.preview_with_evidence(analyzer, 'main.typ', start)
            self.assertEqual(result['status'], 'WITHHELD')
            self.assertIn('route changed', result['reason'])
            self.assertEqual(len(baseline['edits']), 1)

    def test_evidence_work_and_byte_budgets_withhold_only_evidence(self):
        _, analyzer, start = self.issue()
        baseline, result = evidence.preview_with_evidence(analyzer, 'main.typ', start)
        self.assertEqual(result['status'], 'CONDITIONAL')
        self.assertEqual(evidence.preview_with_evidence(analyzer, 'main.typ', start, max_work=result['work_used']), (baseline, result))
        for kwargs in ({'max_work': result['work_used'] - 1},
                       {'max_bytes': evidence._report_bytes(result) - 1}):
            actual, refused = evidence.preview_with_evidence(analyzer, 'main.typ', start, **kwargs)
            self.assertEqual(actual, baseline)
            self.assertEqual(refused['status'], 'WITHHELD')
            self.assertIn('budget', refused['reason'])
        with self.assertRaisesRegex(ValueError, 'byte budget'):
            evidence.preview_with_evidence(analyzer, 'main.typ', start, max_bytes=1)
        with patch.object(parent, 'analyze_one', wraps=parent.analyze_one) as analyzed:
            original, refused = evidence.preview_with_evidence(analyzer, 'main.typ', start, max_work=1)
        self.assertEqual(original, baseline)
        self.assertEqual(refused['status'], 'WITHHELD')
        self.assertEqual(analyzed.call_count, 1)  # Only the separate baseline plan ran.

    def test_cli_opt_in_preserves_every_prior_report_and_limits(self):
        source, _, start = self.issue(appendix=b'\n#paragraph_tab\ncompare the values.\n')
        common = dict(root=str(self.f.project), entrypoint='main.typ', rule_set='pde',
                      package_path=str(self.f.packages), footnote_cache_path=str(self.f.cache),
                      inspect_capitalization_review=True,
                      modern_highlight_evidence=True, preview_conditional_modern_combined_highlights=True,
                      preview_conditional_modern_parent_reference=('main.typ', start))
        baseline = document_cli.report_document(**common)
        self.assertEqual([row['starter'] for row in baseline['capitalization_starter_review']['findings']],
                         ['compare'])
        result = document_cli.report_document(**common, attest_parent_mannot=True)
        extra = result.pop('conditional_parent_mannot_binding')
        self.assertEqual(result, baseline)
        self.assertEqual(extra['status'], 'CONDITIONAL')
        self.assertFalse(result['compiler_invoked'] or result['applied_fixes'])
        self.assertEqual((self.f.project / 'main.typ').read_bytes(), source)
        result['conditional_parent_mannot_binding'] = extra
        size = len(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8'))
        with patch.object(document_cli, 'MAX_MODERN_REPORT_BYTES', size):
            self.assertEqual(document_cli.report_document(**common, attest_parent_mannot=True), result)
        with patch.object(document_cli, 'MAX_MODERN_REPORT_BYTES', size - 1):
            with self.assertRaisesRegex(ValueError, 'byte'):
                document_cli.report_document(**common, attest_parent_mannot=True)
        target = self.f.packages / 'preview/mannot/0.4.0/src/mark.typ'
        target.unlink()
        target.symlink_to(self.f.packages / 'preview/mannot/0.4.0/src/util.typ')
        refused = document_cli.report_document(**common, attest_parent_mannot=True)
        self.assertEqual(refused.pop('conditional_parent_mannot_binding')['status'], 'WITHHELD')
        self.assertEqual(refused, baseline)
