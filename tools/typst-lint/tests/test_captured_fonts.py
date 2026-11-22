"""Owned fake resources by default; actual Mac font copies are local/opt-in.

No font bytes are stored in this repository. Deterministic fixtures are arbitrary
owned bytes registered through private trusted seams, not actual font files.
"""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, asdict, fields, replace
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import unittest
from unittest import mock

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import closed_core_compile as cc
import test_closed_core_compile as fixtures
import test_committed_compile_integration as integration
_REAL_RESOURCE_RESOLVER = cc._reviewed_resource_artifact
from resource_profiles import ASCII_RESOURCE_PROFILE as ASCII, APPLEGOTHIC_RESOURCE_PROFILE as KOREAN


class CapturedFontTests(unittest.TestCase):
    set_source = fixtures.CompilerAdapterTests.set_source
    program = fixtures.CompilerAdapterTests.program
    reviewed = fixtures.CompilerAdapterTests.reviewed

    def setUp(self):
        fixtures.CompilerAdapterTests.setUp(self)
        self.font_path = self.base / 'installed' / 'OwnedFixture.ttf'
        self.font_path.parent.mkdir(mode=0o700)
        self.font_bytes = b'OWNED FAKE FONT RESOURCE: not an actual font\x00\xff'
        self.font_path.write_bytes(self.font_bytes)
        self.font_path.chmod(0o644)
        self.font_identity = cc.FontIdentity('TrueType', 'OwnedFixture', 'OwnedFixture',
                                            'OwnedFixture Regular', '1', 'owned-fixture')
        self.set_source('한글 ㄱㅏ ·–— $alpha_(한) + x^(글)$'.encode())
        patch = mock.patch.object(cc, '_reviewed_resource_artifact', side_effect=self.resources)
        self.resource_lookup = patch.start()
        self.addCleanup(patch.stop)

    def resources(self, profile):
        self.assertEqual(profile, KOREAN)
        return cc._ReviewedResourceArtifact(self.reviewed_hash, (
            cc._ReviewedFontArtifact(self.font_path, cc._hash(self.font_bytes),
                                     len(self.font_bytes), 0, self.font_identity),))

    def compile(self, **kwargs):
        return cc.compile_committed_snapshot(self.fix, self.snapshot, profile=KOREAN, **kwargs)

    def rewrite(self, path, *, replace_inode=False):
        raw = path.read_bytes()
        mode = stat.S_IMODE(path.stat().st_mode)
        path.chmod(0o600)
        if replace_inode:
            path.unlink()
        path.write_bytes(raw[:-1] + bytes((raw[-1] ^ 1,)))
        path.chmod(mode)

    def test_exact_private_capture_argv_environment_evidence_and_cleanup(self):
        run = cc._run
        observed = []

        def inspect(argv, **kwargs):
            base = Path(argv[0]).parent.parent
            private = base / 'fonts' / 'AppleGothic.ttf'
            self.assertEqual(private.read_bytes(), self.font_bytes)
            self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o400)
            self.assertEqual(stat.S_IMODE(private.parent.stat().st_mode), 0o700)
            self.assertEqual(set(private.parent.iterdir()), {private})
            if kwargs['purpose'] == 'compile':
                self.assertEqual(Path(argv[-2]).read_bytes(), self.snapshot.source)
            self.assertEqual(kwargs['environment'], cc._environment(base))
            self.assertNotIn('TYPST_FONT_PATHS', kwargs['environment'])
            if kwargs['purpose'] == 'compile':
                self.assertEqual(argv[-4:-2], ('--font-path', str(private.parent)))
                self.assertEqual(argv.count('--font-path'), 1)
                self.assertIn('--ignore-system-fonts', argv)
                self.assertEqual(argv[1:-4], ('compile', '--format', 'pdf', '--root', str(base / 'project'),
                    '--ignore-system-fonts', '--package-path', str(base / 'packages'),
                    '--package-cache-path', str(base / 'package-cache'), '--creation-timestamp', '0',
                    '--jobs', '1', '--diagnostic-format', 'short', '--deps', str(base / 'out/dependencies.zero'),
                    '--deps-format', 'zero'))
            observed.append(private)
            return run(argv, **kwargs)

        with mock.patch.object(cc, '_run', side_effect=inspect), mock.patch.dict(os.environ, {
                'TYPST_FONT_PATHS': '/wrong', 'TYPST_IGNORE_SYSTEM_FONTS': 'false', 'PATH': '/wrong'}):
            artifact = self.compile()
        report = artifact.resource_report
        self.assertIs(report, artifact.report.resource_report)
        self.assertEqual((report.profile, report.repertoire_version), (KOREAN, 'applegothic-ko-v1'))
        font, = report.fonts
        self.assertEqual((font.face_index, font.identity), (0, self.font_identity))
        self.assertEqual((font.installed.sha256, font.private.sha256), (cc._hash(self.font_bytes),) * 2)
        self.assertEqual((font.installed.size, font.private.size), (len(self.font_bytes),) * 2)
        self.assertEqual(font.installed.path, str(self.font_path))
        self.assertTrue(font.installed.parents)
        self.assertNotEqual(font.installed.identity, font.private.identity)
        self.assertEqual(artifact.dependency_paths, (self.snapshot.target,))
        self.assertFalse(any(path.exists() for path in observed))
        cc.assert_artifact_current(self.fix, artifact)

    def test_ascii_does_not_resolve_or_capture_fonts(self):
        self.set_source(b'ASCII $alpha_(1)$')
        self.font_path.unlink()
        with mock.patch.object(cc, '_reviewed_resource_artifact', side_effect=AssertionError('no font resolution')):
            artifact = cc.compile_committed_snapshot(self.fix, self.snapshot, profile=ASCII)
        self.assertEqual(artifact.resource_report, cc.ResourceReport(ASCII, 'ascii-v1', ()))
        self.assertNotIn('--font-path', artifact.compilation.argv)
        self.assertFalse((Path(artifact.compilation.cwd).parent / 'fonts').exists())

    def test_input_refused_before_any_compiler_or_font_capture(self):
        for source in ('한', 'α', '😀', '\u3164', '한\ufeff'):
            self.set_source(source.encode())
            with mock.patch.object(cc, '_capture_compiler', side_effect=AssertionError('no compiler access')):
                with self.assertRaises(cc.UnsupportedCompileProfile):
                    self.compile()
        self.resource_lookup.assert_not_called()
        for kwargs in ({'font_path': self.font_path}, {'font_hash': cc._hash(self.font_bytes)},
                       {'font_family': 'OwnedFixture'}, {'fonts': []}, {'approved': True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(TypeError):
                self.compile(**kwargs)

    def test_unavailable_unsafe_wrong_size_hash_and_permissions_refuse_before_probe(self):
        cases = ('missing', 'size', 'hash', 'symlink', 'hardlink', 'writable', 'oversize', 'directory', 'fifo')
        for kind in cases:
            with self.subTest(kind=kind):
                if self.font_path.exists() or self.font_path.is_symlink():
                    self.font_path.unlink()
                extra = self.base / ('other-' + kind)
                if kind == 'directory':
                    self.font_path.mkdir()
                elif kind == 'fifo':
                    os.mkfifo(self.font_path)
                elif kind == 'symlink':
                    extra.write_bytes(self.font_bytes)
                    self.font_path.symlink_to(extra)
                elif kind == 'hardlink':
                    extra.write_bytes(self.font_bytes)
                    self.font_path.hardlink_to(extra)
                elif kind != 'missing':
                    self.font_path.write_bytes(self.font_bytes + b'x' if kind == 'size' else
                        b'x' * len(self.font_bytes) if kind == 'hash' else self.font_bytes)
                    self.font_path.chmod(0o666 if kind == 'writable' else 0o644)
                    if kind == 'oversize':
                        with self.font_path.open('r+b') as sparse:
                            sparse.truncate(cc._MAX_FONT + 1)
                with mock.patch.object(cc, '_run', side_effect=AssertionError('must not probe')):
                    with self.assertRaises(cc.CompileSafetyError) as caught:
                        self.compile()
                self.assertEqual(caught.exception.report.attempts, ())
                self.assertIsNone(caught.exception.report.resource_report)
                if kind == 'directory':
                    self.font_path.rmdir()

    def test_private_registry_platform_compiler_count_face_and_byte_bounds(self):
        # Pure names remain known even where live resources are unavailable.
        from resource_profiles import validate_resource_profile
        self.assertEqual(validate_resource_profile(KOREAN), KOREAN)
        record = self.resources(KOREAN)
        invalid = (replace(record, compiler_sha256='0' * 64), replace(record, fonts=()),
            replace(record, fonts=record.fonts * 2),
            replace(record, fonts=(replace(record.fonts[0], face_index=1),)),
            replace(record, fonts=(replace(record.fonts[0], size=cc._MAX_FONT + 1),)))
        for value in invalid:
            with self.subTest(record=value), mock.patch.object(cc, '_reviewed_resource_artifact', return_value=value):
                with mock.patch.object(cc, '_run', side_effect=AssertionError('no probe')):
                    with self.assertRaises(cc.CompileSafetyError):
                        self.compile()
        for bound in ('_MAX_FONT', '_MAX_FONT_TOTAL'):
            with self.subTest(bound=bound), mock.patch.object(cc, bound, 1):
                with self.assertRaises(cc.CompileSafetyError):
                    self.compile()
        # The private resolver's unpatched function checks live platform only.
        with mock.patch.object(cc, '_RESOURCE_ARTIFACTS', {}):
            with self.assertRaises(cc.CompileSafetyError):
                _REAL_RESOURCE_RESOLVER(KOREAN)
        with self.assertRaises(TypeError):
            cc._RESOURCE_ARTIFACTS[(KOREAN, 'new-platform', 'new-machine')] = record

    def test_font_parent_swap_during_anchored_capture_refuses(self):
        read = os.read
        swapped = False
        held = self.font_path.parent.with_name('held-original')

        def swap(fd, count):
            nonlocal swapped
            if not swapped and os.fstat(fd).st_ino == self.font_path.stat().st_ino:
                swapped = True
                self.font_path.parent.rename(held)
                self.font_path.parent.mkdir(mode=0o700)
                self.font_path.write_bytes(self.font_bytes)
                self.font_path.chmod(0o644)
            return read(fd, count)
        with mock.patch.object(cc.os, 'read', side_effect=swap):
            with self.assertRaises(cc.CompileSafetyError):
                self.compile()
        self.assertTrue(swapped)

    def test_installed_private_and_directory_drift_after_probe_or_compile_refuses(self):
        run = cc._run
        for phase in ('version-probe', 'compile'):
            for kind in ('installed', 'private', 'directory'):
                with self.subTest(phase=phase, kind=kind):
                    self.font_path.write_bytes(self.font_bytes)
                    self.font_path.chmod(0o644)
                    original_report = []

                    def mutate(argv, **kwargs):
                        result = run(argv, **kwargs)
                        if kwargs['purpose'] == phase:
                            original_report.append(kwargs['_ledger'].resource_report)
                            directory = Path(argv[0]).parent.parent / 'fonts'
                            if kind == 'directory':
                                directory.rename(directory.with_name('moved-fonts'))
                                directory.mkdir(mode=0o700)
                            else:
                                self.rewrite(self.font_path if kind == 'installed' else directory / 'AppleGothic.ttf')
                        return result
                    with mock.patch.object(cc, '_run', side_effect=mutate):
                        with self.assertRaises(cc.CompileSafetyError) as caught:
                            self.compile()
                    report = caught.exception.report
                    self.assertIs(report.resource_report, original_report[0])
                    self.assertEqual(report.resource_report.fonts[0].installed.sha256, cc._hash(self.font_bytes))
                    self.assertEqual(report.compiler_status, 0 if phase == 'compile' else None)
                    self.assertEqual(report.safety_gate, 'refused')

    def test_font_drift_after_output_capture_refuses_with_original_facts(self):
        capture = cc._capture
        for kind in ('installed', 'private'):
            with self.subTest(kind=kind):
                self.font_path.write_bytes(self.font_bytes)
                self.font_path.chmod(0o644)
                mutated = False

                def mutate(path, bound, **kwargs):
                    nonlocal mutated
                    result = capture(path, bound, **kwargs)
                    if Path(path).name == 'artifact.pdf' and not mutated:
                        mutated = True
                        private = Path(path).parent.parent / 'fonts' / 'AppleGothic.ttf'
                        self.rewrite(self.font_path if kind == 'installed' else private)
                    return result
                with mock.patch.object(cc, '_capture', side_effect=mutate):
                    with self.assertRaises(cc.CompileSafetyError) as caught:
                        self.compile()
                self.assertTrue(mutated)
                self.assertEqual(caught.exception.report.compiler_status, 0)
                self.assertEqual(caught.exception.report.resource_report.fonts[0].installed.sha256,
                                 cc._hash(self.font_bytes))

    def test_compiler_failure_keeps_font_evidence_raw_status_and_typed_failure(self):
        self.program('sys.exit(9)')
        with self.assertRaises(cc.CompilerFailure) as caught:
            self.compile()
        report = caught.exception.report
        self.assertEqual(report.compiler_status, 9)
        self.assertIs(report.compiler_failure.kind, cc.CompilerFailureKind.PROCESS_EXIT)
        self.assertEqual(report.resource_report.fonts[0].installed.sha256, cc._hash(self.font_bytes))
        run = cc._run

        def mutate_on_failure(argv, **kwargs):
            try:
                return run(argv, **kwargs)
            except cc.CompilerFailure:
                self.rewrite(self.font_path)
                raise
        with mock.patch.object(cc, '_run', side_effect=mutate_on_failure):
            with self.assertRaises(cc.CompileSafetyError) as caught:
                self.compile()
        self.assertEqual(caught.exception.report.compiler_status, 9)
        self.assertIs(caught.exception.report.compiler_failure.kind, cc.CompilerFailureKind.PROCESS_EXIT)
        self.assertEqual(caught.exception.report.safety_gate, 'refused')

    def test_installed_drift_after_cleanup_blocks_issuance(self):
        directory = cc.tempfile.TemporaryDirectory
        owner = self

        class ChangedOnCleanup(directory):
            def __exit__(self, *args):
                result = super().__exit__(*args)
                owner.rewrite(owner.font_path)
                return result
        with mock.patch.object(cc.tempfile, 'TemporaryDirectory', ChangedOnCleanup):
            with self.assertRaises(cc.CompileSafetyError) as caught:
                self.compile()
        self.assertEqual(caught.exception.report.compiler_status, 0)
        self.assertIsNotNone(caught.exception.report.resource_report)

    def test_artifact_live_guard_rechecks_installed_font_after_workspace_cleanup(self):
        artifact = self.compile()
        self.assertFalse(Path(artifact.resource_report.fonts[0].private.path).exists())
        self.rewrite(self.font_path, replace_inode=True)
        for access in (lambda: artifact.pdf_bytes, lambda: asdict(artifact),
                       lambda: cc.assert_artifact_current(self.fix, artifact)):
            with self.assertRaises(cc.CompileSafetyError):
                access()
        self.assertEqual(artifact.report.resource_report.fonts[0].installed.sha256, cc._hash(self.font_bytes))

    def test_every_new_evidence_dataclass_is_frozen_and_recursively_sealed(self):
        for kind in ('report', 'font', 'identity', 'installed', 'private'):
            with self.subTest(kind=kind):
                artifact = self.compile()
                font = artifact.resource_report.fonts[0]
                value, field_name = {
                    'report': (artifact.resource_report, 'repertoire_version'),
                    'font': (font, 'face_index'), 'identity': (font.identity, 'family'),
                    'installed': (font.installed, 'sha256'), 'private': (font.private, 'sha256')}[kind]
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, field_name, 'forged')
                object.__setattr__(value, field_name, 'forged')
                with self.assertRaises(cc.CompileSafetyError):
                    cc.assert_artifact_current(self.fix, artifact)
        artifact = self.compile()
        forged = object.__new__(cc.ClosedCoreCompileArtifact)
        for item in fields(artifact):
            object.__setattr__(forged, item.name, getattr(artifact, item.name))
        with self.assertRaises(cc.CompileSafetyError):
            cc.assert_artifact_current(self.fix, forged)
        self.assertIsNone(cc.CompileReport((), 'pending', None).resource_report)

    def test_unicode_target_is_independent_of_source_repertoire(self):
        self.set_source(b'ASCII only source')
        object.__setattr__(self.snapshot, 'target', '한글 문서/α 😀.typ')
        artifact = self.compile()
        self.assertEqual(artifact.dependency_paths, (self.snapshot.target,))


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1',
                     'Opt in with TYPST_LINT_RENDER_TESTS=1 for private local pinned-font rendering')
@unittest.skipUnless(sys.platform == 'darwin' and integration.EXECUTABLE.is_file()
                     and integration.PINNED_COMPILER.is_file() and shutil.which('pdftotext'),
                     'Requires reviewed Mac resources, pdftotext and syntax adapter')
class NativeCapturedFontTests(integration._FixtureTests):
    def test_exact_modern_hangul_compatibility_jamo_punctuation_and_math(self):
        syllables = ''.join(map(chr, range(0xAC00, 0xD7A4)))
        jamo = ''.join(map(chr, range(0x3131, 0x3164)))
        punctuation = '·–—‘’“”…₩、。〈〉《》「」『』【】'
        # Keep tokens separate, while the captured source remains byte-identical.
        text = '\n'.join(syllables[index:index + 96] for index in range(0, len(syllables), 96))
        source = (text + '\n' + ' '.join(jamo) + '\n' + punctuation +
                  '\n$한 + 글$ $x_(한)+y^(글)$ $x_(한 글)$ $ㄱ+ㄴ+ㅏ$ '
                  '$x_(ㄱ)+y^(ㅏ)$ $x_(한^(글))+(한)/(글)$\n').encode()
        f = self.fixture(source=b'\xef\xbb\xbf' + source, target='임시 문서/한글 source.typ')
        with self.context(f) as fix:
            fix.apply(fix.validate())
            artifact = cc.compile_committed_snapshot(fix, fix.compile_snapshot(),
                profile=KOREAN, deadline_seconds=120)
            self.assertEqual(artifact.input.sha256, cc._hash(b'\xef\xbb\xbf' + source))
            self.assertEqual(artifact.compilation.returncode, 0)
            self.assertEqual(artifact.compilation.stderr, b'')
            font, = artifact.resource_report.fonts
            self.assertEqual(font.installed.sha256,
                '69071005e523e3a2bf276b35b0066f68a7ec0d942c860941ad188ef15ef69c51')
            self.assertEqual((font.installed.size, font.face_index), (15253080, 0))
            self.assertFalse(Path(font.private.path).exists())
            extracted = subprocess.run([shutil.which('pdftotext'), '-layout', '-', '-'],
                input=artifact.pdf_bytes, capture_output=True, timeout=20)
            self.assertEqual(extracted.returncode, 0, extracted.stderr)
            result = extracted.stdout.decode()
            actual_syllables = ''.join(c for c in result if 0xAC00 <= ord(c) <= 0xD7A3)
            self.assertEqual(actual_syllables[:len(syllables)], syllables)
            self.assertEqual(''.join(c for c in result if 0x3131 <= ord(c) <= 0x3163)[:len(jamo)], jamo)
            for character in punctuation:
                self.assertIn(character, result)
            cc.assert_artifact_current(fix, artifact)


if __name__ == '__main__':
    unittest.main()
