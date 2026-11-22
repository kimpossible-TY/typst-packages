"""Original-only compilation is an explicit stream, not a lint pass or fix."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_original_compile_cli as cli
import document_preview_compile
import document_resources
import include_context
from syntax_client import EXECUTABLE
from typst_lint import SafetyError
import test_document_preview_cli as fixtures


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class OriginalCompileTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DocumentPreviewCliTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.output = self.f.project / 'existing.pdf'
        self.output.write_bytes(b'existing PDF sentinel')

    def command(self, *extra):
        return ['--root', str(self.f.project), '--entrypoint', 'main.typ',
                '--manifest', str(self.f.manifest), '--pdf-stdout', *extra]

    def capture(self, args, *, fake=None, fail=None, buffer=None):
        out, err = io.StringIO(), io.StringIO()
        out.buffer = io.BytesIO() if buffer is None else buffer
        value = fake or SimpleNamespace(pdf=b'%PDF-1.7\n%%EOF\n', pdf_sha256='a' * 64,
            dependency_count=1, unobserved_capture_count=0, system_font_files=0, compiler_sha256='b' * 64)
        with patch.object(document_preview_compile, 'compile_original', side_effect=fail, return_value=value) as compiler, \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cli.main(args)
        return status, out.buffer, json.loads(err.getvalue()), compiler

    def test_success_streams_only_pdf_and_reports_lint_incomplete(self):
        status, out, report, compiler = self.capture(self.command())
        self.assertEqual(status, 0, report)
        self.assertEqual(out.getvalue(), b'%PDF-1.7\n%%EOF\n')
        self.assertTrue(report['pdf_streamed'])
        self.assertFalse(report['lint_performed'] or report['global_coverage_complete']
                         or report['source_files_opened_for_write_by_tool'] or report['publication_authority']
                         or report['source_application_authority'] or report['dependency_closure_proved'])
        self.assertEqual(report['mode'], 'original-compile')
        self.assertIsNone(compiler.call_args.kwargs['expected_dependencies'])
        self.assertEqual(self.output.read_bytes(), b'existing PDF sentinel')
        self.assertEqual((self.f.project / 'main.typ').read_bytes(), self.f.source)

    def test_failures_before_stream_leave_stdout_empty(self):
        scenarios = ((self.command('--entrypoint', 'main.typ'), None),
                     (self.command('--unknown'), None),
                     (self.command(), SafetyError('refused compile')))
        for args, failure in scenarios:
            with self.subTest(args=args, failure=failure):
                if failure is None:
                    out, err = io.StringIO(), io.StringIO()
                    out.buffer = io.BytesIO()
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                        cli.main(args)
                    self.assertEqual(out.buffer.getvalue(), b'')
                else:
                    status, out, report, _ = self.capture(args, fail=failure)
                    self.assertEqual(status, 2, report)
                    self.assertEqual(out.getvalue(), b'')
                    self.assertFalse(report['pdf_streamed'])
        self.assertEqual(self.output.read_bytes(), b'existing PDF sentinel')

    def test_interrupted_stream_reports_partial_possibility(self):
        class Short(io.BytesIO):
            def write(self, data):
                super().write(data[:1])
                return 1
        status, out, report, _ = self.capture(self.command(), buffer=Short())
        self.assertEqual(status, 3)
        self.assertEqual(out.getvalue(), b'%')
        self.assertTrue(report['stdout_may_be_partial'])
        self.assertFalse(report['pdf_streamed'])

    def test_cancel_observed_after_final_stream_write_is_not_reported_as_success(self):
        import offline_compile_cli
        event = threading.Event()
        signals = SimpleNamespace(cancel_event=event, first_signal=None)
        def finish_then_cancel(data, _):
            sys.stdout.buffer.write(data)
            event.set()
        with patch.object(offline_compile_cli, '_active_signals', signals), \
             patch.object(cli, '_write_pdf', side_effect=finish_then_cancel):
            status, out, report, _ = self.capture(self.command())
        self.assertEqual(status, 3)
        self.assertTrue(out.getvalue().startswith(b'%PDF-'))
        self.assertFalse(report['pdf_streamed'])
        self.assertTrue(report['stdout_may_be_partial'])

    def test_staging_refuses_mutated_or_missing_capture(self):
        context = include_context.load_include_context(self.f.project, (('main.typ', self.f.source),))
        roots = self.f.value['roots']
        rows = [document_resources.ResourceRequest('project', 'main.typ', 'source')]
        snapshot = document_resources.capture(roots, rows)
        staged = document_resources.stage_original(context, snapshot)
        self.assertEqual(staged['project', 'main.typ'], self.f.source)
        object.__setattr__(snapshot.files[0], 'data', b'tampered')
        with self.assertRaises(SafetyError):
            document_resources.stage_original(context, snapshot)

    def test_help_never_contaminates_pdf_stdout(self):
        out, err = io.StringIO(), io.StringIO()
        out.buffer = io.BytesIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            cli.main(['--help'])
        self.assertEqual(out.getvalue(), '')
        self.assertIn('pdf-stdout', err.getvalue())

    def test_merged_stdout_stderr_is_refused_without_bytes(self):
        run = subprocess.run([sys.executable, str(TOOL / 'document_original_compile_cli.py'), *self.command()],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stdout, b'')

    def test_stdout_cannot_alias_manifest_or_any_captured_input(self):
        extra = self.f.project / 'aux.typ'
        extra.write_bytes(b'extra source')
        self.f.value['files'].append({'root': 'project', 'path': 'aux.typ', 'role': 'source'})
        self.f.manifest.write_text(json.dumps(self.f.value))
        for file in (self.f.manifest, self.f.project / 'main.typ', extra):
            original = file.read_bytes()
            with self.subTest(file=file.name), file.open('r+b') as output:
                run = subprocess.run([sys.executable, str(TOOL / 'document_original_compile_cli.py'), *self.command()],
                                     stdout=output, stderr=subprocess.PIPE, timeout=30)
            self.assertEqual(run.returncode, 2, run.stderr)
            self.assertEqual(file.read_bytes(), original)
            self.assertIn('aliases', json.loads(run.stderr)['error'])

    def test_stdout_regular_file_cannot_append_to_uncaptured_source(self):
        unused = self.f.project / 'unused.typ'
        unused.write_bytes(b'untouched source')
        with unused.open('ab') as output:
            run = subprocess.run([sys.executable, str(TOOL / 'document_original_compile_cli.py'), *self.command()],
                                 stdout=output, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(unused.read_bytes(), b'untouched source')
        self.assertIn('pipe or socket', json.loads(run.stderr)['error'])

    def test_stderr_cannot_alias_a_captured_input(self):
        extra = self.f.project / 'aux.typ'
        extra.write_bytes(b'extra source')
        self.f.value['files'].append({'root': 'project', 'path': 'aux.typ', 'role': 'source'})
        self.f.manifest.write_text(json.dumps(self.f.value))
        with extra.open('r+b') as error:
            run = subprocess.run([sys.executable, str(TOOL / 'document_original_compile_cli.py'), *self.command()],
                                 stdout=subprocess.PIPE, stderr=error, timeout=30)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stdout, b'')
        self.assertEqual(extra.read_bytes(), b'extra source')

    def test_stderr_regular_file_refused_before_unknown_include_or_discovery(self):
        child = self.f.project / 'child.typ'
        child.write_bytes(b'unchanged child')
        (self.f.project / 'main.typ').write_bytes(b'#include "child.typ"\n')
        self.f.value['files'] = []
        self.f.manifest.write_text(json.dumps(self.f.value))
        with child.open('ab') as error:
            run = subprocess.run([sys.executable, str(TOOL / 'document_original_compile_cli.py'),
                                  *self.command('--discover-dependencies')],
                                 stdout=subprocess.PIPE, stderr=error, timeout=30)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stdout, b'')
        self.assertEqual(child.read_bytes(), b'unchanged child')

    def _stream_worker(self):
        worker = self.f.root / 'stream-worker.py'
        marker = self.f.root / 'stream-started'
        worker.write_text('''import sys\nfrom pathlib import Path\nfrom types import SimpleNamespace\nsys.path.insert(0, %r)\nimport document_preview_compile, offline_compile_cli\ndef fake(*args, **kwargs):\n    Path(%r).write_text("ready")\n    return SimpleNamespace(pdf=b"%%PDF-1.7\\n" + b"X" * 1048576, pdf_sha256="a" * 64, dependency_count=1, unobserved_capture_count=0, system_font_files=0, compiler_sha256="b" * 64)\ndocument_preview_compile.compile_original = fake\noffline_compile_cli.executable_main(["compile", "--compile-original", *sys.argv[1:]], gate=True)\n''' % (str(TOOL), str(marker)))
        return worker, marker

    def test_broken_pipe_report_survives_executable_flush(self):
        worker, _ = self._stream_worker()
        process = subprocess.Popen([sys.executable, str(worker), *self.command()],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.stdout.close()
        stderr = process.stderr.read()
        process.stderr.close()
        self.assertEqual(process.wait(timeout=15), 3, stderr)
        report = json.loads(stderr)
        self.assertEqual(report['phase'], 'stream')
        self.assertTrue(report['stdout_may_be_partial'])

    def test_blocked_consumer_signal_cancels_without_hanging(self):
        worker, marker = self._stream_worker()
        process = subprocess.Popen([sys.executable, str(worker), *self.command()],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(marker.exists(), 'stream worker did not begin')
            readable, _, _ = select.select([process.stdout], [], [], 5)
            self.assertTrue(readable, 'PDF stream did not become readable')
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, -signal.SIGTERM, stderr)
            self.assertTrue(stdout.startswith(b'%PDF-'))
            report = json.loads(stderr)
            self.assertEqual(report['phase'], 'stream')
            self.assertTrue(report['stdout_may_be_partial'])
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and sys.platform == 'darwin',
                         'Opt in on macOS with reviewed Typst')
    def test_real_original_pdf_preserves_source_existing_pdf_and_runs_once(self):
        source = b'Thus $ x^i $.\n'
        (self.f.project / 'main.typ').write_bytes(source)
        run = subprocess.run([sys.executable, str(TOOL / 'document_original_compile_cli.py'), *self.command()],
                             capture_output=True, timeout=60)
        report = json.loads(run.stderr)
        self.assertEqual(run.returncode, 0, report)
        self.assertTrue(run.stdout.startswith(b'%PDF-'))
        self.assertEqual(hashlib.sha256(run.stdout).hexdigest(), report['pdf_sha256'])
        self.assertEqual(report['compiler_stages_started'], {'baseline': True, 'candidate': False, 'discovery': False})
        self.assertFalse(report['lint_performed'] or report['global_coverage_complete'])
        self.assertEqual((self.f.project / 'main.typ').read_bytes(), source)
        self.assertEqual(self.output.read_bytes(), b'existing PDF sentinel')

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and sys.platform == 'darwin',
                         'Opt in on macOS with reviewed Typst')
    def test_real_dependency_discovery_matches_the_captured_original(self):
        self.f.value['files'] = []
        self.f.manifest.write_text(json.dumps(self.f.value))
        run = subprocess.run([sys.executable, str(TOOL / 'document_original_compile_cli.py'),
                              *self.command('--discover-dependencies')], capture_output=True, timeout=60)
        report = json.loads(run.stderr)
        self.assertEqual(run.returncode, 0, report)
        self.assertTrue(run.stdout.startswith(b'%PDF-'))
        self.assertTrue(report['discovery_performed'])
        self.assertEqual(report['compiler_stages_started'], {'baseline': True, 'candidate': False, 'discovery': True})
        self.assertEqual(report['observed_dependencies'], 1)
        self.assertFalse(report['lint_performed'] or report['global_coverage_complete'])
        self.assertEqual((self.f.project / 'main.typ').read_bytes(), self.f.source)
        self.assertEqual(self.output.read_bytes(), b'existing PDF sentinel')

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and sys.platform == 'darwin',
                         'Opt in on macOS with reviewed Typst')
    def test_real_compiler_failure_streams_nothing(self):
        source = b'#let x = missing_identifier\n#x\n'
        (self.f.project / 'main.typ').write_bytes(source)
        run = subprocess.run([sys.executable, str(TOOL / 'document_original_compile_cli.py'), *self.command()],
                             capture_output=True, timeout=60)
        report = json.loads(run.stderr)
        self.assertNotEqual(run.returncode, 0, report)
        self.assertEqual(run.stdout, b'')
        self.assertFalse(report['pdf_streamed'])
        self.assertEqual((self.f.project / 'main.typ').read_bytes(), source)
        self.assertEqual(self.output.read_bytes(), b'existing PDF sentinel')
