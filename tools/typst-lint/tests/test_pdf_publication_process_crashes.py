"""Real SIGKILL/restart evidence for the internal PDF publication protocol.

Each killed process is an exact owned child at a deterministic barrier. These
are process-crash tests, not proof of power-loss durability. All paths are
isolated temporary consumers, and the compiler fixture is explicitly not Typst.
"""
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_pdf_publication_integration import (
    EXPECTED, ORIGINAL, OLD_OUTPUT, EXECUTABLE, _PublicationFixtures, file_state,
)

WORKER = Path(__file__).with_name('pdf_publication_process_worker.py')


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX process-crash protocol')
class PdfPublicationProcessCrashTests(_PublicationFixtures):
    def request(self, f, action, **kwargs):
        return dict(base=str(f.base), root=str(f.root), target=f.target,
                    catalog=str(f.catalog), source_store=str(f.source_store),
                    destination=f.destination, action=action, **kwargs)

    def run_worker(self, f, action, **kwargs):
        result = subprocess.run([sys.executable, str(WORKER),
            json.dumps(self.request(f, action, **kwargs))], capture_output=True,
            text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1, result.stdout)
        return json.loads(result.stdout)

    def kill_worker(self, f, action, boundary, **kwargs):
        child = subprocess.Popen([sys.executable, str(WORKER),
            json.dumps(self.request(f, action, boundary=boundary, **kwargs))],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                ready = selector.select(timeout=90)
                if not ready:
                    self.fail(f'Child did not reach barrier {boundary}')
                line = child.stdout.readline()
            if not line:
                _, error = child.communicate(timeout=5)
                self.fail(f'Child exited before {boundary}: {error}')
            observed = json.loads(line)
            self.assertEqual(observed.get('paused'), boundary, observed)
            child.kill()
            _, error = child.communicate(timeout=5)
            self.assertEqual(child.returncode, -signal.SIGKILL, error)
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)

    def pending_journal(self, f):
        inventory = self.run_worker(f, 'inventory')
        self.assertNotIn('exception', inventory, inventory)
        entries = [entry for entry in inventory['entries'] if entry['pending']]
        self.assertEqual(len(entries), 1, inventory)
        self.assertEqual(self.run_worker(f, 'lock-probe'), {'lock_reacquired': True})
        return entries[0]['journal_id']

    def recover_worker(self, f, journal, **kwargs):
        return self.run_worker(f, 'recover', journal_id=journal, **kwargs)

    def assert_recovered(self, f, journal, *, original_present=True):
        result = self.recover_worker(f, journal)
        self.assertNotIn('exception', result, result)
        self.assertTrue(not result['report']['recovery_required'], result)
        if original_present:
            self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
        else:
            self.assertFalse(f.output.exists())
        self.assertEqual([e for e in self.run_worker(f, 'inventory')['entries'] if e['pending']], [])
        before = file_state(f.output) if f.output.exists() else None
        repeated = self.recover_worker(f, journal)
        self.assertNotIn('exception', repeated, repeated)
        self.assertTrue(not repeated['report']['recovery_required'], repeated)
        self.assertEqual(file_state(f.output) if f.output.exists() else None, before)
        return result

    def test_sigkill_after_prepared_captures_output_before_source_and_releases_root_lock(self):
        for present in (True, False):
            with self.subTest(present=present):
                f = self.fixture(present=present)
                original = file_state(f.source)
                self.kill_worker(f, 'publish', 'after_prepared')
                self.assertEqual(file_state(f.source), original)
                self.assert_recovered(f, self.pending_journal(f), original_present=present)
                self.assertEqual(f.source.read_bytes(), ORIGINAL)

    def test_sigkill_before_publication_rename_retains_committed_fixes_and_old_output(self):
        f = self.fixture()
        old_output = file_state(f.output)
        self.kill_worker(f, 'publish', 'before_replace')
        self.assertEqual(file_state(f.output), old_output)
        self.assert_source_committed(f)
        self.assert_recovered(f, self.pending_journal(f))
        self.assert_source_committed(f)

    def test_sigkill_immediately_after_rename_without_event_is_truthfully_unresolved(self):
        f = self.fixture()
        self.kill_worker(f, 'publish', 'after_replace_before_event')
        self.assertTrue(f.output.read_bytes().startswith(b'%PDF-'))
        saved = file_state(f.output)
        journal = self.pending_journal(f)
        result = self.recover_worker(f, journal)
        self.assertTrue(result.get('report', {}).get('recovery_required', True), result)
        self.assertEqual(file_state(f.output), saved)
        self.assertEqual(self.pending_journal(f), journal)
        self.assert_source_committed(f)

    def test_sigkill_after_recognized_replacement_before_commit_restores_backup(self):
        for boundary in ('after_replaced', 'after_directory_sync_before_commit', 'before_published_event'):
            with self.subTest(boundary=boundary):
                f = self.fixture()
                self.kill_worker(f, 'publish', boundary)
                self.assertTrue(f.output.read_bytes().startswith(b'%PDF-'))
                self.assert_recovered(f, self.pending_journal(f))
                self.assert_source_committed(f)

    def test_sigkill_after_recognized_absent_output_replacement_removes_only_our_output(self):
        f = self.fixture(present=False)
        self.kill_worker(f, 'publish', 'after_replaced')
        self.assert_recovered(f, self.pending_journal(f), original_present=False)
        self.assert_source_committed(f)

    def test_pending_output_journal_blocks_new_combined_session_before_source_validation(self):
        f = self.fixture()
        self.kill_worker(f, 'publish', 'after_prepared')
        journal = self.pending_journal(f)
        source = file_state(f.source)
        result = self.run_worker(f, 'publish')
        self.assertEqual(result.get('exception'), 'PublicationPending', result)
        self.assertEqual(file_state(f.source), source)
        self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
        self.assertEqual(self.pending_journal(f), journal)

    def test_recovery_does_not_need_original_source_configuration_catalog_or_compiler(self):
        f = self.fixture()
        self.kill_worker(f, 'publish', 'after_replaced')
        journal = self.pending_journal(f)
        f.source.write_bytes(b'later invalid source #read("no")\n')
        f.config.write_bytes(b'no longer valid TOML\xff')
        f.catalog.unlink()
        (f.base / 'owned test compiler').unlink()
        source = file_state(f.source)
        config = file_state(f.config)
        self.assert_recovered(f, journal)
        self.assertEqual(file_state(f.source), source)
        self.assertEqual(file_state(f.config), config)
        self.assertFalse(f.catalog.exists())
        self.assertFalse((f.base / 'owned test compiler').exists())

    def test_later_edit_same_bytes_new_inode_symlink_or_hardlink_survives_recovery(self):
        for kind in ('edit', 'same-bytes-new-inode', 'symlink', 'hardlink'):
            with self.subTest(kind=kind):
                f = self.fixture()
                self.kill_worker(f, 'publish', 'after_replaced')
                journal = self.pending_journal(f)
                if kind == 'edit':
                    f.output.write_bytes(b'later exporter output\n')
                elif kind == 'same-bytes-new-inode':
                    replacement = f.output.with_suffix('.later-save')
                    replacement.write_bytes(f.output.read_bytes())
                    replacement.chmod(0o644)
                    os.replace(replacement, f.output)
                elif kind == 'symlink':
                    f.output.unlink()
                    f.output.symlink_to(f.unrelated)
                else:
                    os.link(f.output, f.base / 'later-hardlink')
                before = file_state(f.output)
                result = self.recover_worker(f, journal)
                self.assertTrue(result.get('report', {}).get('recovery_required', True), result)
                self.assertEqual(file_state(f.output), before)
                self.assertEqual(self.pending_journal(f), journal)
                self.assert_source_committed(f)

    def test_sigkill_during_recovery_before_restore_restarts_safely(self):
        for present in (True, False):
            with self.subTest(present=present):
                f = self.fixture(present=present)
                self.kill_worker(f, 'publish', 'after_replaced')
                journal = self.pending_journal(f)
                self.kill_worker(f, 'recover', 'recovery_before_restore', journal_id=journal)
                self.assert_recovered(f, journal, original_present=present)
                self.assert_source_committed(f)

    def test_sigkill_during_live_rollback_before_restore_restarts_safely(self):
        for present in (True, False):
            with self.subTest(present=present):
                f = self.fixture(present=present)
                self.kill_worker(f, 'publish', 'before_restore', fail_point='after_replaced')
                self.assert_recovered(f, self.pending_journal(f), original_present=present)
                self.assert_source_committed(f)

    def test_sigkill_after_absence_restoration_unlink_is_idempotent_and_preserves_later_new_output(self):
        for later_output in (False, True):
            with self.subTest(later_output=later_output):
                f = self.fixture(present=False)
                self.kill_worker(f, 'publish', 'after_replaced')
                journal = self.pending_journal(f)
                self.kill_worker(f, 'recover', 'recovery_after_restore_before_event', journal_id=journal)
                self.assertFalse(f.output.exists())
                if later_output:
                    f.output.write_bytes(b'later exporter owns newly created output\n')
                    f.output.chmod(0o644)
                    before = file_state(f.output)
                    result = self.recover_worker(f, journal)
                    self.assertTrue(result.get('report', {}).get('recovery_required', True), result)
                    self.assertEqual(file_state(f.output), before)
                else:
                    self.assert_recovered(f, journal, original_present=False)
                self.assert_source_committed(f)

    def test_sigkill_after_durable_restoration_event_recovery_does_not_replace_inode(self):
        f = self.fixture()
        self.kill_worker(f, 'publish', 'after_replaced')
        journal = self.pending_journal(f)
        self.kill_worker(f, 'recover', 'recovery_after_restored', journal_id=journal)
        self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
        before = file_state(f.output)
        self.assert_recovered(f, journal)
        self.assertEqual(file_state(f.output), before)
        self.assert_source_committed(f)

    def test_sigkill_after_restoration_before_event_preserves_ambiguity_and_current_output(self):
        f = self.fixture()
        self.kill_worker(f, 'publish', 'after_replaced')
        journal = self.pending_journal(f)
        self.kill_worker(f, 'recover', 'recovery_after_restore_before_event', journal_id=journal)
        self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
        restored = file_state(f.output)
        result = self.recover_worker(f, journal)
        self.assertTrue(result.get('report', {}).get('recovery_required', True), result)
        self.assertEqual(file_state(f.output), restored)
        self.assert_source_committed(f)

    def test_durable_compile_intent_without_observed_outcome_remains_unknown(self):
        f = self.fixture()
        self.kill_worker(f, 'publish', 'before_compile')
        inventory = self.run_worker(f, 'inventory')
        self.assertNotIn('exception', inventory, inventory)
        pending = [entry for entry in inventory['entries'] if entry['pending']]
        self.assertEqual(len(pending), 1)
        self.assertIsNone(pending[0]['compiler_invoked'])
        self.assertIsNone(pending[0]['compiler_status'])
        result = self.assert_recovered(f, pending[0]['journal_id'])
        self.assertIsNone(result['report']['compiler_invoked'])
        self.assertIsNone(result['report']['compiler_status'])
        self.assert_source_committed(f)

    def test_explicit_recovery_requires_exact_destination_named_journal_and_output_lease(self):
        f = self.fixture()
        self.kill_worker(f, 'publish', 'after_replaced')
        journal = self.pending_journal(f)
        before = file_state(f.output)
        for kwargs in ({'destination': 'other.pdf', 'journal_id': journal},
                       {'journal_id': '../outside'}, {'journal_id': 'f' * 32}):
            request = self.request(f, 'recover', **{})
            request.update(kwargs)
            result = subprocess.run([sys.executable, str(WORKER), json.dumps(request)],
                                    capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('exception', json.loads(result.stdout))
            self.assertEqual(file_state(f.output), before)
        self.assert_recovered(f, journal)

    def test_corrupt_oversized_malformed_or_unsafe_journals_preserve_output_and_block_new_session(self):
        for kind in ('invalid-json', 'duplicate-key', 'wrong-kind', 'wrong-schema',
                     'oversized', 'symlink-manifest', 'changed-backup', 'extra-file'):
            with self.subTest(kind=kind):
                f = self.fixture()
                self.kill_worker(f, 'publish', 'after_prepared')
                journal = self.pending_journal(f)
                manifests = list(f.base.glob('.typst-lint-pdf-journals/root-*/*/manifest.json'))
                self.assertEqual(len(manifests), 1)
                manifest = manifests[0]
                if kind == 'invalid-json':
                    manifest.write_bytes(b'{')
                elif kind == 'duplicate-key':
                    manifest.write_bytes(b'{"schema":1,"schema":1}')
                elif kind in ('wrong-kind', 'wrong-schema'):
                    data = json.loads(manifest.read_bytes())
                    data['kind' if kind == 'wrong-kind' else 'schema'] = 'source' if kind == 'wrong-kind' else 99
                    manifest.write_text(json.dumps(data))
                elif kind == 'oversized':
                    manifest.write_bytes(b' ' * (1024 * 1024 + 1))
                elif kind == 'symlink-manifest':
                    target = manifest.with_suffix('.retained')
                    manifest.rename(target)
                    manifest.symlink_to(target)
                elif kind == 'changed-backup':
                    (manifest.parent / 'backup.bin').write_bytes(b'corrupt backup')
                else:
                    (manifest.parent / 'unknown.bin').write_bytes(b'unrecognized journal material')
                before = file_state(f.output), file_state(f.source)
                inventory = self.run_worker(f, 'inventory')
                self.assertNotIn('exception', inventory, inventory)
                pending = [entry for entry in inventory['entries'] if entry['pending']]
                self.assertEqual(len(pending), 1, inventory)
                self.assertEqual(pending[0]['state'], 'corrupt', inventory)
                result = self.recover_worker(f, journal)
                self.assertTrue(result.get('report', {}).get('recovery_required', True), result)
                self.assertEqual((file_state(f.output), file_state(f.source)), before)
                new_session = self.run_worker(f, 'publish')
                self.assertEqual(new_session.get('exception'), 'PublicationPending', new_session)
                self.assertEqual((file_state(f.output), file_state(f.source)), before)

    def test_sigkill_after_durable_commit_never_rolls_back_published_output(self):
        f = self.fixture()
        self.kill_worker(f, 'publish', 'after_published')
        saved = file_state(f.output)
        inventory = self.run_worker(f, 'inventory')
        self.assertNotIn('exception', inventory, inventory)
        self.assertEqual(len(inventory['entries']), 1)
        entry = inventory['entries'][0]
        self.assertFalse(entry['pending'])
        result = self.recover_worker(f, entry['journal_id'])
        self.assertTrue(not result['report']['recovery_required'], result)
        self.assertEqual(file_state(f.output), saved)
        self.assert_source_committed(f)


if __name__ == '__main__':
    unittest.main()
