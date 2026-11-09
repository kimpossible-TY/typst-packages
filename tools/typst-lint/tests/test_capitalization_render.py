"""Optional isolated render test; no package installation or consumer edits.

Run with TYPST_LINT_RENDER_TESTS=1 and the normal unittest discovery command.
Requires existing typst and pdftotext executables. This is NOT parser coverage.
"""
from dataclasses import replace
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from capitalization import BodyKind, Context, MarkerBinding, StartKind, evaluate
from edit_plan import plan
from typst_lint import digest


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                     and shutil.which('typst') and shutil.which('pdftotext'),
                     'Opt in with TYPST_LINT_RENDER_TESTS=1; requires typst and pdftotext')
class CapitalizationRenderTests(unittest.TestCase):
    def test_explicit_uppercase_source_renders_with_package_indent(self):
        prefix = (b'#import "@local/text-utils:0.1.2": paragraph_tab, apply-paragraph-tabs\n'
                  b'#set page(width: 300pt, height: 200pt, margin: 20pt)\n'
                  b'#set text(size: 12pt, lang: "en")\n'
                  b'#show: apply-paragraph-tabs\nBaseline.\n\n')
        marker = b'#paragraph_tab\n'
        source = prefix + marker + b'this consequence follows.\n'
        start = len(prefix) + len(marker)
        ctx = Context(digest(source), 'fixture.typ', len(prefix), len(source),
                      len(prefix), start-1, start, start+4, b'this',
                      StartKind.LITERAL_WORD, BodyKind.ORDINARY_PROSE,
                      MarkerBinding.PARAGRAPH_TAB, 'en')
        result = evaluate(source, ctx)
        self.assertIsNotNone(result.edit)
        transformed = plan(source, [result.edit]).candidate()
        protected_source = prefix + marker + b'iPhone remains unchanged.\n'
        protected_ctx = replace(ctx, source_sha256=digest(protected_source),
                                body_end=len(protected_source), first_end=start+6,
                                expected_first=b'iPhone')
        protected_result = evaluate(protected_source, protected_ctx)
        self.assertIsNone(protected_result.edit)
        self.assertIsNotNone(protected_result.skip_reason)
        protected_candidate = plan(protected_source, []).candidate()
        self.assertEqual(protected_candidate, protected_source)
        self.assertEqual(transformed[:start], source[:start])
        self.assertEqual(transformed[start:], b'This consequence follows.\n')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_file, pdf, xml = root/'fixture.typ', root/'fixture.pdf', root/'fixture.xml'
            input_file.write_bytes(transformed)
            subprocess.run([shutil.which('typst'), 'compile', '--package-path', str((TOOL / 'tests/fixtures/reviewed-packages')),
                            str(input_file), str(pdf)], check=True, capture_output=True, timeout=60)
            subprocess.run([shutil.which('pdftotext'), '-bbox', str(pdf), str(xml)],
                           check=True, capture_output=True, timeout=30)
            words = [node for node in ET.parse(xml).iter() if node.tag.endswith('}word')]
            first = next(word for word in words if word.text == 'Baseline.')
            capitalized = next(word for word in words if word.text == 'This')
            self.assertFalse(any(word.text == 'this' for word in words))
            self.assertAlmostEqual(float(capitalized.attrib['xMin']) - float(first.attrib['xMin']),
                                   18.0, delta=0.05)  # 1.5em * 12pt.
            input_file.write_bytes(protected_candidate)
            subprocess.run([shutil.which('typst'), 'compile', '--package-path', str((TOOL / 'tests/fixtures/reviewed-packages')),
                            str(input_file), str(pdf)], check=True, capture_output=True, timeout=60)
            subprocess.run([shutil.which('pdftotext'), '-bbox', str(pdf), str(xml)],
                           check=True, capture_output=True, timeout=30)
            protected_words = [node.text for node in ET.parse(xml).iter() if node.tag.endswith('}word')]
            self.assertIn('iPhone', protected_words)
            self.assertNotIn('IPhone', protected_words)
            self.assertEqual(input_file.read_bytes(), protected_source)


if __name__ == '__main__':
    unittest.main()
