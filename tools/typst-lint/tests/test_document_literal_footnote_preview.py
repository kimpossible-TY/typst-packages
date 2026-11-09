"""Literal footnote highlight plans remain independent and source-only."""
from dataclasses import replace
import json
import subprocess
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_modern_highlight as modern
import document_cli
import document_literal_footnote_preview as preview
from edit_plan import Edit, plan
import test_conditional_modern_highlight as fixtures
from typst_lint import InputError, digest


@unittest.skipUnless(fixtures.EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class LiteralFootnotePreviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ConditionalModernHighlightTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def issue(self, body):
        source = fixtures.IMPORT + body
        session, _ = self.fixture.session(source)
        analyzer = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)
        return source, analyzer

    def test_literal_footnote_before_between_after_and_inner_math_script(self):
        cases = (
            b'#highlighted[Before #footnote[See @known] $x_1$ after.]',
            b'#highlighted[Before $x_1$ #footnote[note $y_2$] $z_3$ after.]',
            b'#highlighted[Before $x_1$ after #footnote[note $y_2$]]',
        )
        for body in cases:
            with self.subTest(body=body):
                source, analyzer = self.issue(body)
                result = preview.preview_all(analyzer)
                row, = result['plans']
                self.assertEqual(result['status'], 'CONDITIONAL')
                edit, = row['edits']
                candidate = plan(source, [Edit(edit['start'], edit['end'], edit['replacement'].encode())]).candidate()
                self.assertEqual(candidate.count(b'#footnote['), 1)
                self.assertEqual(row['baseline_sha256'], digest(source))
                self.assertEqual(row['candidate_sha256'], digest(candidate))
                for name in ('runtime_binding_proved', 'execution_proved', 'render_equivalence_proved',
                             'edit_permission', 'compiler_permission'):
                    self.assertFalse(row[name])
                self.assertEqual((self.fixture.project / 'main.typ').read_bytes(), source)

    def test_unknown_dynamic_nested_suppressed_and_multiple_calls_withheld(self):
        cases = (
            b'#let footnote(body) = panic("shadow")\n#highlighted[Before #footnote[note] $x$ after]',
            b'#import "missing.typ": *\n#highlighted[Before #footnote[note] $x$ after]',
            b'#highlighted[Before #footnote[#panic("effect")] $x$ after]',
            b'#highlighted[Before #footnote("note") $x$ after]',
            b'#highlighted[Before #footnote[first] #footnote[second] $x$ after]',
            b'#block[#highlighted[Before #footnote[note] $x$ after]]',
            b'// typst-lint: ignore-next core.parenthesized-scripts -- reason\n'
            b'#highlighted[Before #footnote[note] $x_1$ after]',
        )
        for body in cases:
            with self.subTest(body=body):
                _, analyzer = self.issue(body)
                result = preview.preview_all(analyzer)
                self.assertFalse(result['plans'])

    def test_modified_source_issued_binding_and_candidate_are_withheld(self):
        source, analyzer = self.issue(b'#highlighted[Before #footnote[note] $x$ after.]')
        original = preview.parse
        def tamper(data, *args, **kwargs):
            return original(data.replace(b'#footnote[note]', b'#footnote[nope]'), *args, **kwargs)
        with patch.object(preview, 'parse', side_effect=tamper):
            report = preview.preview_all(analyzer)
        self.assertFalse(report['plans'])
        self.assertTrue(report['withheld'])
        self.assertEqual((self.fixture.project / 'main.typ').read_bytes(), source)

    def test_issued_hash_and_candidate_binding_must_match(self):
        source, analyzer = self.issue(b'#highlighted[Before #footnote[note] $x$ after.]')
        session = analyzer._session
        original_analyze = session.analyze
        def forged(path):
            result = original_analyze(path)
            return replace(result, calls=tuple(replace(call, source_sha256='0' * 64) for call in result.calls))
        with patch.object(session, 'analyze', side_effect=forged):
            report = preview.preview_all(analyzer)
        self.assertFalse(report['plans'])
        self.assertTrue(report['withheld'])
        original_resolver = preview.binding._FootnoteResolver
        class ChangedCandidate(original_resolver):
            def visit(self, index, state, *, output=False):
                value = super().visit(index, state, output=output)
                if index == 0 and self.tree.source != source:
                    self.call_evidence.clear()
                return value
        with patch.object(preview.binding, '_FootnoteResolver', ChangedCandidate):
            report = preview.preview_all(analyzer)
        self.assertFalse(report['plans'])
        self.assertIn('binding or captured namespace changed', report['withheld'][0]['reason'])

    def test_changed_script_and_footnote_findings_are_withheld(self):
        source, analyzer = self.issue(b'#highlighted[Before #footnote[note $y_2$] $x_1$ after]')
        scripts = preview.analyze_scripts
        def drop_script(tree, *args, **kwargs):
            result = scripts(tree, *args, **kwargs)
            return replace(result, diagnostics=()) if tree.source != source else result
        with patch.object(preview, 'analyze_scripts', side_effect=drop_script):
            result = preview.preview_all(analyzer)
        self.assertFalse(result['plans'])
        self.assertIn('Script findings changed', result['withheld'][0]['reason'])
        notes = preview.footnote_rules.analyze_conditional_nested
        def drop_note(tree, *args, **kwargs):
            result = notes(tree, *args, **kwargs)
            if tree.source != source:
                result = {key: replace(value, diagnostics=()) for key, value in result.items()}
            return result
        with patch.object(preview.footnote_rules, 'analyze_conditional_nested', side_effect=drop_note):
            result = preview.preview_all(analyzer)
        self.assertFalse(result['plans'])
        self.assertIn('Footnote findings changed', result['withheld'][0]['reason'])

    def test_item_byte_and_work_limits_fail_closed(self):
        _, analyzer = self.issue(b'#highlighted[A #footnote[note] $x$ end.]\n'
                                 b'#highlighted[B #footnote[note] $y$ end.]')
        evidence, result = preview.preview_all(analyzer, return_evidence=True)
        items = evidence['report_items'] + result['report_items']
        a = json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2)
        b = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
        size = len(a.encode()) + 2 * a.count('\n') + len(b.encode()) + 2 * b.count('\n')
        self.assertEqual(preview.preview_all(analyzer, max_items=items, max_bytes=size), result)
        with self.assertRaises(InputError):
            preview.preview_all(analyzer, max_items=items - 1)
        with self.assertRaises(InputError):
            preview.preview_all(analyzer, max_bytes=size - 1)
        with patch.object(preview, 'MAX_CHECKS', 1):
            result = preview.preview_all(analyzer)
        self.assertEqual(len(result['plans']), 1)
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertTrue(result['truncated'])
        self.assertIn('work budget exceeded', result['withheld'][0]['reason'])

    def test_work_budget_stops_before_later_file_analysis(self):
        for name in ('a.typ', 'b.typ', 'c.typ'):
            (self.fixture.project / name).write_bytes(fixtures.IMPORT +
                b'#highlighted[Before #footnote[note] $x$ after.]')
        source = b'#include "a.typ"\n#include "b.typ"\n#include "c.typ"\n'
        session, _ = self.fixture.session(source)
        analyzer = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)
        original = session.analyze
        visited = []
        def count(path):
            visited.append(path)
            return original(path)
        with patch.object(preview, 'MAX_CHECKS', 1), patch.object(session, 'analyze', side_effect=count):
            result = preview.preview_all(analyzer)
        self.assertEqual(len(visited), 1)
        self.assertEqual(len(result['plans']), 1)
        self.assertEqual(len(result['withheld']), 1)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['status'], 'INCOMPLETE')

    def test_cli_flag_requires_combined_and_text_explains_independence(self):
        source, _ = self.issue(b'#highlighted[Before #footnote[note] $x$ end.]')
        command = [sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
                   '--root', str(self.fixture.project), '--entrypoint', 'main.typ',
                   '--rule-set', 'pde', '--package-path', str(self.fixture.packages),
                   '--footnote-cache-path', str(self.fixture.cache), '--modern-highlight-evidence',
                   '--preview-conditional-modern-literal-footnotes']
        missing = subprocess.run(command + ['--format', 'json'], capture_output=True, text=True, timeout=30)
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(json.loads(missing.stdout)['phase'], 'input')
        run = subprocess.run(command + ['--preview-conditional-modern-combined-highlights', '--format', 'text'],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertIn('Do not merge these plans with other previews', run.stdout)
        self.assertEqual((self.fixture.project / 'main.typ').read_bytes(), source)

    def test_cli_opt_in_preserves_existing_combined_and_source(self):
        source, _ = self.issue(b'#highlighted[Existing $x$ end.]\n'
                               b'#highlighted[Before #footnote[note $y_1$] $z$ end.]')
        kw = dict(root=str(self.fixture.project), entrypoint='main.typ', rule_set='pde',
                  package_path=str(self.fixture.packages), footnote_cache_path=str(self.fixture.cache),
                  modern_highlight_evidence=True, preview_conditional_modern_combined_highlights=True)
        base = document_cli.report_document(**kw)
        result = document_cli.report_document(**kw, preview_conditional_modern_literal_footnotes=True)
        self.assertEqual(base['conditional_modern_combined_previews'], result['conditional_modern_combined_previews'])
        self.assertEqual(len(result['conditional_modern_literal_footnote_previews']['plans']), 1)
        self.assertFalse(result['compiler_invoked'] or result['applied_fixes'])
        self.assertEqual((self.fixture.project / 'main.typ').read_bytes(), source)
        with patch.object(preview, 'MAX_CHECKS', 0):
            limited = document_cli.report_document(**kw, preview_conditional_modern_literal_footnotes=True)
        self.assertEqual(base['conditional_modern_combined_previews'], limited['conditional_modern_combined_previews'])
        self.assertFalse(limited['conditional_modern_literal_footnote_previews']['plans'])
        self.assertEqual(limited['conditional_modern_literal_footnote_previews']['status'], 'INCOMPLETE')
        size = len(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8'))
        with patch.object(document_cli, 'MAX_MODERN_REPORT_BYTES', size):
            self.assertEqual(result, document_cli.report_document(**kw, preview_conditional_modern_literal_footnotes=True))
        with patch.object(document_cli, 'MAX_MODERN_REPORT_BYTES', size - 1):
            with self.assertRaises(InputError):
                document_cli.report_document(**kw, preview_conditional_modern_literal_footnotes=True)
        # Find and test the exact shared item boundary, including both previews.
        low, high = 1, 2048
        while low < high:
            middle = (low + high) // 2
            try:
                with patch.object(document_cli, 'MAX_REPORT_ITEMS', middle):
                    document_cli.report_document(**kw, preview_conditional_modern_literal_footnotes=True)
            except InputError:
                low = middle + 1
            else:
                high = middle
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', low):
            self.assertEqual(result, document_cli.report_document(**kw, preview_conditional_modern_literal_footnotes=True))
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', low - 1):
            with self.assertRaises(InputError):
                document_cli.report_document(**kw, preview_conditional_modern_literal_footnotes=True)


if __name__ == '__main__':
    unittest.main()
