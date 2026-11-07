"""Independent authority/composition checks for internal graph PDF publication.

All graphs, outputs, journals, executables and arbitrary font bytes are owned
temporary fixtures. Runtime seams select those resources; graph artifacts still
come from the real isolated compiler and its issued live bridge authority.
Crash/recovery storage matrices live in the separate integration tests.
"""
from contextlib import contextmanager, nullcontext
import copy
from dataclasses import FrozenInstanceError, asdict, fields, replace
import gc
import json
from pathlib import Path
import signal
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock
import weakref

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import captured_relative_compile as compiler
import captured_relative_core as core
import captured_relative_fix as bridge
import captured_relative_publish as publication
import closed_core_compile as runtime
import pdf_publication_store as store
from resource_profiles import ASCII_RESOURCE_PROFILE as ASCII, APPLEGOTHIC_RESOURCE_PROFILE as KOREAN
from syntax_client import EXECUTABLE
from transactions import TransactionError
from graph_compile_test_support import GraphFixture, clone_opaque, pdf_bytes

OLD_OUTPUT = b'Owned preexisting arbitrary output.\x00\xff\r\n'


@contextmanager
def changed(value, name, replacement):
    previous = getattr(value, name)
    object.__setattr__(value, name, replacement)
    try:
        yield
    finally:
        object.__setattr__(value, name, previous)


def file_state(path):
    info = path.lstat()
    return (path.read_bytes(), info.st_dev, info.st_ino, info.st_mode,
            info.st_uid, info.st_gid, info.st_mtime_ns, info.st_ctime_ns)


def journal_events(pub):
    path = pub._store.namespace / pub._store.journal_id
    return tuple(json.loads(p.read_bytes()) for p in sorted(path.glob('event-*.json')))


class _PublicationFixtureMethods:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='captured-graph-publish-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.serial = 0

    def fixture(self, *, present=True, **kwargs):
        self.serial += 1
        f = GraphFixture(self.base / str(self.serial), **kwargs)
        f.destination = 'build with spaces/nested/owned document.pdf'
        f.output = f.root / f.destination
        f.output.parent.mkdir(parents=True)
        if present:
            f.output.write_bytes(OLD_OUTPUT)
            f.output.chmod(0o644)
        f.output_lease = store.OfflineOutputExclusive.assert_exporters_watchers_closed_and_no_other_writers()
        return f

    def session(self, fix, f, **kwargs):
        kwargs.setdefault('destination', f.destination)
        kwargs.setdefault('lease', f.output_lease)
        return publication.PreparedCapturedRelativePdfPublication(fix, **kwargs)

    def publish(self, pub, fix, **kwargs):
        kwargs.setdefault('profile', ASCII)
        return pub.compile_and_publish(fix.committed_graph_snapshot(), **kwargs)

    def assert_committed_source(self, f):
        self.assertEqual((f.root / f.target).read_bytes(),
                         f.sources[f.target].replace(b'$x_count$', b'$x_(count)$'))
        manifests = list(f.store.glob('root-*/*/manifest.json'))
        self.assertEqual(len(manifests), 1)
        events = [json.loads(p.read_bytes())['kind']
                  for p in sorted(manifests[0].parent.glob('event-*.json'))]
        self.assertEqual(events[-1], 'committed')
        self.assertNotIn('recovery_required', events)

    def assert_no_compilation(self, report):
        self.assertIs(report.compiler_invoked, False)
        self.assertIsNone(report.compiler_status)
        self.assertEqual(report.attempted_invocations, ())

    def assert_compile_facts(self, report, status=0):
        self.assertIs(report.compiler_invoked, True)
        self.assertEqual(report.compiler_status, status)
        self.assertEqual(tuple(a.purpose for a in report.attempted_invocations),
                         ('version-probe', 'compile'))
        self.assertEqual(report.attempted_invocations[-1].returncode, status)
        self.assertIsNotNone(report.resource_report)
        self.assertEqual(report.compile_report.attempts, report.attempted_invocations)


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX owned compiler fixtures')
class CapturedRelativePublicationBoundaryTests(_PublicationFixtureMethods, unittest.TestCase):
    def test_existing_and_absent_output_single_compile_full_graph_and_resource_journal(self):
        for present, profile in ((True, ASCII), (False, ASCII), (True, KOREAN)):
            with self.subTest(present=present, profile=profile):
                f = self.fixture(present=present)
                untouched = file_state(f.unrelated)
                before = {p: file_state(f.root / p) for p in f.sources if p != f.target}
                run = runtime._run
                seen = []
                def observe(argv, **kwargs):
                    seen.append(kwargs['purpose'])
                    return run(argv, **kwargs)
                with f.context(committed=False) as fix, f.resources(korean=profile == KOREAN), \
                        mock.patch.object(runtime, '_run', side_effect=observe):
                    with self.session(fix, f) as pub:
                        fix.apply(fix.validate())
                        report = self.publish(pub, fix, profile=profile)
                        events = journal_events(pub)
                        intent = next(e for e in events if e['state'] == 'compile_launch_intent')
                        self.assertEqual(intent['profile'], profile)
                        self.assertNotEqual(intent['profile'], core.SOURCE_PROFILE)
                        self.assertEqual(intent['source_sha256'],
                                         runtime._hash((f.root / f.target).read_bytes()))
                        self.assertEqual(intent['source_journal_id'], fix.summary.journal_id)
                        self.assertEqual(intent['config_sha256'], runtime._hash(f.config.read_bytes()))
                        self.assertEqual(intent['catalog_sha256'], runtime._hash(f.catalog.read_bytes()))
                        self.assertEqual(report.source_journal_id, fix.summary.journal_id)
                        self.assertEqual(report.phase, 'published')
                        self.assertEqual(report.gate_status, 0)
                        self.assertEqual(report.source_commit, 'committed')
                        self.assertEqual(report.output_disposition, 'published')
                        self.assertFalse(report.recovery_required)
                        self.assert_compile_facts(report)
                        with self.assertRaises((FrozenInstanceError, AttributeError)):
                            report.phase = 'forged'
                        with self.assertRaises(TransactionError):
                            self.publish(pub, fix)
                self.assertEqual(seen, ['version-probe', 'compile'])
                self.assertEqual(f.output.read_bytes(), pdf_bytes())
                self.assertEqual(f.output.stat().st_mode & 0o777, 0o644 if present else 0o600)
                self.assertEqual(file_state(f.unrelated), untouched)
                for path, state in before.items():
                    self.assertEqual(file_state(f.root / path), state)
                self.assert_committed_source(f)
                self.assertFalse(any(e.pending for e in store.inventory_publications(f.root)))

    def test_output_lease_is_independent_and_requires_exact_issued_type(self):
        f = self.fixture()
        before = file_state(f.root / f.target), file_state(f.output)
        class LeaseSubclass(store.OfflineOutputExclusive):
            pass
        subclass = object.__new__(LeaseSubclass)
        object.__setattr__(subclass, '_seal', f.output_lease._seal)
        bare = object.__new__(store.OfflineOutputExclusive)
        with f.context(committed=False) as fix:
            for lease in (None, object(), f.lease, bare, subclass):
                with self.subTest(lease=type(lease)), self.assertRaises(TransactionError):
                    with self.session(fix, f, lease=lease):
                        self.fail('foreign output lease admitted')
        self.assertEqual((file_state(f.root / f.target), file_state(f.output)), before)
        self.assertFalse((f.root.parent / store.STORE_NAME).exists())

    def test_only_original_live_exact_bridge_can_prepare(self):
        f = self.fixture()
        class FixSubclass(bridge.ProtectedCapturedRelativeFix):
            pass
        with f.context(committed=False) as fix:
            subclass = object.__new__(FixSubclass)
            subclass.__dict__.update(fix.__dict__)
            variants = (object(), object.__new__(bridge.ProtectedCapturedRelativeFix),
                        fix._tx, fix._capture, copy.copy(fix), subclass)
            for owner in variants:
                with self.subTest(owner=type(owner)), self.assertRaises(TransactionError):
                    with self.session(owner, f):
                        self.fail('unissued source owner admitted')
        with self.assertRaises(TransactionError):
            with self.session(fix, f):
                self.fail('expired source owner admitted')
        self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)

    def test_preparation_borrows_original_transaction_without_second_flock(self):
        f = self.fixture()
        with f.context(committed=False) as fix, f.resources():
            tx = fix._tx
            with mock.patch.object(store.fcntl, 'flock',
                    side_effect=AssertionError('publication attempted a second root flock')):
                with self.session(fix, f) as pub:
                    self.assertIs(pub._store.transaction, tx)
                    fix.apply(fix.validate())
                    self.publish(pub, fix)
            # Publication close did not unlock or close the borrowed source owner.
            fix.assert_committed_graph_current(fix.committed_graph_snapshot())
            with self.assertRaises(store.PublicationError):
                store.inventory_publications(f.root)
        self.assertEqual(f.output.read_bytes(), pdf_bytes())

    def test_preparation_precedes_successful_or_failed_validation_and_apply_attempts(self):
        for when in ('validate', 'apply', 'snapshot', 'failed-validate', 'failed-apply'):
            with self.subTest(when=when):
                f = self.fixture()
                old = file_state(f.output)
                with f.context(committed=False) as fix:
                    if when == 'failed-validate':
                        with mock.patch.object(fix._session, 'assert_current',
                                side_effect=bridge.CapturedRelativeFixError('owned early validation refusal')):
                            with self.assertRaises(TransactionError):
                                fix.validate()
                    elif when == 'failed-apply':
                        with self.assertRaises(TransactionError):
                            fix.apply(object())
                    else:
                        candidate = fix.validate()
                        if when != 'validate':
                            fix.apply(candidate)
                        if when == 'snapshot':
                            fix.committed_graph_snapshot()
                    with self.assertRaises(TransactionError):
                        with self.session(fix, f):
                            self.fail('late output baseline admitted')
                self.assertEqual(file_state(f.output), old)
                self.assertFalse((f.root.parent / store.STORE_NAME).exists())

    def test_preparation_callbacks_cannot_validate_then_supply_a_late_baseline(self):
        for point, action in ((point, action)
                              for point in ('namespace_fsync', 'after_prepared')
                              for action in ('validate', 'failed-validate', 'failed-apply')):
            with self.subTest(point=point, action=action):
                f = self.fixture()
                original = file_state(f.root / f.target)
                fired = []
                def validate_during_prepare(where, path):
                    if where == point and not fired:
                        fired.append(where)
                        if action == 'validate':
                            fix.validate()
                        elif action == 'failed-apply':
                            with self.assertRaises(TransactionError):
                                fix.apply(object())
                        else:
                            with mock.patch.object(fix._session, 'assert_current',
                                    side_effect=bridge.CapturedRelativeFixError('owned early refusal')):
                                with self.assertRaises(TransactionError):
                                    fix.validate()
                with f.context(committed=False) as fix, mock.patch.object(runtime, '_run',
                        side_effect=AssertionError('preparation launched a compiler')):
                    with self.assertRaises(TransactionError):
                        with self.session(fix, f, fault=validate_during_prepare):
                            self.fail('preparation callback reordered source validation')
                self.assertEqual(fired, [point])
                self.assertEqual(file_state(f.root / f.target), original)
                self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)

    def test_preparation_is_resource_neutral_and_captures_pristine_source_baseline(self):
        f = self.fixture()
        original = file_state(f.root / f.target)
        seen = []
        def observe(point, path):
            if point == 'after_prepared':
                seen.append(point)
                self.assertEqual(file_state(f.root / f.target), original)
                self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
                self.assertIsNone(fix._applied_validation)
        with f.context(committed=False) as fix, f.resources(), \
                mock.patch.object(runtime, '_reviewed_resource_artifact',
                    side_effect=AssertionError('preparation or ASCII consulted a font')):
            with self.session(fix, f, fault=observe) as pub:
                fix.apply(fix.validate())
                self.publish(pub, fix)
        self.assertEqual(seen, ['after_prepared'])

    def test_preparation_protects_all_captured_modules_even_unused_and_transitive(self):
        target = 'nested/main.typ'
        sources = {target: b'#import "../values.typ": count\n#import "../unused.typ": unused\n#count $x_count$\n',
                   'values.typ': b'#import "shared/leaf.typ": value\n#let count = value\n',
                   'shared/leaf.typ': b'#let value = 7\n',
                   'unused.typ': b'#let unused = 99\n'}
        f = self.fixture(target=target, sources=sources)
        prepare = store._Store.prepare
        observed = []
        def observe(instance, *, protected_paths=()):
            observed.append(set(map(Path, protected_paths)))
            return prepare(instance, protected_paths=protected_paths)
        with f.context(committed=False) as fix, f.resources(), \
                mock.patch.object(store._Store, 'prepare', new=observe):
            with self.session(fix, f) as pub:
                fix.apply(fix.validate())
                self.publish(pub, fix)
        self.assertEqual(len(observed), 1)
        expected = {f.root / path for path in sources}
        expected.update((f.config, f.catalog, f.store, f.compiler))
        expected.update(runtime._LAUNCHERS)
        expected.update(record.path for record in runtime._NATIVE_ARTIFACTS.values())
        self.assertTrue(expected <= observed[0], f'missing protected paths: {expected - observed[0]}')
        self.assertNotIn(f.unrelated, observed[0])

    def test_compiler_and_launcher_destination_collisions_refuse_before_source_apply(self):
        for kind in ('compiler', 'launcher', 'catalog'):
            with self.subTest(kind=kind):
                f = self.fixture()
                if kind == 'compiler':
                    f.compiler.chmod(0o700)
                    f.output.write_bytes(f.compiler.read_bytes())
                    # Keep output metadata safe: refusal must come from the
                    # protected compiler path, not an executable output mode.
                    f.output.chmod(0o644)
                    reviewed = runtime._ReviewedNativeArtifact(f.output,
                        runtime._hash(f.output.read_bytes()), f.reviewed.version)
                    patch = mock.patch.object(runtime, '_reviewed_native_artifact', return_value=reviewed)
                elif kind == 'launcher':
                    patch = mock.patch.object(runtime, '_LAUNCHERS', (f.output,))
                else:
                    f.output.write_bytes(f.catalog.read_bytes())
                    f.catalog = f.output
                    patch = mock.patch.object(runtime, '_reviewed_native_artifact', return_value=f.reviewed)
                source = file_state(f.root / f.target)
                output = file_state(f.output)
                with f.context(committed=False) as fix, patch:
                    with self.assertRaises(TransactionError):
                        with self.session(fix, f):
                            self.fail('protected destination admitted')
                self.assertEqual(file_state(f.root / f.target), source)
                self.assertEqual(file_state(f.output), output)

    def test_selected_font_collision_checked_only_at_explicit_profile_selection(self):
        f = self.fixture()
        with f.context(committed=False) as fix, f.resources():
            with mock.patch.object(runtime, 'resource_profile_protected_paths',
                    side_effect=AssertionError('preparation selected a resource profile')):
                pub = self.session(fix, f)
                pub.__enter__()
            try:
                fix.apply(fix.validate())
                with mock.patch.object(runtime, 'resource_profile_protected_paths', return_value=(f.output,)), \
                        mock.patch.object(runtime, '_run', side_effect=AssertionError('font collision compiled')):
                    with self.assertRaises(store.PublicationError) as failed:
                        self.publish(pub, fix, profile=KOREAN)
                self.assert_no_compilation(failed.exception.report)
                self.assertEqual([e['state'] for e in journal_events(pub)],
                                 ['prepared', 'aborted_unpublished'])
            finally:
                pub.__exit__(None, None, None)
        self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
        self.assert_committed_source(f)

    def test_exact_snapshot_bare_copy_subclass_duck_and_foreign_inputs_refuse(self):
        for kind in ('object', 'bare', 'copy', 'deepcopy', 'clone', 'subclass', 'duck', 'foreign'):
            with self.subTest(kind=kind):
                f, alien = self.fixture(), self.fixture()
                with alien.context() as other, f.context(committed=False) as fix, f.resources():
                    with self.session(fix, f) as pub:
                        fix.apply(fix.validate())
                        snapshot = fix.committed_graph_snapshot()
                        if kind == 'subclass':
                            class Subclass(bridge.CapturedRelativeCommittedGraph):
                                pass
                            invalid = object.__new__(Subclass)
                            for item in fields(snapshot):
                                object.__setattr__(invalid, item.name, getattr(snapshot, item.name))
                        else:
                            makers = {'object': object, 'bare': fix._tx.committed_snapshot,
                                'copy': lambda: copy.copy(snapshot), 'deepcopy': lambda: copy.deepcopy(snapshot),
                                'clone': lambda: clone_opaque(snapshot),
                                'duck': lambda: SimpleNamespace(**{i.name: getattr(snapshot, i.name)
                                                                  for i in fields(snapshot)}),
                                'foreign': other.committed_graph_snapshot}
                            if kind == 'deepcopy':
                                # Mapping proxies may reject copying before a fake can be supplied.
                                try:
                                    invalid = makers[kind]()
                                except TypeError:
                                    continue
                            else:
                                invalid = makers[kind]()
                        with mock.patch.object(runtime, '_capture_compiler',
                                side_effect=AssertionError('invalid snapshot captured a compiler')):
                            with self.assertRaises(store.PublicationError) as failed:
                                pub.compile_and_publish(invalid, profile=ASCII)
                        self.assert_no_compilation(failed.exception.report)
                self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
                self.assert_committed_source(f)

    def test_expired_snapshot_or_source_owner_refuses_without_compiler(self):
        f = self.fixture()
        with f.context() as expired:
            snapshot = expired.committed_graph_snapshot()
        other = self.fixture()
        with other.context(committed=False) as fix, other.resources():
            with self.session(fix, other) as pub:
                fix.apply(fix.validate())
                with mock.patch.object(runtime, '_capture_compiler',
                        side_effect=AssertionError('expired graph compiled')):
                    with self.assertRaises(store.PublicationError) as failed:
                        pub.compile_and_publish(snapshot, profile=ASCII)
                self.assert_no_compilation(failed.exception.report)
        self.assertEqual(other.output.read_bytes(), OLD_OUTPUT)

    def test_public_signature_has_no_pdf_artifact_runtime_or_destination_override(self):
        f = self.fixture()
        with f.context(committed=False) as fix, f.resources():
            with self.session(fix, f) as pub:
                fix.apply(fix.validate())
                snapshot = fix.committed_graph_snapshot()
                for kwargs in ({'pdf_bytes': pdf_bytes()}, {'artifact': object()},
                               {'output': 'other.pdf'}, {'compiler': f.compiler},
                               {'environment': {}}, {'extra_args': ('--open',)},
                               {'scalar_render_limit': 1 << 40}, {'approved': True}):
                    with self.subTest(option=tuple(kwargs)), self.assertRaises(TypeError):
                        pub.compile_and_publish(snapshot, profile=ASCII, **kwargs)
                with self.assertRaises(TypeError):
                    pub.compile_and_publish(snapshot)
                report = pub.compile_and_publish(snapshot, profile=ASCII)
        self.assertEqual(report.phase, 'published')
        self.assertEqual(f.output.read_bytes(), pdf_bytes())

    def test_external_real_artifact_is_not_a_committed_graph_argument(self):
        f = self.fixture()
        with f.context(committed=False) as fix, f.resources():
            with self.session(fix, f) as pub:
                fix.apply(fix.validate())
                artifact = compiler.compile_committed_graph(fix, fix.committed_graph_snapshot(), profile=ASCII)
                self.assertEqual(artifact.pdf_bytes, pdf_bytes())
                with mock.patch.object(runtime, '_run', side_effect=AssertionError('external artifact compiled')):
                    with self.assertRaises(store.PublicationError) as failed:
                        pub.compile_and_publish(artifact, profile=ASCII)
                self.assert_no_compilation(failed.exception.report)
        self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
        self.assert_committed_source(f)

    def test_invalid_profile_deadline_and_cancellation_type_refuse_before_resource_capture(self):
        variants = [{'profile': p} for p in ('', None, core.SOURCE_PROFILE, 'ordinary')]
        variants.extend({'deadline_seconds': d} for d in
                        (False, 0, -1, float('nan'), float('inf'), 121, '1'))
        variants.append({'cancel_event': object()})
        for kwargs in variants:
            with self.subTest(kwargs=kwargs):
                f = self.fixture()
                with f.context(committed=False) as fix, f.resources():
                    with self.session(fix, f) as pub:
                        fix.apply(fix.validate())
                        with mock.patch.object(runtime, '_capture_compiler',
                                side_effect=AssertionError('invalid parameters captured resources')):
                            with self.assertRaises(store.PublicationError) as failed:
                                self.publish(pub, fix, **kwargs)
                        self.assert_no_compilation(failed.exception.report)
                        self.assertEqual(failed.exception.report.gate_status, 2)
                        with self.assertRaises(TransactionError):
                            self.publish(pub, fix)
                self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
                self.assert_committed_source(f)

    def test_pre_cancelled_request_does_not_launch_and_consumes_owned_attempt(self):
        f = self.fixture()
        cancelled = threading.Event()
        cancelled.set()
        with f.context(committed=False) as fix, f.resources():
            with self.session(fix, f) as pub:
                fix.apply(fix.validate())
                with mock.patch.object(runtime, '_run', side_effect=AssertionError('cancelled request launched')):
                    with self.assertRaises(store.PublicationError) as failed:
                        self.publish(pub, fix, cancel_event=cancelled)
                self.assert_no_compilation(failed.exception.report)
                with self.assertRaises(TransactionError):
                    self.publish(pub, fix)
        self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
        self.assert_committed_source(f)

    def test_noop_source_apply_reports_committed_with_no_invented_journal(self):
        target = 'nested/main.typ'
        f = self.fixture(target=target, sources={target: b'#import "values.typ": count\n#count $x_(count)$\n',
                                               'nested/values.typ': b'#let count = 7\n'})
        original = file_state(f.root / f.target)
        with f.context(committed=False) as fix, f.resources():
            with self.session(fix, f) as pub:
                applied = fix.apply(fix.validate())
                self.assertEqual(applied.modified, ())
                report = self.publish(pub, fix)
                intent = next(e for e in journal_events(pub) if e['state'] == 'compile_launch_intent')
                self.assertIsNone(intent['source_journal_id'])
        self.assertEqual(file_state(f.root / f.target), original)
        self.assertEqual(report.source_commit, 'committed')
        self.assertIsNone(report.source_journal_id)
        self.assertEqual(list(f.store.glob('root-*/*/manifest.json')), [])
        self.assertEqual(f.output.read_bytes(), pdf_bytes())

    def test_constructor_and_closed_context_cannot_publish_or_be_reused(self):
        f = self.fixture()
        with f.context(committed=False) as fix, f.resources():
            pub = self.session(fix, f)
            with self.assertRaises(TransactionError):
                pub.compile_and_publish(object(), profile=ASCII)
            with pub:
                pass
            self.assert_no_compilation(pub.report)
            self.assertEqual(pub.report.source_commit, 'not_applied')
            with self.assertRaises(TransactionError):
                with pub:
                    self.fail('publication context reused')
        self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)

    def test_publication_copies_and_changed_constructor_bindings_are_not_authority(self):
        f, alien = self.fixture(), self.fixture()
        with alien.context(committed=False) as other, f.context(committed=False) as fix, f.resources():
            pub = self.session(fix, f)
            copied = copy.copy(pub)
            with self.assertRaises(TransactionError):
                with copied:
                    self.fail('copied publisher prepared a baseline')
            for owner, field, value in ((pub, 'fix', other),
                                       (pub, 'destination', 'other.pdf'),
                                       (pub, 'lease', alien.output_lease)):
                with self.subTest(field=field), changed(owner, field, value):
                    with self.assertRaises(TransactionError):
                        with pub:
                            self.fail('changed constructor binding prepared a baseline')
            with pub:
                fix.apply(fix.validate())
                snapshot = fix.committed_graph_snapshot()
                copied = copy.copy(pub)
                with mock.patch.object(runtime, '_run', side_effect=AssertionError('copied publisher compiled')):
                    with self.assertRaises(TransactionError):
                        copied.compile_and_publish(snapshot, profile=ASCII)
                copied.__exit__(None, None, None)
                self.assertTrue(pub._active)
                self.assertTrue(pub._store._active)
                self.assertFalse(pub._attempted)
                report = pub.compile_and_publish(snapshot, profile=ASCII)
            # A copied close and a repeated original close cannot consume or
            # release anyone else's source/output authority.
            events = journal_events(pub)
            pub.__exit__(None, None, None)
            copied.__exit__(None, None, None)
            self.assertIs(pub.report, report)
            self.assertEqual(journal_events(pub), events)
        self.assertEqual(report.phase, 'published')
        self.assertEqual(f.output.read_bytes(), pdf_bytes())
        self.assertEqual(alien.output.read_bytes(), OLD_OUTPUT)

    def test_source_owner_expiry_inside_prepared_context_never_compiles(self):
        f = self.fixture()
        with f.context(committed=False) as fix, f.resources():
            pub = self.session(fix, f)
            pub.__enter__()
            fix.apply(fix.validate())
            snapshot = fix.committed_graph_snapshot()
            journal = fix.summary.journal_id
            # Closing the source owner is not a transfer of its borrowed lock.
            fix.__exit__(None, None, None)
            try:
                with mock.patch.object(runtime, '_run', side_effect=AssertionError('expired owner compiled')):
                    with self.assertRaises(TransactionError):
                        pub.compile_and_publish(snapshot, profile=ASCII)
            finally:
                pub.__exit__(None, None, None)
            self.assert_no_compilation(pub.report)
            self.assertEqual(pub.report.source_commit, 'committed')
            self.assertEqual(pub.report.source_journal_id, journal)
        self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
        self.assert_committed_source(f)

    def test_pristine_baseline_paths_are_literal_and_existing_parent_required(self):
        for destination in ('', '.', '../outside.pdf', '/tmp/outside.pdf',
                            './document.pdf', 'build/../document.pdf', 'double//out.pdf',
                            'out.PDF', 'out.typ', 'out.pdf/', '*.pdf', 'out.pdf\x00',
                            'missing-parent/out.pdf'):
            with self.subTest(destination=destination):
                f = self.fixture()
                source, output = file_state(f.root / f.target), file_state(f.output)
                with f.context(committed=False) as fix:
                    with self.assertRaises((TransactionError, OSError)):
                        with self.session(fix, f, destination=destination):
                            self.fail('unsafe output baseline admitted')
                self.assertEqual(file_state(f.root / f.target), source)
                self.assertEqual(file_state(f.output), output)

    def test_foreign_transaction_cannot_be_borrowed_before_preparation(self):
        f, alien = self.fixture(), self.fixture()
        with alien.context(committed=False) as other, f.context(committed=False) as fix:
            with changed(fix, '_tx', other._tx):
                with self.assertRaises(TransactionError):
                    with self.session(fix, f):
                        self.fail('foreign source transaction admitted')
        self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)

    def test_live_publisher_owner_transaction_or_selection_swaps_refuse_without_compile(self):
        for kind in ('fix', 'bridge-tx', 'session-tx', 'store-tx', 'owner', 'target',
                     'tx-owner', 'tx-root'):
            with self.subTest(kind=kind):
                f, alien = self.fixture(), self.fixture()
                with alien.context(committed=False) as other, f.context(committed=False) as fix, f.resources():
                    with self.session(fix, f) as pub:
                        fix.apply(fix.validate())
                        snapshot = fix.committed_graph_snapshot()
                        expected_journal = fix.summary.journal_id
                        options = {'fix': (pub, 'fix', other), 'bridge-tx': (fix, '_tx', other._tx),
                            'session-tx': (fix._session, '_tx', other._tx),
                            'store-tx': (pub._store, 'transaction', other._tx),
                            'owner': (fix, '_owner', object()), 'target': (fix, 'target', 'other.typ'),
                            'tx-owner': (fix._tx, '_owner', object()),
                            'tx-root': (fix._tx, 'root', alien.root)}
                        with changed(*options[kind]), mock.patch.object(runtime, '_run',
                                side_effect=AssertionError('swapped owner compiled')):
                            with self.assertRaises(TransactionError):
                                pub.compile_and_publish(snapshot, profile=ASCII)
                        # Exit must still describe the original source commit, never an alien owner.
                    self.assertEqual(pub.report.source_commit, 'committed')
                    self.assertEqual(pub.report.source_journal_id, expected_journal)
                self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
                self.assert_committed_source(f)

    def test_mutated_snapshot_and_precompile_source_or_module_drift_never_compile(self):
        for kind in ('snapshot-target', 'snapshot-profile', 'source', 'module', 'config', 'catalog'):
            with self.subTest(kind=kind):
                f = self.fixture()
                with f.context(committed=False) as fix, f.resources():
                    with self.session(fix, f) as pub:
                        fix.apply(fix.validate())
                        snapshot = fix.committed_graph_snapshot()
                        if kind.startswith('snapshot-'):
                            key = 'target' if kind == 'snapshot-target' else 'source_profile'
                            manager = changed(snapshot, key, 'forged')
                        else:
                            paths = {'source': f.root / f.target,
                                'module': f.root / next(p for p in f.sources if p != f.target),
                                'config': f.config, 'catalog': f.catalog}
                            paths[kind].write_bytes(paths[kind].read_bytes() + b'\n')
                            manager = nullcontext()
                        with manager, mock.patch.object(runtime, '_run',
                                side_effect=AssertionError('stale graph compiled')):
                            with self.assertRaises(store.PublicationError) as failed:
                                pub.compile_and_publish(snapshot, profile=ASCII)
                        self.assert_no_compilation(failed.exception.report)
                self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
                # A later user edit is preserved rather than rolled back to either source version.
                if kind == 'source':
                    self.assertTrue((f.root / f.target).read_bytes().endswith(b'\n\n'))
                else:
                    self.assert_committed_source(f)

    def test_foreign_copied_or_mutated_returned_artifacts_never_publish(self):
        for kind in ('object', 'copy', 'clone', 'foreign', 'pdf-mutation'):
            with self.subTest(kind=kind):
                f, alien = self.fixture(), self.fixture()
                compile_graph = compiler.compile_committed_graph
                with alien.context() as other, alien.resources():
                    foreign = compile_graph(other, other.committed_graph_snapshot(), profile=ASCII)
                # The foreign artifact is deliberately expired as well as differently owned.
                with f.context(committed=False) as fix, f.resources():
                    with self.session(fix, f) as pub:
                        fix.apply(fix.validate())
                        def returned(*args, **kwargs):
                            artifact = compile_graph(*args, **kwargs)
                            if kind == 'object':
                                return object()
                            if kind == 'copy':
                                return copy.copy(artifact)
                            if kind == 'clone':
                                return clone_opaque(artifact)
                            if kind == 'foreign':
                                return foreign
                            object.__setattr__(artifact, '_pdf_bytes', b'forged')
                            return artifact
                        with mock.patch.object(compiler, 'compile_committed_graph', side_effect=returned):
                            with self.assertRaises(store.PublicationError):
                                self.publish(pub, fix)
                self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
                self.assert_committed_source(f)

    def test_nonzero_and_signal_statuses_are_raw_and_source_repairs_never_roll_back(self):
        for status, behavior in ((19, 'raise SystemExit(19)'),
                                 (-signal.SIGTERM, 'os.kill(os.getpid(), signal.SIGTERM)')):
            with self.subTest(status=status):
                f = self.fixture()
                f.program(behavior)
                output = file_state(f.output)
                with f.context(committed=False) as fix, f.resources():
                    with self.session(fix, f) as pub:
                        fix.apply(fix.validate())
                        with self.assertRaises(store.PublicationError) as failed:
                            self.publish(pub, fix)
                        report = failed.exception.report
                        self.assertEqual(report.gate_status, 3)
                        self.assert_compile_facts(report, status)
                self.assertEqual(file_state(f.output), output)
                self.assert_committed_source(f)

    def test_late_source_module_or_artifact_guard_refusal_preserves_successful_raw_report(self):
        for kind in ('source', 'module', 'guard'):
            with self.subTest(kind=kind):
                f = self.fixture()
                output = file_state(f.output)
                def late(point, path):
                    if point != 'after_compiled':
                        return
                    if kind == 'guard':
                        raise runtime.CompileSafetyError('owned later empty-report refusal')
                    target = f.root / (f.target if kind == 'source'
                        else next(p for p in f.sources if p != f.target))
                    target.write_bytes(target.read_bytes() + b'\n')
                with f.context(committed=False) as fix, f.resources():
                    with self.session(fix, f, fault=late) as pub:
                        fix.apply(fix.validate())
                        with self.assertRaises(store.PublicationError) as failed:
                            self.publish(pub, fix)
                        report = failed.exception.report
                        self.assertEqual(report.gate_status, 3)
                        self.assert_compile_facts(report)
                        self.assertEqual(report.resource_report.profile, ASCII)
                        self.assertEqual(report.compile_report.safety_gate, 'passed')
                self.assertEqual(file_state(f.output), output)
                if kind == 'source':
                    self.assertTrue((f.root / f.target).read_bytes().endswith(b'\n\n'))
                else:
                    self.assert_committed_source(f)

    def test_late_cancellation_or_deadline_refuses_publication_without_erasing_compile_facts(self):
        for kind in ('cancel', 'deadline'):
            with self.subTest(kind=kind):
                f = self.fixture()
                cancelled = threading.Event()
                patches = []
                def stop_after_compile(point, path):
                    if point != 'after_compiled':
                        return
                    if kind == 'cancel':
                        cancelled.set()
                    else:
                        future = publication.time.monotonic() + 11.0
                        patch = mock.patch.object(publication, 'time',
                            SimpleNamespace(monotonic=lambda: future))
                        patch.start()
                        patches.append(patch)
                try:
                    with f.context(committed=False) as fix, f.resources():
                        with self.session(fix, f, fault=stop_after_compile) as pub:
                            fix.apply(fix.validate())
                            with self.assertRaises(store.PublicationError) as failed:
                                self.publish(pub, fix, cancel_event=cancelled)
                            report = failed.exception.report
                            self.assertEqual(report.gate_status, 3)
                            self.assert_compile_facts(report)
                            self.assertEqual(report.output_disposition, 'untouched')
                            self.assertIn('cancel' if kind == 'cancel' else 'deadline', report.reason.lower())
                finally:
                    for patch in reversed(patches):
                        patch.stop()
                self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
                self.assert_committed_source(f)

    def test_shared_attempt_mutation_does_not_rewrite_observed_process_facts(self):
        for field, replacement in (('returncode', 91), ('stdout', b'forged'),
                                    ('purpose', 'forged'), ('lifecycle', 'uncertain')):
            with self.subTest(field=field):
                f = self.fixture()
                run = runtime._run
                observed = []
                def tamper(argv, **kwargs):
                    result = run(argv, **kwargs)
                    if kwargs['purpose'] == 'compile':
                        observed.append(asdict(result))
                        object.__setattr__(result, field, replacement)
                    return result
                with f.context(committed=False) as fix, f.resources():
                    with self.session(fix, f) as pub:
                        fix.apply(fix.validate())
                        with mock.patch.object(runtime, '_run', side_effect=tamper):
                            with self.assertRaises(store.PublicationError) as failed:
                                self.publish(pub, fix)
                        report = failed.exception.report
                        self.assert_compile_facts(report)
                        self.assertEqual(asdict(report.attempted_invocations[-1]), observed[0])
                        self.assertEqual(report.compile_report.safety_gate, 'refused')
                self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
                self.assert_committed_source(f)

    def test_shared_resource_mutation_or_replacement_preserves_original_observed_report(self):
        for kind in ('profile', 'identity', 'private', 'installed', 'replacement', 'none'):
            with self.subTest(kind=kind):
                f = self.fixture()
                run = runtime._run
                observed = []
                def tamper(argv, **kwargs):
                    result = run(argv, **kwargs)
                    if kwargs['purpose'] == 'compile':
                        report = kwargs['_ledger'].resource_report
                        observed.append(asdict(report))
                        if kind == 'profile':
                            object.__setattr__(report, 'profile', 'forged')
                        elif kind == 'identity':
                            object.__setattr__(report.fonts[0].identity, 'family', 'forged')
                        elif kind == 'private':
                            object.__setattr__(report.fonts[0].private, 'sha256', '0' * 64)
                        elif kind == 'installed':
                            object.__setattr__(report.fonts[0].installed, 'size', 0)
                        else:
                            kwargs['_ledger'].resource_report = (None if kind == 'none'
                                else replace(report, profile='forged'))
                    return result
                with f.context(committed=False) as fix, f.resources(korean=True):
                    with self.session(fix, f) as pub:
                        fix.apply(fix.validate())
                        with mock.patch.object(runtime, '_run', side_effect=tamper):
                            with self.assertRaises(store.PublicationError) as failed:
                                self.publish(pub, fix, profile=KOREAN)
                        report = failed.exception.report
                        self.assert_compile_facts(report)
                        self.assertEqual(asdict(report.resource_report), observed[0])
                        self.assertEqual(report.resource_report.profile, KOREAN)
                self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
                self.assert_committed_source(f)

    def test_late_artifact_and_public_report_mutations_preserve_boundary_facts(self):
        for kind in ('invocation', 'artifact-report', 'artifact-resource',
                     'font-identity', 'public-report-copy'):
            with self.subTest(kind=kind):
                f = self.fixture()
                observed = []
                def tamper(point, path):
                    if point != 'after_compiled':
                        return
                    observed.append(asdict(pub.compile_report))
                    artifact = pub._artifact
                    if kind == 'invocation':
                        object.__setattr__(artifact.compilation, 'returncode', 91)
                    elif kind == 'artifact-report':
                        object.__setattr__(artifact.report, 'compiler_status', 91)
                    elif kind == 'artifact-resource':
                        object.__setattr__(artifact.resource_report, 'profile', 'forged')
                    elif kind == 'font-identity':
                        object.__setattr__(artifact.resource_report.fonts[0].identity, 'family', 'forged')
                    else:
                        public_copy = pub.compile_report
                        object.__setattr__(public_copy, 'compiler_status', 91)
                        object.__setattr__(public_copy.attempts[-1], 'returncode', 91)
                        object.__setattr__(public_copy.resource_report, 'profile', 'forged')
                        # A later empty safety report cannot erase the original call facts.
                        raise runtime.CompileSafetyError('owned late safety refusal')
                with f.context(committed=False) as fix, f.resources(korean=True):
                    with self.session(fix, f, fault=tamper) as pub:
                        fix.apply(fix.validate())
                        with self.assertRaises(store.PublicationError) as failed:
                            self.publish(pub, fix, profile=KOREAN)
                        self.assert_compile_facts(failed.exception.report)
                        self.assertEqual(asdict(failed.exception.report.compile_report), observed[0])
                        self.assertEqual(asdict(pub.compile_report), observed[0])
                self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
                self.assert_committed_source(f)

    def test_rebound_publication_binding_cannot_redirect_reconciliation_reporting_or_close(self):
        for kind in ('replacement-store', 'replacement-binding', 'mutated-binding-store'):
            with self.subTest(kind=kind):
                f, alien = self.fixture(), self.fixture()
                seen = []
                def rebind(point, path):
                    if point != 'after_compiled':
                        return
                    seen.append(point)
                    original = pub._binding
                    if kind == 'replacement-store':
                        pub._store = other_pub._store
                    elif kind == 'replacement-binding':
                        # Exercise the actual binding type without assuming its
                        # representation is a dataclass rather than a tuple.
                        replacement = (original._replace(store=other_pub._store)
                            if hasattr(original, '_replace')
                            else replace(original, store=other_pub._store))
                        pub._binding = replacement
                    else:
                        try:
                            object.__setattr__(original, 'store', other_pub._store)
                        except (AttributeError, TypeError):
                            # An immutable binding is a valid defense. Still
                            # exercise failure reconciliation after this hook.
                            raise runtime.CompileSafetyError('owned immutable binding refused mutation')
                with alien.context(committed=False) as other, f.context(committed=False) as fix:
                    with alien.resources(), self.session(other, alien) as other_pub:
                        other.apply(other.validate())
                        other_report = self.publish(other_pub, other)
                        alien_output = file_state(alien.output)
                        alien_events = journal_events(other_pub)
                        alien_store = other_pub._store
                        self.assertEqual(other_report.phase, 'published')
                        with f.resources():
                            with self.session(fix, f, fault=rebind) as pub:
                                original_store = pub._store
                                original_journal = original_store.journal_id
                                fix.apply(fix.validate())
                                source_journal = fix.summary.journal_id
                                with self.assertRaises(store.PublicationError) as failed:
                                    self.publish(pub, fix)
                                report = failed.exception.report
                                self.assertEqual(report.gate_status, 3)
                                self.assertEqual(report.phase, 'aborted_unpublished')
                                self.assertEqual(report.output_disposition, 'untouched')
                                self.assertEqual(report.journal_id, original_journal)
                                self.assertEqual(report.source_journal_id, source_journal)
                                self.assert_compile_facts(report)
                            self.assertFalse(original_store._active)
                        self.assertTrue(alien_store._active)
                        alien_store.check()
                        self.assertEqual(journal_events(other_pub), alien_events)
                        self.assertEqual(file_state(alien.output), alien_output)
                        self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
                self.assertEqual(seen, ['after_compiled'])
                self.assert_committed_source(f)
                self.assert_committed_source(alien)
                self.assertFalse(any(e.pending for e in store.inventory_publications(f.root)))

    def test_failure_callback_closure_does_not_retain_publication_or_bridge(self):
        def closed_refs():
            f = self.fixture()
            def fail_after_compile(point, path):
                if point == 'after_compiled':
                    # Deliberately create the common callback -> publication
                    # closure cycle. No registry may keep it alive after exit.
                    self.assertIsNotNone(pub.compile_report)
                    raise runtime.CompileSafetyError('owned closure-cycle refusal')
            with f.context(committed=False) as fix, f.resources():
                with self.assertRaises(store.PublicationError):
                    with self.session(fix, f, fault=fail_after_compile) as pub:
                        fix.apply(fix.validate())
                        self.publish(pub, fix)
            self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
            self.assert_committed_source(f)
            return weakref.ref(pub), weakref.ref(fix)
        publication_ref, bridge_ref = closed_refs()
        gc.collect()
        self.assertIsNone(publication_ref())
        self.assertIsNone(bridge_ref())


if __name__ == '__main__':
    unittest.main()
