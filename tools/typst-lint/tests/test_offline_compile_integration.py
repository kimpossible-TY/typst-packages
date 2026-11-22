"""Independent actual-process tests for the narrow explicit compile/output CLI.

The parser, protected source fixes, output journal and process runner are real.
Only temporary owned fake compiler bytes enter the private reviewed-artifact seam.
No actual consumer, install, wrapper rollout or global conformance claim is made.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from syntax_client import EXECUTABLE

CLI = TOOL / 'typst-lint'
STYLE = TOOL / 'typst-style'
WORKER = Path(__file__).with_name('offline_compile_process_worker.py')
ORIGINAL = b'Preserved ASCII prose. $X^i + Y_j$\r\n'
EXPECTED = b'Preserved ASCII prose. $X^(i) + Y_(j)$\r\n'
OLD_OUTPUT = b'Arbitrary previous output, not a PDF.\x00\xff\r\n'
PINNED = Path('/opt/homebrew/Cellar/typst/0.15.1/bin/typst')
PINNED_SHA256 = '08d01e5a434e821c4a1ce30dd1593e1bbc57c9b1203c4d760d8912ef4ce6a621'


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


class _CompileFixtures(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='typst-compile-cli-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.serial = 0
        self.f = self.fixture()

    def fixture(self, *, source=ORIGINAL, target='main.typ',
                output='build/main.pdf', present=True, mode=0o644,
                root_name='owned consumer', config=True):
        self.serial += 1
        base = self.base / str(self.serial)
        root = base / root_name
        root.mkdir(parents=True)
        src = root / target
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(source)
        src.chmod(0o640)
        cfg = root / '.typst-lint.toml'
        if config:
            cfg.write_bytes(('rule-set = "core"\ninclude = [' + json.dumps(target) + ']\n').encode())
        pdf = root / output
        pdf.parent.mkdir(parents=True, exist_ok=True)
        if present:
            pdf.write_bytes(OLD_OUTPUT)
            pdf.chmod(mode)
        unrelated = root / 'unselected.typ'
        unrelated.write_bytes(b'\xff dirty user work #read("never-open")\n')
        namespace = 'root-' + sha256(os.fsencode(root))
        return SimpleNamespace(base=base, root=root, source=src, target=target,
            output=output, pdf=pdf, config=cfg, unrelated=unrelated,
            source_store=base / '.typst-lint-journals',
            source_namespace=base / '.typst-lint-journals' / namespace,
            output_store=base / '.typst-lint-pdf-journals',
            output_namespace=base / '.typst-lint-pdf-journals' / namespace,
            compiler_marker=base / 'compile-started',
            ready=str(base / 'ready-'), release=str(base / 'release-'))

    def argv(self, command='compile', f=None, *, format='json', output=None):
        f = f or self.f
        args = [command, '--root', str(f.root), '--format', format]
        if command == 'compile':
            args += ['--profile', 'closed-core-v1', '--resource-profile', 'embedded-ascii-v1',
                     '--offline-exclusive', '--offline-output-exclusive',
                     '--target', f.target, '--output', f.output if output is None else output]
        return args

    def request(self, f=None, argv=None, **options):
        f = f or self.f
        result = dict(base=str(f.base), root=str(f.root), target=f.target,
            output=f.output, compiler_marker=str(f.compiler_marker),
            ready=f.ready, release=f.release, original_hex=f.source.read_bytes().hex() if f.source.exists() else '',
            argv=self.argv(f=f) if argv is None else argv)
        result.update(options)
        return result

    def run_process(self, f=None, argv=None, *, actual=False, gate=False, **options):
        f = f or self.f
        if actual:
            command = [sys.executable, str(STYLE if gate else CLI),
                       *(self.argv(f=f) if argv is None else argv)]
        else:
            request = self.request(f, argv, gate=gate, **options)
            command = [sys.executable, str(WORKER), json.dumps(request)]
        return subprocess.run(command, cwd=f.base,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'),
            capture_output=True, timeout=90)

    def run_json(self, f=None, argv=None, **options):
        result = self.run_process(f, argv, **options)
        self.assertEqual(result.stderr, b'', result.stderr.decode(errors='replace'))
        try:
            report = json.loads(result.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.fail(f'No complete JSON from real subprocess: rc={result.returncode}; '
                      f'stdout={result.stdout!r}; stderr={result.stderr!r}')
        self.assertIs(type(report), dict)
        self.assertIs(type(report['status']), int)
        self.assertEqual(report['status'], result.returncode, report)
        # Explicit route JSON is locale-independent and cannot leak raw high bytes.
        self.assertTrue(result.stdout.isascii(), result.stdout)
        return report

    def file_state(self, path):
        s = path.lstat()
        data = os.readlink(path) if stat.S_ISLNK(s.st_mode) else path.read_bytes() if stat.S_ISREG(s.st_mode) else None
        return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid,
                s.st_size, s.st_mtime_ns, data)

    def tree_state(self, root):
        return {str(p.relative_to(root)): self.file_state(p)
                for p in [root, *sorted(root.rglob('*'))]}

    def assert_lock_free(self, f=None):
        fd = os.open((f or self.f).root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)

    def journals(self, f=None, *, source=False):
        f = f or self.f
        return sorted((f.source_namespace if source else f.output_namespace).glob('*/manifest.json'))

    def recover_argv(self, journal, f=None, *, output=None):
        f = f or self.f
        return self.argv('recover-output', f) + ['--offline-output-exclusive',
            '--output', f.output if output is None else output, '--journal-id', journal]

    def assert_no_compile(self, report, f=None):
        self.assertIs(report['compiler_invoked'], False, report)
        self.assertIsNone(report['compiler_status'], report)
        self.assertFalse((f or self.f).compiler_marker.exists())

    def publication(self, report):
        value = report['publication_report']
        self.assertIs(type(value), dict)
        return value


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX publication protocol')
class OfflineCompileIntegrationTests(_CompileFixtures):
    def test_success_existing_absent_nested_spaces_bom_and_modes(self):
        for present, mode, bom in ((True, 0o644, b''), (True, 0o600, b'\xef\xbb\xbf'), (False, 0o600, b'')):
            with self.subTest(present=present, mode=mode, bom=bom):
                f = self.fixture(source=bom + ORIGINAL, present=present, mode=mode,
                    target='nested source/source file.typ', output='build with spaces/nested/document file.pdf')
                untouched = self.file_state(f.unrelated), self.file_state(f.config)
                report = self.run_json(f, assert_order=True)
                self.assertEqual(report['status'], 0, report)
                self.assertIs(report['compiler_invoked'], True)
                self.assertEqual(report['compiler_status'], 0)
                pub = self.publication(report)
                self.assertEqual(pub['phase'], 'published')
                self.assertEqual(pub['output_disposition'], 'published')
                self.assertFalse(pub['recovery_required'])
                self.assertEqual(pub['source_commit'], 'committed')
                self.assertEqual(f.source.read_bytes(), bom + EXPECTED)
                self.assertIn((bom + EXPECTED).hex().encode(), f.pdf.read_bytes())
                self.assertEqual(stat.S_IMODE(f.pdf.stat().st_mode), mode)
                self.assertEqual((self.file_state(f.unrelated), self.file_state(f.config)), untouched)
                self.assertEqual(len(self.journals(f)), 1)
                self.assertEqual(len(self.journals(f, source=True)), 1)
                summary = report['source_summary']
                self.assertEqual(summary['coverage']['global_partial_rule_count'], 33)
                self.assertFalse(summary['coverage']['global_complete'])
                self.assertEqual(len(summary['required_rule_ids']), 15)
                self.assert_lock_free(f)

    def test_clean_source_no_source_journal_but_pdf_published(self):
        f = self.fixture(source=EXPECTED)
        before = self.file_state(f.source)
        report = self.run_json(f)
        self.assertEqual(report['status'], 0, report)
        self.assertEqual(self.file_state(f.source), before)
        self.assertEqual(self.journals(f, source=True), [])
        self.assertEqual(len(self.journals(f)), 1)

    def test_success_text_is_actual_process_and_truthful(self):
        result = self.run_process(argv=self.argv(format='text'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b'')
        self.assertIn(b'published', result.stdout.lower())
        self.assertNotIn(b'compiler not invoked', result.stdout.lower())
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assertTrue(self.f.pdf.read_bytes().startswith(b'%PDF-1.7'))

    def test_required_options_reject_before_leases_or_writes(self):
        full = self.argv()
        for option in ('--profile', '--resource-profile', '--offline-exclusive',
                       '--offline-output-exclusive', '--target', '--output'):
            argv = list(full)
            index = argv.index(option)
            del argv[index:index + (1 if option in ('--offline-exclusive', '--offline-output-exclusive') else 2)]
            with self.subTest(option=option):
                before = self.tree_state(self.f.root)
                report = self.run_json(argv=argv, forbid_leases=True)
                self.assertEqual(report['status'], 2, report)
                self.assert_no_compile(report)
                self.assertEqual(self.tree_state(self.f.root), before)
                self.assertFalse(self.f.source_store.exists())
                self.assertFalse(self.f.output_store.exists())

    def test_duplicates_abbreviations_unknown_arguments_and_tails_refuse(self):
        options = [('--root', str(self.f.root)), ('--profile', 'closed-core-v1'),
                   ('--resource-profile', 'embedded-ascii-v1'), ('--target', 'main.typ'),
                   ('--output', 'build/main.pdf'), ('--format', 'json'),
                   ('--offline-exclusive',), ('--offline-output-exclusive',),
                   ('--out', 'build/main.pdf'), ('--resource-prof', 'embedded-ascii-v1'),
                   ('--compiler', '/bin/true'), ('--deadline', '120'), ('--force',),
                   ('--', 'main.typ', 'build/main.pdf'), ('extra.typ',),
                   ('--output=build/main.pdf',), ('--dry-run',), ('--package-path', '/tmp')]
        before = self.tree_state(self.f.root)
        for extra in options:
            with self.subTest(extra=extra):
                report = self.run_json(argv=self.argv() + list(extra), forbid_leases=True)
                self.assertEqual(report['status'], 2, report)
                self.assert_no_compile(report)
                self.assertEqual(self.tree_state(self.f.root), before)
                self.assertFalse(self.f.source_store.exists())
                self.assertFalse(self.f.output_store.exists())

    def test_wrong_profiles_reject_before_lease(self):
        for option, value in (('--profile', 'core'), ('--resource-profile', 'ordinary-fonts')):
            argv = self.argv()
            argv[argv.index(option) + 1] = value
            report = self.run_json(argv=argv, forbid_leases=True)
            self.assertEqual(report['status'], 2, report)
            self.assert_no_compile(report)

    def test_command_names_as_values_do_not_dispatch_writer(self):
        for name in ('compile', 'recover-output', 'output-status'):
            with self.subTest(name=name):
                f = self.fixture(root_name=name, target=name + '/main.typ', output=name + '/main.pdf')
                # Every command-like component remains an option value or literal path.
                report = self.run_json(f, assert_order=True)
                self.assertEqual(report['status'], 0, report)
                self.assertEqual(f.source.read_bytes(), EXPECTED)
                for command in ('check', 'fix'):
                    before = self.tree_state(f.root)
                    argv = ['--root', name, '--format', 'json', command]
                    result = subprocess.run([sys.executable, str(CLI), *argv], cwd=f.base,
                        capture_output=True, timeout=30)
                    payload = json.loads(result.stdout)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(payload['mode'], command, payload)
                    self.assertEqual(self.tree_state(f.root), before)

    def test_invalid_literal_outputs_have_no_source_application(self):
        for output in ('', '.', '../outside.pdf', '/tmp/outside.pdf', './a.pdf',
                       'build/../a.pdf', 'build//a.pdf', 'build/a.PDF', 'build/*.pdf', 'build/a.pdf/'):
            with self.subTest(output=output):
                report = self.run_json(argv=self.argv(output=output))
                self.assertEqual(report['status'], 2, report)
                self.assert_no_compile(report)
                self.assertEqual(self.f.source.read_bytes(), ORIGINAL)
                self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)

    def test_missing_parent_and_unsafe_outputs_refuse_before_apply(self):
        for kind in ('missing-parent', 'symlink', 'hardlink', 'directory', 'fifo', 'mode'):
            with self.subTest(kind=kind):
                f = self.fixture()
                if kind == 'missing-parent':
                    output = 'absent-parent/a.pdf'
                else:
                    output = f.output
                    if kind in ('symlink', 'directory', 'fifo'):
                        f.pdf.unlink()
                    if kind == 'symlink':
                        f.pdf.symlink_to(f.unrelated)
                    elif kind == 'hardlink':
                        (f.root / 'alias.pdf').hardlink_to(f.pdf)
                    elif kind == 'directory':
                        f.pdf.mkdir()
                    elif kind == 'fifo':
                        os.mkfifo(f.pdf)
                    elif kind == 'mode':
                        f.pdf.chmod(0o666)
                before = self.tree_state(f.root)
                report = self.run_json(f, argv=self.argv(f=f, output=output))
                self.assertEqual(report['status'], 3, report)
                self.assert_no_compile(report, f)
                self.assertEqual(f.source.read_bytes(), ORIGINAL)
                self.assertEqual(self.tree_state(f.root), before)
                self.assertEqual(self.journals(f, source=True), [])

    def test_unsupported_config_grammar_encoding_and_resources_are_input_refusals(self):
        cases = [('config', ORIGINAL), ('grammar', b'#read("never-open")\n'),
                 ('encoding', b'\xff invalid UTF-8\n'), ('resources', 'Unicode Korean 한글. $X^i$\n'.encode())]
        for kind, source in cases:
            with self.subTest(kind=kind):
                f = self.fixture(source=source)
                if kind == 'config':
                    f.config.write_bytes(b'rule-set = "pde"\ninclude = ["main.typ"]\n')
                output_before = self.file_state(f.pdf)
                report = self.run_json(f)
                self.assertEqual(report['status'], 2, report)
                self.assert_no_compile(report, f)
                self.assertEqual(self.file_state(f.pdf), output_before)
                self.assert_lock_free(f)

    def test_native_output_metadata_exactly_preserved_and_unknown_xattrs_refused(self):
        from transactions import _xattrs
        def attributes(path):
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                return _xattrs(fd)
            finally:
                os.close(fd)
        f = self.fixture()
        before = attributes(f.pdf)
        report = self.run_json(f)
        self.assertEqual(report['status'], 0, report)
        self.assertEqual(attributes(f.pdf), before)
        f = self.fixture()
        name = 'com.example.typst-cli-fixture' if sys.platform == 'darwin' else 'user.typst-cli-fixture'
        if sys.platform == 'darwin':
            subprocess.run(['/usr/bin/xattr', '-wx', name, b'owned-only'.hex(), str(f.pdf)],
                check=True, capture_output=True, timeout=10)
        else:
            os.setxattr(f.pdf, name, b'owned-only')
        before = self.file_state(f.pdf), attributes(f.pdf)
        report = self.run_json(f)
        self.assertEqual(report['status'], 3, report)
        self.assert_no_compile(report, f)
        self.assertEqual((self.file_state(f.pdf), attributes(f.pdf)), before)
        self.assertEqual(f.source.read_bytes(), ORIGINAL)

    def test_output_parent_symlink_is_never_followed_or_repaired(self):
        f = self.fixture()
        real_parent = f.root / 'existing parent'
        real_parent.mkdir()
        external = real_parent / 'untouched.pdf'
        external.write_bytes(OLD_OUTPUT)
        (f.root / 'linked parent').symlink_to(real_parent, target_is_directory=True)
        before = self.tree_state(f.root)
        report = self.run_json(f, argv=self.argv(f=f, output='linked parent/untouched.pdf'))
        self.assertEqual(report['status'], 3, report)
        self.assert_no_compile(report, f)
        self.assertEqual(self.tree_state(f.root), before)
        self.assertEqual(f.source.read_bytes(), ORIGINAL)

    def test_initial_refusal_with_unsafe_pdf_cleanup_upgrades_to_gate_three(self):
        f = self.fixture(source=b'#read("never-open")\n')
        report = self.run_json(f, fail_boundary=['output', 'before_event', 'aborted_unpublished'])
        self.assertEqual(report['status'], 3, report)
        self.assert_no_compile(report, f)
        self.assertEqual(report['output_recovery']['state'], 'required')
        self.assertEqual(f.source.read_bytes(), b'#read("never-open")\n')
        self.assertEqual(f.pdf.read_bytes(), OLD_OUTPUT)
        self.assert_lock_free(f)

    def test_completed_output_status_remains_readonly_and_historical(self):
        published = self.run_json()
        self.f.source.unlink()
        self.f.config.unlink()
        before = self.tree_state(self.f.root), self.tree_state(self.f.output_store)
        self.f.compiler_marker.unlink()
        report = self.run_json(argv=self.argv('output-status'), readonly=True, forbid_source=True)
        self.assertEqual(report['status'], 0, report)
        self.assert_no_compile(report)
        inventory = report['output_inventory']
        self.assertTrue(inventory['historical_only'])
        self.assertEqual(inventory['pending'], [])
        self.assertEqual(len(inventory['entries']), 1)
        self.assertEqual(inventory['entries'][0]['journal_id'], self.publication(published)['journal_id'])
        self.assertEqual((self.tree_state(self.f.root), self.tree_state(self.f.output_store)), before)

    def test_natural_nonzero_retains_valid_source_and_raw_stream_bytes(self):
        output = self.file_state(self.f.pdf)
        report = self.run_json(compiler_mode='nonzero')
        self.assertEqual(report['status'], 19, report)
        self.assertIs(report['compiler_invoked'], True)
        self.assertEqual(report['compiler_status'], 19)
        self.assertEqual(report['compile_report']['safety_gate'], 'passed')
        failure = report['compile_report']['compiler_failure']
        self.assertEqual(failure['kind'], 'process_exit')
        self.assertEqual(self.publication(report)['phase'], 'aborted_unpublished')
        self.assertFalse(self.publication(report)['recovery_required'])
        self.assertEqual(self.file_state(self.f.pdf), output)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        raw = json.dumps(report)
        self.assertNotIn(EXPECTED.decode(), raw)
        self.assertNotIn(EXPECTED.hex(), raw)
        attempt = report['compile_report']['attempted_invocations'][-1]
        self.assertEqual(attempt['stdout_hex'], b'owned stdout\x00\xff'.hex())
        self.assertEqual(attempt['stderr_hex'], b'owned stderr\xfe\n'.hex())
        self.assertNotIn(OLD_OUTPUT.decode('latin1'), raw)
        # Byte encoding must remain explicit, rather than lossy UTF-8 replacement.
        self.assertNotIn('\ufffd', raw)
        self.assert_lock_free()

    def test_probe_failures_do_not_propagate_child_code_or_signal(self):
        for mode in ('probe-exit', 'probe-signal'):
            with self.subTest(mode=mode):
                f = self.fixture()
                report = self.run_json(f, compiler_mode=mode)
                self.assertEqual(report['status'], 3, report)
                self.assert_no_compile(report, f)
                self.assertEqual(f.source.read_bytes(), EXPECTED)
                self.assertEqual(f.pdf.read_bytes(), OLD_OUTPUT)
                self.assert_lock_free(f)

    def test_timeout_stream_limit_and_launch_outcomes_remain_gate_three(self):
        for mode, invoked in (('timeout', True), ('stream', True),
                              ('launch-failure', False), ('launch-uncertain', None)):
            with self.subTest(mode=mode):
                f = self.fixture()
                report = self.run_json(f, compiler_mode=mode)
                self.assertEqual(report['status'], 3, report)
                self.assertIs(report['compiler_invoked'], invoked, report)
                if mode in ('timeout', 'stream'):
                    self.assertEqual(report['compiler_status'], -signal.SIGKILL)
                else:
                    self.assertIsNone(report['compiler_status'])
                self.assertEqual(f.pdf.read_bytes(), OLD_OUTPUT)
                self.assertEqual(f.source.read_bytes(), EXPECTED)
                self.assert_lock_free(f)

    def test_later_cleanup_or_safety_failure_overrides_natural_child_status(self):
        for option in ('cleanup_failure', 'source_exit_failure', 'drift'):
            with self.subTest(option=option):
                f = self.fixture()
                options = {option: True} if option != 'drift' else {}
                mode = 'nonzero-drift' if option == 'drift' else 'nonzero'
                report = self.run_json(f, compiler_mode=mode, **options)
                self.assertEqual(report['status'], 3, report)
                self.assertEqual(report['compiler_status'], 19)
                self.assertEqual(f.pdf.read_bytes(), OLD_OUTPUT)
                self.assert_lock_free(f)

    def test_zero_compile_later_source_config_catalog_compiler_or_output_drift_blocks(self):
        for kind in ('source', 'config', 'catalog', 'compiler', 'output'):
            with self.subTest(kind=kind):
                f = self.fixture()
                catalog = f.base / 'owned catalog.json'
                catalog.write_bytes((TOOL / 'rules/catalog.json').read_bytes())
                report = self.run_json(f, catalog=str(catalog), mutate_kind=kind,
                    mutate_boundary=['output', 'after_compiled', f.output])
                self.assertEqual(report['status'], 3, report)
                self.assertIs(report['compiler_invoked'], True)
                self.assertEqual(report['compiler_status'], 0)
                self.assertEqual(f.source.read_bytes(), EXPECTED + (b'\nowned later edit\n' if kind == 'source' else b''))
                self.assertEqual(f.pdf.read_bytes(), OLD_OUTPUT + (b'\nowned later edit\n' if kind == 'output' else b''))
                self.assert_lock_free(f)

    def test_zero_compile_publication_failure_rolls_back_existing_or_absent_output(self):
        for present in (True, False):
            for boundary in ('after_staged', 'after_replaced'):
                with self.subTest(present=present, boundary=boundary):
                    f = self.fixture(present=present)
                    report = self.run_json(f, fail_boundary=['output', boundary, f.output])
                    self.assertEqual(report['status'], 3, report)
                    self.assertEqual(report['compiler_status'], 0)
                    self.assertIs(report['compiler_invoked'], True)
                    self.assertEqual(f.source.read_bytes(), EXPECTED)
                    self.assertEqual(f.pdf.exists(), present)
                    if present:
                        self.assertEqual(f.pdf.read_bytes(), OLD_OUTPUT)
                    self.assertFalse(self.publication(report)['recovery_required'])
                    self.assert_lock_free(f)

    def test_status_absent_and_corrupt_is_readonly_without_source_or_assertions(self):
        f = self.fixture(config=False)
        f.source.unlink()
        before_root = before = self.tree_state(f.root)
        report = self.run_json(f, argv=self.argv('output-status', f), readonly=True, forbid_source=True)
        self.assertEqual(report['status'], 0, report)
        self.assert_no_compile(report, f)
        self.assertFalse(f.source_store.exists())
        self.assertFalse(f.output_store.exists())
        self.assertEqual(report['output_inventory'], {'historical_only': True, 'pending': [],
            'entries': [], 'empty': 'no PDF journals observed'})
        self.assertEqual(self.tree_state(f.root), before)
        f.output_namespace.mkdir(parents=True, mode=0o700)
        f.output_store.chmod(0o700)
        attempt = f.output_namespace / ('f' * 32)
        attempt.mkdir(mode=0o700)
        (attempt / 'manifest.json').write_bytes(b'not JSON')
        before = self.tree_state(f.output_store)
        report = self.run_json(f, argv=self.argv('output-status', f), readonly=True, forbid_source=True)
        self.assertEqual(report['status'], 3, report)
        self.assert_no_compile(report, f)
        self.assertEqual(self.tree_state(f.output_store), before)
        self.assertEqual(self.tree_state(f.root), before_root)

    def test_terminal_recovery_needs_no_source_config_or_compiler_and_preserves_later_pdf(self):
        published = self.run_json()
        journal = self.publication(published)['journal_id']
        self.f.source.unlink()
        self.f.config.unlink()
        self.f.pdf.write_bytes(b'Later user output\x00\xff')
        before = self.file_state(self.f.pdf)
        report = self.run_json(argv=self.recover_argv(journal), fake_compiler=False, forbid_source=True)
        self.assertEqual(report['status'], 0, report)
        pub = self.publication(report)
        self.assertIsNone(pub['observed_sha256'])
        self.assertEqual(pub['attempted_invocations'], [])
        self.assertIs(pub['compiler_invoked'], True)
        self.assertEqual(pub['compiler_status'], 0)
        self.assertEqual(self.file_state(self.f.pdf), before)
        self.assert_lock_free()

    def test_wrong_recovery_id_output_and_source_journal_ids_refuse(self):
        published = self.run_json()
        output_id = self.publication(published)['journal_id']
        source_id = self.journals(source=True)[0].parent.name
        before = self.file_state(self.f.pdf)
        for journal, output in (('0' * 32, self.f.output), (source_id, self.f.output),
                                (output_id, 'build/different.pdf')):
            with self.subTest(journal=journal, output=output):
                report = self.run_json(argv=self.recover_argv(journal, output=output),
                                       fake_compiler=False, forbid_source=True)
                self.assertEqual(report['status'], 3, report)
                self.assertEqual(self.file_state(self.f.pdf), before)
        for journal in ('A' * 32, '0' * 31, '../' + output_id):
            report = self.run_json(argv=self.recover_argv(journal), forbid_leases=True)
            self.assertEqual(report['status'], 2, report)
            self.assertEqual(self.file_state(self.f.pdf), before)

    def test_typst_style_and_bare_compile_remain_inert_and_source_only_fix_unchanged(self):
        before = self.tree_state(self.f.root)
        for gate, argv in ((False, ['compile', '--root', str(self.f.root), '--format', 'json']),
                           (True, ['compile', '--root', str(self.f.root), '--format', 'json']),
                           (True, ['compile', '--root', str(self.f.root), '--format', 'json', '--', 'main.typ', 'main.pdf']),
                           (True, self.argv()), (True, self.argv() + ['--', '--output', 'other.pdf'])):
            with self.subTest(gate=gate, argv=argv):
                result = self.run_process(argv=argv, actual=True, gate=gate)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(self.tree_state(self.f.root), before)
                self.assertFalse(self.f.source_store.exists())
                self.assertFalse(self.f.output_store.exists())
        argv = ['fix', '--root', str(self.f.root), '--format', 'json',
                '--profile', 'closed-core-v1', '--offline-exclusive', '--target', 'main.typ']
        result = self.run_process(argv=argv, actual=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertIs(report['compiler_invoked'], False)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assertEqual(self.f.pdf.read_bytes(), OLD_OUTPUT)
        self.assertFalse(self.f.output_store.exists())

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1',
                         'Set TYPST_LINT_RENDER_TESTS=1 for reviewed Mac compiler smoke')
    @unittest.skipUnless(sys.platform == 'darwin', 'Reviewed native compiler is Mac-only')
    def test_actual_launcher_pinned_mac_compiler_temporary_consumer(self):
        self.assertTrue(PINNED.is_file())
        self.assertEqual(sha256(PINNED.read_bytes()), PINNED_SHA256)
        report = self.run_json(actual=True)
        self.assertEqual(report['status'], 0, report)
        self.assertEqual(report['compiler_status'], 0)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        self.assertTrue(self.f.pdf.read_bytes().startswith(b'%PDF-'))
        self.assertEqual(self.publication(report)['output_disposition'], 'published')
        self.assert_lock_free()


if __name__ == '__main__':
    unittest.main()
