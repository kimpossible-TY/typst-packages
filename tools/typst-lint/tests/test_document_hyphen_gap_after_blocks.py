"""Opt-in marker gaps after conditionally classified block-like calls."""
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
import document_hyphen_gap_after_blocks as gap
from document_conditional_hyphen_marker_gap import preview as conservative
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE, parse
from typst_lint import digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class HyphenGapAfterBlocksTests(unittest.TestCase):
    def preview(self, source):
        return gap.preview(parse(source), 'main.typ')

    def candidate(self, source):
        result = self.preview(source)
        self.assertIsNotNone(result)
        self.assertEqual(result['verification'], 'conditional-block-adjacent-marker-gap-reanalysis', result)
        self.assertFalse(result['runtime_binding_proved'] or result['execution_proved']
                         or result['render_equivalence_proved'] or result['edit_permission']
                         or result['compiler_permission'])
        self.assertTrue(result['conditions'])
        candidate = plan(source, [Edit(e['start'], e['end'], e['replacement'].encode())
                                  for e in result['edits']]).candidate()
        self.assertEqual(result['baseline_sha256'], digest(source))
        self.assertEqual(result['candidate_sha256'], digest(candidate))
        self.assertIsNone(self.preview(candidate))
        return result, candidate

    def test_pagebreak_and_flowbox_with_indentation_and_crlf(self):
        for call in (b'#pagebreak(weak: true)', b'#pagebreak(weak: false)', b'#flowbox()[Content.]'):
            for newline in (b'\n', b'\r\n'):
                source = b'#block[' + call + newline + b'  #paragraph-tab' + newline + b'  Next.]'
                with self.subTest(call=call, newline=newline):
                    old = conservative(parse(source), 'main.typ')
                    self.assertEqual(old['verification'], 'withheld')
                    result, candidate = self.candidate(source)
                    self.assertEqual(len(result['edits']), 1)
                    edit = result['edits'][0]
                    self.assertEqual(edit['preceding_conditions'], gap.PRECEDING_CONDITIONS[edit['preceding_kind']])
                    self.assertIn(edit['preceding_kind'], edit['preceding_conditions'][0])
                    self.assertNotIn('flowbox' if edit['preceding_kind'] == 'pagebreak' else 'pagebreak',
                                     edit['preceding_conditions'][0])
                    self.assertEqual(candidate, source.replace(newline + b'  #paragraph-tab',
                                                               newline * 2 + b'  #paragraph-tab'))

    def test_unknown_or_ambiguous_calls_do_not_become_candidates(self):
        cases = (b'#block[#footnote[X]\n#paragraph-tab\nNext.]',
                 b'#block[#other()[X]\n#paragraph-tab\nNext.]',
                 b'#block[#pagebreak()\n#paragraph-tab\nNext.]',
                 b'#block[#pagebreak(weak: flag)\n#paragraph-tab\nNext.]',
                 b'#block[#flowbox(title: "x")[Text]\n#paragraph-tab\nNext.]',
                 b'#block[#flowbox()[Text] // comment\n#paragraph-tab\nNext.]',
                 b'#block[#flowbox()[Text]\n\n#paragraph-tab\nNext.]')
        for source in cases:
            with self.subTest(source=source):
                result = self.preview(source)
                self.assertTrue(result is None or not result['edits'], result)

    def test_known_shadows_aliases_parameters_and_mutations_are_refused(self):
        for name, call in ((b'pagebreak', b'#pagebreak(weak: true)'),
                           (b'flowbox', b'#flowbox()[Text]')):
            prefixes = (b'#let ' + name + b' = none\n',
                        b'#let use(' + name + b') = []\n',
                        b'#{ ' + name + b' = none }\n',
                        b'#import "other.typ" as ' + name + b'\n',
                        b'#import "other.typ": value as ' + name + b'\n')
            if name == b'pagebreak':
                prefixes += (b'#import "other.typ": pagebreak\n',)
            for prefix in prefixes:
                with self.subTest(prefix=prefix):
                    result = self.preview(prefix + call + b'\n#paragraph-tab\nNext.')
                    self.assertTrue(result is None or not result['edits'], result)

    def test_suppressed_and_nonoverlapping_conservative_candidates(self):
        source = (b'// typst-lint: ignore-next pde.paragraph-marker-gap -- intended\n'
                  b'#block[#pagebreak(weak: true)\n#paragraph-tab\nIgnored.]\n'
                  b'#block[#flowbox()[Text]\n#paragraph-tab\nNext.]\n'
                  b'#block[First.\n#paragraph-tab\nSecond.]')
        result, candidate = self.candidate(source)
        self.assertEqual(len(result['edits']), 1)
        self.assertEqual(result['edits'][0]['preceding_kind'], 'flowbox')
        old = conservative(parse(source), 'main.typ')
        self.assertEqual(len(old['edits']), 1)
        self.assertNotEqual(old['edits'][0]['start'], result['edits'][0]['start'])
        self.assertIn(b'#pagebreak(weak: true)\n#paragraph-tab', candidate)

    def test_parse_failure_withholds(self):
        source = b'#block[#flowbox()[Text]\n#paragraph-tab\nNext.]'
        with patch.object(gap, 'parse', side_effect=ValueError('parser failed')):
            result = self.preview(source)
        self.assertEqual(result['verification'], 'withheld')
        self.assertFalse(result['edits'])

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1 and Typst')
    def test_owned_fixtures_compile_and_preserve_sources(self):
        for call in (b'#pagebreak(weak: true)', b'#flowbox()[Text.]'):
            source = b'#import "helpers.typ": paragraph-tab, flowbox\n' + call + b'\n#paragraph-tab\nNext.'
            _, candidate = self.candidate(source)
            with self.subTest(call=call), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                rendered = []
                for label, contents in (('before', source), ('after', candidate)):
                    target = root / label / 'main.typ'
                    target.parent.mkdir()
                    (target.parent / 'helpers.typ').write_bytes(
                        b'#let paragraph-tab = h(2em)\n#let flowbox(body) = block(body)')
                    target.write_bytes(contents)
                    output = target.parent / 'output.pdf'
                    run = subprocess.run(['typst', 'compile', '--root', str(target.parent),
                                          '--creation-timestamp', '0', str(target), str(output)],
                                         capture_output=True, timeout=30)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    rendered.append(output.read_bytes())
                self.assertEqual(rendered[0], rendered[1])  # These owned fixtures only.
                self.assertEqual((root / 'before/main.typ').read_bytes(), source)
