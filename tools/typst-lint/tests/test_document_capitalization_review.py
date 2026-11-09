"""Unknown starters are review-only, source-bound, bounded and opt-in."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_capitalization_review as review
import document_cli
import include_context
from syntax_client import EXECUTABLE
from typst_lint import InputError, load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class CapitalizationReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='capitalization-review-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def capture(self, source):
        (self.root / 'main.typ').write_bytes(source)
        return include_context.load_include_context(self.root, (('main.typ', source),))

    def test_exact_patterns_are_review_only_and_not_rewrites(self):
        source = (b'#paragraph_tab\ncompare the values.\n\n'
                  b'#block[\n#paragraph_tab\ncompare the other values.\n]\n\n'
                  b'#paragraph_tab\nthe approved starter.\n\n'
                  b'#paragraph_tab\neBay is a name.\n\n'
                  b'#paragraph_tab\nx is a symbol.\n\n'
                  b'#paragraph_tab\n$compare$ is math.\n\n'
                  b'#paragraph_tab\n// note\ncompare these.\n')
        result = review.analyze(self.capture(source), load_rules()[0]['rules'])
        self.assertEqual([r['starter'] for r in result['findings']], ['compare', 'compare'])
        self.assertTrue(all(r['status'] == 'REVIEW_ONLY' and not r['violation_proved']
                            and not r['edit_permission'] for r in result['findings']))
        self.assertFalse(result['edit_permission'] or result['compiler_permission']
                         or result['render_equivalence_proved'] or result['coverage_complete'])
        self.assertEqual((self.root / 'main.typ').read_bytes(), source)

    def test_suppression_is_honored_and_shadow_is_not_claimed_resolved(self):
        source = (b'#let paragraph_tab = []\n'
                  b'// typst-lint: ignore-next pde.paragraph-capitalization -- intended\n'
                  b'#paragraph_tab\ncompare the first.\n\n'
                  b'#paragraph_tab\ncompare the second.\n')
        result = review.analyze(self.capture(source), load_rules()[0]['rules'])
        self.assertEqual(result['suppressed_count'], 1)
        self.assertEqual(len(result['findings']), 1)
        self.assertFalse(result['violation_proved'])
        self.assertTrue(any('Marker binding' in text for text in result['conditions']))

    def test_code_raw_math_unicode_and_bare_cr_are_not_prose_starters(self):
        source = (b'#let example = "#paragraph_tab\\ncompare the code"\n'
                  b'`#paragraph_tab\ncompare raw`\n'
                  b'// #paragraph_tab\n// compare comment\n'
                  b'#paragraph_tab\ncaf\xc3\xa9 is a name.\n'
                  b'#paragraph_tab\neBay is a name.\n'
                  b'#paragraph_tab\rcompare is not an adjacent line.\n')
        result = review.analyze(self.capture(source), load_rules()[0]['rules'])
        self.assertFalse(result['findings'])

    def test_issued_ast_tampering_is_rejected(self):
        source = b'#paragraph_tab\ncompare the values.\n'
        context = self.capture(source)
        root = context.sources[0].tree.nodes[0]
        old = root.end
        try:
            object.__setattr__(root, 'end', old - 1)
            with self.assertRaises(include_context.IncludeCaptureError):
                review.analyze(context, load_rules()[0]['rules'])
        finally:
            object.__setattr__(root, 'end', old)

    def test_many_same_line_markers_are_not_standalone(self):
        source = (b'#paragraph_tab ' * 400) + b'\ncompare the values.\n'
        result = review.analyze(self.capture(source), load_rules()[0]['rules'])
        self.assertFalse(result['findings'])

    def test_directive_work_is_checked_before_suppression_resolution(self):
        source = ((b'// typst-lint: ignore-next pde.paragraph-capitalization -- intentional\n'
                   b'#paragraph_tab\ncompare the value.\n\n') * 12)
        context = self.capture(source)
        work = 4 * len(context.sources[0].tree.nodes) + len(source) + 1
        with patch.object(review, 'resolve_suppressions', side_effect=AssertionError('resolver called before budget')):
            with self.assertRaisesRegex(InputError, 'work budget'):
                review.analyze(context, load_rules()[0]['rules'], max_work=work)

    def test_long_first_line_directive_is_bounded_before_regex(self):
        source = b'// typst-lint: ignore-next ' + b' ' * 3000 + b'not-terminated\ntext\n'
        context = self.capture(source)
        with patch.object(review, 'resolve_suppressions', side_effect=AssertionError('regex called before budget')):
            with self.assertRaisesRegex(InputError, 'work budget'):
                review.analyze(context, load_rules()[0]['rules'])

    def test_unresolved_includes_keep_review_incomplete(self):
        source = b'#include "missing.typ"\n#paragraph_tab\ncompare the values.\n'
        result = review.analyze(self.capture(source), load_rules()[0]['rules'])
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertEqual(result['unresolved_include_count'], 1)
        self.assertEqual([row['starter'] for row in result['findings']], ['compare'])
        self.assertFalse(result['coverage_complete'])

    def test_work_items_and_bytes_are_bounded(self):
        source = b'#paragraph_tab\ncompare the values.\n'
        ctx = self.capture(source); catalog = load_rules()[0]['rules']
        result = review.analyze(ctx, catalog)
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
        size = len(encoded.encode('utf-8')) + 2 * encoded.count('\n')
        self.assertEqual(review.analyze(ctx, catalog, max_bytes=size, max_items=result['report_items']), result)
        with self.assertRaises(InputError):
            review.analyze(ctx, catalog, max_bytes=size - 1)
        with self.assertRaises(InputError):
            review.analyze(ctx, catalog, max_items=result['report_items'] - 1)
        with self.assertRaises(InputError):
            review.analyze(ctx, catalog, max_work=1)
        low, high = 1, review.MAX_WORK
        while low < high:
            middle = (low + high) // 2
            try:
                review.analyze(ctx, catalog, max_work=middle)
            except InputError:
                low = middle + 1
            else:
                high = middle
        self.assertEqual(review.analyze(ctx, catalog, max_work=low), result)
        with self.assertRaises(InputError):
            review.analyze(ctx, catalog, max_work=low - 1)

    def test_review_does_not_change_existing_combined_preview(self):
        from test_document_modern_highlight_scoped_preview import ScopedReferencePreviewTests
        fixture = ScopedReferencePreviewTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        body = (b'#local-tag-scope(s => [#highlighted[See #(s.ref)("one") $x$ end.]])\n'
                b'#paragraph_tab\ncompare the values.\n')
        fixture.run_source(body)
        f = fixture.fixture
        kw = dict(root=str(f.project), entrypoint='main.typ', rule_set='pde',
                  package_path=str(f.packages), footnote_cache_path=str(f.cache),
                  modern_highlight_evidence=True, preview_conditional_modern_combined_highlights=True)
        baseline = document_cli.report_document(**kw)
        self.assertGreater(baseline['conditional_modern_combined_previews']['previewed_diagnostics'], 0)
        result = document_cli.report_document(**kw, inspect_capitalization_review=True)
        section = result.pop('capitalization_starter_review')
        self.assertEqual(result, baseline)
        self.assertEqual([row['starter'] for row in section['findings']], ['compare'])

    def test_cli_is_opt_in_and_existing_reports_are_preserved(self):
        source = b'#paragraph_tab\ncompare the values.\n'
        self.capture(source)
        kw = dict(root=str(self.root), entrypoint='main.typ', rule_set='pde', local_rule_hints=True)
        baseline = document_cli.report_document(**kw)
        result = document_cli.report_document(**kw, inspect_capitalization_review=True)
        section = result.pop('capitalization_starter_review')
        self.assertEqual(result, baseline)
        self.assertEqual(section['findings'][0]['starter'], 'compare')
        self.assertFalse(section['violation_proved'])
        with self.assertRaises(InputError):
            document_cli.report_document(root=str(self.root), entrypoint='main.typ',
                                         inspect_capitalization_review=True)
        command = [sys.executable, str(TOOL / 'typst-lint'), 'inspect-document', '--root', str(self.root),
                   '--entrypoint', 'main.typ', '--rule-set', 'pde', '--inspect-capitalization-review', '--format', 'text']
        completed = subprocess.run(command, text=True, capture_output=True, timeout=30)
        self.assertIn('not proven rule violations', completed.stdout)
        self.assertIn("review starter 'compare'", completed.stdout)
        full = document_cli.report_document(**kw, inspect_capitalization_review=True)
        size = len(json.dumps(full, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8'))
        with patch.object(document_cli, 'MAX_MODERN_REPORT_BYTES', size):
            self.assertEqual(document_cli.report_document(**kw, inspect_capitalization_review=True), full)
        with patch.object(document_cli, 'MAX_MODERN_REPORT_BYTES', size - 1):
            with self.assertRaises(InputError):
                document_cli.report_document(**kw, inspect_capitalization_review=True)
        low, high = 1, 1024
        while low < high:
            mid = (low + high) // 2
            try:
                with patch.object(document_cli, 'MAX_REPORT_ITEMS', mid):
                    document_cli.report_document(**kw, inspect_capitalization_review=True)
            except InputError:
                low = mid + 1
            else:
                high = mid
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', low):
            self.assertEqual(document_cli.report_document(**kw, inspect_capitalization_review=True), full)
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', low - 1):
            with self.assertRaises(InputError):
                document_cli.report_document(**kw, inspect_capitalization_review=True)
        self.assertEqual((self.root / 'main.typ').read_bytes(), source)


if __name__ == '__main__':
    unittest.main()
