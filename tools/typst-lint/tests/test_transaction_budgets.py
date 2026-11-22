"""Bounded protected captures, exclusively using small temporary consumers."""
from collections import Counter
from dataclasses import FrozenInstanceError, replace
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import transactions
from edit_plan import Edit
from transactions import (DEFAULT_READ_LIMITS, EvidenceInput, GraphAnalysis,
                          OfflineExclusive, ProtectedTransaction, ReadLimits,
                          RecoveryRequired, SourceEdit, TransactionError,
                          TransactionFailure)

RULES = frozenset({'fixture.transaction.bounds'})
XATTR = 'com.example.bounds' if sys.platform == 'darwin' else 'user.com.example.bounds'


def engine(graph):
    edits = tuple(SourceEdit(p, Edit(0, 3, b'fix')) for p, data in graph.sources.items()
                  if data.startswith(b'bad'))
    return GraphAnalysis(RULES, RULES, frozenset(graph.sources), frozenset(graph.evidence),
                         findings=frozenset(p for p, data in graph.sources.items() if data.startswith(b'bad')),
                         edits=edits)


class Crash(BaseException):
    pass


@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX metadata protocol')
class TransactionBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'consumer'
        self.root.mkdir()
        self.store = self.base / 'journal'
        self.store.mkdir(mode=0o700)
        self.a = self.root / 'a.typ'
        self.a.write_bytes(b'bad')
        self.lease = OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers()

    def context(self, **kwargs):
        options = dict(journal_directory=self.store, lease=self.lease)
        options.update(kwargs)
        return ProtectedTransaction(self.root, options.pop('targets', ('a.typ',)), **options)

    def metadata_bytes(self, path):
        fd = os.open(path, os.O_RDONLY)
        try:
            return transactions._metadata_size(transactions._xattrs(fd))
        finally:
            os.close(fd)

    def apply(self, **kwargs):
        with self.context(**kwargs) as tx:
            return tx.apply(tx.validate(engine, required_rules=RULES))

    def crash_journal(self, **kwargs):
        def crash(point, path):
            if point == 'after_replace':
                raise Crash()
        with self.assertRaises(Crash):
            self.apply(fault=crash, **kwargs)
        return next(next(self.store.glob('root-*')).iterdir())

    def assert_closed_read(self, operation, exception):
        opened, closed = [], []
        original_open, original_close = os.open, os.close
        def track_open(*args, **kwargs):
            fd = original_open(*args, **kwargs)
            opened.append(fd)
            return fd
        def track_close(fd):
            closed.append(fd)
            return original_close(fd)
        with mock.patch('transactions.os.open', side_effect=track_open), \
                mock.patch('transactions.os.close', side_effect=track_close):
            with self.assertRaises(exception):
                operation()
        self.assertTrue(opened)
        self.assertEqual(Counter(opened), Counter(closed))

    def test_defaults_and_limits_are_immutable(self):
        self.assertEqual(DEFAULT_READ_LIMITS.max_source_bytes, 1024 * 1024)
        self.assertEqual(DEFAULT_READ_LIMITS.max_evidence_bytes, 4 * 1024 * 1024)
        self.assertEqual(DEFAULT_READ_LIMITS.max_capture_bytes, 16 * 1024 * 1024)
        with self.assertRaises(FrozenInstanceError):
            DEFAULT_READ_LIMITS.max_source_bytes = 1
        with self.assertRaises(AttributeError):
            self.context().limits = ReadLimits()

    def test_negative_bool_nonfinite_and_other_invalid_limits(self):
        for field in ReadLimits.__dataclass_fields__:
            for invalid in (-1, True, False, 1.5, float('nan'), float('inf'), None, '10'):
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaises(TransactionError):
                        ReadLimits(**{field: invalid})
        for mutable in ({'max_source_bytes': 3}, [], None):
            with self.assertRaisesRegex(TransactionError, 'immutable ReadLimits'):
                self.context(limits=mutable)

    def test_default_source_at_one_mib_and_one_byte_over(self):
        self.a.write_bytes(b'a' * DEFAULT_READ_LIMITS.max_source_bytes)
        with self.context() as tx:
            self.assertEqual(len(tx.snapshot.sources['a.typ']), 1024 * 1024)
        with self.a.open('ab') as stream:
            stream.write(b'x')
        with mock.patch('transactions.os.read', side_effect=AssertionError('Oversize must be rejected by fstat')):
            self.assert_closed_read(lambda: transactions._read(self.a, utf8=True), TransactionError)

    def test_zero_source_and_capture_allow_only_empty_files(self):
        self.a.write_bytes(b'')
        cost = self.metadata_bytes(self.a)
        limits = replace(ReadLimits(), max_source_bytes=0, max_capture_bytes=cost)
        with self.context(limits=limits) as tx:
            self.assertEqual(tx.snapshot.sources['a.typ'], b'')
        self.a.write_bytes(b'x')
        with self.assertRaises(TransactionError):
            with self.context(limits=limits):
                pass

    def test_evidence_per_file_limit_and_binary_at_limit(self):
        evidence = self.base / 'resource.bin'
        evidence.write_bytes(b'\xff' * 16)
        limits = replace(ReadLimits(), max_evidence_bytes=16)
        with self.context(evidence=(EvidenceInput(evidence),), limits=limits) as tx:
            self.assertEqual(tx.snapshot.evidence[str(evidence)], b'\xff' * 16)
        evidence.write_bytes(b'\xff' * 17)
        with self.assertRaisesRegex(TransactionError, 'limit exceeded'):
            with self.context(evidence=(EvidenceInput(evidence),), limits=limits):
                pass

    def test_source_and_evidence_share_exact_aggregate_budget(self):
        b = self.root / 'b.typ'
        b.write_bytes(b'abc')
        resource = self.base / 'resource.bin'
        resource.write_bytes(b'12')
        cost = 8 + sum(self.metadata_bytes(p) for p in (self.a, b, resource))
        options = dict(targets=('a.typ', 'b.typ'), evidence=(EvidenceInput(resource),))
        with self.context(**options, limits=replace(ReadLimits(), max_capture_bytes=cost)) as tx:
            self.assertEqual(len(tx.snapshot.sources), 2)
            tx.validate(engine, required_rules=RULES)  # Repeated reads reset the view budget.
        with self.assertRaisesRegex(TransactionError, 'limit exceeded'):
            with self.context(**options, limits=replace(ReadLimits(), max_capture_bytes=cost - 1)):
                pass

    def test_all_evidence_files_share_aggregate_budget(self):
        files = (self.base / 'one', self.base / 'two')
        for p in files:
            p.write_bytes(b'12345')
        cost = 10 + sum(self.metadata_bytes(p) for p in files)
        with self.assertRaisesRegex(TransactionError, 'limit exceeded'):
            with self.context(targets=(), evidence=tuple(EvidenceInput(p) for p in files),
                              limits=replace(ReadLimits(), max_capture_bytes=cost - 1)):
                pass

    def test_absent_optional_evidence_consumes_no_bytes(self):
        self.a.write_bytes(b'')
        missing = self.base / 'missing'
        limits = replace(ReadLimits(), max_evidence_bytes=0, max_capture_bytes=self.metadata_bytes(self.a))
        with self.context(evidence=(EvidenceInput(missing, allow_missing=True),), limits=limits) as tx:
            self.assertIsNone(tx.snapshot.evidence[str(missing)])

    def test_growth_during_streaming_uses_only_limit_plus_one_and_closes(self):
        self.a.write_bytes(b'12345678')
        inode, original_read, requests = self.a.stat().st_ino, os.read, []
        fired = False
        def growing(fd, amount):
            nonlocal fired
            if os.fstat(fd).st_ino == inode:
                requests.append(amount)
                if not fired:
                    fired = True
                    with self.a.open('ab') as stream:
                        stream.write(b'x' * 32)
            return original_read(fd, amount)
        with mock.patch('transactions.os.read', side_effect=growing):
            self.assert_closed_read(lambda: transactions._read(
                self.a, utf8=True, limits=replace(ReadLimits(), max_source_bytes=8)), TransactionError)
        self.assertEqual(requests, [9])

    def test_growth_after_eof_is_rejected_by_final_fstat(self):
        original_read, fired = os.read, False
        def grow_at_eof(fd, amount):
            nonlocal fired
            data = original_read(fd, amount)
            if not data and not fired:
                fired = True
                with self.a.open('ab') as stream:
                    stream.write(b'x')
            return data
        with mock.patch('transactions.os.read', side_effect=grow_at_eof):
            with self.assertRaisesRegex(TransactionError, 'changed while reading'):
                transactions._read(self.a, utf8=True)

    def test_read_interruption_closes_file_and_all_parent_descriptors(self):
        with mock.patch('transactions.os.read', side_effect=KeyboardInterrupt):
            self.assert_closed_read(lambda: transactions._read(self.a, utf8=True), KeyboardInterrupt)

    def test_stream_requests_are_chunk_bounded_even_with_large_limit(self):
        self.a.write_bytes(b'a' * (transactions._READ_CHUNK_BYTES + 4))
        original_read, requests = os.read, []
        def recording(fd, amount):
            requests.append(amount)
            return original_read(fd, amount)
        with mock.patch('transactions.os.read', side_effect=recording):
            self.assertEqual(len(transactions._read(self.a, utf8=True).data), len(self.a.read_bytes()))
        self.assertTrue(requests)
        self.assertLessEqual(max(requests), 64 * 1024)

    def test_xattr_names_and_values_are_bounded_before_allocation(self):
        fd = os.open(self.a, os.O_RDONLY)
        try:
            old = transactions._xattrs(fd)
            wanted = tuple(sorted((*old, (XATTR, b'12345678'))))
            transactions._copy_xattrs(fd, wanted)
            cost = transactions._metadata_size(wanted)
            self.assertEqual(transactions._xattrs(fd, max_bytes=cost), wanted)
            with self.assertRaisesRegex(TransactionError, 'Metadata read limit'):
                transactions._xattrs(fd, max_bytes=cost - 1)
        finally:
            os.close(fd)
        limits = replace(ReadLimits(), max_capture_bytes=3 + cost)
        self.assertEqual(transactions._read(self.a, utf8=True, limits=limits).data, b'bad')
        with self.assertRaisesRegex(TransactionError, 'Metadata read limit'):
            transactions._read(self.a, utf8=True, limits=replace(limits, max_capture_bytes=2 + cost))

    def test_revalidation_uses_source_limit_not_larger_evidence_limit(self):
        with self.context(limits=replace(ReadLimits(), max_source_bytes=3)) as tx:
            self.a.write_bytes(b'1234')
            with self.assertRaisesRegex(TransactionError, 'limit exceeded'):
                tx.validate(engine, required_rules=RULES)

    def test_evidence_revalidation_cannot_bypass_limit(self):
        resource = self.base / 'resource'
        resource.write_bytes(b'12')
        with self.context(evidence=(EvidenceInput(resource),),
                          limits=replace(ReadLimits(), max_evidence_bytes=2)) as tx:
            resource.write_bytes(b'123')
            with self.assertRaisesRegex(TransactionError, 'limit exceeded'):
                tx.validate(engine, required_rules=RULES)

    def test_candidate_growth_is_rejected_before_materializing_it(self):
        def grow(graph):
            return replace(engine(graph), edits=(SourceEdit('a.typ', Edit(0, 0, b'x')),))
        with self.context(limits=replace(ReadLimits(), max_source_bytes=3)) as tx:
            with mock.patch('edit_plan.Plan.candidate', side_effect=AssertionError('Must check planned size first')):
                with self.assertRaisesRegex(TransactionError, 'Candidate source byte limit'):
                    tx.validate(grow, required_rules=RULES)
        self.assertEqual(self.a.read_bytes(), b'bad')
        self.assertFalse(any(self.store.glob('root-*/*')))

    def test_candidate_aggregate_growth_is_rejected(self):
        b = self.root / 'b.typ'
        b.write_bytes(b'bad')
        def grow(graph):
            return replace(engine(graph), edits=tuple(SourceEdit(p, Edit(0, 0, b'x')) for p in graph.sources))
        cost = 7 + self.metadata_bytes(self.a) + self.metadata_bytes(b)
        with self.context(targets=('a.typ', 'b.typ'), limits=replace(ReadLimits(), max_capture_bytes=cost)) as tx:
            with self.assertRaisesRegex(TransactionError, 'Aggregate read budget'):
                tx.validate(grow, required_rules=RULES)

    def test_staging_fsync_growth_cannot_trigger_unbounded_readback(self):
        def grow(point, path):
            if point == 'stage_fsync':
                stage = next(next(next(self.store.glob('root-*')).iterdir()).glob('stage-*.bin'))
                with stage.open('ab') as stream:
                    stream.write(b'x' * 32)
        with self.assertRaises(TransactionFailure) as caught:
            self.apply(limits=replace(ReadLimits(), max_source_bytes=8), fault=grow)
        self.assertIn('limit exceeded', str(caught.exception))
        self.assertEqual(self.a.read_bytes(), b'bad')

    def test_json_at_limit_and_over_limit_close_descriptors(self):
        path = self.base / 'small.json'
        data = b'{"ok":true}\n'
        path.write_bytes(data)
        path.chmod(0o600)
        tx = self.context(limits=replace(ReadLimits(), max_journal_bytes=len(data)))
        fd = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY)
        try:
            self.assertEqual(tx._load_json(fd, path.name), {'ok': True})
            path.write_bytes(data + b' ')
            self.assert_closed_read(lambda: tx._load_json(fd, path.name), TransactionError)
        finally:
            os.close(fd)

    def test_json_growth_during_read_is_bounded_and_closed(self):
        path = self.base / 'small.json'
        data = b'{"ok":true}\n'
        path.write_bytes(data)
        path.chmod(0o600)
        tx = self.context(limits=replace(ReadLimits(), max_journal_bytes=len(data)))
        directory = os.open(self.base, os.O_RDONLY | os.O_DIRECTORY)
        original_read, fired, requests = os.read, False, []
        def grow(fd, amount):
            nonlocal fired
            requests.append(amount)
            if not fired:
                fired = True
                with path.open('ab') as stream:
                    stream.write(b'x' * 64)
            return original_read(fd, amount)
        try:
            with mock.patch('transactions.os.read', side_effect=grow):
                self.assert_closed_read(lambda: tx._load_json(directory, path.name), TransactionError)
            self.assertEqual(requests, [len(data) + 1])
        finally:
            os.close(directory)

    def test_old_v1_journal_obeys_source_limits_and_larger_recovery_is_explicit(self):
        location = self.crash_journal()
        with self.context(recovery_only=True, limits=replace(ReadLimits(), max_source_bytes=2)) as tx:
            with self.assertRaisesRegex(TransactionError, 'decoded byte limit'):
                tx.recover(location.name)
        self.assertEqual(self.a.read_bytes(), b'fix')
        with self.assertRaises(RecoveryRequired):
            with self.context():
                pass
        with self.context(recovery_only=True, limits=replace(ReadLimits(), max_source_bytes=3)) as tx:
            self.assertTrue(tx.recover(location.name).complete)
        self.assertEqual(self.a.read_bytes(), b'bad')

    def test_manifest_and_events_share_journal_total_limit(self):
        location = self.crash_journal()
        total = sum(p.stat().st_size for p in location.glob('*.json'))
        limits = replace(ReadLimits(), max_journal_total_bytes=total - 1)
        with self.context(recovery_only=True, limits=limits) as tx:
            with self.assertRaisesRegex(TransactionError, 'limit exceeded'):
                tx.recover(location.name)
        self.assertEqual(self.a.read_bytes(), b'fix')
        with self.context(recovery_only=True) as tx:
            self.assertTrue(tx.recover(location.name).complete)

    def test_recovery_manifest_evidence_and_aggregate_cannot_bypass_limits(self):
        resource = self.base / 'resource'
        resource.write_bytes(b'12345678')
        location = self.crash_journal(evidence=(EvidenceInput(resource),))
        variants = (replace(ReadLimits(), max_evidence_bytes=7),
                    replace(ReadLimits(), max_capture_bytes=10 + self.metadata_bytes(self.a)))
        for limits in variants:
            with self.subTest(limits=limits):
                with self.context(recovery_only=True, limits=limits) as tx:
                    with self.assertRaisesRegex(TransactionError, 'decoded byte limit'):
                        tx.recover(location.name)
        self.assertEqual(self.a.read_bytes(), b'fix')
        with self.context(recovery_only=True) as tx:
            self.assertTrue(tx.recover(location.name).complete)

    def test_recovery_current_graph_budget_reserves_recorded_evidence(self):
        resource = self.base / 'resource'
        resource.write_bytes(b'12345678')
        location = self.crash_journal(evidence=(EvidenceInput(resource),))
        self.a.write_bytes(b'user')
        cost = 11 + self.metadata_bytes(self.a)
        with self.context(recovery_only=True, limits=replace(ReadLimits(), max_capture_bytes=cost)) as tx:
            report = tx.recover(location.name)
        self.assertFalse(report.complete)
        self.assertIn('limit exceeded', report.errors[0])
        self.assertEqual(self.a.read_bytes(), b'user')

    def test_journal_aggregate_write_limit_is_not_just_a_per_file_limit(self):
        def observe(point, path):
            if point == 'before_replace':
                raise Crash()
        with self.assertRaises(Crash):
            self.apply(fault=observe)
        location = next(next(self.store.glob('root-*')).iterdir())
        manifest_size = (location / 'manifest.json').stat().st_size
        with self.context(recovery_only=True) as tx:
            self.assertTrue(tx.recover(location.name).complete)
        fresh_store = self.base / 'fresh-journal'
        fresh_store.mkdir(mode=0o700)
        with self.assertRaises(TransactionFailure) as caught:
            self.apply(journal_directory=fresh_store,
                       limits=replace(ReadLimits(), max_journal_total_bytes=manifest_size))
        self.assertIn('Aggregate journal write limit', str(caught.exception))
        self.assertEqual(self.a.read_bytes(), b'bad')

    def test_journal_write_limits_refuse_without_source_replacement(self):
        with self.assertRaises(TransactionFailure) as caught:
            self.apply(limits=replace(ReadLimits(), max_journal_bytes=8))
        self.assertIn('Write byte limit', str(caught.exception))
        self.assertEqual(self.a.read_bytes(), b'bad')

    def test_recovery_live_file_growth_is_preserved_and_reported(self):
        location = self.crash_journal()
        self.a.write_bytes(b'new user work exceeds limit')
        with self.context(recovery_only=True, limits=replace(ReadLimits(), max_source_bytes=8)) as tx:
            report = tx.recover(location.name)
        self.assertFalse(report.complete)
        self.assertEqual(report.unresolved, ('a.typ',))
        self.assertIn('limit exceeded', report.errors[0])
        self.assertEqual(self.a.read_bytes(), b'new user work exceeds limit')


if __name__ == '__main__':
    unittest.main()
