"""Conditional casing proposals for the hyphenated paragraph marker."""
from dataclasses import replace
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import document_hyphen_marker_capitalization as capitalization
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE, parse
from typst_lint import digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class HyphenMarkerCapitalizationTests(unittest.TestCase):
    def preview(self, source):
        return capitalization.preview(parse(source), 'main.typ')

    def candidate(self, source):
        result = self.preview(source)
        self.assertIsNotNone(result)
        self.assertEqual(result['verification'], 'conditional-hyphen-capitalization-syntax-directives-and-reanalysis', result)
        self.assertFalse(result['runtime_binding_proved'] or result['execution_proved']
                         or result['render_equivalence_proved'] or result['edit_permission']
                         or result['compiler_permission'])
        edits = [Edit(row['start'], row['end'], row['replacement'].encode()) for row in result['edits']]
        candidate = plan(source, edits).candidate()
        self.assertEqual(result['baseline_sha256'], digest(source))
        self.assertEqual(result['candidate_sha256'], digest(candidate))
        self.assertIsNone(self.preview(candidate))
        return result, candidate

    def test_one_byte_changes_in_unicode_lf_and_crlf_source(self):
        for newline in (b'\n', b'\r\n'):
            source = '#block[한글.'.encode() + newline + b'#paragraph-tab' + newline + b'therefore x.]'
            result, candidate = self.candidate(source)
            self.assertEqual(len(result['edits']), 1)
            self.assertEqual(candidate, source.replace(b'therefore', b'Therefore'))

    def test_mixed_marker_spelling_and_reasoned_suppression(self):
        source = (b'#block[First.\n#paragraph_tab\ntherefore old.\n'
                  b'// typst-lint: ignore-next pde.paragraph-capitalization -- intentional\n'
                  b'#paragraph-tab\ntherefore ignored.\n#paragraph-tab\nhence changed.]')
        result, candidate = self.candidate(source)
        self.assertEqual(len(result['edits']), 1)
        self.assertIn(b'therefore old.', candidate)
        self.assertIn(b'therefore ignored.', candidate)
        self.assertIn(b'Hence changed.', candidate)

    def test_unknown_starters_and_structural_content_are_not_skipped_into(self):
        for text in (b'x remains.', b'iPhone is a name.', b'$x$ is positive.',
                     b'customword is unknown.', '한글 문장.'.encode(), b'Therefore complete.'):
            with self.subTest(text=text):
                result = self.preview(b'#block[First.\n#paragraph-tab\n' + text + b']')
                self.assertTrue(result is None or not result['edits'], result)

    def test_explicit_shadow_or_failed_reanalysis_withholds(self):
        source = b'#block[First.\n#paragraph-tab\ntherefore x.]'
        result = self.preview(b'#let paragraph-tab = []\n' + source)
        self.assertEqual(result['verification'], 'withheld')
        original = capitalization.continuation_rules.analyze_conditional
        def changed(tree, *args, **kwargs):
            result = original(tree, *args, **kwargs)
            if b'Therefore' in tree.source:
                from typst_lint import Diagnostic
                item = Diagnostic('pde.continuation-line', 'warning', 'main.typ', 1, 1, 'fixture', 'fixture')
                return replace(result, diagnostics=(item,))
            return result
        with patch.object(capitalization.continuation_rules, 'analyze_conditional', side_effect=changed):
            result = self.preview(source)
        self.assertEqual(result['verification'], 'withheld')
        self.assertFalse(result['edits'])

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with the pinned Typst compiler')
    def test_owned_fixture_render_changes_and_original_is_untouched(self):
        source = b'#import "marker.typ": paragraph-tab\n#block[First.\n#paragraph-tab\ntherefore x.]'
        _, candidate = self.candidate(source)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'marker.typ').write_bytes(b'#let paragraph-tab = h(1em)')
            pdfs = []
            for name, contents in (('before', source), ('after', candidate)):
                path, output = root / (name + '.typ'), root / (name + '.pdf')
                path.write_bytes(contents)
                run = subprocess.run(['typst', 'compile', '--root', str(root), '--creation-timestamp', '0',
                                      str(path), str(output)], capture_output=True, timeout=30)
                self.assertEqual(run.returncode, 0, run.stderr)
                pdfs.append(output.read_bytes())
            self.assertNotEqual(pdfs[0], pdfs[1])
            self.assertEqual((root / 'before.typ').read_bytes(), source)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with the pinned Typst compiler')
    def test_owned_uppercase_show_can_make_pdf_equal(self):
        source = (b'#import "marker.typ": paragraph-tab\n'
                  b'#show regex("(?i)therefore"): it => [Therefore]\n'
                  b'#block[First.\n#paragraph-tab\ntherefore x.]')
        _, candidate = self.candidate(source)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'marker.typ').write_bytes(b'#let paragraph-tab = h(1em)')
            pdfs = []
            for name, contents in (('before', source), ('after', candidate)):
                path, output = root / (name + '.typ'), root / (name + '.pdf')
                path.write_bytes(contents)
                run = subprocess.run(['typst', 'compile', '--root', str(root), '--creation-timestamp', '0',
                                      str(path), str(output)], capture_output=True, timeout=30)
                self.assertEqual(run.returncode, 0, run.stderr)
                pdfs.append(output.read_bytes())
            self.assertEqual(pdfs[0], pdfs[1])
            self.assertEqual((root / 'before.typ').read_bytes(), source)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with the pinned Typst compiler')
    def test_reviewed_current_paragraph_show_fixture_can_differ(self):
        if not subprocess.check_output(['typst', '--version'], text=True).startswith('typst 0.15.1 '):
            self.skipTest('Fixture comparison pins Typst 0.15.1')
        source = (b'#import "@local/text-utils:0.1.2": paragraph-tab, apply-paragraph-tabs\n'
                  b'#show: apply-paragraph-tabs\n#block[First.\n#paragraph-tab\ntherefore x.]')
        _, candidate = self.candidate(source)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'packages/local/text-utils/0.1.2'
            package.parent.mkdir(parents=True)
            shutil.copytree(Path(__file__).resolve().parent / 'fixtures/reviewed-text-utils-main/local/text-utils/0.1.2', package)
            (root / 'project').mkdir()
            (root / 'cache').mkdir()
            pdfs = []
            for name, contents in (('before', source), ('after', candidate)):
                path, output = root / 'project' / (name + '.typ'), root / 'project' / (name + '.pdf')
                path.write_bytes(contents)
                run = subprocess.run(['typst', 'compile', '--root', str(root / 'project'),
                                      '--package-path', str(root / 'packages'), '--package-cache-path', str(root / 'cache'),
                                      '--creation-timestamp', '0', str(path), str(output)], capture_output=True, timeout=30)
                self.assertEqual(run.returncode, 0, run.stderr)
                pdfs.append(output.read_bytes())
            self.assertNotEqual(pdfs[0], pdfs[1])
            self.assertEqual((root / 'project/before.typ').read_bytes(), source)
