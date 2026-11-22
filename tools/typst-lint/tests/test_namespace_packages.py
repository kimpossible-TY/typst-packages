"""Real explicit-root namespace snapshots; no user caches or evaluator needed."""
from dataclasses import FrozenInstanceError, replace
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
import namespace_packages as namespace
from package_loader import PackageInputError, _Snapshot

REPOSITORY = TOOL.parents[1]
FIXTURES = TOOL / 'tests/fixtures'
# Pin test input locations independently from the implementation's contracts.
# These existing public fixture roots retain their original package provenance.
ROOTS = (
    ('local', 'text-utils', '0.1.2', 'lib.typ', FIXTURES / 'reviewed-packages'),
    ('local', 'math-blocks', '0.2.0', 'lib.typ', REPOSITORY),
    ('local', 'scoped-annotations', '0.3.0', 'lib.typ', REPOSITORY),
    ('local', 'cetz-helpers', '0.2.0', 'lib.typ', REPOSITORY),
    ('preview', 'mannot', '0.4.0', 'src/lib.typ', FIXTURES / 'scoped-reference-packages'),
    ('preview', 'cetz', '0.4.2', 'src/lib.typ', FIXTURES / 'anchor-overlay-packages'),
)


class NamespacePackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'packages'
        self.root.mkdir()
        self.original = {}
        for ns, name, version, entrypoint, fixture in ROOTS:
            relative = Path(ns) / name / version
            for artifact in ('typst.toml', entrypoint):
                data = (fixture / relative / artifact).read_bytes()
                target = self.root / relative / artifact
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                self.original[target] = data
        self.text = self.root / 'local/text-utils/0.1.2'
        self.source = self.text / 'lib.typ'
        self.manifest = self.text / 'typst.toml'

    def load(self):
        return namespace.load_namespace_packages(self.root)

    def test_exact_six_roots_have_complete_conditional_names_and_byte_evidence(self):
        before = {path: path.stat() for path in self.original}
        capture = self.load()
        self.assertTrue(namespace.verify_namespace_capture(capture))
        self.assertEqual(capture.package_path, str(self.root))
        self.assertEqual(capture.unavailable, ())
        self.assertEqual(len(capture.packages), 6)
        expected_counts = (6, 27, 17, 23, 11, 19)
        self.assertEqual(tuple(len(p.exports) for p in capture.packages), expected_counts)
        for package in capture.packages:
            evidence = package.evidence
            self.assertEqual(package.source, self.original[Path(evidence.source_identity)])
            self.assertEqual(package.manifest, self.original[Path(evidence.manifest_identity)])
            self.assertEqual(evidence.source_sha256, hashlib.sha256(package.source).hexdigest())
            self.assertEqual(evidence.manifest_sha256, hashlib.sha256(package.manifest).hexdigest())
            self.assertEqual(package.exports, tuple(sorted(set(package.exports))))
            self.assertNotIn('footnote', package.exports)
            self.assertNotIn('label', package.exports)
        scopes = capture.packages[2]
        self.assertIn('_local-scope-counter', scopes.exports)
        self.assertIn('_local-scope-annotation-counter', scopes.exports)
        self.assertEqual(capture.packages[0].origins,
                         (('highlighted', '@local/text-utils:0.1.2'),))
        self.assertTrue(all(not p.origins for p in capture.packages[1:]))
        self.assertEqual(capture.evidence, tuple(p.evidence for p in capture.packages))
        for path, data in self.original.items():
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual((path.stat().st_ino, path.stat().st_mtime_ns),
                             (before[path].st_ino, before[path].st_mtime_ns))

    def test_snapshot_is_immutable_and_verifier_does_not_reopen_files(self):
        capture = self.load()
        with self.assertRaises(FrozenInstanceError):
            capture.package_path = '/changed'
        with self.assertRaises(FrozenInstanceError):
            capture.packages[0].exports = ()
        shutil.rmtree(self.root)
        with patch('os.open', side_effect=AssertionError('verifier must be pure')), \
             patch('os.stat', side_effect=AssertionError('verifier must be pure')):
            self.assertTrue(namespace.verify_namespace_capture(capture))

    def test_source_bytes_must_match_without_whitespace_or_bom_normalization(self):
        self.source.write_bytes(self.original[self.source] + b'\n')
        capture = self.load()
        self.assertTrue(namespace.verify_namespace_capture(capture))
        self.assertEqual(len(capture.packages), 5)
        self.assertEqual(capture.unavailable[0].import_path, namespace.TEXT_UTILS)
        self.assertEqual(capture.unavailable[0].code, 'unreviewed-namespace-source')
        self.assertFalse(any(p.origins for p in capture.packages))

    def test_manifest_bytes_are_pinned_before_any_entrypoint_read(self):
        self.manifest.write_bytes(self.original[self.manifest] + b'\n# changed\n')
        self.source.unlink()
        self.source.symlink_to('/unread/untrusted.typ')
        capture = self.load()
        self.assertEqual(capture.unavailable[0].code, 'unreviewed-namespace-manifest')
        self.assertEqual(len(capture.packages), 5)
        self.assertTrue(namespace.verify_namespace_capture(capture))

    def test_partial_absence_preserves_independent_evidence_and_blocks_wildcard_parent(self):
        (self.root / 'preview/mannot/0.4.0/src/lib.typ').unlink()
        capture = self.load()
        self.assertTrue(namespace.verify_namespace_capture(capture))
        self.assertEqual({p.import_path for p in capture.packages}, {
            namespace.TEXT_UTILS, '@local/math-blocks:0.2.0',
            '@local/cetz-helpers:0.2.0', namespace.CETZ})
        self.assertEqual({item.import_path: item.code for item in capture.unavailable}, {
            '@local/scoped-annotations:0.3.0': 'unavailable-namespace-dependency',
            namespace.MANNOT: 'missing-namespace-artifact'})

    def test_mismatched_wildcard_dependency_does_not_invent_parent_names(self):
        cetz = self.root / 'preview/cetz/0.4.2/src/lib.typ'
        cetz.write_bytes(cetz.read_bytes() + b'\n#let highlighted = none\n')
        capture = self.load()
        self.assertEqual(len(capture.packages), 4)
        self.assertEqual({u.import_path for u in capture.unavailable},
                         {namespace.CETZ, '@local/cetz-helpers:0.2.0'})
        self.assertTrue(namespace.verify_namespace_capture(capture))

    def test_unknown_files_are_never_discovered_and_only_twelve_files_are_read(self):
        (self.root / 'unrelated').symlink_to(self.base, target_is_directory=True)
        (self.text / 'unknown.typ').write_bytes(b'\xff')
        original = _Snapshot.read
        reads = []
        def record(snapshot, directory, name, limit):
            reads.append(directory.path / name)
            return original(snapshot, directory, name, limit)
        with patch.object(_Snapshot, 'read', record), \
             patch('os.walk', side_effect=AssertionError('no discovery')), \
             patch('os.scandir', side_effect=AssertionError('no discovery')), \
             patch.object(Path, 'home', side_effect=AssertionError('no ambient cache')):
            capture = self.load()
        self.assertTrue(namespace.verify_namespace_capture(capture))
        self.assertEqual(set(reads), set(self.original))
        self.assertEqual(len(reads), 12)

    def test_symlink_source_and_hardlink_are_input_failures(self):
        backup = self.base / 'real.typ'
        self.source.rename(backup)
        self.source.symlink_to(backup)
        with self.assertRaisesRegex(PackageInputError, 'symbolic link'):
            self.load()
        self.source.unlink()
        os.link(backup, self.source)
        with self.assertRaisesRegex(PackageInputError, 'Hard-linked'):
            self.load()

    def test_symlink_manifest_or_package_directory_is_input_failure(self):
        real = self.base / 'manifest.toml'
        self.manifest.rename(real)
        self.manifest.symlink_to(real)
        with self.assertRaises(PackageInputError):
            self.load()
        self.manifest.unlink()
        real.rename(self.manifest)
        real_directory = self.base / 'real-textutils'
        self.text.rename(real_directory)
        self.text.symlink_to(real_directory, target_is_directory=True)
        with self.assertRaisesRegex(PackageInputError, 'symbolic link'):
            self.load()

    def test_only_canonical_absolute_root_is_accepted(self):
        for value in ('relative', str(self.root) + '/',
                      str(self.root) + '/.', str(self.root) + '/../packages'):
            with self.subTest(value=value), self.assertRaises(PackageInputError):
                namespace.load_namespace_packages(value)
        with self.assertRaises(PackageInputError):
            namespace.load_namespace_packages(self.base / 'absent')

    def test_non_utf8_root_is_rejected_before_filesystem_access(self):
        capture = self.load()
        invalid = str(self.root) + '/' + os.fsdecode(b'\xff')
        with patch.object(namespace, '_Snapshot', side_effect=AssertionError('no snapshot')), \
             patch('os.open', side_effect=AssertionError('no filesystem access')):
            with self.assertRaisesRegex(PackageInputError, 'must be UTF-8') as error:
                namespace.load_namespace_packages(invalid)
            # The error itself must remain safe for ensure_ascii=False JSON.
            str(error.exception).encode('utf-8')
            self.assertFalse(namespace.verify_namespace_capture(replace(capture, package_path=invalid)))

    def test_root_size_limits_are_rejected_before_filesystem_access(self):
        capture = self.load()
        oversized = (
            '/' + 'x' * 4096,
            '/' + '\u00e9' * 2048,
            '/' + '/'.join('a' for _ in range(128)),
        )
        with patch.object(namespace, '_Snapshot', side_effect=AssertionError('no snapshot')), \
             patch('os.open', side_effect=AssertionError('no filesystem access')):
            for value in oversized:
                with self.subTest(size=len(value)), \
                     self.assertRaisesRegex(PackageInputError, 'path limit exceeded'):
                    namespace.load_namespace_packages(value)
                self.assertFalse(namespace.verify_namespace_capture(replace(capture, package_path=value)))
        self.assertEqual(str(namespace._canonical_root('/' + 'x' * 4095)), '/' + 'x' * 4095)
        boundary = '/' + '/'.join('a' for _ in range(127))
        self.assertEqual(str(namespace._canonical_root(boundary)), boundary)

    def test_capture_verifier_rejects_forged_names_hashes_origins_and_identities(self):
        capture = self.load()
        first = capture.packages[0]
        forged = (
            replace(first, source=first.source + b'\n'),
            replace(first, manifest=first.manifest + b'\n'),
            replace(first, exports=first.exports + ('footnote',)),
            replace(first, exports=list(first.exports)),
            replace(first, origins=(('paragraph_tab', namespace.TEXT_UTILS),)),
            replace(first, evidence=replace(first.evidence, source_identity='/other/lib.typ')),
            replace(first, evidence=replace(first.evidence, source_sha256='0' * 64)),
            replace(first, evidence=replace(first.evidence, namespace='preview')),
        )
        for package in forged:
            with self.subTest(package=package):
                changed = replace(capture, packages=(package,) + capture.packages[1:])
                self.assertFalse(namespace.verify_namespace_capture(changed))
        self.assertFalse(namespace.verify_namespace_capture(replace(capture, packages=capture.packages[:-1])))
        self.assertFalse(namespace.verify_namespace_capture(replace(capture, packages=list(capture.packages))))
        self.assertFalse(namespace.verify_namespace_capture(replace(capture, package_path=str(self.base))))
        self.assertFalse(namespace.verify_namespace_capture(replace(capture,
            packages=capture.packages[:-1] + (capture.packages[0],))))

    def test_final_snapshot_verification_detects_in_place_content_change(self):
        original = _Snapshot.verify
        def change(snapshot):
            self.source.write_bytes(self.original[self.source][:-1] + b'!')
            original(snapshot)
        with patch.object(_Snapshot, 'verify', change), \
             self.assertRaisesRegex(PackageInputError, 'changed while reading'):
            self.load()

    def test_oversized_or_non_utf8_source_is_input_failure(self):
        self.source.write_bytes(b'\xff')
        with self.assertRaisesRegex(PackageInputError, 'UTF-8'):
            self.load()
        self.source.write_bytes(b'x' * (namespace.MAX_SOURCE_BYTES + 1))
        with self.assertRaisesRegex(PackageInputError, 'byte limit'):
            self.load()


if __name__ == '__main__':
    unittest.main()
