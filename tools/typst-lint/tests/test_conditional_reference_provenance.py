"""Conditional facade tracing with real pinned package snapshots."""
from dataclasses import replace
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
REPOSITORY = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
import conditional_reference_provenance as reference
import import_graph
import namespace_multiroot
import namespace_packages
from namespace_packages import load_namespace_packages
from package_loader import PackageInputError
from syntax_client import EXECUTABLE


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalReferenceProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / 'project'
        self.namespaces = self.base / 'namespaces'
        self.references = self.base / 'references'
        for directory in (self.project, self.namespaces, self.references):
            directory.mkdir()
        for package, version, root in (('text-utils', '0.1.2', self.namespaces),
                                       ('math-blocks', '0.2.0', self.namespaces),
                                       ('project-references', '0.1.0', self.references)):
            source = REPOSITORY / 'local' / package / version
            destination = root / 'local' / package / version
            destination.mkdir(parents=True)
            for filename in ('typst.toml', 'lib.typ'):
                shutil.copyfile(source / filename, destination / filename)

    def capture(self, facade, main=b'#import "Styles/styles.typ": *\n'):
        (self.project / 'Styles').mkdir(exist_ok=True)
        (self.project / 'Styles/styles.typ').write_bytes(facade)
        (self.project / 'main.typ').write_bytes(main)
        graph = import_graph.load_import_graph(self.project, (('main.typ', main),))
        namespaces = load_namespace_packages(self.namespaces)
        return reference.ConditionalReferenceProvenance(graph, namespaces, self.references)

    def test_reexport_from_pinned_math_blocks_across_relative_wildcard(self):
        analyzer = self.capture(b'#import "@local/math-blocks:0.2.0": *\n'
                                b'#import "@local/text-utils:0.1.2": *\n')
        result = analyzer.export('main.typ')
        self.assertEqual(result.status, 'CONDITIONAL', result.reason)
        self.assertFalse(result.edit_permission)
        self.assertEqual({item.path for item in result.source_hashes}, {'main.typ', 'Styles/styles.typ'})
        packages = {item.import_path for item in result.package_evidence}
        self.assertEqual(packages, {reference.MATH_BLOCKS, reference.PROJECT_REFERENCES,
                                    '@local/text-utils:0.1.2'})
        self.assertEqual([step.operation for step in result.route],
                         ['package-export', 'named-import', 'wildcard-import', 'wildcard-import'])
        self.assertIn('without later shadowing', result.conditions[-1])
        self.assertEqual(analyzer.package_path, str(self.references))
        self.assertEqual(len(analyzer.package_evidence), 1)
        self.assertEqual(analyzer.package_evidence[0].import_path, reference.PROJECT_REFERENCES)
        with self.assertRaises(AttributeError):
            analyzer.package_path = 'changed'

    def test_multiroot_collision_only_variant_keeps_helper_opaque(self):
        # Synthetic bytes exercise capability separation without redistributing
        # the user's installed text-utils source.
        synthetic = b'#let highlighted(body) = body\n#let paragraph_tab = none\n'
        source = self.namespaces / 'local/text-utils/0.1.2/lib.typ'
        source.write_bytes(synthetic)
        shutil.copytree(self.references / 'local/project-references',
                        self.namespaces / 'local/project-references')
        cache = self.base / 'cache'
        cache.mkdir()
        styles = self.project / 'Styles'
        styles.mkdir()
        (styles / 'styles.typ').write_bytes(b'#import "@local/text-utils:0.1.2": *\n'
                                            b'#import "@local/math-blocks:0.2.0": *\n')
        main = b'#import "Styles/styles.typ": *\n'
        (self.project / 'main.typ').write_bytes(main)
        graph = import_graph.load_import_graph(self.project, (('main.typ', main),))
        variant = replace(namespace_packages._TEXT_COLLISION_CONTRACT,
                          source_sha256=hashlib.sha256(synthetic).hexdigest())
        with patch.object(namespace_packages, '_TEXT_COLLISION_CONTRACT', variant):
            captured = namespace_multiroot.load_multiroot_capture(self.namespaces, cache)
            analyzer = reference.ConditionalReferenceProvenance(graph, captured, self.namespaces)
            result = analyzer.export('main.typ')
            self.assertEqual(result.status, 'CONDITIONAL', result.reason)
            self.assertFalse(result.edit_permission)
            self.assertIsNone(dict(analyzer._engine.module('main.typ', ()).exports)['highlighted'])
            # A serialization/reconstruction cannot mint the issued capture.
            forged = reference.ConditionalReferenceProvenance(graph, replace(captured), self.namespaces)
            self.assertEqual(forged.export('main.typ').status, 'UNAVAILABLE')

    def test_multiroot_refuses_reference_from_separate_or_second_root(self):
        cache = self.base / 'cache'
        cache.mkdir()
        main = b'#import "@local/math-blocks:0.2.0": *\n'
        (self.project / 'main.typ').write_bytes(main)
        graph = import_graph.load_import_graph(self.project, (('main.typ', main),))
        first = self.namespaces / 'local/project-references/0.1.0'
        first.mkdir(parents=True)
        (first / 'typst.toml').write_text('unreviewed')
        (first / 'lib.typ').write_text('#let project-ref = none')
        captured = namespace_multiroot.load_multiroot_capture(self.namespaces, cache)
        separate = reference.ConditionalReferenceProvenance(graph, captured, self.references).export('main.typ')
        self.assertEqual(separate.status, 'UNAVAILABLE')
        self.assertIn('first namespace root', separate.reason)
        shutil.rmtree(first.parent)
        shutil.copytree(self.references / 'local/project-references', cache / 'local/project-references')
        captured = namespace_multiroot.load_multiroot_capture(self.namespaces, cache)
        second = reference.ConditionalReferenceProvenance(graph, captured, cache).export('main.typ')
        self.assertEqual(second.status, 'UNAVAILABLE')
        self.assertIn('first namespace root', second.reason)

    def test_shadowing_and_unavailable_wildcard_refuse_promotion(self):
        before = b'#import "@local/math-blocks:0.2.0": *\n'
        for suffix in (b'#let project-ref = none\n',
                       b'#import "@preview/unknown:1.0.0": *\n',
                       b'#import "@preview/unknown:1.0.0": project-ref\n',
                       b'#{ project-ref = none }\n'):
            with self.subTest(suffix=suffix):
                result = self.capture(before + suffix).export('main.typ')
                self.assertEqual(result.status, 'UNAVAILABLE')
                self.assertFalse(result.edit_permission or result.route)

    def test_named_alias_and_direct_package_import(self):
        for facade in (b'#import "@local/math-blocks:0.2.0": project-ref as cite\n',
                       b'#import "@local/math-blocks:0.2.0" as cite: project-ref as cite\n',
                       b'#import "@local/project-references:0.1.0": project-ref as cite\n',
                       b'#import "@local/project-references:0.1.0": *\n#let cite = project-ref\n'):
            with self.subTest(facade=facade):
                result = self.capture(facade).export('main.typ', 'cite')
                self.assertEqual(result.status, 'CONDITIONAL', result.reason)
                self.assertEqual(result.route[0].path, reference.PROJECT_REFERENCES)

    def test_reference_named_import_binding_budget_refuses(self):
        names = ', '.join(f'project-ref as p{i}' for i in range(1025)).encode()
        facade = b'#import "@local/project-references:0.1.0": ' + names + b'\n'
        result = self.capture(facade).export('main.typ', 'p0')
        self.assertEqual(result.status, 'UNAVAILABLE')
        self.assertIn('binding limit', result.reason)

    def test_unreviewed_reference_source_is_refused(self):
        target = self.references / 'local/project-references/0.1.0/lib.typ'
        target.write_bytes(target.read_bytes() + b'\n')
        with self.assertRaises(PackageInputError):
            self.capture(b'#import "@local/math-blocks:0.2.0": *\n')

    def test_reference_evidence_survives_unavailable_export(self):
        analyzer = self.capture(b'#import "@preview/unknown:1.0.0": *\n')
        self.assertEqual(analyzer.export('main.typ').status, 'UNAVAILABLE')
        self.assertEqual(analyzer.package_path, str(self.references))
        self.assertEqual(len(analyzer.package_evidence[0].source_sha256), 64)

    def test_result_is_bound_to_capture_not_current_live_package(self):
        analyzer = self.capture(b'#import "@local/math-blocks:0.2.0": *\n')
        target = self.references / 'local/project-references/0.1.0/lib.typ'
        target.write_bytes(target.read_bytes() + b'\n')
        result = analyzer.export('main.typ')
        self.assertEqual(result.status, 'CONDITIONAL')
        self.assertTrue(any('not a filesystem lease' in condition for condition in result.conditions))
        self.assertFalse(result.edit_permission)

    def test_forged_namespace_capture_and_missing_graph_source_refuse(self):
        source = b'#import "@local/math-blocks:0.2.0": *\n'
        (self.project / 'main.typ').write_bytes(source)
        graph = import_graph.load_import_graph(self.project, (('main.typ', source),))
        namespaces = load_namespace_packages(self.namespaces)
        forged = replace(namespaces, packages=namespaces.packages[:-1])
        analyzer = reference.ConditionalReferenceProvenance(graph, forged, self.references)
        self.assertEqual(analyzer.export('main.typ').status, 'UNAVAILABLE')
        analyzer = reference.ConditionalReferenceProvenance(graph, namespaces, self.references)
        self.assertEqual(analyzer.export('not-captured.typ').status, 'UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
