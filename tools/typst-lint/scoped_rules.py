"""Conservative scoped rules; no writes, graph claims or synthetic scope input."""
from dataclasses import dataclass
from edit_plan import Edit
from scoped_provenance import resolve, arguments, PACKAGE
from static_resolver import _literal_string
from suppression import resolve as suppressions
from typst_lint import Diagnostic, load_rules

CALL = 'core.scoped-function-call'
REFERENCE = 'core.scoped-reference-access'
DISPLAY = 'core.annotation-in-display'
UNIQUE = 'core.unique-scope-namespace'
RULE_IDS = (CALL, REFERENCE, DISPLAY, UNIQUE)


@dataclass(frozen=True)
class ScopedResult:
    diagnostics: tuple = ()
    edits: tuple = ()
    skips: tuple = ()
    parse_errors: tuple = ()
    suppressed: tuple = ()


def analyze(tree, path, *, packages=()):
    r = resolve(tree, packages)
    catalog = {rule['id']: rule for rule in load_rules()[0]['rules']}
    scopes = suppressions(tree, set(catalog))
    diagnostics, edits, suppressed = ({rid: [] for rid in RULE_IDS} for _ in range(3))
    parents = {c: i for i, n in enumerate(tree.nodes) for c in n.children}
    namespaces = {}

    def add(rid, i, message, proposals=()):
        start = tree.nodes[i].start
        rule = catalog[rid]
        finding = Diagnostic(rid, rule['grade'], path, tree.source[:start].count(b'\n') + 1,
                             len(tree.source[tree.source.rfind(b'\n', 0, start)+1:start].decode()) + 1,
                             message, rule['source'])
        if any(s.covers(rid, start) for s in scopes):
            suppressed[rid].append(finding)
        else:
            diagnostics[rid].append(finding)
            edits[rid].extend(proposals)

    def display_ancestor(i):
        while i in parents:
            i = parents[i]
            if tree.nodes[i].kind == 'Equation' and tree.nodes[i].display:
                return True
        return False

    for i, node in enumerate(tree.nodes):
        if node.kind != 'FuncCall':
            continue
        binding = r.facts[i].binding
        parts = r.sig(i)
        if not binding or len(parts) != 2:
            continue
        if binding.kind == 'scope-field' and tree.nodes[parts[0]].kind == 'FieldAccess':
            callee = tree.nodes[parts[0]]
            add(CALL, i, 'Call a known scope dictionary function through parentheses',
                (Edit(callee.start, callee.end, b'(' + tree.source[callee.start:callee.end] + b')'),))
        if ((binding.kind == 'scope-field' and binding.name == 'annot') or
                (binding.kind == 'api' and binding.package == PACKAGE and binding.name == 'annot-cetz-local')):
            if not display_ancestor(i):
                add(DISPLAY, i, 'Known equation overlay is outside a display math ancestor')
        if binding.kind == 'builtin' and binding.name == 'ref':
            args = arguments(r, parts[1])
            for arg in args:
                inner = r.facts[arg].binding
                if (tree.nodes[arg].kind == 'FuncCall' and inner and inner.kind == 'scope-field'
                        and inner.name in ('tag', 'name')):
                    add(REFERENCE, i, 'Use the corresponding scope ref helper for a generated local target')
                    break
        if i in r.scope_calls:
            named = {r.text(r.sig(a)[0]): r.sig(a)[-1] for a in arguments(r, parts[1])
                     if tree.nodes[a].kind == 'Named'}
            prefix = _literal_string(tree, named['prefix']) if 'prefix' in named else None
            key = ('prefix', prefix) if prefix is not None else None
            if key is None and 'parent' in named and 'name' in named:
                parent = r.facts[named['parent']].binding
                name = _literal_string(tree, named['name'])
                # A dynamic explicit prefix takes precedence over parent/name.
                explicit = named.get('prefix')
                auto = explicit is None or tree.nodes[explicit].kind == 'Auto'
                if auto and parent and parent.kind in ('tag-scope', 'annotation-scope') and name is not None:
                    key = ('child', parent.source_identity, name)
            if key is not None:
                if key in namespaces:
                    add(UNIQUE, i, f'Duplicate literal scope namespace; first declaration at byte {namespaces[key]}')
                else:
                    namespaces[key] = node.start
    skips = tuple(dict.fromkeys((*r.skips,
        'Partial: direct reviewed scoped package imports only; no facade, raw mannot, graph-wide collision or reference repair coverage')))
    errors = tuple((n.start, n.end) for n in tree.nodes if n.kind == 'Error')
    return {rid: ScopedResult(tuple(diagnostics[rid]), tuple(edits[rid]), skips, errors,
                               tuple(suppressed[rid])) for rid in RULE_IDS}
