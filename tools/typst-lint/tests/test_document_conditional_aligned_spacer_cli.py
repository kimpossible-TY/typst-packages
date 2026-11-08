"""End-to-end source-only aligned spacer previews on owned fixtures."""
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
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalAlignedSpacerCliTests(unittest.TestCase):
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

    def test_opt_in_reports_candidates_without_changing_files_or_coverage(self):
        main = b'#include "child.typ"\n#block[$ A &= B #footnote[A.] $]'
        child = (b'// typst-lint: ignore-next pde.aligned-footnote-spacer -- intended\n'
                 b'#block[$ A &= B #footnote[B.] $]')
        (self.root / 'main.typ').write_bytes(main)
        (self.root / 'child.typ').write_bytes(child)
        baseline = self.run_cli('--rule-set', 'pde', '--local-rule-hints')
        self.assertNotIn('conditional_aligned_spacer_previews', baseline)
        report = self.run_cli('--rule-set', 'pde', '--local-rule-hints',
                              '--preview-conditional-aligned-spacers')
        section = report['conditional_aligned_spacer_previews']
        self.assertFalse(section['edit_permission'] or section['compiler_permission']
                         or report['compiler_invoked'] or report['applied_fixes'])
        self.assertEqual(report['coverage']['uninspected'], baseline['coverage']['uninspected'])
        self.assertEqual(report['local_rule_hints'], baseline['local_rule_hints'])
        rows = {row['path']: row for row in section['by_file']}
        self.assertEqual(rows['child.typ']['verification'], 'no-candidate')
        edit, = rows['main.typ']['edits']
        candidate = plan(main, [Edit(edit['start'], edit['end'], edit['replacement'].encode())]).candidate()
        self.assertEqual(rows['main.typ']['baseline_sha256'], digest(main))
        self.assertEqual(rows['main.typ']['candidate_sha256'], digest(candidate))
        self.assertIn(b'#dots_space #footnote[A.]', candidate)
        self.assertFalse(rows['main.typ']['runtime_binding_proved'] or rows['main.typ']['execution_proved'])
        text = self.run_cli('--rule-set', 'pde', '--preview-conditional-aligned-spacers', format='text')
        for phrase in ('Conditional aligned spacer previews:', 'conditional edit bytes', 'insert #dots_space',
                       'source sha256', 'candidate sha256', 'condition:', 'no edit or compiler permission'):
            self.assertIn(phrase, text)
        self.assertEqual((self.root / 'main.typ').read_bytes(), main)
        self.assertEqual((self.root / 'child.typ').read_bytes(), child)

    def test_requires_pde_and_boolean_option(self):
        (self.root / 'main.typ').write_bytes(b'Hello.')
        self.assertEqual(self.run_cli('--preview-conditional-aligned-spacers')['phase'], 'input')
        with self.assertRaises(InputError):
            document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                         preview_conditional_aligned_spacers=1)

    def test_other_previews_remain_independent_and_report_limit_applies(self):
        source = b'#block[$ A &= B #footnote[X] $ This.#dots_space #footnote[Y.]]'
        (self.root / 'main.typ').write_bytes(source)
        report = self.run_cli('--rule-set', 'pde', '--preview-conditional-aligned-spacers',
                              '--preview-conditional-prose-spacers')
        self.assertEqual(len(report['conditional_aligned_spacer_previews']['by_file'][0]['edits']), 1)
        self.assertEqual(len(report['conditional_prose_spacer_previews']['by_file'][0]['edits']), 1)
        self.assertEqual((self.root / 'main.typ').read_bytes(), source)
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 3):
            with self.assertRaisesRegex(InputError, 'report item limit'):
                document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                             preview_conditional_aligned_spacers=True)
