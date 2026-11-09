"""Explicit captured evidence reaches inspect-document without compilation."""
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_document_cetz_draw_evidence as fixtures
from test_document_cetz_detached_annotations import PREFIX, BODY
import document_cli
import document_resources
import document_footnote_evidence
from syntax_client import EXECUTABLE
from typst_lint import SafetyError


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class CetzAnnotationCliTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CetzDrawEvidenceTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        evidence = self.fixture.proof(PREFIX + BODY)
        snapshot = evidence._snapshot
        self.manifest = self.fixture.project.parent / 'resources.json'
        self.data = dict(roots=dict(snapshot.roots), files=[
            dict(root=x.request.root, path=x.request.path, role=x.request.role) for x in snapshot.files])
        self.write_manifest()
        self.options = dict(rule_set='pde', package_path=str(self.fixture.packages),
                            footnote_cache_path=str(self.fixture.cache), preview_conditional_scoped_calls=True,
                            cetz_annotation_manifest=str(self.manifest))

    def write_manifest(self):
        self.manifest.write_text(json.dumps(self.data))

    def test_real_entrypoint_outputs_only_conditional_hints(self):
        command = [sys.executable, str(fixtures.TOOL / 'typst-lint'), 'inspect-document',
                   '--root', str(self.fixture.project), '--entrypoint', 'main.typ', '--format', 'json',
                   '--rule-set', 'pde', '--package-path', str(self.fixture.packages),
                   '--footnote-cache-path', str(self.fixture.cache), '--preview-conditional-scoped-calls',
                   '--cetz-annotation-manifest', str(self.manifest)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2, result.stderr)
        report = json.loads(result.stdout)
        self.assertNotIn('error', report)
        self.assertFalse(report['compiler_invoked'])
        self.assertEqual(report['applied_fixes'], [])
        self.assertEqual(len(report['conditional_cetz_detached_annotations']['by_file'][0]['diagnostics']), 1)
        self.assertFalse(report['conditional_cetz_detached_annotations']['edit_permission'])
        self.assertNotIn('core.annotation-in-display', [row['rule_id'] for row in report['diagnostics']])

    def test_wrong_roots_or_oversized_manifest_refused(self):
        for key in ('project', 'packages', 'cache'):
            with self.subTest(key=key):
                old = self.data['roots'][key]
                self.data['roots'][key] = str(self.fixture.project.parent)
                self.write_manifest()
                with self.assertRaises(ValueError):
                    document_cli.report_document(str(self.fixture.project), 'main.typ', **self.options)
                self.data['roots'][key] = old
        self.write_manifest()
        with patch.object(document_resources, 'MAX_FILES', 1):
            with self.assertRaises(ValueError):
                document_cli.report_document(str(self.fixture.project), 'main.typ', **self.options)
        with patch.object(document_resources, 'MAX_TOTAL_BYTES', 8):
            with self.assertRaises(ValueError):
                document_cli.report_document(str(self.fixture.project), 'main.typ', **self.options)

    def test_opt_in_requires_the_namespace_and_scoped_options(self):
        for key in ('footnote_cache_path', 'preview_conditional_scoped_calls'):
            options = dict(self.options)
            options.pop(key)
            with self.subTest(key=key), self.assertRaises(ValueError):
                document_cli.report_document(str(self.fixture.project), 'main.typ', **options)
        options = dict(self.options)
        options.pop('cetz_annotation_manifest')
        report = document_cli.report_document(str(self.fixture.project), 'main.typ', **options)
        self.assertNotIn('conditional_cetz_detached_annotations', report)

    def test_source_change_between_graph_and_resource_capture_is_rejected(self):
        original_capture = document_resources.capture
        def changed(*args, **kwargs):
            path = self.fixture.project / 'main.typ'
            path.write_bytes(path.read_bytes() + b'\nchanged\n')
            return original_capture(*args, **kwargs)
        with patch.object(document_resources, 'capture', side_effect=changed):
            with self.assertRaises(SafetyError):
                document_cli.report_document(str(self.fixture.project), 'main.typ', **self.options)

    def test_tampered_snapshot_never_returns_partial_report(self):
        original = document_footnote_evidence._valid
        calls = 0
        def altered(proof):
            nonlocal calls
            calls += 1
            if calls >= 4:
                raise SafetyError('capture changed')
            return original(proof)
        with patch.object(document_footnote_evidence, '_valid', side_effect=altered):
            with self.assertRaises(SafetyError):
                document_cli.report_document(str(self.fixture.project), 'main.typ', **self.options)

    def test_combined_report_byte_limit_includes_other_sections(self):
        report = document_cli.report_document(str(self.fixture.project), 'main.typ', **self.options)
        section_size = len(json.dumps(report['conditional_cetz_detached_annotations'],
                                      ensure_ascii=False).encode('utf-8'))
        total_size = len(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8'))
        self.assertGreater(total_size, section_size + 1)
        with patch.object(document_cli, 'MAX_MODERN_REPORT_BYTES', section_size + 1):
            with self.assertRaisesRegex(ValueError, 'Document inspection report byte limit'):
                document_cli.report_document(str(self.fixture.project), 'main.typ', **self.options)


if __name__ == '__main__':
    unittest.main()
