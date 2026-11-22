"""Conditional cross-include explicit-prefix hints use issued graph occurrences."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import document_conditional_scope_namespaces as namespaces
import include_context
from syntax_client import EXECUTABLE, parse
from typst_lint import InputError


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalScopeNamespaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def capture(self, files, entrypoints=('main.typ',)):
        for path, source in files.items():
            (self.root / path).write_bytes(source)
        return include_context.load_include_context(str(self.root),
            tuple((entry, files[entry]) for entry in entrypoints))

    def test_prefix_collision_across_sources_is_conditional_and_not_ordered(self):
        context = self.capture({
            'main.typ': b'#include "child.typ"\n#local-tag-scope(s => [], prefix: "detail")',
            'child.typ': b'#local-scope-annotations(t => [], prefix: "detail")',
        })
        result = namespaces.analyze(context)
        finding, = result['diagnostics']
        self.assertEqual(finding['rule_id'], 'core.unique-scope-namespace')
        self.assertEqual({finding['path'], finding['comparison_site']['path']}, {'main.typ', 'child.typ'})
        self.assertEqual(finding['prefix'], 'detail')
        self.assertFalse(result['runtime_binding_proved'] or result['execution_proved']
                         or result['execution_order_proved'] or result['coverage_complete']
                         or result['edit_permission'] or result['compiler_permission'])
        self.assertTrue(result['conditions'])

    def test_repeated_include_is_a_distinct_conditional_occurrence(self):
        context = self.capture({
            'main.typ': b'#include "child.typ"\n#include "child.typ"',
            'child.typ': b'#local-tag-scope(s => [], prefix: "shared")',
        })
        finding, = namespaces.analyze(context)['diagnostics']
        self.assertEqual(finding['path'], 'child.typ')
        self.assertEqual(finding['comparison_site']['path'], 'child.typ')
        self.assertNotEqual(finding['include_chain'], finding['comparison_site']['include_chain'])

    def test_separate_entrypoints_are_not_compared(self):
        context = self.capture({
            'a.typ': b'#local-tag-scope(s => [], prefix: "same")',
            'b.typ': b'#local-tag-scope(s => [], prefix: "same")',
        }, entrypoints=('a.typ', 'b.typ'))
        self.assertFalse(namespaces.analyze(context)['diagnostics'])

    def test_distinct_or_automatic_prefixes_and_dynamic_wrappers_are_not_guessed(self):
        source = (b'#local-tag-scope(s => [])\n#local-tag-scope(s => [])\n'
                  b'#local-tag-scope(s => [], prefix: "a")\n'
                  b'#local-tag-scope(s => [], prefix: "b")\n'
                  b'#let stored = [#local-tag-scope(s => [], prefix: "a")]\n'
                  b'#local-tag-scope(s => [], prefix: key)')
        self.assertFalse(namespaces.analyze(self.capture({'main.typ': source}))['diagnostics'])

    def test_prefix_helper_refuses_unsupported_syntax_consistently(self):
        tree = parse(b'Hello')
        self.assertEqual(namespaces._prefix(tree, 0), (None, False))

    def test_suppressed_collision_is_separate(self):
        source = (b'#local-tag-scope(s => [], prefix: "detail")\n'
                  b'// typst-lint: ignore-next core.unique-scope-namespace -- intentional\n'
                  b'#local-tag-scope(s => [], prefix: "detail")')
        result = namespaces.analyze(self.capture({'main.typ': source}))
        self.assertFalse(result['diagnostics'])
        finding, = result['suppressed']
        self.assertTrue(finding['suppression_ambiguous'])
        directive, = finding['suppressions']
        self.assertEqual(directive['reason'], 'intentional')

    def test_suppression_survives_file_preorder_that_disagrees_with_source_order(self):
        source = (b'#include "child.typ"\n'
                  b'// typst-lint: ignore-next core.unique-scope-namespace -- intentional\n'
                  b'#local-tag-scope(s => [], prefix: "detail")')
        context = self.capture({'main.typ': source,
                                'child.typ': b'#local-tag-scope(s => [], prefix: "detail")'})
        result = namespaces.analyze(context)
        self.assertFalse(result['diagnostics'])
        finding, = result['suppressed']
        self.assertTrue(finding['suppression_ambiguous'])
        self.assertEqual(finding['comparison_site']['path'], 'main.typ')
        directive, = finding['comparison_site']['suppressions']
        self.assertEqual(directive['reason'], 'intentional')
        self.assertEqual(directive['rule_ids'], ['core.unique-scope-namespace'])
        self.assertEqual(source[directive['directive_start']:].splitlines()[0],
                         b'// typst-lint: ignore-next core.unique-scope-namespace -- intentional')

    def test_suppressed_first_site_does_not_hide_two_unsuppressed_sites(self):
        source = (b'// typst-lint: ignore-next core.unique-scope-namespace -- intentional\n'
                  b'#local-tag-scope(s => [], prefix: "detail")\n'
                  b'#local-tag-scope(s => [], prefix: "detail")\n'
                  b'#local-tag-scope(s => [], prefix: "detail")')
        result = namespaces.analyze(self.capture({'main.typ': source}))
        self.assertEqual(len(result['suppressed']), 1)
        self.assertEqual(len(result['diagnostics']), 1)
        self.assertFalse(result['diagnostics'][0]['comparison_site']['suppressions'])

    def test_dynamic_prefix_is_unresolved_while_automatic_prefix_is_not(self):
        for expression in (b'key', b'"a" + "b"', '"한글"'.encode(), b'"a\\u{62}"'):
            source = b'#local-tag-scope(s => [], prefix: ' + expression + b')'
            result = namespaces.analyze(self.capture({'main.typ': source}))
            self.assertFalse(result['diagnostics'])
            self.assertTrue(result['unresolved'])
        for source in (b'#local-tag-scope(s => [])', b'#local-tag-scope(s => [], prefix: auto)'):
            result = namespaces.analyze(self.capture({'main.typ': source}))
            self.assertFalse(result['diagnostics'] or result['unresolved'])

    def test_explicit_shadow_refuses_and_unissued_context_rejected(self):
        source = (b'#let local-tag-scope = (..args) => []\n'
                  b'#local-tag-scope(s => [], prefix: "p")\n'
                  b'#local-tag-scope(s => [], prefix: "p")')
        context = self.capture({'main.typ': source})
        result = namespaces.analyze(context)
        self.assertFalse(result['diagnostics'])
        self.assertTrue(result['unresolved'])
        with self.assertRaises(include_context.IncludeCaptureError):
            namespaces.analyze(replace(context))

    def test_occurrence_limit_refuses_entire_report(self):
        context = self.capture({'main.typ': b'#include "child.typ"\n#include "child.typ"',
                                'child.typ': b'#local-tag-scope(s => [], prefix: "p")'})
        with patch.object(namespaces, 'MAX_NAMESPACE_OCCURRENCES', 1):
            with self.assertRaisesRegex(InputError, 'occurrence limit'):
                namespaces.analyze(context)
        with patch.object(namespaces, 'MAX_NAMESPACE_REPORT_ITEMS', 4):
            with self.assertRaisesRegex(InputError, 'report item limit'):
                namespaces.analyze(context)

    def test_exact_pretty_json_byte_accounting_handles_unicode_and_escapes(self):
        result = dict(diagnostics=[], suppressed=[], unresolved=[], conditions=['조건'], status='PARTIAL')
        budget = namespaces._ReportBudget(result)
        prefix = '{\n  "conditional_scope_namespaces": '
        suffix = '\n}'
        def actual_bytes():
            outer = json.dumps({'conditional_scope_namespaces': result}, ensure_ascii=False,
                               sort_keys=True, indent=2)
            return len(outer.encode('utf-8')) - len(prefix.encode()) - len(suffix.encode())
        self.assertEqual(budget.bytes, actual_bytes())
        for name, value in (('unresolved', '한글\n"quoted"'),
                            ('diagnostics', {'text': '한글\t"quoted"', 'sub': [{'v': 1}]}),
                            ('diagnostics', {'other': [1, 2]}), ('suppressed', {'empty': []})):
            budget.append(name, value)
            self.assertEqual(budget.bytes, actual_bytes())

    def test_repeated_long_prefix_and_suppression_reason_refuse_before_full_report(self):
        main = b'\n'.join([b'#include "child.typ"'] * 8)
        for child in (b'#local-tag-scope(s => [], prefix: "' + b'a' * 3000 + b'")',
                      b'// typst-lint: ignore-next core.unique-scope-namespace -- '
                      + ('"한글" '.encode() * 500) + b'\n'
                      + b'#local-tag-scope(s => [], prefix: "p")'):
            with self.subTest(case=len(child)):
                context = self.capture({'main.typ': main, 'child.typ': child})
                with patch.object(namespaces, 'MAX_NAMESPACE_REPORT_BYTES', 10_000):
                    with self.assertRaisesRegex(InputError, 'report byte limit'):
                        namespaces.analyze(context)

    def test_unresolved_items_are_byte_limited_as_they_are_added(self):
        context = self.capture({'main.typ': b'#local-tag-scope(s => [], prefix: key)'})
        with patch.object(namespaces, 'MAX_NAMESPACE_REPORT_BYTES', 650):
            with self.assertRaisesRegex(InputError, 'report byte limit'):
                namespaces.analyze(context)
