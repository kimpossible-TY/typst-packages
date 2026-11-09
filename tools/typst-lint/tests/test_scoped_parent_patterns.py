"""Bounded source-only parent blocker inventory never grants fixes."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_cli
import scoped_parent_patterns as blockers
import test_document_modern_highlight_scoped_preview as fixtures
from typst_lint import InputError
from syntax_client import parse


@unittest.skipUnless(fixtures.fixtures.EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ScopedParentBlockerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ScopedReferencePreviewTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def issue(self, body):
        return self.fixture.run_source(body)

    def test_source_locations_explain_unverified_calls_and_wrapper(self):
        body = (b'#local-tag-scope(s => [\n'
                b'#local-scope-annotations(m => [#annot((m.tag)("x"))], parent:s,name:"child")\n'
                b'#mannot-scope(m => [#unknown()], parent:s,name:"other")\n'
                b'#highlighted[See #(s.ref)("one") $x$]\n])')
        source, _, analyzer, _ = self.issue(body)
        result = blockers.analyze(analyzer)
        first, second = result['findings']
        self.assertEqual((first['line'], first['wrapper_syntax']), (4, 'local-scope-annotations'))
        self.assertEqual((second['line'], second['wrapper_syntax']), (5, 'mannot-scope'))
        self.assertIn('unreviewed-child-calls', first['reasons'])
        self.assertIn('unreviewed-wrapper', second['reasons'])
        self.assertIn('annot', [c['callee_syntax'] for c in first['child_calls']])
        self.assertEqual(first['status'], 'SOURCE_PATTERN')
        self.assertFalse(result['coverage_complete'] or result['runtime_binding_proved'] or result['execution_proved']
                         or result['edit_permission'] or result['compiler_permission'] or result['render_equivalence_proved'])
        self.assertEqual((self.fixture.fixture.project / 'main.typ').read_bytes(), source)

    def test_outside_or_nonmatching_parent_is_not_scope_evidence(self):
        body = (b'#let s = none\n#unknown(parent:s)\n'
                b'#local-tag-scope(s => [#unknown(parent:other)])')
        _, _, analyzer, _ = self.issue(body)
        self.assertFalse(blockers.analyze(analyzer)['findings'])

    def test_direct_scope_parent_is_a_syntactic_pattern_even_when_shadowed(self):
        body = (b'#local-tag-scope(s => [#let s = none\n'
                b'#local-scope-annotations(m => [], parent:s,name:"child")])')
        _, _, analyzer, _ = self.issue(body)
        item, = blockers.analyze(analyzer)['findings']
        self.assertIn('parent-binding-not-proved', item['reasons'])
        self.assertFalse(item['runtime_binding_proved'] or item['execution_proved'])

    def test_budget_and_output_caps_report_explicit_incomplete(self):
        body = (b'#local-tag-scope(s => [#local-scope-annotations(m => [#f()], parent:s,name:"x")])')
        _, _, analyzer, _ = self.issue(body)
        for kwargs in ({'max_work': 1}, {'max_items': 7}):
            with self.subTest(kwargs=kwargs):
                result = blockers.analyze(analyzer, **kwargs)
                self.assertEqual(result['status'], 'INCOMPLETE')
                self.assertTrue(result['truncated'])
                self.assertIn('budget exceeded', result['reason'])
                self.assertLessEqual(result['report_items'], kwargs.get('max_items', blockers.MAX_ITEMS))
                self.assertFalse(result['edit_permission'] or result['compiler_permission'])

    def test_long_valid_argument_list_is_charged_before_traversal(self):
        options = b','.join(('option' + str(i) + ': 0').encode() for i in range(400))
        source = (b'#local-tag-scope(s => [#local-scope-annotations(m => [], '
                  b'parent:s,name:"x",' + options + b')])')
        tree = parse(source)
        self.assertFalse(tree.nodes[0].errors)
        candidates = [i for i, node in enumerate(tree.nodes) if node.kind == 'Args'
                      and source[node.start:node.end].startswith(b'(m => []')]
        args, = candidates
        initial = 3 * len(tree.nodes) + 2 * len(source)
        with patch.object(blockers.graph, 'significant', wraps=blockers.graph.significant) as seen:
            with self.assertRaises(blockers._Limit):
                blockers._source(tree, 'main.typ', [initial + 100], lambda row: None)
        self.assertFalse(any(call.args[1] == args for call in seen.call_args_list))

    def test_missing_or_mutated_evidence_is_rejected(self):
        with self.assertRaises(InputError):
            blockers.analyze(object())
        _, _, analyzer, _ = self.issue(b'#local-tag-scope(s => [plain])')
        object.__setattr__(analyzer._session._graph.sources[0], 'source', b'changed')
        with self.assertRaises(InputError):
            blockers.analyze(analyzer)

    def test_exhausted_shared_item_budget_keeps_existing_scoped_results(self):
        body = (b'#local-tag-scope(s => [#local-scope-annotations(m => [#effect()], parent:s,name:"child")\n'
                b'#highlighted[Text $x$ end.]])')
        self.issue(body)
        opts = dict(rule_set='pde', package_path=str(self.fixture.fixture.packages),
                    footnote_cache_path=str(self.fixture.fixture.cache), modern_highlight_evidence=True,
                    preview_conditional_modern_highlights=True, preview_conditional_modern_scoped_references=True,
                    preview_conditional_scoped_calls=True)
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 92):
            off = document_cli.report_document(str(self.fixture.fixture.project), 'main.typ', **opts)
            on = document_cli.report_document(str(self.fixture.fixture.project), 'main.typ',
                                              inspect_scoped_parent_patterns=True, **opts)
        inventory = on.pop('scoped_parent_patterns')
        self.assertEqual(on, off)
        self.assertEqual(inventory['status'], 'INCOMPLETE')
        self.assertTrue(inventory['truncated'])
        self.assertIn('budget', inventory['reason'])

    def test_unavailable_variant_does_not_consume_existing_item_budget(self):
        body = (b'#local-tag-scope(s => [#local-scope-annotations(m => [#effect()], parent:s,name:"child")\n'
                b'#highlighted[Text $x$ end.]])')
        self.issue(body)
        shutil.rmtree(self.fixture.fixture.target)
        shutil.copytree(fixtures.fixtures.OLD, self.fixture.fixture.target)
        opts = dict(rule_set='pde', package_path=str(self.fixture.fixture.packages),
                    footnote_cache_path=str(self.fixture.fixture.cache), modern_highlight_evidence=True,
                    preview_conditional_modern_highlights=True, preview_conditional_modern_scoped_references=True,
                    preview_conditional_scoped_calls=True)
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 65):
            off = document_cli.report_document(str(self.fixture.fixture.project), 'main.typ', **opts)
            on = document_cli.report_document(str(self.fixture.fixture.project), 'main.typ',
                                              inspect_scoped_parent_patterns=True, **opts)
        inventory = on.pop('scoped_parent_patterns')
        self.assertEqual(on, off)
        self.assertEqual(inventory['status'], 'UNAVAILABLE')
        self.assertEqual(inventory['report_items'], 0)
        self.assertFalse(inventory['edit_permission'] or inventory['compiler_permission'])

    def test_near_full_report_byte_budget_never_discards_existing_results(self):
        body = (b'#local-tag-scope(s => [#local-scope-annotations(m => [#effect()], parent:s,name:"child")\n'
                b'#highlighted[Text $x$ end.]])')
        self.issue(body)
        opts = dict(rule_set='pde', package_path=str(self.fixture.fixture.packages),
                    footnote_cache_path=str(self.fixture.fixture.cache), modern_highlight_evidence=True,
                    preview_conditional_modern_highlights=True, preview_conditional_modern_scoped_references=True,
                    preview_conditional_scoped_calls=True)
        root = str(self.fixture.fixture.project)
        off = document_cli.report_document(root, 'main.typ', **opts)
        size = len(json.dumps(off, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8'))
        with patch.object(document_cli, 'MAX_MODERN_REPORT_BYTES', size + 512):
            on = document_cli.report_document(root, 'main.typ', inspect_scoped_parent_patterns=True, **opts)
        inventory = on.pop('scoped_parent_patterns')
        self.assertEqual(on, off)
        self.assertEqual(inventory['status'], 'INCOMPLETE')
        self.assertTrue(inventory['truncated'])
        with patch.object(document_cli, 'MAX_MODERN_REPORT_BYTES', size):
            almost_full = document_cli.report_document(root, 'main.typ', inspect_scoped_parent_patterns=True, **opts)
        self.assertEqual(almost_full, off)

    def test_explicit_cli_option_preserves_preview_and_original_source(self):
        body = (b'#local-tag-scope(s => [#local-scope-annotations(m => [#effect()], parent:s,name:"child")\n'
                b'#highlighted[Text $x$ end.]])')
        source, _, _, _ = self.issue(body)
        opts = dict(rule_set='pde', package_path=str(self.fixture.fixture.packages),
                    footnote_cache_path=str(self.fixture.fixture.cache), modern_highlight_evidence=True,
                    preview_conditional_modern_highlights=True,
                    preview_conditional_modern_scoped_references=True)
        off = document_cli.report_document(str(self.fixture.fixture.project), 'main.typ', **opts)
        on = document_cli.report_document(str(self.fixture.fixture.project), 'main.typ',
                                         inspect_scoped_parent_patterns=True, **opts)
        self.assertNotIn('scoped_parent_patterns', off)
        self.assertEqual(on['conditional_modern_highlight_previews'], off['conditional_modern_highlight_previews'])
        self.assertEqual(len(on['scoped_parent_patterns']['findings']), 1)
        self.assertFalse(on['compiler_invoked'] or on['applied_fixes'])
        command = [sys.executable, str(TOOL / 'typst-lint'), 'inspect-document', '--root',
                   str(self.fixture.fixture.project), '--entrypoint', 'main.typ', '--rule-set', 'pde',
                   '--package-path', str(self.fixture.fixture.packages), '--footnote-cache-path',
                   str(self.fixture.fixture.cache), '--modern-highlight-evidence',
                   '--inspect-scoped-parent-patterns', '--format', 'json']
        run = subprocess.run(command, text=True, capture_output=True, timeout=30)
        payload = json.loads(run.stdout)
        self.assertEqual(run.returncode, payload['status'])
        self.assertEqual(len(payload['scoped_parent_patterns']['findings']), 1)
        self.assertFalse(payload['compiler_invoked'] or payload['applied_fixes'])
        self.assertEqual((self.fixture.fixture.project / 'main.typ').read_bytes(), source)
        with self.assertRaisesRegex(ValueError, 'requires --modern-highlight-evidence'):
            document_cli.report_document(str(self.fixture.fixture.project), 'main.typ', rule_set='pde',
                                         inspect_scoped_parent_patterns=True)
