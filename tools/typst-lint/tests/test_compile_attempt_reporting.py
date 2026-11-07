"""Output-only compiler facts survive final safety refusals.

Only owned temporary compiler fixtures run. No report authorizes an artifact,
consumer output, source write, compiler choice, or process after a hard kill.
"""
from dataclasses import FrozenInstanceError, asdict, replace
from itertools import product
import json
import os
from pathlib import Path
import signal
import sys
import threading
import unittest
from unittest import mock

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import closed_core_compile as cc
import test_closed_core_compile as fixtures


class CompileAttemptReportingTests(unittest.TestCase):
    # Reuse owned fixture setup without inheriting/repeating its test methods.
    setUp = fixtures.CompilerAdapterTests.setUp
    set_source = fixtures.CompilerAdapterTests.set_source
    program = fixtures.CompilerAdapterTests.program
    reviewed = fixtures.CompilerAdapterTests.reviewed
    compile = fixtures.CompilerAdapterTests.compile

    def report(self, failure, purposes, *, gate='refused', invoked=False, status=None):
        report = failure.report
        self.assertIs(type(report), cc.CompileReport)
        self.assertEqual(tuple(item.purpose for item in report.attempted_invocations), purposes)
        self.assertEqual(report.attempted_invocations, report.attempts)
        self.assertEqual(report.safety_gate, gate)
        self.assertIs(report.compiler_invoked, invoked)
        self.assertEqual(report.compiler_status, status)
        self.assertEqual(asdict(report)['safety_gate'], gate)
        return report

    def test_prelaunch_safety_refusal_has_no_probe_or_compile_attempt(self):
        self.active = False
        with mock.patch.object(cc.subprocess, 'Popen') as launch:
            with self.assertRaises(cc.CompileSafetyError) as caught:
                self.compile()
        launch.assert_not_called()
        report = self.report(caught.exception, ())
        self.assertIsNone(report.compiler_failure)

    def test_unreviewed_compiler_has_no_attempt(self):
        self.reviewed_hash = '0' * 64
        with self.assertRaises(cc.CompileSafetyError) as caught:
            self.compile()
        self.report(caught.exception, ())

    def test_prelaunch_cancel_and_deadline_have_no_attempt(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(cc.CompilerFailure) as caught:
            self.compile(cancel_event=event)
        report = self.report(caught.exception, (), gate='passed')
        self.assertEqual(report.compiler_failure.reason, 'cancelled')
        self.assertIs(report.compiler_failure.kind, cc.CompilerFailureKind.CANCELLED)
        self.assertIs(caught.exception.kind, cc.CompilerFailureKind.CANCELLED)
        self.assertFalse(caught.exception.invocation.launch_attempted)
        run = cc._run

        def expired(argv, **kwargs):
            kwargs['deadline'] = 0
            return run(argv, **kwargs)
        with mock.patch.object(cc, '_run', side_effect=expired):
            with self.assertRaises(cc.CompilerFailure) as caught:
                self.compile()
        report = self.report(caught.exception, (), gate='passed')
        self.assertEqual(caught.exception.reason, 'timeout')
        self.assertIs(report.compiler_failure.kind, cc.CompilerFailureKind.TIMEOUT)
        self.assertIs(caught.exception.kind, cc.CompilerFailureKind.TIMEOUT)

    def test_probe_success_then_version_mismatch_never_reports_compile(self):
        with mock.patch.object(cc, '_reviewed_native_artifact', return_value=
                cc._ReviewedNativeArtifact(self.executable, self.reviewed_hash, b'wrong\n')):
            with self.assertRaises(cc.CompileSafetyError) as caught:
                self.compile()
        report = self.report(caught.exception, ('version-probe',))
        probe, = report.attempts
        self.assertTrue(probe.launch_attempted)
        self.assertIs(probe.started, True)
        self.assertEqual((probe.lifecycle, probe.returncode), ('exited', 0))
        self.assertEqual(probe.stdout, cc._VERSION)
        self.assertIsNone(report.compiler_failure)

    def test_deadline_before_compile_keeps_successful_probe_only(self):
        run = cc._run

        def expired_compile(argv, **kwargs):
            if kwargs['purpose'] == 'compile':
                kwargs['deadline'] = 0
            return run(argv, **kwargs)
        with mock.patch.object(cc, '_run', side_effect=expired_compile):
            with self.assertRaises(cc.CompilerFailure) as caught:
                self.compile()
        report = self.report(caught.exception, ('version-probe',), gate='passed')
        self.assertEqual(report.attempts[0].returncode, 0)
        self.assertEqual(report.compiler_failure.reason, 'timeout')
        self.assertIs(report.compiler_failure.kind, cc.CompilerFailureKind.TIMEOUT)
        self.assertEqual(caught.exception.invocation.purpose, 'compile')
        self.assertFalse(caught.exception.invocation.launch_attempted)

    def test_probe_and_compile_launch_failure_are_separately_recorded(self):
        popen = cc.subprocess.Popen
        for purpose in ('version-probe', 'compile'):
            with self.subTest(purpose=purpose):
                def launch(argv, **kwargs):
                    if (argv[1] == '--version') == (purpose == 'version-probe'):
                        raise OSError('owned fixture launch failure')
                    return popen(argv, **kwargs)
                with mock.patch.object(cc.subprocess, 'Popen', side_effect=launch):
                    with self.assertRaises(cc.CompileSafetyError) as caught:
                        self.compile()
                purposes = ('version-probe',) if purpose == 'version-probe' else ('version-probe', 'compile')
                report = self.report(caught.exception, purposes)
                attempt = report.attempts[-1]
                self.assertTrue(attempt.launch_attempted)
                self.assertIs(attempt.started, False)
                self.assertEqual(attempt.lifecycle, 'launch_failed')
                self.assertIsNone(attempt.returncode)
                self.assertEqual(attempt.termination_reason, 'launch failed')

    def test_interrupt_during_launch_is_uncertain_not_false(self):
        popen = cc.subprocess.Popen
        for purpose in ('version-probe', 'compile'):
            with self.subTest(purpose=purpose):
                def launch(argv, **kwargs):
                    if (argv[1] == '--version') == (purpose == 'version-probe'):
                        raise KeyboardInterrupt
                    return popen(argv, **kwargs)
                with mock.patch.object(cc.subprocess, 'Popen', side_effect=launch):
                    with self.assertRaises(KeyboardInterrupt) as caught:
                        self.compile()
                purposes = ('version-probe',) if purpose == 'version-probe' else ('version-probe', 'compile')
                report = self.report(caught.exception, purposes, gate='pending',
                                     invoked=None if purpose == 'compile' else False)
                attempt = report.attempts[-1]
                self.assertTrue(attempt.launch_attempted)
                self.assertIsNone(attempt.started)
                self.assertEqual(attempt.lifecycle, 'uncertain')
                self.assertIsNone(attempt.returncode)
                self.assertEqual(attempt.termination_reason, 'interrupted')
                self.assertIsNone(report.compiler_failure)

    def test_started_nonzero_signal_timeout_and_output_limits_keep_raw_status(self):
        kinds = cc.CompilerFailureKind
        cases = (('sys.exit(9)', 'nonzero exit', 9, kinds.PROCESS_EXIT),
                 ('os.kill(os.getpid(), signal.SIGTERM)', 'signal', -signal.SIGTERM, kinds.PROCESS_EXIT),
                 ('os.kill(os.getpid(), signal.SIGKILL)', 'signal', -signal.SIGKILL, kinds.PROCESS_EXIT),
                 ('time.sleep(5)', 'timeout', -signal.SIGKILL, kinds.TIMEOUT),
                 ('os.write(1, b"x" * 1000000); time.sleep(5)', 'stdout byte limit exceeded',
                  -signal.SIGKILL, kinds.STREAM_LIMIT),
                 ('os.write(2, b"x" * 1000000); time.sleep(5)', 'stderr byte limit exceeded',
                  -signal.SIGKILL, kinds.STREAM_LIMIT))
        for behavior, reason, status, kind in cases:
            with self.subTest(reason=reason):
                self.program('os.write(1, b"out"); os.write(2, b"err"); ' + behavior)
                with self.assertRaises(cc.CompilerFailure) as caught:
                    self.compile(deadline_seconds=0.6)
                report = self.report(caught.exception, ('version-probe', 'compile'),
                                     gate='passed', invoked=True, status=status)
                attempt = report.attempts[-1]
                self.assertEqual(attempt, caught.exception.invocation)
                self.assertTrue(attempt.launch_attempted)
                self.assertIs(attempt.started, True)
                self.assertEqual(attempt.lifecycle, 'signaled' if status < 0 else 'exited')
                self.assertEqual(attempt.termination_reason, reason)
                self.assertEqual(report.compiler_failure, cc.CompilerFailureInfo(reason, attempt, kind))
                self.assertIs(caught.exception.kind, kind)
                self.assertEqual(json.loads(json.dumps(asdict(report)['compiler_failure']['kind'])), kind.value)
                self.assertLessEqual(len(attempt.stdout), cc._MAX_STREAM)
                self.assertLessEqual(len(attempt.stderr), cc._MAX_STREAM)

    def test_probe_natural_nonzero_and_signal_have_process_exit_kind_without_compile(self):
        for behavior, status in (('sys.exit(9)', 9),
                ('os.kill(os.getpid(), signal.SIGTERM)', -signal.SIGTERM)):
            with self.subTest(status=status):
                self.program()
                raw = self.executable.read_bytes().replace(
                    f'    os.write(1, {cc._VERSION!r}); sys.exit(0)'.encode(),
                    f'    {behavior}'.encode())
                self.executable.chmod(0o700)
                self.executable.write_bytes(raw)
                self.executable.chmod(0o500)
                self.reviewed_hash = cc._hash(raw)
                with self.assertRaises(cc.CompilerFailure) as caught:
                    self.compile()
                report = self.report(caught.exception, ('version-probe',), gate='passed')
                failure = report.compiler_failure
                self.assertIs(failure.kind, cc.CompilerFailureKind.PROCESS_EXIT)
                self.assertIs(caught.exception.kind, cc.CompilerFailureKind.PROCESS_EXIT)
                self.assertEqual(failure.invocation.purpose, 'version-probe')
                self.assertEqual(failure.invocation.returncode, status)
                self.assertEqual(failure.invocation, report.attempts[0])

    def test_default_failure_kind_is_unknown_and_independent_of_reason_and_status(self):
        invocation = cc.Invocation('compile', ('owned',), str(self.base), (),
                                   -signal.SIGKILL, b'', b'', True, True, 'signaled')
        for reason in ('nonzero exit', 'signal', 'cancelled', 'timeout', 'stdout byte limit exceeded'):
            with self.subTest(reason=reason):
                failure = cc.CompilerFailure(reason, invocation)
                self.assertEqual(str(failure), reason)
                self.assertIs(failure.invocation, invocation)
                self.assertIs(failure.kind, cc.CompilerFailureKind.UNKNOWN)
                with self.assertRaises(AttributeError):
                    failure.kind = cc.CompilerFailureKind.PROCESS_EXIT
                ledger = cc._AttemptLedger()
                ledger.note_failure(failure)
                info = ledger.snapshot('pending').compiler_failure
                self.assertEqual(info, cc.CompilerFailureInfo(reason, invocation))
                self.assertIs(info.kind, cc.CompilerFailureKind.UNKNOWN)
                with self.assertRaises(FrozenInstanceError):
                    info.kind = cc.CompilerFailureKind.PROCESS_EXIT

    def test_cancellation_after_launch_reaps_and_records_signal(self):
        event = threading.Event()
        popen = cc.subprocess.Popen
        self.program('time.sleep(5)')

        def launch(argv, **kwargs):
            process = popen(argv, **kwargs)
            if argv[1] == 'compile':
                event.set()
            return process
        with mock.patch.object(cc.subprocess, 'Popen', side_effect=launch):
            with self.assertRaises(cc.CompilerFailure) as caught:
                self.compile(cancel_event=event)
        report = self.report(caught.exception, ('version-probe', 'compile'), gate='passed',
                             invoked=True, status=-signal.SIGKILL)
        self.assertEqual(report.attempts[-1].termination_reason, 'cancelled')
        self.assertIs(caught.exception.kind, cc.CompilerFailureKind.CANCELLED)
        self.assertIs(report.compiler_failure.kind, cc.CompilerFailureKind.CANCELLED)

    def test_interrupt_after_launch_cleans_up_and_preserves_observed_status(self):
        self.program('time.sleep(5)')
        select = cc.selectors.DefaultSelector
        calls = 0

        def selector():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt
            return select()
        with mock.patch.object(cc.selectors, 'DefaultSelector', side_effect=selector):
            with self.assertRaises(KeyboardInterrupt) as caught:
                self.compile()
        report = self.report(caught.exception, ('version-probe', 'compile'), gate='pending',
                             invoked=True, status=-signal.SIGKILL)
        self.assertEqual(report.attempts[-1].lifecycle, 'signaled')
        self.assertEqual(report.attempts[-1].termination_reason, 'interrupted')
        self.assertIsNone(report.compiler_failure)

    def test_final_guard_overrides_nonzero_and_signal_without_losing_failure(self):
        run = cc._run
        cases = (('sys.exit(7)', 'nonzero exit', 7),
                 ('os.kill(os.getpid(), signal.SIGTERM)', 'signal', -signal.SIGTERM))
        for (behavior, reason, status), drift in product(cases, ('source', 'compiler', 'output')):
            with self.subTest(reason=reason, drift=drift):
                self.active = True
                self.program(behavior)

                def invalidate(argv, **kwargs):
                    try:
                        return run(argv, **kwargs)
                    except cc.CompilerFailure:
                        if drift == 'source':
                            self.active = False
                        elif drift == 'compiler':
                            self.executable.chmod(0o700)
                        else:
                            Path(argv[-1]).unlink()
                        raise
                with mock.patch.object(cc, '_run', side_effect=invalidate):
                    with self.assertRaises(cc.CompileSafetyError) as caught:
                        self.compile()
                report = self.report(caught.exception, ('version-probe', 'compile'),
                                     invoked=True, status=status)
                self.assertEqual(report.compiler_failure.reason, reason)
                self.assertIs(report.compiler_failure.kind, cc.CompilerFailureKind.PROCESS_EXIT)
                self.assertEqual(report.compiler_failure.invocation, report.attempts[-1])

    def test_success_then_source_compiler_or_output_refusal_keeps_zero_exit(self):
        run = cc._run
        for kind in ('source', 'compiler', 'output'):
            with self.subTest(kind=kind):
                self.active = True
                self.program()

                def invalidate(argv, **kwargs):
                    result = run(argv, **kwargs)
                    if kwargs['purpose'] == 'compile':
                        if kind == 'source':
                            self.active = False
                        elif kind == 'compiler':
                            self.executable.chmod(0o700)
                        else:
                            Path(argv[-1]).unlink()
                    return result
                with mock.patch.object(cc, '_run', side_effect=invalidate):
                    with self.assertRaises(cc.CompileSafetyError) as caught:
                        self.compile()
                report = self.report(caught.exception, ('version-probe', 'compile'), invoked=True, status=0)
                self.assertEqual(report.attempts[-1].lifecycle, 'exited')
                self.assertIsNone(report.attempts[-1].termination_reason)
                self.assertIsNone(report.compiler_failure)

    def test_postrun_workspace_guard_preserves_observed_exit_status(self):
        run = cc._run

        def guarded(argv, **kwargs):
            if kwargs['purpose'] == 'compile':
                check = kwargs['check_workspace']
                popen = cc.subprocess.Popen
                processes = []

                def launch(*args, **options):
                    process = popen(*args, **options)
                    processes.append(process)
                    return process

                def check_after_exit():
                    check()
                    if processes and processes[0].returncode == 0:
                        raise cc.CompileSafetyError('owned postrun guard refusal')
                kwargs['check_workspace'] = check_after_exit
                with mock.patch.object(cc.subprocess, 'Popen', side_effect=launch):
                    return run(argv, **kwargs)
            return run(argv, **kwargs)
        with mock.patch.object(cc, '_run', side_effect=guarded):
            with self.assertRaises(cc.CompileSafetyError) as caught:
                self.compile()
        report = self.report(caught.exception, ('version-probe', 'compile'), invoked=True, status=0)
        self.assertEqual(report.attempts[-1].termination_reason, 'safety refusal')

    def test_final_cancellation_preserves_compile_zero_with_distinct_failure_reason(self):
        event = threading.Event()
        guard = cc._guard
        run = cc._run
        completed = False

        def observe(argv, **kwargs):
            nonlocal completed
            result = run(argv, **kwargs)
            completed = completed or kwargs['purpose'] == 'compile'
            return result

        def late_cancel(*args):
            result = guard(*args)
            if completed:
                event.set()
            return result
        with mock.patch.object(cc, '_run', side_effect=observe), \
                mock.patch.object(cc, '_guard', side_effect=late_cancel):
            with self.assertRaises(cc.CompilerFailure) as caught:
                self.compile(cancel_event=event)
        report = self.report(caught.exception, ('version-probe', 'compile'), gate='passed',
                             invoked=True, status=0)
        self.assertEqual(report.compiler_failure.reason, 'cancelled')
        self.assertIs(report.compiler_failure.kind, cc.CompilerFailureKind.CANCELLED)
        self.assertIs(caught.exception.kind, cc.CompilerFailureKind.CANCELLED)
        self.assertEqual(report.compiler_failure.invocation.returncode, 0)

    def test_final_deadline_preserves_compile_zero_with_timeout_kind(self):
        run = cc._run
        monotonic = cc.time.monotonic
        completed = False

        def observe(argv, **kwargs):
            nonlocal completed
            result = run(argv, **kwargs)
            completed = completed or kwargs['purpose'] == 'compile'
            return result

        def late_deadline():
            return monotonic() + (121 if completed else 0)
        with mock.patch.object(cc, '_run', side_effect=observe), \
                mock.patch.object(cc.time, 'monotonic', side_effect=late_deadline):
            with self.assertRaises(cc.CompilerFailure) as caught:
                self.compile()
        report = self.report(caught.exception, ('version-probe', 'compile'), gate='passed',
                             invoked=True, status=0)
        self.assertIs(caught.exception.kind, cc.CompilerFailureKind.TIMEOUT)
        self.assertIs(report.compiler_failure.kind, cc.CompilerFailureKind.TIMEOUT)
        self.assertEqual(report.compiler_failure.reason, 'timeout')
        self.assertEqual(report.compiler_failure.invocation, report.attempts[-1])

    def test_late_safety_refusal_retains_original_cancellation_kind(self):
        event = threading.Event()
        event.set()
        run = cc._run

        def invalidate(argv, **kwargs):
            try:
                return run(argv, **kwargs)
            except cc.CompilerFailure:
                self.active = False
                raise
        with mock.patch.object(cc, '_run', side_effect=invalidate):
            with self.assertRaises(cc.CompileSafetyError) as caught:
                self.compile(cancel_event=event)
        report = self.report(caught.exception, ())
        self.assertIs(report.compiler_failure.kind, cc.CompilerFailureKind.CANCELLED)
        self.assertFalse(report.compiler_failure.invocation.launch_attempted)
        self.assertFalse(hasattr(caught.exception, 'kind'))

    def test_artifact_issuance_failure_does_not_erase_successful_attempts(self):
        class RefuseIssuance(dict):
            def __setitem__(self, key, value):
                raise RuntimeError('owned issuance refusal')
        with mock.patch.object(cc, '_ARTIFACTS', RefuseIssuance()):
            with self.assertRaises(cc.CompileSafetyError) as caught:
                self.compile()
        report = self.report(caught.exception, ('version-probe', 'compile'), invoked=True, status=0)
        self.assertIsNone(report.compiler_failure)

    def test_success_report_is_immutable_and_sealed_but_grants_no_authority(self):
        artifact = self.compile()
        report = self.report(artifact, ('version-probe', 'compile'), gate='passed', invoked=True, status=0)
        before = asdict(report)
        self.assertEqual(report.attempts, (*artifact.probes, artifact.compilation))
        with self.assertRaises(FrozenInstanceError):
            report.safety_gate = 'refused'
        with self.assertRaises(FrozenInstanceError):
            report.attempts[-1].returncode = 1
        with self.assertRaises(cc.CompileSafetyError):
            cc.assert_artifact_current(self.fix, report)
        object.__setattr__(artifact, 'report', replace(report, safety_gate='refused'))
        with self.assertRaises(cc.CompileSafetyError):
            cc.assert_artifact_current(self.fix, artifact)
        self.active = False
        self.assertEqual(asdict(report), before)  # Historical facts outlive authority.

    def test_nested_report_tampering_is_detected_by_artifact_seal(self):
        artifact = self.compile()
        object.__setattr__(artifact.report.attempts[-1], 'started', False)
        with self.assertRaises(cc.CompileSafetyError):
            cc.assert_artifact_current(self.fix, artifact)

    def test_nested_failure_kind_participates_in_artifact_seal_if_represented(self):
        # A successful ordinary report has no failure. Patch only the private
        # reporting seam to represent one, then exercise the real nested seal.
        snapshot = cc._AttemptLedger.snapshot

        def with_failure(ledger, gate):
            report = snapshot(ledger, gate)
            if gate == 'passed':
                return replace(report, compiler_failure=cc.CompilerFailureInfo(
                    'owned historical fixture', None, cc.CompilerFailureKind.UNKNOWN))
            return report
        with mock.patch.object(cc._AttemptLedger, 'snapshot', with_failure):
            artifact = self.compile()
        cc.assert_artifact_current(self.fix, artifact)
        object.__setattr__(artifact.report.compiler_failure, 'kind', cc.CompilerFailureKind.PROCESS_EXIT)
        with self.assertRaises(cc.CompileSafetyError):
            cc.assert_artifact_current(self.fix, artifact)

    def test_interrupted_cleanup_does_not_invent_reaped_process_status(self):
        # A deterministic fake has no OS child; an interrupted wait leaves no
        # observed status. We neither claim a SIGKILL returncode nor false start.
        class Pipe:
            def close(self):
                pass
        class Process:
            pid = -1
            returncode = None
            stdout = stderr = Pipe()
        process = Process()
        with mock.patch.object(cc.subprocess, 'Popen', return_value=process), \
                mock.patch.object(cc.selectors, 'DefaultSelector', side_effect=KeyboardInterrupt), \
                mock.patch.object(cc, '_kill', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt) as caught:
                self.compile()
        report = self.report(caught.exception, ('version-probe',), gate='pending')
        self.assertIs(report.attempts[0].started, True)
        self.assertEqual(report.attempts[0].lifecycle, 'uncertain')
        self.assertIsNone(report.attempts[0].returncode)


if __name__ == '__main__':
    unittest.main()
