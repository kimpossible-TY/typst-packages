"""Storage protocol tests, with synthetic analysis and TEMPORARY consumers only.

These fixtures do not claim complete Typst rule support or enable CLI writes.
"""
import base64
from dataclasses import FrozenInstanceError, replace
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from edit_plan import Edit
from transactions import (EvidenceInput, Fingerprint, GraphAnalysis, OfflineExclusive,
                          ProtectedTransaction, RecoveryRequired, SourceEdit,
                          TransactionError, TransactionFailure, ValidatedCandidate)

RULES = frozenset({'fixture.only'})
FIXTURE_XATTR = 'com.example.review' if sys.platform == 'darwin' else 'user.com.example.review'


def synthetic_engine(graph):
    """Exactly one synthetic byte repair; no claim to implement Typst syntax."""
    edits, findings = [], set()
    for path, data in graph.sources.items():
        at = data.find(b'bad')
        if at >= 0:
            edits.append(SourceEdit(path, Edit(at, at + 3, b'good')))
            findings.add(path + ':synthetic-bad')
        if b'KEEP' in data:
            findings.add(path + ':diagnostic-only')
    return GraphAnalysis(RULES, RULES, frozenset(graph.sources),
                         frozenset(graph.evidence), findings=frozenset(findings), edits=tuple(edits))


class SimulatedCrash(BaseException):
    pass


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'consumer'
        self.root.mkdir()
        self.store = self.base / 'journals'
        self.store.mkdir(mode=0o700)
        self.a = self.root / 'a.typ'
        self.b = self.root / 'b.typ'
        self.a.write_bytes(b'// existing dirty user edit\nbad KEEP\n')
        self.b.write_bytes(b'bad\n')
        self.config = self.root / '.typst-lint.toml'
        self.config.write_bytes(b'rule-set = "fixture"\n')
        self.external = self.base / 'read-only-resource.bin'
        self.external.write_bytes(b'\xff\x00data')
        self.evidence = (EvidenceInput(self.config), EvidenceInput(self.external))
        self.lease = OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers()

    def context(self, **kwargs):
        defaults = dict(journal_directory=self.store, evidence=self.evidence, lease=self.lease)
        defaults.update(kwargs)
        targets = defaults.pop('targets', ('a.typ', 'b.typ'))
        root = defaults.pop('root', self.root)
        return ProtectedTransaction(root, targets, **defaults)

    def apply(self, **kwargs):
        with self.context(**kwargs) as tx:
            return tx.apply(tx.validate(synthetic_engine, required_rules=RULES))

    def journal_path(self, journal):
        return next(self.store.glob('root-*')) / journal

    def recover(self, journal, **kwargs):
        with self.context(recovery_only=True) as tx:
            return tx.recover(journal, **kwargs)

    def failed_after_first(self, change=False):
        def fault(point, path):
            if point == 'before_replace' and path == 'b.typ':
                if change:
                    self.a.write_bytes(b'new user work\n')
                raise OSError('injected second replace failure')
        with self.assertRaises(TransactionFailure) as caught:
            self.apply(fault=fault)
        return caught.exception

    def test_happy_multifile_preserves_dirty_baseline_and_readonly_evidence(self):
        original = self.a.read_bytes()
        old_config, old_external = self.config.read_bytes(), self.external.read_bytes()
        result = self.apply()
        self.assertEqual(result.modified, ('a.typ', 'b.typ'))
        self.assertEqual(result.unresolved_findings, frozenset({'a.typ:diagnostic-only'}))
        self.assertEqual(self.a.read_bytes(), original.replace(b'bad', b'good'))
        self.assertEqual(self.config.read_bytes(), old_config)
        self.assertEqual(self.external.read_bytes(), old_external)
        manifest = json.loads((self.journal_path(result.journal) / 'manifest.json').read_bytes())
        self.assertEqual(base64.b64decode(manifest['files'][0]['before']), original)
        self.assertEqual(base64.b64decode(manifest['files'][0]['after']), self.a.read_bytes())
        self.assertEqual(len(manifest['evidence']), 2)
        self.assertFalse(any(self.root.glob('*.bin')))

    def test_no_lease_refuses_even_journal_creation(self):
        before = self.a.read_bytes()
        with self.assertRaisesRegex(TransactionError, 'offline-exclusive'):
            with self.context(lease=None):
                pass
        self.assertEqual(list(self.store.iterdir()), [])
        self.assertEqual(self.a.read_bytes(), before)

    def test_plain_boolean_or_uninitialized_lease_refused(self):
        for lease in (True, object.__new__(OfflineExclusive)):
            with self.subTest(lease=type(lease).__name__):
                with self.assertRaises(TransactionError):
                    with self.context(lease=lease):
                        pass

    def test_partial_analysis_or_parse_or_new_findings_refused(self):
        changes = [dict(implemented_rules=frozenset()), dict(evaluated_rules=frozenset()),
                   dict(parsed_sources=frozenset({'a.typ'})), dict(examined_evidence=frozenset()),
                   dict(parse_errors=('invalid syntax',)), dict(unsupported=('not implemented',))]
        for change in changes:
            with self.subTest(change=change):
                with self.context() as tx:
                    with self.assertRaises(TransactionError):
                        tx.validate(lambda graph: replace(synthetic_engine(graph), **change), required_rules=RULES)
        self.assertIn(b'bad', self.a.read_bytes())
        with self.context() as tx:
            def introduce(graph):
                state = synthetic_engine(graph)
                return replace(state, findings=frozenset({'new'}) if not state.edits else state.findings)
            with self.assertRaisesRegex(TransactionError, 'new findings'):
                tx.validate(introduce, required_rules=RULES)

    def test_cannot_supply_boolean_or_empty_rule_coverage(self):
        with self.context() as tx:
            with self.assertRaises(TransactionError):
                tx.validate(lambda graph: True, required_rules=RULES)
            with self.assertRaises(TransactionError):
                tx.validate(synthetic_engine, required_rules=frozenset())
            with self.assertRaises(TransactionError):
                tx.apply(True)

    def test_snapshot_is_immutable_and_token_bound_to_active_session(self):
        with self.context() as first:
            token = first.validate(synthetic_engine, required_rules=RULES)
            with self.assertRaises(TypeError):
                token.sources['a.typ'] = b'forged'
            with self.assertRaises(FrozenInstanceError):
                token.passes = 0
            with self.assertRaises(TypeError):
                first.snapshot.evidence[str(self.config)] = b'forged'
        with self.context() as second:
            with self.assertRaisesRegex(TransactionError, 'validated candidate'):
                second.apply(token)
            with self.assertRaises(TransactionError):
                second.apply(object.__new__(ValidatedCandidate))
        with self.assertRaises(TransactionError):
            first.apply(token)

    def test_zero_edit_candidate_reanalyzed_and_no_journal_created(self):
        self.a.write_bytes(b'already good\n')
        self.b.write_bytes(b'already good\n')
        calls = []
        with self.context() as tx:
            def engine(graph):
                calls.append(graph)
                return synthetic_engine(graph)
            result = tx.apply(tx.validate(engine, required_rules=RULES))
        self.assertEqual(len(calls), 2)
        self.assertIsNone(result.journal)
        self.assertEqual(result.modified, ())

    def test_nonidempotent_engine_refused(self):
        self.a.write_bytes(b'good\n')
        self.b.write_bytes(b'good\n')
        calls = 0
        def engine(graph):
            nonlocal calls
            calls += 1
            state = synthetic_engine(graph)
            return replace(state, findings=frozenset({'new'}) if calls == 2 else frozenset())
        with self.context() as tx:
            with self.assertRaisesRegex(TransactionError, 'idempotent'):
                tx.validate(engine, required_rules=RULES)

    def test_new_edits_cannot_escape_allowlist(self):
        with self.context() as tx:
            def engine(graph):
                return replace(synthetic_engine(graph), edits=(SourceEdit('../outside.typ', Edit(0, 0, b'oops')),))
            with self.assertRaisesRegex(TransactionError, 'allowlist'):
                tx.validate(engine, required_rules=RULES)

    def test_stale_bytes_including_preexisting_dirty_edits_are_not_overwritten(self):
        with self.context() as tx:
            token = tx.validate(synthetic_engine, required_rules=RULES)
            self.a.write_bytes(b'later dirty user edit\n')
            with self.assertRaisesRegex(TransactionError, 'Stale baseline'):
                tx.apply(token)
        self.assertEqual(self.a.read_bytes(), b'later dirty user edit\n')
        self.assertIn(b'bad', self.b.read_bytes())

    def test_same_hash_different_inode_refused(self):
        with self.context() as tx:
            token = tx.validate(synthetic_engine, required_rules=RULES)
            other = self.root / 'other.tmp'
            other.write_bytes(self.a.read_bytes())
            os.replace(other, self.a)
            with self.assertRaisesRegex(TransactionError, 'Stale baseline'):
                tx.apply(token)
        self.assertIn(b'bad', self.a.read_bytes())

    def test_outside_dependency_and_config_changes_block_writes(self):
        for path in (self.config, self.external):
            with self.subTest(path=path):
                with self.context() as tx:
                    token = tx.validate(synthetic_engine, required_rules=RULES)
                    path.write_bytes(path.read_bytes() + b'changed')
                    with self.assertRaisesRegex(TransactionError, 'Stale baseline'):
                        tx.apply(token)
                self.assertIn(b'bad', self.a.read_bytes())

    def test_absent_optional_evidence_is_baselined(self):
        absent = self.root / 'optional.json'
        with self.context(evidence=(EvidenceInput(absent, allow_missing=True),)) as tx:
            self.assertIsNone(tx.snapshot.evidence[str(absent)])
            token = tx.validate(synthetic_engine, required_rules=RULES)
            absent.write_bytes(b'{}')
            with self.assertRaisesRegex(TransactionError, 'Stale baseline'):
                tx.apply(token)

    def test_unrelated_outside_file_is_not_read_or_changed(self):
        unrelated = self.base / 'outside.typ'
        unrelated.write_bytes(b'not part of graph')
        def fault(point, path):
            if point == 'before_revalidate':
                unrelated.write_bytes(b'new outside work')
        self.apply(fault=fault)
        self.assertEqual(unrelated.read_bytes(), b'new outside work')

    def test_source_links_nonregular_escape_and_missing_parents_refused(self):
        original = self.a.read_bytes()
        for bad in ('../outside.typ', '/absolute.typ', './a.typ', 'dir//a.typ', 'x/../a.typ', 'a\\b.typ'):
            with self.subTest(bad=bad), self.assertRaises(TransactionError):
                self.context(targets=(bad,))
        link = self.root / 'link.typ'
        link.symlink_to(self.a)
        hard = self.root / 'hard.typ'
        os.link(self.b, hard)
        directory = self.root / 'dir.typ'
        directory.mkdir()
        fifo = self.root / 'fifo.typ'
        os.mkfifo(fifo)
        linked_dir = self.root / 'linked'
        linked_dir.symlink_to(self.root, target_is_directory=True)
        for target in ('link.typ', 'hard.typ', 'b.typ', 'dir.typ', 'fifo.typ', 'missing/a.typ', 'linked/a.typ'):
            with self.subTest(target=target):
                with self.assertRaises((OSError, TransactionError)):
                    with self.context(targets=(target,)):
                        pass
        self.assertEqual(self.a.read_bytes(), original)

    def test_symlinked_root_or_journal_component_refused(self):
        root_link = self.base / 'root-link'
        root_link.symlink_to(self.root, target_is_directory=True)
        store_link = self.base / 'store-link'
        store_link.symlink_to(self.store, target_is_directory=True)
        for kwargs in (dict(root=root_link), dict(journal_directory=store_link)):
            with self.assertRaises(OSError):
                with self.context(**kwargs):
                    pass

    def test_private_external_journal_directory_required(self):
        with self.assertRaises(TransactionError):
            self.context(journal_directory=self.root / '.transactions')
        insecure = self.base / 'public-journal'
        insecure.mkdir(mode=0o755)
        with self.assertRaisesRegex(TransactionError, 'owner-only'):
            with self.context(journal_directory=insecure):
                pass
        existing = self.store / 'unrelated-user-file'
        existing.write_bytes(b'do not overwrite')
        self.apply()
        self.assertEqual(existing.read_bytes(), b'do not overwrite')

    def test_mode_bom_crlf_unicode_and_unedited_bytes_preserved(self):
        original = b'\xef\xbb\xbf// ' + '\ud55c\uae00'.encode() + b'\r\nbad\r\n'
        self.a.write_bytes(original)
        self.a.chmod(0o640)
        self.apply()
        self.assertEqual(self.a.read_bytes(), original.replace(b'bad', b'good'))
        self.assertEqual(stat.S_IMODE(self.a.stat().st_mode), 0o640)

    def test_ownership_and_extended_attributes_preserved(self):
        import transactions
        before = self.a.stat()
        fd = os.open(self.a, os.O_RDONLY)
        try:
            attrs = transactions._xattrs(fd)
        finally:
            os.close(fd)
        self.apply()
        after = self.a.stat()
        self.assertEqual((after.st_uid, after.st_gid), (before.st_uid, before.st_gid))
        fd = os.open(self.a, os.O_RDONLY)
        try:
            self.assertEqual(transactions._xattrs(fd), attrs)
        finally:
            os.close(fd)

    def test_foreign_owned_source_refused_but_foreign_evidence_readable(self):
        original_fstat = os.fstat
        source_inode = self.a.stat().st_ino
        def forged_source_owner(fd):
            value = original_fstat(fd)
            if value.st_ino == source_inode:
                fields = list(value)
                fields[4] = os.geteuid() + 1
                return os.stat_result(fields)
            return value
        with mock.patch('transactions.os.fstat', side_effect=forged_source_owner):
            with self.assertRaisesRegex(TransactionError, 'not owned'):
                with self.context():
                    pass
        # Evidence ownership is recorded, not required to equal the caller's.
        import transactions
        original_read = transactions._read
        def evidence_owned_by_other(path, **kwargs):
            value = original_read(path, **kwargs)
            if path == self.external:
                return replace(value, fingerprint=replace(value.fingerprint, uid=os.geteuid() + 1))
            return value
        with mock.patch('transactions._read', side_effect=evidence_owned_by_other):
            self.apply()

    def add_fixture_xattr(self):
        import transactions
        fd = os.open(self.a, os.O_RDONLY)
        try:
            attrs = transactions._xattrs(fd)
            transactions._copy_xattrs(fd, tuple(sorted((*attrs, (FIXTURE_XATTR, b'keep-me')))))
        finally:
            os.close(fd)

    def remove_fixture_xattr(self, stage):
        import transactions
        fd = os.open(stage, os.O_RDONLY)
        try:
            attrs = transactions._xattrs(fd)
            transactions._copy_xattrs(fd, tuple(pair for pair in attrs if pair[0] != FIXTURE_XATTR))
        finally:
            os.close(fd)

    def test_apply_staging_metadata_tamper_never_replaces_source(self):
        self.add_fixture_xattr()
        before = self.a.read_bytes(), self.a.stat().st_ino
        def fault(point, path):
            if point == 'stage_fsync' and path == 'a.typ':
                self.remove_fixture_xattr(next(self.store.glob('root-*/*/stage-0.bin')))
        with self.assertRaises(TransactionFailure) as caught:
            self.apply(fault=fault)
        self.assertIn('Staged bytes or metadata changed', str(caught.exception))
        self.assertEqual((self.a.read_bytes(), self.a.stat().st_ino), before)
        self.assertEqual(caught.exception.report.restored, ())
        self.assertTrue(self.recover(caught.exception.report.journal).complete)

    def test_recovery_staging_metadata_tamper_never_replaces_postimage(self):
        self.add_fixture_xattr()
        def crash(point, path):
            if point == 'after_replace' and path == 'a.typ':
                raise SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            self.apply(fault=crash)
        journal = next(next(self.store.glob('root-*')).iterdir()).name
        postimage = self.a.read_bytes(), self.a.stat().st_ino
        def fault(point, path):
            if point == 'stage_fsync' and path == 'a.typ':
                self.remove_fixture_xattr(next(self.journal_path(journal).glob('restore-*.bin')))
        with self.context(recovery_only=True, fault=fault) as tx:
            report = tx.recover(journal)
        self.assertFalse(report.complete)
        self.assertEqual(report.unresolved, ('a.typ',))
        self.assertIn('Staged bytes or metadata changed', report.errors[0])
        self.assertEqual((self.a.read_bytes(), self.a.stat().st_ino), postimage)
        self.assertTrue(self.recover(journal).complete)

    def test_readonly_source_target_refused_without_changing_it(self):
        self.a.chmod(0o444)
        before = self.a.read_bytes(), self.a.stat().st_ino, stat.S_IMODE(self.a.stat().st_mode)
        with mock.patch('transactions.os.access', side_effect=AssertionError('Do not infer permission with os.access')):
            with self.assertRaisesRegex(TransactionError, 'not owner-writable: a.typ'):
                with self.context():
                    pass
        self.assertEqual((self.a.read_bytes(), self.a.stat().st_ino,
                          stat.S_IMODE(self.a.stat().st_mode)), before)
        self.assertIn(b'bad', self.b.read_bytes())
        self.assertFalse(any(self.store.glob('root-*/*/manifest.json')))

    def test_readonly_evidence_is_allowed(self):
        self.config.chmod(0o444)
        self.external.chmod(0o444)
        self.apply()
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o444)
        self.assertEqual(stat.S_IMODE(self.external.stat().st_mode), 0o444)

    def test_recovery_can_reconcile_readonly_conflict_without_chmod(self):
        failure = self.failed_after_first(change=True)
        self.a.chmod(0o444)
        before = self.a.read_bytes(), self.a.stat().st_ino
        report = self.recover(failure.report.journal)
        self.assertEqual(report.changed, ('a.typ',))
        self.assertFalse(report.complete)
        resolved = self.recover(report.journal, preserve_conflicts=report.conflicts)
        self.assertTrue(resolved.complete)
        self.assertEqual(resolved.preserved, ('a.typ',))
        self.assertEqual((self.a.read_bytes(), self.a.stat().st_ino), before)
        self.assertEqual(stat.S_IMODE(self.a.stat().st_mode), 0o444)

    def test_special_permission_bits_refused(self):
        self.a.chmod(0o4644)
        with self.assertRaisesRegex(TransactionError, 'Special permission'):
            with self.context():
                pass
        self.assertIn(b'bad', self.a.read_bytes())

    def test_metadata_probe_unavailable_fails_closed(self):
        with mock.patch('transactions.sys.platform', 'unsupported'):
            with self.assertRaisesRegex(TransactionError, 'supported only'):
                with self.context():
                    pass
        self.assertIn(b'bad', self.a.read_bytes())

    def test_mode_change_after_validation_is_stale(self):
        with self.context() as tx:
            token = tx.validate(synthetic_engine, required_rules=RULES)
            self.a.chmod(0o600)
            with self.assertRaisesRegex(TransactionError, 'Stale baseline'):
                tx.apply(token)
        self.assertEqual(stat.S_IMODE(self.a.stat().st_mode), 0o600)

    def test_invalid_utf8_or_bom_removal_refused(self):
        self.a.write_bytes(b'\xffbad')
        with self.assertRaisesRegex(TransactionError, 'UTF-8'):
            with self.context():
                pass
        self.a.write_bytes(b'\xef\xbb\xbfbad\n')
        def engine(graph):
            state = synthetic_engine(graph)
            return replace(state, edits=(SourceEdit('a.typ', Edit(0, 3, b'')),))
        with self.context() as tx:
            with self.assertRaisesRegex(TransactionError, 'BOM'):
                tx.validate(engine, required_rules=RULES)

    def test_root_lock_contention_even_with_different_private_store(self):
        alternative = self.base / 'another-store'
        alternative.mkdir(mode=0o700)
        with self.context():
            for store in (self.store, alternative):
                with self.assertRaisesRegex(TransactionError, 'Root lock'):
                    with self.context(journal_directory=store):
                        pass
        self.apply()

    def test_partial_failure_rolls_back_then_requires_explicit_recovery(self):
        original_a, original_b = self.a.read_bytes(), self.b.read_bytes()
        failure = self.failed_after_first()
        self.assertEqual(failure.report.restored, ('a.typ',))
        self.assertEqual(failure.report.unchanged, ('b.typ',))
        self.assertEqual(failure.report.unresolved, ())
        self.assertFalse(failure.report.complete)
        self.assertEqual(self.a.read_bytes(), original_a)
        self.assertEqual(self.b.read_bytes(), original_b)
        with self.assertRaises(RecoveryRequired):
            with self.context():
                pass
        report = self.recover(failure.report.journal)
        self.assertTrue(report.complete)
        self.apply()

    def test_rollback_conflict_preserves_user_work_until_exact_resolution(self):
        failure = self.failed_after_first(change=True)
        self.assertEqual(failure.report.changed, ('a.typ',))
        self.assertEqual(failure.report.unresolved, ('a.typ',))
        self.assertEqual(self.a.read_bytes(), b'new user work\n')
        report = self.recover(failure.report.journal)
        self.assertFalse(report.complete)
        self.assertEqual(report.changed, ('a.typ',))
        resolved = self.recover(report.journal, preserve_conflicts=report.conflicts)
        self.assertTrue(resolved.complete)
        self.assertEqual(resolved.preserved, ('a.typ',))
        self.assertEqual(self.a.read_bytes(), b'new user work\n')

    def test_stale_explicit_conflict_resolution_does_not_clear_gate(self):
        report = self.failed_after_first(change=True).report
        self.a.write_bytes(b'newer user work\n')
        still = self.recover(report.journal, preserve_conflicts=report.conflicts)
        self.assertFalse(still.complete)
        self.assertEqual(still.unresolved, ('a.typ',))
        self.assertEqual(self.a.read_bytes(), b'newer user work\n')

    def test_recovery_does_not_recreate_missing_source(self):
        report = self.failed_after_first(change=True).report
        self.a.unlink()
        recovered = self.recover(report.journal)
        self.assertFalse(recovered.complete)
        self.assertEqual(recovered.unresolved, ('a.typ',))
        self.assertFalse(self.a.exists())

    def test_crash_after_replace_before_progress_is_recoverable(self):
        original = self.a.read_bytes()
        def fault(point, path):
            if point == 'after_replace' and path == 'a.typ':
                raise SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            self.apply(fault=fault)
        self.assertIn(b'good', self.a.read_bytes())
        with self.assertRaises(RecoveryRequired) as caught:
            with self.context():
                pass
        report = self.recover(caught.exception.journals[0])
        self.assertEqual(report.restored, ('a.typ',))
        self.assertTrue(report.complete)
        self.assertEqual(self.a.read_bytes(), original)
        self.apply()

    def test_stage_creation_write_and_fsync_failures_preserve_sources(self):
        for point in ('stage_create', 'stage_write', 'stage_fsync', 'journal_parent_fsync',
                      'journal_fsync', 'journal_directory_fsync'):
            with self.subTest(point=point):
                store = self.base / ('journal-' + point)
                store.mkdir(mode=0o700)
                before = (self.a.read_bytes(), self.b.read_bytes())
                fired = False
                def fault(where, path):
                    nonlocal fired
                    if where == point and not fired:
                        fired = True
                        raise OSError('injected ' + point)
                with self.assertRaises(TransactionFailure):
                    self.apply(journal_directory=store, fault=fault)
                self.assertTrue(fired)
                self.assertEqual((self.a.read_bytes(), self.b.read_bytes()), before)
                with self.assertRaises(RecoveryRequired):
                    with self.context(journal_directory=store):
                        pass

    def test_real_os_replace_failure_rolls_back_first_file(self):
        original_replace = os.replace
        calls = 0
        def replace_file(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('simulated rename failure')
            return original_replace(*args, **kwargs)
        old = self.a.read_bytes()
        with mock.patch('transactions.os.replace', side_effect=replace_file):
            with self.assertRaises(TransactionFailure) as caught:
                self.apply()
        self.assertEqual(caught.exception.report.restored, ('a.typ',))
        self.assertEqual(self.a.read_bytes(), old)
        self.assertTrue(self.recover(caught.exception.report.journal).complete)

    def test_directory_fsync_failure_after_replace_is_recovered(self):
        fired = False
        def fault(point, path):
            nonlocal fired
            if point == 'source_directory_fsync' and not fired:
                fired = True
                raise OSError('injected fsync')
        old = self.a.read_bytes()
        with self.assertRaises(TransactionFailure) as caught:
            self.apply(fault=fault)
        self.assertEqual(caught.exception.report.restored, ('a.typ',))
        self.assertEqual(self.a.read_bytes(), old)
        self.assertTrue(self.recover(caught.exception.report.journal).complete)

    def test_completion_record_fsync_failure_still_blocks_until_recovery(self):
        for point in ('journal_fsync', 'journal_directory_fsync'):
            with self.subTest(point=point):
                store = self.base / ('completion-' + point)
                store.mkdir(mode=0o700)
                before = (self.a.read_bytes(), self.b.read_bytes())
                fired = False
                def fault(where, path):
                    nonlocal fired
                    if where == point and path == 'event-00000006.json' and not fired:
                        fired = True
                        raise OSError('completion durability failure')
                with self.assertRaises(TransactionFailure) as caught:
                    self.apply(journal_directory=store, fault=fault)
                self.assertTrue(fired)
                self.assertEqual(caught.exception.report.restored, ('a.typ', 'b.typ'))
                self.assertEqual((self.a.read_bytes(), self.b.read_bytes()), before)
                with self.assertRaises(RecoveryRequired):
                    with self.context(journal_directory=store):
                        pass
                with self.context(journal_directory=store, recovery_only=True) as tx:
                    self.assertTrue(tx.recover(caught.exception.report.journal).complete)

    def test_write_error_during_rollback_remains_pending(self):
        def fault(point, path):
            if point == 'before_replace' and path == 'b.typ':
                raise OSError('first error')
            if point == 'before_restore':
                raise OSError('rollback error')
        old = self.a.read_bytes()
        with self.assertRaises(TransactionFailure) as caught:
            self.apply(fault=fault)
        self.assertEqual(caught.exception.report.unresolved, ('a.typ',))
        self.assertIn(b'good', self.a.read_bytes())
        report = self.recover(caught.exception.report.journal)
        self.assertTrue(report.complete)
        self.assertEqual(self.a.read_bytes(), old)

    def test_recovery_completion_fsync_failure_keeps_gate_closed(self):
        failure = self.failed_after_first()
        fired = False
        def fault(point, path):
            nonlocal fired
            if point == 'recovery_directory_fsync' and not fired:
                fired = True
                raise OSError('cannot make recovery durable')
        with self.context(recovery_only=True, fault=fault) as tx:
            report = tx.recover(failure.report.journal)
        self.assertFalse(report.complete)
        self.assertEqual(report.unresolved, ('a.typ', 'b.typ'))
        with self.assertRaises(RecoveryRequired):
            with self.context():
                pass
        self.assertTrue(self.recover(report.journal).complete)

    def test_crash_after_restore_before_progress_recovers_again(self):
        store = self.store
        self.a.write_bytes(b'bad KEEP\n')
        def crash_apply(point, path):
            if point == 'after_replace' and path == 'a.typ':
                raise SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            self.apply(journal_directory=store, fault=crash_apply)
        journal = next(next(store.glob('root-*')).iterdir()).name
        def crash_restore(point, path):
            if point == 'after_restore' and path == 'a.typ':
                raise SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            with self.context(journal_directory=store, recovery_only=True, fault=crash_restore) as tx:
                tx.recover(journal)
        with self.context(journal_directory=store, recovery_only=True) as tx:
            report = tx.recover(journal)
        self.assertTrue(report.complete)
        self.assertEqual(self.a.read_bytes(), b'bad KEEP\n')

    def test_config_change_between_staging_and_writing_blocks_all_source_writes(self):
        def fault(point, path):
            if point == 'before_revalidate':
                self.config.write_bytes(b'changed config')
        old = self.a.read_bytes()
        with self.assertRaises(TransactionFailure) as caught:
            self.apply(fault=fault)
        self.assertEqual(caught.exception.report.restored, ())
        self.assertEqual(self.a.read_bytes(), old)
        self.assertEqual(self.config.read_bytes(), b'changed config')

    def test_crash_then_new_user_inode_is_not_rolled_back_even_at_same_bytes(self):
        def fault(point, path):
            if point == 'after_replace' and path == 'a.typ':
                raise SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            self.apply(fault=fault)
        new = self.root / 'user-temp'
        new.write_bytes(self.a.read_bytes())
        os.replace(new, self.a)
        with self.assertRaises(RecoveryRequired) as caught:
            with self.context():
                pass
        report = self.recover(caught.exception.journals[0])
        self.assertFalse(report.complete)
        self.assertEqual(report.changed, ('a.typ',))

    def test_malicious_manifest_paths_and_stage_paths_are_refused(self):
        failure = self.failed_after_first(change=True)
        manifest_path = self.journal_path(failure.report.journal) / 'manifest.json'
        original = json.loads(manifest_path.read_bytes())
        outside = self.base / 'outside.typ'
        outside.write_bytes(b'outside work')
        for field, bad in (('path', '../outside.typ'), ('path', str(outside)),
                           ('stage', '../outside.typ'), ('stage', str(outside))):
            modified = json.loads(json.dumps(original))
            modified['files'][0][field] = bad
            manifest_path.write_text(json.dumps(modified))
            with self.assertRaises(TransactionError):
                self.recover(failure.report.journal)
            self.assertEqual(outside.read_bytes(), b'outside work')
        manifest_path.write_text(json.dumps(original))
        with self.context(recovery_only=True, targets=('b.typ',)) as tx:
            with self.assertRaisesRegex(TransactionError, 'allowlist'):
                tx.recover(failure.report.journal)

    def test_journal_symlinks_and_existing_collisions_do_not_overwrite_files(self):
        failure = self.failed_after_first(change=True)
        location = self.journal_path(failure.report.journal)
        manifest = location / 'manifest.json'
        original = manifest.read_bytes()
        manifest.unlink()
        victim = self.base / 'victim'
        victim.write_bytes(original)
        manifest.symlink_to(victim)
        with self.assertRaises(TransactionError):
            self.recover(failure.report.journal)
        self.assertEqual(victim.read_bytes(), original)
        with self.assertRaises(RecoveryRequired):
            with self.context():
                pass

    def test_incomplete_or_malformed_journal_blocks_subsequent_transactions(self):
        with self.context():
            pass
        namespace = next(self.store.glob('root-*'))
        damaged = namespace / ('f' * 32)
        damaged.mkdir(mode=0o700)
        with self.assertRaises(RecoveryRequired):
            with self.context():
                pass
        with self.assertRaises(FileNotFoundError):
            self.recover(damaged.name)
        (damaged / 'manifest.json').write_bytes(b'{broken')
        with self.assertRaises(RecoveryRequired):
            with self.context():
                pass

    def test_two_complete_successive_transactions_retain_both_journals(self):
        first = self.apply()
        first_before = (self.journal_path(first.journal) / 'manifest.json').read_bytes()
        self.b.write_bytes(b'another bad\n')
        second = self.apply()
        self.assertNotEqual(first.journal, second.journal)
        self.assertEqual(self.b.read_bytes(), b'another good\n')
        self.assertEqual((self.journal_path(first.journal) / 'manifest.json').read_bytes(), first_before)
        self.assertEqual(second.modified, ('b.typ',))
        self.assertEqual(len(list(next(self.store.glob('root-*')).iterdir())), 2)

    def test_recovery_mode_cannot_create_candidate_or_apply(self):
        with self.context(recovery_only=True) as tx:
            with self.assertRaises(TransactionError):
                tx.validate(synthetic_engine, required_rules=RULES)
            with self.assertRaises(TransactionError):
                tx.apply(True)


if __name__ == '__main__':
    unittest.main()
