"""Independent real parser/storage adversaries for the INTERNAL repair bridge.

All roots, dependencies, catalogs, journals and crash workers are test-owned.
These tests do not enable a public profile, compiler, installation or rollout.
"""
from contextlib import ExitStack
import base64
import copy
from dataclasses import FrozenInstanceError, replace
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest import mock

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import captured_relative_core as core
import captured_relative_fix as bridge
import captured_relative_inputs as inputs
import protected_fix
import script_rule
import source_profiles
from closed_core import ClosedCoreError
from edit_plan import Edit
from syntax_client import EXECUTABLE, parse
import transactions
from transactions import (OfflineExclusive, ProtectedTransaction, ReadLimits, RecoveryRequired,
                          SourceEdit, TransactionError, TransactionFailure)
import typst_lint as lint

ROOT = b'#import "values.typ": alpha\nValue #alpha and $alpha_1 + x^alpha$.\n'
MODULE = b'#let alpha = 7\n'
EXPECTED = ROOT.replace(b'alpha_1', b'alpha_(1)').replace(b'x^alpha', b'x^(alpha)')
WORKER = Path(__file__).with_name('captured_relative_fix_process_worker.py')


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
@unittest.skipUnless(sys.platform in ('darwin', 'linux'), 'POSIX protected transactions')
class CapturedRelativeFixIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='typst-relative-fix-integration-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.catalog_bytes = lint.CATALOG.read_bytes()
        self.sequence = 0
        self.f = self.fixture()

    def fixture(self, *, source=ROOT, target='main.typ'):
        self.sequence += 1
        base = self.base / f'fixture-{self.sequence}'
        root = base / 'consumer'
        root.mkdir(parents=True)
        store = base / 'journals'
        store.mkdir(mode=0o700)
        path = root / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source)
        path.chmod(0o640)
        config = root / '.typst-lint.toml'
        config.write_bytes(('rule-set="core"\ninclude=[' + json.dumps(target) + ']\n').encode())
        catalog = base / 'reviewed-catalog.json'
        catalog.write_bytes(self.catalog_bytes)
        values = self.put('values.typ', MODULE, root=root)
        values.chmod(0o444)
        unrelated = self.put('unselected.typ', b'\xff #read("never")', root=root)
        return SimpleNamespace(base=base, root=root, store=store, source=path,
                               target=target, config=config, catalog=catalog,
                               dependency=values, unrelated=unrelated,
                               lease=OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers())

    def put(self, name, raw, *, fixture=None, root=None):
        path = (root or (fixture or self.f).root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.chmod(0o640)
        path.write_bytes(raw)
        return path

    def context(self, fixture=None, **kwargs):
        f = fixture or self.f
        stack = ExitStack()
        stack.enter_context(mock.patch.object(inputs, 'CATALOG', f.catalog))
        options = dict(journal_directory=f.store, lease=f.lease)
        options.update(kwargs)
        try:
            fix = stack.enter_context(bridge.ProtectedCapturedRelativeFix(f.root, f.target, **options))
        except BaseException:
            stack.close()
            raise
        return _CapturedFixEnteredContext(stack, fix)

    def state(self, path):
        value = path.stat()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            metadata = transactions._xattrs(fd)
        finally:
            os.close(fd)
        return (path.read_bytes(), value.st_dev, value.st_ino, stat.S_IMODE(value.st_mode),
                value.st_uid, value.st_gid, metadata)

    def same_byte_replace(self, path):
        value = path.stat()
        temporary = path.with_name(path.name + '.test-user-save')
        temporary.write_bytes(path.read_bytes())
        temporary.chmod(stat.S_IMODE(value.st_mode))
        temporary.replace(path)
        self.assertNotEqual(value.st_ino, path.stat().st_ino)

    def no_manifest(self, fixture=None):
        self.assertFalse(list((fixture or self.f).store.glob('root-*/*/manifest.json')))

    def apply(self, fixture=None, **kwargs):
        with self.context(fixture, **kwargs) as fix:
            candidate = fix.validate()
            return fix.apply(candidate)

    def test_actual_backend_and_exact_root_only_durable_replace(self):
        f = self.f
        evidence = tuple(self.state(p) for p in (f.dependency, f.config, f.catalog, f.unrelated))
        before = f.source.stat()
        points = []
        actual = script_rule.analyze
        real_replace = os.replace
        with mock.patch.object(bridge, 'analyze_script', wraps=actual) as analyzer, \
                mock.patch.object(transactions.os, 'replace', wraps=real_replace) as rename:
            with self.context(fault=lambda point, path: points.append((point, path))) as fix:
                self.assertIs(fix._tx, fix._session._tx)
                self.assertEqual(fix._tx.targets, ('main.typ',))
                candidate = fix.validate()
                self.assertEqual(candidate.sources, {'main.typ': EXPECTED})
                self.assertEqual((candidate.passes, candidate.findings), (1, frozenset()))
                self.assertEqual(f.source.read_bytes(), ROOT)
                self.no_manifest()
                with self.assertRaises(TransactionError):
                    fix.committed_graph_snapshot()
                result = fix.apply(candidate)
                snapshot = fix.committed_graph_snapshot()
                fix.assert_committed_graph_current(snapshot)
                self.assertIs(snapshot, fix.committed_graph_snapshot())
                self.assertEqual(snapshot.sources, {'main.typ': EXPECTED, 'values.typ': MODULE})
                self.assertEqual(snapshot.captures['values.typ'].source, MODULE)
                self.assertEqual(set(snapshot.evidence), {str(f.dependency), str(f.config), str(f.catalog)})
                self.assertEqual(snapshot.evidence[str(f.dependency)], MODULE)
                self.assertEqual(snapshot.required_rule_ids, tuple(sorted(protected_fix.CORE_RULE_IDS)))
                self.assertEqual(snapshot.evaluated_rule_ids, (script_rule.RULE_ID,))
                self.assertEqual(len(snapshot.structural_reasons), 14)
                self.assertEqual(fix.summary.modified_paths, ('main.typ',))
                self.assertEqual(fix.summary.original_sha256, lint.digest(ROOT))
                self.assertEqual(fix.summary.final_sha256, lint.digest(EXPECTED))
        self.assertGreaterEqual(analyzer.call_count, 6)
        self.assertEqual({c.args[1] for c in analyzer.call_args_list}, {'main.typ', 'values.typ'})
        self.assertTrue(all(c.kwargs['_verify'] is False for c in analyzer.call_args_list))
        self.assertEqual(rename.call_count, 1)
        self.assertEqual(rename.call_args.args[:2], ('stage-0.bin', 'main.typ'))
        self.assertEqual(result.modified, ('main.typ',))
        self.assertFalse(result.unresolved_findings)
        self.assertIn(('after_replace', 'main.typ'), points)
        self.assertIn(('source_directory_fsync', 'main.typ'), points)
        self.assertEqual(f.source.read_bytes(), EXPECTED)
        self.assertNotEqual(f.source.stat().st_ino, before.st_ino)
        self.assertEqual(stat.S_IMODE(f.source.stat().st_mode), stat.S_IMODE(before.st_mode))
        self.assertEqual(tuple(self.state(p) for p in (f.dependency, f.config, f.catalog, f.unrelated)), evidence)
        manifest = json.loads(next(f.store.glob('root-*/*/manifest.json')).read_bytes())
        self.assertEqual(manifest['schema'], 1)
        self.assertEqual([e['path'] for e in manifest['files']], ['main.typ'])
        self.assertEqual({e['path'] for e in manifest['evidence']}, set(snapshot.evidence))

    def test_nested_scripts_fractions_wrappers_preserve_imported_use_owners(self):
        source = (b'#import "values.typ": alpha\n'
                  b'= Heading #alpha $a_(b_alpha)^alpha$\n'
                  b'- *Strong #alpha $x_alpha/y^alpha$*\n'
                  b'+ _Emphasis #alpha $z_(alpha^2) + z_alpha$_\n')
        expected = source.replace(b'b_alpha', b'b_(alpha)').replace(b')^alpha', b')^(alpha)').replace(
            b'x_alpha', b'x_(alpha)').replace(b'y^alpha', b'y^(alpha)').replace(
            b'alpha^2', b'alpha^(2)').replace(b'z_alpha', b'z_(alpha)')
        f = self.fixture(source=source)
        with self.context(f) as fix:
            before = fix._capture.proof
            originals = {p: t.source for p, t in fix._capture.captures.items()}
            candidate = fix.validate()
            self.assertEqual(candidate.sources[f.target], expected)
            fix.apply(candidate)
            after = fix.committed_graph_snapshot().proof
            self.assertEqual(tuple(u.semantic_identity for u in before.imported_uses),
                             tuple(u.semantic_identity for u in after.imported_uses))
            self.assertTrue(any(u.owners and u.operands for u in after.imported_uses))
            self.assertEqual({p: t.source for p, t in fix._capture.captures.items()}, originals)
        self.assertEqual(f.source.read_bytes(), expected)

    def test_diamond_renames_equal_values_shadowing_and_punctuation_keep_origins(self):
        source = (b'#import "left.typ": renamed as count, txt\n'
                  b'#import "right.typ": another, same, sin\n'
                  b'#txt #count $x_count + y_another + z_same + t_sin + w^txt$\n')
        f = self.fixture(source=source)
        self.put('seed.typ', b'#let seed=7\n#let equal=7\n#let txt="(),_+"\n#let sin=3\n', fixture=f)
        self.put('left.typ', b'#import "seed.typ": seed as original, txt\n#let renamed=original\n', fixture=f)
        self.put('right.typ', b'#import "seed.typ": seed, equal as same, sin\n#let another=seed\n', fixture=f)
        paths = [f.root / name for name in ('seed.typ', 'left.typ', 'right.typ')]
        for path in paths:
            path.chmod(0o444)
        before = tuple(self.state(p) for p in paths)
        with self.context(f) as fix:
            old = fix._capture.proof
            env = {b.name: b for b in old.root_bindings}
            self.assertEqual(env['count'].origin, env['another'].origin)
            self.assertNotEqual(env['count'].origin, env['same'].origin)
            self.assertNotEqual(env['count'].provenance, env['another'].provenance)
            candidate = fix.validate()
            self.assertIn(b'w^(txt)', candidate.sources[f.target])
            fix.apply(candidate)
            snapshot = fix.committed_graph_snapshot()
            self.assertEqual(tuple(u.semantic_identity for u in old.imported_uses),
                             tuple(u.semantic_identity for u in snapshot.proof.imported_uses))
            self.assertEqual(set(snapshot.sources), {'main.typ', 'left.typ', 'right.typ', 'seed.typ'})
        self.assertEqual(tuple(self.state(p) for p in paths), before)

    def test_bom_crlf_utf8_and_necessary_parent_import_are_not_normalized(self):
        source = b'\xef\xbb\xbf#import "../values.typ": alpha\r\n' + '제목 #alpha $x_alpha$\r\n'.encode()
        f = self.fixture(source=source, target='chapters/main.typ')
        with self.context(f) as fix:
            candidate = fix.validate()
            expected = source.replace(b'x_alpha', b'x_(alpha)')
            self.assertEqual(candidate.sources[f.target], expected)
            fix.apply(candidate)
            snapshot = fix.committed_graph_snapshot()
            self.assertEqual(snapshot.proof.edges[0].request, '../values.typ')
        self.assertEqual(f.source.read_bytes(), expected)

    def test_clean_noop_still_issues_live_bridge_receipt_without_journal(self):
        for source in (b'', b'Plain prose.\n', b'#import "values.typ": alpha\n#alpha $x_(alpha)$\n'):
            with self.subTest(source=source):
                f = self.fixture(source=source)
                before = self.state(f.source)
                with self.context(f) as fix:
                    candidate = fix.validate()
                    self.assertEqual(candidate.passes, 0)
                    result = fix.apply(candidate)
                    snapshot = fix.committed_graph_snapshot()
                    fix.assert_committed_graph_current(snapshot)
                    self.assertEqual(snapshot.sources[f.target], source)
                    self.assertEqual(fix.summary.modified_paths, ())
                self.assertEqual((result.journal, result.modified), (None, ()))
                self.assertEqual(self.state(f.source), before)
                self.no_manifest(f)

    def test_zero_edit_repeat_preserves_root_inode_and_dependencies(self):
        self.apply()
        before = self.state(self.f.source), self.state(self.f.dependency)
        with self.context() as fix:
            candidate = fix.validate()
            self.assertEqual(candidate.passes, 0)
            result = fix.apply(candidate)
            fix.assert_committed_graph_current(fix.committed_graph_snapshot())
        self.assertEqual((result.modified, result.journal), ((), None))
        self.assertEqual((self.state(self.f.source), self.state(self.f.dependency)), before)

    def test_backend_uses_only_retained_catalog_and_no_callback_file_reads(self):
        def inspect(tree, path, **kwargs):
            catalog = kwargs['catalog']
            self.assertIs(type(catalog), tuple)
            with self.assertRaises(TypeError):
                catalog[0]['id'] = 'core.forged'
            return script_rule.analyze(tree, path, **kwargs)
        with self.context() as fix, \
                mock.patch.object(bridge, 'analyze_script', side_effect=inspect), \
                mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('ambient byte reread')), \
                mock.patch('script_rule.load_rules', side_effect=AssertionError('ambient catalog read')):
            candidate = fix.validate()
            self.assertEqual(candidate.sources['main.typ'], EXPECTED)

    def test_unsupported_initial_root_and_unused_modules_are_input_not_authority(self):
        variants = (
            (ROOT + b'#read("never")\n', MODULE),
            (ROOT, MODULE + b'#let unused=read("never")\n'),
            (ROOT, MODULE + b'#let unused=() => 1\n'),
            (ROOT, MODULE + b'Visible output\n'),
            (ROOT, MODULE + b'// comment\n'),
            (ROOT, MODULE + b'#include "hidden.typ"\n'),
            (ROOT + b'// typst-lint: disable\n', MODULE),
        )
        for source, module in variants:
            with self.subTest(source=source, module=module):
                f = self.fixture(source=source)
                self.put('values.typ', module, fixture=f)
                with self.assertRaises(lint.InputError):
                    with self.context(f) as fix:
                        fix.validate()
                self.assertEqual(f.source.read_bytes(), source)
                self.no_manifest(f)

    def test_unimported_reachable_module_is_still_exhaustively_checked(self):
        f = self.fixture(source=b'#import "values.typ": alpha\nPlain $x_1$\n')
        self.put('values.typ', MODULE + b'#import "hidden.typ": ignored\n', fixture=f)
        self.put('hidden.typ', b'#let ignored=7\n#let unused=image("never")\n', fixture=f)
        with self.assertRaises(lint.InputError):
            with self.context(f) as fix:
                fix.validate()
        self.no_manifest(f)

    def test_public_profile_registry_still_refuses_internal_selection_and_imports(self):
        with self.assertRaises(ValueError):
            source_profiles.grammar_for_source_profile(core.SOURCE_PROFILE)
        for profile in ('closed-core-v1', 'structured-core-v1'):
            with self.subTest(profile=profile), mock.patch.object(protected_fix, 'CATALOG', self.f.catalog):
                with protected_fix.ProtectedCoreFix(self.f.root, self.f.target,
                        journal_directory=self.f.store, lease=self.f.lease,
                        source_profile=profile) as fix:
                    with self.assertRaises((ClosedCoreError, lint.InputError, TransactionError)):
                        fix.validate()
        self.assertEqual(self.f.source.read_bytes(), ROOT)
        self.no_manifest()

    def test_before_apply_byte_inode_and_mode_drift_in_every_input_refuses(self):
        for role in ('source', 'dependency', 'config', 'catalog'):
            for operation in ('bytes', 'inode', 'mode'):
                with self.subTest(role=role, operation=operation):
                    f = self.fixture()
                    with self.context(f) as fix:
                        candidate = fix.validate()
                        path = getattr(f, role)
                        if operation == 'bytes':
                            path.chmod(0o640)
                            path.write_bytes(path.read_bytes() + b'\n')
                        elif operation == 'inode':
                            self.same_byte_replace(path)
                        else:
                            path.chmod(stat.S_IMODE(path.stat().st_mode) ^ 0o040)
                        current = tuple(self.state(p) for p in (f.source, f.dependency, f.config, f.catalog))
                        with self.assertRaises(TransactionError) as caught:
                            fix.apply(candidate)
                        self.assertNotIsInstance(caught.exception, lint.InputError)
                    self.assertEqual(tuple(self.state(p) for p in (f.source, f.dependency, f.config, f.catalog)), current)
                    self.no_manifest(f)

    def test_committed_byte_inode_and_mode_drift_refuses_without_undoing_commit(self):
        for role in ('source', 'dependency', 'config', 'catalog'):
            for operation in ('bytes', 'inode', 'mode'):
                with self.subTest(role=role, operation=operation):
                    f = self.fixture()
                    with self.context(f) as fix:
                        result = fix.apply(fix.validate())
                        snapshot = fix.committed_graph_snapshot()
                        path = getattr(f, role)
                        if operation == 'bytes':
                            path.chmod(0o640)
                            path.write_bytes(path.read_bytes() + b'\n')
                        elif operation == 'inode':
                            self.same_byte_replace(path)
                        else:
                            path.chmod(stat.S_IMODE(path.stat().st_mode) ^ 0o040)
                        current = tuple(self.state(p) for p in (f.source, f.dependency, f.config, f.catalog))
                        with self.assertRaises(TransactionError):
                            fix.assert_committed_graph_current(snapshot)
                        with self.assertRaises(TransactionError):
                            fix.committed_graph_snapshot()
                        self.assertEqual(fix.summary.modified_paths, ('main.typ',))
                        self.assertEqual(fix.summary.journal_id, result.journal)
                        self.assertEqual(fix.summary.final_sha256, lint.digest(EXPECTED))
                    self.assertEqual(tuple(self.state(p) for p in (f.source, f.dependency, f.config, f.catalog)), current)

    def test_intermediate_ancestor_drift_preserving_immediate_parent_refuses(self):
        for phase in ('before_apply', 'committed'):
            for role in ('source', 'dependency', 'config', 'catalog'):
                with self.subTest(phase=phase, role=role):
                    f = self.fixture(target='doc/main.typ', source=ROOT.replace(b'values.typ', b'../mods/sub/values.typ'))
                    f.dependency = self.put('mods/sub/values.typ', MODULE, fixture=f)
                    if role == 'catalog':
                        directory = f.base / 'catalog-parent' / 'sub'
                        directory.mkdir(parents=True)
                        f.catalog.rename(directory / 'catalog.json')
                        f.catalog = directory / 'catalog.json'
                    with self.context(f) as fix:
                        candidate = fix.validate()
                        snapshot = None
                        if phase == 'committed':
                            fix.apply(candidate)
                            snapshot = fix.committed_graph_snapshot()
                        if role == 'dependency':
                            ancestor, child = f.root / 'mods', 'sub'
                        elif role == 'catalog':
                            ancestor, child = f.base / 'catalog-parent', 'sub'
                        else:
                            # Move the captured project and keep its own inode,
                            # replacing a higher parent in its descriptor chain.
                            ancestor, child = f.base, 'consumer'
                        old = ancestor.with_name(ancestor.name + '-old-ancestor')
                        parent_before = (ancestor / child).stat().st_ino
                        ancestor.rename(old)
                        ancestor.mkdir()
                        (old / child).rename(ancestor / child)
                        self.assertEqual((ancestor / child).stat().st_ino, parent_before)
                        if role in ('source', 'config'):
                            # Catalog and journal move back unchanged too, so
                            # the only change is the higher ancestor identity.
                            for remaining in tuple(old.iterdir()):
                                remaining.rename(ancestor / remaining.name)
                        with self.assertRaises(TransactionError):
                            if phase == 'before_apply':
                                fix.apply(candidate)
                            else:
                                fix.assert_committed_graph_current(snapshot)
                        expected = EXPECTED if phase == 'committed' else ROOT
                        expected = expected.replace(b'values.typ', b'../mods/sub/values.typ')
                        self.assertEqual(f.source.read_bytes(), expected)

    def test_opaque_candidate_copies_foreign_tokens_and_expiry_cannot_apply(self):
        f2 = self.fixture()
        with self.context() as fix, self.context(f2) as other:
            candidate = fix.validate()
            foreign = other.validate()
            with self.assertRaises(TypeError):
                bridge.CapturedRelativeFixCandidate()
            with self.assertRaises(TypeError):
                candidate.sources['other.typ'] = b'forged'
            with self.assertRaises(FrozenInstanceError):
                candidate.passes = 0
            for value in (None, candidate.sources, copy.copy(candidate), foreign, candidate._token):
                with self.subTest(value=type(value)), self.assertRaises(TransactionError):
                    fix.apply(value)
            fix.apply(candidate)
            with self.assertRaises(TransactionError):
                fix.apply(candidate)
        with self.assertRaises(TransactionError):
            fix.apply(candidate)
        self.assertEqual(f2.source.read_bytes(), ROOT)

    def test_copied_foreign_expired_and_bare_committed_receipts_refuse(self):
        f2 = self.fixture()
        with self.context() as fix, self.context(f2) as other:
            fix.apply(fix.validate())
            other.apply(other.validate())
            snapshot = fix.committed_graph_snapshot()
            foreign = other.committed_graph_snapshot()
            with self.assertRaises(TypeError):
                bridge.CapturedRelativeCommittedGraph()
            for value in (None, copy.copy(snapshot), foreign, snapshot.proof,
                          snapshot.sources, fix._tx.committed_snapshot()):
                with self.subTest(value=type(value)), self.assertRaises(TransactionError):
                    fix.assert_committed_graph_current(value)
            fix.assert_committed_graph_current(snapshot)
        with self.assertRaises(TransactionError):
            fix.assert_committed_graph_current(snapshot)
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)

    def test_direct_transaction_apply_cannot_issue_a_bridge_committed_graph(self):
        with self.context() as fix:
            candidate = fix.validate()
            result = fix._tx.apply(candidate._token)
            self.assertEqual(result.modified, ('main.typ',))
            with self.assertRaises(TransactionError):
                fix.committed_graph_snapshot()
            with self.assertRaises(TransactionError):
                _ = fix.summary
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)

    def test_bare_transaction_validation_never_becomes_a_bridge_candidate(self):
        with self.context() as fix:
            token = fix._tx.validate(fix._analyze_graph, required_rules=bridge.CORE_RULE_IDS)
            with self.assertRaises(TransactionError):
                fix.apply(token)
            with self.assertRaises(TransactionError):
                fix.committed_graph_snapshot()
        self.assertEqual(self.f.source.read_bytes(), ROOT)
        self.no_manifest()

    def test_independent_replay_content_passes_results_and_attribution_must_match(self):
        for alteration in ('content', 'passes', 'results', 'attribution'):
            with self.subTest(alteration=alteration):
                f = self.fixture()
                with self.context(f) as fix:
                    replay = fix._independent_replay(fix._tx.snapshot)
                    if alteration == 'content':
                        replay = replace(replay, content=replay.content.replace(b'Value', b'Other'))
                    elif alteration == 'passes':
                        replay = replace(replay, passes=0)
                    elif alteration == 'results':
                        replay = replace(replay, initial_results=MappingProxyType({bridge.SCRIPT: script_rule.ScriptResult()}))
                    else:
                        first = replay.steps[0]
                        replay = replace(replay, steps=(replace(first, applied=()),))
                    with mock.patch.object(fix, '_independent_replay', return_value=replay):
                        with self.assertRaises(TransactionError):
                            fix.validate()
                self.assertEqual(f.source.read_bytes(), ROOT)
                self.no_manifest(f)

    def test_missing_or_false_actual_script_coverage_cannot_validate(self):
        for alteration in ('missing', 'no_edits', 'skips', 'suppressed', 'parse_errors', 'location', 'module'):
            with self.subTest(alteration=alteration):
                f = self.fixture()
                def lie(tree, path, **kwargs):
                    result = script_rule.analyze(tree, path, **kwargs)
                    if path != f.target:
                        return replace(result, skips=('false module analysis',)) if alteration == 'module' else result
                    if alteration == 'missing':
                        return replace(result, diagnostics=())
                    if alteration == 'no_edits':
                        return replace(result, edits=())
                    if alteration == 'skips':
                        return replace(result, skips=('not analyzed',))
                    if alteration == 'suppressed':
                        return replace(result, suppressed=result.diagnostics)
                    if alteration == 'parse_errors':
                        return replace(result, parse_errors=((0, 1),))
                    if alteration == 'location':
                        return replace(result, diagnostics=(replace(result.diagnostics[0], column=99),)
                                       + result.diagnostics[1:])
                    return result
                with self.context(f) as fix, mock.patch.object(bridge, 'analyze_script', side_effect=lie):
                    with self.assertRaises(TransactionError):
                        fix.validate()
                self.assertEqual(f.source.read_bytes(), ROOT)
                self.no_manifest(f)

    def test_false_graph_rule_coverage_evidence_and_dependency_edits_fail(self):
        for alteration in ('coverage', 'evidence', 'module_edit', 'findings'):
            with self.subTest(alteration=alteration):
                f = self.fixture()
                with self.context(f) as fix:
                    actual = fix._analyze_graph
                    def lie(graph):
                        result = actual(graph)
                        if alteration == 'coverage':
                            return replace(result, evaluated_rules=frozenset({bridge.SCRIPT}))
                        if alteration == 'evidence':
                            return replace(result, examined_evidence=frozenset({str(f.config), str(f.catalog)}))
                        if alteration == 'module_edit':
                            return replace(result, edits=result.edits + (SourceEdit('values.typ', Edit(0, 0, b' ')),))
                        return replace(result, findings=frozenset({'false finding'}))
                    with mock.patch.object(fix, '_analyze_graph', side_effect=lie):
                        with self.assertRaises(TransactionError):
                            fix.validate()
                self.assertEqual(f.source.read_bytes(), ROOT)
                self.assertEqual(f.dependency.read_bytes(), MODULE)
                self.no_manifest(f)

    def test_graph_callback_rejects_changed_or_hidden_captured_inputs(self):
        with self.context() as fix:
            graph = fix._tx.snapshot
            variants = (
                replace(graph, sources=MappingProxyType({})),
                replace(graph, sources=MappingProxyType({**graph.sources, 'hidden.typ': MODULE})),
                replace(graph, evidence=MappingProxyType({**graph.evidence, '/hidden.typ': MODULE})),
                replace(graph, evidence=MappingProxyType({p: v for p, v in graph.evidence.items() if p != str(self.f.dependency)})),
                replace(graph, evidence=MappingProxyType({**graph.evidence, str(self.f.dependency): MODULE + b'\n'})),
                replace(graph, root=self.f.base),
            )
            for altered in variants:
                with self.subTest(altered=altered), self.assertRaises(TransactionError):
                    fix._analyze_graph(altered)
        self.no_manifest()

    def test_changed_import_items_edges_or_imported_owners_refuse_even_equal_values(self):
        source = (b'#import "values.typ": alpha, equal\n'
                  b'= Heading #alpha $x_alpha$\n- *Body #equal $y_equal$*\n')
        f = self.fixture(source=source)
        self.put('values.typ', b'#let alpha=7\n#let equal=7\n', fixture=f)
        variants = (
            source.replace(b'alpha, equal', b'alpha as alpha, equal'),
            source.replace(b'values.typ', b'hidden.typ'),
            source.replace(b'#alpha', b'#equal').replace(b'x_alpha', b'x_equal'),
            source.replace(b'= Heading #alpha $x_alpha$', b'= Heading #equal $y_equal$').replace(
                b'- *Body #equal $y_equal$*', b'- *Body #alpha $x_alpha$*'),
        )
        with self.context(f) as fix:
            for altered in variants:
                with self.subTest(altered=altered), self.assertRaises(TransactionError):
                    fix._graph_for_tree(parse(altered))
        self.assertEqual(f.source.read_bytes(), source)
        self.no_manifest(f)

    def test_candidate_nested_reflection_type_substitution_and_deleted_fields_refuse(self):
        alterations = ('candidate_pass_bool', 'token_pass_bool', 'source_map', 'replay_pass_bool',
                       'proof_copy', 'tree_source', 'node_errors_int', 'node_start_bool',
                       'candidate_missing', 'replay_missing', 'tree_missing', 'node_missing',
                       'proof_missing', 'extra_node_state')
        for alteration in alterations:
            with self.subTest(alteration=alteration):
                f = self.fixture()
                with self.context(f) as fix:
                    candidate = fix.validate()
                    validation = fix._validation
                    tree = validation.replay.final_tree
                    node = tree.nodes[0]
                    if alteration == 'candidate_pass_bool':
                        object.__setattr__(candidate, 'passes', True)
                    elif alteration == 'token_pass_bool':
                        object.__setattr__(candidate._token, 'passes', True)
                    elif alteration == 'source_map':
                        object.__setattr__(candidate, 'sources', MappingProxyType(dict(candidate.sources)))
                    elif alteration == 'replay_pass_bool':
                        object.__setattr__(validation.replay, 'passes', True)
                    elif alteration == 'proof_copy':
                        object.__setattr__(validation, 'proof', copy.copy(validation.proof))
                    elif alteration == 'tree_source':
                        object.__setattr__(tree, 'source', tree.source.replace(b'Value', b'Other'))
                    elif alteration == 'node_errors_int':
                        object.__setattr__(node, 'errors', 0)
                    elif alteration == 'node_start_bool':
                        object.__setattr__(node, 'start', False)
                    elif alteration == 'candidate_missing':
                        object.__delattr__(candidate, 'passes')
                    elif alteration == 'replay_missing':
                        object.__delattr__(validation.replay, 'baseline')
                    elif alteration == 'tree_missing':
                        object.__delattr__(tree, 'source')
                    elif alteration == 'node_missing':
                        object.__delattr__(node, 'kind')
                    elif alteration == 'proof_missing':
                        object.__delattr__(validation.proof, 'root_bindings')
                    else:
                        object.__setattr__(node, 'injected', True)
                    with self.assertRaises(TransactionError):
                        fix.apply(candidate)
                self.assertEqual(f.source.read_bytes(), ROOT)
                self.no_manifest(f)

    def test_shared_original_module_tree_mutation_never_authorizes_candidate(self):
        for alteration in ('source', 'node', 'map', 'proof'):
            with self.subTest(alteration=alteration):
                f = self.fixture()
                with self.context(f) as fix:
                    candidate = fix.validate()
                    original = fix._capture.captures['values.typ']
                    self.assertIs(fix._validation.captures['values.typ'], original)
                    if alteration == 'source':
                        object.__setattr__(original, 'source', b'#let alpha = 8\n')
                    elif alteration == 'node':
                        object.__setattr__(original.nodes[0], 'errors', 0)
                    elif alteration == 'map':
                        object.__setattr__(fix._capture, 'captures', MappingProxyType(dict(fix._capture.captures)))
                    else:
                        object.__setattr__(fix._capture.proof, 'edges', ())
                    with self.assertRaises(TransactionError):
                        fix.apply(candidate)
                self.assertEqual(f.source.read_bytes(), ROOT)
                self.assertEqual(f.dependency.read_bytes(), MODULE)
                self.no_manifest(f)

    def test_foreign_capture_and_swapped_independently_valid_proof_refuse(self):
        for alteration in ('capture', 'proof', 'graph'):
            with self.subTest(alteration=alteration):
                f, other_f = self.fixture(), self.fixture()
                self.put('values.typ', b'#let alpha = 8\n', fixture=other_f)
                with self.context(f) as fix, self.context(other_f) as other:
                    candidate = fix.validate()
                    other.validate()
                    if alteration == 'capture':
                        fix._capture = other._capture
                    elif alteration == 'proof':
                        object.__setattr__(fix._validation, 'proof', other._validation.proof)
                    else:
                        fix._baseline_graph = other._baseline_graph
                    with self.assertRaises(TransactionError):
                        fix.apply(candidate)
                self.assertEqual(f.source.read_bytes(), ROOT)
                self.no_manifest(f)

    def test_coherent_candidate_replay_tree_proof_and_summary_rewrite_is_not_authority(self):
        with self.context() as fix:
            candidate = fix.validate()
            validation = fix._validation
            alternate = EXPECTED.replace(b'Value', b'Other')
            tree = parse(alternate)
            captures = MappingProxyType({**validation.captures, 'main.typ': tree})
            proof = core.certify_captured_relative_core('main.typ', captures)
            rewritten_sources = MappingProxyType({'main.typ': alternate})
            object.__setattr__(candidate, 'sources', rewritten_sources)
            object.__setattr__(candidate._token, 'sources', rewritten_sources)
            object.__setattr__(validation.replay, 'content', alternate)
            object.__setattr__(validation.replay, 'final_tree', tree)
            object.__setattr__(validation, 'captures', captures)
            object.__setattr__(validation, 'proof', proof)
            object.__setattr__(validation.summary, 'final_sha256', lint.digest(alternate))
            # The coherent rewritten final graph really has a valid pure proof;
            # it still did not arise from this session's original insertions.
            core.verify_captured_relative_core(proof, target_path='main.typ', captures=captures)
            with self.assertRaises(TransactionError):
                fix.apply(candidate)
        self.assertEqual(self.f.source.read_bytes(), ROOT)
        self.no_manifest()

    def test_committed_snapshot_reflection_and_deleted_fields_refuse_without_rollback(self):
        for alteration in ('profile', 'parser', 'proof', 'source_map', 'node_int', 'source_missing',
                           'proof_missing', 'receipt_missing', 'snapshot_missing'):
            with self.subTest(alteration=alteration):
                f = self.fixture()
                with self.context(f) as fix:
                    fix.apply(fix.validate())
                    snapshot = fix.committed_graph_snapshot()
                    if alteration == 'profile':
                        object.__setattr__(snapshot, 'source_profile', 'structured-core-v1')
                    elif alteration == 'parser':
                        object.__setattr__(snapshot, 'parser_contract', ())
                    elif alteration == 'proof':
                        object.__setattr__(snapshot, 'proof', copy.copy(snapshot.proof))
                    elif alteration == 'source_map':
                        object.__setattr__(snapshot, 'sources', MappingProxyType(dict(snapshot.sources)))
                    elif alteration == 'node_int':
                        object.__setattr__(snapshot.captures['main.typ'].nodes[0], 'errors', 0)
                    elif alteration == 'source_missing':
                        object.__delattr__(snapshot.captures['main.typ'], 'source')
                    elif alteration == 'proof_missing':
                        object.__delattr__(snapshot.proof, 'imported_uses')
                    elif alteration == 'receipt_missing':
                        object.__delattr__(snapshot._receipt, 'sources')
                    else:
                        object.__delattr__(snapshot, '_owner')
                    with self.assertRaises(TransactionError):
                        fix.assert_committed_graph_current(snapshot)
                    self.assertEqual(fix.summary.final_sha256, lint.digest(EXPECTED))
                self.assertEqual(f.source.read_bytes(), EXPECTED)

    def test_postcommit_handoff_refusal_retains_honest_scalar_and_nested_summary(self):
        for alteration in ('hash', 'insertion'):
            with self.subTest(alteration=alteration):
                f = self.fixture()
                holder = {}
                def mutate(point, path):
                    if (point, path) == ('before_final_validation', None):
                        report = holder['fix']._validation.summary
                        if alteration == 'hash':
                            object.__setattr__(report, 'final_sha256', '0' * 64)
                        else:
                            object.__setattr__(report.applied_insertions[0], 'replacement', b'CORRUPTED')
                with self.context(f, fault=mutate) as fix:
                    holder['fix'] = fix
                    candidate = fix.validate()
                    expected_insertions = tuple((e.path, e.start, e.end, e.replacement, e.rule_id)
                                                for e in fix._validation.summary.applied_insertions)
                    with self.assertRaises(TransactionError):
                        fix.apply(candidate)
                    self.assertEqual(f.source.read_bytes(), EXPECTED)
                    self.assertEqual(fix.summary.final_sha256, lint.digest(EXPECTED))
                    self.assertEqual(fix.summary.modified_paths, ('main.typ',))
                    self.assertEqual(tuple((e.path, e.start, e.end, e.replacement, e.rule_id)
                                           for e in fix.summary.applied_insertions), expected_insertions)
                    with self.assertRaises(TransactionError):
                        fix.committed_graph_snapshot()

    def test_postcommit_filesystem_refusal_during_apply_does_not_hide_committed_summary(self):
        f = self.f
        actual = ProtectedTransaction.apply
        def apply_then_edit(tx, token):
            result = actual(tx, token)
            f.dependency.chmod(0o640)
            f.dependency.write_bytes(MODULE + b'\n')
            return result
        with self.context() as fix, mock.patch.object(ProtectedTransaction, 'apply', new=apply_then_edit):
            # apply may return success or notice the later drift; either way,
            # no live handoff may be issued and honest commit reporting remains.
            candidate = fix.validate()
            try:
                fix.apply(candidate)
            except TransactionError:
                pass
            with self.assertRaises(TransactionError):
                fix.committed_graph_snapshot()
            self.assertEqual(fix.summary.modified_paths, ('main.typ',))
            self.assertEqual(fix.summary.final_sha256, lint.digest(EXPECTED))
        self.assertEqual(f.source.read_bytes(), EXPECTED)

    def test_deleted_or_reflectively_changed_session_selection_is_typed_refusal(self):
        for name, value in (('target', None), ('source_profile', None), ('root_grammar_version', None),
                            ('module_grammar_version', 'different'), ('parser_contract', (True,))):
            with self.subTest(name=name):
                f = self.fixture()
                with self.context(f) as fix:
                    candidate = fix.validate()
                    if value is None:
                        delattr(fix, name)
                    else:
                        setattr(fix, name, value)
                    with self.assertRaises(TransactionError):
                        fix.apply(candidate)
                self.assertEqual(f.source.read_bytes(), ROOT)
                self.no_manifest(f)

    def test_midapply_private_transaction_alias_cannot_change_written_postimage(self):
        for boundary in (('journal_parent_fsync', None), ('stage_create', 'main.typ'),
                         ('before_replace', 'main.typ')):
            with self.subTest(boundary=boundary):
                f = self.fixture()
                holder, reached = {}, []
                unproven = b'UNVERIFIED changed root\n'
                def mutate(point, path):
                    if point == boundary[0] and (boundary[1] is None or path == boundary[1]):
                        reached.append((point, path))
                        object.__setattr__(holder['candidate']._token, 'sources',
                                           MappingProxyType({'main.typ': unproven}))
                with self.context(f, fault=mutate) as fix:
                    candidate = fix.validate()
                    holder['candidate'] = candidate
                    try:
                        fix.apply(candidate)
                    except TransactionError:
                        pass
                    self.assertTrue(reached, 'The actual requested transaction boundary was not reached')
                    self.assertIn(f.source.read_bytes(), (ROOT, EXPECTED),
                                  'No reflected alias may write an independently unproved postimage')
                    with self.assertRaises(TransactionError):
                        fix.committed_graph_snapshot()
                    if f.source.read_bytes() == EXPECTED:
                        self.assertEqual(fix.summary.final_sha256, lint.digest(EXPECTED))
                        self.assertEqual(fix.summary.modified_paths, ('main.typ',))
                for manifest_path in f.store.glob('root-*/*/manifest.json'):
                    manifest = json.loads(manifest_path.read_bytes())
                    self.assertEqual(base64.b64decode(manifest['files'][0]['after']), EXPECTED)

    def test_invalid_lease_or_widened_limits_refuse_before_capture(self):
        for options in ({'lease': None}, {'limits': ReadLimits(max_source_bytes=core.MAX_FILE_BYTES + 1)}):
            with self.subTest(options=options), self.assertRaises(TransactionError):
                with self.context(**options):
                    pass
        self.assertEqual(self.f.source.read_bytes(), ROOT)
        self.no_manifest()

    def test_read_limit_root_growth_refuses_before_any_journal(self):
        source = ROOT + b'p' * len(self.catalog_bytes)
        self.f.source.write_bytes(source)
        with self.context(limits=ReadLimits(max_source_bytes=len(source))) as fix:
            with self.assertRaises(TransactionError):
                fix.validate()
        self.assertEqual(self.f.source.read_bytes(), source)
        self.no_manifest()

    def test_fixed_file_bound_root_growth_refuses_without_writing(self):
        source = ROOT + b'p' * (core.MAX_FILE_BYTES - len(ROOT))
        f = self.fixture(source=source)
        with self.context(f) as fix:
            with self.assertRaises(TransactionError):
                fix.validate()
        self.assertEqual(f.source.read_bytes(), source)
        self.no_manifest(f)

    def test_fixed_total_graph_byte_bound_counts_root_growth(self):
        source = b''.join(f'#import "pad{i}.typ": n{i}\n'.encode() for i in range(4)) + b'$x_1$\n'
        f = self.fixture(source=source)
        remaining = core.MAX_SOURCE_BYTES - len(source)
        for i in range(4):
            size = remaining // (4 - i)
            prefix = f'#let n{i}="'.encode()
            raw = prefix + b'p' * (size - len(prefix) - 2) + b'"\n'
            self.assertEqual(len(raw), size)
            self.assertLessEqual(len(raw), core.MAX_FILE_BYTES)
            self.put(f'pad{i}.typ', raw, fixture=f).chmod(0o444)
            remaining -= size
        self.assertEqual(remaining, 0)
        before = {p.name: self.state(p) for p in f.root.glob('pad*.typ')}
        with self.context(f) as fix:
            self.assertEqual(sum(len(t.source) for t in fix._capture.captures.values()), core.MAX_SOURCE_BYTES)
            with self.assertRaises(TransactionError):
                fix.validate()
        self.assertEqual(f.source.read_bytes(), source)
        self.assertEqual({p.name: self.state(p) for p in f.root.glob('pad*.typ')}, before)
        self.no_manifest(f)

    def test_real_tree_node_growth_is_bounded_before_source_write(self):
        # A flat 99,998-node equation with just one repair avoids quadratic
        # many-edit work while genuine grouping crosses the 100k parser limit.
        # No fake Trees or changed budgets are involved.
        source = b'$' + b'a+' * 49994 + b'x_1$\n'
        f = self.fixture(source=source)
        self.assertLess(len(parse(source).nodes), 100_000)
        with self.context(f) as fix:
            with self.assertRaises(TransactionError):
                fix.validate()
        self.assertEqual(f.source.read_bytes(), source)
        self.no_manifest(f)

    def test_real_after_replace_failure_rolls_back_only_root_and_needs_recovery(self):
        unchanged = tuple(self.state(p) for p in (self.f.dependency, self.f.config, self.f.catalog))
        def fail(point, path):
            if (point, path) == ('after_replace', 'main.typ'):
                self.assertEqual(self.f.source.read_bytes(), EXPECTED)
                raise OSError('test-owned failure after replacement')
        with self.assertRaises(TransactionFailure) as caught:
            self.apply(fault=fail)
        self.assertEqual(caught.exception.report.restored, ('main.typ',))
        self.assertEqual(self.f.source.read_bytes(), ROOT)
        self.assertEqual(tuple(self.state(p) for p in (self.f.dependency, self.f.config, self.f.catalog)), unchanged)
        with self.assertRaises(RecoveryRequired):
            self.apply()
        with ProtectedTransaction(self.f.root, ('main.typ',), journal_directory=self.f.store,
                lease=self.f.lease, recovery_only=True) as tx:
            self.assertTrue(tx.recover(caught.exception.report.journal).complete)
        self.assertEqual(self.apply().modified, ('main.typ',))

    def request(self, action, **kwargs):
        f = self.f
        return dict(root=str(f.root), target=f.target, store=str(f.store),
                    catalog=str(f.catalog), action=action, **kwargs)

    def worker(self, action, **kwargs):
        result = subprocess.run([sys.executable, str(WORKER), json.dumps(self.request(action, **kwargs))],
                                capture_output=True, text=True, timeout=30,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def kill_worker(self, action, boundary, **kwargs):
        child = subprocess.Popen([sys.executable, str(WORKER),
                                  json.dumps(self.request(action, boundary=boundary, **kwargs))],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=30), 'Owned worker did not reach crash boundary')
                line = child.stdout.readline()
            if not line:
                _, error = child.communicate(timeout=5)
                self.fail('Owned worker exited before boundary: ' + error)
            self.assertEqual(json.loads(line), {'paused': list(boundary)})
            child.kill()  # Only this exact child, deliberately no Python unwinding.
            _, error = child.communicate(timeout=5)
            self.assertEqual(child.returncode, -signal.SIGKILL, error)
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)

    def pending(self):
        report = self.worker('probe')
        self.assertEqual(set(report), {'pending'}, report)
        self.assertEqual(len(report['pending']), 1)
        return report['pending'][0]

    def test_sigkill_before_replace_recovery_then_real_apply_and_noop_restart(self):
        evidence = tuple(self.state(p) for p in (self.f.dependency, self.f.config, self.f.catalog))
        self.kill_worker('apply', ('before_replace', 'main.typ'))
        self.assertEqual(self.f.source.read_bytes(), ROOT)
        report = self.worker('recover', journal=self.pending())
        self.assertTrue(report['complete'], report)
        self.assertEqual(report['unchanged'], ['main.typ'])
        applied = self.worker('apply')
        self.assertEqual((applied['passes'], applied['modified']), (1, ['main.typ']))
        self.assertEqual(applied['graph'], ['main.typ', 'values.typ'])
        fixed = self.state(self.f.source)
        self.assertEqual(self.worker('apply')['modified'], [])
        self.assertEqual(self.state(self.f.source), fixed)
        self.assertEqual(tuple(self.state(p) for p in (self.f.dependency, self.f.config, self.f.catalog)), evidence)

    def test_sigkill_after_replace_and_during_restore_recovery_needs_no_modules(self):
        self.kill_worker('apply', ('after_replace', 'main.typ'))
        self.assertEqual(self.f.source.read_bytes(), EXPECTED)
        journal = self.pending()
        self.f.dependency.unlink()
        self.f.config.unlink()
        self.f.catalog.unlink()
        self.kill_worker('recover', ('after_restore', 'main.typ'), journal=journal)
        self.assertEqual(self.f.source.read_bytes(), ROOT)
        fixed = self.state(self.f.source)
        report = self.worker('recover', journal=journal)
        self.assertTrue(report['complete'], report)
        self.assertEqual(report['unchanged'], ['main.typ'])
        self.assertEqual(self.state(self.f.source), fixed)
        self.assertEqual(self.worker('probe'), {'open': True})

    def test_sigkill_recovery_preserves_later_edits_and_rejects_stale_ack(self):
        self.kill_worker('apply', ('after_replace', 'main.typ'))
        journal = self.pending()
        self.f.source.write_bytes(b'Later user work.\n')
        saved = self.state(self.f.source)
        report = self.worker('recover', journal=journal)
        self.assertFalse(report['complete'])
        self.assertEqual(report['changed'], ['main.typ'])
        self.assertEqual(self.state(self.f.source), saved)
        self.f.source.write_bytes(b'Even later user work.\n')
        latest = self.state(self.f.source)
        stale = self.worker('recover', journal=journal, preserve=report['conflicts'])
        self.assertFalse(stale['complete'])
        self.assertEqual(self.state(self.f.source), latest)
        final = self.worker('recover', journal=journal, preserve=stale['conflicts'])
        self.assertTrue(final['complete'], final)
        self.assertEqual(final['preserved'], ['main.typ'])
        self.assertEqual(self.state(self.f.source), latest)

    def test_sigkill_same_postimage_new_inode_requires_exact_conflict_ack(self):
        self.kill_worker('apply', ('after_replace', 'main.typ'))
        journal = self.pending()
        self.same_byte_replace(self.f.source)
        saved = self.state(self.f.source)
        report = self.worker('recover', journal=journal)
        self.assertFalse(report['complete'])
        self.assertEqual(report['changed'], ['main.typ'])
        self.assertEqual(self.state(self.f.source), saved)
        final = self.worker('recover', journal=journal, preserve=report['conflicts'])
        self.assertTrue(final['complete'], final)
        self.assertEqual(self.state(self.f.source), saved)


class _CapturedFixEnteredContext:
    """Keep only the test-owned catalog path patched during one context."""
    def __init__(self, stack, fix):
        self.stack, self.fix = stack, fix

    def __enter__(self):
        return self.fix

    def __exit__(self, *args):
        return self.stack.__exit__(*args)


if __name__ == '__main__':
    unittest.main()
