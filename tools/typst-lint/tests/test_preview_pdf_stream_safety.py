"""PDF preview streaming protects sources and separates diagnostic output."""
import contextlib
import io
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from syntax_client import EXECUTABLE
import document_original_compile_cli as streams
import document_preview_cli as cli
import offline_compile_cli
import test_partial_document_preview as fixtures


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class PreviewPdfStreamSafetyTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PartialDocumentPreviewTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.good = b'Thus $ x^i $.\n'
        self.f.bad = b'This is $ a^j $.\n'
        (self.f.project / 'good.typ').write_bytes(self.f.good)
        (self.f.project / 'bad.typ').write_bytes(self.f.bad)

    def command(self, *extra):
        return [str(TOOL / 'typst-style'), 'compile', '--preview', '--root', str(self.f.project),
                '--entrypoint', 'main.typ', '--manifest', str(self.f.manifest),
                '--withhold-source', 'bad.typ', *extra]

    def _worker(self):
        marker = self.f.manifest.parent / 'stream-started'
        worker = self.f.manifest.parent / 'preview-stream-worker.py'
        worker.write_text('''import sys\nfrom pathlib import Path\nfrom types import SimpleNamespace\nsys.path.insert(0, %r)\nimport document_preview_compile, offline_compile_cli\ndef fake(*args, **kwargs):\n    Path(%r).write_text("ready")\n    return SimpleNamespace(candidate_pdf=b"%%PDF-1.7\\n" + b"X" * 1048576, baseline_sha256="a" * 64, candidate_sha256="a" * 64, pdfs_identical=True, dependency_count=1, unobserved_capture_count=0, system_font_files=0, compiler_sha256="b" * 64)\ndocument_preview_compile.compile_preview = fake\noffline_compile_cli.executable_main(["compile", "--preview", *sys.argv[1:]], gate=True)\n''' % (str(TOOL), str(marker)))
        return worker, marker

    def worker_args(self):
        return ['--root', str(self.f.project), '--entrypoint', 'main.typ', '--manifest', str(self.f.manifest),
                '--withhold-source', 'bad.typ', '--pdf-stdout']

    def test_merged_streams_are_silently_refused_before_parsing(self):
        for extra in ((), ('--not-an-option',)):
            with self.subTest(extra=extra):
                run = subprocess.run(self.command('--pdf-stdout', *extra), stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, timeout=30)
                self.assertEqual(run.returncode, 2)
                self.assertEqual(run.stdout, b'')
        invalid = subprocess.run(self.command('--pdf-stdout=true'), stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, timeout=30)
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stdout, b'')

    def test_stderr_regular_file_is_refused_before_parsing_without_writes(self):
        target = self.f.project / 'uncaptured.typ'
        target.write_bytes(b'untouched')
        with target.open('ab') as error:
            run = subprocess.run(self.command('--pdf-stdout', '--not-an-option'), stdout=subprocess.PIPE,
                                 stderr=error, timeout=30)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stdout, b'')
        self.assertEqual(target.read_bytes(), b'untouched')

    def test_stdout_regular_file_is_refused_without_appending(self):
        target = self.f.project / 'uncaptured.typ'
        target.write_bytes(b'untouched')
        with target.open('ab') as output:
            run = subprocess.run(self.command('--pdf-stdout'), stdout=output, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(target.read_bytes(), b'untouched')
        report = json.loads(run.stderr)
        self.assertFalse(report['pdf_generated'] or report['pdf_streamed'])
        self.assertIn('pipe or socket', report['error'])
        self.assertIn('already have truncated', report['output_stream_note'])

    def test_non_pdf_json_to_regular_file_keeps_existing_behavior(self):
        flags = [item for name in ('main.typ', 'good.typ') for item in ('--withhold-source', name)]
        with tempfile.TemporaryFile() as output:
            run = subprocess.run(self.command(*flags, '--format', 'json'), stdout=output, stderr=subprocess.PIPE, timeout=30)
            output.seek(0)
            report = json.load(output)
        self.assertEqual(run.returncode, 2, report)
        self.assertIn('no_verified_candidate_edits', report['source_apply_gate']['blockers'])
        self.assertFalse(report['pdf_generated'])

    def test_pre_stream_failure_and_pdf_help_never_write_stdout(self):
        run = subprocess.run(self.command('--withhold-source', 'missing.typ', '--pdf-stdout'),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stdout, b'')
        self.assertFalse(json.loads(run.stderr)['pdf_streamed'])
        help_run = subprocess.run([str(TOOL / 'typst-style'), 'compile', '--preview', '--pdf-stdout', '--help'],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(help_run.returncode, 0)
        self.assertEqual(help_run.stdout, b'')
        self.assertIn(b'pdf-stdout', help_run.stderr)

    def test_stdout_or_stderr_descriptor_change_is_refused_before_transfer(self):
        target = self.f.project / 'uncaptured.typ'
        worker = self.f.manifest.parent / 'stream-change-worker.py'
        worker.write_text('''import os, sys\nfrom types import SimpleNamespace\nsys.path.insert(0, %r)\nimport document_preview_compile, offline_compile_cli\ndef fake(*args, **kwargs):\n    fd = os.open(%r, os.O_WRONLY | os.O_APPEND)\n    os.dup2(fd, int(sys.argv[1]))\n    os.close(fd)\n    return SimpleNamespace(candidate_pdf=b"%%PDF-fixture", baseline_sha256="a" * 64, candidate_sha256="a" * 64, pdfs_identical=True, dependency_count=1, unobserved_capture_count=0, system_font_files=0, compiler_sha256="b" * 64)\ndocument_preview_compile.compile_preview = fake\noffline_compile_cli.executable_main(["compile", "--preview", *sys.argv[2:]], gate=True)\n''' % (str(TOOL), str(target)))
        for changed_fd in (1, 2):
            with self.subTest(changed_fd=changed_fd):
                target.write_bytes(b'untouched')
                run = subprocess.run([sys.executable, str(worker), str(changed_fd), *self.worker_args()],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
                self.assertEqual(target.read_bytes(), b'untouched')
                self.assertEqual(run.stdout, b'')
                if changed_fd == 1:
                    self.assertEqual(run.returncode, 3)
                    self.assertIn('stream changed', json.loads(run.stderr)['error'])
                else:
                    self.assertEqual(run.returncode, 2)
                    self.assertEqual(run.stderr, b'')

    def test_cancel_after_final_write_does_not_report_successful_transfer(self):
        event = threading.Event()
        signal_state = SimpleNamespace(cancel_event=event, first_signal=None)
        fake = SimpleNamespace(candidate_pdf=b'%PDF-fixture', baseline_sha256='a' * 64,
            candidate_sha256='a' * 64, pdfs_identical=True, dependency_count=1,
            unobserved_capture_count=0, system_font_files=0, compiler_sha256='b' * 64)
        def finish_then_cancel(data, _):
            sys.stdout.buffer.write(data)
            event.set()
        out, err = io.StringIO(), io.StringIO()
        out.buffer = io.BytesIO()
        with patch.object(offline_compile_cli, '_active_signals', signal_state), \
             patch.object(cli.document_preview_compile, 'compile_preview', return_value=fake), \
             patch.object(streams, '_write_pdf', side_effect=finish_then_cancel), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cli.main(self.worker_args())
        report = json.loads(err.getvalue())
        self.assertEqual(status, 3)
        self.assertEqual(out.buffer.getvalue(), fake.candidate_pdf)
        self.assertFalse(report['pdf_streamed'])
        self.assertTrue(report['stdout_may_be_partial'])

    def test_broken_pipe_does_not_corrupt_report_or_executable_flush(self):
        worker, _ = self._worker()
        process = subprocess.Popen([sys.executable, str(worker), *self.worker_args()],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process.stdout.close()
        stderr = process.stderr.read()
        process.stderr.close()
        self.assertEqual(process.wait(timeout=15), 3, stderr)
        report = json.loads(stderr)
        self.assertEqual(report['phase'], 'stream')
        self.assertTrue(report['stdout_may_be_partial'] and report['pdf_generated'])
        self.assertFalse(report['pdf_streamed'] or report['publication_authority'])

    def test_blocked_pipe_signal_cancels_and_reports_partial_output(self):
        worker, marker = self._worker()
        process = subprocess.Popen([sys.executable, str(worker), *self.worker_args()],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(marker.exists(), 'stream worker did not begin')
            ready, _, _ = select.select([process.stdout], [], [], 5)
            self.assertTrue(ready, 'PDF stream did not become readable')
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, -signal.SIGTERM, stderr)
            self.assertTrue(stdout.startswith(b'%PDF-'))
            report = json.loads(stderr)
            self.assertEqual(report['phase'], 'stream')
            self.assertTrue(report['stdout_may_be_partial'])
            self.assertFalse(report['pdf_streamed'])
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and sys.platform == 'darwin',
                         'Opt in on macOS with reviewed Typst')
    def test_actual_pdf_pipe_and_json_are_separate_with_source_unchanged(self):
        run = subprocess.run(self.command('--pdf-stdout'), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        report = json.loads(run.stderr)
        self.assertEqual(run.returncode, 2, report)
        self.assertTrue(run.stdout.startswith(b'%PDF-'))
        self.assertTrue(report['pdf_generated'] and report['pdf_streamed'])
        self.assertEqual(report['withheld_sources'][0]['path'], 'bad.typ')
        self.assertFalse(report['source_apply_gate']['eligible'] or report['publication_authority'])
        self.assertEqual((self.f.project / 'good.typ').read_bytes(), self.f.good)
        self.assertEqual((self.f.project / 'bad.typ').read_bytes(), self.f.bad)
