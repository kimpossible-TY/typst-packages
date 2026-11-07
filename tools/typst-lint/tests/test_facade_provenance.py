"""Captured temp consumers and genuine loader packages, using the real parser."""
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import facade_provenance as facade
import import_graph as graph
from package_loader import load_packages
from static_resolver import (TEXT_UTILS, TEXT_UTILS_SOURCE_SHA256,
                             PROJECT_REFERENCES, _Resolver, resolve)
from syntax_client import EXECUTABLE, parse
from typst_lint import digest

STYLE = (b'#import "@local/text-utils:0.1.2": paragraph-tab as marker, '
         b'apply-paragraph-tabs as tabs\n#let saved-marker = marker\n#let count = 1\n')
MAIN = (b'#import "styles.typ": saved-marker as paragraph_tab, tabs as apply-paragraph-tabs\n'
        b'#show: apply-paragraph-tabs\n#set text(lang: "en")\n'
        b'#paragraph_tab\ntherefore result.\n')


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class FacadeProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loaded = load_packages(TOOL.parents[1], (TEXT_UTILS, PROJECT_REFERENCES))
        cls.package = next(p for p in cls.loaded.sources if p.import_path == TEXT_UTILS)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='typst-facade-provenance-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def put(self, path, source):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source)

    def capture(self, source=MAIN, style=STYLE):
        self.put('styles.typ', style)
        self.put('main.typ', source)
        return graph.load_import_graph(self.root, (('main.typ', source),))

    def analyze(self, context):
        return facade.FacadeAnalyzer(context, self.loaded.sources)

    def resolve(self, source=MAIN, context=None, packages=None, path='main.typ'):
        tree = parse(source)
        self.assertFalse(tree.nodes[0].errors, source)
        result = resolve(tree, packages=self.loaded.sources if packages is None else packages,
                         import_graph=context, source_path=path)
        return tree, result

    def last(self, result, raw=b'paragraph_tab', kind='Ident'):
        tree, resolved = result
        return [resolved.facts[i] for i, n in enumerate(tree.nodes)
                if n.kind == kind and tree.source[n.start:n.end] == raw][-1]

    def test_exact_initializer_certificate_and_complete_same_value_identity(self):
        result = facade.certify_initializer(self.package)
        self.assertTrue(result.accepted, result.reason)
        self.assertEqual(len(result.exports), 6)
        exports = dict(result.exports)
        self.assertEqual(exports['paragraph-tab'], exports['paragraph_tab'])
        origin = exports['paragraph_tab'].package_origin
        self.assertEqual((origin.import_path, origin.source_identity, origin.source_sha256),
                         (TEXT_UTILS, self.package.source_identity, TEXT_UTILS_SOURCE_SHA256))
        self.assertEqual({p.namespace for p in self.loaded.evidence}, {'local'})
        for package in (None, replace(self.package, source=self.package.source + b'\n'),
                        replace(self.package, source_identity=''),
                        replace(self.package, import_path='@preview/text-utils:0.1.2'),
                        replace(self.package, artifacts=[]),
                        next(p for p in self.loaded.sources if p.import_path == PROJECT_REFERENCES)):
            with self.subTest(package=package and package.import_path):
                self.assertFalse(facade.certify_initializer(package).accepted)

    def test_initializer_shape_is_checked_in_addition_to_hash_and_metadata(self):
        # Real alternate ASTs verify the structural defense independently of
        # the exact-byte gate. They are not invented completeness annotations.
        variants = (
            self.package.source.replace(b'(body) = {', b'(body: read("x")) = {', 1),
            self.package.source.replace(b'#let paragraph_tab = paragraph-tab',
                                        b'#let paragraph_tab = (paragraph-tab)', 1),
            self.package.source + b'\n#read("other.typ")\n',
        )
        for source in variants:
            tree = parse(source)
            self.assertFalse(tree.nodes[0].errors)
            with self.subTest(source=source[-40:]), patch.object(facade, 'parse', return_value=tree):
                self.assertFalse(facade.certify_initializer(self.package).accepted)
        contracts = tuple(replace(p, version='0.1.3') if p.import_path == TEXT_UTILS else p
                          for p in facade.REVIEWED_PACKAGES)
        with patch.object(facade, 'REVIEWED_PACKAGES', contracts):
            self.assertFalse(facade.certify_initializer(self.package).accepted)

    def test_complete_export_map_retains_local_and_package_identities(self):
        context = self.capture()
        evidence = self.analyze(context).analyze('styles.typ')
        self.assertTrue(evidence.accepted, evidence.reason)
        exports = dict(evidence.exports)
        self.assertEqual(set(exports), {'marker', 'tabs', 'saved-marker', 'count'})
        self.assertEqual(exports['marker'], exports['saved-marker'])
        self.assertEqual(exports['marker'].package_origin.canonical_name, 'paragraph_tab')
        self.assertIsNone(exports['count'].package_origin)
        self.assertEqual((exports['count'].path, exports['count'].source_sha256),
                         ('styles.typ', digest(STYLE)))
        self.assertEqual(evidence.depth, 2)  # Package leaves count.
        self.assertFalse(hasattr(evidence, 'pure'))
        # This capability never expands the old import-reordering authority.
        self.assertFalse(graph.GraphAnalyzer(context).module('styles.typ').pure)

    def test_multihop_aliases_have_actual_bindings_language_and_marker_availability(self):
        self.put('leaf.typ', STYLE)
        middle = (b'#import "leaf.typ": saved-marker as p, tabs as t\n'
                  b'#let saved-marker = p\n#let tabs = t\n')
        context = self.capture(style=middle)
        evidence = self.analyze(context).analyze('styles.typ')
        self.assertTrue(evidence.accepted, evidence.reason)
        self.assertEqual(evidence.dependencies, (facade.CapturedIdentity('leaf.typ', digest(STYLE)),))
        result = self.resolve(context=context)
        marker = self.last(result).binding
        self.assertEqual((marker.kind, marker.name, marker.package), ('api', 'paragraph_tab', TEXT_UTILS))
        prose = self.last(result, b'therefore result.', 'Text')
        self.assertTrue(prose.prose_safe)
        self.assertEqual(prose.effective_language, 'en')
        self.assertEqual(len(result[1].paragraph_insertion_proof.available), 1)

    def test_unicode_relative_consumer_paths_use_captured_literal_edges(self):
        source = MAIN.replace(b'styles.typ', '서식.typ'.encode())
        self.put('서식.typ', STYLE)
        context = self.capture(source)
        self.assertEqual(self.last(self.resolve(source, context)).binding.name, 'paragraph_tab')

    def test_literal_values_never_acquire_package_or_builtin_identity(self):
        for style in (b'#let saved-marker = "\xef\x80\x80"\n#let tabs = 1\n',
                      b'#let saved-marker = "paragraph_tab"\n#let tabs = false\n'):
            context = self.capture(style=style)
            evidence = self.analyze(context).analyze('styles.typ')
            self.assertTrue(evidence.accepted, evidence.reason)
            self.assertIsNone(self.last(self.resolve(context=context)).binding)
        main = (b'#import "styles.typ": text\n#set text(lang: "en")\ntherefore result.\n')
        context = self.capture(main, b'#let text = 1\n')
        result = self.resolve(main, context)
        self.assertIsNone(self.last(result, b'set text(lang: "en")', 'SetRule').binding)
        self.assertIsNone(self.last(result, b'therefore result.', 'Text').effective_language)

    def test_missing_export_refuses_whole_import_and_swapped_kinds_do_not_install_show(self):
        source = MAIN.replace(b'tabs as apply-paragraph-tabs', b'absent as apply-paragraph-tabs')
        context = self.capture(source)
        self.assertIsNone(self.last(self.resolve(source, context)).binding)
        swapped = MAIN.replace(b'saved-marker as paragraph_tab, tabs as apply-paragraph-tabs',
                               b'tabs as paragraph_tab, saved-marker as apply-paragraph-tabs')
        context = self.capture(swapped)
        result = self.resolve(swapped, context)
        self.assertEqual(self.last(result).binding.name, 'apply-paragraph-tabs')
        self.assertFalse(self.last(result, b'therefore result.', 'Text').prose_safe)
        self.assertFalse(result[1].paragraph_insertion_proof.available)

    def test_no_package_no_graph_wrong_path_and_stale_bytes_refuse(self):
        context = self.capture()
        for kwargs in ({'context': None}, {'context': context, 'packages': ()},
                       {'context': context, 'path': 'styles.typ'},
                       {'context': context, 'path': 'absent.typ'},
                       {'context': context, 'path': None},
                       {'context': replace(context, selected=())}):
            with self.subTest(kwargs=kwargs.keys()):
                self.assertIsNone(self.last(self.resolve(**kwargs)).binding)
        stale = MAIN + b'\n'
        self.assertIsNone(self.last(self.resolve(stale, context)).binding)
        bad_package = replace(self.package, source=self.package.source + b'\n')
        self.assertIsNone(self.last(self.resolve(context=context, packages=(bad_package,))).binding)

    def test_source_path_without_graph_preserves_existing_direct_resolver_facts(self):
        source = (b'#import "@local/text-utils:0.1.2": paragraph_tab\n#paragraph_tab\n')
        tree = parse(source)
        plain = resolve(tree, packages=self.loaded.sources)
        with_path = resolve(tree, packages=self.loaded.sources, source_path='main.typ')
        self.assertEqual(plain, with_path)

    def test_direct_imports_never_initialize_or_certify_facade_capability(self):
        source = (b'#import "@local/text-utils:0.1.2": paragraph_tab\n#paragraph_tab\n')
        context = self.capture(source)
        with patch.object(facade, 'FacadeAnalyzer', wraps=facade.FacadeAnalyzer) as initialize, \
             patch.object(facade, 'certify_initializer', wraps=facade.certify_initializer) as certify:
            result = self.resolve(source, context)
        self.assertEqual(self.last(result).binding.name, 'paragraph_tab')
        initialize.assert_not_called()
        certify.assert_not_called()
        # Unsupported consumer forms keep their old refusal/invalidation path
        # and do not initialize a capability that cannot certify those forms.
        for request in (b'#import "styles.typ": *\n', b'#import "styles.typ" as u\n'):
            context = self.capture(request)
            with patch.object(facade, 'FacadeAnalyzer', wraps=facade.FacadeAnalyzer) as initialize:
                self.resolve(request, context)
            initialize.assert_not_called()

    def test_relative_named_consumers_initialize_once_and_validate_exact_source(self):
        source = (b'#import "styles.typ": saved-marker as p\n'
                  b'#import "styles.typ": saved-marker as paragraph_tab\n#paragraph_tab\n')
        context = self.capture(source)
        with patch.object(facade, 'FacadeAnalyzer', wraps=facade.FacadeAnalyzer) as initialize, \
             patch.object(facade, 'certify_initializer', wraps=facade.certify_initializer) as certify:
            result = self.resolve(source, context)
        self.assertEqual(self.last(result).binding.name, 'paragraph_tab')
        self.assertEqual(initialize.call_count, 1)
        self.assertEqual(certify.call_count, 1)
        with patch.object(facade, 'parse', side_effect=AssertionError('Stale context cannot parse facades')):
            self.assertIsNone(self.last(self.resolve(source + b'\n', context)).binding)

    def test_captured_source_parse_refusal_is_never_reparsed(self):
        # Capture genuine malformed dependency bytes, then make any facade
        # reparse fail the test. The same source-level path covers adapter
        # resource-limit failures without generating a large slow fixture.
        context = self.capture(style=b'#let =\n')
        self.assertTrue(any(u.path == 'styles.typ' and not u.request for u in context.unavailable))
        analyzer = self.analyze(context)
        with patch.object(facade, 'parse', side_effect=AssertionError('Known captured parse refusal')) as parser:
            for _ in range(2):
                evidence = analyzer.analyze('styles.typ')
                self.assertFalse(evidence.accepted)
                self.assertEqual(evidence.reason, 'Captured facade syntax or import request is unavailable')
            result = self.resolve(context=context)
        parser.assert_not_called()
        self.assertIsNone(self.last(result).binding)
        self.assertFalse(analyzer.cache)

    def test_alias_only_marker_is_not_canonical_insertion_spelling(self):
        source = MAIN.replace(b'saved-marker as paragraph_tab', b'saved-marker as p').replace(b'#paragraph_tab', b'#p')
        context = self.capture(source)
        result = self.resolve(source, context)
        self.assertEqual(self.last(result, b'p').binding.name, 'paragraph_tab')
        self.assertFalse(result[1].paragraph_insertion_proof.available)

    def test_shadowing_and_mutation_invalidate_only_later_marker_proof(self):
        for fragment in (b'#let paragraph_tab = 1\n', b'#{ paragraph_tab = 1 }\n'):
            source = MAIN + fragment + b'#paragraph_tab\ntherefore later.\n'
            context = self.capture(source)
            result = self.resolve(source, context)
            self.assertEqual(len(result[1].paragraph_insertion_proof.available), 1)
            self.assertIsNone(self.last(result).binding)
        source = MAIN + b'#let apply-paragraph-tabs = 1\n#paragraph_tab\ntherefore later.\n'
        result = self.resolve(source, self.capture(source))
        self.assertEqual(len(result[1].paragraph_insertion_proof.available), 2)

    def test_consumer_wildcard_invalidates_existing_names_and_builtins(self):
        source = (b'#import "@local/text-utils:0.1.2": paragraph_tab\n'
                  b'#import "styles.typ": *\n#set text(lang: "en")\n#paragraph_tab\ntherefore result.\n')
        result = self.resolve(source, self.capture(source))
        self.assertIsNone(self.last(result).binding)
        self.assertIsNone(self.last(result, b'set text(lang: "en")', 'SetRule').binding)

    def test_all_unused_unsafe_statements_and_unsupported_imports_refuse(self):
        fragments = (
            b'#let unused = read("x")\n', b'#let unused = (marker)\n',
            b'#let unused = []\n', b'#let unused(body) = body\n',
            b'#let unused(body: read("x")) = body\n', b'#let unused = later\n#let later = 1\n',
            b'#let count = 2\n', b'#let (a, b) = (1, 2)\n',
            b'#include "other.typ"\n', b'#set text(lang: "en")\n',
            b'#show: tabs\n', b'output\n', b'#{ count = 2 }\n',
            b'#for x in (1, 2) {}\n', b'#if true {}\n',
            b'#import "@local/text-utils:0.1.2": *\n',
            b'#import "@local/text-utils:0.1.2" as u\n',
            b'#import "@local/text-utils:0.1.2": u.paragraph_tab\n',
            b'#import ("other.typ"): a\n', b'#import "missing.typ": a\n',
            b'#import "@local/project-references:0.1.0": project-ref\n',
        )
        for fragment in fragments:
            with self.subTest(fragment=fragment):
                context = self.capture(style=STYLE + fragment)
                self.assertFalse(self.analyze(context).analyze('styles.typ').accepted)
                self.assertIsNone(self.last(self.resolve(context=context)).binding)

    def test_cycles_and_cached_total_depth_and_ancestry_refuse(self):
        self.put('a.typ', b'#import "b.typ": saved-marker, tabs\n')
        self.put('b.typ', b'#import "a.typ": saved-marker, tabs\n')
        context = self.capture(style=b'#import "a.typ": saved-marker, tabs\n')
        evidence = self.analyze(context).analyze('styles.typ')
        self.assertFalse(evidence.accepted)
        self.assertIn('Cyclic', evidence.reason)
        self.put('leaf.typ', STYLE)
        context = self.capture(style=b'#import "leaf.typ": saved-marker, tabs\n')
        analyzer = self.analyze(context)
        with patch.object(graph, 'MAX_GRAPH_DEPTH', 3):
            self.assertTrue(analyzer.analyze('styles.typ').accepted)
            evidence = analyzer.analyze('styles.typ', ('main.typ',))
            self.assertFalse(evidence.accepted)
            self.assertIn('depth', evidence.reason)
        evidence = analyzer.analyze('styles.typ', ('leaf.typ',))
        self.assertFalse(evidence.accepted)
        self.assertIn('Cyclic', evidence.reason)
        analyzer = self.analyze(self.capture())
        with patch.object(graph, 'MAX_GRAPH_DEPTH', 1):
            self.assertFalse(analyzer.analyze('styles.typ').accepted)

    def test_invalid_graph_and_budget_inputs_refuse_without_reads(self):
        context = self.capture()
        variants = (None, replace(context, sources=list(context.sources)),
                    replace(context, sources=context.sources + (context.sources[0],)),
                    replace(context, edges=context.edges + context.edges),
                    replace(context, edges=(replace(context.edges[0], target='other.typ'),)),
                    replace(context, unavailable=context.unavailable +
                            (graph.ImportUnavailable('styles.typ', '', 'Unavailable syntax'),)))
        for value in variants:
            with self.subTest(value=type(value)):
                self.assertFalse(self.analyze(value).analyze('styles.typ').accepted)
        for name, limit in (('MAX_GRAPH_FILES', 1), ('MAX_GRAPH_BYTES', 1),
                            ('MAX_GRAPH_EDGES', 1), ('MAX_INPUT', 1)):
            with self.subTest(name=name), patch.object(graph, name, limit):
                self.assertFalse(self.analyze(context).analyze('styles.typ').accepted)
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('No filesystem lookup')):
            self.assertTrue(self.analyze(context).analyze('styles.typ').accepted)
        for packages in ([self.package], (self.package, self.package),
                         (replace(self.package, source=bytearray(self.package.source)),),
                         (replace(self.package, source_identity=''),)):
            with self.subTest(packages=type(packages)):
                self.assertFalse(facade.FacadeAnalyzer(context, packages).analyze('styles.typ').accepted)

    def test_immutable_evidence_cache_history_and_rebased_candidate_context(self):
        context = self.capture()
        analyzer = self.analyze(context)
        evidence = analyzer.analyze('styles.typ')
        with self.assertRaises(FrozenInstanceError):
            evidence.accepted = False
        with self.assertRaises(FrozenInstanceError):
            dict(evidence.exports)['marker'].name = 'changed'
        modified_map = dict(evidence.exports)
        modified_map.clear()
        self.put('styles.typ', STYLE + b'unsafe output')
        self.assertEqual(analyzer.analyze('styles.typ'), evidence)
        candidate = MAIN.replace(b'therefore result.', b'Therefore result.')
        rebased = graph.rebase_graph(context, 'main.typ', MAIN, candidate)
        self.assertIsNot(rebased, context)
        result = self.resolve(candidate, rebased)
        self.assertEqual(self.last(result).binding.name, 'paragraph_tab')
        self.assertEqual(result[1].source_sha256, digest(candidate))
        self.assertIsNone(self.last(self.resolve(candidate, context)).binding)

    def test_package_binding_translation_checks_kind_hash_and_source_identity(self):
        context = self.capture()
        for change in ({'kind': 'module'}, {'name': 'highlighted'}, {'package': PROJECT_REFERENCES},
                       {'source_identity': 'different-copy'}, {'source_sha256': '0' * 64}):
            original = _Resolver.package_binding
            def substituted(resolver, path, name):
                binding = original(resolver, path, name)
                return replace(binding, **change) if binding and path == TEXT_UTILS else binding
            with self.subTest(change=change), patch.object(_Resolver, 'package_binding', substituted):
                self.assertIsNone(self.last(self.resolve(context=context)).binding)


if __name__ == '__main__':
    unittest.main()
