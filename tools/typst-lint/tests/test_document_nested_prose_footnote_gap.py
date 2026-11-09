"""Independent source-only previews for prose footnotes before existing markers."""
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
import document_nested_prose_footnote_gap as gap
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE, parse
from typst_lint import digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class NestedProseFootnoteGapTests(unittest.TestCase):
    def preview(self, source):
        return gap.preview(parse(source), 'main.typ')

    def candidate(self, source):
        result = self.preview(source)
        self.assertIsNotNone(result)
        self.assertEqual(result['verification'],
                         'conditional-prose-footnote-gap-syntax-directives-and-reanalysis', result)
        self.assertFalse(result['runtime_binding_proved'] or result['render_equivalence_proved']
                         or result['edit_permission'] or result['compiler_permission'])
        self.assertTrue(result['conditions'])
        candidate = plan(source, [Edit(e['start'], e['end'], e['replacement'].encode())
                                  for e in result['edits']]).candidate()
        self.assertEqual(result['baseline_sha256'], digest(source))
        self.assertEqual(result['candidate_sha256'], digest(candidate))
        self.assertIsNone(self.preview(candidate))
        return result, candidate

    def test_existing_marker_preserves_lf_crlf_and_complex_footnote_body(self):
        for newline in (b'\n', b'\r\n'):
            source = (b'#block[Text.#footnote[#emph[Reason.]]' + newline + newline
                      + b'#paragraph_tab' + newline + b'Next.]')
            expected = source.replace(newline + newline + b'#paragraph_tab',
                                      newline + b'#parbreak()' + newline + newline + b'#paragraph_tab')
            with self.subTest(newline=newline):
                result, candidate = self.candidate(source)
                self.assertEqual(len(result['edits']), 1)
                self.assertEqual(candidate, expected)

    def test_multiple_candidates_and_unchanged_suppression(self):
        source = (b'// typst-lint: ignore-next pde.footnote-trailing-gap -- intentional\n'
                  b'#block[Ignored.#footnote[X]\n\n#paragraph_tab\nText.]\n'
                  b'#block[First.#footnote[A]\n\n#paragraph_tab\nText.\n'
                  b'Second.#footnote[B]\n\n#paragraph_tab\nNext.]')
        result, candidate = self.candidate(source)
        self.assertEqual(len(result['edits']), 2)
        self.assertIn(b'Ignored.#footnote[X]\n\n#paragraph_tab', candidate)
        self.assertEqual(candidate.count(b'#parbreak()'), 2)

    def test_missing_marker_comments_math_and_ambiguous_target_are_not_changed(self):
        cases = (
            b'#block[Text.#footnote[X]\n\nNext.]',
            b'#block[Text.#footnote[X]\n\n// attached\n#paragraph_tab\nNext.]',
            b'#block[Text.#footnote[X]\n\n#paragraph_tab\n= Heading]',
            b'#block[Text.#footnote[X]\n\n#paragraph_tab\n$ x $]',
            b'#block[$\n x #footnote[X]\n\n#paragraph_tab\n y\n$]',
            b'#block[Text.#footnote[X]\n\n#paragraph_tab\r\r\nNext.]',
        )
        for source in cases:
            with self.subTest(source=source):
                result = self.preview(source)
                self.assertTrue(result is None or not result['edits'], result)

    def test_shadowed_or_mutated_required_bindings_are_withheld(self):
        suffix = b'\n#block[Text.#footnote[X]\n\n#paragraph_tab\nNext.]'
        prefixes = (b'#let footnote = none', b'#let parbreak = none',
                    b'#let paragraph_tab = []', b'#let f(parbreak) = []',
                    b'#import "parbreak.typ"', b'#import "@local/parbreak:0.1.0"',
                    b'#import "other.typ": other as paragraph_tab',
                    b'#import "other.typ" as parbreak', b'#{parbreak = none}',
                    b'#import "other.typ" as tools: other as parbreak',
                    b'#import "other.typ" as tools: other as footnote',
                    b'#import "other.typ" as tools: parbreak',
                    b'#import "other.typ" as tools: footnote')
        for prefix in prefixes:
            with self.subTest(prefix=prefix):
                result = self.preview(prefix + suffix)
                self.assertEqual(result['verification'], 'withheld', result)
                self.assertFalse(result['edits'])

    def test_combined_unrelated_or_direct_marker_import_remains_conditional(self):
        suffix = b'\n#block[Text.#footnote[X]\n\n#paragraph_tab\nNext.]'
        for prefix in (b'#import "other.typ" as tools: helper',
                       b'#import "other.typ" as tools: paragraph_tab'):
            with self.subTest(prefix=prefix):
                result, candidate = self.candidate(prefix + suffix)
                self.assertEqual(len(result['edits']), 1)
                self.assertIn(b'#parbreak()', candidate)

    def test_malformed_or_reanalysis_failure_withholds(self):
        result = self.preview(b'#block[Text.#footnote[')
        self.assertEqual(result['verification'], 'withheld')
        source = b'#block[Text.#footnote[X]\n\n#paragraph_tab\nNext.]'
        with patch.object(gap, 'parse', side_effect=ValueError('fixture parser failure')):
            result = self.preview(source)
        self.assertEqual(result['verification'], 'withheld')
        self.assertFalse(result['edits'])
        self.assertIn('fixture parser failure', result['reason'])

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1 and Typst')
    def test_owned_fixture_compiles_both_versions_without_source_write(self):
        source = (b'#import "wrapper.typ": paragraph_tab\n'
                  b'#block[Text.#footnote[Reason.]\n\n#paragraph_tab\nNext.]')
        _, candidate = self.candidate(source)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'wrapper.typ').write_bytes(b'#let paragraph_tab = []')
            original, changed = root / 'original.typ', root / 'candidate.typ'
            original.write_bytes(source)
            changed.write_bytes(candidate)
            for target in (original, changed):
                run = subprocess.run(['typst', 'compile', '--root', str(root), str(target),
                                      str(target.with_suffix('.pdf'))], capture_output=True, timeout=30)
                self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(original.read_bytes(), source)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1 and Typst')
    def test_combined_parbreak_shadow_must_not_be_edited(self):
        source = (b'#import "wrapper.typ": paragraph_tab\n'
                  b'#import "other.typ" as tools: parbreak\n'
                  b'#block[Text.#footnote[Reason.]\n\n#paragraph_tab\nNext.]')
        result = self.preview(source)
        self.assertEqual(result['verification'], 'withheld', result)
        self.assertFalse(result['edits'])
        candidate = source.replace(b'\n\n#paragraph_tab', b'\n#parbreak()\n\n#paragraph_tab')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'wrapper.typ').write_bytes(b'#let paragraph_tab = []')
            (root / 'other.typ').write_bytes(b'#let parbreak = none')
            original, changed = root / 'original.typ', root / 'candidate.typ'
            original.write_bytes(source)
            changed.write_bytes(candidate)
            runs = [subprocess.run(['typst', 'compile', '--root', str(root), str(target),
                                    str(target.with_suffix('.pdf'))], capture_output=True, timeout=30)
                    for target in (original, changed)]
            self.assertEqual(runs[0].returncode, 0, runs[0].stderr)
            self.assertNotEqual(runs[1].returncode, 0)
            self.assertIn(b'expected function, found none', runs[1].stderr)
            self.assertEqual(original.read_bytes(), source)
