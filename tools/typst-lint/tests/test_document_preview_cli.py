"""Explicit resource-manifest preview CLI; originals remain untouched."""
import json
import hashlib
import contextlib
import io
import os
from pathlib import Path
import subprocess
import signal
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_preview_cli
import typst_lint
from syntax_client import EXECUTABLE
from typst_lint import InputError, SafetyError


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class DocumentPreviewCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project, self.packages, self.cache = (self.root / name for name in ('project', 'packages', 'cache'))
        for path in (self.project, self.packages, self.cache):
            path.mkdir()
        self.source = b'Thus $ x^i $\n\nHence.\n'
        (self.project / 'main.typ').write_bytes(self.source)
        self.manifest = self.root / 'manifest.json'
        self.value = {'roots': {'project': str(self.project), 'packages': str(self.packages), 'cache': str(self.cache)},
                      'files': [{'root': 'project', 'path': 'main.typ', 'role': 'source'}]}
        self.manifest.write_text(json.dumps(self.value), encoding='utf-8')

    def command(self, *extra):
        return [sys.executable, str(TOOL / 'typst-lint'), 'preview-compile', '--root', str(self.project),
                '--entrypoint', 'main.typ', '--manifest', str(self.manifest), *extra]

    def gate_command(self, *extra):
        return [sys.executable, str(TOOL / 'typst-style'), 'compile', '--preview', '--root', str(self.project),
                '--entrypoint', 'main.typ', '--manifest', str(self.manifest), *extra]

    def test_gate_preview_requires_explicit_option_before_compiler_tail(self):
        with patch.object(document_preview_cli, 'main', return_value=2) as preview:
            self.assertEqual(typst_lint.main(['compile', '--preview', '--root', str(self.project)], gate=True), 2)
            preview.assert_called_once_with(['--root', str(self.project)], gate=True)
        with patch.object(document_preview_cli, 'main') as preview, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(typst_lint.main(['compile', '--root', str(self.project), '--', '--preview'], gate=True), 2)
            preview.assert_not_called()

    def test_gate_preview_rejects_compiler_tail_and_duplicate_opt_in(self):
        for extra in (['--', 'main.typ', 'output.pdf'], ['--preview']):
            run = subprocess.run(self.gate_command(*extra), capture_output=True, timeout=20)
            self.assertEqual(run.returncode, 2)
            self.assertIn(b'unrecognized arguments', run.stderr)
            self.assertEqual((self.project / 'main.typ').read_bytes(), self.source)

    def test_manifest_rejects_duplicate_and_unknown_fields(self):
        self.manifest.write_text('{"roots": {}, "roots": {}, "files": []}', encoding='utf-8')
        with self.assertRaises((InputError, SafetyError)):
            document_preview_cli.load_manifest(str(self.manifest), self.project)
        self.value['unexpected'] = True
        self.manifest.write_text(json.dumps(self.value), encoding='utf-8')
        with self.assertRaises((InputError, SafetyError)):
            document_preview_cli.load_manifest(str(self.manifest), self.project)

    def test_wrong_project_root_refuses_before_compiler_capture(self):
        self.value['roots']['project'] = str(self.root)
        self.manifest.write_text(json.dumps(self.value), encoding='utf-8')
        with patch.object(document_preview_cli.document_preview_compile, 'compile_preview') as compile_preview, \
             contextlib.redirect_stdout(io.StringIO()):
            status = document_preview_cli.main(self.command()[3:] + ['--format', 'json'])
        self.assertEqual(status, 2)
        compile_preview.assert_not_called()
        self.assertEqual((self.project / 'main.typ').read_bytes(), self.source)

    def test_pending_executable_signal_returns_signal_status_before_compiling(self):
        import offline_compile_cli
        cancel = threading.Event()
        cancel.set()
        coordinator = SimpleNamespace(cancel_event=cancel, first_signal=int(signal.SIGTERM))
        with patch.object(offline_compile_cli, '_active_signals', coordinator), \
             patch.object(document_preview_cli.document_preview_compile, 'compile_preview') as compile_preview, \
             contextlib.redirect_stdout(io.StringIO()):
            status = document_preview_cli.main(self.command()[3:] + ['--format', 'json'])
        self.assertEqual(status, -int(signal.SIGTERM))
        compile_preview.assert_not_called()
        self.assertEqual((self.project / 'main.typ').read_bytes(), self.source)

    def test_font_drift_after_discovery_refuses_before_preview_compiler(self):
        fonts = self.project / 'fonts'
        fonts.mkdir()
        (fonts / 'sample.otf').write_bytes(b'font')
        self.value['files'] = [{'root': 'project', 'path': 'fonts/sample.otf', 'role': 'font'}]
        self.manifest.write_text(json.dumps(self.value), encoding='utf-8')
        discovered = (b'main.typ\0', (('project', 'main.typ'),),
                      {('project', 'fonts/sample.otf'): '0' * 64}, '1' * 64)
        output = io.StringIO()
        with patch.object(document_preview_cli.document_preview_discover, 'discover', return_value=discovered), \
             patch.object(document_preview_cli.document_preview_compile, 'compile_preview') as compile_preview, \
             contextlib.redirect_stdout(output):
            status = document_preview_cli.main(self.command()[3:] + ['--discover-dependencies', '--format', 'json'])
        self.assertEqual(status, 2)
        report = json.loads(output.getvalue())
        self.assertIn('Selected fonts changed', report['error'])
        self.assertTrue(report['compiler_invoked'])
        compile_preview.assert_not_called()

    def test_source_drift_after_discovery_refuses_before_compiler_launch(self):
        self.value['files'] = []
        self.manifest.write_text(json.dumps(self.value), encoding='utf-8')
        def changed(*args, **kwargs):
            (self.project / 'main.typ').write_bytes(self.source + b'changed')
            return b'main.typ\0', (('project', 'main.typ'),), {}, '1' * 64
        output = io.StringIO()
        with patch.object(document_preview_cli.document_preview_discover, 'discover', side_effect=changed), \
             patch.object(document_preview_cli.document_preview_compile.runtime, '_capture_compiler') as capture, \
             contextlib.redirect_stdout(output):
            status = document_preview_cli.main(self.command()[3:] + ['--discover-dependencies', '--format', 'json'])
        self.assertEqual(status, 2)
        self.assertIn('absent or differs', json.loads(output.getvalue())['error'])
        capture.assert_not_called()

    def test_review_is_bounded_and_refuses_before_compiling(self):
        output = io.StringIO()
        with patch.object(document_preview_cli, 'MAX_REVIEW_BYTES', 1), \
             patch.object(document_preview_cli.document_preview_compile, 'compile_preview') as compiler, \
             contextlib.redirect_stdout(output):
            status = document_preview_cli.main(self.command()[3:] + ['--review-edits', '--format', 'json'])
        self.assertEqual(status, 2)
        self.assertIn('review size limit', json.loads(output.getvalue())['error'])
        compiler.assert_not_called()

    def test_unequal_preview_cannot_be_an_apply_gate(self):
        fake = SimpleNamespace(baseline_sha256='a' * 64, candidate_sha256='b' * 64,
            pdfs_identical=False, dependency_count=1, unobserved_capture_count=0,
            system_font_files=0, compiler_sha256='c' * 64)
        output = io.StringIO()
        with patch.object(document_preview_cli.document_preview_compile, 'compile_preview', return_value=fake), \
             contextlib.redirect_stdout(output):
            status = document_preview_cli.main(self.command()[3:] + ['--format', 'json'])
        report = json.loads(output.getvalue())
        self.assertEqual(status, 2)
        self.assertFalse(report['source_apply_gate']['eligible'])
        self.assertIn('preview_pdfs_differ', report['source_apply_gate']['blockers'])
        self.assertTrue(report['uninspected_rule_ids'])

    def test_conditional_profile_is_explicit_and_propagated_to_compile(self):
        fake = SimpleNamespace(baseline_sha256='a' * 64, candidate_sha256='a' * 64,
            pdfs_identical=True, dependency_count=1, unobserved_capture_count=0,
            system_font_files=0, compiler_sha256='c' * 64)
        output = io.StringIO()
        with patch.object(document_preview_cli.document_preview_compile, 'compile_preview', return_value=fake) as compiler, \
             contextlib.redirect_stdout(output):
            status = document_preview_cli.main(self.command()[3:] + ['--conditional-gaps', '--format', 'json'])
        report = json.loads(output.getvalue())
        self.assertEqual(status, 2)
        self.assertEqual(compiler.call_args.kwargs['profile'], 'conditional-gaps')
        self.assertEqual(report['sequence_profile'], 'conditional-gaps')
        self.assertIn('pde.proof-trailing-gap', report['conditionally_previewed_rule_ids'])
        self.assertIn('pde.proof-trailing-gap', report['uninspected_rule_ids'])
        self.assertFalse(report['source_apply_gate']['eligible'] or report['source_files_modified'])

    def test_conditional_punctuation_requires_gap_profile_and_propagates(self):
        fake = SimpleNamespace(baseline_sha256='a' * 64, candidate_sha256='a' * 64,
            pdfs_identical=True, dependency_count=1, unobserved_capture_count=0,
            system_font_files=0, compiler_sha256='c' * 64)
        output = io.StringIO()
        with patch.object(document_preview_cli.document_preview_compile, 'compile_preview', return_value=fake) as compiler, \
             contextlib.redirect_stdout(output):
            status = document_preview_cli.main(self.command()[3:] + [
                '--conditional-gaps', '--conditional-punctuation', '--format', 'json'])
        report = json.loads(output.getvalue())
        self.assertEqual(status, 2)
        self.assertEqual(compiler.call_args.kwargs['profile'], 'conditional-gaps-and-punctuation')
        self.assertIn('pde.prose-footnote-punctuation', report['conditionally_previewed_rule_ids'])
        self.assertIn('pde.prose-footnote-punctuation', report['uninspected_rule_ids'])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            document_preview_cli.main(self.command()[3:] + ['--conditional-punctuation'])

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin', 'Opt in on macOS with the reviewed Typst compiler')
    def test_real_preview_reports_success_and_can_stream_pdf(self):
        run = subprocess.run(self.command('--format', 'json'), capture_output=True, timeout=60)
        self.assertEqual(run.returncode, 2, run.stderr)
        report = json.loads(run.stdout)
        self.assertEqual(report['phase'], 'preview')
        self.assertTrue(report['pdf_generated'] and report['pdfs_identical'])
        self.assertFalse(report['source_files_modified'] or report['global_coverage_complete'])
        self.assertFalse(report['publication_authority'] or report['dependency_closure_proved'])
        pdf = subprocess.run(self.command('--pdf-stdout'), capture_output=True, timeout=60)
        self.assertEqual(pdf.returncode, 2, pdf.stderr)
        self.assertTrue(pdf.stdout.startswith(b'%PDF-'))
        self.assertTrue(json.loads(pdf.stderr)['pdf_generated'])
        self.assertEqual((self.project / 'main.typ').read_bytes(), self.source)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin', 'Opt in on macOS with the reviewed Typst compiler')
    def test_discovery_fills_an_empty_manifest_without_writing_sources(self):
        self.value['files'] = []
        self.manifest.write_text(json.dumps(self.value), encoding='utf-8')
        run = subprocess.run(self.command('--discover-dependencies', '--format', 'json'),
                             capture_output=True, timeout=60)
        self.assertEqual(run.returncode, 2, run.stderr)
        report = json.loads(run.stdout)
        self.assertEqual(report['phase'], 'preview')
        self.assertTrue(report['pdf_generated'] and report['discovery_performed'])
        self.assertEqual(report['discovery_observed_dependencies'], 1)
        self.assertEqual(report['resource_count'], 1)
        self.assertEqual((self.project / 'main.typ').read_bytes(), self.source)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin', 'Opt in on macOS with the reviewed Typst compiler')
    def test_style_gate_can_explicitly_preview_without_publishing(self):
        self.value['files'] = []
        self.manifest.write_text(json.dumps(self.value), encoding='utf-8')
        run = subprocess.run(self.gate_command('--discover-dependencies', '--review-edits', '--format', 'json'),
                             capture_output=True, timeout=60)
        self.assertEqual(run.returncode, 2, run.stderr)
        report = json.loads(run.stdout)
        self.assertEqual(report['phase'], 'preview')
        self.assertTrue(report['pdf_generated'] and report['discovery_performed'])
        self.assertFalse(report['source_files_modified'] or report['publication_authority'])
        self.assertFalse(report['source_apply_gate']['eligible'])
        self.assertIn('global_rule_coverage_incomplete', report['source_apply_gate']['blockers'])
        review = report['source_review']['sources']
        self.assertEqual(len(review), 1)
        self.assertEqual(review[0]['path'], 'main.typ')
        candidate = self.source
        self.assertEqual(hashlib.sha256(candidate).hexdigest(), review[0]['baseline_sha256'])
        for step in review[0]['steps']:
            self.assertEqual(hashlib.sha256(candidate).hexdigest(), step['baseline_sha256'])
            for edit in reversed(step['edits']):
                candidate = candidate[:edit['start']] + edit['replacement'].encode('utf-8') + candidate[edit['end']:]
            self.assertEqual(hashlib.sha256(candidate).hexdigest(), step['candidate_sha256'])
        self.assertEqual(hashlib.sha256(candidate).hexdigest(), review[0]['candidate_sha256'])
        self.assertEqual((self.project / 'main.typ').read_bytes(), self.source)
        self.assertFalse((self.project / 'output.pdf').exists())

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin', 'Opt in on macOS with the reviewed Typst compiler')
    def test_missing_package_failure_reports_compiler_diagnostic(self):
        self.source = b'#import "@local/sample:0.1.0": text-value\n#text-value\n'
        (self.project / 'main.typ').write_bytes(self.source)
        package = self.packages / 'local/sample/0.1.0'
        package.mkdir(parents=True)
        (package / 'typst.toml').write_bytes(b'[package]\nname = "sample"\nversion = "0.1.0"\nentrypoint = "lib.typ"\n')
        self.value['files'].append({'root': 'packages', 'path': 'local/sample/0.1.0/typst.toml', 'role': 'package'})
        self.manifest.write_text(json.dumps(self.value), encoding='utf-8')
        run = subprocess.run(self.command('--format', 'json'), capture_output=True, timeout=60)
        self.assertEqual(run.returncode, 2)
        report = json.loads(run.stdout)
        self.assertEqual(report['phase'], 'compile')
        self.assertTrue(report['compiler_invoked'])
        self.assertFalse(report['pdf_generated'])
        self.assertIn('lib.typ', report['compiler_diagnostics'])
        self.assertEqual((self.project / 'main.typ').read_bytes(), self.source)

    def _check_sigterm(self, *extra, gate=False):
        self.source = b'#for n in range(5000) [Line #n.]\nThus $ x^i $\n\nHence.\n'
        (self.project / 'main.typ').write_bytes(self.source)
        command = self.gate_command if gate else self.command
        process = subprocess.Popen(command(*extra, '--format', 'json'), stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True)
        child = None
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and process.poll() is None:
                result = subprocess.run(['/usr/bin/pgrep', '-P', str(process.pid)],
                                        capture_output=True, text=True, timeout=3)
                for value in result.stdout.split():
                    info = subprocess.run(['/bin/ps', '-p', value, '-o', 'command='],
                                          capture_output=True, text=True, timeout=3)
                    if ('/typst-document-preview-' in info.stdout or '/typst-document-discovery-' in info.stdout
                            or '/usr/bin/sandbox-exec' in info.stdout):
                        child = int(value)
                        break
                if child is not None:
                    break
                time.sleep(0.01)
            self.assertIsNotNone(child, 'A private compiler process was not observed')
            os.kill(process.pid, signal.SIGTERM)
            process.communicate(timeout=15)
            self.assertEqual(process.returncode, -int(signal.SIGTERM))
            with self.assertRaises(ProcessLookupError):
                os.kill(child, 0)
            self.assertEqual((self.project / 'main.typ').read_bytes(), self.source)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and Path('/usr/bin/pgrep').is_file(),
                         'Opt in on macOS with process inspection available')
    def test_sigterm_cancels_active_private_compiler_and_preserves_source(self):
        self._check_sigterm()

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and Path('/usr/bin/pgrep').is_file(),
                         'Opt in on macOS with process inspection available')
    def test_sigterm_cancels_active_discovery_and_preserves_source(self):
        self._check_sigterm('--discover-dependencies')

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and Path('/usr/bin/pgrep').is_file(),
                         'Opt in on macOS with process inspection available')
    def test_style_gate_sigterm_reaps_active_preview(self):
        self._check_sigterm(gate=True)
