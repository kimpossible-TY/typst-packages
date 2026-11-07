"""Real subprocess routes for the explicit, isolated closed-core CLI.

All source, config, git index, fake compiler and journal writes are temporary.
Success certifies one closed input, never global 33-rule acceptance/compilation.
The crash worker instruments only its own process and calls the real main().
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from closed_core import CORE_RULE_IDS
from syntax_client import EXECUTABLE

CLI = TOOL / 'typst-lint'
GATE = TOOL / 'typst-style'
WORKER = Path(__file__).with_name('offline_cli_process_worker.py')
CONFIG = b'rule-set = "core"\ninclude = ["main.typ"]\n'
ORIGINAL = b'\xef\xbb\xbf' + '기존 사용자 prose $X^i$ 보존.\r\nUntouched line.\r\n'.encode()
EXPECTED = ORIGINAL.replace(b'X^i', b'X^(i)')
SCRIPT = 'core.parenthesized-scripts'


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX storage protocol')
class OfflineCLIIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='typst-offline-cli-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.counter = 0
        self.f = self.fixture()

    def fixture(self, *, source=ORIGINAL, config=CONFIG, root_name='consumer'):
        self.counter += 1
        base = self.base / f'case-{self.counter}'
        root = base / root_name
        root.mkdir(parents=True)
        target = root / 'main.typ'
        target.write_bytes(source)
        target.chmod(0o640)
        cfg = root / '.typst-lint.toml'
        if config is not None:
            cfg.write_bytes(config)
        unrelated = root / 'unselected.typ'
        unrelated.write_bytes(b'\xff unrelated dirty user work #read("never-open")\n')
        pdf = root / 'main.pdf'
        pdf.write_bytes(b'%PDF-preserve-existing-output\x00\xff')
        store = base / '.typst-lint-journals'
        namespace = store / ('root-' + hashlib.sha256(os.fsencode(root)).hexdigest())
        return SimpleNamespace(base=base, root=root, target=target, config=cfg,
                               unrelated=unrelated, pdf=pdf, store=store,
                               namespace=namespace)

    def argv(self, command='fix', fixture=None, *, writer=True, extra=()):
        f = fixture or self.f
        result = [command, '--root', str(f.root), '--format', 'json']
        if writer:
            if command == 'fix':
                result += ['--profile', 'closed-core-v1']
            result += ['--offline-exclusive', '--target', 'main.typ']
        return result + list(extra)

    def run_cli(self, argv=None, *, gate=False, env=None, worker=None, cwd=None):
        args = self.argv() if argv is None else argv
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        if env:
            environment.update(env)
        if worker is None:
            command = [sys.executable, str(GATE if gate else CLI), *args]
        else:
            command = [sys.executable, str(WORKER),
                       json.dumps(dict(argv=args, gate=gate, **worker))]
        result = subprocess.run(command, cwd=cwd or self.base, env=environment,
                                capture_output=True, text=True, timeout=30)
        self.assertIn(result.returncode, (0, 2, 3), result.stderr)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail(f'CLI did not return typed JSON: {result.stdout!r}; {result.stderr!r}')
        self.assertIs(type(payload), dict)
        self.assertIs(type(payload.get('status')), int)
        self.assertEqual(payload['status'], result.returncode, payload)
        self.assertFalse(payload['compiler_invoked'], payload)
        self.assertIsNone(payload['compiler_status'], payload)
        self.assertEqual(result.stderr, '', result.stderr)
        return payload

    def state(self, path):
        value = path.stat()
        return (path.read_bytes(), value.st_dev, value.st_ino,
                stat.S_IMODE(value.st_mode), value.st_uid, value.st_gid)

    def tree_state(self, root):
        """Byte/identity/permission/mtime snapshot, deliberately excluding atime."""
        result = {}
        for path in [root, *sorted(root.rglob('*'))]:
            value = path.lstat()
            content = (os.readlink(path) if stat.S_ISLNK(value.st_mode) else
                       path.read_bytes() if stat.S_ISREG(value.st_mode) else None)
            result[str(path.relative_to(root))] = (
                value.st_dev, value.st_ino, value.st_mode, value.st_uid,
                value.st_gid, value.st_size, value.st_mtime_ns, content)
        return result

    def manifests(self, fixture=None):
        return sorted((fixture or self.f).store.glob('root-*/*/manifest.json'))

    def inventory(self, fixture=None, **kwargs):
        report = self.run_cli(self.argv('journal-status', fixture, writer=False), **kwargs)
        return report, report['inventory']

    def recover(self, journal, fixture=None, **kwargs):
        return self.run_cli(self.argv('recover', fixture,
                            extra=('--journal-id', journal)), **kwargs)

    def kill_cli(self, argv, boundary, **instrumentation):
        request = dict(argv=argv, boundary=list(boundary), **instrumentation)
        child = subprocess.Popen([sys.executable, str(WORKER), json.dumps(request)],
                                 cwd=self.base, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=30), 'Owned child did not reach boundary')
                line = child.stdout.readline()
            if not line:
                output, error = child.communicate(timeout=5)
                self.fail(f'Owned child exited before boundary: {output!r}; {error!r}')
            self.assertEqual(json.loads(line), {'paused': list(boundary)})
            # Never inspect or signal an external process or a process name.
            child.kill()
            output, error = child.communicate(timeout=5)
            self.assertEqual(child.returncode, -signal.SIGKILL, error)
            self.assertEqual(output, '')
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)

    def pending(self, fixture=None):
        report, inventory = self.inventory(fixture)
        self.assertEqual(report['status'], 3, report)
        self.assertEqual(len(inventory['pending']), 1, report)
        return inventory['pending'][0]

    def assert_error(self, report, status):
        self.assertEqual(report['status'], status, report)
        self.assertIs(type(report.get('error')), str, report)
        self.assertTrue(report['error'], report)
        self.assertIs(type(report.get('error_code')), str, report)

    def test_real_cli_proven_repair_exact_report_and_zero_edit_repeat(self):
        f = self.f
        evidence = self.state(f.config), self.state(f.unrelated), self.state(f.pdf)
        before = self.state(f.target)
        report = self.run_cli()
        self.assertEqual(report['status'], 0, report)
        self.assertEqual(report['phase'], 'completed')
        self.assertEqual(report['profile'], 'closed-core-v1')
        self.assertEqual(report['target'], 'main.typ')
        self.assertEqual(report['original_sha256'], hashlib.sha256(ORIGINAL).hexdigest())
        self.assertEqual(report['final_sha256'], hashlib.sha256(EXPECTED).hexdigest())
        self.assertEqual(set(report['required_rule_ids']), set(CORE_RULE_IDS))
        self.assertEqual(len(report['required_rule_ids']), 15)
        self.assertEqual(report['evaluated_rule_ids'], [SCRIPT])
        discharged = report['not_applicable']
        self.assertEqual(len(discharged), 14)
        self.assertEqual({row['rule_id'] for row in discharged}, set(CORE_RULE_IDS) - {SCRIPT})
        self.assertTrue(all(type(row['reason']) is str and row['reason'] for row in discharged))
        self.assertTrue(report['coverage']['input_profile_complete'])
        self.assertFalse(report['coverage']['global_complete'])
        self.assertEqual(report['coverage']['global_rule_status'], 'PARTIAL')
        self.assertEqual(report['coverage']['global_partial_rule_count'], 33)
        self.assertEqual(report['final_findings'], [])
        self.assertEqual(report['modified_paths'], ['main.typ'])
        insertions = report['applied_insertions']
        self.assertEqual(len(insertions), 2)
        replay = ORIGINAL
        for edit in sorted(insertions, key=lambda edit: edit['start'], reverse=True):
            self.assertEqual(edit['path'], 'main.typ')
            self.assertEqual(edit['rule_id'], SCRIPT)
            self.assertEqual(edit['start'], edit['end'])
            self.assertIn(edit['replacement'], ('(', ')'))
            replay = replay[:edit['start']] + edit['replacement'].encode() + replay[edit['end']:]
        self.assertEqual(replay, EXPECTED)
        self.assertEqual(f.target.read_bytes(), EXPECTED)
        self.assertNotEqual(f.target.stat().st_ino, before[2])
        self.assertEqual(self.state(f.target)[3:], before[3:])
        self.assertEqual((self.state(f.config), self.state(f.unrelated), self.state(f.pdf)), evidence)
        self.assertEqual(stat.S_IMODE(f.store.stat().st_mode), 0o700)
        manifests = self.manifests()
        self.assertEqual(len(manifests), 1)
        self.assertEqual(manifests[0].parent.name, report['journal_id'])
        manifest = json.loads(manifests[0].read_bytes())
        self.assertEqual([entry['path'] for entry in manifest['files']], ['main.typ'])
        state = self.tree_state(f.base)
        clean = self.run_cli()
        self.assertEqual(clean['status'], 0, clean)
        self.assertEqual(clean['modified_paths'], [])
        self.assertEqual(clean['applied_insertions'], [])
        self.assertIsNone(clean['journal_id'])
        self.assertEqual(clean['original_sha256'], clean['final_sha256'])
        self.assertEqual(self.tree_state(f.base), state)

    def test_already_clean_does_not_create_a_transaction_journal(self):
        for source in (b'', b'Plain prose.\n', b'$X^(i)$\n'):
            with self.subTest(source=source):
                f = self.fixture(source=source)
                before = self.state(f.target)
                report = self.run_cli(self.argv(fixture=f))
                self.assertEqual(report['status'], 0, report)
                self.assertIsNone(report['journal_id'])
                self.assertEqual(report['modified_paths'], [])
                self.assertEqual(self.manifests(f), [])
                self.assertEqual(self.state(f.target), before)

    @unittest.skipUnless(shutil.which('git'), 'Git required for dirty index preservation')
    def test_dirty_git_index_unselected_bytes_modes_and_existing_pdf_are_preserved(self):
        f = self.f
        subprocess.run(['git', 'init', '-q', str(f.root)], check=True,
                       capture_output=True, timeout=10)
        f.target.write_bytes(b'Indexed earlier user version.\n')
        subprocess.run(['git', '-C', str(f.root), 'add', 'main.typ'], check=True,
                       capture_output=True, timeout=10)
        f.target.write_bytes(ORIGINAL)
        index = f.root / '.git' / 'index'
        before = self.state(index), self.state(f.unrelated), self.state(f.pdf)
        report = self.run_cli()
        self.assertEqual(report['status'], 0, report)
        self.assertEqual(f.target.read_bytes(), EXPECTED)
        self.assertEqual((self.state(index), self.state(f.unrelated), self.state(f.pdf)), before)
        self.assertEqual(stat.S_IMODE(f.target.stat().st_mode), 0o640)

    @unittest.skipUnless(sys.platform == 'darwin' or hasattr(os, 'setxattr'),
                         'Extended attributes unavailable')
    def test_source_extended_attributes_survive_real_cli_replacement(self):
        name = 'com.example.offline-cli-test' if sys.platform == 'darwin' else 'user.offline-cli-test'
        value = b'owned-fixture-xattr\x00\xff'
        if sys.platform == 'darwin':
            # CPython on macOS need not expose os.setxattr. Exercise the real
            # native tool against only our temporary source instead of skipping.
            subprocess.run(['/usr/bin/xattr', '-wx', name, value.hex(), str(self.f.target)],
                           check=True, capture_output=True, timeout=10)

            def attributes():
                return subprocess.run(['/usr/bin/xattr', '-lx', str(self.f.target)],
                    check=True, capture_output=True, timeout=10).stdout
        else:
            try:
                os.setxattr(self.f.target, name, value)
            except OSError as error:
                self.skipTest(f'Fixture filesystem does not support xattrs: {error}')

            def attributes():
                return {key: os.getxattr(self.f.target, key)
                        for key in os.listxattr(self.f.target)}
        before = attributes()
        report = self.run_cli()
        self.assertEqual(report['status'], 0, report)
        self.assertEqual(attributes(), before)

    def test_ordinary_check_dryrun_fix_and_compile_remain_read_only_partial(self):
        f = self.f
        f.config.write_bytes(b'rule-set = "pde"\ninclude = ["main.typ"]\n')
        before = self.tree_state(f.base)
        for command, extra, gate in (('check', (), False), ('fix', (), False),
                ('fix', ('--dry-run',), False), ('compile', (), True),
                ('compile', ('--', 'main.typ', 'main.pdf'), True)):
            with self.subTest(command=command, extra=extra):
                report = self.run_cli(self.argv(command, writer=False, extra=extra), gate=gate)
                self.assertEqual(report['status'], 2, report)
                self.assertFalse(report['coverage']['complete'])
                self.assertEqual(len(report['coverage']['enabled']), 33)
                self.assertEqual(report['applied_fixes'], [])
                self.assertEqual(self.tree_state(f.base), before)

    def test_old_routes_accept_roots_named_like_new_commands(self):
        for name in ('recover', 'journal-status'):
            f = self.fixture(root_name=name)
            before = self.tree_state(f.base)
            for command, gate in (('check', False), ('fix', False), ('compile', True)):
                for option_first in (False, True):
                    with self.subTest(root=name, command=command, option_first=option_first):
                        options = ['--root', name, '--format', 'json']
                        argv = options + [command] if option_first else [command] + options
                        report = self.run_cli(argv, gate=gate, cwd=f.base)
                        self.assertEqual(report['status'], 2, report)
                        self.assertEqual(report['mode'], command)
                        self.assertIn('coverage', report)
                        self.assertNotIn('error', report)
                        self.assertEqual(self.tree_state(f.base), before)

    def test_no_typst_compiler_is_ever_executed(self):
        bin_directory = self.f.base / 'bin'
        bin_directory.mkdir()
        marker = self.f.base / 'compiler-was-invoked'
        compiler = bin_directory / 'typst'
        compiler.write_text(f'#!/bin/sh\nprintf bad > "{marker}"\nexit 99\n')
        compiler.chmod(0o700)
        env = {'PATH': str(bin_directory) + os.pathsep + os.environ.get('PATH', '')}
        self.assertEqual(self.run_cli(env=env)['status'], 0)
        report = self.run_cli(self.argv('compile', writer=False,
                                      extra=('--', 'main.typ', 'main.pdf')), gate=True, env=env)
        self.assertEqual(report['status'], 2)
        self.assertFalse(marker.exists())
        self.assertEqual(self.f.pdf.read_bytes(), b'%PDF-preserve-existing-output\x00\xff')

    def test_missing_optins_and_unsupported_flag_combinations_are_typed_errors(self):
        base = self.argv(writer=False)
        full = self.argv()
        cases = [base + options for options in (
            ['--profile', 'closed-core-v1'], ['--offline-exclusive'], ['--target', 'main.typ'],
            ['--profile', 'closed-core-v1', '--target', 'main.typ'],
            ['--profile', 'closed-core-v1', '--offline-exclusive'],
            ['--offline-exclusive', '--target', 'main.typ'],
            ['--profile', 'different', '--offline-exclusive', '--target', 'main.typ'])]
        cases += [full + options for options in (
            ['--dry-run'], ['--package-path', str(self.f.base)],
            ['--journal-id', '0' * 32], ['--journal-directory', str(self.f.base / 'elsewhere')],
            ['--journal-store', str(self.f.base / 'elsewhere')], ['--force'],
            ['--', 'main.typ'], ['unexpected.typ'])]
        for command in ('check', 'journal-status'):
            for options in (['--profile', 'closed-core-v1'], ['--offline-exclusive'],
                            ['--target', 'main.typ'], ['--journal-id', '0' * 32]):
                cases.append(self.argv(command, writer=False, extra=options))
        recovery = self.argv('recover', writer=False)
        cases += [recovery + options for options in (
            [], ['--offline-exclusive'], ['--target', 'main.typ'],
            ['--journal-id', '0' * 32], ['--offline-exclusive', '--target', 'main.typ'],
            ['--offline-exclusive', '--journal-id', '0' * 32],
            ['--target', 'main.typ', '--journal-id', '0' * 32])]
        full_recovery = self.argv('recover', extra=('--journal-id', '0' * 32))
        cases += [full_recovery + options for options in (
            ['--profile', 'closed-core-v1'], ['--dry-run'], ['--package-path', str(self.f.base)],
            ['--force'], ['--preserve-conflicts', 'main.typ'], ['--migrate'], ['--', 'main.typ'])]
        before = self.tree_state(self.f.base)
        for argv in cases:
            with self.subTest(argv=argv):
                self.assert_error(self.run_cli(argv), 2)
                self.assertEqual(self.tree_state(self.f.base), before)

    def test_new_options_on_compile_gate_and_raw_tails_never_silently_enable_writer(self):
        before = self.tree_state(self.f.base)
        for options in (('--profile', 'closed-core-v1'), ('--offline-exclusive',),
                        ('--target', 'main.typ'), ('--journal-id', '0' * 32)):
            for tail in (False, True):
                with self.subTest(options=options, tail=tail):
                    extra = (('--',) if tail else ()) + options
                    report = self.run_cli(self.argv('compile', writer=False, extra=extra), gate=True)
                    self.assert_error(report, 2)
                    self.assertEqual(self.tree_state(self.f.base), before)

    def test_duplicate_closed_profile_options_are_rejected_without_overriding(self):
        before = self.tree_state(self.f.base)
        for option in (('--profile', 'closed-core-v1'), ('--target', 'main.typ'),
                       ('--root', str(self.f.root)), ('--offline-exclusive',),
                       ('--format', 'json')):
            with self.subTest(option=option):
                self.assert_error(self.run_cli(self.argv(extra=option)), 2)
                self.assertEqual(self.tree_state(self.f.base), before)

    def test_config_boundary_rejections_preserve_sources_and_output(self):
        configs = (None, b'', b'rule-set="core"\n', b'include=["main.typ"]\n',
            b'rule-set="pde"\ninclude=["main.typ"]\n',
            b'rule-set="core"\ninclude=["**/*.typ"]\n',
            b'rule-set="core"\ninclude=["main.typ","other.typ"]\n',
            b'rule-set="core"\ninclude=["other.typ"]\n',
            CONFIG + b'exclude=[]\n', CONFIG + b'ignore=[]\n',
            CONFIG + b'catalog="other.json"\n', CONFIG + b'entrypoint="other.typ"\n',
            CONFIG + b'[capitalization]\nlanguage="en"\n',
            CONFIG + b'unknown=true\n', b'\xff', b'rule-set = [')
        for config in configs:
            with self.subTest(config=config):
                f = self.fixture(config=config)
                before = self.state(f.target), self.state(f.unrelated), self.state(f.pdf)
                self.assert_error(self.run_cli(self.argv(fixture=f)), 2)
                self.assertEqual((self.state(f.target), self.state(f.unrelated), self.state(f.pdf)), before)
                self.assertEqual(self.manifests(f), [])

    def test_matching_literal_entrypoint_and_nested_target_are_accepted(self):
        f = self.fixture(config=CONFIG + b'entrypoint="main.typ"\n')
        self.assertEqual(self.run_cli(self.argv(fixture=f))['status'], 0)
        f = self.fixture(config=b'rule-set="core"\ninclude=["chapters/main.typ"]\n')
        nested = f.root / 'chapters'
        nested.mkdir()
        f.target.rename(nested / 'main.typ')
        args = self.argv(fixture=f)
        args[args.index('--target') + 1] = 'chapters/main.typ'
        report = self.run_cli(args)
        self.assertEqual(report['status'], 0, report)
        self.assertEqual(report['modified_paths'], ['chapters/main.typ'])
        self.assertEqual((nested / 'main.typ').read_bytes(), EXPECTED)

    def test_closed_grammar_rejects_executable_or_unsupported_typst(self):
        sources = (b'#import "missing.typ": *\n$X^i$\n', b'#include "missing.typ"\n',
            b'#import "@preview/cetz:0.3.4": *\n', b'#read("secrets")\n',
            b'#let value = 1\n', b'#set text(size: 12pt)\n', b'#show: it => it\n',
            b'// a comment\n$X^i$\n', b'/* a comment */\n$X^i$\n',
            b'#metadata("value") <label>\n', b'@label\n', b'$sqrt(x)$\n',
            b'$x^$\n', b'\xff')
        for source in sources:
            with self.subTest(source=source):
                f = self.fixture(source=source)
                before = self.state(f.target), self.state(f.unrelated), self.state(f.pdf)
                self.assert_error(self.run_cli(self.argv(fixture=f)), 2)
                self.assertEqual((self.state(f.target), self.state(f.unrelated), self.state(f.pdf)), before)
                self.assertEqual(self.manifests(f), [])

    def test_adapter_pin_schema_ast_and_json_failures_are_typed_input_errors(self):
        for fault in ('pin', 'schema', 'ast', 'invalid-json'):
            with self.subTest(fault=fault):
                f = self.fixture()
                before = self.state(f.target)
                report = self.run_cli(self.argv(fixture=f), worker={'adapter_fault': fault})
                self.assert_error(report, 2)
                self.assertEqual(self.state(f.target), before)
                self.assertEqual(self.manifests(f), [])

    def test_changed_full_catalog_pin_is_rejected_before_any_source_replacement(self):
        catalog = self.f.base / 'changed-catalog.json'
        catalog.write_bytes((TOOL / 'rules' / 'catalog.json').read_bytes() + b'\n')
        before = self.state(self.f.target), self.state(self.f.unrelated), self.state(self.f.pdf)
        report = self.run_cli(worker={'catalog': str(catalog)})
        self.assert_error(report, 2)
        self.assertEqual(report['error_code'], 'invalid_catalog')
        self.assertEqual((self.state(self.f.target), self.state(self.f.unrelated), self.state(self.f.pdf)), before)
        self.assertEqual(self.manifests(), [])

    def test_target_allowlist_and_path_boundaries_are_enforced(self):
        for target in ('../main.typ', './main.typ', '/main.typ', '**/*.typ',
                       'main.md', 'a//main.typ', 'a\\main.typ'):
            with self.subTest(target=target):
                f = self.fixture()
                before = self.tree_state(f.base)
                args = self.argv(fixture=f)
                args[args.index('--target') + 1] = target
                self.assert_error(self.run_cli(args), 2)
                self.assertEqual(self.tree_state(f.base), before)
        # This is a valid literal path, but the captured config does not select
        # it. Empty storage may be initialized after the explicit assertion;
        # no source or journal is modified by the profile refusal.
        f = self.fixture()
        (f.root / 'other.typ').write_bytes(b'Other user work. $Y_j$\n')
        before = self.state(f.target), self.state(f.root / 'other.typ')
        args = self.argv(fixture=f)
        args[args.index('--target') + 1] = 'other.typ'
        self.assert_error(self.run_cli(args), 2)
        self.assertEqual((self.state(f.target), self.state(f.root / 'other.typ')), before)
        self.assertEqual(self.manifests(f), [])

    def test_symlink_hardlink_and_readonly_sources_are_unsafe(self):
        for kind in ('symlink', 'hardlink', 'readonly'):
            with self.subTest(kind=kind):
                f = self.fixture()
                if kind == 'symlink':
                    real = f.base / 'real.typ'
                    f.target.rename(real)
                    f.target.symlink_to(real)
                elif kind == 'hardlink':
                    os.link(f.target, f.base / 'extra-link.typ')
                else:
                    f.target.chmod(0o440)
                before = self.state(f.target), self.state(f.unrelated)
                self.assert_error(self.run_cli(self.argv(fixture=f)), 3)
                self.assertEqual((self.state(f.target), self.state(f.unrelated)), before)
                self.assertEqual(self.manifests(f), [])

    def test_retained_setuid_source_is_unsafe_and_preserves_its_mode(self):
        f = self.f
        f.target.chmod(0o4640)
        # Some fixture filesystems do not retain the requested special bit.
        # Only that observed precondition can skip this test; once retained,
        # refusal and exact source/metadata preservation remain mandatory.
        if not f.target.stat().st_mode & stat.S_ISUID:
            self.skipTest('Filesystem did not retain the requested setuid bit')
        before = self.state(f.target), self.state(f.unrelated)
        self.assertEqual(before[0][3], 0o4640)
        self.assert_error(self.run_cli(), 3)
        self.assertEqual((self.state(f.target), self.state(f.unrelated)), before)
        self.assertEqual(stat.S_IMODE(f.target.stat().st_mode), 0o4640)
        self.assertEqual(self.manifests(f), [])

    def test_inventory_is_genuinely_readonly_missing_store_or_namespace(self):
        for existing_store in (False, True):
            with self.subTest(existing_store=existing_store):
                f = self.fixture()
                # Neither source presence nor current profile config is needed
                # to report an absent store or uninitialized namespace.
                f.target.unlink()
                f.config.unlink()
                if existing_store:
                    f.store.mkdir(mode=0o700)
                before = self.tree_state(f.base)
                report, inventory = self.inventory(f, worker={'forbid_offline_assert': True})
                self.assertEqual(report['status'], 0, report)
                self.assertEqual(inventory['store'], str(f.store))
                self.assertEqual(inventory['namespace'], str(f.namespace))
                self.assertEqual(inventory['store_present'], existing_store)
                self.assertFalse(inventory['namespace_present'])
                self.assertEqual(inventory['pending'], [])
                self.assertEqual(inventory['entries'], [])
                self.assertEqual(self.tree_state(f.base), before)

    def test_inventory_refuses_a_busy_root_without_creating_or_asserting_anything(self):
        before = self.tree_state(self.f.base)
        root_fd = os.open(self.f.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(root_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            report = self.run_cli(self.argv('journal-status', writer=False),
                                  worker={'forbid_offline_assert': True})
        finally:
            os.close(root_fd)
        self.assert_error(report, 3)
        self.assertEqual(self.tree_state(self.f.base), before)

    def test_inventory_of_committed_journal_never_rewrites_source_or_storage(self):
        fixed = self.run_cli()
        self.assertEqual(fixed['status'], 0, fixed)
        self.f.target.write_bytes(b'Later user edits must survive inventory.\n')
        before = self.tree_state(self.f.base)
        report, inventory = self.inventory(worker={'forbid_offline_assert': True})
        self.assertEqual(report['status'], 0, report)
        self.assertEqual(inventory['pending'], [])
        self.assertEqual(inventory['entries'], [dict(journal=fixed['journal_id'],
                                                   status='committed', error=None,
                                                   targets=['main.typ'])])
        self.assertEqual(self.tree_state(self.f.base), before)
        # Recovery of a terminal journal must also be a no-op, rather than
        # reapplying its old preimage over newer work.
        self.assertEqual(self.recover(fixed['journal_id'])['status'], 0)
        self.assertEqual(self.tree_state(self.f.base), before)

    def test_unsafe_fixed_stores_and_namespaces_fail_closed_without_repairing_them(self):
        for kind in ('store-mode', 'store-symlink', 'store-file',
                     'namespace-mode', 'namespace-symlink', 'namespace-file'):
            with self.subTest(kind=kind):
                f = self.fixture()
                if kind == 'store-mode':
                    f.store.mkdir(mode=0o755)
                elif kind == 'store-symlink':
                    other = f.base / 'other'
                    other.mkdir(mode=0o700)
                    f.store.symlink_to(other, target_is_directory=True)
                elif kind == 'store-file':
                    f.store.write_bytes(b'not a directory')
                else:
                    f.store.mkdir(mode=0o700)
                    if kind == 'namespace-mode':
                        f.namespace.mkdir(mode=0o755)
                    elif kind == 'namespace-symlink':
                        other = f.base / 'other'
                        other.mkdir(mode=0o700)
                        f.namespace.symlink_to(other, target_is_directory=True)
                    else:
                        f.namespace.write_bytes(b'not a directory')
                before = self.tree_state(f.base)
                self.assert_error(self.run_cli(self.argv(fixture=f)), 3)
                self.assertEqual(self.tree_state(f.base), before)
                self.assert_error(self.run_cli(self.argv('journal-status', f, writer=False)), 3)
                self.assertEqual(self.tree_state(f.base), before)

    def test_fixed_store_does_not_follow_environment_or_cli_overrides(self):
        alternate = self.f.base / 'alternate-journals'
        env = {name: str(alternate) for name in (
            'TYPST_LINT_JOURNAL_DIR', 'TYPST_LINT_JOURNAL_DIRECTORY',
            'TYPST_LINT_JOURNAL_STORE', 'XDG_STATE_HOME', 'XDG_CACHE_HOME')}
        report = self.run_cli(env=env)
        self.assertEqual(report['status'], 0, report)
        self.assertTrue(self.f.namespace.is_dir())
        self.assertFalse(alternate.exists())
        status, inventory = self.inventory(env=env)
        self.assertEqual(status['status'], 0)
        self.assertEqual(inventory['store'], str(self.f.store))
        self.assertFalse(alternate.exists())
        before = self.tree_state(self.f.base)
        self.assert_error(self.run_cli(self.argv(extra=(
            '--journal-directory', str(alternate))), env=env), 2)
        self.assertEqual(self.tree_state(self.f.base), before)

    def test_corrupt_journal_remains_visible_and_blocks_new_fix(self):
        fixed = self.run_cli()
        self.assertEqual(fixed['status'], 0, fixed)
        manifest = self.manifests()[0]
        manifest.write_bytes(b'{corrupt journal')
        self.f.target.write_bytes(ORIGINAL)
        before = self.tree_state(self.f.base)
        report, inventory = self.inventory()
        self.assertEqual(report['status'], 3, report)
        self.assertEqual(inventory['pending'], [fixed['journal_id']])
        self.assertEqual(inventory['entries'][0]['status'], 'unreadable-or-corrupt')
        self.assertEqual(inventory['entries'][0]['targets'], [])
        self.assertTrue(inventory['entries'][0]['error'])
        blocked = self.run_cli()
        self.assert_error(blocked, 3)
        self.assertIn(fixed['journal_id'], blocked['recovery_required']['journals'])
        self.assertEqual(self.tree_state(self.f.base), before)
        self.assert_error(self.recover(fixed['journal_id']), 3)
        self.assertEqual(self.tree_state(self.f.base), before)

    def test_explicit_recovery_of_absent_store_namespace_or_journal_creates_nothing(self):
        for existing in ('nothing', 'store', 'namespace'):
            with self.subTest(existing=existing):
                f = self.fixture(config=None)
                if existing != 'nothing':
                    f.store.mkdir(mode=0o700)
                if existing == 'namespace':
                    f.namespace.mkdir(mode=0o700)
                before = self.tree_state(f.base)
                self.assert_error(self.recover('0' * 32, f), 3)
                self.assertEqual(self.tree_state(f.base), before)

    def test_source_config_and_catalog_races_preserve_latest_user_bytes(self):
        for kind in ('source', 'source-same-bytes-new-inode', 'config', 'catalog'):
            with self.subTest(kind=kind):
                f = self.fixture()
                catalog = f.base / 'reviewed-catalog.json'
                catalog.write_bytes((TOOL / 'rules' / 'catalog.json').read_bytes())
                destination = {'source': f.target, 'source-same-bytes-new-inode': f.target,
                               'config': f.config, 'catalog': catalog}[kind]
                data = (ORIGINAL if kind == 'source-same-bytes-new-inode' else
                        b'Latest user bytes.\n' if kind == 'source' else
                        CONFIG + b'entrypoint="main.typ"\n' if kind == 'config' else
                        catalog.read_bytes() + b'\n')
                other = self.state(f.unrelated), self.state(f.pdf)
                report = self.run_cli(self.argv(fixture=f), worker=dict(
                    catalog=str(catalog), mutate_boundary=['before_revalidate', None],
                    mutate_path=str(destination), mutate_hex=data.hex(),
                    replace_inode=kind == 'source-same-bytes-new-inode'))
                self.assert_error(report, 3)
                self.assertEqual(destination.read_bytes(), data)
                if destination != f.target:
                    self.assertEqual(f.target.read_bytes(), ORIGINAL)
                self.assertEqual((self.state(f.unrelated), self.state(f.pdf)), other)

    def test_sigkill_before_replace_is_discovered_and_explicitly_recovered(self):
        before = self.state(self.f.target)
        self.kill_cli(self.argv(), ('before_replace', 'main.typ'))
        self.assertEqual(self.state(self.f.target), before)
        journal = self.pending()
        blocked = self.run_cli()
        self.assert_error(blocked, 3)
        self.assertEqual(blocked['recovery_required']['journals'], [journal])
        self.assertEqual(self.state(self.f.target), before)
        report = self.recover(journal)
        self.assertEqual(report['status'], 0, report)
        self.assertTrue(report['recovery_report']['complete'])
        self.assertEqual(report['recovery_report']['restored'], [])
        self.assertEqual(report['recovery_report']['unchanged'], ['main.typ'])
        self.assertEqual(self.state(self.f.target), before)
        self.assertEqual(self.run_cli()['status'], 0)

    def test_sigkill_after_replace_recovers_after_config_removed_or_changed(self):
        for config in (None, b'rule-set="pde"\ninclude=["**/*.typ"]\n'):
            with self.subTest(config=config):
                f = self.fixture()
                untouched = self.state(f.unrelated), self.state(f.pdf)
                self.kill_cli(self.argv(fixture=f), ('after_replace', 'main.typ'))
                self.assertEqual(f.target.read_bytes(), EXPECTED)
                journal = self.pending(f)
                if config is None:
                    f.config.unlink()
                else:
                    f.config.write_bytes(config)
                blocked = self.run_cli(self.argv(fixture=f))
                self.assert_error(blocked, 3)
                self.assertEqual(blocked['recovery_required']['journals'], [journal])
                self.assertEqual(f.target.read_bytes(), EXPECTED)
                report = self.recover(journal, f)
                self.assertEqual(report['status'], 0, report)
                self.assertEqual(report['recovery_report']['restored'], ['main.typ'])
                self.assertTrue(report['recovery_report']['complete'])
                self.assertEqual(f.target.read_bytes(), ORIGINAL)
                self.assertEqual((self.state(f.unrelated), self.state(f.pdf)), untouched)
                if config is None:
                    self.assertFalse(f.config.exists())
                else:
                    self.assertEqual(f.config.read_bytes(), config)
                before = self.tree_state(f.base)
                repeated = self.recover(journal, f)
                self.assertEqual(repeated['status'], 0, repeated)
                self.assertEqual(self.tree_state(f.base), before)

    def test_sigkill_during_recovery_retains_already_restored_source_inode_on_restart(self):
        self.kill_cli(self.argv(), ('after_replace', 'main.typ'))
        journal = self.pending()
        args = self.argv('recover', extra=('--journal-id', journal))
        self.kill_cli(args, ('after_restore', 'main.typ'))
        restored = self.state(self.f.target)
        self.assertEqual(restored[0], ORIGINAL)
        self.assertEqual(self.pending(), journal)
        report = self.recover(journal)
        self.assertEqual(report['status'], 0, report)
        self.assertEqual(report['recovery_report']['restored'], [])
        self.assertEqual(report['recovery_report']['unchanged'], ['main.typ'])
        self.assertEqual(self.state(self.f.target), restored)
        self.assertEqual(self.inventory()[0]['status'], 0)

    def test_later_edit_conflicts_and_same_bytes_new_inode_are_never_overwritten(self):
        for kind in ('later-user-edit', 'same-postimage-new-inode'):
            with self.subTest(kind=kind):
                f = self.fixture()
                self.kill_cli(self.argv(fixture=f), ('after_replace', 'main.typ'))
                journal = self.pending(f)
                if kind == 'later-user-edit':
                    f.target.write_bytes(b'Later user work must survive recovery.\n')
                else:
                    replacement = f.root / 'owned-user-save'
                    replacement.write_bytes(f.target.read_bytes())
                    replacement.chmod(0o640)
                    replacement.replace(f.target)
                before = self.state(f.target), self.state(f.unrelated), self.state(f.pdf)
                report = self.recover(journal, f)
                self.assertEqual(report['status'], 3, report)
                self.assertFalse(report['recovery_report']['complete'])
                self.assertEqual(report['recovery_report']['changed'], ['main.typ'])
                self.assertEqual((self.state(f.target), self.state(f.unrelated), self.state(f.pdf)), before)
                self.assertEqual(self.pending(f), journal)
                self.assert_error(self.run_cli(self.argv(fixture=f)), 3)
                self.assertEqual((self.state(f.target), self.state(f.unrelated), self.state(f.pdf)), before)

    def test_recovery_requires_matching_journal_root_and_exact_target_allowlist(self):
        self.kill_cli(self.argv(), ('after_replace', 'main.typ'))
        journal = self.pending()
        (self.f.root / 'other.typ').write_bytes(b'Other work.\n')
        before = self.tree_state(self.f.base)
        args = self.argv('recover', extra=('--journal-id', journal))
        args[args.index('--target') + 1] = 'other.typ'
        self.assert_error(self.run_cli(args), 3)
        self.assertEqual(self.tree_state(self.f.base), before)
        other = self.f.base / 'different-consumer'
        other.mkdir()
        (other / 'main.typ').write_bytes(b'Other root work.\n')
        args = self.argv('recover', extra=('--journal-id', journal))
        args[args.index('--root') + 1] = str(other)
        source_before = self.state(self.f.target), self.state(other / 'main.typ')
        self.assert_error(self.run_cli(args), 3)
        self.assertEqual((self.state(self.f.target), self.state(other / 'main.typ')), source_before)
        self.assertEqual(self.recover(journal)['status'], 0)


if __name__ == '__main__':
    unittest.main()
