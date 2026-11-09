"""Root-only relative repairs using real parser, capture and temp consumers."""
from contextlib import contextmanager
from collections.abc import Mapping
import copy
import gc
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
from types import MappingProxyType
import unittest
from unittest import mock

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import captured_relative_core as core
import captured_relative_fix as bridge
import captured_relative_inputs as inputs
from edit_plan import Edit
from script_rule import ScriptResult, analyze as actual_script
from syntax_client import EXECUTABLE, parse
from transactions import GraphSnapshot, OfflineExclusive, TransactionError
from typst_lint import CATALOG, InputError

_MISSING = object()


@contextmanager
def changed(value, name, replacement=_MISSING):
    previous = getattr(value, name)
    if replacement is _MISSING:
        object.__delattr__(value, name)
    else:
        object.__setattr__(value, name, replacement)
    try:
        yield
    finally:
        object.__setattr__(value, name, previous)


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class CapturedRelativeFixTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'consumer'
        self.root.mkdir()
        self.store = self.base / 'journals'
        self.store.mkdir(mode=0o700)
        self.target = self.put('main.typ', b'#import "values.typ": count\nValue #count $x_count$\n')
        self.module = self.put('values.typ', b'#let count = 7\n')
        self.config = self.put('.typst-lint.toml', b'rule-set="core"\ninclude=["main.typ"]\n')
        self.catalog = self.base / 'catalog.json'
        self.catalog.write_bytes(CATALOG.read_bytes())
        patch = mock.patch.object(inputs, 'CATALOG', self.catalog)
        patch.start()
        self.addCleanup(patch.stop)
        self.lease = OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers()

    def put(self, path, raw):
        path = self.root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def context(self, **kwargs):
        options = dict(journal_directory=self.store, lease=self.lease)
        options.update(kwargs)
        return bridge.ProtectedCapturedRelativeFix(self.root, 'main.typ', **options)

    def refused(self, fix, candidate):
        original = self.target.read_bytes()
        with mock.patch.object(fix._tx, '_write_new', side_effect=AssertionError('write before refusal')):
            with self.assertRaises(TransactionError):
                fix.apply(candidate)
        self.assertEqual(self.target.read_bytes(), original)
        self.assertFalse(fix._tx._used)

    def test_real_root_only_repair_and_committed_graph_use_one_owned_transaction(self):
        original_module = self.module.read_bytes()
        self.module.chmod(0o444)
        with self.context() as fix:
            self.assertIs(fix._tx, fix._session._tx)
            with self.assertRaises(TransactionError):
                _ = fix.summary
            token = fix.validate()
            self.assertEqual(token.passes, 1)
            self.assertFalse(token.findings)
            self.assertEqual(tuple(token.sources), ('main.typ',))
            self.assertEqual(len(fix._history), 3)
            self.assertTrue(all(len(p.module_results) == 1 for p in fix._history))
            result = fix.apply(token)
            snapshot = fix.committed_graph_snapshot()
            self.assertIs(snapshot, fix.committed_graph_snapshot())
            fix.assert_committed_graph_current(snapshot)
            self.assertIs(snapshot.sources['main.typ'], snapshot._receipt.sources['main.typ'])
            self.assertIs(snapshot.sources['values.typ'], fix._capture.captures['values.typ'].source)
            self.assertEqual(snapshot.captures['main.typ'].source, snapshot.sources['main.typ'])
            self.assertEqual(len(snapshot.required_rule_ids), 15)
            self.assertEqual(snapshot.evaluated_rule_ids, (bridge.SCRIPT,))
            self.assertEqual(len(snapshot.structural_reasons), 14)
            self.assertEqual(result.modified, ('main.typ',))
            self.assertEqual(fix.summary.final_sha256, bridge.digest(self.target.read_bytes()))
            self.assertEqual(fix.summary.modified_paths, result.modified)
            with self.assertRaises(TransactionError):
                fix._session.assert_current(fix._capture)
            fix._session._assert_binding(fix._capture)
        self.assertEqual(self.module.read_bytes(), original_module)
        self.assertEqual(self.module.stat().st_mode & 0o777, 0o444)
        with self.assertRaises(TransactionError):
            fix.assert_committed_graph_current(snapshot)

    def test_nested_script_fraction_wrapper_and_scalar_ownership(self):
        source = (b'#import "values.typ": count\n'
                  b'= Heading #count\n*Strong $x_count^2$*\n'
                  b'- Item $x_(y_count) + (count/2)^count$\n')
        self.target.write_bytes(source)
        with self.context() as fix:
            candidate = fix.validate()
            before = fix._capture.proof
            after = fix._validation.proof
            self.assertTrue(core._same_exact(bridge._semantics(before, 'main.typ'),
                                            bridge._semantics(after, 'main.typ')))
            self.assertEqual(before.edges, after.edges)
            self.assertEqual(before.modules, after.modules)
            fix.apply(candidate)
            self.assertEqual(self.target.read_bytes(), candidate.sources['main.typ'])
            self.assertEqual(len(fix.summary.applied_insertions), len(bridge._script_insertions(parse(source))))

    def test_diamond_alias_routes_builtin_shadowing_and_punctuation_strings(self):
        self.put('seed.typ', b'#let seed = 7\n#let text = "comma,! (test) ^_"\n')
        self.put('left.typ', b'#import "seed.typ": seed as incoming\n#let left = incoming\n')
        self.put('right.typ', b'#import "seed.typ": seed\n#let right = seed\n')
        self.target.write_bytes(b'#import "left.typ": left as alpha\n'
                                b'#import "right.typ": right\n#import "seed.typ": text\n'
                                b'#text $alpha_right$\n')
        with self.context() as fix:
            candidate = fix.validate()
            before = fix._capture.proof
            after = fix._validation.proof
            self.assertEqual(before.modules, after.modules)
            self.assertNotEqual(before.root_bindings[0].provenance, before.root_bindings[1].provenance)
            self.assertTrue(core._same_exact(bridge._semantics(before, 'main.typ'),
                                            bridge._semantics(after, 'main.typ')))
            fix.apply(candidate)
            self.assertEqual(fix.committed_graph_snapshot().sources['seed.typ'],
                             self.root.joinpath('seed.typ').read_bytes())

    def test_bom_crlf_and_noop_are_preserved_without_journal(self):
        for source in (b'\xef\xbb\xbf#import "values.typ": count\r\n#count $x_count$\r\n',
                       b'#import "values.typ": count\n#count $x_(count)$\n', b''):
            with self.subTest(source=source):
                self.target.write_bytes(source)
                with self.context() as fix:
                    candidate = fix.validate()
                    result = fix.apply(candidate)
                    snapshot = fix.committed_graph_snapshot()
                    self.assertEqual(snapshot.sources['main.typ'], self.target.read_bytes())
                    if candidate.passes:
                        self.assertTrue(self.target.read_bytes().startswith(b'\xef\xbb\xbf'))
                        self.assertEqual(self.target.read_bytes().count(b'\r\n'), source.count(b'\r\n'))
                    else:
                        self.assertEqual((result.journal, result.modified), (None, ()))
                        self.assertEqual(self.target.read_bytes(), source)

    def test_every_source_executes_script_with_retained_catalog_without_ambient_reads(self):
        paths = []
        def analyze(tree, path, **kwargs):
            paths.append(path)
            self.assertIs(type(kwargs['catalog']), tuple)
            self.assertFalse(kwargs['_verify'])
            return actual_script(tree, path, **kwargs)
        with self.context() as fix, mock.patch.object(bridge, 'analyze_script', side_effect=analyze):
            with mock.patch('script_rule.load_rules', side_effect=AssertionError('ambient catalog read')):
                candidate = fix.validate()
                state = fix._history[0].analysis
                self.assertEqual(state.evaluated_rules, bridge.CORE_RULE_IDS)
                self.assertEqual(state.examined_evidence, frozenset(fix._baseline_graph.evidence))
                self.assertEqual(state.parsed_sources, frozenset(('main.typ',)))
                self.assertEqual(paths.count('main.typ'), 6)
                self.assertEqual(paths.count('values.typ'), 6)
                fix.apply(candidate)

    def test_callback_requires_exact_root_and_retained_evidence_byte_identities(self):
        with self.context() as fix:
            graph = fix._tx.snapshot
            variants = (
                replace(graph, sources=MappingProxyType({})),
                replace(graph, sources=MappingProxyType({**graph.sources, 'other.typ': b''})),
                replace(graph, sources=dict(graph.sources)),
                replace(graph, evidence=MappingProxyType({})),
                replace(graph, evidence=MappingProxyType({**graph.evidence, 'unexamined': b''})),
                replace(graph, evidence=MappingProxyType({p: bytes(bytearray(raw))
                    if raw is not None else raw for p, raw in graph.evidence.items()})),
                replace(graph, root=Path(str(graph.root))),
            )
            for variant in variants:
                with self.subTest(variant=variant), self.assertRaises(TransactionError):
                    fix._analyze_graph(variant)

    def test_oversized_callback_and_committed_mapping_backings_refuse_before_traversal(self):
        class OversizedMapping(Mapping):
            def __len__(self):
                return 1000

            def __iter__(self):
                raise AssertionError('Oversized callback mapping must not be traversed')

            def __getitem__(self, key):
                raise AssertionError('Oversized callback mapping must not be read')

        with self.context() as fix:
            graph = replace(fix._tx.snapshot, evidence=MappingProxyType(OversizedMapping()))
            with self.assertRaises(bridge.CapturedRelativeFixError):
                fix._context(graph)

        for name in ('sources', 'evidence'):
            with self.subTest(mapping=name), self.context() as fix:
                fix.apply(fix.validate())
                receipt = fix._tx.committed_snapshot()
                original = getattr(receipt, name)
                backing = next(value for value in gc.get_referents(original)
                               if type(value) is dict)
                backing.update({f'added-{index}': b'' for index in range(1000)})

                # This is the genuine unchanged map object also retained by the
                # transaction graph. Refuse its enlarged backing before tuple()
                # traverses even one of the reflected extra entries.
                def bounded_tuple(value=()):
                    if value is original:
                        raise AssertionError('Oversized committed mapping traversed')
                    return tuple(value)

                with mock.patch.object(bridge, 'tuple', bounded_tuple, create=True):
                    with self.assertRaises(bridge.CapturedRelativeFixError):
                        fix.committed_graph_snapshot()

    def test_initial_language_is_input_but_candidate_grammar_drift_is_safety(self):
        self.target.write_bytes(b'#import "values.typ": count\n#read("secret")\n')
        with self.assertRaises(InputError):
            with self.context():
                pass
        self.target.write_bytes(b'#import "values.typ": count\n$x_count$\n')
        with self.context() as fix:
            graph = fix._tx.snapshot
            changed_graph = replace(graph, sources=MappingProxyType({'main.typ': b'#import "values.typ": count\n#read("secret")\n'}))
            with self.assertRaises(bridge.CapturedRelativeFixError):
                fix._analyze_graph(changed_graph)

    def test_skips_suppression_parse_errors_missing_diagnostics_and_extra_module_results_refuse(self):
        for which in ('skips', 'suppressed', 'parse_errors', 'diagnostics', 'edits', 'module'):
            with self.subTest(which=which), self.context() as fix:
                def analyze(tree, path, **kwargs):
                    result = actual_script(tree, path, **kwargs)
                    if which == 'module' and path == 'values.typ':
                        return replace(result, skips=('unchecked module',))
                    if path != 'main.typ':
                        return result
                    if which in ('diagnostics', 'edits'):
                        return replace(result, **{which: ()})
                    if which == 'suppressed':
                        return replace(result, suppressed=result.diagnostics)
                    if which == 'parse_errors':
                        return replace(result, parse_errors=((0, 1),))
                    if which == 'skips':
                        return replace(result, skips=('unchecked',))
                    return result
                with mock.patch.object(bridge, 'analyze_script', side_effect=analyze):
                    with self.assertRaises(TransactionError):
                        fix.validate()
                self.assertIn(b'$x_count$', self.target.read_bytes())

    def test_second_pass_or_independent_replay_disagreement_refuses(self):
        with self.context() as fix:
            actual = fix._independent_replay
            def wrong(snapshot):
                replay = actual(snapshot)
                return replace(replay, content=replay.content + b'changed')
            with mock.patch.object(fix, '_independent_replay', side_effect=wrong):
                with self.assertRaises(TransactionError):
                    fix.validate()
        with self.context() as fix:
            def second(tree, path, **kwargs):
                result = actual_script(tree, path, **kwargs)
                if path == 'main.typ' and not result.edits:
                    return replace(result, edits=(Edit(len(tree.source), len(tree.source), b'()'),))
                return result
            with mock.patch.object(bridge, 'analyze_script', side_effect=second):
                with self.assertRaises(TransactionError):
                    fix.validate()

    def test_candidate_wrapper_and_private_transaction_token_fields_are_independently_pinned(self):
        with self.context() as fix:
            candidate = fix.validate()
            for target in (candidate, candidate._token):
                variants = (('sources', MappingProxyType(dict(candidate.sources))),
                            ('sources', dict(candidate.sources)),
                            ('sources', MappingProxyType({'other.typ': b'changed'})),
                            ('passes', bool(candidate.passes)), ('passes', 1.0),
                            ('findings', frozenset(('forged',))), ('_owner', object()),
                            ('_seal', object()), ('sources', _MISSING), ('passes', _MISSING))
                for name, value in variants:
                    with self.subTest(target=type(target), field=name), changed(target, name, value):
                        self.refused(fix, candidate)
            fix.apply(candidate)

    def test_replay_tree_proof_history_origins_and_report_fields_are_pinned(self):
        with self.context() as fix:
            candidate = fix.validate()
            v = fix._validation
            variants = (
                (v.replay, 'baseline', b'other'), (v.replay, 'content', b'other'),
                (v.replay, 'passes', True), (v.replay, 'steps', ()),
                (v.replay.final_tree.nodes[0], 'errors', 0),
                (v.replay.final_tree, 'source', b'other'),
                (v.replay.edits[0].contributions[0], 'pass_number', True),
                (v.proof, 'root_grammar_version', 'wrong'),
                (v.proof.root_bindings[0], 'value', True),
                (v.proof.root_bindings[0].origin, 'name', 'other'),
                (v.proof.root_bindings[0].provenance[0], 'source_sha256', '0' * 64),
                (v.history[0].analysis, 'examined_evidence', frozenset()),
                (v.history[0].module_results[0][1], 'skips', ('unchecked',)),
                (v.summary, 'final_sha256', '0' * 64),
                (fix._capture.report, 'target', 'other.typ'),
                (fix._capture.report, 'sources', _MISSING),
                (v, 'captures', _MISSING), (v.proof, 'edges', _MISSING),
            )
            for target, name, value in variants:
                with self.subTest(field=name), changed(target, name, value):
                    self.refused(fix, candidate)
            fix.apply(candidate)

    def test_cyclic_oversized_and_foreign_containers_refuse_before_traversal(self):
        class HostileTuple(tuple):
            def __iter__(self):
                raise AssertionError('foreign container traversed')
        with self.context() as fix:
            candidate = fix.validate()
            replay = fix._validation.replay
            cyclic = {}
            proxy = MappingProxyType(cyclic)
            cyclic['cycle'] = proxy
            variants = ((replay, 'final_results', proxy),
                        (replay, 'steps', (replay.steps,)),
                        (replay.final_tree, 'nodes', replay.final_tree.nodes * 1000),
                        (replay.final_tree, 'nodes', HostileTuple(replay.final_tree.nodes)),
                        (replay, 'final_tree', object()),
                        (candidate, 'sources', MappingProxyType({str(i): b'' for i in range(1000)})))
            for target, name, value in variants:
                with self.subTest(field=name), changed(target, name, value):
                    self.refused(fix, candidate)
            fix.apply(candidate)

    def test_equal_values_do_not_erase_origin_or_full_module_routes(self):
        self.module.write_bytes(b'#let count = 7\n#let equal = 7\n')
        with self.context() as fix:
            proof = fix._capture.proof
            altered = replace(proof.root_bindings[0], origin=replace(proof.root_bindings[0].origin, name='equal'))
            copy_proof = copy.copy(proof)
            object.__setattr__(copy_proof, 'root_bindings', (altered,))
            self.assertFalse(core._same_exact(bridge._semantics(proof, 'main.typ'),
                                             bridge._semantics(copy_proof, 'main.typ')))
            route = proof.root_bindings[0].provenance
            altered = replace(proof.root_bindings[0], provenance=(replace(route[0], span=(0, 1)), *route[1:]))
            object.__setattr__(copy_proof, 'root_bindings', (altered,))
            self.assertFalse(core._same_exact(bridge._semantics(proof, 'main.typ'),
                                             bridge._semantics(copy_proof, 'main.typ')))

    def test_candidate_graph_byte_and_node_budgets_are_rechecked(self):
        with self.context() as fix:
            root_bytes = len(fix._capture.captures['main.typ'].source)
            total = sum(len(t.source) for t in fix._capture.captures.values())
            nodes = sum(len(t.nodes) for t in fix._capture.captures.values())
            for name, maximum in (('MAX_FILE_BYTES', root_bytes), ('MAX_SOURCE_BYTES', total),
                                  ('MAX_TOTAL_NODES', nodes)):
                with self.subTest(budget=name), mock.patch.object(core, name, maximum):
                    with self.assertRaises(TransactionError):
                        fix.validate()
            self.assertIn(b'$x_count$', self.target.read_bytes())

    def test_direct_transaction_apply_does_not_create_bridge_provenance(self):
        with self.context() as fix:
            candidate = fix.validate()
            fix._tx.apply(candidate._token)
            fix._tx.committed_snapshot()
            with self.assertRaises(TransactionError):
                fix.committed_graph_snapshot()
            with self.assertRaises(TransactionError):
                _ = fix.summary
            with self.assertRaises(TransactionError):
                fix.apply(candidate)

    def test_copies_expiry_reuse_and_bare_receipts_are_not_bridge_authority(self):
        with self.context() as fix:
            candidate = fix.validate()
            for other in (copy.copy(candidate), candidate._token, fix._capture, fix._capture.report):
                with self.assertRaises(TransactionError):
                    fix.apply(other)
            fix.apply(candidate)
            snapshot = fix.committed_graph_snapshot()
            for other in (copy.copy(snapshot), snapshot._receipt, fix._capture, snapshot.proof):
                with self.assertRaises(TransactionError):
                    fix.assert_committed_graph_current(other)
            with self.assertRaises(TransactionError):
                fix.apply(candidate)
            with self.assertRaises(TransactionError):
                fix.validate()
        with self.assertRaises(TransactionError):
            fix.assert_committed_graph_current(snapshot)
        with self.assertRaises(TransactionError):
            with fix:
                pass

    def test_postcommit_drift_preserves_valid_fix_and_honest_summary(self):
        with self.context() as fix:
            candidate = fix.validate()
            fix.apply(candidate)
            summary = fix.summary
            snapshot = fix.committed_graph_snapshot()
            self.module.write_bytes(b'#let count = 8\n')
            with self.assertRaises(TransactionError):
                fix.assert_committed_graph_current(snapshot)
            with self.assertRaises(TransactionError):
                fix.committed_graph_snapshot()
            self.assertIs(fix.summary, summary)
            self.assertEqual(self.target.read_bytes(), candidate.sources['main.typ'])

    def test_preapply_capture_and_filesystem_drift_refuses(self):
        for which in ('root', 'module', 'config', 'catalog'):
            with self.subTest(which=which):
                path = {'root': self.target, 'module': self.module,
                        'config': self.config, 'catalog': self.catalog}[which]
                before = path.read_bytes()
                with self.context() as fix:
                    candidate = fix.validate()
                    path.write_bytes(before + b'\n')
                    with self.assertRaises(TransactionError):
                        fix.apply(candidate)
                    self.assertFalse(fix._tx._used)
                path.write_bytes(before)

    def test_failed_actual_apply_grants_no_receipt_and_keeps_modules(self):
        seen = []
        def fail(point, path):
            seen.append(point)
            if point == 'before_replace':
                raise OSError('injected rename fault')
        original = self.target.read_bytes()
        module = self.module.read_bytes()
        with self.context(fault=fail) as fix:
            candidate = fix.validate()
            with self.assertRaises(TransactionError):
                fix.apply(candidate)
            with self.assertRaises(TransactionError):
                fix.committed_graph_snapshot()
            with self.assertRaises(TransactionError):
                _ = fix.summary
        self.assertIn('before_replace', seen)
        self.assertEqual(self.target.read_bytes(), original)
        self.assertEqual(self.module.read_bytes(), module)


if __name__ == '__main__':
    unittest.main()
