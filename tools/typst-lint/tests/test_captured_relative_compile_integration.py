"""Real captured-relative repair/storage -> internal graph compiler adversaries.

Consumers, journals, executables and font bytes belong only to these tests.
The helper executable is not Typst, and no real font/native resource is opened.
"""
from contextlib import ExitStack
import gc
import hashlib
import os
from pathlib import Path
import signal
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import weakref

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import captured_relative_compile as compiler
import captured_relative_core as core
import closed_core_compile as runtime
from graph_compile_test_support import GraphFixture, FONT_BYTES, FONT_IDENTITY, pdf_bytes
from resource_profiles import ASCII_RESOURCE_PROFILE as ASCII, APPLEGOTHIC_RESOURCE_PROFILE as KOREAN
from syntax_client import EXECUTABLE
import source_profiles

ROOT = b'#import "values.typ": count\n#count $x_count$\n'
EXPECTED = ROOT.replace(b'x_count', b'x_(count)')
MODULE = b'#let count = 7\n'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX captured graph compiler')
class CapturedRelativeCompileIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='typst-relative-compile-integration-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.serial = 0

    def fixture(self, *, source=ROOT, modules=None, target='main.typ'):
        self.serial += 1
        sources = {target: source, **({'values.typ': MODULE} if modules is None else modules)}
        f = GraphFixture(self.base / str(self.serial), target=target, sources=sources)
        f.source = f.root / target
        f.source.chmod(0o640)
        for name in sources:
            if name != target:
                (f.root / name).chmod(0o444)
        f.pdf = f.source.with_suffix('.pdf')
        f.pdf.write_bytes(b'%PDF-old-output-sentinel\x00\xff')
        return f

    def compile(self, fix, snapshot=None, *, profile=ASCII, **kwargs):
        return compiler.compile_committed_graph(fix, fix.committed_graph_snapshot()
            if snapshot is None else snapshot, profile=profile, **kwargs)

    def state(self, path):
        value = path.lstat()
        return (path.read_bytes(), value.st_dev, value.st_ino, value.st_mode,
                value.st_uid, value.st_gid, value.st_mtime_ns)

    def states(self, f):
        return {name: self.state(f.root / name) for name in f.sources}

    def events(self, f):
        manifests = tuple(f.store.glob('root-*/*/manifest.json'))
        if not manifests:
            return ()
        self.assertEqual(len(manifests), 1)
        import json
        return tuple(json.loads(path.read_bytes())['kind']
            for path in sorted(manifests[0].parent.glob('event-*.json')))

    def drift(self, path, operation):
        mode = stat.S_IMODE(path.stat().st_mode)
        if operation == 'bytes':
            path.chmod(mode | 0o200)
            path.write_bytes(path.read_bytes() + b'\n')
            path.chmod(mode)
        elif operation == 'inode':
            old = path.stat().st_ino
            replacement = path.with_name(path.name + '.owned-later-save')
            replacement.write_bytes(path.read_bytes())
            replacement.chmod(mode)
            os.replace(replacement, path)
            self.assertNotEqual(path.stat().st_ino, old)
        else:
            path.chmod(mode ^ 0o040)

    def assert_committed(self, f, summary, *, expected=EXPECTED):
        self.assertEqual(f.source.read_bytes(), expected)
        self.assertEqual(summary.modified_paths, (f.target,))
        self.assertEqual(summary.final_sha256, digest(expected))
        self.assertEqual(self.events(f)[-1], 'committed')
        self.assertNotIn('recovery_required', self.events(f))

    def assert_cleaned(self, report):
        for invocation in report.attempts:
            self.assertFalse(Path(invocation.cwd).exists(), 'private workspace survived')

    def test_real_repaired_nested_diamond_and_unused_modules_are_exact_read_only_inputs(self):
        source = (b'#import "../mods/left.typ": renamed as count, txt\n'
                  b'#import "../mods/right.typ": another, same\n'
                  b'#import "../mods/unused.typ": unused\n'
                  b'= Heading #count $a_count$\n'
                  b'- *Strong #txt $x_another/y^same$*\n'
                  b'+ _Emphasis #count $z_(count^2)$_\n')
        modules = {
            'mods/seed.typ': b'#let seed=7\n#let equal=7\n#let txt="(),_+"\n',
            'mods/left.typ': b'#import "seed.typ": seed as original, txt\n#let renamed=original\n',
            'mods/right.typ': b'#import "seed.typ": seed, equal as same\n#let another=seed\n',
            'mods/unused.typ': b'#import "deep/leaf.typ": leaf\n#let unused=leaf\n',
            'mods/deep/leaf.typ': b'#let leaf="unrendered"\n'}
        f = self.fixture(source=source, target='doc/main.typ', modules=modules)
        evidence = {name: self.state(f.root / name) for name in modules}
        sentinels = self.state(f.pdf), self.state(f.unrelated), self.state(f.config), self.state(f.catalog)
        order = tuple(reversed(tuple(f.sources)))
        f.program(dependency_paths=order)
        observed = []
        run = runtime._run

        def inspect(argv, **kwargs):
            if kwargs['purpose'] == 'compile':
                project = Path(kwargs['cwd'])
                copied = {p.relative_to(project).as_posix(): p.read_bytes()
                          for p in project.rglob('*') if p.is_file()}
                observed.append(copied)
                self.assertEqual(copied, dict(snapshot.sources))
                self.assertFalse((project / '.typst-lint.toml').exists())
                self.assertFalse((project / f.unrelated.name).exists())
            return run(argv, **kwargs)

        with f.context() as fix, f.resources(), mock.patch.object(runtime, '_run', side_effect=inspect):
            snapshot = fix.committed_graph_snapshot()
            old = fix._tx.snapshot.sources[f.target]
            self.assertEqual(old, source)
            artifact = self.compile(fix, snapshot)
            compiler.assert_artifact_current(fix, artifact)
            self.assertEqual(artifact.pdf_bytes, pdf_bytes())
            self.assertEqual(artifact.dependency_paths, order)
            self.assertEqual(artifact.source_profile, core.SOURCE_PROFILE)
            self.assertEqual(artifact.root_grammar_version, snapshot.root_grammar_version)
            self.assertEqual(artifact.module_grammar_version, snapshot.module_grammar_version)
            self.assertEqual(artifact.parser_contract, snapshot.parser_contract)
            self.assertEqual(artifact.config_sha256, snapshot.config_sha256)
            self.assertEqual(artifact.catalog_sha256, snapshot.catalog_sha256)
            self.assertEqual({item.path: item.sha256 for item in artifact.sources}, {p: digest(raw) for p, raw in snapshot.sources.items()})
            self.assertEqual(dict(artifact.evidence_hashes), {p: None if raw is None else digest(raw)
                                                           for p, raw in snapshot.evidence.items()})
            self.assertEqual(len(observed), 1)
            self.assertEqual(artifact.report.safety_gate, 'passed')
            self.assertEqual(artifact.report.compiler_status, 0)
            self.assertEqual(artifact.report.resource_report, artifact.resource_report)
            self.assert_cleaned(artifact.report)
            for item in artifact.sources:
                self.assertEqual(item.sha256, digest(snapshot.sources[item.path]))
                self.assertEqual(item.size, len(snapshot.sources[item.path]))
                self.assertEqual(item.private.sha256, item.sha256)
                self.assertEqual(item.private.size, item.size)
                self.assertFalse(Path(item.private.path).exists())
            self.assertEqual({item.path for item in artifact.sources}, set(snapshot.sources))
            self.assertEqual(artifact.dependency_bytes, b"".join(p.encode() + b"\0" for p in order))
            self.assertEqual(artifact.aggregate_source_bytes, sum(map(len, snapshot.sources.values())))
            self.assertEqual(artifact.scalar_budget.use_count, len(snapshot.proof.imported_uses))
            self.assertEqual(fix.summary.modified_paths, (f.target,))
        self.assertEqual({name: self.state(f.root / name) for name in modules}, evidence)
        self.assertEqual((self.state(f.pdf), self.state(f.unrelated), self.state(f.config), self.state(f.catalog)), sentinels)

    def test_actual_noop_receipts_compile_without_journal_or_inode_change(self):
        cases = ((b'', {}), (b'Plain ASCII prose.\n', {}),
                 (EXPECTED, {'values.typ': MODULE}))
        for source, modules in cases:
            with self.subTest(source=source):
                f = self.fixture(source=source, modules=modules)
                original = self.states(f)
                with f.context() as fix, f.resources():
                    summary = fix.summary
                    self.assertEqual(summary.modified_paths, ())
                    self.assertIsNone(summary.journal_id)
                    artifact = self.compile(fix)
                    self.assertEqual(artifact.pdf_bytes, pdf_bytes())
                    self.assertEqual(artifact.scalar_budget.use_count, 2 if modules else 0)
                    self.assert_cleaned(artifact.report)
                self.assertEqual(self.states(f), original)
                self.assertEqual(self.events(f), ())

    def test_bom_crlf_and_necessary_parent_import_bytes_are_not_normalized(self):
        source = b'\xef\xbb\xbf#import "../values.typ": count\r\n#count $x_count$\r\n'
        expected = source.replace(b'x_count', b'x_(count)')
        f = self.fixture(source=source, target='doc/main.typ')
        seen = []
        run = runtime._run

        def inspect(argv, **kwargs):
            if kwargs['purpose'] == 'compile':
                seen.append(Path(argv[-2]).read_bytes())
            return run(argv, **kwargs)

        with f.context() as fix, f.resources(), mock.patch.object(runtime, '_run', side_effect=inspect):
            artifact = self.compile(fix)
            self.assertEqual(seen, [expected])
            self.assertEqual(artifact.dependency_paths, tuple(f.sources))
            self.assert_committed(f, fix.summary, expected=expected)

    def test_unicode_target_filename_is_not_source_repertoire_and_ascii_never_reads_fonts(self):
        target = '한글 e\u0301 😀/ASCII source.typ'
        f = self.fixture(source=b'ASCII $x_i$\n', target=target, modules={})
        f.font.unlink()
        with f.context() as fix, f.resources(), \
                mock.patch.object(runtime, '_reviewed_resource_artifact', side_effect=AssertionError('ASCII consulted fonts')):
            artifact = self.compile(fix)
            self.assertEqual(artifact.target, target)
            self.assertEqual(artifact.resource_report.fonts, ())
            self.assertEqual(artifact.dependency_paths, (target,))
            self.assert_committed(f, fix.summary, expected=b'ASCII $x_(i)$\n')

    def test_ascii_checks_dependency_only_unicode_even_when_initializer_is_unused(self):
        cases = (
            (ROOT, {'values.typ': '#let count=7\n#let unused="한글"\n'.encode()}),
            (ROOT, {'values.typ': b'#import "deep.typ": other\n#let count=7\n',
                    'deep.typ': '#let other="한글"\n'.encode()}),
            ('#import "한글.typ": count\n#count $x_count$\n'.encode(), {'한글.typ': MODULE}),
        )
        for source, modules in cases:
            with self.subTest(source=source, modules=tuple(modules)):
                f = self.fixture(source=source, modules=modules)
                with f.context() as fix, ExitStack() as stack:
                    summary = fix.summary
                    for name in ('_capture_compiler', '_capture_fonts', '_run'):
                        stack.enter_context(mock.patch.object(runtime, name,
                            side_effect=AssertionError('repertoire refusal reached ' + name)))
                    with self.assertRaises(runtime.UnsupportedCompileProfile):
                        self.compile(fix)
                    expected = source.replace(b'x_count', b'x_(count)')
                    self.assert_committed(f, summary, expected=expected)
                    self.assertIs(fix.summary, summary)

    def test_synthetic_korean_profile_checks_every_source_and_retains_actual_font_evidence(self):
        source = '#import "한글.typ": count\n#count $x_count$\n'.encode()
        module = '#let count=7\n#let unused="한글 · ₩"\n'.encode()
        f = self.fixture(source=source, modules={'한글.typ': module})
        module_state = self.state(f.root / '한글.typ')
        with f.context() as fix, f.resources(korean=True):
            artifact = self.compile(fix, profile=KOREAN)
            self.assertEqual(artifact.profile, KOREAN)
            self.assertEqual(artifact.report.resource_report, artifact.resource_report)
            self.assertEqual(len(artifact.resource_report.fonts), 1)
            font = artifact.resource_report.fonts[0]
            self.assertEqual(font.identity, FONT_IDENTITY)
            self.assertEqual(font.installed.path, str(f.font))
            self.assertEqual(font.installed.sha256, digest(FONT_BYTES))
            self.assertEqual(font.private.sha256, digest(FONT_BYTES))
            self.assertNotEqual(font.private.identity[:2], font.installed.identity[:2])
            self.assertFalse(Path(font.private.path).exists())
            self.assertEqual(artifact.pdf_bytes, pdf_bytes())
            self.assertEqual(artifact.scalar_budget.total_charge, 2)
            self.assertEqual(self.state(f.root / '한글.typ'), module_state)
            self.assert_committed(f, fix.summary, expected=source.replace(b'x_count', b'x_(count)'))

    def test_korean_profile_refuses_unrendered_unsupported_dependency_before_resources(self):
        for text in ('😀', '한', 'café', '\u3164'):
            with self.subTest(text=text):
                f = self.fixture(modules={'values.typ': ('#let count=7\n#let unused="' + text + '"\n').encode()})
                with f.context() as fix, ExitStack() as stack:
                    for name in ('_capture_compiler', '_capture_fonts', '_run'):
                        stack.enter_context(mock.patch.object(runtime, name,
                            side_effect=AssertionError('repertoire refusal reached ' + name)))
                    with self.assertRaises(runtime.UnsupportedCompileProfile):
                        self.compile(fix, profile=KOREAN)
                    self.assert_committed(f, fix.summary)

    def test_scalar_amplification_refusal_preserves_actual_commit_before_resource_or_workspace_work(self):
        payload = b'q' * 65536
        source = b'#import "values.typ": text\n' + b'#text ' * 17 + b'$x_i$\n'
        f = self.fixture(source=source, modules={'values.typ': b'#let text="' + payload + b'"\n'})
        before = self.state(f.root / 'values.typ')
        with f.context() as fix, ExitStack() as stack:
            summary = fix.summary
            snapshot = fix.committed_graph_snapshot()
            self.assertEqual(len(snapshot.proof.imported_uses), 17)
            for name in ('_capture_compiler', '_capture_fonts', '_run'):
                stack.enter_context(mock.patch.object(runtime, name,
                    side_effect=AssertionError('scalar admission reached ' + name)))
            stack.enter_context(mock.patch.object(runtime.tempfile, 'TemporaryDirectory',
                side_effect=AssertionError('scalar admission created workspace')))
            with self.assertRaises(runtime.CompileSafetyError) as caught:
                self.compile(fix, snapshot)
            self.assertEqual(caught.exception.report.attempts, ())
            self.assertIsNone(caught.exception.report.resource_report)
            self.assertEqual(caught.exception.report.safety_gate, 'refused')
            self.assertIs(fix.summary, summary)
            fix.assert_committed_graph_current(snapshot)
            self.assert_committed(f, summary, expected=source.replace(b'x_i', b'x_(i)'))
            self.assertEqual(self.state(f.root / 'values.typ'), before)

    def test_unused_large_scalar_has_no_render_charge_but_remains_in_dependency_evidence(self):
        unused = b'owned unused scalar ' * 4096
        f = self.fixture(modules={'values.typ': b'#let count=7\n#let unused="' + unused + b'"\n'})
        with f.context() as fix, f.resources():
            artifact = self.compile(fix)
            self.assertEqual((artifact.scalar_budget.total_charge, artifact.scalar_budget.use_count,
                              artifact.scalar_budget.origin_count), (2, 2, 1))
            self.assertIn('values.typ', {item.path: item.sha256 for item in artifact.sources})
            self.assertEqual(artifact.pdf_bytes, pdf_bytes())
            self.assert_committed(f, fix.summary)

    def test_postcommit_every_input_byte_inode_mode_drift_refuses_before_any_resource_capture(self):
        for role in ('source', 'dependency', 'config', 'catalog'):
            for operation in ('bytes', 'inode', 'mode'):
                with self.subTest(role=role, operation=operation):
                    f = self.fixture()
                    with f.context() as fix, ExitStack() as stack:
                        snapshot = fix.committed_graph_snapshot()
                        summary = fix.summary
                        path = f.root / 'values.typ' if role == 'dependency' else getattr(f, role)
                        self.drift(path, operation)
                        current = self.state(path)
                        for name in ('_capture_compiler', '_capture_fonts', '_run'):
                            stack.enter_context(mock.patch.object(runtime, name,
                                side_effect=AssertionError('stale input reached ' + name)))
                        with self.assertRaises(runtime.CompileSafetyError) as caught:
                            self.compile(fix, snapshot)
                        self.assertEqual(caught.exception.report.attempts, ())
                        self.assertIsNone(caught.exception.report.resource_report)
                        self.assertEqual(self.state(path), current)
                        self.assertIs(fix.summary, summary)
                        self.assertEqual(summary.modified_paths, (f.target,))
                    self.assertEqual(self.state(path), current)
                    self.assertEqual(self.events(f)[-1], 'committed')
                    if role != 'source' or operation != 'bytes':
                        self.assertEqual(f.source.read_bytes(), EXPECTED)

    def test_replaced_higher_ancestors_cannot_refresh_committed_baseline(self):
        for phase in ('before_compile', 'artifact_access'):
            for role in ('source', 'dependency', 'catalog'):
                with self.subTest(phase=phase, role=role):
                    f = self.fixture(source=ROOT.replace(b'values.typ', b'../mods/sub/values.typ'),
                        target='doc/main.typ', modules={'mods/sub/values.typ': MODULE})
                    if role == 'catalog':
                        directory = f.base / 'catalog-parent' / 'sub'
                        directory.mkdir(parents=True)
                        f.catalog.rename(directory / 'catalog.json')
                        f.catalog = directory / 'catalog.json'
                    with f.context() as fix, f.resources():
                        snapshot = fix.committed_graph_snapshot()
                        artifact = self.compile(fix, snapshot) if phase == 'artifact_access' else None
                        ancestor, child = ((f.root / 'mods', 'sub') if role == 'dependency' else
                            (f.base / 'catalog-parent', 'sub') if role == 'catalog' else (f.base, 'consumer'))
                        old = ancestor.with_name(ancestor.name + '-old-ancestor')
                        before = (ancestor / child).stat().st_ino
                        ancestor.rename(old)
                        ancestor.mkdir()
                        (old / child).rename(ancestor / child)
                        if role == 'source':
                            for path in tuple(old.iterdir()):
                                path.rename(ancestor / path.name)
                        self.assertEqual((ancestor / child).stat().st_ino, before)
                        with mock.patch.object(runtime, '_capture_compiler', side_effect=AssertionError('ancestor drift captured compiler')):
                            with self.assertRaises(runtime.CompileSafetyError) as caught:
                                if artifact is None:
                                    self.compile(fix, snapshot)
                                else:
                                    compiler.assert_artifact_current(fix, artifact)
                        if artifact is None:
                            self.assertEqual(caught.exception.report.attempts, ())
                        else:
                            with self.assertRaises(runtime.CompileSafetyError):
                                _ = artifact.pdf_bytes
                            self.assertEqual(artifact.report.compiler_status, 0)
                            self.assert_cleaned(artifact.report)
                        self.assert_committed(f, fix.summary, expected=EXPECTED.replace(b'values.typ', b'../mods/sub/values.typ'))

    def test_after_real_compile_success_later_input_drift_retains_observed_success_and_resources(self):
        for role in ('source', 'dependency', 'config', 'catalog'):
            with self.subTest(role=role):
                f = self.fixture()
                run = runtime._run
                current = []

                def mutate_after_compile(argv, **kwargs):
                    result = run(argv, **kwargs)
                    if kwargs['purpose'] == 'compile':
                        path = f.root / 'values.typ' if role == 'dependency' else getattr(f, role)
                        self.drift(path, 'bytes')
                        current.append((path, self.state(path)))
                    return result

                with f.context() as fix, f.resources(korean=True), mock.patch.object(runtime, '_run', side_effect=mutate_after_compile):
                    snapshot = fix.committed_graph_snapshot()
                    summary = fix.summary
                    with self.assertRaises(runtime.CompileSafetyError) as caught:
                        self.compile(fix, snapshot, profile=KOREAN)
                    report = caught.exception.report
                    self.assertEqual(report.safety_gate, 'refused')
                    self.assertEqual(report.compiler_status, 0)
                    self.assertTrue(report.compiler_invoked)
                    self.assertEqual(tuple(a.purpose for a in report.attempts), ('version-probe', 'compile'))
                    self.assertTrue(all(a.started is True and a.lifecycle == 'exited' for a in report.attempts))
                    self.assertEqual(len(report.resource_report.fonts), 1)
                    self.assertEqual(report.resource_report.fonts[0].installed.sha256, digest(FONT_BYTES))
                    self.assertIsNone(report.compiler_failure)
                    self.assert_cleaned(report)
                    self.assertIs(fix.summary, summary)
                    self.assertEqual(self.state(current[0][0]), current[0][1])
                self.assertEqual(self.state(current[0][0]), current[0][1])
                self.assertEqual(self.events(f)[-1], 'committed')

    def test_running_child_original_input_and_resource_drift_is_caught_before_timeout(self):
        for role in ('source', 'dependency', 'config', 'catalog', 'compiler', 'font'):
            with self.subTest(role=role):
                f = self.fixture()
                path = f.root / 'values.typ' if role == 'dependency' else getattr(f, role)
                marker = f.base / 'owned-child-mutated'
                behavior = (f'pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\n'
                    f'fd=os.open({str(path)!r}, os.O_WRONLY | os.O_APPEND)\n'
                    'assert os.write(fd, b"\\n") == 1\n'
                    'os.close(fd)\n'
                    'time.sleep(30)')
                f.program(behavior)
                # Capture an owner-writable baseline, so the child need only
                # append one byte and cannot be killed between chmod/write.
                path.chmod(stat.S_IMODE(path.stat().st_mode) | 0o200)
                with f.context() as fix, f.resources(korean=True):
                    snapshot = fix.committed_graph_snapshot()
                    summary = fix.summary
                    before = path.read_bytes()
                    before_stat = path.stat()
                    with self.assertRaises(runtime.CompileSafetyError) as caught:
                        self.compile(fix, snapshot, profile=KOREAN, deadline_seconds=5)
                    report = caught.exception.report
                    self.assertTrue(marker.is_file(), 'child never reached mutation handshake')
                    child_pid = int(marker.read_text())
                    self.assertGreater(child_pid, 0)
                    self.assertEqual(path.read_bytes(), before + b'\n')
                    after_stat = path.stat()
                    self.assertEqual((after_stat.st_dev, after_stat.st_ino, after_stat.st_mode),
                                     (before_stat.st_dev, before_stat.st_ino, before_stat.st_mode))
                    self.assertEqual(report.safety_gate, 'refused')
                    self.assertEqual(report.compiler_status, -signal.SIGKILL)
                    self.assertEqual(report.attempts[-1].purpose, 'compile')
                    self.assertTrue(report.attempts[-1].started)
                    self.assertEqual(report.attempts[-1].lifecycle, 'signaled')
                    self.assertEqual(report.attempts[-1].termination_reason, 'safety refusal')
                    self.assertIsNone(report.compiler_failure, 'drift was discovered only after timeout')
                    with self.assertRaises(ChildProcessError):
                        os.waitpid(child_pid, os.WNOHANG)
                    self.assertEqual(len(report.resource_report.fonts), 1)
                    self.assert_cleaned(report)
                    self.assertIs(fix.summary, summary)
                    self.assertEqual(self.events(f)[-1], 'committed')
                    if role != 'source':
                        self.assert_committed(f, summary)

    def test_nonzero_and_signal_have_real_attempt_status_resources_and_no_source_rollback(self):
        cases = (('os.write(2, b"owned graph diagnostic\\n"); raise SystemExit(19)', 19,
                  runtime.CompilerFailureKind.PROCESS_EXIT, 'exited'),
                 ('os.kill(os.getpid(), signal.SIGTERM)', -signal.SIGTERM,
                  runtime.CompilerFailureKind.PROCESS_EXIT, 'signaled'))
        for behavior, status, kind, lifecycle in cases:
            with self.subTest(status=status):
                f = self.fixture()
                f.program(behavior)
                sentinel = self.state(f.pdf), self.state(f.unrelated), self.state(f.root / 'values.typ')
                with f.context() as fix, f.resources(korean=True):
                    with self.assertRaises(runtime.CompilerFailure) as caught:
                        self.compile(fix, profile=KOREAN)
                    failure = caught.exception
                    report = failure.report
                    self.assertEqual(failure.kind, kind)
                    self.assertEqual(report.safety_gate, 'passed')
                    self.assertEqual(report.compiler_status, status)
                    self.assertTrue(report.compiler_invoked)
                    self.assertEqual(report.attempts[-1].returncode, status)
                    self.assertEqual(report.attempts[-1].lifecycle, lifecycle)
                    self.assertTrue(report.attempts[-1].launch_attempted)
                    self.assertIs(report.attempts[-1].started, True)
                    self.assertEqual(report.compiler_failure.kind, kind)
                    self.assertEqual(report.compiler_failure.invocation.returncode, status)
                    self.assertEqual(len(report.resource_report.fonts), 1)
                    self.assert_cleaned(report)
                    self.assert_committed(f, fix.summary)
                self.assertEqual((self.state(f.pdf), self.state(f.unrelated), self.state(f.root / 'values.typ')), sentinel)

    def test_actual_nonzero_plus_input_drift_keeps_failure_when_safety_refusal_takes_precedence(self):
        f = self.fixture()
        path = f.root / 'values.typ'
        f.program('os.write(2, b"owned graph failure\\n"); raise SystemExit(23)')
        run = runtime._run

        def mutate_after_observed_failure(argv, **kwargs):
            try:
                return run(argv, **kwargs)
            except runtime.CompilerFailure:
                if kwargs['purpose'] == 'compile':
                    mode = stat.S_IMODE(path.stat().st_mode)
                    path.chmod(mode | 0o200)
                    path.write_bytes(path.read_bytes() + b'Later external edit\n')
                    path.chmod(mode)
                raise

        with f.context() as fix, f.resources(korean=True), \
                mock.patch.object(runtime, '_run', side_effect=mutate_after_observed_failure):
            snapshot = fix.committed_graph_snapshot()
            summary = fix.summary
            with self.assertRaises(runtime.CompileSafetyError) as caught:
                self.compile(fix, snapshot, profile=KOREAN)
            report = caught.exception.report
            self.assertEqual(report.safety_gate, 'refused')
            self.assertEqual(report.compiler_status, 23)
            self.assertEqual(report.compiler_failure.kind, runtime.CompilerFailureKind.PROCESS_EXIT)
            self.assertEqual(report.compiler_failure.invocation.stderr, b'owned graph failure\n')
            self.assertEqual(len(report.resource_report.fonts), 1)
            self.assert_cleaned(report)
            self.assertEqual(path.read_bytes(), MODULE + b'Later external edit\n')
            self.assert_committed(f, summary)

    def test_handshaken_cancellation_records_owned_child_cleanup_without_erasing_commit(self):
        f = self.fixture()
        marker = f.base / 'owned-child-started'
        f.program(f'pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\ntime.sleep(30)')
        cancel = threading.Event()
        observed = []

        def cancel_after_handshake():
            until = time.monotonic() + 8
            while time.monotonic() < until and not marker.exists():
                time.sleep(0.005)
            observed.append(marker.exists())
            cancel.set()

        thread = threading.Thread(target=cancel_after_handshake, daemon=True)
        with f.context() as fix, f.resources():
            thread.start()
            try:
                with self.assertRaises(runtime.CompilerFailure) as caught:
                    self.compile(fix, deadline_seconds=10, cancel_event=cancel)
            finally:
                thread.join(timeout=9)
            self.assertFalse(thread.is_alive())
            self.assertEqual(observed, [True])
            report = caught.exception.report
            self.assertEqual(caught.exception.kind, runtime.CompilerFailureKind.CANCELLED)
            self.assertEqual(report.compiler_status, -signal.SIGKILL)
            self.assertEqual(report.attempts[-1].lifecycle, 'signaled')
            self.assertIs(report.attempts[-1].started, True)
            self.assertEqual(report.resource_report.fonts, ())
            self.assert_cleaned(report)
            self.assert_committed(f, fix.summary)

    def test_real_timeout_keeps_actual_child_status_and_resource_report(self):
        f = self.fixture()
        marker = f.base / 'owned-timeout-child-started'
        f.program(f'pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\ntime.sleep(30)')
        with f.context() as fix, f.resources(korean=True):
            with self.assertRaises(runtime.CompilerFailure) as caught:
                self.compile(fix, profile=KOREAN, deadline_seconds=3)
            report = caught.exception.report
            self.assertTrue(marker.is_file(), 'deadline expired before child handshake')
            self.assertEqual(caught.exception.kind, runtime.CompilerFailureKind.TIMEOUT)
            self.assertEqual(report.compiler_status, -signal.SIGKILL)
            self.assertEqual(report.attempts[-1].purpose, 'compile')
            self.assertIs(report.attempts[-1].started, True)
            self.assertEqual(report.attempts[-1].lifecycle, 'signaled')
            self.assertEqual(report.attempts[-1].termination_reason, 'timeout')
            self.assertEqual(report.compiler_failure.kind, runtime.CompilerFailureKind.TIMEOUT)
            self.assertEqual(len(report.resource_report.fonts), 1)
            self.assert_cleaned(report)
            self.assert_committed(f, fix.summary)

    def test_artifact_access_rechecks_every_original_input_after_private_cleanup(self):
        for role in ('source', 'dependency', 'config', 'catalog'):
            for operation in ('bytes', 'inode', 'mode'):
                with self.subTest(role=role, operation=operation):
                    f = self.fixture()
                    with f.context() as fix, f.resources():
                        artifact = self.compile(fix)
                        self.assertEqual(artifact.pdf_bytes, pdf_bytes())
                        self.assert_cleaned(artifact.report)
                        path = f.root / 'values.typ' if role == 'dependency' else getattr(f, role)
                        self.drift(path, operation)
                        current = self.state(path)
                        with self.assertRaises(runtime.CompileSafetyError):
                            compiler.assert_artifact_current(fix, artifact)
                        with self.assertRaises(runtime.CompileSafetyError):
                            _ = artifact.pdf_bytes
                        self.assertEqual(self.state(path), current)
                        self.assertEqual(fix.summary.modified_paths, (f.target,))
                    self.assertEqual(self.state(path), current)

    def test_later_original_resource_drift_rejects_artifact_without_refreshing_historical_report(self):
        for role in ('compiler', 'font'):
            for operation in ('bytes', 'inode', 'mode'):
                with self.subTest(role=role, operation=operation):
                    f = self.fixture()
                    with f.context() as fix, f.resources(korean=True):
                        artifact = self.compile(fix, profile=KOREAN)
                        report = artifact.report
                        fonts = artifact.resource_report
                        path = getattr(f, role)
                        self.drift(path, operation)
                        with self.assertRaises(runtime.CompileSafetyError):
                            compiler.assert_artifact_current(fix, artifact)
                        with self.assertRaises(runtime.CompileSafetyError):
                            _ = artifact.pdf_bytes
                        self.assertIs(artifact.report, report)
                        self.assertIs(artifact.resource_report, fonts)
                        self.assertEqual(report.compiler_status, 0)
                        self.assertEqual(fonts.fonts[0].installed.sha256, digest(FONT_BYTES))
                        self.assert_committed(f, fix.summary)

    def test_artifact_expires_on_bridge_close_and_does_not_keep_owner_alive(self):
        f = self.fixture()
        with f.context() as fix, f.resources():
            artifact = self.compile(fix)
            owner = weakref.ref(fix)
            self.assertEqual(artifact.pdf_bytes, pdf_bytes())
        with self.assertRaises(runtime.CompileSafetyError):
            compiler.assert_artifact_current(fix, artifact)
        with self.assertRaises(runtime.CompileSafetyError):
            _ = artifact.pdf_bytes
        del fix
        gc.collect()
        self.assertIsNone(owner(), 'artifact retained the offline bridge')
        with self.assertRaises(runtime.CompileSafetyError):
            _ = artifact.pdf_bytes

    def test_collecting_context_does_not_leave_artifact_as_live_authority(self):
        f = self.fixture()
        context = f.context()
        fix = context.__enter__()
        with f.resources():
            artifact = self.compile(fix)
        owner = weakref.ref(fix)
        del fix, context
        gc.collect()
        self.assertIsNone(owner(), 'artifact retained an abandoned offline context')
        with self.assertRaises(runtime.CompileSafetyError):
            _ = artifact.pdf_bytes
        self.assertEqual(f.source.read_bytes(), EXPECTED)
        self.assertEqual(self.events(f)[-1], 'committed')

    def test_foreign_live_bridge_cannot_use_other_real_graph_or_artifact(self):
        f, other = self.fixture(), self.fixture()
        with f.context() as fix, other.context() as foreign, f.resources():
            snapshot = fix.committed_graph_snapshot()
            artifact = self.compile(fix, snapshot)
            with self.assertRaises(runtime.CompileSafetyError) as caught:
                self.compile(foreign, snapshot)
            self.assertEqual(caught.exception.report.attempts, ())
            with self.assertRaises(runtime.CompileSafetyError):
                compiler.assert_artifact_current(foreign, artifact)
            self.assertEqual(artifact.pdf_bytes, pdf_bytes())
            self.assert_committed(f, fix.summary)
            self.assert_committed(other, foreign.summary)

    def test_internal_identity_is_still_rejected_by_existing_compiler_and_public_registry(self):
        f = self.fixture()
        with f.context() as fix:
            snapshot = fix.committed_graph_snapshot()
            with mock.patch.object(runtime, '_capture_compiler', side_effect=AssertionError('legacy compiler accepted graph')):
                with self.assertRaises(runtime.CompileSafetyError) as caught:
                    runtime.compile_committed_snapshot(fix, snapshot, profile=ASCII)
            self.assertEqual(caught.exception.report.attempts, ())
            self.assertNotIn(core.SOURCE_PROFILE, source_profiles.KNOWN_SOURCE_PROFILES)
            with self.assertRaises(ValueError):
                source_profiles.validate_source_profile(core.SOURCE_PROFILE)
            self.assert_committed(f, fix.summary)


if __name__ == '__main__':
    unittest.main()
