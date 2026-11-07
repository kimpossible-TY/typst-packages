"""Explicit retained paragraph metadata never falls back to a live catalog."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
import capitalization
import paragraph_rules
from package_loader import load_packages
from static_resolver import imported_paths, resolve
from syntax_client import EXECUTABLE, parse
from typst_lint import InputError, digest, load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class ParagraphCatalogInjectionTests(unittest.TestCase):
    def setUp(self):
        self.catalog = tuple(load_rules()[0]['rules'])
        self.source = (b'#import "@local/text-utils:0.1.2": paragraph_tab\n'
                       b'\n#paragraph_tab\nthis holds.\n')
        self.tree = parse(self.source)
        packages = load_packages(REPO, imported_paths(self.tree)).sources
        self.resolution = resolve(self.tree, packages=packages, fallback_language='en')
        start = self.source.index(b'this')
        marker = self.source.index(b'#paragraph_tab')
        self.context = capitalization.Context(digest(self.source), 'main.typ', 0,
            len(self.source), marker, marker + len(b'#paragraph_tab'), start, start + 4,
            b'this', capitalization.StartKind.LITERAL_WORD,
            capitalization.BodyKind.ORDINARY_PROSE,
            capitalization.MarkerBinding.PARAGRAPH_TAB, 'en')

    def test_differential_ordinary_outputs_and_no_ambient_reads(self):
        expected = paragraph_rules.analyze(self.tree, 'main.typ', self.resolution)
        expected_cap = capitalization.evaluate(self.source, self.context)
        with patch.object(paragraph_rules, 'load_rules', side_effect=AssertionError('live paragraph catalog')):
            with patch.object(capitalization, 'load_rules', side_effect=AssertionError('live capitalization catalog')):
                self.assertEqual(paragraph_rules.analyze(self.tree, 'main.typ',
                    self.resolution, catalog=self.catalog), expected)
                self.assertEqual(capitalization.evaluate(self.source, self.context,
                    catalog=self.catalog), expected_cap)

    def test_empty_invalid_or_duplicate_metadata_never_selects_disk(self):
        cap = next(record for record in self.catalog if record['id'] == capitalization.RULE_ID)
        for supplied in ((), {}, (None,), ({'id': capitalization.RULE_ID},),
                         self.catalog + (cap,), self.catalog + ({},),
                         self.catalog + ({'id': []},), self.catalog + ({'id': 3},)):
            with self.subTest(supplied=supplied):
                with patch.object(paragraph_rules, 'load_rules', side_effect=AssertionError('live read')):
                    with patch.object(capitalization, 'load_rules', side_effect=AssertionError('live read')):
                        with self.assertRaises(InputError):
                            paragraph_rules.analyze(self.tree, 'main.typ', self.resolution,
                                                    catalog=supplied)
                        with self.assertRaises(InputError):
                            capitalization.evaluate(self.source, self.context, catalog=supplied)

    def test_retained_metadata_actually_supplies_diagnostics(self):
        records = tuple(dict(record, grade='error', source='retained-test')
                        if record['id'] == capitalization.RULE_ID else record
                        for record in self.catalog)
        result = paragraph_rules.analyze(self.tree, 'main.typ', self.resolution,
                                         catalog=records)[capitalization.RULE_ID]
        self.assertEqual((result.diagnostics[0].grade, result.diagnostics[0].source),
                         ('error', 'retained-test'))
        self.assertEqual(result.edits, (capitalization.evaluate(self.source, self.context,
                                                               catalog=records).edit,))

    def test_uppercase_supplied_empty_catalog_is_still_invalid(self):
        source = self.source.replace(b'this', b'This')
        context = replace(self.context, source_sha256=digest(source), expected_first=b'This')
        with self.assertRaises(InputError):
            capitalization.evaluate(source, context, catalog=())
        with patch.object(capitalization, 'load_rules', side_effect=AssertionError('live read')):
            self.assertEqual(capitalization.evaluate(source, context,
                catalog=self.catalog).skip_reason, 'already-uppercase')


if __name__ == '__main__':
    unittest.main()
