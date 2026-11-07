"""Real-parser capture tests using a private copy of the reviewed package leaf."""
import copy
import ctypes
from dataclasses import FrozenInstanceError, replace
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import captured_text_utils_paragraph_inputs as inputs
import package_loader
import transactions
from facade_provenance import certify_initializer
from static_resolver import PackageSource, TEXT_UTILS, TEXT_UTILS_SOURCE_SHA256
from syntax_client import EXECUTABLE, Tree
from transactions import OfflineExclusive, ReadLimits, TransactionError
from typst_lint import CATALOG, InputError, digest

SOURCE = (b'#import "@local/text-utils:0.1.2": paragraph_tab, apply-paragraph-tabs\n'
          b'#show: apply-paragraph-tabs\n\n#paragraph_tab\nthis is ordinary prose.\n')
CONFIG = b'rule-set="pde"\ninclude=["main.typ"]\n[capitalization]\nlanguage="en"\n'


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class CapturedTextUtilsParagraphInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'project'
        self.root.mkdir()
        self.store = self.base / 'journals'
        self.store.mkdir(mode=0o700)
        self.source = self.root / 'main.typ'
        self.source.write_bytes(SOURCE)
        self.config = self.root / '.typst-lint.toml'
        self.config.write_bytes(CONFIG)
        self.catalog = self.base / 'catalog.json'
        self.catalog.write_bytes(CATALOG.read_bytes())
        self.packages = self.base / 'packages'
        self.leaf = self.packages / 'local/text-utils/0.1.2'
        self.leaf.mkdir(parents=True)
        self.manifest = self.leaf / 'typst.toml'
        self.package = self.leaf / 'lib.typ'
        original = TOOL.parents[1] / 'local/text-utils/0.1.2'
        shutil.copyfile(original / 'typst.toml', self.manifest)
        shutil.copyfile(original / 'lib.typ', self.package)
        self.patch = mock.patch.object(inputs, 'CATALOG', self.catalog)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.lease = OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers()

    def context(self, **kwargs):
        options = dict(package_path=self.packages, journal_directory=self.store, lease=self.lease)
        options.update(kwargs)
        return inputs.CapturedTextUtilsParagraphInputSession(self.root, 'main.typ', **options)

    def evidence_files(self):
        return self.config, self.catalog, self.manifest, self.package

    def test_exact_retained_bytes_trees_package_initializer_and_catalog(self):
        before = {path: path.read_bytes() for path in (self.source, *self.evidence_files())}
        with mock.patch.object(inputs, 'certify_initializer', wraps=certify_initializer) as certify:
            with self.context() as session:
                capture = session.capture
                tx = session._borrow_transaction(capture)
                snapshot = tx.snapshot
                self.assertEqual(tx.targets, ('main.typ',))
                self.assertEqual(len(tx.evidence_inputs), 4)
                self.assertTrue(all(item.utf8 and not item.allow_missing for item in tx.evidence_inputs))
                self.assertTrue(tx._retain_ancestry)
                self.assertEqual(set(snapshot.evidence), {str(p) for p in self.evidence_files()})
                self.assertIs(type(capture.tree), Tree)
                self.assertIs(capture.tree.source, snapshot.sources['main.typ'])
                self.assertIs(type(capture.package), PackageSource)
                self.assertEqual(capture.package.import_path, TEXT_UTILS)
                self.assertEqual(capture.package.source_identity, str(self.package))
                self.assertIs(capture.package.source, snapshot.evidence[str(self.package)])
                self.assertIs(capture.package_tree.source, capture.package.source)
                self.assertIs(capture.manifest, snapshot.evidence[str(self.manifest)])
                self.assertEqual(digest(capture.manifest), inputs.MANIFEST_SHA256)
                self.assertEqual(digest(capture.package.source), TEXT_UTILS_SOURCE_SHA256)
                self.assertEqual(capture.package.artifacts, ())
                self.assertTrue(capture.initializer.accepted)
                self.assertEqual(len(capture.initializer.exports), 6)
                certify.assert_called_once_with(capture.package)
                self.assertIs(certify.call_args.args[0], capture.package)
                self.assertEqual(len(capture.catalog), 33)
                self.assertEqual(capture.report.package_role, 'read-only-semantic-evidence')
                self.assertEqual(len(capture.report.package_files), 2)
                self.assertEqual(capture.config.language, 'en')
                self.assertFalse(hasattr(session, 'transaction'))
                session.assert_current(capture)
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertEqual(list(next(self.store.glob('root-*')).iterdir()), [])

    def test_no_package_loader_or_ambient_read_bytes(self):
        with mock.patch.object(package_loader, 'load_packages', side_effect=AssertionError('No loader')), \
                mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('No ambient reads')):
            with self.context() as session:
                session.assert_current(session.capture)

    def test_read_only_evidence_is_not_a_write_or_recovery_target(self):
        for path in self.evidence_files():
            path.chmod(0o444)
        before = {p: (p.read_bytes(), p.stat().st_mode, p.stat().st_ino) for p in self.evidence_files()}
        with self.context() as session:
            tx = session._borrow_transaction(session.capture)
            self.assertEqual(tx.targets, ('main.typ',))
            self.assertTrue(all(str(path) in tx.snapshot.evidence for path in self.evidence_files()))
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mode, p.stat().st_ino) for p in before})

    def test_initial_missing_inputs_are_input_errors(self):
        for path in (self.source, *self.evidence_files()):
            with self.subTest(path=path):
                data = path.read_bytes()
                path.unlink()
                with self.assertRaises(InputError):
                    with self.context():
                        pass
                path.write_bytes(data)

    def test_initial_invalid_utf8_inputs_are_input_errors(self):
        for path in (self.source, *self.evidence_files()):
            with self.subTest(path=path):
                data = path.read_bytes()
                path.write_bytes(b'\xff')
                with self.assertRaises(InputError):
                    with self.context():
                        pass
                path.write_bytes(data)

    def test_disappearance_after_presence_is_a_transaction_error(self):
        original = inputs._initial_presence
        def disappear(path):
            original(path)
            if path == self.package:
                path.unlink()
        with mock.patch.object(inputs, '_initial_presence', side_effect=disappear):
            with self.assertRaises(TransactionError) as caught:
                with self.context():
                    pass
        self.assertNotIsInstance(caught.exception, InputError)

    def test_exact_package_pins_and_metadata_are_independently_required(self):
        manifest = self.manifest.read_bytes()
        package = self.package.read_bytes()
        for path, bad in ((self.manifest, manifest + b'\n'),
                          (self.manifest, manifest.replace(b'0.1.2', b'0.1.3')),
                          (self.manifest, manifest.replace(b'lib.typ', b'foo.typ')),
                          (self.manifest, b'[package]\nname="text-utils"\n'),
                          (self.package, package + b'\n'),
                          (self.package, package.replace(b'upper', b'lower', 1))):
            with self.subTest(path=path, bad=bad[:40]):
                path.write_bytes(bad)
                with self.assertRaises(InputError):
                    with self.context():
                        pass
                self.manifest.write_bytes(manifest)
                self.package.write_bytes(package)
        with mock.patch.object(inputs, 'MANIFEST_SHA256', digest(manifest.replace(b'0.1.2', b'0.1.3'))):
            self.manifest.write_bytes(manifest.replace(b'0.1.2', b'0.1.3'))
            with self.assertRaisesRegex(InputError, 'metadata'):
                with self.context():
                    pass

    def test_initial_malformed_source_is_input_error(self):
        self.source.write_bytes(b'#let x = (')
        with self.assertRaises(InputError):
            with self.context():
                pass

    def test_strict_configuration_and_optional_matching_entrypoint(self):
        self.config.write_bytes(CONFIG.replace(b'[capitalization]', b'entrypoint="main.typ"\n[capitalization]'))
        with self.context() as session:
            self.assertEqual(session.capture.config.entrypoint, 'main.typ')
        for bad in (CONFIG.replace(b'"pde"', b'"core"'),
                    CONFIG.replace(b'"main.typ"', b'"*.typ"'),
                    CONFIG.replace(b'"en"', b'"ko"'),
                    CONFIG.replace(b'[capitalization]\nlanguage="en"\n', b''),
                    CONFIG + b'other=true\n',
                    CONFIG.replace(b'[capitalization]', b'ignore=["x"]\n[capitalization]'),
                    CONFIG.replace(b'[capitalization]', b'entrypoint="other.typ"\n[capitalization]')):
            with self.subTest(bad=bad):
                self.config.write_bytes(bad)
                with self.assertRaises(InputError):
                    with self.context():
                        pass

    def test_initial_catalog_must_be_exact_reviewed_full_catalog(self):
        self.catalog.write_bytes(self.catalog.read_bytes() + b'\n')
        with self.assertRaises(InputError):
            with self.context():
                pass

    def test_evidence_byte_inode_mode_drift_refuses_original_baseline(self):
        for path in self.evidence_files():
            original = path.read_bytes()
            for change in ('bytes', 'inode', 'mode'):
                with self.subTest(path=path, change=change), self.context() as session:
                    capture = session.capture
                    if change == 'bytes':
                        path.write_bytes(original + b'\n')
                    elif change == 'inode':
                        replacement = self.base / 'replacement'
                        replacement.write_bytes(original)
                        replacement.replace(path)
                    else:
                        path.chmod(0o444)
                    with self.assertRaises((TransactionError, OSError)):
                        session.assert_current(capture)
                    # Historical binding never blesses fresh filesystem bytes.
                    session._assert_binding(capture)
                path.chmod(0o644)
                path.write_bytes(original)

    def test_source_byte_inode_mode_drift_refuses_original_baseline(self):
        for change in ('bytes', 'inode', 'mode'):
            with self.subTest(change=change), self.context() as session:
                capture = session.capture
                if change == 'bytes':
                    self.source.write_bytes(SOURCE.replace(b'this', b'This'))
                elif change == 'inode':
                    replacement = self.base / 'replacement'
                    replacement.write_bytes(SOURCE)
                    replacement.replace(self.source)
                else:
                    self.source.chmod(0o444)
                with self.assertRaises((TransactionError, OSError)):
                    session.assert_current(capture)
            self.source.chmod(0o644)
            self.source.write_bytes(SOURCE)

    def test_evidence_intermediate_ancestor_replacement_refuses(self):
        with self.context() as session:
            capture = session.capture
            old = self.base / 'old-local'
            (self.packages / 'local').rename(old)
            shutil.copytree(old, self.packages / 'local')
            with self.assertRaises(TransactionError):
                session.assert_current(capture)

    def test_same_file_new_ancestor_identity_refuses_even_with_same_final_inode(self):
        with self.context() as session:
            capture = session.capture
            old = self.packages / 'old-local'
            (self.packages / 'local').rename(old)
            (self.packages / 'local').mkdir()
            (old / 'text-utils').rename(self.packages / 'local/text-utils')
            # Immediate lib.typ parent and file inode are unchanged; only an
            # intermediate ancestor changed, which retain_ancestry must catch.
            with self.assertRaises(TransactionError):
                session.assert_current(capture)

    def test_xattr_drift_on_all_evidence_is_refused(self):
        attribute = 'user.typst-lint-capture-test'
        if sys.platform != 'darwin' and not hasattr(os, 'setxattr'):
            self.skipTest('No OS xattr helper')
        for path in self.evidence_files():
            with self.subTest(path=path), self.context() as session:
                capture = session.capture
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    if sys.platform == 'darwin':
                        api = transactions._darwin_metadata_api()
                        ctypes.set_errno(0)
                        if api.fsetxattr(fd, os.fsencode(attribute), b'changed', 7, 0, 0):
                            raise OSError(ctypes.get_errno(), 'Fixture xattr unavailable')
                    else:
                        os.setxattr(path, attribute, b'changed')
                except OSError as error:
                    self.skipTest(f'Filesystem xattrs unavailable: {error}')
                finally:
                    os.close(fd)
                with self.assertRaises(TransactionError):
                    session.assert_current(capture)
            # Each evidence file is visited once; its xattr can stay until the
            # private fixture is deleted. It is retained in subsequent captures.

    def test_symlink_fifo_and_hardlink_evidence_refused(self):
        for path in self.evidence_files():
            original = path.read_bytes()
            for kind in ('symlink', 'fifo', 'hardlink'):
                with self.subTest(path=path, kind=kind):
                    path.unlink()
                    other = self.base / 'other-file'
                    other.write_bytes(original)
                    if kind == 'symlink':
                        path.symlink_to(other)
                    elif kind == 'fifo':
                        os.mkfifo(path)
                    else:
                        os.link(other, path)
                    with self.assertRaises(TransactionError):
                        with self.context():
                            pass
                    path.unlink()
                    other.unlink()
                    path.write_bytes(original)

    def test_package_ancestor_symlink_is_refused(self):
        actual = self.base / 'actual-local'
        (self.packages / 'local').rename(actual)
        (self.packages / 'local').symlink_to(actual, target_is_directory=True)
        with self.assertRaises(TransactionError):
            with self.context():
                pass

    def test_source_symlink_fifo_hardlink_and_read_only_refused(self):
        for kind in ('symlink', 'fifo', 'hardlink', 'read-only'):
            with self.subTest(kind=kind):
                self.source.unlink()
                other = self.base / 'other-file'
                other.write_bytes(SOURCE)
                if kind == 'symlink':
                    self.source.symlink_to(other)
                elif kind == 'fifo':
                    os.mkfifo(self.source)
                elif kind == 'hardlink':
                    os.link(other, self.source)
                else:
                    self.source.write_bytes(SOURCE)
                    self.source.chmod(0o444)
                with self.assertRaises(TransactionError):
                    with self.context():
                        pass
                self.source.unlink()
                other.unlink()
                self.source.write_bytes(SOURCE)

    def test_path_collisions_and_storage_inputs_refused_before_io(self):
        with mock.patch.object(inputs, '_initial_presence', side_effect=AssertionError('No IO')):
            for collision in (self.source, self.config, self.manifest, self.package):
                with self.subTest(collision=collision), mock.patch.object(inputs, 'CATALOG', collision):
                    with self.assertRaises(TransactionError):
                        self.context()
            with self.assertRaises(TransactionError):
                self.context(journal_directory=self.root / 'journals')
            with self.assertRaises(TransactionError):
                self.context(package_path=self.store)

    def test_file_inode_aliases_including_casefold_aliases_refused(self):
        alias = self.package.with_name('LIB.TYP')
        if not alias.exists():
            self.skipTest('Case-sensitive filesystem has no alternate-name inode alias')
        with mock.patch.object(inputs, 'CATALOG', alias):
            with self.assertRaises(TransactionError):
                with self.context():
                    pass

    def test_ancestor_casefold_aliases_are_refused_even_for_distinct_files(self):
        alternate = self.packages / 'LOCAL/text-utils/0.1.2'
        if not alternate.exists():
            self.skipTest('Case-sensitive filesystem has no alternate directory alias')
        copied_catalog = self.leaf / 'rules.json'
        copied_catalog.write_bytes(self.catalog.read_bytes())
        with mock.patch.object(inputs, 'CATALOG', alternate / 'rules.json'):
            with self.assertRaisesRegex(TransactionError, 'directory alias'):
                with self.context():
                    pass

    def test_storage_casefold_aliases_are_refused(self):
        alternate = self.store.with_name('JOURNALS')
        if not alternate.exists():
            self.skipTest('Case-sensitive filesystem has no alternate storage alias')
        leaf = self.store / 'local/text-utils/0.1.2'
        leaf.mkdir(parents=True)
        shutil.copyfile(self.manifest, leaf / 'typst.toml')
        shutil.copyfile(self.package, leaf / 'lib.typ')
        with self.assertRaisesRegex(TransactionError, 'storage'):
            with self.context(package_path=alternate):
                pass

    def test_original_only_receipts_and_read_only_maps(self):
        with self.context() as session:
            receipt = session.capture
            for fake in (receipt.report, receipt.package, copy.copy(receipt), object()):
                with self.assertRaises(TransactionError):
                    session.assert_current(fake)
            with self.assertRaises(TypeError):
                inputs.CapturedTextUtilsParagraphInputs()
            with self.assertRaises(FrozenInstanceError):
                receipt.target = 'other.typ'
            with self.assertRaises(TypeError):
                receipt.dependency_evidence['package'] = 'elsewhere'
            with self.assertRaises(TypeError):
                receipt.catalog[0]['id'] = 'foreign-rule'
        with self.assertRaises(TransactionError):
            session.assert_current(receipt)
        with self.context() as second:
            with self.assertRaises(TransactionError):
                second.assert_current(receipt)

    def test_copied_sessions_cannot_enter_borrow_close_or_outlive_original(self):
        original = self.context()
        before_entry_copy = copy.copy(original)
        with self.assertRaises(TransactionError):
            before_entry_copy.__enter__()
        with original:
            receipt = original.capture
            active_copy = copy.copy(original)
            for operation in (lambda: active_copy.capture,
                              lambda: active_copy.assert_current(receipt),
                              lambda: active_copy._assert_binding(receipt),
                              lambda: active_copy._borrow_transaction(receipt),
                              lambda: active_copy.__exit__(None, None, None)):
                with self.assertRaises(TransactionError):
                    operation()
            original.assert_current(receipt)
        with self.assertRaises(TransactionError):
            active_copy._assert_binding(receipt)
        with self.assertRaises(TransactionError):
            active_copy.__exit__(None, None, None)

    def test_subclass_session_cannot_mint_input_authority(self):
        class Subclass(inputs.CapturedTextUtilsParagraphInputSession):
            pass
        with self.assertRaises(TransactionError):
            Subclass(self.root, 'main.typ', package_path=self.packages,
                     journal_directory=self.store, lease=self.lease)

    def test_nested_receipt_tree_package_config_certificate_and_report_mutations_refused(self):
        mutations = (
            lambda c: object.__setattr__(c.tree, 'source', SOURCE + b' '),
            lambda c: object.__delattr__(c.tree, 'source'),
            lambda c: object.__setattr__(c.tree.nodes[0], 'kind', 'Code'),
            lambda c: object.__setattr__(c.tree, 'nodes', c.tree.nodes + c.tree.nodes),
            lambda c: object.__setattr__(c, 'tree', copy.copy(c.tree)),
            lambda c: object.__setattr__(c.package_tree.nodes[0], 'errors', True),
            lambda c: object.__setattr__(c.package_tree, 'source', b''),
            lambda c: object.__setattr__(c.package, 'source_identity', '/foreign/lib.typ'),
            lambda c: object.__setattr__(c.package, 'source', b''),
            lambda c: object.__setattr__(c, 'package', copy.copy(c.package)),
            lambda c: object.__setattr__(c.config, 'language', 'ko'),
            lambda c: object.__setattr__(c.initializer, 'accepted', False),
            lambda c: object.__setattr__(c, 'initializer', copy.copy(c.initializer)),
            lambda c: object.__setattr__(c.report, 'target', 'other.typ'),
            lambda c: object.__setattr__(c.report, 'package_files', c.report.package_files * 10000),
            lambda c: object.__setattr__(c, 'catalog', tuple(list(c.catalog))),
        )
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index), self.context() as session:
                receipt = session.capture
                mutation(receipt)
                with self.assertRaises(TransactionError):
                    session.assert_current(receipt)

    def test_selection_and_limits_mutation_before_entry_refused_without_io(self):
        limits = replace(ReadLimits())
        session = self.context(limits=limits)
        object.__setattr__(limits, 'max_source_bytes', 2 * 1024 * 1024)
        with mock.patch.object(inputs, '_initial_presence', side_effect=AssertionError('No IO')):
            with self.assertRaises(TransactionError):
                session.__enter__()
        session = self.context()
        session._tx.evidence_inputs = ()
        with mock.patch.object(inputs, '_initial_presence', side_effect=AssertionError('No IO')):
            with self.assertRaises(TransactionError):
                session.__enter__()

    def test_preentry_selector_fields_and_transaction_substitution_refused_before_io(self):
        for name, value in (('_profile', 'forged-profile'), ('_target', 'other.typ'),
                            ('_root', self.base / 'other-project')):
            with self.subTest(name=name):
                session = self.context()
                setattr(session, name, value)
                with mock.patch.object(inputs, '_initial_presence', side_effect=AssertionError('No IO')):
                    with self.assertRaises(TransactionError):
                        session.__enter__()
        session = self.context()
        equivalent = self.context()
        session._tx = equivalent._tx
        with mock.patch.object(inputs, '_initial_presence', side_effect=AssertionError('No IO')):
            with self.assertRaises(TransactionError):
                session.__enter__()

    def test_argument_validation_no_transaction_injection_or_reuse(self):
        with mock.patch.object(inputs, '_initial_presence', side_effect=AssertionError('No IO')):
            for target in ('../main.typ', './main.typ', '*.typ', 'main.md', 'dir//main.typ', '/main.typ'):
                with self.subTest(target=target), self.assertRaises(InputError):
                    inputs.CapturedTextUtilsParagraphInputSession(self.root, target,
                        package_path=self.packages, journal_directory=self.store, lease=self.lease)
            with self.assertRaises(InputError):
                self.context(source_profile='captured-relative-core-v1')
            with self.assertRaises(TransactionError):
                self.context(lease=None)
            with self.assertRaises(TransactionError):
                self.context(package_path=Path('relative'))
            with self.assertRaises(TransactionError):
                self.context(limits=replace(ReadLimits(), max_capture_bytes=32 * 1024 * 1024))
            with self.assertRaises(TypeError):
                self.context(transaction=object())
        session = self.context()
        with session:
            pass
        with self.assertRaises(TransactionError):
            session.__enter__()

    def test_fixed_accepted_manifest_and_config_limits(self):
        self.manifest.write_bytes(b' ' * (inputs.MAX_MANIFEST_BYTES + 1))
        with self.assertRaises(TransactionError):
            with self.context():
                pass
        self.manifest.write_bytes((TOOL.parents[1] / 'local/text-utils/0.1.2/typst.toml').read_bytes())
        self.config.write_bytes(CONFIG + b' ' * (64 * 1024 + 1 - len(CONFIG)))
        with self.assertRaises((InputError, TransactionError)):
            with self.context():
                pass
        self.config.write_bytes(CONFIG)
        with self.assertRaises(TransactionError):
            with self.context(limits=replace(ReadLimits(), max_capture_bytes=10)):
                pass


if __name__ == '__main__':
    unittest.main()
