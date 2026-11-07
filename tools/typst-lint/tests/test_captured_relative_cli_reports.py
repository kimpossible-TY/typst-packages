"""Historical CLI-facing graph facts, using owned temporary resources only.

Reports never authorize source writes, compiler inputs or PDF publication. No
test invokes Typst or reads installed fonts; the genuine graph adapter runs an
owned temporary Python compiler through its existing reviewed-resource seam.
"""
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import captured_relative_compile as compiler
import captured_relative_fix as bridge
import captured_relative_publish as publication
import closed_core_compile as runtime
import pdf_publication_store as store
from graph_compile_test_support import GraphFixture, pdf_bytes
from resource_profiles import ASCII_RESOURCE_PROFILE as ASCII
from syntax_client import EXECUTABLE
from transactions import TransactionError, TransactionFailure


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX owned compiler fixtures')
class CapturedRelativeCliReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='captured-graph-cli-reports-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.serial = 0

    def fixture(self, *, noop=False):
        self.serial += 1
        sources = {
            'main.typ': b'#import "values.typ": count\n#count $x_count$\n',
            'values.typ': b'#import "nested/leaf.typ": number\n#let count = number\n',
            'nested/leaf.typ': b'#let number = 7\n',
        }
        if noop:
            sources['main.typ'] = sources['main.typ'].replace(b'$x_count$', b'$x_(count)$')
        f = GraphFixture(self.base / str(self.serial), target='main.typ', sources=sources)
        f.destination = 'output/document.pdf'
        f.output = f.root / f.destination
        f.output.parent.mkdir()
        f.output.write_bytes(b'Owned preexisting output.\x00\xff')
        f.output_lease = store.OfflineOutputExclusive.assert_exporters_watchers_closed_and_no_other_writers()
        return f

    def session(self, fix, f, **kwargs):
        return publication.PreparedCapturedRelativePdfPublication(fix,
            destination=f.destination, lease=f.output_lease, **kwargs)

    def publish(self, fix, pub):
        return pub.compile_and_publish(fix.committed_graph_snapshot(), profile=ASCII)

    def assert_sources(self, summary, f):
        self.assertEqual(tuple(source.path for source in summary.sources), tuple(sorted(f.sources)))
        expected = dict(f.sources)
        expected[f.target] = expected[f.target].replace(b'$x_count$', b'$x_(count)$')
        self.assertEqual({source.path: (source.sha256, source.size, source.role)
                          for source in summary.sources},
            {path: (runtime._hash(raw), len(raw), 'writable_root' if path == f.target
                    else 'unchanged_read_only_dependency') for path, raw in expected.items()})
        self.assertEqual(tuple((edge.importer, edge.target, edge.items) for edge in summary.import_edges),
            (('main.typ', 'values.typ', (('count', 'count'),)),
             ('values.typ', 'nested/leaf.typ', (('number', 'number'),))))

    def test_real_and_noop_apply_retain_final_sources_and_edges_without_report_reads(self):
        for noop in (False, True):
            with self.subTest(noop=noop):
                f = self.fixture(noop=noop)
                before = {path: (f.root / path).stat() for path in f.sources if path != f.target}
                with f.context(committed=False) as fix:
                    with self.assertRaises(bridge.CapturedRelativeFixError):
                        _ = fix.summary
                    candidate = fix.validate()
                    result = fix.apply(candidate)
                    self.assert_sources(fix.summary, f)
                    self.assertEqual(result.modified, () if noop else (f.target,))
                    self.assertEqual(fix.summary.modified_paths, result.modified)
                    self.assertEqual(fix.summary.journal_id, result.journal)
                    self.assertEqual(bool(fix.summary.applied_insertions), not noop)
                    self.assertEqual(fix.summary.original_sha256, runtime._hash(f.sources[f.target]))
                    if noop:
                        self.assertIsNone(result.journal)
                    else:
                        self.assertIsNotNone(result.journal)
                    expected = asdict(fix.summary)
                for path, old in before.items():
                    now = (f.root / path).stat()
                    self.assertEqual((now.st_ino, now.st_mode, now.st_mtime_ns, now.st_ctime_ns),
                                     (old.st_ino, old.st_mode, old.st_mtime_ns, old.st_ctime_ns))
                    self.assertEqual((f.root / path).read_bytes(), f.sources[path])
                with mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('report reread')):
                    self.assertEqual(asdict(fix.summary), expected)

    def test_returned_summary_nested_mutation_cannot_falsify_or_erase_success_after_close(self):
        f = self.fixture()
        with f.context() as fix:
            summary = fix.summary
            expected = asdict(summary)
            self.assertIs(fix.summary, summary)  # Existing stable-report API.
            with self.assertRaises((FrozenInstanceError, AttributeError)):
                summary.sources[0].sha256 = 'forged'
            object.__setattr__(summary.sources[0], 'sha256', 'forged')
            object.__setattr__(summary.import_edges[0], 'items', (('forged', 'forged'),))
            object.__setattr__(summary.applied_insertions[0], 'path', 'forged.typ')
            object.__setattr__(summary, 'modified_paths', ('forged.typ',))
            self.assertEqual(asdict(fix.summary), expected)
            self.assertIsNot(fix.summary, summary)
        # Live currentness has expired, historical source facts have not.
        fix._summary = None
        with mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('report reread')):
            self.assertEqual(asdict(fix.summary), expected)

    def test_mid_apply_nested_report_mutation_refuses_handoff_but_preserves_actual_success(self):
        for field in ('source', 'edge'):
            with self.subTest(field=field):
                f = self.fixture()
                holder = {}
                def mutate(point, path):
                    if point == 'before_final_validation':
                        summary = holder['fix']._validation.summary
                        if field == 'source':
                            object.__setattr__(summary.sources[0], 'sha256', 'forged')
                        else:
                            object.__setattr__(summary.import_edges[0], 'items', ())
                with f.context(committed=False, fault=mutate) as fix:
                    holder['fix'] = fix
                    candidate = fix.validate()
                    expected = asdict(fix._validation.summary)
                    with self.assertRaises(bridge.CapturedRelativeFixError):
                        fix.apply(candidate)
                    summary = fix.summary
                    self.assert_sources(summary, f)
                    self.assertEqual(summary.sources, tuple(bridge.CapturedRelativeSourceSummary(**record)
                        for record in expected['sources']))
                    self.assertEqual(summary.modified_paths, (f.target,))
                    self.assertIsNotNone(summary.journal_id)
                    with self.assertRaises(TransactionError):
                        fix.committed_graph_snapshot()
                self.assertIn(b'$x_(count)$', (f.root / f.target).read_bytes())
                self.assert_sources(fix.summary, f)

    def test_failed_apply_does_not_invent_committed_source_reports(self):
        f = self.fixture()
        def fail(point, path):
            if point == 'before_replace':
                raise OSError('owned source replacement fault')
        with f.context(committed=False, fault=fail) as fix:
            with self.assertRaises(TransactionFailure):
                fix.apply(fix.validate())
            with self.assertRaises(bridge.CapturedRelativeFixError):
                _ = fix.summary
        self.assertEqual((f.root / f.target).read_bytes(), f.sources[f.target])

    def test_postcommit_dependency_drift_preserves_bounded_source_facts(self):
        f = self.fixture()
        with f.context() as fix:
            expected = asdict(fix.summary)
            snapshot = fix.committed_graph_snapshot()
            (f.root / 'nested/leaf.typ').write_bytes(b'#let number = 8\n')
            with self.assertRaises(TransactionError):
                fix.assert_committed_graph_current(snapshot)
            self.assertEqual(asdict(fix.summary), expected)
        self.assertEqual(asdict(fix.summary), expected)

    def test_successful_observation_uses_actual_dependency_order_and_guarded_manifest(self):
        for noop in (False, True):
            with self.subTest(noop=noop):
                f = self.fixture(noop=noop)
                order = ('values.typ', 'nested/leaf.typ', 'main.typ')
                f.program(dependency_paths=order)
                with f.context(committed=False) as fix, f.resources():
                    with self.session(fix, f) as pub:
                        self.assertIsNone(pub.graph_observation)
                        fix.apply(fix.validate())
                        self.assertIsNone(pub.graph_observation)
                        report = self.publish(fix, pub)
                        observation = pub.graph_observation
                        self.assertEqual(report.phase, 'published')
                        self.assertEqual(observation.dependency_paths, order)
                        self.assertNotEqual(observation.dependency_paths,
                                            tuple(source.path for source in fix.summary.sources))
                        raw = b''.join(path.encode() + b'\0' for path in order)
                        self.assertEqual(observation.dependencies.sha256, runtime._hash(raw))
                        self.assertEqual(observation.dependencies.size, len(raw))
                        self.assertEqual({source.path: (source.sha256, source.size)
                            for source in observation.sources},
                            {source.path: (source.sha256, source.size) for source in fix.summary.sources})
                        self.assertEqual(observation.aggregate_source_bytes,
                                         sum(source.size for source in observation.sources))
                        self.assertEqual(asdict(observation.scalar_budget), {
                            'policy': compiler.SCALAR_RENDER_POLICY, 'limit': compiler.MAX_SCALAR_RENDER_BYTES,
                            'total_charge': 2, 'use_count': 2, 'origin_count': 1})
                        self.assertFalse(hasattr(observation, 'pdf_bytes'))
                        self.assertFalse(hasattr(observation, 'dependency_bytes'))
                        self.assertFalse(any(hasattr(source, 'private') for source in observation.sources))
                        expected = asdict(observation)
                        self.assertIsNot(pub.graph_observation, observation)
                        self.assertIsNot(pub.graph_observation.dependencies, observation.dependencies)
                self.assertEqual(f.output.read_bytes(), pdf_bytes())
                for path in f.sources:
                    (f.root / path).unlink()
                with mock.patch.object(compiler, 'assert_artifact_current',
                        side_effect=AssertionError('historical report used live authority')), \
                        mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('report reread')):
                    self.assertEqual(asdict(pub.graph_observation), expected)

    def test_observation_returned_copy_mutation_cannot_change_historical_facts(self):
        f = self.fixture()
        with f.context(committed=False) as fix, f.resources():
            with self.session(fix, f) as pub:
                fix.apply(fix.validate())
                self.publish(fix, pub)
                observation = pub.graph_observation
                expected = asdict(observation)
                with self.assertRaises((FrozenInstanceError, AttributeError)):
                    observation.dependency_paths = ()
                object.__setattr__(observation, 'dependency_paths', ())
                object.__setattr__(observation.dependencies, 'sha256', 'forged')
                object.__setattr__(observation.sources[0], 'sha256', 'forged')
                object.__setattr__(observation.scalar_budget, 'total_charge', 999)
                self.assertEqual(asdict(pub.graph_observation), expected)
        self.assertEqual(asdict(pub.graph_observation), expected)

    def test_invalid_dependencies_and_compiler_failure_do_not_invent_observations(self):
        for behavior, paths in (('raise SystemExit(23)', None), ('', ('main.typ',)),
                                ('', ('main.typ', 'values.typ', 'nested/leaf.typ', 'main.typ'))):
            with self.subTest(behavior=behavior, paths=paths):
                f = self.fixture()
                f.program(behavior, dependency_paths=paths)
                original = f.output.read_bytes()
                with f.context(committed=False) as fix, f.resources():
                    with self.session(fix, f) as pub:
                        fix.apply(fix.validate())
                        with self.assertRaises(store.PublicationError):
                            self.publish(fix, pub)
                        self.assertIsNone(pub.graph_observation)
                        self.assertTrue(pub.compile_report.compiler_invoked)
                        self.assertEqual(pub.compile_report.compiler_status, 23 if behavior else 0)
                        self.assert_sources(fix.summary, f)
                self.assertIsNone(pub.graph_observation)
                self.assertEqual(f.output.read_bytes(), original)

    def test_returned_artifact_must_pass_guard_before_any_graph_observation(self):
        f = self.fixture()
        with f.context(committed=False) as fix, f.resources():
            with self.session(fix, f) as pub:
                fix.apply(fix.validate())
                with mock.patch.object(compiler, 'assert_artifact_current',
                        side_effect=compiler.CompileSafetyError('owned first artifact guard refusal')):
                    with self.assertRaises(store.PublicationError):
                        self.publish(fix, pub)
                self.assertIsNotNone(pub._artifact)
                self.assertIsNone(pub.graph_observation)
                self.assertTrue(pub.compile_report.compiler_invoked)
                self.assertEqual(pub.compile_report.compiler_status, 0)
        self.assertIsNone(pub.graph_observation)

    def test_later_callback_artifact_mutation_preserves_original_observations_and_compile_report(self):
        for field in ('dependencies', 'sources', 'budget', 'paths', 'common_report'):
            with self.subTest(field=field):
                f = self.fixture()
                original = f.output.read_bytes()
                holder = {}
                def mutate(point, path):
                    if point != 'before_event' or path != 'compiled':
                        return
                    pub = holder['pub']
                    holder['expected'] = asdict(pub.graph_observation)
                    artifact = pub._artifact
                    if field == 'dependencies':
                        object.__setattr__(artifact.dependencies, 'sha256', 'forged')
                    elif field == 'sources':
                        object.__setattr__(artifact.sources[0], 'sha256', 'forged')
                    elif field == 'budget':
                        object.__setattr__(artifact.scalar_budget, 'total_charge', 999)
                    elif field == 'paths':
                        object.__setattr__(artifact, 'dependency_paths', ())
                    else:
                        object.__setattr__(artifact.report.attempts[-1], 'returncode', 99)
                with f.context(committed=False) as fix, f.resources():
                    with self.session(fix, f, fault=mutate) as pub:
                        holder['pub'] = pub
                        fix.apply(fix.validate())
                        with self.assertRaises(store.PublicationError):
                            self.publish(fix, pub)
                        self.assertEqual(pub.report.phase, 'aborted_unpublished')
                        self.assertEqual(asdict(pub.graph_observation), holder['expected'])
                        self.assertEqual(pub.compile_report.compiler_status, 0)
                        self.assertEqual(pub.compile_report.attempts[-1].returncode, 0)
                self.assertEqual(asdict(pub.graph_observation), holder['expected'])
                self.assertEqual(f.output.read_bytes(), original)

    def test_post_observation_publication_fault_preserves_facts_without_persisting_them(self):
        f = self.fixture()
        def fail(point, path):
            if point == 'after_compiled':
                raise OSError('owned post-observation publication fault')
        with f.context(committed=False) as fix, f.resources():
            with self.session(fix, f, fault=fail) as pub:
                fix.apply(fix.validate())
                with self.assertRaises(store.PublicationError):
                    self.publish(fix, pub)
                expected = asdict(pub.graph_observation)
                events = [path.read_text() for path in pub._store.namespace.rglob('event-*.json')]
                self.assertFalse(any('dependency_paths' in event or 'scalar_budget' in event
                                     or 'graph_observation' in event for event in events))
        self.assertEqual(asdict(pub.graph_observation), expected)


if __name__ == '__main__':
    unittest.main()
