"""Named theme and highlight rules through the read-only CLI, not synthetic proofs."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import Edit, plan
from source_analysis import analyze, rule_ids
from syntax_client import parse
from typst_lint import report


class NewRuleIntegrationTests(unittest.TestCase):
    def test_real_cli_preserves_theme_findings_and_extracts_highlight_math(self):
        source = (b'#import "@local/math-blocks:0.2.0": light-theme, theme-from-text-fill\n'
                  b'#let theme = light-theme\n#set theme(fill: red)\n'
                  b'#let helper(body) = { let palette = theme-from-text-fill(); body }\n'
                  b'#highlight[The term $x^i$ matters.]\n')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / 'main.typ'
            target.write_bytes(source)
            (root / '.typst-lint.toml').write_text('rule-set="pde"\n')
            (root / 'output.pdf').write_bytes(b'keep original')
            expected = {'core.parenthesized-scripts', 'core.no-set-theme',
                        'core.theme-in-context', 'pde.text-only-highlight'}
            for mode in ('check', 'fix', 'compile'):
                launcher = 'typst-style' if mode == 'compile' else 'typst-lint'
                command = [sys.executable, str(TOOL / launcher), mode, '--root', str(root),
                           '--package-path', str(REPO), '--format', 'json']
                if mode == 'compile': command += ['--', 'main.typ', 'output.pdf']
                process = subprocess.run(command, capture_output=True, text=True, timeout=30)
                self.assertEqual(process.returncode, 2, process.stderr)
                value = json.loads(process.stdout)
                self.assertEqual({d['rule_id'] for d in value['diagnostics']}, expected)
                self.assertEqual(value['coverage']['available_backends'], list(rule_ids('pde')))
                self.assertFalse(value['coverage']['complete'] or value['compiler_invoked'] or value['applied_fixes'])
                self.assertEqual(target.read_bytes(), source)
                self.assertEqual((root / 'output.pdf').read_bytes(), b'keep original')
            edits = [Edit(e['start'], e['end'], e['replacement'].encode()) for e in value['proposed_edits']]
            candidate = plan(source, edits).candidate()
            self.assertIn(b'#highlight[The term ]$x^(i)$#highlight[ matters.]', candidate)
            self.assertIn(b'#set theme(fill: red)', candidate)
            target.write_bytes(candidate)  # Only this temporary test fixture.
            final = report(root, 'check', package_path=REPO)
            self.assertFalse(final['proposed_edits'])
            self.assertEqual({d['rule_id'] for d in final['diagnostics']},
                             {'core.no-set-theme', 'core.theme-in-context'})

    def test_scoped_callbacks_through_real_loader_and_whole_candidate(self):
        source = (b'#import "@local/scoped-annotations:0.3.0": local-tag-scope, mannot-scope\n'
                  b'#local-tag-scope(s => [#s.tag("x") #ref((s.tag)("x"))], prefix: "p")\n'
                  b'#local-tag-scope(t => [], prefix: "p")\n'
                  b'#mannot-scope(s => [#(s.annot)("x", c, d)])\n')
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as package_directory:
            root = Path(directory).resolve()
            packages = Path(package_directory).resolve()
            fixture_root = TOOL / 'tests/fixtures/scoped-reference-packages'
            for base, relative in ((REPO, 'local/scoped-annotations/0.3.0/lib.typ'),
                                   (REPO, 'local/scoped-annotations/0.3.0/typst.toml'),
                                   (fixture_root, 'preview/mannot/0.4.0/src/lib.typ'),
                                   (fixture_root, 'preview/mannot/0.4.0/typst.toml')):
                path = packages / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((base / relative).read_bytes())
            target = root / 'main.typ'
            target.write_bytes(source)
            value = report(root, 'check', package_path=packages)
            self.assertEqual({d['rule_id'] for d in value['diagnostics']}, {
                'core.scoped-function-call', 'core.scoped-reference-access',
                'core.annotation-in-display', 'core.unique-scope-namespace'})
            edits = [Edit(e['start'], e['end'], e['replacement'].encode()) for e in value['proposed_edits']]
            candidate = plan(source, edits).candidate()
            self.assertEqual(candidate, source.replace(b'#s.tag("x")', b'#(s.tag)("x")')
                             .replace(b'#ref((s.tag)("x"))', b'#(s.ref)("x")'))
            self.assertEqual(target.read_bytes(), source)
            self.assertFalse(value['compiler_invoked'] or value['applied_fixes'] or value['coverage']['complete'])
            target.write_bytes(candidate)
            final = report(root, 'check', package_path=packages)
            self.assertFalse(final['proposed_edits'])
            self.assertEqual(len(final['diagnostics']), 2)

    def test_duplicate_namespace_does_not_cancel_earlier_script_repair(self):
        source = (b'#import "@local/scoped-annotations:0.3.0": local-tag-scope\n'
                  b'$x^i$\n'
                  b'#local-tag-scope(s => [], prefix: "p")\n'
                  b'#local-tag-scope(t => [], prefix: "p")\n')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'main.typ').write_bytes(source)
            value = report(root, 'check', package_path=REPO)
            self.assertEqual({d['rule_id'] for d in value['diagnostics']},
                             {'core.parenthesized-scripts', 'core.unique-scope-namespace'})
            duplicate = next(d for d in value['diagnostics'] if d['rule_id'] == 'core.unique-scope-namespace')
            self.assertIn(f'first declaration at byte {source.index(b"local-tag-scope(s")}', duplicate['explanation'])
            edits = [Edit(e['start'], e['end'], e['replacement'].encode()) for e in value['proposed_edits']]
            self.assertEqual(plan(source, edits).candidate(), source.replace(b'$x^i$', b'$x^(i)$'))
            self.assertEqual((root / 'main.typ').read_bytes(), source)

    def test_suppressed_namespace_survives_unrelated_script_repair(self):
        source = (b'#import "@local/scoped-annotations:0.3.0": local-tag-scope\n'
                  b'$x^i$\n#local-tag-scope(s => [], prefix: "p")\n'
                  b'// typst-lint: ignore-next core.unique-scope-namespace -- intentional\n'
                  b'#local-tag-scope(t => [], prefix: "p")\n')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'main.typ').write_bytes(source)
            value = report(root, 'check', package_path=REPO)
            self.assertEqual(value['suppressed_by_rule'].get('core.unique-scope-namespace'), 1)
            self.assertEqual({d['rule_id'] for d in value['diagnostics']}, {'core.parenthesized-scripts'})
            edits = [Edit(e['start'], e['end'], e['replacement'].encode()) for e in value['proposed_edits']]
            self.assertEqual(plan(source, edits).candidate(), source.replace(b'$x^i$', b'$x^(i)$'))

    def test_literal_catalog_name_with_byte_text_is_not_a_related_location(self):
        source = (b'#import "@local/project-references:0.1.0": project-ref\n'
                  b'$x^i$\n#project-ref("at byte 100", <energy>)\n')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'main.typ').write_bytes(source)
            (root / '.typst-lint.toml').write_text('catalog="catalog.json"\n')
            (root / 'catalog.json').write_text('{"schema":1,"projects":{}}')
            value = report(root, 'check', package_path=REPO)
            edits = [Edit(e['start'], e['end'], e['replacement'].encode()) for e in value['proposed_edits']]
            self.assertEqual(plan(source, edits).candidate(), source.replace(b'$x^i$', b'$x^(i)$'))
            finding = next(d for d in value['diagnostics'] if d['rule_id'] == 'core.project-ref-catalog-target')
            self.assertEqual(finding['explanation'], "Unknown catalog project 'at byte 100'.")

    def test_candidate_cannot_hide_findings_behind_new_analysis_refusals(self):
        original = parse(b'$x^i$\n')
        baseline = analyze(original, 'main.typ', _verify=False)
        candidate = parse(b'$x^(i)$\n')
        final = analyze(candidate, 'main.typ', _verify=False)
        rid = 'core.parenthesized-scripts'
        final[rid] = replace(final[rid], skips=(*final[rid].skips, 'New unsupported ownership at byte 0'))
        with patch('source_analysis._analyze', side_effect=[baseline, final]):
            result = analyze(original, 'main.typ')
        self.assertFalse(any(rule.edits for rule in result.values()))
        self.assertTrue(all(any('introduces an analysis refusal' in reason for reason in rule.skips)
                            for rule in result.values()))

    def test_core_pack_never_runs_pde_highlight_rule(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = b'#highlight[The term $x^(i)$ matters.]\n'
            (root / 'main.typ').write_bytes(source)
            value = report(root, 'check', package_path=REPO)
            self.assertNotIn('pde.text-only-highlight', value['coverage']['available_backends'])
            self.assertFalse(value['diagnostics'] or value['proposed_edits'])
            self.assertEqual((root / 'main.typ').read_bytes(), source)


if __name__ == '__main__':
    unittest.main()
