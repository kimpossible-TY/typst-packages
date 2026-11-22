"""Real CLI graph context, source selection, candidate composition and no-write gates."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import Edit, plan
from import_rules import ORDER, REDUNDANT
from source_analysis import rule_ids
from syntax_client import EXECUTABLE
from typst_lint import digest

STYLES = b'#import "Styles/styles.typ": *\n'
FIGURES = b'#import "figures.typ": *\n'
COMPAT = b'#import "Styles/local_tags.typ": *\n'


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class ImportIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='typst-import-cli-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.put('leaf.typ', b'#let tag = 1\n')
        self.put('Styles/styles.typ', b'#import "../leaf.typ": *\n')
        self.put('Styles/local_tags.typ', b'#import "../leaf.typ": *\n')
        self.put('figures.typ', b'#let diagram = 2\n')
        self.put('.typst-lint.toml', b'rule-set="pde"\ninclude=["main.typ"]\nexclude=["Styles/**"]\n')
        self.put('output.pdf', b'previous output')
        self.spy = self.put('bin/typst', b'#!/bin/sh\ntouch "' + str(self.root / 'compiler-invoked').encode() + b'"\nexit 99\n')
        self.spy.chmod(0o755)

    def put(self, path, source):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source)
        return target

    def cli(self, mode='check'):
        launcher = 'typst-style' if mode == 'compile' else 'typst-lint'
        command = [sys.executable, str(TOOL / launcher), 'fix' if mode == 'dry-run' else mode,
                   '--root', str(self.root), '--format', 'json']
        if mode == 'dry-run':
            command.append('--dry-run')
        if mode == 'compile':
            command.extend(['--', 'main.typ', 'output.pdf'])
        result = subprocess.run(command, capture_output=True, text=True, timeout=30,
                                env={**os.environ, 'PATH': str(self.spy.parent) + os.pathsep + os.environ.get('PATH', '')})
        self.assertEqual(result.returncode, 2, result.stderr)
        value = json.loads(result.stdout)
        self.assertFalse(value['compiler_invoked'])
        self.assertFalse((self.root / 'compiler-invoked').exists())
        return value

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob('*') if path.is_file() and not path.is_symlink()}

    def test_all_cli_routes_propose_both_exact_repairs_but_never_write_or_compile(self):
        source = FIGURES + STYLES + COMPAT + b'#tag + #diagram\n'
        expected = STYLES + FIGURES + b'#tag + #diagram\n'
        self.put('main.typ', source)
        self.put('excluded-unreferenced.typ', b'\xff')
        baseline = self.snapshot()
        for mode in ('check', 'dry-run', 'fix', 'compile'):
            with self.subTest(mode=mode):
                value = self.cli(mode)
                self.assertEqual({d['rule_id'] for d in value['diagnostics']}, {ORDER, REDUNDANT})
                self.assertEqual({e['rule_id'] for e in value['proposed_edits']}, {ORDER, REDUNDANT})
                self.assertEqual([item['path'] for item in value['inputs']], ['main.typ'])
                self.assertEqual({row['path'] for row in value['coverage']['files']}, {'main.typ'})
                self.assertFalse(value['applied_fixes'] or value['coverage']['complete'])
                self.assertEqual(value['proposal_status'], 'partial-not-applicable-transaction')
                self.assertEqual(value['coverage']['available_backends'], list(rule_ids('pde')))
                self.assertTrue({ORDER, REDUNDANT} <= set(rule_ids('pde')))
                for edit in value['proposed_edits']:
                    self.assertEqual(edit['baseline_sha256'], digest(source))
                edits = [Edit(edit['start'], edit['end'], edit['replacement'].encode()) for edit in value['proposed_edits']]
                self.assertEqual(plan(source, edits).candidate(), expected)
                self.assertEqual(self.snapshot(), baseline)
        self.put('main.typ', expected)  # The test harness alone applies a candidate.
        final = self.cli()
        self.assertFalse(final['diagnostics'] or final['proposed_edits'])
        self.assertEqual((self.root / 'main.typ').read_bytes(), expected)

    def test_graph_reanalysis_composes_with_an_independent_script_edit(self):
        source = FIGURES + STYLES + COMPAT + b'$x^i$\n'
        self.put('main.typ', source)
        value = self.cli('dry-run')
        self.assertEqual({e['rule_id'] for e in value['proposed_edits']},
                         {ORDER, REDUNDANT, 'core.parenthesized-scripts'})
        candidate = plan(source, [Edit(e['start'], e['end'], e['replacement'].encode())
                                  for e in value['proposed_edits']]).candidate()
        self.assertEqual(candidate, STYLES + FIGURES + b'$x^(i)$\n')
        self.put('main.typ', candidate)
        final = self.cli()
        self.assertFalse(final['proposed_edits'])
        self.assertFalse(any(d['rule_id'] in (ORDER, REDUNDANT, 'core.parenthesized-scripts')
                             for d in final['diagnostics']))

    def test_core_pack_does_not_expose_pde_import_rules(self):
        self.put('main.typ', FIGURES + STYLES + COMPAT)
        self.put('.typst-lint.toml', b'rule-set="core"\ninclude=["main.typ"]\n')
        value = self.cli()
        self.assertFalse(any(rule in value['coverage']['available_backends'] for rule in (ORDER, REDUNDANT)))
        self.assertFalse(any(d['rule_id'] in (ORDER, REDUNDANT) for d in value['diagnostics']))

    def test_config_ignore_suppresses_only_its_rule_and_does_not_apply_the_ignored_edit(self):
        source = FIGURES + STYLES + COMPAT
        self.put('main.typ', source)
        self.put('.typst-lint.toml', b'rule-set="pde"\ninclude=["main.typ"]\n'
                 b'[[ignore]]\npaths=["main.typ"]\nrules=["pde.import-order"]\nreason="reviewed order"\n')
        value = self.cli()
        self.assertEqual(value['suppressed_by_rule'].get(ORDER), 1)
        self.assertEqual({d['rule_id'] for d in value['diagnostics']}, {REDUNDANT})
        self.assertEqual({e['rule_id'] for e in value['proposed_edits']}, {REDUNDANT})
        self.assertEqual((self.root / 'main.typ').read_bytes(), source)

    def test_inline_suppression_preserves_import_ownership_and_no_edits(self):
        directive = b'// typst-lint: ignore-next pde.no-redundant-facade-import -- preserve compatibility\n'
        source = STYLES + directive + COMPAT
        self.put('main.typ', source)
        value = self.cli()
        self.assertFalse(value['diagnostics'] or value['proposed_edits'])
        self.assertEqual(value['suppressed_by_rule'].get(REDUNDANT), 1)
        self.assertEqual((self.root / 'main.typ').read_bytes(), source)

    def test_unknown_initializer_keeps_normative_facade_case_partial(self):
        self.put('Styles/styles.typ', b'#import "@local/scoped-annotations:0.3.0": *\n#show: identity\n')
        self.put('main.typ', FIGURES + STYLES + COMPAT)
        value = self.cli()
        self.assertFalse(any(d['rule_id'] in (ORDER, REDUNDANT) for d in value['diagnostics']))
        self.assertFalse(value['proposed_edits'])
        rows = [row for row in value['coverage']['files'] if row['rule_id'] in (ORDER, REDUNDANT)]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['status'] == 'partial' and row['reasons'] for row in rows))

    def test_symlink_dependency_refuses_safely_without_any_source_or_output_write(self):
        self.put('main.typ', FIGURES + STYLES)
        (self.root / 'leaf.typ').unlink()
        (self.root / 'leaf.typ').symlink_to(self.root / 'figures.typ')
        baseline = self.snapshot()
        value = self.cli('fix')
        self.assertIn('symbolic link', json.dumps(value))
        self.assertFalse(value.get('proposed_edits'))
        self.assertEqual(self.snapshot(), baseline)


if __name__ == '__main__':
    unittest.main()
