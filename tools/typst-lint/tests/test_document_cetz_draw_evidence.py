"""Pinned CeTZ draw wildcard evidence remains source- and snapshot-bound."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_footnote_binding
import document_cetz_draw_evidence as draw
import document_footnote_evidence
import document_resources
import document_sequence_preview
import include_context
import namespace_multiroot
from syntax_client import EXECUTABLE, parse
from typst_lint import SafetyError, load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class CetzDrawEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.project, self.packages, self.cache = (base / name for name in ('project', 'packages', 'cache'))
        for directory in (self.project, self.packages, self.cache):
            directory.mkdir()
        shutil.copytree(TOOL / 'tests/fixtures/anchor-overlay-packages/preview/cetz/0.4.2',
                        self.packages / 'preview/cetz/0.4.2')
        shutil.copytree(TOOL / 'tests/fixtures/reviewed-packages/local/text-utils/0.1.2',
                        self.packages / 'local/text-utils/0.1.2')
        folder = self.packages / 'preview/cetz/0.4.2/src/draw'
        (folder / 'projection.typ').write_bytes(b'#let ortho = none\n#let on-xy = none\n#let on-xz = none\n#let on-yz = none\n')
        (folder / 'util.typ').write_bytes(b'#let assert-version = none\n#let register-coordinate-resolver = none\n')

    def proof(self, source, *, omit=None, mutate_draw=False, extra_sources=()):
        (self.project / 'main.typ').write_bytes(source)
        for path, data in extra_sources:
            target = self.project / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        context = include_context.load_include_context(self.project, (('main.typ', source),))
        ns = namespace_multiroot.load_multiroot_capture(self.packages, self.cache)
        session = conditional_footnote_binding.ConditionalFootnoteBinding(context, ns)
        draw_path = self.packages / 'preview/cetz/0.4.2/src/draw.typ'
        if mutate_draw:
            draw_path.write_bytes(draw_path.read_bytes() + b'\n#let footnote = none\n')
        paths = ['local/text-utils/0.1.2/typst.toml', 'local/text-utils/0.1.2/lib.typ',
                 'preview/cetz/0.4.2/typst.toml', 'preview/cetz/0.4.2/src/lib.typ',
                 'preview/cetz/0.4.2/src/draw.typ']
        paths += ['preview/cetz/0.4.2/src/draw/' + name + '.typ'
                  for name in ('grouping', 'transformations', 'styling', 'shapes', 'projection', 'util')]
        requests = [document_resources.ResourceRequest('project', item.path, 'source')
                    for item in session._graph.sources]
        requests += [document_resources.ResourceRequest('packages', path, 'package')
                     for path in paths if path != omit]
        snapshot = document_resources.capture(dict(project=str(self.project), packages=str(self.packages),
                                                   cache=str(self.cache)), requests)
        return document_footnote_evidence.PreviewFootnoteEvidence(session, snapshot)

    def supports(self, proof, source):
        tree = parse(source)
        closures = [node for node in tree.nodes if node.kind == 'Closure']
        self.assertEqual(len(closures), 1)
        imports = tuple((node.start, node.end) for node in tree.nodes if node.kind == 'ModuleImport'
                        and source[node.start:node.end].startswith(b'import cetz.draw'))
        return draw.supports_imports(proof, 'main.typ', source, closures[0].start, closures[0].end, imports)

    def supports_named(self, proof, source, path='main.typ'):
        tree = parse(source)
        closure = next(node for node in tree.nodes if node.kind == 'Closure')
        imports = tuple((node.start, node.end) for node in tree.nodes if node.kind == 'ModuleImport'
                        and source[node.start:node.end].startswith(b'import draw:'))
        return draw.supports_named_draw_imports(proof, path, closure.start, closure.end, imports)

    def test_named_draw_import_is_snapshot_bound_and_conditional(self):
        source = (b'#import "@local/text-utils:0.1.2": *\n'
                  b'#import "@preview/cetz:0.4.2": canvas, draw\n'
                  b'#use(x => [#{import draw: *}\n#footnote[Note]])')
        proof = self.proof(source)
        self.assertTrue(self.supports_named(proof, source))
        with self.assertRaises(SafetyError):
            self.supports_named(proof, source, 'missing.typ')
        # This API inspects the issued immutable snapshot, not the live file.
        # No candidate bytes can be passed to substitute a changed source.
        (self.project / 'main.typ').write_bytes(source + b'\n#let draw = none\n')
        self.assertTrue(self.supports_named(proof, source))

    def test_named_draw_shadow_and_mutation_refuse(self):
        prefix = (b'#import "@local/text-utils:0.1.2": *\n'
                  b'#import "@preview/cetz:0.4.2": canvas, draw\n')
        body = b'#use(x => [#{import draw: *}\n#footnote[Note]])'
        for middle in (b'#let draw = none\n', b'#{draw = none}\n',
                       b'#{draw.foo = none}\n', b'#import "unknown.typ": *\n',
                       b'#import "unknown.typ": draw\n',
                       b'#import "@preview/cetz:0.3.0": draw\n'):
            with self.subTest(middle=middle):
                source = prefix + middle + body
                self.assertFalse(self.supports_named(self.proof(source), source))
        for callback in (b'#use(draw => [#{import draw: *}])',
                         b'#use(x => [#let draw = none\n#{import draw: *}])',
                         b'#use(x => [#{import draw: *; draw = none}])'):
            with self.subTest(callback=callback):
                source = prefix + callback
                with self.assertRaises(SafetyError):
                    self.supports_named(self.proof(source), source)

    def test_named_draw_missing_or_changed_package_refuses(self):
        source = (b'#import "@local/text-utils:0.1.2": *\n'
                  b'#import "@preview/cetz:0.4.2": draw\n'
                  b'#use(x => [#{import draw: *}])')
        for missing in ('preview/cetz/0.4.2/src/draw.typ', 'preview/cetz/0.4.2/src/draw/projection.typ'):
            with self.subTest(missing=missing), self.assertRaises(SafetyError):
                self.supports_named(self.proof(source, omit=missing), source)
        with self.assertRaises(SafetyError):
            self.supports_named(self.proof(source, mutate_draw=True), source)

    def test_named_draw_in_transitive_source_and_unrelated_local_let(self):
        main = b'#import "figures.typ": fig\n#fig()\n'
        figures = (b'#import "@local/text-utils:0.1.2": *\n'
                   b'#import "@preview/cetz:0.4.2": canvas, draw\n'
                   b'#let fig(x) = { let size = 1; import draw: *; line((0, 0), (size, size)) }\n')
        proof = self.proof(main, extra_sources=(('figures.typ', figures),))
        self.assertTrue(self.supports_named(proof, figures, 'figures.typ'))
        batch = draw.analyze_named_draw_batch(proof, self.named_requests(figures, 'figures.typ'))
        self.assertEqual(batch['results'][0]['status'], 'CONDITIONAL')

    @unittest.skipUnless(shutil.which('typst'), 'Requires Typst')
    def test_named_module_import_callback_shape_compiles(self):
        (self.project / 'draw.typ').write_text('#let sample = "ok"\n')
        (self.project / 'module.typ').write_text('#import "draw.typ"\n')
        (self.project / 'main.typ').write_text(
            '#import "module.typ": draw\n'
            '#let fig(x) = { let size = x; import draw: *; sample }\n'
            '#fig(1)\n')
        result = subprocess.run([shutil.which('typst'), 'compile', '--root', str(self.project),
                                 str(self.project / 'main.typ'), str(self.project / 'out.pdf')],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))

    def named_requests(self, source, path='main.typ'):
        tree = parse(source)
        result = []
        for closure in (n for n in tree.nodes if n.kind == 'Closure'):
            spans = tuple((n.start, n.end) for n in tree.nodes if n.kind == 'ModuleImport'
                          and closure.start <= n.start and n.end <= closure.end
                          and source[n.start:n.end].startswith(b'import draw:'))
            if spans:
                result.append((path, closure.start, closure.end, spans))
        return tuple(result)

    def test_named_batch_reports_conditions_and_unresolved_separately(self):
        source = (b'#import "@local/text-utils:0.1.2": *\n'
                  b'#import "@preview/cetz:0.4.2": draw\n'
                  b'#use(x => [#let size = 1\n#{import draw: *}])\n'
                  b'#use(draw => [#{import draw: *}])')
        proof = self.proof(source)
        requests = self.named_requests(source)
        result = draw.analyze_named_draw_batch(proof, requests)
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertEqual([r['status'] for r in result['results']], ['CONDITIONAL', 'UNRESOLVED'])
        self.assertIn('shadow', result['results'][1]['reason'])
        self.assertFalse(result['runtime_binding_proved'])
        self.assertFalse(result['edit_permission'])
        self.assertFalse(result['compiler_permission'])
        self.assertFalse(result['other_transitive_imports_proved'])

    def test_named_batch_rejects_forged_and_duplicate_selections(self):
        source = (b'#import "@local/text-utils:0.1.2": *\n'
                  b'#import "@preview/cetz:0.4.2": draw\n'
                  b'#use(x => [#{import draw: *}])')
        proof = self.proof(source)
        req = self.named_requests(source)[0]
        for requests in ((), (req, req), (('missing.typ', *req[1:]),),
                         (req,) * 513,
                         ((req[0], True, req[2], req[3]),),
                         ((req[0], req[1], req[2], req[3] * 4097),),
                         (('\ud800', *req[1:]),),
                         ((req[0], req[1], req[2], ((1, 2),)),)):
            with self.subTest(requests=requests), self.assertRaises(SafetyError):
                draw.analyze_named_draw_batch(proof, requests)
        # A real callback with a forged inner span is incomplete, never proven.
        start, end = req[3][0]
        forged = ((req[0], req[1], req[2], ((start + 1, end),)),)
        self.assertEqual(draw.analyze_named_draw_batch(proof, forged)['results'][0]['status'], 'UNRESOLVED')

    def test_named_batch_discards_all_results_if_snapshot_validation_fails(self):
        source = (b'#import "@local/text-utils:0.1.2": *\n'
                  b'#import "@preview/cetz:0.4.2": draw\n'
                  b'#use(x => [#{import draw: *}])')
        proof = self.proof(source)
        original = document_footnote_evidence._valid
        calls = 0
        def changed(value):
            nonlocal calls
            calls += 1
            if calls == 4:
                raise SafetyError('Snapshot changed during inspection')
            return original(value)
        with patch.object(document_footnote_evidence, '_valid', side_effect=changed):
            with self.assertRaises(SafetyError):
                draw.analyze_named_draw_batch(proof, self.named_requests(source))

    def test_named_batch_withholds_loop_shadow_at_import_site(self):
        prefix = (b'#import "@local/text-utils:0.1.2": *\n'
                  b'#import "@preview/cetz:0.4.2": draw\n')
        for callback in (b'#use(x => [#for draw in (none,) { import draw: * }])',
                         b'#use(x => [#for (draw, other) in ((none, 1),) { import draw: * }])',
                         b'#use(x => [#for other in (1,) { import draw: * }])'):
            with self.subTest(callback=callback):
                source = prefix + callback
                proof = self.proof(source)
                report = draw.analyze_named_draw_batch(proof, self.named_requests(source))
                self.assertEqual(report['results'][0]['status'], 'UNRESOLVED')

    def test_named_batch_shares_namespace_budget_across_sources(self):
        imports = (b'#import "@local/text-utils:0.1.2": *\n'
                   b'#import "@preview/cetz:0.4.2": draw\n')
        figures = imports + b'#let fig(x) = { import draw: *; line((0, 0), (1, 1)) }\n'
        main = imports + b'#import "figures.typ": fig\n#use(x => [#{import draw: *}])\n'
        proof = self.proof(main, extra_sources=(('figures.typ', figures),))
        requests = self.named_requests(main) + self.named_requests(figures, 'figures.typ')
        with patch.object(draw, '_named_observer', wraps=draw._named_observer) as observe:
            report = draw.analyze_named_draw_batch(proof, requests)
        self.assertEqual([row['status'] for row in report['results']], ['CONDITIONAL', 'CONDITIONAL'])
        self.assertIsNone(observe.call_args_list[0].args[-1])
        self.assertIsNotNone(observe.call_args_list[1].args[-1])

    def test_exact_namespace_is_parsed_and_lexically_bound(self):
        source = (b'#import "@local/text-utils:0.1.2": *\n#import "@preview/cetz:0.4.2"\n'
                  b'#use(x => [#{import cetz.draw: *}\n#footnote[Note]])')
        proof = self.proof(source)
        self.assertTrue(self.supports(proof, source))
        tree = parse(source)
        closure = next(node for node in tree.nodes if node.kind == 'Closure')
        for forged in (([1, 2],), ((True, 2),), ((1,),)):
            with self.subTest(forged=forged), self.assertRaises(SafetyError):
                draw.supports_imports(proof, 'main.typ', source, closure.start, closure.end, forged)

    def test_shadow_unknown_wildcard_and_nonmatching_version_refuse(self):
        suffix = b'#use(x => [#{import cetz.draw: *}\n#footnote[Note]])'
        prefix = b'#import "@local/text-utils:0.1.2": *\n#import "@preview/cetz:0.4.2"\n'
        for middle in (b'#let cetz = none\n', b'#import "unknown.typ": *\n',
                       b'#import "@preview/cetz:0.3.0" as cetz\n'):
            with self.subTest(middle=middle):
                source = prefix + middle + suffix
                self.assertFalse(self.supports(self.proof(source), source))
        for body in (b'#use(cetz => [#{import cetz.draw: *}\n#footnote[Note]])',
                     b'#use(x => [#let cetz = none\n#{import cetz.draw: *}\n#footnote[Note]])'):
            with self.subTest(body=body):
                source = prefix + body
                with self.assertRaises(SafetyError):
                    self.supports(self.proof(source), source)
        (self.project / 'shadow.typ').write_bytes(b'#let cetz = none\n')
        source = prefix + b'#import "shadow.typ": *\n' + suffix
        self.assertFalse(self.supports(self.proof(source), source))

    def test_missing_or_changed_draw_and_missing_dependency_refuse(self):
        source = (b'#import "@local/text-utils:0.1.2": *\n#import "@preview/cetz:0.4.2"\n'
                  b'#use(x => [#{import cetz.draw: *}\n#footnote[Note]])')
        for missing in ('preview/cetz/0.4.2/src/draw.typ', 'preview/cetz/0.4.2/src/draw/projection.typ'):
            with self.subTest(missing=missing):
                with self.assertRaises(SafetyError):
                    self.supports(self.proof(source, omit=missing), source)
        with self.assertRaises(SafetyError):
            self.supports(self.proof(source, mutate_draw=True), source)

    def test_partial_classification_checks_cetz_draw_and_rejects_shadow(self):
        prefix = b'#import "@local/text-utils:0.1.2": *\n#import "@preview/cetz:0.4.2"\n'
        body = (b'#use(x => [#context [\n#{import cetz.draw: *}\n'
                b'$ A #dots_space #footnote[Note] $\n]])')
        source = prefix + body
        proof = self.proof(source)
        catalog = load_rules()[0]['rules']
        result = document_sequence_preview.preview(parse(source), 'main.typ', catalog,
            profile='conditional-gaps-and-spacers', footnote_evidence=proof, classify_deferred=True)
        self.assertEqual(result.get('reason_code'), 'unsupported-deferred-footnote-binding', result)
        for middle in (b'#let cetz = none\n', b'#import "unknown.typ": *\n'):
            with self.subTest(middle=middle):
                candidate = prefix + middle + body
                result = document_sequence_preview.preview(parse(candidate), 'main.typ', catalog,
                    profile='conditional-gaps-and-spacers', footnote_evidence=proof, classify_deferred=True)
                self.assertEqual(result['verification'], 'withheld')
                self.assertNotIn('reason_code', result)

    def test_used_prior_wildcard_requires_transitive_snapshot_evidence(self):
        shutil.copytree(TOOL / 'tests/fixtures/reviewed-packages/local/scoped-annotations/0.3.0',
                        self.packages / 'local/scoped-annotations/0.3.0')
        shutil.copytree(TOOL / 'tests/fixtures/scoped-reference-packages/preview/mannot/0.4.0',
                        self.cache / 'preview/mannot/0.4.0')
        source = (b'#import "@local/text-utils:0.1.2": *\n#import "@preview/cetz:0.4.2"\n'
                  b'#import "@local/scoped-annotations:0.3.0": *\n'
                  b'#use(x => [#{import cetz.draw: *}\n#footnote[Note]])')
        (self.project / 'main.typ').write_bytes(source)
        context = include_context.load_include_context(self.project, (('main.typ', source),))
        ns = namespace_multiroot.load_multiroot_capture(self.packages, self.cache)
        session = conditional_footnote_binding.ConditionalFootnoteBinding(context, ns)
        roots = dict(project=str(self.project), packages=str(self.packages), cache=str(self.cache))
        files = [('project', 'main.typ', 'source')]
        for item in ns.packages:
            index = next(row.root_index for row in ns.selections if row.import_path == item.evidence.import_path)
            label = ('packages', 'cache')[index]
            base = Path(ns.roots[index])
            files.extend((label, Path(identity).relative_to(base).as_posix(), 'package')
                         for identity in (item.evidence.manifest_identity, item.evidence.source_identity))
        files.extend(('packages', 'preview/cetz/0.4.2/src/draw/' + name + '.typ', 'package')
                     for name in ('grouping', 'transformations', 'styling', 'shapes', 'projection', 'util'))
        files.append(('packages', 'preview/cetz/0.4.2/src/draw.typ', 'package'))
        def capture(extra=()):
            snapshot = document_resources.capture(roots, [document_resources.ResourceRequest(*x)
                                                           for x in (*files, *extra)])
            return document_footnote_evidence.PreviewFootnoteEvidence(session, snapshot)
        self.assertTrue(self.supports(capture(), source))
        mannot = self.cache / 'preview/mannot/0.4.0/src/lib.typ'
        original = mannot.read_bytes()
        mannot.write_bytes(original + b'\n#let cetz = none\n')
        with self.assertRaises(SafetyError):
            self.supports(capture(), source)
        mannot.write_bytes(original)
        shadow = self.packages / 'Preview/Mannot/0.4.0'
        shadow.mkdir(parents=True)
        (shadow / 'typst.toml').write_text('unreviewed')
        with self.assertRaises(SafetyError):
            self.supports(capture((('packages', 'Preview/Mannot/0.4.0/typst.toml', 'package'),)), source)


if __name__ == '__main__':
    unittest.main()
