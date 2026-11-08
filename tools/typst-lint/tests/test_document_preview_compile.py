"""Private preview compiler integration with owned sources and resources."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_preview_compile as compiler
import document_resources as resources
import document_sequence_preview as sequence
import include_context
from syntax_client import EXECUTABLE
from typst_lint import SafetyError, load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class DocumentPreviewCompileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project, self.packages, self.cache = (self.root / name for name in ('project', 'packages', 'cache'))
        for path in (self.project, self.packages, self.cache):
            path.mkdir()
        main = b'#import "@local/sample:0.1.0": text-value\n#text-value\n#include "child.typ"\n'
        self.child = b'Thus $ x^i $\n\nHence.\n'
        (self.project / 'main.typ').write_bytes(main)
        (self.project / 'child.typ').write_bytes(self.child)
        package = self.packages / 'local/sample/0.1.0'
        package.mkdir(parents=True)
        (package / 'typst.toml').write_bytes(b'[package]\nname = "sample"\nversion = "0.1.0"\nentrypoint = "lib.typ"\n')
        (package / 'lib.typ').write_bytes(b'#let text-value = [Hello.]\n')
        self.context = include_context.load_include_context(self.project, (('main.typ', main),))
        self.catalog = load_rules()[0]['rules']
        self.records = [r for s in self.context.sources
                        if (r := sequence.preview(s.tree, s.path, self.catalog)) is not None]
        requests = [resources.ResourceRequest('project', path, 'source') for path in ('main.typ', 'child.typ')]
        requests += [resources.ResourceRequest('packages', f'local/sample/0.1.0/{name}', 'package')
                     for name in ('typst.toml', 'lib.typ')]
        self.snapshot = resources.capture({'project': str(self.project), 'packages': str(self.packages),
                                           'cache': str(self.cache)}, requests)

    def test_bad_plan_or_deadline_refuses_before_compiler_capture(self):
        with patch.object(compiler.runtime, '_capture_compiler') as capture:
            with self.assertRaises(SafetyError):
                compiler.compile_preview(self.context, [], self.catalog, self.snapshot, entrypoint='main.typ')
            for value in (0, -1, float('nan'), float('inf'), True, 121):
                with self.subTest(deadline=value), self.assertRaises(SafetyError):
                    compiler.compile_preview(self.context, self.records, self.catalog, self.snapshot,
                                             entrypoint='main.typ', deadline_seconds=value)
            capture.assert_not_called()

    def test_pre_cancelled_preview_refuses_before_compiler_capture(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(compiler.runtime, '_capture_compiler') as capture:
            with self.assertRaises(compiler.runtime.CompilerFailure) as error:
                compiler.compile_preview(self.context, self.records, self.catalog, self.snapshot,
                                         entrypoint='main.typ', cancel_event=cancel)
            self.assertEqual(error.exception.kind, compiler.runtime.CompilerFailureKind.CANCELLED)
            capture.assert_not_called()

    def test_workspace_budget_refuses_before_compiler_capture(self):
        with patch.object(compiler, 'MAX_PREVIEW_BYTES', 1), \
             patch.object(compiler.runtime, '_capture_compiler') as capture:
            with self.assertRaisesRegex(SafetyError, 'workspace budget'):
                compiler.compile_preview(self.context, self.records, self.catalog, self.snapshot,
                                         entrypoint='main.typ')
            capture.assert_not_called()

    def test_workspace_guard_rejects_unexpected_entries_and_symlinks(self):
        workspace = self.root / 'private'
        workspace.mkdir(mode=0o700)
        allowed = workspace / 'source.typ'
        allowed.write_bytes(b'source')
        allowed.chmod(0o600)
        compiler._workspace_guard(workspace, {allowed}, set(), {})
        unexpected = workspace / 'unexpected'
        unexpected.write_bytes(b'x')
        unexpected.chmod(0o600)
        with self.assertRaises(SafetyError):
            compiler._workspace_guard(workspace, {allowed}, set(), {})
        unexpected.unlink()
        unexpected.symlink_to(allowed)
        with self.assertRaises(SafetyError):
            compiler._workspace_guard(workspace, {allowed, unexpected}, set(), {})

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with the reviewed Typst compiler')
    def test_owned_include_and_package_compile_without_source_writes(self):
        result = compiler.compile_preview(self.context, self.records, self.catalog, self.snapshot,
                                          entrypoint='main.typ', deadline_seconds=30)
        self.assertTrue(result.pdfs_identical)
        self.assertTrue(result.candidate_pdf.startswith(b'%PDF-'))
        self.assertEqual(result.dependency_count, 4)
        self.assertFalse(result.source_files_modified or result.dependency_closure_proved
                         or result.publication_authority)
        self.assertEqual((self.project / 'child.typ').read_bytes(), self.child)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with the reviewed Typst compiler')
    def test_leading_dash_entrypoint_is_passed_as_a_literal_path(self):
        source = (self.project / 'main.typ').read_bytes()
        (self.project / '-main.typ').write_bytes(source)
        context = include_context.load_include_context(self.project, (('-main.typ', source),))
        records = [r for s in context.sources if (r := sequence.preview(s.tree, s.path, self.catalog)) is not None]
        requests = [resources.ResourceRequest('project', path, 'source') for path in ('-main.typ', 'child.typ')]
        requests += [resources.ResourceRequest('packages', f'local/sample/0.1.0/{name}', 'package')
                     for name in ('typst.toml', 'lib.typ')]
        snapshot = resources.capture({'project': str(self.project), 'packages': str(self.packages),
                                      'cache': str(self.cache)}, requests)
        result = compiler.compile_preview(context, records, self.catalog, snapshot,
                                          entrypoint='-main.typ', deadline_seconds=30)
        self.assertTrue(result.pdfs_identical)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with the reviewed Typst compiler')
    def test_sandbox_denies_network_and_writes_outside_private_workspace(self):
        private = self.root / 'private'
        private.mkdir()
        outside = self.root / 'outside'
        profile = (f'(version 1)(allow default)(deny network*)(deny file-write*)'
                   f'(allow file-write* (subpath "{private}"))')
        write = subprocess.run([str(compiler.SANDBOX), '-p', profile, '/usr/bin/touch', str(outside)],
                               capture_output=True, timeout=10)
        self.assertNotEqual(write.returncode, 0)
        self.assertFalse(outside.exists())
        outside_dir = self.root / 'outside-directory'
        outside_dir.mkdir()
        (private / 'link').symlink_to(outside_dir, target_is_directory=True)
        escaped = outside_dir / 'escaped'
        linked_write = subprocess.run([str(compiler.SANDBOX), '-p', profile,
                                       '/usr/bin/touch', str(private / 'link' / 'escaped')],
                                      capture_output=True, timeout=10)
        self.assertNotEqual(linked_write.returncode, 0)
        self.assertFalse(escaped.exists())
        network = subprocess.run([str(compiler.SANDBOX), '-p', profile, sys.executable, '-c',
                                  'import socket; s=socket.socket(); s.settimeout(2); s.connect(("1.1.1.1",443))'],
                                 capture_output=True, timeout=10)
        self.assertNotEqual(network.returncode, 0)
        self.assertIn(b'Operation not permitted', network.stderr)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with the reviewed Typst compiler')
    def test_changed_dynamic_dependency_membership_is_refused(self):
        main = (b'#let source = read("child.typ")\n'
                b'#if source.contains("x^(i)") [#include "extra.typ"] else [#include "other.typ"]\n'
                b'#include "child.typ"\n')
        (self.project / 'main.typ').write_bytes(main)
        (self.project / 'extra.typ').write_bytes(b'Extra.\n')
        (self.project / 'other.typ').write_bytes(b'Other.\n')
        context = include_context.load_include_context(self.project, (('main.typ', main),))
        records = [r for s in context.sources if (r := sequence.preview(s.tree, s.path, self.catalog)) is not None]
        requests = [resources.ResourceRequest('project', s.path, 'source') for s in context.sources]
        snapshot = resources.capture({'project': str(self.project), 'packages': str(self.packages),
                                      'cache': str(self.cache)}, requests)
        with self.assertRaisesRegex(SafetyError, 'dependency membership changed'):
            compiler.compile_preview(context, records, self.catalog, snapshot, entrypoint='main.typ',
                                     deadline_seconds=30)
        self.assertEqual((self.project / 'child.typ').read_bytes(), self.child)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with the reviewed Typst compiler')
    def test_missing_captured_package_file_cannot_produce_a_pdf(self):
        requests = [resources.ResourceRequest('project', path, 'source') for path in ('main.typ', 'child.typ')]
        requests.append(resources.ResourceRequest('packages', 'local/sample/0.1.0/typst.toml', 'package'))
        snapshot = resources.capture({'project': str(self.project), 'packages': str(self.packages),
                                      'cache': str(self.cache)}, requests)
        with self.assertRaises((SafetyError, compiler.runtime.CompilerFailure)):
            compiler.compile_preview(self.context, self.records, self.catalog, snapshot,
                                     entrypoint='main.typ', deadline_seconds=15)
        self.assertEqual((self.project / 'child.typ').read_bytes(), self.child)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with the reviewed Typst compiler')
    def test_corrupted_private_compiler_or_input_is_refused_before_launch(self):
        original_write = compiler.runtime._write_private
        for target_name, message in (('typst', 'compiler differs'), ('child.typ', 'input differs')):
            def corrupt(path, raw, mode):
                original_write(path, raw + b'corrupt' if path.name == target_name else raw, mode)
            with self.subTest(target=target_name), \
                 patch.object(compiler.runtime, '_write_private', side_effect=corrupt), \
                 patch.object(compiler.runtime, '_run') as run:
                with self.assertRaisesRegex(SafetyError, message):
                    compiler.compile_preview(self.context, self.records, self.catalog, self.snapshot,
                                             entrypoint='main.typ', deadline_seconds=30)
                run.assert_not_called()
