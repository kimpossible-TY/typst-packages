"""Adversarial real-artifact capture and scoped-marker capability boundaries."""
from dataclasses import FrozenInstanceError, replace
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
import package_loader as loader
from marker_bounds_rules import applied_marker, analyze, TAGS, BOUNDS
from package_artifacts import MANNOT_ARTIFACTS
from scoped_provenance import resolve
from static_resolver import (SCOPED_ANNOTATIONS, MANNOT_NAMESPACE, has_mannot_namespace,
                             is_mannot_binding, imported_paths)
from syntax_client import parse
from typst_lint import InputError, digest

FIXTURE = TOOL / 'tests/fixtures/marker-bounds-packages/preview/mannot/0.4.0'
OLD_FIXTURE = TOOL / 'tests/fixtures/scoped-reference-packages/preview/mannot/0.4.0'
IMPORT = (b'#import "@local/scoped-annotations:0.3.0": mannot-scope\n'
          b'#import "@preview/mannot:0.4.0": mark, annot, annot-cetz, core-annot\n')


class SupplementalArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='typst-supplemental-artifacts-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / 'packages'
        self.scope = self.root / 'local/scoped-annotations/0.3.0'
        self.mannot = self.root / 'preview/mannot/0.4.0'
        shutil.copytree(REPO / 'local/scoped-annotations/0.3.0', self.scope)
        shutil.copytree(FIXTURE, self.mannot)

    def load(self):
        # The reviewed wrapper may request only this fixed optional graph.
        return loader.load_packages(self.root, (SCOPED_ANNOTATIONS,))

    def resolver(self, source=b'', packages=None):
        tree = parse(source)
        self.assertFalse(tree.nodes[0].errors, source)
        return tree, resolve(tree, self.load().sources if packages is None else packages)

    def capabilities(self, packages=None):
        _, r = self.resolver(packages=packages)
        return {name: is_mannot_binding(r.package_binding(MANNOT_NAMESPACE, name), name)
                for name in ('mark', 'annot', 'annot-cetz', 'core-annot')}

    def marker_applications(self, body, packages=None):
        tree, r = self.resolver(IMPORT + body, packages)
        return tuple(value for i, node in enumerate(tree.nodes)
                     if node.kind in ('FuncCall', 'MathCall') and (value := applied_marker(r, i)) is not None)

    def test_exact_fixed_artifact_capture_uses_safe_basenames_and_no_discovery(self):
        reads = []
        original = loader._Snapshot.read
        def read(snapshot, parent, name, limit):
            self.assertNotIn('/', name)
            reads.append(parent.path / name)
            return original(snapshot, parent, name, limit)
        with patch.object(loader._Snapshot, 'read', read), \
             patch.object(Path, 'home', side_effect=AssertionError('no home/cache discovery')), \
             patch('os.walk', side_effect=AssertionError('no graph discovery')), \
             patch('os.scandir', side_effect=AssertionError('no graph discovery')):
            loaded = self.load()
        self.assertEqual(set(reads), {self.scope / 'typst.toml', self.scope / 'lib.typ',
                                     self.mannot / 'typst.toml', self.mannot / 'src/lib.typ',
                                     *(self.mannot / relative for relative, _ in MANNOT_ARTIFACTS)})
        package = next(p for p in loaded.sources if p.import_path == MANNOT_NAMESPACE)
        self.assertTrue(has_mannot_namespace(loaded.sources))
        self.assertEqual({a.relative_path: digest(a.source) for a in package.artifacts}, dict(MANNOT_ARTIFACTS))
        for artifact in package.artifacts:
            self.assertEqual(artifact.source_identity, str(self.mannot / artifact.relative_path))
            with self.assertRaises(FrozenInstanceError):
                artifact.source = b'changed'
        evidence = next(e for e in loaded.evidence if e.import_path == MANNOT_NAMESPACE)
        self.assertIsInstance(evidence.artifacts, tuple)
        self.assertEqual({a.relative_path: a.source_sha256 for a in evidence.artifacts}, dict(MANNOT_ARTIFACTS))
        for artifact in evidence.artifacts:
            with self.assertRaises(FrozenInstanceError):
                artifact.source_sha256 = 'changed'
        self.assertTrue(all(self.capabilities(loaded.sources).values()))
        _, r = self.resolver(packages=loaded.sources)
        for name in ('mark', 'annot', 'annot-cetz', 'core-annot'):
            binding = r.package_binding(MANNOT_NAMESPACE, name)
            relative = 'src/mark.typ' if name == 'mark' else 'src/annot.typ'
            self.assertEqual(binding.source_identity, str(self.mannot / relative))
            self.assertEqual(binding.source_sha256, dict(MANNOT_ARTIFACTS)[relative])

    def test_missing_each_definition_withholds_only_its_dependent_capabilities(self):
        for filename in ('mark.typ', 'annot.typ', 'util.typ'):
            target = self.mannot / 'src' / filename
            moved = self.base / 'missing-definition'
            target.rename(moved)
            try:
                with self.subTest(filename=filename):
                    loaded = self.load()
                    self.assertTrue(has_mannot_namespace(loaded.sources))
                    actual = self.capabilities(loaded.sources)
                    self.assertEqual(actual['mark'], filename not in ('mark.typ', 'util.typ'))
                    self.assertEqual({actual[n] for n in ('annot', 'annot-cetz', 'core-annot')},
                                     {filename not in ('annot.typ', 'util.typ')})
                    applications = self.marker_applications(
                        b'#mannot-scope(s => [#let term = (s.mark)("x")\n$ term(x) $])', loaded.sources)
                    self.assertEqual(bool(applications), actual['mark'])
            finally:
                moved.rename(target)

    def test_changed_each_definition_cannot_borrow_its_old_hash_or_neighbor_identity(self):
        for filename in ('mark.typ', 'annot.typ', 'util.typ'):
            target = self.mannot / 'src' / filename
            original = target.read_bytes()
            target.write_bytes(original + b'\n// changed exact definition\n')
            try:
                with self.subTest(filename=filename):
                    loaded = self.load()
                    actual = self.capabilities(loaded.sources)
                    self.assertEqual(actual['mark'], filename not in ('mark.typ', 'util.typ'))
                    self.assertEqual(actual['core-annot'], filename not in ('annot.typ', 'util.typ'))
                    namespace = next(p for p in loaded.sources if p.import_path == MANNOT_NAMESPACE)
                    changed = next(a for a in namespace.artifacts if a.relative_path == 'src/' + filename)
                    self.assertNotEqual(digest(changed.source), dict(MANNOT_ARTIFACTS)[changed.relative_path])
                    _, r = self.resolver(packages=loaded.sources)
                    self.assertTrue(any('Unreviewed supplemental artifact' in reason for reason in r.skips))
            finally:
                target.write_bytes(original)

    def test_namespace_only_old_fixture_is_still_not_a_raw_api_or_factory_proof(self):
        shutil.rmtree(self.mannot)
        shutil.copytree(OLD_FIXTURE, self.mannot)
        loaded = self.load()
        self.assertTrue(has_mannot_namespace(loaded.sources))
        self.assertFalse(any(self.capabilities(loaded.sources).values()))
        self.assertFalse(self.marker_applications(
            b'#mannot-scope(s => [#let term = (s.mark)("x")\n$ term(x) $])', loaded.sources))
        tree = parse(IMPORT + b'#mannot-scope(s => [$ mark(x, tag: #<public>) '
                     b'+ y #(s.tag)("x") #(s.annot)("x", c, ()) $])')
        result = analyze(tree, 'main.typ', packages=loaded.sources)
        self.assertFalse(result[TAGS].diagnostics or result[BOUNDS].diagnostics)

    def test_unchanged_namespace_with_missing_util_never_certifies_mark_factory(self):
        (self.mannot / 'src/util.typ').unlink()
        loaded = self.load()
        package = next(p for p in loaded.sources if p.import_path == MANNOT_NAMESPACE)
        self.assertEqual({a.relative_path for a in package.artifacts}, {'src/mark.typ', 'src/annot.typ'})
        self.assertFalse(any(self.capabilities(loaded.sources).values()))

    def test_mutable_artifact_containers_bytes_and_invalid_records_are_rejected(self):
        packages = self.load().sources
        package = next(p for p in packages if p.import_path == MANNOT_NAMESPACE)
        artifact = package.artifacts[0]
        variants = (
            replace(package, artifacts=list(package.artifacts)),
            replace(package, artifacts=(replace(artifact, source=bytearray(artifact.source)),)),
            replace(package, artifacts=(replace(artifact, source_identity=''),)),
            replace(package, artifacts=({'relative_path': artifact.relative_path, 'source': artifact.source},)),
        )
        for invalid in variants:
            with self.subTest(invalid=type(invalid.artifacts)), self.assertRaises(InputError):
                self.resolver(packages=tuple(invalid if p is package else p for p in packages))

    def test_duplicate_artifact_paths_are_rejected_even_with_equal_actual_bytes(self):
        packages = self.load().sources
        package = next(p for p in packages if p.import_path == MANNOT_NAMESPACE)
        invalid = replace(package, artifacts=(*package.artifacts, package.artifacts[0]))
        with self.assertRaisesRegex(InputError, 'Duplicate resolver artifact path'):
            self.resolver(packages=tuple(invalid if p is package else p for p in packages))

    def test_unknown_relative_artifact_paths_cannot_relabel_an_actual_definition(self):
        packages = self.load().sources
        package = next(p for p in packages if p.import_path == MANNOT_NAMESPACE)
        mark = next(a for a in package.artifacts if a.relative_path == 'src/mark.typ')
        for relative in ('src/../mark.typ', '/src/mark.typ', 'mark.typ', 'src/mark.typ/'):
            invalid = replace(package, artifacts=tuple(
                replace(a, relative_path=relative) if a is mark else a for a in package.artifacts))
            sources = tuple(invalid if p is package else p for p in packages)
            self.assertFalse(self.capabilities(sources)['mark'])
            self.assertTrue(self.capabilities(sources)['core-annot'])

    def test_package_namespace_change_prevents_supplemental_artifacts_from_becoming_bindings(self):
        packages = self.load().sources
        package = next(p for p in packages if p.import_path == MANNOT_NAMESPACE)
        changed = replace(package, source=package.source + b'\n')
        sources = tuple(changed if p is package else p for p in packages)
        self.assertFalse(has_mannot_namespace(sources))
        self.assertFalse(any(self.capabilities(sources).values()))

    def test_factory_aliases_are_real_and_dot_calls_overrides_shadowing_stay_unproven(self):
        good = (
            b'#mannot-scope(s => [#let term = (s.mark)("x")\n$ term(x) $])',
            b'#mannot-scope(s => { let factory = s.mark; let term = factory("x"); '
            b'let actual = term; [$ actual(x) $] })',
            b'#mannot-scope(s => [$ #(s.mark)("x")($x$) $])',
        )
        for body in good:
            self.assertEqual(len(self.marker_applications(body)), 1, body)
        bad = (
            b'#mannot-scope(s => [#let term = s.mark("x")\n$ term(x) $])',
            b'#mannot-scope(s => [$ #s.mark("x")($x$) $])',
            b'#mannot-scope(s => [#let term = (s.mark)("x", tag: <other>)\n$ term(x) $])',
            b'#mannot-scope(s => [#let term = (s.mark)("x")\n$ term(x, tag: #<other>) $])',
            b'#mannot-scope(s => [#let term = (s.mark)("x")\n#let term = x => x\n$ term(x) $])',
            b'#mannot-scope(s => { let s = (:); let term = (s.mark)("x"); [$ term(x) $] })',
            b'#mannot-scope(s => { let delayed = () => [$ #(s.mark)("x")($x$) $]; [] })',
        )
        for body in bad:
            self.assertFalse(self.marker_applications(body), body)

    def test_raw_function_alias_retains_definition_but_shadowed_names_do_not(self):
        body = (b'#let actual = mark\n#let mark = (..args) => []\n'
                b'#mannot-scope(s => [$ actual(x, tag: #<public>) + mark(x, tag: #<other>) $])')
        tree, r = self.resolver(IMPORT + body)
        calls = [r.facts[i].binding for i, n in enumerate(tree.nodes)
                 if n.kind in ('FuncCall', 'MathCall') and tree.source[n.start:n.end].startswith(b'actual(')]
        self.assertEqual(len(calls), 1)
        self.assertTrue(is_mannot_binding(calls[0], 'mark'))
        result = analyze(tree, 'main.typ', packages=self.load().sources)
        self.assertEqual(len(result[TAGS].diagnostics), 1)

    def test_supplemental_symlinks_and_hardlinks_are_not_optional_absence(self):
        for filename in ('mark.typ', 'annot.typ', 'util.typ'):
            target = self.mannot / 'src' / filename
            original = target.read_bytes()
            for kind in ('symlink', 'hardlink'):
                with self.subTest(filename=filename, kind=kind):
                    external = self.base / 'external-definition'
                    external.write_bytes(original)
                    target.unlink()
                    if kind == 'symlink':
                        target.symlink_to(external)
                    else:
                        os.link(external, target)
                    try:
                        with self.assertRaises(loader.PackageInputError):
                            self.load()
                    finally:
                        target.unlink()
                        external.unlink()
                        target.write_bytes(original)

    def test_supplemental_nonregular_files_and_invalid_encoding_fail_closed(self):
        target = self.mannot / 'src/mark.typ'
        original = target.read_bytes()
        for kind in ('directory', 'fifo', 'invalid-utf8'):
            with self.subTest(kind=kind):
                target.unlink()
                if kind == 'directory':
                    target.mkdir()
                elif kind == 'fifo':
                    os.mkfifo(target)
                else:
                    target.write_bytes(b'\xff')
                try:
                    with self.assertRaises(loader.PackageInputError):
                        self.load()
                finally:
                    target.rmdir() if target.is_dir() else target.unlink()
                    target.write_bytes(original)

    def test_disappearance_after_supplemental_existence_check_is_a_race(self):
        original_stat = os.stat
        removed = False
        target = self.mannot / 'src/mark.typ'
        def disappear(path, *args, **kwargs):
            nonlocal removed
            result = original_stat(path, *args, **kwargs)
            if path == 'mark.typ' and kwargs.get('dir_fd') is not None and not removed:
                removed = True
                target.unlink()
            return result
        with patch('os.stat', disappear), self.assertRaises(loader.PackageInputError):
            self.load()
        self.assertTrue(removed)

    def test_supplemental_content_change_after_capture_is_detected(self):
        original_read = loader._Snapshot.read
        changed = False
        def tamper(snapshot, parent, name, limit):
            nonlocal changed
            source = original_read(snapshot, parent, name, limit)
            if parent.path == self.mannot / 'src' and name == 'mark.typ':
                changed = True
                (parent.path / name).write_bytes(source.replace(b'core-mark', b'evil-mark'))
            return source
        with patch.object(loader._Snapshot, 'read', tamper), \
             self.assertRaisesRegex(loader.PackageInputError, 'changed while reading'):
            self.load()
        self.assertTrue(changed)

    def test_supplemental_directory_disappearance_after_entrypoint_capture_is_a_race(self):
        original_directory = loader._Snapshot.directory
        entered_src = 0
        def disappear(snapshot, parent, name):
            nonlocal entered_src
            if parent.path == self.mannot and name == 'src':
                entered_src += 1
                if entered_src == 2:
                    (parent.path / name).rename(self.base / 'removed-src')
            return original_directory(snapshot, parent, name)
        with patch.object(loader._Snapshot, 'directory', disappear), self.assertRaises(loader.PackageInputError):
            self.load()
        self.assertEqual(entered_src, 2)

    def test_permission_failure_on_supplemental_probe_is_not_silently_missing(self):
        original_stat = os.stat
        def deny(path, *args, **kwargs):
            if path == 'mark.typ' and kwargs.get('dir_fd') is not None:
                raise PermissionError('supplemental fixture denied')
            return original_stat(path, *args, **kwargs)
        with patch('os.stat', deny), self.assertRaisesRegex(loader.PackageInputError, 'supplemental fixture denied'):
            self.load()


if __name__ == '__main__':
    unittest.main()
