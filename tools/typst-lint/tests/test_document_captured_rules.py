"""Tests for captured-snapshot 9-rule inspection and independent candidate previews."""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))

import document_captured_rules as captured_rules
import document_cli
from edit_plan import Edit, plan
import include_context
from import_graph import load_import_graph
from syntax_client import EXECUTABLE, parse
from typst_lint import InputError, SafetyError, digest

FLOWBOX_PACKAGE = REPO / 'local/math-blocks/0.2.0/lib.typ'
FLOWBOX_STROKE = b'stroke: 1pt + theme.rule'


def _materialize_all_packages(root: Path, *, split_preview_cache: Path | None = None):
    for name, version in (('scoped-annotations', '0.3.0'),
                          ('math-blocks', '0.2.0'),
                          ('math-book', '0.2.0'),
                          ('text-utils', '0.1.2')):
        shutil.copytree(REPO / 'local' / name / version, root / 'local' / name / version)
    preview_root = root if split_preview_cache is None else split_preview_cache
    shutil.copytree(TOOL / 'tests/fixtures/marker-bounds-packages/preview/mannot/0.4.0',
                    preview_root / 'preview/mannot/0.4.0')
    shutil.copytree(TOOL / 'tests/fixtures/anchor-overlay-packages/preview/cetz/0.4.2',
                    preview_root / 'preview/cetz/0.4.2')


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class DocumentCapturedRulesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='typst-doc-captured-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.doc_root = self.root / 'doc'
        self.doc_root.mkdir()
        self.packages = self.root / 'packages'
        self.packages.mkdir()
        _materialize_all_packages(self.packages)

    def write(self, relpath: str, data: bytes) -> Path:
        target = self.doc_root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def run_cli(self, *options, entrypoint='main.typ', format='json'):
        command = [
            sys.executable, '-B', str(TOOL / 'typst-lint'), 'inspect-document',
            '--root', str(self.doc_root),
            '--entrypoint', entrypoint,
            '--format', format,
            *options,
        ]
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        proc = subprocess.run(command, capture_output=True, text=True, timeout=30, env=env)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertFalse(proc.stderr, proc.stderr)
        return json.loads(proc.stdout) if format == 'json' else proc.stdout

    def _build_all_nine_rules_document(self):
        # 1. Pure relative facade modules for pde.no-redundant-facade-import
        self.write('leaf.typ', b'#let alpha = 1\n')
        self.write('Styles/styles.typ', b'#import "../leaf.typ": *\n')
        self.write('Styles/local_tags.typ', b'#import "../leaf.typ": *\n')
        # 2. Entrypoint: book show + duplicate book reset + theme set + includes
        main_source = (
            b'#import "@local/math-book:0.2.0": apply-math-book\n'
            b'#import "@local/math-blocks:0.2.0": apply-math-block-reset, light-theme\n'
            b'#show: apply-math-book.with(title: "Book")\n'
            b'#show: apply-math-book\n'
            b'#show: apply-math-block-reset\n'
            b'#set light-theme(fill: red)\n'
            b'#include "anchor_and_local.typ"\n'
            b'#include "reference.typ"\n'
            b'#include "bounds.typ"\n'
            b'#include "redundant_import.typ"\n'
            b'#include "flowbox_mod.typ"\n'
        )
        self.write('main.typ', main_source)
        # 3. Anchor access + local overlay API (separate from show/set rules so anchor proof succeeds)
        anchor_source = (
            b'#import "@local/scoped-annotations:0.3.0": mannot-scope\n'
            b'#import "@preview/mannot:0.4.0": mark, annot-cetz\n'
            b'#import "@preview/cetz:0.4.2"\n'
            b'#mannot-scope(s => [$ #(s.mark)("x")(1) #(s.annot)("x", cetz, { cetz.draw.line("x.north", "x.south") }) $])\n'
            b'#mannot-scope(t => [$ #annot-cetz((t.tag)("y"), cetz, {}) $])\n'
        )
        self.write('anchor_and_local.typ', anchor_source)
        # 4. Scoped reference access
        reference_source = (
            b'#import "@local/scoped-annotations:0.3.0": local-tag-scope\n'
            b'#local-tag-scope(s => [#ref((s.tag)("energy"))])\n'
        )
        self.write('reference.typ', reference_source)
        # 5. Annotation marker bounds
        bounds_source = (
            b'#import "@local/scoped-annotations:0.3.0": mannot-scope\n'
            b'#import "@preview/mannot:0.4.0": mark, annot-cetz\n'
            b'#mannot-scope(s => [$ x #(s.tag)("x") #(s.annot)("x", c, ()) $])\n'
        )
        self.write('bounds.typ', bounds_source)
        # 6. Redundant facade import
        redundant_source = (
            b'#import "Styles/styles.typ": *\n'
            b'#import "Styles/local_tags.typ": *\n'
            b'#alpha\n'
        )
        self.write('redundant_import.typ', redundant_source)
        # 7. Flowbox colored border (reviewed math-blocks 0.2.0 module with width-only stroke)
        flowbox_source = FLOWBOX_PACKAGE.read_bytes().replace(FLOWBOX_STROKE, b'stroke: 1pt')
        self.write('flowbox_mod.typ', flowbox_source)
        return {
            'main.typ': main_source,
            'anchor_and_local.typ': anchor_source,
            'reference.typ': reference_source,
            'bounds.typ': bounds_source,
            'redundant_import.typ': redundant_source,
            'flowbox_mod.typ': flowbox_source,
        }

    def test_positive_findings_for_all_nine_rules_core_and_pde(self):
        sources = self._build_all_nine_rules_document()
        # Core rule-set: exactly 7 rules
        core_report = self.run_cli('--rule-set', 'core', '--package-path', str(self.packages),
                                   '--inspect-captured-rules')
        self.assertEqual(core_report['status'], 2)
        self.assertFalse(core_report['compiler_invoked'])
        self.assertEqual(core_report['applied_fixes'], [])
        self.assertNotIn('conditional_captured_rule_previews', core_report)
        self.assertIsNone(core_report['namespace_evidence'])

        core_captured = core_report['captured_rules']
        self.assertEqual(core_captured['rule_set'], 'core')
        self.assertEqual(tuple(core_captured['rule_ids']), captured_rules.CORE_RULE_IDS)
        self.assertEqual(len(core_captured['rule_ids']), 7)
        self.assertFalse(core_captured['edit_permission'] or core_captured['compiler_permission']
                         or core_captured['runtime_binding_proved'] or core_captured['execution_proved'])
        core_found = {d['rule_id'] for d in core_captured['diagnostics']}
        self.assertEqual(core_found, set(captured_rules.CORE_RULE_IDS))
        for rid in captured_rules.CORE_RULE_IDS:
            self.assertGreaterEqual(core_captured['diagnostics_by_rule'][rid], 1, rid)
        # Ensure edits are stripped from diagnostic reporting
        self.assertEqual(core_report['proposed_edits'], [])

        # PDE rule-set: all 9 rules
        pde_report = self.run_cli('--rule-set', 'pde', '--package-path', str(self.packages),
                                  '--inspect-captured-rules')
        self.assertEqual(pde_report['status'], 2)
        self.assertFalse(pde_report['compiler_invoked'])
        self.assertEqual(pde_report['applied_fixes'], [])
        pde_captured = pde_report['captured_rules']
        self.assertEqual(pde_captured['rule_set'], 'pde')
        self.assertEqual(tuple(pde_captured['rule_ids']), captured_rules.ALL_RULE_IDS)
        self.assertEqual(len(pde_captured['rule_ids']), 9)
        pde_found = {d['rule_id'] for d in pde_captured['diagnostics']}
        self.assertEqual(pde_found, set(captured_rules.ALL_RULE_IDS))
        for rid in captured_rules.ALL_RULE_IDS:
            self.assertGreaterEqual(pde_captured['diagnostics_by_rule'][rid], 1, rid)

        # Text output renders cleanly and deterministically
        text = self.run_cli('--rule-set', 'pde', '--package-path', str(self.packages),
                            '--inspect-captured-rules', format='text')
        self.assertIn('Captured rules (pde): 9 rules', text)
        self.assertIn('no edit or compiler permission', text)
        for path, raw in sources.items():
            self.assertEqual((self.doc_root / path).read_bytes(), raw)

    def test_directive_suppressions_respected_across_captured_rules(self):
        self.write('leaf.typ', b'#let alpha = 1\n')
        self.write('Styles/styles.typ', b'#import "../leaf.typ": *\n')
        self.write('Styles/local_tags.typ', b'#import "../leaf.typ": *\n')
        main_source = (
            b'#import "@local/math-book:0.2.0": apply-math-book\n'
            b'#import "@local/math-blocks:0.2.0": apply-math-block-reset, light-theme\n'
            b'#show: apply-math-book.with(title: "Book")\n'
            b'// typst-lint: ignore-next core.single-book-show -- intentional duplicate\n'
            b'#show: apply-math-book\n'
            b'// typst-lint: ignore-next core.no-set-theme -- documented example\n'
            b'#set light-theme(fill: red)\n'
            b'#include "ref_suppressed.typ"\n'
            b'#include "redundant_suppressed.typ"\n'
        )
        self.write('main.typ', main_source)
        self.write('ref_suppressed.typ', (
            b'#import "@local/scoped-annotations:0.3.0": local-tag-scope\n'
            b'#local-tag-scope(s => [\n'
            b'  // typst-lint: ignore-next core.scoped-reference-access -- intentional\n'
            b'  #ref((s.tag)("energy"))\n'
            b'])\n'
        ))
        self.write('redundant_suppressed.typ', (
            b'#import "Styles/styles.typ": *\n'
            b'// typst-lint: ignore-next pde.no-redundant-facade-import -- intentional\n'
            b'#import "Styles/local_tags.typ": *\n'
            b'#alpha\n'
        ))

        report = self.run_cli('--rule-set', 'pde', '--package-path', str(self.packages),
                              '--inspect-captured-rules', '--preview-conditional-captured-rules')
        section = report['captured_rules']
        suppressed_ids = {row['rule_id'] for row in section['suppressed']}
        self.assertEqual(suppressed_ids, {
            'core.single-book-show',
            'core.no-set-theme',
            'core.scoped-reference-access',
            'pde.no-redundant-facade-import',
        })
        self.assertEqual(section['diagnostics'], [])
        # Suppressed findings must not trigger candidate previews
        previews = report['conditional_captured_rule_previews']
        self.assertEqual(previews['candidate_count'], 0)
        self.assertEqual(previews['withheld_count'], 0)

    def test_entrypoint_vs_included_file_book_ownership(self):
        book_body = (
            b'#import "@local/math-book:0.2.0": apply-math-book\n'
            b'#import "@local/math-blocks:0.2.0": apply-math-block-reset\n'
            b'#show: apply-math-book.with(title: "Book")\n'
            b'#show: apply-math-book\n'
            b'#show: apply-math-block-reset\n'
        )
        self.write('main.typ', book_body + b'#include "chapter.typ"\n')
        self.write('chapter.typ', book_body)

        report = self.run_cli('--rule-set', 'core', '--package-path', str(self.packages),
                              '--inspect-captured-rules')
        section = report['captured_rules']
        book_diags = [d for d in section['diagnostics']
                      if d['rule_id'] in ('core.single-book-show', 'core.no-duplicate-book-reset')]
        # Only entrypoint (main.typ) reports book ownership findings
        self.assertEqual({(d['path'], d['rule_id']) for d in book_diags}, {
            ('main.typ', 'core.single-book-show'),
            ('main.typ', 'core.no-duplicate-book-reset'),
        })
        chapter_book_cov = [
            row for row in section['coverage']
            if row['path'] == 'chapter.typ'
            and row['rule_id'] in ('core.single-book-show', 'core.no-duplicate-book-reset')
        ]
        self.assertEqual(len(chapter_book_cov), 2)
        for row in chapter_book_cov:
            self.assertEqual(row['diagnostics'], 0)
            self.assertTrue(any('not the explicit configured entrypoint' in r for r in row['reasons']))

    def test_candidate_previews_verified_for_all_four_proposal_rules(self):
        sources = self._build_all_nine_rules_document()
        # Core preview: 2 proposal-capable rules
        core_report = self.run_cli('--rule-set', 'core', '--package-path', str(self.packages),
                                   '--preview-conditional-captured-rules')
        core_previews = core_report['conditional_captured_rule_previews']
        self.assertEqual(tuple(core_previews['rule_ids']), captured_rules.PROPOSAL_CORE_RULE_IDS)
        self.assertEqual({row['rule_id'] for row in core_previews['previews']},
                         set(captured_rules.PROPOSAL_CORE_RULE_IDS))
        self.assertEqual(core_previews['withheld_count'], 0)
        self.assertFalse(core_previews['runtime_binding_proved'] or core_previews['execution_proved']
                         or core_previews['render_equivalence_proved']
                         or core_previews['edit_permission'] or core_previews['compiler_permission'])

        # PDE preview: all 4 proposal-capable rules
        pde_report = self.run_cli('--rule-set', 'pde', '--package-path', str(self.packages),
                                  '--preview-conditional-captured-rules')
        self.assertEqual(pde_report['conditional_captured_rule_preview_status'],
                         'independent-source-only-not-composed-or-render-verified')
        pde_previews = pde_report['conditional_captured_rule_previews']
        self.assertEqual(tuple(pde_previews['rule_ids']), captured_rules.PROPOSAL_RULE_IDS)
        by_rule = {row['rule_id']: row for row in pde_previews['previews']}
        self.assertEqual(set(by_rule), set(captured_rules.PROPOSAL_RULE_IDS))
        self.assertEqual(pde_previews['withheld_count'], 0)

        # Verify each candidate's exact bytes, hashes, fresh parse, and non-applied flags
        expected_paths = {
            'core.scoped-anchor-access': 'anchor_and_local.typ',
            'core.scoped-reference-access': 'reference.typ',
            'pde.no-redundant-facade-import': 'redundant_import.typ',
            'pde.flowbox-colored-border': 'flowbox_mod.typ',
        }
        for rid, expected_path in expected_paths.items():
            row = by_rule[rid]
            raw = sources[expected_path]
            self.assertEqual(row['path'], expected_path)
            self.assertEqual(row['baseline_sha256'], digest(raw))
            self.assertEqual(row['verification'], captured_rules.VERIFICATION_LABEL)
            self.assertEqual(row['diagnostics_after'], 0)
            self.assertGreaterEqual(row['diagnostics_before'], 1)
            self.assertFalse(row['runtime_binding_proved'] or row['execution_proved']
                             or row['render_equivalence_proved']
                             or row['edit_permission'] or row['compiler_permission'])
            edits = tuple(Edit(e['start'], e['end'], e['replacement'].encode('utf-8')) for e in row['edits'])
            candidate = plan(raw, edits).candidate()
            self.assertEqual(digest(candidate), row['candidate_sha256'])
            self.assertNotEqual(row['candidate_sha256'], row['baseline_sha256'])
            self.assertFalse(parse(candidate).nodes[0].errors)

        # Spot-check exact semantic candidate content for each rule
        anchor_edits = tuple(Edit(e['start'], e['end'], e['replacement'].encode())
                             for e in by_rule['core.scoped-anchor-access']['edits'])
        self.assertIn(b'(s.node)("x", "north")', plan(sources['anchor_and_local.typ'], anchor_edits).candidate())

        ref_edits = tuple(Edit(e['start'], e['end'], e['replacement'].encode())
                          for e in by_rule['core.scoped-reference-access']['edits'])
        self.assertIn(b'#(s.ref)("energy")', plan(sources['reference.typ'], ref_edits).candidate())

        red_edits = tuple(Edit(e['start'], e['end'], e['replacement'].encode())
                          for e in by_rule['pde.no-redundant-facade-import']['edits'])
        self.assertNotIn(b'Styles/local_tags.typ', plan(sources['redundant_import.typ'], red_edits).candidate())

        fb_edits = tuple(Edit(e['start'], e['end'], e['replacement'].encode())
                         for e in by_rule['pde.flowbox-colored-border']['edits'])
        self.assertIn(b'stroke: 1pt + theme.rule', plan(sources['flowbox_mod.typ'], fb_edits).candidate())

        # Text output and zero source mutations
        text = self.run_cli('--rule-set', 'pde', '--package-path', str(self.packages),
                            '--preview-conditional-captured-rules', format='text')
        self.assertIn('Conditional captured rule previews (pde): 4 verified candidates', text)
        self.assertIn('independent source-only, no edit or compiler permission', text)
        for path, raw in sources.items():
            self.assertEqual((self.doc_root / path).read_bytes(), raw)

    def test_withheld_and_refusal_cases_fail_closed(self):
        self.write('leaf.typ', b'#let alpha = 1\n')
        self.write('Styles/styles.typ', b'#import "../leaf.typ": *\n')
        self.write('Styles/local_tags.typ', b'#import "../leaf.typ": *\n')
        self.write('main.typ', (
            b'#include "dotted_anchor.typ"\n'
            b'#include "commented_ref.typ"\n'
            b'#include "commented_redundant.typ"\n'
        ))
        # 1. Dotted marker name -> anchor finding reported, repair refused/withheld
        self.write('dotted_anchor.typ', (
            b'#import "@local/scoped-annotations:0.3.0": mannot-scope\n'
            b'#import "@preview/mannot:0.4.0": mark\n'
            b'#import "@preview/cetz:0.4.2"\n'
            b'#mannot-scope(s => [$ #(s.mark)("a.b")(1) #(s.annot)("a.b", cetz, { cetz.draw.line("a.b.north", (0, 0)) }) $])\n'
        ))
        # 2. Comment inside ref(...) -> scoped reference finding reported, repair refused/withheld
        self.write('commented_ref.typ', (
            b'#import "@local/scoped-annotations:0.3.0": local-tag-scope\n'
            b'#local-tag-scope(s => [#ref(/* keep */ (s.tag)("energy"))])\n'
        ))
        # 3. Comment present in file -> redundant facade import finding reported, delete refused/withheld
        self.write('commented_redundant.typ', (
            b'// keep this comment\n'
            b'#import "Styles/styles.typ": *\n'
            b'#import "Styles/local_tags.typ": *\n'
            b'#alpha\n'
        ))

        report = self.run_cli('--rule-set', 'pde', '--package-path', str(self.packages),
                              '--inspect-captured-rules', '--preview-conditional-captured-rules')
        previews = report['conditional_captured_rule_previews']
        self.assertEqual(previews['candidate_count'], 0)
        self.assertEqual(previews['withheld_count'], 3)
        withheld_by_rule = {row['rule_id']: row for row in previews['withheld']}
        self.assertEqual(set(withheld_by_rule), {
            'core.scoped-anchor-access',
            'core.scoped-reference-access',
            'pde.no-redundant-facade-import',
        })
        for row in withheld_by_rule.values():
            self.assertEqual(row['verification'], 'withheld')
            self.assertIsNone(row['candidate_sha256'])
            self.assertEqual(row['edits'], [])
            self.assertTrue(row.get('reason'))
            self.assertFalse(row['edit_permission'] or row['compiler_permission'])

        # Injected reanalysis failure must fail closed and withhold
        ref_src = (
            b'#import "@local/scoped-annotations:0.3.0": local-tag-scope\n'
            b'#local-tag-scope(s => [#ref((s.tag)("energy"))])\n'
        )
        tree = parse(ref_src)
        bundle = captured_rules.capture_reviewed_packages((str(self.packages),), ('@local/scoped-annotations:0.3.0',))
        baseline = captured_rules.analyze_source(tree, 'ref.typ', rule_set='core',
                                                 packages=bundle.sources, strip_edits=False)
        with patch.object(captured_rules, '_reanalyze_single_rule',
                          side_effect=SafetyError('injected reanalysis failure')):
            verified = captured_rules.verify_rule_candidate(
                tree, 'ref.typ', 'core.scoped-reference-access',
                baseline['core.scoped-reference-access'], packages=bundle.sources)
        self.assertEqual(verified.verification, 'withheld')
        self.assertIsNone(verified.candidate_sha256)
        self.assertEqual(verified.edits, ())
        self.assertIn('injected reanalysis failure', verified.reason)

    def test_no_filesystem_rereads_during_inspect_and_preview(self):
        self._build_all_nine_rules_document()
        main_bytes = (self.doc_root / 'main.typ').read_bytes()
        captured = include_context.load_include_context(self.doc_root, (('main.typ', main_bytes),), budget='book-v1')
        graph = load_import_graph(self.doc_root, ((item.path, item.source) for item in captured.sources))
        imports = captured_rules.collect_import_paths(captured.sources, import_graph=graph)
        bundle = captured_rules.capture_reviewed_packages((str(self.packages),), imports)

        # Delete the entire temporary document & package directories to prove ZERO filesystem access
        shutil.rmtree(self.doc_root)
        shutil.rmtree(self.packages)

        inspected = captured_rules.inspect_captured_document(
            captured, entrypoint='main.typ', rule_set='pde',
            import_graph=graph, package_bundle=bundle,
        )
        self.assertEqual({d['rule_id'] for d in inspected['diagnostics']}, set(captured_rules.ALL_RULE_IDS))
        previewed = captured_rules.preview_captured_document(
            captured, entrypoint='main.typ', rule_set='pde',
            import_graph=graph, package_bundle=bundle,
        )
        self.assertEqual({row['rule_id'] for row in previewed['previews']}, set(captured_rules.PROPOSAL_RULE_IDS))

    def test_cli_backward_compatibility_and_budget_enforcement(self):
        self.write('main.typ', b'$x^i$\n')
        baseline_core = self.run_cli()
        self.assertNotIn('captured_rules', baseline_core)
        self.assertNotIn('conditional_captured_rule_previews', baseline_core)
        self.assertNotIn('conditional_captured_rule_preview_status', baseline_core)

        # Existing core --package-path refusal remains intact when new flags are omitted
        refused = self.run_cli('--package-path', str(self.packages))
        self.assertEqual(refused['phase'], 'input')
        self.assertIn('explicit --rule-set pde', refused['error'])

        # MAX_REPORT_ITEMS budget enforced fail-closed
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 10):
            with self.assertRaisesRegex(InputError, 'report item limit'):
                document_cli.report_document(str(self.doc_root), 'main.typ',
                                             inspect_captured_rules=True)

    def test_multiroot_package_capture_and_resource_capture_primitives(self):
        split_pkg = self.root / 'split-packages'
        split_cache = self.root / 'split-cache'
        split_pkg.mkdir()
        split_cache.mkdir()
        _materialize_all_packages(split_pkg, split_preview_cache=split_cache)

        self._build_all_nine_rules_document()
        report = self.run_cli('--rule-set', 'pde',
                              '--package-path', str(split_pkg),
                              '--footnote-cache-path', str(split_cache),
                              '--inspect-captured-rules',
                              '--preview-conditional-captured-rules')
        self.assertEqual({d['rule_id'] for d in report['captured_rules']['diagnostics']},
                         set(captured_rules.ALL_RULE_IDS))
        self.assertEqual({row['rule_id'] for row in report['conditional_captured_rule_previews']['previews']},
                         set(captured_rules.PROPOSAL_RULE_IDS))

        # Also test ResourceCapture -> PackageSource + ImportGraph pure helpers for Wave 1
        import document_resources
        requests = [
            document_resources.ResourceRequest('project', 'redundant_import.typ', 'source'),
            document_resources.ResourceRequest('project', 'Styles/styles.typ', 'source'),
            document_resources.ResourceRequest('project', 'Styles/local_tags.typ', 'source'),
            document_resources.ResourceRequest('project', 'leaf.typ', 'source'),
            document_resources.ResourceRequest('packages', 'local/math-blocks/0.2.0/typst.toml', 'package'),
            document_resources.ResourceRequest('packages', 'local/math-blocks/0.2.0/lib.typ', 'package'),
        ]
        snap = document_resources.capture(
            {'project': str(self.doc_root), 'packages': str(split_pkg), 'cache': str(split_cache)},
            requests,
        )
        pkg_bundle = captured_rules.packages_from_resource_capture(snap)
        self.assertEqual([s.import_path for s in pkg_bundle.sources], ['@local/math-blocks:0.2.0'])
        in_mem_graph = captured_rules.import_graph_from_resource_capture(snap, ('redundant_import.typ',))
        red_bytes = (self.doc_root / 'redundant_import.typ').read_bytes()
        res = captured_rules.analyze_source(parse(red_bytes), 'redundant_import.typ', rule_set='pde',
                                            packages=pkg_bundle.sources, import_graph=in_mem_graph,
                                            strip_edits=False)
        self.assertEqual(len(res['pde.no-redundant-facade-import'].diagnostics), 1)
        self.assertEqual(len(res['pde.no-redundant-facade-import'].edits), 1)

    def test_verify_rule_candidate_withholds_whole_token_suppressed_target_replacement(self):
        bundle = captured_rules.capture_reviewed_packages(
            (str(self.packages),), ('@local/scoped-annotations:0.3.0',))
        # Both FuncCall (callee start anchor) and Text (end - 1 anchor) whole-token
        # replacements with identical span & AST kind must be rejected by
        # source_analysis._scope_owners via verify_rule_candidate.
        cases = (
            ('FuncCall', b'#rect[x]\n', b'rect[x]', b'hide[x]'),
            ('Text', b'hello\n', b'hello', b'world'),
        )
        for label, target_line, old_tok, new_tok in cases:
            with self.subTest(target_kind=label):
                src = (
                    b'#import "@local/scoped-annotations:0.3.0": local-tag-scope\n'
                    b'#local-tag-scope(s => [\n'
                    b'  // typst-lint: ignore-next core.no-set-theme -- keep target origin\n'
                    b'  ' + target_line
                    + b'  #ref((s.tag)("energy"))\n'
                    b'])\n'
                )
                tree = parse(src)
                baseline = captured_rules.analyze_source(
                    tree, 'ref.typ', rule_set='core', packages=bundle.sources, strip_edits=False)
                ref_result = baseline['core.scoped-reference-access']
                self.assertEqual(len(ref_result.edits), 1)
                tok_start = src.index(old_tok)
                crafted = replace(
                    ref_result,
                    edits=(Edit(tok_start, tok_start + len(old_tok), new_tok), *ref_result.edits),
                )
                verified = captured_rules.verify_rule_candidate(
                    tree, 'ref.typ', 'core.scoped-reference-access',
                    crafted, packages=bundle.sources,
                )
                self.assertEqual(verified.verification, 'withheld')
                self.assertIsNone(verified.candidate_sha256)
                self.assertEqual(verified.edits, ())
                self.assertIn('directive target origin', verified.reason)

    def test_verify_rule_candidate_withholds_when_reanalysis_introduces_new_skip(self):
        src = (
            b'#import "@local/scoped-annotations:0.3.0": local-tag-scope\n'
            b'#local-tag-scope(s => [#ref((s.tag)("energy"))])\n'
        )
        tree = parse(src)
        bundle = captured_rules.capture_reviewed_packages(
            (str(self.packages),), ('@local/scoped-annotations:0.3.0',))
        baseline = captured_rules.analyze_source(
            tree, 'ref.typ', rule_set='core', packages=bundle.sources, strip_edits=False)
        ref_result = baseline['core.scoped-reference-access']
        self.assertEqual(len(ref_result.edits), 1)
        with patch.object(
            captured_rules, '_reanalyze_single_rule',
            return_value=captured_rules.CapturedRuleResult(
                diagnostics=(), edits=(), skips=('refused: synthetic reanalysis skip',)),
        ):
            verified = captured_rules.verify_rule_candidate(
                tree, 'ref.typ', 'core.scoped-reference-access',
                ref_result, packages=bundle.sources,
            )
        self.assertEqual(verified.verification, 'withheld')
        self.assertIsNone(verified.candidate_sha256)
        self.assertEqual(verified.edits, ())
        self.assertIn('analysis refusal', verified.reason)

    def test_import_graph_from_resource_capture_source_overrides(self):
        import document_resources
        from types import MappingProxyType

        self.write('leaf.typ', b'#let alpha = 1\n')
        self.write('alt.typ', b'#let beta = 2\n')
        self.write('Styles/styles.typ', b'#import "../leaf.typ": *\n')
        self.write('main.typ', b'#import "Styles/styles.typ": *\n#alpha\n')
        requests = [
            document_resources.ResourceRequest('project', rel, 'source')
            for rel in ('main.typ', 'Styles/styles.typ', 'leaf.typ', 'alt.typ')
        ]
        snap = document_resources.capture({'project': str(self.doc_root)}, requests)
        base_graph = captured_rules.import_graph_from_resource_capture(snap, ('main.typ',))
        empty_override_graph = captured_rules.import_graph_from_resource_capture(
            snap, ('main.typ',), source_overrides={})
        self.assertEqual(base_graph, empty_override_graph)
        self.assertEqual({e.target for e in base_graph.edges}, {'Styles/styles.typ', 'leaf.typ'})

        # Override via project-relative path dict
        overridden_main = b'#import "alt.typ": *\n#beta\n'
        rel_graph = captured_rules.import_graph_from_resource_capture(
            snap, ('main.typ',), source_overrides={'main.typ': overridden_main})
        self.assertEqual(rel_graph.source_for('main.typ').source, overridden_main)
        self.assertEqual({e.target for e in rel_graph.edges}, {'alt.typ'})

        # Override via MappingProxyType[(root, path), bytes] candidate mapping
        candidate_map = MappingProxyType({
            ('project', 'main.typ'): overridden_main,
            ('project', 'Styles/styles.typ'): (self.doc_root / 'Styles/styles.typ').read_bytes(),
            ('project', 'leaf.typ'): (self.doc_root / 'leaf.typ').read_bytes(),
            ('project', 'alt.typ'): (self.doc_root / 'alt.typ').read_bytes(),
        })
        tuple_graph = captured_rules.import_graph_from_resource_capture(
            snap, ('main.typ',), source_overrides=candidate_map)
        self.assertEqual(tuple_graph, rel_graph)

        # Unknown path or non-bytes override fails closed
        with self.assertRaises(InputError):
            captured_rules.import_graph_from_resource_capture(
                snap, ('main.typ',), source_overrides={'missing.typ': b''})
        with self.assertRaises(InputError):
            captured_rules.import_graph_from_resource_capture(
                snap, ('main.typ',), source_overrides={'main.typ': 'not-bytes'})

    def test_capture_reviewed_packages_handles_corrupt_manifest_symmetrically(self):
        from package_loader import PackageInputError

        corrupt_toml = self.packages / 'local/math-blocks/0.2.0/typst.toml'
        corrupt_toml.write_bytes(b'[package\nname = "broken"\n')
        bundle = captured_rules.capture_reviewed_packages(
            (str(self.packages),),
            ('@local/scoped-annotations:0.3.0', '@local/math-blocks:0.2.0'),
        )
        self.assertIn('@local/scoped-annotations:0.3.0', [s.import_path for s in bundle.sources])
        self.assertNotIn('@local/math-blocks:0.2.0', [s.import_path for s in bundle.sources])
        self.assertEqual(len(bundle.unavailable), 1)
        self.assertEqual(bundle.unavailable[0].import_path, '@local/math-blocks:0.2.0')
        self.assertEqual(bundle.unavailable[0].code, 'malformed-package-manifest')

        # Non-UTF-8 typst.toml is also recorded as malformed-package-manifest symmetrically
        corrupt_toml.write_bytes(b'\xff\xfe')
        bundle_utf8 = captured_rules.capture_reviewed_packages(
            (str(self.packages),),
            ('@local/scoped-annotations:0.3.0', '@local/math-blocks:0.2.0'),
        )
        self.assertIn('@local/scoped-annotations:0.3.0', [s.import_path for s in bundle_utf8.sources])
        self.assertNotIn('@local/math-blocks:0.2.0', [s.import_path for s in bundle_utf8.sources])
        self.assertEqual(bundle_utf8.unavailable[0].code, 'malformed-package-manifest')

        # Unsafe symlinked typst.toml must still fail closed with PackageInputError
        corrupt_toml.unlink()
        target = self.root / 'outside.toml'
        target.write_bytes(b'[package]\nname = "math-blocks"\nversion = "0.2.0"\nentrypoint = "lib.typ"\n')
        corrupt_toml.symlink_to(target)
        with self.assertRaises(PackageInputError):
            captured_rules.capture_reviewed_packages(
                (str(self.packages),), ('@local/math-blocks:0.2.0',))

    def test_read_reviewed_package_from_snapshot_optional_requires_unavailable_records(self):
        from package_loader import (
            PackageInputError,
            PackageUnavailable,
            read_reviewed_package_from_snapshot,
        )

        class UntouchedSnapshot:
            def exists(self, *args, **kwargs):
                raise AssertionError('snapshot.exists must not be called when optional guards fail')

            def read(self, *args, **kwargs):
                raise AssertionError('snapshot.read must not be called when optional guards fail')

        unavailable = PackageUnavailable('@local/math-blocks:0.2.0', 'missing-package-artifact', 'missing')
        invalid_pairs = (
            (None, unavailable),
            (unavailable, None),
            (None, None),
            ('not-unavailable', unavailable),
            (unavailable, {'code': 'missing'}),
        )
        for missing_manifest, missing_entrypoint in invalid_pairs:
            with self.subTest(missing_manifest=missing_manifest, missing_entrypoint=missing_entrypoint):
                with self.assertRaisesRegex(PackageInputError, 'PackageUnavailable'):
                    read_reviewed_package_from_snapshot(
                        UntouchedSnapshot(),
                        object(),
                        object(),
                        optional=True,
                        missing_manifest=missing_manifest,
                        missing_entrypoint=missing_entrypoint,
                    )


if __name__ == '__main__':
    unittest.main()
