"""Namespace-only variant mechanics with synthetic source, never user bytes."""
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
from package_loader import PackageInputError, PackageUnavailable, _Snapshot

REPOSITORY = TOOL.parents[1]
# This is synthetic test input, not a copy of an installed/user package.
SOURCE = b'#let highlighted(body) = body\n#let paragraph_tab = "synthetic"\n'
MANIFEST = (REPOSITORY / 'local/text-utils/0.1.2/typst.toml').read_bytes()


class TextUtilsCollisionNamespaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / 'packages'
        self.directory = self.root / 'local/text-utils/0.1.2'
        self.directory.mkdir(parents=True)
        self.manifest = self.directory / 'typst.toml'
        self.source = self.directory / 'lib.typ'
        self.manifest.write_bytes(MANIFEST)
        self.source.write_bytes(SOURCE)
        # The test seam exercises exact-byte checks with synthetic source; it
        # does not assert that the synthetic source has the reviewed exports.
        contract = replace(namespace._TEXT_COLLISION_CONTRACT,
                           manifest_sha256=hashlib.sha256(MANIFEST).hexdigest(),
                           source_sha256=hashlib.sha256(SOURCE).hexdigest())
        patcher = patch.object(namespace, '_TEXT_COLLISION_CONTRACT', contract)
        patcher.start()
        self.addCleanup(patcher.stop)

    def load(self):
        return namespace.load_text_utils_collision_only(self.root)

    def test_exact_bytes_produce_separate_collision_only_evidence(self):
        before = {p: (p.stat().st_ino, p.stat().st_mtime_ns, p.read_bytes())
                  for p in (self.manifest, self.source)}
        capture = self.load()
        self.assertTrue(namespace.verify_text_utils_collision_capture(capture))
        self.assertIsNone(capture.unavailable)
        package = capture.package
        self.assertIs(type(package), namespace.CollisionOnlyNamespace)
        self.assertEqual(package.exports, namespace._TEXT_EXPORTS)
        self.assertEqual(package.evidence.import_path, namespace.TEXT_UTILS)
        self.assertEqual(package.evidence.source_sha256, hashlib.sha256(SOURCE).hexdigest())
        self.assertFalse(hasattr(package, 'origins'))
        self.assertEqual(namespace._origins(namespace._TEXT_COLLISION_CONTRACT), ())
        with self.assertRaises(FrozenInstanceError):
            package.exports = ()
        self.assertEqual(before, {p: (p.stat().st_ino, p.stat().st_mtime_ns, p.read_bytes())
                                  for p in (self.manifest, self.source)})

    def test_collision_capture_cannot_be_used_as_full_namespace_or_helper_proof(self):
        collision = self.load()
        self.assertFalse(namespace.verify_namespace_capture(collision))
        ordinary = namespace.load_namespace_packages(self.root)
        self.assertTrue(namespace.verify_namespace_capture(ordinary))
        self.assertEqual(next(item for item in ordinary.unavailable
                              if item.import_path == namespace.TEXT_UTILS).code,
                         'unreviewed-namespace-source')
        # Even an attempted promotion preserving all six accounting slots is
        # rejected: the verifier demands the original exact package type.
        remaining = tuple(item for item in ordinary.unavailable if item.import_path != namespace.TEXT_UTILS)
        forged = namespace.NamespacePackageCapture(str(self.root), (collision.package,), remaining)
        self.assertFalse(namespace.verify_namespace_capture(forged))

    def test_manifest_and_source_mutations_are_distinct_refusals(self):
        self.source.write_bytes(SOURCE + b'\n')
        capture = self.load()
        self.assertTrue(namespace.verify_text_utils_collision_capture(capture))
        self.assertEqual(capture.unavailable.code, 'unreviewed-namespace-source')
        self.manifest.write_bytes(MANIFEST + b'\n')
        self.source.unlink()
        self.source.symlink_to('/must-not-be-read')
        capture = self.load()
        self.assertEqual(capture.unavailable.code, 'unreviewed-namespace-manifest')
        self.assertTrue(namespace.verify_text_utils_collision_capture(capture))

    def test_missing_and_unsafe_files_have_different_outcomes(self):
        self.source.unlink()
        capture = self.load()
        self.assertEqual(capture.unavailable.code, 'missing-namespace-artifact')
        self.assertTrue(namespace.verify_text_utils_collision_capture(capture))
        other = self.directory / 'other.typ'
        other.write_bytes(SOURCE)
        self.source.symlink_to(other)
        with self.assertRaises(PackageInputError):
            self.load()
        self.source.unlink()
        os.link(other, self.source)
        with self.assertRaises(PackageInputError):
            self.load()

    def test_verifier_rejects_forgery_and_never_reopens_files(self):
        capture = self.load()
        package = capture.package
        for forged in (replace(capture, package=replace(package, exports=())),
                       replace(capture, package=replace(package, source=SOURCE + b'x')),
                       replace(capture, package=replace(package, evidence=replace(
                           package.evidence, source_identity='/incorrect/path'))),
                       replace(capture, unavailable=PackageUnavailable(namespace.TEXT_UTILS, 'x', 'y'))):
            self.assertFalse(namespace.verify_text_utils_collision_capture(forged))
        shutil.rmtree(self.root)
        with patch('os.open', side_effect=AssertionError('verification must be pure')), \
             patch('os.stat', side_effect=AssertionError('verification must be pure')):
            self.assertTrue(namespace.verify_text_utils_collision_capture(capture))

    def test_snapshot_constructor_requires_exact_bytes_and_does_not_read(self):
        with patch('os.open', side_effect=AssertionError('constructor must be pure')), \
             patch('os.stat', side_effect=AssertionError('constructor must be pure')):
            capture = namespace.text_utils_collision_from_snapshot(str(self.root), MANIFEST, SOURCE)
            self.assertTrue(namespace.verify_text_utils_collision_capture(capture))
            for manifest, source in ((MANIFEST + b'x', SOURCE), (MANIFEST, SOURCE + b'x'),
                                     (bytearray(MANIFEST), SOURCE), (MANIFEST, bytearray(SOURCE))):
                with self.assertRaises(PackageInputError):
                    namespace.text_utils_collision_from_snapshot(str(self.root), manifest, source)

    def test_snapshot_recheck_failure_aborts_capture(self):
        with patch.object(_Snapshot, 'verify', side_effect=PackageInputError('changed during capture')):
            with self.assertRaisesRegex(PackageInputError, 'changed during capture'):
                self.load()
