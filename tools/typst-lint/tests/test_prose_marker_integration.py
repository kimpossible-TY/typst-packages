"""Read-only real CLI and shared whole-candidate marker composition checks."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import Edit, plan
from package_loader import load_packages
from prose_marker_rules import RULE_ID
from source_analysis import analyze, rule_ids
from static_resolver import imported_paths
from syntax_client import EXECUTABLE, parse
from typst_lint import digest, report

PREFIX = (b'#import "@local/text-utils:0.1.2": paragraph_tab, apply-paragraph-tabs\n'
          b'#show: apply-paragraph-tabs\n#set text(lang: "en")\n')


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class ProseMarkerIntegrationTests(unittest.TestCase):
    def result(self, source, *, ignored=frozenset()):
        tree = parse(source)
        packages = load_packages(REPO, imported_paths(tree)).sources
        return analyze(tree, 'main.typ', pack='pde', packages=packages, ignored=ignored)

    def test_shared_script_composition_preserves_prose_indent_and_is_idempotent(self):
        source = PREFIX + b'$x^i$\n\nFirst idea.\n\n \tThis consequence follows.\n'
        results = self.result(source)
        self.assertEqual({rid for rid, result in results.items() if result.edits},
                         {RULE_ID, 'core.parenthesized-scripts'})
        candidate = plan(source, [edit for result in results.values() for edit in result.edits]).candidate()
        self.assertEqual(candidate, source.replace(b'$x^i$', b'$x^(i)$')
                         .replace(b' \tThis', b'#paragraph_tab\n \tThis'))
        final = self.result(candidate)
        self.assertFalse(any(result.edits or result.diagnostics for result in final.values()))

    def test_diagnostic_only_initials_do_not_cancel_independent_script_repair(self):
        for word in (b'this', b'Second', b'iPhone'):
            source = PREFIX + b'$x^i$\n\nFirst idea.\n\n' + word + b' consequence follows.\n'
            results = self.result(source)
            self.assertEqual(len(results[RULE_ID].diagnostics), 1)
            self.assertFalse(results[RULE_ID].edits)
            self.assertTrue(results['core.parenthesized-scripts'].edits)
            self.assertTrue(any('fix_unavailable:' in skip for skip in results[RULE_ID].skips))

    def test_repairing_one_paragraph_retains_later_literal_paragraph_refusal(self):
        source = PREFIX + b'First idea.\n\nThis consequence.\n\nSecond idea.\n'
        results = self.result(source)
        self.assertEqual(len(results[RULE_ID].diagnostics), 2)
        self.assertEqual(len(results[RULE_ID].edits), 1, results[RULE_ID].skips)
        candidate = plan(source, results[RULE_ID].edits).candidate()
        self.assertEqual(candidate, source.replace(b'This consequence.', b'#paragraph_tab\nThis consequence.'))
        final = self.result(candidate)
        self.assertEqual(len(final[RULE_ID].diagnostics), 1)
        self.assertFalse(final[RULE_ID].edits)
        self.assertTrue(any('initial-word-outside-editorial-allowlist' in skip for skip in final[RULE_ID].skips))

    def test_display_and_footnote_rules_keep_their_distinct_boundaries(self):
        display = PREFIX + b'First idea.\n\n$ A = B $\n\nThis consequence follows.\n'
        results = self.result(display)
        self.assertFalse(results[RULE_ID].diagnostics or results[RULE_ID].edits)
        self.assertTrue(results['pde.display-continuation-gap'].edits)
        footnote = PREFIX + b'First idea.#footnote[Reason.]\n\nThis consequence follows.\n'
        results = self.result(footnote)
        self.assertFalse(results[RULE_ID].diagnostics or results[RULE_ID].edits)
        self.assertTrue(results['pde.footnote-trailing-gap'].diagnostics)
        self.assertFalse(results['pde.footnote-trailing-gap'].edits)

    def test_config_ignored_rule_has_no_marker_proposal(self):
        source = PREFIX + b'First idea.\n\nThis consequence follows.\n'
        results = self.result(source, ignored=frozenset({RULE_ID}))
        self.assertFalse(results[RULE_ID].edits)
        self.assertNotIn(RULE_ID, rule_ids('core'))
        self.assertIn(RULE_ID, rule_ids('pde'))

    def test_actual_cli_all_routes_are_read_only_and_do_not_compile(self):
        source = b'\xef\xbb\xbf' + PREFIX.replace(b'\n', b'\r\n') + b'First idea.\r\n\r\n\tThis consequence follows.\r\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            fixture = root / 'main.typ'
            fixture.write_bytes(source)
            config = b'rule-set="pde"\n'
            (root / '.typst-lint.toml').write_bytes(config)
            output = root / 'output.pdf'
            output.write_bytes(b'keep existing output')
            for mode in ('check', 'dry-run', 'fix', 'compile'):
                launcher = 'typst-style' if mode == 'compile' else 'typst-lint'
                command = [sys.executable, str(TOOL / launcher), 'fix' if mode == 'dry-run' else mode,
                           '--root', str(root), '--package-path', str(REPO), '--format', 'json']
                if mode == 'dry-run':
                    command.append('--dry-run')
                if mode == 'compile':
                    command += ['--', 'main.typ', 'output.pdf']
                process = subprocess.run(command, capture_output=True, text=True, timeout=30)
                self.assertEqual(process.returncode, 2, process.stderr)
                value = json.loads(process.stdout)
                self.assertEqual({finding['rule_id'] for finding in value['diagnostics']}, {RULE_ID})
                self.assertEqual({edit['rule_id'] for edit in value['proposed_edits']}, {RULE_ID})
                self.assertFalse(value['compiler_invoked'] or value['applied_fixes'] or value['coverage']['complete'])
                self.assertEqual(value['proposal_status'], 'partial-not-applicable-transaction')
                self.assertEqual(fixture.read_bytes(), source)
                self.assertEqual((root / '.typst-lint.toml').read_bytes(), config)
                self.assertEqual(output.read_bytes(), b'keep existing output')
                for edit in value['proposed_edits']:
                    self.assertEqual(edit['baseline_sha256'], digest(source))
            proposals = [Edit(edit['start'], edit['end'], edit['replacement'].encode())
                         for edit in value['proposed_edits']]
            candidate = plan(source, proposals).candidate()
            self.assertEqual(candidate, source.replace(b'\tThis', b'#paragraph_tab\r\n\tThis'))
            fixture.write_bytes(candidate)  # Test-owned fixture only.
            final = report(root, 'check', package_path=REPO)
            self.assertFalse(final['proposed_edits'])
            self.assertFalse(any(finding['rule_id'] == RULE_ID for finding in final['diagnostics']))


if __name__ == '__main__':
    unittest.main()
