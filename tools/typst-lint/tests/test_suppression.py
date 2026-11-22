import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import plan
from script_rule import analyze
from suppression import SuppressionError, resolve
from syntax_client import EXECUTABLE, parse
from typst_lint import load_rules, report

DIRECTIVE = b'// typst-lint: ignore-next core.parenthesized-scripts -- intentional example'


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter explicitly first')
class SuppressionTests(unittest.TestCase):
    def result(self, source):
        return analyze(parse(source), 'fixture.typ')

    def verify(self, source, expected, active, suppressed):
        result = self.result(source)
        self.assertEqual(result.skips, ())
        self.assertEqual(len(result.diagnostics), active)
        self.assertEqual(len(result.suppressed), suppressed)
        candidate = plan(source, result.edits).candidate()
        self.assertEqual(candidate, expected)
        final = self.result(candidate)
        self.assertEqual(final.edits, ())
        self.assertEqual(final.diagnostics, ())
        self.assertEqual(len(final.suppressed), suppressed)
        return result

    def test_whole_multiline_display_then_expiration(self):
        first = DIRECTIVE + b'\n$\n  x^i + y_j\n$\n'
        self.verify(first+b'$z^k$', first+b'$z^(k)$', 1, 2)

    def test_hash_and_multiline_call_are_one_construct(self):
        first = DIRECTIVE+b'\n#highlighted[\nText $x^i$.\n#box[$y_j$]\n]\n'
        self.verify(first+b'$z^k$', first+b'$z^(k)$', 1, 2)
        tree = parse(first)
        scope = resolve(tree, {r['id'] for r in load_rules()[0]['rules']})[0]
        self.assertEqual(tree.source[scope.start:scope.end], b'#highlighted[\nText $x^i$.\n#box[$y_j$]\n]')

    def test_nested_call_body_scope_does_not_escape(self):
        first = b'#box[\n' + DIRECTIVE + b'\n$x^i$\n$y^j$\n]\n$z^k$'
        expected = b'#box[\n' + DIRECTIVE + b'\n$x^i$\n$y^(j)$\n]\n$z^(k)$'
        self.verify(first, expected, 2, 1)

    def test_explicit_code_mode_targets_complete_statement(self):
        source = DIRECTIVE+b'\nlet first = $x^i$\nlet second = $y^j$'
        expected = DIRECTIVE+b'\nlet first = $x^i$\nlet second = $y^(j)$'
        result = analyze(parse(source, 'code'), 'snippet.typ')
        self.assertEqual(len(result.suppressed), 1)
        self.assertEqual(len(result.diagnostics), 1)
        self.assertEqual(plan(source, result.edits).candidate(), expected)
        self.assertFalse(analyze(parse(expected, 'code'), 'snippet.typ').edits)

    def test_multiple_ids_blank_lines_comments_and_indentation(self):
        prefix = b'\xef\xbb\xbf  // typst-lint: ignore-next pde.text-only-highlight, core.parenthesized-scripts -- compatibility\r\n\r\n// ordinary comment\r\n/* block comment */\r\n'
        self.verify(prefix+b'$x^i$\r\n$x_j$', prefix+b'$x^i$\r\n$x_(j)$', 1, 1)
        self.verify(b'// typst-lint: ignore-next pde.text-only-highlight -- other rule\n$x^i$',
                    b'// typst-lint: ignore-next pde.text-only-highlight -- other rule\n$x^(i)$',1,0)

    def test_duplicate_directives_do_not_double_count(self):
        source = DIRECTIVE+b'\n'+DIRECTIVE+b'\n$x^i$'
        self.verify(source, source, 0, 1)

    def test_invalid_directives_are_unsuppressible(self):
        for directive in (b'// typst-lint: ignore-next core.parenthesized-scripts --',
                          b'// typst-lint: ignore-next core.parenthesized-scripts --   ',
                          b'// typst-lint: ignore-next missing.rule -- reason',
                          b'// typst-lint: ignore-next core.parenthesized-scripts, -- reason',
                          b'// typst-lint: ignore-next * -- reason',
                          b'// typst-lint: ignore core.parenthesized-scripts -- reason',
                          b'// typst-lint: disable core.parenthesized-scripts -- reason',
                          b'// typst-lint: ignore-next core.parenthesized-scripts',
                          b'Trailing text '+DIRECTIVE):
            with self.subTest(directive=directive), self.assertRaises(SuppressionError):
                self.result(directive+b'\n$x^i$')
        for source in (DIRECTIVE, DIRECTIVE+b'\n// comment\n', b'#box[\n'+DIRECTIVE+b'\n]\n$x^i$'):
            with self.subTest(source=source), self.assertRaises(SuppressionError):
                self.result(source)

    def test_math_internal_ownership_explicitly_unsupported(self):
        source = b'$\n'+DIRECTIVE+b'\nx^i + y^j\n$'
        with self.assertRaisesRegex(SuppressionError,'unsupported construct ownership'):
            self.result(source)

    def test_strings_raw_block_comments_are_not_directives(self):
        prefix = b'#let s = "// typst-lint: ignore-next missing -- reason"\n`// typst-lint: ignore-next missing -- reason`\n/* typst-lint: ignore-next missing -- reason */\n'
        self.verify(prefix+b'$x^i$', prefix+b'$x^(i)$',1,0)
        prefix = DIRECTIVE+b'\n`$x^i$`\n'
        self.verify(prefix+b'$y^j$',prefix+b'$y^(j)$',1,0)
        # Escaped hash expression is a whole construct, not just its '#' token.
        prefix = DIRECTIVE+b'\n#box[$x^i$]\n'
        self.verify(prefix+b'$y^j$',prefix+b'$y^(j)$',1,1)

    def test_suppressed_nested_chain_does_not_corrupt_other_shared_boundaries(self):
        prefix = DIRECTIVE+b'\n$x^y^z$\n'
        self.verify(prefix+b'$a^b^c$',prefix+b'$a^(b^(c))$',2,2)

    def test_prose_construct_does_not_swallow_later_equation(self):
        prefix = DIRECTIVE+b'\nOrdinary prose before '
        self.verify(prefix+b'$x^i$',prefix+b'$x^(i)$',1,0)

    def test_cli_counts_ids_and_invalid_config_cannot_hide_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            source=root/'main.typ'
            source.write_bytes(DIRECTIVE+b'\n$x^i$\n$y^j$')
            result=report(root,'dry-run')
            self.assertEqual(result['suppressed_count'],1)
            self.assertEqual(result['suppressed_by_rule'],{'core.parenthesized-scripts':1})
            self.assertEqual(len(result['diagnostics']),1)
            self.assertTrue(result['proposed_edits'])
            self.assertFalse(result['configuration_failures'])
            (root/'.typst-lint.toml').write_text('[[ignore]]\npaths=["**/*.typ"]\nrules=["core.parenthesized-scripts"]\nreason="intentional"')
            source.write_bytes(b'// typst-lint: ignore-next missing -- reason\n$x^i$')
            before=source.read_bytes()
            process=subprocess.run([sys.executable,str(TOOL/'typst-lint'),'fix','--root',str(root),'--format','json'],capture_output=True,text=True)
            self.assertEqual(process.returncode,2,process.stderr)
            result=json.loads(process.stdout)
            self.assertEqual(len(result['configuration_failures']),1)
            self.assertFalse(result['proposed_edits'])
            self.assertFalse(result['applied_fixes'])
            self.assertFalse(result['compiler_invoked'])
            self.assertEqual(source.read_bytes(),before)


if __name__=='__main__':
    unittest.main()
