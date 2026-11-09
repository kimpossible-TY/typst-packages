"""Reflective receipt tampering is refused before writes to temporary consumers.

This exercises the trusted Python bridge's inspection surface, not a capability
available to ordinary Typst input. No compiler or real consumer is involved.
"""
from contextlib import contextmanager, ExitStack
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
from types import MappingProxyType
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import protected_fix as bridge
from source_profiles import CLOSED_CORE_SOURCE_PROFILE, STRUCTURED_CORE_SOURCE_PROFILE
from syntax_client import EXECUTABLE, parse
from transactions import OfflineExclusive


PROFILES = (
    (CLOSED_CORE_SOURCE_PROFILE, b'Preserved prose $x_1$\n', b'Preserved prose $x_(1)$\n'),
    (STRUCTURED_CORE_SOURCE_PROFILE, b'= Heading *$x_1$*\n', b'= Heading *$x_(1)$*\n'),
)
_MISSING = object()


@contextmanager
def changed(value, name, replacement=_MISSING):
    original = getattr(value, name)
    if replacement is _MISSING:
        object.__delattr__(value, name)
    else:
        object.__setattr__(value, name, replacement)
    try:
        yield
    finally:
        object.__setattr__(value, name, original)


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class ProtectedCandidateBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'consumer'
        self.root.mkdir()
        self.store = self.base / 'journals'
        self.store.mkdir(mode=0o700)
        self.source = self.root / 'main.typ'
        (self.root / '.typst-lint.toml').write_bytes(b'rule-set="core"\ninclude=["main.typ"]\n')
        self.catalog = self.base / 'catalog.json'
        self.catalog.write_bytes(bridge.CATALOG.read_bytes())
        patch = mock.patch.object(bridge, 'CATALOG', self.catalog)
        patch.start()
        self.addCleanup(patch.stop)
        # Only isolated, test-owned paths are covered by this assertion.
        self.lease = OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers()

    def context(self, profile, source):
        self.source.write_bytes(source)
        return bridge.ProtectedCoreFix(self.root, 'main.typ', journal_directory=self.store,
                                       lease=self.lease, source_profile=profile)

    def journals(self):
        return {str(path.relative_to(self.store)): path.read_bytes()
                for path in self.store.rglob('*') if path.is_file()}

    def refuse(self, fix, candidate):
        before, journals = self.source.read_bytes(), self.journals()
        with mock.patch.object(fix.transaction, '_write_new', side_effect=AssertionError('journal write')):
            with self.assertRaises(bridge.ProtectedCoreError):
                fix.apply(candidate)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.journals(), journals)
        self.assertFalse(fix.transaction._used)

    def test_all_transaction_candidate_fields_are_bound_for_dirty_and_noop_sources(self):
        for profile, dirty, clean in PROFILES:
            for source in (dirty, clean):
                with self.subTest(profile=profile, noop=source == clean), self.context(profile, source) as fix:
                    candidate = fix.validate()
                    variants = (
                        ('sources', MappingProxyType(dict(candidate.sources))),
                        ('sources', MappingProxyType({'main.typ': b'Changed prose $y_2$\n'})),
                        ('sources', MappingProxyType({'other.typ': clean})),
                        ('sources', MappingProxyType({'main.typ': bytearray(clean)})),
                        ('sources', dict(candidate.sources)),
                        ('findings', frozenset({'forged'})),
                        ('findings', set()),
                        ('passes', bool(candidate.passes)),
                        ('passes', float(candidate.passes)),
                        ('passes', 1 - candidate.passes),
                        ('_owner', object()), ('_seal', object()),
                        ('sources', _MISSING), ('findings', _MISSING), ('passes', _MISSING),
                        ('_owner', _MISSING), ('_seal', _MISSING),
                    )
                    for name, replacement in variants:
                        with self.subTest(field=name, value_type=type(replacement).__name__), \
                             changed(candidate, name, replacement):
                            self.refuse(fix, candidate)
                    # A refused altered receipt does not destroy the genuine candidate.
                    path = fix._compile_validation.root
                    hash(path)
                    tuple(path.parents)
                    self.assertEqual(path, Path(str(path)))
                    self.assertEqual(path.parts[-1], self.root.name)
                    result = fix.apply(candidate)
                    self.assertEqual(self.source.read_bytes(), clean)
                    self.assertEqual(result.modified, ('main.typ',) if source == dirty else ())
                    self.assertEqual(fix.summary.final_sha256, bridge.digest(clean))
                    snapshot = fix.compile_snapshot()
                    fix.assert_compile_current(snapshot)

    def test_retained_rule_attribution_results_tree_proof_and_context_are_independent_facts(self):
        for profile, dirty, _ in PROFILES:
            with self.subTest(profile=profile), self.context(profile, dirty) as fix:
                candidate = fix.validate()
                validation = fix._compile_validation
                replay, proof = validation.replay, validation.proof
                variants = (
                    (replay, 'content', b'Changed prose'),
                    (replay, 'baseline', b'Changed original'),
                    (replay, 'passes', True),
                    (replay, 'steps', ()),
                    (replay.initial_results[bridge.SCRIPT].diagnostics[0], 'rule_id', 'core.forged'),
                    (replay.initial_results[bridge.SCRIPT].edits[0], 'start',
                     float(replay.initial_results[bridge.SCRIPT].edits[0].start)),
                    (replay.final_results[bridge.SCRIPT], 'skips', ('unexamined',)),
                    (replay.edits[0], 'primary_rule_id', 'core.forged'),
                    (replay.edits[0].contributions[0], 'pass_number', True),
                    (replay.final_tree, 'source', b'Changed tree'),
                    (replay.final_tree.nodes[0], 'errors', 0),
                    (proof, 'source_bytes', float(proof.source_bytes)),
                    (proof, 'evaluated_rule_ids', ()),
                    (proof, '_issuer', object()),
                    (validation, 'config_sha256', '0' * 64),
                    (validation, 'catalog_sha256', '0' * 64),
                    (validation, 'root', self.base),
                    (validation, 'target', 'other.typ'),
                    (validation, 'candidate', _MISSING),
                    (validation, 'replay', _MISSING),
                    (validation, 'proof', _MISSING),
                    (validation, 'evidence', _MISSING),
                    (replay, 'content', _MISSING),
                    (replay.final_results[bridge.SCRIPT], 'skips', _MISSING),
                    (replay.final_tree.nodes[0], 'display', _MISSING),
                    (proof, 'source_bytes', _MISSING),
                    (validation.selection, 'grammar_version', _MISSING),
                )
                for target, name, replacement in variants:
                    with self.subTest(field=name), changed(target, name, replacement):
                        self.refuse(fix, candidate)

    def test_malformed_cyclic_oversized_or_substituted_fields_are_bounded_refusals(self):
        class HostileTuple(tuple):
            def __iter__(self):
                raise AssertionError('Substituted container must not be traversed')

        class HostileMapping(Mapping):
            def __iter__(self):
                raise AssertionError('Substituted mapping must not be traversed')

            def __len__(self):
                raise AssertionError('Substituted mapping must not be measured')

            def __getitem__(self, key):
                raise AssertionError('Substituted mapping must not be read')

        for profile, dirty, _ in PROFILES:
            with self.subTest(profile=profile), self.context(profile, dirty) as fix:
                candidate = fix.validate()
                replay = fix._compile_validation.replay
                cyclic = {}
                proxy = MappingProxyType(cyclic)
                cyclic[bridge.SCRIPT] = proxy
                variants = (
                    (replay, 'final_results', proxy),
                    (replay, 'steps', (replay.steps,)),
                    (replay.final_tree, 'nodes', (replay.final_tree.nodes,) * len(replay.final_tree.nodes)),
                    (replay.final_tree, 'nodes', replay.final_tree.nodes * 1000),
                    (replay.final_tree, 'nodes', HostileTuple(replay.final_tree.nodes)),
                    (replay, 'final_tree', object()),
                    (candidate, 'sources', MappingProxyType({str(i): b'' for i in range(1000)})),
                    (candidate, 'sources', MappingProxyType(HostileMapping())),
                    (replay, 'final_results', MappingProxyType(HostileMapping())),
                    (fix._compile_validation, 'evidence', MappingProxyType(HostileMapping())),
                )
                for target, name, replacement in variants:
                    with self.subTest(field=name), changed(target, name, replacement):
                        self.refuse(fix, candidate)

    def test_same_path_identity_does_not_hide_changed_backing_or_cached_path_facts(self):
        for profile, dirty, _ in PROFILES:
            with self.subTest(profile=profile), self.context(profile, dirty) as fix:
                candidate = fix.validate()
                path = fix._compile_validation.root
                with changed(path, '_str', str(path) + '/changed'):
                    self.refuse(fix, candidate)
                # Python 3.11 stores _parts; newer versions retain _raw_paths.
                backing = '_raw_paths' if hasattr(path, '_raw_paths') else '_parts'
                cycle = []
                cycle.append(cycle)
                for replacement in (cycle, ['oversized'] * 1000):
                    with self.subTest(backing=backing), changed(path, backing, replacement):
                        self.refuse(fix, candidate)
                # Legacy Python 3.11 caches feed parts and normalized comparison.
                for cache in ('_pparts', '_cached_cparts'):
                    if hasattr(path, cache):
                        original = getattr(path, cache)
                        replacement = tuple(cycle) if type(original) is tuple else cycle
                        with self.subTest(cache=cache), changed(path, cache, replacement):
                            self.refuse(fix, candidate)

    def test_coherent_candidate_replay_tree_and_fresh_proof_rewrite_does_not_confer_authority(self):
        for profile, dirty, _ in PROFILES:
            with self.subTest(profile=profile), self.context(profile, dirty) as fix:
                candidate = fix.validate()
                validation = fix._compile_validation
                altered = b'Different prose $y_(2)$\n'
                tree = parse(altered)
                replay = replace(validation.replay, content=altered, final_tree=tree)
                proof = bridge.certify(tree, 'main.typ', source_profile=profile)
                with ExitStack() as stack:
                    stack.enter_context(changed(candidate, 'sources', MappingProxyType({'main.typ': altered})))
                    stack.enter_context(changed(validation, 'replay', replay))
                    stack.enter_context(changed(validation, 'proof', proof))
                    self.refuse(fix, candidate)

    def test_revalidation_direct_transaction_bypass_and_postcommit_reporting_stay_separate(self):
        for profile, dirty, clean in PROFILES:
            with self.subTest(profile=profile), self.context(profile, dirty) as fix:
                old = fix.validate()
                candidate = fix.validate()
                self.refuse(fix, old)
                fix.apply(candidate)
                summary = fix.summary
                with changed(candidate, 'passes', True):
                    with self.assertRaises(bridge.ProtectedCoreError):
                        fix.compile_snapshot()
                self.assertIs(fix.summary, summary)
                self.assertEqual(summary.final_sha256, bridge.digest(self.source.read_bytes()))
                self.assertEqual(self.source.read_bytes(), clean)
                fix.assert_compile_current(fix.compile_snapshot())
            with self.subTest(profile=profile, bypass=True), self.context(profile, dirty) as fix:
                candidate = fix.validate()
                fix.transaction.apply(candidate)
                with self.assertRaises(bridge.ProtectedCoreError):
                    fix.compile_snapshot()
                self.assertEqual(self.source.read_bytes(), clean)


if __name__ == '__main__':
    unittest.main()
