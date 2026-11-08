"""Read-only dependency discovery for private document previews."""
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_preview_discover as discovery
import document_preview_compile as compiler
import document_resources as resources
import document_sequence_preview as sequence
import include_context
from syntax_client import EXECUTABLE
from typst_lint import SafetyError, load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class DocumentPreviewDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project, self.packages, self.cache = (self.root / name for name in ('project', 'packages', 'cache'))
        for path in (self.project, self.packages, self.cache):
            path.mkdir()
        self.main = b'#import "@local/sample:0.1.0": text-value\n#text-value\n#include "child.typ"\n'
        self.child = b'Thus $ x^i $\n\nHence.\n'
        (self.project / 'main.typ').write_bytes(self.main)
        (self.project / 'child.typ').write_bytes(self.child)
        package = self.packages / 'local/sample/0.1.0'
        package.mkdir(parents=True)
        (package / 'typst.toml').write_bytes(b'[package]\nname = "sample"\nversion = "0.1.0"\nentrypoint = "lib.typ"\n')
        (package / 'lib.typ').write_bytes(b'#let text-value = [Hello.]\n')
        self.roots = {'project': str(self.project), 'packages': str(self.packages), 'cache': str(self.cache)}
        self.context = include_context.load_include_context(self.project, (('main.typ', self.main),))
        self.catalog = load_rules()[0]['rules']

    def test_invalid_seed_is_refused_before_compiler_capture(self):
        request = resources.ResourceRequest('project', '../escape', 'resource')
        with patch.object(discovery.runtime, '_capture_compiler') as capture:
            with self.assertRaises(SafetyError):
                discovery.discover(self.context, self.roots, [request], entrypoint='main.typ')
            capture.assert_not_called()

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with the reviewed Typst compiler')
    def test_discovery_and_captured_compile_match_without_source_writes(self):
        raw, observed, fonts, baseline_hash = discovery.discover(self.context, self.roots, [],
                                                                   entrypoint='main.typ', deadline_seconds=30)
        self.assertTrue(raw.endswith(b'\0'))
        self.assertEqual(len(observed), 4)
        self.assertFalse(fonts)
        requests = [resources.ResourceRequest(root, path, 'source' if root == 'project' else 'package')
                    for root, path in observed]
        snapshot = resources.capture(self.roots, requests)
        records = [r for s in self.context.sources if (r := sequence.preview(s.tree, s.path, self.catalog)) is not None]
        result = compiler.compile_preview(self.context, records, self.catalog, snapshot,
                                          entrypoint='main.typ', deadline_seconds=30,
                                          expected_dependencies=observed, expected_baseline_sha256=baseline_hash)
        self.assertTrue(result.pdfs_identical)
        self.assertEqual(result.baseline_sha256, baseline_hash)
        self.assertEqual((self.project / 'child.typ').read_bytes(), self.child)
        with self.assertRaisesRegex(SafetyError, 'differ from discovery'):
            compiler.compile_preview(self.context, records, self.catalog, snapshot,
                                     entrypoint='main.typ', deadline_seconds=30,
                                     expected_dependencies=(('project', 'main.typ'),))
        with self.assertRaisesRegex(SafetyError, 'baseline PDF differs'):
            compiler.compile_preview(self.context, records, self.catalog, snapshot,
                                     entrypoint='main.typ', deadline_seconds=30,
                                     expected_dependencies=observed, expected_baseline_sha256='0' * 64)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with the reviewed Typst compiler')
    def test_missing_preview_package_cannot_write_or_download_into_user_cache(self):
        source = b'#import "@preview/nonexistent-test-package:0.0.1": *\n'
        (self.project / 'main.typ').write_bytes(source)
        context = include_context.load_include_context(self.project, (('main.typ', source),))
        with self.assertRaises((SafetyError, compiler.runtime.CompilerFailure)):
            discovery.discover(context, self.roots, [], entrypoint='main.typ', deadline_seconds=15)
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)
