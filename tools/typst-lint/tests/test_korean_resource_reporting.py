"""Original resource facts and pure schema-1 history, without font payloads."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import closed_core_compile as compiler
import closed_core_publish as publication
import offline_compile_cli as cli
import pdf_publication_store as store
import protected_fix
from protected_fix import ProtectedCoreFix
from resource_profiles import ASCII_RESOURCE_PROFILE, APPLEGOTHIC_RESOURCE_PROFILE
from syntax_client import EXECUTABLE
import typst_lint
from test_pdf_publication_integration import make_fixture, make_compiler


def original_resource_report():
    # Output-only synthetic evidence, never used as compilation/write authority.
    installed = compiler.FileEvidence('/declared/local/font.ttf', (1, 2, 3), 'a' * 64,
        123, (('/declared/local', (4, 5, 6)),))
    private = compiler.FileEvidence('/expired/private/fonts/font.ttf', (7, 8, 9), 'a' * 64, 123)
    identity = compiler.FontIdentity('TrueType', 'Original family', 'OriginalPS',
        'Original full name', 'Original version', 'Original unique name')
    return compiler.ResourceReport(APPLEGOTHIC_RESOURCE_PROFILE, 'applegothic-ko-v1',
        (compiler.FontEvidence(0, installed, private, identity),))


def original_compile_report(resources=None):
    invocation = compiler.Invocation('compile', ('private', 'compile'), '/expired/private', (),
        0, b'\x00\xff', b'', True, True, 'exited')
    return compiler.CompileReport((invocation,), 'passed', None, resources)


class ResourceReportSerializationTests(unittest.TestCase):
    def test_explicit_compile_help_discovers_both_exact_names_without_authority(self):
        output = io.StringIO()
        with mock.patch.object(Path, 'resolve', side_effect=AssertionError('No root access')), \
             mock.patch.object(compiler, 'resource_profile_protected_paths', side_effect=AssertionError), \
             redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            typst_lint.main(['compile', '--profile', 'closed-core-v1', '--help'])
        self.assertEqual(stopped.exception.code, 0)
        self.assertIn(ASCII_RESOURCE_PROFILE, output.getvalue())
        self.assertIn(APPLEGOTHIC_RESOURCE_PROFILE, output.getvalue())
        self.assertIn('Explicit compile only', output.getvalue())

    def test_primitive_original_identity_hash_size_and_face_survive_without_live_resources(self):
        resources = original_resource_report()
        report = original_compile_report(resources)
        with mock.patch.object(Path, 'stat', side_effect=AssertionError('No live font lookup')), \
             mock.patch.object(compiler, '_reviewed_native_artifact', side_effect=AssertionError):
            value = cli.compile_report_json(report)
            decoded = json.loads(json.dumps(value))
        self.assertTrue(decoded['resource_details_available'])
        resource = decoded['resource_report']
        self.assertEqual(resource['profile'], APPLEGOTHIC_RESOURCE_PROFILE)
        self.assertEqual(resource['repertoire_version'], 'applegothic-ko-v1')
        font, = resource['fonts']
        self.assertEqual(font['face_index'], 0)
        self.assertEqual(font['installed'], dict(path='/declared/local/font.ttf',
            identity=[1, 2, 3], sha256='a' * 64, size=123,
            parents=[['/declared/local', [4, 5, 6]]]))
        self.assertEqual(font['private']['path'], '/expired/private/fonts/font.ttf')
        self.assertEqual(font['identity']['family'], 'Original family')
        self.assertEqual(font['identity']['unique_name'], 'Original unique name')
        self.assertNotIn('bytes', resource)
        self.assertEqual(decoded['attempted_invocations'][0]['stdout_hex'], '00ff')

    def test_unavailable_differs_from_verified_empty_ascii_fonts(self):
        unavailable = cli.compile_report_json(original_compile_report())
        self.assertFalse(unavailable['resource_details_available'])
        self.assertIsNone(unavailable['resource_report'])
        ascii_report = compiler.ResourceReport(ASCII_RESOURCE_PROFILE, 'ascii-v1', ())
        verified = cli.compile_report_json(original_compile_report(ascii_report))
        self.assertTrue(verified['resource_details_available'])
        self.assertEqual(verified['resource_report']['fonts'], [])

    def test_outcome_retains_same_report_and_empty_later_refusal_cannot_erase_facts(self):
        fix = object.__new__(ProtectedCoreFix)
        session = publication.PreparedPdfPublication(fix, destination='build/report.pdf', lease=None)
        original = original_compile_report(original_resource_report())
        session._outcome(SimpleNamespace(report=original))
        self.assertIs(session.compile_report, original)
        refusal = compiler.CompileSafetyError('later font drift')
        session._outcome(refusal)
        self.assertIs(session.compile_report, original)
        self.assertTrue(session._compiler_invoked)
        self.assertEqual(session._compiler_status, 0)
        self.assertEqual(session._attempts, original.attempted_invocations)
        self.assertEqual(refusal.report.safety_gate, 'refused')
        self.assertEqual(original.safety_gate, 'passed')

    def test_historical_report_never_reuses_even_supplied_original_resource_details(self):
        original = original_compile_report(original_resource_report())
        report = store.PublicationReport('published', 0, True, 0, (), 'historical',
            'published', compile_report=original)
        value = cli.publication_report_json(report, historical=True)
        self.assertFalse(value['resource_details_available'])
        self.assertIsNone(value['resource_report'])
        self.assertIn('not persisted', value['resource_details_note'])
        self.assertIsNone(value['observed_sha256'])

    def test_pre_capture_report_does_not_fabricate_resource_evidence(self):
        report = store.PublicationReport('aborted_unpublished', 3, False, None)
        self.assertIsNone(report.compile_report)
        self.assertIsNone(report.resource_report)
        value = cli.publication_report_json(report)
        self.assertFalse(value['resource_details_available'])
        self.assertIsNone(value['resource_report'])


class ResourceHistoryTests(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return make_fixture(Path(temporary.name).resolve())

    def launched(self, f, profile):
        with store._Store(f.root, create=True) as journal:
            journal.bind_destination(f.destination)
            journal.prepare()
            journal.event('compile_launch_intent', source_sha256='a' * 64,
                source_journal_id=None, profile=profile, source_target=f.target,
                config_sha256='b' * 64, catalog_sha256='c' * 64)
            identifier = journal.journal_id
            event_path = journal.namespace / identifier / 'event-0001.json'
        return identifier, event_path

    def test_ascii_and_korean_schema1_history_recover_without_compiler_font_or_subprocess(self):
        for profile in (ASCII_RESOURCE_PROFILE, APPLEGOTHIC_RESOURCE_PROFILE):
            with self.subTest(profile=profile):
                f = self.fixture()
                identifier, event_path = self.launched(f, profile)
                event = json.loads(event_path.read_bytes())
                self.assertEqual(set(event), {'schema', 'kind', 'sequence', 'state',
                    'source_sha256', 'source_journal_id', 'profile', 'source_target',
                    'config_sha256', 'catalog_sha256'})
                self.assertEqual(event['schema'], 1)
                f.source.unlink()
                f.config.unlink()
                f.catalog.unlink()
                with mock.patch.object(compiler, '_reviewed_native_artifact', side_effect=AssertionError), \
                     mock.patch.object(compiler, 'resource_profile_protected_paths', side_effect=AssertionError), \
                     mock.patch.object(compiler, 'compile_committed_snapshot', side_effect=AssertionError), \
                     mock.patch.object(compiler.subprocess, 'Popen', side_effect=AssertionError):
                    entries = store.inventory_publications(f.root)
                    serialized = cli.inventory_json(entries)['entries'][0]
                    self.assertFalse(serialized['resource_details_available'])
                    self.assertIsNone(serialized['resource_report'])
                    recovered = store.recover_publication(f.root, destination=f.destination,
                        journal_id=identifier, lease=f.output_lease)
                self.assertEqual(recovered.phase, 'restored')
                self.assertIsNone(recovered.compile_report)
                self.assertIsNone(recovered.resource_report)
                self.assertFalse(recovered.recovery_required)

    def test_unknown_historical_name_or_extra_resource_field_remains_corrupt(self):
        for change in ({'profile': 'unknown-profile'}, {'profile': ['embedded-ascii-v1']},
                {'resource_report': None}, {'fonts': []}):
            with self.subTest(change=change):
                f = self.fixture()
                _, event_path = self.launched(f, APPLEGOTHIC_RESOURCE_PROFILE)
                event = json.loads(event_path.read_bytes())
                event.update(change)
                event_path.write_text(json.dumps(event))
                entry, = store.inventory_publications(f.root)
                self.assertEqual(entry.state, 'corrupt')
                self.assertTrue(entry.pending)


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class OriginalCliReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.f = make_fixture(Path(temporary.name).resolve(), target='main.typ', destination='build/main.pdf')

    def invoke(self, *, fault=None):
        record = make_compiler(self.f)
        arguments = ['compile', '--root', str(self.f.root), '--profile', 'closed-core-v1',
            '--resource-profile', ASCII_RESOURCE_PROFILE, '--offline-exclusive',
            '--offline-output-exclusive', '--target', self.f.target, '--output', self.f.destination,
            '--format', 'json']
        output = io.StringIO()
        original_enter = publication.PreparedPdfPublication.__enter__
        def enter(session):
            session.fault = fault
            return original_enter(session)
        with mock.patch.object(protected_fix, 'CATALOG', self.f.catalog), \
             mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record), \
             mock.patch.object(publication.PreparedPdfPublication, '__enter__', enter), \
             mock.patch.object(cli, 'compile_report_json', wraps=cli.compile_report_json) as serialize, \
             redirect_stdout(output):
            status = typst_lint.main(arguments)
        return status, json.loads(output.getvalue()), serialize

    def test_success_serializes_original_report_after_contexts_end(self):
        status, value, serialize = self.invoke()
        self.assertEqual(status, 0)
        original, = serialize.call_args.args
        self.assertIs(type(original), compiler.CompileReport)
        self.assertEqual(original.resource_report.profile, ASCII_RESOURCE_PROFILE)
        self.assertEqual(original.resource_report.fonts, ())
        self.assertEqual(value['compile_report'], cli.compile_report_json(original))
        self.assertTrue(value['publication_report']['resource_details_available'])
        self.assertEqual(value['publication_report']['resource_report']['fonts'], [])

    def test_later_empty_safety_error_preserves_original_report_and_gate3(self):
        def fault(point, path):
            if point == 'after_compiled':
                raise compiler.CompileSafetyError('owned later safety refusal')
        status, value, serialize = self.invoke(fault=fault)
        self.assertEqual(status, 3)
        original, = serialize.call_args.args
        self.assertEqual(value['compile_report'], cli.compile_report_json(original))
        self.assertEqual(value['compile_report']['safety_gate'], 'passed')
        self.assertTrue(value['compiler_invoked'])
        self.assertEqual(value['compiler_status'], 0)
        self.assertEqual(value['publication_report']['gate_status'], 3)
        self.assertIn('owned later safety refusal', value['publication_report']['reason'])
        self.assertTrue(value['publication_report']['resource_details_available'])
