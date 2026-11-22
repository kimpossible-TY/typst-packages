"""Combined source-only highlight previews keep proofs and bounds separate."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_cli
import scoped_reference_preview as scoped
from edit_plan import Edit, plan
from typst_lint import InputError
import test_document_modern_highlight_scoped_preview as fixtures


@unittest.skipUnless(fixtures.fixtures.EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class CombinedHighlightCliTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ScopedReferencePreviewTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project = self.fixture.fixture.project
        self.packages = self.fixture.fixture.packages
        self.cache = self.fixture.fixture.cache

    def setup_source(self, body):
        source, _, _, _ = self.fixture.run_source(body)
        return source

    def report(self, **options):
        return document_cli.report_document(str(self.project), 'main.typ', rule_set='pde',
            package_path=str(self.packages), footnote_cache_path=str(self.cache),
            modern_highlight_evidence=True, **options)

    def test_independent_math_and_scoped_references_form_one_source_plan(self):
        source = self.setup_source(b'#highlighted[Math $cal(x)$ end.]\n'
            b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") and $y$]])')
        ordinary = self.report()
        result = self.report(preview_conditional_modern_combined_highlights=True)
        combined = result['conditional_modern_combined_previews']
        row, = combined['by_file']
        self.assertEqual(combined['previewed_diagnostics'], 2, combined)
        self.assertEqual(row['verification'], 'conditional-modern-highlight-syntax-directives-and-reanalysis')
        edits = [Edit(e['start'], e['end'], e['replacement'].encode()) for e in row['edits']]
        candidate = plan(source, edits).candidate()
        self.assertIn(b'#highlighted[Math ]$cal(x)$#highlighted[ end.]', candidate)
        self.assertIn(b'#highlighted[See #(s.ref)("one") and ]$y$', candidate)
        for key in ('diagnostics', 'conditional_diagnostics', 'coverage', 'proposed_edits'):
            self.assertEqual(result[key], ordinary[key], key)
        self.assertFalse(result['compiler_invoked'] or result['applied_fixes'])
        self.assertFalse(combined['edit_permission'] or combined['compiler_permission']
                         or combined['runtime_binding_proved'] or combined['execution_proved']
                         or combined['render_equivalence_proved'] or combined['coverage_complete'])
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)

    def test_math_call_and_scoped_ref_in_same_callback_remain_unpreviewed(self):
        self.setup_source(b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") and $cal(x)$]])')
        combined = self.report(preview_conditional_modern_combined_highlights=True)['conditional_modern_combined_previews']
        self.assertEqual(combined['status'], 'INCOMPLETE')
        self.assertEqual(combined['diagnostics_count'], 1)
        self.assertEqual(combined['previewed_diagnostics'], 0)
        self.assertEqual(combined['unpreviewed_diagnostics'], 1)
        self.assertFalse(any(row['edits'] for row in combined['by_file']))

    def test_nested_callback_math_call_remains_unpreviewed(self):
        self.setup_source(b'#local-tag-scope(s => [#local-tag-scope(t => '
            b'[#highlighted[See #(t.ref)("one") and $cal(x)$]])])')
        combined = self.report(preview_conditional_modern_combined_highlights=True)['conditional_modern_combined_previews']
        self.assertEqual(combined['status'], 'INCOMPLETE')
        self.assertEqual(combined['previewed_diagnostics'], 0)
        self.assertGreaterEqual(combined['unpreviewed_diagnostics'], 1)

    def test_scoped_budget_exhaustion_retains_independent_math_candidate(self):
        self.setup_source(b'#highlighted[Math $cal(x)$ end.]\n'
            b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") and $y$]])')
        with patch.object(scoped, 'MAX_SCOPE_WORK', 1):
            result = self.report(preview_conditional_modern_combined_highlights=True)
        combined = result['conditional_modern_combined_previews']
        row, = combined['by_file']
        self.assertEqual(combined['status'], 'INCOMPLETE')
        self.assertEqual(combined['previewed_diagnostics'], 1)
        self.assertEqual(row['scoped_reference_withheld']['status'], 'WITHHELD')
        self.assertEqual(len(row['edits']), 1)
        self.assertIn('$cal(x)$', row['edits'][0]['replacement'])

    def test_opt_in_is_explicit_and_does_not_mix_separate_plans(self):
        self.setup_source(b'#highlighted[Math $cal(x)$ end.]')
        self.assertNotIn('conditional_modern_combined_previews', self.report())
        for option in ('preview_conditional_modern_highlights','preview_conditional_modern_math_highlights'):
            with self.subTest(option=option), self.assertRaisesRegex(InputError, 'cannot be mixed'):
                self.report(preview_conditional_modern_combined_highlights=True, **{option: True})
        with self.assertRaisesRegex(InputError, 'require --modern-highlight-evidence'):
            document_cli.report_document(str(self.project), 'main.typ', rule_set='pde',
                preview_conditional_modern_combined_highlights=True)
        with patch.object(document_cli, 'MAX_MODERN_REPORT_BYTES', 1):
            with self.assertRaisesRegex(InputError, 'report byte limit'):
                self.report(preview_conditional_modern_combined_highlights=True)

    def test_combined_item_budget_refuses_rather_than_omitting_report(self):
        source = self.setup_source(b'#highlighted[Math $cal(x)$ end.]\n'
            b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") and $y$]])')
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 60):
            self.assertIn('conditional_modern_highlight', self.report())
            with self.assertRaisesRegex(InputError, 'report budget exceeded'):
                self.report(preview_conditional_modern_combined_highlights=True)
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)

    def test_unreviewed_first_root_reports_unavailable(self):
        self.setup_source(b'#highlighted[Math $cal(x)$ end.]')
        target = self.fixture.fixture.target
        shutil.rmtree(target)
        shutil.copytree(fixtures.fixtures.OLD, target)
        result = self.report(preview_conditional_modern_combined_highlights=True)
        section = result['conditional_modern_combined_previews']
        self.assertEqual(section['status'], 'UNAVAILABLE')
        self.assertFalse(section['by_file'])
        self.assertFalse(section['edit_permission'] or section['compiler_permission'])

    def test_cli_json_and_text_expose_only_conditional_source_plan(self):
        source = self.setup_source(b'#highlighted[Math $cal(x)$ end.]\n'
            b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") and $y$]])')
        base = [sys.executable, str(TOOL / 'typst-lint'), 'inspect-document', '--root', str(self.project),
                '--entrypoint', 'main.typ', '--rule-set', 'pde', '--package-path', str(self.packages),
                '--footnote-cache-path', str(self.cache), '--modern-highlight-evidence',
                '--preview-conditional-modern-combined-highlights']
        result = subprocess.run(base + ['--format', 'json'], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['conditional_modern_combined_previews']['previewed_diagnostics'], 2)
        self.assertFalse(data['compiler_invoked'] or data['applied_fixes'])
        text = subprocess.run(base + ['--format', 'text'], capture_output=True, text=True, timeout=30)
        self.assertEqual(text.returncode, 2, text.stderr)
        self.assertIn('Conditional combined modern highlight previews:', text.stdout)
        self.assertIn('source-only, no edit or compiler permission', text.stdout)
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)


if __name__ == '__main__':
    unittest.main()
