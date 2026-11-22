"""Resource-bound canonical spacer proof for aligned footnote insertion."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from copy import deepcopy
import contextlib
import io
import json
from types import SimpleNamespace
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_aligned_spacer_binding as spacer
import document_composed_aligned_spacer as stage
import document_sequence_preview as sequence
import document_resources
import document_preview_cli
from syntax_client import parse
from typst_lint import load_rules
from typst_lint import SafetyError
from test_document_theme_selector_evidence import ThemeSelectorEvidenceTests


class AlignedSpacerBindingTests(unittest.TestCase):
    def setUp(self):
        self.f = ThemeSelectorEvidenceTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def issue(self, middle=b'', body=None):
        body = body or b'#use(x => [$ A &= B #footnote[Note] $])'
        source = self.f.prefix + middle + body
        proof = self.f.proof(source)
        return source, proof, source.index(b'#footnote')

    def test_facade_binding_replayed_before_and_after(self):
        source, proof, pos = self.issue()
        result = spacer.prove(proof, 'main.typ', source, (pos,))
        self.assertEqual(result['offsets'], [pos])
        candidate = source[:pos] + b'#dots_space ' + source[pos:]
        after = spacer.prove(proof, 'main.typ', candidate, (pos + len(b'#dots_space '),), require_inserted=True)
        self.assertEqual(after['offsets'], [pos + len(b'#dots_space ')])
        self.assertFalse(result['runtime_binding_proved'] or result['execution_proved'] or result['edit_permission'])
        with self.assertRaisesRegex(SafetyError, 'exact math token'):
            spacer.prove(proof, 'main.typ', source, (pos,), require_inserted=True)

    def test_binding_shadow_and_unrecognized_import_refuse(self):
        cases = (dict(middle=b'#let dots_space = []\n'),
                 dict(middle=b'#import "unknown.typ": *\n'),
                 dict(body=b'#use(dots_space => [$ A &= B #footnote[Note] $])'),
                 dict(body=b'#use(x => [#let dots_space = []\n$ A &= B #footnote[Note] $])'),
                 dict(body=b'#use(x => [#let footnote = none\n$ A &= B #footnote[Note] $])'))
        for case in cases:
            with self.subTest(case=case):
                source, proof, pos = self.issue(**case)
                with self.assertRaises(SafetyError):
                    spacer.prove(proof, 'main.typ', source, (pos,))

    def test_named_relative_facade_and_missing_transitive_artifact_refuse(self):
        source = self.f.prefix.replace(b': *', b': dots_space') + b'#use(x => [$ A &= B #footnote[Note] $])'
        proof = self.f.proof(source)
        with self.assertRaises(SafetyError):
            spacer.prove(proof, 'main.typ', source, (source.index(b'#footnote'),))
        source, _, pos = self.issue()
        proof = self.f.proof(source, omit='local/project-references/0.1.0/lib.typ')
        with self.assertRaises(SafetyError):
            spacer.prove(proof, 'main.typ', source, (pos,))

    def test_low_budget_refuses_before_resolver_replay(self):
        source, proof, pos = self.issue()
        with patch.object(spacer.footnotes.PreviewFootnoteEvidence, 'candidate_calls',
                          side_effect=AssertionError('resolver ran before budget')):
            with self.assertRaisesRegex(SafetyError, 'budget'):
                spacer.prove(proof, 'main.typ', source, (pos,), max_work=1)

    def test_offsets_are_bounded_before_resolver_replay(self):
        source, proof, pos = self.issue()
        with patch.object(spacer.footnotes.PreviewFootnoteEvidence, 'candidate_calls',
                          side_effect=AssertionError('resolver ran before offset check')):
            for offsets in (tuple(range(17)), (10 ** 5000,), (True,), (pos, pos)):
                with self.subTest(offsets=type(offsets).__name__), self.assertRaises(SafetyError):
                    spacer.prove(proof, 'main.typ', source, offsets)

    def test_stage_directive_budget_refuses_before_analysis(self):
        source, proof, _ = self.issue(middle=b'// typst-lint: ' + b'x' * 500 + b'\n')
        with patch.object(stage, 'resolve', side_effect=AssertionError('analysis ran before budget')):
            with self.assertRaisesRegex(stage.AlignedSpacerWithheld, 'budget'):
                stage.compose(parse(source), 'main.typ', load_rules()[0]['rules'], proof)

    def test_stage_candidate_budget_refuses_before_suppression_reanalysis(self):
        source, proof, _ = self.issue()
        checks = stage.bounded_scans._within_budget
        def candidate_over_budget(tree):
            return checks(tree) and b'#dots_space #footnote[' not in tree.source
        with patch.object(stage.bounded_scans, '_within_budget', side_effect=candidate_over_budget), \
             patch.object(stage, '_scopes', side_effect=AssertionError('suppression analysis ran')):
            with self.assertRaisesRegex(stage.AlignedSpacerWithheld, 'candidate scan budget'):
                stage.compose(parse(source), 'main.typ', load_rules()[0]['rules'], proof)

    def test_stage_rechecks_exact_diagnostic_and_candidate(self):
        source, proof, pos = self.issue()
        step, parsed = stage.compose(parse(source), 'main.typ', load_rules()[0]['rules'], proof)
        self.assertEqual(step['edits'][0]['start'], pos)
        self.assertEqual(step['conditional_aligned_spacer_calls'], [pos + len(b'#dots_space ')])
        self.assertEqual(parsed.source, source[:pos] + b'#dots_space ' + source[pos:])
        real = stage.independent.preview
        def tampered(*args, **kwargs):
            value = real(*args, **kwargs)
            value['edits'][0]['start'] += 1
            return value
        with patch.object(stage.independent, 'preview', side_effect=tampered):
            with self.assertRaises(SafetyError):
                stage.compose(parse(source), 'main.typ', load_rules()[0]['rules'], proof)

    def test_sequence_and_resource_replay_reject_tampering(self):
        source, proof, pos = self.issue()
        catalog = load_rules()[0]['rules']
        ctx = proof._session._context
        options = dict(profile='conditional-gaps-and-spacers', footnote_evidence=proof,
                       aligned_spacer_target='main.typ')
        self.assertIsNone(sequence.preview(parse(source), 'main.typ', catalog,
            profile=options['profile'], footnote_evidence=proof))
        record = sequence.preview(parse(source), 'main.typ', catalog, **options)
        self.assertEqual([step['rule_id'] for step in record['steps']], ['pde.aligned-footnote-spacer'])
        self.assertFalse(record['runtime_binding_proved'] or record['edit_permission'])
        mapped = sequence.replay_document(ctx, [record], catalog, **options)
        self.assertEqual(mapped['main.typ'], source[:pos] + b'#dots_space ' + source[pos:])
        staged = document_resources.stage_document(ctx, [record], catalog, proof._snapshot, **options)
        self.assertEqual(staged['project', 'main.typ'], mapped['main.typ'])
        for change in ('replacement', 'call', 'hash', 'permission'):
            forged = deepcopy(record)
            step = forged['steps'][0]
            if change == 'replacement':
                step['edits'][0]['replacement'] = '#dots_space  '
            elif change == 'call':
                step['conditional_aligned_spacer_calls'][0] += 1
            elif change == 'hash':
                step['candidate_sha256'] = '0' * 64
            else:
                step['edit_permission'] = True
            with self.subTest(change=change), self.assertRaises(SafetyError):
                sequence.replay_document(ctx, [forged], catalog, **options)
        with self.assertRaises(SafetyError):
            sequence.replay_document(ctx, [record], catalog, profile=options['profile'], footnote_evidence=proof)
        bad_candidate = mapped['main.typ'].replace(b'#dots_space ', b'#space_fake ', 1)
        with patch.object(sequence, '_replay_expected', return_value=bad_candidate):
            with self.assertRaises(SafetyError):
                sequence.replay_document(ctx, [record], catalog, **options)
        changed_facade = proof._session._graph.source_for('styles.typ').source + b'\n#let dots_space = []\n'
        with self.assertRaises(SafetyError):
            proof.validate_staged_facades({'main.typ': mapped['main.typ'], 'styles.typ': changed_facade},
                                          {'main.typ': (pos + len(b'#dots_space '),)})

    def test_unproven_spacer_keeps_prior_sequence(self):
        body = b'#use(x => [$ A &= B #footnote[Note] $])\n$ z_#value $'
        source, proof, _ = self.issue(middle=b'#let dots_space = []\n', body=body)
        catalog = load_rules()[0]['rules']
        kwargs = dict(profile='conditional-gaps-and-spacers', footnote_evidence=proof)
        old = sequence.preview(parse(source), 'main.typ', catalog, **kwargs)
        current = sequence.preview(parse(source), 'main.typ', catalog, aligned_spacer_target='main.typ', **kwargs)
        self.assertTrue(old['steps'])
        self.assertEqual(current['steps'], old['steps'])
        self.assertEqual(current['candidate_sha256'], old['candidate_sha256'])
        self.assertIn('binding', current['aligned_spacer_withheld'])

    def test_aligned_and_prose_marker_options_replay_together(self):
        body = b'#use(x => [$ A &= B #footnote[Note] $])\n\nFirst idea.\n\nSecond idea.'
        source, proof, _ = self.issue(body=body)
        catalog = load_rules()[0]['rules']
        options = dict(profile='conditional-gaps-and-spacers', footnote_evidence=proof,
                       aligned_spacer_target='main.typ', conditional_prose_markers=True)
        record = sequence.preview(parse(source), 'main.typ', catalog, **options)
        self.assertEqual([step['rule_id'] for step in record['steps']],
                         ['pde.aligned-footnote-spacer', 'pde.prose-paragraph-marker'])
        final = sequence.replay_document(proof._session._context, [record], catalog, **options)['main.typ']
        self.assertIn(b'#dots_space #footnote[Note]', final)
        self.assertIn(b'#paragraph_tab\nSecond idea.', final)

    def test_other_source_records_remain_exactly_unchanged(self):
        (self.f.project / 'other.typ').write_bytes(b'$ z_#value $')
        body = b'#include "other.typ"\n#use(x => [$ A &= B #footnote[Note] $])'
        _, proof, _ = self.issue(body=body)
        context, catalog = proof._session._context, load_rules()[0]['rules']
        options = dict(profile='conditional-gaps-and-spacers', footnote_evidence=proof)
        original = {item.path: sequence.preview(item.tree, item.path, catalog, **options)
                    for item in context.sources}
        selected = {item.path: sequence.preview(item.tree, item.path, catalog,
                    aligned_spacer_target='main.typ', **options) for item in context.sources}
        self.assertEqual(selected['other.typ'], original['other.typ'])
        records = [row for row in selected.values() if row is not None]
        final = sequence.replay_document(context, records, catalog,
                                         aligned_spacer_target='main.typ', **options)
        self.assertIn(b'#dots_space #footnote', final['main.typ'])

    def test_injected_wrapper_cannot_forge_shadowed_binding(self):
        source, proof, _ = self.issue(middle=b'#let dots_space = []\n')
        def fake(_evidence, path, data, offsets, **kwargs):
            from typst_lint import digest
            return dict(path=path, source_sha256=digest(data), offsets=list(offsets),
                        runtime_binding_proved=False, execution_proved=False,
                        render_equivalence_proved=False, edit_permission=False, compiler_permission=False)
        with patch.object(stage.binding, 'prove', side_effect=fake):
            with self.assertRaises(stage.AlignedSpacerWithheld):
                stage.compose(parse(source), 'main.typ', load_rules()[0]['rules'], proof)

    def test_cli_passes_explicit_target_to_revalidated_staging(self):
        source, proof, pos = self.issue()
        snapshot = proof._snapshot
        manifest = self.f.project.parent / 'manifest.json'
        manifest.write_text(json.dumps(dict(roots=dict(snapshot.roots), files=[dict(
            root=item.request.root, path=item.request.path, role=item.request.role) for item in snapshot.files])))
        captured = []
        def fake_compile(context, records, catalog, issued, **kwargs):
            self.assertEqual(kwargs['aligned_spacer_target'], 'main.typ')
            staged = document_resources.stage_document(context, records, catalog, issued,
                profile=kwargs['profile'], footnote_evidence=kwargs['footnote_evidence'],
                aligned_spacer_target=kwargs['aligned_spacer_target'])
            captured.append(staged['project', 'main.typ'])
            return SimpleNamespace(baseline_sha256='a'*64, candidate_sha256='b'*64,
                pdfs_identical=False, dependency_count=0, unobserved_capture_count=0,
                system_font_files=0, compiler_sha256='c'*64, modern_highlight_evidence=None)
        args = ['--root', str(self.f.project), '--entrypoint', 'main.typ', '--manifest', str(manifest),
                '--conditional-gaps', '--conditional-spacers', '--conditional-facade-footnotes',
                '--conditional-aligned-spacer', 'main.typ', '--format', 'json']
        output = io.StringIO()
        with patch.object(document_preview_cli.document_preview_compile, 'compile_preview', side_effect=fake_compile), \
             contextlib.redirect_stdout(output):
            self.assertEqual(document_preview_cli.main(args), 2)
        report = json.loads(output.getvalue())
        self.assertEqual(report['conditional_aligned_spacer_preview']['planned_edits'], 1)
        self.assertFalse(report['source_apply_gate']['eligible'])
        self.assertEqual(captured, [source[:pos] + b'#dots_space ' + source[pos:]])
        self.assertEqual((self.f.project / 'main.typ').read_bytes(), source)

    def test_modern_combination_is_rejected_before_capture_or_compilation(self):
        args = ['--root', str(self.f.project), '--entrypoint', 'main.typ',
                '--manifest', str(self.f.project.parent / 'unused.json'), '--conditional-gaps',
                '--conditional-spacers', '--conditional-facade-footnotes',
                '--conditional-aligned-spacer', 'main.typ']
        for modern in ('--compose-modern-highlights', '--conditional-modern-highlights'):
            with self.subTest(modern=modern), patch.object(document_preview_cli, 'load_manifest',
                    side_effect=AssertionError('capture ran')), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    document_preview_cli.main(args + [modern])
        for modern_key in ('composed_modern_evidence', 'modern_highlight_evidence'):
            with self.subTest(modern_key=modern_key), self.assertRaisesRegex(SafetyError, 'modern highlights'):
                document_preview_cli.document_preview_compile.compile_preview(None, None, None, None,
                    entrypoint='main.typ', aligned_spacer_target='main.typ', **{modern_key: object()})
