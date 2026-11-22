"""Authenticate serialized repair lineage against independently captured analysis."""
from dataclasses import dataclass

from candidate_convergence import AttributedEdit, Contribution, Step, converge
from edit_plan import plan
from typst_lint import SafetyError, digest


def serialize_candidate(candidate, path):
    """Only source hashes and actual pass edits, never a completeness assertion."""
    return dict(path=path, baseline_sha256=digest(candidate.baseline),
        candidate_sha256=digest(candidate.content), passes=candidate.passes,
        steps=[dict(before_sha256=digest(step.before), after_sha256=digest(step.after),
            applied=[dict(start=a.edit.start, end=a.edit.end,
                          replacement=a.edit.replacement.decode('utf-8'),
                          rule_id=a.primary_rule_id, rule_ids=list(a.rule_ids))
                     for a in step.applied],
            moves=[list(move) for move in step.moves]) for step in candidate.steps])


def _same_json(actual, expected):
    # Python numeric equality equates bool/int/float. Serialized proof types are
    # exact: a fractional/Boolean offset or pass count must never authenticate.
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return (actual.keys() == expected.keys() and all(type(key) is str for key in actual)
                and all(_same_json(actual[key], value) for key, value in expected.items()))
    if type(expected) is list:
        return len(actual) == len(expected) and all(_same_json(a, b) for a, b in zip(actual, expected))
    return actual == expected



@dataclass(frozen=True)
class AuthenticatedMarkdown:
    """One real script pass with original-byte mapping, never a Typst document tree."""
    baseline: bytes
    content: bytes
    step: Step

    @property
    def passes(self):
        return int(bool(self.step.applied))

    @property
    def edits(self):
        return self.step.applied

    def map_offset(self, position):
        return self.step.map_offset(position)

    def map_boundary(self, position, *, side='right'):
        return self.step.map_boundary(position, side=side)


def _authenticate_markdown(context, baseline, candidate, record):
    from markdown_scripts import RULE_ID
    if (type(baseline) is not bytes or type(candidate) is not bytes or context.source != baseline
            or record is not None):
        raise SafetyError('Original captured Markdown context without synthetic history is required')
    actual = context.analyze()
    if actual.parse_errors or actual != context.analyze():
        raise SafetyError('Markdown analysis is malformed or nondeterministic')
    checked = plan(baseline, actual.edits)
    if checked.deferred or checked.candidate() != candidate:
        raise SafetyError('Candidate differs from independently recomputed Markdown script repairs')
    # Only actual parser-issued parentheses insertions have an original-byte map.
    if any(e.start != e.end or not e.replacement or set(e.replacement) - {40, 41} for e in checked.edits):
        raise SafetyError('Markdown proposal is not a script-parenthesis insertion')
    attributed = tuple(AttributedEdit(e, RULE_ID, (RULE_ID,), (Contribution(1, RULE_ID, e),))
                       for e in checked.edits)
    return AuthenticatedMarkdown(baseline, candidate, Step(baseline, candidate, attributed, ()))


def authenticate_candidate(context, baseline, candidate, record=None):
    """Recompute actual rules and convergence from original immutable resources.

    No filesystem rereads occur. Missing/stripped history, false valid rule IDs,
    wrong pass counts, cycles, and arbitrary edits cannot impersonate the real
    parser/rule results. Final raw analysis is independently repeated by converge.
    This proves bounded repair lineage, never full coverage or permission to write.
    """
    from source_analysis import AnalysisContext, _validate_step, _validate_final, _movement_proofs
    from markdown_scripts import MarkdownContext
    if type(context) is MarkdownContext:
        return _authenticate_markdown(context, baseline, candidate, record)
    if (type(context) is not AnalysisContext or type(baseline) is not bytes
            or type(candidate) is not bytes or context.tree.source != baseline):
        raise SafetyError('Original captured analysis context is required to authenticate repairs')
    initial = context.analyze(context.tree)
    expected = converge(context.tree, initial, context.analyze, _validate_step,
        lambda value: _validate_final(context.tree, initial, context.ignored, value),
        movement_proofs=_movement_proofs)
    if expected.content != candidate:
        raise SafetyError('Candidate differs from independently recomputed rule repairs')
    if expected.passes > 1:
        if type(record) is not dict or not _same_json(record, serialize_candidate(expected, context.path)):
            raise SafetyError('Missing or noncanonical independently verified convergence evidence')
    elif record is not None:
        raise SafetyError('Unexpected history for a single-pass candidate')
    return expected


def replay_evidence(record, baseline, candidate, *, context=None):
    """Semantic authentication is mandatory; byte replay alone is insufficient."""
    if type(record) is not dict:
        raise SafetyError('Malformed convergence evidence')
    return authenticate_candidate(context, baseline, candidate, record)
