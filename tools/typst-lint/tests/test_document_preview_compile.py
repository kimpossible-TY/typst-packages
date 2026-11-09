"""Private preview compiler integration with owned sources and resources."""
from pathlib import Path
from dataclasses import replace
import os
import subprocess
import sys
import tempfile
import threading
import time
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

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with the reviewed Typst compiler')
    def test_conditional_profile_compiles_a_private_combined_fixture(self):
        source = (b'#import "@local/sample:0.1.0": proof\n'
                  b'#proof[Done.]\nNext.\n#block[$\n A #footnote[Reason.]\n\n B\n$]\n')
        (self.project / 'main.typ').write_bytes(source)
        package = self.packages / 'local/sample/0.1.0/lib.typ'
        package.write_bytes(b'#let proof(body) = block(body)\n')
        context = include_context.load_include_context(self.project, (('main.typ', source),))
        records = [r for item in context.sources
                   if (r := sequence.preview(item.tree, item.path, self.catalog, profile='conditional-gaps'))]
        self.assertEqual({step['rule_id'] for row in records for step in row['steps']},
                         {'pde.proof-trailing-gap', 'pde.footnote-trailing-gap'})
        requests = [resources.ResourceRequest('project', 'main.typ', 'source')]
        requests += [resources.ResourceRequest('packages', f'local/sample/0.1.0/{name}', 'package')
                     for name in ('typst.toml', 'lib.typ')]
        snapshot = resources.capture({'project': str(self.project), 'packages': str(self.packages),
                                      'cache': str(self.cache)}, requests)
        result = compiler.compile_preview(context, records, self.catalog, snapshot,
                                          entrypoint='main.typ', profile='conditional-gaps')
        self.assertTrue(result.pdfs_identical)
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with the reviewed Typst compiler')
    def test_conditional_punctuation_profile_compiles_a_private_combined_fixture(self):
        source = (b'#import "@local/sample:0.1.0": proof\n#block[\n'
                  b'#proof[Done.]\nNext.\n$\n A #footnote[Reason.]\n\n B\n$\n'
                  b'Claim. #footnote[Prose reason.]\n]\n')
        (self.project / 'main.typ').write_bytes(source)
        (self.packages / 'local/sample/0.1.0/lib.typ').write_bytes(b'#let proof(body) = block(body)\n')
        context = include_context.load_include_context(self.project, (('main.typ', source),))
        profile = 'conditional-gaps-and-punctuation'
        records = [r for item in context.sources
                   if (r := sequence.preview(item.tree, item.path, self.catalog, profile=profile))]
        self.assertEqual({step['rule_id'] for row in records for step in row['steps']},
                         {'pde.proof-trailing-gap', 'pde.footnote-trailing-gap',
                          'pde.prose-footnote-punctuation'})
        requests = [resources.ResourceRequest('project', 'main.typ', 'source')]
        requests += [resources.ResourceRequest('packages', f'local/sample/0.1.0/{name}', 'package')
                     for name in ('typst.toml', 'lib.typ')]
        snapshot = resources.capture({'project': str(self.project), 'packages': str(self.packages),
                                      'cache': str(self.cache)}, requests)
        result = compiler.compile_preview(context, records, self.catalog, snapshot,
                                          entrypoint='main.typ', profile=profile)
        self.assertTrue(result.pdfs_identical)
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)

    def test_pre_cancelled_preview_refuses_before_compiler_capture(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(compiler.runtime, '_capture_compiler') as capture:
            with self.assertRaises(compiler.runtime.CompilerFailure) as error:
                compiler.compile_preview(self.context, self.records, self.catalog, self.snapshot,
                                         entrypoint='main.typ', cancel_event=cancel)
            self.assertEqual(error.exception.kind, compiler.runtime.CompilerFailureKind.CANCELLED)
            capture.assert_not_called()

    def test_start_tracker_distinguishes_real_probe_compile_and_prelaunch_cancel(self):
        starts = compiler.PreviewCompilerStarts()
        kwargs = dict(cwd=self.root, environment={}, deadline=time.monotonic() + 10,
                      cancel_event=None, check_workspace=lambda: None, starts=starts)
        for purpose in ('document-preview-version', 'document-preview-baseline'):
            compiler._run_tracked([sys.executable, '-c', 'pass'], purpose=purpose, **kwargs)
        cancelled = threading.Event()
        cancelled.set()
        kwargs['cancel_event'] = cancelled
        with self.assertRaises(compiler.runtime.CompilerFailure) as error:
            compiler._run_tracked([sys.executable, '-c', 'pass'], purpose='document-preview-candidate', **kwargs)
        self.assertFalse(error.exception.invocation.started)
        self.assertTrue(starts.compiler_invoked() and starts.version_probe_invoked())
        self.assertEqual(starts.stages(), {'discovery': False, 'baseline': True, 'candidate': False})

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

    def test_sandbox_profile_refuses_unrepresentable_paths(self):
        for path in ('relative', '/tmp/with space', '/tmp/quote"here', '/tmp/new\nline'):
            with self.subTest(path=path), self.assertRaises(SafetyError):
                compiler._sandbox_profile(path)

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
    def test_warning_in_either_real_compile_stage_is_refused_with_evidence(self):
        real_run = compiler.runtime._run
        for stage in ('baseline', 'candidate'):
            def run(*args, **kwargs):
                invocation = real_run(*args, **kwargs)
                if invocation.purpose == f'document-preview-{stage}':
                    return replace(invocation, stderr=b'warning: isolated fixture\n')
                return invocation
            with self.subTest(stage=stage), patch.object(compiler.runtime, '_run', side_effect=run):
                with self.assertRaises(compiler.PreviewCompilerDiagnostics) as error:
                    compiler.compile_preview(self.context, self.records, self.catalog, self.snapshot,
                                             entrypoint='main.typ', deadline_seconds=30)
                self.assertEqual(error.exception.stage, stage)
                self.assertTrue(error.exception.invocation.started)
                self.assertEqual(error.exception.invocation.stderr, b'warning: isolated fixture\n')
                self.assertEqual((self.project / 'child.typ').read_bytes(), self.child)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with the reviewed Typst compiler')
    def test_warning_does_not_override_workspace_or_output_identity_refusal(self):
        real_run = compiler.runtime._run
        for stage in ('baseline', 'candidate'):
            for damage in ('unexpected-entry', 'replaced-output'):
                with self.subTest(stage=stage, damage=damage):
                    def run(*args, **kwargs):
                        invocation = real_run(*args, **kwargs)
                        if invocation.purpose == f'document-preview-{stage}':
                            workspace = Path(kwargs['cwd']).parent
                            if damage == 'unexpected-entry':
                                rogue = workspace / 'unexpected'
                                rogue.write_bytes(b'x')
                                rogue.chmod(0o600)
                            else:
                                output = workspace / 'output.pdf'
                                output.unlink()
                                output.write_bytes(b'replaced')
                                output.chmod(0o600)
                            return replace(invocation, stderr=b'warning: must not escape\n')
                        return invocation
                    with patch.object(compiler.runtime, '_run', side_effect=run):
                        with self.assertRaises(SafetyError) as error:
                            compiler.compile_preview(self.context, self.records, self.catalog, self.snapshot,
                                                     entrypoint='main.typ', deadline_seconds=30)
                    self.assertNotIsInstance(error.exception, compiler.PreviewCompilerDiagnostics)
                    self.assertIn('unexpected' if damage == 'unexpected-entry' else 'output identity',
                                  str(error.exception).lower())

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with the reviewed Typst compiler')
    def test_process_failure_checks_outputs_before_exposing_stderr(self):
        runtime = compiler.runtime
        real_run = runtime._run
        cases = ((runtime.CompilerFailureKind.PROCESS_EXIT, 'replaced-output', True),
                 (runtime.CompilerFailureKind.CANCELLED, 'unexpected-entry', True),
                 (runtime.CompilerFailureKind.TIMEOUT, 'none', True),
                 (runtime.CompilerFailureKind.TIMEOUT, 'replaced-output', None))
        for stage in ('baseline', 'candidate'):
            for kind, damage, started in cases:
                with self.subTest(stage=stage, kind=kind, started=started):
                    failure = []
                    def run(*args, **kwargs):
                        invocation = real_run(*args, **kwargs)
                        if invocation.purpose == f'document-preview-{stage}':
                            workspace = Path(kwargs['cwd']).parent
                            if damage == 'unexpected-entry':
                                rogue = workspace / 'unexpected'
                                rogue.write_bytes(b'x')
                                rogue.chmod(0o600)
                            elif damage == 'replaced-output':
                                output = workspace / 'output.pdf'
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
                                compiler.compile_preview(self.context, self.records, self.catalog, self.snapshot,
                                                         entrypoint='main.typ', deadline_seconds=30)
                            self.assertIs(error.exception, failure[0])
                        else:
                            with self.assertRaises(SafetyError) as error:
                                compiler.compile_preview(self.context, self.records, self.catalog, self.snapshot,
                                                         entrypoint='main.typ', deadline_seconds=30)
                            self.assertIn('unexpected' if damage == 'unexpected-entry' else 'output identity',
                                          str(error.exception).lower())
                            self.assertNotIn('private compiler output', str(error.exception))

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with the reviewed Typst compiler')
    def test_prelaunch_cancel_preserves_original_without_postlaunch_output_check(self):
        runtime = compiler.runtime
        real_run, real_guard = runtime._run, compiler._workspace_guard
        prelaunch = [False]
        invocation = runtime.Invocation('document-preview-baseline', (), '', (), None, b'', b'', started=False)
        failure = runtime.CompilerFailure('cancelled', invocation, kind=runtime.CompilerFailureKind.CANCELLED)
        def run(*args, **kwargs):
            if kwargs['purpose'] == 'document-preview-baseline':
                prelaunch[0] = True
                raise failure
            return real_run(*args, **kwargs)
        def guard(*args, **kwargs):
            if prelaunch[0]:
                raise AssertionError('postlaunch check on prelaunch cancellation')
            return real_guard(*args, **kwargs)
        with patch.object(runtime, '_run', side_effect=run), patch.object(compiler, '_workspace_guard', side_effect=guard):
            with self.assertRaises(runtime.CompilerFailure) as error:
                compiler.compile_preview(self.context, self.records, self.catalog, self.snapshot,
                                         entrypoint='main.typ', deadline_seconds=30)
        self.assertIs(error.exception, failure)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with the reviewed Typst compiler')
    def test_cleanup_safety_failure_is_never_replaced(self):
        runtime = compiler.runtime
        real_run = runtime._run
        def run(*args, **kwargs):
            if kwargs['purpose'] == 'document-preview-baseline':
                command = list(args[0])
                command.insert(command.index('compile') + 1, '--invalid-preview-test-option')
                return real_run(command, **kwargs)
            return real_run(*args, **kwargs)
        with patch.object(runtime, '_run', side_effect=run), \
             patch.object(runtime, '_kill', side_effect=OSError('synthetic cleanup failure')):
            with self.assertRaises(runtime.CompileSafetyError) as error:
                compiler.compile_preview(self.context, self.records, self.catalog, self.snapshot,
                                         entrypoint='main.typ', deadline_seconds=30)
        self.assertIn('cleanup did not complete', str(error.exception))
        self.assertIsInstance(error.exception.__cause__, OSError)

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
        profile = compiler._sandbox_profile(private)
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
