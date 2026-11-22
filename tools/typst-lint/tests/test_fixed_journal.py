"""Fixed-store and read-only inventory checks using temporary consumers only."""
from dataclasses import replace
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import MappingProxyType
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fixed_journal
from fixed_journal import (JournalInventoryError, fixed_journal_directory,
                           inventory_json, journal_inventory, recovery_report_json)
from edit_plan import Edit
import transactions
from transactions import (EvidenceInput, GraphAnalysis, OfflineExclusive,
                          ProtectedTransaction, ReadLimits, RecoveryRequired,
                          SourceEdit, TransactionError)

RULES = frozenset({'fixture.fixed-journal'})


def analyze(graph):
    edits = tuple(SourceEdit(path, Edit(0, 3, b'good'))
                  for path, source in graph.sources.items() if source.startswith(b'bad'))
    return GraphAnalysis(RULES, RULES, frozenset(graph.sources), frozenset(graph.evidence),
                         findings=frozenset(edit.path for edit in edits), edits=edits)


class Crash(BaseException):
    pass


@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX metadata protocol')
class FixedJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'consumer'
        self.root.mkdir()
        self.source = self.root / 'main.typ'
        self.source.write_bytes(b'bad\n')
        self.store = fixed_journal_directory(self.root)
        self.lease = OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers()

    def context(self, **kwargs):
        options = dict(journal_directory=self.store, lease=self.lease,
                       create_journal_directory=True)
        options.update(kwargs)
        return ProtectedTransaction(self.root, ('main.typ',), **options)

    def apply(self, **kwargs):
        with self.context(**kwargs) as tx:
            return tx.apply(tx.validate(analyze, required_rules=RULES))

    def namespace(self):
        return self.store / ('root-' + transactions._hash(os.fsencode(self.root)))

    def crash(self, *, evidence=()):
        def die(point, path):
            if point == 'after_replace':
                raise Crash()
        with self.assertRaises(Crash):
            self.apply(fault=die, evidence=evidence)
        return journal_inventory(self.root).pending[-1]

    def recovery(self, journal):
        with self.context(create_journal_directory=False, require_existing_namespace=True,
                          recovery_only=True) as tx:
            return tx.recover(journal)

    def test_exact_fixed_path_ignores_environment_and_rejects_unsupported_roots(self):
        with mock.patch.dict(os.environ, {'TYPST_LINT_JOURNAL_DIRECTORY': '/evil',
                                         'TYPST_LINT_JOURNALS': '/elsewhere',
                                         'XDG_STATE_HOME': '/other'}):
            self.assertEqual(fixed_journal_directory(self.root), self.base / '.typst-lint-journals')
        for root in (Path('/'), self.base / '.typst-lint-journals', Path('relative')):
            with self.subTest(root=root), self.assertRaises(TransactionError):
                fixed_journal_directory(root)
        self.assertFalse(self.store.exists())

    def test_no_store_inventory_needs_no_lease_creates_nothing_and_reads_no_sources(self):
        before = self.source.stat()
        with mock.patch.object(OfflineExclusive, 'assert_editors_watchers_closed_and_no_other_writers',
                               side_effect=AssertionError('must not assert offline')), \
                mock.patch.object(transactions, '_read', side_effect=AssertionError('must not read source')), \
                mock.patch.object(os, 'mkdir', side_effect=AssertionError('must not create')), \
                mock.patch.object(os, 'fsync', side_effect=AssertionError('must not sync')):
            result = journal_inventory(self.root)
        self.assertFalse(result.store_present)
        self.assertFalse(result.namespace_present)
        self.assertEqual(result.entries, ())
        self.assertEqual(result.pending, ())
        self.assertFalse(self.store.exists())
        self.assertEqual(transactions._stable(before), transactions._stable(self.source.stat()))
        self.assertEqual(self.source.read_bytes(), b'bad\n')

    def test_existing_empty_store_is_uninitialized_without_creating_namespace(self):
        self.store.mkdir(mode=0o700)
        before = self.store.stat()
        result = journal_inventory(self.root)
        self.assertTrue(result.store_present)
        self.assertFalse(result.namespace_present)
        self.assertEqual(list(self.store.iterdir()), [])
        self.assertEqual(transactions._stable(before), transactions._stable(self.store.stat()))
        self.assertFalse(self.namespace().exists())

    def test_legacy_default_requires_existing_store(self):
        with self.assertRaises(FileNotFoundError):
            with self.context(create_journal_directory=False):
                pass
        self.assertFalse(self.store.exists())
        self.assertEqual(self.source.read_bytes(), b'bad\n')

    def test_creation_requires_genuine_lease_and_strict_opt_ins(self):
        for lease in (None, True, object.__new__(OfflineExclusive)):
            with self.subTest(lease=lease), self.assertRaises(TransactionError):
                with self.context(lease=lease):
                    pass
            self.assertFalse(self.store.exists())
        for flag in ('create_journal_directory', 'require_existing_namespace'):
            for value in (None, 1, 'yes'):
                with self.subTest(flag=flag, value=value), self.assertRaises(TransactionError):
                    self.context(**{flag: value})
        with self.assertRaises(TransactionError):
            self.context(require_existing_namespace=True)

    def test_store_creation_uses_owner_only_mode_and_parent_fsync(self):
        synced, points = [], []
        original = os.fsync
        def sync(fd):
            synced.append(transactions._identity(os.fstat(fd)))
            return original(fd)
        with mock.patch.object(os, 'fsync', side_effect=sync):
            with self.context(fault=lambda point, path: points.append((point, path))):
                pass
        self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o700)
        self.assertEqual(self.store.stat().st_uid, os.geteuid())
        self.assertIn(transactions._identity(self.base.stat()), synced)
        self.assertIn(('store_parent_fsync', str(self.store)), points)
        self.assertEqual(self.source.read_bytes(), b'bad\n')

    def test_restrictive_owner_umask_fails_instead_of_chmodding_store(self):
        previous = os.umask(0o200)
        try:
            with self.assertRaisesRegex(TransactionError, 'mode 0700'):
                with self.context():
                    pass
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o500)
        self.assertFalse(self.namespace().exists())
        self.assertEqual(self.source.read_bytes(), b'bad\n')

    def test_existing_store_is_never_chmodded(self):
        self.store.mkdir(mode=0o700)
        sentinel = self.store / 'keep-me'
        sentinel.write_bytes(b'untouched')
        with mock.patch.object(os, 'chmod', side_effect=AssertionError('must not chmod')), \
                mock.patch.object(os, 'fchmod', side_effect=AssertionError('must not fchmod')):
            with self.context():
                pass
            journal_inventory(self.root)
        self.assertEqual(sentinel.read_bytes(), b'untouched')
        self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o700)

    def test_unsafe_store_mode_and_wrong_owner_are_refused_without_relaxation(self):
        self.store.mkdir(mode=0o755)
        for operation in (lambda: self.context().__enter__(), lambda: journal_inventory(self.root)):
            with self.assertRaises(TransactionError):
                operation()
        self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o755)
        self.assertFalse(self.namespace().exists())
        self.store.chmod(0o700)
        with mock.patch.object(os, 'geteuid', return_value=os.geteuid() + 1):
            with self.assertRaises(TransactionError):
                with self.context():
                    pass
            with self.assertRaises(JournalInventoryError):
                journal_inventory(self.root)
        self.assertFalse(self.namespace().exists())

    def test_store_metadata_errors_are_not_relaxed(self):
        self.store.mkdir(mode=0o700)
        original = transactions._plain_metadata
        def reject(fd, path, **kwargs):
            if path == self.store:
                raise TransactionError('fixture unsupported store metadata')
            return original(fd, path, **kwargs)
        with mock.patch.object(transactions, '_plain_metadata', side_effect=reject):
            with self.assertRaises(TransactionError):
                with self.context():
                    pass
            with self.assertRaises(JournalInventoryError):
                journal_inventory(self.root)
        self.assertFalse(self.namespace().exists())

    def test_store_or_namespace_symlinks_never_followed(self):
        outside = self.base / 'outside'
        outside.mkdir(mode=0o700)
        self.store.symlink_to(outside, target_is_directory=True)
        with self.assertRaises((OSError, TransactionError)):
            with self.context():
                pass
        with self.assertRaises(JournalInventoryError):
            journal_inventory(self.root)
        self.assertEqual(list(outside.iterdir()), [])
        self.store.unlink()
        self.store.mkdir(mode=0o700)
        self.namespace().symlink_to(outside, target_is_directory=True)
        with self.assertRaises((OSError, TransactionError)):
            with self.context():
                pass
        with self.assertRaises(JournalInventoryError):
            journal_inventory(self.root)
        self.assertEqual(list(outside.iterdir()), [])

    def test_creation_parent_unwritable_fails_without_fallback(self):
        if os.geteuid() == 0:
            self.skipTest('root bypasses POSIX mode write checks')
        self.base.chmod(0o500)
        try:
            with self.assertRaises(PermissionError):
                with self.context():
                    pass
            self.assertFalse(self.store.exists())
            self.assertEqual(self.source.read_bytes(), b'bad\n')
        finally:
            self.base.chmod(0o700)

    def test_parent_fsync_failure_never_reaches_namespace_or_source_write(self):
        def fail(point, path):
            if point == 'store_parent_fsync':
                raise OSError('fixture parent fsync failed')
        with self.assertRaises(OSError):
            with self.context(fault=fail):
                pass
        self.assertTrue(self.store.is_dir())
        self.assertFalse(self.namespace().exists())
        self.assertEqual(self.source.read_bytes(), b'bad\n')

    def test_concurrent_store_identity_change_refused(self):
        moved = self.base / 'moved-store'
        def replace(point, path):
            if point == 'store_parent_fsync':
                self.store.rename(moved)
                self.store.mkdir(mode=0o700)
        with self.assertRaisesRegex(TransactionError, 'identity changed'):
            with self.context(fault=replace):
                pass
        self.assertEqual(list(self.store.iterdir()), [])
        self.assertEqual(list(moved.iterdir()), [])
        self.assertEqual(self.source.read_bytes(), b'bad\n')

    def test_concurrent_root_identity_change_refused(self):
        moved = self.base / 'moved-root'
        def replace(point, path):
            if point == 'store_parent_fsync':
                self.root.rename(moved)
                self.root.mkdir()
        with self.assertRaisesRegex(TransactionError, 'identity changed'):
            with self.context(fault=replace):
                pass
        self.assertEqual((moved / 'main.typ').read_bytes(), b'bad\n')
        self.assertFalse(self.namespace().exists())

    def test_cross_device_parent_fails_before_store_creation(self):
        original = transactions._identity
        parent_ino = self.base.stat().st_ino
        def different_device(value):
            device, inode = original(value)
            return (device + 1, inode) if inode == parent_ino else (device, inode)
        with mock.patch.object(transactions, '_identity', side_effect=different_device):
            with self.assertRaisesRegex(TransactionError, 'source filesystem'):
                with self.context():
                    pass
            with self.assertRaises(JournalInventoryError):
                journal_inventory(self.root)
        self.assertFalse(self.store.exists())

    def test_lock_collision_blocks_status_and_creation(self):
        fd = transactions._directory(self.root)
        try:
            transactions.fcntl.flock(fd, transactions.fcntl.LOCK_EX | transactions.fcntl.LOCK_NB)
            with self.assertRaisesRegex(JournalInventoryError, 'Root lock unavailable'):
                journal_inventory(self.root)
            with self.assertRaisesRegex(TransactionError, 'Root lock unavailable'):
                with self.context():
                    pass
        finally:
            os.close(fd)
        self.assertFalse(self.store.exists())
        # A failed attempt releases all its own descriptors and never unlocks
        # somebody else's fd. Once the holder closes, both routes work.
        self.assertFalse(journal_inventory(self.root).store_present)
        with self.context():
            pass

    def test_require_existing_namespace_never_initializes_store_or_namespace(self):
        for existing_store in (False, True):
            if existing_store:
                self.store.mkdir(mode=0o700)
            with self.assertRaises(FileNotFoundError):
                with self.context(create_journal_directory=False,
                                  require_existing_namespace=True, recovery_only=True):
                    pass
            self.assertFalse(self.namespace().exists())
        self.assertEqual(list(self.store.iterdir()), [])
        # Generic legacy contexts still initialize an empty namespace.
        with self.context(create_journal_directory=False, recovery_only=True):
            pass
        self.assertTrue(self.namespace().is_dir())

    def test_committed_recovered_incomplete_and_corrupt_entries_visible(self):
        committed = self.apply().journal
        self.source.write_bytes(b'bad\n')
        recovered = self.crash()
        self.assertTrue(self.recovery(recovered).complete)
        self.source.write_bytes(b'bad\n')
        incomplete = self.crash()
        corrupt = self.namespace() / 'not-a-journal'
        corrupt.mkdir(mode=0o700)
        result = journal_inventory(self.root)
        states = {entry.journal: entry.status for entry in result.entries}
        self.assertEqual(states[committed], 'committed')
        self.assertEqual(states[recovered], 'recovered')
        self.assertEqual(states[incomplete], 'incomplete')
        self.assertEqual(states[corrupt.name], 'unreadable-or-corrupt')
        for entry in result.entries:
            self.assertEqual(entry.targets, () if entry.journal == corrupt.name else ('main.typ',))
        self.assertEqual(set(result.pending), {incomplete, corrupt.name})
        self.assertFalse(result.complete)
        value = inventory_json(result)
        self.assertEqual(json.loads(json.dumps(value)), value)
        self.assertTrue(value['store_present'])
        self.assertTrue(value['namespace_present'])
        with self.assertRaises(RecoveryRequired) as caught:
            with self.context():
                pass
        self.assertEqual(set(caught.exception.journals), set(result.pending))

    def test_valid_status_never_reads_sources_or_changes_journal_metadata(self):
        journal = self.apply().journal
        self.source.unlink()  # Status does not imply that current sources are safe.
        paths = [self.store, self.namespace(), *(self.namespace() / journal).iterdir()]
        before = {path: transactions._stable(path.stat()) for path in paths}
        with mock.patch.object(transactions, '_read', side_effect=AssertionError('source read')), \
                mock.patch.object(OfflineExclusive, 'assert_editors_watchers_closed_and_no_other_writers',
                                  side_effect=AssertionError('lease asserted')):
            result = journal_inventory(self.root)
        self.assertEqual(result.entries[0].status, 'committed')
        self.assertEqual(before, {path: transactions._stable(path.stat()) for path in paths})
        self.assertFalse(self.source.exists())

    def test_unknown_artifact_inserted_during_scan_corrupt_on_same_inventory_and_pending_scan(self):
        journal = self.apply().journal
        path = self.namespace() / journal
        artifact = path / 'unrecognized.bin'
        identity = transactions._identity(path.stat())
        original = transactions._JournalReader._journal_names
        def race(reader, fd):
            names = original(reader, fd)
            if transactions._identity(os.fstat(fd)) == identity:
                artifact.write_bytes(b'unknown')
            return names
        with mock.patch.object(transactions._JournalReader, '_journal_names', race):
            result = journal_inventory(self.root)
        self.assertEqual(result.entries[0].status, 'unreadable-or-corrupt')
        self.assertIn('changed while reading', result.entries[0].error)
        artifact.unlink()
        with mock.patch.object(transactions._JournalReader, '_journal_names', race):
            with self.assertRaises(RecoveryRequired) as caught:
                with self.context():
                    pass
        self.assertEqual(caught.exception.journals, (journal,))

    def test_symlinked_journal_and_fifo_json_stay_visible_without_blocking(self):
        journal = self.apply().journal
        linked = 'f' * 32
        (self.namespace() / linked).symlink_to(self.namespace() / journal, target_is_directory=True)
        fifo = self.namespace() / ('e' * 32)
        fifo.mkdir(mode=0o700)
        os.mkfifo(fifo / 'manifest.json', mode=0o600)
        result = journal_inventory(self.root)
        states = {entry.journal: entry.status for entry in result.entries}
        self.assertEqual(states[linked], 'unreadable-or-corrupt')
        self.assertEqual(states[fifo.name], 'unreadable-or-corrupt')
        self.assertEqual(states[journal], 'committed')

    def test_schema_duplicate_keys_event_gaps_and_read_limits_are_not_hidden(self):
        journal = self.apply().journal
        path = self.namespace() / journal
        manifest = path / 'manifest.json'
        original = manifest.read_bytes()
        variants = (b'{"schema":1,"schema":1}',
                    original.replace(b'"schema":1', b'"schema":2'))
        for content in variants:
            manifest.write_bytes(content)
            result = journal_inventory(self.root)
            self.assertEqual(result.entries[0].status, 'unreadable-or-corrupt')
            self.assertEqual(result.pending, (journal,))
        manifest.write_bytes(original)
        for limits in (replace(ReadLimits(), max_journal_bytes=len(original) - 1),
                       replace(ReadLimits(), max_journal_total_bytes=len(original)),
                       replace(ReadLimits(), max_source_bytes=1),
                       replace(ReadLimits(), max_directory_entries=1)):
            with self.subTest(limits=limits):
                result = journal_inventory(self.root, limits=limits)
                self.assertEqual(result.entries[0].status, 'unreadable-or-corrupt')
        events = sorted(path.glob('event-*.json'))
        events[0].unlink()
        result = journal_inventory(self.root)
        self.assertEqual(result.entries[0].status, 'unreadable-or-corrupt')
        self.assertIn('Noncontiguous', result.entries[0].error)

    def test_initial_encoding_error_typed_but_revalidation_is_storage_failure(self):
        self.source.write_bytes(b'\xff')
        with self.assertRaises(transactions.SourceEncodingError):
            with self.context():
                pass
        self.source.write_bytes(b'bad\n')
        with self.context() as tx:
            self.source.write_bytes(b'\xff')
            with self.assertRaises(TransactionError) as caught:
                tx._revalidate()
        self.assertNotIsInstance(caught.exception, transactions.SourceEncodingError)
        self.assertIn('Stale baseline', str(caught.exception))

    def test_corrupt_json_recovery_raises_typed_storage_error(self):
        journal = self.crash()
        manifest = self.namespace() / journal / 'manifest.json'
        for content in (b'{invalid', b'\xff', b'[' * 2000 + b']' * 2000):
            manifest.write_bytes(content)
            with self.assertRaises(TransactionError):
                self.recovery(journal)
            result = journal_inventory(self.root)
            self.assertEqual(result.pending, (journal,))
            self.assertEqual(result.entries[0].targets, ())

    def test_malformed_nested_journal_fields_are_typed_storage_errors(self):
        journal = self.crash()
        directory = self.namespace() / journal
        manifest = directory / 'manifest.json'
        original = manifest.read_bytes()
        value = json.loads(original)
        value['evidence'] = [dict(path=None, bytes=None, fingerprint=None)]
        manifest.write_text(json.dumps(value))
        with self.assertRaises(TransactionError):
            self.recovery(journal)
        manifest.write_bytes(original)
        event = sorted(directory.glob('event-*.json'))[0]
        value = json.loads(event.read_bytes())
        value['path'] = []
        event.write_text(json.dumps(value))
        with self.assertRaises(TransactionError):
            self.recovery(journal)
        self.assertEqual(journal_inventory(self.root).pending, (journal,))

    def test_directory_limit_accepts_exact_count_and_refuses_next_entry(self):
        directory = self.base / 'enumerate'
        directory.mkdir(mode=0o700)
        for name in ('b', 'a'):
            (directory / name).write_bytes(b'')
        reader = self.context(limits=replace(ReadLimits(), max_directory_entries=2))
        fd = transactions._directory(directory)
        try:
            self.assertEqual(sorted(reader._journal_names(fd)), ['a', 'b'])
            (directory / 'c').write_bytes(b'')
            with self.assertRaisesRegex(TransactionError, 'entry limit'):
                reader._journal_names(fd)
            # scandir's own duplicate descriptor is also closed on refusal.
            self.assertEqual(sorted(os.listdir(fd)), ['a', 'b', 'c'])
        finally:
            os.close(fd)

    def test_namespace_entry_limit_is_explicit_failure_not_partial_empty_success(self):
        self.apply()
        (self.namespace() / 'other').mkdir(mode=0o700)
        with self.assertRaisesRegex(JournalInventoryError, 'entry limit'):
            journal_inventory(self.root, limits=replace(ReadLimits(), max_directory_entries=1))

    def test_namespace_or_store_identity_race_cannot_return_clean_inventory(self):
        self.apply()
        original = transactions._JournalReader._journal_entries
        moved = self.base / 'moved-store'
        def race(reader):
            entries = original(reader)
            self.store.rename(moved)
            self.store.mkdir(mode=0o700)
            return entries
        with mock.patch.object(transactions._JournalReader, '_journal_entries', race):
            with self.assertRaisesRegex(JournalInventoryError, 'identity changed'):
                journal_inventory(self.root)

    def test_recovery_does_not_require_current_config_or_source_grammar(self):
        config = self.root / '.typst-lint.toml'
        config.write_bytes(b'fixture = true\n')
        journal = self.crash(evidence=(EvidenceInput(config),))
        config.unlink()
        report = self.recovery(journal)
        self.assertTrue(report.complete)
        self.assertEqual(self.source.read_bytes(), b'bad\n')
        self.assertFalse(config.exists())
        self.assertEqual(journal_inventory(self.root).entries[0].status, 'recovered')

    def test_later_edits_preserved_and_mappingproxy_conflicts_serialize(self):
        journal = self.crash()
        self.source.write_bytes(b'later unparseable user work \xff\n')
        original = self.source.read_bytes()
        report = self.recovery(journal)
        self.assertFalse(report.complete)
        self.assertEqual(report.unresolved, ('main.typ',))
        self.assertEqual(self.source.read_bytes(), original)
        # A parseable later edit gives a typed exact fingerprint conflict too.
        self.source.write_bytes(b'later user work\n')
        report = self.recovery(journal)
        self.assertFalse(report.complete)
        self.assertIsInstance(report.conflicts, MappingProxyType)
        value = recovery_report_json(report)
        self.assertEqual(value['conflicts']['main.typ']['sha256'],
                         transactions._hash(self.source.read_bytes()))
        self.assertEqual(json.loads(json.dumps(value)), value)
        self.assertEqual(journal_inventory(self.root).pending, (journal,))

    def test_inventory_error_and_success_paths_close_descriptors(self):
        self.apply()
        def count():
            path = '/dev/fd' if sys.platform == 'darwin' else '/proc/self/fd'
            return len(os.listdir(path))
        before = count()
        for _ in range(12):
            journal_inventory(self.root)
        self.store.chmod(0o755)
        for _ in range(12):
            with self.assertRaises(JournalInventoryError):
                journal_inventory(self.root)
        self.store.chmod(0o700)
        self.assertEqual(count(), before)


if __name__ == '__main__':
    unittest.main()
