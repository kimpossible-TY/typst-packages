"""Opt-in timings describe completed wall-clock spans without changing authority."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_composed_modern_highlight as composed
import document_preview_cli as cli
import document_preview_compile as compiler
import offline_compile_cli
import test_document_composed_reviewed_math as modern_fixture
import test_document_preview_cli as cli_fixture
import test_document_preview_compile as compiler_fixture
from typst_lint import SafetyError


@unittest.skipUnless(cli_fixture.EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class TimingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = cli_fixture.DocumentPreviewCliTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def command(self, *extra):
        return self.fixture.command('--format', 'json', *extra)

    def test_option_off_default_and_failure_report(self):
        for enabled in (False, True):
            output = io.StringIO()
            def fail(*args, **kwargs):
                if enabled:
                    kwargs['phase_timings']['replay_and_intermediate_seconds'] = 1.25
                raise SafetyError('fixture refusal')
            with patch.object(cli.document_preview_compile, 'compile_preview', side_effect=fail), \
                    contextlib.redirect_stdout(output):
                status = cli.main(self.command(*(('--phase-timings',) if enabled else ()))[3:])
            report = json.loads(output.getvalue())
            self.assertEqual(status, 2)
            self.assertEqual(report['error'], 'fixture refusal')
            if not enabled:
                self.assertNotIn('phase_timings', report)
                continue
            timing = report['phase_timings']
            self.assertEqual((timing['clock'], timing['unit'], timing['sample']),
                             ('monotonic', 'seconds', 'single run'))
            self.assertGreaterEqual(timing['planning_seconds'], 0)
            self.assertEqual(timing['replay_and_intermediate_seconds'], 1.25)
            self.assertIsNone(timing['baseline_compile_seconds'])
            self.assertIsNone(timing['candidate_compile_seconds'])
            self.assertNotIn(str(self.fixture.root), json.dumps(timing))
            self.assertNotIn(self.fixture.source.decode(), json.dumps(timing))

    def test_cancellation_does_not_invent_completed_phases(self):
        event = threading.Event()
        event.set()
        coordinator = SimpleNamespace(cancel_event=event, first_signal=15)
        output = io.StringIO()
        with patch.object(offline_compile_cli, '_active_signals', coordinator), \
                patch.object(cli.document_preview_compile, 'compile_preview') as compile_preview, \
                contextlib.redirect_stdout(output):
            cli.main(self.command('--phase-timings')[3:])
        timing = json.loads(output.getvalue())['phase_timings']
        self.assertIsNone(timing['planning_seconds'])
        self.assertIsNone(timing['private_staging_seconds'])
        self.assertIsNone(timing['baseline_compile_seconds'])
        self.assertIsNone(timing['candidate_compile_seconds'])
        compile_preview.assert_not_called()

    def test_early_argument_error_and_regular_pdf_refusal_have_no_timings(self):
        bad_args = self.fixture.command('--phase-timings')
        rejected = subprocess.run(bad_args, capture_output=True, text=True, timeout=20)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn('--phase-timings requires --format json or --pdf-stdout', rejected.stderr)
        self.assertNotIn('phase_timings', rejected.stderr)
        output = self.fixture.root / 'rejected-output.pdf'
        with output.open('wb') as stream:
            refused = subprocess.run(self.command('--phase-timings', '--pdf-stdout'),
                                     stdout=stream, stderr=subprocess.PIPE, timeout=20)
        self.assertEqual(refused.returncode, 2)
        report = json.loads(refused.stderr)
        self.assertIn('PDF stdout must be a pipe or socket', report['error'])
        self.assertNotIn('phase_timings', report)
        self.assertEqual((self.fixture.project / 'main.typ').read_bytes(), self.fixture.source)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with pinned Typst')
    def test_real_cli_reports_completed_nonoverlapping_spans_and_preserves_output(self):
        reports = []
        for flags in ((), ('--phase-timings',)):
            run = subprocess.run(self.command(*flags), capture_output=True, text=True, timeout=90)
            self.assertEqual(run.returncode, 2, run.stderr)
            reports.append(json.loads(run.stdout))
        without, with_timing = reports
        timing = with_timing.pop('phase_timings')
        self.assertEqual(without, with_timing)
        for key in ('planning_seconds', 'replay_and_intermediate_seconds',
                    'private_staging_seconds', 'baseline_compile_seconds', 'candidate_compile_seconds'):
            self.assertIsInstance(timing[key], float)
            self.assertGreaterEqual(timing[key], 0)
        self.assertIsNone(timing['highlight_analysis_seconds'])
        self.assertIsNone(timing['highlight_replay_seconds'])
        spans = sum(value for key, value in timing.items()
                    if key.endswith('_seconds') and key != 'total_seconds' and value is not None)
        self.assertGreaterEqual(timing['total_seconds'], spans)
        self.assertEqual((self.fixture.project / 'main.typ').read_bytes(), self.fixture.source)
        streamed = subprocess.run(self.command('--phase-timings', '--pdf-stdout'), capture_output=True, timeout=90)
        self.assertEqual(streamed.returncode, 2)
        self.assertTrue(streamed.stdout.startswith(b'%PDF'))
        report = json.loads(streamed.stderr)
        self.assertEqual(hashlib.sha256(streamed.stdout).hexdigest(), report['candidate_pdf_sha256'])
        self.assertIsInstance(report['phase_timings']['candidate_compile_seconds'], float)


@unittest.skipUnless(modern_fixture.fixtures.fixtures.fixtures.EXECUTABLE.is_file(),
                     'Requires pinned syntax adapter')
class ComposedTimingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = modern_fixture.ComposedReviewedMathTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_composed_replay_and_independent_highlight_replay_are_distinct(self):
        source = modern_fixture.IMPORT + b'#highlighted[Text $cal(x)$ end.]'
        proof = self.fixture.issue(source)
        timings = {}
        final, result, _ = composed.stage(proof, [], reviewed_math=True, phase_timings=timings)
        other, other_result, _ = composed.stage(proof, [], reviewed_math=True)
        self.assertEqual(final, other)
        self.assertEqual(result, other_result)
        self.assertEqual(set(timings), {'replay_and_intermediate_seconds',
                                       'highlight_analysis_seconds', 'highlight_replay_seconds'})
        self.assertTrue(all(isinstance(value, float) and value >= 0 for value in timings.values()))

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with pinned Typst')
    def test_real_composed_cli_reports_separate_completed_spans(self):
        source = modern_fixture.IMPORT + b'#highlighted[Text $cal(x)$ end.]'
        proof = self.fixture.issue(source)
        snapshot = proof._snapshot
        manifest = self.fixture.fixture.project.parent / 'timing-manifest.json'
        manifest.write_text(json.dumps(dict(roots=dict(snapshot.roots), files=[
            dict(root=item.request.root, path=item.request.path, role=item.request.role)
            for item in snapshot.files])))
        command = [sys.executable, str(TOOL / 'typst-lint'), 'preview-compile', '--root',
                   str(self.fixture.fixture.project), '--entrypoint', 'main.typ', '--manifest',
                   str(manifest), '--format', 'json', '--compose-modern-highlights',
                   '--compose-reviewed-math-highlights']
        reports = []
        for flags in ((), ('--phase-timings',)):
            run = subprocess.run(command + list(flags), capture_output=True, text=True, timeout=90)
            self.assertEqual(run.returncode, 2, run.stderr)
            reports.append(json.loads(run.stdout))
        plain, measured = reports
        timing = measured.pop('phase_timings')
        self.assertEqual(plain, measured)
        self.assertTrue(measured['pdf_generated'])
        for key in ('planning_seconds', 'replay_and_intermediate_seconds', 'highlight_analysis_seconds',
                    'highlight_replay_seconds', 'private_staging_seconds', 'baseline_compile_seconds',
                    'candidate_compile_seconds'):
            self.assertIsInstance(timing[key], float)
            self.assertGreaterEqual(timing[key], 0)
        spans = sum(value for key, value in timing.items()
                    if key.endswith('_seconds') and key != 'total_seconds' and value is not None)
        self.assertGreaterEqual(timing['total_seconds'], spans)
        self.assertEqual((self.fixture.fixture.project / 'main.typ').read_bytes(), source)


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                     and sys.platform == 'darwin' and compiler.SANDBOX.is_file()
                     and cli_fixture.EXECUTABLE.is_file(), 'Requires reviewed macOS compiler')
class CompileFailureTimingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = compiler_fixture.DocumentPreviewCompileTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_compiler_refusal_retains_only_completed_phase(self):
        original = compiler._run_tracked
        for failed in ('baseline', 'candidate'):
            with self.subTest(failed=failed):
                timings = dict(baseline_compile_seconds=None, candidate_compile_seconds=None)
                def fail(*args, **kwargs):
                    if kwargs.get('purpose') == f'document-preview-{failed}':
                        raise SafetyError('fixture compiler refusal')
                    return original(*args, **kwargs)
                with patch.object(compiler, '_run_tracked', side_effect=fail), \
                        self.assertRaisesRegex(SafetyError, 'fixture compiler refusal'):
                    compiler.compile_preview(self.fixture.context, self.fixture.records, self.fixture.catalog,
                                             self.fixture.snapshot, entrypoint='main.typ', phase_timings=timings)
                self.assertIsInstance(timings['private_staging_seconds'], float)
                self.assertIsNone(timings[f'{failed}_compile_seconds'])
                if failed == 'candidate':
                    self.assertIsInstance(timings['baseline_compile_seconds'], float)
                else:
                    self.assertIsNone(timings['candidate_compile_seconds'])


if __name__ == '__main__':
    unittest.main()
