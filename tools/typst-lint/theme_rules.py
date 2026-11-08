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


def analyze_conditional(tree, path, resolution, *, catalog=None):
    """Report unresolved literal selector calls as conditional source hints.

    The spelling alone does not establish the API: each hint explicitly depends
    on binding and execution evidence. This route grants no edit authority and
    deliberately leaves the existing provenance-backed analyzer unchanged.
    """
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        errors = tuple((n.start, n.end) for n in nodes if n.kind == 'Error') or ((0, len(source)),)
        return ThemeResult(skips=('Malformed Typst syntax; no conditional theme hints',), parse_errors=errors)
    if resolution.source_sha256 != digest(source) or len(resolution.facts) != len(nodes):
        return ThemeResult(skips=('Stale or mismatched static resolution',))
    rules = {r['id']: r for r in (load_rules()[0]['rules'] if catalog is None else catalog)}
    scopes = resolve_suppressions(tree, set(rules))
    parents = {child: i for i, node in enumerate(nodes) for child in node.children}
    findings, suppressed = [], []
    for i, node in enumerate(nodes):
        fact = resolution.facts[i]
        if node.kind != 'FuncCall' or fact.in_context or fact.binding is not None:
            continue
        children = [nodes[j] for j in node.children if nodes[j].kind not in ('Space', 'BlockComment', 'LineComment')]
        if len(children) != 2 or children[0].kind != 'Ident' or children[1].kind != 'Args':
            continue
        if source[children[0].start:children[0].end] != b'theme-from-text-fill':
            continue
        args = [nodes[j].kind for j in children[1].children
                if nodes[j].kind not in ('Space', 'BlockComment', 'LineComment')]
        if args != ['LeftParen', 'RightParen']:
            continue
        ancestor = i
        reusable = False
        while ancestor in parents:
            ancestor = parents[ancestor]
            if nodes[ancestor].kind == 'Params':
                break
            if nodes[ancestor].kind == 'Closure':
                reusable = True
                break
        if not reusable:
            continue
        start = node.start
        line = source[:start].count(b'\n') + 1
        column = len(source[source.rfind(b'\n', 0, start) + 1:start].decode('utf-8')) + 1
        rule = rules[CONTEXT]
        finding = Diagnostic(CONTEXT, rule['grade'], path, line, column,
            'Conditional source hint: if this unresolved name binds the reviewed theme selector and the helper executes, resolve the palette in lexical context.',
            rule['source'])
        (suppressed if any(s.covers(CONTEXT, start) for s in scopes) else findings).append(finding)
    return ThemeResult(diagnostics=tuple(findings), suppressed=tuple(suppressed),
        skips=('Conditional source patterns only: selector binding, execution and helper return type are unproved; no edits are proposed',))
