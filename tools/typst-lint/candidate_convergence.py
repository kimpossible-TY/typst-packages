"""Bounded parser-backed candidate composition, never writes or complete analysis.

The caller owns immutable analysis context and rule/diagnostic/directive proofs.
This module retains exact byte plans, causal rule attribution and source-bound
coordinate maps. It deliberately does not use edit_plan.Analysis/complete=True.
"""
from dataclasses import dataclass, is_dataclass
from types import MappingProxyType
from collections.abc import Mapping

from edit_plan import Edit, conflict, plan
from syntax_client import Tree, parse
from typst_lint import SafetyError


@dataclass(frozen=True)
class Contribution:
    """One real rule proposal, in its own pass's source coordinates."""
    pass_number: int
    rule_id: str
    edit: Edit


@dataclass(frozen=True)
class AttributedEdit:
    edit: Edit
    primary_rule_id: str
    rule_ids: tuple[str, ...]
    contributions: tuple[Contribution, ...]


def _attributed(edit, contributions):
    causes = tuple(sorted(set(contributions), key=lambda c: (
        c.pass_number, c.edit.start, c.edit.end, c.edit.replacement, c.rule_id)))
    if not causes:
        raise SafetyError('Candidate edit has no rule attribution')
    return AttributedEdit(edit, causes[0].rule_id,
                          tuple(sorted({c.rule_id for c in causes})), causes)


@dataclass(frozen=True)
class Step:
    """A source-bound edit pass; moves are independently checked root imports."""
    before: bytes
    after: bytes
    applied: tuple[AttributedEdit, ...]
    deferred: tuple[AttributedEdit, ...]
    moves: tuple[tuple[int, int, int], ...] = ()

    @property
    def edits(self):
        return tuple(item.edit for item in self.applied)

    def _position(self, position):
        if type(position) is not int or not 0 <= position <= len(self.before):
            raise SafetyError('Coordinate lies outside its exact source')

    def map_offset(self, position):
        """Map an original byte, or EOF; None means that byte was consumed.

        Insertions have right affinity. Equal-looking replacement text is not
        proof of byte survival. Only checked AST import movement overrides it.
        """
        self._position(position)
        for start, end, target in self.moves:
            if start <= position < end:
                return target + position - start
        if any(edit.start <= position < edit.end for edit in self.edits):
            return None
        return position + sum(len(edit.replacement) - (edit.end - edit.start)
                              for edit in self.edits if edit.end <= position)

    def map_boundary(self, position, *, side='right'):
        """Map a construct endpoint, with explicit insertion affinity.

        A replacement's exact outer boundaries survive; an interior boundary
        does not. This is distinct from claiming a replaced byte survived.
        """
        self._position(position)
        if side not in ('left', 'right'):
            raise SafetyError('Boundary affinity must be left or right')
        moved = {target + position - start for start, end, target in self.moves
                 if start <= position <= end}
        if len(moved) > 1:
            raise SafetyError('Ambiguous import movement boundary')
        if moved:
            return moved.pop()
        if any(edit.start < position < edit.end for edit in self.edits):
            return None
        return position + sum(len(edit.replacement) - (edit.end - edit.start)
                              for edit in self.edits if edit.end < position
                              or (edit.end == position and
                                  (edit.start < edit.end or side == 'right')))


@dataclass(frozen=True)
class Candidate:
    baseline: bytes
    content: bytes
    passes: int
    steps: tuple[Step, ...]
    edits: tuple[AttributedEdit, ...]
    initial_results: Mapping
    final_results: Mapping
    final_tree: Tree

    def map_offset(self, position):
        if type(position) is not int or not 0 <= position <= len(self.baseline):
            raise SafetyError('Coordinate lies outside original baseline')
        for step in self.steps:
            position = step.map_offset(position)
            if position is None:
                break
        return position

    def map_boundary(self, position, *, side='right'):
        if type(position) is not int or not 0 <= position <= len(self.baseline):
            raise SafetyError('Coordinate lies outside original baseline')
        if side not in ('left', 'right'):
            raise SafetyError('Boundary affinity must be left or right')
        for step in self.steps:
            position = step.map_boundary(position, side=side)
            if position is None:
                break
        return position


def _snapshot(results):
    if not isinstance(results, Mapping) or any(type(rid) is not str or not rid for rid in results):
        raise SafetyError('Expected rule-ID analysis mapping')
    copied = dict(results)
    for result in copied.values():
        if not is_dataclass(result) or not result.__dataclass_params__.frozen:
            raise SafetyError('Expected frozen rule analysis results')
        if not all(type(getattr(result, field, None)) is tuple for field in
                   ('diagnostics', 'edits', 'skips', 'parse_errors', 'suppressed')):
            raise SafetyError('Expected immutable rule-result fields')
        if result.parse_errors:
            raise SafetyError('Candidate has rule parse failures')
    return MappingProxyType(copied)


def _proposals(source, results, pass_number):
    proposals = {}
    for rid, result in results.items():
        for edit in result.edits:
            # Validate each proposal before using it as a dictionary key.
            checked = plan(source, (edit,))
            if checked.edits:
                proposals.setdefault(edit, []).append(Contribution(pass_number, rid, edit))
    planned = plan(source, proposals)
    attributed = {edit: _attributed(edit, causes) for edit, causes in proposals.items()}
    return planned, tuple(attributed[e] for e in planned.edits), tuple(
        attributed[e] for e in planned.deferred)


def _root_imports(tree):
    result = {}
    siblings = tree.nodes[0].children
    for offset, index in enumerate(siblings):
        node = tree.nodes[index]
        if node.kind != 'ModuleImport' or offset == 0:
            continue
        prefix = tree.nodes[siblings[offset - 1]]
        if prefix.kind == 'Hash' and prefix.end == node.start:
            result[(prefix.start, node.end)] = tree.source[prefix.start:node.end]
    return result


def _moves(before, after, edits, proposals):
    """Validate caller AST proofs, never discover moves by matching text."""
    if type(proposals) is not tuple:
        raise SafetyError('Import movement proofs must be immutable tuples')
    old, new = _root_imports(before), _root_imports(after)
    spans = []
    shift = 0
    for edit in edits:
        spans.append((edit, edit.start + shift, edit.start + shift + len(edit.replacement)))
        shift += len(edit.replacement) - (edit.end - edit.start)
    proven = []
    for item in proposals:
        if type(item) is not tuple or len(item) != 3 or any(type(p) is not int for p in item):
            raise SafetyError('Invalid import movement proof')
        start, end, target = item
        length = end - start
        if ((start, end) not in old or (target, target + length) not in new
                or old[(start, end)] != new[(target, target + length)]):
            raise SafetyError('Import movement does not preserve exact root AST statement')
        regions = [(edit, left, right) for edit, left, right in spans
                   if edit.start <= start < end <= edit.end
                   and left <= target < target + length <= right]
        if len(regions) != 1:
            raise SafetyError('Import movement is not owned by one exact edit')
        edit, left, right = regions[0]
        raw = old[(start, end)]
        if (sum(value == raw for (a, b), value in old.items()
                if edit.start <= a and b <= edit.end) != 1
                or sum(value == raw for (a, b), value in new.items()
                       if left <= a and b <= right) != 1):
            raise SafetyError('Import movement has ambiguous statement identity')
        if any(max(start, a) < min(end, b) or max(target, c) < min(target + length, c + b - a)
               for a, b, c in proven):
            raise SafetyError('Overlapping import movement proofs')
        proven.append(item)
    return tuple(sorted(proven))


def _layout(patches):
    shift = 0
    result = []
    for patch in patches:
        edit = patch.edit
        start = edit.start + shift
        result.append((patch, start, start + len(edit.replacement)))
        shift += len(edit.replacement) - (edit.end - edit.start)
    return result


def _original_boundary(position, layout):
    """Invert only exact outer patch boundaries and unchanged gaps."""
    shift = 0
    for patch, start, end in layout:
        edit = patch.edit
        if position < start:
            return position - shift
        if position == start:
            return edit.start
        if position < end:
            raise SafetyError('Cannot infer baseline position inside generated bytes')
        if position == end:
            return edit.end
        shift += len(edit.replacement) - (edit.end - edit.start)
    return position - shift


def _replay(baseline, patches):
    pieces, cursor = [], 0
    for patch in patches:
        edit = patch.edit
        pieces.extend((baseline[cursor:edit.start], edit.replacement))
        cursor = edit.end
    pieces.append(baseline[cursor:])
    return b''.join(pieces)


def _compose(baseline, current, patches, item):
    """Replay a pass edit into baseline envelopes, with no diff/text guessing.

    Expand across any prior generated segment it touches. Every original gap
    outside these explicitly causal envelopes remains byte-for-byte unchanged.
    """
    edit = item.edit
    layout = _layout(patches)
    start, end = edit.start, edit.end
    touched = set()
    while True:
        old = (start, end, len(touched))
        region = Edit(start, end, b'')
        for index, (_, left, right) in enumerate(layout):
            if conflict(region, Edit(left, right, b'')):
                touched.add(index)
                start, end = min(start, left), max(end, right)
        a = _original_boundary(start, layout)
        b = _original_boundary(end, layout)
        # Collapsed deleted regions have two original boundaries at one current
        # position. Include their whole original envelope. A baseline insertion
        # can also touch this envelope while lying just outside the current
        # region, so close conflicts in BOTH coordinate systems.
        if touched:
            a = min(a, *(layout[i][0].edit.start for i in touched))
            b = max(b, *(layout[i][0].edit.end for i in touched))
        for index, (patch, left, right) in enumerate(layout):
            if conflict(Edit(a, b, b''), patch.edit):
                touched.add(index)
                start, end = min(start, left), max(end, right)
        if old == (start, end, len(touched)):
            break
    replacement = current[start:edit.start] + edit.replacement + current[edit.end:end]
    causes = list(item.contributions)
    for index in touched:
        causes.extend(layout[index][0].contributions)
    merged = _attributed(Edit(a, b, replacement), causes)
    remaining = [patch for i, patch in enumerate(patches) if i not in touched]
    remaining.append(merged)
    remaining.sort(key=lambda patch: patch.edit)
    if any(conflict(left.edit, right.edit) for left, right in zip(remaining, remaining[1:])):
        raise SafetyError('Composed original-baseline edits conflict')
    # A cancellation may produce an exact no-op envelope. It has no prospective
    # patch, but its causal proposals remain in immutable Step records.
    remaining = tuple(patch for patch in remaining
                      if baseline[patch.edit.start:patch.edit.end] != patch.edit.replacement)
    expected = current[:edit.start] + edit.replacement + current[edit.end:]
    if _replay(baseline, remaining) != expected:
        raise SafetyError('Original-baseline edit composition failed exact replay')
    return remaining, expected


def converge(tree, initial_results, analyze_candidate, validate_step, validate_final,
             *, movement_proofs=None, max_passes=8):
    """Reparse/reanalyze at most eight edit passes and an independent zero fix.

    analyze_candidate(Tree) must use one captured immutable context, deriving
    candidate-only entrypoint/import-graph evidence from that same baseline.
    validate_step(before_tree, before_results, after_tree, after_results, Step)
    and validate_final(Candidate) are mandatory proof callbacks. They must raise
    SafetyError on changed coverage/refusals/directives or unowned final
    findings. They confer no completeness, writer lease or compiler permission.

    movement_proofs(before_tree, after_tree, applied_edits, before_results)
    may return exact root-import movement tuples (start, end, target). Supplied
    statements are independently checked for AST shape, unique byte identity and
    ownership by one applied replacement; missing proofs never imply survival.
    """
    if type(max_passes) is not int or not 0 <= max_passes <= 8:
        raise SafetyError('Candidate convergence requires a zero-to-eight pass limit')
    if type(tree) is not Tree or not callable(analyze_candidate) or not callable(validate_step) or not callable(validate_final):
        raise SafetyError('Parser tree and explicit analysis/proof callbacks required')
    plan(tree.source, ())
    if parse(tree.source, tree.mode) != tree or tree.nodes[0].errors:
        raise SafetyError('Candidate baseline failed exact parser verification')
    initial = _snapshot(initial_results)
    current_tree, results = tree, initial
    seen, steps, patches = {tree.source}, [], ()
    for iteration in range(max_passes + 1):
        planned, applied, deferred = _proposals(current_tree.source, results, iteration + 1)
        if not planned.edits and not planned.deferred:
            candidate = Candidate(tree.source, current_tree.source, iteration,
                                  tuple(steps), patches, initial, results, current_tree)
            validate_final(candidate)
            again_tree = parse(candidate.content, tree.mode)
            if again_tree.nodes[0].errors:
                raise SafetyError('Independent candidate failed to parse')
            again = _snapshot(analyze_candidate(again_tree))
            second, _, _ = _proposals(candidate.content, again, iteration + 1)
            if second.edits or second.deferred or again != results:
                raise SafetyError('Candidate is not deterministic/idempotent')
            return candidate
        if iteration == max_passes:
            raise SafetyError('Eight-pass convergence limit exceeded')
        if not planned.edits:
            raise SafetyError('Unresolved conflicting candidate edits')
        content = planned.candidate()
        if content in seen:
            raise SafetyError('Repeated candidate state')
        checked = parse(content, tree.mode)
        if checked.nodes[0].errors:
            raise SafetyError('Combined candidate failed to parse')
        final = _snapshot(analyze_candidate(checked))
        if final.keys() != initial.keys():
            raise SafetyError('Combined candidate changed available rule coverage')
        proposed_moves = movement_proofs(current_tree, checked, planned.edits, results) if movement_proofs else ()
        step = Step(current_tree.source, content, applied, deferred,
                    _moves(current_tree, checked, planned.edits, proposed_moves))
        validate_step(current_tree, results, checked, final, step)
        # Right-to-left replay preserves every pass edit's original coordinates.
        replay = current_tree.source
        if not patches and replay == tree.source:
            patches, replay = applied, content
        else:
            for item in reversed(applied):
                patches, replay = _compose(tree.source, replay, patches, item)
        if replay != content or plan(tree.source, (p.edit for p in patches)).candidate() != content:
            raise SafetyError('Candidate baseline replay differs from parsed source')
        steps.append(step)
        seen.add(content)
        current_tree, results = checked, final
    raise AssertionError('Unreachable')
