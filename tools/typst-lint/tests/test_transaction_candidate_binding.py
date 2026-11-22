"""Issued-token alias attacks use only isolated temporary source fixtures.

The direct analyzer exercises storage, not Typst support. Bridge probes use the
existing pinned syntax adapter, never a compiler, installation or real consumer.
"""
import base64
from contextlib import nullcontext
import copy
import gc
import json
from pathlib import Path
import sys
import tempfile
from types import MappingProxyType
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import captured_relative_fix as relative
import captured_relative_inputs as inputs
import protected_fix as closed
from edit_plan import Edit
from syntax_client import EXECUTABLE
from transactions import (GraphAnalysis, OfflineExclusive, ProtectedTransaction,
                          RecoveryRequired, SourceEdit, TransactionError,
                          TransactionFailure, ValidatedCandidate)
from typst_lint import CATALOG, digest

RULES = frozenset({'fixture.candidate.binding'})
FINDINGS = frozenset({'fixture.diagnostic-only'})
INJECTED = b'UNVERIFIED changed root\n'


def engine(graph):
    edits = tuple(SourceEdit(path, Edit(0, 3, b'good'))
                  for path, data in graph.sources.items() if data.startswith(b'bad'))
    return GraphAnalysis(RULES, RULES, frozenset(graph.sources),
                         frozenset(graph.evidence), findings=FINDINGS, edits=edits)


def backing(view):
    """Test-only retained mutable alias behind the issued read-only map."""
    return next(value for value in gc.get_referents(view) if type(value) is dict)


class TokenSubclass(ValidatedCandidate):
    pass


class FindingsSubclass(frozenset):
    pass


class Crash(BaseException):
    pass


class Fixture:
    def __init__(self, *, noop=False):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'consumer'
        self.root.mkdir()
        self.store = self.base / 'journals'
        self.store.mkdir(mode=0o700)
        self.original = {'a.typ': b'good A\n' if noop else b'bad A\n',
                         'b.typ': b'good B\n' if noop else b'bad B\n',
                         'unchanged.typ': b'already good\n'}
        self.expected = {path: data.replace(b'bad', b'good')
                         for path, data in self.original.items()}
        for path, data in self.original.items():
            (self.root / path).write_bytes(data)
        self.lease = OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.temp.cleanup()

    def context(self, **kwargs):
        return ProtectedTransaction(self.root, tuple(self.original),
            journal_directory=self.store, lease=self.lease, **kwargs)

    def read(self):
        return {path: (self.root / path).read_bytes() for path in self.original}

    def journal(self, journal):
        return next(self.store.glob('root-*')) / journal


class TransactionCandidateBindingTests(unittest.TestCase):
    def assert_journal(self, f, tx, result):
        manifest = json.loads((f.journal(result.journal) / 'manifest.json').read_bytes())
        self.assertEqual({entry['path']: base64.b64decode(entry['after'])
                          for entry in manifest['files']},
                         {path: f.expected[path] for path in result.modified})
        entries = tx._journal_entries()  # Validates every progress hash against after.
        self.assertEqual([(entry.journal, entry.status) for entry in entries],
                         [(result.journal, 'committed')])

    def test_changed_and_noop_preapply_field_mutations_refuse_before_filesystem(self):
        mutations = {
            'map-replacement': lambda t: object.__setattr__(t, 'sources', MappingProxyType(dict(t.sources))),
            'map-dict': lambda t: object.__setattr__(t, 'sources', dict(t.sources)),
            'map-payload': lambda t: backing(t.sources).__setitem__('a.typ', INJECTED),
            'map-missing': lambda t: backing(t.sources).__delitem__('a.typ'),
            'map-extra': lambda t: backing(t.sources).__setitem__('extra.typ', INJECTED),
            'map-bytearray': lambda t: backing(t.sources).__setitem__('a.typ', bytearray(t.sources['a.typ'])),
            'passes': lambda t: object.__setattr__(t, 'passes', 42),
            'passes-bool': lambda t: object.__setattr__(t, 'passes', bool(t.passes)),
            'findings': lambda t: object.__setattr__(t, 'findings', frozenset({'forged'})),
            'findings-mutable': lambda t: object.__setattr__(t, 'findings', set(t.findings)),
            'findings-subclass': lambda t: object.__setattr__(t, 'findings', FindingsSubclass(t.findings)),
            'findings-oversize': lambda t: object.__setattr__(t, 'findings', frozenset(map(str, range(256)))),
            'owner': lambda t: object.__setattr__(t, '_owner', object()),
            'seal': lambda t: object.__setattr__(t, '_seal', object()),
            'extra': lambda t: object.__setattr__(t, 'extra', True),
            'type': lambda t: object.__setattr__(t, '__class__', TokenSubclass),
        }
        for name in ('sources', 'findings', 'passes', '_owner', '_seal'):
            mutations['missing-' + name] = lambda token, name=name: object.__delattr__(token, name)
        for noop in (False, True):
            for name, mutate in mutations.items():
                with self.subTest(noop=noop, mutation=name), Fixture(noop=noop) as f, f.context() as tx:
                    token = tx.validate(engine, required_rules=RULES)
                    mutate(token)
                    with mock.patch.object(tx, '_check_active', side_effect=AssertionError('filesystem before refusal')):
                        with self.assertRaises(TransactionError):
                            tx.apply(token)
                    self.assertFalse(tx._used)
                    self.assertEqual(f.read(), f.original)
                    self.assertEqual(list(next(f.store.glob('root-*')).iterdir()), [])

    def test_exact_issued_token_required_and_old_token_expires_on_revalidation(self):
        with Fixture() as f, f.context() as tx:
            old = tx.validate(engine, required_rules=RULES)
            current = tx.validate(engine, required_rules=RULES)
            for token in (old, copy.copy(current), object.__new__(ValidatedCandidate), True):
                with self.subTest(token=type(token)), self.assertRaises(TransactionError):
                    tx.apply(token)
            self.assertEqual(tx.apply(current).modified, ('a.typ', 'b.typ'))
            self.assertIsNone(tx._issued_facts)
        with self.assertRaises(TransactionError):
            tx.apply(current)

    def test_changed_and_noop_inputs_freeze_before_first_filesystem_check(self):
        for noop in (False, True):
            with self.subTest(noop=noop), Fixture(noop=noop) as f, f.context() as tx:
                token = tx.validate(engine, required_rules=RULES)
                original = tx._check_active
                mutated = []
                def first_check():
                    if not mutated:
                        mutated.append(True)
                        object.__setattr__(token, 'sources', MappingProxyType({'a.typ': INJECTED}))
                        object.__setattr__(token, 'findings', frozenset({'forged'}))
                    original()
                with mock.patch.object(tx, '_check_active', side_effect=first_check):
                    result = tx.apply(token)
                self.assertTrue(mutated)
                self.assertEqual(result.unresolved_findings, FINDINGS)
                self.assertEqual(f.read(), f.expected)
                self.assertEqual(tx.committed_snapshot().sources, f.expected)
                if noop:
                    self.assertEqual((result.journal, result.modified), (None, ()))
                else:
                    self.assert_journal(f, tx, result)

    def test_every_apply_phase_uses_fixed_bytes_and_findings_after_public_field_changes(self):
        points = (('journal_parent_fsync', None), ('journal_create', 'manifest.json'),
                  ('stage_create', 'a.typ'), ('stage_write', 'a.typ'),
                  ('stage_fsync', 'b.typ'), ('before_revalidate', None),
                  ('before_replace', 'a.typ'), ('after_replace', 'a.typ'),
                  ('before_replace', 'b.typ'), ('source_directory_fsync', 'b.typ'),
                  ('before_final_validation', None),
                  ('journal_directory_fsync', 'event-00000006.json'))
        for point, selected in points:
            for mutable_alias in (False, True):
                with self.subTest(point=point, alias=mutable_alias), Fixture() as f:
                    mutated = []
                    def fault(actual, path):
                        if actual == point and (selected is None or path == selected) and not mutated:
                            mutated.append(True)
                            if mutable_alias:
                                backing(token.sources).update({'a.typ': INJECTED, 'b.typ': INJECTED})
                            else:
                                object.__setattr__(token, 'sources', MappingProxyType({'a.typ': INJECTED}))
                            for name in ('findings', 'passes', '_owner', '_seal'):
                                object.__delattr__(token, name)
                    with f.context(fault=fault) as tx:
                        token = tx.validate(engine, required_rules=RULES)
                        result = tx.apply(token)
                        self.assertTrue(mutated)
                        self.assertEqual(f.read(), f.expected)
                        self.assertEqual(result.unresolved_findings, FINDINGS)
                        receipt = tx.committed_snapshot()
                        self.assertEqual(receipt.sources, f.expected)
                        self.assert_journal(f, tx, result)
                        (f.root / 'a.typ').write_bytes(b'later user work\n')
                        with self.assertRaises(TransactionError):
                            tx.assert_current(receipt)
                        self.assertEqual((f.root / 'a.typ').read_bytes(), b'later user work\n')
                    with f.context() as reopened:
                        self.assertEqual(reopened.pending, ())
                        self.assertEqual(reopened._journal_entries()[0].status, 'committed')

    def test_alias_mutation_then_failure_rolls_back_and_explicit_recovery_parses_journal(self):
        with Fixture() as f:
            def fault(point, path):
                if point == 'journal_parent_fsync':
                    backing(token.sources).update({'a.typ': INJECTED, 'b.typ': INJECTED})
                    object.__setattr__(token, 'findings', frozenset({'forged'}))
                if point == 'before_replace' and path == 'b.typ':
                    raise OSError('fixture second replacement failure')
            with f.context(fault=fault) as tx:
                token = tx.validate(engine, required_rules=RULES)
                with self.assertRaises(TransactionFailure) as caught:
                    tx.apply(token)
                journal = caught.exception.report.journal
                self.assertEqual(f.read(), f.original)
                with self.assertRaises(TransactionError):
                    tx.committed_snapshot()
                self.assertEqual(tx._journal_entries()[0].status, 'incomplete')
            with f.context(recovery_only=True) as recovery:
                self.assertTrue(recovery.recover(journal).complete)
            with f.context() as reopened:
                self.assertEqual(reopened.pending, ())
                self.assertEqual(f.read(), f.original)

    def test_alias_mutation_then_crash_preserves_later_user_edits_for_recovery(self):
        with Fixture() as f:
            def fault(point, path):
                if point == 'journal_parent_fsync':
                    object.__setattr__(token, 'sources', MappingProxyType({'a.typ': INJECTED}))
                if point == 'after_replace':
                    raise Crash()
            with f.context(fault=fault) as tx:
                token = tx.validate(engine, required_rules=RULES)
                with self.assertRaises(Crash):
                    tx.apply(token)
            journal = next(next(f.store.glob('root-*')).iterdir()).name
            self.assertEqual((f.root / 'a.typ').read_bytes(), f.expected['a.typ'])
            (f.root / 'a.typ').write_bytes(b'later user work\n')
            with self.assertRaises(RecoveryRequired):
                with f.context():
                    pass
            with f.context(recovery_only=True) as recovery:
                report = recovery.recover(journal)
                self.assertFalse(report.complete)
                self.assertEqual(report.changed, ('a.typ',))
                self.assertEqual((f.root / 'a.typ').read_bytes(), b'later user work\n')
                self.assertTrue(recovery.recover(journal, preserve_conflicts=report.conflicts).complete)
            self.assertEqual((f.root / 'a.typ').read_bytes(), b'later user work\n')


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class BridgeCandidateBindingTests(unittest.TestCase):
    def run_bridge(self, kind, *, noop=False, midpoint=False, alias=False):
        with Fixture() as f:
            for path in f.original:
                (f.root / path).unlink()
            source = b'$x_(1)$\n' if noop else b'$x_1$\n'
            if kind == 'relative':
                source = b'#import "values.typ": count\n#count ' + source
                (f.root / 'values.typ').write_bytes(b'#let count = 7\n')
            target = f.root / 'main.typ'
            target.write_bytes(source)
            expected = source.replace(b'x_1', b'x_(1)')
            (f.root / '.typst-lint.toml').write_bytes(b'rule-set="core"\ninclude=["main.typ"]\n')
            catalog = f.base / 'catalog.json'
            catalog.write_bytes(CATALOG.read_bytes())
            module = inputs if kind == 'relative' else closed
            bridge_type = relative.ProtectedCapturedRelativeFix if kind == 'relative' else closed.ProtectedCoreFix
            hit = []
            def mutate():
                hit.append(True)
                if alias:
                    backing(raw.sources)['main.typ'] = INJECTED
                else:
                    object.__setattr__(raw, 'sources', MappingProxyType({'main.typ': INJECTED}))
                object.__setattr__(raw, 'findings', frozenset({'forged'}))
            def fault(point, path):
                if point == 'journal_parent_fsync' and not hit:
                    mutate()
            with mock.patch.object(module, 'CATALOG', catalog), bridge_type(
                    f.root, 'main.typ', journal_directory=f.store, lease=f.lease,
                    fault=fault if midpoint else None) as fix:
                candidate = fix.validate()
                raw = candidate._token if kind == 'relative' else candidate
                if not midpoint:
                    mutate()
                original_pending = fix._tx._pending
                def pending():
                    if not hit:
                        mutate()
                    return original_pending()
                # A no-op has no journal callbacks. Its pending scan is the
                # first external operation after the apply inputs are frozen.
                pending_hook = (mock.patch.object(fix._tx, '_pending', side_effect=pending)
                                if midpoint and noop else nullcontext())
                with pending_hook, self.assertRaises(TransactionError):
                    fix.apply(candidate)
                self.assertTrue(hit)
                tx = fix._tx
                if midpoint:
                    self.assertEqual(target.read_bytes(), expected)
                    receipt = tx.committed_snapshot()
                    self.assertEqual(receipt.sources, {'main.typ': expected})
                    self.assertEqual(fix.summary.final_sha256, digest(expected))
                    journal = fix.summary.journal_id
                    if noop:
                        self.assertEqual((journal, fix.summary.modified_paths), (None, ()))
                        self.assertEqual(tx._journal_entries(), ())
                    else:
                        manifest = json.loads((f.journal(journal) / 'manifest.json').read_bytes())
                        self.assertEqual(base64.b64decode(manifest['files'][0]['after']), expected)
                        self.assertEqual(tx._journal_entries()[0].status, 'committed')
                    guard = fix.committed_graph_snapshot if kind == 'relative' else fix.compile_snapshot
                    with self.assertRaises(TransactionError):
                        guard()
                    if kind == 'relative' and not noop:
                        with self.assertRaises(TransactionError):
                            fix._session.assert_current(fix._capture)
                else:
                    self.assertEqual(target.read_bytes(), source)
                    self.assertFalse(tx._used)
                    self.assertEqual(list(next(f.store.glob('root-*')).iterdir()), [])
            if midpoint:
                with ProtectedTransaction(f.root, ('main.typ',), journal_directory=f.store,
                                          lease=f.lease) as reopened:
                    self.assertEqual(reopened.pending, ())
                    if noop:
                        self.assertEqual(reopened._journal_entries(), ())
                    else:
                        self.assertEqual(reopened._journal_entries()[0].status, 'committed')
                with self.assertRaises(TransactionError):
                    tx.assert_current(receipt)
            if kind == 'relative':
                with self.assertRaises(TransactionError):
                    fix._session.assert_current(fix._capture)

    def test_both_bridges_refuse_changed_and_noop_tokens_before_apply(self):
        for kind in ('closed', 'relative'):
            for noop in (False, True):
                for alias in (False, True):
                    with self.subTest(kind=kind, noop=noop, alias=alias):
                        self.run_bridge(kind, noop=noop, alias=alias)

    def test_both_bridges_commit_only_original_validated_bytes_then_refuse_handoff(self):
        for kind in ('closed', 'relative'):
            for alias in (False, True):
                with self.subTest(kind=kind, alias=alias):
                    self.run_bridge(kind, midpoint=True, alias=alias)

    def test_both_bridges_keep_noop_result_honest_after_inputs_freeze(self):
        for kind in ('closed', 'relative'):
            for alias in (False, True):
                with self.subTest(kind=kind, alias=alias):
                    self.run_bridge(kind, noop=True, midpoint=True, alias=alias)


if __name__ == '__main__':
    unittest.main()
