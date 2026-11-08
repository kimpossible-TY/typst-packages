"""Hyphen marker hints remain opt-in, separate and diagnostic-only."""
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
import document_conditional_hyphen_marker as marker
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalHyphenMarkerCliTests(unittest.TestCase):
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

    def test_root_nested_and_suppression_stay_separate_from_existing_hints(self):
        main = b'#include "child.typ"\n#paragraph-tab\nText.'
        child = (b'#block[First.\n// typst-lint: ignore-next pde.paragraph-marker-gap -- intended\n'
                 b'#paragraph-tab\ntherefore x.]')
        (self.root / 'main.typ').write_bytes(main)
        (self.root / 'child.typ').write_bytes(child)
        baseline = self.run_cli('--rule-set', 'pde')
        self.assertNotIn('conditional_hyphen_markers', baseline)
        report = self.run_cli('--rule-set', 'pde', '--local-rule-hints')
        self.assertEqual(report['coverage'], baseline['coverage'])
        self.assertEqual(report['diagnostics'], baseline['diagnostics'])
        self.assertFalse(report['compiler_invoked'] or report['applied_fixes'])
        section = report['conditional_hyphen_markers']
        self.assertFalse(section['coverage_complete'] or section['edit_permission'] or section['compiler_permission'])
        rows = {row['path']: row for row in section['by_file']}
        self.assertEqual(rows['main.typ']['source_sha256'], digest(main))
        self.assertEqual([x['rule_id'] for x in rows['main.typ']['diagnostics']], ['pde.paragraph-marker-gap'])
        self.assertEqual([x['rule_id'] for x in rows['child.typ']['suppressed']], ['pde.paragraph-marker-gap'])
        self.assertEqual([x['rule_id'] for x in rows['child.typ']['diagnostics']], ['pde.paragraph-capitalization'])
        text = self.run_cli('--rule-set', 'pde', '--local-rule-hints', format='text')
        for phrase in ('Conditional hyphenated paragraph markers:', 'hint main.typ:', 'suppressed child.typ:',
                       'condition:', 'no edit or compiler permission'):
            self.assertIn(phrase, text)
        self.assertEqual((self.root / 'main.typ').read_bytes(), main)
        self.assertEqual((self.root / 'child.typ').read_bytes(), child)

    def test_opt_out_and_shared_item_limit(self):
        (self.root / 'main.typ').write_bytes(b'#block[First.\n#paragraph-tab\ntherefore x.]')
        with patch.object(marker, 'analyze', side_effect=AssertionError('ran without option')):
            result = document_cli.report_document(str(self.root), 'main.typ', rule_set='pde')
        self.assertNotIn('conditional_hyphen_markers', result)
        with patch.object(marker, 'analyze', wraps=marker.analyze) as analyzed:
            report = document_cli.report_document(str(self.root), 'main.typ', rule_set='pde', local_rule_hints=True)
        analyzed.assert_called_once()
        large = dict(report['conditional_hyphen_markers']['by_file'][0])
        large['unresolved'] = ['fixture'] * 1001
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 1000), \
             patch.object(marker, 'analyze', return_value=large) as analyzed:
            with self.assertRaisesRegex(InputError, 'report item limit'):
                document_cli.report_document(str(self.root), 'main.typ', rule_set='pde', local_rule_hints=True)
            analyzed.assert_called_once()
