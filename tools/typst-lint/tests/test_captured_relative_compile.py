"""Independent captured-graph adapter boundaries using only owned temp resources.

No test here invokes Typst or reads an installed font. The existing private
reviewed-resource seams select temporary Python programs and arbitrary bytes.
Real bridge lifetime integration has its own separate test module.
"""
from contextlib import contextmanager
import copy
from dataclasses import FrozenInstanceError, asdict, astuple, fields, replace
import gc
import os
import re
from pathlib import Path
import sys
import tempfile
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest import mock
import weakref

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import captured_relative_compile as compiler
import captured_relative_core as core
import captured_relative_fix as bridge
import captured_relative_inputs as inputs
import closed_core_compile as runtime
from resource_profiles import ASCII_RESOURCE_PROFILE as ASCII, APPLEGOTHIC_RESOURCE_PROFILE as KOREAN
from syntax_client import EXECUTABLE
from graph_compile_test_support import GraphFixture, FONT_BYTES, clone_opaque, pdf_bytes


@contextmanager
def changed(value, name, replacement):
    previous = getattr(value, name)
    object.__setattr__(value, name, replacement)
    try:
        yield
    finally:
        object.__setattr__(value, name, previous)


class _GraphFixtureMethods:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='captured-graph-adapter-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.serial = 0

    def fixture(self, **kwargs):
        self.serial += 1
        return GraphFixture(self.base / str(self.serial), **kwargs)

    def compile(self, fix, **kwargs):
        kwargs.setdefault('profile', ASCII)
        return compiler.compile_committed_graph(fix, fix.committed_graph_snapshot(), **kwargs)


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX private compiler fixtures')
class CapturedRelativeCompileAdapterTests(_GraphFixtureMethods, unittest.TestCase):
    def test_exact_graph_staging_fixed_arguments_environment_and_cleanup(self):
        target = 'nested with spaces/main.typ'
        sources = {target: b'#import "../values.typ": count\n#count $x_count$\n',
                   'values.typ': b'#import "shared/leaf.typ": value\n#let count = value\n',
                   'shared/leaf.typ': b'#let value = 7\n'}
        f = self.fixture(target=target, sources=sources)
        order = tuple(reversed(tuple(sources)))
        f.program(dependency_paths=order)
        run = runtime._run
        seen = []
        with f.context() as fix:
            snapshot = fix.committed_graph_snapshot()
            expected = dict(snapshot.sources)
            def inspect(argv, **kwargs):
                cwd = Path(kwargs['cwd'])
                self.assertNotEqual(cwd, f.root)
                self.assertEqual({p.relative_to(cwd).as_posix(): p.read_bytes()
                    for p in cwd.rglob('*') if p.is_file()}, expected)
                self.assertEqual(dict(kwargs['environment']), runtime._environment(cwd.parent))
                self.assertEqual(set(kwargs['environment']), {'HOME', 'XDG_CACHE_HOME',
                    'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'TZ'})
                self.assertFalse((cwd / '.typst-lint.toml').exists())
                self.assertFalse((cwd / 'unselected.typ').exists())
                if kwargs['purpose'] == 'compile':
                    self.assertEqual(Path(argv[-2]).relative_to(cwd).as_posix(), target)
                    seen.append(tuple(argv))
                return run(argv, **kwargs)
            with f.resources(), mock.patch.object(runtime, '_run', side_effect=inspect), \
                    mock.patch.dict(os.environ, {'TYPST_FONT_PATHS': '/bad',
                        'TYPST_FEATURES': 'html', 'PATH': '/bad', 'DYLD_INSERT_LIBRARIES': '/bad'}), \
                    mock.patch.object(inputs, 'parse', side_effect=AssertionError('fresh parsing')), \
                    mock.patch.object(bridge, 'parse', side_effect=AssertionError('fresh parsing')):
                artifact = self.compile(fix)
            self.assertEqual(artifact.pdf_bytes, pdf_bytes())
            self.assertEqual(artifact.dependency_paths, order)
            self.assertEqual(artifact.dependency_bytes, b''.join(p.encode() + b'\0' for p in order))
            self.assertEqual(artifact.dependencies.sha256, runtime._hash(artifact.dependency_bytes))
            self.assertEqual(artifact.dependencies.size, len(artifact.dependency_bytes))
            self.assertEqual({s.path: s.sha256 for s in artifact.sources},
                             {p: runtime._hash(raw) for p, raw in expected.items()})
            self.assertEqual(artifact.aggregate_source_bytes, sum(map(len, expected.values())))
            self.assertEqual(artifact.source_sha256, runtime._hash(expected[target]))
            argv = seen[0]
            base = Path(artifact.compilation.cwd).parent
            self.assertEqual(argv[1:-2], ('compile', '--format', 'pdf', '--root',
                artifact.compilation.cwd, '--ignore-system-fonts', '--package-path',
                str(base / 'packages'), '--package-cache-path', str(base / 'package-cache'),
                '--creation-timestamp', '0', '--jobs', '1', '--diagnostic-format', 'short',
                '--deps', artifact.dependencies.path, '--deps-format', 'zero'))
            self.assertEqual(len(artifact.probes), 1)
            self.assertEqual(len(artifact.report.attempts), 2)
            self.assertEqual(artifact.report.resource_report.fonts, ())
            self.assertFalse(base.exists())
            compiler.assert_artifact_current(fix, artifact)

    def test_explicit_profile_signature_has_no_bypass_or_budget_override(self):
        f = self.fixture()
        with f.context() as fix:
            snapshot = fix.committed_graph_snapshot()
            with self.assertRaises(TypeError):
                compiler.compile_committed_graph(fix, snapshot)
            for option in ({'compiler': f.compiler}, {'extra_args': ('--open',)},
                           {'environment': {}}, {'output': 'user.pdf'},
                           {'scalar_render_limit': 1 << 40}, {'scalar_policy': 'off'},
                           {'approved': True}):
                with self.subTest(option=option), self.assertRaises(TypeError):
                    self.compile(fix, **option)
            for profile in ('', None, core.SOURCE_PROFILE, 'ordinary'):
                with self.subTest(profile=profile), self.assertRaises(runtime.UnsupportedCompileProfile):
                    self.compile(fix, profile=profile)

    def test_deadline_and_cancellation_validation_precedes_resources(self):
        f = self.fixture()
        with f.context() as fix, mock.patch.object(runtime, '_capture_compiler',
                side_effect=AssertionError('resource before parameter validation')):
            for deadline in (False, 0, -1, float('nan'), float('inf'), 121, '1'):
                with self.subTest(deadline=deadline), self.assertRaises(runtime.UnsupportedCompileProfile):
                    self.compile(fix, deadline_seconds=deadline)
            with self.assertRaises(runtime.UnsupportedCompileProfile):
                self.compile(fix, cancel_event=object())

    def test_exact_receipt_types_and_copy_foreign_bare_and_duck_receipts_refuse(self):
        f = self.fixture()
        class Subclass(bridge.CapturedRelativeCommittedGraph):
            pass
        with f.context() as fix:
            snapshot = fix.committed_graph_snapshot()
            subclass = object.__new__(Subclass)
            for item in fields(snapshot):
                object.__setattr__(subclass, item.name, getattr(snapshot, item.name))
            variants = (copy.copy(snapshot), clone_opaque(snapshot), subclass,
                        fix._tx.committed_snapshot(), SimpleNamespace(**{
                            item.name: getattr(snapshot, item.name) for item in fields(snapshot)}), object())
            with mock.patch.object(runtime, '_capture_compiler',
                    side_effect=AssertionError('invalid authority reached resource')):
                for receipt in variants:
                    with self.subTest(receipt=type(receipt)), self.assertRaises(runtime.CompileSafetyError):
                        compiler.compile_committed_graph(fix, receipt, profile=ASCII)
                for owner in (object(), object.__new__(bridge.ProtectedCapturedRelativeFix)):
                    with self.subTest(owner=type(owner)), self.assertRaises(runtime.CompileSafetyError):
                        compiler.compile_committed_graph(owner, snapshot, profile=ASCII)

    def test_artifact_nested_reports_are_frozen_and_sealed_at_every_depth(self):
        f = self.fixture()
        with f.context() as fix, f.resources():
            artifact = self.compile(fix)
            self.assertEqual(artifact.scalar_budget.policy, 'captured-relative-scalar-render-budget-v1')
            self.assertEqual(artifact.scalar_budget.limit, 1048576)
            with self.assertRaises(FrozenInstanceError):
                artifact.target = 'forged.typ'
            nested = ((artifact.sources[0], 'path', 'forged.typ'),
                      (artifact.sources[0].private, 'size', 0),
                      (artifact.project_directories[0], 'identity', ()),
                      (artifact.output, 'sha256', '0' * 64),
                      (artifact.dependencies, 'identity', ()),
                      (artifact.compiler, 'sha256', '0' * 64),
                      (artifact.private_compiler, 'parents', ()),
                      (artifact.probes[0], 'returncode', 5),
                      (artifact.probes[0], 'returncode', False),
                      (artifact.sources[0].private, 'size', float(artifact.sources[0].private.size)),
                      (artifact.sources[0].private, 'parents', (None,) * 100000),
                      (artifact.sources[0].private, 'parents', (artifact.sources[0].private,)),
                      (artifact.compilation, 'returncode', 5),
                      (artifact.report, 'safety_gate', 'refused'),
                      (artifact.resource_report, 'repertoire_version', 'forged'),
                      (artifact.scalar_budget, 'limit', artifact.scalar_budget.limit + 1),
                      (artifact.scalar_budget, 'use_count', artifact.scalar_budget.use_count + 1),
                      (artifact.scalar_budget, 'total_charge', artifact.scalar_budget.total_charge + 1),
                      (artifact.scalar_budget, 'origin_count', artifact.scalar_budget.origin_count + 1))
            for value, name, replacement in nested:
                with self.subTest(value=type(value), field=name), changed(value, name, replacement):
                    with self.assertRaises(runtime.CompileSafetyError):
                        compiler.assert_artifact_current(fix, artifact)
                    with self.assertRaises(runtime.CompileSafetyError):
                        _ = artifact.pdf_bytes
            for name, replacement in (('dependency_paths', tuple(reversed(artifact.dependency_paths))),
                    ('dependency_bytes', b'fabricated\0'), ('sources', artifact.sources[:-1]),
                    ('scalar_budget', replace(artifact.scalar_budget, policy='off')),
                    ('_pdf_bytes', b'forged')):
                with self.subTest(field=name), changed(artifact, name, replacement):
                    with self.assertRaises(runtime.CompileSafetyError):
                        _ = artifact.pdf_bytes
            compiler.assert_artifact_current(fix, artifact)

    def test_artifact_copy_serialization_expiry_and_payload_redaction(self):
        f = self.fixture()
        with f.context() as fix, f.resources():
            artifact = self.compile(fix)
            self.assertNotIn('%PDF-', repr(artifact))
            self.assertNotIn('_pdf_bytes', repr(artifact))
            self.assertEqual(asdict(artifact)['_pdf_bytes'], pdf_bytes())
            self.assertEqual(astuple(artifact)[-1], pdf_bytes())
            with self.assertRaises(TypeError):
                compiler.CapturedRelativeCompileArtifact()
            for clone in (clone_opaque(artifact), copy.copy(artifact), copy.deepcopy(artifact)):
                with self.subTest(clone=id(clone)):
                    for read in (lambda: clone.pdf_bytes, lambda: asdict(clone), lambda: astuple(clone)):
                        with self.assertRaises(runtime.CompileSafetyError):
                            read()
            with self.assertRaises(runtime.CompileSafetyError):
                compiler.assert_artifact_current(object(), artifact)
        self.assertNotIn('%PDF-', repr(artifact))
        for read in (lambda: artifact.pdf_bytes, lambda: artifact._pdf_bytes,
                     lambda: asdict(artifact), lambda: astuple(artifact),
                     lambda: copy.copy(artifact), lambda: copy.deepcopy(artifact)):
            with self.assertRaises(runtime.CompileSafetyError):
                read()

    def test_artifact_weak_owner_does_not_extend_bridge_lifetime(self):
        f = self.fixture()
        with f.context() as fix, f.resources():
            artifact = self.compile(fix)
            reference = weakref.ref(fix)
        del fix
        gc.collect()
        self.assertIsNone(reference())
        with self.assertRaises(runtime.CompileSafetyError):
            _ = artifact.pdf_bytes

    def test_raw_dependency_records_are_complete_strict_and_preserve_order(self):
        expected = ('nested/main.typ', 'module with spaces.typ', 'shared/한글.typ')
        order = tuple(reversed(expected))
        raw = b''.join(p.encode() + b'\0' for p in order)
        self.assertEqual(compiler._dependency_paths(raw, expected), order)
        malformed = (b'', b'\0', raw[:-1], raw + b'\0', raw + b'extra.typ\0',
                     raw + expected[0].encode() + b'\0', b'\xff\0',
                     b'nested/main.typ\0module with spaces.typ\0',
                     b'/absolute.typ\0', b'../outside.typ\0', b'./nested/main.typ\0',
                     b'nested//main.typ\0', b'nested/../main.typ\0',
                     b'nested\\main.typ\0', b'\xef\xbb\xbfnested/main.typ\0',
                     b'x' * (runtime._MAX_DEPENDENCIES + 1))
        for raw in malformed:
            with self.subTest(raw=raw[:100]), self.assertRaises(runtime.CompileSafetyError):
                compiler._dependency_paths(raw, expected)

    def test_malformed_successful_compiler_dependencies_refuse_without_fabrication(self):
        f = self.fixture()
        run = runtime._run
        for raw in (b'', b'main.typ', b'\0', b'main.typ\0', b'\xff\0',
                    b'nested with spaces/main.typ\0nested with spaces/main.typ\0'):
            with self.subTest(raw=raw), f.context() as fix, f.resources():
                def corrupt(argv, **kwargs):
                    result = run(argv, **kwargs)
                    if kwargs['purpose'] == 'compile':
                        Path(argv[argv.index('--deps') + 1]).write_bytes(raw)
                    return result
                with mock.patch.object(runtime, '_run', side_effect=corrupt):
                    with self.assertRaises(runtime.CompileSafetyError) as failed:
                        self.compile(fix)
                report = failed.exception.report
                self.assertEqual(report.compiler_status, 0)
                self.assertTrue(report.compiler_invoked)
                self.assertEqual(report.safety_gate, 'refused')
                self.assertIsNotNone(report.resource_report)
                self.assertEqual(len(report.attempts), 2)

    def test_refused_shared_resource_report_tamper_preserves_original_observed_facts(self):
        run = runtime._run
        for kind in ('profile', 'font-family', 'private-font-sha256', 'installed-font-size',
                     'replacement', 'replacement-none'):
            with self.subTest(kind=kind):
                f = self.fixture()
                original = []
                with f.context() as fix, f.resources(korean=True):
                    committed = dict(fix.committed_graph_snapshot().sources)
                    def tamper(argv, **kwargs):
                        result = run(argv, **kwargs)
                        if kwargs['purpose'] == 'compile':
                            report = kwargs['_ledger'].resource_report
                            original.append(asdict(report))
                            font = report.fonts[0]
                            if kind == 'replacement':
                                kwargs['_ledger'].resource_report = replace(report, profile='forged-profile')
                            elif kind == 'replacement-none':
                                kwargs['_ledger'].resource_report = None
                            elif kind == 'profile':
                                object.__setattr__(report, 'profile', 'forged-profile')
                            elif kind == 'font-family':
                                object.__setattr__(font.identity, 'family', 'forged-family')
                            elif kind == 'private-font-sha256':
                                object.__setattr__(font.private, 'sha256', '0' * 64)
                            else:
                                object.__setattr__(font.installed, 'size', 0)
                        return result
                    with mock.patch.object(runtime, '_run', side_effect=tamper):
                        with self.assertRaises(runtime.CompileSafetyError) as failed:
                            self.compile(fix, profile=KOREAN)
                    report = failed.exception.report
                    self.assertEqual(report.safety_gate, 'refused')
                    self.assertEqual(tuple(item.returncode for item in report.attempts), (0, 0))
                    self.assertTrue(report.compiler_invoked)
                    self.assertEqual(asdict(report.resource_report), original[0])
                    self.assertEqual(dict(fix.committed_graph_snapshot().sources), committed)

    def test_returned_invocation_primitive_type_tamper_cannot_match_actual_ledger(self):
        run = runtime._run
        for purpose in ('version-probe', 'compile'):
            for field, replacement in (('returncode', False), ('started', 1)):
                with self.subTest(purpose=purpose, field=field):
                    f = self.fixture()
                    with f.context() as fix, f.resources():
                        def tamper(argv, **kwargs):
                            result = run(argv, **kwargs)
                            if kwargs['purpose'] == purpose:
                                object.__setattr__(result, field, replacement)
                            return result
                        with mock.patch.object(runtime, '_run', side_effect=tamper):
                            with self.assertRaises(runtime.CompileSafetyError) as failed:
                                self.compile(fix)
                        attempts = failed.exception.report.attempts
                        self.assertTrue(attempts)
                        for actual in attempts:
                            self.assertIs(type(actual.returncode), int)
                            self.assertEqual(actual.returncode, 0)
                            self.assertIs(actual.started, True)

    def test_private_workspace_limits_refuse_and_cleanup_before_process_attempts(self):
        for name in ('_MAX_WORKSPACE', '_MAX_ENTRIES', '_MAX_EXECUTABLE'):
            with self.subTest(limit=name):
                f = self.fixture()
                with f.context() as fix, f.resources(), mock.patch.object(runtime, name, 1):
                    with self.assertRaises(runtime.CompileSafetyError) as failed:
                        self.compile(fix)
                    self.assertEqual(failed.exception.report.attempts, ())

    def test_private_sources_directories_and_membership_are_rechecked_after_compile(self):
        run = runtime._run
        for kind in ('bytes', 'identity', 'symlink', 'hardlink', 'missing',
                     'directory-identity', 'directory-symlink', 'directory-mode',
                     'extra-file', 'extra-directory', 'unselected-config', 'installed-compiler'):
            with self.subTest(kind=kind):
                f = self.fixture()
                observed = []
                with f.context() as fix, f.resources():
                    def corrupt(argv, **kwargs):
                        result = run(argv, **kwargs)
                        if kwargs['purpose'] != 'compile':
                            return result
                        project = Path(kwargs['cwd'])
                        observed.append(project.parent)
                        target = project / 'nested with spaces/values.typ'
                        if kind == 'bytes':
                            target.chmod(0o600); target.write_bytes(b'#let count = 8\n')
                        elif kind == 'identity':
                            raw = target.read_bytes(); moved = target.with_name('moved')
                            target.rename(moved); target.write_bytes(raw); moved.unlink(); target.chmod(0o400)
                        elif kind == 'symlink':
                            target.unlink(); target.symlink_to(f.root / 'nested with spaces/values.typ')
                        elif kind == 'hardlink':
                            target.unlink(); target.hardlink_to(f.root / 'nested with spaces/values.typ')
                        elif kind == 'missing':
                            target.unlink()
                        elif kind.startswith('directory-'):
                            parent = target.parent
                            if kind == 'directory-mode':
                                parent.chmod(0o750)
                            else:
                                moved = project.parent / 'moved-directory'
                                parent.rename(moved)
                                if kind == 'directory-symlink':
                                    parent.symlink_to(moved, target_is_directory=True)
                                else:
                                    parent.mkdir(mode=0o700)
                                    for item in moved.iterdir():
                                        item.rename(parent / item.name)
                                    moved.rmdir()
                        elif kind == 'extra-file':
                            (project / 'unselected.typ').write_bytes(b'')
                        elif kind == 'extra-directory':
                            (project / 'unselected-directory').mkdir()
                        elif kind == 'unselected-config':
                            (project / '.typst-lint.toml').write_bytes(b'rule-set="all"')
                        else:
                            f.compiler.chmod(0o700); f.compiler.write_bytes(b'changed resource')
                        return result
                    with mock.patch.object(runtime, '_run', side_effect=corrupt):
                        with self.assertRaises(runtime.CompileSafetyError) as failed:
                            self.compile(fix)
                    self.assertEqual(failed.exception.report.compiler_status, 0)
                    self.assertTrue(observed)
                    self.assertFalse(observed[0].exists())

    def test_private_staging_is_directory_first_and_exclusive_no_follow(self):
        f = self.fixture()
        write = runtime._write_private
        observed = []
        with f.context() as fix, f.resources():
            manifest = compiler._manifest(fix.committed_graph_snapshot())
            def inspect(path, raw, mode):
                path = Path(path)
                if '/project/' in str(path):
                    project = next(parent for parent in path.parents if parent.name == 'project')
                    self.assertTrue(all((project / logical).is_dir() for logical in manifest.directories))
                    self.assertFalse(path.exists())
                    self.assertFalse(path.is_symlink())
                    observed.append((path.relative_to(project).as_posix(), mode))
                return write(path, raw, mode)
            with mock.patch.object(runtime, '_write_private', side_effect=inspect):
                self.compile(fix)
            self.assertEqual(set(observed), {(path, 0o400) for path in f.sources})

    def test_probe_and_postcapture_private_changes_refuse_at_next_boundary(self):
        run, capture = runtime._run, runtime._capture
        for kind in ('source-after-probe', 'font-after-probe', 'output-after-capture', 'deps-after-capture'):
            with self.subTest(kind=kind):
                f = self.fixture()
                did_mutate = []
                with f.context() as fix, f.resources(korean=True):
                    def mutate_probe(argv, **kwargs):
                        result = run(argv, **kwargs)
                        if kwargs['purpose'] == 'version-probe' and kind.endswith('after-probe'):
                            project = Path(kwargs['cwd'])
                            path = (project / f.target if kind.startswith('source') else
                                    project.parent / 'fonts' / 'AppleGothic.ttf')
                            raw = path.read_bytes()
                            path.chmod(0o600); path.write_bytes(raw + b'changed'); path.chmod(0o400)
                            did_mutate.append(path)
                        return result
                    def mutate_capture(path, bound, **kwargs):
                        raw, evidence = capture(path, bound, **kwargs)
                        expected = 'artifact.pdf' if kind.startswith('output') else 'dependencies.zero'
                        if kind.endswith('after-capture') and Path(path).name == expected and not did_mutate:
                            Path(path).write_bytes(raw + b'changed')
                            did_mutate.append(Path(path))
                        return raw, evidence
                    with mock.patch.object(runtime, '_run', side_effect=mutate_probe), \
                            mock.patch.object(runtime, '_capture', side_effect=mutate_capture):
                        with self.assertRaises(runtime.CompileSafetyError) as failed:
                            self.compile(fix, profile=KOREAN)
                    self.assertTrue(did_mutate)
                    self.assertFalse(did_mutate[0].exists())
                    self.assertEqual(len(failed.exception.report.attempts),
                                     1 if kind.endswith('after-probe') else 2)
                    if kind.endswith('after-capture'):
                        self.assertEqual(failed.exception.report.compiler_status, 0)

    def test_early_private_evidence_pin_cannot_be_reblessed_before_compile_launch(self):
        f = self.fixture()
        capture, run = runtime._capture, runtime._run
        saved = []
        roots = []
        with f.context() as fix, f.resources():
            original = dict(fix.committed_graph_snapshot().sources)
            def remember(path, bound, **kwargs):
                raw, evidence = capture(path, bound, **kwargs)
                if '/project/' in str(path) and str(path).endswith(f.target):
                    saved.append(evidence)
                return raw, evidence
            def mutate(argv, **kwargs):
                if kwargs['purpose'] == 'compile':
                    target = Path(argv[-2])
                    roots.append(Path(kwargs['cwd']).parent)
                    target.chmod(0o600)
                    target.write_bytes(original[f.target] + b'Injected private prose.\n')
                    target.chmod(0o400)
                    _, newer = capture(target, 1048576)
                    self.assertTrue(saved)
                    for item in fields(newer):
                        object.__setattr__(saved[0], item.name, getattr(newer, item.name))
                return run(argv, **kwargs)
            with mock.patch.object(runtime, '_capture', side_effect=remember), \
                    mock.patch.object(runtime, '_run', side_effect=mutate):
                with self.assertRaises(runtime.CompileSafetyError) as failed:
                    self.compile(fix)
            self.assertEqual(len(failed.exception.report.attempts), 1)
            self.assertEqual(failed.exception.report.attempts[0].purpose, 'version-probe')
            self.assertEqual(failed.exception.report.attempts[0].returncode, 0)
            self.assertFalse(failed.exception.report.compiler_invoked)
            self.assertEqual(dict(fix.committed_graph_snapshot().sources), original)
            self.assertTrue(roots)
            self.assertFalse(roots[0].exists())

    def test_output_and_dependency_file_aliases_identity_bytes_and_limits_refuse(self):
        run = runtime._run
        for kind in ('missing', 'identity', 'symlink', 'hardlink', 'truncated',
                     'bad-xref', 'oversized', 'deps-identity', 'deps-symlink', 'deps-hardlink'):
            with self.subTest(kind=kind):
                f = self.fixture()
                with f.context() as fix, f.resources():
                    def corrupt(argv, **kwargs):
                        result = run(argv, **kwargs)
                        if kwargs['purpose'] == 'compile':
                            output = Path(argv[-1])
                            deps = Path(argv[argv.index('--deps') + 1])
                            target = deps if kind.startswith('deps-') else output
                            if kind == 'missing':
                                target.unlink()
                            elif kind in ('identity', 'deps-identity'):
                                raw = target.read_bytes(); moved = target.with_name('moved')
                                target.rename(moved); target.write_bytes(raw); moved.unlink(); target.chmod(0o600)
                            elif 'symlink' in kind:
                                target.unlink(); target.symlink_to(Path(argv[-2]))
                            elif 'hardlink' in kind:
                                target.unlink(); target.hardlink_to(Path(argv[-2]))
                            elif kind == 'truncated':
                                target.write_bytes(b'%PDF-1.7\ntruncated')
                            elif kind == 'bad-xref':
                                target.write_bytes(b'%PDF-1.7\nstartxref\n999999\n%%EOF\n')
                            else:
                                target.write_bytes(b'x' * 1025)
                        return result
                    with mock.patch.object(runtime, '_run', side_effect=corrupt), \
                            mock.patch.object(runtime, '_MAX_PDF', 1024):
                        with self.assertRaises(runtime.CompileSafetyError):
                            self.compile(fix)

    def test_korean_profile_adds_font_arg_and_keeps_fonts_out_of_dependencies(self):
        f = self.fixture(sources={'nested with spaces/main.typ':
            '#import "values.typ": word\n한글 #word\n'.encode(),
            'nested with spaces/values.typ': '#let word = "한글"\n'.encode()})
        run = runtime._run
        with f.context() as fix, f.resources(korean=True):
            def inspect(argv, **kwargs):
                if kwargs['purpose'] == 'compile':
                    self.assertEqual(argv.count('--font-path'), 1)
                    directory = Path(argv[argv.index('--font-path') + 1])
                    self.assertEqual([p.name for p in directory.iterdir()], ['AppleGothic.ttf'])
                    self.assertEqual((directory / 'AppleGothic.ttf').read_bytes(), FONT_BYTES)
                return run(argv, **kwargs)
            with mock.patch.object(runtime, '_run', side_effect=inspect):
                artifact = self.compile(fix, profile=KOREAN)
            self.assertEqual(set(artifact.dependency_paths), set(f.sources))
            self.assertEqual(len(artifact.resource_report.fonts), 1)
            self.assertNotIn('AppleGothic.ttf', artifact.dependency_bytes.decode())
            font = artifact.resource_report.fonts[0]
            for value, field, replacement in ((font, 'face_index', 2),
                    (font.identity, 'family', 'forged'), (font.private, 'sha256', '0' * 64)):
                with self.subTest(field=field), changed(value, field, replacement):
                    with self.assertRaises(runtime.CompileSafetyError):
                        _ = artifact.pdf_bytes


class CapturedRelativeManifestTests(unittest.TestCase):
    def manifest(self, sources, *, target=None, **metadata):
        # Unit-only retained-record plan. This is never passed to compile and
        # confers no bridge authority; compile tests above use real receipts.
        values = dict(sources=MappingProxyType(sources), target=next(iter(sources)),
            source_profile=core.SOURCE_PROFILE, root_grammar_version=core.ROOT_GRAMMAR_VERSION,
            module_grammar_version=core.MODULE_GRAMMAR_VERSION, parser_contract=core.PARSER_CONTRACT)
        values.update(metadata)
        if target is not None:
            values['target'] = target
        return compiler._manifest(SimpleNamespace(**values))

    def test_manifest_exact_ancestor_reuse_and_no_ambient_discovery(self):
        sources = {'shared/left/main.typ': b'root', 'shared/right.typ': b'dep',
                   'shared/left/unused.typ': b'unused'}
        result = self.manifest(sources)
        self.assertEqual(result.directories, ('', 'shared', 'shared/left'))
        self.assertEqual(result.aggregate_source_bytes, 13)
        self.assertEqual(tuple(item.path for item in result.sources), tuple(sorted(sources)))
        for source in result.sources:
            self.assertIs(source.raw, sources[source.path])
            self.assertEqual(source.sha256, runtime._hash(source.raw))
        with self.assertRaises(FrozenInstanceError):
            result.aggregate_source_bytes = 0

    def test_manifest_file_count_bytes_aggregate_and_directory_caps(self):
        raw = b'x' * 1048576
        self.assertEqual(len(self.manifest({f'{n}.typ': b'' for n in range(32)}).sources), 32)
        self.assertEqual(self.manifest({'a.typ': raw}).aggregate_source_bytes, 1048576)
        self.assertEqual(self.manifest({f'{n}.typ': raw for n in range(4)}).aggregate_source_bytes, 4194304)
        exact_directory_path = '/'.join(['a'] * 63 + ['file.typ'])
        self.assertEqual(len(self.manifest({exact_directory_path: b''}).directories), 64)
        rejected = ({f'{n}.typ': b'' for n in range(33)}, {'a.typ': raw + b'x'},
                    {**{f'{n}.typ': raw for n in range(4)}, 'extra.typ': b'x'},
                    {'/'.join(['a'] * 64 + ['file.typ']): b''})
        for sources in rejected:
            with self.subTest(paths=tuple(sources)), self.assertRaises(runtime.CompileSafetyError):
                self.manifest(sources)

    def test_manifest_path_utf8_budget_and_total_exact_limit(self):
        path = 'x' * 508 + '.typ'
        self.assertEqual(self.manifest({path: b''}).sources[0].path, path)
        # Thirty-two 512-byte paths also meet the full 16 KiB path allowance.
        sources = {str(n).zfill(2) + 'x' * 506 + '.typ': b'' for n in range(32)}
        self.assertEqual(sum(len(s.path.encode()) for s in self.manifest(sources).sources), 16384)
        for path in ('x' * 509 + '.typ', '한' * 170 + '.typ'):
            with self.subTest(path_bytes=len(path.encode())), self.assertRaises(runtime.CompileSafetyError):
                self.manifest({path: b''})

    def test_manifest_prefix_case_unicode_and_directory_component_collisions_refuse(self):
        for sources in ({'a.typ': b'', 'a.typ/b.typ': b''}, {'a.typ': b'', 'A.typ': b''},
                        {'Dir/a.typ': b'', 'dir/b.typ': b''},
                        {'e\u0301/a.typ': b'', 'é/b.typ': b''},
                        {'é.typ': b'', 'e\u0301.typ': b''},
                        {'straße/a.typ': b'', 'STRASSE/b.typ': b''}):
            with self.subTest(paths=tuple(sources)), self.assertRaises(runtime.CompileSafetyError):
                self.manifest(sources)

    def test_manifest_exact_types_canonical_paths_target_and_selection(self):
        for path in ('', '/absolute.typ', './main.typ', '../main.typ', 'a//main.typ',
                     'a/../main.typ', 'a\\main.typ', 'a\x00.typ'):
            with self.subTest(path=path), self.assertRaises(runtime.CompileSafetyError):
                self.manifest({path: b''})
        for raw in ('text', bytearray(b'text'), memoryview(b'text')):
            with self.subTest(raw=type(raw)), self.assertRaises(runtime.CompileSafetyError):
                self.manifest({'main.typ': raw})
        with self.assertRaises(runtime.CompileSafetyError):
            self.manifest({'main.typ': b''}, target='missing.typ')
        for field in ('source_profile', 'root_grammar_version', 'module_grammar_version', 'parser_contract'):
            with self.subTest(field=field), self.assertRaises(runtime.CompileSafetyError):
                self.manifest({'main.typ': b''}, **{field: 'foreign'})
        with self.assertRaises(runtime.CompileSafetyError):
            compiler._manifest(SimpleNamespace(sources={'main.typ': b''}))


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class CapturedRelativeScalarBudgetTests(_GraphFixtureMethods, unittest.TestCase):
    def scalar_fixture(self, declaration, body, *, extra_modules=None):
        names = re.findall(rb'#let ([A-Za-z][A-Za-z0-9_]*) =', declaration)
        sources = {'main.typ': b'#import "values.typ": ' + b', '.join(names) + b'\n' + body,
                   'values.typ': declaration}
        sources.update(extra_modules or {})
        return self.fixture(target='main.typ', sources=sources)

    def budget(self, f):
        with f.context() as fix:
            return compiler._scalar_budget(fix.committed_graph_snapshot())

    def test_scalar_alias_diamond_math_and_markup_uses_charge_same_origin_per_use(self):
        f = self.fixture(target='main.typ', sources={
            'main.typ': b'#import "left.typ": left as first\n#import "right.typ": right\n'
                        b'#first $first + right$ #right\n',
            'seed.typ': b'#let seed = "abc"\n',
            'left.typ': b'#import "seed.typ": seed as incoming\n#let left = incoming\n',
            'right.typ': b'#import "seed.typ": seed\n#let right = seed\n'})
        budget = self.budget(f)
        self.assertEqual((budget.total_charge, budget.use_count, budget.origin_count), (12, 4, 1))

    def test_scalar_equal_origins_empty_and_unused_values_remain_distinct(self):
        f = self.scalar_fixture(b'#let first = "same"\n#let second = "same"\n'
            b'#let empty = ""\n#let unused = "' + b'x' * 10000 + b'"\n',
            b'#first #second #empty #empty\n')
        budget = self.budget(f)
        self.assertEqual((budget.total_charge, budget.use_count, budget.origin_count), (8, 4, 3))
        with self.assertRaises(FrozenInstanceError):
            budget.limit = 1 << 40

    def test_scalar_integers_canonical_digits_zero_leading_zeros_and_maximum(self):
        f = self.scalar_fixture(b'#let zero = 0\n#let leading = 000007\n'
            b'#let maximum = 9223372036854775807\n', b'#zero #leading #maximum #zero\n')
        budget = self.budget(f)
        self.assertEqual((budget.total_charge, budget.use_count, budget.origin_count), (22, 4, 3))

    def test_scalar_korean_payload_counts_utf8_bytes_and_empty_counts_use(self):
        f = self.scalar_fixture('#let word = "한글"\n#let empty = ""\n'.encode(),
                                b'#word #word #empty\n')
        budget = self.budget(f)
        self.assertEqual((budget.total_charge, budget.use_count, budget.origin_count), (12, 3, 2))

    def test_scalar_exact_limit_accepted_and_one_more_refuses_before_any_resources(self):
        declaration = b'#let value = "' + b'x' * 524288 + b'"\n#let zero = 0\n'
        f = self.scalar_fixture(declaration, b'#value #value\n')
        with f.context() as fix, f.resources():
            # Exercise the scalar budget boundary, not the default deadline;
            # repeated checks of this large retained source need CI headroom.
            artifact = self.compile(fix, deadline_seconds=30)
            self.assertEqual(artifact.scalar_budget.total_charge, 1048576)
            self.assertEqual(artifact.scalar_budget.use_count, 2)
        f = self.scalar_fixture(declaration, b'#value #value #zero\n')
        with f.context() as fix, mock.patch.object(runtime, '_capture_compiler',
                side_effect=AssertionError('budget refusal captured compiler')), \
                mock.patch.object(runtime, '_capture_fonts',
                    side_effect=AssertionError('budget refusal captured font')), \
                mock.patch.object(runtime.tempfile, 'TemporaryDirectory',
                    side_effect=AssertionError('budget refusal created workspace')):
            committed = dict(fix.committed_graph_snapshot().sources)
            with self.assertRaises(runtime.CompileSafetyError) as failed:
                self.compile(fix)
            self.assertEqual(failed.exception.report.attempts, ())
            self.assertIsNone(failed.exception.report.resource_report)
            self.assertEqual(dict(fix.committed_graph_snapshot().sources), committed)

    def test_scalar_nearly_one_megabyte_repeated_thousands_refuses_without_encoding_per_use(self):
        # Unique literal bytes stay below 1 MiB; static multiplication exceeds
        # one GiB. No expanded string or per-use encoding should be constructed.
        payload = b'x' * (1048576 - 32)
        f = self.scalar_fixture(b'#let value = "' + payload + b'"\n',
                                b' '.join([b'#value'] * 2000) + b'\n')
        with f.context() as fix, mock.patch.object(runtime, '_capture_compiler',
                side_effect=AssertionError('amplification reached resources')):
            snapshot = fix.committed_graph_snapshot()
            value = snapshot.proof.imported_uses[0].binding.value
            encodes = []
            def profile(frame, event, function):
                if (event == 'c_call' and getattr(function, '__name__', None) == 'encode'
                        and getattr(function, '__self__', None) is value):
                    encodes.append(None)
            previous_profile = sys.getprofile()
            try:
                sys.setprofile(profile)
                with mock.patch.object(compiler, '_hash', wraps=compiler._hash) as hashes:
                    with self.assertRaises(runtime.CompileSafetyError):
                        compiler._scalar_budget(snapshot)
                self.assertEqual(hashes.call_count, 1)
            finally:
                sys.setprofile(previous_profile)
            self.assertLessEqual(len(encodes), 1)
            with self.assertRaises(runtime.CompileSafetyError) as failed:
                self.compile(fix)
            self.assertEqual(failed.exception.report.attempts, ())
            self.assertIsNone(failed.exception.report.resource_report)

    def test_scalar_nonrenderable_use_and_changed_origin_authority_refuse(self):
        f = self.scalar_fixture(b'#let value = "abc"\n', b'#value\n')
        with f.context() as fix:
            snapshot = fix.committed_graph_snapshot()
            binding = snapshot.proof.imported_uses[0].binding
            for kind, value in (('boolean', True), ('none', None), ('auto', None)):
                with self.subTest(kind=kind), changed(binding, 'kind', kind), \
                        changed(binding, 'value', value):
                    with self.assertRaises(runtime.CompileSafetyError):
                        compiler._scalar_budget(snapshot)
            origin = binding.origin
            for field, replacement in (('source_sha256', '0' * 64),
                    ('initializer_span', (-1, 5)), ('initializer_span', (0, 2)),
                    ('initializer_span', (False, 2)), ('path', 'missing.typ')):
                with self.subTest(field=field, replacement=replacement), changed(origin, field, replacement):
                    with self.assertRaises(runtime.CompileSafetyError):
                        compiler._scalar_budget(snapshot)

    def test_scalar_unused_large_string_has_zero_charge_and_zero_origins(self):
        f = self.scalar_fixture(b'#let unused = "' + b'x' * 500000 + b'"\n', b'ASCII prose.\n')
        budget = self.budget(f)
        self.assertEqual((budget.total_charge, budget.use_count, budget.origin_count), (0, 0, 0))

if __name__ == '__main__':
    unittest.main()
