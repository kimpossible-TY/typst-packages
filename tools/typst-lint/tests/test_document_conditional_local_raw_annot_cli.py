"""Local-scope raw annotation hints are optional and diagnostic-only."""
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
import document_conditional_local_raw_annot as local
from paragraph_rules import NESTED_GAP_UNRESOLVED
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ConditionalLocalRawAnnotCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='conditional-local-annot-cli-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def run_cli(self, *args, format='json'):
        process = subprocess.run([sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
            '--root', str(self.root), '--entrypoint', 'main.typ', '--format', format, *args],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertFalse(process.stderr)
        return json.loads(process.stdout) if format == 'json' else process.stdout

    def test_json_text_suppression_dynamic_context_and_source_preservation(self):
        main = (b'#include "child.typ"\n#let later() = [#local-scope-annotations(s => ['
                b'$ x #annot(<x>, [inside]) $\n#annot(<y>, [outside])])]' )
        child = (b'#local-scope-annotations(s => [\n'
                 b'// typst-lint: ignore-next core.annotation-in-display -- deliberate\n'
                 b'#annot(<z>, [note])\n])')
        (self.root / 'main.typ').write_bytes(main)
        (self.root / 'child.typ').write_bytes(child)
        ordinary = self.run_cli('--rule-set', 'pde')
        self.assertNotIn('conditional_local_scope_raw_annotations', ordinary)
        report = self.run_cli('--rule-set', 'pde', '--local-rule-hints')
        self.assertEqual(report['coverage'], ordinary['coverage'])
        self.assertEqual(report['diagnostics'], ordinary['diagnostics'])
        self.assertFalse(report['compiler_invoked'] or report['applied_fixes'])
        section = report['conditional_local_scope_raw_annotations']
        self.assertFalse(section['edit_permission'] or section['compiler_permission'] or section['coverage_complete'])
        rows = {row['path']: row for row in section['by_file']}
        self.assertEqual(rows['main.typ']['source_sha256'], digest(main))
        self.assertEqual(rows['main.typ']['wrappers_in_dynamic_context'], 1)
        self.assertEqual(rows['main.typ']['lexical_display_calls'], 1)
        self.assertEqual(len(rows['main.typ']['diagnostics']), 1)
        self.assertEqual(len(rows['child.typ']['suppressed']), 1)
        for row in rows.values():
            self.assertFalse(row['runtime_binding_proved'] or row['execution_proved'])
        text = self.run_cli('--rule-set', 'pde', '--local-rule-hints', format='text')
        for fragment in ('Conditional local-scope raw annotations:', 'calls lexically in display',
                         'no edit or compiler permission', 'condition:'):
            self.assertIn(fragment, text)
        self.assertEqual((self.root / 'main.typ').read_bytes(), main)
        self.assertEqual((self.root / 'child.typ').read_bytes(), child)

    def test_opt_out_does_not_run_and_report_limit_counts_calls(self):
        (self.root / 'main.typ').write_bytes(b'No annotations.')
        with patch.object(local, 'analyze', side_effect=AssertionError('unexpected analysis')):
            result = document_cli.report_document(str(self.root), 'main.typ', rule_set='pde')
        self.assertNotIn('conditional_local_scope_raw_annotations', result)
        fake = dict(path='main.typ', source_sha256=digest(b'No annotations.'), status='PARTIAL',
                    diagnostics=[], suppressed=[], unresolved=[], conditions=(), wrappers_examined=1001,
                    wrappers_in_dynamic_context=0, lexical_display_calls=1001, runtime_binding_proved=False,
                    execution_proved=False, coverage_complete=False, edit_permission=False, compiler_permission=False)
        with patch.object(local, 'analyze', return_value=fake) as analyzed, \
             patch.object(document_cli, 'MAX_REPORT_ITEMS', 1000):
            with self.assertRaisesRegex(InputError, 'report item limit'):
                document_cli.report_document(str(self.root), 'main.typ', rule_set='pde', local_rule_hints=True)
            analyzed.assert_called_once()

    def test_nested_gap_reasons_are_visible_with_bounded_text_output(self):
        source = b'\n'.join([b'#block[Text.#n[X]\n#paragraph_tab\nNext.]'] * 25)
        (self.root / 'main.typ').write_bytes(source)
        report = self.run_cli('--rule-set', 'pde', '--local-rule-hints')
        row, = [row for row in report['local_rule_hints']['coverage']
                if row['path'] == 'main.typ' and row['rule_id'] == 'pde.paragraph-marker-gap']
        reasons = [reason for reason in row['reasons'] if reason.startswith(NESTED_GAP_UNRESOLVED)]
        self.assertEqual(len(reasons), 25)
        text = self.run_cli('--rule-set', 'pde', '--local-rule-hints', format='text')
        self.assertEqual(text.count(NESTED_GAP_UNRESOLVED), 20)
        self.assertIn('5 additional nested paragraph marker boundaries omitted', text)
        self.assertEqual((self.root / 'main.typ').read_bytes(), source)
