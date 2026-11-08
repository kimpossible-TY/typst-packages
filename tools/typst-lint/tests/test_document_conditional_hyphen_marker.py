"""Hyphenated paragraph markers yield separate source-only conditional hints."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import document_conditional_hyphen_marker as marker
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalHyphenMarkerTests(unittest.TestCase):
    def test_gap_and_capitalization_preserve_utf8_offsets_and_crlf(self):
        for newline in (b'\n', b'\r\n'):
            source = '#block[한글.'.encode() + newline + b'  #paragraph-tab' + newline + b'therefore it follows.]'
            result = marker.analyze(source, 'main.typ')
            self.assertEqual(result['status'], 'PARTIAL', result)
            self.assertEqual(result['source_sha256'], digest(source))
            entries = {row['rule_id']: row for row in result['diagnostics']}
            self.assertEqual(set(entries), {'pde.paragraph-marker-gap', 'pde.paragraph-capitalization'})
            gap = entries['pde.paragraph-marker-gap']
            cap = entries['pde.paragraph-capitalization']
            self.assertEqual(source[gap['start']:gap['end']], b'#paragraph-tab')
            self.assertEqual(source[cap['start']:cap['end']], b't')
            self.assertEqual(cap['marker_start'], gap['start'])
            self.assertEqual((gap['line'], gap['column'], cap['line'], cap['column']), (2, 3, 3, 1))
            self.assertFalse(result['runtime_binding_proved'] or result['execution_proved']
                             or result['edit_permission'] or result['compiler_permission']
                             or result['coverage_complete'] or result['render_equivalence_proved'])

    def test_heading_and_display_targets_have_separate_rule(self):
        for target in (b'= Heading', b'$ x $'):
            result = marker.analyze(b'#block[First.\n#paragraph-tab\n' + target + b']', 'main.typ')
            self.assertEqual([x['rule_id'] for x in result['diagnostics']], ['pde.paragraph-marker-target'], result)

    def test_root_gap_is_conditional_and_respects_targets_and_suppression(self):
        source = '첫 문장.\n#paragraph-tab\nText.'.encode()
        result = marker.analyze(source, 'main.typ')
        finding, = result['diagnostics']
        self.assertEqual(finding['rule_id'], 'pde.paragraph-marker-gap')
        self.assertEqual((finding['line'], finding['column']), (2, 1))
        self.assertEqual(source[finding['start']:finding['end']], b'#paragraph-tab')
        for source in (b'First.\n\n#paragraph-tab\nText.', b'First.\n#paragraph-tab\n= Heading',
                       b'First.\n#paragraph-tab\n$ x $'):
            with self.subTest(source=source):
                self.assertFalse(marker.analyze(source, 'main.typ')['diagnostics'])
        suppressed = (b'// typst-lint: ignore-next pde.paragraph-marker-gap -- intended\n'
                      b'#paragraph-tab\nText.')
        result = marker.analyze(suppressed, 'main.typ')
        self.assertFalse(result['diagnostics'])
        self.assertEqual(len(result['suppressed']), 1)

    def test_underscore_markers_comments_and_strings_are_not_reported(self):
        cases = (b'#block[First.\n#paragraph_tab\ntherefore x.]',
                 b'// #paragraph-tab\n#block[First.\n#paragraph_tab\ntherefore x.]',
                 b'#let a = "#paragraph-tab"\n#block[First.\n#paragraph_tab\ntherefore x.]')
        for source in cases:
            with self.subTest(source=source):
                result = marker.analyze(source, 'main.typ')
                self.assertFalse(result['diagnostics'] or result['suppressed'], result)

    def test_mixed_spelling_reports_only_hyphenated_tokens(self):
        source = (b'#block[First.\n#paragraph_tab\ntherefore one.\n'
                  b'#paragraph-tab\ntherefore two.]')
        result = marker.analyze(source, 'main.typ')
        self.assertEqual(len(result['diagnostics']), 2, result)
        starts = {source.find(b'#paragraph-tab')}
        self.assertEqual({row['marker_start'] for row in result['diagnostics']}, starts)

    def test_reasoned_suppression_attaches_to_original_marker(self):
        source = (b'#block[First.\n// typst-lint: ignore-next pde.paragraph-marker-gap -- deliberate\n'
                  b'#paragraph-tab\ntherefore x.]')
        result = marker.analyze(source, 'main.typ')
        self.assertEqual([x['rule_id'] for x in result['suppressed']], ['pde.paragraph-marker-gap'])
        self.assertEqual([x['rule_id'] for x in result['diagnostics']], ['pde.paragraph-capitalization'])

    def test_explicit_shadow_is_unresolved(self):
        body = b'#block[First.\n#paragraph-tab\ntherefore x.]'
        for prefix in (b'#let paragraph-tab = []\n', b'#import "x.typ" as paragraph-tab\n',
                       b'#import "x.typ": other as paragraph-tab\n'):
            result = marker.analyze(prefix + body, 'main.typ')
            self.assertFalse(result['diagnostics'] or result['suppressed'])
            self.assertTrue(result['unresolved'])

    def test_virtual_parser_failure_and_limits_return_unavailable_not_partial(self):
        source = b'#block[First.\n#paragraph-tab\ntherefore x.]'
        original = marker.parse
        def fail_virtual(data, *args):
            if b'#paragraph_tab' in data:
                raise ValueError('virtual parse failure')
            return original(data, *args)
        with patch.object(marker, 'parse', side_effect=fail_virtual):
            result = marker.analyze(source, 'main.typ')
        self.assertEqual(result['status'], 'UNAVAILABLE')
        self.assertFalse(result['diagnostics'] or result['suppressed'])
        with patch.object(marker, 'MAX_HINT_ITEMS', 1):
            result = marker.analyze(source, 'main.typ')
        self.assertEqual(result['status'], 'UNAVAILABLE')
        self.assertFalse(result['diagnostics'] or result['suppressed'])
        with self.assertRaises(InputError):
            marker.analyze('not bytes', 'main.typ')
