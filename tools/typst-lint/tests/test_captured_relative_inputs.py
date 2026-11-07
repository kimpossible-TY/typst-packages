"""Internal captured graph receipts, using only real parser and temp consumers."""
import copy
from dataclasses import FrozenInstanceError, replace
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import captured_relative_inputs as inputs
import captured_relative_core as core
import transactions
from syntax_client import EXECUTABLE
from transactions import OfflineExclusive, ReadLimits, TransactionError
from typst_lint import CATALOG, InputError


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class CapturedRelativeInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'project'
        self.root.mkdir()
        self.store = self.base / 'journals'
        self.store.mkdir(mode=0o700)
        self.config = self.root / '.typst-lint.toml'
        self.config.write_bytes(b'rule-set="core"\ninclude=["main.typ"]\n')
        self.catalog = self.base / 'rules.json'
        self.catalog.write_bytes(CATALOG.read_bytes())
        self.patch = mock.patch.object(inputs, 'CATALOG', self.catalog)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.lease = OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers()
        self.write('main.typ', b'#import "constants.typ": count\nCount #count. $x_count$\n')
        self.write('constants.typ', b'#let count = 3\n')

    def write(self, path, data):
        output = self.root / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
        return output

    def context(self, **kwargs):
        options = dict(journal_directory=self.store, lease=self.lease)
        options.update(kwargs)
        return inputs.CapturedRelativeInputSession(self.root, 'main.typ', **options)

    def race(self, mutation):
        original = inputs._discover
        def discover(*args):
            result = original(*args)
            mutation()
            return result
        return mock.patch.object(inputs, '_discover', side_effect=discover)

    def test_transitive_diamond_uses_exact_transaction_evidence(self):
        self.write('main.typ', b'#import "a.typ": a\n#import "b.typ": b\n#a #b $x_a$\n')
        self.write('a.typ', b'#import "shared.typ": n\n#let a = n\n')
        self.write('b.typ', b'#import "shared.typ": n\n#let b = n\n')
        shared = self.write('shared.typ', b'#let n = 7\n')
        shared.chmod(0o444)
        before = {p.name: p.read_bytes() for p in self.root.glob('*.typ')}
        with self.context() as session:
            capture = session.capture
            self.assertEqual(set(capture.captures), {'main.typ', 'a.typ', 'b.typ', 'shared.typ'})
            self.assertEqual(len(capture.proof.edges), 4)
            self.assertEqual(capture.captures['shared.typ'].source, b'#let n = 7\n')
            self.assertEqual(capture.dependency_evidence['shared.typ'], str(shared))
            self.assertEqual(session._tx.targets, ('main.typ',))
            self.assertTrue(all(e.utf8 for e in session._tx.evidence_inputs))
            self.assertTrue(all(e.path != self.root / 'main.typ' for e in session._tx.evidence_inputs))
            session.assert_current(capture)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.glob('*.typ')})
        self.assertEqual(shared.stat().st_mode & 0o777, 0o444)
        self.assertEqual(list(next(self.store.glob('root-*')).iterdir()), [])

    def test_same_closure_new_value_is_independently_certified_baseline(self):
        with self.race(lambda: self.write('constants.typ', b'#let count = 9\n')):
            with self.context() as session:
                capture = session.capture
                self.assertEqual(capture.captures['constants.typ'].source, b'#let count = 9\n')
                self.assertEqual(capture.proof.root_bindings[0].value, 9)
                session.assert_current(capture)

    def test_changed_closure_is_refused_even_when_all_new_sources_are_safe(self):
        self.write('other.typ', b'#let count = 5\n')
        with self.race(lambda: self.write('main.typ', b'#import "other.typ": count\n#count\n')):
            with self.assertRaises(TransactionError):
                with self.context():
                    self.fail('Changed closure admitted')

    def test_changed_edge_set_with_same_files_is_refused(self):
        self.write('main.typ', b'#import "a.typ": a\n#import "b.typ": b\n#a #b\n')
        self.write('a.typ', b'#let a = 1\n')
        self.write('b.typ', b'#let b = 2\n')
        def mutate():
            self.write('a.typ', b'#import "b.typ": b\n#let a = b\n')
        with self.race(mutate), self.assertRaisesRegex(TransactionError, 'closure or edges'):
            with self.context():
                pass

    def test_postcapture_bytes_inode_mode_and_parent_drift_fail(self):
        for change in ('bytes', 'inode', 'mode', 'parent'):
            with self.subTest(change=change):
                self.write('main.typ', b'#import "dir/constants.typ": count\n#count\n')
                previous = self.root / 'dir/constants.typ'
                if previous.exists():
                    previous.chmod(0o644)
                path = self.write('dir/constants.typ', b'#let count = 3\n')
                path.chmod(0o644)
                with self.context() as session:
                    capture = session.capture
                    if change == 'bytes':
                        path.write_bytes(b'#let count = 4\n')
                    elif change == 'inode':
                        replacement = self.base / 'replacement'
                        replacement.write_bytes(path.read_bytes())
                        replacement.replace(path)
                    elif change == 'mode':
                        path.chmod(0o444)
                    else:
                        renamed = self.root / 'old-dir'
                        (self.root / 'dir').rename(renamed)
                        self.write('dir/constants.typ', b'#let count = 3\n')
                    with self.assertRaises((TransactionError, OSError)):
                        session.assert_current(capture)
                if (self.root / 'old-dir').exists():
                    (self.root / 'old-dir/constants.typ').unlink()
                    (self.root / 'old-dir').rmdir()

    def test_initial_absence_and_encoding_are_input_failures(self):
        for operation in (lambda: (self.root / 'constants.typ').unlink(),
                          lambda: self.write('constants.typ', b'\xff')):
            self.write('constants.typ', b'#let count = 3\n')
            operation()
            with self.assertRaises(inputs.CapturedRelativeInputError):
                with self.context():
                    pass

    def test_disappearance_after_initial_presence_is_unsafe_not_input(self):
        actual = inputs._initial_presence
        def disappear(root, path):
            actual(root, path)
            if path == 'constants.typ':
                (root / path).unlink()
        with mock.patch.object(inputs, '_initial_presence', side_effect=disappear):
            with self.assertRaises(inputs.CapturedRelativeCaptureError) as caught:
                with self.context():
                    pass
        self.assertNotIsInstance(caught.exception, InputError)

    def test_unsupported_initial_language_and_configuration_are_input(self):
        with self.race(lambda: self.write('constants.typ', b'#let count = (() => 3)()\n')):
            with self.assertRaises(InputError):
                with self.context():
                    pass
        self.write('constants.typ', b'#let count = 3\n')
        self.config.write_bytes(b'rule-set="core"\ninclude=["*.typ"]\n')
        with self.assertRaises(InputError):
            with self.context():
                pass

    def test_symlink_fifo_and_hardlink_are_unsafe(self):
        dependency = self.root / 'constants.typ'
        for kind in ('symlink', 'fifo', 'hardlink'):
            with self.subTest(kind=kind):
                if dependency.exists() or dependency.is_symlink():
                    dependency.unlink()
                other = self.base / ('other-' + kind)
                other.write_bytes(b'#let count = 3\n')
                if kind == 'symlink':
                    dependency.symlink_to(other)
                elif kind == 'fifo':
                    os.mkfifo(dependency)
                else:
                    os.link(other, dependency)
                with self.assertRaises(TransactionError):
                    with self.context():
                        pass

    def test_root_escape_and_nonminimal_paths_refuse_without_outside_read(self):
        for spelling in ('../outside.typ', './constants.typ', 'x/../constants.typ',
                         'a//b.typ', '/constants.typ', '@local/foo:0.1.0'):
            with self.subTest(spelling=spelling):
                self.write('main.typ', f'#import "{spelling}": count\n#count\n'.encode())
                with self.assertRaises((TransactionError, InputError)):
                    with self.context():
                        pass

    def test_necessary_leading_parent_path_is_safe(self):
        self.config.write_bytes(b'rule-set="core"\ninclude=["doc/main.typ"]\n')
        self.write('doc/main.typ', b'#import "../constants.typ": count\n#count\n')
        with inputs.CapturedRelativeInputSession(self.root, 'doc/main.typ',
                journal_directory=self.store, lease=self.lease) as session:
            self.assertEqual(set(session.capture.captures), {'doc/main.typ', 'constants.typ'})

    def test_original_only_live_receipt_and_proof(self):
        with self.context() as session:
            receipt = session.capture
            for foreign in (receipt.report, receipt.proof, copy.copy(receipt), object()):
                with self.assertRaises(TransactionError):
                    session.assert_current(foreign)
            with self.assertRaises(TypeError):
                inputs.CapturedRelativeInputs()
            with self.assertRaises(TypeError):
                receipt.captures['new.typ'] = receipt.captures['main.typ']
            with self.assertRaises(FrozenInstanceError):
                receipt.target = 'other.typ'
            object.__setattr__(receipt, 'proof', copy.copy(receipt.proof))
            with self.assertRaises(TransactionError):
                session.assert_current(receipt)
        with self.assertRaises(TransactionError):
            session.assert_current(receipt)

    def test_in_place_tree_node_proof_and_report_alterations_fail(self):
        for altered in ('source', 'node', 'proof', 'report'):
            with self.subTest(altered=altered), self.context() as session:
                receipt = session.capture
                if altered == 'source':
                    object.__setattr__(receipt.captures['constants.typ'], 'source', b'#let count = 4\n')
                elif altered == 'node':
                    object.__setattr__(receipt.captures['constants.typ'].nodes[0], 'kind', 'Code')
                elif altered == 'proof':
                    object.__setattr__(receipt.proof, 'target_path', 'other.typ')
                else:
                    object.__setattr__(receipt.report, 'target', 'other.typ')
                with self.assertRaises(TransactionError):
                    session.assert_current(receipt)

    def test_deleted_tree_source_is_typed_capture_refusal(self):
        for path in ('main.typ', 'constants.typ'):
            with self.subTest(path=path), self.context() as session:
                receipt = session.capture
                object.__delattr__(receipt.captures[path], 'source')
                with self.assertRaises(inputs.CapturedRelativeCaptureError):
                    session.assert_current(receipt)

    def test_deleted_original_receipt_fields_are_typed_capture_refusals(self):
        for name in inputs.CapturedRelativeInputs.__dataclass_fields__:
            with self.subTest(field=name), self.context() as session:
                receipt = session.capture
                object.__delattr__(receipt, name)
                with self.assertRaises(inputs.CapturedRelativeCaptureError):
                    session.assert_current(receipt)

    def test_deleted_nested_report_fields_are_typed_capture_refusals(self):
        for name in inputs.CapturedRelativeInputReport.__dataclass_fields__:
            with self.subTest(field=name), self.context() as session:
                receipt = session.capture
                object.__delattr__(receipt.report, name)
                with self.assertRaises(inputs.CapturedRelativeCaptureError):
                    session.assert_current(receipt)

    def test_malformed_report_and_oversized_tree_refuse_before_signature_copy(self):
        with self.context() as session:
            receipt = session.capture
            object.__setattr__(receipt.report, 'sources', object())
            with self.assertRaises(TransactionError):
                session.assert_current(receipt)
        with self.context() as session:
            receipt = session.capture
            tree = receipt.captures['constants.typ']
            object.__setattr__(tree, 'nodes', (tree.nodes[0],) * (core.MAX_TOTAL_NODES + 1))
            with mock.patch.object(inputs, '_tree_signature', side_effect=AssertionError('No unbounded copy')):
                with self.assertRaises(TransactionError):
                    session.assert_current(receipt)

    def test_intermediate_ancestor_drift_even_with_same_immediate_parent_fails(self):
        self.write('main.typ', b'#import "outer/inner/constants.typ": count\n#count\n')
        self.write('outer/inner/constants.typ', b'#let count = 3\n')
        with self.context() as session:
            receipt = session.capture
            original_parent = (self.root / 'outer/inner').stat().st_ino
            (self.root / 'outer').rename(self.root / 'old-outer')
            (self.root / 'outer').mkdir()
            (self.root / 'old-outer/inner').rename(self.root / 'outer/inner')
            self.assertEqual((self.root / 'outer/inner').stat().st_ino, original_parent)
            with self.assertRaises(TransactionError):
                session.assert_current(receipt)

    def test_fingerprints_are_retained_identity_not_later_stat(self):
        with self.context() as session:
            original = session._tx.captured_fingerprints
            dependency = self.root / 'constants.typ'
            replacement = self.base / 'replacement'
            replacement.write_bytes(dependency.read_bytes())
            replacement.replace(dependency)
            with mock.patch.object(transactions, '_read', side_effect=AssertionError('No refresh')):
                retained = session._tx.captured_fingerprints
            self.assertEqual(retained.evidence, original.evidence)
            self.assertNotEqual(retained.evidence[str(dependency)].inode, dependency.stat().st_ino)
            with self.assertRaises(TypeError):
                retained.evidence['forged'] = None
            self.assertTrue(retained.directories)
            with self.assertRaises(TransactionError):
                session.assert_current(session._retained.receipt)

    def test_source_like_evidence_policy_is_opt_in_and_retained(self):
        dependency = self.root / 'constants.typ'
        dependency.write_bytes(b'\xff')
        with transactions.ProtectedTransaction(self.root, ('main.typ',),
                journal_directory=self.store, lease=self.lease,
                evidence=(transactions.EvidenceInput(dependency),)) as tx:
            self.assertEqual(tx.snapshot.evidence[str(dependency)], b'\xff')
            self.assertFalse(tx.captured_fingerprints.directories)
        with self.assertRaises(transactions.SourceEncodingError):
            with transactions.ProtectedTransaction(self.root, ('main.typ',),
                    journal_directory=self.store, lease=self.lease,
                    evidence=(transactions.EvidenceInput(dependency, utf8=True),)):
                pass

    def test_foreign_session_does_not_accept_receipt(self):
        with self.context() as first:
            receipt = first.capture
        with self.context() as second:
            with self.assertRaises(TransactionError):
                second.assert_current(receipt)

    def test_session_reuse_and_implicit_lease_refused(self):
        session = self.context()
        with session:
            pass
        with self.assertRaises(TransactionError):
            with session:
                pass
        with mock.patch.object(inputs, '_discover', side_effect=AssertionError('No discovery')):
            with self.assertRaises(TransactionError):
                self.context(lease=None)

    def test_argument_validation_precedes_any_filesystem_work(self):
        with mock.patch.object(inputs, '_discover', side_effect=AssertionError('No discovery')), \
                mock.patch.object(transactions, '_directory', side_effect=AssertionError('No filesystem')):
            for target, profile in (('../main.typ', core.SOURCE_PROFILE),
                                    ('*.typ', core.SOURCE_PROFILE),
                                    ('main.typ', 'structured-core-only-v1')):
                with self.subTest(target=target, profile=profile), self.assertRaises(InputError):
                    inputs.CapturedRelativeInputSession(self.root, target,
                        journal_directory=self.store, lease=self.lease, source_profile=profile)
            with self.assertRaises(TransactionError):
                self.context(journal_directory=self.root / 'journals')
            with self.assertRaises(TransactionError):
                self.context(limits=replace(ReadLimits(), max_capture_bytes=32 * 1024 * 1024))

    def test_no_unused_modules_are_admitted(self):
        original = inputs._discover
        self.write('unused.typ', b'#let unused = 1\n')
        def extra(*args):
            discovered = original(*args)
            return inputs._Discovery((*discovered.paths, 'unused.typ'), discovered.edges)
        with mock.patch.object(inputs, '_discover', side_effect=extra):
            with self.assertRaises(TransactionError):
                with self.context():
                    pass

    def test_dependency_and_catalog_canonical_overlap_refused(self):
        with mock.patch.object(inputs, 'CATALOG', self.root / 'constants.typ'):
            with self.assertRaises(TransactionError):
                with self.context():
                    pass

    def test_file_count_exactly_32_then_33(self):
        def fixture(count):
            imports = []
            for number in range(count - 1):
                self.write(f'd{number}.typ', f'#let n{number} = {number}\n'.encode())
                imports.append(f'#import "d{number}.typ": n{number}\n')
            self.write('main.typ', ''.join(imports).encode() + b'Count.\n')
        fixture(32)
        with self.context() as session:
            self.assertEqual(len(session.capture.captures), 32)
        fixture(33)
        with self.assertRaisesRegex(TransactionError, 'file budget'):
            with self.context():
                pass

    def test_depth_exactly_16_edges_then_17(self):
        def fixture(depth):
            self.write('main.typ', b'#import "d1.typ": n\n#n\n')
            for i in range(1, depth + 1):
                data = (f'#import "d{i+1}.typ": n\n'.encode() if i < depth else b'#let n = 1\n')
                self.write(f'd{i}.typ', data)
        fixture(16)
        with self.context() as session:
            self.assertEqual(len(session.capture.proof.edges), 16)
        fixture(17)
        with self.assertRaisesRegex(TransactionError, 'depth budget'):
            with self.context():
                pass

    def test_file_bytes_exactly_one_mib_then_excess(self):
        prefix = b'#let count = 3\n'
        self.write('constants.typ', prefix + b' ' * (core.MAX_FILE_BYTES - len(prefix)))
        with self.context() as session:
            self.assertEqual(len(session.capture.captures['constants.typ'].source), core.MAX_FILE_BYTES)
        self.write('constants.typ', prefix + b' ' * (core.MAX_FILE_BYTES + 1 - len(prefix)))
        with self.assertRaises(TransactionError):
            with self.context():
                pass

    def test_aggregate_source_bytes_exactly_four_mib_then_excess(self):
        prefix = b''.join(f'#import "d{i}.typ": n{i}\n'.encode() for i in range(3))
        self.write('main.typ', prefix + b'A' * (core.MAX_FILE_BYTES - len(prefix)))
        for i in range(3):
            prefix = f'#let n{i} = 1\n'.encode()
            self.write(f'd{i}.typ', prefix + b' ' * (core.MAX_FILE_BYTES - len(prefix)))
        with self.context() as session:
            self.assertEqual(sum(len(t.source) for t in session.capture.captures.values()), core.MAX_SOURCE_BYTES)
        self.write('small.typ', b'#let n3 = 1\n')
        data = (self.root / 'main.typ').read_bytes()
        prefix = b'#import "small.typ": n3\n'
        self.write('main.typ', prefix + data[:-len(prefix)])
        with self.assertRaisesRegex(TransactionError, 'aggregate source byte budget'):
            with self.context():
                pass


if __name__ == '__main__':
    unittest.main()
