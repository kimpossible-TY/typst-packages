"""Actual document CLI over isolated include graphs and source-only candidates."""
import json
import os
import shutil
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import document_cli
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class DocumentCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='typst-document-cli-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def write(self, path, source):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source)
        return target

    def run_cli(self, *options, entrypoint='main.typ', root=None, format='json', env=None):
        command = [sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
                   '--root', str(self.root if root is None else root),
                   '--entrypoint', entrypoint, '--format', format, *options]
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, env=env)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(result.stderr, result.stderr)
        return json.loads(result.stdout) if format == 'json' else result.stdout

    def script_coverage(self, result):
        return [row for row in result['coverage']['files']
                if row['rule_id'] == 'core.parenthesized-scripts']

    def test_source_local_rule_hints_cover_includes_without_granting_edits(self):
        self.write('main.typ', b'#book-part[\n#include "chapter.typ"\n]\n')
        source = (b'This statement holds.\nAnother statement follows.\n$cancel(x)$\n'
                  b'#block[This is the definition.\nIt follows.\n]\n')
        chapter = self.write('chapter.typ', source)
        result = self.run_cli('--rule-set', 'pde', '--local-rule-hints')
        hints = result['local_rule_hints']
        self.assertFalse(hints['edit_permission'])
        self.assertEqual(len(hints['rule_ids']), 13)
        found = {(row['path'], row['rule_id']) for row in hints['diagnostics']}
        self.assertIn(('chapter.typ', 'pde.continuation-line'), found)
        self.assertIn(('chapter.typ', 'pde.theme-cancelmark'), found)
        self.assertEqual(sum(row['rule_id'] == 'pde.continuation-line' and row['path'] == 'chapter.typ'
                             for row in hints['diagnostics']), 2)
        self.assertIn('pde.continuation-line', result['coverage']['uninspected'])
        self.assertFalse(result['applied_fixes'] or result['proposed_edits'])
        self.assertEqual(chapter.read_bytes(), source)
        self.assertTrue(all(row['status'] == 'conditional' and not row['edit_permission']
                            for row in hints['coverage']))

    def test_source_local_rule_hints_are_opt_in_and_preserve_suppressions(self):
        self.write('main.typ', b'// typst-lint: ignore-next pde.continuation-line -- intentional\n'
                               b'This statement holds.\nAnother statement follows.\n')
        ordinary = self.run_cli('--rule-set', 'pde')
        self.assertNotIn('local_rule_hints', ordinary)
        result = self.run_cli('--rule-set', 'pde', '--local-rule-hints')
        hints = result['local_rule_hints']
        self.assertTrue(any(row['rule_id'] == 'pde.continuation-line' for row in hints['suppressed']))
        self.assertFalse(any(row['rule_id'] == 'pde.continuation-line' for row in hints['diagnostics']))
        invalid = self.run_cli('--local-rule-hints')
        self.assertEqual(invalid['phase'], 'input')
        self.assertIn('require explicit --rule-set pde', invalid['error'])

    def test_nested_footnote_hints_are_conditional_and_do_not_apply_edits(self):
        source = b'#block[Claim#footnote[Text]\n\nNext.\n]\n'
        target = self.write('main.typ', source)
        report = self.run_cli('--rule-set', 'pde', '--local-rule-hints')
        hints = report['local_rule_hints']
        ids = {item['rule_id'] for item in hints['diagnostics']}
        self.assertIn('pde.prose-footnote-punctuation', ids)
        self.assertIn('pde.footnote-trailing-gap', ids)
        self.assertFalse(hints['edit_permission'] or report['applied_fixes'] or report['proposed_edits'])
        self.assertEqual(target.read_bytes(), source)

    def test_nested_proof_hints_remain_conditional_and_do_not_apply_edits(self):
        source = b'#block[\n#proof[Done.]\nNext.\n]\n'
        target = self.write('main.typ', source)
        report = self.run_cli('--rule-set', 'pde', '--local-rule-hints')
        hints = report['local_rule_hints']
        self.assertTrue(any(item['rule_id'] == 'pde.proof-trailing-gap' for item in hints['diagnostics']))
        self.assertFalse(hints['edit_permission'] or report['applied_fixes'] or report['proposed_edits'])
        self.assertEqual(target.read_bytes(), source)

    def test_prose_and_theme_hints_in_included_source_are_conditional(self):
        self.write('main.typ', b'#include "chapter.typ"\n')
        source = (b'First ordinary paragraph.\n\nSecond ordinary paragraph.\n\n'
                  b'#let reusable() = [#theme-from-text-fill()]\n')
        target = self.write('chapter.typ', source)
        report = self.run_cli('--rule-set', 'pde', '--local-rule-hints')
        hints = report['local_rule_hints']
        ids = {item['rule_id'] for item in hints['diagnostics'] if item['path'] == 'chapter.typ'}
        self.assertIn('pde.prose-paragraph-marker', ids)
        self.assertIn('core.theme-in-context', ids)
        self.assertFalse(hints['edit_permission'] or report['applied_fixes'] or report['proposed_edits'])
        self.assertEqual(target.read_bytes(), source)

    def test_conditional_gap_previews_are_opt_in_independent_and_read_only(self):
        self.write('main.typ', b'#include "chapter.typ"\n')
        source = (b'#block[\n#proof[Done.]\nNext.\n'
                  b'$\n A #footnote[Reason.]\n\n B\n$\n]')
        target = self.write('chapter.typ', source)
        ordinary = self.run_cli('--rule-set', 'pde')
        self.assertNotIn('conditional_gap_previews', ordinary)
        report = self.run_cli('--rule-set', 'pde', '--preview-conditional-gaps')
        previews = report['conditional_gap_previews']
        self.assertEqual(len(previews), 2)
        self.assertEqual({row['edits'][0]['rule_id'] for row in previews},
                         {'pde.proof-trailing-gap', 'pde.footnote-trailing-gap'})
        self.assertTrue(all(row['baseline_sha256'] == digest(source) and not row['edit_permission']
                            and not row['render_equivalence_proved'] for row in previews))
        self.assertIn('not-composed', report['conditional_gap_preview_status'])
        self.assertFalse(report['applied_fixes'])
        self.assertEqual(target.read_bytes(), source)
        refused = self.run_cli('--preview-conditional-gaps')
        self.assertEqual(refused['phase'], 'input')

    def test_explicit_reference_facade_provenance_is_conditional_and_reports_shadowing(self):
        main = self.write('main.typ', b'#import "Styles/styles.typ": *\n')
        facade = self.write('Styles/styles.typ', b'#import "@local/math-blocks:0.2.0": *\n')
        packages = self.namespace_packages()
        references = self.root / 'reference-packages'
        destination = references / 'local/project-references/0.1.0'
        destination.mkdir(parents=True)
        source = TOOL.parents[1] / 'local/project-references/0.1.0'
        for filename in ('typst.toml', 'lib.typ'):
            shutil.copyfile(source / filename, destination / filename)
        options = ('--rule-set', 'pde', '--package-path', str(packages),
                   '--reference-package-path', str(references), '--reference-facade', 'main.typ')
        result = self.run_cli(*options)
        proof = result['reference_provenance']
        self.assertEqual(proof['export']['status'], 'CONDITIONAL')
        self.assertFalse(proof['call_binding_proved'] or proof['runtime_selection_proved']
                         or proof['catalog_checked'] or proof['edit_permission'])
        self.assertEqual(proof['reference_package_evidence'][0]['import_path'], '@local/project-references:0.1.0')
        self.assertTrue(proof['export']['route'])
        self.assertEqual(main.read_bytes(), b'#import "Styles/styles.typ": *\n')
        cache = self.root / 'reference-cache'
        cache.mkdir()
        shutil.move(str(packages / 'preview'), str(cache / 'preview'))
        shutil.copytree(destination, packages / 'local/project-references/0.1.0')
        multi_options = ('--rule-set', 'pde', '--package-path', str(packages),
                         '--reference-package-path', str(packages), '--reference-facade', 'main.typ',
                         '--reference-cache-path', str(cache))
        multi = self.run_cli(*multi_options)['reference_provenance']
        self.assertEqual(multi['export']['status'], 'CONDITIONAL')
        self.assertEqual(multi['namespace_roots'], [str(packages), str(cache)])
        selections = {row['import_path']: row['root_index'] for row in multi['namespace_selections']}
        self.assertEqual(selections['@preview/mannot:0.4.0'], 1)
        self.assertFalse(multi['runtime_selection_proved'])
        facade.write_bytes(facade.read_bytes() + b'#let project-ref = none\n')
        refused = self.run_cli(*multi_options)['reference_provenance']
        self.assertEqual(refused['export']['status'], 'UNAVAILABLE')
        self.assertTrue(refused['export']['reason'])
        self.assertTrue(refused['reference_package_evidence'])

    def test_reference_facade_options_require_both_roots_and_facade(self):
        self.write('main.typ', b'Hello.\n')
        report = self.run_cli('--rule-set', 'pde', '--reference-facade', 'main.typ')
        self.assertEqual(report['phase'], 'input')
        self.assertIn('--reference-package-path', report['error'])

    def test_reference_calls_report_per_file_and_remain_conditional(self):
        source = (b'#import "Styles/styles.typ": *\n'
                  b'#project-ref("book", <x>)\n'
                  b'#let deferred() = [#project-ref("book", <y>)]\n')
        target = self.write('main.typ', source)
        self.write('Styles/styles.typ', b'#import "@local/math-blocks:0.2.0": *\n')
        packages = self.namespace_packages()
        references = self.root / 'reference-packages'
        shutil.copytree(TOOL / 'tests/fixtures/reviewed-packages/local/project-references',
                        references / 'local/project-references')
        report = self.run_cli('--rule-set', 'pde', '--package-path', str(packages),
                              '--reference-package-path', str(references),
                              '--reference-facade', 'Styles/styles.typ', '--reference-calls')
        provenance = report['reference_provenance']
        row = next(item for item in provenance['calls_by_file'] if item['path'] == 'main.typ')
        self.assertEqual(len(row['calls']), 1)
        self.assertEqual(row['status'], 'PARTIAL')
        self.assertTrue(row['unresolved'])
        self.assertFalse(row['edit_permission'] or provenance['call_binding_proved'])
        self.assertEqual(target.read_bytes(), source)

    def test_reference_calls_require_reference_options(self):
        self.write('main.typ', b'Hello.\n')
        report = self.run_cli('--rule-set', 'pde', '--reference-calls')
        self.assertEqual(report['phase'], 'input')
        self.assertIn('requires explicit reference provenance', report['error'])

    def namespace_packages(self):
        package_root = self.root / 'packages'
        roots = [('local', name, version, 'lib.typ', TOOL / 'tests/fixtures/reviewed-packages')
                 for name, version in (('text-utils', '0.1.2'), ('math-blocks', '0.2.0'),
                                       ('scoped-annotations', '0.3.0'), ('cetz-helpers', '0.2.0'))]
        roots.extend((('preview', 'mannot', '0.4.0', 'src/lib.typ',
                       TOOL / 'tests/fixtures/scoped-reference-packages'),
                      ('preview', 'cetz', '0.4.2', 'src/lib.typ',
                       TOOL / 'tests/fixtures/anchor-overlay-packages')))
        for namespace, name, version, entrypoint, source in roots:
            relative = Path(namespace) / name / version
            for filename in ('typst.toml', entrypoint):
                target = package_root / relative / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / relative / filename, target)
        return package_root

    def conditional_fixture(self):
        self.write('Styles/styles.typ', b'#import "@local/text-utils:0.1.2": *\n'
            b'#import "@local/math-blocks:0.2.0": *\n'
            b'#import "@local/scoped-annotations:0.3.0": *\n'
            b'#import "@local/cetz-helpers:0.2.0": *\n'
            b'#import "@preview/mannot:0.4.0": *\n'
            b'#let rmark = mark.with(color: red)\n#let dx = $upright(d) x$\n')
        self.write('figures.typ', b'#import "@preview/cetz:0.4.2": *\n'
            b'#import "@preview/fletcher:0.5.8": diagram, node, edge\n'
            b'#let wave(t) = (t,t)\n')
        return self.write('main.typ', b'#import "Styles/styles.typ": *\n'
            b'#import "figures.typ": *\n#import "@preview/mannot:0.4.0": *\n'
            b'#highlighted[Text and $x^2$ together.]\n')

    def test_nested_repeated_includes_and_opaque_wrappers_preserve_inventory(self):
        self.write('main.typ', b'#book-part[\n#include "chapter/chapter.typ"\n'
                               b'#include "chapter/chapter.typ"\n]\n')
        self.write('chapter/chapter.typ', b'#include "section.typ"\n')
        source = b'#import "missing-styles.typ": *\nWords $x^i$ and $y_(j)$.\n'
        self.write('chapter/section.typ', source)
        result = self.run_cli()
        context = result['include_context']
        self.assertEqual(context['budget'], 'book-v1')
        self.assertEqual(len(context['sources']), 3)
        self.assertEqual(len(context['edges']), 3)
        self.assertEqual(len(context['occurrences']), 5)
        self.assertTrue(context['unresolved'], 'Opaque containment must remain unresolved')
        occurrences = [row for row in context['occurrences']
                       if row['source']['path'] == 'chapter/section.typ']
        self.assertEqual(len(occurrences), 2)
        self.assertNotEqual(occurrences[0]['edge_chain'], occurrences[1]['edge_chain'])
        self.assertEqual(len(result['diagnostics']), 1, 'Check each source once, not each occurrence')
        finding, = result['diagnostics']
        self.assertEqual(finding['path'], 'chapter/section.typ')
        self.assertEqual(finding['line'], 2)
        self.assertEqual(len(result['proposed_edits']), 2)
        self.assertTrue(all(row['path'] == 'chapter/section.typ' for row in result['proposed_edits']))
        self.assertEqual(self.run_cli(), result, 'Inventory output must be deterministic')
        self.assertEqual((self.root / 'chapter/section.typ').read_bytes(), source)
        self.assertFalse(result['package_resolution_performed'])
        self.assertFalse(result['coverage']['global_complete'])

    def test_source_bound_candidate_can_be_replayed_and_is_idempotent(self):
        original = (b'\xef\xbb\xbf#import "unused-missing.typ": *\r\n'
                    + '한글 $x^i_j$ /* preserve */\r\n'.encode())
        target = self.write('main.typ', original)
        before = self.run_cli()
        edits = []
        for row in before['proposed_edits']:
            self.assertEqual(row['baseline_sha256'], digest(original))
            self.assertEqual(row['rule_id'], 'core.parenthesized-scripts')
            edits.append(Edit(row['start'], row['end'], row['replacement'].encode()))
        candidate = plan(original, edits).candidate()
        self.assertIn(b'$x^(i)_(j)$', candidate)
        self.assertIn(b'/* preserve */\r\n', candidate)
        file, = self.script_coverage(before)
        self.assertEqual(file['candidate_sha256'], digest(candidate))
        self.assertEqual(file['candidate_verification'], 'parse-and-rule-idempotence-checked')
        self.assertEqual(target.read_bytes(), original)
        target.write_bytes(candidate)  # This test owns its disposable fixture.
        after = self.run_cli()
        self.assertEqual(after['diagnostics'], [])
        self.assertEqual(after['proposed_edits'], [])
        self.assertFalse(after['coverage']['global_complete'])
        self.assertFalse(after['compiler_invoked'])

    def test_inline_suppression_is_honored_but_consumer_config_is_not_loaded(self):
        self.write('.typst-lint.toml', b'not even valid TOML or UTF-8: \xff')
        self.write('main.typ', b'// typst-lint: ignore-next core.parenthesized-scripts -- intentional\n'
                               b'$x^i$\n\n$y^j$\n')
        result = self.run_cli()
        self.assertFalse(result['consumer_config_loaded'])
        self.assertEqual(result['suppressed_count'], 1)
        self.assertEqual(len(result['diagnostics']), 1)
        self.assertEqual(result['diagnostics'][0]['line'], 4)
        self.assertEqual(len(result['proposed_edits']), 2)

    def test_missing_and_dynamic_include_requests_remain_explicit(self):
        self.write('main.typ', b'#include "absent.typ"\n#include selected\n$x^i$\n')
        result = self.run_cli()
        context = result['include_context']
        codes = {code for row in context['unresolved'] for code in row['codes']}
        self.assertIn('missing-source', codes)
        self.assertIn('dynamic-request', codes)
        self.assertEqual(len(context['sources']), 1)
        self.assertEqual(len(result['diagnostics']), 1)

    def test_symlink_entrypoint_and_dependency_are_refused(self):
        self.write('actual.typ', b'$x^i$\n')
        (self.root / 'main.typ').symlink_to('actual.typ')
        result = self.run_cli()
        self.assertEqual(result['phase'], 'input')
        self.assertIn('link', result['error'].lower())
        (self.root / 'main.typ').unlink()
        self.write('main.typ', b'#include "linked.typ"\n')
        (self.root / 'linked.typ').symlink_to('actual.typ')
        result = self.run_cli()
        self.assertEqual(result['phase'], 'input')
        self.assertIn('error', result)
        self.assertNotIn('include_context', result)

    def test_malformed_source_and_invalid_directive_refuse_without_partial_report(self):
        for source in (b'$x^(', b'// typst-lint: ignore-next core.parenthesized-scripts\n$x^i$\n'):
            with self.subTest(source=source):
                self.write('main.typ', source)
                result = self.run_cli()
                self.assertEqual(result['phase'], 'input')
                self.assertIn('error', result)
                self.assertNotIn('proposed_edits', result)
                self.assertFalse(result['compiler_invoked'])

    def test_invalid_directive_in_include_identifies_the_source(self):
        self.write('main.typ', b'#include "chapter/section.typ"\n')
        self.write('chapter/section.typ', b'// typst-lint: ignore-next core.parenthesized-scripts\n$x^i$\n')
        result = self.run_cli()
        self.assertEqual(result['phase'], 'input')
        self.assertIn('chapter/section.typ:', result['error'])
        self.assertIn('directive', result['error'])
        self.assertNotIn('proposed_edits', result)

    def test_partial_script_source_keeps_findings_and_withholds_every_edit(self):
        self.write('main.typ', b'$x^#(value + 1)$ and $y^j$.\n')
        result = self.run_cli()
        self.assertEqual(len(result['diagnostics']), 1)
        self.assertEqual(result['proposed_edits'], [])
        file, = self.script_coverage(result)
        self.assertEqual(file['status'], 'partial')
        self.assertEqual(file['proposed_edits'], 0)
        self.assertIsNone(file['candidate_sha256'])
        self.assertEqual(file['candidate_verification'], 'no-candidate')
        self.assertTrue(any('withheld' in reason for reason in file['reasons']))

    def test_code_call_no_longer_withholds_other_script_proposals(self):
        source = b'$x^i + y^#footnote[Text]$\n'
        target = self.write('main.typ', source)
        result = self.run_cli()
        self.assertEqual(len(result['diagnostics']), 2)
        edits = tuple(Edit(e['start'], e['end'], e['replacement'].encode())
                      for e in result['proposed_edits'])
        self.assertEqual(plan(source, edits).candidate(), b'$x^(i) + y^(#footnote[Text])$\n')
        file, = self.script_coverage(result)
        self.assertEqual(file['status'], 'evaluated')
        self.assertEqual(file['candidate_verification_rules'], ['core.parenthesized-scripts'])
        self.assertEqual(target.read_bytes(), source)

    def test_candidate_losing_script_coverage_is_withheld_after_fresh_parse(self):
        source = b'$x^i$\n'
        target = self.write('main.typ', source)
        original_analyze = document_cli.analyze

        def lose_coverage(tree, path, **kwargs):
            result = original_analyze(tree, path, **kwargs)
            if tree.source != source:
                return replace(result, skips=('new unsupported candidate operand',))
            return result

        with patch.object(document_cli, 'analyze', side_effect=lose_coverage) as analyze:
            result = document_cli.report_document(str(self.root), 'main.typ')
        self.assertEqual(analyze.call_count, 2)
        self.assertEqual(len(result['diagnostics']), 1)
        self.assertEqual(result['proposed_edits'], [])
        file, = self.script_coverage(result)
        self.assertEqual(file['status'], 'partial')
        self.assertIsNone(file['candidate_sha256'])
        self.assertEqual(file['candidate_verification'], 'no-candidate')
        self.assertIn('Candidate coverage: new unsupported candidate operand', file['reasons'])
        self.assertEqual(target.read_bytes(), source)

    def test_paths_must_be_canonical_and_entrypoint_typst(self):
        self.write('main.typ', b'$x^i$\n')
        for path in ('../main.typ', '/main.typ', './main.typ', 'main.md', 'bad\n.typ'):
            with self.subTest(path=path):
                result = self.run_cli(entrypoint=path)
                self.assertEqual(result['phase'], 'input')
                self.assertIn('error', result)
        result = self.run_cli(root='.')
        self.assertEqual(result['phase'], 'input')

    def test_no_compiler_or_consumer_mutations_and_clean_source_stays_incomplete(self):
        source = b'Literal prose and $x^(i)$.\n'
        self.write('main.typ', source)
        self.write('existing.pdf', b'previous output')
        binary = self.write('bin/typst', b'#!/bin/sh\nprintf invoked > "$DOCUMENT_COMPILER_TRIPWIRE"\nexit 99\n')
        binary.chmod(0o755)
        tripwire = self.root / 'compiler-attempt'
        env = dict(os.environ, PATH=str(binary.parent) + os.pathsep + os.environ.get('PATH', ''),
                   DOCUMENT_COMPILER_TRIPWIRE=str(tripwire))
        baseline = {str(path.relative_to(self.root)): path.read_bytes()
                    for path in self.root.rglob('*') if path.is_file()}
        result = self.run_cli(env=env)
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(result['proposed_edits'], [])
        self.assertEqual(result['applied_fixes'], [])
        self.assertFalse(result['compiler_invoked'])
        self.assertFalse(result['coverage']['global_complete'])
        self.assertFalse(tripwire.exists())
        self.assertEqual({str(path.relative_to(self.root)): path.read_bytes()
                          for path in self.root.rglob('*') if path.is_file()}, baseline)
        text = self.run_cli(format='text', env=env)
        self.assertIn('Global coverage incomplete (exit 2)', text)
        self.assertIn('Only core.parenthesized-scripts', text)

    def test_nested_display_gap_and_script_diagnostics_are_unique_per_source(self):
        self.write('main.typ', b'#book-part[\n#include "section.typ"\n#include "section.typ"\n]\n')
        source = (b'#import "Styles/styles.typ": *\n'
                  b'#local-scope-annotations(s => [\n'
                  b'  $ x^i = 1 $ <identity>\n\n'
                  b'  Hence the claim follows.\n])\n')
        target = self.write('section.typ', source)
        default = self.run_cli()
        self.assertEqual(default['rule_set'], 'core')
        self.assertEqual(default['coverage']['available_backends'], ['core.parenthesized-scripts'])
        self.assertEqual(len(default['coverage']['enabled']), 15)
        self.assertEqual(len(default['coverage']['uninspected']), 14)
        self.assertTrue(all(row['rule_id'] == 'core.parenthesized-scripts' for row in default['diagnostics']))
        result = self.run_cli('--rule-set', 'pde')
        self.assertEqual(result['rule_set'], 'pde')
        self.assertEqual(len(result['coverage']['enabled']), 33)
        self.assertEqual(len(result['coverage']['uninspected']), 30)
        self.assertEqual([row['rule_id'] for row in result['diagnostics']].count(
            'core.parenthesized-scripts'), 1)
        self.assertEqual([row['rule_id'] for row in result['diagnostics']].count(
            'pde.display-continuation-gap'), 1)
        self.assertEqual(len(result['proposed_edits']), 2)
        self.assertTrue(all(row['rule_id'] == 'core.parenthesized-scripts'
                            for row in result['proposed_edits']))
        gaps = [row for row in result['coverage']['files']
                if row['rule_id'] == 'pde.display-continuation-gap' and row['path'] == 'section.typ']
        gap, = gaps
        self.assertEqual(gap['diagnostics'], 1)
        self.assertEqual(gap['proposed_edits'], 0)
        self.assertEqual(gap['candidate_verification'], 'diagnostic-only')
        self.assertEqual(gap['analysis_scope'], 'literal-markup-source-pattern')
        script = next(row for row in self.script_coverage(result) if row['path'] == 'section.typ')
        self.assertEqual(script['candidate_verification_rules'], ['core.parenthesized-scripts'])
        candidate = plan(source, tuple(Edit(e['start'], e['end'], e['replacement'].encode())
                                      for e in result['proposed_edits'])).candidate()
        self.assertIn(b'<identity>\n\n  Hence', candidate)
        self.assertEqual(target.read_bytes(), source)

    def test_nested_display_suppression_is_owned_by_equation(self):
        self.write('main.typ', b'#unknown(s => [\n'
                   b'  // typst-lint: ignore-next pde.display-continuation-gap -- intentional new idea\n'
                   b'  $ x^i = 1 $\n\n'
                   b'  Hence this source gap is intentional.\n])\n')
        result = self.run_cli('--rule-set', 'pde')
        self.assertEqual([row['rule_id'] for row in result['diagnostics']], ['core.parenthesized-scripts'])
        self.assertEqual(result['suppressed_count'], 1)
        self.assertEqual(result['suppressed'][0]['rule_id'], 'pde.display-continuation-gap')
        self.assertFalse(result['coverage']['global_complete'])

    def test_explicit_gap_preview_is_independent_of_script_edits_and_does_not_write(self):
        source = b'#wrapper[\n$ x^i $ <equation>\n\nTherefore.\n]\n'
        target = self.write('main.typ', source)
        baseline = self.run_cli('--rule-set', 'pde')
        self.assertNotIn('display_gap_previews', baseline)
        result = self.run_cli('--rule-set', 'pde', '--preview-display-gaps')
        self.assertEqual(result['proposed_edits'], baseline['proposed_edits'])
        self.assertEqual(result['diagnostics'], baseline['diagnostics'])
        preview, = result['display_gap_previews']
        self.assertEqual(preview['baseline_sha256'], digest(source))
        edit, = preview['edits']
        self.assertEqual(edit['rule_id'], 'pde.display-continuation-gap')
        self.assertEqual(preview['verification'], 'parse-syntax-directives-and-display-reanalysis')
        self.assertIn('not-composed', result['display_gap_preview_status'])
        self.assertEqual(target.read_bytes(), source)

    def test_gap_preview_requires_explicit_pde_rule_set(self):
        self.write('main.typ', b'$ A=B $\n\nHence.')
        result = self.run_cli('--preview-display-gaps')
        self.assertEqual(result['phase'], 'input')
        self.assertIn('explicit --rule-set pde', result['error'])

    def test_explicit_standalone_preview_is_separate_from_other_proposals(self):
        source = b'#wrapper[\n  Thus $ x^i = 1 $ <equation> hence.\n]\n'
        target = self.write('main.typ', source)
        baseline = self.run_cli('--rule-set', 'pde')
        self.assertNotIn('standalone_display_previews', baseline)
        result = self.run_cli('--rule-set', 'pde', '--preview-standalone-displays', '--preview-display-gaps')
        self.assertEqual(result['proposed_edits'], baseline['proposed_edits'])
        self.assertEqual(result['diagnostics'], baseline['diagnostics'])
        preview, = result['standalone_display_previews']
        self.assertEqual(preview['baseline_sha256'], digest(source))
        self.assertEqual(len(preview['edits']), 2)
        self.assertTrue(all(e['rule_id'] == 'pde.standalone-display' for e in preview['edits']))
        self.assertEqual(target.read_bytes(), source)

    def test_standalone_preview_requires_explicit_pde_rule_set(self):
        self.write('main.typ', b'Thus $ A=B $ hence.')
        result = self.run_cli('--preview-standalone-displays')
        self.assertEqual(result['phase'], 'input')
        self.assertIn('explicit --rule-set pde', result['error'])

    def test_sequence_preview_is_separate_and_never_writes(self):
        source = b'Thus $ x^i $\n\nHence.\n'
        target = self.write('main.typ', source)
        report = self.run_cli('--rule-set', 'pde', '--preview-sequence')
        sequence, = report['sequence_previews']
        self.assertEqual(sequence['baseline_sha256'], digest(source))
        self.assertEqual(len(sequence['steps']), 3)
        self.assertEqual(report['sequence_preview_status'], 'hash-chained-read-only-not-a-write-transaction')
        self.assertTrue(report['proposed_edits'], 'Ordinary script proposals are retained')
        self.assertEqual(target.read_bytes(), source)

    def test_sequence_preview_requires_explicit_pde_rule_set(self):
        self.write('main.typ', b'$ x^i $')
        report = self.run_cli('--preview-sequence')
        self.assertEqual(report['phase'], 'input')
        self.assertIn('explicit --rule-set pde', report['error'])

    def test_nested_standalone_display_is_diagnostic_only(self):
        source = (b'#local-scope-annotations(s => [\n'
                  b'  Thus $ x^i = 1 $ <identity> and the claim follows.\n])\n')
        target = self.write('main.typ', source)
        result = self.run_cli('--rule-set', 'pde')
        self.assertEqual({d['rule_id'] for d in result['diagnostics']},
                         {'core.parenthesized-scripts', 'pde.standalone-display'})
        standalone, = [d for d in result['diagnostics'] if d['rule_id'] == 'pde.standalone-display']
        self.assertEqual(standalone['line'], 2)
        self.assertEqual(len(result['proposed_edits']), 2)
        self.assertTrue(all(e['rule_id'] == 'core.parenthesized-scripts' for e in result['proposed_edits']))
        coverage = next(row for row in result['coverage']['files']
                        if row['rule_id'] == 'pde.standalone-display')
        self.assertEqual(coverage['diagnostics'], 1)
        self.assertEqual(coverage['proposed_edits'], 0)
        self.assertIsNone(coverage['candidate_sha256'])
        self.assertEqual(target.read_bytes(), source)

    def test_display_gap_does_not_cross_marker_or_comment(self):
        for following in (b'#paragraph-tab\n  New idea.', b'/* note */\n  Prose.'):
            with self.subTest(following=following):
                self.write('main.typ', b'#scope(s => [\n  $ A=B $\n\n  ' + following + b'\n])\n')
                result = self.run_cli('--rule-set', 'pde')
                self.assertFalse(result['diagnostics'])
                self.assertFalse(result['proposed_edits'])
                gap = next(row for row in result['coverage']['files']
                           if row['rule_id'] == 'pde.display-continuation-gap')
                self.assertEqual(gap['status'], 'partial')
                self.assertTrue(gap['reasons'])

    def test_report_budget_refuses_instead_of_truncating(self):
        self.write('main.typ', b'$x^i$\n')
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 1):
            with self.assertRaisesRegex(InputError, 'report item limit'):
                document_cli.report_document(str(self.root), 'main.typ')

    def test_report_budget_includes_nested_wrapper_provenance(self):
        package_root = self.namespace_packages()
        target = self.conditional_fixture()
        target.write_bytes(target.read_bytes().replace(
            b'#highlighted[Text and $x^2$ together.]',
            b'#proof[#note[#highlighted[Text and $x^2$ together.]]]'))
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 32):
            with self.assertRaisesRegex(InputError, 'report item limit'):
                document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                             package_path=package_root)
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 40):
            result = document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                                  package_path=package_root)
        finding, = result['conditional_diagnostics']
        self.assertEqual(len(finding['content_context']), 2)
        self.assertFalse(finding['edit_permission'])

    def test_explicit_package_namespace_hints_are_separate_and_never_edits(self):
        package_root = self.namespace_packages()
        target = self.conditional_fixture()
        original = target.read_bytes()
        plain = self.run_cli('--rule-set', 'pde')
        self.assertIsNone(plain['namespace_evidence'])
        self.assertEqual(plain['conditional_diagnostics'], [])
        result = self.run_cli('--rule-set', 'pde', '--package-path', str(package_root))
        finding, = result['conditional_diagnostics']
        self.assertEqual(finding['rule_id'], 'pde.text-only-highlight')
        self.assertEqual(finding['status'], 'CONDITIONAL')
        self.assertFalse(finding['edit_permission'])
        self.assertTrue(finding['conditions'])
        self.assertEqual(finding['root_sha256'], digest(original))
        self.assertEqual({item['path'] for item in finding['source_hashes']},
                         {'main.typ', 'Styles/styles.typ', 'figures.typ'})
        self.assertEqual(len(result['namespace_evidence']['packages']), 6)
        self.assertFalse(result['namespace_evidence']['initializer_purity_proved'])
        self.assertEqual(result['package_resolution_scope'], 'namespace-roots-only')
        self.assertIn('pde.text-only-highlight', result['coverage']['uninspected'])
        self.assertNotIn('pde.text-only-highlight', result['coverage']['available_backends'])
        self.assertEqual(len(result['coverage']['uninspected']), 30)
        self.assertTrue(all(d['rule_id'] != 'pde.text-only-highlight' for d in result['diagnostics']))
        self.assertTrue(all(e['rule_id'] == 'core.parenthesized-scripts' for e in result['proposed_edits']))
        self.assertEqual(target.read_bytes(), original)

    def test_conditional_literal_body_scope_keeps_script_candidates_separate(self):
        package_root = self.namespace_packages()
        target = self.conditional_fixture()
        source = target.read_bytes() + (
            b'#proof[Before #highlighted[Math $y^2$.] After.]\n'
            b'#proof[#let highlighted(body) = body\n#highlighted[$z^2$]]\n'
            b'#highlighted[$w^2$]\n')
        target.write_bytes(source)
        result = self.run_cli('--rule-set', 'pde', '--package-path', str(package_root))
        self.assertEqual(len(result['conditional_diagnostics']), 3)
        self.assertTrue(all(not row['edit_permission'] for row in result['conditional_diagnostics']))
        self.assertTrue(all(row['rule_id'] == 'core.parenthesized-scripts'
                            for row in result['proposed_edits']))
        self.assertEqual(len(result['proposed_edits']), 8)
        self.assertEqual(result['applied_fixes'], [])
        self.assertEqual(target.read_bytes(), source)
        self.assertFalse(result['coverage']['global_complete'])
        self.assertIn('pde.text-only-highlight', result['coverage']['uninspected'])

    def test_namespace_hints_withhold_later_shadowed_or_unreviewed_helpers(self):
        package_root = self.namespace_packages()
        self.conditional_fixture()
        figures = self.root / 'figures.typ'
        figures.write_bytes(figures.read_bytes() + b'#let highlighted(body) = body\n')
        result = self.run_cli('--rule-set', 'pde', '--package-path', str(package_root))
        self.assertEqual(result['conditional_diagnostics'], [])
        figures.write_bytes(b'#import "@preview/cetz:0.4.2": *\n')
        package = package_root / 'local/text-utils/0.1.2/lib.typ'
        package.write_bytes(package.read_bytes() + b'\n')
        result = self.run_cli('--rule-set', 'pde', '--package-path', str(package_root))
        self.assertEqual(result['conditional_diagnostics'], [])
        self.assertTrue(result['namespace_evidence']['unavailable'])
        self.assertTrue(result['conditional_coverage'][0]['reasons'])
        self.assertEqual(len(result['proposed_edits']), 2)

    def test_conditional_suppression_does_not_hide_source_script_finding(self):
        package_root = self.namespace_packages()
        target = self.conditional_fixture()
        target.write_bytes(target.read_bytes().replace(b'#highlighted',
            b'// typst-lint: ignore-next pde.text-only-highlight -- intentional\n#highlighted'))
        result = self.run_cli('--rule-set', 'pde', '--package-path', str(package_root))
        self.assertEqual(result['conditional_diagnostics'], [])
        self.assertEqual(result['conditional_suppressed_count'], 1)
        self.assertEqual(result['suppressed_count'], 0)
        self.assertEqual([d['rule_id'] for d in result['diagnostics']], ['core.parenthesized-scripts'])

    def test_conditional_marker_gaps_have_separate_metadata_and_rule_coverage(self):
        package_root = self.namespace_packages()
        target = self.conditional_fixture()
        source = target.read_bytes() + (
            b'= Heading\n#paragraph_tab\nProse $y^2$.\n'
            b'= Another\n#paragraph-tab\nMore prose.\n'
            b'#paragraph_tab\n= Structural target\n')
        target.write_bytes(source)
        result = self.run_cli('--rule-set', 'pde', '--package-path', str(package_root))
        gap = 'pde.paragraph-marker-gap'
        findings = [row for row in result['conditional_diagnostics'] if row['rule_id'] == gap]
        self.assertEqual(len(findings), 2)
        metadata = next(row for row in document_cli.load_rules()[0]['rules'] if row['id'] == gap)
        self.assertTrue(all(row['source'] == metadata['source'] and row['grade'] == metadata['grade']
                            and not row['edit_permission'] for row in findings))
        rows = {row['rule_id']: row for row in result['conditional_coverage']}
        self.assertEqual(rows[gap]['diagnostics'], 2)
        self.assertTrue(any('target' in reason for reason in rows[gap]['reasons']))
        self.assertEqual(rows['pde.text-only-highlight']['reasons'], [])
        self.assertEqual(len(result['coverage']['uninspected']), 30)
        self.assertIn(gap, result['coverage']['uninspected'])
        self.assertNotIn(gap, result['coverage']['available_backends'])
        self.assertEqual(result['coverage']['conditional_hint_backends'], list(document_cli.CONDITIONAL_RULES))
        self.assertEqual(len(result['proposed_edits']), 4)
        self.assertTrue(all(row['rule_id'] == 'core.parenthesized-scripts' for row in result['proposed_edits']))
        self.assertEqual(target.read_bytes(), source)

    def test_conditional_marker_suppression_keeps_highlight_and_script_findings(self):
        package_root = self.namespace_packages()
        target = self.conditional_fixture()
        source = target.read_bytes() + (
            b'// typst-lint: ignore-next pde.paragraph-marker-gap -- intentional\n'
            b'#paragraph-tab\nProse.\n')
        target.write_bytes(source)
        result = self.run_cli('--rule-set', 'pde', '--package-path', str(package_root))
        self.assertEqual([row['rule_id'] for row in result['conditional_diagnostics']],
                         ['pde.text-only-highlight'])
        self.assertEqual([row['rule_id'] for row in result['conditional_suppressed']],
                         ['pde.paragraph-marker-gap'])
        self.assertEqual(result['suppressed_count'], 0)
        self.assertEqual(len(result['proposed_edits']), 2)
        self.assertEqual(target.read_bytes(), source)

    def test_conditional_marker_shadow_and_literal_wrapper_scope(self):
        package_root = self.namespace_packages()
        target = self.conditional_fixture()
        target.write_bytes(target.read_bytes() + (
            b'#let paragraph_tab = "other"\n#paragraph_tab\nUnrelated.\n'
            b'#proof[\n#paragraph-tab\nLiteral body.\n]\n'))
        result = self.run_cli('--rule-set', 'pde', '--package-path', str(package_root))
        findings = [row for row in result['conditional_diagnostics']
                    if row['rule_id'] == 'pde.paragraph-marker-gap']
        finding, = findings
        self.assertTrue(finding['content_context'])
        self.assertTrue(any('paragraph-tab transformation' in condition for condition in finding['conditions']))
        self.assertEqual(result['applied_fixes'], [])

    def test_conditional_scoped_callbacks_keep_provenance_and_script_candidates(self):
        package_root = self.namespace_packages()
        target = self.conditional_fixture()
        source = target.read_bytes() + (
            b'#local-tag-scope(s => [\n'
            b'#local-scope-annotations(t => ([\n'
            b'#paragraph_tab\nCallback #highlighted[$y^2$].\n]))\n])\n'
            b'#highlighted[$z^2$]\n')
        target.write_bytes(source)
        result = self.run_cli('--rule-set', 'pde', '--package-path', str(package_root))
        callbacks = [row for row in result['conditional_diagnostics'] if row.get('callback_context')]
        self.assertEqual({row['rule_id'] for row in callbacks},
                         {'pde.text-only-highlight', 'pde.paragraph-marker-gap'})
        self.assertEqual(len(result['conditional_diagnostics']), 4)
        for finding in callbacks:
            self.assertEqual([row['parameters'] for row in finding['callback_context']], [['s'], ['t']])
            for context in finding['callback_context']:
                self.assertLess(context['content_context_index'], len(finding['content_context']))
                self.assertLessEqual(context['call_start'], context['body_start'])
                self.assertLess(context['body_start'], context['body_end'])
                self.assertLessEqual(context['body_end'], context['call_end'])
            self.assertFalse(finding['edit_permission'])
        self.assertEqual(len(result['proposed_edits']), 6)
        self.assertTrue(all(row['rule_id'] == 'core.parenthesized-scripts' for row in result['proposed_edits']))
        self.assertEqual(target.read_bytes(), source)
        self.assertFalse(result['coverage']['global_complete'])

    def test_callback_parameter_shadow_and_rule_suppression_remain_local(self):
        package_root = self.namespace_packages()
        target = self.conditional_fixture()
        target.write_bytes(target.read_bytes() + (
            b'#local-tag-scope(highlighted => [#highlighted[$hidden^2$]])\n'
            b'#local-tag-scope(s => [\n'
            b'// typst-lint: ignore-next pde.text-only-highlight -- intentional\n'
            b'#highlighted[$suppressed^2$]\n])\n'
            b'#highlighted[$visible^2$]\n'))
        result = self.run_cli('--rule-set', 'pde', '--package-path', str(package_root))
        self.assertEqual(len(result['conditional_diagnostics']), 2)
        suppressed, = result['conditional_suppressed']
        self.assertEqual(suppressed['rule_id'], 'pde.text-only-highlight')
        self.assertTrue(suppressed['callback_context'])
        self.assertTrue(all(not row.get('callback_context') for row in result['conditional_diagnostics']))

    def test_callback_metadata_participates_in_report_item_limit(self):
        package_root = self.namespace_packages()
        target = self.conditional_fixture()
        target.write_bytes(target.read_bytes().replace(
            b'#highlighted[Text and $x^2$ together.]',
            b'#local-tag-scope(s => [#highlighted[Text and $x^2$ together.]])'))
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 37):
            with self.assertRaisesRegex(InputError, 'report item limit'):
                document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                             package_path=package_root)
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 100):
            result = document_cli.report_document(str(self.root), 'main.typ', rule_set='pde',
                                                  package_path=package_root)
        finding, = result['conditional_diagnostics']
        self.assertTrue(finding['callback_context'])

    def test_callback_return_withholds_abandoned_hints_without_hiding_outer_call(self):
        package_root = self.namespace_packages()
        target = self.conditional_fixture()
        target.write_bytes(target.read_bytes() + (
            b'#local-tag-scope(s => [\n#highlighted[$a^2$]\n#return []\n'
            b'#import "@local/text-utils:0.1.2": highlighted\n'
            b'#highlighted[$b^2$]\n])\n#highlighted[$c^2$]\n'))
        result = self.run_cli('--rule-set', 'pde', '--package-path', str(package_root))
        self.assertEqual(len(result['conditional_diagnostics']), 2)
        self.assertTrue(all(not row.get('callback_context') for row in result['conditional_diagnostics']))
        self.assertEqual(result['conditional_suppressed'], [])
        self.assertTrue(any('return' in reason.lower() for row in result['conditional_coverage']
                            for reason in row['reasons']))
        self.assertEqual(len(result['proposed_edits']), 8)

    def test_context_without_detected_mutation_keeps_later_scoped_hints_available(self):
        package_root = self.namespace_packages()
        target = self.conditional_fixture()
        source = target.read_bytes() + (
            b'#local-tag-scope(s => [#proof[\n'
            b'#context { let highlighted = "local"; [inside] }\n'
            b'#paragraph_tab\nBody #highlighted[$y^2$].\n]])\n')
        target.write_bytes(source)
        result = self.run_cli('--rule-set', 'pde', '--package-path', str(package_root))
        callbacks = [row for row in result['conditional_diagnostics'] if row.get('callback_context')]
        self.assertEqual({row['rule_id'] for row in callbacks},
                         {'pde.text-only-highlight', 'pde.paragraph-marker-gap'})
        self.assertEqual(len(callbacks), 2)
        self.assertTrue(all(not row['edit_permission'] for row in callbacks))
        self.assertEqual(len(result['proposed_edits']), 4)
        self.assertEqual(target.read_bytes(), source)

    def test_namespace_option_requires_pde_and_unsafe_evidence_refuses(self):
        self.write('main.typ', b'$x^i$\n')
        result = self.run_cli('--package-path', str(self.root / 'never-opened'))
        self.assertEqual(result['phase'], 'input')
        self.assertIn('explicit --rule-set pde', result['error'])
        packages = self.namespace_packages()
        target = packages / 'local/text-utils/0.1.2/lib.typ'
        backup = self.root / 'real-helper.typ'
        target.rename(backup)
        target.symlink_to(backup)
        result = self.run_cli('--rule-set', 'pde', '--package-path', str(packages))
        self.assertEqual(result['phase'], 'input')
        self.assertNotIn('conditional_diagnostics', result)
        self.assertFalse(result['compiler_invoked'])


if __name__ == '__main__':
    unittest.main()
