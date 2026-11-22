"""Independent real-parser integration for the internal PDF publication session.

Every source, evidence file, output, compiler and journal is test-owned and
short-lived. The helper executable is deliberately not Typst: only the existing
private reviewed-artifact seam is patched. Actual pinned Mac renders are opt-in.
"""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import closed_core_compile as compiler
import closed_core_publish as publication
import pdf_publication_store as store
import protected_fix
from protected_fix import ProtectedCoreFix
from syntax_client import EXECUTABLE
from transactions import OfflineExclusive, TransactionError, _copy_xattrs, _xattrs
import typst_lint

ORIGINAL = b'Preserved ASCII prose. $X^i + Y_j$\n'
EXPECTED = b'Preserved ASCII prose. $X^(i) + Y_(j)$\n'
OLD_OUTPUT = b'Not required to be a PDF.\x00\xff\r\n'
PINNED = Path('/opt/homebrew/Cellar/typst/0.15.1/bin/typst')
PINNED_SHA256 = '08d01e5a434e821c4a1ce30dd1593e1bbc57c9b1203c4d760d8912ef4ce6a621'


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def make_fixture(base, *, source=ORIGINAL, target='nested source/source file.typ',
                 destination='build with spaces/nested/document file.pdf', present=True,
                 output_mode=0o644):
    """Create one consumer; never creates or changes real project documents."""
    root = base / 'temporary consumer'
    root.mkdir(parents=True)
    source_path = root / target
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(source)
    config = root / '.typst-lint.toml'
    config.write_bytes(('rule-set = "core"\ninclude = [' + json.dumps(target) + ']\n').encode())
    catalog = base / 'reviewed catalog.json'
    catalog.write_bytes(typst_lint.CATALOG.read_bytes())
    source_store = base / 'source journals'
    source_store.mkdir(mode=0o700)
    output = root / destination
    output.parent.mkdir(parents=True, exist_ok=True)
    if present:
        output.write_bytes(OLD_OUTPUT)
        output.chmod(output_mode)
    unrelated = root / 'unselected.typ'
    unrelated.write_bytes(b'\xff dirty user work #read("never")\n')
    return SimpleNamespace(base=base, root=root, target=target, source=source_path,
        config=config, catalog=catalog, source_store=source_store,
        destination=destination, output=output, unrelated=unrelated,
        source_lease=OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers(),
        output_lease=store.OfflineOutputExclusive.assert_exporters_watchers_closed_and_no_other_writers())


def make_compiler(f, *, mode='success', marker=None):
    path = f.base / 'owned test compiler'
    body = '''import os, pathlib, signal, sys, time
if sys.argv[1:] == ['--version']:
    os.write(1, VERSION)
    raise SystemExit(0)
assert sys.argv[1] == 'compile'
source = pathlib.Path(sys.argv[-2])
output = pathlib.Path(sys.argv[-1])
deps = pathlib.Path(sys.argv[sys.argv.index('--deps') + 1])
if MARKER:
    pathlib.Path(MARKER).write_text(str(os.getpid()))
if MODE == 'nonzero':
    os.write(2, b'owned fixture failed\\n')
    raise SystemExit(19)
if MODE == 'signal':
    os.kill(os.getpid(), signal.SIGTERM)
if MODE == 'hang':
    time.sleep(30)
prefix = b'%PDF-1.7\\n%retained-source=' + source.read_bytes().hex().encode() + b'\\n'
pdf = prefix + b'xref\\n0 1\\n0000000000 65535 f \\ntrailer\\n<< /Size 1 >>\\nstartxref\\n' + str(len(prefix)).encode() + b'\\n%%EOF\\n'
output.write_bytes(pdf)
deps.write_bytes(os.fsencode(source.relative_to(pathlib.Path.cwd())) + b'\\0')
'''
    raw = (f'#!{sys.executable}\nVERSION = {compiler._VERSION!r}\n'
           f'MODE = {mode!r}\nMARKER = {str(marker) if marker else None!r}\n' + body).encode()
    if path.exists():
        path.chmod(0o700)
    path.write_bytes(raw)
    path.chmod(0o500)
    return compiler._ReviewedNativeArtifact(path, sha256(raw), compiler._VERSION)


@contextmanager
def fixture_fix(f, **kwargs):
    with mock.patch.object(protected_fix, 'CATALOG', f.catalog):
        with ProtectedCoreFix(f.root, f.target, journal_directory=f.source_store,
                              lease=f.source_lease, **kwargs) as fix:
            yield fix


def file_state(path):
    value = path.lstat()
    attrs = ()
    if stat.S_ISREG(value.st_mode):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            attrs = _xattrs(fd)
        finally:
            os.close(fd)
    return (path.read_bytes(), value.st_dev, value.st_ino, value.st_mode,
            value.st_uid, value.st_gid, value.st_mtime_ns, value.st_ctime_ns, attrs)


class _PublicationFixtures(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='typst-pdf-integration-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.serial = 0

    def fixture(self, **kwargs):
        self.serial += 1
        return make_fixture(self.base / str(self.serial), **kwargs)

    def session(self, fix, f, **kwargs):
        return publication.PreparedPdfPublication(fix, destination=f.destination,
                                                  lease=f.output_lease, **kwargs)

    def publish(self, pub, fix, **kwargs):
        return pub.compile_and_publish(fix.compile_snapshot(),
            profile=kwargs.pop('profile', compiler.RESOURCE_PROFILE), **kwargs)

    def assert_source_committed(self, f):
        self.assertEqual(f.source.read_bytes(), EXPECTED)
        manifests = list(f.source_store.glob('root-*/*/manifest.json'))
        self.assertEqual(len(manifests), 1)
        events = [json.loads(p.read_bytes())['kind']
                  for p in sorted(manifests[0].parent.glob('event-*.json'))]
        self.assertEqual(events[-1], 'committed')
        self.assertNotIn('recovery_required', events)

    def recover(self, f, journal_id, **kwargs):
        return store.recover_publication(f.root, destination=f.destination,
            journal_id=journal_id, lease=f.output_lease, **kwargs)

    def pending(self, f):
        return tuple(e for e in store.inventory_publications(f.root) if e.pending)


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX publication protocol')
class PdfPublicationIntegrationTests(_PublicationFixtures):
    def test_existing_and_absent_nested_spaced_outputs_publish_exact_retained_source(self):
        for present, mode in ((True, 0o644), (True, 0o600), (False, 0o600)):
            with self.subTest(present=present, mode=mode):
                f = self.fixture(present=present, output_mode=mode)
                untouched = file_state(f.unrelated)
                record = make_compiler(f)
                with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
                    with self.session(fix, f) as pub:
                        fix.apply(fix.validate())
                        report = self.publish(pub, fix)
                self.assertTrue(f.output.read_bytes().startswith(b'%PDF-1.7\n'))
                self.assertIn(EXPECTED.hex().encode(), f.output.read_bytes())
                self.assertNotIn(ORIGINAL.hex().encode(), f.output.read_bytes())
                self.assertIn(stat.S_IMODE(f.output.stat().st_mode), (0o600, 0o644))
                self.assertEqual(file_state(f.unrelated), untouched)
                self.assert_source_committed(f)
                self.assertEqual(self.pending(f), ())
                with self.assertRaises((FrozenInstanceError, AttributeError)):
                    report.phase = 'forged'

    def test_output_capture_and_pending_scan_happen_before_source_application_or_compiler(self):
        f = self.fixture()
        original = file_state(f.source)
        seen = []

        def observe(point, path):
            if point == 'after_prepared':
                self.assertEqual(file_state(f.source), original)
                self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
                self.assertEqual(len([entry for entry in store._inventory_already_locked(fix.transaction) if entry.pending]), 1)
                # Borrowed inventory must not release or replace the source lock.
                with self.assertRaises(store.PublicationError):
                    store.inventory_publications(f.root)
                seen.append(point)
        record = make_compiler(f)
        with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
            with self.session(fix, f, fault=observe) as pub:
                self.assertEqual(seen, ['after_prepared'])
                fix.apply(fix.validate())
                self.publish(pub, fix)
        self.assert_source_committed(f)

    def test_separate_explicit_output_lease_and_real_active_fix_are_required(self):
        f = self.fixture()
        before = file_state(f.source), file_state(f.output)
        with fixture_fix(f) as fix:
            for lease in (None, object(), f.source_lease):
                with self.subTest(lease=type(lease)):
                    with self.assertRaises((TransactionError, TypeError, ValueError)):
                        with publication.PreparedPdfPublication(fix, destination=f.destination, lease=lease):
                            self.fail('invalid output lease entered')
        for fix in (object(),):
            with self.assertRaises((TransactionError, TypeError, ValueError)):
                with self.session(fix, f):
                    self.fail('foreign fix entered')
        self.assertEqual((file_state(f.source), file_state(f.output)), before)

    def test_publication_must_be_prepared_before_validate_apply_and_snapshot(self):
        f = self.fixture()
        with fixture_fix(f) as fix:
            fix.apply(fix.validate())
            with self.assertRaises(TransactionError):
                with self.session(fix, f):
                    self.fail('late output backup admitted')
        self.assert_source_committed(f)
        self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)

    def test_destination_lexical_paths_reject_without_source_or_output_changes(self):
        bad = ('', '.', '..', '../outside.pdf', '/tmp/outside.pdf', 'build/../document.pdf',
               './document.pdf', 'double//document.pdf', 'document.PDF', 'document.typ',
               'document.pdf/', 'document.pdf\x00', 'new-parent/document.pdf')
        for destination in bad:
            with self.subTest(destination=destination):
                f = self.fixture()
                before = file_state(f.source), file_state(f.output)
                with fixture_fix(f) as fix:
                    with self.assertRaises((TransactionError, ValueError, OSError)):
                        with publication.PreparedPdfPublication(fix, destination=destination, lease=f.output_lease):
                            self.fail('unsafe destination entered')
                self.assertEqual((file_state(f.source), file_state(f.output)), before)

    def test_symlink_hardlink_directory_fifo_and_unsafe_modes_refuse_before_apply(self):
        for kind in ('symlink', 'hardlink', 'directory', 'fifo', 'readonly', 'group-writable', 'executable'):
            with self.subTest(kind=kind):
                f = self.fixture()
                if kind in ('symlink', 'directory', 'fifo'):
                    f.output.unlink()
                if kind == 'symlink':
                    f.output.symlink_to(f.unrelated)
                elif kind == 'hardlink':
                    os.link(f.output, f.base / 'second-output-link')
                elif kind == 'directory':
                    f.output.mkdir()
                elif kind == 'fifo':
                    os.mkfifo(f.output)
                else:
                    f.output.chmod({'readonly': 0o444, 'group-writable': 0o664, 'executable': 0o755}.get(kind, 0o644))
                source = file_state(f.source)
                with fixture_fix(f) as fix:
                    with self.assertRaises((TransactionError, OSError)):
                        with self.session(fix, f):
                            self.fail('unsafe output entered')
                self.assertEqual(file_state(f.source), source)

    def test_symlink_output_ancestor_refuses_without_following_it(self):
        f = self.fixture()
        moved = f.output.parent.with_name('actual-output-parent')
        f.output.parent.rename(moved)
        f.output.parent.symlink_to(moved, target_is_directory=True)
        before = file_state(moved / f.output.name)
        with fixture_fix(f) as fix:
            with self.assertRaises((TransactionError, OSError)):
                with self.session(fix, f):
                    self.fail('symlink ancestor entered')
        self.assertEqual(file_state(moved / f.output.name), before)

    def test_output_same_bytes_on_new_inode_and_later_edits_are_never_overwritten(self):
        for kind in ('same-bytes-new-inode', 'later-edit', 'deleted', 'hardlinked'):
            with self.subTest(kind=kind):
                f = self.fixture()
                record = make_compiler(f)
                saved = []

                def mutate(point, path):
                    if point != 'after_compiled':
                        return
                    if kind == 'same-bytes-new-inode':
                        replacement = f.output.with_suffix('.user-save')
                        replacement.write_bytes(f.output.read_bytes())
                        replacement.chmod(0o644)
                        os.replace(replacement, f.output)
                    elif kind == 'later-edit':
                        f.output.write_bytes(b'later exporter output\n')
                    elif kind == 'deleted':
                        f.output.unlink()
                    else:
                        os.link(f.output, f.base / 'later-output-link')
                    saved.append(file_state(f.output) if f.output.exists() else None)
                with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
                    with self.assertRaises((publication.PublicationError, TransactionError, OSError)):
                        with self.session(fix, f, fault=mutate) as pub:
                            fix.apply(fix.validate())
                            self.publish(pub, fix)
                self.assertEqual(file_state(f.output) if f.output.exists() else None, saved[0])
                self.assert_source_committed(f)

    def test_output_parent_or_root_replacement_is_preserved_and_never_rebased(self):
        for kind in ('output-parent', 'root'):
            with self.subTest(kind=kind):
                f = self.fixture()
                record = make_compiler(f)
                saved = []

                def mutate(point, path):
                    if point != 'after_compiled':
                        return
                    if kind == 'output-parent':
                        moved = f.output.parent.with_name('retained-original-output-parent')
                        f.output.parent.rename(moved)
                        f.output.parent.mkdir()
                        f.output.write_bytes((moved / f.output.name).read_bytes())
                        f.output.chmod(0o644)
                        saved.append((file_state(f.output), moved / f.output.name,
                                      file_state(moved / f.output.name)))
                    else:
                        moved = f.root.with_name('retained-original-consumer')
                        f.root.rename(moved)
                        f.root.mkdir()
                        f.source.parent.mkdir(parents=True)
                        f.source.write_bytes(EXPECTED)
                        f.config.write_bytes((moved / '.typst-lint.toml').read_bytes())
                        f.output.parent.mkdir(parents=True)
                        f.output.write_bytes(OLD_OUTPUT)
                        f.output.chmod(0o644)
                        saved.append((file_state(f.output), moved / f.destination,
                                      file_state(moved / f.destination)))
                with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
                    with self.assertRaises((publication.PublicationError, TransactionError, OSError)) as error:
                        with self.session(fix, f, fault=mutate) as pub:
                            fix.apply(fix.validate())
                            self.publish(pub, fix)
                self.assertEqual(file_state(f.output), saved[0][0])
                self.assertEqual(file_state(saved[0][1]), saved[0][2])
                self.assertEqual(f.source.read_bytes(), EXPECTED)
                self.assertEqual(error.exception.report.gate_status, 3)
                self.assertEqual(error.exception.report.compiler_status, 0)

    def test_source_config_catalog_or_compiler_drift_refuses_after_zero_exit(self):
        for kind in ('source', 'config', 'catalog', 'compiler'):
            with self.subTest(kind=kind):
                f = self.fixture()
                record = make_compiler(f)
                old_output = file_state(f.output)

                def mutate(point, path):
                    if point == 'after_compiled':
                        changed = {'source': f.source, 'config': f.config,
                                   'catalog': f.catalog, 'compiler': record.path}[kind]
                        changed.chmod(0o600)
                        changed.write_bytes(changed.read_bytes() + b'\n')
                with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
                    with self.assertRaises((publication.PublicationError, compiler.CompileSafetyError, TransactionError)):
                        with self.session(fix, f, fault=mutate) as pub:
                            fix.apply(fix.validate())
                            self.publish(pub, fix)
                self.assertEqual(file_state(f.output), old_output)
                self.assertEqual(f.source.read_bytes(), EXPECTED + (b'\n' if kind == 'source' else b''))

    def test_compiler_nonzero_signal_timeout_and_cancel_preserve_output_and_source_fix(self):
        for mode in ('nonzero', 'signal', 'timeout', 'cancel'):
            with self.subTest(mode=mode):
                f = self.fixture()
                marker = f.base / 'compile actually started.pid'
                record = make_compiler(f, mode='hang' if mode in ('timeout', 'cancel') else mode, marker=marker)
                old_output = file_state(f.output)
                event = threading.Event()
                timer = None
                try:
                    with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
                        with self.assertRaises(publication.PublicationError) as error:
                            with self.session(fix, f) as pub:
                                fix.apply(fix.validate())
                                if mode == 'cancel':
                                    def cancel_after_actual_launch():
                                        until = time.monotonic() + 4
                                        while not marker.exists() and time.monotonic() < until:
                                            time.sleep(0.01)
                                        event.set()
                                    timer = threading.Thread(target=cancel_after_actual_launch)
                                    timer.start()
                                self.publish(pub, fix, deadline_seconds=2 if mode == 'timeout' else 5,
                                             cancel_event=event)
                finally:
                    if timer:
                        timer.join()
                self.assertTrue(marker.exists(), 'Failure happened before actual compiler launch')
                report = error.exception.report
                self.assertEqual(report.gate_status, 3)
                self.assertIs(report.compiler_invoked, True)
                expected_status = {'nonzero': 19, 'signal': -signal.SIGTERM,
                                   'timeout': -signal.SIGKILL, 'cancel': -signal.SIGKILL}[mode]
                self.assertEqual(report.compiler_status, expected_status)
                observed = [a for a in report.attempted_invocations if a.purpose == 'compile']
                self.assertEqual(len(observed), 1)
                self.assertEqual(observed[0].returncode, expected_status)
                self.assertEqual(file_state(f.output), old_output)
                self.assert_source_committed(f)

    def test_no_public_bytes_artifact_output_or_compiler_override(self):
        f = self.fixture()
        record = make_compiler(f)
        with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
            with self.session(fix, f) as pub:
                fix.apply(fix.validate())
                snapshot = fix.compile_snapshot()
                for kwargs in ({'pdf_bytes': b'%PDF forged'}, {'artifact': object()},
                               {'output': 'other.pdf'}, {'compiler': record.path},
                               {'extra_args': ['--open']}):
                    with self.subTest(kwargs=tuple(kwargs)), self.assertRaises(TypeError):
                        self.publish(pub, fix, **kwargs)
                with self.assertRaises((TransactionError, publication.PublicationError, compiler.CompileSafetyError)):
                    pub.compile_and_publish(object(), profile=compiler.RESOURCE_PROFILE)
        self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
        self.assert_source_committed(f)

    def test_noop_source_application_can_publish_without_manufacturing_source_journal(self):
        f = self.fixture(source=EXPECTED)
        record = make_compiler(f)
        before = file_state(f.source)
        with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
            with self.session(fix, f) as pub:
                result = fix.apply(fix.validate())
                self.assertEqual(result.modified, ())
                report = self.publish(pub, fix)
        self.assertEqual(file_state(f.source), before)
        self.assertEqual(report.source_commit, 'committed')
        self.assertIsNone(report.source_journal_id)
        self.assertEqual(list(f.source_store.glob('root-*/*/manifest.json')), [])
        self.assertTrue(f.output.read_bytes().startswith(b'%PDF-'))

    def test_unknown_compiler_or_explicit_unsupported_profile_reports_no_compile(self):
        for kind in ('unknown-compiler', 'unsupported-profile'):
            with self.subTest(kind=kind):
                f = self.fixture()
                record = make_compiler(f)
                if kind == 'unknown-compiler':
                    record = compiler._ReviewedNativeArtifact(record.path, '0' * 64, record.version)
                before = file_state(f.output)
                with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
                    with self.assertRaises(publication.PublicationError) as error:
                        with self.session(fix, f) as pub:
                            fix.apply(fix.validate())
                            self.publish(pub, fix, profile='ordinary' if kind == 'unsupported-profile' else compiler.RESOURCE_PROFILE)
                report = error.exception.report
                self.assertIs(report.compiler_invoked, False)
                self.assertIsNone(report.compiler_status)
                self.assertEqual(report.attempted_invocations, ())
                self.assertEqual(report.gate_status, 2 if kind == 'unsupported-profile' else 3)
                self.assertEqual(file_state(f.output), before)
                self.assert_source_committed(f)

    def test_foreign_snapshot_refuses_before_any_compile(self):
        f = self.fixture()
        alien = self.fixture()
        record = make_compiler(f)
        with fixture_fix(alien) as other:
            other.apply(other.validate())
            foreign = other.compile_snapshot()
            with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record), \
                    mock.patch.object(compiler, '_run', side_effect=AssertionError('foreign snapshot compiled')):
                with self.session(fix, f) as pub:
                    fix.apply(fix.validate())
                    with self.assertRaises((publication.PublicationError, TransactionError, compiler.CompileSafetyError)):
                        pub.compile_and_publish(foreign, profile=compiler.RESOURCE_PROFILE)
        self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
        self.assert_source_committed(f)

    def test_preparation_write_readback_and_fsync_faults_never_apply_source_or_compile(self):
        failures = [('namespace_fsync', None), ('metadata_copy', 'backup.bin')]
        for name in ('backup.bin', 'manifest.json', 'event-0000.json'):
            failures.extend((point, name) for point in
                            ('before_write', 'write', 'file_fsync', 'before_readback'))
        failures.extend((point, 'prepared') for point in ('before_event', 'event_directory_fsync'))
        for point, name in failures:
            with self.subTest(point=point, name=name):
                f = self.fixture()
                before = file_state(f.source), file_state(f.output)
                fired = []

                def fail(where, path):
                    if not fired and where == point and (name is None or path == name):
                        fired.append(where)
                        raise OSError('owned preparation fault')
                with fixture_fix(f) as fix, mock.patch.object(compiler, '_run',
                        side_effect=AssertionError('preparation invoked compiler')):
                    with self.assertRaises((publication.PublicationError, OSError, TransactionError)):
                        with self.session(fix, f, fault=fail):
                            self.fail('fault did not interrupt preparation')
                self.assertEqual(fired, [point])
                self.assertEqual((file_state(f.source), file_state(f.output)), before)

    def test_pre_replace_write_readback_event_and_fsync_failures_keep_committed_source(self):
        failures = [(point, 'publish.bin') for point in
                    ('before_write', 'write', 'metadata_copy', 'file_fsync', 'before_readback')]
        failures += [(point, state) for point in ('before_event', 'event_directory_fsync')
                     for state in ('compiled', 'staged', 'replace_intent')]
        failures += [(point, None) for point in ('after_compiled', 'after_staged', 'before_replace')]
        for point, name in failures:
            with self.subTest(point=point, name=name):
                f = self.fixture()
                before = file_state(f.output)
                record = make_compiler(f)
                fired = []

                def fail(where, path):
                    if not fired and where == point and (name is None or path == name):
                        fired.append(where)
                        raise OSError('owned pre-replace publication fault')
                with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
                    with self.assertRaises((publication.PublicationError, OSError, TransactionError)):
                        with self.session(fix, f, fault=fail) as pub:
                            fix.apply(fix.validate())
                            self.publish(pub, fix)
                self.assertEqual(fired, [point])
                self.assertEqual(file_state(f.output), before)
                self.assert_source_committed(f)

    def test_replace_then_directory_fsync_and_other_precommit_failures_restore_supported_mode(self):
        failures = ('after_replace_before_event', 'after_replaced', 'destination_directory_fsync',
                    'moved_stage_directory_fsync', 'after_directory_sync_before_commit')
        for present, mode in ((True, 0o600), (True, 0o644), (False, 0o600)):
            for point in failures:
                with self.subTest(present=present, mode=mode, point=point):
                    f = self.fixture(present=present, output_mode=mode)
                    record = make_compiler(f)
                    fired = []

                    def fail(where, path):
                        if not fired and where == point:
                            self.assertTrue(f.output.read_bytes().startswith(b'%PDF-'))
                            fired.append(where)
                            raise OSError('owned post-replace fault')
                    with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
                        with self.assertRaises(publication.PublicationError) as error:
                            with self.session(fix, f, fault=fail) as pub:
                                fix.apply(fix.validate())
                                self.publish(pub, fix)
                    self.assertEqual(fired, [point])
                    report = error.exception.report
                    self.assertEqual(report.gate_status, 3)
                    self.assertIs(report.compiler_invoked, True)
                    self.assertEqual(report.compiler_status, 0)
                    if present:
                        self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
                        self.assertEqual(stat.S_IMODE(f.output.stat().st_mode), mode)
                    else:
                        self.assertFalse(f.output.exists())
                    self.assert_source_committed(f)

    def test_replace_syscall_failure_or_success_then_error_is_truthfully_classified(self):
        for performed in (False, True):
            with self.subTest(performed=performed):
                f = self.fixture()
                record = make_compiler(f)
                before = file_state(f.output)
                actual_replace = os.replace
                calls = []

                def replace(source, destination, *args, **kwargs):
                    if source == 'publish.bin':
                        calls.append(True)
                        if performed:
                            actual_replace(source, destination, *args, **kwargs)
                        raise OSError('owned replace syscall outcome test')
                    return actual_replace(source, destination, *args, **kwargs)
                with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
                    with self.session(fix, f) as pub:
                        fix.apply(fix.validate())
                        with mock.patch.object(store.os, 'replace', side_effect=replace):
                            with self.assertRaises(publication.PublicationError) as error:
                                self.publish(pub, fix)
                self.assertEqual(calls, [True])
                report = error.exception.report
                self.assertEqual(report.gate_status, 3)
                self.assertEqual(report.compiler_status, 0)
                if performed:
                    self.assertTrue(f.output.read_bytes().startswith(b'%PDF-'))
                    self.assertTrue(report.recovery_required)
                    self.assertNotEqual(report.output_disposition, 'untouched')
                else:
                    self.assertEqual(file_state(f.output), before)
                    self.assertEqual(report.output_disposition, 'untouched')
                self.assert_source_committed(f)

    def test_restoration_write_readback_and_fsync_faults_leave_truthful_pending_evidence(self):
        failures = [(point, 'restore.bin') for point in
                    ('before_write', 'write', 'metadata_copy', 'file_fsync', 'before_readback')]
        failures += [(point, None) for point in
                     ('before_restore', 'after_restore_before_event',
                      'restoration_directory_fsync', 'restoration_journal_directory_fsync')]
        for point, name in failures:
            with self.subTest(point=point, name=name):
                f = self.fixture()
                record = make_compiler(f)
                armed = []
                fired = []

                def fail(where, path):
                    if where == 'after_replaced':
                        armed.append(True)
                        raise OSError('owned initial publication failure')
                    if armed and not fired and where == point and (name is None or path == name):
                        fired.append(where)
                        raise OSError('owned restoration fault')
                with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
                    with self.assertRaises(publication.PublicationError) as error:
                        with self.session(fix, f, fault=fail) as pub:
                            fix.apply(fix.validate())
                            self.publish(pub, fix)
                self.assertEqual(fired, [point])
                report = error.exception.report
                self.assertTrue(report.recovery_required)
                self.assertEqual(report.gate_status, 3)
                self.assertEqual(report.compiler_status, 0)
                self.assertEqual(len(self.pending(f)), 1)
                self.assert_source_committed(f)

    def test_later_output_edit_after_visible_replace_is_preserved_instead_of_rolled_back(self):
        for kind in ('edit', 'same-bytes-new-inode'):
            with self.subTest(kind=kind):
                f = self.fixture()
                record = make_compiler(f)
                saved = []

                def fail(point, path):
                    if point == 'after_replaced':
                        if kind == 'edit':
                            f.output.write_bytes(b'later writer after visible replacement\n')
                        else:
                            alternate = f.output.with_suffix('.later')
                            alternate.write_bytes(f.output.read_bytes())
                            alternate.chmod(stat.S_IMODE(f.output.stat().st_mode))
                            os.replace(alternate, f.output)
                        saved.append(file_state(f.output))
                        raise OSError('owned failure after later output write')
                with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
                    with self.assertRaises(publication.PublicationError) as error:
                        with self.session(fix, f, fault=fail) as pub:
                            fix.apply(fix.validate())
                            self.publish(pub, fix)
                self.assertEqual(file_state(f.output), saved[0])
                self.assertTrue(error.exception.report.recovery_required)
                self.assertEqual(error.exception.report.gate_status, 3)
                self.assertEqual(error.exception.report.compiler_status, 0)
                self.assert_source_committed(f)

    def test_replacement_and_restoration_event_persistence_failures_preserve_truth_and_sources(self):
        for state in ('replaced', 'rollback_intent', 'restored'):
            for failure in ('before_write', 'write', 'file_fsync', 'before_readback', 'event_directory_fsync'):
                with self.subTest(state=state, failure=failure):
                    f = self.fixture()
                    record = make_compiler(f)
                    armed = []
                    fired = []

                    def fail(point, path):
                        if state != 'replaced' and point == 'after_replaced':
                            raise OSError('owned event test starts rollback')
                        if point == 'before_event' and path == state:
                            armed.append(f'event-{len(pub._store.events):04d}.json')
                        if armed and not fired and point == failure and (
                                path == armed[-1] or (point == 'event_directory_fsync' and path == state)):
                            fired.append(point)
                            raise OSError('owned publication event persistence fault')
                    with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
                        with self.assertRaises(publication.PublicationError) as error:
                            with self.session(fix, f, fault=fail) as pub:
                                fix.apply(fix.validate())
                                self.publish(pub, fix)
                    self.assertEqual(fired, [failure])
                    report = error.exception.report
                    self.assertEqual(report.gate_status, 3)
                    self.assertEqual(report.compiler_status, 0)
                    self.assertIs(report.compiler_invoked, True)
                    raw = f.output.read_bytes()
                    self.assertTrue(raw == OLD_OUTPUT or (raw.startswith(b'%PDF-') and EXPECTED.hex().encode() in raw))
                    if state == 'restored':
                        self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
                    self.assert_source_committed(f)

    def test_completion_event_fsync_uncertainty_never_claims_success_or_blindly_rolls_back(self):
        for failure in ('file_fsync', 'event_directory_fsync'):
            with self.subTest(failure=failure):
                f = self.fixture()
                record = make_compiler(f)
                armed = []
                fired = []

                def fail(point, path):
                    if point == 'before_event' and path == 'published':
                        armed.append(True)
                    if armed and not fired and point == failure:
                        fired.append(point)
                        raise OSError('owned uncertain completion fsync')
                with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
                    with self.assertRaises(publication.PublicationError) as error:
                        with self.session(fix, f, fault=fail) as pub:
                            fix.apply(fix.validate())
                            self.publish(pub, fix)
                self.assertEqual(fired, [failure])
                self.assertTrue(f.output.read_bytes().startswith(b'%PDF-'))
                report = error.exception.report
                self.assertEqual(report.gate_status, 3)
                self.assertEqual(report.compiler_status, 0)
                self.assertTrue(report.recovery_required)
                self.assertNotEqual(report.output_disposition, 'untouched')
                saved = file_state(f.output)
                def fail_terminal_sync(point, path):
                    if point == 'recovery_terminal_directory_fsync':
                        raise OSError('owned terminal acknowledgement sync failure')
                retry = self.recover(f, report.journal_id, fault=fail_terminal_sync)
                self.assertTrue(retry.recovery_required)
                self.assertEqual(file_state(f.output), saved)
                complete = self.recover(f, report.journal_id)
                self.assertFalse(complete.recovery_required)
                self.assertEqual(complete.phase, 'published')
                self.assertEqual(file_state(f.output), saved)
                self.assert_source_committed(f)

    def test_terminal_recovery_acknowledgements_need_successful_file_and_directory_sync(self):
        for state in ('published', 'restored', 'aborted_unpublished'):
            with self.subTest(state=state):
                f = self.fixture()
                record = make_compiler(f, mode='nonzero' if state == 'aborted_unpublished' else 'success')
                def fail_publication(point, path):
                    if state == 'restored' and point == 'after_replaced':
                        raise OSError('owned terminal restored fixture')
                with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
                    with self.session(fix, f, fault=fail_publication) as pub:
                        fix.apply(fix.validate())
                        if state == 'published':
                            report = self.publish(pub, fix)
                        else:
                            with self.assertRaises(publication.PublicationError) as error:
                                self.publish(pub, fix)
                            report = error.exception.report
                self.assertEqual(report.phase, state)
                saved = file_state(f.output)
                with mock.patch.object(store.os, 'fsync', side_effect=AssertionError('read-only inventory fsync')):
                    inventory = store.inventory_publications(f.root)
                self.assertEqual(len(inventory), 1)
                self.assertFalse(inventory[0].pending)
                self.assertEqual(file_state(f.output), saved)
                for failure in ('recovery_terminal_file_fsync', 'recovery_terminal_directory_fsync'):
                    fired = []
                    def fail(point, path):
                        if point == failure:
                            fired.append(point)
                            raise OSError('owned terminal synchronization fault')
                    retry = self.recover(f, report.journal_id, fault=fail)
                    self.assertEqual(fired, [failure])
                    self.assertTrue(retry.recovery_required)
                    self.assertEqual(retry.gate_status, 3)
                    self.assertEqual(file_state(f.output), saved)
                complete = self.recover(f, report.journal_id)
                self.assertFalse(complete.recovery_required)
                self.assertEqual(complete.phase, state)
                self.assertEqual(file_state(f.output), saved)
                self.assert_source_committed(f)

    def test_new_writer_strengthens_uncertain_visible_terminal_before_validate_or_new_journal(self):
        for failure in ('writer_terminal_file_fsync', 'writer_terminal_directory_fsync'):
            with self.subTest(failure=failure):
                f = self.fixture()
                record = make_compiler(f)
                def uncertain_commit(point, path):
                    if point == 'event_directory_fsync' and path == 'published':
                        raise OSError('owned uncertain visible terminal fixture')
                with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
                    with self.assertRaises(publication.PublicationError) as error:
                        with self.session(fix, f, fault=uncertain_commit) as pub:
                            fix.apply(fix.validate())
                            self.publish(pub, fix)
                self.assertTrue(error.exception.report.recovery_required)
                saved = file_state(f.output), file_state(f.source)
                before = {str(p.relative_to(f.base)): file_state(p)
                          for p in f.base.glob('.typst-lint-pdf-journals/root-*/*/*') if p.is_file()}
                fired = []
                def fail(point, path):
                    if point == failure:
                        fired.append(point)
                        raise OSError('owned new-writer terminal durability failure')
                with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
                    with self.assertRaises((publication.PublicationError, OSError)):
                        with self.session(fix, f, fault=fail):
                            self.fail('new session entered before prior terminal durability was confirmed')
                self.assertEqual(fired, [failure])
                self.assertEqual((file_state(f.output), file_state(f.source)), saved)
                after = {str(p.relative_to(f.base)): file_state(p)
                         for p in f.base.glob('.typst-lint-pdf-journals/root-*/*/*') if p.is_file()}
                self.assertEqual(after, before)
                # Retry succeeds without source reversion or deleting old history.
                with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
                    with self.session(fix, f) as pub:
                        result = fix.apply(fix.validate())
                        self.assertEqual(result.modified, ())
                        completed = self.publish(pub, fix)
                self.assertEqual(completed.gate_status, 0)
                self.assertEqual(self.pending(f), ())
                self.assert_source_committed(f)

    def test_failure_reporting_after_durable_publication_never_rolls_back_commit(self):
        f = self.fixture()
        record = make_compiler(f)
        saved = []

        def fail(point, path):
            if point == 'after_published':
                saved.append(file_state(f.output))
                raise OSError('owned post-durable-commit reporting fault')
        with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
            with self.assertRaises((publication.PublicationError, OSError)):
                with self.session(fix, f, fault=fail) as pub:
                    fix.apply(fix.validate())
                    self.publish(pub, fix)
        self.assertEqual(file_state(f.output), saved[0])
        self.assertEqual(self.pending(f), ())
        self.assert_source_committed(f)

    def test_protected_catalog_compiler_and_launcher_destination_collisions_refuse(self):
        for kind in ('catalog', 'compiler', 'launcher'):
            with self.subTest(kind=kind):
                f = self.fixture()
                record = make_compiler(f)
                if kind == 'catalog':
                    f.output.write_bytes(f.catalog.read_bytes())
                    f.catalog = f.output
                elif kind == 'compiler':
                    record = compiler._ReviewedNativeArtifact(f.output, sha256(f.output.read_bytes()), compiler._VERSION)
                before = file_state(f.output), file_state(f.source)
                launchers = (f.output,) if kind == 'launcher' else compiler._LAUNCHERS
                with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record), \
                        mock.patch.object(compiler, '_LAUNCHERS', launchers):
                    with self.assertRaises((publication.PublicationError, TransactionError)):
                        with self.session(fix, f):
                            self.fail('protected output collision entered')
                self.assertEqual((file_state(f.output), file_state(f.source)), before)

    @unittest.skipUnless(sys.platform == 'darwin', 'Native macOS provenance characterization')
    def test_real_mac_provenance_is_exactly_preserved_by_publication_and_restoration(self):
        for rollback in (False, True):
            with self.subTest(rollback=rollback):
                f = self.fixture(output_mode=0o644)
                record = make_compiler(f)
                baseline = file_state(f.output)[-1]
                self.assertIn('com.apple.provenance', dict(baseline))

                def fail(point, path):
                    if rollback and point == 'after_replaced':
                        self.assertEqual(file_state(f.output)[-1], baseline)
                        raise OSError('owned provenance-preserving rollback test')
                with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
                    if rollback:
                        with self.assertRaises(publication.PublicationError):
                            with self.session(fix, f, fault=fail) as pub:
                                fix.apply(fix.validate())
                                self.publish(pub, fix)
                    else:
                        with self.session(fix, f, fault=fail) as pub:
                            fix.apply(fix.validate())
                            self.publish(pub, fix)
                self.assertEqual(file_state(f.output)[-1], baseline)
                self.assertEqual(stat.S_IMODE(f.output.stat().st_mode), 0o644)
                if rollback:
                    self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
                else:
                    self.assertTrue(f.output.read_bytes().startswith(b'%PDF-'))
                self.assert_source_committed(f)

    def test_unsupported_output_xattr_is_preserved_and_refused_before_apply(self):
        f = self.fixture()
        name = 'com.example.pdf-publication-test' if sys.platform == 'darwin' else 'user.pdf-publication-test'
        fd = os.open(f.output, os.O_RDWR | os.O_NOFOLLOW)
        try:
            wanted = dict(_xattrs(fd))
            wanted[name] = b'unsupported output metadata'
            _copy_xattrs(fd, tuple(sorted(wanted.items())))
        finally:
            os.close(fd)
        before = file_state(f.output), file_state(f.source)
        with fixture_fix(f) as fix:
            with self.assertRaises((publication.PublicationError, TransactionError)):
                with self.session(fix, f):
                    self.fail('unsupported output metadata entered')
        self.assertEqual((file_state(f.output), file_state(f.source)), before)
        fd = os.open(f.output, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            self.assertEqual(dict(_xattrs(fd))[name], b'unsupported output metadata')
        finally:
            os.close(fd)

    def test_output_size_and_journal_bounds_fail_at_the_correct_commit_boundary(self):
        for limits, after_apply in (
                (store.PublicationLimits(max_old_pdf_bytes=len(OLD_OUTPUT)-1), False),
                (store.PublicationLimits(max_journal_binary_bytes=len(OLD_OUTPUT)-1), False),
                (store.PublicationLimits(max_journal_file_bytes=1), False),
                (store.PublicationLimits(max_store_bytes=1), False),
                (store.PublicationLimits(max_new_pdf_bytes=1), True)):
            with self.subTest(limits=limits):
                f = self.fixture()
                record = make_compiler(f)
                before = file_state(f.output)
                applied = []
                with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
                    with self.assertRaises((publication.PublicationError, TransactionError)):
                        with self.session(fix, f, limits=limits) as pub:
                            fix.apply(fix.validate())
                            applied.append(True)
                            self.publish(pub, fix)
                self.assertEqual(bool(applied), after_apply)
                self.assertEqual(file_state(f.output), before)
                self.assertEqual(f.source.read_bytes(), EXPECTED if after_apply else ORIGINAL)
                if after_apply:
                    self.assert_source_committed(f)

    def test_inventory_on_missing_store_is_read_only(self):
        f = self.fixture()
        before = {str(p.relative_to(f.base)) for p in f.base.rglob('*')}
        with mock.patch.object(store.os, 'fsync', side_effect=AssertionError('read-only inventory fsync')):
            self.assertEqual(store.inventory_publications(f.root), ())
        self.assertEqual({str(p.relative_to(f.base)) for p in f.base.rglob('*')}, before)


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1', 'Explicit native Mac compiler opt-in')
@unittest.skipUnless(sys.platform == 'darwin' and EXECUTABLE.is_file() and PINNED.is_file(),
                     'Pinned Mac compiler and official parser required')
class NativePdfPublicationIntegrationTests(_PublicationFixtures):
    def test_actual_reviewed_typst_publishes_only_temporary_consumer(self):
        self.assertEqual(sha256(PINNED.read_bytes()), PINNED_SHA256)
        f = self.fixture()
        with fixture_fix(f) as fix:
            with self.session(fix, f) as pub:
                fix.apply(fix.validate())
                self.publish(pub, fix)
        self.assertTrue(f.output.read_bytes().startswith(b'%PDF-'))
        self.assertEqual(self.pending(f), ())
        self.assert_source_committed(f)


if __name__ == '__main__':
    unittest.main()
