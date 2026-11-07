"""Real pinned ASTs and the complete reviewed module, never invented owners."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import plan
from flowbox_rules import (RULE_ID, REVIEWED_SOURCE_SHA256, REVIEWED_OWNER_SIGNATURE_SHA256,
                           _border_slot, _signature, analyze, prove_owner)
from static_resolver import resolve
from syntax_client import EXECUTABLE, parse
from suppression import resolve as suppressions
from typst_lint import digest, load_rules

PACKAGE = TOOL.parents[1] / 'local/math-blocks/0.2.0/lib.typ'
STROKE = b'stroke: 1pt + theme.rule'
DIRECTIVE = b'// typst-lint: ignore-next pde.flowbox-colored-border -- reviewed example\n'


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter explicitly first')
class FlowboxTests(unittest.TestCase):
    def source(self, stroke=b'1pt'):
        original = PACKAGE.read_bytes()
        self.assertEqual(original.count(STROKE), 1)
        return original.replace(STROKE, b'stroke: ' + stroke)

    def check(self, source):
        tree = parse(source)
        self.assertFalse(tree.nodes[0].errors, source[-300:])
        return analyze(tree, 'candidate-lib.typ', resolve(tree))

    def test_reviewed_artifact_and_independent_normalized_pin(self):
        source = PACKAGE.read_bytes()
        self.assertEqual(digest(source), REVIEWED_SOURCE_SHA256)
        tree = parse(source)
        self.assertEqual(_signature(tree, _border_slot(tree)[1]), REVIEWED_OWNER_SIGNATURE_SHA256)
        proof = prove_owner(tree)
        self.assertTrue(proof.colored)
        self.assertEqual(proof.palette_identifier, 'theme')
        self.assertEqual(proof.source_sha256, REVIEWED_SOURCE_SHA256)
        self.assertFalse(self.check(source).diagnostics or self.check(source).edits)

    def test_exact_one_insertion_preserves_original_width_and_all_other_bytes(self):
        for width in (b'1pt', b'.75pt', b'01.250pt', b'2.5mm', b'3cm', b'1in', b'1em', b'0pt'):
            with self.subTest(width=width):
                source = self.source(width)
                result = self.check(source)
                self.assertEqual(len(result.diagnostics), 1)
                self.assertEqual(len(result.edits), 1)
                edit = result.edits[0]
                self.assertEqual(edit.start, edit.end)
                self.assertEqual(edit.replacement, b' + theme.rule')
                candidate = plan(source, result.edits).candidate()
                self.assertEqual(candidate, source.replace(b'stroke: ' + width + b',',
                                                          b'stroke: ' + width + b' + theme.rule,'))
                final = self.check(candidate)
                self.assertFalse(final.diagnostics or final.edits)
                self.assertEqual(final.skips, result.skips)
                self.assertEqual(PACKAGE.read_bytes().count(STROKE), 1)

    def test_supported_colored_width_variants_are_not_findings(self):
        for value in (b'2.50pt + theme.rule', b'1em /* width */ + /* palette */ theme.rule'):
            result = self.check(self.source(value))
            self.assertFalse(result.diagnostics or result.edits)
            self.assertIsNotNone(prove_owner(parse(self.source(value))))

    def test_arbitrary_helpers_figures_and_copied_fragments_are_not_owners(self):
        fragment = b'#let flowbox(body) = context { let theme = theme-from-text-fill(); block(stroke: 1pt, body) }\n'
        for source in (
            fragment,
            b'#import "@local/math-blocks:0.2.0": *\n' + fragment,
            b'#let other(body) = { block(stroke: 1pt, body) }\n',
            b'#figure(block(stroke: 1pt)[An arbitrary border])\n',
            self.source().replace(b'#let flowbox(body)', b'#let unrelated(body)'),
            self.source() + b'\n#block(stroke: 1pt)[extra]\n',
        ):
            with self.subTest(source=source[-150:]):
                result = self.check(source)
                self.assertFalse(result.diagnostics or result.edits)
                self.assertIsNone(prove_owner(parse(source)))
                self.assertTrue(result.skips)

    def test_other_semantic_source_changes_and_shadowing_invalidate_ownership(self):
        original = self.source()
        for old, new in (
            (b'let theme = theme-from-text-fill()', b'let theme = (rule: red)'),
            (b'let theme = theme-from-text-fill()', b'let theme = theme-from-text-fill(); let theme = (rule: red)'),
            (b'let theme-from-text-fill() = {', b'let theme-from-text-fill() = { return (rule: red);'),
            (b'let flowbox(body)', b'let flowbox(body, theme-from-text-fill: none)'),
            (b'let theme = theme-from-text-fill()', b'let theme = theme-from-text-fill(); theme.rule = red'),
            (b'let theme = theme-from-text-fill()', b'let theme = theme-from-text-fill(); let block = (..args) => []'),
            (b'rule: rgb("#2c3e50")', b'rule: rgb("#2c3e51")'),
            (b'align(center, body)', b'align(left, body)'),
            (b'let math_font = "New Computer Modern Math"', b'let math_font = "Other font"'),
            (b'[Proof. ]', b'[Proof.  ]'),
            (b'$& wide dots.h.c thin$', b'$& wide dots.h.c  thin$'),
        ):
            self.assertIn(old, original)
            with self.subTest(change=new):
                result = self.check(original.replace(old, new))
                self.assertFalse(result.diagnostics or result.edits)

    def test_dynamic_nonliteral_nonlength_and_unrelated_color_strokes_are_unresolved(self):
        for value in (b'width', b'none', b'red', b'1%', b'-1pt', b'(1pt)', b'1pt * 2',
                      b'(thickness: 1pt)', b'1pt + red', b'1pt + theme.text',
                      b'1pt + theme.rule + red', b'theme.rule + 1pt'):
            with self.subTest(value=value):
                source = self.source(value)
                result = self.check(source)
                self.assertFalse(result.diagnostics or result.edits)
                self.assertIsNone(prove_owner(parse(source)))

    def test_code_comments_crlf_bom_and_trailing_comment_are_byte_preserved(self):
        source = self.source(b'0.750pt /* literal width, keep me */')
        source = source.replace(b'  let theme = theme-from-text-fill()',
                                b'  /* contextual palette */ let theme = theme-from-text-fill()')
        source = b'\xef\xbb\xbf' + source.replace(b'\n', b'\r\n')
        result = self.check(source)
        self.assertEqual(len(result.edits), 1)
        candidate = plan(source, result.edits).candidate()
        self.assertEqual(candidate, source.replace(b'0.750pt /* literal', b'0.750pt + theme.rule /* literal'))
        self.assertEqual(candidate.count(b'\r\n'), source.count(b'\r\n'))
        self.assertTrue(candidate.startswith(b'\xef\xbb\xbf'))
        self.assertFalse(self.check(candidate).diagnostics or self.check(candidate).edits)

    def test_suppression_targets_the_owner_or_border_not_another_statement(self):
        source = self.source()
        for old, new in (
            (b'#let flowbox(body)', DIRECTIVE + b'#let flowbox(body)'),
            (b'  let theme = theme-from-text-fill()\n\n  block(',
             b'  let theme = theme-from-text-fill()\n\n  ' + DIRECTIVE + b'  block('),
        ):
            result = self.check(source.replace(old, new))
            self.assertFalse(result.diagnostics or result.edits)
            self.assertEqual(len(result.suppressed), 1)
        source = source.replace(b'#let dots_space = {', DIRECTIVE + b'#let dots_space = {')
        result = self.check(source)
        self.assertEqual(len(result.diagnostics), 1)
        self.assertFalse(result.suppressed)
        self.assertEqual(len(result.edits), 1)
        candidate = plan(source, result.edits).candidate()
        known = {r['id'] for r in load_rules()[0]['rules']}
        before = suppressions(parse(source), known)
        after = suppressions(parse(candidate), known)
        self.assertEqual(before, after)  # The different statement ends before the insertion.
        self.assertFalse(self.check(candidate).diagnostics or self.check(candidate).edits)

    def test_malformed_stale_and_candidate_proof_failure_never_propose_repairs(self):
        tree = parse(b'#let flowbox( = {')
        self.assertTrue(analyze(tree, 'bad.typ', resolve(tree)).parse_errors)
        tree = parse(self.source())
        resolution = resolve(tree)
        stale = replace(resolution, source_sha256='stale')
        self.assertFalse(analyze(tree, 'stale.typ', stale).diagnostics)
        self.assertFalse(analyze(tree, 'stale.typ', stale).edits)
        with patch('flowbox_rules.parse', return_value=parse(b'#let other = 1')):
            result = analyze(tree, 'candidate-lib.typ', resolution)
        self.assertEqual(len(result.diagnostics), 1)
        self.assertFalse(result.edits)
        self.assertTrue(any('fix_unavailable' in reason for reason in result.skips))


if __name__ == '__main__':
    unittest.main()
