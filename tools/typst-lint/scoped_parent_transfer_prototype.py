"""Conditional source-only proof of narrow parent scope transfers.

Independent evidence only. It does not modify scoped-reference preview rules,
claim callback execution, verify rendering, or authorize edits.
"""
from dataclasses import asdict, replace
import json

import conditional_highlight as highlight
import conditional_modern_highlight as modern
import conditional_footnote_binding as binding
import import_graph as graph
import scoped_reference_preview as scoped
import static_resolver
from typst_lint import InputError, SafetyError, digest, require

MAX_WORK = 2_000_000
MAX_PREVIEW_WORK = 10_000_000
MAX_PARENTS = 32
MAX_BYTES = 256 * 1024
_MANNOT_SOURCE = '288027cea059b327bf3253c44ae975339e7afc0f149375d9d87ca1c1fe739f57'
_MANNOT_MANIFEST = 'aef4310b36955dc98d8b03ec4abf17d45777d0a08b65d6e67214752e57647545'
_ALLOWED = frozenset(('ref', 'tag', 'tags', 'name', 'names', 'anchor', 'node', 'mark', 'annot'))
_ASSIGNMENTS = frozenset(('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq'))
_FORBIDDEN = frozenset(('LetBinding', 'ModuleImport', 'ModuleInclude', 'DestructAssignment',
                        'Conditional', 'ForLoop', 'WhileLoop', 'Contextual'))
CONDITIONS = (
    'Captured package and facade bytes are selected and initialize successfully at runtime.',
    'The reviewed outer scope and each child wrapper execute in the assumed context.',
    'The source proof does not establish callback execution, side effects, label resolution, or rendering.',
    'Source hashes refer to captured bytes; current filesystem state is not revalidated.',
)
PREVIEW_CONDITIONS = CONDITIONS + (
    'This independent source-only plan is not a union with other reports; do not merge plans without separate verification.',
)


class _Tracer(modern._ModernAnalyzer):
    """Replay lexical namespace routes for exact parent-helper call sites."""
    def __init__(self, session, variant, tree, targets, outer_name):
        super().__init__(session, variant, scoped_callbacks=True)
        self.target_tree, self.targets = tree, targets
        self.names = {'mannot-scope', 'local-scope-annotations'} - {outer_name}

    def incoming(self, tree, index, path, active):
        result = super().incoming(tree, index, path, active)
        shape = graph.import_shape(tree, index)
        if (not result.reason and shape is not None and shape.items is None
                and shape.request == highlight.MATH_BLOCKS):
            pkg = self.packages.get(highlight.MATH_BLOCKS)
            name = 'theme-from-text-fill'
            if (pkg is not None and pkg.evidence.source_sha256 == static_resolver.MATH_BLOCKS_SOURCE_SHA256
                    and name in pkg.exports):
                values = dict(result.exports)
                route = (highlight.NameRoute(highlight.MATH_BLOCKS, pkg.evidence.source_sha256,
                         'package-export', highlight.MATH_BLOCKS, name, name),
                         highlight.NameRoute(path, digest(tree.source), 'wildcard-import',
                         highlight.MATH_BLOCKS, name, name))
                values[name] = highlight._Value(route, 'theme-selector')
                result = replace(result, exports=tuple(sorted(values.items())))
        if (result.reason or shape is None or shape.request != highlight.SCOPED_ANNOTATIONS
                or shape.items is not None):
            return result
        pkg = self.packages.get(highlight.SCOPED_ANNOTATIONS)
        if pkg is None or pkg.evidence.source_sha256 != scoped._SCOPE_HASH:
            return result
        values = dict(result.exports)
        for name in self.names:
            if name not in values or name not in pkg.exports:
                continue
            route = (highlight.NameRoute(highlight.SCOPED_ANNOTATIONS, scoped._SCOPE_HASH,
                     'package-export', highlight.SCOPED_ANNOTATIONS, name, name),
                     highlight.NameRoute(path, digest(tree.source), 'wildcard-import',
                     highlight.SCOPED_ANNOTATIONS, name, name))
            values[name] = highlight._Value(route, 'highlighted')
        return replace(result, exports=tuple(sorted(values.items())))

    def body_math(self, tree, call_id):
        # The replay emits source-route evidence only; this is not a highlight
        # diagnostic or a claim that the child body contains math.
        n = tree.nodes[call_id]
        return (tree is self.target_tree and (n.start, n.end) in self.targets), None


def _child(tree, call, parents):
    n = tree.nodes
    parts = graph.significant(tree, call)
    if len(parts) != 2 or n[parts[0]].kind != 'Ident' or n[parts[1]].kind != 'Args':
        return None
    name = graph.raw(tree, parts[0]).decode('utf-8')
    if name not in ('mannot-scope', 'local-scope-annotations'):
        return None
    args = [i for i in graph.significant(tree, parts[1])
            if n[i].kind not in ('LeftParen', 'RightParen', 'Comma')]
    positional = [i for i in args if n[i].kind != 'Named']
    named = {}
    for i in args:
        if n[i].kind != 'Named':
            continue
        bits = graph.significant(tree, i)
        if len(bits) != 3 or n[bits[0]].kind != 'Ident':
            return None
        key = graph.raw(tree, bits[0]).decode('utf-8')
        if key in named:
            return None
        named[key] = bits[2]
    if len(positional) != 1 or n[positional[0]].kind != 'Closure':
        return None
    closure = positional[0]
    parts = graph.significant(tree, closure)
    if (len(parts) != 3 or tuple(n[i].kind for i in parts) != ('Params', 'Arrow', 'ContentBlock')):
        return None
    params = graph.significant(tree, parts[0])
    if len(params) != 1 or n[params[0]].kind != 'Ident' or graph.raw(tree, params[0]) != b'm':
        return None
    parent = named.get('parent')
    if parent is not None:
        if (set(named) != {'parent', 'name'} or n[parent].kind != 'Ident' or graph.raw(tree, parent) != b's'
                or graph.literal_string(tree, named['name']) is None):
            return None
    elif set(named) != {'prefix'} or graph.literal_string(tree, named['prefix']) is None:
        return None
    return dict(call=call, closure=closure, body=parts[2], parameter=params[0], parent=parent, name=name)


def _theme_context(tree, context):
    """Recognize a narrow, non-shadowing palette setup; never hide its body."""
    n = tree.nodes
    parts = graph.significant(tree, context)
    if len(parts) != 2 or tuple(n[i].kind for i in parts) != ('Context', 'CodeBlock'):
        return None
    block = graph.significant(tree, parts[1])
    if len(block) != 3 or tuple(n[i].kind for i in block) != ('LeftBrace', 'Code', 'RightBrace'):
        return None
    statements = graph.significant(tree, block[1])
    if len(statements) < 2 or n[statements[-1]].kind != 'FuncCall':
        return None
    lets, names = statements[:-1], set()
    for pos, i in enumerate(lets):
        p = graph.significant(tree, i)
        if (n[i].kind != 'LetBinding' or len(p) != 4
                or tuple(n[x].kind for x in p[:3]) != ('Let', 'Ident', 'Eq')):
            return None
        name = graph.raw(tree, p[1])
        if name in names or name in (b's', b'm', b'mannot-scope', b'local-scope-annotations', b'theme-from-text-fill'):
            return None
        names.add(name)
        if pos == 0:
            q = graph.significant(tree, p[3])
            if (name != b'theme' or n[p[3]].kind != 'FuncCall' or len(q) != 2
                    or n[q[0]].kind != 'Ident' or graph.raw(tree, q[0]) != b'theme-from-text-fill'
                    or n[q[1]].kind != 'Args'
                    or tuple(n[x].kind for x in graph.significant(tree, q[1])) != ('LeftParen', 'RightParen')):
                return None
        elif (n[p[3]].kind != 'FieldAccess' or graph.raw(tree, p[3]) not in
              (b'theme.callouts.proposition.border', b'theme.callouts.important.border')):
            return None
    return set(lets)


def _root_bindings(tree, path, outer, tracer, remaining):
    """Conservatively replay root bindings up to the issued callback call."""
    nodes = tree.nodes
    parents = {c: i for i, n in enumerate(nodes) for c in n.children}
    calls = [i for i, n in enumerate(nodes) if n.kind == 'FuncCall'
             and (n.start, n.end) == (outer['call_start'], outer['call_end'])]
    if len(calls) != 1 or parents.get(calls[0]) != 0 or nodes[0].kind != 'Markup':
        raise SafetyError('Outer callback is not a direct root call')
    env = {}
    found = False
    for i in nodes[0].children:
        node = nodes[i]
        if i == calls[0]:
            found = True
            break
        if node.kind in graph._TRIVIA or node.kind in ('Hash', 'Semicolon'):
            continue
        # This upper bound covers the namespace/effect traversals even when
        # the root statement's actual subtree is much smaller than the tree.
        remaining -= 2 * len(nodes)
        if remaining < 0:
            raise SafetyError('Parent proof work budget exceeded')
        if highlight._effects(tree, i) is not None or highlight._namespace_injection(tree, i):
            raise SafetyError('Root namespace has an unsupported effect')
        if node.kind == 'ModuleImport':
            incoming = tracer.incoming(tree, i, path, (path,))
            if incoming.reason:
                raise SafetyError('Root import binding is unresolved')
            env.update(incoming.exports)
            if not tracer.bindings(env):
                raise SafetyError('Root import binding budget exceeded')
        elif node.kind == 'LetBinding':
            item = highlight._let(tree, i, env, path, tracer)
            if item is None:
                raise SafetyError('Root lexical binding is unsupported')
            env[item[0]] = item[1]
        elif node.kind in ('ModuleInclude', 'CodeBlock', 'Conditional', 'WhileLoop', 'ForLoop', 'Contextual'):
            raise SafetyError('Root lexical scope is unresolved')
    if not found:
        raise SafetyError('Outer callback source call is unavailable')
    return env, remaining


def analyze_one(evidence, path, highlight_start, *, max_work=MAX_WORK, max_bytes=MAX_BYTES):
    """Inspect one issued outer callback, without creating a proposal."""
    require(type(evidence) is modern.ModernHighlightAnalyzer and type(path) is str
            and type(highlight_start) is int and type(max_work) is int and 0 < max_work <= MAX_WORK
            and type(max_bytes) is int and 0 < max_bytes <= MAX_BYTES, 'Invalid parent proof request')
    modern.ModernHighlightAnalyzer._check(evidence)
    source = evidence._session._context.source_for(path)
    if source is None:
        raise InputError('Source is outside the issued capture')
    packages = evidence._session._namespaces.packages
    wrapper = next((p for p in packages if p.evidence.import_path == highlight.SCOPED_ANNOTATIONS), None)
    mannot = next((p for p in packages if p.evidence.import_path == '@preview/mannot:0.4.0'), None)
    if (wrapper is None or wrapper.evidence.source_sha256 != scoped._SCOPE_HASH
            or mannot is None or mannot.evidence.source_sha256 != _MANNOT_SOURCE
            or mannot.evidence.manifest_sha256 != _MANNOT_MANIFEST):
        raise SafetyError('Reviewed scoped wrapper or mannot namespace is unavailable')
    tree, nodes = source.tree, source.tree.nodes
    remaining = max_work - 4 * len(nodes)
    if remaining < 0:
        raise SafetyError('Parent proof work budget exceeded')
    replay = modern._ModernAnalyzer(evidence._session, evidence._variant,
                                    scoped_callbacks=evidence._scoped_callbacks)
    report = modern.ModernHighlightAnalyzer._analyze_one(evidence, tree, path, replay)
    remaining -= replay.ast_work
    if remaining < 0 or replay.exhausted:
        raise SafetyError('Parent proof work budget exceeded')
    findings = [d for d in report['diagnostics'] if d['start'] == highlight_start]
    if len(findings) != 1:
        raise InputError('No unique issued highlight diagnostic at this location')
    d = findings[0]
    contexts = d.get('callback_context') or ()
    routes = d.get('content_context') or ()
    if len(contexts) != 1:
        raise SafetyError('Exactly one issued outer callback is required')
    outer = contexts[0]
    ordinal = outer.get('content_context_index')
    if type(ordinal) is not int or not 0 <= ordinal < len(routes) or not routes[ordinal]:
        raise SafetyError('Outer callback route is missing')
    origin = routes[ordinal][0]
    if (origin.get('path') != highlight.SCOPED_ANNOTATIONS or origin.get('source_sha256') != scoped._SCOPE_HASH
            or origin.get('operation') != 'package-export' or origin.get('imported_name') not in highlight._SCOPED_CALLBACKS
            or tuple(outer.get('parameters', ())) != ('s',)):
        raise SafetyError('Outer callback is not the reviewed scope')
    return _prove_tree(evidence, tree, path, outer, origin, routes[ordinal], remaining, max_work, max_bytes)


def _prove_tree(evidence, tree, path, outer, origin, outer_route, remaining, max_work, max_bytes, *, graph_override=None):
    nodes = tree.nodes
    bodies = [i for i, n in enumerate(nodes) if n.kind == 'ContentBlock'
              and (n.start, n.end) == (outer['body_start'], outer['body_end'])]
    if len(bodies) != 1:
        raise SafetyError('Outer callback body is ambiguous')
    body = bodies[0]
    parents = {child: i for i, n in enumerate(nodes) for child in n.children}
    closure = parents.get(body)
    if closure is None or nodes[closure].kind != 'Closure':
        raise SafetyError('Outer scope is not a literal callback')
    parts = graph.significant(tree, closure)
    if (len(parts) != 3 or tuple(nodes[x].kind for x in parts) != ('Params', 'Arrow', 'ContentBlock')
            or parts[2] != body):
        raise SafetyError('Outer callback shape changed')
    params = graph.significant(tree, parts[0])
    if len(params) != 1 or nodes[params[0]].kind != 'Ident' or graph.raw(tree, params[0]) != b's':
        raise SafetyError('Outer callback parameter changed')
    args = parents.get(closure)
    call = parents.get(args) if args is not None else None
    if (args is None or nodes[args].kind != 'Args' or call is None or nodes[call].kind != 'FuncCall'
            or parents.get(call) != 0 or (nodes[call].start, nodes[call].end) != (outer['call_start'], outer['call_end'])):
        raise SafetyError('Outer wrapper ownership changed')
    pending, inside, child_by_call = [body], [], {}
    contexts, context_lets = set(), set()
    while pending:
        i = pending.pop()
        remaining -= 1
        if remaining < 0:
            raise SafetyError('Parent proof work budget exceeded')
        n = nodes[i]
        if n.kind == 'Contextual':
            approved = _theme_context(tree, i)
            if approved is None:
                raise SafetyError('Contextual palette binding is unsupported')
            contexts.add(i)
            context_lets.update(approved)
        if ((n.kind in _FORBIDDEN and i not in contexts and i not in context_lets)
                or n.errors or (n.kind == 'Binary' and any(
                nodes[c].kind in _ASSIGNMENTS for c in n.children))):
            raise SafetyError('Outer scope contains dynamic binding or mutation')
        inside.append(i)
        if n.kind == 'FuncCall':
            child = _child(tree, i, parents)
            if child is not None:
                child_by_call[i] = child
        pending.extend(n.children)
    if not child_by_call or len(child_by_call) > MAX_PARENTS:
        raise SafetyError('Parent-transfer child count is unsupported')
    if any(nodes[i].kind == 'Closure' and i not in {c['closure'] for c in child_by_call.values()} for i in inside):
        raise SafetyError('Unreviewed nested closure')
    child_ids = {c['parameter'] for c in child_by_call.values()}
    allowed_parent_ids = {c['parent'] for c in child_by_call.values() if c['parent'] is not None}
    if not allowed_parent_ids:
        raise SafetyError('No literal parent transfer is present')
    outer_fields, inner_fields, outer_inside_child = {}, {}, 0
    for i in inside:
        n = nodes[i]
        if n.kind not in ('Ident', 'MathIdent') or graph.raw(tree, i) not in (b's', b'm'):
            continue
        if n.kind == 'MathIdent':
            raise SafetyError('Scope identity is used from math code')
        text = graph.raw(tree, i)
        if text == b's' and i in allowed_parent_ids:
            continue
        if text == b'm' and i in child_ids:
            continue
        parent = parents.get(i)
        if parent is None or nodes[parent].kind != 'FieldAccess':
            raise SafetyError('Scope identity escapes or is shadowed')
        parts = graph.significant(tree, parent)
        if (len(parts) != 3 or parts[0] != i or nodes[parts[1]].kind != 'Dot'
                or nodes[parts[2]].kind != 'Ident' or graph.raw(tree, parts[2]) not in (b'tag', b'ref')):
            raise SafetyError('Unreviewed scope field access')
        # Do not let a helper function value escape to arbitrary code. Admit
        # only an exact parenthesized direct call with one literal argument.
        wrapped = parents.get(parent)
        call = parents.get(wrapped) if wrapped is not None else None
        if wrapped is None or nodes[wrapped].kind != 'Parenthesized' or call is None or nodes[call].kind != 'FuncCall':
            raise SafetyError('Scope helper value escapes its direct call')
        enclosed = graph.significant(tree, wrapped)
        called = graph.significant(tree, call)
        if (len(enclosed) != 3 or tuple(nodes[x].kind for x in enclosed) != ('LeftParen', 'FieldAccess', 'RightParen')
                or enclosed[1] != parent or len(called) != 2 or called[0] != wrapped or nodes[called[1]].kind != 'Args'):
            raise SafetyError('Scope helper value escapes its direct call')
        args = graph.significant(tree, called[1])
        if (len(args) != 3 or tuple(nodes[x].kind for x in args) != ('LeftParen', 'Str', 'RightParen')
                or graph.literal_string(tree, args[1]) is None):
            raise SafetyError('Scope helper arguments are not one literal string')
        remaining -= len(child_by_call)
        if remaining < 0:
            raise SafetyError('Parent proof work budget exceeded')
        owners = [c for c in child_by_call.values() if nodes[c['body']].start <= n.start < n.end <= nodes[c['body']].end]
        field = graph.raw(tree, parts[2]).decode('utf-8')
        if text == b'm':
            if len(owners) != 1:
                raise SafetyError('Child scope lexical ownership is ambiguous')
            inner_fields[field] = inner_fields.get(field, 0) + 1
        else:
            outer_fields[field] = outer_fields.get(field, 0) + 1
            if owners:
                outer_inside_child += 1
    targets = frozenset((nodes[i].start, nodes[i].end) for i in child_by_call)
    # Each emitted route computes a line/column using a source prefix scan.
    # Charge its maximum byte scan before starting the route replay.
    remaining -= len(tree.source) * len(targets)
    if remaining < 0:
        raise SafetyError('Parent proof work budget exceeded')
    tracer = _Tracer(evidence._session, evidence._variant, tree, targets, origin['imported_name'])
    if graph_override is not None:
        tracer.graph = graph_override
    traced = highlight._analyze(tree, path, tracer)
    remaining -= tracer.ast_work
    if remaining < 0 or tracer.exhausted:
        raise SafetyError('Parent proof work budget exceeded')
    routes_by_span = {(x.start, x.end): x for x in traced.diagnostics}
    if not routes_by_span:
        raise SafetyError('Outer wrapper route could not be replayed')
    known = {}
    for i, child in child_by_call.items():
        n = nodes[i]
        route = routes_by_span.get((n.start, n.end))
        if route is None:
            continue
        if (not route.route or route.route[0].path != highlight.SCOPED_ANNOTATIONS
                or route.route[0].source_sha256 != scoped._SCOPE_HASH
                or route.route[0].imported_name != child['name']
                or len(route.callback_context) != 1 or asdict(route.callback_context[0]) != outer
                or not 0 <= outer['content_context_index'] < len(route.content_context)
                or tuple(asdict(step) for step in route.content_context[outer['content_context_index']])
                   != tuple(outer_route)):
            raise SafetyError('Child or parent lexical route changed')
        previous = known.get(child['name'])
        if previous is not None and previous.route != route.route:
            raise SafetyError('Inconsistent child helper binding routes')
        known[child['name']] = route
    missing_names = {child['name'] for i, child in child_by_call.items()
                     if (nodes[i].start, nodes[i].end) not in routes_by_span and child['name'] not in known}
    root_values = {}
    if missing_names or contexts:
        prior_ast_work = tracer.ast_work
        root_values, remaining = _root_bindings(tree, path, outer, tracer, remaining)
        remaining -= tracer.ast_work - prior_ast_work
        if remaining < 0 or tracer.exhausted:
            raise SafetyError('Parent proof work budget exceeded')
        for name in missing_names:
            value = root_values.get(name)
            if (type(value) is not highlight._Value or not value.route
                    or value.route[0].path != highlight.SCOPED_ANNOTATIONS
                    or value.route[0].source_sha256 != scoped._SCOPE_HASH
                    or value.route[0].imported_name != name):
                raise SafetyError('Child wrapper root binding is unresolved')
    theme_route = []
    if contexts:
        value = root_values.get('theme-from-text-fill')
        if (type(value) is not highlight._Value or not value.route
                or value.route[0].path != highlight.MATH_BLOCKS
                or value.route[0].source_sha256 != static_resolver.MATH_BLOCKS_SOURCE_SHA256
                or value.route[0].imported_name != 'theme-from-text-fill'):
            raise SafetyError('Theme selector root binding is unresolved')
        theme_route = [asdict(step) for step in value.route]
    rows = []
    for i, child in sorted(child_by_call.items(), key=lambda pair: nodes[pair[0]].start):
        n = nodes[i]
        route = routes_by_span.get((n.start, n.end))
        method = 'direct'
        if route is None:
            # Unrecognized content wrappers may delay execution without creating
            # a lexical name binding. Only infer the *same* already proved
            # helper when no intervening scoped/deferred construct can shadow it.
            route = known.get(child['name'])
            root_value = root_values.get(child['name']) if route is None else None
            if route is None and root_value is None:
                raise SafetyError('Child wrapper binding is unresolved')
            cursor = i
            while cursor != body:
                remaining -= 1
                if remaining < 0:
                    raise SafetyError('Parent proof work budget exceeded')
                cursor = parents.get(cursor)
                if cursor is None or (cursor != body and cursor not in contexts and nodes[cursor].kind in
                        ('Closure', 'Contextual', 'ShowRule', 'Conditional', 'ForLoop', 'WhileLoop')):
                    raise SafetyError('Child wrapper lexical scope is unresolved')
            method = 'same-callback-lexical' if route is not None else 'captured-root-lexical'
        route_steps = route.route if route is not None else root_value.route
        evidence_start = route.start if route is not None else outer['call_start']
        rows.append(dict(call_start=n.start, call_end=n.end, helper=child['name'],
                         parent_transfer=child['parent'] is not None, route_method=method,
                         route_evidence_start=evidence_start,
                         child_body_start=nodes[child['body']].start, child_body_end=nodes[child['body']].end,
                         route=[asdict(step) for step in route_steps]))
    result = dict(path=path, source_sha256=digest(tree.source), status='CONDITIONAL', parents=rows,
                  outer_scope_fields=outer_fields, child_scope_fields=inner_fields,
                  outer_scope_uses_inside_children=outer_inside_child, work_used=max_work - remaining,
                  theme_contexts=len(contexts), theme_selector_route=theme_route,
                  conditions=CONDITIONS, runtime_binding_proved=False, execution_proved=False,
                  coverage_complete=False, render_equivalence_proved=False, edit_permission=False,
                  compiler_permission=False)
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
    if len(encoded.encode('utf-8')) + 2 * encoded.count('\n') > max_bytes:
        raise SafetyError('Parent proof report byte budget exceeded')
    modern.ModernHighlightAnalyzer._check(evidence)
    return result


def _verification_cost(original, candidate, edit, mapper):
    """Upper-bound duplicate-span matching and child comparisons."""
    edits = edit if type(edit) is tuple else (edit,)
    old, new = {}, {}
    for node in original.nodes:
        if node.start == node.end or any(item.start < node.end and node.start < item.end for item in edits):
            continue
        key = (node.kind, mapper.mapped(node.start), mapper.mapped(node.end))
        count, degree, byte_work = old.get(key, (0, 0, 0))
        # Each potential pair may slice and compare both complete byte spans.
        old[key] = (count + 1, degree + len(node.children) + len(node.operands),
                    byte_work + 2 * (node.end - node.start))
    for node in candidate.nodes:
        key = (node.kind, node.start, node.end)
        count, degree = new.get(key, (0, 0))
        new[key] = (count + 1, degree + len(node.children) + len(node.operands))
    return sum(a * b + da * b + db * a + bytes_a * b for key, (a, da, bytes_a) in old.items()
               for b, db in (new.get(key, (0, 0)),))


def _preview_selected(evidence, path, highlight_starts, *, max_work=MAX_PREVIEW_WORK, max_bytes=MAX_BYTES):
    """Revalidate a bounded set of same-callback edits against one source."""
    import document_modern_highlight_preview as preview
    from document_gap_preview import _MappedEdits
    from edit_plan import plan
    from syntax_client import parse
    from typst_lint import load_rules

    require(type(max_work) is int and 0 < max_work <= MAX_PREVIEW_WORK
            and type(max_bytes) is int and 0 < max_bytes <= MAX_BYTES, 'Invalid parent preview budget')
    require(type(highlight_starts) is tuple and 0 < len(highlight_starts) <= 4
            and all(type(value) is int and value >= 0 for value in highlight_starts)
            and len(set(highlight_starts)) == len(highlight_starts), 'Invalid parent highlight locations')
    parent_proof = analyze_one(evidence, path, highlight_starts[0], max_work=min(max_work, MAX_WORK), max_bytes=max_bytes)
    remaining = max_work - parent_proof['work_used']
    source = evidence._session._context.source_for(path)
    tree, nodes = source.tree, source.tree.nodes
    replay = modern._ModernAnalyzer(evidence._session, evidence._variant, scoped_callbacks=evidence._scoped_callbacks)
    issued = modern.ModernHighlightAnalyzer._analyze_one(evidence, tree, path, replay)
    remaining -= replay.ast_work
    if remaining <= 0 or replay.exhausted:
        raise SafetyError('Parent preview work budget exceeded')
    # Reject known-insufficient budgets before selecting, parsing, or checking
    # a candidate; parsed-tree work is charged separately after parsing.
    remaining -= 64 * len(nodes) + len(tree.source)
    if remaining <= 0:
        raise SafetyError('Parent preview work budget exceeded')
    selected = []
    for start in highlight_starts:
        matches = [d for d in issued['diagnostics'] if d['start'] == start]
        if len(matches) != 1:
            raise InputError('No unique issued highlight diagnostic at this location')
        selected.append(matches[0])
    contexts = [d.get('callback_context') or () for d in selected]
    if any(len(value) != 1 or value[0] != contexts[0][0] for value in contexts):
        raise SafetyError('Selected highlights do not share one issued callback')
    diagnostic = selected[0]
    callback = contexts[0][0]
    route = diagnostic['content_context'][callback['content_context_index']]
    if any(d['content_context'][callback['content_context_index']] != route for d in selected):
        raise SafetyError('Selected highlights do not share one wrapper route')
    original_refs = set()
    for diagnostic_item in selected:
        refs = {(n.start, n.end) for i, n in enumerate(nodes)
                if n.kind == 'FuncCall' and diagnostic_item['start'] <= n.start < n.end <= diagnostic_item['end']
                and scoped._reference(tree, i, 's')}
        if not refs:
            raise SafetyError('No literal outer scoped reference is selected')
        original_refs.update(refs)
        edit_start, edit_end = diagnostic_item['start'] - 1, diagnostic_item['end']
        if any(row['call_start'] < edit_end and edit_start < row['call_end'] for row in parent_proof['parents']):
            raise SafetyError('Selected highlight overlaps a parent-scope call')
    edits, plans = preview._select(tree, selected, allow_direct_references=True,
                                   proven_scoped_references=frozenset(original_refs))
    if len(edits) != len(selected):
        raise SafetyError('Highlight with a literal outer reference is unsupported')
    ordered = sorted(zip(edits, plans), key=lambda value: (value[0].start, value[0].end))
    edits, plans = tuple(x[0] for x in ordered), tuple(x[1] for x in ordered)
    proposal = plan(tree.source, edits)
    if proposal.deferred or len(proposal.edits) != len(edits):
        raise SafetyError('Candidate highlight edits conflict')
    candidate = proposal.candidate()
    parsed = parse(candidate, tree.mode)
    mapper = _MappedEdits(edits)
    remaining -= 64 * len(parsed.nodes) + len(candidate)
    if remaining <= 0:
        raise SafetyError('Parent preview work budget exceeded')
    # `_verify_insertions` may compare several AST nodes sharing one span;
    # charge a conservative matching/child upper bound before invoking it.
    remaining -= _verification_cost(tree, parsed, edits, mapper)
    if remaining <= 0:
        raise SafetyError('Parent preview work budget exceeded')
    records = load_rules()[0]['rules']
    if parsed.nodes[0].errors or preview._scopes(tree, records, edits) != preview._scopes(parsed, records):
        raise SafetyError('Candidate syntax or suppression targets changed')
    preview._verify_insertions(tree, parsed, edits, plans)
    # Reissue all parent/child lexical routes on the actual candidate tree.
    # Only the selected captured source may differ; its imports must remain
    # byte-identical, with every other graph source and edge unchanged.
    def imports(value):
        return tuple(value.source[n.start:n.end] for n in value.nodes if n.kind == 'ModuleImport')
    if imports(tree) != imports(parsed):
        raise SafetyError('Candidate changed captured import statements')
    original_graph = evidence._session._graph
    original_graph_source = original_graph.source_for(path)
    if original_graph_source is None or original_graph_source.source != tree.source:
        raise SafetyError('Captured source graph is inconsistent')
    derived_graph = replace(original_graph, sources=tuple(
        replace(item, source=candidate) if item.path == path else item for item in original_graph.sources))
    # This validates derived graph structure; it is not equality with the original signature.
    binding._graph_signature(derived_graph)
    mapped_callback = dict(callback)
    for key in ('body_start', 'body_end', 'call_start', 'call_end'):
        mapped_callback[key] = mapper.mapped(callback[key])
    origin = diagnostic['content_context'][callback['content_context_index']][0]
    # Candidate scope replay has its own bound within the remaining work.
    candidate_budget = min(MAX_WORK, remaining)
    candidate_remaining = candidate_budget - 4 * len(parsed.nodes)
    if candidate_remaining < 0:
        raise SafetyError('Parent preview work budget exceeded')
    original_outer_route = diagnostic['content_context'][callback['content_context_index']]
    mapped_outer_route = tuple(dict(step, source_sha256=digest(candidate)) if step['path'] == path else step
                               for step in original_outer_route)
    after_proof = _prove_tree(evidence, parsed, path, mapped_callback, origin, mapped_outer_route,
                              candidate_remaining, candidate_budget, max_bytes, graph_override=derived_graph)
    remaining -= after_proof['work_used']
    if remaining < 0:
        raise SafetyError('Parent preview work budget exceeded')
    if (parent_proof['outer_scope_fields'] != after_proof['outer_scope_fields']
            or parent_proof['child_scope_fields'] != after_proof['child_scope_fields']
            or parent_proof['outer_scope_uses_inside_children'] != after_proof['outer_scope_uses_inside_children']
            or parent_proof['theme_contexts'] != after_proof['theme_contexts']
            or len(parent_proof['parents']) != len(after_proof['parents'])):
        raise SafetyError('Candidate parent/child lexical identities changed')
    expected_theme_route = [dict(step, source_sha256=digest(candidate)) if step['path'] == path else step
                            for step in parent_proof['theme_selector_route']]
    if expected_theme_route != after_proof['theme_selector_route']:
        raise SafetyError('Candidate theme selector binding route changed')
    for before, after in zip(parent_proof['parents'], after_proof['parents']):
        if (before['helper'] != after['helper'] or before['parent_transfer'] != after['parent_transfer']
                or before['route_method'] != after['route_method']
                or mapper.mapped(before['call_start']) != after['call_start']
                or mapper.mapped(before['call_end']) != after['call_end']
                or mapper.mapped(before['child_body_start']) != after['child_body_start']
                or mapper.mapped(before['child_body_end']) != after['child_body_end']
                or mapper.mapped(before['route_evidence_start']) != after['route_evidence_start']):
            raise SafetyError('Candidate parent/child ownership changed')
        expected_route = [dict(step, source_sha256=digest(candidate)) if step['path'] == path else step
                          for step in before['route']]
        if expected_route != after['route']:
            raise SafetyError('Candidate parent/child binding route changed')
    for row in parent_proof['parents']:
        old_start, old_end = row['call_start'], row['call_end']
        start, end = mapper.mapped(old_start), mapper.mapped(old_end)
        matches = [n for n in parsed.nodes if n.kind == 'FuncCall' and (n.start, n.end) == (start, end)]
        if len(matches) != 1 or tree.source[old_start:old_end] != candidate[start:end]:
            raise SafetyError('Captured parent/child call bytes or ownership changed')
    def scope_fields(value, start, end):
        ns = value.nodes
        parents = {c: i for i, node in enumerate(ns) for c in node.children}
        result = []
        for i, node in enumerate(ns):
            if node.kind != 'Ident' or not start <= node.start < node.end <= end:
                continue
            name = graph.raw(value, i)
            if name not in (b's', b'm'):
                continue
            parent = parents.get(i)
            if parent is not None and ns[parent].kind == 'FieldAccess':
                pieces = graph.significant(value, parent)
                if len(pieces) != 3 or pieces[0] != i or ns[pieces[2]].kind != 'Ident':
                    raise SafetyError('Scoped field syntax changed')
                result.append((name, graph.raw(value, pieces[2])))
        return result
    old_bounds = callback['body_start'], callback['body_end']
    new_bounds = mapper.mapped(old_bounds[0]), mapper.mapped(old_bounds[1])
    if scope_fields(tree, *old_bounds) != scope_fields(parsed, *new_bounds):
        raise SafetyError('Outer/child field order or call count changed')
    before_resolution, after_resolution = preview.resolve(tree, packages=()), preview.resolve(parsed, packages=())
    before_display, after_display = preview.analyze_displays(tree, path, catalog=records), preview.analyze_displays(parsed, path, catalog=records)
    if any(not preview._same_findings(tree, before_display[rid], parsed, after_display[rid], edits) for rid in before_display):
        raise SafetyError('Display findings changed')
    before_scripts = preview.analyze_scripts(tree, path, catalog=records, _verify=False, _allow_code_call=True)
    after_scripts = preview.analyze_scripts(parsed, path, catalog=records, _verify=False, _allow_code_call=True)
    if not preview._same_script_findings_with_moved_equations(tree, before_scripts, parsed, after_scripts, edits, plans):
        raise SafetyError('Script findings changed')
    before_notes = preview.footnote_rules.analyze_conditional_nested(tree, path, before_resolution, catalog=records)
    after_notes = preview.footnote_rules.analyze_conditional_nested(parsed, path, after_resolution, catalog=records)
    if any(not preview._same_findings(tree, before_notes[rid], parsed, after_notes[rid], edits)
           for rid in preview.footnote_rules.RULE_IDS):
        raise SafetyError('Footnote findings changed')
    for check in (preview.paragraph_rules.analyze_conditional_nested_gap,
                  preview.paragraph_rules.analyze_conditional_nested_target,
                  preview.paragraph_rules.analyze_conditional_nested_capitalization,
                  preview.prose_marker_rules.analyze_conditional, preview.continuation_rules.analyze_conditional):
        if not preview._same_findings(tree, check(tree, path, catalog=records),
                                      parsed, check(parsed, path, catalog=records), edits):
            raise SafetyError('Paragraph findings changed')
    if not preview._same_findings(tree, preview.proof_rules.analyze_conditional(tree, path, before_resolution, catalog=records),
                                  parsed, preview.proof_rules.analyze_conditional(parsed, path, after_resolution, catalog=records), edits):
        raise SafetyError('Proof findings changed')
    modern.ModernHighlightAnalyzer._check(evidence)
    result = dict(path=path, baseline_sha256=digest(tree.source), candidate_sha256=digest(candidate),
                  verification='conditional-parent-scoped-source-only', work_used=max_work - remaining,
                  verified_parent_transfers=sum(row['parent_transfer'] for row in parent_proof['parents']),
                  verified_child_scopes=len(parent_proof['parents']),
                  outer_scope_uses_inside_children=parent_proof['outer_scope_uses_inside_children'],
                  edits=[dict(start=e.start, end=e.end, replacement=e.replacement.decode('utf-8'), rule_id=preview.RULE)
                         for e in edits], conditions=PREVIEW_CONDITIONS,
                  independent_source_only=True, composition_proved=False,
                  runtime_binding_proved=False, execution_proved=False, coverage_complete=False,
                  render_equivalence_proved=False, edit_permission=False, compiler_permission=False)
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
    original_json = json.dumps(parent_proof, ensure_ascii=False, sort_keys=True, indent=2)
    candidate_json = json.dumps(after_proof, ensure_ascii=False, sort_keys=True, indent=2)
    total_bytes = sum(len(value.encode('utf-8')) + 2 * value.count('\n')
                      for value in (encoded, original_json, candidate_json))
    if total_bytes > max_bytes:
        raise SafetyError('Parent preview report byte budget exceeded')
    return result


def preview_one(evidence, path, highlight_start, *, max_work=MAX_PREVIEW_WORK, max_bytes=MAX_BYTES):
    """Return an independent single-site plan without applying it."""
    return _preview_selected(evidence, path, (highlight_start,), max_work=max_work, max_bytes=max_bytes)
