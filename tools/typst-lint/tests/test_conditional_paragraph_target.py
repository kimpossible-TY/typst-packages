"""Nested structural-target hints do not establish binding or authorize edits."""
from pathlib import Path
import sys
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from paragraph_rules import MARKER_TARGET, analyze, analyze_conditional_nested_target
from package_loader import load_packages
from static_resolver import resolve, TEXT_UTILS
from syntax_client import EXECUTABLE, parse
from typst_lint import InputError, load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalParagraphTargetTests(unittest.TestCase):
    def check(self, source):
        tree = parse(source)
        self.assertFalse(tree.nodes[0].errors, source)
        return analyze_conditional_nested_target(tree, 'chapter.typ')

    def test_nested_heading_display_comments_and_crlf(self):
        for source, line, column in (
            (b'#block[First.\n#paragraph_tab\n= Heading\n]', 2, 1),
            (b'#block[\n#paragraph_tab\n/* keep */\n\n// keep\n$ x = y $\n]', 2, 1),
            ('#block[한글.\r\n  #paragraph_tab\t\r\n= 제목\r\n]'.encode(), 2, 3),
            (b'#block[\n$ x = y $\n#paragraph_tab\n$ y = z $ <label>\n]', 3, 1),
        ):
            with self.subTest(source=source):
                result = self.check(source)
                self.assertEqual(len(result.diagnostics), 1)
                finding = result.diagnostics[0]
                self.assertEqual((finding.line, finding.column, finding.rule_id), (line, column, MARKER_TARGET))
                self.assertIn('if this standalone name', finding.explanation)
                self.assertFalse(result.edits)
                self.assertIn('binding', result.skips[0])

    def test_root_prose_inline_math_and_lookalikes_not_diagnosed(self):
        for source in (
            b'#paragraph_tab\n= Heading\n',
            b'#block[\n#paragraph_tab\nProse.\n]',
            b'#block[\n#paragraph_tab\n$x=y$\n]',
            b'#block[\n#paragraph_tab inline\n= Heading\n]',
            b'#block[\n// #paragraph_tab\n= Heading\n]',
            b'#block[\n`#paragraph_tab`\n= Heading\n]',
            b'#block[\n#paragraph_tab\n#box[= Heading]\n]',
            b'#let sample = "#paragraph_tab\\n= Heading"',
            b'#let f() = { let paragraph_tab = 1; paragraph_tab }',
            b'= Heading #paragraph_tab\n',
        ):
            with self.subTest(source=source):
                self.assertFalse(self.check(source).diagnostics)

    def test_suppressed_marker_is_retained_without_an_edit(self):
        source = (b'// typst-lint: ignore-next pde.paragraph-marker-target -- deliberate\n'
                  b'#block[\n#paragraph_tab\n= Heading\n]')
        result = self.check(source)
        self.assertFalse(result.diagnostics or result.edits)
        self.assertEqual(len(result.suppressed), 1)

    def test_root_analyzer_and_conditional_locations_are_disjoint(self):
        packages = load_packages(TOOL / 'tests/fixtures/reviewed-packages', (TEXT_UTILS,)).sources
        prefix = b'#import "@local/text-utils:0.1.2": paragraph_tab\n'
        for body in (b'#paragraph_tab\n= Heading\n',
                     b'#block[\n#paragraph_tab\n= Heading\n]',
                     b'#footnote[\n#paragraph_tab\n$ x=y $\n]'):
            tree = parse(prefix + body)
            for evidence in ((), packages):
                with self.subTest(body=body, packages=bool(evidence)):
                    root = analyze(tree, 'chapter.typ', resolve(tree, packages=evidence))[MARKER_TARGET]
                    nested = analyze_conditional_nested_target(tree, 'chapter.typ')
                    old = {(d.line, d.column) for d in (*root.diagnostics, *root.suppressed)}
                    new = {(d.line, d.column) for d in (*nested.diagnostics, *nested.suppressed)}
                    self.assertFalse(old & new)

    def test_missing_catalog_and_malformed_input_fail_closed(self):
        tree = parse(b'#block[\n#paragraph_tab\n= Heading\n]')
        records = load_rules()[0]['rules']
        with self.assertRaises(InputError):
            analyze_conditional_nested_target(tree, 'chapter.typ',
                catalog=[row for row in records if row['id'] != MARKER_TARGET])
        malformed = parse(b'#block[\n#paragraph_tab\n= Heading')
        self.assertTrue(analyze_conditional_nested_target(malformed, 'chapter.typ').parse_errors)


if __name__ == '__main__':
    unittest.main()
