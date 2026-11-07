"""Real closed-core bridge tests. Every document and writable input is temporary."""
from dataclasses import replace
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest import mock

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import protected_fix as bridge
from closed_core import ClosedCoreError
from edit_plan import Edit
from protected_fix import ProtectedCoreError, ProtectedCoreFix
from script_rule import ScriptResult, analyze as real_script
from syntax_client import EXECUTABLE, parse
from transactions import (EvidenceInput, GraphSnapshot, OfflineExclusive,
                          ReadLimits, TransactionError)
from typst_lint import CATALOG, Diagnostic, InputError, load_rules, parse_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class ProtectedFixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'consumer'
        self.root.mkdir()
        self.store = self.base / 'journals'
        self.store.mkdir(mode=0o700)
        self.target = self.root / 'main.typ'
        self.target.write_bytes(b'Preserved user prose. $x_1 + y^2$\n')
        self.config = self.root / '.typst-lint.toml'
        self.config.write_bytes(b'rule-set = "core"\ninclude = ["main.typ"]\n')
        self.catalog = self.base / 'catalog.json'
        self.catalog.write_bytes(CATALOG.read_bytes())
        self.patch = mock.patch.object(bridge, 'CATALOG', self.catalog)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        # Only this isolated fixture deliberately makes the offline assertion.
        self.lease = OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers()

    def context(self, **kwargs):
        options = dict(journal_directory=self.store, lease=self.lease)
        options.update(kwargs)
        return ProtectedCoreFix(self.root, 'main.typ', **options)

    def test_real_one_pass_and_fresh_final_analyses_are_not_nested_convergence(self):
        with mock.patch.object(bridge, 'analyze_script', wraps=real_script) as actual:
            with self.context() as fix:
                candidate = fix.validate()
                self.assertEqual(candidate.passes, 1)
                self.assertFalse(candidate.findings)
                self.assertEqual(tuple(p.tree.source for p in fix._history),
                                 (self.target.read_bytes(), candidate.sources['main.typ'],
                                  candidate.sources['main.typ']))
                result = fix.apply(candidate)
            self.assertEqual(len(actual.call_args_list), 6)  # 3 transaction + 3 independent
            self.assertTrue(all(c.kwargs['_verify'] is False for c in actual.call_args_list))
        self.assertEqual(result.modified, ('main.typ',))
        self.assertFalse(result.unresolved_findings)
        self.assertEqual(self.target.read_bytes(), b'Preserved user prose. $x_(1) + y^(2)$\n')

    def test_already_clean_is_noop_without_journal(self):
        for source in (b'', b'Ordinary prose.\n', b'Already $x_(1)$\n'):
            with self.subTest(source=source):
                self.target.write_bytes(source)
                with self.context() as fix:
                    token = fix.validate()
                    self.assertEqual(token.passes, 0)
                    result = fix.apply(token)
                self.assertEqual((result.journal, result.modified), (None, ()))
                self.assertEqual(self.target.read_bytes(), source)

    def test_config_is_explicit_closed_schema_and_exact_selection(self):
        configs = (
            b'', b'rule-set="core"', b'include=["main.typ"]',
            b'rule-set="pde"\ninclude=["main.typ"]',
            b'rule-set="core"\ninclude=["**/*.typ"]',
            b'rule-set="core"\ninclude=["main.typ","other.typ"]',
            b'rule-set="core"\ninclude=["other.typ"]',
            b'rule-set="core"\ninclude=["main.typ"]\nexclude=[]',
            b'rule-set="core"\ninclude=["main.typ"]\nignore=[]',
            b'rule-set="core"\ninclude=["main.typ"]\ncatalog="extra.json"',
            b'rule-set="core"\ninclude=["main.typ"]\n[capitalization]\nlanguage="en"',
            b'rule-set="core"\ninclude=["main.typ"]\nentrypoint="other.typ"',
            b'rule-set="core"\ninclude=["main.typ"]\nunknown=true',
            b'\xff', b'rule-set = [',
        )
        original = self.target.read_bytes()
        for raw in configs:
            with self.subTest(config=raw):
                self.config.write_bytes(raw)
                with self.assertRaises(ProtectedCoreError):
                    with self.context():
                        pass
                self.assertEqual(self.target.read_bytes(), original)
        self.config.unlink()
        with self.assertRaises((OSError, TransactionError)):
            with self.context():
                pass

    def test_same_literal_entrypoint_is_allowed(self):
        self.config.write_bytes(self.config.read_bytes() + b'entrypoint = "main.typ"\n')
        with self.context() as fix:
            self.assertEqual(fix.validate().passes, 1)

    def test_full_catalog_digest_blocks_schema_ids_repairs_and_pack_changes(self):
        original = self.catalog.read_bytes()
        mutations = []
        for change in ('id', 'repair', 'grade', 'pack', 'schema', 'extra'):
            data = json.loads(original)
            if change == 'id':
                data['rules'].pop()
            elif change in ('repair', 'grade'):
                data['rules'][0][change] = 'diagnostic-only' if change == 'repair' else 'error'
            elif change == 'pack':
                data['packs']['core'] = []
            elif change == 'schema':
                data['schema'] = 2
            else:
                data['injected'] = True
            mutations.append(json.dumps(data).encode())
        mutations.append(original + b'\n')  # Same parsed schema is still changed evidence.
        for raw in mutations:
            with self.subTest(length=len(raw)):
                self.catalog.write_bytes(raw)
                with self.assertRaisesRegex(ProtectedCoreError, 'digest'):
                    with self.context():
                        pass
        self.assertIn(b'$x_1', self.target.read_bytes())

    def test_captured_config_catalog_are_parsed_without_callback_file_reads(self):
        with self.context() as fix:
            snapshot = fix.transaction.snapshot
            with mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('callback reread')):
                state = fix._analyze_graph(snapshot)
            self.assertEqual(state.evaluated_rules, bridge.CORE_RULE_IDS)
            self.assertEqual(len(state.evaluated_rules), 15)
            self.assertEqual(state.examined_evidence,
                             frozenset((str(self.config), str(self.catalog))))
            self.assertEqual(state.parsed_sources, frozenset(('main.typ',)))

    def test_extra_unavailable_or_missing_graph_inputs_fail(self):
        with self.context() as fix:
            original = fix.transaction.snapshot
            variants = (
                replace(original, sources={**original.sources, 'other.typ': b'prose'}),
                replace(original, sources={}),
                replace(original, evidence={**original.evidence, '/unexamined': b'extra'}),
                replace(original, evidence={str(self.config): original.evidence[str(self.config)]}),
                replace(original, evidence={**original.evidence, str(self.catalog): None}),
                replace(original, evidence={**original.evidence, str(self.config): None}),
                replace(original, root=self.base),
            )
            for graph in variants:
                with self.subTest(graph=graph):
                    with self.assertRaises(ProtectedCoreError):
                        fix._analyze_graph(graph)

    def test_actual_rule_receives_immutable_captured_catalog(self):
        def inspect(tree, path, **kwargs):
            records = kwargs['catalog']
            self.assertIs(type(records), tuple)
            with self.assertRaises(TypeError):
                records[0]['id'] = 'core.forged'
            return real_script(tree, path, **kwargs)
        with self.context() as fix, mock.patch.object(bridge, 'analyze_script', side_effect=inspect):
            with mock.patch('script_rule.load_rules', side_effect=AssertionError('ambient catalog read')):
                self.assertEqual(fix.validate().passes, 1)

    def test_skipped_suppressed_parse_error_or_missing_script_findings_fail(self):
        initial = real_script(parse(self.target.read_bytes()), 'main.typ')
        variants = (
            replace(initial, skips=('not analyzed',)),
            replace(initial, suppressed=initial.diagnostics),
            replace(initial, parse_errors=((0, 1),)),
            replace(initial, diagnostics=()),
            replace(initial, edits=()),
            replace(initial, diagnostics=(replace(initial.diagnostics[0], column=99),)
                    + initial.diagnostics[1:]),
        )
        for result in variants:
            with self.subTest(result=result):
                with self.context() as fix, mock.patch.object(bridge, 'analyze_script', return_value=result):
                    with self.assertRaises(ProtectedCoreError):
                        fix.validate()
        self.assertIn(b'$x_1', self.target.read_bytes())

    def test_unsupported_prose_and_math_descendants_fail_before_writes(self):
        for source in (b'// ordinary comment\n$x_1$', b'#import "dependency.typ"\n$x_1$',
                       b'$sin(x)_1$', b'#let x = 1\n$x_1$', b'@reference $x_1$',
                       b'$x_1$ <label>', b'$x_$', b'$x_1 + image("x.png")$'):
            with self.subTest(source=source):
                self.target.write_bytes(source)
                with self.context() as fix:
                    with self.assertRaises((ClosedCoreError, InputError, ProtectedCoreError)):
                        fix.validate()
                self.assertEqual(self.target.read_bytes(), source)

    def test_operand_identities_survive_inserted_math_groups_and_no_final_findings(self):
        source = b'Prefix $a_b^c + x_1 + x_1 + y_(2)$ suffix\n'
        self.target.write_bytes(source)
        before = parse(source)
        with self.context() as fix:
            token = fix.validate()
            after = parse(token.sources['main.typ'])
            before_ids = tuple(r[0] for r in bridge._operand_records(before, 'main.typ'))
            after_ids = tuple(r[0] for r in bridge._operand_records(after, 'main.typ'))
            self.assertEqual(before_ids, after_ids)
            self.assertEqual(len(before_ids), len(set(before_ids)))
            self.assertTrue(all(r[3] for r in bridge._operand_records(after, 'main.typ')))
            self.assertFalse(token.findings)

    def test_transaction_only_token_cannot_skip_independent_bridge_comparison(self):
        with self.context() as fix:
            token = fix.transaction.validate(fix._analyze_graph, required_rules=bridge.CORE_RULE_IDS)
            with self.assertRaisesRegex(ProtectedCoreError, 'bridge session'):
                fix.apply(token)
        self.assertIn(b'$x_1', self.target.read_bytes())

    def test_independent_provenance_result_must_match_transaction_exactly(self):
        with self.context() as fix:
            replay = fix._independent_replay(fix.transaction.snapshot)
            for changed in (replace(replay, content=b'Different'), replace(replay, passes=0),
                            replace(replay, initial_results={bridge.SCRIPT: ScriptResult()})):
                with self.subTest(changed=changed):
                    with mock.patch.object(fix, '_independent_replay', return_value=changed):
                        with self.assertRaises(ProtectedCoreError):
                            fix.validate()
            with self.assertRaises(ProtectedCoreError):
                fix.apply(None)
        self.assertIn(b'$x_1', self.target.read_bytes())

    def test_bounds_are_passed_through_and_growth_fails_before_source_write(self):
        before = self.target.read_bytes()
        with self.context(limits=ReadLimits(max_source_bytes=len(before))) as fix:
            with self.assertRaisesRegex(TransactionError, 'source byte limit'):
                fix.validate()
        self.assertEqual(self.target.read_bytes(), before)

    def test_no_report_or_global_catalog_selected_subset_is_used(self):
        with mock.patch('typst_lint.report', side_effect=AssertionError('report cannot authorize')):
            with self.context() as fix:
                candidate = fix.validate()
                self.assertEqual(fix._history[0].analysis.implemented_rules, bridge.CORE_RULE_IDS)
                self.assertFalse(candidate.findings)


class PureCatalogTests(unittest.TestCase):
    def test_pure_parser_preserves_existing_catalog_loader(self):
        raw = CATALOG.read_bytes()
        with mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('pure parser read')):
            parsed = parse_rules(raw)
        self.assertEqual(parsed, load_rules())
        with self.assertRaises(InputError):
            parse_rules(bytearray(raw))


if __name__ == '__main__':
    unittest.main()
