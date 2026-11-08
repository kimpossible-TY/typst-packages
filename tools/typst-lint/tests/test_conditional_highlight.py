"""Conditional namespace diagnostics with genuine captures and official ASTs."""
from dataclasses import FrozenInstanceError, asdict, replace
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_highlight as conditional
import import_graph as graph
from namespace_packages import load_namespace_packages
from syntax_client import EXECUTABLE, parse
from typst_lint import digest

STYLE = b'''#import "@local/text-utils:0.1.2": *
#import "@local/math-blocks:0.2.0": *
#import "@local/scoped-annotations:0.3.0": *
#import "@local/cetz-helpers:0.2.0": *
#import "@preview/mannot:0.4.0": *
#let rmark = mark.with(color: red)
#let bmark = mark.with(color: blue)
#let pmark = mark.with(color: purple)
#let dx = $upright(d) x$
#let tilde(body, size:150%) = math.tilde(body, size:size)
'''
FIGURES = b'''#import "@preview/cetz:0.4.2": *
#import "@preview/fletcher:0.5.8": diagram, node, edge
#import "@local/math-blocks:0.2.0": theme-from-text-fill
#let wave(t) = (calc.sin(t), t)
#let sketch = canvas({ import draw: *; line((0,0),(1,1)) })
#let figure-caption = [An unrelated figure.]
'''
PREFIX = b'''#import "Styles/styles.typ": *
#import "Chapter1/figures.typ": *
#import "@preview/cetz:0.4.2"
#import "@preview/mannot:0.4.0": *
'''
MAIN = PREFIX + b'''= A chapter
#paragraph_tab
Ordinary prose $x^2$ and a #footnote[remark] before the call.
#highlighted[Text and $x^2$ together.]
'''


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ConditionalHighlightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package_tmp = tempfile.TemporaryDirectory(prefix='typst-conditional-packages-')
        cls.addClassCleanup(cls.package_tmp.cleanup)
        root = Path(cls.package_tmp.name).resolve()
        for name, version in (('text-utils', '0.1.2'), ('math-blocks', '0.2.0'),
                              ('scoped-annotations', '0.3.0'), ('cetz-helpers', '0.2.0')):
            source = TOOL.parents[1] / 'local' / name / version
            target = root / 'local' / name / version
            target.mkdir(parents=True)
            for filename in ('typst.toml', 'lib.typ'):
                shutil.copyfile(source / filename, target / filename)
        for fixture, name, version in (('scoped-reference-packages', 'mannot', '0.4.0'),
                                        ('anchor-overlay-packages', 'cetz', '0.4.2')):
            source = TOOL / 'tests' / 'fixtures' / fixture / 'preview' / name / version
            target = root / 'preview' / name / version
            (target / 'src').mkdir(parents=True)
            for filename in ('typst.toml', 'src/lib.typ'):
                shutil.copyfile(source / filename, target / filename)
        cls.namespaces = load_namespace_packages(root)
        if cls.namespaces.unavailable:
            raise AssertionError(cls.namespaces.unavailable)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='typst-conditional-highlight-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def put(self, path, source):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source)

    def capture(self, source=MAIN, style=STYLE, figures=FIGURES):
        self.put('Styles/styles.typ', style)
        self.put('Chapter1/figures.typ', figures)
        self.put('main.typ', source)
        return graph.load_import_graph(self.root, (('main.typ', source),))

    def analyze(self, source=MAIN, style=STYLE, figures=FIGURES, context=None, namespaces=None):
        tree = parse(source)
        self.assertFalse(tree.nodes[0].errors, source)
        return conditional.analyze(tree, 'main.typ',
            self.capture(source, style, figures) if context is None else context,
            self.namespaces if namespaces is None else namespaces)

    def test_realistic_wildcard_facade_and_figures_keep_conditional_identity(self):
        result = self.analyze()
        self.assertEqual(len(result.diagnostics), 1, result.unresolved)
        finding = result.diagnostics[0]
        self.assertEqual((finding.rule_id, finding.path, finding.status),
                         ('pde.text-only-highlight', 'main.typ', 'CONDITIONAL'))
        self.assertEqual(finding.root_sha256, digest(MAIN))
        self.assertFalse(finding.edit_permission)
        self.assertFalse(hasattr(result, 'edits'))
        self.assertEqual({s.path for s in finding.source_hashes},
                         {'main.typ', 'Styles/styles.typ', 'Chapter1/figures.typ'})
        self.assertEqual(len(finding.package_evidence), 6)
        self.assertIn('@preview/fletcher:0.5.8', finding.package_requests)
        self.assertEqual([(r.path, r.local_name) for r in finding.route], [
            ('@local/text-utils:0.1.2', 'highlighted'),
            ('Styles/styles.typ', 'highlighted'), ('main.typ', 'highlighted')])
        self.assertEqual(finding.conditions, conditional.CONDITIONS)
        self.assertIn('initializer purity is not proved', finding.conditions[0])
        self.assertIn('ambient package selection is not proved', finding.conditions[1])
        json.dumps(asdict(result))
        with self.assertRaises(FrozenInstanceError):
            finding.edit_permission = True

    def test_named_renamed_forwarding_and_immutable_aliases(self):
        style = (b'#import "@local/text-utils:0.1.2": highlighted as mark-math\n'
                 b'#let saved = mark-math\n#let other = make-value()\n')
        source = (b'#import "Styles/styles.typ": saved as hi\n'
                  b'#let original = hi\n#let hi(body) = body\n'
                  b'#hi[$wrong$]\n#original[$right$]\n')
        result = self.analyze(source, style)
        self.assertEqual(len(result.diagnostics), 1, result.unresolved)
        self.assertEqual(result.diagnostics[0].route[-1].local_name, 'original')
        self.assertEqual([r.operation for r in result.diagnostics[0].route],
                         ['package-export', 'named-import', 'alias', 'named-import', 'alias'])

    def test_statement_order_rebinding_and_unknown_mutation(self):
        source = (PREFIX + b'#highlighted[$before$]\n'
                  b'#let highlighted(body) = body\n#highlighted[$shadowed$]\n'
                  b'#import "@local/text-utils:0.1.2": highlighted\n'
                  b'#highlighted[$restored$]\n#(highlighted = fake)\n#highlighted[$mutated$]')
        result = self.analyze(source)
        self.assertEqual(len(result.diagnostics), 2, result.unresolved)
        self.assertTrue(any('mutation' in r for r in result.unresolved))
        for mutation in (b'#{highlighted = fake}', b'#let unused = {highlighted = fake; 1}',
                         b'#if true { highlighted = fake }', b'#(highlighted += fake)',
                         b'#let unused(x: (highlighted = fake)) = x'):
            with self.subTest(mutation=mutation):
                self.assertFalse(self.analyze(PREFIX + mutation + b'\n#highlighted[$x$]').diagnostics)

    def test_later_wildcard_shadow_and_facade_rebind_are_not_guessed(self):
        figures = FIGURES + b'#let highlighted(body) = body\n'
        self.assertFalse(self.analyze(figures=figures).diagnostics)
        self.assertFalse(self.analyze(style=STYLE + b'#let highlighted = other\n').diagnostics)
        self.assertFalse(self.analyze(b'#highlighted[$x$]').diagnostics)
        for suffix in (b'#import "@preview/unreviewed:1.0.0": *\n',
                       b'#import dynamic-path: *\n', b'#import "missing.typ": *\n'):
            with self.subTest(suffix=suffix):
                result = self.analyze(PREFIX + suffix + b'#highlighted[$x$]')
                self.assertFalse(result.diagnostics)
                self.assertTrue(result.unresolved)
                result = self.analyze(style=STYLE + suffix)
                self.assertFalse(result.diagnostics)

    def test_only_rendered_literal_body_math_is_reported(self):
        for call in (b'#highlighted[plain]', b'#highlighted(fill: $option$)[plain]',
                     b'#highlighted[`$raw$`]', b'#highlighted("$string$")',
                     b'#highlighted[#text[$nested$]]', b'#highlighted(..args)[$dynamic$]',
                     b'#highlighted(fill: $option$)[A $body$]'):
            with self.subTest(call=call):
                self.assertFalse(self.analyze(PREFIX + call).diagnostics)
        for call in (b'#highlighted[*A $x$*]', b'#highlighted(body: [A $x$])'):
            with self.subTest(call=call):
                self.assertEqual(len(self.analyze(PREFIX + call).diagnostics), 1)

    def test_expression_position_namespace_declarations_invalidate(self):
        for statement in (b'#let unused = import "Chapter1/figures.typ": *',
                          b'#let unused = let highlighted = fake',
                          b'#foo(import "Chapter1/figures.typ": *)',
                          b'#(let highlighted = fake)',
                          b'#let unused(x: (import "Chapter1/figures.typ": *)) = x'):
            with self.subTest(statement=statement):
                result = self.analyze(PREFIX + statement + b'\n#highlighted[$x$]')
                self.assertFalse(result.diagnostics)
                self.assertTrue(any('Expression-position' in r for r in result.unresolved))
                result = self.analyze(style=STYLE + statement)
                self.assertFalse(result.diagnostics)
        # A captured local initializer scope has finite root binding names;
        # its internal imports do not become exports of the enclosing module.
        figures = FIGURES + b'#let unrelated = { let highlighted = fake; 1 }\n'
        self.assertEqual(len(self.analyze(figures=figures).diagnostics), 1)

    def test_nested_callbacks_and_scopes_are_explicitly_unresolved(self):
        for call in (b'#block[#highlighted[$x$]]', b'#let f() = [#highlighted[$x$]]',
                     b'#context [#highlighted[$x$]]'):
            with self.subTest(call=call):
                result = self.analyze(PREFIX + call)
                self.assertFalse(result.diagnostics)
                self.assertTrue(any('context' in r for r in result.unresolved))

    def test_call_owned_suppression_and_unrelated_directive(self):
        for rule in (conditional.RULE_ID, 'core.parenthesized-scripts'):
            source = PREFIX + f'// typst-lint: ignore-next {rule} -- intentional\n#highlighted[$x$]'.encode()
            result = self.analyze(source)
            self.assertEqual(len(result.suppressed), int(rule == conditional.RULE_ID))
            self.assertEqual(len(result.diagnostics), int(rule != conditional.RULE_ID))

    def test_source_namespace_bytes_and_graph_identity_mismatches_refuse(self):
        context = self.capture()
        result = self.analyze(MAIN + b' ', context=context)
        self.assertFalse(result.diagnostics)
        self.assertIn('snapshot', result.unresolved[0])
        package = self.namespaces.packages[0]
        for mutated in (replace(package, source=package.source + b'\n'),
                        replace(package, exports=package.exports + ('forged',)),
                        replace(package, origins=() if package.origins else
                                (('highlighted', '@local/text-utils:0.1.2'),))):
            capture = replace(self.namespaces, packages=(mutated,) + self.namespaces.packages[1:])
            result = self.analyze(context=context, namespaces=capture)
            self.assertFalse(result.diagnostics)
            self.assertIn('namespace evidence', result.unresolved[0])
        for invalid in (replace(context, sources=list(context.sources)),
                        replace(context, edges=context.edges + context.edges)):
            result = self.analyze(context=invalid)
            self.assertFalse(result.diagnostics)
            self.assertTrue(result.unresolved)

    def test_captured_parser_refusal_is_not_retried(self):
        context = self.capture()
        context = replace(context, unavailable=context.unavailable + (
            graph.ImportUnavailable('Styles/styles.typ', '', 'Syntax adapter output limit exceeded'),))
        source = b'#import "Styles/styles.typ": *\n#highlighted[$x$]'
        self.put('main.typ', source)
        context = replace(context, sources=tuple(replace(s, source=source) if s.path == 'main.typ' else s
                                                for s in context.sources))
        tree = parse(source)
        with patch.object(conditional, 'parse', side_effect=AssertionError('Do not retry captured refusal')):
            result = conditional.analyze(tree, 'main.typ', context, self.namespaces)
        self.assertFalse(result.diagnostics)
        self.assertIn('output limit', result.unresolved[0])

    def test_session_reuses_retained_dependency_trees_without_source_reads(self):
        context = self.capture()
        tree = parse(MAIN)
        session = conditional.ConditionalHighlightAnalyzer(context, self.namespaces)
        with patch.object(conditional, 'parse', wraps=parse) as parser, \
                patch.object(Path, 'read_bytes', side_effect=AssertionError('No source file lookup')):
            first = session.analyze(tree, 'main.typ')
            first_work = session._analyzer.ast_work
            second = session.analyze(tree, 'main.typ')
        self.assertEqual(first, second)
        self.assertEqual(parser.call_count, 2)
        self.assertEqual(len(first.diagnostics), 1)
        self.assertEqual(session._analyzer.ast_work - first_work, 3 * len(tree.nodes))
        self.assertEqual(set(session._analyzer.modules), {'Styles/styles.typ', 'Chapter1/figures.typ'})

    def test_malformed_dependency_and_cycle_withhold(self):
        result = self.analyze(style=STYLE + b'#let broken = [')
        self.assertFalse(result.diagnostics)
        self.assertTrue(result.unresolved)
        result = self.analyze(style=b'#import "../main.typ": *\n' + STYLE)
        self.assertFalse(result.diagnostics)
        self.assertTrue(any('Cyclic' in reason for reason in result.unresolved))

    def test_doubled_acyclic_import_chain_reuses_facts_and_keeps_visit_budget(self):
        # The graph has only 11 dependencies and 11 distinct edges, but folding
        # each repeated import without a work budget would branch exponentially.
        for index in range(11):
            source = (f'#import "m{index + 1}.typ": *\n'.encode() * 2 if index < 10
                      else b'#import "@local/text-utils:0.1.2": *\n')
            self.put(f'm{index}.typ', source)
        source = b'#import "m0.typ": *\n#highlighted[$x$]'
        self.put('main.typ', source)
        context = graph.load_import_graph(self.root, (('main.typ', source),))
        session = conditional.ConditionalHighlightAnalyzer(context, self.namespaces)
        result = session.analyze(parse(source), 'main.typ')
        self.assertEqual(len(result.diagnostics), 1, result.unresolved)
        self.assertEqual(session._analyzer.module_visits, 21)
        cached = session._analyzer.modules['m0.typ']
        self.assertEqual(cached.height, 11)
        self.assertEqual(cached.closure, {f'm{i}.typ' for i in range(11)})
        with patch.object(conditional, 'MAX_MODULE_VISITS', 8):
            session = conditional.ConditionalHighlightAnalyzer(context, self.namespaces)
            result = session.analyze(parse(source), 'main.typ')
            self.assertFalse(result.diagnostics)
            self.assertIn('module visit limit', result.unresolved[0])
            self.assertEqual(session._analyzer.module_visits, 9)
            tree = parse(source)
            with patch.object(conditional, 'parse', side_effect=AssertionError('Exhausted session must refuse')):
                again = session.analyze(tree, 'main.typ')
            self.assertEqual(result, again)

    def test_cached_fact_checks_complete_ancestry_and_maximum_height(self):
        self.put('head.typ', b'#import "leaf.typ": *\n')
        self.put('leaf.typ', b'#import "@local/text-utils:0.1.2": *\n')
        source = b'#import "head.typ": *\n#highlighted[$x$]'
        self.put('main.typ', source)
        context = graph.load_import_graph(self.root, (('main.typ', source),))
        session = conditional.ConditionalHighlightAnalyzer(context, self.namespaces)
        self.assertEqual(len(session.analyze(parse(source), 'main.typ').diagnostics), 1)
        analyzer = session._analyzer
        fact = analyzer.modules['head.typ']
        self.assertEqual((fact.height, fact.closure), (2, {'head.typ', 'leaf.typ'}))
        # The requested module itself is absent from active: checking only its
        # path would miss the dependency already present in caller ancestry.
        self.assertIn('Cyclic', analyzer.module('head.typ', ('leaf.typ',)).reason)
        with patch.object(graph, 'MAX_GRAPH_DEPTH', 3):
            self.assertIn('depth limit', analyzer.module('head.typ', ('a.typ', 'b.typ')).reason)
            self.assertIs(analyzer.module('head.typ', ('main.typ',)), fact)

    def test_opaque_relative_alias_preserves_dependency_height(self):
        self.put('head.typ', b'#import "leaf.typ" as opaque\n#import "@local/text-utils:0.1.2": highlighted\n')
        self.put('leaf.typ', b'#import "deep.typ" as also-opaque\n')
        self.put('deep.typ', b'#let value = 1\n')
        source = b'#import "head.typ": *\n#highlighted[$x$]'
        self.put('main.typ', source)
        context = graph.load_import_graph(self.root, (('main.typ', source),))
        session = conditional.ConditionalHighlightAnalyzer(context, self.namespaces)
        self.assertEqual(len(session.analyze(parse(source), 'main.typ').diagnostics), 1)
        analyzer = session._analyzer
        fact = analyzer.modules['head.typ']
        self.assertEqual((fact.height, fact.closure), (3, {'head.typ', 'leaf.typ', 'deep.typ'}))
        with patch.object(graph, 'MAX_GRAPH_DEPTH', 4):
            self.assertIn('depth limit', analyzer.module('head.typ', ('a.typ', 'b.typ')).reason)

    def test_intrinsic_refusal_is_reused_without_positive_promotion(self):
        source = b'#import "Styles/styles.typ": *\n#highlighted[$x$]'
        context = self.capture(source, STYLE + b'#context [unproved module scope]\n')
        tree = parse(source)
        session = conditional.ConditionalHighlightAnalyzer(context, self.namespaces)
        first = session.analyze(tree, 'main.typ')
        first_work = session._analyzer.ast_work
        second = session.analyze(tree, 'main.typ')
        self.assertFalse(first.diagnostics)
        self.assertEqual(first, second)
        self.assertEqual(session._analyzer.ast_work - first_work, 3 * len(tree.nodes))
        self.assertIn('Styles/styles.typ', session._analyzer.refusals)
        self.assertNotIn('Styles/styles.typ', session._analyzer.modules)

    def test_ancestry_dependent_refusals_do_not_poison_later_good_path(self):
        self.put('head.typ', b'#import "leaf.typ": *\n')
        self.put('leaf.typ', b'#import "@local/text-utils:0.1.2": *\n')
        source = b'#import "head.typ": *\n#highlighted[$x$]'
        self.put('main.typ', source)
        context = graph.load_import_graph(self.root, (('main.typ', source),))
        for active, depth in ((('leaf.typ',), graph.MAX_GRAPH_DEPTH),
                              (('a.typ', 'b.typ'), 3)):
            with self.subTest(active=active):
                session = conditional.ConditionalHighlightAnalyzer(context, self.namespaces)
                analyzer = session._analyzer
                with patch.object(graph, 'MAX_GRAPH_DEPTH', depth):
                    failure = analyzer.module('head.typ', active)
                self.assertTrue(failure.reason)
                self.assertFalse(failure.intrinsic_refusal)
                self.assertFalse(analyzer.refusals)
                result = session.analyze(parse(source), 'main.typ')
                self.assertEqual(len(result.diagnostics), 1, result.unresolved)

    def test_alias_route_and_binding_sizes_are_bounded(self):
        prefix = b'#import "@local/text-utils:0.1.2": *\n'
        aliases = b'#let a0 = highlighted\n' + b''.join(
            f'#let a{i} = a{i - 1}\n'.encode() for i in range(1, 12))
        source = prefix + b'#highlighted[$before$]\n' + aliases + b'#a11[$after$]'
        with patch.object(conditional, 'MAX_ROUTE_ENTRIES', 8):
            result = self.analyze(source)
            self.assertFalse(result.diagnostics)
            self.assertIn('route limit', result.unresolved[0])
            result = self.analyze(style=prefix + aliases)
            self.assertFalse(result.diagnostics)
            self.assertIn('route limit', result.unresolved[0])
        source = prefix + b''.join(f'#let a{i} = 1\n'.encode() for i in range(20)) + b'#highlighted[$x$]'
        with patch.object(conditional, 'MAX_BINDINGS', 16):
            result = self.analyze(source)
        self.assertFalse(result.diagnostics)
        self.assertIn('binding limit', result.unresolved[0])

    def test_ast_work_limit_and_definition_time_default_mutation_refuse(self):
        with patch.object(conditional, 'MAX_AST_WORK', 1):
            result = self.analyze()
        self.assertFalse(result.diagnostics)
        self.assertIn('AST work limit', result.unresolved[0])
        # Native Typst evaluates this named default while defining poison;
        # it changes the outer helper binding even without calling poison.
        poison = b'#let fake(body) = body\n#let poison(a:{highlighted=fake;none})=a\n'
        result = self.analyze(PREFIX + poison + b'#highlighted[$x$]')
        self.assertFalse(result.diagnostics)
        self.assertTrue(any('mutation' in reason for reason in result.unresolved))
        result = self.analyze(style=STYLE + poison)
        self.assertFalse(result.diagnostics)
        self.assertTrue(any('mutation' in reason for reason in result.unresolved))


if __name__ == '__main__':
    unittest.main()
