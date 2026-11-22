"""Independent source-only footnote breaks before marker and figure forms."""
from pathlib import Path
from dataclasses import replace
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import document_prose_footnote_extra_gaps as gaps
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE, parse
from typst_lint import digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ProseFootnoteExtraGapsTests(unittest.TestCase):
    def preview(self, source):
        return gaps.preview(parse(source), 'main.typ')

    def candidate(self, source):
        result = self.preview(source)
        self.assertIsNotNone(result)
        self.assertEqual(result['verification'], 'conditional-prose-extra-gap-syntax-directives-and-reanalysis', result)
        self.assertFalse(result['runtime_binding_proved'] or result['execution_proved']
                         or result['render_equivalence_proved'] or result['edit_permission']
                         or result['compiler_permission'])
        edits = [Edit(row['start'], row['end'], row['replacement'].encode()) for row in result['edits']]
        candidate = plan(source, edits).candidate()
        self.assertEqual(result['baseline_sha256'], digest(source))
        self.assertEqual(result['candidate_sha256'], digest(candidate))
        self.assertIsNone(self.preview(candidate))
        return result, candidate

    def test_hyphen_marker_and_figure_are_independent_conditions(self):
        for newline in (b'\n', b'\r\n'):
            cases = ((b'#paragraph-tab' + newline + b'Next.', 'hyphenated-paragraph-marker'),
                     (b'#figure([Illustration])', 'figure'))
            for following, kind in cases:
                source = '#block[한글.#footnote[Note.]'.encode() + newline * 2 + following + b']'
                with self.subTest(newline=newline, kind=kind):
                    result, candidate = self.candidate(source)
                    edit, = result['edits']
                    self.assertEqual(edit['following_kind'], kind)
                    self.assertTrue(edit['following_conditions'])
                    self.assertEqual(candidate, source.replace(newline * 2, newline + b'#parbreak()' + newline * 2))

    def test_existing_other_preview_forms_do_not_overlap(self):
        for after in (b'#paragraph_tab\nNext.', b'$ x $', b'Ordinary prose.', b'#unknown([x])'):
            source = b'#block[Text.#footnote[Note.]\n\n' + after + b']'
            with self.subTest(after=after):
                self.assertIsNone(self.preview(source))

    def test_suppression_preserves_original(self):
        source = (b'#block[Text.\n// typst-lint: ignore-next pde.footnote-trailing-gap -- intended\n'
                  b'#footnote[Note.]\n\n#paragraph-tab\nNext.]')
        result = self.preview(source)
        self.assertTrue(result is None or not result['edits'], result)

    def test_comments_and_marker_line_suffix_are_not_changed(self):
        for following in (b'// comment\n#paragraph-tab\nNext.', b'#paragraph-tab // comment\nNext.',
                          b'#paragraph-tab extra\nNext.', b'#figurex([x])'):
            with self.subTest(following=following):
                self.assertIsNone(self.preview(b'#block[Text.#footnote[Note.]\n\n' + following + b']'))

    def test_cross_rule_change_and_parser_failure_withhold(self):
        source = b'#block[Text.#footnote[Note.]\n\n#figure([x])]'
        original = gaps.continuation_rules.analyze_conditional
        def changed(tree, *args, **kwargs):
            result = original(tree, *args, **kwargs)
            if b'#parbreak()' in tree.source:
                from typst_lint import Diagnostic
                finding = Diagnostic('pde.continuation-line', 'warning', 'main.typ', 1, 1,
                                     'Fixture finding', 'fixture')
                return replace(result, diagnostics=(finding,))
            return result
        with patch.object(gaps.continuation_rules, 'analyze_conditional', side_effect=changed):
            result = self.preview(source)
        self.assertEqual(result['verification'], 'withheld')
        with patch.object(gaps, 'parse', side_effect=ValueError('fixture parse failure')):
            result = self.preview(source)
        self.assertEqual(result['verification'], 'withheld')
        self.assertFalse(result['edits'])

    def test_explicit_shadows_are_withheld(self):
        source = b'#block[Text.#footnote[Note.]\n\n#figure([x])]'
        for prefix in (b'#let figure = x => x\n', b'#import "x.typ": figure\n',
                       b'#import "x.typ" as mod: figure\n', b'#let parbreak = () => []\n',
                       b'#let paragraph-tab = []\n'):
            with self.subTest(prefix=prefix):
                result = self.preview(prefix + source)
                self.assertEqual(result['verification'], 'withheld', result)
                self.assertFalse(result['edits'])

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1 and Typst')
    def test_owned_fixtures_compile_and_record_render_behavior(self):
        for header, tail in ((b'#import "marker.typ": paragraph-tab\n', b'#paragraph-tab\nNext.'),
                             (b'', b'#figure([Illustration])')):
            source = header + b'#block[Text.#footnote[Note.]\n\n' + tail + b']'
            _, candidate = self.candidate(source)
            with self.subTest(tail=tail), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'marker.typ').write_bytes(b'#let paragraph-tab = h(1em)')
                outputs = []
                for name, contents in (('before', source), ('after', candidate)):
                    path, output = root / (name + '.typ'), root / (name + '.pdf')
                    path.write_bytes(contents)
                    run = subprocess.run(['typst', 'compile', '--root', str(root), '--creation-timestamp', '0',
                                          str(path), str(output)], capture_output=True, timeout=30)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    outputs.append(output.read_bytes())
                self.assertEqual((root / 'before.typ').read_bytes(), source)
                # Equality is limited to these owned fixtures, not a general claim.
                self.assertEqual(outputs[0], outputs[1])
