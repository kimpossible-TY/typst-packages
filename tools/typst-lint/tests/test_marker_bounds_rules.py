"""Real AST and authentic package evidence for diagnostic-only marker rules."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
from marker_bounds_rules import (analyze, applied_marker, scoped_targets,
                                 display_proof, TAGS, BOUNDS, RULE_IDS)
from package_loader import load_packages
from scoped_provenance import resolve
from static_resolver import imported_paths
from syntax_client import EXECUTABLE, parse

FIXTURE = TOOL / 'tests/fixtures/marker-bounds-packages'
IMPORT = (b'#import "@local/scoped-annotations:0.3.0": '
          b'mannot-scope, local-scope-annotations, local-tag-scope, annot-cetz-local\n'
          b'#import "@preview/mannot:0.4.0": mark, annot, annot-cetz\n')
PLAIN = b'#mannot-scope(s => [$ x #(s.tag)("x") #(s.annot)("x", c, ()) $])'


def materialize_packages(root):
    shutil.copytree(REPO / 'local/scoped-annotations/0.3.0', root / 'local/scoped-annotations/0.3.0')
    shutil.copytree(FIXTURE / 'preview/mannot/0.4.0', root / 'preview/mannot/0.4.0')


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class MarkerBoundsRulesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='typst-marker-bounds-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.package_root = self.base / 'packages'
        materialize_packages(self.package_root)

    def evaluate(self, body, *, prefix=IMPORT, packages=None):
        tree = parse(prefix + body)
        self.assertFalse(tree.nodes[0].errors, prefix + body)
        if packages is None:
            packages = load_packages(self.package_root, imported_paths(tree)).sources
        return analyze(tree, 'main.typ', packages=packages), tree, packages

    def test_public_artifact_hashes_and_original_license_are_exact(self):
        expected = {
            'typst.toml': ('aef4310b36955dc98d8b03ec4abf17d45777d0a08b65d6e67214752e57647545', '746072d9feb0d3a1258c0c3113546bd06d683933'),
            'src/lib.typ': ('288027cea059b327bf3253c44ae975339e7afc0f149375d9d87ca1c1fe739f57', '525541bb6b2ecea89769a56444b1b1b29d0552d2'),
            'src/mark.typ': ('86f7653d76aa4574f811c8b63a31f79b0a90c4989e24ac1277857334b6d35882', 'cc4aaf702b40a128211a2e7a6fe01eecc8144307'),
            'src/annot.typ': ('12ea7288aeb7378bb6cdf8747d0d94fb3828def856521540a881edf25f4d42bd', '798f42d05b42116a12007a652110d48f5ebaf2ff'),
            'src/util.typ': ('b88ea6e8d207c611925822d9d6ae7cb83bcf0486ba68af57807a9e4539b98583', '2b90e6934f4bbf847af9c0652ca3845dc587d8d8'),
            'LICENSE': ('5aba3493920d5fb3d7b095f3e0c4a0311cf1feb24274789dbe16c5182febf1c9', '72330967fa06c3ad7d2544982feddfad8a8ab287'),
        }
        for path, (sha256, blob) in expected.items():
            raw = (FIXTURE / 'preview/mannot/0.4.0' / path).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), sha256)
            self.assertEqual(hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest(), blob)

    def test_manual_raw_mannot_label_in_actual_annotation_scope(self):
        for constructor in (b'mannot-scope', b'local-scope-annotations'):
            result, _, _ = self.evaluate(b'#' + constructor + b'(actual => [$ mark(x, tag: #<x>) $])')
            self.assertEqual(len(result[TAGS].diagnostics), 1, result[TAGS].skips)
            self.assertEqual(result[TAGS].diagnostics[0].grade, 'warning')
            self.assertFalse(result[TAGS].edits)

    def test_raw_alias_module_member_and_scoped_tag_are_not_name_guesses(self):
        prefix = (b'#import "@local/scoped-annotations:0.3.0": mannot-scope as scope\n'
                  b'#import "@preview/mannot:0.4.0" as raw\n#let marker = raw.mark\n')
        result, _, _ = self.evaluate(b'#scope(t => [$ marker(x, tag: #<x>) $])', prefix=prefix)
        self.assertEqual(len(result[TAGS].diagnostics), 1)
        result, _, _ = self.evaluate(b'#mannot-scope(s => [$ mark(x, tag: #(s.tag)("x")) $])')
        self.assertFalse(result[TAGS].diagnostics)
        result, _, _ = self.evaluate(b'$ mark(x, tag: #<global>) $\nOrdinary label. <semantic>')
        self.assertFalse(result[TAGS].diagnostics)

    def test_nearest_preceding_plain_scoped_tag_has_no_bounds(self):
        result, _, _ = self.evaluate(PLAIN)
        self.assertEqual(len(result[BOUNDS].diagnostics), 1, result[BOUNDS].skips)
        self.assertEqual(result[BOUNDS].diagnostics[0].grade, 'error')
        self.assertIn('"x"', result[BOUNDS].diagnostics[0].explanation)
        self.assertFalse(result[BOUNDS].edits)

    def test_generated_label_targets_of_local_and_actual_raw_overlays(self):
        for overlay in (b'annot-cetz-local((s.tag)("x"), c, ())',
                        b'annot-cetz((s.tag)("x"), c, ())',
                        b'annot((s.tag)("x"), [Annotation])'):
            body = b'#mannot-scope(s => [$ x #(s.tag)("x") #' + overlay + b' $])'
            result, _, _ = self.evaluate(body)
            self.assertEqual(len(result[BOUNDS].diagnostics), 1, result[BOUNDS].skips)

    def test_wrapper_only_import_loads_fixed_optional_capabilities_without_cache_discovery(self):
        prefix = b'#import "@local/scoped-annotations:0.3.0": mannot-scope\n'
        result, _, _ = self.evaluate(PLAIN, prefix=prefix)
        self.assertEqual(len(result[BOUNDS].diagnostics), 1, result[BOUNDS].skips)

    def test_raw_marker_and_applied_scope_marker_supply_bounds(self):
        cases = (
            b'#mannot-scope(s => [$ mark(x, tag: #(s.tag)("x")) #(s.annot)("x", c, ()) $])',
            b'#mannot-scope(s => [#let term = (s.mark)("x")\n$ term(x) #(s.annot)("x", c, ()) $])',
            b'#mannot-scope(s => [$ #(s.mark)("x")($x$) #(s.annot)("x", c, ()) $])',
            b'#mannot-scope(s => { let term = (s.mark)("x"); let actual = term; '
            b'[$ actual(x) #(s.annot)("x", c, ()) $] })',
        )
        for body in cases:
            with self.subTest(body=body):
                result, tree, packages = self.evaluate(body)
                r = resolve(tree, packages)
                markers = [applied_marker(r, i) for i, node in enumerate(tree.nodes)
                           if node.kind in ('FuncCall', 'MathCall') and applied_marker(r, i) is not None]
                self.assertEqual(len(markers), 1, result[BOUNDS].skips)
                self.assertFalse(result[BOUNDS].diagnostics)
                displays = [i for i, node in enumerate(tree.nodes) if node.kind == 'Equation' and node.display]
                self.assertTrue(display_proof(r, displays[0]).complete, display_proof(r, displays[0]).reasons)

    def test_later_plain_label_overrides_older_marker_for_the_same_target(self):
        body = (b'#mannot-scope(s => [$ mark(x, tag: #(s.tag)("x")) + '
                b'y #(s.tag)("x") #(s.annot)("x", c, ()) $])')
        result, _, _ = self.evaluate(body)
        self.assertEqual(len(result[BOUNDS].diagnostics), 1, result[BOUNDS].skips)
        body = (b'#mannot-scope(s => [$ y #(s.tag)("x") + '
                b'mark(x, tag: #(s.tag)("x")) #(s.annot)("x", c, ()) $])')
        self.assertFalse(self.evaluate(body)[0][BOUNDS].diagnostics)

    def test_applied_marker_in_another_display_or_unapplied_factory_does_not_supply_bounds(self):
        cases = (
            b'#mannot-scope(s => [$ mark(x, tag: #(s.tag)("x")) $\n'
            b'$ y #(s.tag)("x") #(s.annot)("x", c, ()) $])',
            b'#mannot-scope(s => { let term = (s.mark)("x"); '
            b'[$ x #(s.tag)("x") #(s.annot)("x", c, ()) $] })',
        )
        for body in cases:
            self.assertEqual(len(self.evaluate(body)[0][BOUNDS].diagnostics), 1)

    def test_unknown_display_output_cannot_be_declared_marker_free(self):
        for output in (b'#unknown()', b'#value', b'unknown(x)', b'#{ [] }', b'#(s.mark)(target)($x$)'):
            for display in (b'x #(s.tag)("x") ' + output + b' #(s.annot)("x", c, ())',
                            output + b' x #(s.tag)("x") #(s.annot)("x", c, ())'):
                result, _, _ = self.evaluate(b'#mannot-scope(s => [$ ' + display + b' $])')
                self.assertFalse(result[BOUNDS].diagnostics, display)
                self.assertTrue(any('Bounds refused' in reason for reason in result[BOUNDS].skips))

    def test_unknown_target_production_drawable_calls_and_label_attachment_refuse(self):
        cases = (
            PLAIN.replace(b'(s.annot)("x"', b'(s.annot)(target'),
            PLAIN.replace(b'(s.annot)("x"', b'(s.annot)("x" + ""'),
            PLAIN.replace(b'c, ()', b'c, make-drawing()'),
            PLAIN.replace(b'c, ()', b'c, { unknown(); () }'),
            PLAIN.replace(b'(s.annot)("x", c, ())', b'annot((s.tag)("x"), annotation-value)'),
            b'#mannot-scope(s => [$ mark(y, tag: #(s.tag)("y")) #(s.tag)("x") #(s.annot)("x", c, ()) $])',
        )
        for body in cases:
            result, _, _ = self.evaluate(body)
            self.assertFalse(result[BOUNDS].diagnostics, body)
            self.assertTrue(any('Bounds refused' in reason for reason in result[BOUNDS].skips))

    def test_dictionary_dot_calls_and_marker_overrides_do_not_supply_semantic_proof(self):
        cases = (
            PLAIN.replace(b'(s.tag)("x")', b's.tag("x")'),
            PLAIN.replace(b'(s.annot)("x", c, ())', b's.annot("x", c, ())'),
            b'#mannot-scope(s => [#let term = (s.mark)("x")\n'
            b'$ x #(s.tag)("x") term(y, tag: #<override>) #(s.annot)("x", c, ()) $])',
            b'#mannot-scope(s => [#let term = (s.mark)("x", tag: <override>)\n'
            b'$ x #(s.tag)("x") term(y) #(s.annot)("x", c, ()) $])',
        )
        for body in cases:
            result, tree, packages = self.evaluate(body)
            self.assertFalse(result[BOUNDS].diagnostics, body)
            self.assertTrue(any('Bounds refused' in reason for reason in result[BOUNDS].skips))
            if b'override' in body:
                r = resolve(tree, packages)
                self.assertFalse(any(applied_marker(r, i) for i, node in enumerate(tree.nodes)
                                     if node.kind in ('FuncCall', 'MathCall')))

    def test_stored_content_unknown_wrappers_styles_and_explicit_collisions_refuse(self):
        cases = (
            b'#let stored = [' + PLAIN + b']',
            b'#unknown([' + PLAIN + b'])',
            b'#show math.equation: it => it\n' + PLAIN,
            PLAIN.replace(b'$])', b'$], prefix: "same")') + b'\n#mannot-scope(t => [], prefix: "same")',
        )
        for body in cases:
            result, _, _ = self.evaluate(body)
            self.assertFalse(result[BOUNDS].diagnostics, body)
            self.assertTrue(any('Bounds refused' in reason for reason in result[BOUNDS].skips))

    def test_scopes_literal_sets_and_immutable_receiver_aliases_use_real_identities(self):
        body = (b'#mannot-scope(s => { let t = s; '
                b'[$ x #(t.tag)("x") + y #(t.tag)("y") #(t.annot)(("x", "y"), c, ()) $] })')
        result, tree, packages = self.evaluate(body)
        self.assertEqual(len(result[BOUNDS].diagnostics), 2)
        r = resolve(tree, packages)
        targets = [scoped_targets(r, i) for i, node in enumerate(tree.nodes)
                   if node.kind in ('FuncCall', 'MathCall') and scoped_targets(r, i) is not None]
        self.assertEqual([value[0][1] for value in targets], ['x', 'y'])
        self.assertEqual(targets[0][0][0], targets[1][0][0])
        body = (b'#mannot-scope(s => [$ x #(s.tag)("x") #(s.annot)("x", c, ()) $\n'
                b'#mannot-scope(t => [$ mark(x, tag: #(t.tag)("x")) #(t.annot)("x", c, ()) $])])')
        self.assertEqual(len(self.evaluate(body)[0][BOUNDS].diagnostics), 1)

    def test_cross_scope_targets_and_nonliteral_names_are_refused(self):
        body = (b'#mannot-scope(s => [#mannot-scope(t => [$ x #(s.tag)("x") '
                b'#(s.annot)("x", c, ()) $])])')
        result, _, _ = self.evaluate(body)
        self.assertFalse(result[BOUNDS].diagnostics)
        self.assertTrue(any('different scope' in reason for reason in result[BOUNDS].skips))
        for literal in ('"에너지"'.encode(), b'"energy\\u{2d}x"'):
            result, _, _ = self.evaluate(PLAIN.replace(b'"x"', literal))
            self.assertFalse(result[BOUNDS].diagnostics)
            self.assertTrue(any('Bounds refused' in reason for reason in result[BOUNDS].skips))

    def test_duplicate_overlay_target_is_reported_once_per_target_and_call(self):
        result, _, _ = self.evaluate(PLAIN.replace(b'(s.annot)("x",', b'(s.annot)(("x", "x"),'))
        self.assertEqual(len(result[BOUNDS].diagnostics), 1)

    def test_missing_changed_and_shadowed_provenance_never_invents_semantics(self):
        result, _, _ = self.evaluate(PLAIN, packages=())
        self.assertFalse(result[BOUNDS].diagnostics)
        _, _, packages = self.evaluate(PLAIN)
        namespace_only = tuple(replace(package, artifacts=()) for package in packages)
        self.assertFalse(self.evaluate(PLAIN, packages=namespace_only)[0][BOUNDS].diagnostics)
        self.assertFalse(self.evaluate(b'#mannot-scope(s => [$ mark(x, tag: #<x>) $])',
                                       packages=namespace_only)[0][TAGS].diagnostics)
        for filename in ('mark.typ', 'annot.typ', 'util.typ'):
            with self.subTest(filename=filename):
                target = self.package_root / 'preview/mannot/0.4.0/src' / filename
                original = target.read_bytes()
                target.write_bytes(original + b'\n// changed\n')
                result, _, _ = self.evaluate(b'#mannot-scope(s => [$ mark(x, tag: #<x>) $])')
                if filename in ('mark.typ', 'util.typ'):
                    self.assertFalse(result[TAGS].diagnostics)
                result, _, _ = self.evaluate(PLAIN)
                if filename in ('annot.typ', 'util.typ'):
                    self.assertFalse(result[BOUNDS].diagnostics)
                target.write_bytes(original)
        for body in (b'#let mark = (..args) => []\n#mannot-scope(s => [$ mark(x, tag: #<x>) $])',
                     b'#mannot-scope(s => { let s = (:); [$ x #(s.tag)("x") #(s.annot)("x", c, ()) $] })',
                     b'#mannot-scope(s => { let f = () => [$ mark(x, tag: #<x>) $]; [] })'):
            result, _, _ = self.evaluate(body)
            self.assertFalse(any(result[rid].diagnostics for rid in RULE_IDS))

    def test_suppression_is_attached_to_the_original_display_and_no_edits_exist(self):
        body = (b'#mannot-scope(s => [\n'
                b'// typst-lint: ignore-next core.scoped-marker-tags -- public consumer\n'
                b'$ mark(x, tag: #<x>) $\n'
                b'// typst-lint: ignore-next core.annotation-marker-bounds -- documented fixture\n'
                b'$ x #(s.tag)("x") #(s.annot)("x", c, ()) $\n])')
        result, _, _ = self.evaluate(body)
        self.assertFalse(any(result[rid].diagnostics or result[rid].edits for rid in RULE_IDS))
        self.assertEqual([len(result[rid].suppressed) for rid in RULE_IDS], [1, 1])

    def test_cli_positive_all_modes_remain_read_only_and_do_not_invoke_compiler(self):
        root = self.base / 'consumer'
        root.mkdir()
        source = IMPORT + b'#mannot-scope(s => [$ mark(y, tag: #<public>) $])\n' + PLAIN
        target = root / 'main.typ'
        target.write_bytes(source)
        (root / '.typst-lint.toml').write_text('rule-set="core"\nentrypoint="main.typ"\n')
        output = root / 'existing.pdf'
        output.write_bytes(b'OUTPUT SENTINEL')
        cache = self.base / 'empty-cache'
        cache.mkdir()
        tripwire = self.base / 'bin'
        tripwire.mkdir()
        called = self.base / 'compiler-called'
        compiler = tripwire / 'typst'
        compiler.write_text('#!/bin/sh\nprintf called > "$TYPST_TEST_COMPILER_MARKER"\nexit 97\n')
        compiler.chmod(0o700)
        env = dict(os.environ, TYPST_PACKAGE_CACHE_PATH=str(cache),
                   TYPST_TEST_COMPILER_MARKER=str(called),
                   PATH=str(tripwire) + os.pathsep + os.environ.get('PATH', ''))
        before = {p.relative_to(self.package_root): p.read_bytes()
                  for p in self.package_root.rglob('*') if p.is_file()}
        for mode in ('check', 'dry-run', 'fix', 'compile'):
            command = [sys.executable, str(TOOL / ('typst-style' if mode == 'compile' else 'typst-lint')),
                       'fix' if mode == 'dry-run' else mode, '--root', str(root),
                       '--package-path', str(self.package_root), '--format', 'json']
            if mode == 'dry-run':
                command.append('--dry-run')
            if mode == 'compile':
                command.extend(['--', 'main.typ', 'existing.pdf'])
            process = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, 2, process.stderr)
            value = json.loads(process.stdout)
            self.assertEqual({row['rule_id'] for row in value['diagnostics']}, set(RULE_IDS), value)
            self.assertFalse(value['compiler_invoked'] or value['proposed_edits'] or value['applied_fixes'])
            self.assertFalse(value['coverage']['complete'])
            self.assertEqual(target.read_bytes(), source)
            self.assertEqual(output.read_bytes(), b'OUTPUT SENTINEL')
            self.assertFalse(called.exists() or list(cache.rglob('*')))
        (root / '.typst-lint.toml').write_text('rule-set="core"\nentrypoint="main.typ"\n'
            '[[ignore]]\npaths=["main.typ"]\n'
            'rules=["core.scoped-marker-tags","core.annotation-marker-bounds"]\n'
            'reason="documented public fixture"\n')
        process = subprocess.run([sys.executable, str(TOOL / 'typst-lint'), 'check', '--root', str(root),
                                  '--package-path', str(self.package_root), '--format', 'json'],
                                 env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 2, process.stderr)
        value = json.loads(process.stdout)
        self.assertFalse(value['diagnostics'] or value['proposed_edits'])
        self.assertEqual(value['suppressed_count'], 2)
        self.assertEqual(before, {p.relative_to(self.package_root): p.read_bytes()
                                  for p in self.package_root.rglob('*') if p.is_file()})


if __name__ == '__main__':
    unittest.main()
