"""Real parser/provenance tests for the bounded project-reference rule subset."""
from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import plan
from package_loader import load_packages
from reference_catalog import parse_reference_catalog
from reference_rules import ARGUMENTS as ARGS, CATALOG_TARGET as TARGET, analyze as references
from source_analysis import analyze, rule_ids
from static_resolver import PROJECT_REFERENCES, imported_paths, resolve
from syntax_client import EXECUTABLE, parse
from typst_lint import digest, report

IMPORT = b'#import "@local/project-references:0.1.0": project-ref\n'
CATALOG_DATA = {'schema': 1, 'projects': {'pde': {
    'references': {'energy': {'page': 1, 'text': 'Theorem 1'},
                   'duplicate': {'page': 2, 'text': 'Theorem 2'},
                   '에너지': {'page': 3, 'text': 'Theorem 3'}},
    'ambiguous_labels': ['duplicate']}}}
CATALOG_RAW = json.dumps(CATALOG_DATA).encode()
CATALOG = parse_reference_catalog(CATALOG_RAW, 'explicit-test-catalog')


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter explicitly first')
class ReferenceRuleTests(unittest.TestCase):
    def run_source(self, source, *, catalog=CATALOG, packages=True, ignored=frozenset(), pack='core'):
        tree = parse(source)
        loaded = load_packages(REPO, imported_paths(tree)).sources if packages else ()
        return analyze(tree, 'main.typ', pack=pack, packages=loaded,
                       reference_catalog=catalog, ignored=ignored)

    def candidate(self, source, **kwargs):
        result = self.run_source(source, **kwargs)
        return result, plan(source, [e for r in result.values() for e in r.edits]).candidate()

    def test_exact_ref_content_repair_preserves_identity_and_is_idempotent(self):
        source = IMPORT + b'#project-ref("pde", [@energy])\n'
        result, candidate = self.candidate(source)
        self.assertEqual(candidate, source.replace(b'[@energy]', b'<energy>'))
        self.assertEqual(len(result[ARGS].diagnostics), 1)
        self.assertEqual(len(result[ARGS].edits), 1)
        self.assertFalse(result[TARGET].diagnostics or result[TARGET].edits)
        edit = result[ARGS].edits[0]
        self.assertEqual(source[edit.start:edit.end], b'[@energy]')
        self.assertFalse(parse(candidate).nodes[0].errors)
        final = self.run_source(candidate)
        self.assertFalse(any(r.edits or r.diagnostics for r in final.values()))
        self.assertTrue(final[ARGS].skips)  # bounded implementation, never full coverage

    def test_only_exact_content_and_unique_configured_target_can_be_repaired(self):
        targets = [b'[@energy ]', b'[ @energy]', b'[@energy\n]', b'[@energy[Supplement]]',
                   b'[@energy @energy]', b'[@energy /* note */]', b'[@energy// note\n]',
                   b'[#ref(<energy>)]', b'[Theorem @energy]', b'[]', b'[1.1]',
                   b'[@missing]', b'[@duplicate]', b'[@local-scope-energy]',
                   b'[@mannot-scope-energy]', b'[@_mannot-energy]']
        for target in targets:
            source = IMPORT + b'#project-ref("pde", ' + target + b')\n'
            with self.subTest(target=target):
                result, candidate = self.candidate(source)
                self.assertEqual(candidate, source)
                self.assertEqual(len(result[ARGS].diagnostics), 1)
                self.assertFalse(result[ARGS].edits or result[TARGET].edits)
        for source in (IMPORT + b'#project-ref("absent", [@energy])',
                       IMPORT + b'#project-ref(project, [@energy])',
                       IMPORT + b'#project-ref("pd\\u{65}", [@energy])',
                       IMPORT + b'#project-ref("pde", /* preserve */ [@energy])'):
            result, candidate = self.candidate(source)
            self.assertTrue(result[ARGS].diagnostics)
            self.assertEqual(candidate, source)
        source = IMPORT + b'#project-ref("pde", [@energy])'
        result, candidate = self.candidate(source, catalog=None)
        self.assertEqual(candidate, source)
        self.assertTrue(result[ARGS].diagnostics)
        self.assertTrue(any('No catalog configured' in s for s in result[TARGET].skips))

    def test_bare_reference_is_parse_failure_without_any_repair(self):
        source = IMPORT + b'#project-ref("pde", @energy)\n$x^i$'
        result, candidate = self.candidate(source)
        self.assertEqual(candidate, source)
        self.assertTrue(result[ARGS].parse_errors)
        self.assertFalse(any(r.edits or r.diagnostics for r in result.values()))

    def test_proven_bad_shapes_and_types_have_diagnostics_without_guessed_fixes(self):
        calls = ['project-ref()', 'project-ref("pde")', 'project-ref("pde", <energy>, 3)',
                 'project-ref(project: "pde", target: <energy>)',
                 'project-ref("pde", <energy>, supplement: [x])',
                 'project-ref("pde", <energy>)[extra]',
                 'project-ref("pde", "1.1.1")', 'project-ref("pde", 42)',
                 'project-ref("pde", "ene\\u{72}gy")',
                 'project-ref("pde", 1.5)', 'project-ref("pde", 1pt)',
                 'project-ref("pde", true)', 'project-ref("pde", none)',
                 'project-ref("pde", $1.1$)',
                 'project-ref("pde", auto)', 'project-ref("pde", (1, 2))',
                 'project-ref("pde", (a: 1))', 'project-ref(42, <energy>)',
                 'project-ref([pde], <energy>)']
        for call in calls:
            source = IMPORT + ('#' + call).encode()
            with self.subTest(call=call):
                result, candidate = self.candidate(source)
                self.assertFalse(result[ARGS].parse_errors)
                self.assertTrue(result[ARGS].diagnostics)
                self.assertEqual(candidate, source)

    def test_literal_global_labels_and_local_prefix_contract(self):
        for prefix in ('local-scope-', 'local-scope-annotations-', 'mannot-scope-', '_mannot-'):
            source = IMPORT + f'#project-ref("pde", <{prefix}energy>)'.encode()
            result = self.run_source(source)
            self.assertEqual(len(result[ARGS].diagnostics), 1)
            self.assertIn('local-scope', result[ARGS].diagnostics[0].explanation)
            self.assertFalse(result[TARGET].diagnostics or result[ARGS].edits)
        result = self.run_source(IMPORT + b'#project-ref("pde", <energy>)')
        self.assertFalse(result[ARGS].diagnostics or result[TARGET].diagnostics)

    def test_catalog_reports_unknown_missing_and_ambiguous_without_fallback(self):
        source = IMPORT + b'#project-ref("absent", <energy>)\n#project-ref("pde", <missing>)\n#project-ref("pde", <duplicate>)\n'
        result, candidate = self.candidate(source)
        self.assertFalse(result[ARGS].diagnostics)
        self.assertEqual([d.explanation.split()[0] for d in result[TARGET].diagnostics],
                         ['Unknown', 'Missing', 'Ambiguous'])
        self.assertFalse(result[TARGET].edits)
        self.assertEqual(candidate, source)
        result = self.run_source(source, catalog=None)
        self.assertFalse(result[TARGET].diagnostics)

    def test_dynamic_types_scoped_generators_and_spreads_are_explicit_skips(self):
        for call in ('project-ref(project, target)', 'project-ref("pde", (s.tag)("energy"))',
                     'project-ref("pde", label("energy"))', 'project-ref("pde", target)',
                     'project-ref("pde", <energy>, ..args)', 'project-ref(..args)',
                     'project-ref("pde", (<energy>))'):
            source = IMPORT + ('#' + call).encode()
            with self.subTest(call=call):
                result, candidate = self.candidate(source)
                self.assertFalse(result[ARGS].diagnostics or result[TARGET].diagnostics)
                self.assertEqual(candidate, source)
                self.assertTrue(any('unresolved' in s.lower() for s in result[ARGS].skips))

    def test_verified_import_and_callee_aliases(self):
        cases = [
            (IMPORT, 'project-ref'),
            (b'#import "@local/project-references:0.1.0": project-ref as pr\n', 'pr'),
            (b'#import "@local/project-references:0.1.0" as refs\n', 'refs.project-ref'),
            (b'#import "@local/project-references:0.1.0"\n', 'project-references.project-ref'),
            (b'#import "@local/project-references:0.1.0": *\n', 'project-ref'),
            (IMPORT + b'#let pr = project-ref\n', 'pr'),
            (IMPORT + b'#let pr = (project-ref)\n#let project-ref = []\n', 'pr'),
            (b'#import "@local/project-references:0.1.0" as refs\n#let saved = refs\n', 'saved.project-ref'),
        ]
        for prefix, callee in cases:
            source = prefix + f'#{callee}("pde", [@energy])'.encode()
            with self.subTest(callee=callee, prefix=prefix):
                result, candidate = self.candidate(source)
                self.assertEqual(len(result[ARGS].edits), 1)
                self.assertEqual(candidate, source.replace(b'[@energy]', b'<energy>'))

    def test_missing_modified_facade_and_shadowed_provenance_never_trigger(self):
        source = IMPORT + b'#project-ref("pde", [@energy])'
        result = self.run_source(source, packages=False)
        self.assertFalse(result[ARGS].diagnostics or result[ARGS].edits)
        for prefix in (b'', b'#import "facade.typ": project-ref\n',
                       b'#import "@local/project-references:0.1.1": project-ref\n',
                       IMPORT + b'#let project-ref = (a, b) => b\n',
                       IMPORT + b'#import "unknown.typ": *\n',
                       IMPORT + b'#{ project-ref = (a, b) => b }\n'):
            with self.subTest(prefix=prefix):
                result = self.run_source(prefix + b'#project-ref("pde", [@energy])')
                self.assertFalse(result[ARGS].diagnostics or result[ARGS].edits)
        tree = parse(source)
        packages = load_packages(REPO, imported_paths(tree)).sources
        result = analyze(tree, 'main.typ', reference_catalog=CATALOG,
                         packages=tuple(replace(p, source=p.source + b'\n') for p in packages))
        self.assertFalse(result[ARGS].diagnostics or result[ARGS].edits)

    def test_nested_captured_and_deferred_calls_remain_partial(self):
        for body in (b'#let captured = [#project-ref("pde", [@energy])]\n',
                     b'#let f() = project-ref("pde", [@energy])\n',
                     b'#context project-ref("pde", [@energy])\n',
                     b'#[#project-ref("pde", [@energy])]\n',
                     b'#{ project-ref("pde", [@energy]) }\n'):
            with self.subTest(body=body):
                result, candidate = self.candidate(IMPORT + body)
                self.assertFalse(result[ARGS].edits or result[ARGS].diagnostics)
                self.assertEqual(candidate, IMPORT + body)
                self.assertTrue(result[ARGS].skips)

    def test_comments_strings_and_raw_are_not_calls(self):
        source = IMPORT + b'// #project-ref("pde", [@energy])\n`#project-ref("pde", [@energy])`\n#let text = "project-ref(1, 2)"\n'
        result = self.run_source(source)
        self.assertFalse(result[ARGS].diagnostics or result[ARGS].edits)

    def test_bom_crlf_unicode_preserve_exact_bytes_and_columns(self):
        source = b'\xef\xbb\xbf' + IMPORT.replace(b'\n', b'\r\n') + '앞 #project-ref("pde", [@에너지])\r\n'.encode()
        result, candidate = self.candidate(source)
        self.assertEqual(candidate, source.replace('[@에너지]'.encode(), '<에너지>'.encode()))
        self.assertEqual((result[ARGS].diagnostics[0].line, result[ARGS].diagnostics[0].column), (2, 23))
        self.assertTrue(candidate.startswith(b'\xef\xbb\xbf'))
        self.assertNotIn(b'\n', candidate.replace(b'\r\n', b''))

    def test_unescaped_unicode_project_key_is_preserved_exactly(self):
        data = {'schema': 1, 'projects': {'해석': CATALOG_DATA['projects']['pde']}}
        catalog = parse_reference_catalog(json.dumps(data).encode(), 'unicode-project-fixture')
        source = IMPORT + '#project-ref("해석", [@에너지])'.encode()
        result, candidate = self.candidate(source, catalog=catalog)
        self.assertEqual(candidate, source.replace('[@에너지]'.encode(), '<에너지>'.encode()))
        self.assertEqual(len(result[ARGS].edits), 1)

    def test_inline_and_config_suppressions_remove_only_owned_edits(self):
        directive = b'// typst-lint: ignore-next core.project-ref-arguments -- preserve this example\n'
        call = b'#project-ref("pde", [@energy])\n'
        source = IMPORT + directive + call + call
        result, candidate = self.candidate(source)
        self.assertEqual(len(result[ARGS].suppressed), 1)
        self.assertEqual(len(result[ARGS].diagnostics), 1)
        self.assertEqual(candidate, IMPORT + directive + call + call.replace(b'[@energy]', b'<energy>'))
        result, candidate = self.candidate(source, ignored=frozenset((ARGS,)))
        self.assertEqual(candidate, source)
        self.assertFalse(result[ARGS].edits)
        source = IMPORT + b'// typst-lint: ignore-next core.project-ref-catalog-target -- archived\n#project-ref("pde", <missing>)\n#project-ref("pde", <missing>)'
        result = self.run_source(source)
        self.assertEqual(len(result[TARGET].suppressed), 1)
        self.assertEqual(len(result[TARGET].diagnostics), 1)

    def test_composition_preserves_shifted_existing_diagnostics(self):
        source = IMPORT + b'$x^i$\n#project-ref("pde", [@energy])\n#project-ref("pde", <missing>)\n'
        result, candidate = self.candidate(source)
        self.assertEqual(candidate, source.replace(b'x^i', b'x^(i)').replace(b'[@energy]', b'<energy>'))
        self.assertEqual(len(result[TARGET].diagnostics), 1)
        self.assertTrue(result[ARGS].edits)
        final = self.run_source(candidate)
        self.assertFalse(any(r.edits for r in final.values()))
        self.assertEqual(len(final[TARGET].diagnostics), 1)

    def test_stale_resolution_refuses_diagnostics_and_proposals(self):
        source = IMPORT + b'#project-ref("pde", [@energy])'
        tree = parse(source)
        resolved = resolve(tree, packages=load_packages(REPO, imported_paths(tree)).sources)
        for value in (replace(resolved, source_sha256='stale'), replace(resolved, facts=())):
            result = references(tree, 'main.typ', value, CATALOG)
            self.assertFalse(any(r.diagnostics or r.edits for r in result.values()))
            self.assertTrue(all(r.skips for r in result.values()))

    def test_cli_every_mode_is_status_two_read_only_and_never_compiles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = IMPORT + b'#project-ref("pde", [@energy])\n'
            target = root / 'main.typ'
            target.write_bytes(source)
            catalog_path = root / 'catalog.json'
            catalog_path.write_bytes(CATALOG_RAW)
            (root / '.typst-lint.toml').write_text('catalog="catalog.json"\n')
            output = root / 'output.pdf'
            output.write_bytes(b'old output')
            spy = root / 'compiler-spy'
            spy.write_text('#!/bin/sh\ntouch "' + str(root / 'compiler-ran') + '"\n')
            spy.chmod(0o700)
            for command, extra in (('check', []), ('fix', ['--dry-run']), ('fix', []), ('compile', [])):
                launcher = TOOL / ('typst-style' if command == 'compile' else 'typst-lint')
                args = [sys.executable, str(launcher), command, *extra, '--root', str(root),
                        '--package-path', str(REPO), '--format', 'json']
                if command == 'compile':
                    args += ['--', str(spy), 'main.typ', 'output.pdf']
                process = subprocess.run(args, capture_output=True, text=True)
                self.assertEqual(process.returncode, 2, process.stderr)
                value = json.loads(process.stdout)
                self.assertEqual(value['coverage']['available_backends'], list(rule_ids('core')))
                self.assertEqual({e['rule_id'] for e in value['proposed_edits']}, {ARGS})
                self.assertEqual(value['hashes']['reference_catalog'], digest(CATALOG_RAW))
                self.assertEqual(value['package_evidence'][0]['import_path'], PROJECT_REFERENCES)
                self.assertFalse(value['coverage']['complete'] or value['compiler_invoked'] or value['applied_fixes'])
                self.assertEqual(target.read_bytes(), source)
                self.assertEqual(catalog_path.read_bytes(), CATALOG_RAW)
                self.assertEqual(output.read_bytes(), b'old output')
                self.assertFalse((root / 'compiler-ran').exists())

    def test_report_lists_wired_pack_backends_without_claiming_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for pack in ('core', 'pde'):
                config = root / '.typst-lint.toml'
                config.write_text(f'rule-set="{pack}"\n')
                original = config.read_bytes()
                value = report(root, 'check')
                coverage = value['coverage']
                self.assertEqual(coverage['available_backends'], list(rule_ids(pack)))
                self.assertIn(ARGS, coverage['available_backends'])
                self.assertIn(TARGET, coverage['available_backends'])
                self.assertEqual(coverage['implemented'], [])
                self.assertEqual(coverage['evaluated'], [])
                self.assertEqual(coverage['files'], [])
                self.assertFalse(coverage['complete'])
                self.assertEqual(value['status'], 2)
                self.assertFalse(value['applied_fixes'] or value['compiler_invoked'])
                self.assertEqual(config.read_bytes(), original)


class ReferencePackageTests(unittest.TestCase):
    def test_only_reviewed_definition_and_manifest_are_loaded_never_runtime_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            package = root / 'local/project-references/0.1.0'
            package.mkdir(parents=True)
            for filename in ('lib.typ', 'typst.toml'):
                shutil.copyfile(REPO / 'local/project-references/0.1.0' / filename, package / filename)
            # A symlink is deliberately unsafe to read. Loading must not touch
            # this runtime dependency or evaluate the function that names it.
            (package / 'projects.json').symlink_to(root / 'missing')
            result = load_packages(root, (PROJECT_REFERENCES,))
            self.assertEqual(len(result.sources), 1)
            self.assertFalse(result.unavailable)
            source = (package / 'lib.typ').read_bytes()
            (package / 'lib.typ').write_bytes(source + b'\n')
            changed = load_packages(root, (PROJECT_REFERENCES,))
            self.assertFalse(changed.sources)
            self.assertEqual(changed.unavailable[0].code, 'unreviewed-package-source')
            (package / 'typst.toml').write_text('[package]\nname="project-references"\nversion="0.1.0"\nentrypoint="other.typ"\n')
            (package / 'lib.typ').write_bytes(b'\xff')
            changed = load_packages(root, (PROJECT_REFERENCES,))
            self.assertFalse(changed.sources)
            self.assertEqual(changed.unavailable[0].code, 'unreviewed-package-metadata')


if __name__ == '__main__':
    unittest.main()
