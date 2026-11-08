"""Explicit multi-root namespace capture and precedence evidence."""
from dataclasses import replace
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
import namespace_multiroot as multi
import namespace_packages as namespace
from package_loader import PackageInputError, _Snapshot

REPOSITORY = TOOL.parents[1]
FIXTURES = TOOL / 'tests/fixtures'
ARTIFACTS = (
    ('local', 'text-utils', '0.1.2', 'lib.typ', (TOOL / 'tests/fixtures/reviewed-packages')),
    ('local', 'math-blocks', '0.2.0', 'lib.typ', REPOSITORY),
    ('local', 'scoped-annotations', '0.3.0', 'lib.typ', REPOSITORY),
    ('local', 'cetz-helpers', '0.2.0', 'lib.typ', REPOSITORY),
    ('preview', 'mannot', '0.4.0', 'src/lib.typ', FIXTURES / 'scoped-reference-packages'),
    ('preview', 'cetz', '0.4.2', 'src/lib.typ', FIXTURES / 'anchor-overlay-packages'),
)


class MultiRootNamespaceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.package, self.cache = self.base / 'packages', self.base / 'cache'
        self.package.mkdir()
        self.cache.mkdir()
        for ns, name, version, entry, source in ARTIFACTS:
            relative = Path(ns) / name / version
            target_root = self.package if ns == 'local' else self.cache
            for filename in ('typst.toml', entry):
                target = target_root / relative / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / relative / filename, target)

    def capture(self):
        return multi.load_multiroot_capture(self.package, self.cache)

    def test_explicit_roots_complete_cross_root_wildcard_dependencies(self):
        capture = self.capture()
        self.assertTrue(multi.verify_multiroot_capture(capture))
        self.assertEqual(capture.roots, (str(self.package), str(self.cache)))
        self.assertEqual(len(capture.packages), 6)
        self.assertFalse(capture.unavailable)
        selected = {row.import_path: row.root_index for row in capture.selections}
        self.assertEqual(selected['@local/scoped-annotations:0.3.0'], 0)
        self.assertEqual(selected['@preview/mannot:0.4.0'], 1)
        self.assertEqual(selected['@preview/cetz:0.4.2'], 1)
        self.assertEqual(capture.evidence, tuple(item.evidence for item in capture.packages))

    def test_first_existing_version_wins_and_does_not_fall_back_on_bad_artifacts(self):
        relative = Path('preview/mannot/0.4.0')
        first = self.package / relative
        shutil.copytree(self.cache / relative, first)
        capture = self.capture()
        self.assertEqual(next(x.root_index for x in capture.selections if x.import_path == namespace.MANNOT), 0)
        for mutation, expected in (('missing_manifest', 'missing-namespace-artifact'),
                                   ('bad_manifest', 'unreviewed-namespace-manifest'),
                                   ('missing_source', 'missing-namespace-artifact'),
                                   ('bad_source', 'unreviewed-namespace-source')):
            with self.subTest(mutation=mutation):
                shutil.rmtree(first)
                shutil.copytree(self.cache / relative, first)
                if mutation == 'missing_manifest':
                    (first / 'typst.toml').unlink()
                elif mutation == 'bad_manifest':
                    (first / 'typst.toml').write_text('bad')
                elif mutation == 'missing_source':
                    (first / 'src/lib.typ').unlink()
                else:
                    (first / 'src/lib.typ').write_text('bad')
                capture = self.capture()
                self.assertEqual(next(x.root_index for x in capture.selections if x.import_path == namespace.MANNOT), 0)
                self.assertEqual(next(x.code for x in capture.unavailable if x.import_path == namespace.MANNOT), expected)
                self.assertFalse(any(x.evidence.import_path == namespace.MANNOT for x in capture.packages))
                self.assertFalse(any(x.evidence.import_path == '@local/scoped-annotations:0.3.0'
                                     for x in capture.packages))

    def test_version_symlinks_files_and_root_aliases_do_not_fall_back(self):
        version = self.package / 'preview/mannot/0.4.0'
        version.parent.mkdir(parents=True)
        version.symlink_to(self.cache / 'preview/mannot/0.4.0', target_is_directory=True)
        with self.assertRaises(PackageInputError):
            self.capture()
        version.unlink()
        version.write_text('not a directory')
        with self.assertRaises(PackageInputError):
            self.capture()
        with self.assertRaises(PackageInputError):
            multi.load_multiroot_capture(self.package, self.package)

    def test_missing_higher_priority_package_is_rechecked_after_reads(self):
        original = _Snapshot.verify
        triggered = False
        def create_first(snapshot):
            nonlocal triggered
            if snapshot.root == self.cache and not triggered:
                triggered = True
                (self.package / 'preview/mannot/0.4.0').mkdir(parents=True)
            return original(snapshot)
        with patch.object(_Snapshot, 'verify', create_first), self.assertRaisesRegex(PackageInputError, 'absence changed'):
            self.capture()
        self.assertTrue(triggered)

    def test_changed_selected_source_is_refused(self):
        original = _Snapshot.verify
        triggered = False
        def change_file(snapshot):
            nonlocal triggered
            if snapshot.root == self.package and not triggered:
                triggered = True
                target = self.package / 'local/text-utils/0.1.2/lib.typ'
                target.write_bytes(target.read_bytes() + b'\n')
            return original(snapshot)
        with patch.object(_Snapshot, 'verify', change_file), self.assertRaises(PackageInputError):
            self.capture()
        self.assertTrue(triggered)

    def test_issuance_and_pure_verification_prevent_forged_selection(self):
        capture = self.capture()
        with patch('os.open', side_effect=AssertionError('no filesystem IO')), \
             patch('os.stat', side_effect=AssertionError('no filesystem IO')):
            self.assertTrue(multi.verify_multiroot_capture(capture))
            self.assertFalse(multi.verify_multiroot_capture(replace(capture)))
        changed = replace(capture.selections[0], root_index=1)
        object.__setattr__(capture, 'selections', (changed,) + capture.selections[1:])
        self.assertFalse(multi.verify_multiroot_capture(capture))

    def test_collision_only_variant_never_acquires_helper_origins(self):
        target = self.package / 'local/text-utils/0.1.2/lib.typ'
        synthetic = b'#let test_namespace_only = none\n'
        target.write_bytes(synthetic)
        variant = replace(namespace._TEXT_COLLISION_CONTRACT,
                          source_sha256=hashlib.sha256(synthetic).hexdigest())
        with patch.object(namespace, '_TEXT_COLLISION_CONTRACT', variant):
            capture = self.capture()
            self.assertTrue(multi.verify_multiroot_capture(capture))
            item = next(x for x in capture.packages if x.evidence.import_path == namespace.TEXT_UTILS)
            self.assertIs(type(item), namespace.CollisionOnlyNamespace)
            self.assertFalse(hasattr(item, 'origins'))
            self.assertNotIn('project-ref', item.exports)

    def test_unreviewed_first_root_does_not_fall_through_to_collision_variant(self):
        relative = Path('local/text-utils/0.1.2')
        shutil.copytree(self.package / relative, self.cache / relative)
        (self.package / relative / 'lib.typ').write_text('unreviewed first source')
        synthetic = b'#let synthetic_collision_namespace = none\n'
        (self.cache / relative / 'lib.typ').write_bytes(synthetic)
        variant = replace(namespace._TEXT_COLLISION_CONTRACT,
                          source_sha256=hashlib.sha256(synthetic).hexdigest())
        with patch.object(namespace, '_TEXT_COLLISION_CONTRACT', variant):
            capture = self.capture()
            self.assertTrue(multi.verify_multiroot_capture(capture))
            self.assertEqual(next(x.root_index for x in capture.selections
                                  if x.import_path == namespace.TEXT_UTILS), 0)
            self.assertEqual(next(x.code for x in capture.unavailable
                                  if x.import_path == namespace.TEXT_UTILS), 'unreviewed-namespace-source')
            self.assertFalse(any(item.evidence.import_path == namespace.TEXT_UTILS
                                 for item in capture.packages))


if __name__ == '__main__':
    unittest.main()
