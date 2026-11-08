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
from typst_lint import InputError, digest, load_rules

RULE_ID = 'pde.text-only-highlight'
MARKER_GAP = 'pde.paragraph-marker-gap'
SUPPORTED_RULE_IDS = (RULE_ID, MARKER_GAP)
TEXT_UTILS = '@local/text-utils:0.1.2'
MATH_BLOCKS = '@local/math-blocks:0.2.0'
SCOPED_ANNOTATIONS = '@local/scoped-annotations:0.3.0'
_CONTENT_WRAPPERS = frozenset(('proof', 'theorem', 'note'))
_SCOPED_CALLBACKS = {
    'local-tag-scope': frozenset(('prefix',)),
    'local-scope-annotations': frozenset(('prefix', 'parent', 'name')),
}
CONDITIONS = (
    'Every referenced module loads and initializes successfully; initializer purity is not proved.',
    'Runtime imports select the exact captured package versions and bytes; ambient package selection is not proved.',
    'The reported source call actually executes and its literal body is rendered in the intended document context.',
)
MARKER_CONDITIONS = CONDITIONS[:2] + (
    'The reported source marker is emitted and used as a paragraph marker in the intended document context.',
    'The document applies the captured paragraph-tab transformation; this source pattern does not prove paragraph rendering.',
)
CALLBACK_CONDITIONS = (
    'Every captured scoped callback is invoked by its exact pinned wrapper with the documented single dictionary argument.',
    'The callback returns this literal content for actual rendering; callback invocation, argument values and rendering are not proved.',
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
class CallbackContext:
    content_context_index: int
    call_start: int
    call_end: int
    body_start: int
    body_end: int
    parameters: tuple[str, ...]


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
    callback_context: tuple[CallbackContext, ...] = ()
    status: str = field(default='CONDITIONAL', init=False)
    edit_permission: bool = field(default=False, init=False)


@dataclass(frozen=True)
class ConditionalHighlightResult:
    diagnostics: tuple[ConditionalDiagnostic, ...] = ()
    suppressed: tuple[ConditionalDiagnostic, ...] = ()
    unresolved: tuple[str, ...] = ()
    parse_errors: tuple[tuple[int, int], ...] = ()
    rule_reasons: tuple[tuple[str, tuple[str, ...]], ...] = ()
    status: str = field(default='CONDITIONAL', init=False)
    edit_permission: bool = field(default=False, init=False)

    def reasons_for(self, rule_id):
        for selected, reasons in self.rule_reasons:
            if selected == rule_id:
                return reasons
        return self.unresolved if not self.rule_reasons and rule_id == RULE_ID else ()


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


class _CallbackReturn(Exception):
    def __init__(self, start):
        self.start = start


def _effects(tree, index, skip=(), *, return_barrier=False):
    """Unknown active mutation defeats lexical identity, even inside arguments.

    A function body is deferred. Parameter defaults are still checked
    conservatively; their evaluation timing is not certified here.
    """
    pending = [index]
    mutation = None
    while pending:
        current = pending.pop()
        if current in skip:
            continue
        node = tree.nodes[current]
        if node.kind == 'Closure':
            pending.extend(c for c in node.children if tree.nodes[c].kind == 'Params')
            continue
        if return_barrier and node.kind == 'FuncReturn':
            return 'return', node.start
        if node.kind == 'DestructAssignment':
            mutation = 'mutation', node.start
        if node.kind == 'Binary' and any(tree.nodes[c].kind in _ASSIGNMENTS
                                        for c in graph.significant(tree, current)):
            mutation = 'mutation', node.start
        pending.extend(node.children)
    return mutation


def _assignment(tree, index, skip=()):
    return _effects(tree, index, skip) is not None


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
    def __init__(self, context, namespaces, rule_ids, scoped_callbacks):
        self.graph = context
        self.rule_ids = frozenset(rule_ids)
        self.scoped_callbacks = scoped_callbacks
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

    def charge_ast(self, tree, *, passes=3):
        # Each root sequence is walked for active assignment, namespace
        # injection and nested-call ownership. Charge all three walks even
        # when a refusal or deferred body would skip part of the work. Opt-in
        # marker discovery also scans unknown-context Markup sibling rows.
        self.ast_work += passes * len(tree.nodes)
        if self.ast_work > MAX_AST_WORK:
            self.exhaust('Conditional namespace AST work limit exceeded')
        return self.exhausted is None

    def charge_scope(self, env, sources, packages, requests, context, *, callbacks=(), parameters=()):
        # Each inherited namespace/evidence/context container is copied once;
        # immutable route entries inside the context remain shared.
        self.ast_work += sum(map(len, (env, sources, packages, requests, context))) + 1
        if parameters:
            self.ast_work += len(callbacks) + 1 + len(parameters)
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
                marker = (MARKER_GAP in self.rule_ids and request == TEXT_UTILS
                          and name in ('paragraph-tab', 'paragraph_tab'))
                supported = ((name == 'highlighted' and origins.get(name) == TEXT_UTILS)
                             or (request == MATH_BLOCKS and name in _CONTENT_WRAPPERS) or marker
                             or (self.scoped_callbacks and request == SCOPED_ANNOTATIONS
                                 and name in _SCOPED_CALLBACKS))
                exports[name] = (_Value((NameRoute(request, package.evidence.source_sha256,
                    'package-export', request, name, name),), 'paragraph_tab' if marker else name)
                    if supported else None)
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


def _scoped_body(tree, index, canonical_name):
    """One direct callback, one opaque dictionary parameter, literal return."""
    children = tree.nodes[index].children
    if len(children) != 2 or tree.nodes[children[1]].kind != 'Args':
        return None, 'Scoped wrapper requires one direct positional callback'
    positional, named = [], set()
    for child in graph.significant(tree, children[1]):
        kind = tree.nodes[child].kind
        if kind in ('LeftParen', 'RightParen', 'Comma'):
            continue
        if kind == 'Named':
            parts = graph.significant(tree, child)
            if len(parts) != 3 or tree.nodes[parts[0]].kind != 'Ident':
                return None, 'Unsupported scoped-wrapper option syntax'
            name = graph.raw(tree, parts[0]).decode('utf-8')
            if name not in _SCOPED_CALLBACKS[canonical_name] or name in named:
                return None, 'Unknown or duplicate scoped-wrapper option'
            if tree.nodes[parts[2]].kind != 'Auto':
                return None, 'Scoped-wrapper options require literal auto values'
            named.add(name)
        else:
            positional.append(child)
    if len(positional) != 1 or tree.nodes[positional[0]].kind != 'Closure':
        return None, 'Scoped wrapper requires one direct positional callback'
    closure = graph.significant(tree, positional[0])
    if (len(closure) != 3 or tree.nodes[closure[0]].kind != 'Params'
            or tree.nodes[closure[1]].kind != 'Arrow'):
        return None, 'Scoped callback requires an anonymous arrow closure'
    params = graph.significant(tree, closure[0])
    if (len(params) == 3 and tree.nodes[params[0]].kind == 'LeftParen'
            and tree.nodes[params[2]].kind == 'RightParen'):
        params = (params[1],)
    if len(params) != 1 or tree.nodes[params[0]].kind != 'Ident':
        return None, 'Scoped callback requires exactly one positional Ident parameter'
    body = closure[2]
    while tree.nodes[body].kind == 'Parenthesized':
        parts = graph.significant(tree, body)
        if (len(parts) != 3 or tree.nodes[parts[0]].kind != 'LeftParen'
                or tree.nodes[parts[2]].kind != 'RightParen'):
            return None, 'Scoped callback return is not one literal content block'
        body = parts[1]
    if tree.nodes[body].kind != 'ContentBlock':
        return None, 'Scoped callback return is not one literal content block'
    return (body, (graph.raw(tree, params[0]).decode('utf-8'),)), None


def _marker_gap(tree, sequence, position):
    """Physical source-line pattern, not proof of a rendered prose paragraph."""
    source, nodes = tree.source, tree.nodes
    siblings = nodes[sequence].children
    marker = nodes[siblings[position]]
    previous = nodes[siblings[position - 1]] if position else None
    if previous is None or previous.kind != 'Hash' or previous.end != marker.start:
        return False, marker.start, marker.end, 'Paragraph marker requires adjacent same-Markup Hash+Ident'
    start, end = previous.start, marker.end
    line_start = source.rfind(b'\n', 0, start) + 1
    line_end = source.find(b'\n', end)
    line_end = len(source) if line_end < 0 else line_end
    prefix = source[line_start:start]
    if line_start == 0 and prefix.startswith(b'\xef\xbb\xbf'):
        prefix = prefix[3:]
    if prefix.strip(b' \t') or source[end:line_end].strip(b' \t\r'):
        return False, start, end, 'Paragraph marker is not standalone on its whole physical source line'
    previous_start = source.rfind(b'\n', 0, max(0, line_start - 1)) + 1
    preceding_line = source[previous_start:line_start]
    physical_line = source[line_start:min(len(source), line_end + 1)]
    if b'\r' in (preceding_line + physical_line).replace(b'\r\n', b''):
        return False, start, end, 'Bare-CR paragraph-marker source boundaries are unsupported'
    target_position = position + 1
    while target_position < len(siblings) and nodes[siblings[target_position]].kind in graph._TRIVIA:
        target_position += 1
    target = nodes[siblings[target_position]] if target_position < len(siblings) else None
    if target is not None and (target.kind == 'Heading' or (target.kind == 'Equation' and target.display)):
        return False, start, end, 'Paragraph-marker target is a heading/display; marker-target rule takes precedence'
    # Never clip an opener/BOM off the previous physical source line and then
    # mistake its remaining newline for a blank line inside this Markup.
    blank = (previous_start >= nodes[sequence].start and preceding_line.endswith(b'\n')
             and not preceding_line.strip(b' \t\r\n'))
    return not blank, start, end, None


class ConditionalHighlightAnalyzer:
    """One captured session; default highlight-only, marker gaps opt in explicitly."""
    def __init__(self, import_graph, namespace_capture, *, known_rule_ids=None, rule_ids=(RULE_ID,),
                 scoped_callbacks=False):
        if (type(rule_ids) not in (tuple, frozenset) or not rule_ids
                or any(type(rid) is not str or rid not in SUPPORTED_RULE_IDS for rid in rule_ids)
                or len(set(rule_ids)) != len(rule_ids)):
            raise InputError('Conditional inspection requires explicit supported rule IDs')
        if type(scoped_callbacks) is not bool:
            raise InputError('Scoped callback inspection requires an explicit boolean option')
        self._analyzer = _Analyzer(import_graph, namespace_capture, rule_ids, scoped_callbacks)
        # Catalog configuration is captured once, separately from source
        # analysis. Callers with an already loaded catalog can pass its IDs.
        self._analyzer.known_rule_ids = frozenset(known_rule_ids if known_rule_ids is not None
            else (r['id'] for r in load_rules()[0]['rules']))

    def analyze(self, tree, path):
        return _analyze(tree, path, self._analyzer)


def analyze(tree, path, import_graph, namespace_capture, *, rule_ids=(RULE_ID,), scoped_callbacks=False):
    """Return separate CONDITIONAL findings; no trusted binding or edit exists."""
    return ConditionalHighlightAnalyzer(import_graph, namespace_capture, rule_ids=rule_ids,
                                        scoped_callbacks=scoped_callbacks).analyze(tree, path)


def _analyze(tree, path, analyzer):
    source, nodes = tree.source, tree.nodes

    def refusal(reason, parse_errors=()):
        return ConditionalHighlightResult(unresolved=(reason,), parse_errors=parse_errors,
            rule_reasons=tuple((rid, (reason,)) for rid in SUPPORTED_RULE_IDS if rid in analyzer.rule_ids))

    if nodes[0].errors:
        return refusal('Malformed root Typst syntax',
            tuple((n.start, n.end) for n in nodes if n.kind == 'Error') or ((0, len(source)),))
    if analyzer.reason:
        return refusal(analyzer.reason)
    if analyzer.exhausted:
        return refusal(analyzer.exhausted)
    import_graph = analyzer.graph
    item = import_graph.source_for(path)
    if path not in import_graph.selected or item is None or item.source != source:
        return refusal('Root source differs from the selected captured snapshot')
    if tree.mode != 'markup':
        return refusal('Only root markup calls have conditional namespace ownership')
    marker_enabled = MARKER_GAP in analyzer.rule_ids
    if not analyzer.charge_ast(tree, passes=4 if marker_enabled else 3):
        return refusal(analyzer.exhausted)
    scopes = suppressions(tree, analyzer.known_rule_ids)
    findings, suppressed, unresolved = [], [], []
    rule_reasons = {rid: [] for rid in analyzer.rule_ids}

    def record(reason, rule_id=None):
        unresolved.append(reason)
        for rid in rule_reasons:
            if rule_id is None or rid == rule_id:
                rule_reasons[rid].append(reason)

    def call_value(index, env):
        node = nodes[index]
        if node.kind == 'FuncCall' and node.children and nodes[node.children[0]].kind == 'Ident':
            return env.get(graph.raw(tree, node.children[0]).decode('utf-8'))
        return None

    def nested(index, env, skip=()):
        for child in _descendants(tree, index, skip):
            value = call_value(child, env)
            if (RULE_ID in analyzer.rule_ids and value is not None
                    and value.canonical_name == 'highlighted'):
                record(f'Nested or deferred helper call has unresolved execution context at byte {nodes[child].start}', RULE_ID)
            if marker_enabled and nodes[child].kind == 'Markup':
                siblings = nodes[child].children
                for position, candidate in enumerate(siblings):
                    marker = nodes[candidate]
                    value = env.get(graph.raw(tree, candidate).decode('utf-8')) if marker.kind == 'Ident' else None
                    previous = nodes[siblings[position - 1]] if position else None
                    if (value is not None and value.canonical_name == 'paragraph_tab' and previous is not None
                            and previous.kind == 'Hash' and previous.end == marker.start):
                        record(f'Nested paragraph-marker expression has unresolved binding/execution context at byte {previous.start}', MARKER_GAP)

    def emit(rule_id, start, end, message, value, sources, packages, requests, context, callbacks, conditions):
        line = source[:start].count(b'\n') + 1
        column = len(source[source.rfind(b'\n', 0, start) + 1:start].decode('utf-8')) + 1
        conditions += (('Every enclosing recognized literal content wrapper executes and renders this body.',)
                       if len(context) > len(callbacks) else ())
        if callbacks:
            conditions += CALLBACK_CONDITIONS
        finding = ConditionalDiagnostic(rule_id, path, line, column, start, end, message,
            digest(source), tuple(sources[k] for k in sorted(sources)),
            tuple(packages[k] for k in sorted(packages)), tuple(sorted(requests)), value.route,
            conditions=conditions, content_context=context, callback_context=callbacks)
        (suppressed if any(s.covers(rule_id, start) for s in scopes) else findings).append(finding)

    def scan(sequence, env, sources, packages, requests, context=(), callbacks=()):
        mutated_outer = False
        for position, index in enumerate(nodes[sequence].children):
            if analyzer.exhausted:
                break
            node = nodes[index]
            if node.kind in graph._TRIVIA or node.kind in ('Hash', 'Semicolon'):
                continue
            value = call_value(index, env)
            bodies = (_wrapper_bodies(tree, index, value.canonical_name) if value is not None
                      and value.canonical_name in _CONTENT_WRAPPERS else ())
            callback, callback_reason = (None, None)
            if value is not None and value.canonical_name in _SCOPED_CALLBACKS:
                callback, callback_reason = _scoped_body(tree, index, value.canonical_name)
                if callback is not None:
                    bodies = (callback[0],)
            # Admitted literal bodies are scanned exactly once below. Outer
            # assignment/descendant scans skip these subtrees, retaining the
            # bounded source-wide walks charged by charge_ast rather than depth*n.
            effect = _effects(tree, index, bodies, return_barrier=bool(callbacks))
            if effect is not None and effect[0] == 'return':
                raise _CallbackReturn(effect[1])
            if effect is not None:
                env.clear()
                mutated_outer = True
                record(f'Unknown active namespace mutation at byte {node.start}')
                continue
            if _namespace_injection(tree, index):
                env.clear()
                record(f'Expression-position namespace declaration at byte {node.start}')
                continue
            if node.kind == 'ModuleImport':
                incoming = analyzer.incoming(tree, index, path, (path,))
                if incoming.reason:
                    env.clear()
                    record(f'{incoming.reason} at byte {node.start}')
                else:
                    env.update(incoming.exports)
                    analyzer.bindings(env)
                    sources.update((s.path, s) for s in incoming.sources)
                    packages.update((p.import_path, p) for p in incoming.packages)
                    requests.update(incoming.requests)
                continue
            if callback_reason:
                record(f'{callback_reason} at byte {node.start}')
            nested(index, env, bodies)
            if node.kind == 'LetBinding':
                binding = _let(tree, index, env, path, analyzer)
                if binding is None:
                    env.clear()
                    record(f'Unsupported root namespace declaration at byte {node.start}')
                else:
                    env[binding[0]] = binding[1]
                    analyzer.bindings(env)
                continue
            if node.kind == 'Contextual' and callbacks:
                # Context captures its surrounding lexical bindings. Its body
                # remains unresolved; assignments and returns detected by the
                # syntactic effects scan were already refused above. Other
                # effects and the rendering context are not proved here.
                record(f'Contextual body has unresolved execution context at byte {node.start}')
                continue
            if node.kind in ('ModuleInclude', 'CodeBlock', 'Conditional', 'WhileLoop', 'ForLoop', 'Contextual'):
                env.clear()
                mutated_outer = True
                record(f'Unknown root namespace scope at byte {node.start}')
                continue
            if bodies:
                for body in bodies:
                    parts = graph.significant(tree, body)
                    markup = next((c for c in parts if nodes[c].kind == 'Markup'), None)
                    if markup is None or not analyzer.charge_scope(env, sources, packages, requests, context,
                                                                  callbacks=callbacks,
                                                                  parameters=callback[1] if callback is not None else ()):
                        break
                    child_env, child_callbacks = dict(env), callbacks
                    if callback is not None:
                        child_env.update((name, None) for name in callback[1])
                        analyzer.bindings(child_env)
                        child_callbacks += (CallbackContext(len(context), node.start, node.end,
                            nodes[body].start, nodes[body].end, callback[1]),)
                    before = len(findings), len(suppressed)
                    try:
                        changed = scan(markup, child_env, dict(sources), dict(packages), set(requests),
                                       context + (value.route,), child_callbacks)
                    except _CallbackReturn as barrier:
                        if callback is None:
                            raise
                        # A return can discard content constructed before it;
                        # do not resume unreachable imports/statements or keep
                        # hints from this callback's abandoned literal body.
                        del findings[before[0]:]
                        del suppressed[before[1]:]
                        record(f'Active return leaves scoped callback rendering unresolved at byte {barrier.start}')
                        changed = False
                    if changed and callback is None:
                        # Local declarations stay inside the content scope;
                        # assignments/default mutations can change outer vars.
                        env.clear()
                        mutated_outer = True
                continue
            if marker_enabled:
                marker = env.get(graph.raw(tree, index).decode('utf-8')) if node.kind == 'Ident' else value
                if marker is not None and marker.canonical_name == 'paragraph_tab':
                    if node.kind != 'Ident':
                        record(f'Paragraph marker requires own-line Hash+Ident at byte {node.start}', MARKER_GAP)
                        continue
                    gap, start, end, reason = _marker_gap(tree, sequence, position)
                    if reason:
                        record(f'{reason} at byte {start}', MARKER_GAP)
                    if gap:
                        emit(MARKER_GAP, start, end,
                             'Place a whitespace-only blank source line before the standalone paragraph marker.',
                             marker, sources, packages, requests, context, callbacks, MARKER_CONDITIONS)
                    continue
            if RULE_ID not in analyzer.rule_ids or value is None or value.canonical_name != 'highlighted':
                continue
            has_math, reason = _body_math(tree, index)
            if reason:
                record(f'{reason} at byte {node.start}', RULE_ID)
            if not has_math:
                continue
            emit(RULE_ID, node.start, node.end,
                'Keep math outside text highlighting, conditional on the captured helper identity and execution context.',
                value, sources, packages, requests, context, callbacks, CONDITIONS)
        return mutated_outer

    scan(0, {}, {path: SourceHash(path, digest(source))},
         {p.import_path: p for p in analyzer.evidence}, set())
    if analyzer.exhausted:
        return refusal(analyzer.exhausted)
    return ConditionalHighlightResult(tuple(findings), tuple(suppressed), tuple(unresolved),
        rule_reasons=tuple((rid, tuple(rule_reasons[rid])) for rid in SUPPORTED_RULE_IDS if rid in rule_reasons))
