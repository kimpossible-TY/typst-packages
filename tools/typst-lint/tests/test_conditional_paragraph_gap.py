"""Nested paragraph marker source hints carry no binding or write authority."""
from pathlib import Path
import sys
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from paragraph_rules import MARKER_GAP, analyze, analyze_conditional_nested_gap
from package_loader import load_packages
from static_resolver import resolve, TEXT_UTILS
from syntax_client import EXECUTABLE, parse
from typst_lint import InputError, load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalParagraphGapTests(unittest.TestCase):
    def analyze(self, source):
        tree = parse(source)
        self.assertFalse(tree.nodes[0].errors, source)
        return analyze_conditional_nested_gap(tree, 'chapter.typ')

    def test_nested_marker_adjacent_and_content_opening_are_conditional(self):
        for source, line in ((b'#block[First.\n#paragraph_tab\nSecond.]', 2),
                             (b'#block[\n#paragraph_tab\nSecond.]', 2),
                             ('#block[한글.\r\n  #paragraph_tab\r\n다음.]'.encode(), 2)):
            with self.subTest(source=source):
                result = self.analyze(source)
                self.assertEqual(len(result.diagnostics), 1)
                self.assertEqual(result.diagnostics[0].line, line)
                self.assertEqual(result.diagnostics[0].rule_id, MARKER_GAP)
                self.assertIn('if this standalone name', result.diagnostics[0].explanation)
                self.assertFalse(result.edits)
                self.assertIn('binding', result.skips[0])

    def test_existing_blank_line_and_root_are_not_duplicated(self):
        for source in (b'#block[First.\n\n#paragraph_tab\nSecond.]',
                       b'#block[\n\n #paragraph_tab\nSecond.]',
                       b'First.\n#paragraph_tab\nSecond.'):
            with self.subTest(source=source):
                self.assertFalse(self.analyze(source).diagnostics)

    def test_heading_display_raw_and_nonstandalone_markers_are_excluded(self):
        for source in (b'#block[First.\n#paragraph_tab\n= Heading]',
                       b'#block[First.\n#paragraph_tab\n$ x = y $\n]',
                       b'#block[First.\n#paragraph_tab extra\nSecond.]',
                       b'#block[First.\n// #paragraph_tab\nSecond.]',
                       b'#block[`#paragraph_tab`]',
                       b'= Heading #paragraph_tab\n'):
            with self.subTest(source=source):
                self.assertFalse(self.analyze(source).diagnostics)

    def test_suppression_targets_nested_marker(self):
        source = (b'// typst-lint: ignore-next pde.paragraph-marker-gap -- deliberate\n'
                  b'#block[First.\n#paragraph_tab\nSecond.]')
        result = self.analyze(source)
        self.assertFalse(result.diagnostics)
        self.assertEqual(len(result.suppressed), 1)

    def test_current_root_and_nested_analyzers_have_disjoint_locations(self):
        packages = load_packages(TOOL / 'tests/fixtures/reviewed-packages', (TEXT_UTILS,)).sources
        prefix = b'#import "@local/text-utils:0.1.2": paragraph_tab\n'
        for body in (b'First.\n#paragraph_tab\nSecond.',
                     b'#block[First.\n#paragraph_tab\nSecond.]',
                     b'#footnote[First.\n#paragraph_tab\nSecond.]',
                     b'#f(s => [First.\n#paragraph_tab\nSecond.])',
                     b'// typst-lint: ignore-next pde.paragraph-marker-gap -- intentional\n'
                     b'#block[First.\n#paragraph_tab\nSecond.]'):
            tree = parse(prefix + body)
            for evidence in ((), packages):
                with self.subTest(body=body, packages=bool(evidence)):
                    old = analyze(tree, 'chapter.typ', resolve(tree, packages=evidence))[MARKER_GAP]
                    new = analyze_conditional_nested_gap(tree, 'chapter.typ')
                    old_locations = {(d.path, d.line, d.column) for d in (*old.diagnostics, *old.suppressed)}
                    new_locations = {(d.path, d.line, d.column) for d in (*new.diagnostics, *new.suppressed)}
                    self.assertFalse(old_locations & new_locations)

    def test_catalog_and_malformed_syntax_fail_closed(self):
        tree = parse(b'#block[First.\n#paragraph_tab\nSecond.]')
        records = load_rules()[0]['rules']
        with self.assertRaises(InputError):
            analyze_conditional_nested_gap(tree, 'x.typ', catalog=[r for r in records if r['id'] != MARKER_GAP])
        malformed = parse(b'#block[First.\n#paragraph_tab\nSecond.')
        self.assertTrue(analyze_conditional_nested_gap(malformed, 'x.typ').parse_errors)


if __name__ == '__main__':
    unittest.main()
