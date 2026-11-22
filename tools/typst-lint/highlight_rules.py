"""Bounded, syntax-only text highlighting proposals; no writes or evaluation.

Only root markup calls with literal text/space and direct equations are split.
Equation bytes are never replaced, so operand repairs can compose independently.
The reviewed helper's geometry may change intentionally when math is extracted.
"""
from dataclasses import dataclass

from edit_plan import Edit, plan
from static_resolver import TEXT_UTILS, TEXT_UTILS_SOURCE_SHA256
from suppression import resolve as suppressions
from syntax_client import parse
from typst_lint import Diagnostic, digest, load_rules

RULE_ID = 'pde.text-only-highlight'


@dataclass(frozen=True)
class HighlightResult:
    diagnostics: tuple[Diagnostic, ...] = ()
    edits: tuple[Edit, ...] = ()
    skips: tuple[str, ...] = ()
    parse_errors: tuple[tuple[int, int], ...] = ()
    suppressed: tuple[Diagnostic, ...] = ()


def _supported(binding):
    return binding and ((binding.kind == 'builtin' and binding.name == 'highlight') or
                        (binding.kind == 'api' and binding.name == 'highlighted' and
                         binding.package == TEXT_UTILS and
                         binding.source_sha256 == TEXT_UTILS_SOURCE_SHA256))


def analyze(tree, path, resolution):
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        return HighlightResult(skips=('Malformed Typst syntax; no repair proposed',),
                               parse_errors=tuple((n.start, n.end) for n in nodes if n.kind == 'Error') or ((0, len(source)),))
    if resolution.source_sha256 != digest(source) or len(resolution.facts) != len(nodes):
        return HighlightResult(skips=('Stale or mismatched static resolution; no repair proposed',))
    catalog = {r['id']: r for r in load_rules()[0]['rules']}
    scopes = suppressions(tree, set(catalog))
    parents = {c: i for i, n in enumerate(nodes) for c in n.children}
    findings, suppressed, edits, skips = [], [], [], list(resolution.skips)

    def descendants(index):
        pending = list(nodes[index].children)
        while pending:
            child = pending.pop()
            yield child
            pending.extend(nodes[child].children)

    def rendered_math(index):
        node = nodes[index]
        if node.kind == 'Equation':
            return True
        if node.kind not in ('ContentBlock', 'Markup', 'Strong', 'Emph'):
            return False
        return any(rendered_math(c) for c in node.children)

    for index, call in enumerate(nodes):
        if call.kind != 'FuncCall' or not _supported(resolution.binding_for(index)):
            continue
        descendants_ids = tuple(descendants(index))
        children = call.children
        args = nodes[children[1]] if len(children) == 2 and nodes[children[1]].kind == 'Args' else None
        # An equation used as an option is not highlighted body content.
        content_args = [c for c in args.children if nodes[c].kind == 'ContentBlock'] if args else []
        if args and any(nodes[c].kind not in ('ContentBlock', 'Space', 'LeftParen', 'RightParen', 'Comma', 'Named', 'LineComment', 'BlockComment') for c in args.children):
            skips.append(f'Unsupported dynamic highlight arguments at byte {call.start}')
            continue
        if not any(rendered_math(body_id) for body_id in content_args):
            continue
        line = source[:call.start].count(b'\n') + 1
        column = len(source[source.rfind(b'\n', 0, call.start) + 1:call.start].decode('utf-8')) + 1
        rule = catalog[RULE_ID]
        finding = Diagnostic(RULE_ID, rule['grade'], path, line, column,
                             'Keep math outside text highlighting.', rule['source'])
        if any(s.covers(RULE_ID, call.start) for s in scopes):
            suppressed.append(finding)
            continue
        findings.append(finding)
        reason = None
        if any(s.start <= call.start < s.end for s in scopes):
            reason = 'Directive targets the original highlight call'
        elif tree.mode != 'markup' or parents.get(index) != 0 or resolution.facts[index].deferred:
            reason = 'Only nondeferred root markup calls have supported extraction ownership'
        elif any(nodes[c].kind in ('LineComment', 'BlockComment', 'Label', 'Hash', 'FuncCall',
                                  'SetRule', 'ShowRule', 'LetBinding') for c in descendants_ids):
            reason = 'Comments, labels, nested calls or executable content prevent extraction'
        significant = [c for c in args.children if nodes[c].kind not in ('Space', 'LeftParen', 'RightParen', 'Comma')] if args else []
        body = nodes[significant[0]] if len(significant) == 1 and nodes[significant[0]].kind == 'ContentBlock' else None
        markup = nodes[body.children[1]] if body and len(body.children) == 3 and nodes[body.children[1]].kind == 'Markup' else None
        if not markup or any(nodes[c].kind not in ('Text', 'Space', 'Equation') for c in markup.children):
            reason = reason or 'Requires one literal content argument with only text, spaces and direct math'
        siblings = nodes[0].children
        if parents.get(index) == 0:
            position = siblings.index(index)
            previous = nodes[siblings[position - 1]] if position else None
            after = [nodes[c] for c in siblings[position + 1:] if nodes[c].kind not in ('Space', 'Parbreak')]
            if not previous or previous.kind != 'Hash' or previous.end != call.start:
                reason = reason or 'Requires an adjacent markup hash'
            if after and after[0].kind == 'Label':
                reason = reason or 'Attached semantic label prevents extraction'
        if reason:
            skips.append(f'fix_unavailable at byte {call.start}: {reason}')
            continue
        equations = [nodes[c] for c in markup.children if nodes[c].kind == 'Equation']
        if not equations:
            skips.append(f'fix_unavailable at byte {call.start}: Math is not a direct content child')
            continue
        callee = nodes[children[0]]
        prefix = b'#' + source[callee.start:callee.end] + b'['
        proposals = []
        # Replace only wrappers, never original prose or equation bytes.
        proposals.append(Edit(call.start - 1, markup.start, prefix if markup.start < equations[0].start else b''))
        for pos, equation in enumerate(equations):
            previous_end = markup.start if pos == 0 else equations[pos - 1].end
            next_start = markup.end if pos == len(equations) - 1 else equations[pos + 1].start
            if previous_end < equation.start:
                proposals.append(Edit(equation.start, equation.start, b']'))
            if equation.end < next_start:
                proposals.append(Edit(equation.end, equation.end, prefix))
        proposals.append(Edit(markup.end, call.end, b']' if equations[-1].end < markup.end else b''))
        candidate = plan(source, proposals).candidate()
        checked = parse(candidate, tree.mode)
        # Catch lexical joining at either edge (including adjacent dollar runs).
        before = [source[n.start:n.end] for n in nodes if n.kind == 'Equation']
        after_math = [candidate[n.start:n.end] for n in checked.nodes if n.kind == 'Equation']
        if checked.nodes[0].errors or before != after_math:
            skips.append(f'fix_unavailable at byte {call.start}: Candidate changes equation parsing')
            continue
        edits.extend(proposals)
    return HighlightResult(tuple(findings), tuple(edits), tuple(skips), suppressed=tuple(suppressed))
