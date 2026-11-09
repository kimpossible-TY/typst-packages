"""Separate opt-in and conditional reporting for block-adjacent marker gaps."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_cli
import document_hyphen_gap_after_blocks as gap
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class HyphenGapAfterBlocksCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def run_cli(self, *extra, format='json'):
        command = [sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
                   '--root', str(self.root), '--entrypoint', 'main.typ', '--format', format, *extra]
        run = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertFalse(run.stderr)
        return json.loads(run.stdout) if format == 'json' else run.stdout

    def test_extra_opt_in_preserves_old_withholding_and_shows_specific_conditions(self):
        source = (b'#block[#flowbox()[Text]\n#paragraph-tab\nNext.]\n'
                  b'#block[#pagebreak(weak: true)\n#paragraph-tab\nNext.]')
        (self.root / 'main.typ').write_bytes(source)
        baseline = self.run_cli('--rule-set', 'pde', '--preview-conditional-paragraph-gaps')
        self.assertNotIn('conditional_hyphen_block_gap_previews', baseline)
        old = baseline['conditional_hyphen_marker_gap_previews']['by_file'][0]
        self.assertEqual(old['verification'], 'withheld')
        self.assertEqual(len(old['unresolved']), 2)
        report = self.run_cli('--rule-set', 'pde', '--preview-conditional-paragraph-gaps',
                              '--preview-conditional-block-marker-gaps')
        self.assertEqual(report['conditional_hyphen_marker_gap_previews'],
                         baseline['conditional_hyphen_marker_gap_previews'])
        self.assertEqual(report['coverage']['uninspected'], baseline['coverage']['uninspected'])
        self.assertFalse(report['compiler_invoked'] or report['applied_fixes'])
        section = report['conditional_hyphen_block_gap_previews']
        self.assertFalse(section['edit_permission'] or section['compiler_permission'])
        row, = section['by_file']
        self.assertFalse(row['runtime_binding_proved'] or row['execution_proved']
                         or row['render_equivalence_proved'] or row['edit_permission'])
        self.assertEqual(len(row['edits']), 2)
        for edit in row['edits']:
            self.assertEqual(edit['preceding_conditions'], list(gap.PRECEDING_CONDITIONS[edit['preceding_kind']]))
            self.assertIn(edit['preceding_kind'], edit['preceding_conditions'][0])
        candidate = plan(source, [Edit(e['start'], e['end'], e['replacement'].encode())
                                  for e in row['edits']]).candidate()
        self.assertEqual(row['baseline_sha256'], digest(source))
        self.assertEqual(row['candidate_sha256'], digest(candidate))
        text = self.run_cli('--rule-set', 'pde', '--preview-conditional-block-marker-gaps', format='text')
        for phrase in ('Conditional block-adjacent marker gap previews:', 'condition:', 'pagebreak', 'flowbox',
                       'no edit or compiler permission'):
            self.assertIn(phrase, text)
        self.assertEqual((self.root / 'main.typ').read_bytes(), source)

    def test_pde_boolean_validation_and_per_edit_item_limit(self):
        (self.root / 'main.typ').write_bytes(b'Hello.')
        self.assertEqual(self.run_cli('--preview-conditional-block-marker-gaps')['phase'], 'input')
        with self.assertRaises(InputError):
            document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                         preview_conditional_block_marker_gaps=1)
        fake = dict(path='main.typ', baseline_sha256=digest(b'Hello.'), candidate_sha256='0' * 64,
                    verification='conditional-block-adjacent-marker-gap-reanalysis',
                    conditions=(), runtime_binding_proved=False, execution_proved=False,
                    render_equivalence_proved=False, edit_permission=False, compiler_permission=False,
                    edits=[dict(start=0, end=0, replacement='\n', rule_id=gap.RULE,
                                preceding_kind='flowbox', preceding_conditions=('condition',) * 1001)])
        with patch.object(gap, 'preview', return_value=fake) as analyzed, \
             patch.object(document_cli, 'MAX_REPORT_ITEMS', 1000):
            with self.assertRaisesRegex(InputError, 'report item limit'):
                document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                             preview_conditional_block_marker_gaps=True)
            analyzed.assert_called_once()
