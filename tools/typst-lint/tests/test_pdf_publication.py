"""Focused storage protocol tests, using owned temporary outputs only."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import pdf_publication_store as ps


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / 'consumer'
        self.root.mkdir()
        (self.root / 'nested with spaces').mkdir()
        self.destination = 'nested with spaces/old file.pdf'
        self.output = self.root / self.destination
        self.output.write_bytes(b'not initially a PDF\x00\xff')
        self.output.chmod(0o644)
        self.lease = ps.OfflineOutputExclusive.assert_exporters_watchers_closed_and_no_other_writers()

    def prepare(self, **kwargs):
        store = ps._Store(self.root, create=True, **kwargs)
        store.__enter__()
        self.addCleanup(store.close)
        store.bind_destination(self.destination)
        store.prepare()
        return store

    def compile_and_stage(self, store):
        raw = b'%PDF-1.7\nowned stage\n%%EOF\n'
        store.event('compile_launch_intent', source_sha256='a'*64, source_journal_id=None,
                    profile='embedded-ascii-v1', source_target='input.typ',
                    config_sha256='c'*64, catalog_sha256='d'*64)
        store.event('compiled', compiler_sha256='b'*64, pdf_sha256=ps._hash(raw),
            compiler_invoked=True, compiler_status=0,
            invocations=[dict(purpose='compile', started=True, returncode=0, lifecycle='exited')])
        return store.stage(raw)

    def test_inventory_absent_store_is_read_only(self):
        self.assertEqual(ps.inventory_publications(self.root), ())
        self.assertFalse((self.base / ps.STORE_NAME).exists())

    def test_old_arbitrary_bytes_and_mode_are_retained(self):
        store = self.prepare()
        self.assertEqual(store.old.sha256, ps._hash(self.output.read_bytes()))
        backup = store.namespace / store.journal_id / 'backup.bin'
        self.assertEqual(backup.read_bytes(), self.output.read_bytes())
        self.assertEqual(store.inventory()[0].state, 'prepared')
        self.assertTrue(store.inventory()[0].pending)

    def test_standalone_lock_cannot_overlap_borrowed_storage(self):
        self.prepare()
        with self.assertRaises(ps.PublicationError):
            ps.inventory_publications(self.root)

    def test_existing_success_and_terminal_inventory(self):
        store = self.prepare()
        stage = self.compile_and_stage(store)
        post = store.replace(stage, lambda: None)
        self.assertEqual(post.mode, 0o644)
        self.assertEqual(self.output.read_bytes(), b'%PDF-1.7\nowned stage\n%%EOF\n')
        self.assertEqual(store.inventory()[0].state, 'published')
        self.assertFalse(store.inventory()[0].pending)
        journal = store.journal_id
        store.close()
        result = ps.recover_publication(self.root, destination=self.destination,
            journal_id=journal, lease=self.lease)
        self.assertEqual(result.output_disposition, 'published')
        self.assertEqual(self.output.read_bytes(), b'%PDF-1.7\nowned stage\n%%EOF\n')

    def test_absent_success_new_mode_0600(self):
        self.output.unlink()
        store = self.prepare()
        self.assertIsNone(store.old)
        stage = self.compile_and_stage(store)
        store.replace(stage, lambda: None)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)

    def test_explicit_recovery_prepared_is_idempotent_no_rewrite(self):
        original = self.output.stat().st_ino
        store = self.prepare()
        journal = store.journal_id
        store.close()
        result = ps.recover_publication(self.root, destination=self.destination,
            journal_id=journal, lease=self.lease)
        self.assertEqual(result.output_disposition, 'untouched')
        self.assertFalse(result.recovery_required)
        self.assertEqual(self.output.stat().st_ino, original)
        again = ps.recover_publication(self.root, destination=self.destination,
            journal_id=journal, lease=self.lease)
        self.assertEqual(again.phase, 'restored')

    def test_failed_parent_sync_restore_exact_owned_postimage(self):
        old = self.output.read_bytes()
        store = self.prepare()
        stage = self.compile_and_stage(store)
        def fail(point, path):
            if point == 'destination_directory_fsync':
                raise OSError('owned injected sync failure')
        store.fault = fail
        with self.assertRaises(OSError):
            store.replace(stage, lambda: None)
        self.assertNotEqual(self.output.read_bytes(), old)
        store.fault = None
        restored = store.restore(store.postimage)
        self.assertEqual(restored.sha256, ps._hash(old))
        self.assertEqual(self.output.read_bytes(), old)
        self.assertEqual(store.inventory()[0].state, 'restored')

    def test_recovery_preserves_same_bytes_new_inode(self):
        store = self.prepare()
        journal = store.journal_id
        old = self.output.read_bytes()
        replacement = self.output.with_name('new.pdf')
        replacement.write_bytes(old)
        replacement.chmod(0o644)
        replacement.replace(self.output)
        inode = self.output.stat().st_ino
        store.close()
        result = ps.recover_publication(self.root, destination=self.destination,
            journal_id=journal, lease=self.lease)
        self.assertTrue(result.recovery_required)
        self.assertEqual(self.output.stat().st_ino, inode)
        self.assertEqual(self.output.read_bytes(), old)

    def test_pending_corruption_and_backup_tampering_remain_pending(self):
        store = self.prepare()
        backup = store.namespace / store.journal_id / 'backup.bin'
        backup.write_bytes(b'tampered')
        entry = store.inventory()[0]
        self.assertTrue(entry.pending)
        self.assertEqual(entry.state, 'corrupt')

    def test_contradictory_completion_is_corrupt(self):
        store = self.prepare()
        stage = self.compile_and_stage(store)
        store.replace(stage, lambda: None)
        path = store.namespace / store.journal_id / 'event-0006.json'
        data = json.loads(path.read_bytes())
        self.assertEqual(data['state'], 'published')
        data['postimage']['sha256'] = '0'*64
        path.write_text(json.dumps(data))
        self.assertEqual(store.inventory()[0].state, 'corrupt')

    def test_terminal_uncertain_ack_recovery_resync_failure_and_retry_preserve_later_edit(self):
        store = self.prepare()
        stage = self.compile_and_stage(store)
        def fail_commit_sync(point, path):
            if point == 'event_directory_fsync' and path == 'published':
                raise OSError('owned uncertain completion acknowledgment')
        store.fault = fail_commit_sync
        with self.assertRaises(OSError):
            store.replace(stage, lambda: None)
        journal = store.journal_id
        store.fault = None
        self.assertEqual(store.inventory()[0].state, 'published')
        store.close()
        self.output.write_bytes(b'later user work')
        inode = self.output.stat().st_ino
        for failed_point in ('recovery_terminal_file_fsync', 'recovery_terminal_directory_fsync'):
            def fail_terminal(point, path):
                if point == failed_point:
                    raise OSError('owned recovery terminal sync failure')
            result = ps.recover_publication(self.root, destination=self.destination,
                journal_id=journal, lease=self.lease, fault=fail_terminal)
            self.assertTrue(result.recovery_required)
            self.assertEqual(result.output_disposition, 'uncertain')
            self.assertEqual(self.output.read_bytes(), b'later user work')
            self.assertEqual(self.output.stat().st_ino, inode)
        result = ps.recover_publication(self.root, destination=self.destination,
            journal_id=journal, lease=self.lease)
        self.assertFalse(result.recovery_required)
        self.assertEqual(result.phase, 'published')
        self.assertIsNone(result.observed_sha256)
        self.assertIn('Historical', result.reason)
        self.assertEqual(self.output.read_bytes(), b'later user work')
        self.assertEqual(self.output.stat().st_ino, inode)

    def test_old_specific_bound_refuses_before_payload_read(self):
        limits = replace(ps.DEFAULT_PUBLICATION_LIMITS, max_old_pdf_bytes=1)
        with ps._Store(self.root, create=True, limits=limits) as store:
            store.bind_destination(self.destination)
            with mock.patch.object(ps.os, 'read', side_effect=AssertionError('must reject before read')):
                with self.assertRaises(ps.PublicationError):
                    store.prepare()

    def test_supported_metadata_fidelity_and_extra_xattr_refusal(self):
        from transactions import _copy_xattrs, _xattrs
        fd = os.open(self.output, os.O_RDONLY)
        try:
            original = _xattrs(fd)
        finally:
            os.close(fd)
        store = self.prepare()
        stage = self.compile_and_stage(store)
        def stop_after_replace(point, path):
            if point == 'after_replaced':
                raise OSError('owned rollback trigger')
        store.fault = stop_after_replace
        with self.assertRaises(OSError):
            store.replace(stage, lambda: None)
        store.fault = None
        restored = store.restore(store.postimage)
        self.assertEqual(restored.xattrs, original)
        fd = os.open(self.output, os.O_RDONLY)
        try:
            self.assertEqual(_xattrs(fd), original)
        finally:
            os.close(fd)

    def test_limits_and_selection_fail_closed(self):
        for value in (True,-1,1.2):
            with self.subTest(value=value), self.assertRaises(ps.PublicationError):
                ps.PublicationLimits(max_new_pdf_bytes=value)
        for destination in ('/tmp/x.pdf','x/../x.pdf','x//x.pdf','x.PDF','x.pdf/','*.pdf','-'):
            with self.subTest(destination=destination), self.assertRaises(ps.PublicationError):
                ps.literal_destination(destination)
        with self.assertRaises(ps.PublicationError):
            self.prepare(limits=replace(ps.DEFAULT_PUBLICATION_LIMITS,max_old_pdf_bytes=1))

    def test_event_schemas_and_live_transitions_are_closed(self):
        store = self.prepare()
        with self.assertRaises(ps.PublicationError):
            store.event('published', postimage=store.old.json())
        with self.assertRaises(ps.PublicationError):
            store.event('recovery_required', reason='x', arbitrary='field')

    def test_missing_lease_and_exact_recovery_allowlist(self):
        store = self.prepare()
        journal = store.journal_id
        store.close()
        with self.assertRaises(ps.PublicationError):
            ps.recover_publication(self.root, destination=self.destination, journal_id=journal, lease=None)
        with self.assertRaises(ps.PublicationError):
            ps.recover_publication(self.root, destination='other.pdf', journal_id=journal, lease=self.lease)


if __name__ == '__main__':
    unittest.main()
