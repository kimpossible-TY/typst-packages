"""Partial continuation-line analysis, isolated from the shared dispatcher.

Only direct root markup Text/Space/Text triples are analyzed. A period is
accepted only after an allowlist of complete, non-abbreviated words;
other sentence boundaries and nested or inline constructs remain unsupported.
Proposals must still pass the caller's combined-candidate checks before use.
"""
from dataclasses import dataclass
from bisect import bisect_left
from collections.abc import Mapping
import re

from edit_plan import Edit
from suppression import resolve as resolve_suppressions
from typst_lint import Diagnostic, InputError, digest, load_rules

RULE_ID = 'pde.continuation-line'
# Complete ordinary mathematical prose words only. Keep this an explicit set:
# treating arbitrary periods as sentence endings would misclassify e.g. Prof.,
# approx., eq., Fig. or a person's initial. No stems or prefix matches.
_PERIOD_WORDS = frozenset(b'supported follows holds converges choice definition exist '
    b'understandable useless equation solution continuous bounded finite positive '
    b'negative equal unique defined zero constant condition boundary interval '
    b'space time value proof theorem example respectively necessary sufficient '
    b'obvious clear true false therefore thus then hence again term terms below '
    b'above independent otherwise smooth'.split())
_LEFT = re.compile(rb'(?:^|[ \t])[A-Za-z]{2,}[ \t]+([a-z]{3,})([.!?])$')
# "We", "If", "It", "A" and "I" can begin a sentence too. Single-letter
# starts require a following word so an initial like "J." is not promoted.
_RIGHT = re.compile(rb'(?:[A-Z][a-z]+(?:[ \t]+[A-Za-z][A-Za-z\x27-]*|[.!?])|[AI][ \t]+[A-Za-z][A-Za-z\x27-]*)')
_GAP = re.compile(rb'[ \t]*(?:\r\n|\n)[ \t]*\Z')
_CONDITIONAL_EXCLUDED = frozenset(('Equation', 'Math', 'Code', 'CodeBlock',
                                   'Heading', 'ListItem', 'EnumItem', 'TermItem', 'Raw', 'Str'))


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


def analyze_conditional(tree, path, *, catalog=None):
    """Inspect source-local markup boundaries without claiming runtime ownership.

    This reports root and nested markup patterns only as conditional findings.
    It never returns edits. Whether an enclosing body executes, renders as prose,
    or is transformed by a show rule is deliberately not assumed.
    """
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        errors = tuple((node.start, node.end) for node in nodes if node.kind == 'Error')
        return ContinuationResult(skips=('Malformed Typst syntax; no source pattern diagnosis',),
                                  parse_errors=errors or ((0, len(source)),))
    if tree.mode != 'markup' or nodes[0].kind != 'Markup':
        return ContinuationResult(skips=('Only markup source patterns are supported',))
    records = load_rules()[0]['rules'] if catalog is None else catalog
    if (type(records) not in (tuple, list) or any(not isinstance(item, Mapping)
            or type(item.get('id')) is not str for item in records)):
        raise InputError('Conditional continuation analysis requires rule metadata records')
    selected = [item for item in records if item['id'] == RULE_ID]
    if (len(selected) != 1 or any(type(selected[0].get(key)) is not str
            or not selected[0][key] for key in ('grade', 'source'))):
        raise InputError('Missing or invalid continuation rule metadata')
    rule = selected[0]
    scopes = resolve_suppressions(tree, {item['id'] for item in records})
    parents = {child: index for index, node in enumerate(nodes) for child in node.children}
    excluded = {}
    for index, node in enumerate(nodes):
        parent = parents.get(index)
        excluded[index] = parent is not None and (excluded[parent] or nodes[parent].kind in _CONDITIONAL_EXCLUDED)
    findings, suppressed = [], []
    # Line indexing is built once even for many candidates in a large document.
    newlines = [index for index, byte in enumerate(source) if byte == 10]
    for body_id, body in enumerate(nodes):
        if body.kind != 'Markup' or excluded[body_id]:
            continue
        siblings = body.children
        for position in range(1, len(siblings) - 1):
            left, gap, right = (nodes[index] for index in siblings[position - 1:position + 2])
            if ((left.kind, gap.kind, right.kind) != ('Text', 'Space', 'Text')
                    or not _GAP.fullmatch(source[gap.start:gap.end])):
                continue
            match = _LEFT.search(source[left.start:left.end])
            if (not match or not _RIGHT.match(source[right.start:right.end])
                    or (match.group(2) == b'.' and match.group(1) not in _PERIOD_WORDS)):
                continue
            anchor = left.end - 1
            line_index = bisect_left(newlines, anchor)
            line_start = 0 if line_index == 0 else newlines[line_index - 1] + 1
            column = len(source[line_start:anchor].decode('utf-8')) + 1
            finding = Diagnostic(RULE_ID, rule['grade'], path, line_index + 1, column,
                                 'Join these sentences if this markup is rendered as ordinary prose.', rule['source'])
            if any(scope.covers(RULE_ID, anchor) for scope in scopes):
                suppressed.append(finding)
            else:
                findings.append(finding)
    return ContinuationResult(tuple(findings), (),
        ('Source-local pattern only; execution, enclosing context and prose rendering are unproved.',),
        suppressed=tuple(suppressed))
