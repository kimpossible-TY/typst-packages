"""Read-only dependency discovery for private document previews."""
from pathlib import Path
from dataclasses import replace
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
    def test_discovery_warning_requires_workspace_and_inode_checks(self):
        real_run = compiler.runtime._run
        for damage in ('none', 'unexpected-entry', 'replaced-output'):
            with self.subTest(damage=damage):
                def run(*args, **kwargs):
                    invocation = real_run(*args, **kwargs)
                    if invocation.purpose == 'document-discovery-compile':
                        output = Path(args[0][-1])
                        if damage == 'unexpected-entry':
                            rogue = output.parent / 'unexpected'
                            rogue.write_bytes(b'x')
                            rogue.chmod(0o600)
                        elif damage == 'replaced-output':
                            output.unlink()
                            output.write_bytes(b'replaced')
                            output.chmod(0o600)
                        return replace(invocation, stderr=b'warning: isolated fixture\n')
                    return invocation

                with patch.object(compiler.runtime, '_run', side_effect=run):
                    with self.assertRaises(SafetyError) as error:
                        discovery.discover(self.context, self.roots, [], entrypoint='main.typ', deadline_seconds=30)
                if damage == 'none':
                    self.assertIsInstance(error.exception, compiler.PreviewCompilerDiagnostics)
                    self.assertEqual(error.exception.stage, 'discovery')
                    self.assertEqual(error.exception.invocation.stderr, b'warning: isolated fixture\n')
                else:
                    self.assertNotIsInstance(error.exception, compiler.PreviewCompilerDiagnostics)
                    self.assertIn('unexpected' if damage == 'unexpected-entry' else 'output identity',
                                  str(error.exception).lower())
                self.assertEqual((self.project / 'child.typ').read_bytes(), self.child)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with the reviewed Typst compiler')
    def test_discovery_process_failure_checks_outputs_before_stderr(self):
        runtime = compiler.runtime
        real_run = runtime._run
        cases = ((runtime.CompilerFailureKind.PROCESS_EXIT, 'replaced-output', True),
                 (runtime.CompilerFailureKind.CANCELLED, 'unexpected-entry', True),
                 (runtime.CompilerFailureKind.TIMEOUT, 'none', True),
                 (runtime.CompilerFailureKind.TIMEOUT, 'replaced-output', None))
        for kind, damage, started in cases:
            with self.subTest(kind=kind, started=started, damage=damage):
                failure = []
                def run(*args, **kwargs):
                    invocation = real_run(*args, **kwargs)
                    if invocation.purpose == 'document-discovery-compile':
                        output = Path(args[0][-1])
                        if damage == 'unexpected-entry':
                            rogue = output.parent / 'unexpected'
                            rogue.write_bytes(b'x')
                            rogue.chmod(0o600)
                        elif damage == 'replaced-output':
                            output.unlink()
                            output.write_bytes(b'replaced')
                            output.chmod(0o600)
                        error = runtime.CompilerFailure('synthetic failure',
                            replace(invocation, stderr=b'private compiler output', started=started), kind=kind)
                        failure.append(error)
                        raise error
                    return invocation
                with patch.object(runtime, '_run', side_effect=run):
                    if damage == 'none':
                        with self.assertRaises(runtime.CompilerFailure) as error:
                            discovery.discover(self.context, self.roots, [], entrypoint='main.typ', deadline_seconds=30)
                        self.assertIs(error.exception, failure[0])
                    else:
                        with self.assertRaises(SafetyError) as error:
                            discovery.discover(self.context, self.roots, [], entrypoint='main.typ', deadline_seconds=30)
                        self.assertIn('unexpected' if damage == 'unexpected-entry' else 'output identity',
                                      str(error.exception).lower())
                        self.assertNotIn('private compiler output', str(error.exception))
                self.assertEqual((self.project / 'child.typ').read_bytes(), self.child)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with the reviewed Typst compiler')
    def test_discovery_prelaunch_cancel_preserves_original(self):
        runtime = compiler.runtime
        real_run, real_guard = runtime._run, compiler._workspace_guard
        prelaunch = [False]
        invocation = runtime.Invocation('document-discovery-compile', (), '', (), None, b'', b'', started=False)
        failure = runtime.CompilerFailure('cancelled', invocation, kind=runtime.CompilerFailureKind.CANCELLED)
        def run(*args, **kwargs):
            if kwargs['purpose'] == 'document-discovery-compile':
                prelaunch[0] = True
                raise failure
            return real_run(*args, **kwargs)
        def guard(*args, **kwargs):
            if prelaunch[0]:
                raise AssertionError('postlaunch check on prelaunch cancellation')
            return real_guard(*args, **kwargs)
        with patch.object(runtime, '_run', side_effect=run), patch.object(compiler, '_workspace_guard', side_effect=guard):
            with self.assertRaises(runtime.CompilerFailure) as error:
                discovery.discover(self.context, self.roots, [], entrypoint='main.typ', deadline_seconds=30)
        self.assertIs(error.exception, failure)

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
