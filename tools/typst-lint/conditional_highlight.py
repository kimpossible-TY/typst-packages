"""Conditional, read-only helper diagnostics from captured namespace facts.

This is not static_resolver.Binding evidence. A namespace contract describes
which names a successful module import exposes, not initialization purity,
runtime package selection, evaluation, rendering or permission to edit.
Only retained bytes and the pinned syntax adapter are consumed here.
"""
from dataclasses import dataclass, field
import re

import import_graph as graph
from facade_provenance import _graph_reason
from namespace_packages import verify_namespace_capture
from package_loader import PackageEvidence
from suppression import resolve as suppressions
from syntax_client import AdapterError, parse
from typst_lint import digest, load_rules

RULE_ID = 'pde.text-only-highlight'
TEXT_UTILS = '@local/text-utils:0.1.2'
MATH_BLOCKS = '@local/math-blocks:0.2.0'
_CONTENT_WRAPPERS = frozenset(('proof', 'theorem', 'note'))
CONDITIONS = (
    'Every referenced module loads and initializes successfully; initializer purity is not proved.',
    'Runtime imports select the exact captured package versions and bytes; ambient package selection is not proved.',
    'The reported source call actually executes and its literal body is rendered in the intended document context.',
)
_ASSIGNMENTS = frozenset(('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq'))
_PACKAGE = re.compile(r'^@(local|preview)/([a-z][a-z0-9-]*):[0-9]+\.[0-9]+\.[0-9]+$')
MAX_MODULE_VISITS = 512
MAX_ROUTE_ENTRIES = 128
MAX_BINDINGS = 1024
MAX_AST_WORK = 2_000_000


@dataclass(frozen=True)
class SourceHash:
    path: str
    source_sha256: str


@dataclass(frozen=True)
class NameRoute:
    path: str
    source_sha256: str
    operation: str
    request: str
    imported_name: str
    local_name: str


@dataclass(frozen=True)
class ConditionalDiagnostic:
    rule_id: str
    path: str
    line: int
    column: int
    start: int
    end: int
    message: str
    root_sha256: str
    source_hashes: tuple[SourceHash, ...]
    package_evidence: tuple[PackageEvidence, ...]
    package_requests: tuple[str, ...]
    route: tuple[NameRoute, ...]
    conditions: tuple[str, ...] = CONDITIONS
    content_context: tuple[tuple[NameRoute, ...], ...] = ()
    status: str = field(default='CONDITIONAL', init=False)
    edit_permission: bool = field(default=False, init=False)


@dataclass(frozen=True)
class ConditionalHighlightResult:
    diagnostics: tuple[ConditionalDiagnostic, ...] = ()
    suppressed: tuple[ConditionalDiagnostic, ...] = ()
    unresolved: tuple[str, ...] = ()
    parse_errors: tuple[tuple[int, int], ...] = ()
    status: str = field(default='CONDITIONAL', init=False)
    edit_permission: bool = field(default=False, init=False)


@dataclass(frozen=True)
class _Value:
    """Conditional helper/wrapper names; remaining exports stay opaque."""
    route: tuple[NameRoute, ...]
    canonical_name: str = 'highlighted'


@dataclass(frozen=True)
class _Module:
    exports: tuple[tuple[str, _Value | None], ...] = ()
    sources: tuple[SourceHash, ...] = ()
    packages: tuple[PackageEvidence, ...] = ()
    requests: tuple[str, ...] = ()
    reason: str | None = None
    height: int = 0
    intrinsic_refusal: bool = False

    @property
    def closure(self):
        return frozenset(source.path for source in self.sources)


def _descendants(tree, index, skip=()):
    pending = list(reversed(tree.nodes[index].children))
    while pending:
        child = pending.pop()
        if child in skip:
            continue
        yield child
        pending.extend(reversed(tree.nodes[child].children))


def _assignment(tree, index, skip=()):
    """Unknown active mutation defeats lexical identity, even inside arguments.

    A function body is deferred. Parameter defaults are still checked
    conservatively; their evaluation timing is not certified here.
    """
    pending = [index]
    while pending:
        current = pending.pop()
        if current in skip:
            continue
        node = tree.nodes[current]
        if node.kind == 'Closure':
            pending.extend(c for c in node.children if tree.nodes[c].kind == 'Params')
            continue
        if node.kind == 'DestructAssignment':
            return True
        if node.kind == 'Binary' and any(tree.nodes[c].kind in _ASSIGNMENTS
                                        for c in graph.significant(tree, current)):
            return True
        pending.extend(node.children)
    return False


def _namespace_injection(tree, index):
    """Expression-position declarations are not merely initializer values.

    The grammar permits ``let other = import ...: *`` and nested ``let``
    expressions. They may introduce additional names in the active scope.
    Blocks and closures have their own lexical namespaces; their execution is
    conditional, while explicit assignments are checked separately above.
    """
    pending = list(tree.nodes[index].children)
    while pending:
        current = pending.pop()
        node = tree.nodes[current]
        if node.kind == 'Closure':
            pending.extend(c for c in node.children if tree.nodes[c].kind == 'Params')
            continue
        if node.kind in ('ContentBlock', 'CodeBlock'):
            continue
        if node.kind in ('LetBinding', 'ModuleImport', 'ModuleInclude'):
            return True
        pending.extend(node.children)
    return False


def _let(tree, index, env, path, analyzer):
    """Return a declared root name and optional immutable helper alias."""
    parts = graph.significant(tree, index)
    if not parts or tree.nodes[parts[0]].kind != 'Let':
        return None
    if (len(parts) == 2 and tree.nodes[parts[1]].kind == 'Closure'):
        closure = graph.significant(tree, parts[1])
        if closure and tree.nodes[closure[0]].kind == 'Ident':
            return graph.raw(tree, closure[0]).decode('utf-8'), None
    if (len(parts) != 4 or tree.nodes[parts[1]].kind != 'Ident'
            or tree.nodes[parts[2]].kind != 'Eq'):
        return None
    name = graph.raw(tree, parts[1]).decode('utf-8')
    value = None
    if tree.nodes[parts[3]].kind == 'Ident':
        original = graph.raw(tree, parts[3]).decode('utf-8')
        previous = env.get(original)
        if previous is not None:
            value = analyzer.route(previous, NameRoute(path, digest(tree.source),
                'alias', '', original, name))
    # An arbitrary initializer establishes this name only. In particular,
    # mark.with(...) neither proves purity nor introduces mark's other names.
    return name, value


def _module_alias(tree, index):
    parts = graph.significant(tree, index)
    request = graph.import_request(tree, index)
    if request is None:
        return None
    if (len(parts) == 4 and tree.nodes[parts[2]].kind == 'As'
            and tree.nodes[parts[3]].kind == 'Ident'):
        return request, graph.raw(tree, parts[3]).decode('utf-8')
    if len(parts) == 2:
        match = _PACKAGE.fullmatch(request)
        if match:
            return request, match[2]
        name = request.rsplit('/', 1)[-1][:-4]
        if (request.endswith('.typ') and not request.startswith(('/', '@'))
                and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', name)):
            return request, name
    return None


def _combined_import(tree, index):
    """Exact official AST for ``import literal as module: simple-items``.

    Native Typst binds the module alias first, then the explicit/wildcard
    names. Keep this conditional grammar separate from trusted import_shape.
    """
    parts = graph.significant(tree, index)
    request = graph.import_request(tree, index)
    if (request is None or len(parts) != 6
            or tuple(tree.nodes[c].kind for c in parts[2:5]) != ('As', 'Ident', 'Colon')):
        return None
    alias = graph.raw(tree, parts[3]).decode('utf-8')
    if tree.nodes[parts[5]].kind == 'Star':
        return graph.ImportShape(index, request, None), alias
    if tree.nodes[parts[5]].kind != 'ImportItems':
        return None
    items = []
    for item in graph.significant(tree, parts[5]):
        if tree.nodes[item].kind == 'Comma':
            continue
        local = None
        if tree.nodes[item].kind == 'RenamedImportItem':
            values = graph.significant(tree, item)
            if (len(values) != 3 or tree.nodes[values[1]].kind != 'As'
                    or tree.nodes[values[2]].kind != 'Ident'):
                return None
            item, local = values[0], graph.raw(tree, values[2]).decode('utf-8')
        values = graph.significant(tree, item)
        if (tree.nodes[item].kind != 'ImportItemPath' or len(values) != 1
                or tree.nodes[values[0]].kind != 'Ident'):
            return None
        name = graph.raw(tree, values[0]).decode('utf-8')
        items.append((name, local or name))
    if not items or len({local for _, local in items}) != len(items):
        return None
    return graph.ImportShape(index, request, tuple(items)), alias


class _Analyzer:
    def __init__(self, context, namespaces):
        self.graph = context
        self.reason = _graph_reason(context)
        self.packages = {}
        self.evidence = ()
        self.trees = {}
        self.modules = {}
        self.refusals = {}
        self.module_visits = 0
        self.ast_work = 0
        self.exhausted = None
        if not verify_namespace_capture(namespaces):
            self.reason = self.reason or 'Invalid or stale captured package namespace evidence'
        else:
            self.packages = {p.import_path: p for p in namespaces.packages}
            # Namespace contracts may reexport names from another captured
            # package. Retain the whole verified six-package evidence set so
            # a standalone facade cannot omit its namespace prerequisites.
            self.evidence = namespaces.evidence

    def exhaust(self, reason):
        self.exhausted = self.exhausted or reason
        return _Module(reason=self.exhausted)

    def refuse_module(self, path, reason):
        """Cache only a fixed-capture refusal, never ancestry/work failures."""
        refusal = _Module(reason=reason, intrinsic_refusal=True)
        self.refusals[path] = refusal
        return refusal

    def charge_ast(self, tree):
        # Each root sequence is walked for active assignment, namespace
        # injection and nested-call ownership. Charge all three walks even
        # when a refusal or deferred body would skip part of the work.
        self.ast_work += 3 * len(tree.nodes)
        if self.ast_work > MAX_AST_WORK:
            self.exhaust('Conditional namespace AST work limit exceeded')
        return self.exhausted is None

    def charge_scope(self, env, sources, packages, requests, context):
        # Each inherited namespace/evidence/context container is copied once;
        # immutable route entries inside the context remain shared.
        self.ast_work += sum(map(len, (env, sources, packages, requests, context))) + 1
        if self.ast_work > MAX_AST_WORK:
            self.exhaust('Conditional namespace AST work limit exceeded')
        return self.exhausted is None

    def route(self, previous, step):
        if len(previous.route) >= MAX_ROUTE_ENTRIES:
            self.exhaust('Conditional namespace route limit exceeded')
            return None
        return _Value(previous.route + (step,), previous.canonical_name)

    def bindings(self, env):
        if len(env) > MAX_BINDINGS:
            self.exhaust('Conditional namespace binding limit exceeded')
        return self.exhausted is None

    def incoming(self, tree, index, path, active):
        if self.exhausted:
            return _Module(reason=self.exhausted)
        shape = graph.import_shape(tree, index)
        combined = _combined_import(tree, index) if shape is None else None
        if combined is not None:
            shape, module_name = combined
            alias = (shape.request, module_name)
        else:
            alias = _module_alias(tree, index) if shape is None else None
        if (shape is not None and shape.items is not None
                and len(shape.items) + int(combined is not None) > MAX_BINDINGS):
            return self.exhaust('Conditional namespace binding limit exceeded')
        request = shape.request if shape else (alias[0] if alias else None)
        if request is None:
            return _Module(reason='Dynamic or unsupported import namespace', intrinsic_refusal=True)
        if request.startswith('@'):
            package = self.packages.get(request)
            if package is None:
                # Named syntax exposes exactly these local binding names even
                # when the package's namespace has no reviewed shape. Their
                # values remain opaque, and successful loading stays explicit.
                if shape is not None and shape.items is not None and _PACKAGE.fullmatch(request):
                    names = ((alias[1], None),) if combined is not None else ()
                    return _Module(names + tuple((local, None) for _, local in shape.items), requests=(request,))
                if shape is None and alias is not None and _PACKAGE.fullmatch(request):
                    return _Module(((alias[1], None),), requests=(request,))
                return _Module(reason=f'Wildcard package namespace unavailable: {request}', intrinsic_refusal=True)
            exports = {}
            origins = dict(package.origins)
            for name in package.exports:
                supported = ((name == 'highlighted' and origins.get(name) == TEXT_UTILS)
                             or (request == MATH_BLOCKS and name in _CONTENT_WRAPPERS))
                exports[name] = (_Value((NameRoute(request, package.evidence.source_sha256,
                    'package-export', request, name, name),), name) if supported else None)
            if not self.bindings(exports):
                return _Module(reason=self.exhausted)
            module = _Module(tuple(sorted(exports.items())), packages=(package.evidence,), requests=(request,))
        else:
            target = self.graph.target_for(path, request)
            if target is None:
                return _Module(reason=f'Captured relative import unavailable: {request}', intrinsic_refusal=True)
            module = self.module(target, active)
            if module.reason:
                return module
        if shape is None and alias is not None:
            return _Module(((alias[1], None),), module.sources, module.packages, module.requests,
                           height=module.height)
        exports = dict(module.exports)
        items = tuple((name, name) for name in exports) if shape.items is None else shape.items
        if any(name not in exports for name, _ in items):
            return _Module(reason=f'Requested namespace export unavailable: {request}', intrinsic_refusal=True)
        incoming = [(alias[1], None)] if combined is not None else []
        for original, local in items:
            value = exports[original]
            if value is not None:
                value = self.route(value, NameRoute(path, digest(tree.source),
                    ('combined-' if combined is not None else '') +
                    ('wildcard-import' if shape.items is None else 'named-import'),
                    request, original, local))
                if self.exhausted:
                    return _Module(reason=self.exhausted)
            incoming.append((local, value))
        return _Module(tuple(incoming), module.sources, module.packages, module.requests,
                       height=module.height)

    def module(self, path, active):
        if self.exhausted:
            return _Module(reason=self.exhausted)
        self.module_visits += 1
        if self.module_visits > MAX_MODULE_VISITS:
            return self.exhaust('Conditional namespace module visit limit exceeded')
        if path in active:
            return _Module(reason='Cyclic captured namespace import graph')
        if len(active) >= graph.MAX_GRAPH_DEPTH:
            return _Module(reason='Captured namespace import depth limit exceeded')
        cached = self.modules.get(path)
        if cached is not None:
            # The successful fact is tied to this exact immutable graph and
            # namespace capture. Its complete closure and maximum height must
            # fit this caller's ancestry; a path-only hit is insufficient.
            if cached.closure.intersection(active):
                return _Module(reason='Cyclic captured namespace import ancestry')
            if len(active) + cached.height > graph.MAX_GRAPH_DEPTH:
                return _Module(reason='Captured namespace import depth limit exceeded')
            return cached
        if path in self.refusals:
            return self.refusals[path]
        item = self.graph.source_for(path)
        if item is None:
            return self.refuse_module(path, 'Imported source snapshot is unavailable')
        # The graph also records nonliteral imports inside deferred/local
        # scopes with an empty request. Those are not parser failures and do
        # not expose root names. A root dynamic import is refused by incoming.
        refused = next((u for u in self.graph.unavailable if u.path == path and not u.request
                        and u.reason != 'Dynamic or escaped import is unsupported'), None)
        if refused is not None:
            return self.refuse_module(path, f'Captured imported namespace is unavailable: {refused.reason}')
        if path not in self.trees:
            try:
                self.trees[path] = parse(item.source)
            except AdapterError as error:
                self.trees[path] = f'Imported namespace syntax unavailable: {error}'
        tree = self.trees[path]
        if type(tree) is str:
            return self.refuse_module(path, tree)
        if tree.nodes[0].errors:
            return self.refuse_module(path, 'Malformed imported namespace syntax')
        if not self.charge_ast(tree):
            return _Module(reason=self.exhausted)
        env, sources, packages, requests = {}, {path: SourceHash(path, item.sha256)}, {}, set()
        dependency_height = 0
        for index in tree.nodes[0].children:
            node = tree.nodes[index]
            if node.kind in graph._TRIVIA or node.kind in ('Hash', 'Semicolon'):
                continue
            if _assignment(tree, index):
                return self.refuse_module(path, f'Unknown namespace mutation in {path} at byte {node.start}')
            if _namespace_injection(tree, index):
                return self.refuse_module(path, f'Expression-position namespace declaration in {path} at byte {node.start}')
            if node.kind == 'ModuleImport':
                incoming = self.incoming(tree, index, path, (*active, path))
                if incoming.reason:
                    return (self.refuse_module(path, incoming.reason)
                            if incoming.intrinsic_refusal else incoming)
                env.update(incoming.exports)
                if not self.bindings(env):
                    return _Module(reason=self.exhausted)
                sources.update((s.path, s) for s in incoming.sources)
                packages.update((p.import_path, p) for p in incoming.packages)
                requests.update(incoming.requests)
                dependency_height = max(dependency_height, incoming.height)
            elif node.kind == 'LetBinding':
                binding = _let(tree, index, env, path, self)
                if binding is None:
                    return self.refuse_module(path, f'Unsupported root namespace declaration in {path}')
                env[binding[0]] = binding[1]
                if not self.bindings(env):
                    return _Module(reason=self.exhausted)
            elif node.kind in ('ModuleInclude', 'CodeBlock', 'Conditional', 'WhileLoop', 'ForLoop', 'Contextual'):
                return self.refuse_module(path, f'Unknown namespace scope in {path} at byte {node.start}')
            # Ordinary source output and calls do not declare root names. Their
            # execution and rendering are conditions, never a purity claim.
        result = _Module(tuple(sorted(env.items())), tuple(sources[k] for k in sorted(sources)),
                         tuple(packages[k] for k in sorted(packages)), tuple(sorted(requests)),
                         height=1 + dependency_height)
        self.modules[path] = result
        return result


def _body_math(tree, call_id):
    children = tree.nodes[call_id].children
    if len(children) != 2 or tree.nodes[children[1]].kind != 'Args':
        return False, 'Unsupported highlight call arguments'
    bodies = []
    for child in graph.significant(tree, children[1]):
        node = tree.nodes[child]
        if node.kind == 'ContentBlock':
            bodies.append(child)
        elif node.kind == 'Named':
            parts = graph.significant(tree, child)
            if (len(parts) == 3 and tree.nodes[parts[0]].kind == 'Ident'
                    and graph.raw(tree, parts[0]) == b'body'):
                if tree.nodes[parts[2]].kind != 'ContentBlock':
                    return False, 'Highlight body is not literal content'
                bodies.append(parts[2])
            else:
                return False, 'Unsupported named argument for the single-body highlighted helper'
        elif node.kind not in ('LeftParen', 'RightParen', 'Comma'):
            return False, 'Highlight body is not one literal content argument'
    if len(bodies) != 1:
        return False, 'Highlight body is not one literal content argument'
    def rendered(index):
        node = tree.nodes[index]
        if node.kind == 'Equation':
            return True
        return node.kind in ('ContentBlock', 'Markup', 'Strong', 'Emph') and any(
            rendered(c) for c in node.children)
    return rendered(bodies[0]), None


def _wrapper_bodies(tree, index, canonical_name):
    """One direct literal body of a recognized wrapper; no callback model."""
    children = tree.nodes[index].children
    if len(children) != 2 or tree.nodes[children[1]].kind != 'Args':
        return ()
    bodies = []
    named = set()
    for child in graph.significant(tree, children[1]):
        kind = tree.nodes[child].kind
        if kind == 'ContentBlock':
            bodies.append(child)
        elif kind == 'Named':
            parts = graph.significant(tree, child)
            if (len(parts) != 3 or tree.nodes[parts[0]].kind != 'Ident'
                    or canonical_name == 'proof' or graph.raw(tree, parts[0]) != b'title'
                    or 'title' in named):
                return ()
            named.add('title')
        elif kind not in ('LeftParen', 'RightParen', 'Comma'):
            return ()
    return tuple(bodies) if len(bodies) == 1 else ()


class ConditionalHighlightAnalyzer:
    """One immutable capture session; parsed dependency trees are reused only here."""
    def __init__(self, import_graph, namespace_capture, *, known_rule_ids=None):
        self._analyzer = _Analyzer(import_graph, namespace_capture)
        # Catalog configuration is captured once, separately from source
        # analysis. Callers with an already loaded catalog can pass its IDs.
        self._analyzer.known_rule_ids = frozenset(known_rule_ids if known_rule_ids is not None
            else (r['id'] for r in load_rules()[0]['rules']))

    def analyze(self, tree, path):
        return _analyze(tree, path, self._analyzer)


def analyze(tree, path, import_graph, namespace_capture):
    """Return separate CONDITIONAL findings; no trusted binding or edit exists."""
    return ConditionalHighlightAnalyzer(import_graph, namespace_capture).analyze(tree, path)


def _analyze(tree, path, analyzer):
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        return ConditionalHighlightResult(unresolved=('Malformed root Typst syntax',),
            parse_errors=tuple((n.start, n.end) for n in nodes if n.kind == 'Error') or ((0, len(source)),))
    if analyzer.reason:
        return ConditionalHighlightResult(unresolved=(analyzer.reason,))
    if analyzer.exhausted:
        return ConditionalHighlightResult(unresolved=(analyzer.exhausted,))
    import_graph = analyzer.graph
    item = import_graph.source_for(path)
    if path not in import_graph.selected or item is None or item.source != source:
        return ConditionalHighlightResult(unresolved=('Root source differs from the selected captured snapshot',))
    if tree.mode != 'markup':
        return ConditionalHighlightResult(unresolved=('Only root markup calls have conditional namespace ownership',))
    if not analyzer.charge_ast(tree):
        return ConditionalHighlightResult(unresolved=(analyzer.exhausted,))
    scopes = suppressions(tree, analyzer.known_rule_ids)
    findings, suppressed, unresolved = [], [], []

    def call_value(index, env):
        node = nodes[index]
        if node.kind == 'FuncCall' and node.children and nodes[node.children[0]].kind == 'Ident':
            return env.get(graph.raw(tree, node.children[0]).decode('utf-8'))
        return None

    def nested(index, env, skip=()):
        for child in _descendants(tree, index, skip):
            value = call_value(child, env)
            if value is not None and value.canonical_name == 'highlighted':
                unresolved.append(f'Nested or deferred helper call has unresolved execution context at byte {nodes[child].start}')

    def scan(sequence, env, sources, packages, requests, context=()):
        mutated_outer = False
        for index in nodes[sequence].children:
            if analyzer.exhausted:
                break
            node = nodes[index]
            if node.kind in graph._TRIVIA or node.kind in ('Hash', 'Semicolon'):
                continue
            value = call_value(index, env)
            bodies = (_wrapper_bodies(tree, index, value.canonical_name) if value is not None
                      and value.canonical_name in _CONTENT_WRAPPERS else ())
            # Admitted literal bodies are scanned exactly once below. Outer
            # assignment/descendant scans skip these subtrees, retaining the
            # three source-wide walks charged by charge_ast rather than depth*n.
            if _assignment(tree, index, bodies):
                env.clear()
                mutated_outer = True
                unresolved.append(f'Unknown active namespace mutation at byte {node.start}')
                continue
            if _namespace_injection(tree, index):
                env.clear()
                unresolved.append(f'Expression-position namespace declaration at byte {node.start}')
                continue
            if node.kind == 'ModuleImport':
                incoming = analyzer.incoming(tree, index, path, (path,))
                if incoming.reason:
                    env.clear()
                    unresolved.append(f'{incoming.reason} at byte {node.start}')
                else:
                    env.update(incoming.exports)
                    analyzer.bindings(env)
                    sources.update((s.path, s) for s in incoming.sources)
                    packages.update((p.import_path, p) for p in incoming.packages)
                    requests.update(incoming.requests)
                continue
            nested(index, env, bodies)
            if node.kind == 'LetBinding':
                binding = _let(tree, index, env, path, analyzer)
                if binding is None:
                    env.clear()
                    unresolved.append(f'Unsupported root namespace declaration at byte {node.start}')
                else:
                    env[binding[0]] = binding[1]
                    analyzer.bindings(env)
                continue
            if node.kind in ('ModuleInclude', 'CodeBlock', 'Conditional', 'WhileLoop', 'ForLoop', 'Contextual'):
                env.clear()
                mutated_outer = True
                unresolved.append(f'Unknown root namespace scope at byte {node.start}')
                continue
            if bodies:
                for body in bodies:
                    parts = graph.significant(tree, body)
                    markup = next((c for c in parts if nodes[c].kind == 'Markup'), None)
                    if markup is None or not analyzer.charge_scope(env, sources, packages, requests, context):
                        break
                    changed = scan(markup, dict(env), dict(sources), dict(packages), set(requests),
                                   context + (value.route,))
                    if changed:
                        # Local declarations stay inside the content scope;
                        # assignments/default mutations can change outer vars.
                        env.clear()
                        mutated_outer = True
                continue
            if value is None or value.canonical_name != 'highlighted':
                continue
            has_math, reason = _body_math(tree, index)
            if reason:
                unresolved.append(f'{reason} at byte {node.start}')
            if not has_math:
                continue
            line = source[:node.start].count(b'\n') + 1
            column = len(source[source.rfind(b'\n', 0, node.start) + 1:node.start].decode('utf-8')) + 1
            conditions = CONDITIONS + (('Every enclosing recognized literal content wrapper executes and renders this body.',)
                                       if context else ())
            finding = ConditionalDiagnostic(RULE_ID, path, line, column, node.start, node.end,
                'Keep math outside text highlighting, conditional on the captured helper identity and execution context.',
                digest(source), tuple(sources[k] for k in sorted(sources)),
                tuple(packages[k] for k in sorted(packages)), tuple(sorted(requests)), value.route,
                conditions=conditions, content_context=context)
            (suppressed if any(s.covers(RULE_ID, node.start) for s in scopes) else findings).append(finding)
        return mutated_outer

    scan(0, {}, {path: SourceHash(path, digest(source))},
         {p.import_path: p for p in analyzer.evidence}, set())
    if analyzer.exhausted:
        return ConditionalHighlightResult(unresolved=(analyzer.exhausted,))
    return ConditionalHighlightResult(tuple(findings), tuple(suppressed), tuple(unresolved))
