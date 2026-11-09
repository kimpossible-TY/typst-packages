"""Namespace-only evidence from explicit roots, without cache or import evaluation."""
from pathlib import Path
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
import package_loader as loader
from scoped_rules import analyze, REFERENCE, CALL
from static_resolver import (SCOPED_ANNOTATIONS, SCOPED_ANNOTATIONS_SOURCE_SHA256,
                             MANNOT_NAMESPACE, MANNOT_NAMESPACE_SOURCE_SHA256,
                             MANNOT_NAMESPACE_MANIFEST_SHA256, resolve)
from syntax_client import parse

FIXTURE = TOOL / 'tests/fixtures/scoped-reference-packages/preview/mannot/0.4.0'
SOURCE = (b'#import "@local/scoped-annotations:0.3.0": local-tag-scope\n'
          b'#local-tag-scope(s => [#s.tag("x") #ref((s.tag)("x"))])')


class ScopedNamespaceLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / 'packages'
        self.scope = self.root / 'local/scoped-annotations/0.3.0'
        self.namespace = self.root / 'preview/mannot/0.4.0'
        shutil.copytree(REPO / 'local/scoped-annotations/0.3.0', self.scope)
        shutil.copytree(FIXTURE, self.namespace)

    def load(self, paths=(SCOPED_ANNOTATIONS,)):
        return loader.load_packages(self.root, paths)

    def test_exact_namespace_manifest_and_source_are_real_separate_evidence(self):
        original_read = loader._Snapshot.read
        reads = []
        def read(snapshot, parent, name, limit):
            self.assertNotIn('/', name)
            reads.append(parent.path / name)
            return original_read(snapshot, parent, name, limit)
        with patch.object(loader._Snapshot, 'read', read), \
             patch.object(Path, 'home', side_effect=AssertionError('no home discovery')), \
             patch('os.walk', side_effect=AssertionError('no dependency traversal')), \
             patch('os.scandir', side_effect=AssertionError('no dependency traversal')):
            result = self.load()
        self.assertEqual([p.import_path for p in result.sources], [SCOPED_ANNOTATIONS, MANNOT_NAMESPACE])
        self.assertFalse(result.unavailable)
        self.assertEqual(set(reads), {self.scope / 'typst.toml', self.scope / 'lib.typ',
                                    self.namespace / 'typst.toml', self.namespace / 'src/lib.typ'})
        self.assertFalse((self.namespace / 'src/mark.typ').exists())
        evidence = next(p for p in result.evidence if p.import_path == MANNOT_NAMESPACE)
        self.assertEqual(evidence.source_sha256, MANNOT_NAMESPACE_SOURCE_SHA256)
        self.assertEqual(evidence.manifest_sha256, MANNOT_NAMESPACE_MANIFEST_SHA256)
        self.assertEqual(evidence.source_identity, str(self.namespace / 'src/lib.typ'))
        analyzed = analyze(parse(SOURCE), 'main.typ', packages=result.sources)
        self.assertTrue(analyzed[REFERENCE].edits)
        self.assertTrue(analyzed[CALL].edits)

    def test_every_initial_optional_absence_keeps_other_rules_and_explicit_refusal(self):
        for relative in ('preview', 'preview/mannot', 'preview/mannot/0.4.0',
                         'preview/mannot/0.4.0/typst.toml', 'preview/mannot/0.4.0/src',
                         'preview/mannot/0.4.0/src/lib.typ'):
            with self.subTest(relative=relative):
                path = self.root / relative
                moved = self.base / 'temporarily-absent'
                path.rename(moved)
                try:
                    result = self.load()
                    self.assertEqual([p.import_path for p in result.sources], [SCOPED_ANNOTATIONS])
                    self.assertEqual([u.code for u in result.unavailable], ['missing-optional-namespace'])
                    analyzed = analyze(parse(SOURCE), 'main.typ', packages=result.sources)
                    self.assertTrue(analyzed[REFERENCE].diagnostics)
                    self.assertFalse(analyzed[REFERENCE].edits)
                    self.assertTrue(analyzed[CALL].edits)
                    self.assertTrue(any('exclude ref/label shadowing' in s for s in analyzed[REFERENCE].skips))
                finally:
                    moved.rename(path)

    def test_direct_requested_artifacts_remain_required_and_are_not_duplicated(self):
        result = self.load((MANNOT_NAMESPACE, SCOPED_ANNOTATIONS))
        self.assertEqual(len(result.sources), 2)
        self.assertEqual(len(result.evidence), 2)
        (self.namespace / 'src/lib.typ').unlink()
        with self.assertRaises(loader.PackageInputError):
            self.load((MANNOT_NAMESPACE, SCOPED_ANNOTATIONS))
        with self.assertRaises(loader.PackageInputError):
            self.load((MANNOT_NAMESPACE,))
        self.assertEqual(self.load().unavailable[0].code, 'missing-optional-namespace')

    def test_unverified_wrapper_never_requests_namespace_artifact(self):
        moved = self.base / 'real-namespace'
        (self.root / 'preview').rename(moved)
        (self.root / 'preview').symlink_to(moved, target_is_directory=True)
        (self.scope / 'lib.typ').write_bytes(b'// modified wrapper\n')
        result = self.load()
        self.assertFalse(result.sources)
        self.assertEqual([e.import_path for e in result.evidence], [SCOPED_ANNOTATIONS])
        self.assertEqual([u.code for u in result.unavailable], ['unreviewed-package-source'])
        with patch.object(loader._Snapshot, 'read', side_effect=AssertionError('no unrelated read')):
            result = self.load(('@local/scoped-annotations:0.4.0',))
        self.assertEqual(result.unavailable[0].code, 'unreviewed-package')

    def test_namespace_only_artifact_never_exposes_raw_api_or_complete_wildcard(self):
        packages = self.load().sources
        for source in (
            b'#import "@preview/mannot:0.4.0": mark\n#mark[x]',
            b'#import "@preview/mannot:0.4.0" as m\n#m.mark[x]',
            b'#let mark = highlight\n#import "@preview/mannot:0.4.0": *\n#mark[x]',
            b'#let ref = highlight\n#import "@preview/mannot:0.4.0": *\n#ref[x]',
        ):
            with self.subTest(source=source):
                tree = parse(source)
                resolved = resolve(tree, packages=packages)
                call = next(i for i, n in enumerate(tree.nodes) if n.kind == 'FuncCall')
                self.assertIsNone(resolved.binding_for(call))
                self.assertTrue(any('verified direct-package contract' in s for s in resolved.skips))

    def test_changed_manifest_or_source_never_attests_namespace(self):
        for relative, suffix, code in (('typst.toml', b'\n# changed\n', 'unreviewed-package-manifest'),
                                       ('src/lib.typ', b'#let ref = x => x\n', 'unreviewed-package-source')):
            with self.subTest(relative=relative):
                path = self.namespace / relative
                original = path.read_bytes()
                path.write_bytes(original + suffix)
                try:
                    result = self.load()
                    self.assertEqual([p.import_path for p in result.sources], [SCOPED_ANNOTATIONS])
                    self.assertEqual(result.unavailable[0].code, code)
                    self.assertFalse(analyze(parse(SOURCE), 'main.typ', packages=result.sources)[REFERENCE].edits)
                finally:
                    path.write_bytes(original)

    def test_malformed_optional_manifest_is_not_treated_as_missing(self):
        (self.namespace / 'typst.toml').write_bytes(b'[package')
        with self.assertRaisesRegex(loader.PackageInputError, 'Malformed package manifest'):
            self.load()

    def test_symlink_components_and_optional_files_are_fail_closed(self):
        for relative in ('preview', 'preview/mannot/0.4.0/src',
                         'preview/mannot/0.4.0/src/lib.typ', 'preview/mannot/0.4.0/typst.toml'):
            path = self.root / relative
            moved = self.base / 'symlink-target'
            path.rename(moved)
            path.symlink_to(moved, target_is_directory=moved.is_dir())
            try:
                with self.subTest(relative=relative), self.assertRaises(loader.PackageInputError):
                    self.load()
            finally:
                path.unlink()
                moved.rename(path)

    def test_hardlink_fifo_and_directory_inputs_are_not_optional_absence(self):
        for relative in ('typst.toml', 'src/lib.typ'):
            path = self.namespace / relative
            original = path.read_bytes()
            for kind in ('hardlink', 'fifo', 'directory'):
                with self.subTest(relative=relative, kind=kind):
                    path.unlink()
                    other = self.base / 'hardlink-target'
                    if kind == 'hardlink':
                        other.write_bytes(original)
                        os.link(other, path)
                    elif kind == 'fifo':
                        os.mkfifo(path)
                    else:
                        path.mkdir()
                    try:
                        with self.assertRaises(loader.PackageInputError):
                            self.load()
                    finally:
                        path.rmdir() if path.is_dir() else path.unlink()
                        if other.exists(): other.unlink()
                        path.write_bytes(original)

    def test_raced_optional_disappearance_after_existence_check_fails(self):
        original_read = loader._Snapshot.read
        def disappear(snapshot, parent, name, limit):
            if parent.path == self.namespace / 'src' and name == 'lib.typ':
                (parent.path / name).unlink()
            return original_read(snapshot, parent, name, limit)
        with patch.object(loader._Snapshot, 'read', disappear), self.assertRaises(loader.PackageInputError):
            self.load()

    def test_raced_optional_directory_disappearance_fails(self):
        original_directory = loader._Snapshot.directory
        def disappear(snapshot, parent, name):
            if parent.path == self.namespace and name == 'src':
                (parent.path / name).rename(self.base / 'moved-src')
            return original_directory(snapshot, parent, name)
        with patch.object(loader._Snapshot, 'directory', disappear), self.assertRaises(loader.PackageInputError):
            self.load()

    def test_namespace_change_after_capture_is_detected_by_retained_snapshot(self):
        original_read = loader._Snapshot.read
        def tamper(snapshot, parent, name, limit):
            raw = original_read(snapshot, parent, name, limit)
            if parent.path == self.namespace / 'src' and name == 'lib.typ':
                (parent.path / name).write_bytes(raw.replace(b'core-mark', b'evil-mark'))
            return raw
        with patch.object(loader._Snapshot, 'read', tamper), \
             self.assertRaisesRegex(loader.PackageInputError, 'changed while reading'):
            self.load()

    def test_permissions_error_is_not_optional_absence(self):
        original_stat = os.stat
        def deny(path, *args, **kwargs):
            if path == 'preview' and kwargs.get('dir_fd') is not None:
                raise PermissionError('fixture denied')
            return original_stat(path, *args, **kwargs)
        with patch('os.stat', deny), self.assertRaisesRegex(loader.PackageInputError, 'fixture denied'):
            self.load()


if __name__ == '__main__':
    unittest.main()
