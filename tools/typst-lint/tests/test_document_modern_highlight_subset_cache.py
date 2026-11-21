"""Subset baseline caching retains per-candidate validation and capture checks."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_modern_highlight as modern
import document_modern_highlight_preview as preview
import test_conditional_modern_highlight as fixtures
from syntax_client import Node, Tree
from typst_lint import Diagnostic, InputError, SafetyError


class SubsetBaselineTests(unittest.TestCase):
    def setUp(self):
        self.tree = Tree(b'x', 'markup', (Node('Markup', 0, 1, (), False, (), None),))
        self.records = [{'id': 'x', 'grade': 'warning', 'source': 'test'}]
        self.baseline = preview._SubsetBaseline(self.tree, 'main.typ', self.records)

    def test_original_tree_catalog_and_path_are_bound(self):
        self.baseline.check(self.tree, 'main.typ', self.records)
        with self.assertRaises(SafetyError):
            self.baseline.check(replace(self.tree), 'main.typ', self.records)
        with self.assertRaises(SafetyError):
            self.baseline.check(self.tree, 'other.typ', self.records)
        self.records[0]['grade'] = 'error'
        with self.assertRaises(SafetyError):
            self.baseline.check(self.tree, 'main.typ', self.records)
        self.records[0]['grade'] = 'warning'
        object.__setattr__(self.tree.nodes[0], 'end', 0)
        with self.assertRaises(SafetyError):
            self.baseline.check(self.tree, 'main.typ', self.records)

    def test_cached_diagnostics_are_reconstructed_from_immutable_values(self):
        finding = Diagnostic('x', 'warning', 'main.typ', 1, 1, 'test', 'test')
        result = preview._BaselineFindings((finding,), ())
        first = self.baseline.get('example', lambda: result)
        object.__setattr__(first.diagnostics[0], 'explanation', 'mutated')
        second = self.baseline.get('example', lambda: self.fail('must reuse baseline'))
        self.assertEqual(second.diagnostics[0].explanation, 'test')


@unittest.skipUnless(fixtures.EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class SubsetIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ConditionalModernHighlightTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.source = (fixtures.IMPORT + b'#highlighted[Bad $ x $ end.]\n'
                       b'#highlighted[Good $x$ end.]\n#highlighted[More $y$ end.]')
        session, self.context = self.fixture.session(self.source)
        self.evidence = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)

    def test_subset_reuses_original_analysis_but_checks_every_candidate(self):
        original = preview.analyze_scripts
        counts = {'before': 0, 'candidate': 0}
        def count(tree, *args, **kwargs):
            counts['before' if tree.source == self.source else 'candidate'] += 1
            return original(tree, *args, **kwargs)
        with patch.object(preview, 'analyze_scripts', side_effect=count):
            first = preview.preview_all(self.evidence)
        self.assertEqual(first['previewed_diagnostics'], 2)
        self.assertEqual(counts['before'], 1)  # display conflict rejects initial union before script analysis
        self.assertGreaterEqual(counts['candidate'], 3)
        with patch.object(preview, 'analyze_scripts', side_effect=count):
            second = preview.preview_all(self.evidence)
        self.assertEqual(first, second)
        self.assertEqual(counts['before'], 2)  # independent replay has its own baseline

    def test_catalog_mutation_between_subsets_withholds_entire_source(self):
        original = preview._preview_source
        calls = 0
        def mutate(*args, **kwargs):
            nonlocal calls
            value = original(*args, **kwargs)
            if preview._SUBSET_BASELINE.get() is not None:
                calls += 1
                if calls == 1:
                    args[4][0]['grade'] = 'tampered'
            return value
        with patch.object(preview, '_preview_source', side_effect=mutate):
            row, = preview.preview_all(self.evidence)['by_file']
        self.assertEqual(row['verification'], 'withheld')
        self.assertIn('baseline changed', row['reason'])
        self.assertFalse(row['edits'])

    def test_tree_mutation_between_subsets_invalidates_capture(self):
        original = preview._preview_source
        tree = self.context.source_for('main.typ').tree
        root = tree.nodes[0]
        end = root.end
        calls = 0
        def mutate(*args, **kwargs):
            nonlocal calls
            value = original(*args, **kwargs)
            if preview._SUBSET_BASELINE.get() is not None:
                calls += 1
                if calls == 1:
                    object.__setattr__(root, 'end', end - 1)
            return value
        try:
            with patch.object(preview, '_preview_source', side_effect=mutate):
                with self.assertRaises(InputError):
                    preview.preview_all(self.evidence)
        finally:
            object.__setattr__(root, 'end', end)

    def test_candidate_analysis_is_not_reused(self):
        original = preview.analyze_scripts
        def changed(tree, *args, **kwargs):
            result = original(tree, *args, **kwargs)
            if tree.source != self.source and preview._SUBSET_BASELINE.get() is not None:
                finding = Diagnostic('core.parenthesized-scripts', 'warning', 'main.typ',
                                     1, 1, 'changed candidate', 'test')
                return replace(result, diagnostics=result.diagnostics + (finding,))
            return result
        with patch.object(preview, 'analyze_scripts', side_effect=changed):
            row, = preview.preview_all(self.evidence)['by_file']
        self.assertEqual(row['verification'], 'withheld')
        self.assertFalse(row['edits'])


if __name__ == '__main__':
    unittest.main()
