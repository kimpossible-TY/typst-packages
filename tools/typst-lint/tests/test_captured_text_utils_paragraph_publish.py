"""Deterministic paragraph publication authority/chronology/finality boundaries.

All resources and outputs are owned temporary fixtures. No Typst, font or PDF
process is invoked. General crash/restart tests live in their separate module.
"""
import copy
from dataclasses import asdict
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest import mock

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import captured_text_utils_paragraph_compile as compiler
import captured_text_utils_paragraph_fix as bridge
import captured_text_utils_paragraph_publish as publication
import closed_core_compile as runtime
import pdf_publication_store as store
from resource_profiles import ASCII_RESOURCE_PROFILE as ASCII
from syntax_client import EXECUTABLE
from transactions import TransactionError
from paragraph_compile_test_support import OLD_OUTPUT, clone_opaque, pdf_bytes
from paragraph_fix_test_support import EXPECTED, changed, evidence, state
from test_captured_text_utils_paragraph_compile import ParagraphCompileFixtures


def events(pub):
    directory = pub._store.namespace / pub._store.journal_id
    return tuple(json.loads(p.read_bytes()) for p in sorted(directory.glob('event-*.json')))


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX owned fixtures')
class ParagraphPublicationAuthorityTests(ParagraphCompileFixtures, unittest.TestCase):
    def session(self, fix, f, **kwargs):
        kwargs.setdefault('destination', f.destination)
        kwargs.setdefault('lease', f.output_lease)
        return publication.PreparedCapturedTextUtilsParagraphPdfPublication(fix, **kwargs)

    def publish(self, pub, fix, **kwargs):
        kwargs.setdefault('profile', ASCII)
        kwargs.setdefault('deadline_seconds', 120)
        return pub.compile_and_publish(fix.committed_snapshot(), **kwargs)

    def assert_no_compile(self, report):
        self.assertIs(report.compiler_invoked, False)
        self.assertIsNone(report.compiler_status)
        self.assertEqual(report.attempted_invocations, ())

    def assert_successful_simulated_compile(self, report):
        self.assertIs(report.compiler_invoked, True)
        self.assertEqual(report.compiler_status, 0)
        self.assertEqual(tuple(a.purpose for a in report.attempted_invocations), ('version-probe', 'compile'))
        self.assertEqual(report.compile_report.attempts, report.attempted_invocations)
        self.assertEqual(report.resource_report.profile, ASCII)

    def test_preparation_borrows_same_transaction_and_preserves_all_readonly_evidence(self):
        f = self.fixture()
        before = tuple(state(p) for p in evidence(f))
        protected = []
        prepare = store._Store.prepare
        def inspect(instance, *, protected_paths=()):
            protected.extend(map(Path, protected_paths))
            self.assertEqual(f.source.read_bytes(), f.original)
            return prepare(instance, protected_paths=protected_paths)
        with f.context(committed=False) as fix, f.resources(), \
                mock.patch.object(store._Store, 'prepare', new=inspect), \
                mock.patch.object(store.fcntl, 'flock', side_effect=AssertionError('second root flock')):
            with self.session(fix, f) as pub:
                self.assertIs(pub._store.transaction, fix._tx)
                fix.apply(fix.validate())
                report = self.publish(pub, fix)
                self.assertEqual((report.phase, report.gate_status, report.source_commit), ('published', 0, 'committed'))
                self.assert_successful_simulated_compile(report)
                observation = pub.package_observation
                self.assertEqual(observation.root_source.path, 'project/' + f.target)
                self.assertEqual(observation.root_source.sha256, runtime._hash(EXPECTED))
                self.assertEqual(len(observation.package_sources), 2)
                self.assertEqual(len(observation.dependency_paths), 3)
                self.assertEqual(observation.dependencies.sha256,
                    runtime._hash(b''.join(p.encode() + b'\0' for p in observation.dependency_paths)))
                self.assertNotIn('scalar_budget', asdict(observation))
                self.assertNotIn('sources', asdict(observation))
                intent = next(e for e in events(pub) if e['state'] == 'compile_launch_intent')
                self.assertEqual(intent['source_journal_id'], fix.summary.journal_id)
                self.assertEqual(intent['source_sha256'], runtime._hash(EXPECTED))
            fix.assert_committed_current(fix.committed_snapshot())
        self.assertTrue({f.source, f.config, f.catalog, f.manifest, f.package, f.store} <= set(protected))
        self.assertEqual(tuple(state(p) for p in evidence(f)), before)
        self.assertEqual(f.output.read_bytes(), pdf_bytes())
        self.assertFalse(any(i.pending for i in store.inventory_publications(f.root)))

    def test_preparation_refuses_after_every_successful_or_failed_validation_apply_or_snapshot_attempt(self):
        for action in ('validate', 'apply', 'failed-validate', 'failed-apply', 'snapshot', 'noop-apply'):
            with self.subTest(action=action):
                f = self.fixture(source=EXPECTED) if action == 'noop-apply' else self.fixture()
                output = state(f.output)
                with f.context(committed=False) as fix:
                    if action == 'failed-validate':
                        with mock.patch.object(fix._session, 'assert_current', side_effect=TransactionError('owned failed validation')):
                            with self.assertRaises(TransactionError):
                                fix.validate()
                    elif action == 'failed-apply':
                        with self.assertRaises(TransactionError):
                            fix.apply(object())
                    elif action == 'validate':
                        fix.validate()
                    else:
                        fix.apply(fix.validate())
                        if action == 'snapshot':
                            fix.committed_snapshot()
                    with self.assertRaises(TransactionError):
                        with self.session(fix, f):
                            self.fail('late output preparation admitted')
                self.assertEqual(state(f.output), output)
                self.assertFalse((f.root.parent / store.STORE_NAME).exists())

    def test_reflective_failed_attempt_flag_resets_cannot_reorder_preparation(self):
        for action in ('validate', 'apply'):
            with self.subTest(action=action):
                f = self.fixture()
                before = state(f.source), state(f.output)
                with f.context(committed=False) as fix:
                    if action == 'validate':
                        with mock.patch.object(fix._session, 'assert_current', side_effect=TransactionError('owned failure')):
                            with self.assertRaises(TransactionError):
                                fix.validate()
                    else:
                        with self.assertRaises(TransactionError):
                            fix.apply(None)
                    fix._validation_attempt_started = fix._apply_attempt_started = False
                    with self.assertRaises(TransactionError):
                        with self.session(fix, f):
                            self.fail('reflective flag reset erased a failed source attempt')
                self.assertEqual((state(f.source), state(f.output)), before)
                self.assertFalse((f.root.parent / store.STORE_NAME).exists())

    def test_source_attempt_inside_preparation_hook_is_not_pristine(self):
        for action in ('validate', 'failed-validate', 'failed-apply'):
            with self.subTest(action=action):
                f = self.fixture()
                before = state(f.source), state(f.output)
                fired = []
                def reorder(point, path):
                    if point != 'after_prepared':
                        return
                    fired.append(point)
                    if action == 'validate':
                        fix.validate()
                    elif action == 'failed-apply':
                        with self.assertRaises(TransactionError):
                            fix.apply(object())
                    else:
                        with mock.patch.object(fix._session, 'assert_current', side_effect=TransactionError('owned failed validation')):
                            with self.assertRaises(TransactionError):
                                fix.validate()
                with f.context(committed=False) as fix, f.resources():
                    with self.assertRaises(TransactionError):
                        with self.session(fix, f, fault=reorder):
                            self.fail('source work during preparation admitted')
                self.assertEqual(fired, ['after_prepared'])
                self.assertEqual((state(f.source), state(f.output)), before)
                self.assertEqual(f.attempts, [])

    def test_copied_owner_publisher_foreign_receipt_and_rebound_construction_never_compile(self):
        f, alien = self.fixture(), self.fixture()
        with f.context(committed=False) as fix, alien.context() as other, f.resources():
            for owner in (copy.copy(fix), fix._tx, object()):
                with self.subTest(owner=type(owner)), self.assertRaises(TransactionError):
                    with self.session(owner, f):
                        self.fail('unissued source owner prepared')
            pub = self.session(fix, f)
            with self.assertRaises(TransactionError):
                with copy.copy(pub):
                    self.fail('copied publisher prepared')
            with changed(pub, 'destination', 'other.pdf'), self.assertRaises(TransactionError):
                with pub:
                    self.fail('rebound destination prepared')
            with pub:
                fix.apply(fix.validate())
                receipt = fix.committed_snapshot()
                copied = copy.copy(pub)
                with self.assertRaises(TransactionError):
                    copied.compile_and_publish(receipt, profile=ASCII)
                copied.__exit__(None, None, None)
                self.assertTrue(pub._active)
                with self.assertRaises(store.PublicationError) as failed:
                    pub.compile_and_publish(other.committed_snapshot(), profile=ASCII)
                self.assert_no_compile(failed.exception.report)
                with self.assertRaises(TransactionError):
                    self.publish(pub, fix)
        self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
        self.assertEqual(f.source.read_bytes(), EXPECTED)
        self.assertEqual(f.attempts, [])

    def test_bare_cloned_receipts_and_external_artifact_are_not_publication_arguments(self):
        for kind in ('bare', 'clone', 'artifact'):
            with self.subTest(kind=kind):
                f = self.fixture()
                with f.context(committed=False) as fix, f.resources():
                    with self.session(fix, f) as pub:
                        fix.apply(fix.validate())
                        receipt = fix.committed_snapshot()
                        invalid = (fix._tx.committed_snapshot() if kind == 'bare' else
                                   clone_opaque(receipt) if kind == 'clone' else self.compile(fix))
                        attempts = len(f.attempts)
                        with mock.patch.object(runtime, '_capture_compiler', side_effect=AssertionError('invalid publication captures resources')):
                            with self.assertRaises(store.PublicationError) as failed:
                                pub.compile_and_publish(invalid, profile=ASCII)
                        self.assert_no_compile(failed.exception.report)
                        self.assertEqual(len(f.attempts), attempts)
                self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)

    def test_noop_source_commit_preserves_inode_and_absent_output_has_no_invented_source_journal(self):
        f = self.fixture(source=EXPECTED, present=False)
        original = state(f.source)
        with f.context(committed=False) as fix, f.resources():
            with self.session(fix, f) as pub:
                applied = fix.apply(fix.validate())
                report = self.publish(pub, fix)
                self.assertEqual(applied.modified, ())
                self.assertEqual(report.source_commit, 'committed')
                self.assertIsNone(report.source_journal_id)
        self.assertEqual(state(f.source), original)
        self.assertFalse(list(f.store.glob('root-*/*/manifest.json')))
        self.assertEqual(f.output.read_bytes(), pdf_bytes())
        self.assertEqual(f.output.stat().st_mode & 0o777, 0o600)

    def test_snapshot_mutation_of_artifact_report_and_public_observation_cannot_rewrite_original_facts(self):
        for kind in ('artifact-attempt', 'public-report', 'public-observation', 'source-history'):
            with self.subTest(kind=kind):
                f = self.fixture()
                boundary = []
                def mutate(point, path):
                    if point != 'after_compiled':
                        return
                    boundary.append(asdict(pub.compile_report))
                    if kind == 'artifact-attempt':
                        object.__setattr__(pub._artifact.compilation, 'returncode', 91)
                    elif kind == 'public-report':
                        object.__setattr__(pub.compile_report.attempts[-1], 'returncode', 91)
                        raise runtime.CompileSafetyError('owned late refusal')
                    elif kind == 'public-observation':
                        observation = pub.package_observation
                        object.__setattr__(observation.root_source.private, 'sha256', '0' * 64)
                        object.__setattr__(observation.package_sources[0], 'path', 'forged')
                        raise runtime.CompileSafetyError('owned late refusal')
                    else:
                        fix._applied_validation = None
                with f.context(committed=False) as fix, f.resources():
                    with self.session(fix, f, fault=mutate) as pub:
                        fix.apply(fix.validate())
                        original_journal = fix.summary.journal_id
                        with self.assertRaises(store.PublicationError) as failed:
                            self.publish(pub, fix)
                        report = failed.exception.report
                        self.assert_successful_simulated_compile(report)
                        self.assertEqual(asdict(report.compile_report), boundary[0])
                        self.assertEqual(report.source_commit, 'committed')
                        self.assertEqual(report.source_journal_id, original_journal)
                        self.assertEqual(pub.package_observation.root_source.sha256, runtime._hash(EXPECTED))
                        self.assertNotEqual(pub.package_observation.package_sources[0].path, 'forged')
                self.assertEqual(f.source.read_bytes(), EXPECTED)
                self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)

    def test_late_cancellation_preserves_successful_compile_and_old_output(self):
        f = self.fixture()
        cancelled = threading.Event()
        def cancel(point, path):
            if point == 'after_compiled':
                cancelled.set()
        with f.context(committed=False) as fix, f.resources():
            with self.session(fix, f, fault=cancel) as pub:
                fix.apply(fix.validate())
                with self.assertRaises(store.PublicationError) as failed:
                    self.publish(pub, fix, cancel_event=cancelled)
                self.assert_successful_simulated_compile(failed.exception.report)
                self.assertEqual(failed.exception.report.output_disposition, 'untouched')
                self.assertEqual(failed.exception.report.source_commit, 'committed')
        self.assertEqual(f.output.read_bytes(), OLD_OUTPUT)
        self.assertEqual(f.source.read_bytes(), EXPECTED)

    def test_output_rollback_and_durable_finality_never_undo_source_commit(self):
        for point in ('after_replace_before_event', 'before_published_event', 'after_published'):
            with self.subTest(point=point):
                f = self.fixture()
                fired = []
                def fault(where, path):
                    if where == point:
                        fired.append(where)
                        if where == 'after_published':
                            public = pub.report
                            object.__setattr__(public, 'phase', 'forged')
                            object.__setattr__(public, 'gate_status', 91)
                            object.__setattr__(public.attempted_invocations[-1], 'returncode', 91)
                        raise OSError('owned publication fault')
                with f.context(committed=False) as fix, f.resources():
                    with self.session(fix, f, fault=fault) as pub:
                        fix.apply(fix.validate())
                        with self.assertRaises((store.PublicationError, OSError)):
                            self.publish(pub, fix)
                        report = pub.report
                        self.assert_successful_simulated_compile(report)
                        self.assertEqual(report.source_commit, 'committed')
                        self.assertEqual(report.phase, 'published' if point == 'after_published' else 'restored')
                        self.assertEqual(report.gate_status, 0 if point == 'after_published' else 3)
                        with self.assertRaises(TransactionError):
                            self.publish(pub, fix)
                self.assertEqual(fired, [point])
                self.assertEqual(f.output.read_bytes(), pdf_bytes() if point == 'after_published' else OLD_OUTPUT)
                self.assertEqual(f.source.read_bytes(), EXPECTED)
                self.assertFalse(any(i.pending for i in store.inventory_publications(f.root)))


if __name__ == '__main__':
    unittest.main()
