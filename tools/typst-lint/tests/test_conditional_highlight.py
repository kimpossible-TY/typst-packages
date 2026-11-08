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
from typst_lint import InputError, digest

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
            source = (TOOL / 'tests/fixtures/reviewed-packages') / 'local' / name / version
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

    def analyze(self, source=MAIN, style=STYLE, figures=FIGURES, context=None, namespaces=None,
                rule_ids=(conditional.RULE_ID,), scoped_callbacks=False):
        tree = parse(source)
        self.assertFalse(tree.nodes[0].errors, source)
        return conditional.analyze(tree, 'main.typ',
            self.capture(source, style, figures) if context is None else context,
            self.namespaces if namespaces is None else namespaces, rule_ids=rule_ids,
            scoped_callbacks=scoped_callbacks)

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

    def test_combined_module_alias_and_named_or_wildcard_import(self):
        for declaration, call, operation in (
            (b'#import "@local/text-utils:0.1.2" as helpers: highlighted as hi\n',
             b'#hi[$x$]', 'combined-named-import'),
            (b'#import "@local/text-utils:0.1.2" as helpers: *\n',
             b'#highlighted[$x$]', 'combined-wildcard-import'),
            (b'#import "Styles/styles.typ" as styles: highlighted as hi\n',
             b'#hi[$x$]', 'combined-named-import')):
            with self.subTest(declaration=declaration):
                tree = parse(declaration + call)
                imported = next(i for i, n in enumerate(tree.nodes) if n.kind == 'ModuleImport')
                self.assertIsNone(graph.import_shape(tree, imported), 'Trusted grammar stays unchanged')
                result = self.analyze(declaration + call)
                self.assertEqual(len(result.diagnostics), 1, result.unresolved)
                self.assertEqual(result.diagnostics[0].route[-1].operation, operation)
        figures = FIGURES.replace(
            b'#import "@preview/fletcher:0.5.8": diagram, node, edge',
            b'#import "@preview/fletcher:0.5.8" as fletcher: diagram, node, edge')
        result = self.analyze(figures=figures)
        self.assertEqual(len(result.diagnostics), 1, result.unresolved)
        self.assertIn('@preview/fletcher:0.5.8', result.diagnostics[0].package_requests)

    def test_combined_alias_is_bound_before_items_and_later_shadows_win(self):
        # Native Typst's module alias is bound first; a same-name explicit or
        # wildcard item can overwrite it with the actual imported helper.
        for items in (b'highlighted', b'*'):
            source = b'#import "@local/text-utils:0.1.2" as highlighted: ' + items + b'\n#highlighted[$x$]'
            self.assertEqual(len(self.analyze(source).diagnostics), 1)
        for declaration in (
            b'#import "@local/text-utils:0.1.2" as highlighted: paragraph_tab\n',
            b'#import "@preview/fletcher:0.5.8" as highlighted: diagram, node, edge\n',
            b'#import "@local/text-utils:0.1.2" as helpers: paragraph_tab as highlighted\n'):
            with self.subTest(declaration=declaration):
                self.assertFalse(self.analyze(PREFIX + declaration + b'#highlighted[$x$]').diagnostics)
        source = (b'#import "@local/text-utils:0.1.2" as helpers: highlighted as hi\n'
                  b'#let hi(body) = body\n#hi[$x$]')
        self.assertFalse(self.analyze(source).diagnostics)

    def test_combined_unknown_wildcard_and_unsupported_items_refuse(self):
        for declaration in (
            b'#import "@preview/unreviewed:1.0.0" as module: *\n',
            b'#import "@local/text-utils:0.1.2" as module: nested.highlighted\n',
            b'#import "@local/text-utils:0.1.2" as module: highlighted as hi, paragraph_tab as hi\n'):
            with self.subTest(declaration=declaration):
                result = self.analyze(PREFIX + declaration + b'#highlighted[$x$]')
                self.assertFalse(result.diagnostics)
                self.assertTrue(result.unresolved)
        malformed = PREFIX + b'#import "@local/text-utils:0.1.2" as module: highlighted as\n#highlighted[$x$]'
        context = self.capture(malformed)
        result = conditional.analyze(parse(malformed), 'main.typ', context, self.namespaces)
        self.assertFalse(result.diagnostics)
        self.assertTrue(result.parse_errors)

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

    def test_recognized_literal_wrapper_bodies_have_conditional_context_routes(self):
        source = PREFIX + b'\n'.join(b'#' + name + b'[Text #highlighted[A $x$ B].]'
                                    for name in (b'proof', b'theorem', b'note'))
        result = self.analyze(source)
        self.assertEqual(len(result.diagnostics), 3, result.unresolved)
        self.assertFalse(result.unresolved)
        for finding, wrapper in zip(result.diagnostics, ('proof', 'theorem', 'note')):
            self.assertEqual(len(finding.content_context), 1)
            self.assertEqual(finding.content_context[0][0].request, conditional.MATH_BLOCKS)
            self.assertEqual(finding.content_context[0][-1].local_name, wrapper)
            self.assertIn('Every enclosing recognized literal content wrapper', finding.conditions[-1])
            self.assertFalse(finding.edit_permission)

    def test_literal_body_aliases_shadowing_and_sibling_scopes_are_ordered(self):
        source = (b'#import "Styles/styles.typ": proof as demonstrate, highlighted as hi\n'
                  b'#demonstrate[#let saved = hi\n#saved[$first$]\n'
                  b'#let hi(body) = body\n#hi[$shadowed$]]\n'
                  b'#demonstrate[#hi[$sibling$]]\n#hi[$root$]')
        result = self.analyze(source)
        self.assertEqual(len(result.diagnostics), 3, result.unresolved)
        self.assertEqual(result.diagnostics[0].route[-1].local_name, 'saved')
        self.assertEqual(result.diagnostics[0].content_context[0][-1].local_name, 'demonstrate')
        self.assertFalse(result.diagnostics[-1].content_context)

    def test_literal_body_local_imports_and_unknown_wildcards_do_not_escape(self):
        source = (PREFIX + b'#proof[#import "@local/text-utils:0.1.2": highlighted as local-hi\n'
                  b'#local-hi[$first$]\n#import "@preview/unknown:1.0.0": *\n'
                  b'#local-hi[$unresolved$]]\n#highlighted[$root$]')
        result = self.analyze(source)
        self.assertEqual(len(result.diagnostics), 2, result.unresolved)
        self.assertTrue(any('Wildcard package namespace unavailable' in reason for reason in result.unresolved))
        self.assertTrue(result.diagnostics[0].content_context)
        self.assertFalse(result.diagnostics[1].content_context)

    def test_literal_body_active_and_default_mutations_invalidate_outer_bindings(self):
        source = (PREFIX + b'#proof[#highlighted[$before$]\n#(highlighted = fake)\n'
                  b'#highlighted[$after$]]\n#highlighted[$outer-after$]')
        result = self.analyze(source)
        self.assertEqual(len(result.diagnostics), 1, result.unresolved)
        self.assertTrue(any('mutation' in reason for reason in result.unresolved))
        for body in (b'#let poison(a:{highlighted=fake;none})=a\n#highlighted[$x$]',
                     b'#note[#(highlighted=fake)]\n#highlighted[$x$]'):
            with self.subTest(body=body):
                result = self.analyze(PREFIX + b'#proof[' + body + b']\n#highlighted[$outer$]')
                self.assertFalse(result.diagnostics)
                self.assertTrue(any('mutation' in reason for reason in result.unresolved))

    def test_literal_body_suppression_and_unknown_or_deferred_wrappers(self):
        for body in (
            f'// typst-lint: ignore-next {conditional.RULE_ID} -- intentional\n#proof[#highlighted[$x$]]'.encode(),
            f'#proof[\n// typst-lint: ignore-next {conditional.RULE_ID} -- intentional\n#highlighted[$x$]]'.encode()):
            result = self.analyze(PREFIX + body)
            self.assertFalse(result.diagnostics)
            self.assertEqual(len(result.suppressed), 1)
        for body in (b'#let proof(body)=body\n#proof[#highlighted[$x$]]',
                     b'#let f()=[#proof[#highlighted[$x$]]]',
                     b'#{proof[#highlighted[$x$]]}',
                     b'#proof(..args)[#highlighted[$x$]]',
                     b'#proof(unknown: 1)[#highlighted[$x$]]',
                     b'#proof(title: "invalid")[#highlighted[$x$]]',
                     b'#note(unknown: 1)[#highlighted[$x$]]',
                     b'#note(body: [duplicate])[#highlighted[$x$]]'):
            with self.subTest(body=body):
                result = self.analyze(PREFIX + body)
                self.assertFalse(result.diagnostics)
                self.assertTrue(result.unresolved)
        malformed = PREFIX + b'#theorem(title: "A", title: "B")[#highlighted[$x$]]'
        result = conditional.analyze(parse(malformed), 'main.typ', self.capture(malformed), self.namespaces)
        self.assertFalse(result.diagnostics)
        self.assertTrue(result.parse_errors)
        for wrapper in (b'note', b'theorem'):
            result = self.analyze(PREFIX + b'#' + wrapper + b'(title: "Valid")[#highlighted[$x$]]')
            self.assertEqual(len(result.diagnostics), 1, result.unresolved)

    def test_nested_literal_scope_copy_work_uses_existing_session_budget(self):
        source = PREFIX + b'#proof[' * 12 + b'#highlighted[$x$]' + b']' * 12
        context = self.capture(source)
        tree = parse(source)
        # Cost the genuine shared module facts normally, then leave only a
        # small additional budget beyond the three root syntax scans.
        session = conditional.ConditionalHighlightAnalyzer(context, self.namespaces)
        session._analyzer.module('Styles/styles.typ', ('main.typ',))
        session._analyzer.module('Chapter1/figures.typ', ('main.typ',))
        root_work = session._analyzer.ast_work + 3 * len(tree.nodes)
        with patch.object(conditional, 'MAX_AST_WORK', root_work + 100):
            result = session.analyze(tree, 'main.typ')
            self.assertFalse(result.diagnostics)
            self.assertIn('AST work limit', result.unresolved[0])
            self.assertGreater(session._analyzer.ast_work, root_work)
            self.assertEqual(result, session.analyze(tree, 'main.typ'))

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

    def test_marker_rule_is_explicit_opt_in_and_default_highlight_is_unchanged(self):
        default = self.analyze()
        self.assertEqual(default, self.analyze(rule_ids=(conditional.RULE_ID,)))
        self.assertEqual([d.rule_id for d in default.diagnostics], [conditional.RULE_ID])
        both = self.analyze(rule_ids=conditional.SUPPORTED_RULE_IDS)
        self.assertEqual({d.rule_id for d in both.diagnostics}, set(conditional.SUPPORTED_RULE_IDS))
        marker = self.analyze(rule_ids=(conditional.MARKER_GAP,))
        self.assertEqual([d.rule_id for d in marker.diagnostics], [conditional.MARKER_GAP])
        self.assertEqual(marker.diagnostics[0].conditions, conditional.MARKER_CONDITIONS)
        self.assertFalse(marker.diagnostics[0].edit_permission)
        self.assertFalse(hasattr(marker, 'edits'))
        for selected in ((), ['pde.paragraph-marker-gap'], ('unknown',), (conditional.MARKER_GAP,) * 2):
            with self.subTest(selected=selected), self.assertRaises(InputError):
                conditional.ConditionalHighlightAnalyzer(self.capture(), self.namespaces, rule_ids=selected)

    def test_marker_export_and_immutable_alias_identity_not_spelling(self):
        source = (b'#import "@local/text-utils:0.1.2": paragraph-tab as p\n'
                  b'#let saved = p\n#let p = "shadowed"\n#saved\nText.\n')
        result = self.analyze(source, rule_ids=(conditional.MARKER_GAP,))
        self.assertEqual(len(result.diagnostics), 1, result.unresolved)
        finding = result.diagnostics[0]
        self.assertEqual(finding.route[0].imported_name, 'paragraph-tab')
        self.assertEqual(finding.route[-1].local_name, 'saved')
        self.assertEqual((finding.line, finding.column), (4, 1))
        for source in (b'#paragraph_tab\nText.',
                       PREFIX + b'#let paragraph_tab = "unrelated"\n#paragraph_tab\nText.',
                       PREFIX + b'#import "@preview/unknown:1.0.0": other as paragraph_tab\n#paragraph_tab\nText.'):
            with self.subTest(source=source):
                self.assertFalse(self.analyze(source, rule_ids=(conditional.MARKER_GAP,)).diagnostics)

    def test_marker_checks_whole_source_line_and_literal_body_openers(self):
        for body in (b'Text #paragraph_tab\nNext.', b'#paragraph_tab // same-line comment\nNext.',
                     b'#proof[#paragraph_tab\nNext.]', b'#proof[#paragraph_tab]',
                     b'#proof[\n#paragraph_tab]', b'#proof[\nText.\n#paragraph_tab]'):
            with self.subTest(body=body):
                result = self.analyze(PREFIX + body, rule_ids=(conditional.MARKER_GAP,))
                self.assertFalse(result.diagnostics)
                self.assertTrue(any('whole physical source line' in reason for reason in result.unresolved))
        # A body opener is not a blank line, even when Markup.start points to
        # only the newline after '['. The complete previous physical line wins.
        source = PREFIX + b'#proof[\n#paragraph_tab\nNext.\n]'
        result = self.analyze(source, rule_ids=(conditional.MARKER_GAP,))
        self.assertEqual(len(result.diagnostics), 1, result.unresolved)
        self.assertEqual(len(result.diagnostics[0].content_context), 1)
        self.assertFalse(self.analyze(PREFIX + b'#proof[\n\n#paragraph_tab\nNext.\n]',
                                     rule_ids=(conditional.MARKER_GAP,)).diagnostics)

    def test_marker_blank_lines_crlf_bom_and_bare_cr(self):
        for gap in (b'\n', b' \t\n', b'\r\n', b' \t\r\n'):
            with self.subTest(gap=gap):
                self.assertFalse(self.analyze(PREFIX + gap + b'#paragraph_tab\nText.',
                                             rule_ids=(conditional.MARKER_GAP,)).diagnostics)
        result = self.analyze(PREFIX + b' \t#paragraph_tab \t\r\nText.', rule_ids=(conditional.MARKER_GAP,))
        self.assertEqual(len(result.diagnostics), 1, result.unresolved)
        result = self.analyze(PREFIX + b'\xef\xbb\xbf\n#paragraph_tab\nText.', rule_ids=(conditional.MARKER_GAP,))
        self.assertEqual(len(result.diagnostics), 1, 'BOM is not a blank source line')
        result = self.analyze(PREFIX + b'#paragraph_tab\r', rule_ids=(conditional.MARKER_GAP,))
        self.assertFalse(result.diagnostics)
        self.assertTrue(any('Bare-CR' in reason for reason in result.unresolved))

    def test_marker_preceding_barriers_are_patterns_not_edit_permission(self):
        for preceding in (b'// preceding comment', b'#footnote[remark]', b'= Heading',
                          b'$ X=Y $', b'$ X=Y $ <equation-label>', b'#block[Content.]'):
            with self.subTest(preceding=preceding):
                result = self.analyze(PREFIX + preceding + b'\n#paragraph_tab\nText.',
                                      rule_ids=(conditional.MARKER_GAP,))
                self.assertEqual(len(result.diagnostics), 1, result.unresolved)
                self.assertFalse(result.diagnostics[0].edit_permission)
                self.assertIn('does not prove paragraph rendering', result.diagnostics[0].conditions[-1])

    def test_marker_following_heading_display_target_takes_precedence(self):
        for following in (b'\n// intervening comment\n= Heading', b'\n/* comment */\n$ X=Y $'):
            with self.subTest(following=following):
                result = self.analyze(PREFIX + b'#paragraph_tab' + following, rule_ids=(conditional.MARKER_GAP,))
                self.assertFalse(result.diagnostics)
                self.assertTrue(any('marker-target rule takes precedence' in reason for reason in result.unresolved))
        self.assertEqual(len(self.analyze(PREFIX + b'#paragraph_tab\n$x$ then text.',
                                         rule_ids=(conditional.MARKER_GAP,)).diagnostics), 1)

    def test_marker_local_scopes_unknown_contexts_and_active_mutation(self):
        source = (PREFIX + b'#proof[\n#let p = paragraph_tab\n#p\nText.\n]\n'
                  b'#proof[\n#let paragraph_tab = "unrelated"\n#paragraph_tab\nText.\n]\n'
                  b'#paragraph_tab\nRoot text.')
        result = self.analyze(source, rule_ids=(conditional.MARKER_GAP,))
        self.assertEqual(len(result.diagnostics), 2, result.unresolved)
        self.assertTrue(result.diagnostics[0].content_context)
        self.assertFalse(result.diagnostics[1].content_context)
        for body in (b'#let callback()=[\n#paragraph_tab\nText.]',
                     b'#block[\n#paragraph_tab\nText.]', b'#proof[\n#block[\n#paragraph_tab\nText.]]'):
            with self.subTest(body=body):
                result = self.analyze(PREFIX + body, rule_ids=(conditional.MARKER_GAP,))
                self.assertFalse(result.diagnostics)
                self.assertTrue(any('unresolved binding/execution context' in reason for reason in result.unresolved))
        source = PREFIX + b'#proof[\n#(paragraph_tab = fake)\n#paragraph_tab\nText.]\n#paragraph_tab\nRoot.'
        result = self.analyze(source, rule_ids=(conditional.MARKER_GAP,))
        self.assertFalse(result.diagnostics)
        self.assertTrue(any('mutation' in reason for reason in result.unresolved))

    def test_marker_uses_hash_suppression_anchor_and_shared_source_validation(self):
        for rule in (conditional.MARKER_GAP, conditional.RULE_ID):
            source = PREFIX + f'// typst-lint: ignore-next {rule} -- intentional\n#paragraph_tab\nText.'.encode()
            result = self.analyze(source, rule_ids=(conditional.MARKER_GAP,))
            self.assertEqual(len(result.suppressed), int(rule == conditional.MARKER_GAP))
            self.assertEqual(len(result.diagnostics), int(rule != conditional.MARKER_GAP))
        source = PREFIX + b'#paragraph_tab\nText.'
        result = self.analyze(source + b' ', context=self.capture(source), rule_ids=(conditional.MARKER_GAP,))
        self.assertFalse(result.diagnostics)
        self.assertTrue(any('snapshot' in reason for reason in result.unresolved))

    def test_opt_in_rules_have_separate_and_shared_coverage_reasons(self):
        source = PREFIX + b'#let callback()=[#highlighted[$x$]\n#paragraph_tab\nText.]'
        result = self.analyze(source, rule_ids=conditional.SUPPORTED_RULE_IDS)
        highlight = result.reasons_for(conditional.RULE_ID)
        marker = result.reasons_for(conditional.MARKER_GAP)
        self.assertEqual(len(highlight), 1, highlight)
        self.assertEqual(len(marker), 1, marker)
        self.assertIn('helper call', highlight[0])
        self.assertNotIn('paragraph-marker', highlight[0])
        self.assertIn('paragraph-marker', marker[0])
        self.assertNotIn('helper call', marker[0])
        self.assertEqual(set(result.unresolved), set(highlight + marker))
        source = PREFIX + b'#import "@preview/unknown:1.0.0": *'
        result = self.analyze(source, rule_ids=conditional.SUPPORTED_RULE_IDS)
        self.assertEqual(result.reasons_for(conditional.RULE_ID), result.reasons_for(conditional.MARKER_GAP))
        self.assertTrue(result.reasons_for(conditional.MARKER_GAP))
        with patch.object(conditional, 'MAX_AST_WORK', 1):
            result = self.analyze(rule_ids=conditional.SUPPORTED_RULE_IDS)
        self.assertEqual(result.reasons_for(conditional.RULE_ID), result.reasons_for(conditional.MARKER_GAP))
        self.assertIn('AST work limit', result.unresolved[0])

    def test_scoped_callbacks_are_opt_in_with_literal_returns_and_bound_provenance(self):
        source = PREFIX + b'#local-tag-scope(s => ([\n#highlighted[A $x$ B]\n#paragraph_tab\nText.\n]))'
        default = self.analyze(source, rule_ids=conditional.SUPPORTED_RULE_IDS)
        self.assertFalse(default.diagnostics)
        self.assertEqual(default, self.analyze(source, rule_ids=conditional.SUPPORTED_RULE_IDS,
                                              scoped_callbacks=False))
        result = self.analyze(source, rule_ids=conditional.SUPPORTED_RULE_IDS, scoped_callbacks=True)
        self.assertEqual({d.rule_id for d in result.diagnostics}, set(conditional.SUPPORTED_RULE_IDS), result.unresolved)
        for finding in result.diagnostics:
            self.assertEqual(len(finding.callback_context), 1)
            callback = finding.callback_context[0]
            self.assertEqual(callback.parameters, ('s',))
            self.assertEqual(callback.content_context_index, 0)
            self.assertTrue(source[callback.call_start:callback.call_end].startswith(b'local-tag-scope('))
            self.assertTrue(source[callback.body_start:callback.body_end].startswith(b'[\n'))
            self.assertEqual(finding.content_context[0][0].request, conditional.SCOPED_ANNOTATIONS)
            self.assertTrue(all(condition in finding.conditions for condition in conditional.CALLBACK_CONDITIONS))
            self.assertFalse(finding.edit_permission)
        json.dumps(asdict(result))
        with self.assertRaises(FrozenInstanceError):
            result.diagnostics[0].callback_context[0].parameters = ('forged',)
        for invalid in (1, None, 'true'):
            with self.subTest(invalid=invalid), self.assertRaises(InputError):
                conditional.ConditionalHighlightAnalyzer(self.capture(), self.namespaces, scoped_callbacks=invalid)

    def test_scoped_callbacks_nest_with_literal_wrappers_and_exact_options(self):
        source = (PREFIX + b'#proof[#local-tag-scope((s) => [#local-scope-annotations(t => [\n'
                  b'#highlighted[$x$]\n#paragraph_tab\nText.\n], parent:auto, name:auto)], prefix:auto) ]')
        result = self.analyze(source, rule_ids=conditional.SUPPORTED_RULE_IDS, scoped_callbacks=True)
        self.assertEqual(len(result.diagnostics), 2, result.unresolved)
        for finding in result.diagnostics:
            self.assertEqual(len(finding.content_context), 3)
            self.assertEqual([c.content_context_index for c in finding.callback_context], [1, 2])
            self.assertEqual([c.parameters for c in finding.callback_context], [('s',), ('t',)])
            self.assertIn('Every enclosing recognized literal content wrapper', finding.conditions[3 if finding.rule_id == conditional.RULE_ID else 4])

    def test_scoped_callback_parameters_shadow_inherited_helper_marker_and_wrapper(self):
        cases = (
            (b'highlighted', b'#highlighted[$inner$]', b'#highlighted[$root$]', conditional.RULE_ID),
            (b'paragraph_tab', b'\n#paragraph_tab\nText.\n', b'#paragraph_tab\nRoot.', conditional.MARKER_GAP),
            (b'proof', b'#proof[#highlighted[$inner$]]', b'#proof[#highlighted[$root$]]', conditional.RULE_ID),
            (b'local-tag-scope', b'#local-tag-scope(s=>[#highlighted[$inner$]])', b'#highlighted[$root$]', conditional.RULE_ID),
        )
        for parameter, body, after, rule in cases:
            with self.subTest(parameter=parameter):
                source = PREFIX + b'#local-tag-scope(' + parameter + b' => [' + body + b'])\n' + after
                result = self.analyze(source, rule_ids=(rule,), scoped_callbacks=True)
                self.assertEqual(len(result.diagnostics), 1, result.unresolved)
                self.assertFalse(result.diagnostics[0].callback_context)

    def test_scoped_callback_creation_bindings_aliases_and_local_scope_isolation(self):
        source = (PREFIX + b'#let saved = highlighted\n#local-tag-scope(s => ['
                  b'#let highlighted(body)=body\n#saved[$captured$]\n#highlighted[$shadowed$]])\n'
                  b'#highlighted[$root$]\n#let highlighted(body)=body\n'
                  b'#local-tag-scope(s => [#highlighted[$later-shadowed$]])')
        result = self.analyze(source, scoped_callbacks=True)
        self.assertEqual(len(result.diagnostics), 2, result.unresolved)
        self.assertTrue(result.diagnostics[0].callback_context)
        self.assertFalse(result.diagnostics[1].callback_context)

    def test_scoped_callback_mutation_is_local_but_definition_defaults_invalidate_outer(self):
        source = (PREFIX + b'#local-tag-scope(s => [#(highlighted=fake)\n#highlighted[$inner$]])\n'
                  b'#highlighted[$outer$]')
        result = self.analyze(source, scoped_callbacks=True)
        self.assertEqual(len(result.diagnostics), 1, result.unresolved)
        self.assertFalse(result.diagnostics[0].callback_context)
        self.assertTrue(any('mutation' in reason for reason in result.unresolved))
        source = (PREFIX + b'#local-tag-scope((s:{highlighted=fake;none}) => [#highlighted[$inner$]])\n'
                  b'#highlighted[$outer$]')
        result = self.analyze(source, scoped_callbacks=True)
        self.assertFalse(result.diagnostics)
        self.assertTrue(any('mutation' in reason for reason in result.unresolved))

    def test_scoped_callback_ambiguous_signatures_and_unknown_contexts_are_refused(self):
        calls = (
            b'#local-tag-scope((s,t)=>[#highlighted[$x$]])',
            b'#local-tag-scope(()=>[#highlighted[$x$]])',
            b'#local-tag-scope((..s)=>[#highlighted[$x$]])',
            b'#local-tag-scope((s:1)=>[#highlighted[$x$]])',
            b'#local-tag-scope(s=>{[#highlighted[$x$]]})',
            b'#local-tag-scope(s=>[#highlighted[$x$]],unknown:1)',
            b'#local-tag-scope(s=>[#highlighted[$x$]],prefix:"fixed")',
            b'#local-tag-scope(s=>[#highlighted[$x$]],namespace:auto)',
            b'#local-scope-annotations(s=>[#highlighted[$x$]],parent:s)',
            b'#local-tag-scope(body:s=>[#highlighted[$x$]])',
            b'#local-tag-scope(s=>[#highlighted[$x$]], t=>[])',
            b'#local-tag-scope(s=>[#block[#highlighted[$x$]]])',
            b'#local-tag-scope(s=>[#context [#highlighted[$x$]]])',
        )
        for call in calls:
            with self.subTest(call=call):
                result = self.analyze(PREFIX + call, scoped_callbacks=True)
                self.assertFalse(result.diagnostics)
                self.assertTrue(result.unresolved)
        source = PREFIX + b'#let local-tag-scope(body)=body\n#local-tag-scope(s=>[#highlighted[$x$]])'
        self.assertFalse(self.analyze(source, scoped_callbacks=True).diagnostics)

    def test_context_without_detected_mutation_preserves_callback_bindings(self):
        source = (PREFIX + b'#local-tag-scope(s => [\n'
                  b'#context { let highlighted = "local"; [inside] }\n'
                  b'#highlighted[$x^2$]\n])\n')
        result = self.analyze(source, scoped_callbacks=True)
        self.assertEqual(len(result.diagnostics), 1, result.unresolved)
        self.assertTrue(result.diagnostics[0].callback_context)
        self.assertTrue(any('Contextual body' in reason for reason in result.unresolved))
        root = PREFIX + b'#context { let highlighted = "local"; [inside] }\n#highlighted[$x^2$]'
        self.assertFalse(self.analyze(root, scoped_callbacks=True).diagnostics)

    def test_context_with_active_mutation_still_invalidates_callback_bindings(self):
        source = (PREFIX + b'#local-tag-scope(s => [\n'
                  b'#context { highlighted = other }\n#highlighted[$x^2$]\n])\n')
        result = self.analyze(source, scoped_callbacks=True)
        self.assertFalse(result.diagnostics)
        self.assertTrue(any('mutation' in reason for reason in result.unresolved))

    def test_scoped_callback_suppression_and_scope_budget(self):
        for source in (
            PREFIX + f'// typst-lint: ignore-next {conditional.RULE_ID} -- intentional\n#local-tag-scope(s=>[#highlighted[$x$]])'.encode(),
            PREFIX + f'#local-tag-scope(s=>[\n// typst-lint: ignore-next {conditional.RULE_ID} -- intentional\n#highlighted[$x$]])'.encode()):
            result = self.analyze(source, scoped_callbacks=True)
            self.assertFalse(result.diagnostics)
            self.assertEqual(len(result.suppressed), 1)
        source = PREFIX + b'#local-tag-scope(s=>[' * 12 + b'#highlighted[$x$]' + b'])' * 12
        context, tree = self.capture(source), parse(source)
        session = conditional.ConditionalHighlightAnalyzer(context, self.namespaces, scoped_callbacks=True)
        session._analyzer.module('Styles/styles.typ', ('main.typ',))
        session._analyzer.module('Chapter1/figures.typ', ('main.typ',))
        root_work = session._analyzer.ast_work + 3 * len(tree.nodes)
        with patch.object(conditional, 'MAX_AST_WORK', root_work + 150):
            result = session.analyze(tree, 'main.typ')
            self.assertFalse(result.diagnostics)
            self.assertIn('AST work limit', result.unresolved[0])
            self.assertEqual(result, session.analyze(tree, 'main.typ'))

    def test_scoped_callback_returns_withhold_abandoned_and_unreachable_hints(self):
        for returning in (b'#return []', b'#proof[#return []]', b'#block[#return []]',
                          b'#context { return [] }',
                          b'#let deferred(a:{return [];none})=a'):
            with self.subTest(returning=returning):
                source = (PREFIX + b'#local-tag-scope(s=>[#highlighted[$before$]\n' + returning
                          + b'\n#import "@local/text-utils:0.1.2": highlighted\n#highlighted[$unreachable$]])\n'
                          b'#highlighted[$caller$]')
                result = self.analyze(source, scoped_callbacks=True)
                self.assertEqual(len(result.diagnostics), 1, result.unresolved)
                self.assertFalse(result.diagnostics[0].callback_context)
                self.assertTrue(any('Active return' in reason for reason in result.unresolved))
        # A deferred closure's body does not exit the current callback. A
        # return from an inner scoped callback exits that inner invocation only.
        source = (PREFIX + b'#local-tag-scope(s=>[#let later()=[#return []]\n'
                  b'#local-scope-annotations(t=>[#return []])\n#highlighted[$reachable$]])')
        result = self.analyze(source, scoped_callbacks=True)
        self.assertEqual(len(result.diagnostics), 1, result.unresolved)
        self.assertEqual(len(result.diagnostics[0].callback_context), 1)


if __name__ == '__main__':
    unittest.main()
