"""Document-only script proposals for syntax-embedded code calls."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import plan
from script_rule import analyze
from syntax_client import EXECUTABLE, parse


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class DocumentCodeScriptTests(unittest.TestCase):
    def test_code_call_is_grouped_with_hash_and_idempotent(self):
        for source, expected in (
            (b'$x^#footnote[Text]$', b'$x^(#footnote[Text])$'),
            (b'$x^ #f(2)$', b'$x^ (#f(2))$'),
            (b'$x_#f(2)$', b'$x_(#f(2))$'),
        ):
            with self.subTest(source=source):
                tree = parse(source)
                self.assertTrue(analyze(tree, 'x.typ').skips, 'Existing default stays conservative')
                result = analyze(tree, 'x.typ', _allow_code_call=True)
                self.assertEqual(len(result.diagnostics), 1)
                self.assertFalse(result.skips)
                candidate = plan(source, result.edits).candidate()
                self.assertEqual(candidate, expected)
                final = analyze(parse(candidate), 'x.typ', _allow_code_call=True)
                self.assertFalse(final.diagnostics or final.edits or final.skips)

    def test_comments_and_non_call_escapes_remain_unsupported(self):
        for source in (b'$x^/* comment */#f(2)$', b'$x^#value$', b'$x^/* comment */i$'):
            with self.subTest(source=source):
                result = analyze(parse(source), 'x.typ', _allow_code_call=True)
                self.assertTrue(result.skips)
                self.assertFalse(result.edits)

    def test_suppression_prevents_the_code_call_edit(self):
        source = (b'// typst-lint: ignore-next core.parenthesized-scripts -- intentional\n'
                  b'$x^#footnote[Text]$')
        result = analyze(parse(source), 'x.typ', _allow_code_call=True)
        self.assertEqual(len(result.suppressed), 1)
        self.assertFalse(result.edits or result.skips)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1 and Typst')
    def test_generated_footnote_candidate_renders_the_same_owned_fixture(self):
        source = b'The assumption follows: $"by assuming"^#footnote[Example.]$\n'
        result = analyze(parse(source), 'main.typ', _allow_code_call=True)
        candidate = plan(source, result.edits).candidate()
        self.assertEqual(candidate, b'The assumption follows: $"by assuming"^(#footnote[Example.])$\n')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = root / 'main.typ'
            output = root / 'output.pdf'
            (root / 'packages').mkdir()
            (root / 'cache').mkdir()
            def compile(data):
                entry.write_bytes(data)
                subprocess.run(['typst', 'compile', '--root', str(root),
                                '--package-path', str(root / 'packages'),
                                '--package-cache-path', str(root / 'cache'),
                                '--creation-timestamp', '1700000000', str(entry), str(output)],
                               check=True, capture_output=True, timeout=30)
                return output.read_bytes()
            self.assertEqual(compile(source), compile(candidate))
