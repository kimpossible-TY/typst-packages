"""Package-status CLI uses explicit selections and never installs packages."""
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import package_status_cli
from syntax_client import EXECUTABLE
import typst_lint


def supports_no_lazy_fetch():
    git = shutil.which('git', path='/usr/bin:/bin')
    return bool(git and subprocess.run([git, '--no-lazy-fetch', '--version'],
                                       capture_output=True).returncode == 0)


class PackageStatusCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='typst-package-status-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.project, self.checkout, self.installed, self.cache = (
            self.base / name for name in ('project', 'checkout', 'installed', 'cache'))
        for root in (self.project, self.checkout, self.installed, self.cache):
            root.mkdir()
        source = self.checkout / 'local/example/1.0.0'
        source.mkdir(parents=True)
        (source / 'typst.toml').write_text('[package]\nname = "example"\nversion = "1.0.0"\nentrypoint = "lib.typ"\n')
        (source / 'lib.typ').write_text('#let value = 1\n')
        destination = self.installed / 'local/example/1.0.0'
        destination.parent.mkdir(parents=True)
        shutil.copytree(source, destination)
        self.git('init', '-q')
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
        self.manifest = self.base / 'resources.json'
        self.data = {'roots': {'project': str(self.project), 'packages': str(self.installed), 'cache': str(self.cache)},
                     'files': [{'root': 'packages', 'path': 'local/example/1.0.0/typst.toml', 'role': 'package'},
                               {'root': 'packages', 'path': 'local/example/1.0.0/lib.typ', 'role': 'package'},
                               {'root': 'cache', 'path': 'preview/demo/1.0.0/lib.typ', 'role': 'package'}]}
        self.write_manifest()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.checkout), *args], stderr=subprocess.PIPE).decode().strip()

    def write_manifest(self):
        self.manifest.write_text(json.dumps(self.data), encoding='utf-8')

    def args(self, *extra):
        return ['--root', str(self.project), '--manifest', str(self.manifest), '--checkout', str(self.checkout),
                '--upstream', 'https://github.com/example/packages.git', '--ref', 'refs/heads/main',
                '--format', 'json', *extra]

    def invoke(self, *extra):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = typst_lint.main(['packages-status', *self.args(*extra)])
        return status, json.loads(output.getvalue())

    def test_offline_report_distinguishes_file_evidence_from_freshness(self):
        before = (self.installed / 'local/example/1.0.0/lib.typ').read_bytes()
        with patch('package_freshness._remote', side_effect=AssertionError('no network')), \
             patch('package_freshness_registry._fetch_index', side_effect=AssertionError('no network')):
            status, report = self.invoke()
        self.assertEqual(status, 0)
        self.assertFalse(report['current_runtime_selection_proved'] or report['update_permission'])
        self.assertEqual(report['selection'], 'caller-supplied-manifest')
        self.assertEqual(report['local']['checkout']['relation'], 'unknown')
        self.assertTrue(report['local']['packages'][0]['matches_checkout_snapshot'])
        self.assertIsNone(report['local']['packages'][0]['matches_upstream_tree'])
        self.assertEqual(report['preview_registry']['packages'][0]['status'], 'unknown')
        self.assertEqual(len(report['declared_file_comparisons'][0]['comparison']['files']), 2)
        self.assertEqual((self.installed / 'local/example/1.0.0/lib.typ').read_bytes(), before)

    def test_text_report_labels_unknown_git_evidence(self):
        args = self.args()
        args[args.index('--format') + 1] = 'text'
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = typst_lint.main(['packages-status', *args])
        self.assertEqual(status, 0)
        self.assertIn('Checkout dirty: unknown', output.getvalue())
        self.assertIn('upstream tree match=unknown', output.getvalue())

    @unittest.skipUnless(supports_no_lazy_fetch(), 'Requires Git with --no-lazy-fetch')
    def test_explicit_online_reports_ref_and_registry_separately(self):
        head = self.git('rev-parse', 'HEAD')
        body = json.dumps([{'name': 'demo', 'version': '1.0.0'}, {'name': 'demo', 'version': '1.1.0'}]).encode()
        with patch('package_freshness._remote', return_value=(head, head)) as remote, \
             patch('package_freshness_registry._fetch_index', return_value=(body, {'etag': None, 'last_modified': None})):
            status, report = self.invoke('--online')
        self.assertEqual(status, 0)
        remote.assert_called_once()
        self.assertEqual(report['local']['checkout']['relation'], 'equal')
        self.assertTrue(report['local']['packages'][0]['matches_upstream_tree'])
        self.assertEqual(report['preview_registry']['packages'][0]['status'], 'newer_version_available')
        self.assertFalse(report['preview_registry']['compatibility_proved'])

    def test_unsupported_git_is_unknown_without_remote_lookup(self):
        body = json.dumps([{'name': 'demo', 'version': '1.0.0'}]).encode()
        with patch('package_freshness._git', return_value=(129, b'', b'')), \
             patch('package_freshness._remote', side_effect=AssertionError('no Git network')), \
             patch('package_freshness_registry._fetch_index', return_value=(body, {'etag': None, 'last_modified': None})):
            status, report = self.invoke('--online')
        self.assertEqual(status, 0)
        self.assertFalse(report['local']['checkout']['no_lazy_fetch_supported'])
        self.assertEqual(report['local']['checkout']['relation'], 'unknown')
        self.assertFalse(report['local']['upstream']['network_attempted'])

    @unittest.skipUnless(supports_no_lazy_fetch(), 'Requires Git with --no-lazy-fetch')
    def test_cli_does_not_run_repository_clean_filter(self):
        marker = self.base / 'filter-executed'
        (self.checkout / '.gitattributes').write_text('*.typ filter=probe\n')
        self.git('config', 'filter.probe.clean', f'touch {shlex.quote(str(marker))}; cat')
        source = self.checkout / 'local/example/1.0.0/lib.typ'
        source.write_text('#let value = 2\n')
        status, report = self.invoke()
        self.assertEqual(status, 0)
        self.assertFalse(marker.exists())
        self.assertIsNone(report['local']['checkout']['dirty'])
        item = report['local']['packages'][0]
        self.assertIsNone(item['package_dirty'])
        self.assertFalse(item['checkout_raw_matches_head_tree'])
        self.assertTrue(item['index_matches_head_tree'])

    def test_duplicate_and_unrecognized_selections_never_become_current(self):
        self.data['files'].append(self.data['files'][0])
        self.write_manifest()
        status, result = self.invoke()
        self.assertEqual(status, 2)
        self.assertIn('duplicate', result['error'])
        self.data['files'].pop()
        self.data['files'].append({'root': 'cache', 'path': 'preview/demo/not-semver/file.typ', 'role': 'package'})
        self.write_manifest()
        status, result = self.invoke()
        self.assertEqual(status, 0)
        self.assertEqual(len(result['unrecognized_manifest_entries']), 1)
        self.assertFalse(result['current_runtime_selection_proved'])

    def test_non_string_manifest_root_or_role_is_an_input_error(self):
        for field in ('root', 'role'):
            with self.subTest(field=field):
                original = self.data['files'][0][field]
                self.data['files'][0][field] = []
                self.write_manifest()
                status, result = self.invoke()
                self.assertEqual(status, 2)
                self.assertIn('invalid resource root or role', result['error'])
                self.data['files'][0][field] = original

    def test_unsupported_namespace_and_incomplete_package_path_are_reported(self):
        self.data['files'] = [{'root': 'packages', 'path': 'vendor/foo/v1/lib.typ', 'role': 'package'},
                              {'root': 'packages', 'path': 'local/truncated', 'role': 'package'}]
        self.write_manifest()
        status, result = self.invoke()
        self.assertEqual(status, 0)
        self.assertIsNone(result['local'])
        self.assertEqual({entry['reason'] for entry in result['unrecognized_manifest_entries']},
                         {'unsupported_namespace', 'incomplete_package_path'})
        self.assertFalse(result['current_runtime_selection_proved'])

    def test_missing_installation_does_not_discard_other_results(self):
        shutil.rmtree(self.installed)
        status, result = self.invoke()
        self.assertEqual(status, 0)
        self.assertEqual(result['local']['packages'][0]['installed_path_state'], 'missing')
        self.assertEqual(result['declared_file_comparisons'][0]['status'], 'unknown')
        self.assertEqual(result['preview_registry']['packages'][0]['status'], 'unknown')

    @unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
    def test_discovery_selects_observed_paths_not_unobserved_seed_entries(self):
        (self.project / 'main.typ').write_text('Hello.\n')
        self.data['files'] = [{'root': 'cache', 'path': 'preview/unused/1.0.0/lib.typ', 'role': 'package'}]
        self.write_manifest()
        observed = (('packages', 'local/example/1.0.0/typst.toml'),
                    ('packages', 'local/example/1.0.0/lib.typ'),
                    ('cache', 'preview/demo/1.0.0/lib.typ'))
        selected_fonts = {('packages', 'local/fontpack/1.0.0/fonts/example.ttf'): 'a' * 64}
        with patch('package_status_cli.document_preview_discover.discover',
                   return_value=(b'', observed, selected_fonts, 'f' * 64)) as discovery:
            status, report = self.invoke('--discover-dependencies', '--entrypoint', 'main.typ')
        self.assertEqual(status, 0)
        discovery.assert_called_once()
        self.assertEqual(report['selection'], 'compiler-discovery-paths')
        self.assertEqual(report['discovery']['observed_dependencies'], 3)
        self.assertEqual(report['discovery']['baseline_pdf_sha256'], 'f' * 64)
        self.assertFalse(report['discovery']['compared_bytes_same_as_discovery_proved'])
        self.assertFalse(report['discovery']['selected_fonts_used_proved'])
        self.assertFalse(report['discovery']['dependency_list_exhaustive_proved'])
        self.assertEqual(report['discovery']['selected_fonts'][0]['path'], 'local/fontpack/1.0.0/fonts/example.ttf')
        self.assertEqual([item['name'] for item in report['preview_registry']['packages']], ['demo'])
        self.assertEqual(len(report['local']['packages']), 1)
        self.assertEqual((self.project / 'main.typ').read_text(), 'Hello.\n')

    def test_entrypoint_without_discovery_is_rejected(self):
        status, report = self.invoke('--entrypoint', 'main.typ')
        self.assertEqual(status, 2)
        self.assertIn('required only', report['error'])

    def test_cancellation_during_file_comparison_prevents_registry_lookup(self):
        import offline_compile_cli
        event = threading.Event()
        coordinator = SimpleNamespace(cancel_event=event, first_signal=None)
        original = package_status_cli.package_freshness_observed.compare_observed
        def cancel_after_sample(*args, **kwargs):
            sample = original(*args, **kwargs)
            coordinator.first_signal = int(signal.SIGTERM)
            event.set()
            return sample
        output = io.StringIO()
        with patch.object(offline_compile_cli, '_active_signals', coordinator), \
             patch.object(package_status_cli.package_freshness_observed, 'compare_observed', side_effect=cancel_after_sample), \
             patch.object(package_status_cli.package_freshness_registry, 'inspect_preview_registry') as registry, \
             contextlib.redirect_stdout(output):
            status = typst_lint.main(['packages-status', *self.args()])
        self.assertEqual(status, -int(signal.SIGTERM))
        registry.assert_not_called()

    @unittest.skipUnless(supports_no_lazy_fetch(), 'Git must support --no-lazy-fetch')
    def test_cancellation_during_git_checks_prevents_network_lookups(self):
        import offline_compile_cli
        event = threading.Event()
        coordinator = SimpleNamespace(cancel_event=event, first_signal=None)
        original = package_status_cli.package_freshness._require_local_complete_store

        def cancel_after_check(*args, **kwargs):
            result = original(*args, **kwargs)
            coordinator.first_signal = int(signal.SIGTERM)
            event.set()
            return result

        with patch.object(offline_compile_cli, '_active_signals', coordinator), \
             patch.object(package_status_cli.package_freshness, '_require_local_complete_store', side_effect=cancel_after_check), \
             patch.object(package_status_cli.package_freshness, '_remote') as remote, \
             patch.object(package_status_cli.package_freshness_registry, 'inspect_preview_registry') as registry, \
             contextlib.redirect_stdout(io.StringIO()):
            status = typst_lint.main(['packages-status', *self.args('--online')])
        self.assertEqual(status, -int(signal.SIGTERM))
        remote.assert_not_called()
        registry.assert_not_called()

    def test_registry_cancellation_propagates_to_cli(self):
        import offline_compile_cli
        event = threading.Event()
        coordinator = SimpleNamespace(cancel_event=event, first_signal=None)

        def cancel_registry(*args, **kwargs):
            self.assertIs(kwargs['cancel_event'], event)
            coordinator.first_signal = int(signal.SIGTERM)
            event.set()
            raise package_status_cli.package_freshness_registry.RegistryCancelled('cancelled')

        with patch.object(offline_compile_cli, '_active_signals', coordinator), \
             patch.object(package_status_cli.package_freshness_registry, 'inspect_preview_registry', side_effect=cancel_registry), \
             contextlib.redirect_stdout(io.StringIO()):
            status = typst_lint.main(['packages-status', *self.args()])
        self.assertEqual(status, -int(signal.SIGTERM))

    @unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
    def test_discovery_failure_reports_bounded_compiler_diagnostics(self):
        (self.project / 'main.typ').write_text('Hello.\n')
        failure = package_status_cli.runtime.CompilerFailure('compile failed',
                    SimpleNamespace(stderr=b'missing package\n' + b'x' * 9000))
        with patch('package_status_cli.document_preview_discover.discover', side_effect=failure):
            status, report = self.invoke('--discover-dependencies', '--entrypoint', 'main.typ')
        self.assertEqual(status, 2)
        self.assertTrue(report['compiler_diagnostics'].startswith('missing package'))
        self.assertLessEqual(len(report['compiler_diagnostics']), 8192)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and Path('/usr/bin/pgrep').is_file(),
                         'Opt in on macOS with process inspection available')
    def test_discovery_sigterm_reaps_private_compiler_and_preserves_source(self):
        source = b'#for n in range(5000) [Line #n.]\n'
        entry = self.project / 'main.typ'
        entry.write_bytes(source)
        command = [sys.executable, str(TOOL / 'typst-lint'), 'packages-status',
                   *self.args('--discover-dependencies', '--entrypoint', 'main.typ')]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        child = None
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and process.poll() is None:
                children = subprocess.run(['/usr/bin/pgrep', '-P', str(process.pid)],
                                          capture_output=True, text=True, timeout=3)
                for value in children.stdout.split():
                    info = subprocess.run(['/bin/ps', '-p', value, '-o', 'command='],
                                          capture_output=True, text=True, timeout=3)
                    if '/typst-document-discovery-' in info.stdout or '/usr/bin/sandbox-exec' in info.stdout:
                        child = int(value)
                        break
                if child is not None:
                    break
                time.sleep(0.01)
            self.assertIsNotNone(child, 'A private discovery compiler was not observed')
            os.kill(process.pid, signal.SIGTERM)
            process.communicate(timeout=20)
            self.assertEqual(process.returncode, -int(signal.SIGTERM))
            with self.assertRaises(ProcessLookupError):
                os.kill(child, 0)
            self.assertEqual(entry.read_bytes(), source)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
