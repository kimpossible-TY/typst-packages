"""Conservative scoped rules; no writes, graph claims or synthetic scope input."""
from dataclasses import dataclass
from edit_plan import Edit
from scoped_provenance import resolve, arguments, PACKAGE, SOURCE_SHA256
from static_resolver import (_literal_string, PackageSource, MANNOT_NAMESPACE,
                             MANNOT_NAMESPACE_SOURCE_SHA256)
from suppression import resolve as suppressions
from typst_lint import Diagnostic, digest, load_rules

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


def reference_repair(r, outer, target, packages):
    """The exact reviewed ref((scope.tag)(literal)) contract, not name guessing.

    Retain the actual receiver and the complete inner call's bytes, changing
    only its `tag` field to `ref` and deleting the outer builtin call. Scope
    field aliases lose the receiver needed to prove a corresponding ref field.
    Comments and line breaks in removed scaffolding are never relocated.
    """
    nodes, source = r.nodes, r.tree.source
    outer_parts, target_parts = r.sig(outer), r.sig(target)
    outer_binding, target_binding = r.facts[outer].binding, r.facts[target].binding
    if not (outer_binding and outer_binding.kind == 'builtin' and outer_binding.name == 'ref'
            and target_binding and target_binding.kind == 'scope-field'
            and target_binding.package == PACKAGE and target_binding.source_sha256 == SOURCE_SHA256):
        return (), 'builtin ref and reviewed scope-field provenance are required'
    if (len(outer_parts) != 2 or nodes[outer_parts[0]].kind != 'Ident'
            or nodes[outer_parts[1]].kind != 'Args'
            or arguments(r, outer_parts[1]) != [target]):
        return (), 'requires one generated target and no ref options, spread or extra arguments'
    if target_binding.name != 'tag':
        return (), 'generated-name references have no supported tag-to-ref repair'
    if len(target_parts) != 2 or nodes[target_parts[1]].kind != 'Args':
        return (), 'requires one literal local target argument'
    local_args = arguments(r, target_parts[1])
    if len(local_args) != 1 or nodes[local_args[0]].kind != 'Str':
        return (), 'requires one literal local target and no tag options, spread or extra arguments'
    callee = target_parts[0]
    callee_parts = r.sig(callee)
    if (nodes[callee].kind != 'Parenthesized' or len(callee_parts) != 3
            or [nodes[c].kind for c in callee_parts] != ['LeftParen', 'FieldAccess', 'RightParen']):
        return (), 'requires a direct parenthesized scope.tag field; function aliases are unsupported'
    field = callee_parts[1]
    field_parts = r.sig(field)
    if (len(field_parts) != 3 or [nodes[c].kind for c in field_parts] != ['Ident', 'Dot', 'Ident']
            or r.text(field_parts[2]) != 'tag'):
        return (), 'the original scope receiver is not a direct identifier'
    receiver = r.facts[field_parts[0]].binding
    if not (receiver and receiver.kind in ('tag-scope', 'annotation-scope')
            and receiver.package == PACKAGE and receiver.source_sha256 == SOURCE_SHA256
            and receiver.source_identity == target_binding.source_identity
            and r.facts[field].binding == target_binding):
        return (), 'the corresponding ref field is not proven on the same resolved scope'
    node, inner, name = nodes[outer], nodes[target], nodes[field_parts[2]]
    pending = [outer]
    while pending:
        child = nodes[pending.pop()]
        if child.kind in ('LineComment', 'BlockComment'):
            return (), 'comments or directives inside the reference must retain their ownership'
        pending.extend(child.children)
    removed = source[node.start:inner.start] + source[inner.end:node.end]
    if b'\n' in removed or b'\r' in removed:
        return (), 'line breaks in the outer ref scaffolding cannot be removed or relocated'
    # The local wrapper imports mannot with `*`. Its source alone cannot prove
    # package-local ref/label are builtins. This exact namespace-only entrypoint
    # has two named imports that export neither name. Only real loader-supplied
    # immutable bytes can attest that boundary; no boolean/test-only proof.
    if not any(type(p) is PackageSource and p.import_path == MANNOT_NAMESPACE
               and digest(p.source) == MANNOT_NAMESPACE_SOURCE_SHA256 for p in packages):
        return (), 'reviewed mannot export namespace evidence is required to exclude ref/label shadowing'
    replacement = source[inner.start:name.start] + b'ref' + source[name.end:inner.end]
    return (Edit(node.start, node.end, replacement),), None


def analyze(tree, path, *, packages=(), import_graph=None):
    packages = tuple(packages)
    r = resolve(tree, packages, import_graph=import_graph, source_path=path)
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

    def owned_scope(i):
        """Only direct root output and direct literal scope callback output.

        Stored content, argument content of arbitrary wrappers, code-block
        return values and nested ordinary closures are not document-graph
        membership evidence. Even two calls under the same opaque wrapper do
        not establish whether both are rendered, or in the same graph.
        """
        parent = parents.get(i)
        if parent == 0:
            return tree.mode == 'markup' and tree.nodes[0].kind == 'Markup'
        if parent is None or tree.nodes[parent].kind != 'Markup':
            return False
        body = parents.get(parent)
        if body is None or tree.nodes[body].kind != 'ContentBlock':
            return False
        closure = parents.get(body)
        args = parents.get(closure)
        outer = parents.get(args)
        return (outer in r.callback_bodies and r.callback_bodies[outer] == body
                and owned_scope(outer))

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
                    proposals, refusal = reference_repair(r, i, arg, packages)
                    add(REFERENCE, i, 'Use the corresponding scope ref helper for a generated local target', proposals)
                    if refusal and not any(s.covers(REFERENCE, node.start) for s in scopes):
                        r.note(i, f'{REFERENCE} repair refused: {refusal}')
                    break
        if i in r.scope_calls:
            if not owned_scope(i):
                r.note(i, 'Namespace collision skipped: output/document-graph ownership is unproven')
                continue
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
        'Partial: direct reviewed scoped package imports only; no facade, raw mannot or graph-wide collision coverage; reference repairs require namespace evidence, a literal tag target and the same proven scope receiver; namespace checks require direct root or literal callback output')))
    errors = tuple((n.start, n.end) for n in tree.nodes if n.kind == 'Error')
    return {rid: ScopedResult(tuple(diagnostics[rid]), tuple(edits[rid]), skips, errors,
                               tuple(suppressed[rid])) for rid in RULE_IDS}
