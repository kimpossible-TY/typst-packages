"""Executable-only signal semantics and fresh-process recovery boundaries.

Signals target only Popen children created by this test. Handshake files keep
startup/cleanup scheduling independent from the fixed production 10s deadline.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_offline_compile_integration import (
    _CompileFixtures, EXECUTABLE, WORKER, ORIGINAL, EXPECTED, OLD_OUTPUT,
)


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX signal/recovery protocol')
class OfflineCompileSignalTests(_CompileFixtures):
    def spawn(self, f=None, **options):
        f = f or self.f
        child = subprocess.Popen([sys.executable, str(WORKER),
            json.dumps(self.request(f, **options))], cwd=f.base,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def cleanup():
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)
        self.addCleanup(cleanup)
        return child

    def wait_file(self, child, path):
        until = time.monotonic() + 90
        while not Path(path).exists():
            if child.poll() is not None:
                out, err = child.communicate(timeout=5)
                self.fail(f'Owned worker exited before handshake: rc={child.returncode}; {out!r}; {err!r}')
            if time.monotonic() > until:
                self.fail('Owned worker failed to reach bounded handshake')
            time.sleep(.01)
        # The compiler marker contains its own child PID. Wait for a complete write.
        while not Path(path).read_bytes():
            self.assertLess(time.monotonic(), until)
            time.sleep(.005)
        return Path(path).read_text()

    def wait_boundary(self, child, f=None, index=0):
        f = f or self.f
        return json.loads(self.wait_file(child, f.ready + str(index)))

    def release(self, f=None, index=0):
        Path((f or self.f).release + str(index)).touch()

    def finish_json(self, child, *, expected):
        out, err = child.communicate(timeout=90)
        self.assertEqual(err, b'', err.decode(errors='replace'))
        self.assertEqual(child.returncode, expected, (out, err))
        self.assertTrue(out.isascii(), out)
        report = json.loads(out)
        self.assertEqual(report['status'], expected, report)
        if expected < 0:
            self.assertEqual(report['termination_signal'], -expected)
        return report

    def assert_child_reaped(self, pid):
        # This PID came only from the owned fake compiler's marker, never a name scan.
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_natural_child_signals_have_true_negative_process_returncodes(self):
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
            with self.subTest(number=number):
                f = self.fixture()
                before = self.file_state(f.pdf)
                report = self.run_json(f, compiler_mode=f'signal-{int(number)}')
                self.assertEqual(report['status'], -number, report)
                self.assertEqual(report['compiler_status'], -number)
                self.assertEqual(report['compile_report']['compiler_failure']['kind'], 'process_exit')
                self.assertEqual(report['compile_report']['safety_gate'], 'passed')
                self.assertEqual(self.file_state(f.pdf), before)
                self.assertEqual(f.source.read_bytes(), EXPECTED)
                self.assertFalse(report['output_recovery']['state'] == 'required')
                self.assert_child_reaped(int(f.compiler_marker.read_text()))
                self.assert_lock_free(f)

    def test_natural_signal_text_and_ignored_blocked_parent_handler(self):
        result = self.run_process(argv=self.argv(format='text'),
            compiler_mode=f'signal-{int(signal.SIGTERM)}', ignore_block_signal=int(signal.SIGTERM))
        self.assertEqual(result.returncode, -signal.SIGTERM, result.stderr)
        self.assertEqual(result.stderr, b'')
        self.assertIn(b'compiler invoked', result.stdout)
        self.assertIn(b'untouched', result.stdout)
        self.assertIn(b'15', result.stdout)
        self.assert_lock_free()

    def test_main_callable_never_self_signals_or_changes_handlers(self):
        destination = self.f.base / 'callable result.json'
        result = self.run_process(callable=True, callable_result=str(destination),
            compiler_mode=f'signal-{int(signal.SIGTERM)}')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b'')
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], -signal.SIGTERM)
        self.assertEqual(json.loads(destination.read_text()),
            {'status': -signal.SIGTERM, 'handlers_unchanged': True})
        self.assert_lock_free()

    def test_sigterm_before_validation_or_compile_flushes_json_and_releases_locks(self):
        for point, committed in (('after_prepared', False), ('before_compile', True)):
            with self.subTest(point=point):
                f = self.fixture()
                boundary = ['output', point, f.output]
                child = self.spawn(f, boundaries=[boundary])
                self.assertEqual(self.wait_boundary(child, f), boundary)
                child.send_signal(signal.SIGTERM)
                self.release(f)
                report = self.finish_json(child, expected=-signal.SIGTERM)
                self.assertEqual(report['received_signals'][0], signal.SIGTERM)
                self.assertIs(report['compiler_invoked'], False, report)
                self.assertIsNone(report['compiler_status'])
                self.assertEqual(f.source.read_bytes(), EXPECTED if committed else ORIGINAL)
                self.assertEqual(f.pdf.read_bytes(), OLD_OUTPUT)
                self.assertEqual(report['output_recovery']['state'], 'not_required')
                self.assert_lock_free(f)

    def test_received_sigint_during_compiler_retains_original_signal_not_cleanup_sigkill(self):
        child = self.spawn(compiler_mode='hang')
        pid = int(self.wait_file(child, self.f.compiler_marker))
        child.send_signal(signal.SIGINT)
        report = self.finish_json(child, expected=-signal.SIGINT)
        self.assertEqual(report['compiler_status'], -signal.SIGKILL)
        self.assertEqual(report['compile_report']['compiler_failure']['kind'], 'cancelled')
        self.assertEqual(report['received_signals'][0], signal.SIGINT)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.assert_child_reaped(pid)
        self.assert_lock_free()

    def test_received_signal_with_compiler_or_context_cleanup_failure_stays_gate_three(self):
        for failure in ('cleanup_failure', 'source_exit_failure'):
            with self.subTest(failure=failure):
                f = self.fixture()
                child = self.spawn(f, compiler_mode='hang', **{failure: True})
                pid = int(self.wait_file(child, f.compiler_marker))
                child.send_signal(signal.SIGTERM)
                report = self.finish_json(child, expected=3)
                self.assertEqual(report['received_signals'][0], signal.SIGTERM)
                self.assertNotIn('termination_signal', report)
                self.assertEqual(report['compiler_status'], -signal.SIGKILL)
                self.assertEqual(f.source.read_bytes(), EXPECTED)
                self.assertEqual(f.pdf.read_bytes(), OLD_OUTPUT)
                self.assert_child_reaped(pid)
                self.assert_lock_free(f)

    def test_received_signal_with_later_output_conflict_preserves_edit_and_gate_three(self):
        boundary = ['output', 'after_replaced', self.f.output]
        child = self.spawn(boundaries=[boundary])
        self.wait_boundary(child)
        self.f.pdf.write_bytes(b'Later user PDF edit during interruption.\x00\xff')
        before = self.file_state(self.f.pdf)
        child.send_signal(signal.SIGTERM)
        self.release()
        report = self.finish_json(child, expected=3)
        self.assertEqual(report['received_signals'][0], signal.SIGTERM)
        self.assertNotIn('termination_signal', report)
        self.assertEqual(report['output_recovery']['state'], 'required')
        self.assertEqual(self.file_state(self.f.pdf), before)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assert_lock_free()

    def test_sigterm_during_source_application_is_deferred_until_valid_commit(self):
        boundary = ['source', 'after_replace', self.f.target]
        child = self.spawn(boundaries=[boundary])
        self.assertEqual(self.wait_boundary(child), boundary)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        child.send_signal(signal.SIGTERM)
        self.release()
        report = self.finish_json(child, expected=-signal.SIGTERM)
        self.assertIs(report['compiler_invoked'], False)
        self.assertEqual(report['source_commit'], 'committed')
        self.assertEqual(report['source_recovery']['state'], 'not_required')
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.assertFalse(self.f.compiler_marker.exists())
        self.assert_lock_free()

    def test_signal_after_pdf_replacement_rolls_back_then_preserves_original_signal(self):
        boundary = ['output', 'after_replaced', self.f.output]
        child = self.spawn(boundaries=[boundary])
        self.assertEqual(self.wait_boundary(child), boundary)
        self.assertTrue(self.f.pdf.read_bytes().startswith(b'%PDF-1.7'))
        child.send_signal(signal.SIGINT)
        self.release()
        report = self.finish_json(child, expected=-signal.SIGINT)
        self.assertEqual(report['compiler_status'], 0)
        self.assertEqual(report['publication_report']['output_disposition'], 'restored')
        self.assertFalse(report['publication_report']['recovery_required'])
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assert_lock_free()

    def test_repeated_signals_during_output_cleanup_do_not_interrupt_rollback(self):
        boundaries = [['output', 'after_replaced', self.f.output],
                      ['output', 'before_restore', self.f.output]]
        child = self.spawn(boundaries=boundaries)
        self.wait_boundary(child)
        child.send_signal(signal.SIGTERM)
        self.release()
        self.assertEqual(self.wait_boundary(child, index=1), boundaries[1])
        child.send_signal(signal.SIGINT)
        child.send_signal(signal.SIGTERM)
        self.release(index=1)
        report = self.finish_json(child, expected=-signal.SIGTERM)
        self.assertEqual(report['received_signals'][0], signal.SIGTERM)
        self.assertIn(signal.SIGINT, report['received_signals'])
        self.assertEqual(report['publication_report']['output_disposition'], 'restored')
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assert_lock_free()

    def test_interrupted_source_apply_never_claims_not_applied_means_unchanged(self):
        report = self.run_json(interrupt_boundary=['source', 'after_replace', self.f.target])
        self.assertEqual(report['status'], 3, report)
        self.assertEqual(report['source_commit'], 'unknown')
        self.assertEqual(report['source_recovery']['state'], 'required')
        self.assertTrue(report['source_recovery']['checked'])
        self.assertEqual(len(report['source_recovery']['pending']), 1)
        self.assertIs(report['compiler_invoked'], False)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.assertEqual(report['publication_report']['source_commit'], 'not_applied')
        self.assertNotEqual(report.get('source_outcome'), 'unchanged')
        self.assert_lock_free()

    def crash_at(self, f=None, *, domain='output', point='after_prepared'):
        f = f or self.f
        path = f.target if domain == 'source' else f.output
        child = self.spawn(f, boundaries=[[domain, point, path]])
        self.wait_boundary(child, f)
        child.kill()  # Only this owned worker. No unknown PID or process-name action.
        out, err = child.communicate(timeout=10)
        self.assertEqual(child.returncode, -signal.SIGKILL, err)
        self.assertEqual(out, b'')
        self.assertEqual(err, b'')
        self.assert_lock_free(f)
        return self.journals(f)[0].parent.name

    def test_pending_pdf_blocks_before_validate_and_preserves_original_source(self):
        journal = self.crash_at()
        report = self.run_json(forbid_validate=True)
        self.assertEqual(report['status'], 3, report)
        self.assertIs(report['compiler_invoked'], False)
        self.assertEqual(self.f.source.read_bytes(), ORIGINAL)
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.assertIn(journal, report['output_inventory']['pending'])
        self.assert_lock_free()

    def test_both_pending_stores_source_blocks_without_claiming_pdf_scanned(self):
        output_id = self.crash_at(domain='source', point='after_replace')
        source_id = self.journals(source=True)[0].parent.name
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        report = self.run_json(forbid_validate=True, forbid_output_scan=True)
        self.assertEqual(report['status'], 3, report)
        self.assertEqual(report['source_recovery']['state'], 'required')
        self.assertEqual(report['output_recovery']['state'], 'not_checked')
        self.assertNotIn('output_inventory', report)
        self.assertIn(source_id, report['source_recovery']['pending'])
        # Output-only recovery can complete without resolving the separate source.
        self.f.config.unlink()
        recovered = self.run_json(argv=self.recover_argv(output_id), fake_compiler=False, forbid_source=True)
        self.assertEqual(recovered['status'], 0, recovered)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.assertEqual(recovered['source_recovery']['state'], 'not_checked')
        source_events = [json.loads(p.read_bytes())['kind'] for p in sorted(self.journals(source=True)[0].parent.glob('event-*.json'))]
        self.assertNotIn('committed', source_events)
        self.assertNotIn('recovered', source_events)
        self.assert_lock_free()

    def test_crash_before_compile_has_unknown_historical_launch_and_independent_recovery(self):
        journal = self.crash_at(point='before_compile')
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.f.source.unlink()
        self.f.config.unlink()
        report = self.run_json(argv=self.recover_argv(journal), fake_compiler=False, forbid_source=True)
        self.assertEqual(report['status'], 0, report)
        pub = report['publication_report']
        self.assertIsNone(pub['compiler_invoked'])
        self.assertIsNone(pub['compiler_status'])
        self.assertEqual(pub['attempted_invocations'], [])
        self.assertFalse(pub['invocation_details_available'])
        self.assertIn('does not mean', pub['invocation_details_note'])
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.assert_lock_free()

    def test_crash_recovery_preserves_later_user_output_conflict(self):
        journal = self.crash_at(point='after_replaced')
        self.f.pdf.write_bytes(b'Later user output is never overwritten.\x00\xff')
        before = self.file_state(self.f.pdf)
        self.f.config.unlink()
        report = self.run_json(argv=self.recover_argv(journal), fake_compiler=False, forbid_source=True)
        self.assertEqual(report['status'], 3, report)
        self.assertEqual(report['output_recovery']['state'], 'required')
        self.assertEqual(self.file_state(self.f.pdf), before)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assert_lock_free()


if __name__ == '__main__':
    unittest.main()
