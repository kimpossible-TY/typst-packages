"""Partial continuation-line analysis, isolated from the shared dispatcher.

Only direct root markup Text/Space/Text triples are analyzed. A period is
accepted only after a small allowlist of complete, non-abbreviated words;
other sentence boundaries and nested or inline constructs remain unsupported.
Proposals must still pass the caller's combined-candidate checks before use.
"""
from dataclasses import dataclass
import re

from edit_plan import Edit
from suppression import resolve as resolve_suppressions
from typst_lint import Diagnostic, digest, load_rules

RULE_ID = 'pde.continuation-line'
# Narrow, explicit vocabulary; do not infer sentence endings from arbitrary
# periods. This covers the normative example without treating e.g. "Prof."
# or an initial as an unambiguous sentence boundary.
_PERIOD_WORDS = frozenset((b'supported', b'follows', b'holds', b'converges'))
_LEFT = re.compile(rb'(?:^|[ \t])[A-Za-z]{2,}[ \t]+([a-z]{3,})([.!?])$')
_RIGHT = re.compile(rb'[A-Z][a-z]{2,}(?:[ \t]+[A-Za-z][A-Za-z\x27-]*|[.!?])')
_GAP = re.compile(rb'[ \t]*(?:\r\n|\n)[ \t]*\Z')


@dataclass(frozen=True)
class ContinuationResult:
    diagnostics: tuple[Diagnostic, ...] = ()
    edits: tuple[Edit, ...] = ()
    skips: tuple[str, ...] = ()
    parse_errors: tuple[tuple[int, int], ...] = ()
    suppressed: tuple[Diagnostic, ...] = ()


def analyze(tree, path, resolution):
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        errors = tuple((node.start, node.end) for node in nodes if node.kind == 'Error')
        return ContinuationResult(skips=('Malformed Typst syntax; no repair proposed',),
                                  parse_errors=errors or ((0, len(source)),))
    if resolution.source_sha256 != digest(source) or len(resolution.facts) != len(nodes):
        return ContinuationResult(skips=('Stale or mismatched static resolution; no repair proposed',))
    if tree.mode != 'markup' or nodes[0].kind != 'Markup':
        return ContinuationResult(skips=('Only root markup prose is supported',))
    catalog = {rule['id']: rule for rule in load_rules()[0]['rules']}
    rule = catalog[RULE_ID]
    scopes = resolve_suppressions(tree, set(catalog))
    findings, edits, suppressed = [], [], []
    # The bounded recognizer cannot certify the absence of violations in
    # nested markup, inline constructs, or arbitrary sentence vocabulary.
    # Keep this file's coverage partial even when it finds no candidate.
    skips = [*resolution.skips, 'Only direct root prose with bounded sentence endings is supported']
    siblings = nodes[0].children
    for position in range(1, len(siblings) - 1):
        left_id, gap_id, right_id = siblings[position - 1:position + 2]
        left, gap, right = nodes[left_id], nodes[gap_id], nodes[right_id]
        if (left.kind, gap.kind, right.kind) != ('Text', 'Space', 'Text'):
            continue
        raw_gap = source[gap.start:gap.end]
        if not _GAP.fullmatch(raw_gap):
            continue
        if not all(resolution.facts[i].prose_safe and not resolution.facts[i].deferred
                   for i in (left_id, gap_id, right_id)):
            skips.append(f'Unproven or deferred prose boundary at byte {gap.start}')
            continue
        before = source[left.start:left.end]
        after = source[right.start:right.end]
        left_match, right_match = _LEFT.search(before), _RIGHT.match(after)
        if not left_match or not right_match:
            if before.endswith((b'.', b'?', b'!')):
                skips.append(f'Ambiguous sentence boundary at byte {gap.start}')
            continue
        word, punctuation = left_match.groups()
        if punctuation == b'.' and word not in _PERIOD_WORDS:
            skips.append(f'Period may be an abbreviation at byte {gap.start}')
            continue
        # Anchor at the terminal punctuation so ignore-next owns the Text node
        # and offset mapping remains stable when adjacent Text nodes coalesce.
        anchor = left.end - 1
        line = source[:anchor].count(b'\n') + 1
        column = len(source[source.rfind(b'\n', 0, anchor) + 1:anchor].decode('utf-8')) + 1
        finding = Diagnostic(RULE_ID, rule['grade'], path, line, column,
                             'Join adjacent prose sentences within the same source paragraph.',
                             rule['source'])
        if any(scope.covers(RULE_ID, anchor) for scope in scopes):
            suppressed.append(finding)
        else:
            findings.append(finding)
            edits.append(Edit(gap.start, gap.end, b' '))
    return ContinuationResult(tuple(findings), tuple(edits), tuple(dict.fromkeys(skips)),
                              suppressed=tuple(suppressed))
