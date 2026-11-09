"""Composition replays intermediate bytes and preserves conditional routes."""
from pathlib import Path
import json
import os
import contextlib
import io
import subprocess
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_composed_modern_highlight as composed
import document_sequence_preview as sequence
import document_footnote_evidence as footnote
import document_preview_compile as compiler
import document_preview_cli
import test_document_intermediate_context as fixtures
from typst_lint import SafetyError, digest


@unittest.skipUnless(fixtures.fixtures.fixtures.EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ComposedModernHighlightTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.IntermediateContextTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_standard_rederives_chain_and_final_bytes(self):
        proof, records, main, child = self.fixture.setup_capture()
        final, report, view = composed.stage(proof, records)
        changed = {row['path']: row for row in report['sources']}
        row = changed['child.typ']
        middle = view.sources['project', 'child.typ']
        self.assertEqual(row['baseline_sha256'], digest(child))
        self.assertEqual(row['intermediate_sha256'], digest(middle))
        self.assertEqual(row['candidate_sha256'], digest(final['project', 'child.typ']))
        self.assertEqual(row['stages'][0]['baseline_sha256'], row['baseline_sha256'])
        self.assertEqual(row['stages'][-1]['candidate_sha256'], row['candidate_sha256'])
        self.assertTrue(all(left['candidate_sha256'] == right['baseline_sha256']
                            for left, right in zip(row['stages'], row['stages'][1:])))
        self.assertTrue(all(stage['edit_spans'] for stage in row['stages']))
        self.assertNotEqual(child, middle)
        self.assertNotEqual(middle, final['project', 'child.typ'])
        self.assertEqual(report['candidate_count'], 1)
        self.assertFalse(report['edit_permission'] or report['compiler_permission']
                         or report['runtime_binding_proved'] or report['render_equivalence_proved'])
        self.assertEqual((self.fixture.project / 'main.typ').read_bytes(), main)
        self.assertEqual((self.fixture.project / 'child.typ').read_bytes(), child)

    def test_forged_record_and_snapshot_mutation_refuse(self):
        proof, records, _, _ = self.fixture.setup_capture()
        bad = [dict(row) for row in records]
        bad[0]['candidate_sha256'] = '0' * 64
        with self.assertRaises(SafetyError):
            composed.stage(proof, bad)
        original = composed.highlight.preview_all
        calls = 0
        def changed(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs)
            calls += 1
            if calls == 2:
                object.__setattr__(proof._snapshot.files[0], 'data', b'changed')
            return result
        with patch.object(composed.highlight, 'preview_all', changed):
            with self.assertRaises(SafetyError):
                composed.stage(proof, records)

    def test_sequence_record_changed_during_highlight_replay_refuses(self):
        proof, records, _, _ = self.fixture.setup_capture()
        original = composed.highlight.preview_all
        calls = 0
        def changed(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs)
            calls += 1
            if calls == 1:
                records[0]['candidate_sha256'] = '0' * 64
            return result
        with patch.object(composed.highlight, 'preview_all', changed):
            with self.assertRaisesRegex(SafetyError, 'records changed'):
                composed.stage(proof, records)

    def test_explicit_withheld_source_cannot_receive_highlight_edits(self):
        proof, _, _, child = self.fixture.setup_capture()
        source = proof._session._context.source_for('child.typ')
        withheld = [sequence.explicit_withheld_record(source)]
        final, report, _ = composed.stage(proof, [], explicit_withheld_sources=('child.typ',),
                                          withheld_records=withheld)
        self.assertEqual(final['project', 'child.typ'], child)
        self.assertEqual(report['candidate_count'], 0)
        self.assertEqual(report['sequence_withheld_source_count'], 1)
        self.assertEqual(report['status'], 'INCOMPLETE')

    def test_all_selected_profiles_replay_before_highlights(self):
        proof, _, _, _ = self.fixture.setup_capture()
        catalog = fixtures.load_rules()[0]['rules']
        for profile in ('standard', 'conditional-gaps', 'conditional-gaps-and-punctuation',
                        'conditional-gaps-and-spacers', 'conditional-gaps-punctuation-and-spacers'):
            with self.subTest(profile=profile):
                records = [record for item in proof._session._context.sources
                           if (record := sequence.preview(item.tree, item.path, catalog, profile=profile,
                               conditional_continuations=True, conditional_adjacent_punctuation=True)) is not None]
                self.assertTrue(all(row['verification'] != 'withheld' for row in records), records)
                _, report, _ = composed.stage(proof, records, profile=profile,
                    conditional_continuations=True, conditional_adjacent_punctuation=True)
                self.assertEqual(report['candidate_count'], 1)

    def test_cross_file_modern_route_change_refuses(self):
        fixture = self.fixture.fixture
        facade = fixtures.fixtures.fixtures.IMPORT + b'#highlighted[Facade $y$ end.]'
        (fixture.project / 'styles.typ').write_bytes(facade)
        source = b'#include "styles.typ"\n#import "styles.typ": *\n#highlighted[Main $x$ end.]'
        _, analyzer, snapshot, _ = fixture.issue(source)
        proof = footnote.PreviewFootnoteEvidence(analyzer._session, snapshot)
        with self.assertRaisesRegex(SafetyError, 'another finding route source'):
            composed.stage(proof, [])

    def test_highlights_before_after_and_between_facade_footnotes_are_mapped(self):
        fixture = self.fixture.fixture
        note = b'#block[$ x #dots_space #footnote[Note] $]'
        other = b'#block[$ y #dots_space #footnote[Second] $]'
        highlight = b'#highlighted[Text $x$ end.]'
        for body, count in ((highlight + b'\n' + note, 1), (note + b'\n' + highlight, 1),
                            (note + b'\n' + highlight + b'\n' + other, 2)):
            with self.subTest(body=body):
                _, analyzer, snapshot, _ = fixture.issue(fixtures.fixtures.fixtures.IMPORT + body)
                proof = footnote.PreviewFootnoteEvidence(analyzer._session, snapshot)
                item = analyzer._session._context.source_for('main.typ')
                profile = 'conditional-gaps-and-spacers'
                record = sequence.preview(item.tree, item.path, fixtures.load_rules()[0]['rules'],
                                          profile=profile, footnote_evidence=proof)
                self.assertTrue(any(step.get('conditional_footnote_calls') for step in record['steps']))
                final, report, _ = composed.stage(proof, [record], profile=profile, sequence_facade_evidence=True)
                self.assertEqual(report['candidate_count'], 1)
                self.assertEqual(len(proof.candidate_calls('main.typ', final['project', 'main.typ']).calls), count)
                self.assertFalse(report['modern_withheld_sources'])

    def test_overlapping_footnote_call_withholds_only_that_modern_source(self):
        fixture = self.fixture.fixture
        source = fixtures.fixtures.fixtures.IMPORT + (b'#highlighted[Text $x$ end.]\n'
                 b'#block[$ x #dots_space #footnote[Note] $]')
        _, analyzer, snapshot, _ = fixture.issue(source)
        proof = footnote.PreviewFootnoteEvidence(analyzer._session, snapshot)
        item = analyzer._session._context.source_for('main.typ')
        profile = 'conditional-gaps-and-spacers'
        record = sequence.preview(item.tree, item.path, fixtures.load_rules()[0]['rules'],
                                  profile=profile, footnote_evidence=proof)
        with patch.object(sequence, '_map_conditional_calls_through_edits',
                          side_effect=sequence.ConditionalFootnoteEditOverlap('fixture overlap')):
            final, report, view = composed.stage(proof, [record], profile=profile, sequence_facade_evidence=True)
        self.assertEqual(final['project', 'main.typ'], view.sources['project', 'main.typ'])
        self.assertEqual(report['candidate_count'], 0)
        self.assertEqual(report['status'], 'INCOMPLETE')
        self.assertEqual(report['modern_withheld_sources'][0]['reason_code'], 'conditional-footnote-overlap')

    def test_highlight_changing_another_footnote_facade_is_withheld(self):
        fixture = self.fixture.fixture
        (fixture.project / 'styles.typ').write_bytes(fixtures.fixtures.fixtures.IMPORT
                                                     + b'#highlighted[Text $x$ end.]')
        source = (b'#include "styles.typ"\n#import "styles.typ": *\n'
                  b'#block[$ x #dots_space #footnote[Note] $]')
        _, analyzer, snapshot, _ = fixture.issue(source)
        proof = footnote.PreviewFootnoteEvidence(analyzer._session, snapshot)
        catalog = fixtures.load_rules()[0]['rules']
        profile = 'conditional-gaps-and-spacers'
        records = [record for item in analyzer._session._context.sources
                   if (record := sequence.preview(item.tree, item.path, catalog, profile=profile,
                                                  footnote_evidence=proof)) is not None]
        self.assertTrue(any(step.get('conditional_footnote_calls') for row in records for step in row['steps']))
        with self.assertRaisesRegex(SafetyError, 'facade changes'):
            composed.stage(proof, records, profile=profile, sequence_facade_evidence=True)

    def test_facade_offsets_follow_continuation_before_other_file_highlight(self):
        fixture = self.fixture.fixture
        (fixture.project / 'styles.typ').write_bytes(fixtures.fixtures.fixtures.IMPORT)
        child = b'#import "@local/text-utils:0.1.2": highlighted\n#highlighted[Text $y$ end.]'
        (fixture.project / 'child.typ').write_bytes(child)
        source = (b'#include "child.typ"\n#import "styles.typ": *\n'
                  b'#block[This solution holds.\n    We continue.]\n'
                  b'#block[$ x #dots_space #footnote[Note] $]')
        _, analyzer, snapshot, _ = fixture.issue(source)
        proof = footnote.PreviewFootnoteEvidence(analyzer._session, snapshot)
        catalog = fixtures.load_rules()[0]['rules']
        profile = 'conditional-gaps-and-spacers'
        records = [record for item in analyzer._session._context.sources
                   if (record := sequence.preview(item.tree, item.path, catalog, profile=profile,
                       footnote_evidence=proof, conditional_continuations=True)) is not None]
        self.assertTrue(any(step.get('conditional_footnote_calls') for row in records for step in row['steps']))
        final, report, _ = composed.stage(proof, records, profile=profile, sequence_facade_evidence=True,
                                           conditional_continuations=True)
        self.assertEqual(report['candidate_count'], 1)
        self.assertNotIn(b'#dots_space', final['project', 'main.typ'])
        self.assertNotEqual(final['project', 'child.typ'], child)

    def test_stage_failure_keeps_explicit_withheld_source_report(self):
        fixture = self.fixture.fixture
        child = fixtures.fixtures.fixtures.IMPORT + b'#highlighted[Text $x$ end.]'
        (fixture.project / 'child.typ').write_bytes(child)
        _, _, snapshot, _ = fixture.issue(b'#include "child.typ"\n')
        manifest = fixture.project.parent / 'combined-manifest.json'
        manifest.write_text(json.dumps(dict(roots=dict(snapshot.roots), files=[
            dict(root=x.request.root, path=x.request.path, role=x.request.role) for x in snapshot.files])))
        output = io.StringIO()
        with patch.object(document_preview_cli.document_preview_compile, 'compile_preview',
                          side_effect=SafetyError('fixture stage failure')), contextlib.redirect_stdout(output):
            document_preview_cli.main(['--root', str(fixture.project), '--entrypoint', 'main.typ',
                '--manifest', str(manifest), '--format', 'json', '--compose-modern-highlights',
                '--withhold-source', 'main.typ'])
        result = json.loads(output.getvalue())
        self.assertEqual(result['error'], 'fixture stage failure')
        self.assertEqual([row['path'] for row in result['withheld_sources']], ['main.typ'])
        self.assertFalse(result['source_apply_gate']['eligible'] or result['pdf_generated'])

    def test_fully_withheld_cli_reports_sources_and_disabled_gate(self):
        proof, _, main, child = self.fixture.setup_capture()
        snapshot = proof._snapshot
        manifest = self.fixture.project.parent / 'combined-manifest.json'
        manifest.write_text(json.dumps(dict(roots=dict(snapshot.roots), files=[
            dict(root=x.request.root, path=x.request.path, role=x.request.role) for x in snapshot.files])))
        run = subprocess.run([sys.executable, str(TOOL / 'typst-lint'), 'preview-compile',
            '--root', str(self.fixture.project), '--entrypoint', 'main.typ', '--manifest', str(manifest),
            '--format', 'json', '--compose-modern-highlights', '--withhold-source', 'main.typ',
            '--withhold-source', 'child.typ'], capture_output=True, text=True, timeout=30)
        result = json.loads(run.stdout)
        self.assertEqual(result['error'], 'No verified composed candidate edits')
        self.assertFalse(result['pdf_generated'] or result['compiler_invoked'])
        self.assertEqual({row['path'] for row in result['withheld_sources']}, {'main.typ', 'child.typ'})
        self.assertFalse(result['source_apply_gate']['eligible'])
        self.assertIn('no_verified_candidate_edits', result['source_apply_gate']['blockers'])
        self.assertEqual(result['conditional_modern_highlight_preview']['candidate_count'], 0)
        self.assertEqual((self.fixture.project / 'main.typ').read_bytes(), main)
        self.assertEqual((self.fixture.project / 'child.typ').read_bytes(), child)

    def test_cli_rejects_conflicting_profile_and_review_options(self):
        project = self.fixture.project
        command = [sys.executable, str(TOOL / 'typst-lint'), 'preview-compile', '--root', str(project),
                   '--entrypoint', 'main.typ', '--manifest', str(project.parent / 'missing.json'),
                   '--format', 'json', '--compose-modern-highlights']
        for option in ('--conditional-modern-highlights', '--review-edits'):
            with self.subTest(option=option):
                run = subprocess.run(command + [option], capture_output=True, timeout=10)
                self.assertEqual(run.returncode, 2)
                self.assertIn(b'error:', run.stderr)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with pinned Typst')
    def test_sequence_only_candidates_can_compile_when_highlight_has_no_findings(self):
        fixture = self.fixture.fixture
        source = fixtures.fixtures.fixtures.IMPORT + b'Text $x_1$.'
        _, _, snapshot, _ = fixture.issue(source)
        manifest = fixture.project.parent / 'combined-manifest.json'
        manifest.write_text(json.dumps(dict(roots=dict(snapshot.roots), files=[
            dict(root=x.request.root, path=x.request.path, role=x.request.role) for x in snapshot.files])))
        run = subprocess.run([sys.executable, str(TOOL / 'typst-lint'), 'preview-compile',
            '--root', str(fixture.project), '--entrypoint', 'main.typ', '--manifest', str(manifest),
            '--format', 'json', '--compose-modern-highlights'], capture_output=True, text=True, timeout=90)
        report = json.loads(run.stdout)
        self.assertTrue(report.get('pdf_generated'), report)
        self.assertGreater(report['sequence_planned_edits'], 0)
        self.assertEqual(report['conditional_modern_highlight_preview']['candidate_count'], 0)
        self.assertEqual((fixture.project / 'main.typ').read_bytes(), source)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with pinned Typst')
    def test_cli_compiles_standard_and_highlight_together(self):
        proof, records, main, child = self.fixture.setup_capture()
        snapshot = proof._snapshot
        manifest = self.fixture.project.parent / 'combined-manifest.json'
        manifest.write_text(json.dumps(dict(roots=dict(snapshot.roots), files=[
            dict(root=x.request.root, path=x.request.path, role=x.request.role) for x in snapshot.files])))
        run = subprocess.run([sys.executable, str(TOOL / 'typst-lint'), 'preview-compile',
            '--root', str(self.fixture.project), '--entrypoint', 'main.typ', '--manifest', str(manifest),
            '--format', 'json', '--compose-modern-highlights'], capture_output=True, text=True, timeout=90)
        self.assertEqual(run.returncode, 2, run.stderr)
        result = json.loads(run.stdout)
        self.assertTrue(result.get('pdf_generated'), result)
        self.assertTrue(result['composed_modern_highlights'])
        self.assertEqual(result['sequence_planned_edits'],
                         sum(len(step['edits']) for row in records for step in row['steps']))
        self.assertGreater(result['sequence_planned_edits'], 0)
        self.assertEqual(result['conditional_modern_highlight_preview']['candidate_count'], 1)
        self.assertFalse(result['source_apply_gate']['eligible'] or result['publication_authority'])
        self.assertEqual((self.fixture.project / 'main.typ').read_bytes(), main)
        self.assertEqual((self.fixture.project / 'child.typ').read_bytes(), child)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with pinned Typst')
    def test_withheld_main_still_previews_child_highlight_without_sequence_edits(self):
        fixture = self.fixture.fixture
        child = fixtures.fixtures.fixtures.IMPORT + b'#highlighted[Text $x$ end.]'
        (fixture.project / 'child.typ').write_bytes(child)
        main = b'#include "child.typ"\n'
        _, analyzer, snapshot, _ = fixture.issue(main)
        manifest = fixture.project.parent / 'combined-manifest.json'
        manifest.write_text(json.dumps(dict(roots=dict(snapshot.roots), files=[
            dict(root=x.request.root, path=x.request.path, role=x.request.role) for x in snapshot.files])))
        run = subprocess.run([sys.executable, str(TOOL / 'typst-lint'), 'preview-compile',
            '--root', str(fixture.project), '--entrypoint', 'main.typ', '--manifest', str(manifest),
            '--format', 'json', '--compose-modern-highlights', '--withhold-source', 'main.typ'],
            capture_output=True, text=True, timeout=90)
        result = json.loads(run.stdout)
        self.assertTrue(result.get('pdf_generated'), result)
        self.assertEqual(result['sequence_planned_edits'], 0)
        self.assertEqual(result['conditional_modern_highlight_preview']['candidate_count'], 1)
        self.assertEqual(result['conditional_modern_highlight_preview']['status'], 'INCOMPLETE')
        self.assertEqual((fixture.project / 'main.typ').read_bytes(), main)
        self.assertEqual((fixture.project / 'child.typ').read_bytes(), child)
