"""Cross-file namespace hints are opt-in and never change sources."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_cli
import document_conditional_scope_namespaces as namespaces
from syntax_client import EXECUTABLE
from typst_lint import InputError


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalScopeNamespaceCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def run_cli(self, *args, format='json'):
        run = subprocess.run([sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
            '--root', str(self.root), '--entrypoint', 'main.typ', '--format', format, *args],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertFalse(run.stderr)
        return json.loads(run.stdout) if format == 'json' else run.stdout

    def test_cross_file_hints_in_json_and_text_preserve_sources_and_coverage(self):
        main = b'#include "child.typ"\n#local-tag-scope(s => [], prefix: "shared")'
        child = b'#local-scope-annotations(s => [], prefix: "shared")'
        (self.root / 'main.typ').write_bytes(main)
        (self.root / 'child.typ').write_bytes(child)
        ordinary = self.run_cli('--rule-set', 'pde')
        self.assertNotIn('conditional_scope_namespaces', ordinary)
        report = self.run_cli('--rule-set', 'pde', '--preview-conditional-scoped-calls')
        self.assertEqual(report['coverage'], ordinary['coverage'])
        self.assertEqual(report['diagnostics'], ordinary['diagnostics'])
        self.assertFalse(report['applied_fixes'] or report['compiler_invoked'])
        section = report['conditional_scope_namespaces']
        finding, = section['diagnostics']
        self.assertEqual({finding['path'], finding['comparison_site']['path']}, {'main.typ', 'child.typ'})
        self.assertFalse(section['runtime_binding_proved'] or section['execution_proved']
                         or section['execution_order_proved'] or section['edit_permission']
                         or section['compiler_permission'] or section['coverage_complete'])
        text = self.run_cli('--rule-set', 'pde', '--preview-conditional-scoped-calls', format='text')
        for phrase in ('Conditional scope namespaces:', 'core.unique-scope-namespace',
                       'Comparison:', 'condition:', 'no edit or compiler permission'):
            self.assertIn(phrase, text)
        self.assertEqual((self.root / 'main.typ').read_bytes(), main)
        self.assertEqual((self.root / 'child.typ').read_bytes(), child)

    def test_opt_out_and_budget(self):
        (self.root / 'main.typ').write_bytes(b'#local-tag-scope(s => [], prefix: "shared")')
        with patch.object(namespaces, 'analyze', side_effect=AssertionError('unexpected opt-in')):
            report = document_cli.report_document(str(self.root), 'main.typ', rule_set='pde')
        self.assertNotIn('conditional_scope_namespaces', report)
        with patch.object(namespaces, 'analyze', wraps=namespaces.analyze) as analyzed:
            report = document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                                  preview_conditional_scoped_calls=True)
        analyzed.assert_called_once()
        large = dict(report['conditional_scope_namespaces'])
        large['unresolved'] = ['fixture'] * 1001
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 1000), \
             patch.object(namespaces, 'analyze', return_value=large) as analyzed:
            with self.assertRaisesRegex(InputError, 'report item limit'):
                document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                             preview_conditional_scoped_calls=True)
            analyzed.assert_called_once()
        large['unresolved'] = []
        large['diagnostics'] = [dict(include_chain=[], include_unresolved=[],
            suppressions=[dict(rule_ids=['fixture'] * 1001)],
            comparison_site=dict(include_chain=[], include_unresolved=[], suppressions=[]))]
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 1000), \
             patch.object(namespaces, 'analyze', return_value=large):
            with self.assertRaisesRegex(InputError, 'report item limit'):
                document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                             preview_conditional_scoped_calls=True)
