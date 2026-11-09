"""Conditional root marker adjacency never infers bindings or proposes writes."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_root_marker_gap as root_gap
import document_local_rules
from syntax_client import EXECUTABLE, parse
from suppression import Scope
from typst_lint import load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class RootMarkerGapTests(unittest.TestCase):
    def analyze(self, source):
        tree = parse(source)
        self.assertFalse(tree.nodes[0].errors, source)
        result = root_gap.analyze(tree, 'fixture.typ')
        self.assertFalse(result.edits)
        return result

    def test_direct_root_heading_display_and_literal_content_call(self):
        cases = (b'= Heading\n#paragraph_tab\nText.',
                 b'$ x $\n#paragraph_tab\nText.',
                 b'$ x $ <eq>\n#paragraph_tab\nText.',
                 b'#definition[Statement.]\n#paragraph_tab\nText.',
                 b'#flowbox[Statement.]\n#paragraph_tab\nText.',
                 b'#block[Statement.]\n#paragraph_tab\nText.',
                 '= 제목\r\n  #paragraph_tab\r\nText.'.encode())
        for source in cases:
            with self.subTest(source=source):
                result = self.analyze(source)
                finding, = result.diagnostics
                self.assertEqual(finding.rule_id, 'pde.paragraph-marker-gap')
                self.assertEqual(finding.line, 2)
                self.assertIn('Conditional source hint', finding.explanation)
                self.assertEqual(finding.column, 3 if b'\r\n  #' in source else 1)
                self.assertFalse(result.suppressed)

    def test_existing_blank_and_non_equivalent_boundaries_are_not_hints(self):
        cases = (b'= Heading\n\n#paragraph_tab\nText.',
                 b'$ x $\n\n#paragraph_tab\nText.',
                 b'$x$\n#paragraph_tab\nText.',  # inline math
                 b'#block(breakable: false)[Text.]\n#paragraph_tab\nText.',
                 b'#flowbox()[Text.]\n#paragraph_tab\nText.',
                 b'#block(dynamic)[Text.]\n#paragraph_tab\nText.',
                 b'#ns.block[Text.]\n#paragraph_tab\nText.',
                 b'#block[Text.] <label>\n#paragraph_tab\nText.',
                 b'#block[Text.] // comment\n#paragraph_tab\nText.',
                 b'#block[Text.]\n// comment\n#paragraph_tab\nText.',
                 b'#block[Text.]\n\n#paragraph_tab\nText.',
                 b'#block[#definition[Text.]\n#paragraph_tab\nText.]',
                 b'`#block[Text.]\n#paragraph_tab\nText.`',
                 b'#box[$ x $]\n#paragraph_tab\nText.',
                 b'#block[= Heading\n#paragraph_tab\nText.]',
                 b'$ x $\n#paragraph_tab\n$ y $',
                 b'$ x $\n#paragraph_tab\n= Heading',
                 b'$ x $\n#paragraph_tab(extra)\nText.',
                 b'$ x $\n#paragraph_tab // comment\nText.',
                 b'$ x $ // comment\n#paragraph_tab\nText.',
                 b'$ x $\n// comment\n#paragraph_tab\nText.',
                 b'$ x $\n<eq>\n#paragraph_tab\nText.',
                 b'$ x $ <eq> <other>\n#paragraph_tab\nText.',
                 b'`= Heading\n#paragraph_tab\nText.`',
                 b'#let s = "= Heading\\n#paragraph_tab\\nText."')
        for source in cases:
            with self.subTest(source=source):
                self.assertFalse(self.analyze(source).diagnostics)

    def test_explicit_shadows_are_withheld(self):
        cases = (b'#let paragraph_tab = none\n',
                 b'#let f(paragraph_tab) = []\n',
                 b'#import "other.typ" as paragraph_tab\n',
                 b'#import "other.typ": item as paragraph_tab\n',
                 b'#{paragraph_tab = none}\n')
        for prefix in cases:
            with self.subTest(prefix=prefix):
                result = self.analyze(prefix + b'= Heading\n#paragraph_tab\nText.')
                self.assertFalse(result.diagnostics)
                self.assertTrue(result.skips)

    def test_suppression_and_local_report_integration(self):
        source = b'$ x $\n#paragraph_tab\nText.\n\n= Heading\n#paragraph_tab\nMore.'
        start = source.index(b'#paragraph_tab')
        scope = Scope(frozenset(('pde.paragraph-marker-gap',)), 'test', start, start + 14, 0)
        with patch.object(root_gap, 'resolve_suppressions', return_value=(scope,)):
            result = self.analyze(source)
            self.assertEqual((len(result.diagnostics), len(result.suppressed)), (1, 1))
        _, values = document_local_rules.analyze(parse(source), 'fixture.typ', load_rules()[0]['rules'])
        merged = values['pde.paragraph-marker-gap']
        self.assertEqual((len(merged.diagnostics), len(merged.suppressed)), (2, 0))
        self.assertFalse(merged.edits)
        self.assertTrue(any('binding' in reason and 'unproved' in reason for reason in merged.skips))
        intervening = (b'$ x $\n// typst-lint: ignore-next pde.paragraph-marker-gap -- reviewed\n'
                       b'#paragraph_tab\nText.')
        self.assertFalse(self.analyze(intervening).diagnostics)

    def test_root_literal_block_does_not_duplicate_local_hint(self):
        source = b'#block[Inside.]\n#paragraph_tab\nOutside.'
        _, values = document_local_rules.analyze(parse(source), 'fixture.typ', load_rules()[0]['rules'])
        result = values['pde.paragraph-marker-gap']
        self.assertEqual(len(result.diagnostics), 1)
        self.assertFalse(result.edits)

    def test_malformed_source_is_not_diagnosed(self):
        result = root_gap.analyze(parse(b'= Heading\n#paragraph_tab\n#let x = ('), 'fixture.typ')
        self.assertFalse(result.diagnostics or result.edits)

    def test_cli_hint_is_opt_in_and_does_not_modify_source(self):
        source = (b'= Heading\n#paragraph_tab\nText.\n\n$ x $ <eq>\n#paragraph_tab\nMore.\n\n'
                  b'#definition[Statement.]\r\n#paragraph_tab\r\nNext.')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = root / 'main.typ'
            entry.write_bytes(source)
            base = [sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
                    '--root', str(root), '--entrypoint', 'main.typ', '--rule-set', 'pde',
                    '--format', 'json']
            def invoke(*extra):
                run = subprocess.run([*base, *extra], capture_output=True, text=True, timeout=15)
                self.assertEqual(run.returncode, 2, run.stderr)
                self.assertFalse(run.stderr)
                return json.loads(run.stdout)
            ordinary = invoke()
            hinted = invoke('--local-rule-hints')
            self.assertNotIn('local_rule_hints', ordinary)
            hints = hinted['local_rule_hints']
            matches = [row for row in hints['diagnostics']
                       if row['rule_id'] == 'pde.paragraph-marker-gap']
            self.assertEqual([row['line'] for row in matches], [2, 6, 10])
            self.assertFalse(hinted['applied_fixes'] or hinted['compiler_invoked'])
            self.assertEqual(entry.read_bytes(), source)


if __name__ == '__main__':
    unittest.main()
