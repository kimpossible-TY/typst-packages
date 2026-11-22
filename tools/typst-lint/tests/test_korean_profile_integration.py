"""Independent Korean source -> protected fix -> private compile -> PDF checks.

Default tests use owned, arbitrary resource bytes through private review seams.
The opt-in Mac test copies only the installed pinned font into the compiler's
temporary local workspace. No standalone Apple font files are stored as
fixtures or journal resource fields; native PDFs may contain embedded subsets.
"""
from contextlib import ExitStack, contextmanager, redirect_stdout
from dataclasses import FrozenInstanceError
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import closed_core_compile as cc
import closed_core_publish as publication
from closed_core import ClosedCoreError
import offline_compile_cli as cli
import pdf_publication_store as store
import protected_fix
from resource_profiles import ASCII_RESOURCE_PROFILE as ASCII, APPLEGOTHIC_RESOURCE_PROFILE as KOREAN
from syntax_client import EXECUTABLE
import test_pdf_publication_integration as fixtures

PUNCTUATION = '·–—‘’“”…₩、。〈〉《》「」『』【】'
PROSE = '한글 가힣 ㄱㅏ ' + PUNCTUATION + ' '
ORIGINAL = b'\xef\xbb\xbf' + (PROSE + '$x_한+y^글$\r\n').encode()
EXPECTED = b'\xef\xbb\xbf' + (PROSE + '$x_(한)+y^(글)$\r\n').encode()
FONT_BYTES = b'Owned Korean integration font fixture, not a real font.\x00\xff'
FONT_IDENTITY = cc.FontIdentity('TrueType', 'Owned Korean Fixture', 'OwnedKoreanFixture',
                               'Owned Korean Fixture Regular', '1', 'owned-korean-fixture')
FONT_PATH = Path('/System/Library/Fonts/Supplemental/AppleGothic.ttf')
FONT_SHA256 = '69071005e523e3a2bf276b35b0066f68a7ec0d942c860941ad188ef15ef69c51'


class _KoreanFixtures(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='typst-korean-publication-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.serial = 0

    def fixture(self, **kwargs):
        self.serial += 1
        kwargs.setdefault('source', ORIGINAL)
        # Filename Unicode is independent of the source repertoire, including
        # decomposed characters and emoji that are forbidden in source bytes.
        kwargs.setdefault('target', '한글 e\u0301 😀/문서 with spaces.typ')
        f = fixtures.make_fixture(self.base / str(self.serial), **kwargs)
        f.config.write_text('rule-set = "core"\ninclude = [' +
                            json.dumps(f.target, ensure_ascii=False) + ']\n')
        f.font = f.base / 'owned installed resources' / 'OwnedFixture.ttf'
        f.font.parent.mkdir(mode=0o700)
        f.font.write_bytes(FONT_BYTES)
        f.font.chmod(0o644)
        return f

    @contextmanager
    def resources(self, f, *, font_path=None):
        record = fixtures.make_compiler(f)
        font = cc._ReviewedFontArtifact(font_path or f.font, cc._hash(FONT_BYTES),
                                       len(FONT_BYTES), 0, FONT_IDENTITY)
        resources = cc._ReviewedResourceArtifact(record.sha256, (font,))
        with mock.patch.object(cc, '_reviewed_native_artifact', return_value=record), \
                mock.patch.object(cc, '_reviewed_resource_artifact', return_value=resources):
            yield record

    def session(self, fix, f, **kwargs):
        return publication.PreparedPdfPublication(fix, destination=f.destination,
                                                  lease=f.output_lease, **kwargs)

    def assert_committed(self, f, expected=EXPECTED):
        self.assertEqual(f.source.read_bytes(), expected)
        manifests = list(f.source_store.glob('root-*/*/manifest.json'))
        self.assertEqual(len(manifests), 1)
        events = [json.loads(path.read_bytes())['kind']
                  for path in sorted(manifests[0].parent.glob('event-*.json'))]
        self.assertEqual(events[-1], 'committed')
        self.assertNotIn('recovery_required', events)

    def run_cli(self, arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            status = cli.main(arguments)
        result = json.loads(output.getvalue())
        self.assertEqual(result['status'], status)
        self.assertTrue(output.getvalue().isascii())
        return result

    def compile_arguments(self, f, profile=KOREAN):
        return ['compile', '--root', str(f.root), '--format', 'json',
                '--profile', 'closed-core-v1', '--resource-profile', profile,
                '--offline-exclusive', '--offline-output-exclusive',
                '--target', f.target, '--output', f.destination]

    def journal(self, f, journal_id):
        namespace = 'root-' + cc._hash(os.fsencode(f.root))
        return f.root.parent / store.STORE_NAME / namespace / journal_id

    @contextmanager
    def no_live_resources(self):
        """A restarted reader must work on a host with no usable resources."""
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(cc, '_NATIVE_ARTIFACTS', {}))
            stack.enter_context(mock.patch.object(cc, '_RESOURCE_ARTIFACTS', {}))
            for name in ('_reviewed_native_artifact', '_reviewed_resource_artifact',
                         'resource_profile_protected_paths', '_capture_compiler',
                         '_capture_fonts', '_capture', '_run'):
                stack.enter_context(mock.patch.object(cc, name,
                    side_effect=AssertionError('Historical reader consulted live resources: ' + name)))
            stack.enter_context(mock.patch.object(cc.subprocess, 'Popen',
                side_effect=AssertionError('Historical reader launched a process')))
            yield

    def remove_live_inputs(self, f, compiler):
        for path in (f.font, compiler.path, f.source, f.config, f.catalog):
            path.unlink()


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX protected publication')
class KoreanProfileIntegrationTests(_KoreanFixtures):
    def test_real_korean_repair_publication_retains_original_resource_report(self):
        f = self.fixture()
        old_unselected = fixtures.file_state(f.unrelated)
        deps = []
        run = cc._run

        def observe(argv, **kwargs):
            result = run(argv, **kwargs)
            if kwargs['purpose'] == 'compile':
                deps.append(Path(argv[argv.index('--deps') + 1]).read_bytes())
                self.assertEqual(argv.count('--font-path'), 1)
                directory = Path(argv[argv.index('--font-path') + 1])
                self.assertEqual([p.name for p in directory.iterdir()], ['AppleGothic.ttf'])
                self.assertEqual((directory / 'AppleGothic.ttf').read_bytes(), FONT_BYTES)
                self.assertIn('--ignore-system-fonts', argv)
            return result

        with self.resources(f), fixtures.fixture_fix(f) as fix:
            with self.session(fix, f) as pub, mock.patch.object(cc, '_run', side_effect=observe):
                candidate = fix.validate()
                self.assertEqual(candidate.passes, 1)
                fix.apply(candidate)
                self.assertEqual(fix.compile_snapshot().source, EXPECTED)
                report = pub.compile_and_publish(fix.compile_snapshot(), profile=KOREAN)
                original = pub._artifact.report
                self.assertIs(pub.compile_report, original)
                self.assertIs(report.compile_report, original)
                self.assertIs(report.resource_report, pub._artifact.resource_report)
                self.assertEqual(pub._artifact.dependency_paths, (f.target,))
        self.assertIs(pub.compile_report, original)
        self.assertIs(report.resource_report, original.resource_report)
        self.assertEqual((report.phase, report.gate_status, report.compiler_status), ('published', 0, 0))
        self.assertEqual(deps, [os.fsencode(f.target) + b'\0'])
        resource = report.resource_report
        self.assertEqual((resource.profile, resource.repertoire_version), (KOREAN, 'applegothic-ko-v1'))
        font, = resource.fonts
        self.assertEqual((font.face_index, font.identity), (0, FONT_IDENTITY))
        self.assertEqual(font.installed.sha256, cc._hash(FONT_BYTES))
        self.assertEqual(font.installed.sha256, font.private.sha256)
        self.assertNotEqual(font.installed.identity, font.private.identity)
        self.assertFalse(Path(font.private.path).exists())
        self.assertTrue(font.installed.parents)
        with self.assertRaises(FrozenInstanceError):
            font.identity.version = 'changed'
        self.assertIn(EXPECTED.hex().encode(), f.output.read_bytes())
        self.assert_committed(f)
        self.assertEqual(fixtures.file_state(f.unrelated), old_unselected)

    def test_font_absence_or_drift_refuses_before_probe_preserving_source_and_old_output(self):
        for kind in ('missing', 'wrong-bytes'):
            with self.subTest(kind=kind):
                f = self.fixture()
                old = fixtures.file_state(f.output)
                with self.resources(f), fixtures.fixture_fix(f) as fix:
                    with self.session(fix, f) as pub:
                        fix.apply(fix.validate())
                        if kind == 'missing':
                            f.font.unlink()
                        else:
                            f.font.write_bytes(b'X' * len(FONT_BYTES))
                        with mock.patch.object(cc, '_run', side_effect=AssertionError('must not probe')):
                            with self.assertRaises(store.PublicationError) as caught:
                                pub.compile_and_publish(fix.compile_snapshot(), profile=KOREAN)
                self.assertIsInstance(caught.exception.__cause__, cc.CompileSafetyError)
                self.assertEqual(pub.report.gate_status, 3)
                self.assertFalse(pub.report.compiler_invoked)
                self.assertEqual(pub.compile_report.attempts, ())
                self.assertEqual(pub.report.source_commit, 'committed')
                self.assertEqual(fixtures.file_state(f.output), old)
                self.assert_committed(f)

    def test_postcompile_font_drift_preserves_original_facts_and_blocks_publication(self):
        f = self.fixture()
        old = fixtures.file_state(f.output)
        run = cc._run
        originals = []

        def drift(argv, **kwargs):
            result = run(argv, **kwargs)
            if kwargs['purpose'] == 'compile':
                originals.append(kwargs['_ledger'].resource_report)
                f.font.write_bytes(b'X' * len(FONT_BYTES))
            return result

        with self.resources(f), fixtures.fixture_fix(f) as fix:
            with self.session(fix, f) as pub:
                fix.apply(fix.validate())
                with mock.patch.object(cc, '_run', side_effect=drift):
                    with self.assertRaises(store.PublicationError):
                        pub.compile_and_publish(fix.compile_snapshot(), profile=KOREAN)
        self.assertEqual((pub.report.gate_status, pub.report.compiler_status), (3, 0))
        self.assertIs(pub.report.resource_report, originals[0])
        self.assertIs(pub.report.compile_report, pub.compile_report)
        self.assertEqual(pub.report.resource_report.fonts[0].installed.sha256, cc._hash(FONT_BYTES))
        self.assertFalse(Path(pub.report.resource_report.fonts[0].private.path).exists())
        self.assertEqual(fixtures.file_state(f.output), old)
        self.assert_committed(f)

    def test_selected_font_output_collision_refuses_before_launch_or_font_capture(self):
        f = self.fixture()
        f.output.write_bytes(FONT_BYTES)
        old = fixtures.file_state(f.output)
        with self.resources(f, font_path=f.output), fixtures.fixture_fix(f) as fix:
            with self.session(fix, f) as pub:
                fix.apply(fix.validate())
                with mock.patch.object(cc, 'resource_profile_protected_paths', return_value=(f.output,)), \
                        mock.patch.object(cc, '_capture_compiler', side_effect=AssertionError('no compiler capture')), \
                        mock.patch.object(cc, '_capture_fonts', side_effect=AssertionError('no font capture')):
                    with self.assertRaises(store.PublicationError):
                        pub.compile_and_publish(fix.compile_snapshot(), profile=KOREAN)
        self.assertEqual(pub.report.gate_status, 3)
        self.assertFalse(pub.report.compiler_invoked)
        self.assertEqual(fixtures.file_state(f.output), old)
        self.assert_committed(f)

    def test_no_fallback_for_forbidden_unicode_even_when_a_renderer_might_exit_zero(self):
        sources = [('한글 ' + character + ' $x_i$\n').encode()
                   for character in ('😀', '\u3164', '\u115f', '\u1160', '한', '\ufe0f', 'α')]
        sources.extend(source.encode() for source in ('한글 $α + x_i$\n', '한글 $∑ + x_i$\n'))
        for source in sources:
            with self.subTest(source=source):
                expected = source.replace(b'x_i', b'x_(i)')
                f = self.fixture(source=source)
                old = fixtures.file_state(f.output)
                with self.resources(f), fixtures.fixture_fix(f) as fix:
                    with self.session(fix, f) as pub:
                        fix.apply(fix.validate())
                        with mock.patch.object(cc, '_capture_compiler', side_effect=AssertionError('no compiler')), \
                                mock.patch.object(cc, '_capture_fonts', side_effect=AssertionError('no font fallback')):
                            with self.assertRaises(store.PublicationError) as caught:
                                pub.compile_and_publish(fix.compile_snapshot(), profile=KOREAN)
                self.assertIsInstance(caught.exception.__cause__, cc.UnsupportedCompileProfile)
                self.assertEqual(pub.report.gate_status, 2)
                self.assertFalse(pub.report.compiler_invoked)
                self.assertEqual(fixtures.file_state(f.output), old)
                self.assert_committed(f, expected)

    def test_repertoire_does_not_widen_closed_core_grammar_or_nonliteral_prose(self):
        sources = [('한글 $x_(' + character + ')$\n').encode() for character in PUNCTUATION]
        sources.extend(source.encode() for source in ('한글 \u202e $x_i$\n',
            '한글 \u200d $x_i$\n', '#set text(font: "AppleGothic")\n한글 $x_i$\n'))
        for source in sources:
            with self.subTest(source=source):
                f = self.fixture(source=source)
                before = fixtures.file_state(f.source), fixtures.file_state(f.output)
                with self.resources(f), fixtures.fixture_fix(f) as fix:
                    with self.session(fix, f), \
                            mock.patch.object(cc, '_capture_compiler', side_effect=AssertionError('no compiler')):
                        with self.assertRaises(ClosedCoreError):
                            fix.validate()
                self.assertEqual((fixtures.file_state(f.source), fixtures.file_state(f.output)), before)
                self.assertEqual(list(f.source_store.glob('root-*/*/manifest.json')), [])

    def test_ascii_publication_never_resolves_or_reads_external_fonts(self):
        f = self.fixture(source=fixtures.ORIGINAL)
        f.font.unlink()
        with self.resources(f), fixtures.fixture_fix(f) as fix, \
                mock.patch.object(cc, '_reviewed_resource_artifact', side_effect=AssertionError('no font resolution')):
            with self.session(fix, f) as pub:
                fix.apply(fix.validate())
                report = pub.compile_and_publish(fix.compile_snapshot(), profile=ASCII)
        self.assertEqual(report.resource_report, cc.ResourceReport(ASCII, 'ascii-v1', ()))
        invocation = report.attempted_invocations[-1]
        self.assertNotIn('--font-path', invocation.argv)
        self.assertEqual(report.gate_status, 0)
        self.assert_committed(f, fixtures.EXPECTED)

    def test_cli_serializes_original_font_evidence_after_both_contexts_close(self):
        f = self.fixture()
        captured = []
        compile_snapshot = cc.compile_committed_snapshot

        def record(*args, **kwargs):
            artifact = compile_snapshot(*args, **kwargs)
            captured.append(artifact.report)
            return artifact

        with self.resources(f), mock.patch.object(protected_fix, 'CATALOG', f.catalog), \
                mock.patch.object(cc, 'compile_committed_snapshot', side_effect=record):
            result = self.run_cli(self.compile_arguments(f))
        self.assertEqual(result['status'], 0)
        self.assertEqual(f.source.read_bytes(), EXPECTED)
        expected = cli.resource_report_json(captured[0].resource_report)
        self.assertEqual(result['compile_report']['resource_report'], expected)
        self.assertEqual(result['publication_report']['resource_report'], expected)
        self.assertTrue(result['compile_report']['resource_details_available'])
        font, = expected['fonts']
        self.assertEqual(set(font), {'face_index', 'installed', 'private', 'identity'})
        self.assertEqual(set(font['installed']), {'path', 'identity', 'sha256', 'size', 'parents'})
        self.assertFalse(Path(font['private']['path']).exists())
        self.assertNotIn(FONT_BYTES.hex(), json.dumps(result))

    def test_cli_safety_three_keeps_successful_compile_resource_facts_after_later_drift(self):
        f = self.fixture()
        old = fixtures.file_state(f.output)
        compile_snapshot = cc.compile_committed_snapshot
        captured = []

        def drift(*args, **kwargs):
            artifact = compile_snapshot(*args, **kwargs)
            captured.append(artifact.report)
            f.font.write_bytes(b'X' * len(FONT_BYTES))
            return artifact

        with self.resources(f), mock.patch.object(protected_fix, 'CATALOG', f.catalog), \
                mock.patch.object(cc, 'compile_committed_snapshot', side_effect=drift):
            result = self.run_cli(self.compile_arguments(f))
        self.assertEqual((result['status'], result['compiler_status']), (3, 0))
        self.assertEqual(result['compile_report']['resource_report'],
                         cli.resource_report_json(captured[0].resource_report))
        self.assertEqual(result['publication_report']['resource_report'],
                         result['compile_report']['resource_report'])
        self.assertEqual(f.source.read_bytes(), EXPECTED)
        self.assertEqual(fixtures.file_state(f.output), old)

    def test_schema_one_terminal_history_is_resource_independent_and_preserves_later_output(self):
        for profile in (ASCII, KOREAN):
            with self.subTest(profile=profile):
                f = self.fixture(source=fixtures.ORIGINAL if profile == ASCII else ORIGINAL)
                with self.resources(f) as compiler, fixtures.fixture_fix(f) as fix:
                    with self.session(fix, f) as pub:
                        fix.apply(fix.validate())
                        report = pub.compile_and_publish(fix.compile_snapshot(), profile=profile)
                directory = self.journal(f, report.journal_id)
                manifest = json.loads((directory / 'manifest.json').read_bytes())
                self.assertEqual(manifest['schema'], 1)
                self.assertEqual(set(manifest), {'schema', 'kind', 'root', 'root_ancestors',
                    'destination', 'parents', 'old', 'backup', 'private_metadata'})
                events = [json.loads(path.read_bytes()) for path in sorted(directory.glob('event-*.json'))]
                self.assertEqual(events[1]['profile'], profile)
                self.assertEqual(set(events[1]), {'schema', 'kind', 'sequence', 'state',
                    'source_sha256', 'source_journal_id', 'profile', 'source_target',
                    'config_sha256', 'catalog_sha256'})
                self.assertEqual(set(events[2]), {'schema', 'kind', 'sequence', 'state',
                    'compiler_sha256', 'pdf_sha256', 'invocations', 'compiler_invoked', 'compiler_status'})
                self.remove_live_inputs(f, compiler)
                f.output.write_bytes(b'Later independently saved user output.\x00\xff')
                later = fixtures.file_state(f.output)
                with self.no_live_resources():
                    entries = store.inventory_publications(f.root)
                    self.assertEqual([(e.state, e.pending) for e in entries], [('published', False)])
                    recovered = store.recover_publication(f.root, destination=f.destination,
                        journal_id=report.journal_id, lease=f.output_lease)
                    status = self.run_cli(['output-status', '--root', str(f.root), '--format', 'json'])
                    history = self.run_cli(['recover-output', '--root', str(f.root), '--format', 'json',
                        '--output', f.destination, '--journal-id', report.journal_id,
                        '--offline-output-exclusive'])
                self.assertEqual(fixtures.file_state(f.output), later)
                self.assertFalse(recovered.recovery_required)
                self.assertIsNone(recovered.compile_report)
                self.assertIsNone(recovered.resource_report)
                for item in (status['output_inventory']['entries'][0], history['publication_report']):
                    self.assertIsNone(item['resource_report'])
                    self.assertFalse(item['resource_details_available'])
                    self.assertIn('no live resources', item['resource_details_note'])
                self.assertEqual(history['publication_report']['source_commit'], 'historical')

    def test_schema_one_pending_known_postimage_recovers_without_font_compiler_or_sources(self):
        for profile in (ASCII, KOREAN):
            with self.subTest(profile=profile):
                f = self.fixture(source=fixtures.ORIGINAL if profile == ASCII else ORIGINAL)

                def stop_before_commit(point, value):
                    if point == 'before_event' and value == 'published':
                        raise OSError('owned interrupted completion, replaced evidence is durable')

                with self.resources(f) as compiler, fixtures.fixture_fix(f) as fix:
                    with self.session(fix, f, fault=stop_before_commit) as pub:
                        fix.apply(fix.validate())
                        with self.assertRaises(store.PublicationError):
                            pub.compile_and_publish(fix.compile_snapshot(), profile=profile)
                self.assertNotEqual(f.output.read_bytes(), fixtures.OLD_OUTPUT)
                self.assertTrue(pub.report.recovery_required)
                self.remove_live_inputs(f, compiler)
                with self.no_live_resources():
                    entries = store.inventory_publications(f.root)
                    self.assertEqual([(e.state, e.pending) for e in entries], [('replaced', True)])
                    recovered = store.recover_publication(f.root, destination=f.destination,
                        journal_id=pub.report.journal_id, lease=f.output_lease)
                    self.assertEqual(store.inventory_publications(f.root)[0].state, 'restored')
                self.assertEqual((recovered.phase, recovered.output_disposition), ('restored', 'restored'))
                self.assertIsNone(recovered.resource_report)
                self.assertEqual(f.output.read_bytes(), fixtures.OLD_OUTPUT)

    def test_unknown_profile_or_extra_resource_fields_remain_corrupt_schema_one(self):
        for corruption in ('unknown-profile', 'extra-field'):
            with self.subTest(corruption=corruption):
                f = self.fixture()
                with self.resources(f) as compiler, fixtures.fixture_fix(f) as fix:
                    with self.session(fix, f) as pub:
                        fix.apply(fix.validate())
                        report = pub.compile_and_publish(fix.compile_snapshot(), profile=KOREAN)
                event = self.journal(f, report.journal_id) / 'event-0001.json'
                data = json.loads(event.read_bytes())
                if corruption == 'unknown-profile':
                    data['profile'] = KOREAN + '-future'
                else:
                    data['resource_report'] = {'fonts': []}
                event.write_text(json.dumps(data))
                self.remove_live_inputs(f, compiler)
                old = fixtures.file_state(f.output)
                with self.no_live_resources():
                    entry, = store.inventory_publications(f.root)
                    self.assertEqual((entry.state, entry.pending), ('corrupt', True))
                    with self.assertRaises(store.PublicationError):
                        store.recover_publication(f.root, destination=f.destination,
                            journal_id=report.journal_id, lease=f.output_lease)
                self.assertEqual(fixtures.file_state(f.output), old)


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1',
                     'Opt in to private local pinned-font publication with TYPST_LINT_RENDER_TESTS=1')
@unittest.skipUnless(sys.platform == 'darwin' and EXECUTABLE.is_file()
                     and fixtures.PINNED.is_file() and FONT_PATH.is_file()
                     and shutil.which('pdftotext') and shutil.which('pdffonts'),
                     'Requires pinned Mac resources, syntax adapter and local PDF test helpers')
class NativeKoreanPublicationTests(_KoreanFixtures):
    def test_korean_multi_letter_math_identifier_keeps_real_compiler_one_and_font_evidence(self):
        # Repertoire support does not declare arbitrary identifiers as variables.
        # This source is certified, then Typst itself reports an unknown variable.
        source = '한글 $x_(한글)$\n'.encode()
        f = self.fixture(source=source)
        before = fixtures.file_state(f.source), fixtures.file_state(f.output)
        with mock.patch.object(protected_fix, 'CATALOG', f.catalog):
            result = self.run_cli(self.compile_arguments(f))
        self.assertEqual((result['status'], result['compiler_status']), (1, 1))
        report = result['compile_report']
        self.assertEqual(report['safety_gate'], 'passed')
        self.assertEqual(report['compiler_failure']['kind'], 'process_exit')
        self.assertTrue(report['resource_details_available'])
        self.assertEqual(report['resource_report']['fonts'][0]['installed']['sha256'], FONT_SHA256)
        self.assertEqual(report['resource_report'], result['publication_report']['resource_report'])
        diagnostic = bytes.fromhex(report['attempted_invocations'][-1]['stderr_hex'])
        self.assertIn(b'unknown variable', diagnostic)
        self.assertIn('한글'.encode(), diagnostic)
        self.assertEqual((fixtures.file_state(f.source), fixtures.file_state(f.output)), before)

    def test_real_fix_compile_publication_extracts_korean_prose_punctuation_and_math(self):
        f = self.fixture()
        with fixtures.fixture_fix(f) as fix:
            with self.session(fix, f) as pub:
                fix.apply(fix.validate())
                self.assertEqual(fix.compile_snapshot().source, EXPECTED)
                report = pub.compile_and_publish(fix.compile_snapshot(), profile=KOREAN,
                                                 deadline_seconds=120)
                self.assertEqual(pub._artifact.dependency_paths, (f.target,))
                self.assertEqual(pub._artifact.compiler.sha256, fixtures.PINNED_SHA256)
                self.assertEqual(pub._artifact.compilation.stderr, b'')
        self.assertEqual((report.gate_status, report.compiler_status), (0, 0))
        font, = report.resource_report.fonts
        self.assertEqual((font.installed.path, font.installed.sha256, font.installed.size, font.face_index),
                         (str(FONT_PATH), FONT_SHA256, 15253080, 0))
        self.assertEqual(font.identity, cc.FontIdentity('TrueType', 'AppleGothic', 'AppleGothic',
            'AppleGothic Regular', '22.0d2e1', 'AppleGothic Regular;13.0d1e3;2017-07-12'))
        self.assertEqual(font.private.sha256, FONT_SHA256)
        self.assertFalse(Path(font.private.path).exists())
        compile_attempt = report.attempted_invocations[-1]
        self.assertIn('--ignore-system-fonts', compile_attempt.argv)
        self.assertEqual(compile_attempt.argv.count('--font-path'), 1)
        extracted = subprocess.run([shutil.which('pdftotext'), '-layout', str(f.output), '-'],
                                   capture_output=True, timeout=20)
        self.assertEqual(extracted.returncode, 0, extracted.stderr)
        text = extracted.stdout.decode()
        for character in '한글가힣ㄱㅏ' + PUNCTUATION:
            self.assertIn(character, text)
        self.assertIn('𝑥한 + 𝑦글', text)
        fonts = subprocess.run([shutil.which('pdffonts'), str(f.output)],
                               capture_output=True, timeout=20)
        self.assertEqual(fonts.returncode, 0, fonts.stderr)
        self.assertIn(b'AppleGothic', fonts.stdout)
        apple_font = next(line for line in fonts.stdout.splitlines() if b'AppleGothic' in line)
        self.assertEqual(apple_font.split()[-5:-2], [b'yes', b'yes', b'yes'])
        self.assert_committed(f)
        self.assertFalse(store.inventory_publications(f.root)[0].pending)


if __name__ == '__main__':
    unittest.main()
