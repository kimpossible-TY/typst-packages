"""Real ASTs and genuine explicitly captured package artifacts, never fake facts."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
from anchor_overlay_rules import analyze, ANCHOR, LOCAL, DIRECTIONS
from edit_plan import Edit, plan
from package_loader import load_packages
from static_resolver import imported_paths
from syntax_client import parse

IMPORT = (b'#import "@local/scoped-annotations:0.3.0": mannot-scope, local-tag-scope, annot-cetz-local\n'
          b'#import "@preview/mannot:0.4.0": mark, annot-cetz\n'
          b'#import "@preview/cetz:0.4.2"\n')


def materialize(root):
    shutil.copytree(REPO / 'local/scoped-annotations/0.3.0', root / 'local/scoped-annotations/0.3.0')
    shutil.copytree(TOOL / 'tests/fixtures/marker-bounds-packages/preview/mannot/0.4.0',
                    root / 'preview/mannot/0.4.0')
    shutil.copytree(TOOL / 'tests/fixtures/anchor-overlay-packages/preview/cetz/0.4.2',
                    root / 'preview/cetz/0.4.2')


def document(draw=b'cetz.draw.line("x.north", "x.south")', *, target=b'"x"',
             marker=b'#(s.mark)("x")(1)', receiver=b's'):
    return (b'#mannot-scope(' + receiver + b' => [$ ' + marker + b' #('
            + receiver + b'.annot)(' + target + b', cetz, { ' + draw + b' }) $])')


class AnchorOverlayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / 'packages'
        materialize(self.root)

    def run_rules(self, body, *, prefix=IMPORT, packages=None):
        source = prefix + body
        if packages is None:
            packages = load_packages(self.root, imported_paths(parse(source))).sources
        return source, analyze(parse(source), 'main.typ', packages=packages)

    def candidate(self, body, expected, *, prefix=IMPORT):
        source, results = self.run_rules(body, prefix=prefix)
        result = results[ANCHOR]
        self.assertTrue(result.edits, result.skips)
        candidate = plan(source, result.edits).candidate()
        self.assertEqual(candidate, prefix + expected)
        self.assertFalse(parse(candidate).nodes[0].errors)
        packages = load_packages(self.root, imported_paths(parse(candidate))).sources
        again = analyze(parse(candidate), 'main.typ', packages=packages)
        self.assertFalse(again[ANCHOR].diagnostics or again[ANCHOR].edits)
        self.assertEqual(again, analyze(parse(candidate), 'main.typ', packages=packages))
        return result

    def test_real_scoped_marker_and_two_coordinate_repairs(self):
        body = document()
        result = self.candidate(body, body.replace(b'"x.north"', b'(s.node)("x", "north")').replace(
            b'"x.south"', b'(s.node)("x", "south")'))
        self.assertEqual(len(result.diagnostics), 2)

    def test_eight_exact_compass_suffixes_and_drawing_style_is_not_a_coordinate(self):
        for direction in sorted(DIRECTIONS):
            with self.subTest(direction=direction):
                raw = b'"x.' + direction.encode() + b'"'
                body = document(b'cetz.draw.line(' + raw + b', (0, 0), stroke: "x.north")')
                self.candidate(body, body.replace(raw, b'(s.node)("x", "' + direction.encode() + b'")', 1))
        for suffix in (b'center', b'unknown', b'north.extra', b'', b'northward'):
            source, result = self.run_rules(document(b'cetz.draw.line("x.' + suffix + b'", (0,0))'))
            self.assertFalse(result[ANCHOR].diagnostics or result[ANCHOR].edits)

    def test_array_targets_and_raw_marker_tag_ownership(self):
        body = document(b'cetz.draw.line("x.north", "y.south")', target=b'("x", "y")',
                        marker=b'mark(1, tag: #(s.tag)("x")) mark(2, tag: #(s.tag)("y"))')
        self.candidate(body, body.replace(b'"x.north"', b'(s.node)("x", "north")').replace(
            b'"y.south"', b'(s.node)("y", "south")'))

    def test_real_aliases_do_not_depend_on_variable_or_namespace_spelling(self):
        prefix = IMPORT.replace(b'"@preview/cetz:0.4.2"', b'"@preview/cetz:0.4.2" as picture')
        prefix += b'#let wire = picture.draw.line\n#let backend = picture\n'
        body = document(b'wire("x.north", "x.south")', receiver=b'actual',
                        marker=b'#(actual.mark)("x")(1)').replace(b', cetz,', b', backend,')
        self.candidate(body, body.replace(b'"x.north"', b'(actual.node)("x", "north")').replace(
            b'"x.south"', b'(actual.node)("x", "south")'), prefix=prefix)

    def test_immutable_receiver_alias_retains_same_scope(self):
        body = (b'#mannot-scope(s => { let actual = s; let s = (:); '
                b'[$ #(actual.mark)("x")(1) #(actual.annot)("x", cetz, '
                b'{ cetz.draw.line("x.north", "x.south") }) $] })')
        self.candidate(body, body.replace(b'"x.north"', b'(actual.node)("x", "north")').replace(
            b'"x.south"', b'(actual.node)("x", "south")'))

    def test_local_api_uses_actual_generated_tag_receiver(self):
        body = (b'#local-tag-scope(t => [$ mark(1, tag: #(t.tag)("x")) '
                b'#annot-cetz-local((t.tag)("x"), cetz, '
                b'{ cetz.draw.line("x.north", "x.south") }) $])')
        self.candidate(body, body.replace(b'"x.north"', b'(t.anchor)("x", "north")').replace(
            b'"x.south"', b'(t.anchor)("x", "south")'))

    def test_utf8_receiver_bom_crlf_and_neighbor_comments_are_untouched(self):
        prefix = b'\xef\xbb\xbf' + IMPORT.replace(b'\n', b'\r\n')
        body = document(b'cetz.draw.line(/* before */ "x.north",\r\n "x.south" /* after */)',
                        receiver='범위'.encode(), marker='#(범위.mark)("x")(1)'.encode())
        self.candidate(body, body.replace(b'"x.north"', '(범위.node)("x", "north")'.encode()).replace(
            b'"x.south"', '(범위.node)("x", "south")'.encode()), prefix=prefix)

    def test_unknown_or_custom_same_spelled_apis_never_acquire_semantics(self):
        bodies = (
            document(b'line("x.north", "x.south")'),
            document().replace(b'cetz.draw.line', b'fake.draw.line'),
            document().replace(b', cetz,', b', fake,'),
            document().replace(b'#(s.mark)("x")(1)', b'#(s.mark)("x")'),
            document().replace(b'#(s.mark)("x")(1)', b'#(s.tag)("x")'),
        )
        prefix = IMPORT + b'#let line = (a,b) => ()\n#let fake = (draw: (line: line))\n'
        for body in bodies:
            with self.subTest(body=body):
                _, result = self.run_rules(body, prefix=prefix)
                self.assertFalse(result[ANCHOR].diagnostics or result[ANCHOR].edits)
        _, result = self.run_rules(document(), packages=())
        self.assertFalse(result[ANCHOR].diagnostics or result[ANCHOR].edits)

    def test_same_display_target_scope_and_unique_application_are_required(self):
        bodies = (
            document(target=b'"y"'),
            document(target=b'("x", "x")'),
            b'#mannot-scope(s => [$ #(s.annot)("x", cetz, { cetz.draw.line("x.north", "x.south") }) #(s.mark)("x")(1) $])',
            document(marker=b'#(s.mark)("x")(1) #(s.mark)("x")(2)'),
            b'#mannot-scope(s => [$ #(s.mark)("x")(1) $ $ #(s.annot)("x", cetz, { cetz.draw.line("x.north", "x.south") }) $])',
            b'#mannot-scope(outer => [$ #(outer.mark)("x")(1) $ #mannot-scope(s => [$ #(s.annot)("x", cetz, { cetz.draw.line("x.north", "x.south") }) $])])',
            document(marker=b'#unknown([#(s.mark)("x")(1)])'),
            document(marker=b'#(s.mark)("x")(1) #unknown()'),
            b'#mannot-scope(outer => [#mannot-scope(s => [$ #(outer.mark)("x")(1) #(s.mark)("x")(2) #(s.annot)("x", cetz, { cetz.draw.line("x.north", "x.south") }) $])])',
            document(marker=b'#(s.mark)("x")(1) 2 #(s.tag)("x")'),
            b'#let stored = [' + document() + b']',
            b'#unknown([' + document() + b'])',
            b'#mannot-scope(s => { let stored = [$ #(s.mark)("x")(1) #(s.annot)("x", cetz, { cetz.draw.line("x.north", "x.south") }) $]; [] })',
            document().replace(b'])', b'], prefix: "same")') + b'\n#mannot-scope(t => [], prefix: "same")',
        )
        for body in bodies:
            with self.subTest(body=body):
                _, result = self.run_rules(body)
                self.assertFalse(result[ANCHOR].diagnostics or result[ANCHOR].edits)
                self.assertTrue(any('skipped:' in s for s in result[ANCHOR].skips))

    def test_ordinary_draw_nodes_and_unsupported_geometry_are_not_rewritten(self):
        drawings = (
            b'cetz.draw.line("ordinary.north", "ordinary.south")',
            b'cetz.draw.line("x.north", "x.south", name: "x")',
            b'cetz.draw.line("x.north", "x.south", name: dynamic)',
            b'cetz.draw.line("x.north", "x.south", ..options)',
            b'cetz.draw.rect((0,0),(1,1),name:"x"); cetz.draw.line("x.north", "x.south")',
            b'unknown(); cetz.draw.line("x.north", "x.south")',
            b'let x = "x.north"; cetz.draw.line(x, "x.south")',
            b'cetz.draw.line("x.north", "x.south", stroke: unknown())',
            b'cetz.draw.line("x.north", "x.south", mark: custom)',
        )
        for draw in drawings:
            with self.subTest(draw=draw):
                _, result = self.run_rules(document(draw))
                self.assertFalse(result[ANCHOR].diagnostics or result[ANCHOR].edits)

    def test_helpers_are_accepted_and_escaped_literals_are_not_decoded(self):
        drawings = (
            b'cetz.draw.line((s.node)("x", "north"), (s.anchor)("x", "south"))',
            b'cetz.draw.line("x\\u{2e}north", "x\\u{2e}south")',
        )
        for draw in drawings:
            _, result = self.run_rules(document(draw))
            self.assertFalse(result[ANCHOR].diagnostics or result[ANCHOR].edits)

    def test_dotted_marker_is_identified_by_binding_but_not_blindly_repaired(self):
        body = document(b'cetz.draw.line("a.b.north", "a.b.south")', target=b'"a.b"',
                        marker=b'#(s.mark)("a.b")(1)')
        _, result = self.run_rules(body)
        self.assertEqual(len(result[ANCHOR].diagnostics), 2)
        self.assertFalse(result[ANCHOR].edits)
        self.assertTrue(any('dotted marker names' in s for s in result[ANCHOR].skips))

    def test_prefix_namespace_and_parent_names_require_actual_dot_free_proof(self):
        base = document()
        for suffix in (b', prefix: "a.b"', b', prefix: dynamic', b', prefix: "a\\u{2e}b"'):
            body = base[:-1] + suffix + b')'
            _, result = self.run_rules(body)
            self.assertEqual(len(result[ANCHOR].diagnostics), 2, body)
            self.assertFalse(result[ANCHOR].edits)
            self.assertTrue(any('generated scope prefix' in s for s in result[ANCHOR].skips))
        for suffix in (b', prefix: "safe"', b', prefix: auto'):
            body = base[:-1] + suffix + b')'
            self.candidate(body, body.replace(b'"x.north"', b'(s.node)("x", "north")').replace(
                b'"x.south"', b'(s.node)("x", "south")'))
        for namespace in (b'"a.b"', b'dynamic'):
            body = base.replace(b'mannot-scope', b'local-scope-annotations')[:-1] + b', namespace: ' + namespace + b')'
            prefix = IMPORT.replace(b'mannot-scope, local-tag-scope', b'mannot-scope, local-scope-annotations, local-tag-scope')
            _, result = self.run_rules(body, prefix=prefix)
            self.assertEqual(len(result[ANCHOR].diagnostics), 2)
            self.assertFalse(result[ANCHOR].edits)
        for child, safe in ((b'"child"', True), (b'"child.with.dot"', False), (b'dynamic', False)):
            body = b'#mannot-scope(parent => [' + base[:-1] + b', parent: parent, name: ' + child + b')])'
            if safe:
                self.candidate(body, body.replace(b'"x.north"', b'(s.node)("x", "north")').replace(
                    b'"x.south"', b'(s.node)("x", "south")'))
            else:
                _, result = self.run_rules(body)
                self.assertEqual(len(result[ANCHOR].diagnostics), 2)
                self.assertFalse(result[ANCHOR].edits)

    def test_overlay_alias_loses_receiver_and_is_diagnostic_only(self):
        body = (b'#mannot-scope(s => { let overlay = s.annot; '
                b'[$ #(s.mark)("x")(1) #overlay("x", cetz, '
                b'{ cetz.draw.line("x.north", "x.south") }) $] })')
        _, result = self.run_rules(body)
        self.assertEqual(len(result[ANCHOR].diagnostics), 2)
        self.assertFalse(result[ANCHOR].edits)
        self.assertTrue(any('receiver is unavailable' in s for s in result[ANCHOR].skips))

    def test_anchor_suppression_remains_on_original_line_call(self):
        body = document(b'cetz.draw.line((0,0), (1,1));\n// typst-lint: ignore-next core.scoped-anchor-access -- external geometry\n'
                        b'cetz.draw.line("x.north", "x.south")\n')
        source, result = self.run_rules(body)
        self.assertFalse(result[ANCHOR].diagnostics or result[ANCHOR].edits)
        self.assertEqual(len(result[ANCHOR].suppressed), 2)
        self.assertEqual(plan(source, result[ANCHOR].edits).candidate(), source)

    def test_raw_local_overlay_is_diagnostic_only_and_aliases_are_resolved(self):
        cases = (
            (IMPORT, b'annot-cetz'),
            (IMPORT.replace(b'mark, annot-cetz', b'mark, annot-cetz as raw'), b'raw'),
            (IMPORT + b'#let raw = annot-cetz\n', b'raw'),
        )
        for prefix, callee in cases:
            with self.subTest(callee=callee):
                body = b'#mannot-scope(actual => [$ mark(1, tag: #(actual.tag)("x")) #' + callee + b'((actual.tag)("x"), cetz, {}) $])'
                source, result = self.run_rules(body, prefix=prefix)
                self.assertEqual(len(result[LOCAL].diagnostics), 1)
                self.assertFalse(result[LOCAL].edits)
                self.assertEqual(plan(source, result[LOCAL].edits).candidate(), source)
        body = b'#annot-cetz(<x>, cetz, {})\n' + document()
        _, result = self.run_rules(body)
        self.assertFalse(result[LOCAL].diagnostics or result[LOCAL].edits)
        _, result = self.run_rules(b'#mannot-scope(s => { let annot-cetz = (a,b,c) => (); annot-cetz(1,2,3) })')
        self.assertFalse(result[LOCAL].diagnostics or result[LOCAL].edits)

    def test_supported_wrappers_and_local_apis_are_unchanged(self):
        for body in (document(b'cetz.draw.line((s.node)("x","north"),(s.node)("x","south"))'),
                     b'#mannot-scope(s => [$ #(s.mark)("x")(1) #annot-cetz-local((s.tags)(("x",)), cetz, {}) $])'):
            source, result = self.run_rules(body)
            self.assertFalse(result[LOCAL].diagnostics or result[LOCAL].edits)
            self.assertEqual(plan(source, result[LOCAL].edits).candidate(), source)

    def test_missing_or_changed_real_artifacts_do_not_become_caller_attestations(self):
        for relative in ('src/draw.typ', 'src/draw/shapes.typ', 'src/coordinate.typ', 'src/anchor.typ'):
            with self.subTest(relative=relative):
                target = self.root / 'preview/cetz/0.4.2' / relative
                original = target.read_bytes()
                target.write_bytes(original + b'\n// altered\n')
                try:
                    _, result = self.run_rules(document())
                    self.assertFalse(result[ANCHOR].diagnostics or result[ANCHOR].edits)
                finally:
                    target.write_bytes(original)

    def test_combined_script_edits_preserve_anchor_suppression_and_config_ignores(self):
        from source_analysis import analyze as combined
        prefix = b'\xef\xbb\xbf' + IMPORT.replace(b'\n', b'\r\n')
        suppressed = document(b'cetz.draw.line((0,0), (1,1));\r\n'
            b'// typst-lint: ignore-next core.scoped-anchor-access -- external geometry\r\n'
            b'cetz.draw.line("x.north", "x.south")\r\n')
        body = document()
        source = prefix + b'$x^i$\r\n' + suppressed + b'\r\n' + body
        repaired = body.replace(b'"x.north"', b'(s.node)("x", "north")').replace(
            b'"x.south"', b'(s.node)("x", "south")')
        expected = prefix + b'$x^(i)$\r\n' + suppressed + b'\r\n' + repaired
        packages = load_packages(self.root, imported_paths(parse(source))).sources
        result = combined(parse(source), 'main.typ', packages=packages)
        self.assertEqual(plan(source, [e for r in result.values() for e in r.edits]).candidate(), expected)
        self.assertEqual(len(result[ANCHOR].suppressed), 2)
        second = combined(parse(expected), 'main.typ', packages=packages)
        self.assertFalse(any(r.edits for r in second.values()))
        self.assertEqual(len(second[ANCHOR].suppressed), 2)
        ignored = combined(parse(source), 'main.typ', packages=packages, ignored=frozenset((ANCHOR,)))
        self.assertFalse(ignored[ANCHOR].edits)
        self.assertEqual(plan(source, [e for r in ignored.values() for e in r.edits]).candidate(),
                         source.replace(b'$x^i$', b'$x^(i)$'))

    def test_actual_cli_modes_propose_without_writes_and_recheck_idempotently(self):
        from source_analysis import analyze as combined
        body = document() + b'\n#mannot-scope(t => [$ #(t.mark)("y")(1) #annot-cetz((t.tag)("y"), cetz, {}) $])\n'
        source = IMPORT + body
        expected = source.replace(b'"x.north"', b'(s.node)("x", "north")').replace(
            b'"x.south"', b'(s.node)("x", "south")')
        root = self.base / 'consumer'
        root.mkdir()
        target, output = root / 'main.typ', root / 'output.pdf'
        target.write_bytes(source)
        output.write_bytes(b'original output')
        for mode, dry in (('check', False), ('fix', True), ('fix', False), ('compile', False)):
            with self.subTest(mode=mode, dry=dry):
                launcher = 'typst-style' if mode == 'compile' else 'typst-lint'
                command = [sys.executable, str(TOOL / launcher), mode, '--root', str(root),
                           '--package-path', str(self.root), '--format', 'json']
                if dry: command.append('--dry-run')
                if mode == 'compile': command += ['--', 'main.typ', 'output.pdf']
                run = subprocess.run(command, capture_output=True, text=True, timeout=30)
                self.assertEqual(run.returncode, 2, run.stderr)
                result = json.loads(run.stdout)
                self.assertEqual({d['rule_id'] for d in result['diagnostics']}, {ANCHOR, LOCAL})
                self.assertEqual({e['rule_id'] for e in result['proposed_edits']}, {ANCHOR})
                self.assertFalse(result['compiler_invoked'] or result['applied_fixes'] or result['coverage']['complete'])
                self.assertEqual(result['proposal_status'], 'partial-not-applicable-transaction')
                self.assertTrue(all(e['baseline_sha256'] == hashlib.sha256(source).hexdigest()
                                    for e in result['proposed_edits']))
                edits = [Edit(e['start'], e['end'], e['replacement'].encode()) for e in result['proposed_edits']]
                self.assertEqual(plan(source, edits).candidate(), expected)
                self.assertEqual(target.read_bytes(), source)
                self.assertEqual(output.read_bytes(), b'original output')
        packages = load_packages(self.root, imported_paths(parse(source))).sources
        whole = combined(parse(source), 'main.typ', packages=packages)
        self.assertEqual(plan(source, [e for r in whole.values() for e in r.edits]).candidate(), expected)
        second = combined(parse(expected), 'main.typ', packages=packages)
        self.assertFalse(any(r.edits for r in second.values()))
        target.write_bytes(expected)  # Only this test's isolated temporary file.
        check = subprocess.run([sys.executable, str(TOOL / 'typst-lint'), 'check', '--root', str(root),
                                '--package-path', str(self.root), '--format', 'json'],
                               capture_output=True, text=True, timeout=30)
        self.assertEqual(check.returncode, 2, check.stderr)
        value = json.loads(check.stdout)
        self.assertFalse(value['proposed_edits'])
        self.assertEqual({d['rule_id'] for d in value['diagnostics']}, {LOCAL})
        self.assertEqual(output.read_bytes(), b'original output')


if __name__ == '__main__':
    unittest.main()
