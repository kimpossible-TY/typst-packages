"""Deterministic adapter tests, no native compiler or parser installation needed.

Bridge guards are mocked at their trusted seam; real bridge/provenance integration
lives in test_committed_compile_integration.py. Every executable here is an owned
temporary Python fixture registered only through a patched private review seam.
"""
from dataclasses import FrozenInstanceError, asdict, astuple
import copy
import gc
import os
import re
from pathlib import Path
import signal
import sys
import tempfile
import threading
from types import MappingProxyType
import unittest
import weakref
from unittest import mock

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import closed_core_compile as cc
from protected_fix import ClosedCoreCompileSnapshot, ProtectedCoreFix
from transactions import TransactionError


def pdf_bytes():
    start = b'%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n'
    return (start + b'xref\n0 1\n0000000000 65535 f \ntrailer\n<<>>\nstartxref\n'
            + str(len(start)).encode() + b'\n%%EOF\n')


class CompilerAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.fix = object.__new__(ProtectedCoreFix)
        self.snapshot = object.__new__(ClosedCoreCompileSnapshot)
        self.active = True
        self.guard_calls = 0
        self.set_source(b'ASCII prose. $alpha_(1) + x^(2)$\n')
        values = dict(root=self.base, target='nested with spaces/input file.typ',
            evidence=MappingProxyType({'config': b'config', 'catalog': b'catalog'}),
            config_sha256=cc._hash(b'config'), catalog_sha256=cc._hash(b'catalog'))
        for key, value in values.items():
            object.__setattr__(self.snapshot, key, value)

        def guard(fix, snapshot):
            self.guard_calls += 1
            if fix is not self.fix or snapshot is not self.snapshot or not self.active:
                raise TransactionError('expired/stale test authority')
        self.guard_patch = mock.patch.object(ProtectedCoreFix, 'assert_compile_current', guard)
        self.guard_patch.start()
        self.addCleanup(self.guard_patch.stop)
        self.executable = self.base / 'owned-test-compiler'
        self.program()
        self.registry = mock.patch.object(cc, '_reviewed_native_artifact', side_effect=self.reviewed)
        self.registry.start()
        self.addCleanup(self.registry.stop)

    def set_source(self, source):
        object.__setattr__(self.snapshot, 'source', source)
        object.__setattr__(self.snapshot, 'source_sha256', cc._hash(source))

    def program(self, behavior=''):
        raw = (f'#!{sys.executable}\n'
            'import os, pathlib, signal, sys, time\n'
            'if sys.argv[1:] == ["--version"]:\n'
            f'    os.write(1, {cc._VERSION!r}); sys.exit(0)\n'
            'assert sys.argv[1] == "compile"\n'
            'input = pathlib.Path(sys.argv[-2])\n'
            'output = pathlib.Path(sys.argv[-1])\n'
            'deps = pathlib.Path(sys.argv[sys.argv.index("--deps") + 1])\n'
            f'{behavior}\n'
            f'output.write_bytes({pdf_bytes()!r})\n'
            'deps.write_bytes(os.fsencode(input.relative_to(pathlib.Path.cwd())) + b"\\0")\n').encode()
        if self.executable.exists():
            self.executable.chmod(0o700)
        self.executable.write_bytes(raw)
        self.executable.chmod(0o500)
        self.reviewed_hash = cc._hash(raw)

    def reviewed(self):
        return cc._ReviewedNativeArtifact(self.executable, self.reviewed_hash, cc._VERSION)

    def compile(self, **kwargs):
        return cc.compile_committed_snapshot(self.fix, self.snapshot,
            profile=cc.RESOURCE_PROFILE, **kwargs)

    def test_success_exact_source_paths_argv_environment_and_private_cleanup(self):
        run = cc._run
        captured = []

        def inspect(argv, **kwargs):
            self.assertIsNone(kwargs['cancel_event'])
            if kwargs['purpose'] == 'compile':
                source = Path(argv[-2])
                self.assertEqual(source.read_bytes(), self.snapshot.source)
                self.assertEqual(source.relative_to(kwargs['cwd']).as_posix(), self.snapshot.target)
                self.assertNotEqual(kwargs['cwd'], self.snapshot.root)
                self.assertEqual(kwargs['environment'], cc._environment(source.parents[2]))
                captured.append(source)
            return run(argv, **kwargs)

        with mock.patch.object(cc, '_run', side_effect=inspect), mock.patch.dict(os.environ, {
                'TYPST_FEATURES': 'html', 'TYPST_FONT_PATHS': '/untrusted',
                'SOURCE_DATE_EPOCH': 'now', 'DYLD_INSERT_LIBRARIES': '/evil',
                'https_proxy': 'untrusted', 'PATH': '/evil'}):
            artifact = self.compile()
        self.assertEqual(artifact.pdf_bytes, pdf_bytes())
        self.assertEqual(artifact.pdf_sha256, cc._hash(pdf_bytes()))
        self.assertEqual(artifact.source_sha256, self.snapshot.source_sha256)
        self.assertEqual(artifact.compiler.sha256, self.reviewed_hash)
        self.assertEqual(artifact.private_compiler.sha256, self.reviewed_hash)
        self.assertEqual(artifact.compilation.returncode, 0)
        self.assertEqual(len(artifact.probes), 1)
        self.assertEqual(artifact.probes[0].purpose, 'version-probe')
        argv = artifact.compilation.argv
        self.assertEqual(argv[1:-2], ('compile', '--format', 'pdf', '--root', artifact.compilation.cwd,
            '--ignore-system-fonts', '--package-path', str(captured[0].parents[2] / 'packages'),
            '--package-cache-path', str(captured[0].parents[2] / 'package-cache'),
            '--creation-timestamp', '0', '--jobs', '1', '--diagnostic-format', 'short',
            '--deps', artifact.dependencies.path, '--deps-format', 'zero'))
        self.assertFalse(captured[0].exists())
        self.assertFalse(Path(artifact.private_compiler.path).exists())
        self.assertGreaterEqual(self.guard_calls, 10)
        with self.assertRaises(FrozenInstanceError):
            artifact.target = 'forged.typ'

    def test_profile_required_and_no_public_bypass_parameters(self):
        with self.assertRaises(TypeError):
            cc.compile_committed_snapshot(self.fix, self.snapshot)
        for kwargs in ({'compiler': self.executable}, {'extra_args': ['--open']},
                       {'environment': {}}, {'approved': True}, {'output': 'user.pdf'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(TypeError):
                self.compile(**kwargs)
        for profile in ('closed-core-v1', '', None, 'ordinary'):
            with self.subTest(profile=profile), self.assertRaises(cc.UnsupportedCompileProfile):
                cc.compile_committed_snapshot(self.fix, self.snapshot, profile=profile)

    def test_unicode_rejected_before_compiler_capture_and_optional_bom_accepted(self):
        for raw in ('한글', '$α_1$', 'ASCII\ufeff', '\u00a0'):
            self.set_source(raw.encode())
            with mock.patch.object(cc, '_capture_compiler', side_effect=AssertionError('must not probe')):
                with self.assertRaises(cc.UnsupportedCompileProfile):
                    self.compile()
        self.set_source(b'\xef\xbb\xbfASCII $alpha_(1)$')
        self.assertEqual(self.compile().source_sha256, self.snapshot.source_sha256)

    def test_invalid_deadlines_and_cancellation_objects(self):
        for deadline in (False, 0, -1, float('nan'), float('inf'), 121, '1'):
            with self.subTest(deadline=deadline), self.assertRaises(cc.UnsupportedCompileProfile):
                self.compile(deadline_seconds=deadline)
        with self.assertRaises(cc.UnsupportedCompileProfile):
            self.compile(cancel_event=object())

    def test_foreign_missing_and_expired_authority_refused_before_compiler(self):
        for fix, snapshot in ((object(), self.snapshot), (self.fix, {}),
                              (self.fix, object.__new__(ClosedCoreCompileSnapshot))):
            with self.subTest(snapshot=type(snapshot)), self.assertRaises(cc.CompileSafetyError):
                cc.compile_committed_snapshot(fix, snapshot, profile=cc.RESOURCE_PROFILE)
        self.active = False
        with self.assertRaises(cc.CompileSafetyError):
            self.compile()

    def test_missing_and_wrong_hash_refuse_without_any_execution(self):
        self.executable.unlink()
        with mock.patch.object(cc, '_run', side_effect=AssertionError('must not execute')):
            with self.assertRaises(cc.CompileSafetyError):
                self.compile()
        self.program()
        self.executable.chmod(0o700)
        self.executable.write_bytes(b'#!/bin/sh\nexit 0\n')
        self.executable.chmod(0o500)
        with mock.patch.object(cc, '_run', side_effect=AssertionError('must not execute')):
            with self.assertRaisesRegex(cc.CompileSafetyError, 'Unreviewed'):
                self.compile()

    def test_wrong_version_is_separate_probe_and_never_compiles(self):
        with mock.patch.object(cc, '_reviewed_native_artifact', return_value=
                cc._ReviewedNativeArtifact(self.executable, self.reviewed_hash, b'wrong version\n')):
            run = cc._run
            with mock.patch.object(cc, '_run', wraps=run) as calls:
                with self.assertRaisesRegex(cc.CompileSafetyError, 'version'):
                    self.compile()
                self.assertEqual(len(calls.call_args_list), 1)
                self.assertEqual(calls.call_args.kwargs['purpose'], 'version-probe')

    def test_recursive_path_and_inode_are_refused(self):
        with mock.patch.object(cc, '_LAUNCHERS', (self.executable,)):
            with self.assertRaisesRegex(cc.CompileSafetyError, 'Recursive'):
                self.compile()
        alias = self.base / 'hardlink-launcher'
        alias.hardlink_to(self.executable)
        with mock.patch.object(cc, '_LAUNCHERS', (alias,)):
            with self.assertRaises(cc.CompileSafetyError):
                self.compile()

    def test_nonzero_signal_timeout_cancellation_and_large_streams(self):
        for behavior, reason in (
            ('sys.exit(9)', 'nonzero exit'),
            ('os.kill(os.getpid(), signal.SIGTERM)', 'signal'),
            ('time.sleep(2)', 'timeout'),
            ('os.write(1, b"x" * 1000000)', 'stdout byte limit'),
            ('os.write(2, b"x" * 1000000)', 'stderr byte limit')):
            with self.subTest(reason=reason):
                self.program(behavior)
                with self.assertRaises(cc.CompilerFailure) as failed:
                    self.compile(deadline_seconds=0.5)
                self.assertIn(reason, failed.exception.reason)
                self.assertEqual(failed.exception.invocation.purpose, 'compile')
                self.assertLessEqual(len(failed.exception.invocation.stdout), cc._MAX_STREAM)
                self.assertLessEqual(len(failed.exception.invocation.stderr), cc._MAX_STREAM)
                if reason == 'signal':
                    self.assertEqual(failed.exception.invocation.returncode, -signal.SIGTERM)
        event = threading.Event()
        event.set()
        with self.assertRaises(cc.CompilerFailure) as failed:
            self.compile(cancel_event=event)
        self.assertEqual(failed.exception.reason, 'cancelled')
        self.assertIsNone(failed.exception.invocation.returncode)

    def test_native_copy_input_identity_changes_are_rejected(self):
        run = cc._run
        for kind in ('installed', 'copy', 'input'):
            with self.subTest(kind=kind):
                self.program()

                def mutate(argv, **kwargs):
                    invocation = run(argv, **kwargs)
                    if kwargs['purpose'] == 'compile':
                        target = self.executable if kind == 'installed' else Path(argv[0] if kind == 'copy' else argv[-2])
                        original = target.read_bytes()
                        target.chmod(0o700 if kind != 'input' else 0o600)
                        target.unlink()
                        target.write_bytes(original)
                        target.chmod(0o500 if kind != 'input' else 0o400)
                    return invocation
                with mock.patch.object(cc, '_run', side_effect=mutate):
                    with self.assertRaises(cc.CompileSafetyError):
                        self.compile()

    def test_expiry_during_compile_overrides_success_without_artifact(self):
        run = cc._run

        def expire(argv, **kwargs):
            result = run(argv, **kwargs)
            if kwargs['purpose'] == 'compile':
                self.active = False
            return result
        with mock.patch.object(cc, '_run', side_effect=expire):
            with self.assertRaises(cc.CompileSafetyError):
                self.compile()

    def test_artifact_access_rechecks_context_and_rejects_forgery_or_tampering(self):
        artifact = self.compile()
        cc.assert_artifact_current(self.fix, artifact)
        forged = object.__new__(cc.ClosedCoreCompileArtifact)
        for field in cc.fields(cc.ClosedCoreCompileArtifact):
            object.__setattr__(forged, field.name, getattr(artifact, field.name))
        with self.assertRaises(cc.CompileSafetyError):
            cc.assert_artifact_current(self.fix, forged)
        with self.assertRaises(cc.CompileSafetyError):
            _ = forged.pdf_bytes
        with self.assertRaises(cc.CompileSafetyError):
            cc.assert_artifact_current(object.__new__(ProtectedCoreFix), artifact)
        object.__setattr__(artifact, 'target', 'forged.typ')
        with self.assertRaises(cc.CompileSafetyError):
            _ = artifact.pdf_bytes
        object.__setattr__(artifact, 'target', self.snapshot.target)
        self.active = False
        with self.assertRaises(cc.CompileSafetyError):
            _ = artifact.pdf_bytes

    def test_pdf_payload_never_appears_in_repr_and_expired_serialization_refuses(self):
        artifact = self.compile()
        payload = artifact.pdf_bytes
        for description in (repr(artifact), str(artifact)):
            self.assertNotIn('%PDF-', description)
            self.assertNotIn('_pdf_bytes', description)
            self.assertNotIn(repr(payload), description)
        self.assertEqual(asdict(artifact)['_pdf_bytes'], payload)
        self.assertEqual(astuple(artifact)[-1], payload)
        for clone in (copy.copy(artifact), copy.deepcopy(artifact)):
            self.assertNotIn('%PDF-', repr(clone))
            with self.assertRaises(cc.CompileSafetyError):
                cc.assert_artifact_current(self.fix, clone)
            for read in (lambda: clone.pdf_bytes, lambda: asdict(clone), lambda: astuple(clone)):
                with self.assertRaises(cc.CompileSafetyError):
                    read()
        with self.assertRaises(cc.CompileSafetyError):
            cc.assert_artifact_current(object.__new__(ProtectedCoreFix), artifact)
        self.active = False
        for description in (repr(artifact), str(artifact)):
            self.assertNotIn('%PDF-', description)
            self.assertNotIn('_pdf_bytes', description)
            self.assertNotIn(repr(payload), description)
        for read in (lambda: artifact.pdf_bytes, lambda: artifact._pdf_bytes,
                     lambda: asdict(artifact), lambda: astuple(artifact),
                     lambda: copy.copy(artifact), lambda: copy.deepcopy(artifact)):
            with self.assertRaises(cc.CompileSafetyError):
                read()

    def test_artifact_payload_guard_does_not_retain_its_owner(self):
        artifact = self.compile()
        owner = weakref.ref(self.fix)
        del self.fix
        gc.collect()
        self.assertIsNone(owner())
        self.assertNotIn('%PDF-', repr(artifact))
        for read in (lambda: artifact.pdf_bytes, lambda: asdict(artifact), lambda: astuple(artifact)):
            with self.assertRaises(cc.CompileSafetyError):
                read()

    def test_later_installed_compiler_replacement_blocks_artifact(self):
        artifact = self.compile()
        self.executable.unlink()
        with self.assertRaises(cc.CompileSafetyError):
            _ = artifact.pdf_bytes

    def test_missing_invalid_large_or_symlink_output_and_dependency_escape(self):
        run = cc._run
        for kind in ('missing', 'replaced', 'deps-replaced', 'truncated', 'badxref', 'large', 'symlink', 'hardlink', 'deps', 'extra'):
            with self.subTest(kind=kind):
                def corrupt(argv, **kwargs):
                    result = run(argv, **kwargs)
                    if kwargs['purpose'] == 'compile':
                        output = Path(argv[-1])
                        deps = Path(argv[argv.index('--deps') + 1])
                        if kind == 'missing':
                            output.unlink()
                        elif kind == 'replaced':
                            output.unlink(); output.write_bytes(pdf_bytes())
                            output.chmod(0o600)
                        elif kind == 'deps-replaced':
                            raw = deps.read_bytes()
                            deps.unlink(); deps.write_bytes(raw); deps.chmod(0o600)
                        elif kind == 'truncated':
                            output.write_bytes(b'%PDF-1.7\ntruncated')
                        elif kind == 'badxref':
                            output.write_bytes(re.sub(rb'startxref\n[0-9]+', b'startxref\n999999', pdf_bytes()))
                        elif kind == 'large':
                            output.write_bytes(b'x' * 1025)
                        elif kind == 'symlink':
                            output.unlink(); output.symlink_to(Path(argv[-2]))
                        elif kind == 'hardlink':
                            output.unlink(); output.hardlink_to(Path(argv[-2]))
                        elif kind == 'deps':
                            deps.write_bytes(b'/outside/resource\0')
                        else:
                            (output.parent / 'surprise.txt').write_text('uncontrolled')
                    return result
                with mock.patch.object(cc, '_run', side_effect=corrupt), mock.patch.object(cc, '_MAX_PDF', 1024):
                    with self.assertRaises(cc.CompileSafetyError):
                        self.compile()

    def test_capture_parent_substitution_and_retained_parent_identity(self):
        parent = self.base / 'captured-parent'
        parent.mkdir()
        target = parent / 'data'
        target.write_bytes(b'original')
        other = self.base / 'substitute-parent'
        other.mkdir()
        (other / 'data').write_bytes(b'substitute')
        (other / 'data').chmod(0o600)
        opener = os.open
        swapped = False

        def substitute(name, flags, *args, **kwargs):
            nonlocal swapped
            if name == 'data' and not swapped:
                swapped = True
                parent.rename(self.base / 'retained-parent')
                parent.symlink_to(other, target_is_directory=True)
            return opener(name, flags, *args, **kwargs)
        with mock.patch.object(os, 'open', side_effect=substitute):
            with self.assertRaises(cc.CompileSafetyError):
                cc._capture(target, 1024)
        parent.unlink()
        (self.base / 'retained-parent').rename(parent)
        _, evidence = cc._capture(target, 1024)
        parent.rename(self.base / 'retained-parent')
        parent.mkdir()
        # The same leaf inode under a newly substituted parent is still stale.
        target.hardlink_to(self.base / 'retained-parent' / 'data')
        (self.base / 'retained-parent' / 'data').unlink()
        with self.assertRaises(cc.CompileSafetyError):
            cc._same(target, evidence, 1024)

    def test_same_inode_output_rewrite_after_capture_is_rejected(self):
        capture = cc._capture
        modified = False

        def rewrite(path, bound, **kwargs):
            nonlocal modified
            raw, evidence = capture(path, bound, **kwargs)
            if Path(path).name == 'artifact.pdf' and not modified:
                modified = True
                Path(path).write_bytes(raw + b'changed after capture')
            return raw, evidence
        with mock.patch.object(cc, '_capture', side_effect=rewrite):
            with self.assertRaises(cc.CompileSafetyError):
                self.compile()
        self.assertTrue(modified)

    def test_exact_subprocess_options_and_running_cancellation(self):
        popen = cc.subprocess.Popen
        with mock.patch.object(cc.subprocess, 'Popen', wraps=popen) as calls:
            artifact = self.compile()
            self.assertEqual(len(calls.call_args_list), 2)
            for call in calls.call_args_list:
                self.assertEqual(call.kwargs['stdin'], cc.subprocess.DEVNULL)
                self.assertIs(call.kwargs['shell'], False)
                self.assertIs(call.kwargs['close_fds'], True)
                self.assertIs(call.kwargs['start_new_session'], True)
                self.assertEqual(call.kwargs['umask'], 0o077)
                self.assertEqual(set(call.kwargs['env']),
                    {'HOME', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME',
                     'TMPDIR', 'LANG', 'LC_ALL', 'TZ'})
                self.assertNotEqual(call.args[0][0], str(self.executable))
        self.program('time.sleep(5)')
        event = threading.Event()
        timer = threading.Timer(0.4, event.set)
        timer.start()
        self.addCleanup(timer.cancel)
        with self.assertRaises(cc.CompilerFailure) as failed:
            self.compile(deadline_seconds=4, cancel_event=event)
        self.assertEqual(failed.exception.reason, 'cancelled')
        self.assertEqual(failed.exception.invocation.purpose, 'compile')
        self.assertEqual(failed.exception.invocation.returncode, -signal.SIGKILL)

    def test_workspace_and_executable_limits(self):
        with mock.patch.object(cc, '_MAX_EXECUTABLE', 1):
            with self.assertRaises(cc.CompileSafetyError):
                self.compile()
        with mock.patch.object(cc, '_MAX_WORKSPACE', 1):
            with self.assertRaises(cc.CompileSafetyError):
                self.compile()
        with mock.patch.object(cc, '_MAX_ENTRIES', 1):
            with self.assertRaises(cc.CompileSafetyError):
                self.compile()

    def test_capture_rejects_nonregular_symlink_hardlink_and_writable_modes(self):
        for kind in ('directory', 'fifo', 'symlink', 'hardlink', 'writable'):
            with self.subTest(kind=kind):
                target = self.base / kind
                if kind == 'directory':
                    target.mkdir()
                elif kind == 'fifo':
                    os.mkfifo(target)
                elif kind == 'symlink':
                    target.symlink_to(self.executable)
                elif kind == 'hardlink':
                    target.hardlink_to(self.executable)
                else:
                    target.write_bytes(b'x')
                    target.chmod(0o666)
                with self.assertRaises(cc.CompileSafetyError):
                    cc._capture(target, 1024)


    def test_capture_rejects_retained_setuid_bit(self):
        target = self.base / 'setuid'
        target.write_bytes(b'x')
        target.chmod(0o4600)
        if not target.stat().st_mode & 0o4000:
            self.skipTest('Filesystem did not retain the setuid fixture bit')
        with self.assertRaises(cc.CompileSafetyError):
            cc._capture(target, 1024)



if __name__ == '__main__':
    unittest.main()
