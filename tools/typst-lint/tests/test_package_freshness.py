"""Read-only Git and whole-package freshness comparisons using owned fixtures."""
from pathlib import Path
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import package_freshness as freshness


def no_lazy_supported():
    binary = shutil.which('git', path='/usr/bin:/bin')
    if binary is None:
        return False
    return subprocess.run([binary, '--no-lazy-fetch', '--version'], capture_output=True).returncode == 0


@unittest.skipUnless(no_lazy_supported(), 'Requires Git with --no-lazy-fetch support')
class FreshnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='typst-freshness-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'checkout'
        self.root.mkdir()
        self.git_env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.base), 'LC_ALL': 'C',
                        'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                        'GIT_TERMINAL_PROMPT': '0'}
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.first = self.root / 'local/first/0.1.0'
        self.second = self.root / 'local/second/0.2.0'
        for path, name, version in ((self.first, 'first', '0.1.0'), (self.second, 'second', '0.2.0')):
            path.mkdir(parents=True)
            (path / 'typst.toml').write_text(f'[package]\nname = "{name}"\nversion = "{version}"\n')
            (path / 'lib.typ').write_text(f'#let value = "{name}"\n')
        (self.root / '.gitignore').write_text('*.cache\n')
        (self.root / 'README.md').write_text('A\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'base')
        self.base_commit = self.git('rev-parse', 'HEAD')
        (self.root / 'README.md').write_text('B\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'docs')
        self.docs_commit = self.git('rev-parse', 'HEAD')
        (self.first / 'lib.typ').write_text('#let value = "changed"\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'package')
        self.package_commit = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-q', '--detach', self.base_commit)
        (self.root / 'README.md').write_text('Divergent\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'divergent')
        self.divergent_commit = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-q', '--detach', self.docs_commit)
        self.installed_first = self.base / 'installed-first'
        self.installed_second = self.base / 'installed-second'
        shutil.copytree(self.first, self.installed_first)
        shutil.copytree(self.second, self.installed_second)
        self.url = 'https://github.com/example/typst-packages'
        self.ref = 'refs/heads/main'

    def git(self, *args):
        run = subprocess.run(['/usr/bin/git', '-C', str(self.root), *args],
                             capture_output=True, text=True, check=True, env=self.git_env)
        return run.stdout.strip()

    def inspect(self, target=None, *, online=True):
        pairs = [(self.first, self.installed_first), (self.second, self.installed_second)]
        if not online:
            with patch.object(freshness, '_remote', side_effect=AssertionError('offline network')):
                return freshness.inspect_freshness_many(self.root, pairs, self.url, self.ref)
        with patch.object(freshness, '_remote', return_value=(target, target)) as remote:
            result = freshness.inspect_freshness_many(self.root, pairs, self.url, self.ref, online=True)
            remote.assert_called_once_with(self.url, self.ref)
            return result

    def test_equal_ref_and_package_trees_with_single_network_query(self):
        report = self.inspect(self.docs_commit)
        self.assertEqual(report['checkout']['relation'], 'equal')
        self.assertIsNone(report['checkout']['dirty'])
        self.assertIn('avoid_git_filters', report['checkout']['dirty_reason'])
        self.assertIsNone(report['checkout']['branch'])
        self.assertEqual(report['upstream']['commit'], self.docs_commit)
        self.assertIsNotNone(report['upstream']['observed_at_utc'])
        self.assertTrue(report['upstream']['network_attempted'])
        self.assertEqual(len(report['packages']), 2)
        for item in report['packages']:
            self.assertTrue(item['matches_checkout_snapshot'] and item['matches_upstream_tree'])
            self.assertFalse(item['runtime_selection_proved'])
            self.assertIsNone(item['package_dirty'])
            self.assertTrue(item['checkout_raw_matches_head_tree'] and item['index_matches_head_tree'])

    def test_commit_relation_and_package_content_are_separate(self):
        report = self.inspect(self.package_commit)
        self.assertEqual(report['checkout']['relation'], 'behind')
        self.assertFalse(report['packages'][0]['matches_upstream_tree'])
        self.assertTrue(report['packages'][1]['matches_upstream_tree'])
        self.assertEqual(self.inspect(self.base_commit)['checkout']['relation'], 'ahead')
        self.assertEqual(self.inspect(self.divergent_commit)['checkout']['relation'], 'diverged')

    def test_unknown_remote_history_and_offline_remain_unknown(self):
        missing = self.inspect('f' * 40)
        self.assertEqual(missing['checkout']['relation'], 'unknown')
        self.assertEqual(missing['checkout']['reason'], 'upstream_oid_unverified_locally')
        self.assertEqual(missing['upstream']['observed_oid'], 'f' * 40)
        self.assertIsNone(missing['upstream']['commit'])
        self.assertTrue(missing['upstream']['network_attempted'])
        self.assertIsNone(missing['packages'][0]['matches_upstream_tree'])
        self.assertTrue(missing['packages'][0]['matches_checkout_snapshot'])
        offline = self.inspect(online=False)
        self.assertEqual(offline['upstream']['reason'], 'offline_not_checked')
        self.assertFalse(offline['upstream']['network_attempted'])
        self.assertIsNone(offline['packages'][0]['matches_upstream_tree'])

    def test_shallow_or_grafted_history_does_not_claim_divergence(self):
        real_output = freshness._output
        def shallow(root, *args):
            if args == ('rev-parse', '--is-shallow-repository'):
                return 'true'
            return real_output(root, *args)
        with patch.object(freshness, '_output', side_effect=shallow):
            report = self.inspect(self.divergent_commit)
        self.assertEqual(report['checkout']['relation'], 'unknown')
        self.assertEqual(report['checkout']['reason'], 'history_incomplete_or_ambiguous')
        grafts = self.root / '.git/info/grafts'
        grafts.write_text('# fixture\n')
        report = self.inspect(self.divergent_commit)
        self.assertEqual(report['checkout']['relation'], 'unknown')
        self.assertEqual(report['checkout']['reason'], 'grafted_history_unverified')

    def test_promisor_clone_never_runs_object_queries_fetch_or_writes_objects(self):
        marker = self.base / 'fetch-marker'
        helper = self.base / 'upload-pack.sh'
        helper.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 1\n')
        helper.chmod(0o755)
        self.git('config', 'remote.origin.url', 'file:///nonexistent/repository')
        self.git('config', 'remote.origin.promisor', 'true')
        self.git('config', 'remote.origin.uploadpack', str(helper))
        objects = self.root / '.git/objects'
        def inventory():
            return {path.relative_to(objects).as_posix(): (path.stat().st_size, path.read_bytes())
                    for path in objects.rglob('*') if path.is_file()}
        before = inventory()
        runner = freshness._git
        with patch.object(freshness, '_git', wraps=runner) as traced, \
             patch.object(freshness, '_remote', side_effect=AssertionError('must not query partial clone')):
            report = freshness.inspect_freshness(self.root, self.first, self.installed_first,
                                                 self.url, self.ref, online=True)
        self.assertTrue(report['checkout']['is_git'])
        self.assertFalse(report['checkout']['metadata_complete'])
        self.assertIn('promisor', report['checkout']['reason'])
        self.assertFalse(report['upstream']['network_attempted'])
        self.assertEqual(before, inventory())
        self.assertFalse(marker.exists())
        forbidden = {'cat-file', 'ls-tree', 'ls-files', 'merge-base', 'fetch', 'status'}
        for call in traced.call_args_list:
            self.assertTrue(forbidden.isdisjoint(call.args[1]))

    def test_promisor_pack_marker_and_alternate_store_are_refused(self):
        pack = self.root / '.git/objects/pack'
        pack.mkdir(exist_ok=True)
        marker = pack / 'pack-fixture.promisor'
        marker.write_bytes(b'')
        report = self.inspect(online=False)
        self.assertFalse(report['checkout']['metadata_complete'])
        self.assertIn('promisor', report['checkout']['reason'])
        marker.unlink()
        alternate = self.root / '.git/objects/info/alternates'
        alternate.parent.mkdir(exist_ok=True)
        alternate.write_text('/unreviewed/object/store\n')
        report = self.inspect(online=False)
        self.assertFalse(report['checkout']['metadata_complete'])
        self.assertIn('alternate', report['checkout']['reason'])

    def test_worktree_specific_promisor_config_is_also_refused(self):
        self.git('config', 'extensions.worktreeConfig', 'true')
        self.git('config', '--worktree', 'remote.fixture.promisor', 'true')
        report = self.inspect(online=False)
        self.assertFalse(report['checkout']['metadata_complete'])
        self.assertIn('promisor', report['checkout']['reason'])

    def test_modified_and_ignored_files_are_never_clean_package_evidence(self):
        (self.first / 'lib.typ').write_text('local change\n')
        report = self.inspect(self.docs_commit)
        self.assertIsNone(report['checkout']['dirty'])
        self.assertIsNone(report['packages'][0]['package_dirty'])
        self.assertFalse(report['packages'][0]['checkout_raw_matches_head_tree'])
        self.assertTrue(report['packages'][0]['index_matches_head_tree'])
        self.assertFalse(report['packages'][0]['matches_checkout_snapshot'])
        self.assertTrue(report['packages'][0]['matches_upstream_tree'])
        self.git('checkout', '--', 'local/first/0.1.0/lib.typ')
        (self.first / 'generated.cache').write_text('ignored\n')
        ignored = self.inspect(self.docs_commit)
        self.assertIsNone(ignored['checkout']['dirty'])
        self.assertIsNone(ignored['packages'][0]['package_dirty'])
        self.assertFalse(ignored['packages'][0]['checkout_raw_matches_head_tree'])
        self.assertFalse(ignored['packages'][0]['matches_checkout_snapshot'])

    def test_staged_index_difference_is_reported_without_dirty_claim(self):
        (self.first / 'lib.typ').write_text('staged change\n')
        self.git('add', 'local/first/0.1.0/lib.typ')
        report = self.inspect(self.docs_commit)
        self.assertIsNone(report['checkout']['dirty'])
        self.assertIsNone(report['packages'][0]['package_dirty'])
        self.assertFalse(report['packages'][0]['checkout_raw_matches_head_tree'])
        self.assertFalse(report['packages'][0]['index_matches_head_tree'])

    def test_index_change_during_check_is_not_reported_as_stable(self):
        real_index = freshness._index_tree
        seen = {}
        def changing(root, relative):
            value = real_index(root, relative)
            seen[relative] = seen.get(relative, 0) + 1
            if relative == 'local/first/0.1.0' and seen[relative] == 2:
                return {}
            return value
        with patch.object(freshness, '_index_tree', side_effect=changing):
            report = self.inspect(online=False)
        self.assertIsNone(report['packages'][0]['index_matches_head_tree'])
        self.assertEqual(report['packages'][0]['package_dirty_reason'], 'index_changed_or_recheck_failed')
        self.assertTrue(report['packages'][1]['index_matches_head_tree'])

    def test_extra_or_changed_installed_files_are_not_upstream_equivalent(self):
        (self.installed_first / 'extra.typ').write_text('extra')
        (self.installed_second / 'lib.typ').write_text('changed')
        result = self.inspect(self.docs_commit)
        self.assertTrue(all(not row['matches_checkout_snapshot'] and not row['matches_upstream_tree']
                            for row in result['packages']))
        self.assertEqual(result['packages'][0]['installed_manifest'], {'name': 'first', 'version': '0.1.0'})

    def test_modes_and_symlinks_are_not_silently_treated_as_equal(self):
        (self.installed_first / 'lib.typ').chmod(0o755)
        result = self.inspect(self.docs_commit)
        self.assertFalse(result['packages'][0]['matches_upstream_tree'])
        self.assertFalse(result['packages'][0]['matches_checkout_snapshot'])
        (self.installed_second / 'linked.typ').symlink_to(self.installed_first / 'lib.typ')
        result = self.inspect(self.docs_commit)
        self.assertIsNone(result['packages'][1]['matches_upstream_tree'])
        self.assertIn('symlink', result['packages'][1]['reason'])

    def test_all_execute_bits_must_match_a_git_executable(self):
        (self.first / 'lib.typ').chmod(0o755)
        self.git('add', 'local/first/0.1.0/lib.typ')
        self.git('commit', '-qm', 'executable')
        commit = self.git('rev-parse', 'HEAD')
        (self.installed_first / 'lib.typ').chmod(0o744)
        different = self.inspect(commit)
        self.assertFalse(different['packages'][0]['matches_upstream_tree'])
        self.assertFalse(different['packages'][0]['matches_checkout_snapshot'])
        (self.installed_first / 'lib.typ').chmod(0o755)
        equal = self.inspect(commit)
        self.assertTrue(equal['packages'][0]['matches_upstream_tree'])
        self.assertTrue(equal['packages'][0]['matches_checkout_snapshot'])

    def test_failed_remote_keeps_local_comparison_but_not_latest_claim(self):
        with patch.object(freshness, '_remote', side_effect=freshness.FreshnessError('offline or unavailable')):
            report = freshness.inspect_freshness(self.root, self.first, self.installed_first,
                                                 self.url, self.ref, online=True)
        self.assertEqual(report['upstream']['reason'], 'offline or unavailable')
        self.assertIsNone(report['upstream']['network_attempted'])
        self.assertEqual(report['checkout']['relation'], 'unknown')
        self.assertTrue(report['packages'][0]['matches_checkout_snapshot'])
        self.assertIsNone(report['packages'][0]['matches_upstream_tree'])

    def test_remote_process_start_failure_does_not_claim_a_network_attempt(self):
        real_git = freshness._git
        def fail_start(root, arguments, **kwargs):
            if kwargs.get('network'):
                raise freshness.FreshnessError('Git could not start')
            return real_git(root, arguments, **kwargs)
        with patch.object(freshness, '_git', side_effect=fail_start):
            report = freshness.inspect_freshness(self.root, self.first, self.installed_first,
                                                 self.url, self.ref, online=True)
        self.assertIsNone(report['upstream']['network_attempted'])
        self.assertIsNone(report['upstream']['observed_oid'])
        self.assertIsNone(report['upstream']['commit'])
        self.assertEqual(report['upstream']['reason'], 'Git could not start')

    def test_observed_tag_object_is_not_misreported_as_its_peeled_commit(self):
        self.git('tag', '-a', 'fixture-tag', '-m', 'fixture')
        tag = self.git('rev-parse', 'refs/tags/fixture-tag')
        self.ref = 'refs/tags/fixture-tag'
        report = self.inspect(tag)
        self.assertEqual(report['upstream']['observed_oid'], tag)
        self.assertEqual(report['upstream']['ref_object'], tag)
        self.assertEqual(report['upstream']['commit'], self.docs_commit)
        self.assertEqual(report['checkout']['relation'], 'equal')

    def test_remote_success_is_preserved_when_local_object_lookup_fails(self):
        real_git = freshness._git
        def fail_local(root, arguments, **kwargs):
            if arguments == ['rev-parse', '--verify', self.docs_commit + '^{commit}']:
                raise freshness.FreshnessError('local object check unavailable')
            return real_git(root, arguments, **kwargs)
        with patch.object(freshness, '_git', side_effect=fail_local):
            report = self.inspect(self.docs_commit)
        self.assertTrue(report['upstream']['network_attempted'])
        self.assertEqual(report['upstream']['observed_oid'], self.docs_commit)
        self.assertIsNone(report['upstream']['commit'])
        self.assertIsNone(report['upstream']['reason'])
        self.assertEqual(report['checkout']['reason'], 'local object check unavailable')

    def test_remote_oid_is_not_called_a_commit_without_local_commit_resolution(self):
        blob = self.git('hash-object', '-w', 'local/first/0.1.0/typst.toml')
        report = self.inspect(blob)
        self.assertTrue(report['upstream']['network_attempted'])
        self.assertEqual(report['upstream']['observed_oid'], blob)
        self.assertIsNone(report['upstream']['commit'])
        self.assertEqual(report['checkout']['reason'], 'upstream_oid_unverified_locally')
        self.assertIsNone(report['packages'][0]['matches_upstream_tree'])

    def test_cancelled_before_start_never_calls_git_or_network(self):
        event = threading.Event()
        event.set()
        with patch.object(freshness, '_git') as git, patch.object(freshness, '_remote') as remote:
            with self.assertRaises(freshness.FreshnessCancelled):
                freshness.inspect_freshness(self.root, self.first, self.installed_first,
                                            self.url, self.ref, online=True, cancel_event=event)
        git.assert_not_called()
        remote.assert_not_called()

    def test_cancel_after_first_git_stops_before_remote_or_capture(self):
        event = threading.Event()
        real_git = freshness._git
        def cancelling(root, arguments, **kwargs):
            result = real_git(root, arguments, **kwargs)
            event.set()
            return result
        with patch.object(freshness, '_git', side_effect=cancelling) as git, \
             patch.object(freshness, '_remote') as remote, patch.object(freshness, '_capture') as capture:
            with self.assertRaises(freshness.FreshnessCancelled):
                freshness.inspect_freshness(self.root, self.first, self.installed_first,
                                            self.url, self.ref, online=True, cancel_event=event)
        self.assertEqual(git.call_count, 1)
        remote.assert_not_called()
        capture.assert_not_called()

    def test_cancel_after_remote_stops_before_object_or_package_work(self):
        event = threading.Event()
        def cancelling(*args):
            event.set()
            return self.docs_commit, self.docs_commit
        real_git = freshness._git
        with patch.object(freshness, '_remote', side_effect=cancelling) as remote, \
             patch.object(freshness, '_git', wraps=real_git) as git, patch.object(freshness, '_capture') as capture:
            with self.assertRaises(freshness.FreshnessCancelled):
                freshness.inspect_freshness(self.root, self.first, self.installed_first,
                                            self.url, self.ref, online=True, cancel_event=event)
        remote.assert_called_once()
        capture.assert_not_called()
        self.assertFalse(any('cat-file' in call.args[1] or 'ls-tree' in call.args[1]
                             or ('rev-parse' in call.args[1] and self.docs_commit + '^{commit}' in call.args[1])
                             for call in git.call_args_list))

    def test_cancel_after_capture_does_not_start_next_package_or_tree(self):
        event = threading.Event()
        real_capture = freshness._capture
        def cancelling(*args):
            result = real_capture(*args)
            event.set()
            return result
        with patch.object(freshness, '_capture', side_effect=cancelling) as capture, \
             patch.object(freshness, '_tree') as tree:
            with self.assertRaises(freshness.FreshnessCancelled):
                freshness.inspect_freshness_many(self.root,
                    [(self.first, self.installed_first), (self.second, self.installed_second)],
                    self.url, self.ref, cancel_event=event)
        self.assertEqual(capture.call_count, 1)
        tree.assert_not_called()

    def test_cancel_reaps_an_active_git_child(self):
        marker = self.base / 'git-started'
        fake_git = self.base / 'slow-git'
        fake_git.write_text(f'#!/bin/sh\ntouch {shlex.quote(str(marker))}\nsleep 30\n')
        fake_git.chmod(0o755)
        event = threading.Event()
        failures, processes, reset = [], [], []
        real_popen = subprocess.Popen
        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process
        def run():
            try:
                freshness.inspect_freshness(self.root, self.first, self.installed_first,
                                            self.url, self.ref, online=True, cancel_event=event)
            except BaseException as error:
                failures.append(error)
            finally:
                reset.append(freshness._CANCEL_EVENT.get())
        with patch.object(freshness.shutil, 'which', return_value=str(fake_git)), \
             patch.object(freshness.subprocess, 'Popen', side_effect=spawn), \
             patch.object(freshness, '_remote') as remote:
            worker = threading.Thread(target=run)
            worker.start()
            try:
                deadline = time.monotonic() + 5
                while not marker.exists() and worker.is_alive() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(marker.exists(), 'Git child did not start')
                event.set()
                worker.join(5)
                self.assertFalse(worker.is_alive(), 'Git cancellation did not finish')
                self.assertEqual(len(failures), 1)
                self.assertIsInstance(failures[0], freshness.FreshnessCancelled)
                self.assertTrue(processes and all(process.poll() is not None for process in processes))
                self.assertEqual(reset, [None])
                remote.assert_not_called()
            finally:
                event.set()
                worker.join(5)

    def test_non_git_or_checkout_package_outside_checkout_is_not_proven(self):
        plain = self.base / 'plain'
        plain.mkdir()
        report = freshness.inspect_freshness(plain, self.first, self.installed_first, self.url, self.ref)
        self.assertFalse(report['checkout']['is_git'])
        self.assertEqual(report['checkout']['relation'], 'unknown')
        outside = freshness.inspect_freshness(self.root, self.installed_first, self.installed_first,
                                             self.url, self.ref)
        self.assertEqual(outside['packages'][0]['reason'], 'checkout_package_not_inside_checkout')
        self.assertIsNone(outside['packages'][0]['matches_upstream_tree'])

    def test_unborn_git_and_changed_checkout_never_claim_current(self):
        unborn = self.base / 'unborn'
        unborn.mkdir()
        subprocess.run(['/usr/bin/git', '-C', str(unborn), 'init', '-q'], check=True, env=self.git_env)
        with patch.object(freshness, '_remote', side_effect=AssertionError('no HEAD must not query')):
            report = freshness.inspect_freshness(unborn, self.first, self.installed_first,
                                                 self.url, self.ref, online=True)
        self.assertTrue(report['checkout']['is_git'])
        self.assertFalse(report['checkout']['metadata_complete'])
        self.assertEqual(report['checkout']['relation'], 'unknown')
        self.assertFalse(report['upstream']['network_attempted'])
        real_output = freshness._output
        count = [0]
        def changing(root, *args):
            if args == ('rev-parse', '--verify', 'HEAD^{commit}'):
                count[0] += 1
                if count[0] == 2:
                    return self.package_commit
            return real_output(root, *args)
        with patch.object(freshness, '_output', side_effect=changing):
            report = self.inspect(self.docs_commit)
        self.assertFalse(report['checkout']['metadata_complete'])
        self.assertIsNone(report['checkout']['dirty'])
        self.assertEqual(report['checkout']['relation'], 'unknown')
        self.assertEqual(report['checkout']['reason'], 'checkout_changed_or_recheck_failed')

    def test_batch_budget_failure_remains_explicit_for_later_packages(self):
        with patch.object(freshness, 'MAX_BATCH_BYTES', 1):
            report = self.inspect(online=False)
        self.assertIn('byte limit', report['packages'][0]['reason'])
        self.assertEqual(report['packages'][1]['reason'], 'package_batch_byte_limit_exceeded')
        self.assertIsNone(report['packages'][1]['matches_upstream_tree'])

    def test_timeout_is_reported_as_bounded_failure(self):
        with patch.object(freshness, 'GIT_TIMEOUT', 0.00000001), self.assertRaises(freshness.FreshnessError):
            freshness._git(self.root, ['--version'])

    def test_remote_tag_peeling_and_ambiguous_responses(self):
        tag, commit = 'a' * 40, 'b' * 40
        ref = 'refs/tags/v1.0.0'
        listing = f'{tag}\t{ref}\n{commit}\t{ref}^{{}}\n'.encode()
        with patch.object(freshness, '_git', return_value=(0, listing, b'')) as runner:
            self.assertEqual(freshness._remote(self.url, ref), (commit, tag))
        self.assertTrue(runner.call_args.kwargs['network'])
        with patch.object(freshness, '_git', return_value=(0, listing + listing, b'')):
            with self.assertRaises(freshness.FreshnessError):
                freshness._remote(self.url, ref)

    def test_missing_selected_directories_do_not_erase_other_package_results(self):
        absent = self.base / 'not-installed'
        pairs = [(self.first, absent), (self.second, self.installed_second),
                 (self.base / 'missing-checkout', self.installed_first)]
        with patch.object(freshness, '_remote', return_value=(self.docs_commit, self.docs_commit)) as remote:
            report = freshness.inspect_freshness_many(self.root, pairs, self.url, self.ref, online=True)
        remote.assert_called_once()
        self.assertEqual(report['packages'][0]['installed_path_state'], 'missing')
        self.assertIsNone(report['packages'][0]['matches_upstream_tree'])
        self.assertTrue(report['packages'][1]['matches_upstream_tree'])
        self.assertEqual(report['packages'][2]['checkout_path_state'], 'missing')

    def test_selected_root_symlinks_are_resolved_but_inner_links_refused(self):
        root_link = self.base / 'selected-link'
        root_link.symlink_to(self.installed_first, target_is_directory=True)
        report = freshness.inspect_freshness(self.root, self.first, root_link, self.url, self.ref)
        self.assertEqual(report['packages'][0]['installed_path'], str(root_link))
        self.assertEqual(report['packages'][0]['resolved_installed_path'], str(self.installed_first))
        self.assertTrue(report['packages'][0]['matches_checkout_snapshot'])
        self.assertTrue(any('symlinks are resolved' in message for message in report['limitations']))

    def test_git_status_does_not_execute_configured_fsmonitor(self):
        marker = self.base / 'marker'
        monitor = self.base / 'monitor.sh'
        monitor.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 0\n')
        monitor.chmod(0o755)
        self.git('config', 'core.fsmonitor', str(monitor))
        report = self.inspect(online=False)
        self.assertTrue(report['checkout']['is_git'])
        self.assertFalse(marker.exists())

    def test_repository_attributes_cannot_execute_clean_or_process_filters(self):
        attributes = self.root / '.gitattributes'
        attributes.write_text('*.typ filter=fixture\n')
        self.git('add', '.gitattributes')
        self.git('commit', '-qm', 'attributes')
        info = self.root / '.git/info/attributes'
        info.write_text('typst.toml filter=infofixture\n')
        markers = [self.base / f'filter-{index}' for index in range(4)]
        self.git('config', 'filter.fixture.clean', f'touch {shlex.quote(str(markers[0]))}; cat')
        self.git('config', 'filter.infofixture.clean', f'touch {shlex.quote(str(markers[1]))}; cat')
        for path in (self.first / 'lib.typ', self.first / 'typst.toml'):
            path.write_text(path.read_text().replace('first', 'FIRST'))
        self.git('status', '--porcelain=v1')
        self.assertTrue(markers[0].exists() and markers[1].exists(),
                        'Fixture must reproduce Git status executing both clean filters')
        for marker in markers:
            marker.unlink(missing_ok=True)
        self.git('config', 'filter.fixture.process', f'touch {shlex.quote(str(markers[2]))}; exit 1')
        self.git('config', 'filter.infofixture.process', f'touch {shlex.quote(str(markers[3]))}; exit 1')
        runner = freshness._git
        with patch.object(freshness, '_git', wraps=runner) as traced:
            report = self.inspect(online=False)
        self.assertTrue(all(not marker.exists() for marker in markers))
        self.assertIsNone(report['checkout']['dirty'])
        self.assertIsNone(report['packages'][0]['package_dirty'])
        self.assertFalse(report['packages'][0]['checkout_raw_matches_head_tree'])
        self.assertTrue(report['packages'][0]['index_matches_head_tree'])
        for call in traced.call_args_list:
            self.assertTrue({'status', 'diff', 'diff-files', 'diff-index', 'check-attr'}.isdisjoint(call.args[1]))

    def test_attributes_and_filter_config_changes_during_check_are_never_executed(self):
        marker = self.base / 'racing-filter-ran'
        real_index = freshness._index_tree
        mutated = [False]
        def changing(root, relative):
            if not mutated[0]:
                (self.root / '.gitattributes').write_text('*.typ filter=racing\n')
                (self.root / '.git/info/attributes').write_text('typst.toml filter=racing\n')
                self.git('config', 'filter.racing.clean', f'touch {shlex.quote(str(marker))}; cat')
                self.git('config', 'filter.racing.process', f'touch {shlex.quote(str(marker))}; exit 1')
                mutated[0] = True
            return real_index(root, relative)
        with patch.object(freshness, '_index_tree', side_effect=changing):
            report = self.inspect(online=False)
        self.assertTrue(mutated[0])
        self.assertFalse(marker.exists())
        self.assertIsNone(report['checkout']['dirty'])
        self.assertIsNone(report['packages'][0]['package_dirty'])
        self.assertTrue(report['packages'][0]['checkout_raw_matches_head_tree'])
        self.assertTrue(report['packages'][0]['index_matches_head_tree'])

    def test_upstream_input_is_strict_and_credentials_are_not_inherited(self):
        for url in ('https://token@github.com/example/repo', 'https://github.com.evil/example/repo',
                    'https://github.com/example/repo?secret=yes', 'file:///tmp/repo',
                    'https://github.com/example/repo/extra', 'https://github.com/example/../x'):
            with self.subTest(url=url), self.assertRaises(freshness.FreshnessError):
                freshness.inspect_freshness(self.root, self.first, self.installed_first, url, self.ref)
        for ref in ('HEAD', 'main', 'refs/heads/a..b', 'refs/heads/a.lock', 'refs/heads/x//y',
                    'refs/tags/v1^{}', 'refs/heads/x;touch bad'):
            with self.subTest(ref=ref), self.assertRaises(freshness.FreshnessError):
                freshness.inspect_freshness(self.root, self.first, self.installed_first, self.url, ref)
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'fixture-secret', 'HTTPS_PROXY': 'fixture-proxy'}), \
             patch.object(freshness.subprocess, 'Popen', wraps=subprocess.Popen) as popen:
            code, _, _ = freshness._git(None, ['--version'], network=True)
        self.assertEqual(code, 0)
        env = popen.call_args.kwargs['env']
        self.assertNotIn('GITHUB_TOKEN', env)
        self.assertNotIn('HTTPS_PROXY', env)
        self.assertEqual(env['GIT_TERMINAL_PROMPT'], '0')
        self.assertEqual(env['GIT_NO_LAZY_FETCH'], '1')
        self.assertIn('--no-lazy-fetch', popen.call_args.args[0])
        self.assertNotEqual(popen.call_args.kwargs['cwd'], str(self.root))


class FreshnessUnsupportedGitTests(unittest.TestCase):
    def test_old_git_stays_unknown_without_network_or_object_queries(self):
        with tempfile.TemporaryDirectory(prefix='typst-freshness-old-git-') as temporary:
            root = Path(temporary).resolve()
            package = root / 'package'
            package.mkdir()
            (package / 'typst.toml').write_text('[package]\nname="x"\nversion="0.1.0"\n')
            with patch.object(freshness, '_git', return_value=(129, b'', b'unknown option: --no-lazy-fetch')) as runner, \
                 patch.object(freshness, '_remote', side_effect=AssertionError('no supported Git')):
                report = freshness.inspect_freshness(root, package, package,
                    'https://github.com/example/repo', 'refs/heads/main', online=True)
            runner.assert_called_once()
            self.assertFalse(report['checkout']['no_lazy_fetch_supported'])
            self.assertFalse(report['checkout']['metadata_complete'])
            self.assertEqual(report['checkout']['relation'], 'unknown')
            self.assertFalse(report['upstream']['network_attempted'])
            self.assertTrue(report['packages'][0]['matches_checkout_snapshot'])
            self.assertIsNone(report['packages'][0]['matches_upstream_tree'])


if __name__ == '__main__':
    unittest.main()
