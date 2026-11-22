"""Real parser/rule interactions plus explicitly injected planner failures.

Fault-injected proposals below test machinery, never claim rule eligibility.
No consumer writer, package installation, compiler gate or complete=True exists.
"""
from collections import Counter
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import random
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
from book_provenance import EntrypointSnapshot
from candidate_convergence import (Candidate, Contribution, Step, _attributed, _compose,
                                   _moves, converge)
from edit_plan import Edit, plan
from import_graph import load_import_graph, rebase_graph
from import_rules import ORDER
from package_loader import load_packages
from reference_catalog import parse_reference_catalog
from script_rule import RULE_ID as SCRIPT, ScriptResult
from source_analysis import _analyze, _findings, _import_statement_moves
from static_resolver import imported_paths
from suppression import resolve as scopes
from syntax_client import EXECUTABLE, parse
from typst_lint import Diagnostic, SafetyError, load_rules

HIGHLIGHT = 'pde.text-only-highlight'
STANDALONE = 'pde.standalone-display'
CONTINUATION = 'pde.display-continuation-gap'
KNOWN = frozenset(rule['id'] for rule in load_rules()[0]['rules'])


def scope_records(tree, mapping):
    result = []
    for scope in scopes(tree, KNOWN):
        # Suppression resolver anchors a Hash + expression as one construct.
        kinds = [node.kind for node in tree.nodes if node.start == scope.start
                 and node.end == scope.end and node.kind not in ('Markup', 'Code')]
        if not kinds:
            kinds = [node.kind for node in tree.nodes if node.start == scope.start + 1
                     and node.end == scope.end]
        result.append((scope.rules, scope.reason, mapping.map_boundary(scope.start),
                       mapping.map_boundary(scope.end, side='left'),
                       mapping.map_offset(scope.directive_start), tuple(kinds)))
    return tuple(result)


class Identity:
    def map_offset(self, position):
        return position
    def map_boundary(self, position, *, side='right'):
        return position


class Proofs:
    """Test adapter for the parent's mandatory ownership validation callbacks."""
    def __init__(self, ignored=frozenset()):
        self.ignored = ignored

    def step(self, before, old, after, new, step):
        for rid, result in old.items():
            expected = Counter(re.sub(r'at byte (\d+)',
                lambda match: f'at byte {step.map_offset(int(match[1]))}', reason)
                for reason in result.skips)
            if Counter(new[rid].skips) - expected:
                raise SafetyError('Candidate introduces an analysis refusal')
        if _findings(before.source, old, (), step.map_offset, suppressed=True) != _findings(
                after.source, new, (), suppressed=True):
            raise SafetyError('Candidate changed directive suppression ownership')
        if scope_records(before, step) != scope_records(after, Identity()):
            raise SafetyError('Candidate changed exact directive targets')

    def final(self, candidate):
        before = _findings(candidate.baseline, candidate.initial_results, self.ignored, candidate.map_offset)
        after = _findings(candidate.content, candidate.final_results, self.ignored)
        if after - before:
            raise SafetyError('Candidate introduces a new finding')
        if _findings(candidate.baseline, candidate.initial_results, (), candidate.map_offset,
                     suppressed=True) != _findings(candidate.content, candidate.final_results,
                                                  (), suppressed=True):
            raise SafetyError('Candidate changed original suppression ownership')


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class CandidateConvergenceTests(unittest.TestCase):
    def run_source(self, source, *, packages=(), catalog=None, entrypoint=None, graph=None,
                   ignored=frozenset()):
        def analyze(tree):
            root = (replace(entrypoint, source=tree.source)
                    if type(entrypoint) is EntrypointSnapshot and entrypoint.path == 'main.typ'
                    and entrypoint.source == source else entrypoint)
            context = rebase_graph(graph, 'main.typ', source, tree.source)
            results = _analyze(tree, 'main.typ', 'pde', None, packages, catalog, root, context)
            return {rid: replace(result, edits=()) if rid in ignored else result
                    for rid, result in results.items()}
        def moves(before, after, edits, results):
            return _import_statement_moves(before, after, edits,
                    results[ORDER].edits if ORDER in results else ())
        tree = parse(source)
        proofs = Proofs(ignored)
        return converge(tree, analyze(tree), analyze, proofs.step, proofs.final,
                        movement_proofs=moves)

    def replay(self, candidate):
        planned = plan(candidate.baseline, (item.edit for item in candidate.edits))
        self.assertFalse(planned.deferred)
        self.assertEqual(planned.candidate(), candidate.content)
        current = candidate.baseline
        for step in candidate.steps:
            self.assertEqual(step.before, current)
            current = plan(current, step.edits).candidate()
            self.assertEqual(step.after, current)
        self.assertEqual(current, candidate.content)
        self.assertFalse(any(result.edits for result in candidate.final_results.values()))

    def test_actual_highlight_extraction_then_display_has_exact_multiowner_baseline_patch(self):
        source = b'Thus #highlight[$ X=Y $] and more.'
        candidate = self.run_source(source)
        self.assertEqual(candidate.content, b'Thus\n$ X=Y $\nand more.')
        self.assertEqual(candidate.passes, 2)
        self.assertEqual([item.edit for item in candidate.edits],
                         [Edit(4, 16, b'\n'), Edit(23, 25, b'\n')])
        for item in candidate.edits:
            self.assertEqual(item.primary_rule_id, HIGHLIGHT)
            self.assertEqual(item.rule_ids, tuple(sorted((HIGHLIGHT, STANDALONE))))
            self.assertEqual([cause.pass_number for cause in item.contributions], [1, 2])
        self.assertIsNone(candidate.map_offset(source.index(b'highlight')))
        self.assertEqual(candidate.content[candidate.map_offset(source.index(b'X')):][:1], b'X')
        self.assertEqual(candidate.map_boundary(len(source)), len(candidate.content))
        self.replay(candidate)
        self.assertEqual(self.run_source(candidate.content).passes, 0)

    def test_actual_standalone_then_continuation_has_distinct_owners(self):
        source = b'Thus $ A=B $\n\nHence the claim.\n'
        candidate = self.run_source(source)
        self.assertEqual(candidate.content, b'Thus\n$ A=B $\nHence the claim.\n')
        self.assertEqual(candidate.passes, 2)
        self.assertEqual([item.rule_ids for item in candidate.edits],
                         [(STANDALONE,), (CONTINUATION,)])
        self.assertTrue(candidate.initial_results[STANDALONE].diagnostics)
        self.assertFalse(candidate.initial_results[CONTINUATION].diagnostics)
        self.assertTrue(candidate.steps[1].applied)
        self.replay(candidate)

    def test_bom_crlf_unicode_comments_and_suppressed_ownership_survive_multiple_passes(self):
        source = (b'\xef\xbb\xbf// retained comment\r\n' + '따라서 '.encode()
                  + b'#highlight[$ X^i=Y $] and more.\r\n'
                  b'// typst-lint: ignore-next core.parenthesized-scripts -- intentional\r\n'
                  b'$z^j$\r\n')
        candidate = self.run_source(source)
        self.assertEqual(candidate.content, source.replace(b'#highlight[', b'').replace(b'$] ', b'$\r\n')
                         .replace('따라서 '.encode(), '따라서\r\n'.encode()).replace(b'X^i', b'X^(i)'))
        self.assertTrue(candidate.content.startswith(b'\xef\xbb\xbf// retained comment\r\n'))
        self.assertNotIn(b'\n', candidate.content.replace(b'\r\n', b''))
        self.assertEqual(len(candidate.final_results[SCRIPT].suppressed), 1)
        self.assertTrue(candidate.content.endswith(b'$z^j$\r\n'))
        self.replay(candidate)

    def test_captured_packages_catalog_entrypoint_and_graph_are_never_reread_or_mutated(self):
        source = (b'#import "@local/project-references:0.1.0": project-ref\n'
                  b'Thus #highlight[$ X=Y $] and more.\n#project-ref("pde", <missing>)\n')
        tree = parse(source)
        packages = load_packages((TOOL / 'tests/fixtures/reviewed-packages'), imported_paths(tree)).sources
        catalog = parse_reference_catalog(b'{"schema":1,"projects":{"pde":{"references":{}}}}', 'owned-test')
        entrypoint = EntrypointSnapshot('main.typ', source)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'main.typ').write_bytes(source)
            graph = load_import_graph(root, (('main.typ', source),))
            retained = (packages, catalog, entrypoint, graph)
            (root / 'main.typ').write_bytes(b'changed after capture')
            original_read = Path.read_bytes
            def read(path):
                if path != TOOL / 'rules/catalog.json':
                    raise AssertionError('No captured source/context rereads')
                return original_read(path)
            with patch.object(Path, 'read_bytes', read):
                candidate = self.run_source(source, packages=packages, catalog=catalog,
                                            entrypoint=entrypoint, graph=graph)
            self.assertEqual((packages, catalog, entrypoint, graph), retained)
            self.assertEqual(entrypoint.source, source)
            self.assertEqual(graph.source_for('main.typ').source, source)
            unresolved = candidate.final_results['core.project-ref-catalog-target'].diagnostics
            self.assertEqual(len(unresolved), 1)
            self.assertEqual(unresolved[0].explanation,
                             candidate.initial_results['core.project-ref-catalog-target'].diagnostics[0].explanation)
            self.assertEqual((root / 'main.typ').read_bytes(), b'changed after capture')
            self.replay(candidate)

    def test_real_import_movement_and_independent_highlight_passes_preserve_exact_statement_bytes(self):
        figures = b'#import "figures.typ": diagram\n'
        styles = b'#import "Styles/styles.typ": value\n'
        source = figures + styles + b'Thus #highlight[$ X=Y $] and more.\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'main.typ').write_bytes(source)
            (root / 'figures.typ').write_bytes(b'#let diagram = 2\n')
            (root / 'Styles').mkdir()
            (root / 'leaf.typ').write_bytes(b'#let value = 1\n')
            (root / 'Styles/styles.typ').write_bytes(b'#import "../leaf.typ": *\n')
            graph = load_import_graph(root, (('main.typ', source),))
            candidate = self.run_source(source, graph=graph)
        self.assertTrue(candidate.content.startswith(styles + figures))
        self.assertTrue(candidate.steps[0].moves)
        self.assertEqual(candidate.map_offset(source.index(b'figures.typ')),
                         candidate.content.index(b'figures.typ'))
        self.assertEqual(candidate.map_offset(source.index(b'Styles/styles.typ')),
                         candidate.content.index(b'Styles/styles.typ'))
        self.replay(candidate)

    def test_real_import_diagnostic_owner_survives_inside_a_merged_baseline_envelope(self):
        # The followup insertion is a coordinate-composition fixture, not a
        # claim that a production rule requests this extra import separator.
        figures = b'#import "figures.typ": diagram\n'
        styles = b'#import "Styles/styles.typ": value\n'
        source = figures + styles
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'main.typ').write_bytes(source)
            (root / 'figures.typ').write_bytes(b'#let diagram = 2\n')
            (root / 'leaf.typ').write_bytes(b'#let value = 1\n')
            (root / 'Styles').mkdir()
            (root / 'Styles/styles.typ').write_bytes(b'#import "../leaf.typ": *\n')
            graph = load_import_graph(root, (('main.typ', source),))
            first = self.run_source(source, graph=graph)
        finding = first.initial_results[ORDER].diagnostics[0]
        original_offset = sum(len(line) + 1 for line in source.split(b'\n')[:finding.line - 1]) + finding.column - 1
        insertion = Edit(0, 0, b'\n')
        extra = _attributed(insertion, (Contribution(2, STANDALONE, insertion),))
        patches, final = _compose(source, first.content, first.edits, extra)
        second = Step(first.content, final, (extra,), ())
        candidate = Candidate(source, final, 2, (*first.steps, second), patches,
                              first.initial_results, first.final_results, parse(final))
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0].rule_ids, tuple(sorted((ORDER, STANDALONE))))
        mapped = candidate.map_offset(original_offset)
        self.assertGreater(mapped, patches[0].edit.start)
        self.assertLess(mapped, len(patches[0].edit.replacement))
        self.assertTrue(final[mapped:].startswith(figures.rstrip(b'\n')))
        inner = source.index(b'figures.typ')
        self.assertEqual(candidate.map_offset(inner), final.index(b'figures.typ'))
        # A final-envelope-only map would mark both original owners consumed.
        self.assertTrue(patches[0].edit.start <= inner < patches[0].edit.end)
        self.assertEqual(plan(source, (item.edit for item in patches)).candidate(), final)

    def test_ignored_followup_is_not_invented_or_applied(self):
        source = b'Thus #highlight[$ X=Y $] and more.'
        candidate = self.run_source(source, ignored=frozenset({STANDALONE}))
        self.assertEqual(candidate.content, b'Thus $ X=Y $ and more.')
        self.assertEqual(candidate.passes, 1)
        self.assertTrue(candidate.final_results[STANDALONE].diagnostics)
        self.assertTrue(all(item.rule_ids == (HIGHLIGHT,) for item in candidate.edits))

    def harness(self, source, analyze, *, step=None, final=None, **kwargs):
        tree = parse(source)
        return converge(tree, analyze(tree), analyze, step or (lambda *args: None),
                        final or (lambda *args: None), **kwargs)

    def test_unresolved_conflicts_and_conflicts_remaining_after_progress_refuse(self):
        for independent in (False, True):
            calls = []
            def analyze(tree):
                extra = (Edit(len(tree.source), len(tree.source), b'!'),) if independent and tree.source == b'abcdef' else ()
                return {SCRIPT: ScriptResult(edits=(Edit(0, 2, b'x'), Edit(1, 3, b'y'), *extra))}
            with self.subTest(independent=independent), self.assertRaisesRegex(SafetyError, 'Unresolved conflicting'):
                self.harness(b'abcdef', analyze, step=lambda *args: calls.append(args[-1]))
            if independent:
                self.assertEqual(len(calls), 1)
                self.assertEqual(len(calls[0].deferred), 2)

    def test_parser_failures_and_changed_rule_coverage_refuse(self):
        def malformed(tree):
            return {SCRIPT: ScriptResult(edits=(Edit(0, len(tree.source), b'#let ='),))}
        with self.assertRaisesRegex(SafetyError, 'failed to parse'):
            self.harness(b'abc', malformed)
        for change in ('keys', 'rule-errors'):
            def changed(tree):
                if tree.source == b'abc':
                    return {SCRIPT: ScriptResult(edits=(Edit(0, 1, b'A'),))}
                return {} if change == 'keys' else {SCRIPT: ScriptResult(parse_errors=((0, 1),))}
            with self.subTest(change=change), self.assertRaisesRegex(SafetyError, 'coverage|parse failures'):
                self.harness(b'abc', changed)

    def test_repeated_states_and_eight_pass_bound_are_real_parser_checked(self):
        def oscillating(tree):
            return {SCRIPT: ScriptResult(edits=(Edit(0, 1, b'b' if tree.source == b'a' else b'a'),))}
        with self.assertRaisesRegex(SafetyError, 'Repeated candidate'):
            self.harness(b'a', oscillating)
        def endless(tree):
            return {SCRIPT: ScriptResult(edits=(Edit(len(tree.source), len(tree.source), b'x'),))}
        with self.assertRaisesRegex(SafetyError, 'Eight-pass'):
            self.harness(b'', endless)
        def eight(tree):
            return {SCRIPT: ScriptResult(edits=(Edit(len(tree.source), len(tree.source), b'x'),)
                                         if len(tree.source) < 8 else ())}
        candidate = self.harness(b'', eight)
        self.assertEqual((candidate.passes, candidate.content), (8, b'xxxxxxxx'))
        self.replay(candidate)

    def test_independent_zero_edit_reanalysis_must_match_exact_results(self):
        count = 0
        def nondeterministic(tree):
            nonlocal count
            count += 1
            return {SCRIPT: ScriptResult(skips=(str(count),))}
        with self.assertRaisesRegex(SafetyError, 'deterministic/idempotent'):
            self.harness(b'abc', nondeterministic)
        for limit in (-1, 9, True):
            with self.subTest(limit=limit), self.assertRaisesRegex(SafetyError, 'zero-to-eight'):
                self.harness(b'abc', lambda tree: {SCRIPT: ScriptResult()}, max_passes=limit)

    def test_callbacks_block_new_refusals_and_final_unowned_findings(self):
        proofs = Proofs()
        for error in ('refusal', 'finding'):
            def analyze(tree):
                if tree.source == b'abc':
                    return {SCRIPT: ScriptResult(edits=(Edit(0, 1, b'A'),))}
                result = ScriptResult(skips=('New analysis refusal',)) if error == 'refusal' else ScriptResult(
                    diagnostics=(Diagnostic(SCRIPT, 'warning', 'main.typ', 1, 2, 'New finding', 'test fault'),))
                return {SCRIPT: result}
            with self.subTest(error=error), self.assertRaisesRegex(SafetyError, 'analysis refusal|new finding'):
                self.harness(b'abc', analyze, step=proofs.step, final=proofs.final)

    def test_callbacks_block_changed_directive_target_even_when_no_finding_is_suppressed(self):
        source = b'// typst-lint: ignore-next pde.text-only-highlight -- reason\n#text[$x$]'
        start = source.index(b'#text[')
        def analyze(tree):
            return {SCRIPT: ScriptResult(edits=(Edit(start, start + 6, b''),
                                                Edit(len(source) - 1, len(source), b'')))
                    if tree.source == source else ScriptResult()}
        with self.assertRaisesRegex(SafetyError, 'exact directive targets'):
            self.harness(source, analyze, step=Proofs().step, final=Proofs().final)

    def test_source_bound_maps_never_guess_survival_inside_equal_replacement_text(self):
        before, after = b'abc', b'Abc'
        item = _attributed(Edit(0, 3, after), (Contribution(1, SCRIPT, Edit(0, 3, after)),))
        step = Step(before, after, (item,), ())
        self.assertIsNone(step.map_offset(1))  # Equal-looking b is not an ownership proof.
        self.assertEqual(step.map_boundary(0), 0)
        self.assertEqual(step.map_boundary(3), 3)
        self.assertIsNone(step.map_boundary(1))
        for position in (-1, 4, True):
            with self.subTest(position=position), self.assertRaises(SafetyError):
                step.map_offset(position)
        with self.assertRaises(SafetyError):
            step.map_boundary(1, side='ambiguous')
        with self.assertRaises(FrozenInstanceError):
            step.before = b'other'

    def test_import_movement_refuses_non_ast_duplicate_and_unowned_equality(self):
        raw = b'#import "a.typ": *'
        before = parse(raw + b'\n' + raw + b'\n')
        replacement = before.source + b'\n'
        after = parse(replacement)
        edit = Edit(0, len(before.source), replacement)
        with self.assertRaisesRegex(SafetyError, 'ambiguous statement'):
            _moves(before, after, (edit,), ((0, len(raw), 0),))
        for old, new, edits, proof in (
                (parse(b'abc'), parse(b'abc!'), (Edit(0, 3, b'abc!'),), ((0, 3, 0),)),
                (parse(raw), parse(raw), (), ((0, len(raw), 0),))):
            with self.subTest(proof=proof), self.assertRaises(SafetyError):
                _moves(old, new, edits, proof)

    def test_candidate_mappings_and_results_are_immutable_and_source_bounded(self):
        candidate = self.run_source(b'Thus $ A=B $\n\nHence the claim.\n')
        with self.assertRaises(TypeError):
            candidate.final_results[SCRIPT] = ScriptResult()
        with self.assertRaises(FrozenInstanceError):
            candidate.content = b'changed'
        for mapping in (candidate.map_offset, candidate.map_boundary):
            with self.assertRaises(SafetyError):
                mapping(len(candidate.baseline) + 1)
        forged = replace(parse(b'abc'), source=b'#let =')
        with self.assertRaisesRegex(SafetyError, 'exact parser verification'):
            converge(forged, {SCRIPT: ScriptResult()}, lambda tree: {}, lambda *args: None, lambda *args: None)


class BaselineCompositionTests(unittest.TestCase):
    def test_many_deterministic_edits_replay_without_diff_or_text_identity_guessing(self):
        randomizer = random.Random(20261107)
        for case in range(80):
            baseline = b'abcabcabc'
            patches, current = (), baseline
            for number in range(1, 25):
                start = randomizer.randrange(len(current) + 1)
                end = randomizer.randrange(start, len(current) + 1)
                replacement = randomizer.choice((b'', b'a', b'bc', b'abc', b'X'))
                edit = Edit(start, end, replacement)
                if current[start:end] == replacement:
                    continue
                rule = SCRIPT if number % 2 else HIGHLIGHT
                item = _attributed(edit, (Contribution(number, rule, edit),))
                with self.subTest(case=case, edit=number):
                    expected = current[:start] + replacement + current[end:]
                    patches, current = _compose(baseline, current, patches, item)
                    self.assertEqual(current, expected)
                    self.assertEqual(plan(baseline, (patch.edit for patch in patches)).candidate(), current)
                    self.assertFalse(plan(baseline, (patch.edit for patch in patches)).deferred)
                    for patch in patches:
                        self.assertEqual(patch.rule_ids, tuple(sorted({cause.rule_id for cause in patch.contributions})))


if __name__ == '__main__':
    unittest.main()
