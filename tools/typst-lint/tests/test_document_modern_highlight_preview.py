"""Conditional literal modern highlight splits never authorize writes."""
from pathlib import Path
from dataclasses import replace
import os
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_modern_highlight as modern
import document_modern_highlight_preview as preview
from edit_plan import Edit, plan
from syntax_client import parse
import test_conditional_modern_highlight as fixtures
from typst_lint import InputError, SafetyError, digest


@unittest.skipUnless(fixtures.EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ModernHighlightPreviewTests(unittest.TestCase):
    # Reuse the isolated exact package/session setup, not any user source.
    def setUp(self):
        self.fixture = fixtures.ConditionalModernHighlightTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project = self.fixture.project
        self.packages = self.fixture.packages
        self.cache = self.fixture.cache

    def proposal(self, body):
        source = fixtures.IMPORT + body
        session, context = self.fixture.session(source)
        evidence = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)
        result = preview.preview_all(evidence)
        return source, context, evidence, result

    def candidate(self, body):
        source, _, evidence, result = self.proposal(body)
        row, = result['by_file']
        self.assertEqual(row['verification'], 'conditional-modern-highlight-syntax-directives-and-reanalysis', row)
        self.assertFalse(row['runtime_binding_proved'] or row['render_equivalence_proved']
                         or row['edit_permission'] or row['compiler_permission'])
        edits = [Edit(item['start'], item['end'], item['replacement'].encode()) for item in row['edits']]
        candidate = plan(source, edits).candidate()
        self.assertEqual(row['baseline_sha256'], digest(source))
        self.assertEqual(row['candidate_sha256'], digest(candidate))
        return source, candidate, result

    def test_reviewed_math_preview_is_explicit_and_rechecked(self):
        source = fixtures.IMPORT + b'#highlighted[Before $cal(frac(x, y))$ after]'
        session, context = self.fixture.session(source)
        analyzer = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)
        self.assertFalse(preview.preview_all(analyzer)['by_file'])
        _, math, result = preview.preview_reviewed_math_all(analyzer, return_evidence=True)
        self.assertEqual(len(math['by_file'][0]['findings']), 1)
        row, = result['by_file']
        self.assertEqual(row['verification'], 'conditional-modern-highlight-syntax-directives-and-reanalysis')
        self.assertEqual(len(row['edits']), 1)
        edit = row['edits'][0]
        candidate = plan(source, [Edit(edit['start'], edit['end'], edit['replacement'].encode())]).candidate()
        self.assertEqual(candidate, fixtures.IMPORT + b'#highlighted[Before ]$cal(frac(x, y))$#highlighted[ after]')
        self.assertEqual(row['candidate_sha256'], digest(candidate))
        self.assertFalse(row['edit_permission'] or row['compiler_permission'] or row['render_equivalence_proved'])

    def test_reviewed_double_dot_preserves_exact_equation_and_authority(self):
        source = fixtures.IMPORT + b'#highlighted[Before $dot.double(x)$ after]'
        session, _ = self.fixture.session(source)
        analyzer = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)
        self.assertFalse(preview.preview_all(analyzer)['by_file'])
        result = preview.preview_reviewed_math_all(analyzer)
        row, = result['by_file']
        edit, = row['edits']
        candidate = plan(source, [Edit(edit['start'], edit['end'], edit['replacement'].encode())]).candidate()
        self.assertEqual(candidate, fixtures.IMPORT + b'#highlighted[Before ]$dot.double(x)$#highlighted[ after]')
        self.assertTrue(any('symbol variants' in condition for condition in row['conditions']))
        self.assertFalse(row['runtime_binding_proved'] or row['render_equivalence_proved']
                         or row['edit_permission'] or row['compiler_permission'])
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)

    def test_reviewed_double_dot_candidate_tampering_is_withheld(self):
        source = fixtures.IMPORT + b'#highlighted[Before $dot.double(x)$ after]'
        session, _ = self.fixture.session(source)
        analyzer = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)
        original_parse = preview.parse
        def tamper(data, *args, **kwargs):
            return original_parse(data.replace(b'dot.double(x)', b'dot.triple(x)'), *args, **kwargs)
        with patch.object(preview, 'parse', side_effect=tamper):
            row, = preview.preview_reviewed_math_all(analyzer)['by_file']
        self.assertEqual(row['verification'], 'withheld')
        self.assertFalse(row['edits'])

    def test_builtin_block_preview_retains_runtime_conditions(self):
        _, candidate, result = self.candidate(b'#block(sticky: true)[#highlighted[Text $x$]]')
        self.assertIn(b'#block(sticky: true)[#highlighted[Text ]$x$]', candidate)
        conditions = result['by_file'][0]['conditions']
        self.assertTrue(any('builtin block wrapper' in item and 'runtime' in item for item in conditions))
        self.assertTrue(any('wrappers and callbacks execute' in item for item in conditions))

    def test_reviewed_math_preserves_existing_script_diagnostics(self):
        for prefix in (b'', b'// typst-lint: ignore-next core.parenthesized-scripts -- deliberate\n'):
            with self.subTest(prefix=prefix):
                source = fixtures.IMPORT + prefix + b'#highlighted[Before $cal(x_1)$ after]'
                session, _ = self.fixture.session(source)
                analyzer = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)
                result = preview.preview_reviewed_math_all(analyzer)
                row, = result['by_file']
                if prefix:
                    self.assertEqual(row['verification'], 'withheld')
                    self.assertFalse(row['edits'])
                    continue
                self.assertEqual(row['verification'], 'conditional-modern-highlight-syntax-directives-and-reanalysis')
                edit, = row['edits']
                candidate = plan(source, [Edit(edit['start'], edit['end'], edit['replacement'].encode())]).candidate()
                self.assertIn(b'$cal(x_1)$', candidate)
                before = preview.analyze_scripts(parse(source), 'main.typ', _verify=False, _allow_code_call=True)
                after = preview.analyze_scripts(parse(candidate), 'main.typ', _verify=False, _allow_code_call=True)
                self.assertEqual(len(before.diagnostics), 1)
                self.assertEqual(len(before.diagnostics), len(after.diagnostics))
                self.assertEqual(len(before.suppressed), len(after.suppressed))

    def test_reviewed_math_low_budget_fails_before_math_analysis(self):
        import document_math_builtin_evidence as math_evidence
        source = fixtures.IMPORT + b'#highlighted[Before $cal(X)$ after]'
        session, _ = self.fixture.session(source)
        analyzer = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)
        with patch.object(math_evidence, '_MathResolver', side_effect=AssertionError('unexpected math scan')):
            with self.assertRaises(InputError):
                preview.preview_reviewed_math_all(analyzer, max_items=1)

    def test_reviewed_math_rejects_changed_script_diagnostic(self):
        source = fixtures.IMPORT + b'#highlighted[Before $cal(x_1)$ after]'
        session, _ = self.fixture.session(source)
        analyzer = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)
        original = preview.analyze_scripts
        def drop_candidate_diagnostic(tree, *args, **kwargs):
            result = original(tree, *args, **kwargs)
            return replace(result, diagnostics=()) if tree.source != source else result
        with patch.object(preview, 'analyze_scripts', side_effect=drop_candidate_diagnostic):
            row, = preview.preview_reviewed_math_all(analyzer)['by_file']
        self.assertEqual(row['verification'], 'withheld')
        self.assertFalse(row['edits'])
        self.assertIn('script findings', row['reason'])

    def test_reviewed_math_shadow_suppression_and_unknown_are_withheld(self):
        for body in (b'#let cal(x) = panic("shadow")\n#highlighted[text $cal(x)$]',
                     b'#highlighted[text $cal(unknown(x))$]',
                     b'// typst-lint: ignore-next pde.text-only-highlight -- known\n#highlighted[text $cal(x)$]'):
            with self.subTest(body=body):
                source = fixtures.IMPORT + body
                session, _ = self.fixture.session(source)
                analyzer = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)
                self.assertFalse(preview.preview_reviewed_math_all(analyzer)['by_file'])

    def test_literal_prose_and_math_preserve_tokens_and_unicode_spaces(self):
        cases = ((b'#highlighted[Text $x$ follows.]', b'#highlighted[Text ]$x$#highlighted[ follows.]'),
                 (b'#highlighted()[Text $x$ follows.]', b'#highlighted[Text ]$x$#highlighted[ follows.]'),
                 (b'#highlighted[$x$]', b'$x$'),
                 (b'#highlighted()[$x$]', b'$x$'),
                 ('#highlighted[한글 $x$ 다음 $y$ 끝]'.encode(),
                  '#highlighted[한글 ]$x$#highlighted[ 다음 ]$y$#highlighted[ 끝]'.encode()))
        for body, expected in cases:
            with self.subTest(body=body):
                source, candidate, _ = self.candidate(body)
                self.assertEqual(candidate, fixtures.IMPORT + expected)
                self.assertEqual((self.project / 'main.typ').read_bytes(), source)

    def test_smartquote_tokens_preserve_order_and_empty_parentheses(self):
        for call in (b'#highlighted', b'#highlighted()'):
            for quote in (b'"', b"'"):
                body = call + b'[' + quote + b'text $x$ end' + quote + b']'
                expected = (b'#highlighted[' + quote + b'text ]$x$'
                            b'#highlighted[ end' + quote + b']')
                with self.subTest(call=call, quote=quote):
                    _, candidate, result = self.candidate(body)
                    self.assertEqual(candidate, fixtures.IMPORT + expected)
                    self.assertEqual(candidate.count(quote), fixtures.IMPORT.count(quote) + 2)
                    self.assertFalse(result['edit_permission'] or result['compiler_permission'])

    def test_smartquote_directive_preserves_suppression_and_side_effects_stay_excluded(self):
        suppressed = (b'// typst-lint: ignore-next pde.text-only-highlight -- intentional\n'
                      b'#highlighted["text $x$ end"]')
        _, _, _, result = self.proposal(suppressed)
        self.assertFalse(result['by_file'])
        for body in (b'#highlighted["text $x$ #unknown[] end"]',
                     b'#highlighted["text $cal(x)$ end"]',
                     b'#highlighted()["text $x$ // comment\n end"]'):
            with self.subTest(body=body):
                _, _, _, result = self.proposal(body)
                self.assertFalse(any(row['edits'] for row in result['by_file']), result)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with the pinned Typst compiler')
    def test_owned_smartquote_and_korean_fixture_can_change_pdf(self):
        if not subprocess.check_output(['typst', '--version'], text=True).startswith('typst 0.15.1 '):
            self.skipTest('Fixture comparison pins Typst 0.15.1')
        source, candidate, _ = self.candidate('#highlighted()["한글 $x$ 다음"]'.encode())
        pdfs = []
        for name, data in (('quote-before', source), ('quote-after', candidate)):
            path, output = self.project / (name + '.typ'), self.project / (name + '.pdf')
            path.write_bytes(data)
            run = subprocess.run(['typst', 'compile', '--root', str(self.project),
                                  '--package-path', str(self.packages), '--package-cache-path', str(self.cache),
                                  '--creation-timestamp', '0', str(path), str(output)], capture_output=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            pdfs.append(output.read_bytes())
        self.assertNotEqual(pdfs[0], pdfs[1])
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)

    def test_unrelated_duplicate_math_nodes_and_adjacent_text_keep_ownership(self):
        body = b'Before $x(y)$, #highlighted[Text $x$ follows.] after.'
        source, candidate, _ = self.candidate(body)
        self.assertEqual(candidate, fixtures.IMPORT +
                         b'Before $x(y)$, #highlighted[Text ]$x$#highlighted[ follows.] after.')
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)

    def test_unchanged_duplicate_math_nodes_cannot_share_one_match(self):
        source, context, evidence, _ = self.proposal(b'Before $x(y)$, #highlighted[Text $x$ follows.] after.')
        tree = context.source_for('main.typ').tree
        diagnostics = evidence.analyze(tree, 'main.typ')['diagnostics']
        edits, plans = preview._select(tree, diagnostics)
        candidate = parse(plan(source, edits).candidate())
        grouped = {}
        for i, node in enumerate(candidate.nodes):
            if node.kind == 'Math':
                grouped.setdefault((node.start, node.end), []).append(i)
        duplicate = next(group for group in grouped.values() if len(group) == 2)
        nodes = list(candidate.nodes)
        nodes[duplicate[1]] = replace(nodes[duplicate[1]], kind='Text')
        with self.assertRaisesRegex(SafetyError, 'syntax outside'):
            preview._verify_insertions(tree, replace(candidate, nodes=tuple(nodes)), edits, plans)

    def test_opaque_modes_comments_calls_and_code_math_are_not_proposed(self):
        bodies = (b'#highlighted(mode: "flow")[Text $x$ end.]',
                  b'#highlighted( )[Text $x$ end.]',
                  b'#highlighted(/* retain */)[Text $x$ end.]',
                  b'#highlighted[#unknown[$x$]]', b'#highlighted[Text // comment\n$x$]',
                  b'#highlighted[Text $ f(x) $]', b'#highlighted[Text $ #footnote[x] $]',
                  b'#let highlighted = none\n#highlighted[Text $x$]')
        for body in bodies:
            with self.subTest(body=body):
                _, _, _, result = self.proposal(body)
                self.assertFalse(any(row['edits'] for row in result['by_file']), result)

    def test_unpreviewed_and_withheld_diagnostics_are_visible_in_summary(self):
        body = b'#highlighted[Text $x$ follows.]\n#highlighted(mode: "phrase")[Other $y$ text.]'
        _, _, _, result = self.proposal(body)
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertEqual(result['diagnostics_count'], 2)
        self.assertEqual(result['previewed_diagnostics'], 1)
        self.assertEqual(result['unpreviewed_diagnostics'], 1)
        self.assertEqual(result['withheld_source_count'], 0)
        body = (b'// typst-lint: ignore-next pde.footnote-trailing-gap -- intentional\n'
                b'#highlighted[Text $x$ follows.]')
        _, _, _, result = self.proposal(body)
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertEqual(result['diagnostics_count'], 1)
        self.assertEqual(result['previewed_diagnostics'], 0)
        self.assertEqual(result['unpreviewed_diagnostics'], 1)
        self.assertEqual(result['withheld_source_count'], 1)

    def test_suppression_and_other_rule_directive_are_preserved(self):
        suppressed = (b'// typst-lint: ignore-next pde.text-only-highlight -- intentional\n'
                      b'#highlighted[Text $x$ follows.]')
        _, _, _, result = self.proposal(suppressed)
        self.assertFalse(result['by_file'])
        other_rule = (b'// typst-lint: ignore-next pde.footnote-trailing-gap -- intentional\n'
                      b'#highlighted[Text $x$ follows.]')
        _, _, _, result = self.proposal(other_rule)
        row, = result['by_file']
        self.assertEqual(row['verification'], 'withheld')
        self.assertFalse(row['edits'])

    def test_simple_equations_preserve_script_diagnostics_across_splits(self):
        cases = (
            (b'#highlighted[Before $x_1$ after.]', 1, 1),
            (b'#highlighted[Before $x_1^2$ and $y_2 + z^3$ after.]', 4, 1),
            ('#highlighted[한글 $x_1$ after.]\n#highlighted[é $z^2$ end.]'.encode(), 2, 2),
        )
        for body, count, edit_count in cases:
            with self.subTest(body=body):
                source, candidate, result = self.candidate(body)
                self.assertEqual(result['previewed_diagnostics'], edit_count)
                before = preview.analyze_scripts(parse(source), 'main.typ', _verify=False, _allow_code_call=True)
                after = preview.analyze_scripts(parse(candidate), 'main.typ', _verify=False, _allow_code_call=True)
                self.assertEqual((len(before.diagnostics), len(after.diagnostics)), (count, count))
                self.assertEqual(len(before.suppressed), len(after.suppressed))
                old_tree, new_tree = parse(source), parse(candidate)
                for old, new in zip(before.diagnostics, after.diagnostics):
                    old_pos = preview._FindingMapper(source, ()).offset(old)
                    new_pos = preview._FindingMapper(candidate, ()).offset(new)
                    old_equation, = (n for n in old_tree.nodes if n.kind == 'Equation' and n.start <= old_pos < n.end)
                    new_equation, = (n for n in new_tree.nodes if n.kind == 'Equation' and n.start <= new_pos < n.end)
                    self.assertEqual(old_pos - old_equation.start, new_pos - new_equation.start)
                    self.assertEqual((old.rule_id, old.grade, old.explanation, old.source),
                                     (new.rule_id, new.grade, new.explanation, new.source))
                for equation in (b'$x_1$', b'$x_1^2$', b'$y_2 + z^3$', b'$z^2$'):
                    if equation in source:
                        self.assertEqual(source.count(equation), candidate.count(equation))
                self.assertEqual((self.project / 'main.typ').read_bytes(), source)

    def test_simple_equation_script_suppression_and_tampering_are_withheld(self):
        body = (b'// typst-lint: ignore-next core.parenthesized-scripts -- deliberate\n'
                b'#highlighted[Before $x_1$ after.]')
        _, _, _, result = self.proposal(body)
        row, = result['by_file']
        self.assertEqual(row['verification'], 'withheld')
        self.assertIn('suppression targets', row['reason'])
        self.assertFalse(row['edits'])

        source = fixtures.IMPORT + b'#highlighted[Before $x_1$ after.]'
        session, _ = self.fixture.session(source)
        analyzer = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)
        original_parse = preview.parse
        def tamper_candidate(data, *args, **kwargs):
            return original_parse(data.replace(b'$x_1$', b'$y_1$'), *args, **kwargs)
        with patch.object(preview, 'parse', side_effect=tamper_candidate):
            row, = preview.preview_all(analyzer)['by_file']
        self.assertEqual(row['verification'], 'withheld')
        self.assertFalse(row['edits'])

        original_scripts = preview.analyze_scripts
        def drop_candidate_diagnostic(tree, *args, **kwargs):
            result = original_scripts(tree, *args, **kwargs)
            return replace(result, diagnostics=()) if tree.source != source else result
        with patch.object(preview, 'analyze_scripts', side_effect=drop_candidate_diagnostic):
            row, = preview.preview_all(analyzer)['by_file']
        self.assertEqual(row['verification'], 'withheld')
        self.assertIn('script findings', row['reason'])
        self.assertFalse(row['edits'])

    def test_nested_and_ambiguous_equation_ownership_is_not_proposed(self):
        nested = b'#highlighted[Text $ #([nested $x_1$]) $ end.]'
        source, _, _, result = self.proposal(nested)
        equations = [n for n in parse(source).nodes if n.kind == 'Equation']
        self.assertEqual(len(equations), 2)
        self.assertLess(equations[0].start, equations[1].start)
        self.assertLess(equations[1].end, equations[0].end)
        self.assertFalse(any(row['edits'] for row in result['by_file']))
        self.assertEqual(result['unpreviewed_diagnostics'], 1)

        source, context, evidence, _ = self.proposal(b'#highlighted[Before $x_1$ after.]')
        tree = context.source_for('main.typ').tree
        diagnostics = evidence.analyze(tree, 'main.typ')['diagnostics']
        edits, plans = preview._select(tree, diagnostics)
        candidate = parse(plan(source, edits).candidate())
        old = preview.analyze_scripts(tree, 'main.typ', _verify=False, _allow_code_call=True)
        new = preview.analyze_scripts(candidate, 'main.typ', _verify=False, _allow_code_call=True)
        equation, = (n for n in tree.nodes if n.kind == 'Equation')
        ambiguous = replace(tree, nodes=tree.nodes + (equation,))
        with self.assertRaisesRegex(SafetyError, 'ambiguous equations'):
            preview._same_script_findings_with_moved_equations(ambiguous, old, candidate, new, edits, plans)

    def test_many_edits_index_equations_with_one_tree_scan(self):
        body = b''.join(b'Ordinary text.\n#highlighted[Before $x_1$ after.]\n' for _ in range(40))
        source, context, evidence, _ = self.proposal(body)
        tree = context.source_for('main.typ').tree
        diagnostics = evidence.analyze(tree, 'main.typ')['diagnostics']
        edits, plans = preview._select(tree, diagnostics)
        self.assertEqual(len(edits), 40)
        candidate = parse(plan(source, edits).candidate())
        old = preview.analyze_scripts(tree, 'main.typ', _verify=False, _allow_code_call=True)
        new = preview.analyze_scripts(candidate, 'main.typ', _verify=False, _allow_code_call=True)
        class CountedNodes:
            def __init__(self, nodes):
                self.nodes, self.visits = nodes, 0
            def __iter__(self):
                for node in self.nodes:
                    self.visits += 1
                    yield node
        counted = CountedNodes(tree.nodes)
        self.assertTrue(preview._same_script_findings_with_moved_equations(
            replace(tree, nodes=counted), old, candidate, new, edits, plans))
        self.assertEqual(counted.visits, len(tree.nodes))

    def test_subset_keeps_conflicting_finding_and_rechecks_combination(self):
        body = (b'#highlighted[Bad $ x $ end.]\n'
                b'#highlighted[Good $x$ end.]\n#highlighted()[More $y$ end.]')
        source, candidate, result = self.candidate(body)
        self.assertIn(b'#highlighted[Bad $ x $ end.]', candidate)
        self.assertIn(b'#highlighted[Good ]$x$#highlighted[ end.]', candidate)
        self.assertIn(b'#highlighted[More ]$y$#highlighted[ end.]', candidate)
        self.assertEqual((result['diagnostics_count'], result['previewed_diagnostics'],
                          result['unpreviewed_diagnostics'], result['withheld_source_count']), (3, 2, 1, 0))
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)

        # Individually safe edits do not establish that their union is safe.
        original = preview._preview_source
        def refuse_union(evidence, tree, path, diagnostics, records):
            value = original(evidence, tree, path, diagnostics, records)
            if len(diagnostics) == 2 and value is not None:
                return dict(value, verification='withheld', edits=[], reason='combined conflict')
            return value
        session, _ = self.fixture.session(source)
        evidence = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)
        with patch.object(preview, '_preview_source', side_effect=refuse_union):
            row, = preview.preview_all(evidence)['by_file']
        self.assertEqual(row['verification'], 'withheld')
        self.assertFalse(row['edits'])

    def test_subset_work_is_bounded_and_suppression_is_not_retargeted(self):
        body = (b'#highlighted[Bad $ x $ end.]\n'
                b'#highlighted[Good $x$ end.]\n#highlighted[More $y$ end.]')
        with patch.object(preview, 'MAX_SUBSET_CHECKS', 2):
            _, _, _, result = self.proposal(body)
        row, = result['by_file']
        self.assertEqual(row['verification'], 'withheld')
        self.assertIn('budget exceeded', row['reason'])
        self.assertFalse(row['edits'])
        body = (b'// typst-lint: ignore-next core.parenthesized-scripts -- deliberate\n'
                b'#highlighted[Bad $x_1$ end.]\n#highlighted[Good $x$ end.]')
        _, _, _, result = self.proposal(body)
        row, = result['by_file']
        self.assertEqual(row['verification'], 'withheld')
        self.assertIn('suppression targets', row['reason'])

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with the pinned Typst compiler')
    def test_owned_partial_subset_renders_before_and_after(self):
        if not subprocess.check_output(['typst', '--version'], text=True).startswith('typst 0.15.1 '):
            self.skipTest('Fixture comparison pins Typst 0.15.1')
        body = (b'#highlighted[Bad $ x $ end.]\n'
                b'#highlighted[Good $x$ end.]\n#highlighted[More $y$ end.]')
        source, candidate, result = self.candidate(body)
        self.assertEqual(result['previewed_diagnostics'], 2)
        pdfs = []
        for name, data in (('partial-before', source), ('partial-after', candidate)):
            path, output = self.project / (name + '.typ'), self.project / (name + '.pdf')
            path.write_bytes(data)
            run = subprocess.run(['typst', 'compile', '--root', str(self.project),
                                  '--package-path', str(self.packages), '--package-cache-path', str(self.cache),
                                  '--creation-timestamp', '0', str(path), str(output)], capture_output=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            pdfs.append(output.read_bytes())
        self.assertNotEqual(pdfs[0], pdfs[1])
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)

    def test_reparse_failure_and_unsafe_owner_are_withheld(self):
        source, context, evidence, _ = self.proposal(b'#highlighted[Text $x$ follows.]')
        with patch.object(preview, 'parse', side_effect=ValueError('fixture parse failure')):
            row, = preview.preview_all(evidence)['by_file']
        self.assertEqual(row['verification'], 'withheld')
        self.assertFalse(row['edits'])
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)
        with self.assertRaises(InputError):
            preview.preview_all(object())
        with self.assertRaisesRegex(InputError, 'budget'):
            preview.preview_all(evidence, max_items=1)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with the pinned Typst compiler')
    def test_owned_korean_math_fixture_renders_and_differs(self):
        if not subprocess.check_output(['typst', '--version'], text=True).startswith('typst 0.15.1 '):
            self.skipTest('Fixture comparison pins Typst 0.15.1')
        source, candidate, _ = self.candidate('#highlighted[한글 $x$ 다음]'.encode())
        pdfs = []
        for name, data in (('before', source), ('after', candidate)):
            path, output = self.project / (name + '.typ'), self.project / (name + '.pdf')
            path.write_bytes(data)
            run = subprocess.run(['typst', 'compile', '--root', str(self.project),
                                  '--package-path', str(self.packages), '--package-cache-path', str(self.cache),
                                  '--creation-timestamp', '0', str(path), str(output)], capture_output=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            pdfs.append(output.read_bytes())
        self.assertNotEqual(pdfs[0], pdfs[1])
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with the pinned Typst compiler')
    def test_owned_empty_parens_fixture_preserves_math_and_can_change_pdf(self):
        if not subprocess.check_output(['typst', '--version'], text=True).startswith('typst 0.15.1 '):
            self.skipTest('Fixture comparison pins Typst 0.15.1')
        source, candidate, _ = self.candidate('#highlighted()[앞 $x$ 뒤]'.encode())
        pdfs = []
        for name, data in (('empty-before', source), ('empty-after', candidate)):
            path, output = self.project / (name + '.typ'), self.project / (name + '.pdf')
            path.write_bytes(data)
            run = subprocess.run(['typst', 'compile', '--root', str(self.project),
                                  '--package-path', str(self.packages), '--package-cache-path', str(self.cache),
                                  '--creation-timestamp', '0', str(path), str(output)], capture_output=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            pdfs.append(output.read_bytes())
        self.assertNotEqual(pdfs[0], pdfs[1])
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)
