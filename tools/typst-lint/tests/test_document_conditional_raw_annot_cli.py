"""Optional raw annot hints remain separate from other document checks."""
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
import document_conditional_raw_annot as raw_annot
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalRawAnnotCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='conditional-raw-annot-cli-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def run_cli(self, *args, format='json'):
        result = subprocess.run([sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
            '--root', str(self.root), '--entrypoint', 'main.typ', '--format', format, *args],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(result.stderr)
        return json.loads(result.stdout) if format == 'json' else result.stdout

    def test_opt_in_json_text_suppression_and_independent_counts(self):
        main = (b'#include "child.typ"\n#import "styles.typ": *\n'
                b'#proof[$x$\n#annot(<x>, pos: top)[Note.]\n]')
        child = (b'#proof[\n// typst-lint: ignore-next core.annotation-in-display -- intentional\n'
                 b'#annot(<y>, dx: 1pt)[Other.]\n]')
        (self.root / 'main.typ').write_bytes(main)
        (self.root / 'child.typ').write_bytes(child)
        (self.root / 'styles.typ').write_bytes(b'#let other = none\n')
        ordinary = self.run_cli('--rule-set', 'pde')
        self.assertNotIn('conditional_raw_annotations', ordinary)
        result = self.run_cli('--rule-set', 'pde', '--preview-conditional-scoped-calls')
        self.assertEqual(result['coverage'], ordinary['coverage'])
        self.assertEqual(result['diagnostics'], ordinary['diagnostics'])
        self.assertEqual(result['conditional_diagnostics'], ordinary['conditional_diagnostics'])
        section = result['conditional_raw_annotations']
        self.assertFalse(section['edit_permission'] or section['compiler_permission'] or section['coverage_complete'])
        self.assertFalse(result['applied_fixes'] or result['compiler_invoked'])
        rows = {row['path']: row for row in section['by_file']}
        for path, source, key in (('main.typ', main, 'diagnostics'), ('child.typ', child, 'suppressed')):
            row = rows[path]
            finding, = row[key]
            self.assertEqual(row['status'], 'PARTIAL')
            self.assertEqual(row['source_sha256'], digest(source))
            self.assertEqual(finding['rule_id'], 'core.annotation-in-display')
            self.assertTrue(source[finding['start']:finding['end']].startswith(b'#annot('))
            self.assertTrue(row['conditions'])
            self.assertFalse(row['runtime_binding_proved'] or row['execution_proved']
                             or row['edit_permission'] or row['compiler_permission'])
        self.assertFalse(any(row['diagnostics'] for row in result['conditional_raw_overlays']['by_file']))
        text = self.run_cli('--rule-set', 'pde', '--preview-conditional-scoped-calls', format='text')
        for phrase in ('Conditional raw annotations:', 'hint main.typ:', 'suppressed child.typ:',
                       'source sha256', 'condition:', 'no edit or compiler permission'):
            self.assertIn(phrase, text)
        self.assertEqual((self.root / 'main.typ').read_bytes(), main)
        self.assertEqual((self.root / 'child.typ').read_bytes(), child)

    def test_combined_import_shadow_is_unresolved_in_json_and_text(self):
        source = b'#import "other.typ" as tools: other as annot\n#annot(<x>)[Note.]'
        (self.root / 'main.typ').write_bytes(source)
        (self.root / 'other.typ').write_bytes(b'#let other = none\n')
        options = ('--rule-set', 'pde', '--preview-conditional-scoped-calls')
        result = self.run_cli(*options)
        row, = result['conditional_raw_annotations']['by_file']
        self.assertFalse(row['diagnostics'] or row['suppressed'])
        self.assertTrue(row['unresolved'])
        self.assertIn('unresolved:', self.run_cli(*options, format='text'))
        self.assertEqual((self.root / 'main.typ').read_bytes(), source)

    def test_opt_out_does_not_call_analyzer_and_budget_counts_section(self):
        (self.root / 'main.typ').write_bytes(b'#proof[#annot(<x>)[Note.]]')
        with patch.object(raw_annot, 'analyze', side_effect=AssertionError('ran without opt-in')):
            report = document_cli.report_document(str(self.root), 'main.typ', rule_set='pde')
        self.assertNotIn('conditional_raw_annotations', report)
        # A normal report establishes that earlier independent sections fit.
        # Enlarge only the raw-annotation results so this checks that section's
        # contribution without depending on the other sections' item counts.
        report = document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                              preview_conditional_scoped_calls=True)
        large = dict(report['conditional_raw_annotations']['by_file'][0])
        large['diagnostics'] = [{}] * 1001
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 1000), \
             patch.object(raw_annot, 'analyze', return_value=large) as analyzed:
            with self.assertRaisesRegex(InputError, 'report item limit'):
                document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                             preview_conditional_scoped_calls=True)
            analyzed.assert_called_once()


if __name__ == '__main__':
    unittest.main()
