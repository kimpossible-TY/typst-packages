"""Live committed-source receipts, using real parsing and temporary consumers.

No compiler, output publication, consumer rollout or global coverage is claimed.
The one-rule direct-transaction fixture exists only to exercise absent evidence.
"""
import copy
from dataclasses import FrozenInstanceError, replace
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
import protected_fix as bridge
import transactions as storage
from protected_fix import ClosedCoreCompileSnapshot, ProtectedCoreError, ProtectedCoreFix
from syntax_client import parse
from transactions import (CommittedSnapshot, EvidenceInput, GraphAnalysis, OfflineExclusive,
                          ProtectedTransaction, ReadLimits, SourceEdit, TransactionError,
                          TransactionFailure)
from typst_lint import CATALOG, digest

XATTR = 'com.example.committed' if sys.platform == 'darwin' else 'user.com.example.committed'
REFUSAL = (TransactionError, OSError)
DIRTY = b'Preserved user prose. $x_1 + y^2$\r\n'
FIXED = b'Preserved user prose. $x_(1) + y^(2)$\r\n'


class Fixture:
    def __init__(self, source=DIRTY, target='nested path/main.typ'):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'consumer'
        self.root.mkdir()
        self.store = self.base / 'journals'
        self.store.mkdir(mode=0o700)
        self.target = target
        self.source = self.root / target
        self.source.parent.mkdir(parents=True, exist_ok=True)
        self.source.write_bytes(source)
        self.config = self.root / '.typst-lint.toml'
        self.config.write_bytes(f'rule-set = "core"\ninclude = ["{target}"]\n'.encode())
        self.catalog = self.base / 'evidence' / 'catalog.json'
        self.catalog.parent.mkdir()
        self.catalog.write_bytes(CATALOG.read_bytes())
        # This assertion applies only to these isolated, test-owned paths.
        self.lease = OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers()

    def __enter__(self):
        self.patch = mock.patch.object(bridge, 'CATALOG', self.catalog)
        self.patch.start()
        return self

    def __exit__(self, *args):
        self.patch.stop()
        self.temp.cleanup()

    def context(self, **kwargs):
        return ProtectedCoreFix(self.root, self.target, journal_directory=self.store,
                                lease=self.lease, **kwargs)

    def transaction(self, **kwargs):
        evidence = kwargs.pop('evidence', (EvidenceInput(self.config), EvidenceInput(self.catalog)))
        return ProtectedTransaction(self.root, (self.target,), journal_directory=self.store,
                                    evidence=evidence, lease=self.lease, **kwargs)

    def journal_files(self):
        return {str(p.relative_to(self.store)): p.read_bytes()
                for p in self.store.rglob('*') if p.is_file()}

    def events(self, journal):
        directory = next(self.store.glob('root-*')) / journal
        return [json.loads(p.read_bytes()) for p in sorted(directory.glob('event-*.json'))]


def one_rule_engine(graph):
    """Actually parse/analyze scripts; optional evidence is only a storage probe."""
    catalog, _ = bridge._catalog(next(raw for path, raw in graph.evidence.items()
                                    if path.endswith('/catalog.json')))
    edits, findings = [], set()
    for path, raw in graph.sources.items():
        result, found = bridge._script(parse(raw), path, catalog)
        edits.extend(SourceEdit(path, e) for e in result.edits)
        findings.update(found)
    rules = frozenset((bridge.SCRIPT,))
    return GraphAnalysis(rules, rules, frozenset(graph.sources), frozenset(graph.evidence),
                         findings=frozenset(findings), edits=tuple(edits))


def replace_same_bytes(path):
    new = path.with_name(path.name + '.replacement')
    new.write_bytes(path.read_bytes())
    new.chmod(stat.S_IMODE(path.stat().st_mode))
    os.replace(new, path)


def replace_parent(path):
    parent = path.parent
    moved = parent.with_name(parent.name + '-old')
    parent.rename(moved)
    parent.mkdir()
    for old in moved.iterdir():
        if old.is_file():
            (parent / old.name).write_bytes(old.read_bytes())


def set_fixture_xattr(path, value):
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        attrs = dict(storage._xattrs(fd))
        attrs[XATTR] = value
        storage._copy_xattrs(fd, tuple(sorted(attrs.items())))
    finally:
        os.close(fd)


def get_fixture_xattr(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        return dict(storage._xattrs(fd))[XATTR]
    finally:
        os.close(fd)


class CommittedSnapshotTests(unittest.TestCase):
    def assert_stale_preserves(self, fixture, fix, snapshot, receipt):
        journals = fixture.journal_files()
        before_source = (fixture.source.lstat() if fixture.source.exists() or fixture.source.is_symlink()
                         else None)
        source_bytes = (fixture.source.read_bytes() if before_source is not None
                        and stat.S_ISREG(before_source.st_mode) else None)
        retained = fix.transaction._committed_files
        for guard in (lambda: fix.assert_compile_current(snapshot), fix.compile_snapshot,
                      lambda: fix.transaction.assert_current(receipt),
                      fix.transaction.committed_snapshot):
            with self.assertRaises(REFUSAL):
                guard()
        self.assertIs(fix.transaction._committed_files, retained)
        self.assertEqual(fixture.journal_files(), journals)
        if source_bytes is not None:
            self.assertEqual(fixture.source.read_bytes(), source_bytes)
            self.assertEqual(fixture.source.lstat(), before_source)
        elif before_source is None:
            self.assertFalse(fixture.source.exists())
        else:
            self.assertEqual(fixture.source.lstat(), before_source)

    def test_changed_receipts_retain_exact_verified_postimage_and_baseline(self):
        with Fixture() as f, f.context() as fix:
            tx = fix.transaction
            original = tx._files[f.target]
            candidate = fix.validate()
            result = fix.apply(candidate)
            receipt = tx.committed_snapshot()
            snapshot = fix.compile_snapshot()
            self.assertEqual(result.modified, (f.target,))
            self.assertEqual(receipt.sources, {f.target: FIXED})
            self.assertEqual(tx.snapshot.sources, {f.target: DIRTY})
            self.assertEqual(snapshot.source, FIXED)
            self.assertIs(snapshot.source, receipt.sources[f.target])
            self.assertEqual(snapshot.source_sha256, digest(FIXED))
            self.assertEqual(snapshot.target, f.target)
            self.assertEqual(snapshot.root, f.root)
            self.assertEqual(snapshot.required_rule_ids, tuple(sorted(bridge.CORE_RULE_IDS)))
            self.assertEqual(snapshot.evaluated_rule_ids, (bridge.SCRIPT,))
            self.assertEqual(len(snapshot.structural_reasons), 14)
            self.assertEqual(snapshot.config_sha256, digest(f.config.read_bytes()))
            self.assertEqual(snapshot.catalog_sha256, digest(f.catalog.read_bytes()))
            self.assertIs(tx._files[f.target], original)
            postimage = tx._committed_files[f.target]
            self.assertEqual(postimage, storage._read(f.source, utf8=True, limits=tx.limits))
            self.assertNotEqual(postimage.fingerprint.inode, original.fingerprint.inode)
            self.assertIs(postimage.data, receipt.sources[f.target])
            self.assertEqual(tx._committed_evidence, tx._evidence)
            self.assertIs(tx.committed_snapshot(), receipt)
            self.assertIs(fix.compile_snapshot(), snapshot)
            tx.assert_current(receipt)
            fix.assert_compile_current(snapshot)
            self.assertEqual(f.events(result.journal)[-1], {'kind': 'committed'})

    def test_noop_retains_original_records_not_a_fresh_capture(self):
        for source in (b'', b'Already $x_(1)$\r\n', '\ufeff한글 α. $x_(1)$\r\n'.encode()):
            with self.subTest(source=source), Fixture(source) as f, f.context() as fix:
                tx = fix.transaction
                before = tx._files[f.target]
                candidate = fix.validate()
                self.assertEqual(candidate.passes, 0)
                result = fix.apply(candidate)
                snapshot = fix.compile_snapshot()
                receipt = tx.committed_snapshot()
                self.assertEqual((result.journal, result.modified), (None, ()))
                self.assertEqual(snapshot.source, source)
                self.assertIs(tx._committed_files[f.target], before)
                self.assertIs(receipt.sources[f.target], before.data)
                for path, record in tx._evidence.items():
                    self.assertIs(tx._committed_evidence[path], record)
                    self.assertIs(receipt.evidence[path], record.data)
                self.assertEqual(tx.snapshot.sources, {f.target: source})
                self.assertEqual(f.journal_files(), {})

    def test_partial_multifile_apply_retains_changed_and_unchanged_records(self):
        with Fixture() as f:
            other = f.root / 'unchanged.typ'
            other.write_bytes(b'Already $z_(2)$\n')
            with ProtectedTransaction(f.root, (f.target, 'unchanged.typ'),
                    journal_directory=f.store, lease=f.lease,
                    evidence=(EvidenceInput(f.catalog),)) as tx:
                unchanged = tx._files['unchanged.typ']
                token = tx.validate(one_rule_engine, required_rules=frozenset((bridge.SCRIPT,)))
                result = tx.apply(token)
                receipt = tx.committed_snapshot()
                self.assertEqual(result.modified, (f.target,))
                self.assertEqual(receipt.sources, {f.target: FIXED, 'unchanged.typ': unchanged.data})
                self.assertEqual(tx.snapshot.sources[f.target], DIRTY)
                self.assertIs(tx._committed_files['unchanged.typ'], unchanged)
                self.assertIsNot(tx._committed_files[f.target], tx._files[f.target])
                tx.assert_current(receipt)

    def test_receipts_and_views_are_immutable_and_cannot_be_constructed(self):
        for cls in (CommittedSnapshot, ClosedCoreCompileSnapshot):
            with self.assertRaises(TypeError):
                cls()
            with self.assertRaises(TypeError):
                cls(source=b'caller bytes')
        with Fixture() as f, f.context() as fix:
            fix.apply(fix.validate())
            receipt, snapshot = fix.transaction.committed_snapshot(), fix.compile_snapshot()
            for obj, name, value in ((receipt, 'root', f.base), (snapshot, 'source', b'forged')):
                with self.assertRaises(FrozenInstanceError):
                    setattr(obj, name, value)
            for values in (receipt.sources, receipt.evidence, snapshot.evidence):
                with self.assertRaises(TypeError):
                    values['forged'] = b'caller bytes'
            with self.assertRaises(TypeError):
                fix.transaction._committed_files[f.target] = None
            with self.assertRaises(TypeError):
                fix.transaction._committed_evidence[str(f.config)] = None

    def test_before_apply_after_validation_and_after_context_have_no_authority(self):
        with Fixture() as f:
            fix = f.context()
            with fix:
                tx = fix.transaction
                for stage in ('before validation', 'after validation'):
                    if stage == 'after validation':
                        candidate = fix.validate()
                    with self.subTest(stage=stage):
                        with self.assertRaises(TransactionError):
                            tx.committed_snapshot()
                        with self.assertRaises(ProtectedCoreError):
                            fix.compile_snapshot()
                fix.apply(candidate)
                receipt, snapshot = tx.committed_snapshot(), fix.compile_snapshot()
            for guard in (tx.committed_snapshot, lambda: tx.assert_current(receipt),
                          fix.compile_snapshot, lambda: fix.assert_compile_current(snapshot)):
                with self.assertRaises(TransactionError):
                    guard()
            # Immutable reporting remains available outside the active context.
            self.assertEqual(fix.summary.final_sha256, digest(FIXED))

    def test_failed_apply_and_explicit_recovery_cannot_issue_receipts(self):
        def fail(point, path):
            if point == 'before_replace':
                raise OSError('fixture pre-commit refusal')
        with Fixture() as f:
            with f.context(fault=fail) as fix:
                candidate = fix.validate()
                with self.assertRaises(TransactionFailure) as caught:
                    fix.apply(candidate)
                journal = caught.exception.report.journal
                for guard in (fix.transaction.committed_snapshot, fix.compile_snapshot):
                    with self.assertRaises(TransactionError):
                        guard()
                self.assertEqual(f.source.read_bytes(), DIRTY)
                self.assertTrue(f.journal_files())
            with f.transaction(recovery_only=True) as recovery:
                with self.assertRaises(TransactionError):
                    recovery.committed_snapshot()
                self.assertTrue(recovery.recover(journal).complete)
                with self.assertRaises(TransactionError):
                    recovery.committed_snapshot()
                with self.assertRaises(TransactionError):
                    recovery.assert_current(object.__new__(CommittedSnapshot))
            self.assertEqual(f.source.read_bytes(), DIRTY)
            self.assertTrue(f.journal_files())

    def test_receipts_cannot_cross_active_or_later_sessions(self):
        with Fixture() as first, first.context() as a:
            a.apply(a.validate())
            old_receipt, old_snapshot = a.transaction.committed_snapshot(), a.compile_snapshot()
            with Fixture() as second, second.context() as b:
                b.apply(b.validate())
                b.compile_snapshot()
                with self.assertRaises(TransactionError):
                    b.transaction.assert_current(old_receipt)
                with self.assertRaises(ProtectedCoreError):
                    b.assert_compile_current(old_snapshot)
        with Fixture() as f:
            with f.context() as a:
                a.apply(a.validate())
                old_receipt, old_snapshot = a.transaction.committed_snapshot(), a.compile_snapshot()
            with f.context() as b:
                b.apply(b.validate())
                b.compile_snapshot()
                with self.assertRaises(TransactionError):
                    b.transaction.assert_current(old_receipt)
                with self.assertRaises(ProtectedCoreError):
                    b.assert_compile_current(old_snapshot)

    def test_equivalent_copies_forged_receipts_reports_bytes_are_not_authority(self):
        with Fixture() as f, f.context() as fix:
            fix.apply(fix.validate())
            tx = fix.transaction
            receipt, snapshot = tx.committed_snapshot(), fix.compile_snapshot()
            for value in (None, True, FIXED, digest(FIXED), dict(receipt.sources),
                          tx.snapshot, fix.summary, tx._committed_files[f.target].fingerprint,
                          object.__new__(CommittedSnapshot), copy.copy(receipt), copy.copy(snapshot)):
                with self.subTest(value=type(value).__name__):
                    with self.assertRaises(TransactionError):
                        tx.assert_current(value)
                    with self.assertRaises(ProtectedCoreError):
                        fix.assert_compile_current(value)
            self.assertEqual(f.source.read_bytes(), FIXED)
            tx.assert_current(receipt)
            fix.assert_compile_current(snapshot)

    def test_reflectively_changed_receipt_fields_do_not_change_retained_authority(self):
        with Fixture() as f, f.context() as fix:
            fix.apply(fix.validate())
            tx = fix.transaction
            receipt, snapshot = tx.committed_snapshot(), fix.compile_snapshot()
            for name, value in (('sources', MappingProxyType(dict(receipt.sources))),
                                ('evidence', MappingProxyType(dict(receipt.evidence))),
                                ('root', f.base), ('_owner', object()), ('_seal', object())):
                original = getattr(receipt, name)
                try:
                    object.__setattr__(receipt, name, value)
                    with self.assertRaises(TransactionError):
                        tx.assert_current(receipt)
                finally:
                    object.__setattr__(receipt, name, original)
                tx.assert_current(receipt)
            for name, value in (('source', b'caller bytes'), ('source_sha256', '0' * 64),
                                ('target', 'forged.typ'), ('required_rule_ids', ()),
                                ('config_sha256', '0' * 64), ('catalog_sha256', '0' * 64),
                                ('_receipt', object.__new__(CommittedSnapshot)),
                                ('_owner', object())):
                original = getattr(snapshot, name)
                try:
                    object.__setattr__(snapshot, name, value)
                    with self.assertRaises(ProtectedCoreError):
                        fix.assert_compile_current(snapshot)
                finally:
                    object.__setattr__(snapshot, name, original)
                fix.assert_compile_current(snapshot)
            self.assertEqual(f.source.read_bytes(), FIXED)

    def test_noop_receipt_also_refuses_later_changes_without_a_journal(self):
        with Fixture(FIXED) as f, f.context() as fix:
            result = fix.apply(fix.validate())
            self.assertIsNone(result.journal)
            snapshot, receipt = fix.compile_snapshot(), fix.transaction.committed_snapshot()
            original = fix.transaction._files[f.target]
            replace_same_bytes(f.source)
            self.assert_stale_preserves(f, fix, snapshot, receipt)
            self.assertIs(fix.transaction._committed_files[f.target], original)
            self.assertEqual(f.journal_files(), {})

    def test_transaction_bypass_and_stale_summary_do_not_authorize_bridge(self):
        for bridge_validated in (False, True):
            with self.subTest(bridge_validated=bridge_validated), Fixture() as f:
                with f.context() as old:
                    old.apply(old.validate())
                    summary = old.summary
                f.source.write_bytes(DIRTY)
                with f.context() as fix:
                    if bridge_validated:
                        candidate = fix.validate()
                    else:
                        candidate = fix.transaction.validate(fix._analyze_graph,
                                                             required_rules=bridge.CORE_RULE_IDS)
                    fix.transaction.apply(candidate)
                    receipt = fix.transaction.committed_snapshot()
                    self.assertEqual(receipt.sources[f.target], FIXED)
                    # Even a formerly valid, immutable report is only output.
                    fix._summary = summary
                    with self.assertRaises(ProtectedCoreError):
                        fix.compile_snapshot()
                    with self.assertRaises(ProtectedCoreError):
                        fix.assert_compile_current(receipt)
                    self.assertEqual(f.source.read_bytes(), FIXED)

    def test_summary_changes_do_not_change_actual_compile_authority(self):
        with Fixture() as f, f.context() as fix:
            fix.apply(fix.validate())
            fix._summary = replace(fix.summary, target='forged.typ', final_sha256='0' * 64,
                                   required_rule_ids=(), structural_reasons=())
            snapshot = fix.compile_snapshot()
            self.assertEqual(snapshot.target, f.target)
            self.assertEqual(snapshot.source_sha256, digest(FIXED))
            self.assertEqual(len(snapshot.required_rule_ids), 15)
            fix.assert_compile_current(snapshot)

    def test_retention_and_compile_handoff_do_not_recapture_or_reanalyze(self):
        with Fixture() as f, f.context() as fix:
            tx = fix.transaction
            candidate = fix.validate()
            retain = tx._retain_committed
            def checked_retain(expected):
                with mock.patch.object(storage, '_read', side_effect=AssertionError('ambient recapture')):
                    return retain(expected)
            with mock.patch.object(tx, '_retain_committed', side_effect=checked_retain):
                fix.apply(candidate)
            with mock.patch.object(bridge, 'parse', side_effect=AssertionError('new live parse')), \
                 mock.patch.object(bridge, '_config', side_effect=AssertionError('new configuration parse')), \
                 mock.patch.object(bridge, '_catalog', side_effect=AssertionError('new catalog parse')), \
                 mock.patch.object(bridge, 'analyze_script', side_effect=AssertionError('new script analysis')):
                snapshot = fix.compile_snapshot()
                fix.assert_compile_current(snapshot)
            self.assertEqual(snapshot.source, FIXED)

    def test_mutation_after_committed_event_before_handoff_is_never_blessed_or_rolled_back(self):
        with Fixture() as f, f.context() as fix:
            candidate = fix.validate()
            event = fix.transaction._event
            later = b'Later user work. $z_9$\n'
            def change_after_commit(kind, **values):
                event(kind, **values)
                if kind == 'committed':
                    f.source.write_bytes(later)
            with mock.patch.object(fix.transaction, '_event', side_effect=change_after_commit):
                result = fix.apply(candidate)
            self.assertEqual(f.source.read_bytes(), later)
            self.assertEqual(fix.transaction._committed_files[f.target].data, FIXED)
            for guard in (fix.compile_snapshot, fix.transaction.committed_snapshot):
                with self.assertRaises(TransactionError):
                    guard()
            self.assertEqual(f.source.read_bytes(), later)
            self.assertEqual(f.events(result.journal)[-1], {'kind': 'committed'})
            self.assertNotIn({'kind': 'recovery_required'}, f.events(result.journal))
            self.assertFalse(fix.transaction._pending())

    def test_post_commit_retention_failure_does_not_enter_rollback_handler(self):
        for edit_after_commit in (False, True):
            with self.subTest(later_edit=edit_after_commit), Fixture() as f, f.context() as fix:
                candidate = fix.validate()
                later = b'Later independent edit.\n'
                def fail_handoff(expected):
                    self.assertEqual(expected[f.target].data, FIXED)
                    if edit_after_commit:
                        f.source.write_bytes(later)
                    raise OSError('fixture receipt allocation failure AFTER commit')
                with mock.patch.object(fix.transaction, '_retain_committed', side_effect=fail_handoff):
                    with self.assertRaisesRegex(OSError, 'AFTER commit'):
                        fix.apply(candidate)
                self.assertEqual(f.source.read_bytes(), later if edit_after_commit else FIXED)
                journal = fix.transaction._journal
                self.assertEqual(f.events(journal)[-1], {'kind': 'committed'})
                self.assertFalse(fix.transaction._pending())
                for guard in (fix.compile_snapshot, fix.transaction.committed_snapshot):
                    with self.assertRaises(TransactionError):
                        guard()

    def test_source_mutations_refuse_without_refresh_writes_or_journal_changes(self):
        def changed_bytes(path):
            path.write_bytes(b'Later user source.\n')
        def same_bytes_write(path):
            path.write_bytes(path.read_bytes())
        def timestamp(path):
            s = path.stat()
            os.utime(path, ns=(s.st_atime_ns, s.st_mtime_ns + 1_000_000_000))
        def mode(path):
            path.chmod(0o640 if stat.S_IMODE(path.stat().st_mode) != 0o640 else 0o600)
        def symlink(path):
            original = path.with_name('original.typ')
            path.rename(original)
            path.symlink_to(original)
        def hardlink(path):
            os.link(path, path.with_name('hardlinked.typ'))
        def fifo(path):
            path.unlink()
            os.mkfifo(path)
        operations = (changed_bytes, same_bytes_write, replace_same_bytes, timestamp, mode,
                      symlink, hardlink, fifo, lambda p: p.unlink(),
                      lambda p: p.write_bytes(b'\xff'), replace_parent)
        for operation in operations:
            with self.subTest(operation=operation.__name__), Fixture() as f, f.context() as fix:
                fix.apply(fix.validate())
                snapshot, receipt = fix.compile_snapshot(), fix.transaction.committed_snapshot()
                operation(f.source)
                self.assert_stale_preserves(f, fix, snapshot, receipt)

    def test_root_replacement_refuses_and_preserves_both_old_and_new_sources(self):
        with Fixture() as f, f.context() as fix:
            fix.apply(fix.validate())
            snapshot, receipt = fix.compile_snapshot(), fix.transaction.committed_snapshot()
            old_root = f.root.with_name('old-consumer')
            f.root.rename(old_root)
            f.source.parent.mkdir(parents=True)
            f.source.write_bytes(b'Independent replacement root.\n')
            self.assert_stale_preserves(f, fix, snapshot, receipt)
            self.assertEqual((old_root / f.target).read_bytes(), FIXED)

    def test_source_xattr_change_refuses_and_retains_original_postimage_metadata(self):
        with Fixture() as f:
            try:
                set_fixture_xattr(f.source, b'original')
            except (OSError, TransactionError) as error:
                self.skipTest(f'Temporary filesystem does not support fixture xattrs: {error}')
            with f.context() as fix:
                fix.apply(fix.validate())
                snapshot, receipt = fix.compile_snapshot(), fix.transaction.committed_snapshot()
                self.assertIn((XATTR, b'original'), fix.transaction._committed_files[f.target].xattrs)
                set_fixture_xattr(f.source, b'later metadata')
                self.assert_stale_preserves(f, fix, snapshot, receipt)
                self.assertEqual(get_fixture_xattr(f.source), b'later metadata')

    def test_configuration_and_catalog_mutations_refuse_without_refresh(self):
        def bytes_change(path):
            path.write_bytes(path.read_bytes() + b'\n')
        def timestamp(path):
            s = path.stat()
            os.utime(path, ns=(s.st_atime_ns, s.st_mtime_ns + 1_000_000_000))
        def mode(path):
            path.chmod(0o600 if stat.S_IMODE(path.stat().st_mode) != 0o600 else 0o640)
        def symlink(path):
            old = path.with_name(path.name + '.old')
            path.rename(old)
            path.symlink_to(old)
        def hardlink(path):
            os.link(path, path.with_name(path.name + '.link'))
        def fifo(path):
            path.unlink()
            os.mkfifo(path)
        for input_name in ('config', 'catalog'):
            for operation in (bytes_change, replace_same_bytes, timestamp, mode,
                              symlink, hardlink, fifo, lambda p: p.unlink()):
                with self.subTest(input=input_name, operation=operation.__name__), \
                     Fixture() as f, f.context() as fix:
                    fix.apply(fix.validate())
                    snapshot, receipt = fix.compile_snapshot(), fix.transaction.committed_snapshot()
                    path = getattr(f, input_name)
                    operation(path)
                    self.assert_stale_preserves(f, fix, snapshot, receipt)
                    self.assertEqual(f.source.read_bytes(), FIXED)

    def test_catalog_parent_and_evidence_xattrs_are_guarded(self):
        for change in ('parent', 'catalog-xattr', 'config-xattr'):
            with self.subTest(change=change), Fixture() as f, f.context() as fix:
                fix.apply(fix.validate())
                snapshot, receipt = fix.compile_snapshot(), fix.transaction.committed_snapshot()
                if change == 'parent':
                    replace_parent(f.catalog)
                else:
                    try:
                        set_fixture_xattr(f.catalog if change == 'catalog-xattr' else f.config,
                                          b'later evidence metadata')
                    except (OSError, TransactionError) as error:
                        self.skipTest(f'Temporary filesystem does not support fixture xattrs: {error}')
                self.assert_stale_preserves(f, fix, snapshot, receipt)

    def test_absent_evidence_is_retained_and_guarded_without_new_selection(self):
        for change in ('appearance', 'parent'):
            with self.subTest(change=change), Fixture() as f:
                absent = f.base / 'optional' / 'absent.json'
                absent.parent.mkdir()
                evidence = (EvidenceInput(f.catalog), EvidenceInput(absent, allow_missing=True))
                with f.transaction(evidence=evidence) as tx:
                    candidate = tx.validate(one_rule_engine, required_rules=frozenset((bridge.SCRIPT,)))
                    result = tx.apply(candidate)
                    receipt = tx.committed_snapshot()
                    original = tx._evidence[str(absent)]
                    self.assertIsNone(receipt.evidence[str(absent)])
                    self.assertIs(tx._committed_evidence[str(absent)], original)
                    if change == 'appearance':
                        absent.write_bytes(b'new optional input')
                    else:
                        replace_parent(absent)
                    journals = f.journal_files()
                    with self.assertRaises(TransactionError):
                        tx.assert_current(receipt)
                    with self.assertRaises(TransactionError):
                        tx.committed_snapshot()
                    self.assertEqual(f.source.read_bytes(), FIXED)
                    self.assertEqual(f.journal_files(), journals)
                    self.assertEqual(f.events(result.journal)[-1], {'kind': 'committed'})

    def test_revalidation_uses_shared_graph_budget_and_existing_descriptor_reader(self):
        with Fixture() as f, f.context() as fix:
            fix.apply(fix.validate())
            receipt = fix.transaction.committed_snapshot()
            with mock.patch.object(storage, '_read', wraps=storage._read) as reads:
                fix.transaction.assert_current(receipt)
            self.assertEqual(len(reads.call_args_list), 3)
            budgets = [call.kwargs['budget'] for call in reads.call_args_list]
            self.assertTrue(all(budget is budgets[0] for budget in budgets))
            self.assertTrue(all(call.kwargs['limits'] is fix.transaction.limits
                                for call in reads.call_args_list))
            self.assertEqual({call.args[0] for call in reads.call_args_list},
                             {f.source, f.config, f.catalog})

    def test_guard_obeys_source_evidence_and_aggregate_limits(self):
        for kind in ('source', 'evidence', 'aggregate'):
            with self.subTest(limit=kind), Fixture() as f:
                source_size = len(FIXED)
                evidence_size = max(f.config.stat().st_size, f.catalog.stat().st_size)
                metadata_size = sum(storage._metadata_size(storage._read(path, utf8=path == f.source).xattrs)
                                    for path in (f.source, f.config, f.catalog))
                total = source_size + f.config.stat().st_size + f.catalog.stat().st_size + metadata_size
                limits = ReadLimits(max_source_bytes=source_size,
                                    max_evidence_bytes=evidence_size, max_capture_bytes=total)
                with f.context(limits=limits) as fix:
                    fix.apply(fix.validate())
                    snapshot, receipt = fix.compile_snapshot(), fix.transaction.committed_snapshot()
                    if kind == 'source':
                        f.source.write_bytes(b'x' * (source_size + 1))
                    elif kind == 'evidence':
                        f.catalog.write_bytes(b'x' * (evidence_size + 1))
                    else:
                        f.config.write_bytes(f.config.read_bytes() + b' ')
                    self.assert_stale_preserves(f, fix, snapshot, receipt)

    def test_descriptor_read_race_refuses_and_closes_without_replacing_user_edit(self):
        with Fixture() as f, f.context() as fix:
            fix.apply(fix.validate())
            receipt = fix.transaction.committed_snapshot()
            bounded = storage._bounded_bytes
            descriptor = None
            later = b'Later raced user work.\n'
            def race(fd, maximum, label, **kwargs):
                nonlocal descriptor
                result = bounded(fd, maximum, label, **kwargs)
                if label == f.source:
                    descriptor = fd
                    f.source.write_bytes(later)
                return result
            journals = f.journal_files()
            with mock.patch.object(storage, '_bounded_bytes', side_effect=race):
                with self.assertRaises(TransactionError):
                    fix.transaction.assert_current(receipt)
            self.assertIsNotNone(descriptor)
            with self.assertRaises(OSError):
                os.fstat(descriptor)
            self.assertEqual(f.source.read_bytes(), later)
            self.assertEqual(f.journal_files(), journals)
            self.assertEqual(fix.transaction._committed_files[f.target].data, FIXED)

    def test_same_bytes_written_back_cannot_refresh_stale_fingerprint(self):
        with Fixture() as f, f.context() as fix:
            fix.apply(fix.validate())
            snapshot, receipt = fix.compile_snapshot(), fix.transaction.committed_snapshot()
            expected = fix.transaction._committed_files[f.target]
            f.source.write_bytes(b'Later work.\n')
            self.assert_stale_preserves(f, fix, snapshot, receipt)
            f.source.write_bytes(FIXED)
            self.assert_stale_preserves(f, fix, snapshot, receipt)
            self.assertIs(fix.transaction._committed_files[f.target], expected)


if __name__ == '__main__':
    unittest.main()
