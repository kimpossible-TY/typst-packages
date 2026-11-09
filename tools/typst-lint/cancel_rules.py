"""Partial builtin math-cancellation diagnostics, with no replacement proof.

Only resolver-certified builtin math.cancel identity is evidence. The repository
contains no reviewed cancelmark implementation, so this backend never edits.
"""
from dataclasses import dataclass

from static_resolver import is_math_cancel_binding
from suppression import resolve as resolve_suppressions
from typst_lint import Diagnostic, digest, load_rules

RULE_ID = 'pde.theme-cancelmark'
DEFERRED_REASON = 'Deferred or dynamic mathematical call is unsupported at byte '
UNRESOLVED_REASON = 'Unresolved or shadowed mathematical callee; no cancellation identity proof at byte '
_DEFERRED = frozenset(('Closure', 'Contextual', 'ShowRule', 'Conditional',
                       'ForLoop', 'WhileLoop'))


@dataclass(frozen=True)
class CancelResult:
    diagnostics: tuple[Diagnostic, ...] = ()
    edits: tuple = ()
    skips: tuple[str, ...] = ()
    parse_errors: tuple[tuple[int, int], ...] = ()
    suppressed: tuple[Diagnostic, ...] = ()


def analyze(tree, path, resolution):
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        errors = tuple((n.start, n.end) for n in nodes if n.kind == 'Error') or ((0, len(source)),)
        return CancelResult(skips=('Malformed Typst syntax; no cancellation proof',), parse_errors=errors)
    if resolution.source_sha256 != digest(source) or len(resolution.facts) != len(nodes):
        return CancelResult(skips=('Stale or mismatched static resolution',))
    rules = {r['id']: r for r in load_rules()[0]['rules']}
    scopes = resolve_suppressions(tree, set(rules))
    rule = rules[RULE_ID]
    parents = {child: i for i, node in enumerate(nodes) for child in node.children}
    findings, suppressed = [], []
    skips = [*resolution.skips,
             'Partial cancellation coverage: only proven builtin math.cancel calls in mathematical source; deferred/dynamic execution is unsupported']

    for i, node in enumerate(nodes):
        if node.kind not in ('MathCall', 'FuncCall'):
            continue
        ancestors = []
        parent = i
        while parent in parents:
            parent = parents[parent]
            ancestors.append(nodes[parent].kind)
        if node.kind == 'FuncCall' and 'Math' not in ancestors:
            continue
        fact = resolution.facts[i]
        if fact.deferred or any(kind in _DEFERRED for kind in ancestors):
            skips.append(f'{DEFERRED_REASON}{node.start}')
            continue
        if not is_math_cancel_binding(fact.binding):
            if fact.binding is None:
                skips.append(f'{UNRESOLVED_REASON}{node.start}')
            continue
        start = node.start
        line = source[:start].count(b'\n') + 1
        column = len(source[source.rfind(b'\n', 0, start) + 1:start].decode('utf-8')) + 1
        finding = Diagnostic(RULE_ID, rule['grade'], path, line, column,
                             'Use the facade\'s theme-aware cancelmark instead of this resolved builtin math.cancel call.',
                             rule['source'])
        if any(scope.covers(RULE_ID, start) for scope in scopes):
            suppressed.append(finding)
        else:
            findings.append(finding)
            skips.append(f'fix_unavailable at byte {start}: exact cancelmark implementation, in-scope binding and compatible signature are unproven')
    return CancelResult(diagnostics=tuple(findings), suppressed=tuple(suppressed),
                        skips=tuple(dict.fromkeys(skips)))
