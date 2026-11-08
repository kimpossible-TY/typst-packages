"""Verify independent, read-only previews of source display-gap edits."""
from bisect import bisect_left, bisect_right

from document_display import RULE_ID, STANDALONE, analyze_all, preview_gap_edits
from edit_plan import Edit, plan
from suppression import resolve
from syntax_client import parse
from typst_lint import InputError, SafetyError, digest


def _syntax_signature(tree):
    """Retain all non-whitespace syntax, parentage, operands and leaf bytes."""
    nodes = tree.nodes
    kept = [i for i, node in enumerate(nodes) if node.kind not in ('Space', 'Parbreak')]
    remap = {old: new for new, old in enumerate(kept)}
    parents = {child: parent for parent, node in enumerate(nodes) for child in node.children}
    result = []
    for i in kept:
        node = nodes[i]
        parent = parents.get(i)
        if parent is not None and parent not in remap:
            raise SafetyError('Unexpected syntax inside whitespace')
        if any(target not in remap for _, target in node.operands):
            raise SafetyError('An operand refers to whitespace syntax')
        operands = tuple((name, remap[target]) for name, target in node.operands)
        result.append((node.kind, remap.get(parent), node.display, node.errors, operands,
                       tree.source[node.start:node.end] if not node.children else None))
    return tuple(result)


def _mapped(position, edits):
    for edit in edits:
        if edit.start < position < edit.end:
            raise SafetyError('A directive boundary lies inside a proposed gap')
    return position + sum(len(edit.replacement) - (edit.end - edit.start)
                          for edit in edits if edit.end <= position)


class _MappedEdits:
    """Index ordinary edits without assuming ordering or non-overlap."""

    def __init__(self, edits):
        by_start = sorted((edit.start, edit.end) for edit in edits)
        self.starts = [start for start, _ in by_start]
        self.farthest_end = []
        farthest = None
        for _, end in by_start:
            farthest = end if farthest is None else max(farthest, end)
            self.farthest_end.append(farthest)
        by_end = sorted((edit.end, len(edit.replacement) - (edit.end - edit.start))
                        for edit in edits)
        self.ends = [end for end, _ in by_end]
        self.shifts = [0]
        for _, delta in by_end:
            self.shifts.append(self.shifts[-1] + delta)

    def mapped(self, position):
        before = bisect_left(self.starts, position)
        if before and self.farthest_end[before - 1] > position:
            raise SafetyError('A directive boundary lies inside a proposed gap')
        return position + self.shifts[bisect_right(self.ends, position)]


def _scopes(tree, catalog, edits=()):
    scopes = resolve(tree, {rule['id'] for rule in catalog})
    # Preserve historical iteration and custom-object semantics on unusual
    # callers; normal plan output consists of an immutable tuple of Edits.
    if (type(scopes) is tuple and scopes and type(edits) in (tuple, list)
            and all(type(edit) is Edit and type(edit.start) is int and type(edit.end) is int
                    and type(edit.replacement) is bytes for edit in edits)):
        mapped = _MappedEdits(edits).mapped
    else:
        mapped = lambda position: _mapped(position, edits)
    return tuple((scope.rules, scope.reason, mapped(scope.start),
                  mapped(scope.end), mapped(scope.directive_start)) for scope in scopes)


def preview(tree, path, catalog, *, baseline_displays=None):
    """Produce a verified candidate for one captured source; never write it."""
    result = preview_gap_edits(tree, path, catalog=catalog)
    if not result.edits:
        return None
    try:
        candidate_plan = plan(tree.source, result.edits)
        if candidate_plan.deferred or len(candidate_plan.edits) != len(result.edits):
            raise SafetyError('Overlapping or ineffective gap proposals')
        candidate = candidate_plan.candidate()
        parsed = parse(candidate, tree.mode)
        if parsed.nodes[0].errors or _syntax_signature(tree) != _syntax_signature(parsed):
            raise SafetyError('Candidate changes non-whitespace syntax or fails to parse')
        if _scopes(tree, catalog, candidate_plan.edits) != _scopes(parsed, catalog):
            raise SafetyError('Candidate changes an inline directive target')
        final = analyze_all(parsed, path, catalog=catalog)
        original = baseline_displays if baseline_displays is not None else analyze_all(tree, path, catalog=catalog)
        if (final[RULE_ID].diagnostics or
                len(final[RULE_ID].suppressed) != len(original[RULE_ID].suppressed) or
                len(final[STANDALONE].diagnostics) != len(original[STANDALONE].diagnostics) or
                len(final[STANDALONE].suppressed) != len(original[STANDALONE].suppressed)):
            raise SafetyError('Candidate changes other display findings or fails gap reanalysis')
        return dict(path=path, baseline_sha256=digest(tree.source), candidate_sha256=digest(candidate),
                    verification='parse-syntax-directives-and-display-reanalysis',
                    edits=[dict(start=e.start, end=e.end, replacement=e.replacement.decode('utf-8'),
                                rule_id=RULE_ID) for e in candidate_plan.edits])
    except (InputError, SafetyError, OSError, ValueError) as error:
        return dict(path=path, baseline_sha256=digest(tree.source), candidate_sha256=None,
                    verification='withheld', edits=[], reason=str(error))
