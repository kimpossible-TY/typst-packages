"""Proven marker removal before structural siblings, never guessed names."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import plan
from package_loader import load_packages
from paragraph_rules import MARKER_TARGET as TARGET, MARKER_GAP as GAP, analyze as paragraphs
from source_analysis import analyze
from static_resolver import imported_paths, resolve
from syntax_client import EXECUTABLE, parse

IMPORT = b'#import "@local/text-utils:0.1.2": paragraph_tab\n'
MARKER = b'#paragraph_tab'


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter explicitly first')
class ParagraphTargetTests(unittest.TestCase):
    def check(self, source, *, packages=True, ignored=frozenset()):
        tree = parse(source)
        evidence = load_packages((TOOL / 'tests/fixtures/reviewed-packages'), imported_paths(tree)).sources if packages else ()
        results = analyze(tree, 'main.typ', pack='pde', packages=evidence, ignored=ignored)
        candidate = plan(source, [e for r in results.values() for e in r.edits]).candidate()
        return results, candidate

    def test_heading_display_and_consecutive_displays(self):
        for body in (b'#paragraph_tab\n= Heading\n',
                     b'#paragraph_tab\n$ x=y $\n',
                     b'$ x=y $\n#paragraph_tab\n$ y=z $ <stable>\n'):
            with self.subTest(body=body):
                source = IMPORT + body
                result, candidate = self.check(source)
                self.assertEqual(len(result[TARGET].diagnostics), 1)
                self.assertEqual(candidate, source.replace(MARKER, b''))
                self.assertFalse(result[GAP].diagnostics, 'Do not also repair a marker that should be removed')
                second, unchanged = self.check(candidate)
                self.assertFalse(second[TARGET].diagnostics or second[TARGET].edits)
                self.assertEqual(candidate, unchanged)

    def test_comments_blank_lines_and_crlf_preserved(self):
        source = b'\xef\xbb\xbf' + IMPORT.replace(b'\n', b'\r\n')
        source += '이전 문장.\r\n  #paragraph_tab\t\r\n/* keep */\r\n\r\n// keep\r\n= 제목\r\n'.encode()
        result, candidate = self.check(source)
        self.assertEqual(candidate, source.replace(MARKER, b''))
        self.assertEqual((result[TARGET].diagnostics[0].line, result[TARGET].diagnostics[0].column), (3, 3))
        self.assertNotIn(b'\n', candidate.replace(b'\r\n', b''))

    def test_prose_inline_math_and_other_constructs_unchanged(self):
        for next_text in (b'Therefore it holds.', b'$x=y$', b'- List item', b'#box[= Heading]',
                          b'`$ x=y $`', b'@target', b'#unknown()\n= Heading', b''):
            with self.subTest(next_text=next_text):
                source = IMPORT + b'\n#paragraph_tab\n' + next_text
                result, candidate = self.check(source)
                self.assertFalse(result[TARGET].diagnostics or result[TARGET].edits)
                self.assertEqual(candidate, source)

    def test_resolved_marker_and_root_ownership_are_required(self):
        for prefix, body, packages in (
                (IMPORT, b'\n#paragraph_tab\n= Heading', False),
                (IMPORT + b'#let paragraph_tab = []\n', b'\n#paragraph_tab\n= Heading', True),
                (b'#import "unreviewed.typ": paragraph_tab\n', b'\n#paragraph_tab\n= Heading', True),
                (IMPORT, b'\n#block[\n#paragraph_tab\n= Heading\n]', True),
                (IMPORT, b'\n#show: unknown\n#paragraph_tab\n= Heading', True),
                (IMPORT, b'\n#paragraph_tab inline\n= Heading', True)):
            with self.subTest(prefix=prefix, body=body):
                source = prefix + body
                result, candidate = self.check(source, packages=packages)
                self.assertFalse(result[TARGET].diagnostics or result[TARGET].edits)
                self.assertTrue(result[TARGET].skips)
                self.assertEqual(source, candidate)

    def test_suppression_and_config_ignore(self):
        directive = b'// typst-lint: ignore-next pde.paragraph-marker-target -- intentional\n'
        source = IMPORT + directive + b'#paragraph_tab\n= First\n\n#paragraph_tab\n= Second\n'
        result, candidate = self.check(source)
        self.assertEqual(len(result[TARGET].suppressed), 1)
        self.assertEqual(len(result[TARGET].diagnostics), 1)
        self.assertIn(directive + b'#paragraph_tab\n= First', candidate)
        self.assertTrue(candidate.endswith(b'\n\n= Second\n'))
        result, candidate = self.check(source, ignored=frozenset((TARGET,)))
        self.assertEqual(candidate, source)
        self.assertFalse(result[TARGET].edits)

    def test_unrelated_directive_does_not_acquire_heading_ownership(self):
        source = IMPORT + b'// typst-lint: ignore-next core.parenthesized-scripts -- intentional\n#paragraph_tab\n= Heading\n'
        result, candidate = self.check(source)
        self.assertTrue(result[TARGET].diagnostics)
        self.assertFalse(result[TARGET].edits)
        self.assertTrue(any('reassign' in s for s in result[TARGET].skips))
        self.assertEqual(source, candidate)
        # A directive already between marker and heading keeps its target.
        source = IMPORT + b'\n#paragraph_tab\n// typst-lint: ignore-next core.parenthesized-scripts -- intentional\n= Heading $x^i$\n'
        result, candidate = self.check(source)
        self.assertTrue(result[TARGET].edits)
        self.assertEqual(candidate, source.replace(MARKER, b''))

    def test_script_composition_and_semantic_label_remain_intact(self):
        source = IMPORT + b'\n#paragraph_tab\n$ x^i $ <stable>\n'
        result, candidate = self.check(source)
        self.assertTrue(result[TARGET].edits)
        self.assertEqual(candidate, source.replace(MARKER, b'').replace(b'x^i', b'x^(i)'))
        self.assertFalse(parse(candidate).nodes[0].errors)

    def test_stale_resolution_and_parse_errors_fail_closed(self):
        source = IMPORT + b'\n#paragraph_tab\n= Heading\n'
        tree = parse(source)
        resolution = resolve(tree, packages=load_packages((TOOL / 'tests/fixtures/reviewed-packages'), imported_paths(tree)).sources)
        result = paragraphs(tree, 'main.typ', replace(resolution, source_sha256='wrong'))[TARGET]
        self.assertTrue(result.skips)
        self.assertFalse(result.diagnostics or result.edits)
        result, candidate = self.check(source + b'#let =')
        self.assertTrue(result[TARGET].parse_errors)
        self.assertFalse(result[TARGET].edits)

    def test_real_cli_reports_proposal_without_writing_or_compiling(self):
        source = IMPORT + b'\n#paragraph_tab\n= Heading\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'main.typ').write_bytes(source)
            (root / '.typst-lint.toml').write_text('rule-set="pde"\n')
            (root / 'output.pdf').write_bytes(b'keep')
            for launcher, arguments in (('typst-lint', ['fix']), ('typst-style', ['compile'])):
                command = [sys.executable, str(TOOL / launcher), *arguments, '--root', str(root),
                           '--package-path', str((TOOL / 'tests/fixtures/reviewed-packages')), '--format', 'json']
                if launcher == 'typst-style': command += ['--', 'main.typ', 'output.pdf']
                p = subprocess.run(command, capture_output=True, text=True, timeout=30)
                self.assertEqual(p.returncode, 2, p.stderr)
                report = json.loads(p.stdout)
                self.assertFalse(report['compiler_invoked'] or report['applied_fixes'])
                self.assertFalse(report['coverage']['complete'])
                self.assertEqual([e['rule_id'] for e in report['proposed_edits']], [TARGET])
                self.assertEqual((root / 'main.typ').read_bytes(), source)
                self.assertEqual((root / 'output.pdf').read_bytes(), b'keep')


if __name__ == '__main__':
    unittest.main()
