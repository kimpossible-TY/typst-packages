"""Conditional raw label hints in literal scoped annotation callbacks."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import document_conditional_marker_tags as marker_tags
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalMarkerTagTests(unittest.TestCase):
    def test_literal_marker_tag_is_conditional_and_source_bound(self):
        source = '#local-scope-annotations(s => [$ 한글 + mark(x, tag: #<x>) $])'.encode()
        report = marker_tags.analyze(source, 'main.typ')
        finding, = report['diagnostics']
        self.assertEqual(source[finding['label_start']:finding['label_end']], b'<x>')
        self.assertEqual(finding['rule_id'], 'core.scoped-marker-tags')
        self.assertEqual(report['source_sha256'], digest(source))
        self.assertFalse(report['runtime_binding_proved'] or report['execution_proved']
                         or report['edit_permission'] or report['compiler_permission']
                         or report['coverage_complete'])
        self.assertEqual(finding['column'], len(source[:finding['start']].decode()) + 1)

    def test_generated_scoped_label_and_unrelated_labels_are_not_diagnosed(self):
        for source in (b'#local-scope-annotations(s => [$ mark(x, tag: #(s.tag)("x")) $])',
                       b'$ mark(x, tag: #<outside>) $\nordinary <semantic>',
                       b'#local-scope-annotations(s => [$ x $ <semantic>])',
                       b'#local-scope-annotations(s => [$ marker(x, tag: #<x>) $])'):
            with self.subTest(source=source):
                self.assertFalse(marker_tags.analyze(source, 'main.typ')['diagnostics'])

    def test_known_bindings_deferred_scopes_and_nonliteral_shapes_are_not_guessed(self):
        cases = (b'#let mark = (..args) => []\n#local-scope-annotations(s => [$ mark(x, tag: #<x>) $])',
                 b'#import "x.typ" as mark\n#local-scope-annotations(s => [$ mark(x, tag: #<x>) $])',
                 b'#import "x.typ": other as mark\n#local-scope-annotations(s => [$ mark(x, tag: #<x>) $])',
                 b'#let local-scope-annotations = (f) => f\n#local-scope-annotations(s => [$ mark(x, tag: #<x>) $])',
                 b'#let later = () => local-scope-annotations(s => [$ mark(x, tag: #<x>) $])',
                 b'#local-scope-annotations(s => [$ mark(x, tag: #<x>, fill: red) $])',
                 b'#local-scope-annotations(s => [$ mark(x, #<x>) $])',
                 b'#local-scope-annotations(s => [$ f(mark(x, tag: #<x>)) $])')
        for source in cases:
            with self.subTest(source=source):
                self.assertFalse(marker_tags.analyze(source, 'main.typ')['diagnostics'])

    def test_suppression_retains_a_separate_record(self):
        source = (b'// typst-lint: ignore-next core.scoped-marker-tags -- external label\n'
                  b'#local-scope-annotations(s => [$ mark(x, tag: #<external>) $])')
        report = marker_tags.analyze(source, 'main.typ')
        self.assertFalse(report['diagnostics'])
        self.assertEqual(len(report['suppressed']), 1)

    def test_invalid_input_and_parse_failure_are_not_clean_results(self):
        with self.assertRaises(InputError):
            marker_tags.analyze('not bytes', 'main.typ')
        with self.assertRaises(InputError):
            marker_tags.analyze(b'Hello', '', catalog=[])
        with patch.object(marker_tags, 'parse', side_effect=OSError('fixture unavailable')):
            result = marker_tags.analyze(b'Hello', 'main.typ')
        self.assertEqual(result['status'], 'UNAVAILABLE')
        self.assertTrue(result['unresolved'])
