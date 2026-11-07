import sys
from pathlib import Path
import unittest
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from syntax_client import parse
from static_resolver import PackageSource, MANNOT_NAMESPACE
from scoped_provenance import PACKAGE
from scoped_rules import analyze, CALL, DISPLAY, UNIQUE, REFERENCE
from edit_plan import plan

SOURCE = (TOOL.parents[1] / 'local/scoped-annotations/0.3.0/lib.typ').read_bytes()
NAMESPACE_FILE = TOOL / 'tests/fixtures/scoped-reference-packages/preview/mannot/0.4.0/src/lib.typ'
PACKAGES = (PackageSource(PACKAGE, 'reviewed-test-entrypoint', SOURCE),
            PackageSource(MANNOT_NAMESPACE, str(NAMESPACE_FILE), NAMESPACE_FILE.read_bytes()))
IMPORT = b'#import "@local/scoped-annotations:0.3.0": local-tag-scope, mannot-scope, annot-cetz-local\n'

class ScopedRulesTests(unittest.TestCase):
    def run_rules(self, body, packages=PACKAGES):
        return analyze(parse(IMPORT + body), 'main.typ', packages=packages)

    def test_real_callback_renamed_receiver_and_argument_trivia(self):
        body = b'#local-tag-scope(t => [#t.tag(/*keep*/ "x")])'
        result = self.run_rules(body)
        self.assertEqual(len(result[CALL].diagnostics), 1)
        candidate = plan(IMPORT+body, result[CALL].edits).candidate()
        self.assertIn(b'#(t.tag)(/*keep*/ "x")', candidate)
        self.assertFalse(analyze(parse(candidate), 'main.typ', packages=PACKAGES)[CALL].edits)

    def test_no_spelling_or_forged_source_proof(self):
        body = b'#let s = (tag: x => x)\n#s.tag("x")\n#local-tag-scope(t => [#t.tag("x")])'
        self.assertFalse(self.run_rules(body, ())[CALL].diagnostics)
        bad = (PackageSource(PACKAGE, 'wrong', SOURCE+b'// changed'),)
        self.assertFalse(self.run_rules(body, bad)[CALL].diagnostics)

    def test_shadow_and_deferred_closure(self):
        for body in (b'#local-tag-scope(s => { let s = (:); s.tag("x") })',
                     b'#local-tag-scope(s => { let f = () => s.tag("x"); [] })',
                     b'#local-tag-scope(s => { s = (:); s.tag("x") })'):
            self.assertFalse(self.run_rules(body)[CALL].diagnostics, body)

    def test_display_ancestor_and_scope_fields(self):
        body = b'#mannot-scope(s => [$ x #(s.annot)("x", c, d) $ #(s.annot)("x", c, d)])'
        self.assertEqual(len(self.run_rules(body)[DISPLAY].diagnostics), 1)
        self.assertEqual(len(self.run_rules(b'#annot-cetz-local(x,c,d)')[DISPLAY].diagnostics), 1)
        self.assertFalse(self.run_rules(b'#local-tag-scope(s => [#(s.annot)("x",c,d)])')[DISPLAY].diagnostics)

    def test_reference_exact_repair_and_shadowed_builtin(self):
        body = b'#local-tag-scope(s => [#ref((s.tag)("x"))])'
        result = self.run_rules(body)[REFERENCE]
        self.assertEqual(len(result.diagnostics), 1)
        self.assertEqual(plan(IMPORT + body, result.edits).candidate(),
                         IMPORT + b'#local-tag-scope(s => [#(s.ref)("x")])')
        self.assertFalse(self.run_rules(b'#let ref = x => x\n'+body)[REFERENCE].diagnostics)

    def test_literal_prefix_and_parent_identity(self):
        body = (b'#local-tag-scope(s => [], prefix: "p")\n'
                b'#local-tag-scope(t => [], prefix: "p")\n'
                b'#mannot-scope(s => [#mannot-scope(t => [], parent: s, name: "n")'
                b'#mannot-scope(t => [], parent: s, name: "n")])')
        self.assertEqual(len(self.run_rules(body)[UNIQUE].diagnostics), 2)
        self.assertFalse(self.run_rules(b'#local-tag-scope(s => []) #local-tag-scope(s => [])')[UNIQUE].diagnostics)

    def test_partial_wildcard_and_unknown_callbacks(self):
        source = b'#import "@local/scoped-annotations:0.3.0": *\n#local-tag-scope(s => [#s.tag("x")])'
        self.assertFalse(analyze(parse(source), 'm.typ', packages=PACKAGES)[CALL].diagnostics)
        for body in (b'#local-tag-scope((s,t) => [#s.tag("x")])',
                     b'#local-tag-scope(s => [#s.tag("x")], ..options)',
                     b'#include "other.typ"\n#local-tag-scope(s => [#s.tag("x")])'):
            self.assertFalse(self.run_rules(body)[CALL].diagnostics)

    def test_distinct_parents_shadowed_parent_and_mutation(self):
        body = (b'#mannot-scope(s => [#mannot-scope(t => [], parent: s, name: "n")])'
                b'#mannot-scope(s => [#mannot-scope(t => [], parent: s, name: "n")])')
        self.assertFalse(self.run_rules(body)[UNIQUE].diagnostics)
        body = (b'#mannot-scope(s => { let s = (:); '
                b'mannot-scope(t => [], parent: s, name: "n"); '
                b'mannot-scope(t => [], parent: s, name: "n") })')
        self.assertFalse(self.run_rules(body)[UNIQUE].diagnostics)
        self.assertFalse(self.run_rules(
            b'#mannot-scope(s => { s.tag = x => x; s.tag("x") })')[CALL].diagnostics)

    def test_unreviewed_mannot_does_not_acquire_provenance(self):
        body = (b'#import "@preview/mannot:0.4.0": annot, mark\n'
                b'#mannot-scope(s => [#annot(x) $ mark(x, tag: #<x>) $])')
        self.assertFalse(self.run_rules(body)[DISPLAY].diagnostics)
        body = b'#import "@preview/mannot:0.4.0": *\n#mannot-scope(s => [#s.tag("x")])'
        self.assertFalse(self.run_rules(body)[CALL].diagnostics)

    def test_namespace_requires_proven_output_ownership(self):
        examples = (
            b'#let a = [#local-tag-scope(s => [], prefix: "p")]\n'
            b'#let b = [#local-tag-scope(s => [], prefix: "p")]',
            b'#unknown([#local-tag-scope(s => [], prefix: "p")], '
            b'[#local-tag-scope(s => [], prefix: "p")])',
            b'#local-tag-scope(s => { let a = [#local-tag-scope(t => [], prefix: "p")]; '
            b'let b = [#local-tag-scope(t => [], prefix: "p")]; [] })',
            b'#local-tag-scope(s => [], prefix: "p")\n'
            b'#let b = [#local-tag-scope(s => [], prefix: "p")]',
        )
        for body in examples:
            result = self.run_rules(body)[UNIQUE]
            self.assertFalse(result.diagnostics, body)
            self.assertTrue(any('ownership is unproven' in skip for skip in result.skips))

    def test_suppression_and_comment_preservation(self):
        body = b'#local-tag-scope(s => [\n// typst-lint: ignore-next core.scoped-function-call -- test\n#s.tag("x")\n])'
        result = self.run_rules(body)[CALL]
        self.assertFalse(result.edits)
        self.assertEqual(len(result.suppressed), 1)

if __name__ == '__main__': unittest.main()
