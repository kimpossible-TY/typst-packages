"""mannot-scope annotation hints remain optional and diagnostic-only."""
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
import document_conditional_mannot_scope as mannot
from syntax_client import EXECUTABLE
from typst_lint import digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalMannotScopeCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='conditional-mannot-cli-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def run_cli(self, *args, format='json'):
        process = subprocess.run([sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
            '--root', str(self.root), '--entrypoint', 'main.typ', '--format', format, *args],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertFalse(process.stderr)
        return json.loads(process.stdout) if format == 'json' else process.stdout

    def test_json_text_and_source_preservation(self):
        main = (b'#include "child.typ"\n#let later() = [#mannot-scope(s => ['
                b'$ x #annot(<x>, [inside]) $\n#annot(<y>, [outside])])]' )
        child = (b'#mannot-scope(s => [\n'
                 b'// typst-lint: ignore-next core.annotation-in-display -- deliberate\n'
                 b'#annot(<z>, [note])\n])')
        (self.root / 'main.typ').write_bytes(main)
        (self.root / 'child.typ').write_bytes(child)
        options = ('--rule-set', 'pde', '--preview-conditional-scoped-calls')
        ordinary = self.run_cli('--rule-set', 'pde')
        self.assertNotIn('conditional_mannot_scope_annotations', ordinary)
        report = self.run_cli(*options)
        self.assertEqual(report['coverage'], ordinary['coverage'])
        self.assertEqual(report['diagnostics'], ordinary['diagnostics'])
        self.assertFalse(report['compiler_invoked'] or report['applied_fixes'])
        section = report['conditional_mannot_scope_annotations']
        self.assertTrue(section['duplicates_existing_hints_possible'])
        self.assertFalse(section['edit_permission'] or section['compiler_permission'] or section['coverage_complete'])
        rows = {row['path']: row for row in section['by_file']}
        self.assertEqual(rows['main.typ']['source_sha256'], digest(main))
        self.assertEqual(rows['main.typ']['wrappers_in_dynamic_context'], 1)
        self.assertEqual(rows['main.typ']['lexical_display_calls'], 1)
        self.assertEqual(len(rows['main.typ']['diagnostics']), 1)
        self.assertEqual(len(rows['child.typ']['suppressed']), 1)
        for row in rows.values():
            self.assertFalse(row['runtime_binding_proved'] or row['execution_proved'])
        text = self.run_cli(*options, format='text')
        for fragment in ('Conditional mannot-scope annotations:', 'calls lexically in display',
                         'may duplicate raw annotation hints', 'no edit or compiler permission', 'condition:'):
            self.assertIn(fragment, text)
        self.assertEqual((self.root / 'main.typ').read_bytes(), main)
        self.assertEqual((self.root / 'child.typ').read_bytes(), child)

    def test_opt_out_does_not_run_the_analyzer(self):
        (self.root / 'main.typ').write_bytes(b'No annotations.')
        with patch.object(mannot, 'analyze', side_effect=AssertionError('unexpected analysis')):
            result = document_cli.report_document(str(self.root), 'main.typ', rule_set='pde')
        self.assertNotIn('conditional_mannot_scope_annotations', result)


if __name__ == '__main__':
    unittest.main()
