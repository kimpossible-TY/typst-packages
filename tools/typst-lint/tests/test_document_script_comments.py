"""Document-only math-script grouping around retained block comments."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import document_sequence_preview
from edit_plan import plan
from script_rule import analyze
from syntax_client import EXECUTABLE, parse
from typst_lint import load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class DocumentScriptCommentTests(unittest.TestCase):
    def test_block_comments_stay_in_place_and_default_profile_stays_partial(self):
        cases = (
            (b'$x^/* preserve */i$', b'$x^/* preserve */(i)$'),
            (b'$x^ /* preserve */ #v$', b'$x^ /* preserve */ (#v)$'),
            (b'$x_/* sub */j^/* sup */i$', b'$x_/* sub */(j)^/* sup */(i)$'),
            (b'$x^/* multi\nline */i$', b'$x^/* multi\nline */(i)$'),
            (b'$x^/* note */#f(2)$', b'$x^/* note */(#f(2))$'),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                tree = parse(source)
                self.assertFalse(tree.nodes[0].errors)
                ordinary = analyze(tree, 'main.typ')
                self.assertTrue(ordinary.skips)
                self.assertFalse(ordinary.edits)
                result = analyze(tree, 'main.typ', _allow_code_call=True)
                self.assertFalse(result.skips)
                candidate = plan(source, result.edits).candidate()
                self.assertEqual(candidate, expected)
                after = analyze(parse(candidate), 'main.typ', _allow_code_call=True)
                self.assertFalse(after.skips or after.edits or after.diagnostics)

    def test_document_sequence_preserves_comments_and_hash_chain(self):
        source = b'$x^/* a */i + y_/* b */#v$\n'
        tree = parse(source)
        catalog = load_rules()[0]['rules']
        record = document_sequence_preview.preview(tree, 'main.typ', catalog)
        self.assertEqual(record['verification'], 'hash-chained-parse-and-three-rule-reanalysis')
        candidate = document_sequence_preview.replay(tree, 'main.typ', catalog, record)
        self.assertEqual(candidate, b'$x^/* a */(i) + y_/* b */(#v)$\n')
        self.assertIsNone(document_sequence_preview.preview(parse(candidate), 'main.typ', catalog))

    def test_suppression_still_owns_the_commented_operand(self):
        source = (b'// typst-lint: ignore-next core.parenthesized-scripts -- intentional\n'
                  b'$x^/* keep */i$\n')
        result = analyze(parse(source), 'main.typ', _allow_code_call=True)
        self.assertEqual(len(result.suppressed), 1)
        self.assertFalse(result.diagnostics or result.edits or result.skips)

    def test_line_comments_and_complex_code_remain_unsupported(self):
        for source in (b'$x^// comment\ni$', b'$x^// comment\n#value$',
                       b'$x^/* comment */#(value + 1)$',
                       b'$x^/* comment */#f().value$'):
            with self.subTest(source=source):
                result = analyze(parse(source), 'main.typ', _allow_code_call=True)
                self.assertTrue(result.skips)
                self.assertFalse(result.edits)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1 and Typst')
    def test_owned_fixture_renders_identically(self):
        source = (b'#let v = [value]\n#let f(x) = x\n'
                  b'$x^/* preserve */i + y_/* sub */#v + z^/* line\nbreak */#f(2)$\n')
        result = analyze(parse(source), 'main.typ', _allow_code_call=True)
        self.assertEqual(len(result.diagnostics), 3)
        self.assertFalse(result.skips)
        candidate = plan(source, result.edits).candidate()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry, output = root / 'main.typ', root / 'out.pdf'
            (root / 'packages').mkdir()
            (root / 'cache').mkdir()
            def compile_source(data):
                entry.write_bytes(data)
                subprocess.run(['typst', 'compile', '--root', str(root),
                                '--package-path', str(root / 'packages'),
                                '--package-cache-path', str(root / 'cache'),
                                '--ignore-system-fonts', '--creation-timestamp', '1700000000',
                                str(entry), str(output)], check=True, capture_output=True, timeout=30)
                return output.read_bytes()
            self.assertEqual(compile_source(source), compile_source(candidate))
