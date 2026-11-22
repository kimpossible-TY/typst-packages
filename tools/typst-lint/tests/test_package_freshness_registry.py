"""Offline tests for the explicit official-preview-index comparison."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import package_freshness_registry as registry


def index(*pairs):
    return json.dumps([{'name': name, 'version': version} for name, version in pairs]).encode()


class RegistryFreshnessTests(unittest.TestCase):
    def test_offline_never_fetches_and_has_no_update_authority(self):
        with patch.object(registry, '_fetch_index', side_effect=AssertionError('Unexpected network')):
            report = registry.inspect_preview_registry([('cetz', '0.4.2')])
        self.assertFalse(report['network_attempted'])
        self.assertEqual(report['packages'][0]['status'], 'unknown')
        self.assertEqual(report['packages'][0]['reason'], 'offline_not_checked')
        self.assertIsNone(report['snapshot_sha256'])
        for field in ('runtime_selection_proved', 'installed_bytes_verified',
                      'compatibility_proved', 'direct_dependency_proved', 'update_permission'):
            self.assertFalse(report[field])

    def test_numeric_order_versions_and_snapshot_evidence(self):
        raw = index(('cetz', '0.3.4'), ('cetz', '0.4.2'), ('cetz', '0.10.0'), ('mannot', '0.4.0'))
        meta = {'etag': '"etag"', 'last_modified': 'Fri, 06 Nov 2026 12:37:03 GMT'}
        with patch.object(registry, '_fetch_index', return_value=(raw, meta)):
            report = registry.inspect_preview_registry([
                ('cetz', '0.4.2'), ('cetz', '0.10.0'), ('cetz', '0.9.0'),
                ('mannot', '0.4.0'), ('missing', '1.0.0')], online=True)
        rows = report['packages']
        self.assertEqual([row['status'] for row in rows],
                         ['newer_version_available', 'matches_latest_version', 'unknown', 'matches_latest_version', 'unknown'])
        self.assertEqual(rows[0]['latest_version'], '0.10.0')
        self.assertEqual(rows[2]['reason'], 'version_not_in_index')
        self.assertEqual(rows[4]['reason'], 'package_not_in_index')
        self.assertEqual(report['snapshot_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(report['etag'], '"etag"')
        self.assertIsNotNone(report['retrieved_at_utc'])

    def test_invalid_requests_are_rejected_before_network(self):
        invalid = [[], [('cetz', '01.2.3')], [('cetz', '1.2.4294967296')],
                   [('cetz', '1.2.3-rc1')], [('cetz/evil', '1.0.0')],
                   [('cetz', '1.0.0'), ('cetz', '1.0.0')], [('cetz', True)]]
        with patch.object(registry, '_fetch_index', side_effect=AssertionError('Unexpected network')):
            for value in invalid:
                with self.subTest(value=value), self.assertRaises(registry.RegistryInputError):
                    registry.inspect_preview_registry(value, online=True)
            with self.assertRaises(registry.RegistryInputError):
                registry.inspect_preview_registry([('cetz', '1.0.0')], online=1)
            with self.assertRaises(registry.RegistryInputError):
                registry.inspect_preview_registry([('cetz', '1.0.0')], cancel_event=object())

    def test_malformed_index_and_duplicate_entries_are_unknown(self):
        payloads = (b'[]', b'{"name":"cetz"}', b'not-json', b'[{"name":"cetz","name":"other","version":"1.0.0"}]',
                    index(('cetz', '1.0.0'), ('cetz', '1.0.0')), index(('cetz', '01.0.0')),
                    b'[{"name":"cetz","version":"1.0.0","extra":NaN}]')
        for raw in payloads:
            with self.subTest(raw=raw), patch.object(registry, '_fetch_index', return_value=(raw, {'etag': None, 'last_modified': None})):
                report = registry.inspect_preview_registry([('cetz', '1.0.0')], online=True)
                self.assertEqual(report['packages'][0]['status'], 'unknown')
                self.assertEqual(report['packages'][0]['reason'], 'registry_index_invalid')
                self.assertEqual(report['snapshot_sha256'], hashlib.sha256(raw).hexdigest())

    def test_timeout_and_unavailable_registry_are_unknown(self):
        started = time.monotonic()
        with patch.object(registry, '_WORKER', 'import time; time.sleep(30)'), \
             patch.object(registry, 'FETCH_TIMEOUT_SECONDS', 0.15):
            report = registry.inspect_preview_registry([('cetz', '1.0.0')], online=True)
        self.assertLess(time.monotonic() - started, 4)
        self.assertTrue(report['online_lookup_requested'])
        self.assertIsNone(report['network_attempted'])
        self.assertEqual(report['packages'][0]['status'], 'unknown')
        self.assertEqual(report['packages'][0]['reason'], 'registry_unreachable_or_timed_out')
        self.assertIsNone(report['snapshot_sha256'])

    def test_cleanup_failure_is_not_hidden_by_cancellation(self):
        event = threading.Event()
        def failed_cleanup(*args, **kwargs):
            event.set()
            raise registry.RegistryCleanupError('registry_worker_cleanup_failed')
        with patch.object(registry, '_fetch_index', side_effect=failed_cleanup):
            with self.assertRaisesRegex(registry.RegistryCleanupError, 'cleanup_failed'):
                registry.inspect_preview_registry([('cetz', '1.0.0')], online=True, cancel_event=event)

    def test_worker_is_isolated_bounded_and_uses_only_fixed_url(self):
        raw = index(('cetz', '1.0.0'))
        output = b'{"etag":null,"last_modified":null}\n' + raw
        worker = 'import sys; sys.stdout.buffer.write(' + repr(output) + ')'
        actual_popen = subprocess.Popen
        with patch.object(registry, '_WORKER', worker), \
             patch.object(registry.subprocess, 'Popen', wraps=actual_popen) as run:
            report = registry.inspect_preview_registry([('cetz', '1.0.0')], online=True)
        self.assertEqual(report['packages'][0]['status'], 'matches_latest_version')
        args, kwargs = run.call_args
        self.assertEqual(args[0][1:4], ['-I', '-S', '-c'])
        self.assertEqual(args[0][4], worker)
        self.assertIn(registry.REGISTRY_URL, registry._WORKER)
        self.assertIn('ProxyHandler({})', registry._WORKER)
        self.assertIn('class NoRedirect', registry._WORKER)
        self.assertNotIn('cetz', registry._WORKER)
        self.assertTrue(kwargs['start_new_session'])
        self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
        self.assertEqual(set(kwargs['env']), {'PATH', 'LANG', 'LC_ALL'})

    def test_oversized_or_malformed_worker_response_is_unknown(self):
        responses = (b'X' * (registry.MAX_INDEX_BYTES + 1025), b'bad-header\n{}',
                     b'{"etag":"bad\\nheader","last_modified":null}\n[]')
        for output in responses:
            with self.subTest(size=len(output)), patch.object(registry, '_run_worker', return_value=output):
                report = registry.inspect_preview_registry([('cetz', '1.0.0')], online=True)
                self.assertEqual(report['packages'][0]['status'], 'unknown')
                self.assertIsNone(report['snapshot_sha256'])

    def test_worker_redirect_handler_never_follows_new_urls(self):
        definition = registry._WORKER.split('\nurl =', 1)[0]
        namespace = {}
        exec(definition, namespace)
        handler = namespace['NoRedirect']()
        self.assertIsNone(handler.redirect_request(None, None, 302, '', {},
                                                    'https://elsewhere.example/index.json'))

    def test_worker_refuses_truncated_declared_response(self):
        class Response:
            status = 200
            headers = {'Content-Length': '20'}

            def __enter__(self): return self
            def __exit__(self, *args): return False
            def geturl(self): return registry.REGISTRY_URL
            def read(self, limit): return b'[]'

        class Opener:
            def open(self, request, timeout): return Response()

        with patch('urllib.request.build_opener', return_value=Opener()), self.assertRaises(ValueError):
            exec(registry._WORKER, {})

    def test_cancel_before_network_or_offline_report(self):
        cancelled = threading.Event()
        cancelled.set()
        with patch.object(registry.subprocess, 'Popen', side_effect=AssertionError('Unexpected worker')):
            for online in (False, True):
                with self.subTest(online=online), self.assertRaises(registry.RegistryCancelled):
                    registry.inspect_preview_registry([('cetz', '1.0.0')], online=online,
                                                     cancel_event=cancelled)

    def test_cancel_running_worker_kills_and_reaps_it(self):
        cancelled, started = threading.Event(), threading.Event()
        children = []
        actual_popen = subprocess.Popen
        def spawn(*args, **kwargs):
            process = actual_popen(*args, **kwargs)
            children.append(process)
            started.set()
            return process
        with tempfile.TemporaryDirectory(prefix='registry-cancel-test-') as directory:
            marker = Path(directory) / 'ready'
            worker = ('from pathlib import Path; import time; '
                      f'Path({str(marker)!r}).write_text("ready"); time.sleep(30)')
            def request_cancel():
                deadline = time.monotonic() + 5
                if started.wait(5):
                    while time.monotonic() < deadline and not marker.exists():
                        time.sleep(0.01)
                    if marker.exists():
                        cancelled.set()
            trigger = threading.Thread(target=request_cancel)
            trigger.start()
            try:
                with patch.object(registry, '_WORKER', worker), \
                     patch.object(registry.subprocess, 'Popen', side_effect=spawn), \
                     self.assertRaises(registry.RegistryCancelled):
                    registry.inspect_preview_registry([('cetz', '1.0.0')], online=True,
                                                     cancel_event=cancelled)
            finally:
                trigger.join(timeout=5)
            self.assertTrue(marker.is_file(), 'Synthetic worker never started')
        self.assertFalse(trigger.is_alive())
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].returncode)
        with self.assertRaises(ProcessLookupError):
            os.kill(children[0].pid, 0)

    def test_oversized_active_worker_is_stopped_and_reaped(self):
        children = []
        actual_popen = subprocess.Popen
        def spawn(*args, **kwargs):
            process = actual_popen(*args, **kwargs)
            children.append(process)
            return process
        worker = 'import os,time; os.write(1, b"x" * 65536); time.sleep(30)'
        with patch.object(registry, '_WORKER', worker), \
             patch.object(registry, 'MAX_INDEX_BYTES', 2048), \
             patch.object(registry.subprocess, 'Popen', side_effect=spawn):
            report = registry.inspect_preview_registry([('cetz', '1.0.0')], online=True)
        self.assertEqual(report['packages'][0]['status'], 'unknown')
        self.assertEqual(report['packages'][0]['reason'], 'registry_response_unavailable')
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].returncode)
        with self.assertRaises(ProcessLookupError):
            os.kill(children[0].pid, 0)

    def test_cancel_after_fetch_is_not_reported_as_unknown(self):
        cancelled = threading.Event()
        def fetched(*args):
            cancelled.set()
            return index(('cetz', '1.0.0')), {'etag': None, 'last_modified': None}
        with patch.object(registry, '_fetch_index', side_effect=fetched), \
             self.assertRaises(registry.RegistryCancelled):
            registry.inspect_preview_registry([('cetz', '1.0.0')], online=True,
                                             cancel_event=cancelled)

    def test_cancel_during_fetch_failure_is_not_reported_as_unknown(self):
        cancelled = threading.Event()
        def failed(*args):
            cancelled.set()
            raise registry._RegistryUnavailable('registry_response_unavailable')
        with patch.object(registry, '_fetch_index', side_effect=failed), \
             self.assertRaises(registry.RegistryCancelled):
            registry.inspect_preview_registry([('cetz', '1.0.0')], online=True,
                                             cancel_event=cancelled)

    def test_offline_cancel_during_report_construction_is_not_ignored(self):
        cancelled = threading.Event()
        actual_datetime = registry.datetime
        class Clock:
            @classmethod
            def now(cls, timezone):
                cancelled.set()
                return actual_datetime.now(timezone)
        with patch.object(registry, 'datetime', Clock), \
             self.assertRaises(registry.RegistryCancelled):
            registry.inspect_preview_registry([('cetz', '1.0.0')], cancel_event=cancelled)

    def test_cancel_during_index_iteration_is_not_reported_as_unknown(self):
        cancelled = threading.Event()
        raw = index(*[(f'pkg{i}', '1.0.0') for i in range(300)])
        original = registry._version
        count = 0
        def parse_version(value):
            nonlocal count
            count += 1
            if count == 2:
                cancelled.set()
            return original(value)
        with patch.object(registry, '_fetch_index', return_value=(raw, {'etag': None, 'last_modified': None})), \
             patch.object(registry, '_version', side_effect=parse_version), \
             self.assertRaises(registry.RegistryCancelled):
            registry.inspect_preview_registry([('pkg0', '1.0.0')], online=True,
                                             cancel_event=cancelled)
