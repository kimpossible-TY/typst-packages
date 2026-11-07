"""Independent real-filesystem integration/restart tests for graph publication.

Owned graph bridges use the real parser and source transaction. Native resources
are replaced only at the existing reviewed-artifact seam with arbitrary synthetic
programs. The killed PID is always an exact child created by this test module.
"""
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import captured_relative_publish as publication
import closed_core_compile as runtime
import pdf_publication_store as store
from graph_compile_test_support import GraphFixture, pdf_bytes
from syntax_client import EXECUTABLE
from transactions import TransactionError, _xattrs

OLD_OUTPUT = b'Owned earlier exporter bytes.\x00\xff\r\n'
WORKER = Path(__file__).with_name('captured_relative_publish_process_worker.py')


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


class _GraphPublicationFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='typst-graph-publication-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.serial = 0

    def fixture(self, *, present=True, mode=0o644):
        self.serial += 1
        f = GraphFixture(self.base / str(self.serial))
        f.destination = 'build with spaces/nested/document file.pdf'
        f.output = f.root / f.destination
        f.output.parent.mkdir(parents=True)
        if present:
            f.output.write_bytes(OLD_OUTPUT)
            f.output.chmod(mode)
        f.output_lease = store.OfflineOutputExclusive.assert_exporters_watchers_closed_and_no_other_writers()
        f.source = f.root / f.target
        f.dependency = f.root / next(name for name in f.sources if name != f.target)
        f.expected = f.sources[f.target].replace(b'x_count', b'x_(count)')
        f.marker = f.base / 'compiler started'
        # Record even a version probe. A pending/corrupt history gate must
        # precede every child launch, not merely the final compile invocation.
        raw = f.compiler.read_bytes().replace(b'if sys.argv[1:] ==',
            (f'pathlib.Path({str(f.marker)!r}).write_bytes(b"started\\n")\n'
             'if sys.argv[1:] ==').encode(), 1)
        f.compiler.chmod(0o700)
        f.compiler.write_bytes(raw)
        f.compiler.chmod(0o500)
        f.reviewed = runtime._ReviewedNativeArtifact(f.compiler, runtime._hash(raw), f.reviewed.version)
        return f

    def session(self, f, fix, **kwargs):
        return publication.PreparedCapturedRelativePdfPublication(fix,
            destination=f.destination, lease=f.output_lease, **kwargs)

    def publish(self, pub, fix):
        return pub.compile_and_publish(fix.committed_graph_snapshot(),
            profile=runtime.RESOURCE_PROFILE, deadline_seconds=60.)

    def assert_committed(self, f, *, current_source=True):
        if current_source:
            self.assertEqual(f.source.read_bytes(), f.expected)
        manifests = list(f.store.glob('root-*/*/manifest.json'))
        self.assertEqual(len(manifests), 1)
        events = [json.loads(path.read_bytes())['kind']
                  for path in sorted(manifests[0].parent.glob('event-*.json'))]
        self.assertEqual(events[-1], 'committed')
        self.assertNotIn('recovery_required', events)

    def assert_compile_zero_failure(self, report):
        self.assertIsNotNone(report)
        self.assertEqual(report.gate_status, 3)
        self.assertIs(report.compiler_invoked, True)
        self.assertEqual(report.compiler_status, 0)
        self.assertEqual(report.source_commit, 'committed')
        self.assertEqual(report.compile_report.compiler_status, 0)
        self.assertEqual(report.resource_report.fonts, ())
        self.assertEqual(sum(i.purpose == 'compile' for i in report.attempted_invocations), 1)

    def pending(self, f):
        return tuple(entry for entry in store.inventory_publications(f.root) if entry.pending)

    def request(self, f, action, **kwargs):
        return dict(action=action, base=str(f.base), root=str(f.root),
            target=f.target, catalog=str(f.catalog), source_store=str(f.store),
            compiler=str(f.compiler), destination=f.destination, **kwargs)

    def worker(self, f, action, **kwargs):
        result = subprocess.run([sys.executable, str(WORKER),
            json.dumps(self.request(f, action, **kwargs))], capture_output=True,
            text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1, result.stdout)
        return json.loads(result.stdout)

    def kill_worker(self, f, action, boundary, *, source=False, **kwargs):
        options = {'source_boundary' if source else 'boundary': boundary}
        child = subprocess.Popen([sys.executable, str(WORKER),
            json.dumps(self.request(f, action, **options, **kwargs))],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=90), f'Child did not reach {boundary}')
                line = child.stdout.readline()
            if not line:
                _, error = child.communicate(timeout=5)
                self.fail(f'Child exited before {boundary}: {error}')
            observed = json.loads(line)
            self.assertEqual(observed.get('paused'), boundary, observed)
            child.kill()
            _, error = child.communicate(timeout=5)
            self.assertEqual(child.returncode, -signal.SIGKILL, error)
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)

    def pending_journal(self, f):
        observed = self.worker(f, 'inventory')
        self.assertNotIn('exception', observed, observed)
        pending = [entry for entry in observed['entries'] if entry['pending']]
        self.assertEqual(len(pending), 1, observed)
        return pending[0]['journal_id']

    def assert_recovered(self, f, journal, *, present=True):
        result = self.worker(f, 'recover', journal_id=journal)
        self.assertNotIn('exception', result, result)
        self.assertFalse(result['report']['recovery_required'], result)
        self.assertEqual(result['report']['gate_status'], 0, result)
        if present:
            self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
        else:
            self.assertFalse(f.output.exists())
        self.assertEqual(self.pending(f), ())
        saved = file_state(f.output) if f.output.exists() else None
        repeated = self.worker(f, 'recover', journal_id=journal)
        self.assertNotIn('exception', repeated, repeated)
        self.assertFalse(repeated['report']['recovery_required'], repeated)
        self.assertEqual(file_state(f.output) if f.output.exists() else None, saved)
        return result


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX publication protocol')
class CapturedRelativePublishIntegrationTests(_GraphPublicationFixture):
    def test_prepared_inventory_and_backup_precede_real_graph_validation(self):
        f = self.fixture()
        before = file_state(f.source), file_state(f.dependency), file_state(f.output)
        points = []

        def observe(point, path):
            if point == 'after_prepared':
                self.assertEqual((file_state(f.source), file_state(f.dependency),
                                  file_state(f.output)), before)
                self.assertFalse(f.marker.exists())
                self.assertEqual(len([e for e in pub._store.inventory() if e.pending]), 1)
                points.append(point)

        with f.context(committed=False) as fix, f.resources():
            pub = self.session(f, fix, fault=observe)
            with pub:
                fix.apply(fix.validate())
                report = self.publish(pub, fix)
        self.assertEqual(points, ['after_prepared'])
        self.assertEqual(report.phase, 'published')
        self.assertEqual(report.gate_status, 0)
        self.assertEqual(f.output.read_bytes(), pdf_bytes())
        self.assertEqual(file_state(f.dependency), before[1])
        self.assert_committed(f)

    def test_selected_preparation_faults_do_not_apply_graph_or_start_compiler(self):
        cases = (('namespace_fsync', None), ('file_fsync', 'backup.bin'),
                 ('before_readback', 'manifest.json'), ('event_directory_fsync', 'prepared'))
        for point, name in cases:
            with self.subTest(point=point, name=name):
                f = self.fixture()
                before = file_state(f.source), file_state(f.dependency), file_state(f.output)
                fired = []

                def fail(where, path):
                    if not fired and where == point and (name is None or path == name):
                        fired.append(where)
                        raise OSError('owned graph preparation failure')

                with f.context(committed=False) as fix, f.resources(), \
                        mock.patch.object(runtime, '_run', side_effect=AssertionError('preparation compiled')):
                    with self.assertRaises((TransactionError, OSError)):
                        with self.session(f, fix, fault=fail):
                            self.fail('preparation fault was not observed')
                self.assertEqual(fired, [point])
                self.assertEqual((file_state(f.source), file_state(f.dependency),
                                  file_state(f.output)), before)
                self.assertFalse(list(f.store.glob('root-*/*/manifest.json')))
                self.assertFalse(f.marker.exists())

    def test_selected_stage_failures_leave_output_exact_and_graph_fix_committed(self):
        cases = (('file_fsync', 'publish.bin'), ('before_readback', 'publish.bin'),
                 ('event_directory_fsync', 'staged'), ('before_replace', None))
        for point, name in cases:
            with self.subTest(point=point, name=name):
                f = self.fixture()
                old, module = file_state(f.output), file_state(f.dependency)
                fired = []

                def fail(where, path):
                    if not fired and where == point and (name is None or path == name):
                        fired.append(where)
                        raise OSError('owned graph stage failure')

                with f.context(committed=False) as fix, f.resources():
                    with self.session(f, fix, fault=fail) as pub:
                        fix.apply(fix.validate())
                        with self.assertRaises(store.PublicationError) as error:
                            self.publish(pub, fix)
                self.assertEqual(fired, [point])
                self.assert_compile_zero_failure(error.exception.report)
                if point == 'event_directory_fsync':
                    # The staged event is visible but unacknowledged. A later
                    # abort must not overwrite that event or claim a durable end.
                    self.assertTrue(error.exception.report.recovery_required)
                else:
                    self.assertEqual(error.exception.report.output_disposition, 'untouched')
                self.assertEqual(file_state(f.output), old)
                self.assertEqual(file_state(f.dependency), module)
                self.assert_committed(f)

    def test_before_rename_output_drift_preserves_new_bytes_inode_or_metadata(self):
        for point in ('after_compiled', 'after_staged', 'before_replace'):
            for kind in ('bytes', 'inode', 'mode'):
                with self.subTest(point=point, kind=kind):
                    f = self.fixture()
                    saved = []

                    def mutate(where, path):
                        if where != point or saved:
                            return
                        if kind == 'bytes':
                            f.output.write_bytes(b'later user output before rename\n')
                        elif kind == 'inode':
                            other = f.output.with_suffix('.user-save')
                            other.write_bytes(f.output.read_bytes())
                            other.chmod(stat.S_IMODE(f.output.stat().st_mode))
                            os.replace(other, f.output)
                        else:
                            f.output.chmod(0o600)
                        saved.append(file_state(f.output))

                    with f.context(committed=False) as fix, f.resources():
                        with self.session(f, fix, fault=mutate) as pub:
                            fix.apply(fix.validate())
                            with self.assertRaises(store.PublicationError) as error:
                                self.publish(pub, fix)
                    self.assert_compile_zero_failure(error.exception.report)
                    self.assertTrue(error.exception.report.recovery_required)
                    self.assertEqual(file_state(f.output), saved[0])
                    self.assert_committed(f)

    def test_dependency_byte_inode_metadata_and_ancestor_drift_refuse_at_every_guard(self):
        for point in ('after_compiled', 'after_staged', 'before_replace', 'before_published_event'):
            for kind in ('bytes', 'inode', 'mode', 'ancestor'):
                with self.subTest(point=point, kind=kind):
                    f = self.fixture()
                    old = file_state(f.output)
                    saved = []

                    def mutate(where, path):
                        if where != point or saved:
                            return
                        if kind == 'bytes':
                            f.dependency.write_bytes(b'#let count = 8\n')
                        elif kind == 'inode':
                            other = f.dependency.with_suffix('.user-save')
                            other.write_bytes(f.dependency.read_bytes())
                            other.chmod(stat.S_IMODE(f.dependency.stat().st_mode))
                            os.replace(other, f.dependency)
                        elif kind == 'mode':
                            f.dependency.chmod(0o600)
                        else:
                            parent = f.dependency.parent
                            retained = parent.with_name(parent.name + ' user moved')
                            parent.rename(retained)
                            parent.mkdir()
                            for child in retained.iterdir():
                                replacement = parent / child.name
                                replacement.write_bytes(child.read_bytes())
                                replacement.chmod(stat.S_IMODE(child.stat().st_mode))
                        saved.append((file_state(f.source), file_state(f.dependency)))

                    with f.context(committed=False) as fix, f.resources():
                        with self.session(f, fix, fault=mutate) as pub:
                            fix.apply(fix.validate())
                            with self.assertRaises(store.PublicationError) as error:
                                self.publish(pub, fix)
                    self.assert_compile_zero_failure(error.exception.report)
                    self.assertEqual((file_state(f.source), file_state(f.dependency)), saved[0])
                    if point == 'before_published_event':
                        self.assertEqual(error.exception.report.output_disposition, 'restored')
                        self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
                    else:
                        self.assertEqual(file_state(f.output), old)
                    self.assert_committed(f)

    def test_dependency_mutation_by_running_compiler_preserves_later_module_and_old_output(self):
        for kind in ('bytes', 'inode', 'mode'):
            with self.subTest(kind=kind):
                f = self.fixture()
                old = file_state(f.output)
                path = str(f.dependency)
                if kind == 'bytes':
                    behavior = f'pathlib.Path({path!r}).write_bytes(b"#let count=99\\n")'
                elif kind == 'inode':
                    behavior = (f'p=pathlib.Path({path!r}); q=p.with_suffix(".compiler-user-save"); '
                        'q.write_bytes(p.read_bytes()); q.chmod(p.stat().st_mode & 0o777); os.replace(q,p)')
                else:
                    behavior = f'pathlib.Path({path!r}).chmod(0o600)'
                f.program(behavior)
                with f.context(committed=False) as fix, f.resources():
                    with self.session(f, fix) as pub:
                        fix.apply(fix.validate())
                        with self.assertRaises(store.PublicationError) as error:
                            self.publish(pub, fix)
                        saved = file_state(f.dependency)
                self.assert_compile_zero_failure(error.exception.report)
                self.assertEqual(file_state(f.output), old)
                self.assertEqual(file_state(f.dependency), saved)
                self.assert_committed(f)

    def test_later_source_and_module_edits_survive_output_only_postrename_restore(self):
        f = self.fixture()
        saved = []

        def mutate(point, path):
            if point == 'after_replaced':
                f.source.write_bytes(b'user changed root after compilation\n')
                f.dependency.write_bytes(b'#let count = 101\n')
                saved.extend((file_state(f.source), file_state(f.dependency)))
                raise OSError('owned graph post-rename failure')

        with f.context(committed=False) as fix, f.resources():
            with self.session(f, fix, fault=mutate) as pub:
                fix.apply(fix.validate())
                with self.assertRaises(store.PublicationError) as error:
                    self.publish(pub, fix)
        self.assert_compile_zero_failure(error.exception.report)
        self.assertEqual(error.exception.report.output_disposition, 'restored')
        self.assertFalse(error.exception.report.recovery_required)
        self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
        self.assertEqual((file_state(f.source), file_state(f.dependency)), tuple(saved))
        self.assert_committed(f, current_source=False)

    def test_observed_rename_faults_restore_output_and_leave_all_graph_evidence_committed(self):
        for present, mode in ((True, 0o600), (True, 0o644), (False, 0o600)):
            for point in ('after_replace_before_event', 'destination_directory_fsync',
                          'moved_stage_directory_fsync', 'before_published_event'):
                with self.subTest(present=present, mode=mode, point=point):
                    f = self.fixture(present=present, mode=mode)
                    module = file_state(f.dependency)
                    fired = []

                    def fail(where, path):
                        if where == point and not fired:
                            self.assertEqual(f.output.read_bytes(), pdf_bytes())
                            fired.append(where)
                            raise OSError('owned graph observed-rename failure')

                    with f.context(committed=False) as fix, f.resources():
                        with self.session(f, fix, fault=fail) as pub:
                            fix.apply(fix.validate())
                            with self.assertRaises(store.PublicationError) as error:
                                self.publish(pub, fix)
                    self.assertEqual(fired, [point])
                    self.assert_compile_zero_failure(error.exception.report)
                    self.assertEqual(error.exception.report.output_disposition, 'restored')
                    self.assertFalse(error.exception.report.recovery_required)
                    if present:
                        self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
                        self.assertEqual(stat.S_IMODE(f.output.stat().st_mode), mode)
                    else:
                        self.assertFalse(f.output.exists())
                    self.assertEqual(file_state(f.dependency), module)
                    self.assert_committed(f)

    def test_syscall_success_then_error_or_postimage_observation_failure_remains_unresolved(self):
        for kind in ('rename-refusal', 'rename-error', 'postimage-read'):
            with self.subTest(kind=kind):
                f = self.fixture()
                real_replace, real_output_now = os.replace, store._Store.output_now
                replaced, fired = [], []
                old = file_state(f.output)

                def replace(source, destination, *args, **kwargs):
                    if source == 'publish.bin' and kind == 'rename-refusal':
                        fired.append(True)
                        raise OSError('owned graph rename failed without mutation')
                    result = real_replace(source, destination, *args, **kwargs)
                    if source == 'publish.bin':
                        replaced.append(True)
                        if kind == 'rename-error':
                            fired.append(True)
                            raise OSError('owned graph rename returned uncertain result')
                    return result

                def output_now(session, **kwargs):
                    if kind == 'postimage-read' and replaced and not fired:
                        fired.append(True)
                        raise OSError('owned graph immediate postimage read failure')
                    return real_output_now(session, **kwargs)

                with f.context(committed=False) as fix, f.resources():
                    with self.session(f, fix) as pub:
                        fix.apply(fix.validate())
                        with mock.patch.object(store.os, 'replace', side_effect=replace), \
                                mock.patch.object(store._Store, 'output_now', output_now):
                            with self.assertRaises(store.PublicationError) as error:
                                self.publish(pub, fix)
                self.assertEqual(fired, [True])
                self.assert_compile_zero_failure(error.exception.report)
                if kind == 'rename-refusal':
                    self.assertEqual(error.exception.report.output_disposition, 'untouched')
                    self.assertFalse(error.exception.report.recovery_required)
                    self.assertEqual(file_state(f.output), old)
                    self.assert_committed(f)
                    continue
                self.assertTrue(error.exception.report.recovery_required)
                self.assertEqual(f.output.read_bytes(), pdf_bytes())
                saved = file_state(f.output)
                recovered = self.worker(f, 'recover', journal_id=error.exception.report.journal_id)
                self.assertNotIn('exception', recovered, recovered)
                self.assertTrue(recovered['report']['recovery_required'], recovered)
                self.assertEqual(file_state(f.output), saved)
                self.assert_committed(f)

    def test_selected_restoration_faults_keep_pending_graph_journal_and_truthful_compile_zero(self):
        cases = (('before_readback', 'restore.bin'), ('file_fsync', 'restore.bin'),
                 ('before_restore', None), ('restoration_directory_fsync', None))
        for point, name in cases:
            with self.subTest(point=point, name=name):
                f = self.fixture()
                fired = []

                def fail(where, path):
                    if where == 'after_replaced':
                        raise OSError('owned graph rollback trigger')
                    if not fired and where == point and (name is None or path == name):
                        fired.append(where)
                        raise OSError('owned graph restoration failure')

                with f.context(committed=False) as fix, f.resources():
                    with self.session(f, fix, fault=fail) as pub:
                        fix.apply(fix.validate())
                        with self.assertRaises(store.PublicationError) as error:
                            self.publish(pub, fix)
                self.assertEqual(fired, [point])
                self.assert_compile_zero_failure(error.exception.report)
                self.assertTrue(error.exception.report.recovery_required)
                self.assertEqual(len(self.pending(f)), 1)
                self.assert_committed(f)

    def test_later_output_edit_after_observed_rename_is_never_restored_over(self):
        f = self.fixture()
        saved = []

        def mutate(point, path):
            if point == 'after_replaced':
                f.output.write_bytes(b'later exporter owns this output\n')
                saved.append(file_state(f.output))
                raise OSError('owned postrename user edit')

        with f.context(committed=False) as fix, f.resources():
            with self.session(f, fix, fault=mutate) as pub:
                fix.apply(fix.validate())
                with self.assertRaises(store.PublicationError) as error:
                    self.publish(pub, fix)
        self.assert_compile_zero_failure(error.exception.report)
        self.assertTrue(error.exception.report.recovery_required)
        self.assertEqual(file_state(f.output), saved[0])
        retry = self.worker(f, 'recover', journal_id=error.exception.report.journal_id)
        self.assertNotIn('exception', retry, retry)
        self.assertTrue(retry['report']['recovery_required'], retry)
        self.assertEqual(file_state(f.output), saved[0])
        self.assert_committed(f)

    def test_uncertain_visible_terminal_needs_strengthening_before_new_graph_validation(self):
        for failure in ('writer_terminal_file_fsync', 'writer_terminal_directory_fsync'):
            with self.subTest(failure=failure):
                f = self.fixture()

                def uncertain(point, path):
                    if point == 'event_directory_fsync' and path == 'published':
                        raise OSError('owned graph uncertain final event')

                with f.context(committed=False) as fix, f.resources():
                    with self.session(f, fix, fault=uncertain) as pub:
                        fix.apply(fix.validate())
                        with self.assertRaises(store.PublicationError) as error:
                            self.publish(pub, fix)
                self.assert_compile_zero_failure(error.exception.report)
                self.assertTrue(error.exception.report.recovery_required)
                self.assertEqual(error.exception.report.phase, 'publication_commit_uncertain')
                # Historical completion does not authorize reading current graph
                # files, output, or native authority to manufacture old evidence.
                f.output.write_bytes(b'later output survives historical strengthening\n')
                saved = file_state(f.source), file_state(f.dependency), file_state(f.output)
                entries = store.inventory_publications(f.root)
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0].state, 'published')
                fired = []

                def fail(point, path):
                    if point == failure:
                        fired.append(point)
                        raise OSError('owned graph terminal strengthening failure')

                with f.context(committed=False) as fix, f.resources(), \
                        mock.patch.object(type(fix), 'validate', side_effect=AssertionError('validated before strengthen')), \
                        mock.patch.object(runtime, '_run', side_effect=AssertionError('strengthening compiled')):
                    with self.assertRaises((TransactionError, OSError)):
                        with self.session(f, fix, fault=fail):
                            self.fail('failed strengthening admitted writer')
                self.assertEqual(fired, [failure])
                self.assertEqual((file_state(f.source), file_state(f.dependency), file_state(f.output)), saved)
                self.assertEqual(len(store.inventory_publications(f.root)), 1)
                result = self.worker(f, 'recover', journal_id=entries[0].journal_id)
                self.assertNotIn('exception', result, result)
                self.assertEqual(result['report']['phase'], 'published', result)
                self.assertFalse(result['report']['recovery_required'], result)
                self.assertIsNone(result['report']['observed_sha256'], result)
                self.assertEqual(file_state(f.output), saved[2])
                self.assert_committed(f)

    def test_callback_failure_after_durable_published_event_never_rolls_back(self):
        f = self.fixture()
        saved = []

        def fail(point, path):
            if point == 'after_published':
                saved.append(file_state(f.output))
                raise OSError('owned graph post-publication callback failure')

        with f.context(committed=False) as fix, f.resources():
            with self.session(f, fix, fault=fail) as pub:
                fix.apply(fix.validate())
                with self.assertRaises(OSError):
                    self.publish(pub, fix)
                self.assertEqual(pub.report.phase, 'published')
                self.assertEqual(pub.report.gate_status, 0)
                self.assertFalse(pub.report.recovery_required)
        self.assertEqual(file_state(f.output), saved[0])
        self.assertEqual(self.pending(f), ())
        self.assert_committed(f)


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX process-crash protocol')
class CapturedRelativePublishRestartTests(_GraphPublicationFixture):
    def test_sigkill_prepared_blocks_new_graph_attempt_before_source_or_compile(self):
        for present in (True, False):
            with self.subTest(present=present):
                f = self.fixture(present=present)
                original = file_state(f.source), file_state(f.dependency)
                self.kill_worker(f, 'publish', 'after_prepared')
                journal = self.pending_journal(f)
                self.assertEqual(self.worker(f, 'lock-probe'), {'lock_reacquired': True})
                self.assertEqual((file_state(f.source), file_state(f.dependency)), original)
                self.assertFalse(f.marker.exists())
                result = self.worker(f, 'publish')
                self.assertEqual(result.get('exception'), 'PublicationPending', result)
                self.assertFalse(f.marker.exists())
                self.assertEqual((file_state(f.source), file_state(f.dependency)), original)
                self.assert_recovered(f, journal, present=present)

    def test_pending_or_corrupt_source_journals_block_graph_publication_without_output_journal(self):
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt):
                f = self.fixture()
                self.kill_worker(f, 'source-apply', 'after_replace', source=True)
                if corrupt:
                    manifest = next(f.store.glob('root-*/*/manifest.json'))
                    manifest.write_bytes(b'not a source journal\xff')
                source, module, output = file_state(f.source), file_state(f.dependency), file_state(f.output)
                result = self.worker(f, 'publish')
                self.assertIn(result.get('exception'), ('RecoveryRequired', 'TransactionError'), result)
                self.assertEqual((file_state(f.source), file_state(f.dependency), file_state(f.output)),
                                 (source, module, output))
                self.assertFalse(f.marker.exists())
                self.assertEqual(self.worker(f, 'inventory'), {'entries': []})

    def test_corrupt_pdf_journal_blocks_before_real_graph_validation_or_compiler(self):
        f = self.fixture()
        original = file_state(f.source), file_state(f.dependency), file_state(f.output)
        self.kill_worker(f, 'publish', 'after_prepared')
        journal = self.pending_journal(f)
        manifest = next((f.base / store.STORE_NAME).glob('root-*/*/manifest.json'))
        self.assertEqual(manifest.parent.name, journal)
        manifest.write_bytes(b'{"schema": 99}')
        result = self.worker(f, 'publish')
        self.assertEqual(result.get('exception'), 'PublicationPending', result)
        self.assertEqual((file_state(f.source), file_state(f.dependency), file_state(f.output)), original)
        self.assertFalse(f.marker.exists())
        inventory = self.worker(f, 'inventory')
        self.assertEqual(inventory['entries'][0]['state'], 'corrupt', inventory)
        self.assertTrue(inventory['entries'][0]['pending'], inventory)

    def test_sigkill_before_rename_and_observed_rename_recover_only_output(self):
        for present in (True, False):
            for boundary in ('before_replace', 'after_replaced', 'before_published_event'):
                with self.subTest(present=present, boundary=boundary):
                    f = self.fixture(present=present)
                    module = file_state(f.dependency)
                    self.kill_worker(f, 'publish', boundary)
                    self.assert_committed(f)
                    journal = self.pending_journal(f)
                    self.assertEqual(self.worker(f, 'lock-probe'), {'lock_reacquired': True})
                    report = self.assert_recovered(f, journal, present=present)['report']
                    self.assertIs(report['compiler_invoked'], True, report)
                    self.assertEqual(report['compiler_status'], 0, report)
                    self.assertEqual(report['attempted_invocations'], [], report)
                    self.assertIsNone(report['compile_report'], report)
                    self.assertEqual(file_state(f.dependency), module)
                    self.assert_committed(f)

    def test_sigkill_unobserved_postrename_keeps_ctime_ambiguity_unresolved(self):
        f = self.fixture()
        self.kill_worker(f, 'publish', 'after_replace_before_event')
        self.assertEqual(f.output.read_bytes(), pdf_bytes())
        saved = file_state(f.output)
        journal = self.pending_journal(f)
        result = self.worker(f, 'recover', journal_id=journal)
        self.assertNotIn('exception', result, result)
        self.assertTrue(result['report']['recovery_required'], result)
        self.assertNotEqual(result['report']['gate_status'], 0, result)
        self.assertEqual(file_state(f.output), saved)
        self.assertEqual(self.pending_journal(f), journal)
        self.assert_committed(f)

    def test_sigkill_during_live_and_restart_restoration_is_safe_or_truthfully_ambiguous(self):
        for live in (False, True):
            for present in (True, False):
                for boundary in ('before_restore', 'after_restore_before_event', 'after_restored'):
                    with self.subTest(live=live, present=present, boundary=boundary):
                        f = self.fixture(present=present)
                        if live:
                            self.kill_worker(f, 'publish', boundary, fail_point='after_replaced')
                            entries = self.worker(f, 'inventory')['entries']
                            self.assertEqual(len(entries), 1)
                            journal = entries[0]['journal_id']
                        else:
                            self.kill_worker(f, 'publish', 'after_replaced')
                            journal = self.pending_journal(f)
                            self.kill_worker(f, 'recover', 'recovery_' + boundary, journal_id=journal)
                        if boundary == 'after_restore_before_event' and present:
                            saved = file_state(f.output)
                            result = self.worker(f, 'recover', journal_id=journal)
                            self.assertNotIn('exception', result, result)
                            self.assertTrue(result['report']['recovery_required'], result)
                            self.assertEqual(file_state(f.output), saved)
                        else:
                            self.assert_recovered(f, journal, present=present)
                        self.assert_committed(f)

    def test_sigkill_after_durable_published_event_is_historical_not_a_rollback_target(self):
        f = self.fixture()
        self.kill_worker(f, 'publish', 'after_published')
        inventory = self.worker(f, 'inventory')
        self.assertEqual(len(inventory['entries']), 1, inventory)
        entry = inventory['entries'][0]
        self.assertEqual(entry['state'], 'published', entry)
        self.assertFalse(entry['pending'], entry)
        f.output.write_bytes(b'later exporter owns this file after durable commit\n')
        saved = file_state(f.output)
        result = self.worker(f, 'recover', journal_id=entry['journal_id'])
        self.assertNotIn('exception', result, result)
        self.assertEqual(result['report']['phase'], 'published', result)
        self.assertFalse(result['report']['recovery_required'], result)
        self.assertIsNone(result['report']['observed_sha256'], result)
        self.assertEqual(file_state(f.output), saved)
        self.assert_committed(f)

    def test_mixed_legacy_and_graph_schema1_history_recovers_without_current_source_authority(self):
        f = self.fixture()
        # Produce a genuine older publisher record in this same root/namespace.
        legacy_target = 'legacy standalone.typ'
        legacy_source = f.put(legacy_target, b'Legacy $x_i$\n')
        f.config.write_bytes(('rule-set="core"\ninclude=[' + json.dumps(legacy_target) + ']\n').encode())
        f.program(dependency_paths=(legacy_target,))
        # Distinct valid old PDF bytes make restoration observable, even though
        # both publishers otherwise use the same tiny synthetic executable.
        raw = f.compiler.read_bytes().replace(b'%PDF-1.7', b'%PDF-1.6')
        f.compiler.chmod(0o700)
        f.compiler.write_bytes(raw)
        f.compiler.chmod(0o500)
        legacy_request = self.request(f, 'legacy-publish')
        legacy_request['target'] = legacy_target
        result = subprocess.run([sys.executable, str(WORKER), json.dumps(legacy_request)],
            capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stderr)
        legacy = json.loads(result.stdout)
        self.assertNotIn('exception', legacy, legacy)
        self.assertEqual(legacy['report']['phase'], 'published', legacy)
        legacy_journal = legacy['report']['journal_id']
        baseline = f.output.read_bytes()
        self.assertNotEqual(baseline, pdf_bytes())
        f.config.write_bytes(('rule-set="core"\ninclude=[' + json.dumps(f.target) + ']\n').encode())
        f.program()
        self.kill_worker(f, 'publish', 'after_replaced')
        graph_journal = self.pending_journal(f)
        manifests = list((f.base / store.STORE_NAME).glob('root-*/*/manifest.json'))
        self.assertEqual(len(manifests), 2)
        for path in manifests:
            data = json.loads(path.read_bytes())
            self.assertEqual((data['schema'], data['kind']), (1, 'typst-pdf-publication'))
        # Restart recovery must not infer authority from the current graph,
        # source/profile registry, compiler, font catalog, or current configuration.
        f.source.write_bytes(b'later invalid root #read("not allowed")\n')
        f.dependency.unlink()
        legacy_source.unlink()
        f.config.write_bytes(b'no longer TOML\xff')
        f.catalog.unlink()
        f.compiler.chmod(0o700)
        f.compiler.unlink()
        f.font.unlink()
        saved = file_state(f.source), file_state(f.config)
        recovered = self.worker(f, 'recover', journal_id=graph_journal)
        self.assertNotIn('exception', recovered, recovered)
        self.assertFalse(recovered['report']['recovery_required'], recovered)
        self.assertEqual(f.output.read_bytes(), baseline)
        output = file_state(f.output)
        historical = self.worker(f, 'recover', journal_id=legacy_journal)
        self.assertNotIn('exception', historical, historical)
        self.assertEqual(historical['report']['phase'], 'published', historical)
        self.assertFalse(historical['report']['recovery_required'], historical)
        self.assertIsNone(historical['report']['observed_sha256'], historical)
        self.assertEqual(file_state(f.output), output)
        self.assertEqual((file_state(f.source), file_state(f.config)), saved)
        for path in (f.dependency, legacy_source, f.catalog, f.compiler, f.font):
            self.assertFalse(path.exists(), path)
        self.assertEqual(self.pending(f), ())


if __name__ == '__main__':
    unittest.main()
