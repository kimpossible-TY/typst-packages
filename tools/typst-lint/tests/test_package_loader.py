"""Explicit package fixtures only; never read/install a user's package cache."""
from dataclasses import FrozenInstanceError, asdict
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import package_loader as loader
from static_resolver import PackageSource, TEXT_UTILS, TEXT_UTILS_SOURCE_SHA256

FIXTURE = (TOOL / 'tests/fixtures/reviewed-packages') / 'local/text-utils/0.1.2'


class PackageLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / 'packages'
        self.root.mkdir()
        self.directory = self.root / 'local/text-utils/0.1.2'
        self.directory.mkdir(parents=True)
        # Copy from the reviewed repository fixture only into this test's
        # explicitly passed package path. The loader never knows FIXTURE.
        for name in ('typst.toml', 'lib.typ'):
            shutil.copyfile(FIXTURE / name, self.directory / name)
        self.source_path = self.directory / 'lib.typ'
        self.manifest_path = self.directory / 'typst.toml'
        self.source = self.source_path.read_bytes()
        self.manifest = self.manifest_path.read_bytes()

    def load(self, imports=(TEXT_UTILS,)):
        return loader.load_packages(self.root, imports)

    def write_manifest(self, **overrides):
        fields = dict(name='text-utils', version='0.1.2', entrypoint='lib.typ')
        fields.update(overrides)
        text = '[package]\n' + '\n'.join(f'{key} = "{value}"' for key, value in fields.items()) + '\n'
        self.manifest_path.write_text(text)

    def test_exact_original_bytes_and_artifact_identities_are_hashed(self):
        result = self.load()
        self.assertEqual(result.package_path, str(self.root))
        self.assertEqual(result.sources, (PackageSource(TEXT_UTILS, str(self.source_path), self.source),))
        self.assertEqual(result.unavailable, ())
        self.assertEqual(len(result.evidence), 1)
        evidence = result.evidence[0]
        self.assertEqual(evidence.import_path, TEXT_UTILS)
        self.assertEqual(evidence.source_identity, str(self.source_path))
        self.assertEqual(evidence.source_sha256, TEXT_UTILS_SOURCE_SHA256)
        self.assertEqual(evidence.manifest_identity, str(self.manifest_path))
        self.assertEqual(evidence.manifest_sha256, hashlib.sha256(self.manifest).hexdigest())
        self.assertEqual((evidence.namespace, evidence.name, evidence.version, evidence.entrypoint),
                         ('local', 'text-utils', '0.1.2', 'lib.typ'))
        self.assertEqual(self.source_path.read_bytes(), self.source)
        self.assertEqual(self.manifest_path.read_bytes(), self.manifest)
        self.assertEqual(asdict(evidence)['source_sha256'], TEXT_UTILS_SOURCE_SHA256)

    def test_results_and_sources_are_immutable(self):
        result = self.load()
        self.assertIsInstance(result.sources, tuple)
        self.assertIsInstance(result.evidence, tuple)
        self.assertIsInstance(result.unavailable, tuple)
        for item, field in ((result, 'package_path'), (result.sources[0], 'source'),
                            (result.evidence[0], 'source_sha256')):
            with self.subTest(field=field), self.assertRaises(FrozenInstanceError):
                setattr(item, field, None)
        self.source_path.write_bytes(b'changed after snapshot')
        self.assertEqual(result.sources[0].source, self.source)

    def test_same_named_unreviewed_copy_never_supplies_a_contract(self):
        self.source_path.write_bytes(b'#let paragraph_tab = "not the reviewed implementation"\n')
        result = self.load()
        self.assertEqual(result.sources, ())
        self.assertEqual(result.unavailable[0].code, 'unreviewed-package-source')
        self.assertEqual(result.evidence[0].source_sha256,
                         hashlib.sha256(self.source_path.read_bytes()).hexdigest())

    def test_original_source_is_not_normalized_before_verification(self):
        for changed in (b'\xef\xbb\xbf' + self.source, self.source.replace(b'\n', b'\r\n'), self.source + b'\n'):
            with self.subTest(prefix=changed[:12]):
                self.source_path.write_bytes(changed)
                result = self.load()
                self.assertEqual(result.sources, ())
                self.assertEqual(result.unavailable[0].code, 'unreviewed-package-source')
                self.assertEqual(result.evidence[0].source_sha256, hashlib.sha256(changed).hexdigest())

    def test_manifest_noncontract_metadata_is_hashed_without_normalization(self):
        manifest = self.manifest.replace(b'\n', b'\r\n') + b'\r\n# reviewed source, original manifest bytes\r\n'
        self.manifest_path.write_bytes(manifest)
        result = self.load()
        self.assertEqual(len(result.sources), 1)
        self.assertEqual(result.evidence[0].manifest_sha256, hashlib.sha256(manifest).hexdigest())

    def test_unreviewed_manifest_identity_is_unavailable_without_reading_entrypoint(self):
        for field, value in (('name', 'other'), ('version', '0.1.3'),
                             ('namespace', 'preview'), ('entrypoint', 'other.typ'),
                             ('entrypoint', 'src/lib.typ')):
            with self.subTest(field=field, value=value):
                self.write_manifest(**{field: value})
                # Even the usual entrypoint is unreadable data. No identity
                # mismatch may cause it or an alternative file to be opened.
                self.source_path.write_bytes(b'\xff')
                result = self.load()
                self.assertEqual(result.sources, ())
                self.assertEqual(result.unavailable[0].code, 'unreviewed-package-metadata')
                self.assertIsNone(result.evidence[0].source_identity)
                self.assertIsNone(result.evidence[0].source_sha256)

    def test_namespace_is_implied_by_explicit_path_and_may_be_consistent(self):
        self.write_manifest(namespace='local')
        self.assertEqual(len(self.load().sources), 1)

    def test_malformed_manifest_is_input_failure(self):
        for source in (b'\xff', b'[package', b'', b'package = "text-utils"', b'[package]\nname=1',
                       b'[package]\nname="text-utils"\nversion="0.1.2"',
                       b'[package]\nname="text-utils"\nversion=[]\nentrypoint="lib.typ"',
                       b'[package]\nname="text-utils"\nversion="0.1.2"\nentrypoint=""',
                       self.manifest + b'namespace = 42\n'):
            with self.subTest(source=source), self.assertRaises(loader.PackageInputError):
                self.manifest_path.write_bytes(source)
                self.load()

    def test_invalid_source_encoding_is_input_failure(self):
        self.source_path.write_bytes(b'\xff')
        with self.assertRaisesRegex(loader.PackageInputError, 'UTF-8'):
            self.load()

    def test_unsafe_entrypoints_are_rejected_without_reading_targets(self):
        for entrypoint in ('../lib.typ', '/outside.typ', 'sub/../../outside.typ',
                           './lib.typ', 'sub//lib.typ', 'sub/', 'C:\\outside.typ'):
            with self.subTest(entrypoint=entrypoint), self.assertRaises(loader.PackageInputError):
                # Escape the backslash for TOML, preserving the unsafe value.
                self.write_manifest(entrypoint=entrypoint.replace('\\', '\\\\'))
                self.load()

    def test_missing_root_and_required_files_are_input_failure_no_checkout_fallback(self):
        with self.assertRaises(loader.PackageInputError):
            loader.load_packages(self.base / 'missing', ())
        for name in ('typst.toml', 'lib.typ'):
            path = self.directory / name
            original = path.read_bytes()
            path.unlink()
            with self.subTest(name=name), self.assertRaises(loader.PackageInputError):
                self.load()
            path.write_bytes(original)
        shutil.rmtree(self.root / 'local')
        with self.assertRaises(loader.PackageInputError):
            self.load()

    def test_no_imports_only_validate_root_without_package_discovery(self):
        shutil.rmtree(self.root / 'local')
        with patch('os.walk', side_effect=AssertionError('no traversal')), \
             patch('os.scandir', side_effect=AssertionError('no traversal')), \
             patch.object(Path, 'home', side_effect=AssertionError('no home discovery')):
            result = self.load(())
        self.assertEqual((result.sources, result.evidence, result.unavailable), ((), (), ()))

    def test_unknown_imports_are_not_read_and_have_explicit_coverage_unavailability(self):
        self.source_path.write_bytes(b'\xff')
        self.manifest_path.write_bytes(b'\xff')
        requested = ('@preview/text-utils:0.1.2', '@local/text-utils:0.1.3',
                     '@local/unknown:0.1.0', 'other.typ', '../other.typ')
        with patch.object(loader._Snapshot, 'read', side_effect=AssertionError('unknown import was read')):
            result = self.load(requested)
        self.assertEqual((result.sources, result.evidence), ((), ()))
        self.assertEqual(tuple(item.import_path for item in result.unavailable), tuple(sorted(requested)))
        self.assertTrue(all(item.code == 'unreviewed-package' for item in result.unavailable))

    def test_requested_known_import_only_no_recursive_or_user_cache_reads(self):
        (self.root / 'unrelated').symlink_to(self.base, target_is_directory=True)
        (self.directory / 'irrelevant.typ').write_bytes(b'\xff')
        with patch('os.walk', side_effect=AssertionError('no traversal')), \
             patch('os.scandir', side_effect=AssertionError('no traversal')), \
             patch.object(Path, 'home', side_effect=AssertionError('no home discovery')):
            result = self.load((TEXT_UTILS, TEXT_UTILS))
        self.assertEqual(len(result.sources), 1)
        self.assertEqual(len(result.evidence), 1)

    def test_relative_explicit_root_has_canonical_absolute_identity(self):
        relative = os.path.relpath(self.root, '/private' if str(self.root).startswith('/private/') else '/')
        base = Path('/private') if str(self.root).startswith('/private/') else Path('/')
        # Do not change process cwd, which other tests/workers may share.
        with patch('os.getcwd', return_value=str(base)):
            result = loader.load_packages(relative, (TEXT_UTILS,))
        self.assertEqual(result.package_path, str(self.root))
        self.assertEqual(result.sources[0].source_identity, str(self.source_path))

    def test_invalid_root_values_fail_even_without_known_imports(self):
        for value in (None, '', b'/tmp', '\x00', '//', self.root / '..' / 'packages', self.source_path):
            with self.subTest(value=value), self.assertRaises(loader.PackageInputError):
                loader.load_packages(value, ())

    def test_symlink_root_or_ancestor_is_rejected(self):
        link = self.base / 'linked'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(loader.PackageInputError):
            loader.load_packages(link, (TEXT_UTILS,))
        parent_link = self.base / 'parent-link'
        parent_link.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(loader.PackageInputError):
            loader.load_packages(parent_link / 'packages', (TEXT_UTILS,))

    def test_symlink_package_components_are_rejected(self):
        for relative in ('local', 'local/text-utils', 'local/text-utils/0.1.2'):
            path = self.root / relative
            moved = path.with_name(path.name + '-real')
            path.rename(moved)
            path.symlink_to(moved, target_is_directory=True)
            try:
                with self.subTest(component=relative), self.assertRaises(loader.PackageInputError):
                    self.load()
            finally:
                path.unlink()
                moved.rename(path)

    def test_symlink_hardlink_and_nonregular_files_are_rejected(self):
        for name in ('typst.toml', 'lib.typ'):
            path = self.directory / name
            original = path.read_bytes()
            for kind in ('symlink', 'hardlink', 'fifo', 'directory'):
                path.unlink()
                target = self.base / f'{name}-{kind}'
                target.write_bytes(original)
                if kind == 'symlink':
                    path.symlink_to(target)
                elif kind == 'hardlink':
                    os.link(target, path)
                elif kind == 'fifo':
                    os.mkfifo(path)
                else:
                    path.mkdir()
                try:
                    with self.subTest(name=name, kind=kind), self.assertRaises(loader.PackageInputError):
                        self.load()
                finally:
                    path.rmdir() if kind == 'directory' else path.unlink()
                    path.write_bytes(original)

    def test_readonly_package_files_are_not_modified(self):
        self.source_path.chmod(0o444)
        self.manifest_path.chmod(0o444)
        before = {path: (path.stat().st_mode, path.read_bytes())
                  for path in (self.source_path, self.manifest_path)}
        result = self.load()
        self.assertEqual(len(result.sources), 1)
        for path, expected in before.items():
            self.assertEqual((path.stat().st_mode, path.read_bytes()), expected)

    def test_byte_limits_are_bounded_and_exact_boundary_is_accepted(self):
        for key, length in (('MAX_SOURCE_BYTES', len(self.source)),
                            ('MAX_MANIFEST_BYTES', len(self.manifest))):
            with self.subTest(key=key):
                with patch.object(loader, key, length):
                    self.assertEqual(len(self.load().sources), 1)
                with patch.object(loader, key, length - 1), self.assertRaisesRegex(
                        loader.PackageInputError, 'byte limit'):
                    self.load()

    def test_import_list_validation_and_bounds(self):
        for imports in (None, TEXT_UTILS, [None], [''], ['\x00'], ['\ud800']):
            with self.subTest(imports=imports), self.assertRaises(loader.PackageInputError):
                self.load(imports)
        with patch.object(loader, 'MAX_IMPORT_PATHS', 1), self.assertRaises(loader.PackageInputError):
            self.load((TEXT_UTILS, TEXT_UTILS))
        with patch.object(loader, 'MAX_IMPORT_PATH_BYTES', 1), self.assertRaises(loader.PackageInputError):
            self.load((TEXT_UTILS,))

    def test_replacement_between_lstat_and_open_is_input_failure(self):
        original_open = os.open
        replacement = self.directory / 'replacement.typ'
        replacement.write_bytes(self.source)

        def opening(name, flags, **kwargs):
            if name == 'lib.typ':
                os.replace(replacement, self.source_path)
            return original_open(name, flags, **kwargs)

        with patch.object(loader.os, 'open', side_effect=opening), self.assertRaisesRegex(
                loader.PackageInputError, 'changed'):
            self.load()

    def test_replacement_during_read_is_input_failure(self):
        original_read = loader._Snapshot._read_bytes
        replacement = self.directory / 'replacement.typ'
        replacement.write_bytes(self.source)

        def reading(fd, limit, path):
            source = original_read(fd, limit, path)
            if path.name == 'lib.typ' and replacement.exists():
                os.replace(replacement, self.source_path)
            return source

        with patch.object(loader._Snapshot, '_read_bytes', staticmethod(reading)), self.assertRaisesRegex(
                loader.PackageInputError, 'changed'):
            self.load()

    def test_manifest_changed_after_source_read_is_input_failure(self):
        original_read = loader._Snapshot.read

        def reading(snapshot, directory, name, limit):
            source = original_read(snapshot, directory, name, limit)
            if name == 'lib.typ':
                self.manifest_path.write_bytes(self.manifest + b'\n# changed\n')
            return source

        with patch.object(loader._Snapshot, 'read', reading), self.assertRaisesRegex(
                loader.PackageInputError, 'changed'):
            self.load()

    def test_same_size_in_place_edit_is_input_failure(self):
        original_read = loader._Snapshot.read

        def reading(snapshot, directory, name, limit):
            source = original_read(snapshot, directory, name, limit)
            if name == 'lib.typ':
                previous = self.source_path.stat()
                self.source_path.write_bytes(b'X' + source[1:])
                os.utime(self.source_path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
            return source

        with patch.object(loader._Snapshot, 'read', reading), self.assertRaisesRegex(
                loader.PackageInputError, 'changed'):
            self.load()

    def test_final_content_check_detects_changes_even_if_stat_signatures_match(self):
        original_read = loader._Snapshot.read

        def reading(snapshot, directory, name, limit):
            source = original_read(snapshot, directory, name, limit)
            if name == 'lib.typ':
                self.source_path.write_bytes(b'X' + source[1:])
            return source

        # Exercise the independent second byte comparison rather than relying
        # on nanosecond timestamp support or the usual metadata mismatch.
        with patch.object(loader._Snapshot, 'read', reading), \
             patch.object(loader, '_file_signature', return_value=()), \
             self.assertRaisesRegex(loader.PackageInputError, 'content changed'):
            self.load()

    def test_directory_replacement_is_input_failure_even_with_same_files(self):
        original_read = loader._Snapshot.read

        def reading(snapshot, directory, name, limit):
            source = original_read(snapshot, directory, name, limit)
            if name == 'lib.typ':
                moved = self.directory.with_name('old-version')
                self.directory.rename(moved)
                self.directory.mkdir()
                for item in ('lib.typ', 'typst.toml'):
                    shutil.copyfile(moved / item, self.directory / item)
            return source

        with patch.object(loader._Snapshot, 'read', reading), self.assertRaisesRegex(
                loader.PackageInputError, 'directory changed'):
            self.load()


if __name__ == '__main__':
    unittest.main()
