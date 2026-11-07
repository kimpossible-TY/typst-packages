"""Explicit offline CLI tests; every writable consumer and journal is temporary."""
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import FrozenInstanceError, replace
import io
import errno
import json
import os
from pathlib import Path
import sys
import tempfile
from types import MappingProxyType
import unittest
from unittest import mock

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import offline_cli
import protected_fix as bridge
import syntax_client
import transactions
import typst_lint as lint
from closed_core import CORE_RULE_IDS
from fixed_journal import fixed_journal_directory, journal_inventory
from protected_fix import ProtectedCoreError, ProtectedCoreFix
from syntax_client import AdapterError, EXECUTABLE
from transactions import (Fingerprint, OfflineExclusive, RecoveryReport,
                          RecoveryRequired, TransactionError, TransactionFailure)


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class OfflineCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'project'
        self.root.mkdir()
        self.target = self.root / 'main.typ'
        self.target.write_bytes(b'Preserved prose. $X^i + y_2$\n')
        self.config = self.root / '.typst-lint.toml'
        self.config.write_bytes(b'rule-set="core"\ninclude=["main.typ"]\n')
        self.output = self.root / 'output.pdf'
        self.output.write_bytes(b'existing output sentinel')
        self.original = self.target.read_bytes()
        self.store = fixed_journal_directory(self.root)

    def fix_args(self):
        return ['fix', '--root', str(self.root), '--profile', 'closed-core-v1',
                '--offline-exclusive', '--target', 'main.typ', '--format', 'json']

    def invoke(self, argv, gate=False):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = lint.main(argv, gate=gate)
        self.assertEqual(errors.getvalue(), '')
        payload = json.loads(output.getvalue())
        self.assertEqual(payload['status'], status)
        self.assertFalse(payload['compiler_invoked'])
        self.assertIsNone(payload['compiler_status'])
        self.assertEqual(self.output.read_bytes(), b'existing output sentinel')
        return payload

    def test_actual_fix_complete_input_profile_and_idempotence(self):
        with mock.patch.object(lint, 'report', side_effect=AssertionError('General report is not authority')):
            payload = self.invoke(self.fix_args())
            self.assertEqual(payload['status'], 0)
            self.assertEqual(payload['profile'], 'closed-core-v1')
            self.assertEqual(payload['original_sha256'], lint.digest(self.original))
            self.assertEqual(payload['final_sha256'], lint.digest(self.target.read_bytes()))
            self.assertEqual(set(payload['required_rule_ids']), set(CORE_RULE_IDS))
            self.assertEqual(len(payload['required_rule_ids']), 15)
            self.assertEqual(payload['evaluated_rule_ids'], ['core.parenthesized-scripts'])
            reasons = payload['not_applicable']
            self.assertEqual(len(reasons), 14)
            self.assertTrue(all(item['reason'] for item in reasons))
            self.assertEqual({item['rule_id'] for item in reasons}, set(CORE_RULE_IDS) - {'core.parenthesized-scripts'})
            self.assertEqual(payload['modified_paths'], ['main.typ'])
            self.assertTrue(payload['journal_id'])
            self.assertEqual(len(payload['applied_insertions']), 4)
            self.assertTrue(all(e['start'] == e['end'] and e['replacement'] in ('(', ')') for e in payload['applied_insertions']))
            self.assertTrue(payload['coverage']['input_profile_complete'])
            self.assertFalse(payload['coverage']['global_complete'])
            self.assertEqual(payload['coverage']['global_partial_rule_count'], 33)
            self.assertEqual(payload['final_findings'], [])
            self.assertEqual(self.target.read_bytes(), b'Preserved prose. $X^(i) + y_(2)$\n')
            no_op = self.invoke(self.fix_args())
            self.assertEqual(no_op['status'], 0)
            self.assertIsNone(no_op['journal_id'])
            self.assertEqual(no_op['modified_paths'], [])
            self.assertEqual(no_op['applied_insertions'], [])
        inventory = self.invoke(['journal-status', '--root', str(self.root), '--format', 'json'])
        self.assertEqual(inventory['status'], 0)
        self.assertEqual(len(inventory['inventory']['entries']), 1)
        self.assertEqual(inventory['inventory']['entries'][0]['status'], 'committed')

    def test_only_pinned_syntax_parser_process_is_ever_invoked(self):
        with mock.patch.object(syntax_client.subprocess, 'Popen',
                               wraps=syntax_client.subprocess.Popen) as processes:
            payload = self.invoke(self.fix_args())
        self.assertEqual(payload['status'], 0)
        self.assertTrue(processes.call_args_list)
        self.assertTrue(all(call.args[0] == [str(EXECUTABLE), 'markup']
                            for call in processes.call_args_list))

    def test_optins_combinations_and_untrusted_authority_options_rejected(self):
        variants = []
        good = self.fix_args()
        for remove in ('--profile', '--offline-exclusive', '--target'):
            args = list(good)
            index = args.index(remove)
            del args[index:index + (1 if remove == '--offline-exclusive' else 2)]
            variants.append(args)
        variants += [good + ['--dry-run'], good + ['--package-path', str(self.base)],
                     good + ['--journal-id', 'a'*32], good + ['--profile', 'closed-core-v1'],
                     good + ['--target', 'main.typ'], good + ['--root', str(self.root)],
                     good + ['--', 'main.typ', 'output.pdf']]
        for option in ('--candidate', '--coverage-complete', '--fault', '--catalog',
                       '--journal-directory', '--compiler-path', '--compiler-args'):
            variants.append(good + [option, 'untrusted'])
        for command, gate in (('check', False), ('compile', True)):
            variants.append(([command] + good[1:], gate))
        variants.append(['journal-status'] + good[1:])
        variants.append(['recover'] + good[1:] + ['--journal-id', 'a'*32])
        wrong = list(good)
        wrong[wrong.index('closed-core-v1')] = 'closed-core-v2'
        variants.append(wrong)
        with mock.patch.object(OfflineExclusive, 'assert_editors_watchers_closed_and_no_other_writers',
                               side_effect=AssertionError('No implicit assertion')):
            for value in variants:
                argv, gate = value if isinstance(value, tuple) else (value, False)
                with self.subTest(argv=argv):
                    payload = self.invoke(argv, gate=gate)
                    self.assertEqual(payload['status'], 2)
                    self.assertEqual(payload['error_code'], 'invalid_arguments')
                    self.assertEqual(payload['phase'], 'arguments')
                    self.assertEqual(self.target.read_bytes(), self.original)
                    self.assertFalse(self.store.exists())

    def test_argument_errors_requested_json_include_missing_root_and_target(self):
        for argv in (['fix', '--profile', 'closed-core-v1', '--format=json'],
                     ['recover', '--root', str(self.root), '--offline-exclusive', '--format=json'],
                     ['journal-status', '--format', 'json']):
            with self.subTest(argv=argv):
                payload = self.invoke(argv)
                self.assertEqual(payload['status'], 2)
                self.assertEqual(payload['error_code'], 'invalid_arguments')

    def test_initial_invalid_root_is2_but_later_root_failure_is3(self):
        for root in (self.base / 'absent', self.target, self.target / 'child'):
            with self.subTest(root=root):
                argv = self.fix_args()
                argv[argv.index(str(self.root))] = str(root)
                payload = self.invoke(argv)
                self.assertEqual((payload['status'], payload['error_code'], payload['phase']),
                                 (2, 'invalid_root', 'root'))
        with mock.patch.object(Path, 'resolve', side_effect=PermissionError('root denied')):
            payload = self.invoke(self.fix_args())
        self.assertEqual((payload['status'], payload['error_code']), (3, 'filesystem_failure'))
        with mock.patch.object(Path, 'resolve', return_value=self.root), \
             mock.patch.object(Path, 'stat', side_effect=FileNotFoundError('root disappeared after admission')):
            payload = self.invoke(self.fix_args())
        self.assertEqual((payload['status'], payload['error_code']), (3, 'filesystem_failure'))
        self.assertFalse(self.store.exists())

    def test_cyclic_root_and_older_pathlib_loop_error_are_safety3(self):
        loop = self.base / 'root-loop'
        loop.symlink_to(loop.name, target_is_directory=True)
        argv = self.fix_args()
        argv[argv.index(str(self.root))] = str(loop)
        with mock.patch.object(OfflineExclusive, 'assert_editors_watchers_closed_and_no_other_writers',
                               side_effect=AssertionError('Root refusal cannot assert offline authority')):
            payload = self.invoke(argv)
            self.assertEqual((payload['status'], payload['error_code'], payload['phase']),
                             (3, 'unsafe_root_resolution', 'root'))
            with mock.patch.object(Path, 'resolve', side_effect=RuntimeError(f'Symlink loop from {str(loop)!r}')):
                payload = self.invoke(argv)
            self.assertEqual((payload['status'], payload['error_code'], payload['phase']),
                             (3, 'unsafe_root_resolution', 'root'))
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertFalse(self.store.exists())
        # Compatibility normalization is limited to root resolution, not an
        # exception blanket that could hide unrelated analyzer implementation bugs.
        with mock.patch.object(ProtectedCoreFix, 'validate', side_effect=RuntimeError('unexpected analyzer bug')):
            with redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, 'unexpected analyzer bug'):
                lint.main(self.fix_args())
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_bad_config_and_grammar_are_status2_and_do_not_write(self):
        configurations = (b'', b'rule-set="pde"\ninclude=["main.typ"]',
            b'rule-set="core"\ninclude=["*.typ"]',
            b'rule-set="core"\ninclude=["main.typ","other.typ"]',
            b'rule-set="core"\ninclude=["main.typ"]\nunknown=true')
        for config in configurations:
            with self.subTest(config=config):
                self.config.write_bytes(config)
                payload = self.invoke(self.fix_args())
                self.assertEqual(payload['status'], 2)
                self.assertEqual(payload['error_code'], 'invalid_configuration')
                self.assertEqual(self.target.read_bytes(), self.original)
        self.config.unlink()
        payload = self.invoke(self.fix_args())
        self.assertEqual(payload['error_code'], 'invalid_configuration')
        self.config.write_bytes(b'rule-set="core"\ninclude=["main.typ"]\n')
        for source in (b'#import "other.typ": *\n$X^i$', b'$sqrt(X)^i$',
                       b'// typst-lint-disable core.parenthesized-scripts\n$X^i$',
                       b'#set text(size: 12pt)\n$X^i$', b'$X^$'):
            with self.subTest(source=source):
                self.target.write_bytes(source)
                payload = self.invoke(self.fix_args())
                self.assertEqual(payload['status'], 2)
                self.assertEqual(payload['error_code'], 'unsupported_grammar')
                self.assertEqual(self.target.read_bytes(), source)
        self.assertFalse(any(self.store.glob('root-*/*')))

    def test_parser_failure_is_status2(self):
        with mock.patch.object(bridge, 'parse', side_effect=AdapterError('bad pinned parser')):
            payload = self.invoke(self.fix_args())
        self.assertEqual((payload['status'], payload['phase'], payload['error_code']),
                         (2, 'parser', 'parser_failure'))
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_initial_bad_source_encoding_is_input2_but_later_encoding_race_is3(self):
        self.target.write_bytes(b'\xff')
        payload = self.invoke(self.fix_args())
        self.assertEqual((payload['status'], payload['error_code']), (2, 'invalid_source_encoding'))
        self.assertEqual(self.target.read_bytes(), b'\xff')
        self.target.write_bytes(self.original)
        read = transactions._read
        changed = False

        def edit_after_capture(path, **options):
            nonlocal changed
            captured = read(path, **options)
            if path == self.target and not changed:
                changed = True
                self.target.write_bytes(b'\xfflater user edit')
            return captured

        with mock.patch.object(transactions, '_read', side_effect=edit_after_capture):
            payload = self.invoke(self.fix_args())
        self.assertEqual((payload['status'], payload['error_code']), (3, 'unsafe_transaction'))
        self.assertEqual(self.target.read_bytes(), b'\xfflater user edit')

    def test_unsafe_storage_and_stale_transaction_are_status3(self):
        self.store.mkdir(mode=0o755)
        payload = self.invoke(self.fix_args())
        self.assertEqual(payload['status'], 3)
        self.assertEqual(payload['error_code'], 'unsafe_transaction')
        self.assertEqual(self.store.stat().st_mode & 0o777, 0o755)
        self.store.rmdir()
        with mock.patch.object(ProtectedCoreFix, 'validate', side_effect=TransactionError('stale source identity')):
            payload = self.invoke(self.fix_args())
        self.assertEqual((payload['status'], payload['phase']), (3, 'validate'))
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_typed_recovery_errors_serialize_immutable_conflict_map(self):
        fp = Fingerprint(1, 2, 0o600, os.geteuid(), os.getegid(), 'a'*64, 'b'*64)
        recovery = RecoveryReport('c'*32, unresolved=('main.typ',),
                                  conflicts=MappingProxyType({'main.typ': fp}))
        for error, code in ((RecoveryRequired(('a'*32,)), 'recovery_required'),
                            (TransactionFailure('write failed', recovery), 'transaction_failure')):
            with self.subTest(error=type(error).__name__):
                with mock.patch.object(ProtectedCoreFix, 'validate', side_effect=error):
                    payload = self.invoke(self.fix_args())
                self.assertEqual((payload['status'], payload['error_code']), (3, code))
                if code == 'recovery_required':
                    self.assertEqual(payload['recovery_required']['journals'], ['a'*32])
                else:
                    self.assertEqual(payload['recovery_report']['conflicts']['main.typ']['sha256'], 'a'*64)

    def test_inventory_is_readonly_without_offline_assertion_or_config(self):
        self.config.unlink()
        with mock.patch.object(OfflineExclusive, 'assert_editors_watchers_closed_and_no_other_writers',
                               side_effect=AssertionError('Inventory has no offline assertion')):
            payload = self.invoke(['journal-status', '--root', str(self.root), '--format', 'json'])
        self.assertEqual(payload['status'], 0)
        self.assertFalse(payload['inventory']['store_present'])
        self.assertFalse(payload['inventory']['namespace_present'])
        self.assertFalse(self.store.exists())

    def test_inventory_invalid_byte_name_has_utf8_safe_json_and_remains_visible(self):
        self.invoke(self.fix_args())
        namespace = next(self.store.glob('root-*'))
        try:
            os.mkdir(os.fsencode(namespace) + b'/\xff', mode=0o700)
            observed = journal_inventory(self.root)
        except OSError as error:
            if error.errno not in (errno.EILSEQ, errno.EINVAL):
                raise
            # APFS forbids invalid UTF-8 filenames. Exercise the same immutable
            # inventory output with the POSIX surrogate name a Linux reader can
            # really observe, rather than omitting the JSON encoding regression.
            observed = journal_inventory(self.root)
            corrupt = transactions.JournalEntry(os.fsdecode(b'\xff'), 'unreadable-or-corrupt', 'invalid filename')
            observed = replace(observed, entries=observed.entries + (corrupt,))
        output = io.StringIO()
        with redirect_stdout(output), mock.patch.object(offline_cli, 'journal_inventory', return_value=observed):
            status = lint.main(['journal-status', '--root', str(self.root), '--format', 'json'])
        raw = output.getvalue()
        raw.encode('utf-8', errors='strict')
        self.assertIn('\\udcff', raw)
        payload = json.loads(raw)
        self.assertEqual((status, payload['status']), (3, 3))
        self.assertIn(os.fsdecode(b'\xff'), payload['inventory']['pending'])

    def test_recovery_preserves_later_non_utf8_user_edit_and_returns3(self):
        class SimulatedDeath(BaseException):
            pass

        def stop_after_replace(point, path):
            if point == 'after_replace':
                raise SimulatedDeath()

        lease = OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers()
        with self.assertRaises(SimulatedDeath):
            with ProtectedCoreFix(self.root, 'main.typ', journal_directory=self.store,
                    lease=lease, create_journal_directory=True, fault=stop_after_replace) as fix:
                fix.apply(fix.validate())
        namespace = next(self.store.glob('root-*'))
        journal = next(namespace.iterdir()).name
        later = b'\xfflater user source bytes'
        self.target.write_bytes(later)
        self.config.unlink()
        payload = self.invoke(['recover', '--root', str(self.root), '--offline-exclusive',
                              '--target', 'main.typ', '--journal-id', journal, '--format', 'json'])
        self.assertEqual((payload['status'], payload['error_code']), (3, 'unresolved_recovery'))
        self.assertIn('main.typ', payload['recovery_report']['unresolved'])
        self.assertEqual(self.target.read_bytes(), later)

    def test_recover_absence_is_status3_and_never_creates_namespace(self):
        args = ['recover', '--root', str(self.root), '--offline-exclusive',
                '--target', 'main.typ', '--journal-id', 'a'*32, '--format', 'json']
        self.config.unlink()
        payload = self.invoke(args)
        self.assertEqual(payload['status'], 3)
        self.assertFalse(self.store.exists())
        self.store.mkdir(mode=0o700)
        payload = self.invoke(args)
        self.assertEqual(payload['status'], 3)
        self.assertEqual(list(self.store.iterdir()), [])

    def test_reporting_summary_is_immutable_and_never_authorizes_apply(self):
        lease = OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers()
        with ProtectedCoreFix(self.root, 'main.typ', journal_directory=self.store,
                              lease=lease, create_journal_directory=True) as fix:
            with self.assertRaises(ProtectedCoreError):
                _ = fix.summary
            token = fix.validate()
            forged = replace(fix._validated_summary, final_sha256='forged')
            with self.assertRaises(FrozenInstanceError):
                forged.target = 'other.typ'
            with self.assertRaises(ProtectedCoreError):
                fix.apply(forged)
            self.assertEqual(self.target.read_bytes(), self.original)
            fix.apply(token)
            summary = fix.summary
        self.assertEqual(summary.final_sha256, lint.digest(self.target.read_bytes()))
        with self.assertRaises(FrozenInstanceError):
            summary.required_rule_ids = ('fake',)

    def test_ordinary_routes_stay_readonly_and_compile_tail_inert(self):
        with mock.patch.object(OfflineExclusive, 'assert_editors_watchers_closed_and_no_other_writers',
                               side_effect=AssertionError('No implicit offline assertion')):
            for command in (['check'], ['fix'], ['fix', '--dry-run']):
                payload = self.invoke(command + ['--root', str(self.root), '--format', 'json'])
                self.assertEqual(payload['status'], 2)
                self.assertFalse(payload['coverage']['complete'])
            for tail in (['main.typ', 'output.pdf'], ['recover', 'journal-status']):
                payload = self.invoke(['compile', '--root', str(self.root), '--format', 'json', '--'] + tail, gate=True)
                self.assertEqual(payload['status'], 2)
        self.assertFalse(self.store.exists())
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_dispatch_respects_option_values_and_global_options(self):
        for command in ('check', 'fix'):
            for option, value in (('--root', 'recover'), ('--root', 'journal-status'),
                                  ('--package-path', 'recover'), ('--package-path', 'journal-status')):
                with self.subTest(command=command, option=option, value=value):
                    argv = [command, '--root', str(self.root), '--format', 'json']
                    if option == '--root':
                        argv[argv.index(str(self.root))] = value
                    else:
                        argv += [option, value]
                    with mock.patch.object(lint, 'report', return_value=dict(status=2,
                            compiler_invoked=False, compiler_status=None, ordinary_route=True)) as ordinary:
                        if option == '--root':
                            # A real relative root named like a command remains ordinary.
                            named_root = self.base / value
                            named_root.mkdir(exist_ok=True)
                            previous = Path.cwd()
                            try:
                                os.chdir(self.base)
                                payload = self.invoke(argv)
                            finally:
                                os.chdir(previous)
                        else:
                            payload = self.invoke(argv)
                    self.assertTrue(payload['ordinary_route'])
                    self.assertEqual(ordinary.call_count, 1)
        for marker in ([], ['--']):
            payload = self.invoke(['--root', str(self.root), '--format', 'json'] + marker + ['journal-status'])
            self.assertEqual(payload['command'], 'journal-status')
            self.assertEqual(payload['status'], 0)
            self.assertFalse(self.store.exists())

    def test_help_advertises_explicit_routes(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as exit_code:
            lint.main(['--help'])
        self.assertEqual(exit_code.exception.code, 0)
        for word in ('journal-status', 'recover', '--offline-exclusive', '--profile'):
            self.assertIn(word, output.getvalue())
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as exit_code:
            lint.main(['fix', '--help'])
        self.assertEqual(exit_code.exception.code, 0)
        for word in ('--profile', '--offline-exclusive', '--target'):
            self.assertIn(word, output.getvalue())


if __name__ == '__main__':
    unittest.main()
