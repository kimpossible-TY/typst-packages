"""Parent scope proof remains diagnostic-only and fails closed."""
from dataclasses import replace
from pathlib import Path
import json
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_modern_highlight as modern
import document_cli
import scoped_parent_transfer_prototype as proof
import document_modern_highlight_preview as preview
from typst_lint import InputError, SafetyError
from test_document_modern_highlight_scoped_preview import ScopedReferencePreviewTests
import test_conditional_modern_highlight as fixtures


@unittest.skipUnless(fixtures.EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ParentTransferPrototypeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ScopedReferencePreviewTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def issue(self, body, *, facade=False):
        if facade:
            (self.fixture.fixture.project / 'styles.typ').write_bytes(
                b'#import "@local/scoped-annotations:0.3.0": *\n')
            prefix = b'#import "styles.typ": *\n'
        else:
            prefix = b'#import "@local/scoped-annotations:0.3.0": *\n'
        source = fixtures.IMPORT + prefix + body
        session, context = self.fixture.fixture.session(source)
        analyzer = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)
        tree = context.source_for('main.typ').tree
        diagnostics = analyzer.analyze(tree, 'main.typ')['diagnostics']
        return source, analyzer, diagnostics

    def issue_theme(self, context, prefix=b''):
        package = TOOL / 'tests/fixtures/reviewed-packages/local/math-blocks/0.2.0'
        dest = self.fixture.fixture.packages / 'local/math-blocks/0.2.0'
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copytree(package, dest)
        body = (b'#import "@local/math-blocks:0.2.0": *\n' + prefix
                + b'#local-tag-scope(s => [\n'
                + b'#mannot-scope(m => [#(m.tag)("a")], parent: s, name: "one")\n'
                + context + b'\n#highlighted[See #(s.ref)("a") $x_1$ end.]])')
        return self.issue(body)

    def test_context_palette_reissues_binding_and_independent_candidate(self):
        context = (b'#context { let theme = theme-from-text-fill()\n'
                   b'let tint = theme.callouts.proposition.border\n'
                   b'local-scope-annotations(m => [#(m.tag)("b")], parent: s, name: "two") }')
        source, analyzer, diagnostics = self.issue_theme(context)
        start = diagnostics[0]['start']
        baseline = preview.preview_all(analyzer, allow_direct_references=True,
                                       allow_scoped_references=True, reviewed_math=True)
        result = proof.analyze_one(analyzer, 'main.typ', start)
        self.assertEqual(result['theme_contexts'], 1)
        self.assertEqual(result['theme_selector_route'][0]['imported_name'], 'theme-from-text-fill')
        self.assertEqual(len(result['parents']), 2)
        candidate = proof.preview_one(analyzer, 'main.typ', start)
        self.assertEqual(len(candidate['edits']), 1)
        self.assertFalse(candidate['execution_proved'] or candidate['render_equivalence_proved'])
        self.assertEqual(baseline, preview.preview_all(analyzer, allow_direct_references=True,
                                                       allow_scoped_references=True, reviewed_math=True))
        self.assertEqual((self.fixture.fixture.project / 'main.typ').read_bytes(), source)
        with self.assertRaisesRegex(SafetyError, 'budget'):
            proof.analyze_one(analyzer, 'main.typ', start, max_work=result['work_used'] - 1)

    def test_context_palette_shadow_escape_and_tampering_are_refused(self):
        good = (b'#context { let theme = theme-from-text-fill()\n'
                b'let tint = theme.callouts.proposition.border\n'
                b'local-scope-annotations(m => [#(m.tag)("b")], parent: s, name: "two") }')
        bad = (
            good.replace(b'let tint', b'let s'),
            good.replace(b'let tint', b'let local-scope-annotations'),
            good.replace(b'theme-from-text-fill()', b'theme-from-text-fill(s)'),
            good.replace(b'theme.callouts.proposition.border', b'unknown(s)'),
            good.replace(b'#(m.tag)("b")', b'#unknown(m)'),
            good.replace(b'let tint =', b'let theme ='),
        )
        for context in bad:
            with self.subTest(context=context):
                _, analyzer, diagnostics = self.issue_theme(context)
                with self.assertRaises((InputError, SafetyError)):
                    proof.analyze_one(analyzer, 'main.typ', diagnostics[0]['start'])
        _, analyzer, diagnostics = self.issue_theme(good, b'#let theme-from-text-fill() = 1\n')
        with self.assertRaises((InputError, SafetyError)):
            proof.analyze_one(analyzer, 'main.typ', diagnostics[0]['start'])
        original = proof._prove_tree
        _, analyzer, diagnostics = self.issue_theme(good)
        def tamper(*args, **kwargs):
            result = original(*args, **kwargs)
            if kwargs.get('graph_override') is not None:
                result['theme_selector_route'][0]['source_sha256'] = '0' * 64
            return result
        with patch.object(proof, '_prove_tree', side_effect=tamper):
            with self.assertRaisesRegex(SafetyError, 'theme selector binding route changed'):
                proof.preview_one(analyzer, 'main.typ', diagnostics[0]['start'])

    def test_context_selector_facade_shadow_is_refused(self):
        # Install the reviewed math-blocks fixture, then exercise an actual
        # wildcard facade rather than a direct root import.
        self.issue_theme(b'')
        facade = self.fixture.fixture.project / 'palette.typ'
        source = (fixtures.IMPORT + b'#import "@local/scoped-annotations:0.3.0": *\n'
                  b'#import "palette.typ": *\n#local-tag-scope(s => ['
                  b'#mannot-scope(m => [#(m.tag)("a")], parent: s, name: "one")\n'
                  b'#context { let theme = theme-from-text-fill()\n figure([]) }\n'
                  b'#highlighted[See #(s.ref)("a") $x$ end.]])')
        facade.write_bytes(b'#import "@local/math-blocks:0.2.0": *\n')
        session, context = self.fixture.fixture.session(source)
        analyzer = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)
        start = analyzer.analyze(context.source_for('main.typ').tree, 'main.typ')['diagnostics'][0]['start']
        self.assertTrue(any(step['request'] == 'palette.typ' for step in
                            proof.analyze_one(analyzer, 'main.typ', start)['theme_selector_route']))
        facade.write_bytes(facade.read_bytes() + b'#let theme-from-text-fill() = 1\n')
        session, context = self.fixture.fixture.session(source)
        analyzer = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)
        with self.assertRaisesRegex(SafetyError, 'Theme selector root binding is unresolved'):
            proof.analyze_one(analyzer, 'main.typ', start)
    def test_literal_tag_captured_child_helper_is_checked(self):
        body = (b'#local-tag-scope(s => [#mannot-scope(m => [\n'
                b'#let markchild(body) = { set math.equation(numbering: none)\n'
                b'markub(body, tag: (m.tag)("x"), bracket: sym.brace.b) }\n'
                b'$markchild(x)$], parent: s, name: "one")\n'
                b'#highlighted[See #(s.ref)("a") $x$ end.]])')
        source, analyzer, diagnostics = self.issue(body)
        start = diagnostics[0]['start']
        proof_result = proof.analyze_one(analyzer, 'main.typ', start)
        self.assertEqual(proof_result['captured_child_helpers'], 1)
        self.assertEqual(proof_result['child_scope_fields']['tag'], 1)
        result = proof.preview_one(analyzer, 'main.typ', start)
        self.assertEqual(len(result['edits']), 1)
        self.assertFalse(result['execution_proved'] or result['render_equivalence_proved'])
        self.assertEqual((self.fixture.fixture.project / 'main.typ').read_bytes(), source)
        with self.assertRaisesRegex(SafetyError, 'budget'):
            proof.analyze_one(analyzer, 'main.typ', start, max_work=proof_result['work_used'] - 1)
        self.assertEqual(proof.preview_one(analyzer, 'main.typ', start, max_work=result['work_used']), result)
        with self.assertRaisesRegex(SafetyError, 'budget'):
            proof.preview_one(analyzer, 'main.typ', start, max_work=result['work_used'] - 1)
        original = proof._prove_tree
        def tamper(*args, **kwargs):
            checked = original(*args, **kwargs)
            if kwargs.get('graph_override') is not None:
                checked['captured_child_helpers'] = 0
            return checked
        with patch.object(proof, '_prove_tree', side_effect=tamper):
            with self.assertRaisesRegex(SafetyError, 'lexical identities changed'):
                proof.preview_one(analyzer, 'main.typ', start)

    def test_captured_child_helper_escape_shadow_dynamic_tag_and_tampering_refused(self):
        template = (b'#local-tag-scope(s => [#mannot-scope(m => [\n'
                    b'#let markchild(body) = { set math.equation(numbering: none)\n'
                    b'markub(body, tag: (m.tag)("x"), bracket: sym.brace.b) }\n'
                    b'$markchild(x)$%s], parent: s, name: "one")\n'
                    b'#highlighted[See #(s.ref)("a") $x$ end.]])')
        bad = (template % b'#unknown(markchild)',
               (template % b'').replace(b'(m.tag)("x")', b'(m.tag)(compute())'),
               (template % b'').replace(b'markchild(body)', b'markchild(m)'),
               (template % b'').replace(b'markub(body,', b'markub(m,'),
               (template % b'').replace(b'$markchild(x)$', b'#unknown(markchild)'),
               (template % b'').replace(b'$markchild(x)$', b'$markchild(#m)$'),
               (template % b'').replace(b'$markchild(x)$', b'#let m = 1\n$markchild(x)$'),
               (template % b'').replace(b'$markchild(x)$', b'#(m.tag = x => x)\n$markchild(x)$'),
               (template % b'').replace(b'markchild(body)', b'mannot-scope(body)'))
        for body in bad:
            with self.subTest(body=body):
                _, analyzer, diagnostics = self.issue(body)
                if diagnostics:
                    with self.assertRaises((InputError, SafetyError)):
                        proof.analyze_one(analyzer, 'main.typ', diagnostics[0]['start'])

    def test_many_captured_helpers_are_charged_and_bounded(self):
        declarations = b''.join(
            (b'#let f%d(body) = { set math.equation(numbering: none)\n'
             b'markub(body, tag: (m.tag)("x"), bracket: sym.brace.b) }\n'
             b'$f%d(x)$\n') % (i, i) for i in range(24))
        body = (b'#local-tag-scope(s => [#mannot-scope(m => [\n' + declarations
                + b'], parent: s, name: "one")\n#highlighted[See #(s.ref)("a") $x$ end.]])')
        _, analyzer, diagnostics = self.issue(body)
        start = diagnostics[0]['start']
        result = proof.analyze_one(analyzer, 'main.typ', start)
        self.assertEqual(result['captured_child_helpers'], 24)
        self.assertEqual(proof.analyze_one(analyzer, 'main.typ', start, max_work=result['work_used']), result)
        with self.assertRaisesRegex(SafetyError, 'budget'):
            proof.analyze_one(analyzer, 'main.typ', start, max_work=result['work_used'] - 1)

    def test_direct_facade_route_and_outer_s_inside_child(self):
        body = (b'#local-tag-scope(s => [\n'
                b'#mannot-scope(m => [#(m.tag)("child") and #(s.ref)("outer")], parent: s, name: "one")\n'
                b'#highlighted[See #(s.ref)("outer") $x$ end.]\n])')
        for facade in (False, True):
            with self.subTest(facade=facade):
                source, analyzer, diagnostics = self.issue(body, facade=facade)
                start = diagnostics[0]['start']
                baseline = preview.preview_all(analyzer, allow_direct_references=True,
                                               allow_scoped_references=True, reviewed_math=True)
                result = proof.analyze_one(analyzer, 'main.typ', start)
                self.assertEqual(result['status'], 'CONDITIONAL')
                parent, = result['parents']
                self.assertTrue(parent['parent_transfer'])
                self.assertEqual(parent['helper'], 'mannot-scope')
                self.assertEqual(parent['route'][0]['path'], '@local/scoped-annotations:0.3.0')
                if facade:
                    self.assertTrue(any(step['request'] == 'styles.typ' for step in parent['route']))
                self.assertEqual(result['outer_scope_fields']['ref'], 2)
                self.assertEqual(result['child_scope_fields']['tag'], 1)
                self.assertEqual(result['outer_scope_uses_inside_children'], 1)
                self.assertTrue(all(not result[k] for k in ('runtime_binding_proved', 'execution_proved',
                    'render_equivalence_proved', 'edit_permission', 'compiler_permission')))
                self.assertEqual(baseline, preview.preview_all(analyzer, allow_direct_references=True,
                                                               allow_scoped_references=True, reviewed_math=True))
                self.assertEqual((self.fixture.fixture.project / 'main.typ').read_bytes(), source)

    def test_lexical_sibling_route_is_recorded_for_opaque_content(self):
        body = (b'#local-tag-scope(s => [\n'
                b'#mannot-scope(m => [#(m.tag)("a")], parent: s, name: "one")\n'
                b'#unknown[#mannot-scope(m => [#(m.tag)("b") and #(s.ref)("outer")], parent: s, name: "two")]\n'
                b'#highlighted[See #(s.ref)("outer") $x$ end.]\n])')
        _, analyzer, diagnostics = self.issue(body)
        result = proof.analyze_one(analyzer, 'main.typ', diagnostics[0]['start'])
        self.assertEqual([p['route_method'] for p in result['parents']], ['direct', 'same-callback-lexical'])
        self.assertEqual(result['parents'][1]['route_evidence_start'], result['parents'][0]['call_start'])
        self.assertEqual(result['outer_scope_uses_inside_children'], 1)

    def test_scope_shadow_escape_mutation_and_dynamic_constructs_refuse(self):
        cases = (
            b'#let s = (ref: x => x)\n',
            b'#unknown(s)\n',
            b'#unknown(s.ref)\n',
            b'#mannot-scope(m => [#unknown(m.tag)], parent: s, name: "bad")\n',
            b'#(s.ref)(compute())\n',
            b'#(s.ref)("x", extra: true)\n',
            b'$leak(#s)$\n',
            b'#mannot-scope(m => [$leak(#m)$], parent: s, name: "bad")\n',
            b'#(s.ref = x => x)\n',
            b'#if true [text]\n',
            b'#mannot-scope(m => [#unknown(m)], parent: s, name: "bad")\n',
            b'#mannot-scope(m => [#let m = 1], parent: s, name: "bad")\n',
            b'#mannot-scope(s => [text], parent: s, name: "bad")\n',
            b'#mannot-scope(m => [#unknown(s)], parent: s, name: "bad")\n',
        )
        child = b'#mannot-scope(m => [#(m.tag)("a")], parent: s, name: "one")\n'
        for insertion in cases:
            with self.subTest(insertion=insertion):
                body = b'#local-tag-scope(s => [\n' + insertion + child + b'#highlighted[See #(s.ref)("a") $x$ end.]])'
                _, analyzer, diagnostics = self.issue(body)
                if diagnostics:
                    with self.assertRaises((InputError, SafetyError)):
                        proof.analyze_one(analyzer, 'main.typ', diagnostics[0]['start'])

    def test_shadowed_helper_and_changed_package_are_not_proved(self):
        body = (b'#let mannot-scope(body, ..args) = panic("shadow")\n'
                b'#local-tag-scope(s => [#mannot-scope(m => [text], parent: s, name: "x")\n'
                b'#highlighted[See #(s.ref)("x") $x$ end.]])')
        _, analyzer, diagnostics = self.issue(body)
        with self.assertRaises((InputError, SafetyError)):
            proof.analyze_one(analyzer, 'main.typ', diagnostics[0]['start'])
        wrapper = self.fixture.fixture.packages / 'local/scoped-annotations/0.3.0/lib.typ'
        wrapper.write_bytes(wrapper.read_bytes() + b'\n// altered\n')
        source, analyzer, _ = self.issue(body)
        with self.assertRaisesRegex(SafetyError, 'Reviewed scoped wrapper'):
            proof.analyze_one(analyzer, 'main.typ', source.rindex(b'highlighted'))

    def test_candidate_reissues_parent_routes_and_preserves_reference(self):
        body = (b'#local-tag-scope(s => [#mannot-scope(m => [#(m.tag)("a") and #(s.ref)("a")], '
                b'parent: s, name: "one")\n#highlighted[See #(s.ref)("a") $x_1$ end.]])')
        source, analyzer, diagnostics = self.issue(body)
        result = proof.preview_one(analyzer, 'main.typ', diagnostics[0]['start'])
        self.assertEqual(result['verification'], 'conditional-parent-scoped-source-only')
        self.assertEqual(len(result['edits']), 1)
        self.assertIn('#(s.ref)("a")', result['edits'][0]['replacement'])
        self.assertFalse(result['runtime_binding_proved'] or result['execution_proved']
                         or result['render_equivalence_proved'] or result['edit_permission']
                         or result['compiler_permission'])
        self.assertEqual((self.fixture.fixture.project / 'main.typ').read_bytes(), source)
        self.assertEqual(proof.preview_one(analyzer, 'main.typ', diagnostics[0]['start'],
                                           max_work=result['work_used']), result)
        with self.assertRaisesRegex(SafetyError, 'budget'):
            proof.preview_one(analyzer, 'main.typ', diagnostics[0]['start'], max_work=result['work_used'] - 1)
        low, high = 1, proof.MAX_BYTES
        while low < high:
            middle = (low + high) // 2
            try:
                proof.preview_one(analyzer, 'main.typ', diagnostics[0]['start'], max_bytes=middle)
            except SafetyError as error:
                if 'byte budget' not in str(error):
                    raise
                low = middle + 1
            else:
                high = middle
        self.assertEqual(proof.preview_one(analyzer, 'main.typ', diagnostics[0]['start'], max_bytes=low), result)
        with self.assertRaisesRegex(SafetyError, 'byte budget'):
            proof.preview_one(analyzer, 'main.typ', diagnostics[0]['start'], max_bytes=low - 1)

    def test_candidate_route_and_ownership_tampering_are_withheld(self):
        body = (b'#local-tag-scope(s => [#mannot-scope(m => [#(m.tag)("a")], parent: s, name: "one")\n'
                b'#highlighted[See #(s.ref)("a") $x$ end.]])')
        _, analyzer, diagnostics = self.issue(body)
        start = diagnostics[0]['start']
        original = proof._prove_tree
        for change in ('route', 'owner'):
            def tamper(*args, **kwargs):
                result = original(*args, **kwargs)
                if kwargs.get('graph_override') is not None:
                    if change == 'route':
                        result['parents'][0]['route'][0]['source_sha256'] = '0' * 64
                    else:
                        result['parents'][0]['child_body_start'] += 1
                return result
            with self.subTest(change=change), patch.object(proof, '_prove_tree', side_effect=tamper):
                with self.assertRaisesRegex(SafetyError, 'route changed|ownership changed'):
                    proof.preview_one(analyzer, 'main.typ', start)

    def test_candidate_callback_parameter_owner_and_outer_route_are_replayed(self):
        body = (b'#local-tag-scope(s => [#mannot-scope(m => [#(m.tag)("a")], parent: s, name: "one")\n'
                b'#highlighted[See #(s.ref)("a") $x$ end.]])')
        source, analyzer, diagnostics = self.issue(body)
        start = diagnostics[0]['start']
        original_prove = proof._prove_tree
        for change in ('parameter', 'owner'):
            def tamper(*args, **kwargs):
                if kwargs.get('graph_override') is not None:
                    args = list(args)
                    tree, callback = args[1], args[3]
                    nodes = list(tree.nodes)
                    body_index = next(i for i, n in enumerate(nodes) if n.kind == 'ContentBlock'
                        and (n.start, n.end) == (callback['body_start'], callback['body_end']))
                    parents = {c: i for i, n in enumerate(nodes) for c in n.children}
                    closure = parents[body_index]
                    if change == 'parameter':
                        params = proof.graph.significant(tree, closure)[0]
                        param = proof.graph.significant(tree, params)[0]
                        nodes[param] = replace(nodes[param], kind='Text')
                    else:
                        owner = parents[closure]
                        nodes[owner] = replace(nodes[owner], children=())
                    args[1] = replace(tree, nodes=tuple(nodes))
                return original_prove(*args, **kwargs)
            with self.subTest(change=change), patch.object(proof, '_prove_tree', side_effect=tamper):
                with self.assertRaisesRegex(SafetyError, 'Outer callback parameter changed|Outer wrapper ownership changed'):
                    proof.preview_one(analyzer, 'main.typ', start)
        original_incoming = proof._Tracer.incoming
        def hide_outer(self, tree, index, path, active):
            result = original_incoming(self, tree, index, path, active)
            if path == 'main.typ' and tree.source != source:
                return replace(result, exports=tuple((name, None if name == 'local-tag-scope' else value)
                                                      for name, value in result.exports))
            return result
        with patch.object(proof._Tracer, 'incoming', hide_outer):
            with self.assertRaisesRegex(SafetyError, 'Outer wrapper route could not be replayed'):
                proof.preview_one(analyzer, 'main.typ', start)

    def test_cli_inspect_and_preview_preserve_baseline_and_limits(self):
        body = (b'#local-tag-scope(s => [#mannot-scope(m => [#(m.tag)("a") and #(s.ref)("a")], '
                b'parent: s, name: "one")\n#highlighted[See #(s.ref)("a") $x$ end.]])')
        source, _, diagnostics = self.issue(body)
        start = diagnostics[0]['start']
        common = dict(root=str(self.fixture.fixture.project), entrypoint='main.typ', rule_set='pde',
                      package_path=str(self.fixture.fixture.packages), footnote_cache_path=str(self.fixture.fixture.cache),
                      modern_highlight_evidence=True, preview_conditional_modern_combined_highlights=True)
        baseline = document_cli.report_document(**common)
        self.assertNotIn('conditional_scoped_parent_transfer', baseline)
        inspected = document_cli.report_document(**common, inspect_scoped_parent_transfer=('main.typ', start))
        self.assertEqual(inspected['conditional_modern_combined_previews'], baseline['conditional_modern_combined_previews'])
        evidence = inspected['conditional_scoped_parent_transfer']
        self.assertEqual(sum(row['parent_transfer'] for row in evidence['parents']), 1)
        self.assertEqual(evidence['outer_scope_uses_inside_children'], 1)
        inspect_size = len(json.dumps(inspected, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8'))
        with patch.object(document_cli, 'MAX_MODERN_REPORT_BYTES', inspect_size):
            self.assertEqual(inspected, document_cli.report_document(**common,
                    inspect_scoped_parent_transfer=('main.typ', start)))
        with patch.object(document_cli, 'MAX_MODERN_REPORT_BYTES', inspect_size - 1):
            with self.assertRaises(InputError):
                document_cli.report_document(**common, inspect_scoped_parent_transfer=('main.typ', start))
        inspect_low, inspect_high = 1, 2048
        while inspect_low < inspect_high:
            mid = (inspect_low + inspect_high) // 2
            try:
                with patch.object(document_cli, 'MAX_REPORT_ITEMS', mid):
                    document_cli.report_document(**common, inspect_scoped_parent_transfer=('main.typ', start))
            except InputError:
                inspect_low = mid + 1
            else:
                inspect_high = mid
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', inspect_low):
            self.assertEqual(inspected, document_cli.report_document(**common,
                    inspect_scoped_parent_transfer=('main.typ', start)))
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', inspect_low - 1):
            with self.assertRaises(InputError):
                document_cli.report_document(**common, inspect_scoped_parent_transfer=('main.typ', start))
        result = document_cli.report_document(**common, preview_conditional_modern_parent_reference=('main.typ', start))
        self.assertEqual(result['conditional_modern_combined_previews'], baseline['conditional_modern_combined_previews'])
        row = result['conditional_modern_parent_reference_preview']
        self.assertEqual(row['verification'], 'conditional-parent-scoped-source-only')
        self.assertEqual(row['verified_parent_transfers'], 1)
        self.assertTrue(row['independent_source_only'])
        self.assertFalse(row['composition_proved'])
        self.assertTrue(any('do not merge' in condition for condition in row['conditions']))
        self.assertEqual(len(row['edits']), 1)
        self.assertFalse(result['compiler_invoked'] or result['applied_fixes'])
        with patch.object(proof, '_verification_cost', return_value=proof.MAX_PREVIEW_WORK):
            withheld = document_cli.report_document(**common, preview_conditional_modern_parent_reference=('main.typ', start))
        self.assertEqual(withheld['conditional_modern_combined_previews'], baseline['conditional_modern_combined_previews'])
        self.assertEqual(withheld['conditional_modern_parent_reference_preview']['verification'], 'withheld')
        size = len(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8'))
        with patch.object(document_cli, 'MAX_MODERN_REPORT_BYTES', size):
            self.assertEqual(result, document_cli.report_document(**common,
                    preview_conditional_modern_parent_reference=('main.typ', start)))
        with patch.object(document_cli, 'MAX_MODERN_REPORT_BYTES', size - 1):
            with self.assertRaises(InputError):
                document_cli.report_document(**common, preview_conditional_modern_parent_reference=('main.typ', start))
        low, high = 1, 2048
        while low < high:
            mid = (low + high) // 2
            try:
                with patch.object(document_cli, 'MAX_REPORT_ITEMS', mid):
                    document_cli.report_document(**common, preview_conditional_modern_parent_reference=('main.typ', start))
            except InputError:
                low = mid + 1
            else:
                high = mid
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', low):
            self.assertEqual(result, document_cli.report_document(**common,
                    preview_conditional_modern_parent_reference=('main.typ', start)))
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', low - 1):
            with self.assertRaises(InputError):
                document_cli.report_document(**common, preview_conditional_modern_parent_reference=('main.typ', start))
        for path in ('../main.typ', '/etc/passwd'):
            with self.subTest(path=path):
                other = document_cli.report_document(**common, preview_conditional_modern_parent_reference=(path, start))
                self.assertEqual(other['conditional_modern_parent_reference_preview']['verification'], 'withheld')
                self.assertFalse(other['conditional_modern_parent_reference_preview']['edits'])
        for offset in (-1, True, '１２', '1.2', '1234567890123'):
            with self.subTest(offset=offset), self.assertRaises(InputError):
                document_cli.report_document(**common, preview_conditional_modern_parent_reference=('main.typ', offset))
        with self.assertRaises(InputError):
            document_cli.report_document(**common, inspect_scoped_parent_transfer=('main.typ', start),
                preview_conditional_modern_parent_reference=('main.typ', start))
        self.assertEqual((self.fixture.fixture.project / 'main.typ').read_bytes(), source)

    def test_cli_flags_are_explicit_and_text_warns_against_merging(self):
        source, _, diagnostics = self.issue(b'#local-tag-scope(s => [#mannot-scope(m => [#(m.tag)("a")], '
                                             b'parent: s, name: "one")\n#highlighted[See #(s.ref)("a") $x$ end.]])')
        command = [sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
                   '--root', str(self.fixture.fixture.project), '--entrypoint', 'main.typ',
                   '--rule-set', 'pde', '--package-path', str(self.fixture.fixture.packages),
                   '--footnote-cache-path', str(self.fixture.fixture.cache), '--modern-highlight-evidence',
                   '--preview-conditional-modern-parent-reference', 'main.typ', str(diagnostics[0]['start'])]
        missing = subprocess.run(command + ['--format', 'json'], capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(missing.stdout)['phase'], 'input')
        run = subprocess.run(command + ['--preview-conditional-modern-combined-highlights', '--format', 'text'],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertIn('Do not merge with other plans', run.stdout)
        self.assertEqual((self.fixture.fixture.project / 'main.typ').read_bytes(), source)

    def test_preview_work_preflight_runs_before_candidate_parse(self):
        import syntax_client
        body = (b'#local-tag-scope(s => [#mannot-scope(m => [#(m.tag)("a")], parent: s, name: "one")\n'
                b'#highlighted[See #(s.ref)("a") $x$ end.]])')
        _, analyzer, diagnostics = self.issue(body)
        start = diagnostics[0]['start']
        original = proof.analyze_one(analyzer, 'main.typ', start)
        tree = analyzer._session._context.source_for('main.typ').tree
        replay = modern._ModernAnalyzer(analyzer._session, analyzer._variant, scoped_callbacks=analyzer._scoped_callbacks)
        modern.ModernHighlightAnalyzer._analyze_one(analyzer, tree, 'main.typ', replay)
        low = original['work_used'] + replay.ast_work + 64 * len(tree.nodes) + len(tree.source) - 1
        with patch.object(syntax_client, 'parse', side_effect=AssertionError('candidate parsed before preflight')):
            with self.assertRaisesRegex(SafetyError, 'work budget'):
                proof.preview_one(analyzer, 'main.typ', start, max_work=low)

    def test_duplicate_span_verification_cost_grows_before_matching(self):
        from document_gap_preview import _MappedEdits
        from edit_plan import Edit
        from syntax_client import parse
        source = b'outside text\n#highlighted[before $x$ after]'
        tree = parse(source)
        edit = Edit(source.index(b'#highlighted'), len(source), b'#highlighted[before ]$x$#highlighted[ after]')
        candidate = parse(source[:edit.start] + edit.replacement)
        mapper = _MappedEdits((edit,))
        original = proof._verification_cost(tree, candidate, edit, mapper)
        node = next(n for n in candidate.nodes if n.kind == 'Text' and n.end <= edit.start)
        duplicated = replace(candidate, nodes=candidate.nodes + (node,) * 20)
        self.assertGreater(proof._verification_cost(tree, duplicated, edit, mapper), original)
        longer_source = b'o' * 1000 + b'\n#highlighted[before $x$ after]'
        longer_tree = parse(longer_source)
        longer_edit = Edit(longer_source.index(b'#highlighted'), len(longer_source),
                           b'#highlighted[before ]$x$#highlighted[ after]')
        longer_candidate = parse(longer_source[:longer_edit.start] + longer_edit.replacement)
        longer_cost = proof._verification_cost(longer_tree, longer_candidate, longer_edit, _MappedEdits((longer_edit,)))
        self.assertGreater(longer_cost, original + 1000)

    def test_instance_forgery_capture_tamper_and_limits(self):
        body = (b'#local-tag-scope(s => [#mannot-scope(m => [#(m.tag)("a")], parent: s, name: "one")\n'
                b'#highlighted[See #(s.ref)("a") $x$ end.]])')
        _, analyzer, diagnostics = self.issue(body)
        start = diagnostics[0]['start']
        fake = {'diagnostics': [dict(diagnostics[0], start=start + 1)]}
        with patch.object(analyzer, 'analyze', return_value=fake), patch.object(analyzer, '_analyze_one', return_value=fake):
            with self.assertRaises(InputError):
                proof.analyze_one(analyzer, 'main.typ', start + 1)
        result = proof.analyze_one(analyzer, 'main.typ', start)
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
        size = len(encoded.encode()) + 2 * encoded.count('\n')
        self.assertEqual(proof.analyze_one(analyzer, 'main.typ', start, max_work=result['work_used'], max_bytes=size), result)
        with self.assertRaisesRegex(SafetyError, 'work budget'):
            proof.analyze_one(analyzer, 'main.typ', start, max_work=result['work_used'] - 1)
        with self.assertRaisesRegex(SafetyError, 'byte budget'):
            proof.analyze_one(analyzer, 'main.typ', start, max_bytes=size - 1)
        tree = analyzer._session._context.source_for('main.typ').tree
        root, old = tree.nodes[0], tree.nodes[0].end
        try:
            object.__setattr__(root, 'end', old - 1)
            with self.assertRaises(InputError):
                proof.analyze_one(analyzer, 'main.typ', start)
        finally:
            object.__setattr__(root, 'end', old)


if __name__ == '__main__':
    unittest.main()
