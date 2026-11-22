"""Explicit CLI contract tests. All mutation uses owned temporary consumers."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import closed_core_compile as compiler
import offline_cli as offline
import offline_compile_cli as cli
import pdf_publication_store as store
import protected_fix
from protected_fix import ProtectedCoreFix
from syntax_client import EXECUTABLE
from transactions import OfflineExclusive
import typst_lint as lint
from test_pdf_publication_integration import make_fixture, make_compiler


class ClassifierTests(unittest.TestCase):
    def failure(self, status=19, kind=compiler.CompilerFailureKind.PROCESS_EXIT,
                purpose='compile', started=True, safety_gate='passed'):
        invocation = compiler.Invocation(purpose, ('private', 'compile'), '/private', (),
            status, b'\x00\xff', b'\xfe', True, started,
            'signaled' if status < 0 else 'exited', 'anything, not interpreted')
        report = compiler.CompileReport((invocation,), safety_gate,
            compiler.CompilerFailureInfo('opaque reason', invocation, kind))
        failure = compiler.CompilerFailure('opaque reason', invocation, kind=kind, report=report)
        pub = store.PublicationReport('aborted_unpublished', 3, purpose == 'compile' and started,
            status if purpose == 'compile' else None, (invocation,), 'committed', 'untouched')
        wrapped = store.PublicationError('wrapped', pub)
        wrapped.__cause__ = failure
        return wrapped, pub

    def test_natural_positive_and_signal_only(self):
        for status in (1, 2, 3, 19, 255, -int(signal.SIGTERM), -int(signal.SIGKILL)):
            with self.subTest(status=status):
                error, pub = self.failure(status)
                self.assertEqual(cli.classify_status(error, pub), status)
        for kind in compiler.CompilerFailureKind:
            if kind is compiler.CompilerFailureKind.PROCESS_EXIT:
                continue
            error, pub = self.failure(-9, kind)
            self.assertEqual(cli.classify_status(error, pub), 3)

    def test_nonterminating_signal_evidence_is3_without_sending_signals(self):
        names = ('SIGSTOP', 'SIGTSTP', 'SIGTTIN', 'SIGTTOU', 'SIGCONT',
                 'SIGCHLD', 'SIGURG', 'SIGWINCH', 'SIGINFO')
        numbers = {int(getattr(signal, name)) for name in names if hasattr(signal, name)}
        if sys.platform == 'darwin':
            numbers.add(int(signal.SIGIO))
        with mock.patch.object(cli.os, 'kill', side_effect=AssertionError('No signal probes')):
            for number in numbers:
                with self.subTest(number=number):
                    error, pub = self.failure(-number)
                    self.assertEqual(cli.classify_status(error, pub), 3)
        for value in (True, False, 0, -1, 999, 15.0):
            self.assertFalse(cli._fatal_signal(value))

    def test_fatal_signal_policy_is_platform_aware_including_linux_realtime(self):
        # Emulate names/validity only. No OS signal or child is involved.
        signals = SimpleNamespace(SIGTERM=15, SIGKILL=9, SIGIO=23, SIGEMT=7,
            SIGPWR=30, SIGSTKFLT=16, SIGRTMIN=34, SIGRTMAX=64,
            valid_signals=lambda: set(range(1, 65)))
        for platform in ('darwin', 'linux', 'unknown-posix'):
            with self.subTest(platform=platform), mock.patch.object(cli, 'signal', signals), \
                 mock.patch.object(cli.sys, 'platform', platform):
                self.assertTrue(cli._fatal_signal(15))
                self.assertTrue(cli._fatal_signal(9))
                self.assertEqual(cli._fatal_signal(7), platform == 'darwin')
                for number in (23, 30, 16, 34, 47, 64):
                    self.assertEqual(cli._fatal_signal(number), platform == 'linux')
                self.assertFalse(cli._fatal_signal(26))
                self.assertFalse(cli._fatal_signal(65))
        for name in ('SIGTERM', 'SIGKILL', 'SIGSYS', 'SIGXCPU', 'SIGXFSZ',
                     'SIGVTALRM', 'SIGPROF'):
            if hasattr(signal, name):
                number = int(getattr(signal, name))
                error, pub = self.failure(-number)
                self.assertEqual(cli.classify_status(error, pub), -number)
        if sys.platform.startswith('linux') and hasattr(signal, 'SIGRTMIN'):
            for number in range(int(signal.SIGRTMIN), int(signal.SIGRTMAX) + 1):
                if number in signal.valid_signals():
                    error, pub = self.failure(-number)
                    self.assertEqual(cli.classify_status(error, pub), -number)

    def test_executable_boundary_refuses_nonfatal_negative_before_any_signal_change(self):
        for name in ('SIGSTOP', 'SIGTSTP', 'SIGTTIN', 'SIGTTOU', 'SIGCONT',
                     'SIGCHLD', 'SIGURG', 'SIGWINCH', 'SIGINFO'):
            if not hasattr(signal, name):
                continue
            number = int(getattr(signal, name))
            with self.subTest(name=name), mock.patch.object(lint, 'main', return_value=-number), \
                 mock.patch.object(cli.os, 'kill', side_effect=AssertionError('No self-stop')), \
                 mock.patch.object(cli.signal, 'signal', side_effect=AssertionError('No handler change')), \
                 mock.patch.object(cli.signal, 'pthread_sigmask', side_effect=AssertionError('No mask change')), \
                 self.assertRaises(SystemExit) as exited:
                cli.executable_main(['check'])
            self.assertEqual(exited.exception.code, 3)

    def test_unknown_probe_cleanup_and_later_safety_stay3(self):
        for kwargs in (dict(purpose='version-probe'), dict(started=None),
                       dict(started=False), dict(safety_gate='refused'), dict(status=0)):
            error, pub = self.failure(**kwargs)
            self.assertEqual(cli.classify_status(error, pub), 3)
        error, pub = self.failure()
        for change in (dict(phase='restored', output_disposition='restored'),
                       dict(recovery_required=True), dict(compiler_status=0)):
            self.assertEqual(cli.classify_status(error, replace(pub, **change)), 3)
        self.assertEqual(cli.classify_status(error, pub, source_cleanup_safe=False), 3)
        self.assertEqual(cli.classify_status(error, pub, context_cleanup_safe=False), 3)
        safety = compiler.CompileSafetyError('later guard')
        safety.__cause__ = error.__cause__
        error.__cause__ = safety
        self.assertEqual(cli.classify_status(error, pub), 3)
        nested = store.PublicationError('later publication error')
        nested.__cause__ = error
        self.assertEqual(cli.classify_status(nested, pub), 3)

    def test_malformed_cleanup_and_report_fields_refuse_conservatively(self):
        error, pub = self.failure()
        for value in ('unknown', 1, None, False):
            self.assertEqual(cli.classify_status(error, pub, source_cleanup_safe=value), 3)
            self.assertEqual(cli.classify_status(error, pub, context_cleanup_safe=value), 3)
        for value in (None, 0, 'false'):
            self.assertEqual(cli.classify_status(error, replace(pub, recovery_required=value)), 3)
        self.assertEqual(cli.classify_status(error, object()), 3)
        error_one, pub_one = self.failure(1)
        for value in (True, 1.0):
            self.assertEqual(cli.classify_status(error_one, replace(pub_one, compiler_status=value)), 3)
        self.assertEqual(cli.classify_status(None, store.PublicationReport(
            'published', False, True, 0, (), 'committed', 'published')), 3)
        error, pub = self.failure(-999)
        self.assertEqual(cli.classify_status(error, pub), 3)

    def test_success_requires_published_terminal_report(self):
        invocation = compiler.Invocation('compile', ('private',), '/private', (), 0, b'', b'', True, True, 'exited')
        pub = store.PublicationReport('published', 0, True, 0, (invocation,), 'committed', 'published')
        self.assertEqual(cli.classify_status(None, pub), 0)
        self.assertEqual(cli.classify_status(None, replace(pub, recovery_required=True)), 3)
        self.assertEqual(cli.classify_status(None, None), 3)

    def test_explicit_serializers_never_require_opaque_artifact(self):
        error, pub = self.failure()
        data = cli.compile_report_json(error.__cause__.report)
        self.assertEqual(data['compiler_failure']['kind'], 'process_exit')
        self.assertEqual(data['attempted_invocations'][0]['stdout_hex'], '00ff')
        self.assertEqual(data['attempted_invocations'][0]['stderr_hex'], 'fe')
        historical = cli.publication_report_json(replace(pub, attempted_invocations=(),
            observed_sha256=None), historical=True)
        self.assertEqual(historical['invocation_origin'], 'historical_recovery')
        self.assertFalse(historical['invocation_details_available'])
        self.assertIn('does not mean no past execution', historical['invocation_details_note'])
        self.assertIsNone(historical['observed_sha256'])
        self.assertEqual(cli.inventory_json(()), dict(historical_only=True, pending=[], entries=[],
            empty='no PDF journals observed'))


class ParserTests(unittest.TestCase):
    def args(self):
        return ['compile', '--root', '/unused', '--profile', 'closed-core-v1',
            '--resource-profile', 'embedded-ascii-v1', '--offline-exclusive',
            '--offline-output-exclusive', '--target', 'main.typ', '--output', 'build/main.pdf',
            '--format', 'json']

    def invoke(self, arguments, **kwargs):
        output = io.StringIO()
        with redirect_stdout(output):
            status = lint.main(arguments, **kwargs)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload['status'], status)
        return payload

    def test_authority_all_required_and_invalid_output_is_arguments2(self):
        variants = []
        for option in ('--profile', '--resource-profile', '--offline-exclusive',
                       '--offline-output-exclusive', '--target', '--output'):
            argv = self.args()
            index = argv.index(option)
            del argv[index:index + (1 if option.startswith('--offline-') else 2)]
            variants.append(argv)
        for output in ('-', 'x.svg', '/tmp/x.pdf', './x.pdf', '../x.pdf', 'x//y.pdf',
                       'x/{p}.pdf', 'x/*.pdf', 'x.PDF'):
            argv = self.args()
            argv[argv.index('build/main.pdf')] = output
            variants.append(argv)
        variants += [self.args() + tail for tail in (['--dry-run'], ['--', 'extra'],
            ['--compiler-path', '/tmp/x'], ['--deadline-seconds', '1'],
            ['--package-path', '/tmp/pkg'], ['--journal-id', 'a'*32], ['extra'],
            ['--out', 'x.pdf'], ['--output=x.pdf'], ['--offline-output-exclusive'])]
        with mock.patch.object(OfflineExclusive, 'assert_editors_watchers_closed_and_no_other_writers',
                side_effect=AssertionError('No source lease')), \
             mock.patch.object(store.OfflineOutputExclusive, 'assert_exporters_watchers_closed_and_no_other_writers',
                side_effect=AssertionError('No output lease')), \
             mock.patch.object(Path, 'resolve', side_effect=AssertionError('No root access')):
            for args in variants:
                with self.subTest(args=args):
                    result = self.invoke(args)
                    self.assertEqual((result['status'], result['phase'], result['error_code']),
                                     (2, 'arguments', 'invalid_arguments'))

    def test_dispatch_values_and_inert_tails_do_not_select_routes(self):
        for value in ('compile', 'recover', 'output-status'):
            for option in ('--root', '--format', '--package-path', '--profile', '--target',
                           '--journal-id', '--resource-profile', '--output'):
                self.assertEqual(offline._command([option, value, 'fix']), 'fix')
                self.assertEqual(offline._command([option+'='+value, 'fix']), 'fix')
        self.assertFalse(offline.requested(['compile', '--root', '/tmp', '--', '--output', 'x.pdf']))
        self.assertTrue(offline.requested(['compile', '--root', '/tmp', '--', '--resource-profile=x']))
        self.assertTrue(offline.requested(['output-status', '--root', '/tmp']))
        self.assertEqual(offline._command(['--root', 'x', '--', 'compile']), 'compile')

    def test_gate_and_source_only_routes_reject_output_authority(self):
        for args, gate in ((self.args(), True),
            (['fix'] + self.args()[1:], False),
            (['journal-status', '--root', '/unused', '--format=json', '--output', 'x.pdf'], False)):
            result = self.invoke(args, gate=gate)
            self.assertEqual(result['status'], 2)
            self.assertEqual(result['phase'], 'arguments')

    def test_new_module_cannot_mint_output_authority_for_source_only_routes(self):
        args = ['fix', '--root', '/unused', '--profile', 'closed-core-v1',
                '--offline-exclusive', '--target', 'main.typ', '--format=json']
        output = io.StringIO()
        with redirect_stdout(output), mock.patch.object(OfflineExclusive,
                'assert_editors_watchers_closed_and_no_other_writers', side_effect=AssertionError):
            status = cli.main(args)
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(output.getvalue())['phase'], 'arguments')

    def test_status_rejects_all_authority_recovery_rejects_source_options(self):
        for tail in (['--profile', 'closed-core-v1'], ['--resource-profile', 'embedded-ascii-v1'],
                     ['--offline-exclusive'], ['--offline-output-exclusive'],
                     ['--target', 'main.typ'], ['--output', 'main.pdf'], ['--journal-id', 'a'*32]):
            result = self.invoke(['output-status', '--root', '/unused', '--format=json'] + tail)
            self.assertEqual(result['status'], 2)
        recovery = ['recover-output', '--root', '/unused', '--output', 'x.pdf',
                    '--offline-output-exclusive', '--journal-id', 'a'*32, '--format=json']
        for tail in (['--target', 'main.typ'], ['--offline-exclusive'],
                     ['--profile', 'closed-core-v1'], ['--resource-profile', 'embedded-ascii-v1']):
            self.assertEqual(self.invoke(recovery+tail)['status'], 2)
        for journal in ('A'*32, 'a'*31, 'a'*33, '../anything'):
            args = list(recovery)
            args[args.index('a'*32)] = journal
            self.assertEqual(self.invoke(args)['status'], 2)


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class TemporaryConsumerTests(ParserTests):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.f = make_fixture(Path(self.temporary.name).resolve(), target='main.typ', destination='build/main.pdf')
        self.args_value = ParserTests.args(self)
        self.args_value[self.args_value.index('/unused')] = str(self.f.root)

    def args(self):
        return list(self.args_value)

    def test_success_order_and_independent_assertions(self):
        record = make_compiler(self.f)
        order = []
        original_validate = ProtectedCoreFix.validate
        original_enter = cli.PreparedPdfPublication.__enter__
        def entered(publication):
            result = original_enter(publication)
            order.append('prepared')
            return result
        def validate(fix):
            order.append('validate')
            return original_validate(fix)
        with mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record), \
             mock.patch.object(ProtectedCoreFix, 'validate', validate), \
             mock.patch.object(cli.PreparedPdfPublication, '__enter__', entered), \
             mock.patch.object(OfflineExclusive, 'assert_editors_watchers_closed_and_no_other_writers',
                wraps=OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers) as source_lease, \
             mock.patch.object(OfflineOutputExclusive := store.OfflineOutputExclusive,
                'assert_exporters_watchers_closed_and_no_other_writers',
                wraps=OfflineOutputExclusive.assert_exporters_watchers_closed_and_no_other_writers) as output_lease:
            result = self.invoke(self.args())
        self.assertEqual(result['status'], 0)
        self.assertEqual(order, ['prepared', 'validate'])
        source_lease.assert_called_once_with()
        output_lease.assert_called_once_with()
        self.assertEqual(result['source_commit'], 'committed')
        self.assertTrue(result['compiler_invoked'])
        self.assertEqual(result['compiler_status'], 0)
        self.assertEqual(result['publication_report']['output_disposition'], 'published')
        self.assertEqual(result['compile_report']['safety_gate'], 'passed')
        self.assertNotIn('pdf_bytes', json.dumps(result))
        self.assertTrue(self.f.output.read_bytes().startswith(b'%PDF'))

    def test_input_refusals2_despite_output_abort_gate3(self):
        self.f.source.write_bytes(b'#read("anything")')
        result = self.invoke(self.args())
        self.assertEqual(result['status'], 2)
        self.assertEqual(result['publication_report']['phase'], 'aborted_unpublished')
        self.assertEqual(result['publication_report']['gate_status'], 3)
        self.assertFalse(result['compiler_invoked'])

    def test_status_has_no_creation_lease_parser_compiler_or_fsync(self):
        with mock.patch.object(OfflineExclusive, 'assert_editors_watchers_closed_and_no_other_writers', side_effect=AssertionError), \
             mock.patch.object(store.OfflineOutputExclusive, 'assert_exporters_watchers_closed_and_no_other_writers', side_effect=AssertionError), \
             mock.patch.object(os, 'fsync', side_effect=AssertionError), \
             mock.patch.object(ProtectedCoreFix, '__enter__', side_effect=AssertionError), \
             mock.patch.object(compiler, 'compile_committed_snapshot', side_effect=AssertionError):
            result = self.invoke(['output-status', '--root', str(self.f.root), '--format=json'])
        self.assertEqual(result['status'], 0)
        self.assertEqual(result['output_inventory']['empty'], 'no PDF journals observed')
        self.assertNotIn('store_present', result['output_inventory'])
        self.assertFalse((self.f.root.parent/store.STORE_NAME).exists())

    def test_recovery_needs_only_output_lease_and_current_root(self):
        self.f.source.write_bytes(b'#read("anything")')
        refused = self.invoke(self.args())
        journal = refused['publication_report']['journal_id']
        self.f.source.unlink()
        self.f.config.unlink()
        with mock.patch.object(OfflineExclusive, 'assert_editors_watchers_closed_and_no_other_writers', side_effect=AssertionError), \
             mock.patch.object(ProtectedCoreFix, '__enter__', side_effect=AssertionError), \
             mock.patch.object(compiler, '_reviewed_native_artifact', side_effect=AssertionError):
            result = self.invoke(['recover-output', '--root', str(self.f.root),
                '--offline-output-exclusive', '--output', self.f.destination,
                '--journal-id', journal, '--format=json'])
        self.assertEqual(result['status'], 0)
        self.assertEqual(result['source_recovery']['state'], 'not_checked')
        self.assertEqual(result['publication_report']['invocation_origin'], 'historical_recovery')
        self.assertIsNone(result['publication_report']['observed_sha256'])

    def test_main_has_no_self_signal_or_handler_changes(self):
        record = make_compiler(self.f, mode='signal')
        before = {s:signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        with mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record), \
             mock.patch.object(cli.os, 'kill', side_effect=AssertionError('No self signal')):
            result = self.invoke(self.args())
        self.assertEqual(result['status'], -signal.SIGTERM)
        self.assertEqual(result['termination_signal'], signal.SIGTERM)
        self.assertEqual(before, {s:signal.getsignal(s) for s in before})


if __name__ == '__main__':
    unittest.main()
