"""Partial configured-root book ownership diagnostics. No source repair yet."""
from dataclasses import dataclass

from book_provenance import BookProof, EntrypointSnapshot
from static_resolver import is_book_binding, is_reset_binding
from suppression import resolve as resolve_suppressions
from typst_lint import Diagnostic, digest, load_rules

SINGLE = 'core.single-book-show'
RESET = 'core.no-duplicate-book-reset'
RULE_IDS = (SINGLE, RESET)
_LIMIT = ('Only proven immediate configured-root applications with explicit reviewed book and blocks '
          'imports are supported; missing owners, wildcard uncertainty, facades and include graphs remain unresolved')


@dataclass(frozen=True)
class BookResult:
    diagnostics: tuple[Diagnostic, ...] = ()
    edits: tuple = ()
    skips: tuple[str, ...] = ()
    parse_errors: tuple[tuple[int, int], ...] = ()
    suppressed: tuple[Diagnostic, ...] = ()


def analyze(tree, path, resolution, *, entrypoint=None):
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        errors = tuple((n.start, n.end) for n in nodes if n.kind == 'Error') or ((0, len(source)),)
        result = BookResult(skips=('Malformed Typst syntax; no book ownership proof',), parse_errors=errors)
        return {rid: result for rid in RULE_IDS}
    if (type(entrypoint) is not EntrypointSnapshot or type(entrypoint.path) is not str
            or type(entrypoint.source) is not bytes or entrypoint.path != path):
        return {rid: BookResult(skips=('This input is not the explicit configured entrypoint',)) for rid in RULE_IDS}
    if entrypoint.source != source:
        return {rid: BookResult(skips=('Configured entrypoint snapshot does not match analyzed bytes',)) for rid in RULE_IDS}
    proof = resolution.book_proof
    if (resolution.source_sha256 != digest(source) or len(resolution.facts) != len(nodes)
            or type(proof) is not BookProof or proof.source_sha256 != digest(source)):
        return {rid: BookResult(skips=('Stale or mismatched book ownership resolution',)) for rid in RULE_IDS}
    rules = {r['id']: r for r in load_rules()[0]['rules']}
    scopes = resolve_suppressions(tree, set(rules))
    findings, suppressed = ({rid: [] for rid in RULE_IDS} for _ in range(2))
    skips = {rid: [*resolution.skips, _LIMIT] for rid in RULE_IDS}

    def add(rid, application, explanation):
        start = nodes[application.node_id].start
        line = source[:start].count(b'\n') + 1
        column = len(source[source.rfind(b'\n', 0, start) + 1:start].decode('utf-8')) + 1
        rule = rules[rid]
        finding = Diagnostic(rid, rule['grade'], path, line, column, explanation, rule['source'])
        (suppressed if any(s.covers(rid, start) for s in scopes) else findings)[rid].append(finding)

    books = tuple(a for a in proof.applications if is_book_binding(a.binding)
                  and is_reset_binding(a.reset_binding))
    resets = tuple(a for a in proof.applications if is_reset_binding(a.binding)
                   and a.binding == a.reset_binding)
    for application in books[1:]:
        add(SINGLE, application, 'Another proven math-book document show is already installed at this entrypoint.')
    enabled = tuple(a for a in books if a.reset_enabled is True)

    def adjacent(left, right):
        # Immediate root transforms nest over the same suffix. An intervening
        # arbitrary transform/output can replace or own that body; a retained
        # callable binding alone is not a shared-body proof.
        first, second = sorted((left.node_id, right.node_id), key=lambda i: nodes[i].start)
        siblings = nodes[0].children
        first_pos, second_pos = siblings.index(first), siblings.index(second)
        between = siblings[first_pos + 1:second_pos]
        return (bool(between) and nodes[between[-1]].kind == 'Hash'
                and nodes[between[-1]].end == nodes[second].start
                and all(nodes[i].kind in ('Space', 'Parbreak', 'LineComment', 'BlockComment')
                        for i in between[:-1]))

    for application in resets:
        matching = tuple(book for book in enabled if book.reset_binding == application.binding)
        if not matching:
            continue
        if not any(adjacent(book, application) for book in matching):
            skips[RESET].append(
                f'Book/reset body ownership across intervening code or output is unsupported at byte {nodes[application.node_id].start}')
            continue
        add(RESET, application, 'This standalone reset overlaps the same reset already owned by a proven enabled math-book show.')
        skips[RESET].append(
            f'fix_unavailable at byte {nodes[application.node_id].start}: reset-wrapper equivalence for the entire body and its heading/reference transformations is not certified')
    return {rid: BookResult(diagnostics=tuple(findings[rid]), suppressed=tuple(suppressed[rid]),
                            skips=tuple(dict.fromkeys(skips[rid]))) for rid in RULE_IDS}
