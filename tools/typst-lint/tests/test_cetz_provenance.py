"""Authentic CeTZ capability boundary through real ASTs and explicit captures.

No constructed Binding/Facts or caller-attested semantic flags are used. Every
capability is produced by the real resolver from the loader's immutable copy of
the genuine selected package artifacts.
"""
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import package_loader as loader
from package_artifacts import (CETZ, CETZ_ARTIFACTS, CETZ_MANIFEST_SHA256,
                               CETZ_SOURCE_SHA256)
from scoped_provenance import resolve as scoped_resolve
from static_resolver import (imported_paths, is_cetz_line_binding,
                             is_cetz_module_binding, resolve)
from syntax_client import parse

FIXTURE = TOOL / 'tests/fixtures/anchor-overlay-packages/preview/cetz/0.4.2'
IMPORT = b'#import "@preview/cetz:0.4.2"\n'
SOURCE = IMPORT + b'#cetz.draw.line("x.north", "x.south")\n'
TRIVIA = frozenset(('Space', 'Parbreak', 'LineComment', 'BlockComment'))


def raw(tree, i):
    node = tree.nodes[i]
    return tree.source[node.start:node.end]


def significant(tree, i):
    return [child for child in tree.nodes[i].children if tree.nodes[child].kind not in TRIVIA]


def top_level_definitions(tree):
    """Names of actual top-level lets, excluding example text and nested lets."""
    definitions = {}
    for i in tree.nodes[0].children:
        if tree.nodes[i].kind != 'LetBinding':
            continue
        parts = significant(tree, i)
        if len(parts) < 2:
            continue
        value = parts[1]
        if tree.nodes[value].kind == 'Closure':
            closure = significant(tree, value)
            value = closure[0] if closure else value
        if tree.nodes[value].kind == 'Ident':
            definitions[raw(tree, value).decode()] = i
    return definitions


class CetzProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / 'packages'
        self.package = self.root / 'preview/cetz/0.4.2'
        shutil.copytree(FIXTURE, self.package)

    def capture(self, source=SOURCE):
        return loader.load_packages(self.root, imported_paths(parse(source)))

    def resolved_calls(self, source, *, packages=None, scoped=False):
        tree = parse(source)
        self.assertFalse(tree.nodes[0].errors, source)
        packages = self.capture(source).sources if packages is None else packages
        resolution = scoped_resolve(tree, packages) if scoped else resolve(tree, packages=packages)
        calls = [(raw(tree, i), resolution.facts[i].binding)
                 for i, node in enumerate(tree.nodes) if node.kind in ('FuncCall', 'MathCall')]
        return tree, resolution, calls

    def assert_line_calls(self, source, expected, *, packages=None, scoped=False):
        tree, resolution, calls = self.resolved_calls(source, packages=packages, scoped=scoped)
        actual = [callee for callee, binding in calls if is_cetz_line_binding(binding)]
        self.assertEqual(actual, expected, (source, resolution.skips))
        return tree, resolution, calls

    def test_exact_registry_hashes_and_single_selected_snapshot(self):
        self.assertEqual(hashlib.sha256((FIXTURE / 'typst.toml').read_bytes()).hexdigest(), CETZ_MANIFEST_SHA256)
        self.assertEqual(hashlib.sha256((FIXTURE / 'src/lib.typ').read_bytes()).hexdigest(), CETZ_SOURCE_SHA256)
        self.assertEqual(len(CETZ_ARTIFACTS), 7)
        for relative, digest in CETZ_ARTIFACTS:
            self.assertEqual(hashlib.sha256((FIXTURE / relative).read_bytes()).hexdigest(), digest, relative)
        reads = []
        original = loader._Snapshot.read
        def read(snapshot, directory, name, limit):
            self.assertNotIn('/', name)
            reads.append(directory.path / name)
            return original(snapshot, directory, name, limit)
        with patch.object(loader._Snapshot, 'read', read), \
             patch.object(Path, 'home', side_effect=AssertionError('No ambient cache discovery')), \
             patch('os.walk', side_effect=AssertionError('No dependency traversal')), \
             patch('os.scandir', side_effect=AssertionError('No recursive discovery')):
            captured = self.capture()
        expected = {self.package / 'typst.toml', self.package / 'src/lib.typ'}
        expected.update(self.package / relative for relative, digest in CETZ_ARTIFACTS)
        self.assertEqual(set(reads), expected)
        self.assertEqual(len(reads), len(expected))
        self.assertFalse(captured.unavailable)
        self.assertEqual(len(captured.sources), 1)
        source, evidence = captured.sources[0], captured.evidence[0]
        self.assertEqual(source.import_path, CETZ)
        self.assertEqual({a.relative_path for a in source.artifacts}, dict(CETZ_ARTIFACTS).keys())
        self.assertEqual({a.relative_path: a.source_sha256 for a in evidence.artifacts}, dict(CETZ_ARTIFACTS))
        self.assert_line_calls(SOURCE, [b'cetz.draw.line("x.north", "x.south")'], packages=captured.sources)
        # The capture does not become live filesystem authority after loading.
        (self.package / 'src/draw/shapes.typ').write_bytes(b'changed after capture')
        self.assert_line_calls(SOURCE, [b'cetz.draw.line("x.north", "x.south")'], packages=captured.sources)

    def test_every_missing_supplement_withholds_all_cetz_capability(self):
        for relative, digest in CETZ_ARTIFACTS:
            with self.subTest(relative=relative):
                path = self.package / relative
                original = path.read_bytes()
                path.unlink()
                try:
                    captured = self.capture()
                    self.assertEqual(captured.sources[0].source, (FIXTURE / 'src/lib.typ').read_bytes())
                    self.assertNotIn(relative, {a.relative_path for a in captured.sources[0].artifacts})
                    tree, result, calls = self.assert_line_calls(SOURCE, [], packages=captured.sources)
                    self.assertFalse(any(is_cetz_module_binding(f.binding) for f in result.facts))
                    self.assertTrue(any('verified direct-package contract' in s for s in result.skips))
                finally:
                    path.write_bytes(original)

    def test_every_changed_supplement_is_captured_but_never_certified(self):
        for relative, digest in CETZ_ARTIFACTS:
            with self.subTest(relative=relative):
                path = self.package / relative
                original = path.read_bytes()
                path.write_bytes(original + b'\n// changed artifact\n')
                try:
                    captured = self.capture()
                    artifact = next(a for a in captured.sources[0].artifacts if a.relative_path == relative)
                    self.assertNotEqual(hashlib.sha256(artifact.source).hexdigest(), digest)
                    tree, result, calls = self.assert_line_calls(SOURCE, [], packages=captured.sources)
                    self.assertFalse(any(is_cetz_module_binding(f.binding) for f in result.facts))
                    self.assertIn(f'Unreviewed supplemental artifact: {CETZ}/{relative}', result.skips)
                finally:
                    path.write_bytes(original)

    def test_namespace_only_source_does_not_certify_module_draw_or_line(self):
        for relative, digest in CETZ_ARTIFACTS:
            (self.package / relative).unlink()
        captured = self.capture()
        self.assertEqual(captured.sources[0].artifacts, ())
        self.assertEqual(hashlib.sha256(captured.sources[0].source).hexdigest(), CETZ_SOURCE_SHA256)
        tree, result, calls = self.assert_line_calls(SOURCE, [], packages=captured.sources)
        self.assertFalse(any(f.binding and f.binding.package == CETZ for f in result.facts))
        for source in (b'#import "@preview/cetz:0.4.2": draw\n#draw.line((0,0),(1,1))',
                       IMPORT + b'#import cetz.draw: line\n#line((0,0),(1,1))'):
            self.assert_line_calls(source, [], packages=captured.sources)

    def test_changed_entrypoint_or_manifest_withholds_supplements_as_well(self):
        for relative, code in (('src/lib.typ', 'unreviewed-package-source'),
                               ('typst.toml', 'unreviewed-package-manifest')):
            with self.subTest(relative=relative):
                path = self.package / relative
                original = path.read_bytes()
                path.write_bytes(original + b'\n# changed bytes\n')
                try:
                    captured = self.capture()
                    self.assertFalse(captured.sources)
                    self.assertTrue(any(u.code == code for u in captured.unavailable))
                    self.assert_line_calls(SOURCE, [], packages=captured.sources)
                finally:
                    path.write_bytes(original)

    def test_named_draw_import_and_immutable_function_alias_use_actual_definition(self):
        source = (IMPORT + b'#import cetz.draw: line as wire\n'
                  b'#let saved = wire\n#wire((0,0),(1,1))\n#saved((1,1),(2,2))')
        tree, result, calls = self.assert_line_calls(source, [b'wire((0,0),(1,1))', b'saved((1,1),(2,2))'])
        for call, binding in calls:
            if is_cetz_line_binding(binding):
                self.assertEqual(binding.source_identity, str(self.package / 'src/draw/shapes.typ'))
                self.assertEqual(binding.source_sha256, dict(CETZ_ARTIFACTS)['src/draw/shapes.typ'])
        self.assert_line_calls(b'#import "@preview/cetz:0.4.2": draw as drawing\n'
                               b'#import drawing: line\n#line((0,0),(1,1))', [b'line((0,0),(1,1))'])

    def test_unaliased_identifier_import_keeps_actual_source_identifier(self):
        source = (b'#import "@preview/cetz:0.4.2" as actual\n#import actual\n'
                  b'#actual.draw.line((0,0),(1,1))\n#cetz.draw.line((2,2),(3,3))')
        self.assert_line_calls(source, [b'actual.draw.line((0,0),(1,1))'])
        source = (b'#import "@preview/cetz:0.4.2": draw as actual\n#import actual\n'
                  b'#actual.line((0,0),(1,1))\n#draw.line((2,2),(3,3))')
        self.assert_line_calls(source, [b'actual.line((0,0),(1,1))'])
        self.assert_line_calls(IMPORT + b'#let chosen = cetz.draw\n#import chosen as paint\n'
                               b'#paint.line((0,0),(1,1))', [b'paint.line((0,0),(1,1))'])

    def test_root_cetz_line_is_never_a_draw_line_export(self):
        source = (IMPORT + b'#cetz.line((0,0),(1,1))\n'
                  b'#import "@preview/cetz:0.4.2": line\n#line((2,2),(3,3))\n'
                  b'#cetz.draw.line((4,4),(5,5))')
        self.assert_line_calls(source, [b'cetz.draw.line((4,4),(5,5))'])

    def test_partial_wildcards_mask_unrelated_names_and_only_restore_reviewed_exports(self):
        root = (IMPORT + b'#let old = cetz.draw.line\n#let prose = text\n'
                b'#import "@preview/cetz:0.4.2": *\n'
                b'#draw.line((0,0),(1,1))\n#old((2,2),(3,3))\n#text("x")\n#prose("x")\n')
        tree, result, calls = self.assert_line_calls(root, [b'draw.line((0,0),(1,1))'])
        self.assertTrue(all(binding is None for call, binding in calls if call != b'draw.line((0,0),(1,1))'))
        self.assertTrue(any('partial export namespace' in reason for reason in result.skips))
        drawing = (IMPORT + b'#let old = cetz.draw.line\n#let prose = text\n'
                   b'#import cetz.draw: *\n#line((0,0),(1,1))\n#old((2,2),(3,3))\n'
                   b'#text("x")\n#prose("x")\n#cetz.draw.line((4,4),(5,5))')
        tree, result, calls = self.assert_line_calls(drawing, [b'line((0,0),(1,1))'])
        self.assertTrue(all(binding is None for call, binding in calls if call != b'line((0,0),(1,1))'))
        self.assertTrue(any('partial export namespace' in reason for reason in result.skips))
        self.assert_line_calls(IMPORT + b'#import cetz: *\n#draw.line((0,0),(1,1))\n#line((2,2),(3,3))',
                               [b'draw.line((0,0),(1,1))'])

    def test_dynamic_shadowed_deferred_and_custom_apis_do_not_acquire_capability(self):
        cases = (
            b'#let package = "@preview/cetz:0.4.2"\n#import package as cetz\n#cetz.draw.line((0,0),(1,1))',
            IMPORT + b'#let cetz = (draw: (line: (a,b) => ()))\n#cetz.draw.line((0,0),(1,1))',
            IMPORT + b'#import cetz.draw: line\n#let line = (a,b) => ()\n#line((0,0),(1,1))',
            IMPORT + b'#let delayed = () => cetz.draw.line((0,0),(1,1))',
            IMPORT + b'#context cetz.draw.line((0,0),(1,1))',
            IMPORT + b'#let unknown = x => x\n#let chosen = unknown(cetz.draw)\n#chosen.line((0,0),(1,1))',
        )
        for source in cases:
            with self.subTest(source=source):
                self.assert_line_calls(source, [])
        self.assert_line_calls(IMPORT + b'#let saved = cetz.draw.line\n#let cetz = (: )\n#saved((0,0),(1,1))',
                               [b'saved((0,0),(1,1))'])

    def test_mutated_receiver_loses_capability_and_scoped_backend_withholds_all_proofs(self):
        for target in (b'cetz', b'cetz.draw', b'cetz.draw.line'):
            source = IMPORT + b'#{ ' + target + b' = (:); cetz.draw.line((0,0),(1,1)) }'
            with self.subTest(target=target):
                tree, result, calls = self.assert_line_calls(source, [])
                self.assertTrue(any('Assignment invalidates' in s for s in result.skips))
                tree, result, calls = self.assert_line_calls(source, [], scoped=True)
                self.assertTrue(any('mutation' in s for s in result.skips))
        # Typst values are immutable captures. Invalidating a receiver does not
        # rewrite a previously captured function, but the scoped backend still
        # refuses execution-order claims anywhere in a mutation-bearing tree.
        source = IMPORT + b'#{ let saved = cetz.draw.line; cetz = (:); saved((0,0),(1,1)) }'
        self.assert_line_calls(source, [b'saved((0,0),(1,1))'])
        self.assert_line_calls(source, [], scoped=True)

    def test_supplemental_nested_symlinks_and_capture_races_fail_closed(self):
        target = self.package / 'src/draw/shapes.typ'
        outside = self.base / 'original-shapes.typ'
        target.rename(outside)
        target.symlink_to(outside)
        try:
            with self.assertRaises(loader.PackageInputError):
                self.capture()
        finally:
            target.unlink()
            outside.rename(target)
        directory = self.package / 'src/draw'
        outside_dir = self.base / 'original-draw'
        directory.rename(outside_dir)
        directory.symlink_to(outside_dir, target_is_directory=True)
        try:
            with self.assertRaises(loader.PackageInputError):
                self.capture()
        finally:
            directory.unlink()
            outside_dir.rename(directory)
        original_read = loader._Snapshot.read
        original_bytes = target.read_bytes()
        changed = False
        def raced(snapshot, directory, name, limit):
            nonlocal changed
            data = original_read(snapshot, directory, name, limit)
            if directory.path / name == target and not changed:
                target.write_bytes(original_bytes + b'\n// raced after capture\n')
                changed = True
            return data
        try:
            with patch.object(loader._Snapshot, 'read', raced), self.assertRaises(loader.PackageInputError):
                self.capture()
            self.assertTrue(changed)
        finally:
            target.write_bytes(original_bytes)

    def test_actual_namespace_chain_and_coordinate_role_are_in_pinned_source_ast(self):
        trees = {relative: parse((FIXTURE / relative).read_bytes())
                 for relative in ('src/lib.typ', *(path for path, digest in CETZ_ARTIFACTS))}
        for relative, tree in trees.items():
            self.assertFalse(tree.nodes[0].errors, relative)
        root, drawing, shapes = (trees[path] for path in ('src/lib.typ', 'src/draw.typ', 'src/draw/shapes.typ'))
        root_imports = [i for i in root.nodes[0].children if root.nodes[i].kind == 'ModuleImport']
        self.assertTrue(any(raw(root, i) == b'import "draw.typ"' for i in root_imports))
        self.assertTrue(all(not any(root.nodes[c].kind == 'Star' for c in significant(root, i)) for i in root_imports))
        shapes_import = next(i for i in drawing.nodes[0].children
                             if drawing.nodes[i].kind == 'ModuleImport' and b'"draw/shapes.typ"' in raw(drawing, i))
        self.assertIn(b'line', [raw(drawing, i) for i, n in enumerate(drawing.nodes)
                                if n.kind == 'Ident' and drawing.nodes[shapes_import].start <= n.start < drawing.nodes[shapes_import].end])
        wildcard_imports = [i for i in shapes.nodes[0].children if shapes.nodes[i].kind == 'ModuleImport'
                            and any(shapes.nodes[c].kind == 'Star' for c in significant(shapes, i))]
        self.assertEqual({raw(shapes, i) for i in wildcard_imports},
                         {b'import "transformations.typ": *', b'import "styling.typ": *', b'import "grouping.typ": *'})
        line = top_level_definitions(shapes)['line']
        self.assertTrue(all(shapes.nodes[i].end < shapes.nodes[line].start for i in wildcard_imports))
        for relative in ('src/draw/transformations.typ', 'src/draw/styling.typ', 'src/draw/grouping.typ'):
            tree = trees[relative]
            self.assertNotIn('line', top_level_definitions(tree))
            imports = [i for i in tree.nodes[0].children if tree.nodes[i].kind == 'ModuleImport']
            self.assertTrue(all(not any(tree.nodes[c].kind == 'Star' for c in significant(tree, i)) for i in imports))
            self.assertTrue(all(b': line' not in raw(tree, i) for i in imports))
            # The wildcard modules that re-export a coordinate module all name
            # the exact same package-root file, not a different implementation.
            coordinate_imports = [raw(tree, i) for i in imports if b'coordinate.typ' in raw(tree, i)]
            if coordinate_imports:
                self.assertEqual(coordinate_imports, [b'import "/src/coordinate.typ"'])
        start, end = shapes.nodes[line].start, shapes.nodes[line].end
        actual_calls = [raw(shapes, i) for i, n in enumerate(shapes.nodes)
                        if n.kind == 'FuncCall' and start <= n.start < end]
        self.assertIn(b'pts-style.pos()', actual_calls)
        self.assertIn(b'pts-style.named()', actual_calls)
        self.assertTrue(any(call.startswith(b'coordinate.resolve(ctx, ..pts)') for call in actual_calls))


if __name__ == '__main__':
    unittest.main()
