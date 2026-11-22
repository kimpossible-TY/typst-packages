"""Independent paragraph public-process reporting, signals and restart recovery.

Only owned child processes are signalled and reaped. Compiler programs and all
mutations are private fixtures. Existing generic storage coverage is not copied.
"""
import base64
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import unittest

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paragraph_fix_test_support import ROOT, EXPECTED, fixture, evidence, same_byte_replace, state
from syntax_client import EXECUTABLE
from test_offline_compile_integration import _CompileFixtures, OLD_OUTPUT, sha256
import test_offline_compile_signals as process_protocol

WORKER = Path(__file__).with_name('captured_text_utils_paragraph_cli_process_worker.py')
PROFILE = 'captured-text-utils-paragraph-v1'


class _ParagraphCliFixtures(_CompileFixtures):
    def fixture(self, *, source=ROOT, present=True):
        self.serial += 1
        f = fixture(self.base, self.serial, source=source,
                    target='nested source/paragraph file.typ')
        f.output = 'build with spaces/nested/paragraph file.pdf'
        f.pdf = f.root / f.output
        f.pdf.parent.mkdir(parents=True)
        if present:
            f.pdf.write_bytes(OLD_OUTPUT)
            f.pdf.chmod(0o644)
        namespace = 'root-' + sha256(os.fsencode(f.root))
        f.source_store = f.base / '.typst-lint-journals'
        f.source_namespace = f.source_store / namespace
        f.output_store = f.base / '.typst-lint-pdf-journals'
        f.output_namespace = f.output_store / namespace
        f.compiler_marker = f.base / 'compile-started'
        f.ready, f.release = str(f.base / 'ready-'), str(f.base / 'release-')
        f.preserved = {p: state(p) for p in evidence(f)}
        return f

    def argv(self, command='compile', f=None, *, format='json', output=None):
        f = f or self.f
        args = [command, '--root', str(f.root), '--format', format]
        if command in ('compile', 'fix'):
            args += ['--profile', PROFILE, '--offline-exclusive', '--target', f.target,
                     '--package-path', str(f.package_path)]
        if command == 'compile':
            args += ['--resource-profile', 'embedded-ascii-v1', '--offline-output-exclusive',
                     '--output', f.output if output is None else output]
        return args

    def request(self, f=None, argv=None, **options):
        f = f or self.f
        options.setdefault('catalog', str(f.catalog))
        return super().request(f, argv, **options)

    def run_process(self, f=None, argv=None, *, actual=False, **options):
        if actual:
            return super().run_process(f, argv, actual=True, **options)
        f = f or self.f
        return subprocess.run([sys.executable, str(WORKER), json.dumps(self.request(f, argv, **options))],
            cwd=f.base, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
            capture_output=True, timeout=90)

    def spawn(self, f=None, **options):
        f = f or self.f
        child = subprocess.Popen([sys.executable, str(WORKER), json.dumps(self.request(f, **options))],
            cwd=f.base, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def cleanup():
            if child.poll() is None:
                # Release owned barriers and allow the production coordinator to
                # reap an active fake compiler before any last-resort kill.
                for index in range(len(options.get('boundaries', []))):
                    Path(f.release + str(index)).touch()
                child.send_signal(signal.SIGTERM)
                try:
                    child.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
            child.communicate(timeout=10)
        self.addCleanup(cleanup)
        return child

    # Process-handshake helpers only, not inherited test cases.
    wait_file = process_protocol.OfflineCompileSignalTests.wait_file
    wait_boundary = process_protocol.OfflineCompileSignalTests.wait_boundary
    release = process_protocol.OfflineCompileSignalTests.release
    finish_json = process_protocol.OfflineCompileSignalTests.finish_json
    assert_child_reaped = process_protocol.OfflineCompileSignalTests.assert_child_reaped

    def complete_state(self, path):
        return self.file_state(path), path.stat().st_ctime_ns, state(path)[-1]

    def events(self, f=None, *, source=False):
        return [[json.loads(p.read_bytes())['kind' if source else 'state']
                 for p in sorted(m.parent.glob('event-*.json'))]
                for m in self.journals(f, source=source)]

    def preserved(self, f=None):
        f = f or self.f
        self.assertEqual({p: state(p) for p in f.preserved}, f.preserved)

    def source_recover_argv(self, journal, f=None):
        f = f or self.f
        return self.argv('recover', f) + ['--offline-exclusive', '--target', f.target,
                                         '--journal-id', journal]

    def crash(self, f=None, *, domain='output', point='after_prepared', path=None):
        f = f or self.f
        path = (f.target if domain == 'source' else f.output) if path is None else path
        boundary = [domain, point, path]
        child = self.spawn(f, boundaries=[boundary])
        self.assertEqual(self.wait_boundary(child, f), boundary)
        child.kill()
        out, err = child.communicate(timeout=10)
        self.assertEqual((child.returncode, out, err), (-signal.SIGKILL, b'', b''))
        self.assert_lock_free(f)
        return self.journals(f)[0].parent.name


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX owned-child protocol')
class ParagraphCliRecoveryTests(_ParagraphCliFixtures):
    def test_real_public_source_fix_then_noop_preserves_identity_and_evidence(self):
        report = self.run_json(argv=self.argv('fix'), actual=True)
        self.assertEqual(report['status'], 0, report)
        self.assert_no_compile(report)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        manifest, = self.journals(source=True)
        data = json.loads(manifest.read_bytes())
        self.assertEqual(data['schema'], 1)
        record, = data['files']
        self.assertEqual((record['path'], base64.b64decode(record['before']),
                          base64.b64decode(record['after'])), (self.f.target, ROOT, EXPECTED))
        self.assertEqual(self.events(source=True)[0][-1], 'committed')
        before = self.file_state(self.f.source)
        again = self.run_json(argv=self.argv('fix'), actual=True)
        self.assertEqual(again['status'], 0, again)
        self.assertEqual(again['modified_paths'], [])
        self.assertIsNone(again['journal_id'])
        self.assertEqual(self.file_state(self.f.source), before)
        self.assertEqual(len(self.journals(source=True)), 1)
        self.assertEqual(self.journals(), [])
        self.preserved()

    def test_compiler_exit_keeps_genuine_source_commit_and_prepared_original_output(self):
        before = self.complete_state(self.f.pdf)
        report = self.run_json(compiler_mode='nonzero', assert_order=True)
        self.assertEqual((report['status'], report['source_commit'], report['compiler_status']),
                         (19, 'committed', 19), report)
        self.assertTrue(report['compiler_invoked'])
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assertEqual(self.complete_state(self.f.pdf), before)
        self.assertEqual(self.events(source=True)[0][-1], 'committed')
        self.assertEqual(report['source_summary']['journal_id'], self.journals(source=True)[0].parent.name)
        publication = report['publication_report']
        self.assertEqual((publication['phase'], publication['output_disposition'],
                          publication['recovery_required']), ('aborted_unpublished', 'untouched', False))
        invocation = report['compile_report']['attempted_invocations'][-1]
        self.assertEqual(bytes.fromhex(invocation['stdout_hex']), b'owned paragraph stdout\x00\xff')
        self.assertEqual(bytes.fromhex(invocation['stderr_hex']), b'owned paragraph stderr\xfe\n')
        self.assertEqual(self.events()[0], ['prepared', 'compile_launch_intent', 'aborted_unpublished'])
        self.preserved()
        self.assert_lock_free()

    def test_sigterm_before_validation_and_during_apply_has_truthful_commit(self):
        for domain, point, committed in (('output', 'after_prepared', False),
                                         ('source', 'after_replace', True)):
            with self.subTest(domain=domain):
                f = self.fixture()
                boundary = [domain, point, f.output if domain == 'output' else f.target]
                child = self.spawn(f, boundaries=[boundary])
                self.assertEqual(self.wait_boundary(child, f), boundary)
                child.send_signal(signal.SIGTERM)
                self.release(f)
                report = self.finish_json(child, expected=-signal.SIGTERM)
                self.assertEqual(report['received_signals'][0], signal.SIGTERM)
                self.assert_no_compile(report, f)
                self.assertEqual(f.source.read_bytes(), EXPECTED if committed else ROOT)
                self.assertEqual(f.pdf.read_bytes(), OLD_OUTPUT)
                self.assertEqual(report['source_commit'], 'committed' if committed else 'not_applied')
                self.assertEqual(report['output_recovery']['state'], 'not_required')
                if committed:
                    self.assertEqual(self.events(f, source=True)[0][-1], 'committed')
                self.preserved(f)
                self.assert_lock_free(f)

    def test_received_sigint_and_sigterm_during_compiler_reap_owned_child(self):
        for number in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(number=number):
                f = self.fixture()
                child = self.spawn(f, compiler_mode='hang')
                pid = int(self.wait_file(child, f.compiler_marker))
                child.send_signal(number)
                report = self.finish_json(child, expected=-number)
                self.assertEqual(report['received_signals'][0], number)
                self.assertEqual(report['compiler_status'], -signal.SIGKILL)
                self.assertEqual(report['compile_report']['compiler_failure']['kind'], 'cancelled')
                self.assertEqual(report['source_commit'], 'committed')
                self.assertEqual(f.source.read_bytes(), EXPECTED)
                self.assertEqual(f.pdf.read_bytes(), OLD_OUTPUT)
                self.assert_child_reaped(pid)
                self.preserved(f)
                self.assert_lock_free(f)

    def test_sigint_after_pdf_rename_restores_only_pdf(self):
        boundary = ['output', 'after_replaced', self.f.output]
        child = self.spawn(boundaries=[boundary])
        self.wait_boundary(child)
        self.assertTrue(self.f.pdf.read_bytes().startswith(b'%PDF-1.7'))
        child.send_signal(signal.SIGINT)
        self.release()
        report = self.finish_json(child, expected=-signal.SIGINT)
        self.assertEqual(report['compiler_status'], 0)
        self.assertEqual(report['publication_report']['output_disposition'], 'restored')
        self.assertFalse(report['publication_report']['recovery_required'])
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.preserved()

    def test_source_kill_before_or_after_replace_recovers_with_independent_output_history(self):
        for point in ('before_replace', 'after_replace'):
            with self.subTest(point=point):
                f = self.fixture()
                output_id = self.crash(f, domain='source', point=point)
                source_id = self.journals(f, source=True)[0].parent.name
                self.assertEqual(f.source.read_bytes(), ROOT if point == 'before_replace' else EXPECTED)
                blocked = self.run_json(f, forbid_validate=True)
                self.assertEqual(blocked['status'], 3)
                self.assertEqual(blocked['source_recovery']['state'], 'required')
                source = self.run_json(f, argv=self.source_recover_argv(source_id, f), fake_compiler=False)
                self.assertEqual(source['status'], 0, source)
                self.assertTrue(source['recovery_report']['complete'])
                self.assertEqual(f.source.read_bytes(), ROOT)
                self.assertEqual(self.events(f, source=True)[0][-1], 'recovered')
                output = self.run_json(f, argv=self.recover_argv(output_id, f), fake_compiler=False, forbid_source=True)
                self.assertEqual(output['status'], 0, output)
                self.assertEqual(f.pdf.read_bytes(), OLD_OUTPUT)
                self.preserved(f)

    def test_output_kill_barriers_restart_and_durable_publication_is_final(self):
        for point, path, durable in (('after_prepared', None, False),
                                      ('after_staged', None, False),
                                      ('after_replace_before_event', None, False),
                                      ('before_published_event', None, False),
                                      ('after_event', 'published', True)):
            with self.subTest(point=point):
                f = self.fixture()
                output_id = self.crash(f, point=point, path=path)
                final = self.complete_state(f.pdf)
                if not durable:
                    blocked = self.run_json(f, forbid_validate=True)
                    self.assertEqual(blocked['status'], 3, blocked)
                    self.assertIn(output_id, blocked['output_inventory']['pending'])
                report = self.run_json(f, argv=self.recover_argv(output_id, f), fake_compiler=False, forbid_source=True)
                # An unobserved rename has no durable full postrename
                # fingerprint. Existing schema1 recovery must preserve that
                # uncertainty, never relax stage ctime into rollback authority.
                uncertain = point == 'after_replace_before_event'
                self.assertEqual(report['status'], 3 if uncertain else 0, report)
                self.assertEqual(report['publication_report']['invocation_origin'], 'historical_recovery')
                self.assertFalse(report['publication_report']['invocation_details_available'])
                self.assertEqual(report['publication_report']['attempted_invocations'], [])
                self.assertFalse(report['publication_report']['resource_details_available'])
                if uncertain:
                    publication = report['publication_report']
                    self.assertEqual((publication['output_disposition'], publication['recovery_required'],
                                      publication['compiler_invoked'], publication['compiler_status']),
                                     ('preservedconflict', True, True, 0))
                    self.assertEqual(self.complete_state(f.pdf), final)
                    inventory = self.run_json(f, argv=self.argv('output-status', f), fake_compiler=False)
                    self.assertIn(output_id, inventory['output_inventory']['pending'])
                    self.assertEqual(f.source.read_bytes(), EXPECTED)
                elif durable:
                    self.assertEqual(self.complete_state(f.pdf), final)
                    self.assertEqual(self.events(f)[0][-1], 'published')
                else:
                    self.assertEqual(f.pdf.read_bytes(), OLD_OUTPUT)
                self.preserved(f)

    def test_postkill_user_edits_and_identical_inode_replacements_remain_untouched(self):
        for domain in ('source', 'output'):
            for change in ('edit', 'inode'):
                with self.subTest(domain=domain, change=change):
                    f = self.fixture()
                    output_id = self.crash(f, domain=domain,
                        point='after_replace' if domain == 'source' else 'after_replaced')
                    path = f.source if domain == 'source' else f.pdf
                    if change == 'edit':
                        path.write_bytes(path.read_bytes() + b'\nLater private user edit.\n')
                    else:
                        same_byte_replace(path)
                    before = self.complete_state(path)
                    if domain == 'source':
                        journal = self.journals(f, source=True)[0].parent.name
                        args = self.source_recover_argv(journal, f)
                    else:
                        args = self.recover_argv(output_id, f)
                    report = self.run_json(f, argv=args, fake_compiler=False)
                    self.assertEqual(report['status'], 3, report)
                    self.assertEqual(self.complete_state(path), before)
                    self.preserved(f)
                    self.assert_lock_free(f)

    def test_historical_recovery_needs_no_current_config_package_catalog_or_compiler(self):
        output_id = self.crash(domain='source', point='after_replace')
        source_id = self.journals(source=True)[0].parent.name
        for path in (self.f.config, self.f.catalog, self.f.manifest, self.f.package,
                     self.f.base / 'owned paragraph compiler'):
            path.unlink()
        self.f.package.parent.rmdir()
        output = self.run_json(argv=self.recover_argv(output_id), fake_compiler=False, forbid_source=True)
        self.assertEqual(output['status'], 0, output)
        self.assertEqual(output['source_recovery']['state'], 'not_checked')
        source = self.run_json(argv=self.source_recover_argv(source_id), fake_compiler=False)
        self.assertEqual(source['status'], 0, source)
        self.assertEqual(self.f.source.read_bytes(), ROOT)
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.assertEqual(source['recovery_report']['complete'], True)
        self.assert_lock_free()


if __name__ == '__main__':
    unittest.main()
