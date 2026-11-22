"""Independent paragraph capability/staging boundaries with simulated runtime.

These tests never invoke native Typst, font inspection or a PDF utility. The
real parser/transaction issues source authority; the fake runner issues only
owned fixture output through the existing attempt-ledger seam.
"""
from contextlib import ExitStack
import copy
from dataclasses import FrozenInstanceError, asdict, astuple, fields
import gc
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest import mock
import weakref

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import captured_text_utils_paragraph_compile as compiler
import captured_text_utils_paragraph_fix as bridge
import captured_text_utils_paragraph_inputs as inputs
import closed_core_compile as runtime
import package_loader
from resource_profiles import ASCII_RESOURCE_PROFILE as ASCII, APPLEGOTHIC_RESOURCE_PROFILE as KOREAN
from syntax_client import EXECUTABLE
from transactions import TransactionError
from paragraph_fix_test_support import ROOT, EXPECTED, changed, evidence, same_byte_replace, state
from paragraph_compile_test_support import ParagraphFixture, clone_opaque, pdf_bytes


class ParagraphCompileFixtures:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='paragraph-compile-boundaries-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.sequence = 0

    def fixture(self, **kwargs):
        self.sequence += 1
        return ParagraphFixture(self.base, self.sequence, **kwargs)

    def compile(self, fix, **kwargs):
        kwargs.setdefault('profile', ASCII)
        # The product default remains 10s. Owned success fixtures allow bounded
        # headroom for repeated independent replay on a loaded test machine.
        kwargs.setdefault('deadline_seconds', 120)
        return compiler.compile_committed_snapshot(fix, fix.committed_snapshot(), **kwargs)


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX owned fixtures')
class ParagraphCommittedAuthorityTests(ParagraphCompileFixtures, unittest.TestCase):
    def test_unchanged_ten_second_default_compiles_real_receipt_with_simulated_runtime(self):
        import inspect
        self.assertEqual(inspect.signature(compiler.compile_committed_snapshot).parameters['deadline_seconds'].default, 10.0)
        f = self.fixture()
        with f.context() as fix, f.resources():
            receipt = fix.committed_snapshot()
            # Intentionally no deadline override: this is the production default
            # with genuine source authority and only the native runner simulated.
            artifact = compiler.compile_committed_snapshot(fix, receipt, profile=ASCII)
            self.assertEqual(artifact.pdf_bytes, pdf_bytes())
            self.assertEqual(len(artifact.report.attempts), 2)
            self.assertTrue(artifact.report.compiler_invoked)
            self.assertEqual(artifact.report.compiler_status, 0)

    def test_committed_dag_seal_rejects_nested_nodes_aliases_cycles_and_field_shape_changes(self):
        f = self.fixture()
        with f.context() as fix:
            receipt = fix.committed_snapshot()
            node = receipt.package_tree.nodes[0]
            alterations = ((node, 'kind', 'Forged'), (node, 'start', False),
                (node, 'children', (node,)),
                (receipt.package_tree, 'nodes', tuple(list(receipt.package_tree.nodes))),
                (receipt.proof, 'package', clone_opaque(receipt.proof.package)))
            with mock.patch.object(runtime, '_capture_compiler', side_effect=AssertionError('altered DAG captured resources')):
                for value, name, replacement in alterations:
                    with self.subTest(record=type(value), field=name), changed(value, name, replacement):
                        with self.assertRaises(TransactionError):
                            fix.assert_committed_current(receipt)
                        with self.assertRaises(runtime.CompileSafetyError):
                            compiler.compile_committed_snapshot(fix, receipt, profile=ASCII)
                object.__setattr__(node, 'forged_field', True)
                try:
                    with self.assertRaises(TransactionError):
                        fix.assert_committed_current(receipt)
                finally:
                    object.__delattr__(node, 'forged_field')
                original = node.kind
                object.__delattr__(node, 'kind')
                try:
                    with self.assertRaises(TransactionError):
                        fix.assert_committed_current(receipt)
                finally:
                    object.__setattr__(node, 'kind', original)
            fix.assert_committed_current(receipt)
        self.assertEqual(f.source.read_bytes(), EXPECTED)

    def test_receipt_requires_successful_apply_and_is_the_original_same_live_generation(self):
        for source in (ROOT, EXPECTED):
            with self.subTest(source=source):
                f = self.fixture(source=source)
                with f.context(committed=False) as fix:
                    with self.assertRaises(TransactionError):
                        fix.committed_snapshot()
                    candidate = fix.validate()
                    with self.assertRaises(TransactionError):
                        fix.committed_snapshot()
                    result = fix.apply(candidate)
                    receipt = fix.committed_snapshot()
                    self.assertIs(receipt, fix.committed_snapshot())
                    self.assertIs(receipt._receipt, fix._tx.committed_snapshot())
                    self.assertIs(receipt.tree, fix._applied_validation.replay.final_tree)
                    self.assertEqual(receipt.sources, {f.target: EXPECTED})
                    self.assertEqual(len(receipt.evidence), 4)
                    self.assertEqual(result.modified, () if source == EXPECTED else (f.target,))
                    fix.assert_committed_current(receipt)
                with self.assertRaises(TransactionError):
                    fix.assert_committed_current(receipt)

    def test_forged_foreign_copied_bare_and_mutated_receipts_refuse_before_resources(self):
        f, alien = self.fixture(), self.fixture()
        with f.context() as fix, alien.context() as other:
            receipt = fix.committed_snapshot()
            class Subclass(bridge.CapturedTextUtilsParagraphCommittedSnapshot):
                pass
            subclass = object.__new__(Subclass)
            for item in fields(receipt):
                object.__setattr__(subclass, item.name, getattr(receipt, item.name))
            variants = (copy.copy(receipt), clone_opaque(receipt), subclass,
                fix._tx.committed_snapshot(), other.committed_snapshot(), object(),
                SimpleNamespace(**{i.name: getattr(receipt, i.name) for i in fields(receipt)}))
            with mock.patch.object(runtime, '_capture_compiler', side_effect=AssertionError('resource capture')):
                for invalid in variants:
                    with self.subTest(kind=type(invalid)), self.assertRaises(runtime.CompileSafetyError):
                        compiler.compile_committed_snapshot(fix, invalid, profile=ASCII)
                for owner in (copy.copy(fix), object(), object.__new__(bridge.ProtectedCapturedTextUtilsParagraphFix)):
                    with self.subTest(owner=type(owner)), self.assertRaises(runtime.CompileSafetyError):
                        compiler.compile_committed_snapshot(owner, receipt, profile=ASCII)
                for field, value in (('target', 'other.typ'), ('sources', MappingProxyType(dict(receipt.sources))),
                                     ('source_profile', 'closed-core-v1'), ('_receipt', other._tx.committed_snapshot()),
                                     ('proof', object()), ('required_rule_ids', ())):
                    with self.subTest(field=field), changed(receipt, field, value):
                        with self.assertRaises(runtime.CompileSafetyError):
                            compiler.compile_committed_snapshot(fix, receipt, profile=ASCII)
            fix.assert_committed_current(receipt)
        with mock.patch.object(runtime, '_capture_compiler', side_effect=AssertionError('expired resources')):
            with self.assertRaises(runtime.CompileSafetyError):
                compiler.compile_committed_snapshot(fix, receipt, profile=ASCII)

    def test_noop_receipt_retains_inode_and_has_no_invented_journal_or_source_write(self):
        f = self.fixture(source=EXPECTED)
        before = state(f.source), tuple(state(p) for p in evidence(f))
        with f.context() as fix, f.resources():
            artifact = self.compile(fix)
            self.assertIsNone(fix.summary.journal_id)
            self.assertEqual(fix.summary.modified_paths, ())
            self.assertEqual(fix.summary.applied_edits, ())
            self.assertEqual(artifact.source_sha256, runtime._hash(EXPECTED))
        self.assertEqual((state(f.source), tuple(state(p) for p in evidence(f))), before)
        self.assertFalse(list(f.store.glob('root-*/*/manifest.json')))

    def test_exact_private_root_package_arguments_and_no_fresh_parser_or_loader_calls(self):
        f = self.fixture(target='nested with spaces/main.typ')
        untouched = tuple(state(p) for p in evidence(f))
        observed = []
        def inspect(argv, purpose, project):
            if purpose != 'compile':
                return
            packages = Path(argv[argv.index('--package-path') + 1])
            expected = {'local/text-utils/0.1.2/lib.typ': f.package.read_bytes(),
                        'local/text-utils/0.1.2/typst.toml': f.manifest.read_bytes()}
            self.assertEqual({p.relative_to(packages).as_posix(): p.read_bytes()
                for p in packages.rglob('*') if p.is_file()}, expected)
            self.assertEqual({p.relative_to(project).as_posix(): p.read_bytes()
                for p in project.rglob('*') if p.is_file()}, {f.target: EXPECTED})
            self.assertNotEqual(project, f.root)
            self.assertNotEqual(packages, f.package_path)
            self.assertEqual(argv[-2], str(project / f.target))
            self.assertEqual(argv[argv.index('--deps-format') + 1], 'zero')
            self.assertIn('--ignore-system-fonts', argv)
            self.assertNotIn('--font-path', argv)
            observed.append((project, packages))
        f.before_run = inspect
        with f.context() as fix, f.resources(), ExitStack() as patches:
            for module, name in ((inputs, 'parse'), (bridge, 'parse'), (package_loader, 'load_packages')):
                patches.enter_context(mock.patch.object(module, name, side_effect=AssertionError('fresh input evaluation')))
            patches.enter_context(mock.patch.dict(os.environ, {'TYPST_PACKAGE_PATH': '/ambient/forbidden',
                'TYPST_FONT_PATHS': '/ambient/forbidden', 'DYLD_INSERT_LIBRARIES': '/ambient/forbidden'}))
            artifact = self.compile(fix)
            self.assertEqual(artifact.pdf_bytes, pdf_bytes())
            self.assertEqual(artifact.dependency_paths[0], f.target)
            self.assertEqual(artifact.report.resource_report.fonts, ())
            self.assertEqual(tuple(a.purpose for a in artifact.report.attempts), ('version-probe', 'compile'))
            self.assertTrue(artifact.report.compiler_invoked)
            self.assertEqual(artifact.report.compiler_status, 0)
            self.assertEqual(artifact.root_source.sha256, runtime._hash(EXPECTED))
            self.assertEqual(len(artifact.package_sources), 2)
            self.assertFalse(hasattr(artifact, 'scalar_budget'))
            self.assertEqual(artifact.marker_policy.policy, compiler.MARKER_RENDER_POLICY)
            self.assertEqual(artifact.marker_policy.private_codepoint, 0xF000)
            self.assertIs(artifact.marker_policy.marker_removed, True)
            self.assertEqual(artifact.marker_policy.horizontal_spacing, '1.5em')
            self.assertTrue(f.package.read_bytes().isascii())
            self.assertIn(b'\\u{F000}', f.package.read_bytes())
            for _, _, _, environment in f.attempts:
                self.assertEqual(set(environment), {'HOME', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME',
                    'XDG_DATA_HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'TZ'})
            compiler.assert_artifact_current(fix, artifact)
        self.assertEqual(tuple(state(p) for p in evidence(f)), untouched)
        self.assertEqual(len(observed), 1)
        self.assertTrue(all(not p.exists() for pair in observed for p in pair))

    def test_original_package_config_catalog_and_source_inode_drift_never_refresh_authority(self):
        for role in ('config', 'catalog', 'manifest', 'package', 'source'):
            with self.subTest(role=role):
                f = self.fixture()
                with f.context() as fix:
                    receipt = fix.committed_snapshot()
                    same_byte_replace(getattr(f, role))
                    with mock.patch.object(runtime, '_capture_compiler', side_effect=AssertionError('drift captured resource')):
                        with self.assertRaises(runtime.CompileSafetyError):
                            compiler.compile_committed_snapshot(fix, receipt, profile=ASCII)
                    self.assertEqual(f.source.read_bytes(), EXPECTED)
                    self.assertEqual(fix.summary.final_sha256, runtime._hash(EXPECTED))
                    self.assertEqual(fix.summary.modified_paths, (f.target,))

    def test_staged_package_file_and_ancestor_swaps_refuse_with_original_source_preserved(self):
        for role in ('lib.typ', 'typst.toml', 'version-directory', 'namespace-directory'):
            with self.subTest(role=role):
                f = self.fixture()
                fired = []
                def swap(argv, purpose, project):
                    if purpose != 'compile':
                        return
                    packages = Path(argv[argv.index('--package-path') + 1])
                    leaf = packages / 'local/text-utils/0.1.2'
                    path = (leaf / role if role in ('lib.typ', 'typst.toml') else
                            leaf if role == 'version-directory' else packages / 'local')
                    old = path.with_name(path.name + '.old')
                    path.rename(old)
                    if old.is_file():
                        path.write_bytes(old.read_bytes())
                        path.chmod(old.stat().st_mode & 0o777)
                        old.unlink()
                    else:
                        import shutil
                        shutil.copytree(old, path)
                        shutil.rmtree(old)
                    fired.append(path)
                f.before_run = swap
                with f.context() as fix, f.resources():
                    with self.assertRaises(runtime.CompileSafetyError) as failed:
                        self.compile(fix)
                    self.assertEqual(tuple(a.purpose for a in failed.exception.report.attempts), ('version-probe',))
                    self.assertFalse(failed.exception.report.compiler_invoked)
                    self.assertEqual(fix.summary.modified_paths, (f.target,))
                self.assertEqual(len(fired), 1)
                self.assertEqual(f.source.read_bytes(), EXPECTED)

    def test_package_directory_creation_collision_refuses_before_any_runtime_attempt(self):
        f = self.fixture()
        mkdir = compiler._mkdir_private
        fired = []
        def collision(path):
            if path.parts[-4:] == ('packages', 'local', 'text-utils', '0.1.2'):
                path.mkdir(mode=0o700)
                fired.append(path)
            return mkdir(path)
        with f.context() as fix, f.resources(), mock.patch.object(compiler, '_mkdir_private', side_effect=collision):
            with self.assertRaises(runtime.CompileSafetyError) as failed:
                self.compile(fix)
            self.assertFalse(failed.exception.report.compiler_invoked)
            self.assertEqual(failed.exception.report.attempts, ())
        self.assertEqual(len(fired), 1)
        self.assertEqual(f.source.read_bytes(), EXPECTED)
        self.assertEqual(f.attempts, [])

    def test_observed_compile_cancellation_keeps_actual_attempt_and_typed_failure(self):
        f = self.fixture()
        def cancel(argv, purpose, project, attempt):
            if purpose == 'compile':
                attempt.returncode, attempt.lifecycle = -15, 'signaled'
                attempt.termination_reason = 'cancelled'
                raise runtime.CompilerFailure('cancelled', attempt.snapshot(),
                    kind=runtime.CompilerFailureKind.CANCELLED)
        f.after_run = cancel
        with f.context() as fix, f.resources():
            with self.assertRaises(runtime.CompilerFailure) as failed:
                self.compile(fix)
            report = failed.exception.report
            self.assertIs(report.compiler_invoked, True)
            self.assertEqual(report.compiler_status, -15)
            self.assertEqual(tuple(a.purpose for a in report.attempts), ('version-probe', 'compile'))
            self.assertEqual(report.compiler_failure.kind, runtime.CompilerFailureKind.CANCELLED)
            self.assertEqual(report.attempts[-1].termination_reason, 'cancelled')
            self.assertEqual(report.compiler_failure.invocation.returncode, -15)
            self.assertEqual(fix.summary.modified_paths, (f.target,))
        self.assertEqual(f.source.read_bytes(), EXPECTED)

    def test_changed_returned_invocation_cannot_rewrite_ledger_facts(self):
        f = self.fixture()
        run = f.run
        def mutate(argv, **kwargs):
            result = run(argv, **kwargs)
            if kwargs['purpose'] == 'compile':
                object.__setattr__(result, 'returncode', 91)
            return result
        with f.context() as fix, f.resources(), mock.patch.object(runtime, '_run', side_effect=mutate):
            with self.assertRaises(runtime.CompileSafetyError) as failed:
                self.compile(fix)
            report = failed.exception.report
            self.assertIs(report.compiler_invoked, True)
            self.assertEqual(report.compiler_status, 0)
            self.assertEqual(report.attempts[-1].returncode, 0)
            self.assertEqual(report.safety_gate, 'refused')
        self.assertEqual(f.source.read_bytes(), EXPECTED)

    def test_artifact_copies_mutation_nested_reports_expiry_and_weak_owner_refuse(self):
        f = self.fixture()
        with f.context() as fix, f.resources():
            artifact = self.compile(fix)
            self.assertNotIn('%PDF-', repr(artifact))
            with self.assertRaises(FrozenInstanceError):
                artifact.target = 'forged.typ'
            for clone in (copy.copy(artifact), clone_opaque(artifact)):
                with self.assertRaises(runtime.CompileSafetyError):
                    _ = clone.pdf_bytes
            for value, name, replacement in ((artifact, '_pdf_bytes', b'forged'),
                    (artifact.root_source, 'path', 'other.typ'),
                    (artifact.package_sources[0].private, 'size', 0),
                    (artifact.dependencies, 'identity', ()),
                    (artifact.compilation, 'returncode', False),
                    (artifact.report, 'attempts', ()),
                    (artifact.resource_report, 'profile', KOREAN),
                    (artifact.marker_policy, 'marker_removed', False)):
                with self.subTest(record=type(value), field=name), changed(value, name, replacement):
                    with self.assertRaises(runtime.CompileSafetyError):
                        compiler.assert_artifact_current(fix, artifact)
                    with self.assertRaises(runtime.CompileSafetyError):
                        _ = artifact.pdf_bytes
            reference = weakref.ref(fix)
            compiler.assert_artifact_current(fix, artifact)
        for read in (lambda: artifact.pdf_bytes, lambda: artifact._pdf_bytes,
                     lambda: asdict(artifact), lambda: astuple(artifact)):
            with self.assertRaises(runtime.CompileSafetyError):
                read()
        del fix
        gc.collect()
        self.assertIsNone(reference())

    def test_profiles_parameters_and_signature_refuse_before_resource_capture(self):
        f = self.fixture()
        with f.context() as fix, mock.patch.object(runtime, '_capture_compiler', side_effect=AssertionError('resources')):
            receipt = fix.committed_snapshot()
            for profile in ('', None, KOREAN, 'captured-text-utils-paragraph-v1', 'ordinary'):
                with self.subTest(profile=profile), self.assertRaises(runtime.UnsupportedCompileProfile):
                    compiler.compile_committed_snapshot(fix, receipt, profile=profile)
            for deadline in (False, 0, -1, float('nan'), float('inf'), 121, '1'):
                with self.subTest(deadline=deadline), self.assertRaises(runtime.UnsupportedCompileProfile):
                    compiler.compile_committed_snapshot(fix, receipt, profile=ASCII, deadline_seconds=deadline)
            with self.assertRaises(runtime.UnsupportedCompileProfile):
                compiler.compile_committed_snapshot(fix, receipt, profile=ASCII, cancel_event=object())
            with self.assertRaises(TypeError):
                compiler.compile_committed_snapshot(fix, receipt)
            for kwargs in ({'compiler': f.compiler}, {'package_path': f.package_path}, {'environment': {}},
                           {'extra_args': ('--open',)}, {'output': 'other.pdf'}, {'approved': True}):
                with self.subTest(kwargs=kwargs), self.assertRaises(TypeError):
                    compiler.compile_committed_snapshot(fix, receipt, profile=ASCII, **kwargs)

    def test_pre_cancelled_compile_has_no_invented_attempts(self):
        f = self.fixture()
        cancelled = threading.Event()
        cancelled.set()
        with f.context() as fix, f.resources():
            with self.assertRaises(runtime.CompilerFailure) as failed:
                self.compile(fix, cancel_event=cancelled)
            self.assertEqual(failed.exception.kind, runtime.CompilerFailureKind.CANCELLED)
            self.assertEqual(failed.exception.report.attempts, ())
            self.assertFalse(failed.exception.report.compiler_invoked)
            self.assertEqual(f.attempts, [])


class ParagraphRawDependencyTests(unittest.TestCase):
    def test_exact_three_raw_records_allow_any_order_and_require_final_nul(self):
        expected = ('nested with spaces/main.typ', '/private/owned/packages/local/text-utils/0.1.2/lib.typ',
                    '/private/owned/packages/local/text-utils/0.1.2/typst.toml')
        import itertools
        for paths in itertools.permutations(expected):
            raw = b''.join(p.encode() + b'\0' for p in paths)
            self.assertEqual(compiler._dependency_paths(raw, expected), paths)

    def test_malformed_missing_duplicate_extra_or_alternate_spellings_refuse_before_traversal(self):
        expected = ('nested/main.typ', '/private/owned/packages/local/text-utils/0.1.2/lib.typ',
                    '/private/owned/packages/local/text-utils/0.1.2/typst.toml')
        good = b''.join(p.encode() + b'\0' for p in expected)
        variants = [b'', good[:-1], good + b'\0', good + b'extra.typ\0', b'\xff\0',
                    b''.join(p.encode() + b'\0' for p in expected[:-1]),
                    good + expected[0].encode() + b'\0']
        for index, alternate in ((0, './nested/main.typ'), (0, '/project/nested/main.typ'),
                (0, 'nested//main.typ'), (0, 'nested/a/../main.typ'), (0, 'nested\\main.typ'),
                (1, '@local/text-utils:0.1.2'), (1, expected[1].replace('/local/', '/LOCAL/')),
                (1, expected[1].replace('/packages/', '/packages/./')),
                (2, expected[2].replace('/0.1.2/', '/0.1.3/'))):
            paths = list(expected)
            paths[index] = alternate
            variants.append(b''.join(p.encode() + b'\0' for p in paths))
        with mock.patch.object(Path, 'resolve', side_effect=AssertionError('dependency traversal')):
            for raw in variants:
                with self.subTest(raw=raw), self.assertRaises(runtime.CompileSafetyError):
                    compiler._dependency_paths(raw, expected)


if __name__ == '__main__':
    unittest.main()
