"""Bounded, provenance-backed theme diagnostics. No evaluation or source writes.

Context insertion remains unavailable: lexical palette provenance does not prove
that a helper returns content or that every external call site accepts content.
"""
from dataclasses import dataclass

from static_resolver import is_theme_binding
from suppression import resolve as resolve_suppressions
from typst_lint import Diagnostic, digest, load_rules

NO_SET = 'core.no-set-theme'
CONTEXT = 'core.theme-in-context'
RULE_IDS = (NO_SET, CONTEXT)


@dataclass(frozen=True)
class ThemeResult:
    diagnostics: tuple[Diagnostic, ...] = ()
    edits: tuple = ()
    skips: tuple[str, ...] = ()
    parse_errors: tuple[tuple[int, int], ...] = ()
    suppressed: tuple[Diagnostic, ...] = ()


def analyze(tree, path, resolution):
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        errors = tuple((n.start, n.end) for n in nodes if n.kind == 'Error') or ((0, len(source)),)
        return {r: ThemeResult(skips=('Malformed Typst syntax; no theme proof',),
                               parse_errors=errors) for r in RULE_IDS}
    if resolution.source_sha256 != digest(source) or len(resolution.facts) != len(nodes):
        return {r: ThemeResult(skips=('Stale or mismatched static resolution',)) for r in RULE_IDS}
    rules = {r['id']: r for r in load_rules()[0]['rules']}
    scopes = resolve_suppressions(tree, set(rules))
    findings, suppressed = ({r: [] for r in RULE_IDS} for _ in range(2))
    skips = {r: [*resolution.skips,
                 'Only exact reviewed math-blocks 0.2.0 theme provenance is supported; local facades and other versions remain unresolved']
             for r in RULE_IDS}
    parents = {child: i for i, node in enumerate(nodes) for child in node.children}

    def reusable(i):
        while i in parents:
            i = parents[i]
            if nodes[i].kind == 'Params':
                return False
            if nodes[i].kind == 'Closure':
                return True
        return False

    def add(rid, node, message):
        start = node.start
        line = source[:start].count(b'\n') + 1
        column = len(source[source.rfind(b'\n', 0, start) + 1:start].decode('utf-8')) + 1
        rule = rules[rid]
        finding = Diagnostic(rid, rule['grade'], path, line, column, message, rule['source'])
        (suppressed if any(s.covers(rid, start) for s in scopes) else findings)[rid].append(finding)

    for i, node in enumerate(nodes):
        fact = resolution.facts[i]
        binding = fact.binding
        if not is_theme_binding(binding):
            continue
        if node.kind == 'SetRule' and binding.kind in ('api', 'palette'):
            add(NO_SET, node, 'This proven custom palette helper or value is not a settable Typst element.')
        if (node.kind == 'FuncCall' and binding.kind == 'api'
                and binding.name == 'theme-from-text-fill'
                and not fact.in_context and reusable(i)):
            add(CONTEXT, node, 'Resolve this palette inside lexical context in the reusable helper.')
            skips[CONTEXT].append(
                f'fix_unavailable at byte {node.start}: content result, mutation/early-return freedom and all accepting call sites are not proven')
    return {r: ThemeResult(diagnostics=tuple(findings[r]), suppressed=tuple(suppressed[r]),
                           skips=tuple(dict.fromkeys(skips[r]))) for r in RULE_IDS}
