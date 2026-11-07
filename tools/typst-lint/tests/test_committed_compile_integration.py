"""Real-parser committed-source/internal-compiler boundary integration.

All consumers, catalogs, compilers, journals and outputs are temporary fixtures.
The compiler prototype never promotes the ordinary CLI or publishes a PDF.
Real pinned Typst rendering is separately opt-in; deterministic tests do not
claim that a helper program is a reviewed Typst compiler.
"""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, fields
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import closed_core_compile as compiler
import protected_fix
from protected_fix import (ProtectedCoreError, ProtectedCoreFix,
                           REVIEWED_CATALOG_SHA256)
from syntax_client import EXECUTABLE
import transactions
from transactions import OfflineExclusive, TransactionError
import typst_lint as lint

ORIGINAL = b'Preserved ASCII prose. $X^i + Y_j$\n'
EXPECTED = b'Preserved ASCII prose. $X^(i) + Y_(j)$\n'
PINNED_COMPILER = Path('/opt/homebrew/Cellar/typst/0.15.1/bin/typst')
PINNED_COMPILER_SHA256 = '08d01e5a434e821c4a1ce30dd1593e1bbc57c9b1203c4d760d8912ef4ce6a621'


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def clone_opaque(value):
    forged = object.__new__(type(value))
    for item in fields(value):
        object.__setattr__(forged, item.name, getattr(value, item.name))
    return forged


class _FixtureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='typst-committed-compile-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.serial = 0
        self.catalog_bytes = lint.CATALOG.read_bytes()
        self.assertEqual(sha256(self.catalog_bytes), REVIEWED_CATALOG_SHA256)

    def fixture(self, *, source=ORIGINAL, target='main.typ'):
        from types import SimpleNamespace
        self.serial += 1
        base = self.base / f'case-{self.serial}'
        root = base / 'consumer with spaces'
        root.mkdir(parents=True)
        path = root / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source)
        path.chmod(0o640)
        config = root / '.typst-lint.toml'
        config_bytes = ('rule-set = "core"\ninclude = [' + json.dumps(target, ensure_ascii=False) + ']\n').encode()
        config.write_bytes(config_bytes)
        catalog = base / 'reviewed catalog.json'
        catalog.write_bytes(self.catalog_bytes)
        store = base / 'journals'
        store.mkdir(mode=0o700)
        unrelated = root / 'unselected.typ'
        unrelated.write_bytes(b'\xff dirty unrelated input #read("never-open")\n')
        pdf = path.with_suffix('.pdf')
        pdf.write_bytes(b'%PDF-old-output-sentinel\x00\xff')
        return SimpleNamespace(base=base, root=root, target=target, source=path,
                               config=config, config_bytes=config_bytes,
                               catalog=catalog, store=store, unrelated=unrelated,
                               pdf=pdf, lease=OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers())

    @contextmanager
    def context(self, fixture, **kwargs):
        with mock.patch.object(protected_fix, 'CATALOG', fixture.catalog):
            with ProtectedCoreFix(fixture.root, fixture.target,
                                  journal_directory=fixture.store,
                                  lease=fixture.lease, **kwargs) as fix:
                yield fix

    def state(self, path):
        value = path.lstat()
        return (path.read_bytes(), value.st_dev, value.st_ino, value.st_mode,
                value.st_uid, value.st_gid, value.st_mtime_ns)

    def replace_same_bytes(self, path):
        previous = path.stat().st_ino
        replacement = path.with_name(path.name + '.later-user-save')
        replacement.write_bytes(path.read_bytes())
        replacement.chmod(stat.S_IMODE(path.stat().st_mode))
        os.replace(replacement, path)
        self.assertNotEqual(path.stat().st_ino, previous)

    def events(self, fixture):
        manifests = list(fixture.store.glob('root-*/*/manifest.json'))
        self.assertEqual(len(manifests), 1)
        return [json.loads(path.read_bytes())['kind']
                for path in sorted(manifests[0].parent.glob('event-*.json'))]


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX protected transactions')
class CommittedSnapshotIntegrationTests(_FixtureTests):
    def test_real_repair_retains_postimage_and_original_baseline_separately(self):
        f = self.fixture(target='nested 한글 dir/source with spaces.typ')
        sentinels = self.state(f.pdf), self.state(f.unrelated)
        with self.context(f) as fix:
            tx = fix.transaction
            baseline = tx.snapshot
            with self.assertRaises(TransactionError):
                tx.committed_snapshot()
            with self.assertRaises(ProtectedCoreError):
                fix.compile_snapshot()
            candidate = fix.validate()
            with self.assertRaises(ProtectedCoreError):
                fix.compile_snapshot()
            result = fix.apply(candidate)
            receipt = tx.committed_snapshot()
            snapshot = fix.compile_snapshot()
            self.assertIs(receipt, tx.committed_snapshot())
            self.assertIs(snapshot, fix.compile_snapshot())
            self.assertEqual(receipt.sources, {f.target: EXPECTED})
            self.assertEqual(snapshot.source, EXPECTED)
            self.assertIs(snapshot.source, receipt.sources[f.target])
            self.assertEqual(baseline.sources, {f.target: ORIGINAL})
            self.assertEqual(tx.snapshot.sources, baseline.sources)
            self.assertEqual(snapshot.root, f.root)
            self.assertEqual(snapshot.target, f.target)
            self.assertEqual(snapshot.source_sha256, sha256(EXPECTED))
            self.assertEqual(snapshot.config_sha256, sha256(f.config_bytes))
            self.assertEqual(snapshot.catalog_sha256, REVIEWED_CATALOG_SHA256)
            self.assertEqual(snapshot.evidence, {str(f.config): f.config_bytes,
                                                 str(f.catalog): self.catalog_bytes})
            self.assertEqual(set(snapshot.required_rule_ids), protected_fix.CORE_RULE_IDS)
            self.assertEqual(snapshot.evaluated_rule_ids, ('core.parenthesized-scripts',))
            tx.assert_current(receipt)
            fix.assert_compile_current(snapshot)
            self.assertEqual(result.modified, (f.target,))
            with self.assertRaises((FrozenInstanceError, AttributeError)):
                snapshot.source = ORIGINAL
            with self.assertRaises(TypeError):
                receipt.sources[f.target] = ORIGINAL
            with self.assertRaises(TypeError):
                snapshot.evidence[str(f.config)] = b'forged'
        self.assertEqual(f.source.read_bytes(), EXPECTED)
        self.assertEqual((self.state(f.pdf), self.state(f.unrelated)), sentinels)
        self.assertEqual(self.events(f)[-1], 'committed')

    def test_real_noop_issues_current_snapshot_without_journal_or_inode_change(self):
        for source in (b'', b'Unchanged ASCII prose.\n', EXPECTED):
            with self.subTest(source=source):
                f = self.fixture(source=source)
                before = self.state(f.source)
                with self.context(f) as fix:
                    candidate = fix.validate()
                    self.assertEqual(candidate.passes, 0)
                    result = fix.apply(candidate)
                    snapshot = fix.compile_snapshot()
                    self.assertEqual(snapshot.source, source)
                    self.assertEqual(fix.transaction.committed_snapshot().sources, {f.target: source})
                    fix.assert_compile_current(snapshot)
                    self.assertEqual((result.modified, result.journal), ((), None))
                self.assertEqual(self.state(f.source), before)
                self.assertEqual(list(f.store.glob('root-*/*/manifest.json')), [])

    def test_postcommit_snapshot_failure_never_rolls_back_or_erases_later_edit(self):
        for later_edit in (False, True):
            with self.subTest(later_edit=later_edit):
                f = self.fixture()
                latest = EXPECTED + b'Later user edit after durable commit.\n'
                with self.context(f) as fix:
                    token = fix.validate()

                    def fail_retention(expected):
                        self.assertEqual(expected[f.target].data, EXPECTED)
                        self.assertEqual(self.events(f)[-1], 'committed')
                        if later_edit:
                            f.source.write_bytes(latest)
                        raise OSError('injected postcommit snapshot allocation failure')

                    with mock.patch.object(fix.transaction, '_retain_committed', side_effect=fail_retention), \
                            mock.patch.object(fix.transaction, '_recover', side_effect=AssertionError('postcommit rollback')):
                        with self.assertRaisesRegex(OSError, 'postcommit'):
                            fix.apply(token)
                    with self.assertRaises(TransactionError):
                        fix.transaction.committed_snapshot()
                    with self.assertRaises(ProtectedCoreError):
                        fix.compile_snapshot()
                self.assertEqual(f.source.read_bytes(), latest if later_edit else EXPECTED)
                self.assertEqual(self.events(f)[-1], 'committed')
                self.assertNotIn('recovery_required', self.events(f))


# The helper deliberately is not Typst. Only the private reviewed-artifact seam
# is substituted, so real capture/copy/launch/draining/workspace/output guards run.
_FAKE_VERSION = b'typst owned integration fixture 1\n'
_FAKE_BODY = r'''
import json, os, pathlib, signal, subprocess, sys, time
request = json.loads(REQUEST)
if sys.argv[1:] == ['--version']:
    os.write(1, VERSION if request['mode'] != 'wrong-version' else b'wrong version\n')
    raise SystemExit(0)
assert sys.argv[1] == 'compile'
source = pathlib.Path(sys.argv[-2])
output = pathlib.Path(sys.argv[-1])
deps = pathlib.Path(sys.argv[sys.argv.index('--deps') + 1])
mode = request['mode']
if request.get('marker'):
    pathlib.Path(request['marker']).write_text(str(os.getpid()))
if mode == 'hang':
    time.sleep(30)
if mode == 'nonzero':
    os.write(2, b'owned fixture diagnostic\n')
    raise SystemExit(19)
if mode == 'signal':
    os.kill(os.getpid(), signal.SIGTERM)
if mode in ('stdout-limit', 'stderr-limit'):
    os.write(1 if mode == 'stdout-limit' else 2, b'x' * request['count'])
    time.sleep(30)
if mode == 'mutate-live':
    path = pathlib.Path(request['mutate_path'])
    path.write_bytes(path.read_bytes() + b'Later user edit during compile.\n')
raw = source.read_bytes()
prefix = b'%PDF-1.7\n%fixture source hex=' + raw.hex().encode() + b'\n'
offset = len(prefix)
pdf = prefix + b'xref\n0 1\n0000000000 65535 f \ntrailer\n<< /Size 1 >>\nstartxref\n' + str(offset).encode() + b'\n%%EOF\n'
if mode == 'truncated':
    pdf = b'%PDF-1.7\ntruncated\n'
if mode == 'bad-xref':
    pdf = b'%PDF-1.7\nxxxxxxxxxxxxxxxxxxxxxxxxxxxx\nstartxref\n999999\n%%EOF\n'
if mode == 'oversized-pdf':
    pdf += b'x' * request['count']
if mode != 'missing-pdf':
    output.write_bytes(pdf)
if mode == 'linked-pdf':
    alternate = output.parent / 'alternate'
    output.rename(alternate)
    output.symlink_to(alternate)
if mode == 'hardlinked-pdf':
    os.link(output, output.parent / 'second-pdf-link')
if mode == 'unsafe-mode':
    output.chmod(0o644)
if mode == 'unexpected-file':
    (output.parent / 'unexpected.txt').write_bytes(b'not an allowed resource')
if mode != 'missing-deps':
    deps.write_bytes(os.fsencode(source.relative_to(pathlib.Path.cwd())) + b'\0' if mode != 'wrong-deps' else b'/unexpected/input.typ\0')
if mode in ('replace-pdf', 'replace-deps'):
    path = output if mode == 'replace-pdf' else deps
    replacement = path.with_name(path.name + '.same-byte-replacement')
    replacement.write_bytes(path.read_bytes())
    replacement.replace(path)
if mode in ('remove-pdf', 'remove-deps'):
    (output if mode == 'remove-pdf' else deps).unlink()
'''


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX isolated compiler')
class CommittedCompilerIntegrationTests(_FixtureTests):
    def fake_compiler(self, fixture, *, mode='success', **request):
        path = fixture.base / 'owned compiler with spaces'
        request = dict(mode=mode, **request)
        code = ('#!' + sys.executable + '\n'
                + 'REQUEST = ' + repr(json.dumps(request)) + '\n'
                + 'VERSION = ' + repr(_FAKE_VERSION) + '\n' + _FAKE_BODY)
        path.write_bytes(code.encode())
        path.chmod(0o500)
        record = compiler._ReviewedNativeArtifact(path, sha256(code.encode()), _FAKE_VERSION)
        return record

    @contextmanager
    def reviewed(self, record):
        with mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
            yield

    def compile(self, fix, snapshot=None, **kwargs):
        return compiler.compile_committed_snapshot(
            fix, fix.compile_snapshot() if snapshot is None else snapshot,
            profile=kwargs.pop('profile', compiler.RESOURCE_PROFILE), **kwargs)

    def assert_committed_untouched(self, fixture, sentinels):
        self.assertEqual(fixture.source.read_bytes(), EXPECTED)
        self.assertEqual((self.state(fixture.pdf), self.state(fixture.unrelated)), sentinels)
        self.assertEqual(self.events(fixture)[-1], 'committed')
        self.assertNotIn('recovery_required', self.events(fixture))

    def test_exact_postimage_private_copy_minimal_environment_and_immutable_evidence(self):
        f = self.fixture(target='nested 한글 dir/ASCII source with spaces.typ')
        record = self.fake_compiler(f)
        sentinels = self.state(f.pdf), self.state(f.unrelated)
        inherited = {'PATH': '/untrusted', 'TYPST_FONT_PATHS': '/user/fonts',
                     'TYPST_PACKAGE_PATH': '/user/packages', 'SOURCE_DATE_EPOCH': '999',
                     'DYLD_INSERT_LIBRARIES': '/user/injected', 'LD_PRELOAD': '/user/injected',
                     'HTTP_PROXY': 'http://user-proxy', 'CREDENTIAL_FOR_FIXTURE': 'must-not-inherit'}
        with self.context(f) as fix:
            fix.apply(fix.validate())
            snapshot = fix.compile_snapshot()
            with self.reviewed(record), mock.patch.dict(os.environ, inherited), \
                    mock.patch.object(compiler.subprocess, 'Popen', wraps=subprocess.Popen) as launched:
                artifact = self.compile(fix, snapshot)
                raw = artifact.pdf_bytes
                compiler.assert_artifact_current(fix, artifact)
            self.assertEqual(launched.call_count, 2)
            for call in launched.call_args_list:
                self.assertIs(call.kwargs['stdin'], subprocess.DEVNULL)
                self.assertFalse(call.kwargs['shell'])
                self.assertTrue(call.kwargs['close_fds'])
                self.assertTrue(call.kwargs['start_new_session'])
                self.assertEqual(call.kwargs['umask'], 0o077)
            self.assertIn(EXPECTED.hex().encode(), raw)
            self.assertNotIn(ORIGINAL.hex().encode(), raw)
            self.assertEqual(artifact.profile, 'embedded-ascii-v1')
            self.assertEqual(artifact.target, f.target)
            self.assertEqual(artifact.source_sha256, sha256(EXPECTED))
            self.assertEqual(artifact.source_bytes, len(EXPECTED))
            self.assertEqual(artifact.config_sha256, sha256(f.config_bytes))
            self.assertEqual(artifact.catalog_sha256, REVIEWED_CATALOG_SHA256)
            self.assertEqual(dict(artifact.evidence_hashes), {str(f.config): sha256(f.config_bytes),
                                                           str(f.catalog): REVIEWED_CATALOG_SHA256})
            self.assertEqual(artifact.compiler.path, str(record.path))
            self.assertEqual(artifact.compiler.sha256, record.sha256)
            self.assertEqual(artifact.private_compiler.sha256, record.sha256)
            self.assertNotEqual(artifact.compiler.path, artifact.private_compiler.path)
            self.assertNotEqual(artifact.compiler.identity[:2], artifact.private_compiler.identity[:2])
            self.assertEqual(artifact.input.sha256, snapshot.source_sha256)
            self.assertEqual(artifact.output.sha256, artifact.pdf_sha256)
            self.assertEqual(artifact.pdf_sha256, sha256(raw))
            self.assertEqual(artifact.dependency_paths, (f.target,))
            invocation = artifact.compilation
            private = Path(artifact.private_compiler.path)
            base = private.parent.parent
            expected_argv = (str(private), 'compile', '--format', 'pdf', '--root', str(base / 'project'),
                '--ignore-system-fonts', '--package-path', str(base / 'packages'),
                '--package-cache-path', str(base / 'package-cache'), '--creation-timestamp', '0',
                '--jobs', '1', '--diagnostic-format', 'short', '--deps', artifact.dependencies.path,
                '--deps-format', 'zero', artifact.input.path, artifact.output.path)
            self.assertEqual(invocation.argv, expected_argv)
            self.assertEqual(invocation.cwd, str(base / 'project'))
            self.assertEqual(Path(artifact.input.path).relative_to(base / 'project').as_posix(), f.target)
            self.assertEqual(invocation.returncode, 0)
            environment = dict(invocation.environment)
            self.assertEqual(set(environment), {'HOME', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME',
                                              'XDG_DATA_HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'TZ'})
            self.assertEqual({environment[key] for key in ('LANG', 'LC_ALL')}, {'C'})
            self.assertEqual(environment['TZ'], 'UTC')
            for key in ('HOME', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'TMPDIR'):
                self.assertIn(base, Path(environment[key]).parents)
            self.assertEqual(len(artifact.probes), 1)
            self.assertEqual(artifact.probes[0].argv, (str(private), '--version'))
            self.assertEqual(artifact.probes[0].stdout, _FAKE_VERSION)
            self.assertFalse(base.exists(), 'private workspace survived successful compile')
            with self.assertRaises((FrozenInstanceError, AttributeError)):
                artifact.pdf_sha256 = 'forged'
            with self.assertRaises(compiler.CompileSafetyError):
                compiler.assert_artifact_current(fix, clone_opaque(artifact))
            with self.assertRaises(TypeError):
                compiler.ClosedCoreCompileArtifact()
        with self.assertRaises(compiler.CompileSafetyError):
            _ = artifact.pdf_bytes
        self.assert_committed_untouched(f, sentinels)

    def test_profile_is_explicit_and_unicode_refuses_before_compiler_probe(self):
        for source in ("Korean 한글 $X^i$\n".encode(), 'Accented café $X^i$\n'.encode()):
            with self.subTest(source=source):
                f = self.fixture(source=source)
                with self.context(f) as fix:
                    fix.apply(fix.validate())
                    expected = source.replace(b'X^i', b'X^(i)')
                    with mock.patch.object(compiler, '_capture_compiler', side_effect=AssertionError('Unicode probed compiler')):
                        with self.assertRaisesRegex(compiler.UnsupportedCompileProfile, 'ASCII|Unicode'):
                            self.compile(fix)
                    self.assertEqual(f.source.read_bytes(), expected)
                    self.assertEqual(self.events(f)[-1], 'committed')
        f = self.fixture()
        with self.context(f) as fix:
            fix.apply(fix.validate())
            snapshot = fix.compile_snapshot()
            with self.assertRaises(TypeError):
                compiler.compile_committed_snapshot(fix, snapshot)
            with mock.patch.object(compiler, '_capture_compiler', side_effect=AssertionError('invalid profile probed compiler')):
                for profile in (None, '', 'ordinary', 'embedded-unicode-v1', True):
                    with self.subTest(profile=profile), self.assertRaises(compiler.UnsupportedCompileProfile):
                        self.compile(fix, snapshot, profile=profile)
                for deadline in (0, -1, 121, float('nan'), float('inf'), True, '10'):
                    with self.subTest(deadline=deadline), self.assertRaises(compiler.UnsupportedCompileProfile):
                        self.compile(fix, snapshot, deadline_seconds=deadline)
                with self.assertRaises(compiler.UnsupportedCompileProfile):
                    self.compile(fix, snapshot, cancel_event=object())

    def test_leading_bom_is_retained_not_silently_reencoded(self):
        source = b'\xef\xbb\xbf' + ORIGINAL
        f = self.fixture(source=source)
        record = self.fake_compiler(f)
        with self.context(f) as fix, self.reviewed(record):
            fix.apply(fix.validate())
            artifact = self.compile(fix)
            expected = b'\xef\xbb\xbf' + EXPECTED
            self.assertEqual(artifact.source_sha256, sha256(expected))
            self.assertEqual(artifact.input.size, len(expected))
            self.assertIn(expected.hex().encode(), artifact.pdf_bytes)

    def test_unknown_hash_is_refused_before_any_probe_and_preserves_fix(self):
        f = self.fixture()
        record = self.fake_compiler(f)
        wrong = compiler._ReviewedNativeArtifact(record.path, '0' * 64, record.version)
        sentinels = self.state(f.pdf), self.state(f.unrelated)
        with self.context(f) as fix, self.reviewed(wrong):
            fix.apply(fix.validate())
            with mock.patch.object(compiler, '_run', side_effect=AssertionError('unreviewed compiler executed')):
                with self.assertRaisesRegex(compiler.CompileSafetyError, 'SHA-256'):
                    self.compile(fix)
        self.assert_committed_untouched(f, sentinels)

    def test_wrong_version_probe_refuses_before_compile(self):
        f = self.fixture()
        record = self.fake_compiler(f, mode='wrong-version')
        with self.context(f) as fix, self.reviewed(record), mock.patch.object(compiler, '_run', wraps=compiler._run) as run:
            fix.apply(fix.validate())
            with self.assertRaisesRegex(compiler.CompileSafetyError, 'version'):
                self.compile(fix)
            self.assertEqual([call.kwargs['purpose'] for call in run.call_args_list], ['version-probe'])
        self.assertEqual(f.source.read_bytes(), EXPECTED)

    def test_process_nonzero_signal_and_stream_limits_never_publish_or_roll_back(self):
        cases = (('nonzero', 'nonzero exit', 19), ('signal', 'signal', -signal.SIGTERM),
                 ('stdout-limit', 'stdout byte limit exceeded', -signal.SIGKILL),
                 ('stderr-limit', 'stderr byte limit exceeded', -signal.SIGKILL))
        for mode, reason, status in cases:
            with self.subTest(mode=mode):
                f = self.fixture()
                record = self.fake_compiler(f, mode=mode, count=4097)
                sentinels = self.state(f.pdf), self.state(f.unrelated)
                with self.context(f) as fix, self.reviewed(record), mock.patch.object(compiler, '_MAX_STREAM', 4096):
                    fix.apply(fix.validate())
                    with self.assertRaises(compiler.CompilerFailure) as caught:
                        self.compile(fix)
                    failure = caught.exception
                    self.assertEqual(failure.reason, reason)
                    self.assertEqual(failure.invocation.purpose, 'compile')
                    self.assertEqual(failure.invocation.returncode, status)
                    self.assertLessEqual(len(failure.invocation.stdout), 4096)
                    self.assertLessEqual(len(failure.invocation.stderr), 4096)
                    self.assertFalse(Path(failure.invocation.cwd).exists())
                self.assert_committed_untouched(f, sentinels)

    def test_timeout_and_handshaken_cancellation_kill_only_owned_compiler(self):
        for mode in ('timeout', 'cancelled'):
            with self.subTest(mode=mode):
                f = self.fixture()
                marker = f.base / 'owned-compiler.started'
                record = self.fake_compiler(f, mode='hang', marker=str(marker))
                cancel = threading.Event()
                failures = []

                def cancel_when_started():
                    until = time.monotonic() + 5
                    while not marker.exists() and time.monotonic() < until:
                        time.sleep(0.005)
                    if not marker.exists():
                        failures.append('owned compiler did not reach compile handshake')
                    cancel.set()

                thread = None
                if mode == 'cancelled':
                    thread = threading.Thread(target=cancel_when_started, daemon=True)
                    thread.start()
                with self.context(f) as fix, self.reviewed(record):
                    fix.apply(fix.validate())
                    with self.assertRaises(compiler.CompilerFailure) as caught:
                        self.compile(fix, deadline_seconds=0.5 if mode == 'timeout' else 5,
                                     cancel_event=cancel if mode == 'cancelled' else None)
                    invocation = caught.exception.invocation
                    self.assertEqual(caught.exception.reason, mode)
                    self.assertEqual(invocation.purpose, 'compile')
                    self.assertEqual(invocation.returncode, -signal.SIGKILL)
                    self.assertTrue(marker.is_file())
                    self.assertFalse(Path(invocation.cwd).exists())
                if thread:
                    thread.join(timeout=6)
                    self.assertFalse(thread.is_alive())
                self.assertEqual(failures, [])
                self.assertEqual(f.source.read_bytes(), EXPECTED)
                self.assertEqual(self.events(f)[-1], 'committed')

    def test_missing_truncated_oversized_and_unsafe_outputs_are_rejected(self):
        modes = ('missing-pdf', 'missing-deps', 'truncated', 'bad-xref', 'oversized-pdf',
                 'linked-pdf', 'hardlinked-pdf', 'unsafe-mode', 'unexpected-file', 'wrong-deps',
                 'replace-pdf', 'replace-deps', 'remove-pdf', 'remove-deps')
        for mode in modes:
            with self.subTest(mode=mode):
                f = self.fixture()
                record = self.fake_compiler(f, mode=mode, count=4097)
                sentinels = self.state(f.pdf), self.state(f.unrelated)
                with self.context(f) as fix, self.reviewed(record), mock.patch.object(compiler, '_MAX_PDF', 4096):
                    fix.apply(fix.validate())
                    with self.assertRaises(compiler.CompileSafetyError):
                        self.compile(fix)
                self.assert_committed_untouched(f, sentinels)

    def test_source_config_and_catalog_edits_during_compile_are_preserved_and_rejected(self):
        for field in ('source', 'config', 'catalog'):
            with self.subTest(field=field):
                f = self.fixture()
                path = getattr(f, field)
                record = self.fake_compiler(f, mode='mutate-live', mutate_path=str(path))
                sentinels = self.state(f.pdf), self.state(f.unrelated)
                with self.context(f) as fix, self.reviewed(record):
                    fix.apply(fix.validate())
                    previous = path.read_bytes()
                    with self.assertRaises(compiler.CompileSafetyError):
                        self.compile(fix)
                    self.assertEqual(path.read_bytes(), previous + b'Later user edit during compile.\n')
                self.assertEqual((self.state(f.pdf), self.state(f.unrelated)), sentinels)
                self.assertEqual(f.source.read_bytes(), EXPECTED + (b'Later user edit during compile.\n' if field == 'source' else b''))
                self.assertEqual(self.events(f)[-1], 'committed')
                self.assertNotIn('recovery_required', self.events(f))

    def test_later_live_source_cannot_be_recaptured_or_compiled(self):
        f = self.fixture()
        record = self.fake_compiler(f)
        with self.context(f) as fix, self.reviewed(record):
            fix.apply(fix.validate())
            snapshot = fix.compile_snapshot()
            later = EXPECTED + b'Later live user source must not compile.\n'
            f.source.write_bytes(later)
            unchanged = self.state(f.source)
            with mock.patch.object(compiler, '_capture_compiler', side_effect=AssertionError('stale source captured compiler')), \
                    mock.patch.object(compiler, '_run', side_effect=AssertionError('stale source compiled')):
                with self.assertRaises(compiler.CompileSafetyError):
                    self.compile(fix, snapshot)
            self.assertEqual(snapshot.source, EXPECTED)
            self.assertEqual(self.state(f.source), unchanged)
        self.assertEqual(f.source.read_bytes(), later)
        self.assertEqual(self.events(f)[-1], 'committed')

    def test_private_input_compiler_and_original_compiler_tampering_is_detected(self):
        for target in ('input', 'private_compiler', 'original_compiler'):
            for mutation in ('content', 'same_bytes_new_inode'):
                with self.subTest(target=target, mutation=mutation):
                    f = self.fixture()
                    record = self.fake_compiler(f)
                    actual_run = compiler._run

                    def tamper(argv, **kwargs):
                        report = actual_run(argv, **kwargs)
                        if kwargs['purpose'] == 'compile':
                            path = (Path(argv[-2]) if target == 'input' else
                                    Path(argv[0]) if target == 'private_compiler' else record.path)
                            if mutation == 'same_bytes_new_inode':
                                self.replace_same_bytes(path)
                            else:
                                path.chmod(0o600)
                                path.write_bytes(path.read_bytes() + b'\n# changed after use\n')
                        return report

                    with self.context(f) as fix, self.reviewed(record), mock.patch.object(compiler, '_run', side_effect=tamper):
                        fix.apply(fix.validate())
                        with self.assertRaises(compiler.CompileSafetyError):
                            self.compile(fix)
                    self.assertEqual(f.source.read_bytes(), EXPECTED)
                    self.assertEqual(self.events(f)[-1], 'committed')

    def test_workspace_directory_replacement_and_permission_changes_are_detected(self):
        for name in ('home', 'packages'):
            for mutation in ('same_path_new_inode', 'unsafe_mode'):
                with self.subTest(name=name, mutation=mutation):
                    f = self.fixture()
                    record = self.fake_compiler(f)
                    actual_run = compiler._run

                    def tamper(argv, **kwargs):
                        report = actual_run(argv, **kwargs)
                        if kwargs['purpose'] == 'version-probe':
                            directory = Path(argv[0]).parent.parent / name
                            if mutation == 'same_path_new_inode':
                                old_inode = directory.stat().st_ino
                                # Retain the removed directory outside the compiler
                                # workspace, preventing immediate inode reuse.
                                directory.rename(f.base / ('retained-old-' + name))
                                directory.mkdir(mode=0o700)
                                self.assertNotEqual(directory.stat().st_ino, old_inode)
                            else:
                                directory.chmod(0o755)
                        return report

                    with self.context(f) as fix, self.reviewed(record), mock.patch.object(compiler, '_run', side_effect=tamper):
                        fix.apply(fix.validate())
                        with self.assertRaises(compiler.CompileSafetyError):
                            self.compile(fix)
                    self.assertEqual(f.source.read_bytes(), EXPECTED)
                    self.assertEqual(self.events(f)[-1], 'committed')

    def test_output_and_dependency_changes_after_capture_are_detected(self):
        for name in ('artifact.pdf', 'dependencies.zero'):
            for mutation in ('same_bytes_new_inode', 'truncate'):
                with self.subTest(name=name, mutation=mutation):
                    f = self.fixture()
                    record = self.fake_compiler(f)
                    actual_capture = compiler._capture
                    changed = False

                    def capture(path, *args, **kwargs):
                        nonlocal changed
                        result = actual_capture(path, *args, **kwargs)
                        path = Path(path)
                        if path.name == name and not changed:
                            changed = True
                            if mutation == 'same_bytes_new_inode':
                                self.replace_same_bytes(path)
                            else:
                                path.chmod(0o600)
                                path.write_bytes(b'truncated after capture')
                        return result

                    with self.context(f) as fix, self.reviewed(record), mock.patch.object(compiler, '_capture', side_effect=capture):
                        fix.apply(fix.validate())
                        with self.assertRaises(compiler.CompileSafetyError):
                            self.compile(fix)
                        self.assertTrue(changed)
                    self.assertEqual(f.source.read_bytes(), EXPECTED)

    def test_compilation_has_no_hidden_source_config_catalog_reads(self):
        f = self.fixture()
        record = self.fake_compiler(f)
        with self.context(f) as fix, self.reviewed(record):
            fix.apply(fix.validate())
            snapshot = fix.compile_snapshot()
            actual_guard, actual_read, actual_capture = compiler._guard, transactions._read, compiler._capture
            guarding = False
            reads = []

            def guard(*args):
                nonlocal guarding
                self.assertFalse(guarding)
                guarding = True
                try:
                    return actual_guard(*args)
                finally:
                    guarding = False

            def read(path, *args, **kwargs):
                self.assertTrue(guarding, f'hidden committed-input reread: {path}')
                reads.append(Path(path))
                return actual_read(path, *args, **kwargs)

            def capture(path, *args, **kwargs):
                self.assertNotIn(Path(path), (f.source, f.config, f.catalog))
                return actual_capture(path, *args, **kwargs)

            with mock.patch.object(compiler, '_guard', side_effect=guard), \
                    mock.patch.object(transactions, '_read', side_effect=read), \
                    mock.patch.object(compiler, '_capture', side_effect=capture), \
                    mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('hidden Path.read_bytes')):
                artifact = self.compile(fix, snapshot)
                self.assertIn(EXPECTED.hex().encode(), artifact.pdf_bytes)
            self.assertEqual(set(reads), {f.source, f.config, f.catalog})

    def test_artifact_staleness_and_forgery_refuse_use_without_touching_committed_source(self):
        for target in ('source', 'config', 'catalog', 'compiler', 'artifact'):
            with self.subTest(target=target):
                f = self.fixture()
                record = self.fake_compiler(f)
                with self.context(f) as fix, self.reviewed(record):
                    fix.apply(fix.validate())
                    artifact = self.compile(fix)
                    original = artifact.pdf_bytes
                    forged = clone_opaque(artifact)
                    with self.assertRaises(compiler.CompileSafetyError):
                        _ = forged.pdf_bytes
                    if target == 'artifact':
                        object.__setattr__(artifact, '_pdf_bytes', original + b'forged')
                    else:
                        path = record.path if target == 'compiler' else getattr(f, target)
                        self.replace_same_bytes(path)
                        changed = self.state(path)
                    with self.assertRaises(compiler.CompileSafetyError):
                        compiler.assert_artifact_current(fix, artifact)
                    with self.assertRaises(compiler.CompileSafetyError):
                        _ = artifact.pdf_bytes
                    if target != 'artifact':
                        self.assertEqual(self.state(path), changed)
                self.assertEqual(f.source.read_bytes(), EXPECTED)

    def test_default_cli_compile_stays_inert_and_explicit_offline_routes_stay_compiler_free(self):
        f = self.fixture()
        spy = f.base / 'compiler-spy'
        marker = f.base / 'compiler-spy.ran'
        spy.write_text('#!' + sys.executable + '\nfrom pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("ran")\n')
        spy.chmod(0o500)
        sentinels = self.state(f.pdf), self.state(f.unrelated)
        gate = subprocess.run([sys.executable, str(TOOL / 'typst-style'), 'compile',
                               '--root', str(f.root), '--format', 'json', '--',
                               str(spy), f.target, str(f.pdf)],
                              capture_output=True, text=True, timeout=20)
        self.assertEqual(gate.returncode, 2, gate.stderr)
        self.assertFalse(json.loads(gate.stdout)['compiler_invoked'])
        self.assertFalse(marker.exists())
        self.assertEqual(f.source.read_bytes(), ORIGINAL)
        cli = [sys.executable, str(TOOL / 'typst-lint')]
        fixed = subprocess.run([*cli, 'fix', '--root', str(f.root), '--format', 'json',
                                '--profile', 'closed-core-v1', '--offline-exclusive',
                                '--target', f.target], capture_output=True, text=True, timeout=20)
        self.assertEqual(fixed.returncode, 0, fixed.stderr)
        report = json.loads(fixed.stdout)
        self.assertFalse(report['compiler_invoked'])
        self.assertEqual(report['modified_paths'], [f.target])
        self.assertEqual(f.source.read_bytes(), EXPECTED)
        journal = report['journal_id']
        for arguments in (['journal-status'], ['recover', '--offline-exclusive', '--target', f.target,
                                             '--journal-id', journal]):
            result = subprocess.run([*cli, *arguments, '--root', str(f.root), '--format', 'json'],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)['compiler_invoked'])
            self.assertEqual(f.source.read_bytes(), EXPECTED)
        self.assertFalse(marker.exists())
        self.assertEqual((self.state(f.pdf), self.state(f.unrelated)), sentinels)


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1',
                     'Opt in with TYPST_LINT_RENDER_TESTS=1 for pinned native Typst rendering')
@unittest.skipUnless(EXECUTABLE.is_file() and PINNED_COMPILER.is_file() and shutil.which('pdftotext'),
                     'Requires pinned Mac Typst 0.15.1, pdftotext and built syntax adapter')
@unittest.skipUnless(sys.platform == 'darwin', 'Reviewed native compiler profile is macOS only')
class PinnedCommittedCompilerRenderTests(_FixtureTests):
    def setUp(self):
        super().setUp()
        self.assertEqual(sha256(PINNED_COMPILER.read_bytes()), PINNED_COMPILER_SHA256,
                         'Installed native compiler differs from reviewed bytes')

    def test_pinned_typst_ascii_math_render_and_extracted_text_from_exact_committed_postimage(self):
        for target, bom in (('main.typ', b''), ('nested 한글 dir/source with spaces.typ', b'\xef\xbb\xbf')):
            with self.subTest(target=target, bom=bool(bom)):
                source = bom + b'ASCII rendering sentinel. $X^i + Y_j$\n'
                expected = source.replace(b'X^i', b'X^(i)').replace(b'Y_j', b'Y_(j)')
                f = self.fixture(source=source, target=target)
                sentinels = self.state(f.pdf), self.state(f.unrelated)
                with self.context(f) as fix:
                    fix.apply(fix.validate())
                    snapshot = fix.compile_snapshot()
                    artifact = compiler.compile_committed_snapshot(fix, snapshot,
                        profile='embedded-ascii-v1', deadline_seconds=30)
                    raw = artifact.pdf_bytes
                    self.assertTrue(raw.startswith(b'%PDF-'))
                    self.assertGreater(len(raw), 1000)
                    self.assertEqual(artifact.compiler.path, str(PINNED_COMPILER))
                    self.assertEqual(artifact.compiler.sha256, PINNED_COMPILER_SHA256)
                    self.assertEqual(artifact.private_compiler.sha256, PINNED_COMPILER_SHA256)
                    self.assertEqual(artifact.source_sha256, sha256(expected))
                    self.assertEqual(artifact.input.sha256, sha256(expected))
                    self.assertEqual(artifact.input.size, len(expected))
                    self.assertEqual(artifact.config_sha256, sha256(f.config_bytes))
                    self.assertEqual(artifact.catalog_sha256, REVIEWED_CATALOG_SHA256)
                    self.assertEqual(artifact.probes[0].stdout, compiler._VERSION)
                    self.assertEqual(artifact.compilation.returncode, 0)
                    self.assertEqual(artifact.compilation.stderr, b'')
                    self.assertEqual(artifact.dependency_paths, (f.target,))
                    self.assertFalse(Path(artifact.compilation.cwd).exists())
                    extracted = subprocess.run([shutil.which('pdftotext'), '-layout', '-', '-'],
                                               input=raw, capture_output=True, timeout=10)
                    self.assertEqual(extracted.returncode, 0, extracted.stderr)
                    text = extracted.stdout.decode('utf-8')
                    self.assertIn('ASCII rendering sentinel.', text)
                    # ASCII Typst math intentionally extracts as mathematical
                    # italic codepoints. This says nothing about Unicode input.
                    math_text = text.split('sentinel.', 1)[1]
                    self.assertEqual(''.join(math_text.split()), '𝑋𝑖+𝑌𝑗')
                    compiler.assert_artifact_current(fix, artifact)
                self.assertEqual(f.source.read_bytes(), expected)
                self.assertEqual((self.state(f.pdf), self.state(f.unrelated)), sentinels)
                self.assertEqual(self.events(f)[-1], 'committed')


if __name__ == '__main__':
    unittest.main()
