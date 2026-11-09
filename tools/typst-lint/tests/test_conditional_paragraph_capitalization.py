"""Nested paragraph starter hints remain conditional and do not write."""
from pathlib import Path
import sys
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from paragraph_rules import CAPITALIZATION, analyze, analyze_conditional_nested_capitalization
from package_loader import load_packages
from static_resolver import resolve, TEXT_UTILS
from syntax_client import EXECUTABLE, parse
from typst_lint import InputError, load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalParagraphCapitalizationTests(unittest.TestCase):
    def analyze(self, source):
        tree = parse(source)
        self.assertFalse(tree.nodes[0].errors, source)
        return analyze_conditional_nested_capitalization(tree, 'chapter.typ')

    def test_supported_starters_report_conditional_locations_without_edits(self):
        for source in (b'#block[\n#paragraph_tab\ntherefore this holds.]',
                       b'#block[\r\n  #paragraph_tab\r\n  this $x$ holds.\r\n]',
                       '#block[한글\n#paragraph_tab\nwe know.]'.encode()):
            with self.subTest(source=source):
                result = self.analyze(source)
                self.assertEqual(len(result.diagnostics), 1)
                finding = result.diagnostics[0]
                self.assertEqual(finding.rule_id, CAPITALIZATION)
                self.assertIn('ordinary English prose', finding.explanation)
                self.assertIn('English language', result.skips[0])
                self.assertFalse(result.edits)

    def test_noneligible_or_nonliteral_starters_are_not_guessed(self):
        for tail in (b'iPhone is here.', b'eBay is here.', b'unknown follows.', b'i am here.',
                     b'This is correct.', b'this.', b'this123 starts.', b'$x$ is here.',
                     b'`this` is here.', b'#emph[this] is here.', b'[@key] is here.',
                     '한글 this follows.'.encode(), b'// comment\nthis follows.',
                     b'\nthis follows.', b'= this heading', b'- this item'):
            source = b'#block[\n#paragraph_tab\n' + tail + b'\n]'
            with self.subTest(tail=tail):
                self.assertFalse(self.analyze(source).diagnostics)

    def test_root_and_nonstandalone_markers_are_not_covered(self):
        for source in (b'#paragraph_tab\nthis follows.', b'#block[#paragraph_tab\nthis follows.]',
                       b'#block[\n#paragraph_tab extra\nthis follows.]',
                       b'#block[`#paragraph_tab\nthis follows.`]'):
            with self.subTest(source=source):
                self.assertFalse(self.analyze(source).diagnostics)

    def test_suppression_covers_nested_marker_or_body(self):
        source = (b'// typst-lint: ignore-next pde.paragraph-capitalization -- deliberate\n'
                  b'#block[\n#paragraph_tab\nthis follows.\n]')
        result = self.analyze(source)
        self.assertFalse(result.diagnostics)
        self.assertEqual(len(result.suppressed), 1)

    def test_current_root_and_nested_analyzers_do_not_duplicate(self):
        packages = load_packages(TOOL / 'tests/fixtures/reviewed-packages', (TEXT_UTILS,)).sources
        prefix = b'#import "@local/text-utils:0.1.2": paragraph_tab\n#set text(lang: "en")\n'
        for body in (b'#paragraph_tab\nthis follows.', b'#block[\n#paragraph_tab\nthis follows.]'):
            tree = parse(prefix + body)
            for evidence in ((), packages):
                with self.subTest(body=body, packages=bool(evidence)):
                    root = analyze(tree, 'chapter.typ', resolve(tree, packages=evidence))[CAPITALIZATION]
                    nested = analyze_conditional_nested_capitalization(tree, 'chapter.typ')
                    before = {(d.path, d.line, d.column) for d in (*root.diagnostics, *root.suppressed)}
                    after = {(d.path, d.line, d.column) for d in (*nested.diagnostics, *nested.suppressed)}
                    self.assertFalse(before & after)

    def test_invalid_metadata_and_malformed_syntax_are_refused(self):
        tree = parse(b'#block[\n#paragraph_tab\nthis follows.]')
        records = load_rules()[0]['rules']
        with self.assertRaises(InputError):
            analyze_conditional_nested_capitalization(tree, 'x.typ',
                catalog=[r for r in records if r['id'] != CAPITALIZATION])
        malformed = parse(b'#block[\n#paragraph_tab\nthis follows.')
        self.assertTrue(analyze_conditional_nested_capitalization(malformed, 'x.typ').parse_errors)


if __name__ == '__main__':
    unittest.main()
