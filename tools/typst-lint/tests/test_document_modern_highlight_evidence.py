"""Modern helper provenance is conditional and tied to captured resources."""
from dataclasses import replace
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_modern_highlight as modern
import document_modern_highlight_evidence as binding
import document_modern_highlight_preview as preview
import document_resources
from edit_plan import Edit, plan
import test_conditional_modern_highlight as fixtures
from typst_lint import InputError, SafetyError


@unittest.skipUnless(fixtures.EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class CapturedModernHighlightTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ConditionalModernHighlightTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project, self.packages, self.cache = self.fixture.project, self.fixture.packages, self.fixture.cache

    def issue(self, source=None, *, omitted=(), extra=(), scoped_callbacks=True):
        source = source or fixtures.IMPORT + b'#highlighted[Text $x$ end.]'
        session, context = self.fixture.session(source)
        analyzer = modern.ModernHighlightAnalyzer(session, scoped_callbacks=scoped_callbacks)
        roots = dict(project=str(self.project), packages=str(self.packages), cache=str(self.cache))
        index = next(x.root_index for x in session._namespaces.selections
                     if x.import_path == '@local/text-utils:0.1.2')
        root = ('packages', 'cache')[index]
        requests = [('project', x.path, 'source') for x in session._graph.sources]
        requests += [(root, 'local/text-utils/0.1.2/' + x, 'package') for x in ('typst.toml', 'lib.typ')]
        requests += list(extra)
        snapshot = document_resources.capture(roots, [document_resources.ResourceRequest(*x)
                                                      for x in requests if x[1] not in omitted])
        proof = binding.CapturedModernHighlightEvidence(analyzer, snapshot)
        return proof, analyzer, snapshot, document_resources.stage_original(context, snapshot)

    def test_issued_evidence_binds_route_and_preserves_conditions(self):
        proof, _, _, staged = self.issue()
        report = proof.analyze(staged)
        row, = report['by_file']
        finding, = row['diagnostics']
        self.assertTrue(report['resource_capture_bound'])
        self.assertTrue(finding['route'] and finding['conditions'])
        for name in ('runtime_binding_proved', 'execution_proved', 'coverage_complete',
                     'edit_permission', 'compiler_permission', 'render_equivalence_proved'):
            self.assertFalse(report[name])
        self.assertEqual((self.project / 'main.typ').read_bytes(), fixtures.IMPORT + b'#highlighted[Text $x$ end.]')

    def test_final_project_and_package_changes_or_omissions_refuse(self):
        proof, _, _, staged = self.issue()
        for key in (('project', 'main.typ'), ('packages', 'local/text-utils/0.1.2/lib.typ')):
            changed = dict(staged); changed[key] += b'\n'
            with self.subTest(key=key), self.assertRaises(SafetyError):
                proof.analyze(changed)
        missing = dict(staged); missing.pop(('project', 'main.typ'))
        with self.assertRaises(SafetyError):
            proof.analyze(missing)
        with self.assertRaises(InputError):
            proof.analyze(None)

    def test_missing_and_fabricated_snapshots_and_mutated_options_refuse(self):
        for name in ('local/text-utils/0.1.2/typst.toml', 'local/text-utils/0.1.2/lib.typ'):
            with self.subTest(name=name), self.assertRaises(SafetyError):
                self.issue(omitted=(name,))
        proof, analyzer, snapshot, staged = self.issue()
        with self.assertRaises(SafetyError):
            binding.CapturedModernHighlightEvidence(analyzer, replace(snapshot))
        analyzer._scoped_callbacks = False
        with self.assertRaises(InputError):
            proof.analyze(staged)

    def test_selected_cache_shadowed_by_higher_priority_is_rejected(self):
        cached = self.cache / 'local/text-utils/0.1.2'
        cached.parent.mkdir(parents=True)
        shutil.move(str(self.fixture.target), str(cached))
        source = fixtures.IMPORT + b'#highlighted[Text $x$ end.]'
        session, _ = self.fixture.session(source)
        analyzer = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)
        shadow = self.packages / 'Local/Text-Utils/0.1.2/typst.toml'
        shadow.parent.mkdir(parents=True); shadow.write_text('unreviewed')
        roots = dict(project=str(self.project), packages=str(self.packages), cache=str(self.cache))
        paths = [('project', 'main.typ', 'source'), ('packages', 'Local/Text-Utils/0.1.2/typst.toml', 'package'),
                 ('cache', 'local/text-utils/0.1.2/typst.toml', 'package'),
                 ('cache', 'local/text-utils/0.1.2/lib.typ', 'package')]
        snapshot = document_resources.capture(roots, [document_resources.ResourceRequest(*x) for x in paths])
        with self.assertRaisesRegex(SafetyError, 'higher-priority'):
            binding.CapturedModernHighlightEvidence(analyzer, snapshot)

    def test_forged_route_and_output_budget_refuse(self):
        proof, analyzer, _, staged = self.issue()
        original = modern.ModernHighlightAnalyzer.analyze_all
        def forged(instance, **kwargs):
            result = original(instance, **kwargs)
            result['by_file'][0]['diagnostics'][0]['route'][0]['source_sha256'] = '0' * 64
            return result
        with patch.object(modern.ModernHighlightAnalyzer, 'analyze_all', forged):
            with self.assertRaises(SafetyError):
                proof.analyze(staged)
        with self.assertRaises(InputError):
            proof.analyze(staged, max_items=1)
        with self.assertRaises(InputError):
            proof.analyze(staged, max_bytes=10)

    def test_facade_source_is_required_and_cannot_change_in_final_map(self):
        facade = b'#import "@local/text-utils:0.1.2": *\n'
        (self.project / 'styles.typ').write_bytes(facade)
        source = b'#import "styles.typ": *\n#highlighted[Text $x$ end.]'
        proof, _, _, staged = self.issue(source)
        finding, = proof.analyze(staged)['by_file'][0]['diagnostics']
        self.assertTrue(any(item['path'] == 'styles.typ' for item in finding['source_hashes']))
        altered = dict(staged); altered['project', 'styles.typ'] += b'\n'
        with self.assertRaises(SafetyError):
            proof.analyze(altered)
        with self.assertRaises(SafetyError):
            self.issue(source, omitted=('styles.typ',))

    def test_source_shadow_and_mutation_during_analysis_are_not_promoted(self):
        source = fixtures.IMPORT + b'#let highlighted = none\n#highlighted[Text $x$ end.]'
        proof, _, _, staged = self.issue(source)
        self.assertFalse(proof.analyze(staged)['by_file'][0]['diagnostics'])
        proof, _, snapshot, staged = self.issue()
        original = modern.ModernHighlightAnalyzer.analyze_all
        def changed(instance, **kwargs):
            result = original(instance, **kwargs)
            object.__setattr__(snapshot.files[0], 'data', b'altered')
            return result
        with patch.object(modern.ModernHighlightAnalyzer, 'analyze_all', changed):
            with self.assertRaises(SafetyError):
                proof.analyze(staged)

    def candidate_map(self, analyzer, staged):
        result = preview.preview_all(analyzer)
        candidate = dict(staged)
        for row in result['by_file']:
            if row['verification'] == 'withheld':
                continue
            key = ('project', row['path'])
            edits = [Edit(x['start'], x['end'], x['replacement'].encode()) for x in row['edits']]
            candidate[key] = plan(staged[key], edits).candidate()
        return candidate

    def test_exact_rederived_candidate_only_and_no_authority(self):
        proof, analyzer, _, staged = self.issue()
        candidate = self.candidate_map(analyzer, staged)
        result = proof.analyze_candidates(candidate)
        self.assertEqual((result['source_count'], result['candidate_count']), (1, 1))
        self.assertTrue(result['resource_capture_bound'])
        for name in ('runtime_binding_proved', 'execution_proved', 'coverage_complete',
                     'render_equivalence_proved', 'edit_permission', 'compiler_permission'):
            self.assertFalse(result[name])
        changed = dict(candidate); changed['project', 'main.typ'] += b'\n'
        with self.assertRaises(SafetyError):
            proof.analyze_candidates(changed)
        changed = dict(candidate); changed['packages', 'local/text-utils/0.1.2/lib.typ'] += b'\n'
        with self.assertRaises(SafetyError):
            proof.analyze_candidates(changed)
        added = dict(candidate); added['project', 'extra.typ'] = b''
        with self.assertRaises(SafetyError):
            proof.analyze_candidates(added)
        with self.assertRaises(InputError):
            proof.analyze_candidates(candidate, max_items=1)
        self.assertEqual((self.project / 'main.typ').read_bytes(), fixtures.IMPORT + b'#highlighted[Text $x$ end.]')

    def test_cross_source_route_changes_refuse_combined_candidates(self):
        facade = fixtures.IMPORT + b'#highlighted[Facade $y$ end.]'
        (self.project / 'styles.typ').write_bytes(facade)
        source = b'#include "styles.typ"\n#import "styles.typ": *\n#highlighted[Main $x$ end.]'
        proof, analyzer, _, staged = self.issue(source)
        candidate = self.candidate_map(analyzer, staged)
        self.assertNotEqual(candidate['project', 'main.typ'], staged['project', 'main.typ'])
        self.assertNotEqual(candidate['project', 'styles.typ'], staged['project', 'styles.typ'])
        with self.assertRaisesRegex(SafetyError, 'route source changed'):
            proof.analyze_candidates(candidate)
        candidate['project', 'styles.typ'] = staged['project', 'styles.typ']
        result = proof.analyze_candidates(candidate)
        self.assertEqual((result['source_count'], result['candidate_count']), (1, 1))

    def test_unselected_and_unsupported_diagnostics_remain_incomplete(self):
        source = fixtures.IMPORT + (b'#highlighted[Bad $x_1$ end.]\n'
                                    b'#highlighted[Good $x$ end.]')
        proof, analyzer, _, staged = self.issue(source)
        candidate = self.candidate_map(analyzer, staged)
        result = proof.analyze_candidates(candidate)
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertEqual((result['candidate_count'], result['unpreviewed_diagnostics']), (1, 1))
        untouched = proof.analyze_candidates(staged)
        self.assertEqual((untouched['status'], untouched['candidate_count'],
                          untouched['unpreviewed_diagnostics']), ('INCOMPLETE', 0, 2))


if __name__ == '__main__':
    unittest.main()
