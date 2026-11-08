"""Pinned CeTZ draw wildcard evidence remains source- and snapshot-bound."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

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

    def proof(self, source, *, omit=None, mutate_draw=False):
        (self.project / 'main.typ').write_bytes(source)
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
