"""Exact reference repair and refusals from the real AST and reviewed package."""
import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import Edit, plan
from scoped_provenance import PACKAGE
from scoped_rules import analyze, CALL, REFERENCE, UNIQUE
from source_analysis import analyze as combined
from static_resolver import (PackageSource, SCOPED_ANNOTATIONS_SOURCE_SHA256,
                             MANNOT_NAMESPACE, MANNOT_NAMESPACE_SOURCE_SHA256)
from syntax_client import parse
from typst_lint import digest

SOURCE = (REPO / 'local/scoped-annotations/0.3.0/lib.typ').read_bytes()
NAMESPACE_ROOT = TOOL / 'tests/fixtures/scoped-reference-packages'
NAMESPACE_FILE = NAMESPACE_ROOT / 'preview/mannot/0.4.0/src/lib.typ'
SCOPED_ONLY = (PackageSource(PACKAGE, 'reviewed-test-entrypoint', SOURCE),)
PACKAGES = (*SCOPED_ONLY, PackageSource(MANNOT_NAMESPACE, str(NAMESPACE_FILE), NAMESPACE_FILE.read_bytes()))


def materialize_packages(root):
    shutil.copytree(REPO / 'local/scoped-annotations/0.3.0', root / 'local/scoped-annotations/0.3.0')
    shutil.copytree(NAMESPACE_ROOT / 'preview/mannot/0.4.0', root / 'preview/mannot/0.4.0')

IMPORT = (b'#import "@local/scoped-annotations:0.3.0": '
          b'local-tag-scope, local-scope-annotations, mannot-scope\n')


class ScopedReferenceRepairTests(unittest.TestCase):
    def analyze(self, body, *, prefix=IMPORT, packages=PACKAGES):
        source = prefix + body
        return source, analyze(parse(source), 'main.typ', packages=packages)[REFERENCE]

    def assert_candidate(self, body, expected, *, prefix=IMPORT):
        source, result = self.analyze(body, prefix=prefix)
        self.assertTrue(result.edits, result.skips)
        candidate = plan(source, result.edits).candidate()
        self.assertEqual(candidate, prefix + expected)
        self.assertFalse(parse(candidate).nodes[0].errors)
        second = analyze(parse(candidate), 'main.typ', packages=PACKAGES)[REFERENCE]
        self.assertFalse(second.diagnostics or second.edits)
        # The full dispatcher must retain exactly the same permitted edits.
        whole = combined(parse(source), 'main.typ', packages=PACKAGES)
        self.assertEqual(plan(source, [e for r in whole.values() for e in r.edits]).candidate(), candidate)
        again = combined(parse(candidate), 'main.typ', packages=PACKAGES)
        self.assertFalse(any(r.edits for r in again.values()))
        self.assertEqual(again, combined(parse(candidate), 'main.typ', packages=PACKAGES))
        return result

    def test_exact_replacement_retains_markup_hash_and_code_context(self):
        for wrapper in (b'local-tag-scope', b'local-scope-annotations', b'mannot-scope'):
            with self.subTest(wrapper=wrapper):
                self.assert_candidate(b'#' + wrapper + b'(s => [Before #ref((s.tag)("energy")) after.])',
                                      b'#' + wrapper + b'(s => [Before #(s.ref)("energy") after.])')
                self.assert_candidate(b'#' + wrapper + b'(s => { ref((s.tag)("energy")) })',
                                      b'#' + wrapper + b'(s => { (s.ref)("energy") })')

    def test_renamed_import_module_alias_and_callback_receiver(self):
        cases = (
            (b'#import "@local/scoped-annotations:0.3.0": local-tag-scope as scope\n', b'scope'),
            (b'#import "@local/scoped-annotations:0.3.0" as scoped\n', b'scoped.local-tag-scope'),
            (b'#import "@local/scoped-annotations:0.3.0" as scoped\n#let scope = scoped.local-tag-scope\n', b'scope'),
        )
        for prefix, wrapper in cases:
            with self.subTest(prefix=prefix):
                self.assert_candidate(b'#' + wrapper + b'(actual => [#ref((actual.tag)("x"))])',
                                      b'#' + wrapper + b'(actual => [#(actual.ref)("x")])', prefix=prefix)

    def test_receiver_and_builtin_aliases_keep_their_original_bindings(self):
        body = (b'#let original-ref = ref\n#let ref = x => x\n'
                b'#local-tag-scope(s => { let actual = s; let s = (:); '
                b'original-ref((actual.tag)("x")) })')
        self.assert_candidate(body, body.replace(b'original-ref((actual.tag)("x"))', b'(actual.ref)("x")'))

    def test_nested_scopes_reuse_only_the_receiver_of_each_original_target(self):
        body = (b'#local-tag-scope(outer => [#mannot-scope(inner => ['
                b'#ref((outer.tag)("x")) and #ref((inner.tag)("x"))])])')
        expected = body.replace(b'ref((outer.tag)("x"))', b'(outer.ref)("x")').replace(
            b'ref((inner.tag)("x"))', b'(inner.ref)("x")')
        result = self.assert_candidate(body, expected)
        self.assertEqual(len(result.edits), 2)

    def test_unicode_bom_crlf_and_literal_bytes_survive(self):
        prefix = b'\xef\xbb\xbf' + IMPORT.replace(b'\n', b'\r\n')
        body = ('#local-tag-scope(범위 => [앞 #ref((범위.tag)(\r\n  "에너지",\r\n)) 뒤.])\r\n').encode()
        expected = body.replace('ref((범위.tag)(\r\n  "에너지",\r\n))'.encode(),
                                '(범위.ref)(\r\n  "에너지",\r\n)'.encode())
        result = self.assert_candidate(body, expected, prefix=prefix)
        self.assertEqual((result.diagnostics[0].line, result.diagnostics[0].column), (2, 28))
        for literal in (b'"energy\\u{2d}x"', b'"/* literal, not a comment */"', b'""'):
            body = b'#local-tag-scope(s => [#ref((s.tag)(' + literal + b'))])'
            self.assert_candidate(body, body.replace(b'ref((s.tag)(', b'(s.ref)(').replace(b'))])', b')])'))

    def test_shadowed_unknown_and_unreviewed_apis_never_gain_repairs(self):
        cases = (
            b'#let ref = x => x\n#local-tag-scope(s => [#ref((s.tag)("x"))])',
            b'#local-tag-scope(s => { let s = (tag: x => x); ref((s.tag)("x")) })',
            b'#let local-tag-scope = f => f((tag: x => x))\n#local-tag-scope(s => [#ref((s.tag)("x"))])',
            b'#import "unknown.typ": *\n#local-tag-scope(s => [#ref((s.tag)("x"))])',
            b'#local-tag-scope(s => { let delayed = () => ref((s.tag)("x")); [] })',
            b'#local-tag-scope(s => [#context ref((s.tag)("x"))])',
        )
        for body in cases:
            with self.subTest(body=body):
                _, result = self.analyze(body)
                self.assertFalse(result.diagnostics or result.edits)
        for packages in ((), (PackageSource(PACKAGE, 'changed', SOURCE + b'// changed'),)):
            _, result = self.analyze(b'#local-tag-scope(s => [#ref((s.tag)("x"))])', packages=packages)
            self.assertFalse(result.diagnostics or result.edits)
            self.assertTrue(any('verified direct-package contract' in reason for reason in result.skips))

    def test_missing_or_changed_namespace_is_diagnostic_only(self):
        body = b'#local-tag-scope(s => [#ref((s.tag)("x"))])'
        for packages in (SCOPED_ONLY, (*SCOPED_ONLY, PackageSource(
                MANNOT_NAMESPACE, 'changed-entrypoint', NAMESPACE_FILE.read_bytes() + b'#let ref = x => x\n'))):
            with self.subTest(packages=len(packages)):
                _, result = self.analyze(body, packages=packages)
                self.assertEqual(len(result.diagnostics), 1)
                self.assertFalse(result.edits)
                self.assertTrue(any('exclude ref/label shadowing' in s for s in result.skips))

    def test_config_ignored_reference_never_proposes_a_repair(self):
        source = IMPORT + b'#local-tag-scope(s => [#ref((s.tag)("x"))])'
        result = combined(parse(source), 'main.typ', packages=PACKAGES, ignored=frozenset((REFERENCE,)))
        self.assertFalse(any(r.edits for r in result.values()))

    def test_options_dynamic_targets_and_name_forms_are_diagnostic_only(self):
        cases = (
            (b'ref((s.tag)("x"), supplement: [Equation])', 'ref options'),
            (b'ref((s.tag)("x"), "extra")', 'extra arguments'),
            (b'ref((s.tag)("x"), ..options)', 'spread'),
            (b'ref((s.tag)(target))', 'literal local target'),
            (b'ref((s.tag)("x" + "y"))', 'literal local target'),
            (b'ref((s.tag)("x", "y"))', 'tag options'),
            (b'ref((s.tag)("x", ..options))', 'tag options'),
            (b'ref((s.name)("x"))', 'generated-name'),
        )
        for expression, reason in cases:
            with self.subTest(expression=expression):
                source, result = self.analyze(b'#local-tag-scope(s => [#' + expression + b'])')
                self.assertEqual(len(result.diagnostics), 1)
                self.assertFalse(result.edits)
                self.assertTrue(any(REFERENCE + ' repair refused:' in s and reason in s for s in result.skips))
                self.assertEqual(plan(source, result.edits).candidate(), source)

    def test_function_alias_is_diagnostic_but_does_not_guess_a_receiver(self):
        body = b'#local-tag-scope(s => { let target = s.tag; let s = (:); ref(target("x")) })'
        _, result = self.analyze(body)
        self.assertEqual(len(result.diagnostics), 1)
        self.assertFalse(result.edits)
        self.assertTrue(any('function aliases are unsupported' in reason for reason in result.skips))

    def test_comments_and_outer_newlines_are_never_deleted_or_reassigned(self):
        expressions = (
            b'ref(/* keep */ (s.tag)("x"))', b'ref((s.tag)(/* keep */ "x"))',
            b'ref((s./* keep */tag)("x"))', b'ref((s.tag)("x") /* keep */)',
            b'ref((s.tag)("x") // keep\n)', b'ref(\r\n(s.tag)("x"))',
        )
        for expression in expressions:
            with self.subTest(expression=expression):
                source, result = self.analyze(b'#local-tag-scope(s => [#' + expression + b'])')
                self.assertEqual(len(result.diagnostics), 1)
                self.assertFalse(result.edits)
                self.assertTrue(any(REFERENCE + ' repair refused:' in s for s in result.skips))
                self.assertEqual(plan(source, result.edits).candidate(), source)
        body = b'#local-tag-scope(s => [/* before */ #ref((s.tag)("x")) /* after */])'
        self.assert_candidate(body, body.replace(b'ref((s.tag)("x"))', b'(s.ref)("x")'))

    def test_suppression_covers_the_original_call_without_moving_a_directive(self):
        body = (b'#local-tag-scope(s => [\n'
                b'// typst-lint: ignore-next core.scoped-reference-access -- keep this one\n'
                b'#ref((s.tag)("x"))\n#ref((s.tag)("y"))\n])')
        source, result = self.analyze(body)
        self.assertEqual(len(result.suppressed), 1)
        self.assertEqual(len(result.edits), 1)
        candidate = plan(source, result.edits).candidate()
        self.assertEqual(candidate, source.replace(b'ref((s.tag)("y"))', b'(s.ref)("y")'))
        whole = combined(parse(source), 'main.typ', packages=PACKAGES)
        self.assertEqual(whole[REFERENCE].edits, result.edits)
        final = combined(parse(candidate), 'main.typ', packages=PACKAGES)
        self.assertFalse(any(r.edits for r in final.values()))
        self.assertEqual(len(final[REFERENCE].suppressed), 1)

    def test_nonparenthesized_tag_requires_reparsed_second_rule_proof(self):
        source = IMPORT + b'$x^i$\n#local-tag-scope(s => [#ref(s.tag("x"))])'
        individual = analyze(parse(source), 'main.typ', packages=PACKAGES)
        self.assertTrue(individual[CALL].edits)
        self.assertFalse(individual[REFERENCE].edits)
        self.assertTrue(any('direct parenthesized' in reason for reason in individual[REFERENCE].skips))
        whole = combined(parse(source), 'main.typ', packages=PACKAGES)
        candidate = plan(source, [e for r in whole.values() for e in r.edits]).candidate()
        self.assertEqual(candidate, IMPORT + b'$x^(i)$\n#local-tag-scope(s => [#(s.ref)("x")])')
        self.assertEqual(whole[CALL].repair_passes, 2)
        self.assertFalse(any(r.edits for r in combined(parse(candidate), 'main.typ', packages=PACKAGES).values()))

    def test_mutation_dynamic_execution_and_parse_errors_withhold_all_proof(self):
        for body in (b'#local-tag-scope(s => { s.ref = x => x; ref((s.tag)("x")) })',
                     b'#local-tag-scope(s => { if true { [] }; ref((s.tag)("x")) })',
                     b'#include "other.typ"\n#local-tag-scope(s => [#ref((s.tag)("x"))])',
                     b'#local-tag-scope(s => [#ref((s.tag)("x"))]'):
            _, result = self.analyze(body)
            self.assertFalse(result.diagnostics or result.edits)
            self.assertTrue(any('scope proofs withheld' in reason for reason in result.skips))

    def test_raw_and_strings_are_not_generated_reference_calls(self):
        _, result = self.analyze(b'`ref((s.tag)("x"))`\n#let text = "ref((s.tag)(x))"')
        self.assertFalse(result.diagnostics or result.edits)


class ScopedReferenceCliTests(unittest.TestCase):
    def test_actual_cli_missing_optional_namespace_keeps_other_proposals(self):
        source = IMPORT + b'#local-tag-scope(s => [#s.tag("x") #ref((s.tag)("x"))])'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / 'main.typ'
            target.write_bytes(source)
            command = [sys.executable, str(TOOL / 'typst-lint'), 'fix', '--root', str(root),
                       '--package-path', str(REPO), '--format', 'json']
            process = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, 2, process.stderr)
            value = json.loads(process.stdout)
            self.assertEqual({e['rule_id'] for e in value['proposed_edits']}, {CALL})
            self.assertEqual({d['rule_id'] for d in value['diagnostics']}, {CALL, REFERENCE})
            missing = next(u for u in value['package_unavailable'] if u['import_path'] == MANNOT_NAMESPACE)
            self.assertEqual(missing['code'], 'missing-optional-namespace')
            edits = [Edit(e['start'], e['end'], e['replacement'].encode()) for e in value['proposed_edits']]
            self.assertEqual(plan(source, edits).candidate(), source.replace(b'#s.tag("x")', b'#(s.tag)("x")'))
            self.assertEqual(target.read_bytes(), source)
            self.assertFalse(value['compiler_invoked'] or value['applied_fixes'])

    def test_actual_modes_compose_and_recheck_without_writes_or_compilation(self):
        source = (b'\xef\xbb\xbf' + IMPORT.replace(b'\n', b'\r\n') +
                  '한글 $x^i$\r\n'.encode() +
                  b'#local-tag-scope(s => [#s.tag("x") #ref((s.tag)("x"))], prefix: "p")\r\n'
                  b'#local-tag-scope(t => [], prefix: "p")\r\n'
                  b'// typst-lint: ignore-next core.scoped-reference-access -- retain options\r\n'
                  b'#mannot-scope(s => [#ref((s.tag)("y"), supplement: [Y])])\r\n')
        expected = source.replace(b'$x^i$', b'$x^(i)$').replace(b'#s.tag("x")', b'#(s.tag)("x")').replace(
            b'ref((s.tag)("x"))', b'(s.ref)("x")')
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            root, packages = base / 'consumer', base / 'packages'
            root.mkdir()
            materialize_packages(packages)
            target, output = root / 'main.typ', root / 'output.pdf'
            target.write_bytes(source)
            output.write_bytes(b'original output')
            proposals = None
            for mode, dry_run in (('check', False), ('fix', True), ('fix', False), ('compile', False)):
                launcher = 'typst-style' if mode == 'compile' else 'typst-lint'
                command = [sys.executable, str(TOOL / launcher), mode, '--root', str(root),
                           '--package-path', str(packages), '--format', 'json']
                if dry_run: command += ['--dry-run']
                if mode == 'compile': command += ['--', 'main.typ', 'output.pdf']
                process = subprocess.run(command, capture_output=True, text=True, timeout=30)
                self.assertEqual(process.returncode, 2, process.stderr)
                value = json.loads(process.stdout)
                self.assertFalse(value['compiler_invoked'] or value['applied_fixes'] or value['coverage']['complete'])
                self.assertEqual(value['proposal_status'], 'partial-not-applicable-transaction')
                self.assertEqual({d['rule_id'] for d in value['diagnostics']},
                                 {'core.parenthesized-scripts', CALL, REFERENCE, UNIQUE})
                self.assertEqual(value['suppressed_by_rule'].get(REFERENCE), 1)
                evidence = next(e for e in value['package_evidence'] if e['import_path'] == PACKAGE)
                self.assertEqual(evidence['source_sha256'], SCOPED_ANNOTATIONS_SOURCE_SHA256)
                namespace = next(e for e in value['package_evidence'] if e['import_path'] == MANNOT_NAMESPACE)
                self.assertEqual(namespace['source_sha256'], MANNOT_NAMESPACE_SOURCE_SHA256)
                self.assertEqual({e['rule_id'] for e in value['proposed_edits']},
                                 {'core.parenthesized-scripts', CALL, REFERENCE})
                self.assertTrue(all(e['baseline_sha256'] == digest(source) for e in value['proposed_edits']))
                edits = [Edit(e['start'], e['end'], e['replacement'].encode()) for e in value['proposed_edits']]
                self.assertEqual(plan(source, edits).candidate(), expected)
                if proposals is not None: self.assertEqual(edits, proposals)
                proposals = edits
                self.assertEqual(target.read_bytes(), source)
                self.assertEqual(output.read_bytes(), b'original output')
            target.write_bytes(expected)  # Only this test's temporary input.
            command = [sys.executable, str(TOOL / 'typst-lint'), 'check', '--root', str(root),
                       '--package-path', str(packages), '--format', 'json']
            final = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(final.returncode, 2, final.stderr)
            checked = json.loads(final.stdout)
            self.assertFalse(checked['proposed_edits'])
            self.assertEqual({d['rule_id'] for d in checked['diagnostics']}, {UNIQUE})
            self.assertEqual(checked['suppressed_by_rule'].get(REFERENCE), 1)
            self.assertEqual(target.read_bytes(), expected)
            self.assertEqual(output.read_bytes(), b'original output')


if __name__ == '__main__':
    unittest.main()
