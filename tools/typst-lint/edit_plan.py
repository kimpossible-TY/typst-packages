"""Pure byte-edit/convergence primitives, NOT a protected write transaction.

Only a future verified syntax adapter may issue proposals. These routines do not
establish parser correctness, editor safety, filesystem identity or build safety.
"""
from dataclasses import dataclass
from typing import Callable

from typst_lint import SafetyError


@dataclass(frozen=True, order=True)
class Edit:
    start: int
    end: int
    replacement: bytes


@dataclass(frozen=True)
class Plan:
    baseline: bytes
    edits: tuple[Edit, ...]
    deferred: tuple[Edit, ...]

    def candidate(self) -> bytes:
        result = self.baseline
        for edit in reversed(self.edits):
            result = result[:edit.start] + edit.replacement + result[edit.end:]
        return result


def conflict(a: Edit, b: Edit) -> bool:
    # Boundary insertions are conservatively deferred as attachment is unknown.
    if a.start == a.end or b.start == b.end:
        return max(a.start, b.start) <= min(a.end, b.end)
    return max(a.start, b.start) < min(a.end, b.end)


def plan(baseline: bytes, proposals) -> Plan:
    if not isinstance(baseline, bytes):
        raise SafetyError('Baseline must be immutable bytes')
    try:
        baseline.decode('utf-8')
    except UnicodeError as error:
        raise SafetyError('Baseline must be UTF-8') from error
    edits = []
    for e in proposals:
        if not isinstance(e, Edit) or type(e.start) is not int or type(e.end) is not int or not isinstance(e.replacement, bytes):
            raise SafetyError('Invalid edit type')
        if not 0 <= e.start <= e.end <= len(baseline):
            raise SafetyError('Edit lies outside baseline')
        try:
            baseline[:e.start].decode('utf-8')
            baseline[:e.end].decode('utf-8')
            e.replacement.decode('utf-8')
        except UnicodeError as error:
            raise SafetyError('Edit splits UTF-8 or inserts invalid bytes') from error
        if baseline[e.start:e.end] != e.replacement:
            edits.append(e)
    edits = sorted(set(edits))
    blocked = {e for e in edits if any(e != other and conflict(e, other) for other in edits)}
    return Plan(baseline, tuple(e for e in edits if e not in blocked), tuple(sorted(blocked)))


@dataclass(frozen=True)
class Analysis:
    complete: bool
    parse_valid: bool
    findings: frozenset[str] = frozenset()
    edits: tuple[Edit, ...] = ()


@dataclass(frozen=True)
class Candidate:
    content: bytes
    passes: int
    unresolved: frozenset[str]


def converge(baseline: bytes, analyze: Callable[[bytes], Analysis]) -> Candidate:
    """At most eight edit passes, plus zero-edit validation and idempotence.

    Finding identities must be stable semantic IDs from the future adapter, not
    offsets that shift when a preceding edit moves a diagnostic. Malformed input
    is always refused here; dictionary-call parser recovery is not implemented.
    """
    def checked(content):
        state = analyze(content)
        if not isinstance(state, Analysis) or not state.complete or not state.parse_valid:
            raise SafetyError('Complete parse-valid analysis required')
        if not isinstance(state.findings, frozenset) or not all(isinstance(f, str) for f in state.findings):
            raise SafetyError('Finding identities must be immutable strings')
        return state

    plan(baseline, ())  # Validate immutable UTF-8 before invoking an adapter.
    current = baseline
    seen = {baseline}
    state = checked(current)
    original = state.findings
    for iteration in range(9):
        p = plan(current, state.edits)
        if not p.edits and not p.deferred:
            if not state.findings <= original:
                raise SafetyError('Candidate introduces new findings')
            again = checked(current)
            second = plan(current, again.edits)
            if second.edits or second.deferred or again.findings != state.findings:
                raise SafetyError('Candidate is not idempotent')
            return Candidate(current, iteration, state.findings)
        if iteration == 8:
            raise SafetyError('Eight-pass convergence limit exceeded')
        if not p.edits:
            raise SafetyError('Unresolved conflicting edits')
        current = p.candidate()
        if current in seen:
            raise SafetyError('Repeated candidate state')
        seen.add(current)
        state = checked(current)
    raise AssertionError('Unreachable')
