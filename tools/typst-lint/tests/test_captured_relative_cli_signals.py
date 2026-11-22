"""Representative owned-child public graph cancellation and independent recovery.

Only Popen children created here receive signals. Marker/boundary helpers reuse
the already exercised closed-core protocol, without any production test hooks.
"""
import json
import os
from pathlib import Path
import signal
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_captured_relative_cli import _GraphCliFixtures, ORIGINAL, EXPECTED, EXECUTABLE, OLD_OUTPUT
import test_offline_compile_signals as existing


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX owned-child signal protocol')
class GraphCliSignalTests(_GraphCliFixtures):
    # Reuse only process/boundary assertions, not another class's test cases.
    spawn = existing.OfflineCompileSignalTests.spawn
    wait_file = existing.OfflineCompileSignalTests.wait_file
    wait_boundary = existing.OfflineCompileSignalTests.wait_boundary
    release = existing.OfflineCompileSignalTests.release
    finish_json = existing.OfflineCompileSignalTests.finish_json
    assert_child_reaped = existing.OfflineCompileSignalTests.assert_child_reaped
    crash_at = existing.OfflineCompileSignalTests.crash_at

    def test_signal_before_validate_and_before_compile_preserves_commit_facts(self):
        for point, committed in (('after_prepared', False), ('before_compile', True)):
            with self.subTest(point=point):
                f = self.fixture()
                boundary = ['output', point, f.output]
                child = self.spawn(f, boundaries=[boundary])
                self.assertEqual(self.wait_boundary(child, f), boundary)
                child.send_signal(signal.SIGTERM)
                self.release(f)
                result = self.finish_json(child, expected=-signal.SIGTERM)
                self.assertEqual(result['received_signals'][0], signal.SIGTERM)
                self.assertIs(result['compiler_invoked'], False)
                self.assertIsNone(result['compiler_status'])
                self.assertEqual(f.source.read_bytes(), EXPECTED if committed else ORIGINAL)
                self.assertEqual(f.pdf.read_bytes(), OLD_OUTPUT)
                self.assertEqual(result['output_recovery']['state'], 'not_required')
                self.assertFalse(result['graph_observation_available'])
                if committed:
                    self.assert_membership(result['source_summary'], f)
                    self.assertEqual(self.events(f, source=True)[0][-1], 'committed')
                else:
                    self.assertIsNone(result['source_summary'])
                    self.assertEqual(self.journals(f, source=True), [])
                self.assert_lock_free(f)

    def test_signal_during_source_apply_is_deferred_until_genuine_commit(self):
        boundary = ['source', 'after_replace', self.f.target]
        child = self.spawn(boundaries=[boundary])
        self.assertEqual(self.wait_boundary(child), boundary)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        child.send_signal(signal.SIGTERM)
        self.release()
        result = self.finish_json(child, expected=-signal.SIGTERM)
        self.assertEqual(result['source_commit'], 'committed')
        self.assert_membership(result['source_summary'])
        self.assertEqual(result['source_recovery']['state'], 'not_required')
        self.assertEqual(self.events(source=True)[0][-1], 'committed')
        self.assertIs(result['compiler_invoked'], False)
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.assert_lock_free()

    def test_signal_during_compile_reaps_child_and_preserves_original_received_signal(self):
        child = self.spawn(compiler_mode='hang')
        pid = int(self.wait_file(child, self.f.compiler_marker))
        child.send_signal(signal.SIGINT)
        result = self.finish_json(child, expected=-signal.SIGINT)
        self.assertEqual(result['compiler_status'], -signal.SIGKILL)
        self.assertEqual(result['compile_report']['compiler_failure']['kind'], 'cancelled')
        self.assertEqual(result['received_signals'][0], signal.SIGINT)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.assertFalse(result['graph_observation_available'])
        self.assert_child_reaped(pid)
        self.assert_lock_free()

    def test_signal_after_rename_restores_output_and_retains_actual_observations(self):
        boundary = ['output', 'after_replaced', self.f.output]
        child = self.spawn(boundaries=[boundary])
        self.assertEqual(self.wait_boundary(child), boundary)
        self.assertTrue(self.f.pdf.read_bytes().startswith(b'%PDF-1.7'))
        child.send_signal(signal.SIGINT)
        self.release()
        result = self.finish_json(child, expected=-signal.SIGINT)
        self.assertEqual(result['compiler_status'], 0)
        self.assertEqual(result['publication_report']['output_disposition'], 'restored')
        self.assertFalse(result['publication_report']['recovery_required'])
        self.assertTrue(result['graph_observation_available'])
        self.assertEqual(result['graph_observation']['dependency_paths'], list(self.f.sources))
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assertEqual(self.events()[0][-1], 'restored')
        self.assert_lock_free()

    def test_repeated_signals_during_restore_do_not_interrupt_cleanup(self):
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
        result = self.finish_json(child, expected=-signal.SIGTERM)
        self.assertEqual(result['received_signals'][0], signal.SIGTERM)
        self.assertIn(signal.SIGINT, result['received_signals'])
        self.assertEqual(result['publication_report']['output_disposition'], 'restored')
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assert_lock_free()

    def test_cleanup_failure_overrides_raw_signal_without_hiding_compiler_execution(self):
        for failure in ('cleanup_failure', 'source_exit_failure'):
            with self.subTest(failure=failure):
                f = self.fixture()
                child = self.spawn(f, compiler_mode='hang', **{failure: True})
                pid = int(self.wait_file(child, f.compiler_marker))
                child.send_signal(signal.SIGTERM)
                result = self.finish_json(child, expected=3)
                self.assertEqual(result['received_signals'][0], signal.SIGTERM)
                self.assertNotIn('termination_signal', result)
                self.assertEqual(result['compiler_status'], -signal.SIGKILL)
                self.assertEqual(f.source.read_bytes(), EXPECTED)
                self.assertEqual(f.pdf.read_bytes(), OLD_OUTPUT)
                self.assert_child_reaped(pid)
                self.assert_lock_free(f)

    def test_later_output_conflict_overrides_signal_and_preserves_user_bytes(self):
        boundary = ['output', 'after_replaced', self.f.output]
        child = self.spawn(boundaries=[boundary])
        self.wait_boundary(child)
        self.f.pdf.write_bytes(b'Later user output edit.\x00\xff')
        before = self.complete_state(self.f.pdf)
        child.send_signal(signal.SIGTERM)
        self.release()
        result = self.finish_json(child, expected=3)
        self.assertNotIn('termination_signal', result)
        self.assertEqual(result['output_recovery']['state'], 'required')
        self.assertEqual(self.complete_state(self.f.pdf), before)
        self.assertTrue(result['graph_observation_available'])
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assert_lock_free()

    def test_callable_main_keeps_signal_handlers_and_returns_negative_status(self):
        destination = self.f.base / 'callable result.json'
        result = self.run_process(callable=True, callable_result=str(destination),
            compiler_mode=f'signal-{int(signal.SIGTERM)}')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b'')
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], -signal.SIGTERM)
        self.assertEqual(json.loads(destination.read_text()),
                         {'status': -signal.SIGTERM, 'handlers_unchanged': True})
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.assert_lock_free()

    def test_pending_output_history_blocks_before_graph_validate(self):
        journal = self.crash_at()
        result = self.run_json(forbid_validate=True)
        self.assertEqual(result['status'], 3, result)
        self.assert_no_compile(result)
        self.assertEqual(self.f.source.read_bytes(), ORIGINAL)
        self.assertIn(journal, result['output_inventory']['pending'])
        self.assertEqual(self.journals(source=True), [])
        self.assert_lock_free()

    def test_sigkill_source_and_output_histories_recover_independently_without_current_inputs(self):
        output_id = self.crash_at(domain='source', point='after_replace')
        source_id = self.journals(source=True)[0].parent.name
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        blocked = self.run_json(forbid_validate=True, forbid_output_scan=True)
        self.assertEqual(blocked['status'], 3, blocked)
        self.assertEqual(blocked['source_recovery']['state'], 'required')
        self.assertEqual(blocked['output_recovery']['state'], 'not_checked')
        self.assertNotIn('output_inventory', blocked)
        # Remove all current nonhistorical inputs. Recovery must not rediscover
        # imports, admit a language, capture native resources or require config.
        self.f.config.unlink()
        (self.f.root / 'values.typ').unlink()
        self.f.compiler.unlink()
        (self.f.base / 'owned compile fixture').unlink()
        self.f.font.unlink()
        self.f.catalog.unlink()
        output = self.run_json(argv=self.recover_argv(output_id), fake_compiler=False, forbid_source=True)
        self.assertEqual(output['status'], 0, output)
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assertEqual(output['source_recovery']['state'], 'not_checked')
        self.assertEqual(output['publication_report']['invocation_origin'], 'historical_recovery')
        self.assertFalse(output['publication_report']['invocation_details_available'])
        self.assertFalse(output['publication_report']['resource_details_available'])
        self.assertNotIn('recovered', self.events(source=True)[0])
        args = self.argv('recover') + ['--offline-exclusive', '--target', self.f.target,
                                      '--journal-id', source_id]
        source = self.run_json(argv=args, fake_compiler=False, forbid_output_scan=True)
        self.assertEqual(source['status'], 0, source)
        self.assertTrue(source['recovery_report']['complete'])
        self.assertEqual(self.f.source.read_bytes(), ORIGINAL)
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.assertEqual(self.events(source=True)[0][-1], 'recovered')
        self.assert_lock_free()

    def test_sigkill_after_output_rename_recovers_without_source_or_modules(self):
        output_id = self.crash_at(point='after_replaced')
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assertTrue(self.f.pdf.read_bytes().startswith(b'%PDF-1.7'))
        self.f.source.unlink()
        (self.f.root / 'values.typ').unlink()
        self.f.config.unlink()
        self.f.compiler.unlink()
        (self.f.base / 'owned compile fixture').unlink()
        self.f.font.unlink()
        self.f.catalog.unlink()
        result = self.run_json(argv=self.recover_argv(output_id), fake_compiler=False, forbid_source=True)
        self.assertEqual(result['status'], 0, result)
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.assertFalse(self.f.source.exists())
        self.assertEqual(result['publication_report']['compiler_status'], 0)
        self.assertEqual(result['publication_report']['attempted_invocations'], [])
        self.assertFalse(result['publication_report']['invocation_details_available'])
        self.assertFalse(result['publication_report']['resource_details_available'])
        self.assertNotIn('graph_observation', result)
        self.assert_lock_free()


if __name__ == '__main__':
    unittest.main()
