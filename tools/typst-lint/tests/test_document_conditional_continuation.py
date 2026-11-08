"""Independent source-only preview of literal prose continuation lines."""
from dataclasses import replace
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_conditional_continuation as continuation
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE, parse
from typst_lint import digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalContinuationTests(unittest.TestCase):
    def preview(self, source):
        return continuation.preview(parse(source), 'main.typ')

    def candidate(self, source):
        result = self.preview(source)
        self.assertIsNotNone(result)
        self.assertEqual(result['verification'], 'conditional-continuation-syntax-directives-and-reanalysis', result)
        self.assertFalse(result['execution_proved'] or result['render_equivalence_proved']
                         or result['edit_permission'] or result['compiler_permission'])
        self.assertTrue(result['conditions'])
        edits = [Edit(row['start'], row['end'], row['replacement'].encode()) for row in result['edits']]
        candidate = plan(source, edits).candidate()
        self.assertEqual(result['baseline_sha256'], digest(source))
        self.assertEqual(result['candidate_sha256'], digest(candidate))
        self.assertIsNone(self.preview(candidate))
        return result, candidate

    def test_lf_crlf_indent_and_utf8_bytes_are_preserved(self):
        for newline in (b'\n', b'\r\n'):
            source = ('#block[한글. This solution holds.'.encode() + newline
                      + b'  We continue.]')
            with self.subTest(newline=newline):
                result, candidate = self.candidate(source)
                self.assertEqual(len(result['edits']), 1)
                self.assertEqual(candidate, source.replace(newline + b'  ', b' '))

    def test_multiple_edits_and_other_rule_findings_survive_mapping(self):
        source = (b'#block[This solution holds.\nWe continue.\n'
                  b'This condition holds.\nThey follow.\n\n'
                  b'Claim #footnote[Reason.]\n\nNext.]')
        result, candidate = self.candidate(source)
        self.assertEqual(len(result['edits']), 2)
        self.assertIn(b'This solution holds. We continue.', candidate)
        self.assertIn(b'This condition holds. They follow.', candidate)
        self.assertIn(b'Claim #footnote[Reason.]\n\nNext.', candidate)

    def test_suppressed_directive_remains_attached(self):
        source = (b'// typst-lint: ignore-next pde.continuation-line -- intentional\n'
                  b'#block[This solution holds.\nWe continue.]\n'
                  b'#block[This theorem holds.\nWe continue.]')
        result, candidate = self.candidate(source)
        self.assertEqual(len(result['edits']), 1)
        self.assertIn(b'This solution holds.\nWe continue.', candidate)
        self.assertIn(b'This theorem holds. We continue.', candidate)

    def test_ambiguity_comments_structural_boundaries_and_blank_lines_are_skipped(self):
        cases = (b'#block[This was Fig.\nWe continue.]',
                 b'#block[This solution holds.\n\nWe continue.]',
                 b'#block[This solution holds. // attached\nWe continue.]',
                 b'#block[This solution holds.\\\nWe continue.]',
                 b'#block[$ x $\nWe continue.]',
                 b'#block[This solution holds.\n- We continue.]',
                 b'`This solution holds.\nWe continue.`')
        for source in cases:
            with self.subTest(source=source):
                result = self.preview(source)
                self.assertTrue(result is None or not result['edits'], result)

    def test_changed_other_findings_and_parse_failure_withhold(self):
        source = b'#block[This solution holds.\nWe continue. Claim#footnote[X]]'
        original = continuation.footnote_rules.analyze_conditional_nested
        def changed(tree, *args, **kwargs):
            values = original(tree, *args, **kwargs)
            if b'holds. We' in tree.source:
                rule = values[continuation.footnote_rules.PUNCT]
                if rule.diagnostics:
                    values[continuation.footnote_rules.PUNCT] = replace(
                        rule, diagnostics=(replace(rule.diagnostics[0], explanation='altered'),))
            return values
        with patch.object(continuation.footnote_rules, 'analyze_conditional_nested', side_effect=changed):
            result = self.preview(source)
        self.assertEqual(result['verification'], 'withheld')
        self.assertFalse(result['edits'])
        with patch.object(continuation, 'parse', side_effect=ValueError('fixture parser failure')):
            result = self.preview(source)
        self.assertEqual(result['verification'], 'withheld')
        self.assertFalse(result['edits'])

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1 and Typst')
    def test_owned_fixture_pdf_comparison_without_source_write(self):
        source = b'#block[This solution holds.\nWe continue.]'
        _, candidate = self.candidate(source)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outputs = []
            for label, contents in (('before', source), ('after', candidate)):
                target = root / label / 'main.typ'
                target.parent.mkdir()
                target.write_bytes(contents)
                output = target.parent / 'output.pdf'
                run = subprocess.run(['typst', 'compile', '--root', str(target.parent),
                                      '--creation-timestamp', '0', str(target), str(output)],
                                     capture_output=True, timeout=30)
                self.assertEqual(run.returncode, 0, run.stderr)
                outputs.append(output.read_bytes())
            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual((root / 'before/main.typ').read_bytes(), source)
