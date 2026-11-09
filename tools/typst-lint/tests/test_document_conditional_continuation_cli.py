"""Opt-in conditional continuation previews over owned document fixtures."""
from pathlib import Path
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_cli
import document_conditional_continuation as continuation
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalContinuationCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def args(self, *extra):
        return ['--root', str(self.root), '--entrypoint', 'main.typ', *extra]

    def run_cli(self, *extra, format='json'):
        command = [sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
                   *self.args('--format', format, *extra)]
        run = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertFalse(run.stderr)
        return json.loads(run.stdout) if format == 'json' else run.stdout

    def test_opt_in_reports_independent_candidates_and_preserves_sources(self):
        main = b'#include "child.typ"\n#block[This solution holds.\nWe continue.]'
        child = (b'// typst-lint: ignore-next pde.continuation-line -- intentional\n'
                 b'#block[This theorem holds.\nWe continue.]')
        (self.root / 'main.typ').write_bytes(main)
        (self.root / 'child.typ').write_bytes(child)
        common = ('--rule-set', 'pde', '--local-rule-hints')
        baseline = self.run_cli(*common)
        self.assertNotIn('conditional_continuation_previews', baseline)
        flag = '--preview-conditional-continuations'
        report = self.run_cli(*common, flag)
        section = report['conditional_continuation_previews']
        self.assertFalse(section['edit_permission'] or section['compiler_permission']
                         or report['compiler_invoked'] or report['applied_fixes'])
        self.assertEqual(report['coverage']['uninspected'], baseline['coverage']['uninspected'])
        self.assertEqual(report['local_rule_hints'], baseline['local_rule_hints'])
        rows = {row['path']: row for row in section['by_file']}
        self.assertEqual(rows['child.typ']['verification'], 'no-candidate')
        self.assertEqual(rows['main.typ']['baseline_sha256'], digest(main))
        edit, = rows['main.typ']['edits']
        candidate = plan(main, [Edit(edit['start'], edit['end'], edit['replacement'].encode())]).candidate()
        self.assertEqual(rows['main.typ']['candidate_sha256'], digest(candidate))
        self.assertIn(b'holds. We continue.', candidate)
        self.assertFalse(rows['main.typ']['execution_proved'] or rows['main.typ']['render_equivalence_proved'])
        text = self.run_cli(*common, flag, format='text')
        for phrase in ('Conditional continuation previews:', 'conditional edit bytes', 'one space',
                       'source sha256', 'candidate sha256', 'condition:', 'no edit or compiler permission'):
            self.assertIn(phrase, text)
        self.assertEqual((self.root / 'main.typ').read_bytes(), main)
        self.assertEqual((self.root / 'child.typ').read_bytes(), child)

    def test_option_requires_pde_and_rejects_invalid_api_type(self):
        (self.root / 'main.typ').write_bytes(b'Hello.')
        report = self.run_cli('--preview-conditional-continuations')
        self.assertEqual(report['phase'], 'input')
        self.assertFalse(report['compiler_invoked'] or report['applied_fixes'])
        with self.assertRaises(InputError):
            document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                         preview_conditional_continuations=1)

    def test_continuation_and_scoped_options_keep_independent_reports(self):
        source = (b'#block[This solution holds.\nWe continue.]\n'
                  b'#local-tag-scope(s => [#s.tag("x")])')
        (self.root / 'main.typ').write_bytes(source)
        report = self.run_cli('--rule-set', 'pde', '--preview-conditional-continuations',
                              '--preview-conditional-scoped-calls')
        self.assertEqual(len(report['conditional_continuation_previews']['by_file'][0]['edits']), 1)
        self.assertEqual(len(report['conditional_scoped_calls']['by_file'][0]['edits']), 1)
        self.assertIn('conditional_raw_overlays', report)
        self.assertIn('conditional_detached_annotations', report)
        self.assertEqual((self.root / 'main.typ').read_bytes(), source)

    def test_withheld_reason_is_visible_and_never_applies_edits(self):
        source = b'#block[This solution holds.\nWe continue.]'
        (self.root / 'main.typ').write_bytes(source)
        def withhold(tree, path, **kwargs):
            return dict(path=path, baseline_sha256=digest(tree.source), candidate_sha256=None,
                        verification='withheld', reason='fixture refusal', edits=[],
                        conditions=continuation.CONDITIONS, execution_proved=False,
                        render_equivalence_proved=False, edit_permission=False, compiler_permission=False)
        output = io.StringIO()
        with patch.object(continuation, 'preview', side_effect=withhold), contextlib.redirect_stdout(output):
            status = document_cli.main(self.args('--rule-set', 'pde', '--preview-conditional-continuations'))
        self.assertEqual(status, 2)
        self.assertIn('unresolved: fixture refusal', output.getvalue())
        self.assertEqual((self.root / 'main.typ').read_bytes(), source)

    def test_shared_report_item_limit_is_enforced(self):
        (self.root / 'main.typ').write_bytes(b'#block[This solution holds.\nWe continue.]')
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 3):
            with self.assertRaisesRegex(InputError, 'report item limit'):
                document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                             preview_conditional_continuations=True)
