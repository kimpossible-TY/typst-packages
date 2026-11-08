"""Conditional membership checks require issued snapshots and fresh traces."""
from dataclasses import replace
import gc
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_reference_catalog as check
import conditional_reference_provenance as provenance
import import_graph
import namespace_packages
from syntax_client import EXECUTABLE
from package_loader import PackageInputError
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ConditionalCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='conditional-catalog-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project, self.packages = self.root / 'project', self.root / 'packages'
        self.project.mkdir()
        fixture = TOOL / 'tests/fixtures/reviewed-packages/local'
        for name, version in (('project-references', '0.1.0'), ('math-blocks', '0.2.0'),
                              ('text-utils', '0.1.2')):
            shutil.copytree(fixture / name / version, self.packages / 'local' / name / version)
        self.raw = json.dumps({'schema': 1, 'projects': {'book': {
            'references': {'ok': {'page': 1, 'text': 'A'}}, 'ambiguous_labels': ['duplicate']}}}).encode()
        self.catalog = check.capture_catalog(self.raw, 'explicit-fixture.json')

    def session(self, body):
        source = b'#import "@local/project-references:0.1.0": project-ref\n' + body
        (self.project / 'main.typ').write_bytes(source)
        graph = import_graph.load_import_graph(self.project, (('main.typ', source),))
        capture = namespace_packages.load_namespace_packages(str(self.packages))
        return provenance.ConditionalReferenceProvenance(graph, capture, str(self.packages))

    def test_exact_membership_and_unresolved_values(self):
        body = (b'#project-ref("book", <ok>)\n#project-ref("other", <x>)\n'
                b'#project-ref("book", <duplicate>)\n#project-ref("book", <missing>)\n'
                b'#project-ref("book", target)\n#project-ref(..values)\n')
        result = check.analyze(self.session(body), 'main.typ', self.catalog)
        self.assertEqual(result.status, 'PARTIAL')
        self.assertEqual(result.checked_calls, 4)
        self.assertEqual(len(result.diagnostics), 3)
        self.assertEqual(result.catalog_sha256, digest(self.raw))
        self.assertTrue(any('no project' in d.explanation for d in result.diagnostics))
        self.assertTrue(any('ambiguous' in d.explanation for d in result.diagnostics))
        self.assertTrue(any('lacks label' in d.explanation for d in result.diagnostics))
        self.assertTrue(all(d.status == 'CONDITIONAL' and not d.edit_permission and d.route
                            for d in result.diagnostics))
        self.assertFalse(result.edit_permission or result.freshness_proved or result.runtime_selection_proved)
        self.assertEqual(len(result.trace.calls), 6)
        self.assertIs(result.argument_result.trace, result.trace)

    def test_suppression_and_shadow_remain_conditional(self):
        body = (b'// typst-lint: ignore-next core.project-ref-catalog-target -- deliberate\n'
                b'#project-ref("book", <missing>)\n')
        result = check.analyze(self.session(body), 'main.typ', self.catalog)
        self.assertFalse(result.diagnostics)
        self.assertEqual(len(result.suppressed), 1)
        self.assertEqual(result.checked_calls, 1)
        result = check.analyze(self.session(b'#let project-ref = none\n#project-ref("book", <missing>)'),
                               'main.typ', self.catalog)
        self.assertFalse(result.diagnostics or result.checked_calls)

    def test_issued_snapshots_cannot_be_forged_or_mutated(self):
        session = self.session(b'#project-ref("book", <ok>)')
        forged = replace(self.catalog)
        self.assertFalse(check.verify_catalog(forged))
        with self.assertRaises(InputError):
            check.analyze(session, 'main.typ', forged)
        object.__setattr__(self.catalog.catalog.projects[0], 'alias', 'changed')
        self.assertFalse(check.verify_catalog(self.catalog))
        with self.assertRaises(InputError):
            check.analyze(session, 'main.typ', self.catalog)

    def test_collected_capture_is_removed_from_issued_registry(self):
        capture = check.capture_catalog(self.raw, 'temporary')
        key = id(capture)
        self.assertIn(key, check._ISSUED)
        del capture
        gc.collect()
        self.assertNotIn(key, check._ISSUED)

    def test_invalid_catalogs_and_wrong_snapshot_type_refuse(self):
        for raw in (b'{}', b'{"schema":1,"schema":1,"projects":{}}', b'{"schema":1,"projects":{"x":{}}}'):
            with self.subTest(raw=raw), self.assertRaises(InputError):
                check.capture_catalog(raw, 'fixture')
        session = self.session(b'#project-ref("book", <ok>)')
        with self.assertRaises(InputError):
            check.analyze(session, 'main.typ', object())
        with self.assertRaises(InputError):
            check.analyze(object(), 'main.typ', self.catalog)

    def test_explicit_safe_catalog_path_and_source_preservation(self):
        path = self.root / 'catalog.json'
        path.write_bytes(self.raw)
        snapshot = check.capture_catalog_path(path)
        self.assertEqual(snapshot.catalog.source_identity, str(path))
        self.assertEqual(snapshot.catalog.sha256, digest(self.raw))
        path.write_bytes(b'changed after capture')
        result = check.analyze(self.session(b'#project-ref("book", <ok>)'), 'main.typ', snapshot)
        self.assertEqual(result.checked_calls, 1)
        self.assertFalse(result.freshness_proved or result.diagnostics)
        with self.assertRaises(InputError):
            check.capture_catalog_path('relative.json')
        link = self.root / 'linked.json'
        link.symlink_to(path)
        with self.assertRaises(PackageInputError):
            check.capture_catalog_path(link)

    def test_unicode_grouped_literals_and_nested_footnote(self):
        body = ('#block[한글 #project-ref(("book"), (<missing>))]\n'
                '$\n A #footnote[See #project-ref("book", <missing>)]\n$\n').encode()
        result = check.analyze(self.session(body), 'main.typ', self.catalog)
        self.assertEqual(len(result.diagnostics), 2)
        self.assertEqual(result.checked_calls, 2)
        self.assertTrue(any('footnote' in ' '.join(item.conditions).lower() for item in result.diagnostics))
        self.assertEqual(result.status, 'PARTIAL')

    def test_unknown_scope_and_invalid_call_do_not_fake_checks(self):
        body = (b'#import "unknown.typ": *\n#project-ref("book", <missing>)\n')
        result = check.analyze(self.session(body), 'main.typ', self.catalog)
        self.assertEqual(result.status, 'PARTIAL')
        self.assertEqual(result.checked_calls, 0)
        self.assertFalse(result.diagnostics)
        body = b'#project-ref("book", "not-a-label")\n#project-ref("book", <ok>, 3)\n'
        result = check.analyze(self.session(body), 'main.typ', self.catalog)
        self.assertEqual(result.status, 'PARTIAL')
        self.assertEqual(result.checked_calls, 0)

    def test_invalid_call_span_rejects_entire_result(self):
        session = self.session(b'#project-ref("book", <ok>)')
        traced = session.calls('main.typ')
        altered = replace(traced.calls[0], end=traced.calls[0].end - 1)
        with patch.object(provenance.ConditionalReferenceProvenance, 'calls',
                          return_value=replace(traced, calls=(altered,))):
            result = check.analyze(session, 'main.typ', self.catalog)
        self.assertEqual(result.status, 'UNAVAILABLE')
        self.assertFalse(result.diagnostics or result.checked_calls)


if __name__ == '__main__':
    unittest.main()
