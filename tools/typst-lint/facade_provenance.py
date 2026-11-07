"""Bounded export-access evidence for captured, immutable local facades.

This capability is intentionally separate from ``import_graph.ModuleEvidence``:
safe access to a deferred helper is not purity of that helper's calls, and is
never permission to reorder or remove an import. No filesystem/package lookup
or evaluation occurs here. PackageSource retains the existing trusted-loader
contract: the loader has checked manifest metadata and source identity; actual
bytes and the one initializer certificate below are checked again here.
"""
from dataclasses import dataclass

import import_graph as graph
from static_resolver import (PackageSource, REVIEWED_PACKAGES, TEXT_UTILS,
                             TEXT_UTILS_EXPORTS, TEXT_UTILS_SOURCE_SHA256)
from syntax_client import AdapterError, parse
from typst_lint import digest


@dataclass(frozen=True)
class CapturedIdentity:
    path: str
    source_sha256: str


@dataclass(frozen=True)
class PackageOrigin:
    import_path: str
    source_identity: str
    source_sha256: str
    export_name: str
    canonical_name: str


@dataclass(frozen=True)
class FacadeExport:
    """Original value identity; aliases retain it, never their new spelling."""
    path: str
    source_sha256: str
    name: str
    package_origin: PackageOrigin | None = None


@dataclass(frozen=True)
class FacadeEvidence:
    path: str
    source_sha256: str
    exports: tuple[tuple[str, FacadeExport], ...] = ()
    dependencies: tuple[CapturedIdentity, ...] = ()
    package_origins: tuple[PackageOrigin, ...] = ()
    accepted: bool = False
    reason: str | None = None
    depth: int = 1


@dataclass(frozen=True)
class InitializerCertificate:
    import_path: str
    source_identity: str
    source_sha256: str
    exports: tuple[tuple[str, FacadeExport], ...] = ()
    accepted: bool = False
    reason: str | None = None


def _statements(tree):
    """Root hash-adjacent statements only, with no unexamined output."""
    siblings = tree.nodes[0].children
    statements, position = [], 0
    while position < len(siblings):
        index = siblings[position]
        node = tree.nodes[index]
        position += 1
        if node.kind in graph._TRIVIA:
            continue
        if node.kind != 'Hash' or position == len(siblings):
            return None
        index = siblings[position]
        position += 1
        if node.end != tree.nodes[index].start:
            return None
        statements.append(index)
        # A semicolon belongs to the immediately preceding statement, not an
        # independent output/initializer. Nothing else may be skipped here.
        if position < len(siblings) and tree.nodes[siblings[position]].kind == 'Semicolon':
            position += 1
    return tuple(statements)


def certify_initializer(package):
    """Only exact text-utils 0.1.2 may initialize a facade's package leaf.

    The six declarations create two immutable marker names and four deferred
    functions with one identifier parameter each. Their bodies are not called
    or certified as pure. Other reviewed packages have no such certificate.
    """
    valid = (type(package) is PackageSource and type(package.source) is bytes
             and type(package.import_path) is str
             and type(package.source_identity) is str and bool(package.source_identity)
             and type(package.artifacts) is tuple)
    path = package.import_path if valid else ''
    identity = package.source_identity if valid else ''
    sha = digest(package.source) if valid else ''
    def refusal(reason):
        return InitializerCertificate(path, identity, sha, reason=reason)
    contract = next((p for p in REVIEWED_PACKAGES if p.import_path == path), None)
    if (not valid or path != TEXT_UTILS or contract is None
            or (contract.namespace, contract.name, contract.version, contract.entrypoint)
            != ('local', 'text-utils', '0.1.2', 'lib.typ')
            or contract.source_sha256 != TEXT_UTILS_SOURCE_SHA256
            or contract.exports != TEXT_UTILS_EXPORTS or sha != TEXT_UTILS_SOURCE_SHA256):
        return refusal('Package has no reviewed safe-initializer certificate')
    try:
        tree = parse(package.source)
    except AdapterError:
        return refusal('Package initializer syntax is unavailable')
    statements = _statements(tree)
    if tree.nodes[0].errors or statements is None or len(statements) != 6:
        return refusal('Package initializer declarations do not match the certificate')
    for position, index in enumerate(statements):
        parts = graph.significant(tree, index)
        if tree.nodes[index].kind != 'LetBinding' or not parts or tree.nodes[parts[0]].kind != 'Let':
            return refusal('Package initializer contains an unsupported root statement')
        if position < 2:
            expected_name = ('paragraph-tab', 'paragraph_tab')[position]
            expected_value = (b'"\\u{F000}"', b'paragraph-tab')[position]
            if (len(parts) != 4 or tree.nodes[parts[1]].kind != 'Ident'
                    or graph.raw(tree, parts[1]).decode('utf-8') != expected_name
                    or tree.nodes[parts[2]].kind != 'Eq'
                    or tree.nodes[parts[3]].kind != ('Str', 'Ident')[position]
                    or graph.raw(tree, parts[3]) != expected_value):
                return refusal('Package marker initializer is not the reviewed immutable value')
            continue
        if len(parts) != 2 or tree.nodes[parts[1]].kind != 'Closure':
            return refusal('Package helper initializer is not a deferred function')
        closure = graph.significant(tree, parts[1])
        name, parameter, body_kind = (
            ('apply-paragraph-tabs', 'body', 'CodeBlock'),
            ('highlighted', 'body', 'Contextual'),
            ('capitalize-title', 'title', 'CodeBlock'),
            ('is-numeric-title', 'title', 'CodeBlock'),
        )[position - 2]
        if (len(closure) != 4 or tuple(tree.nodes[c].kind for c in closure)
                != ('Ident', 'Params', 'Eq', body_kind)
                or graph.raw(tree, closure[0]).decode('utf-8') != name):
            return refusal('Package deferred helper declaration does not match the certificate')
        params = graph.significant(tree, closure[1])
        if (len(params) != 3 or tuple(tree.nodes[c].kind for c in params)
                != ('LeftParen', 'Ident', 'RightParen')
                or graph.raw(tree, params[1]).decode('utf-8') != parameter):
            return refusal('Package helper parameters have defaults or unsupported patterns')
    # The exact-byte gate also pins all deferred bodies and proves there are no
    # external dependencies. In particular, no generic Closure whitelist exists.
    values, exports = {}, []
    for name, canonical in TEXT_UTILS_EXPORTS:
        if canonical not in values:
            values[canonical] = FacadeExport(path, sha, canonical,
                PackageOrigin(path, identity, sha, name, canonical))
        exports.append((name, values[canonical]))
    return InitializerCertificate(path, identity, sha, tuple(exports), True)


def _graph_reason(value):
    """Validate captured graph structure/budgets without trusting annotations."""
    if (type(value) is not graph.ImportGraph or type(value.project_root) is not str
            or type(value.sources) is not tuple or type(value.selected) is not tuple
            or type(value.edges) is not tuple or type(value.unavailable) is not tuple):
        return 'Facade proof requires an immutable captured import graph'
    if len(value.sources) > graph.MAX_GRAPH_FILES:
        return 'Facade graph file limit exceeded'
    if len(value.edges) + len(value.unavailable) > graph.MAX_GRAPH_EDGES:
        return 'Facade graph edge limit exceeded'
    sources, total = {}, 0
    try:
        for source in value.sources:
            if (type(source) is not graph.ImportSource or type(source.source) is not bytes
                    or source.path in sources or len(source.source) > graph.MAX_INPUT):
                return 'Invalid, duplicate, mutable or oversized facade source'
            graph._seed_path(source.path)
            sources[source.path] = source
            total += len(source.source)
        if total > graph.MAX_GRAPH_BYTES:
            return 'Facade graph aggregate byte limit exceeded'
        if (len(set(value.selected)) != len(value.selected)
                or any(path not in sources for path in value.selected)):
            return 'Facade selected paths do not match captured sources'
        edges = set()
        for edge in value.edges:
            if (type(edge) is not graph.ImportEdge or edge.importer not in sources
                    or edge.target not in sources
                    or graph.normalize_relative(edge.importer, edge.request) != edge.target
                    or (edge.importer, edge.request) in edges):
                return 'Invalid or ambiguous captured facade dependency edge'
            edges.add((edge.importer, edge.request))
        for unavailable in value.unavailable:
            if (type(unavailable) is not graph.ImportUnavailable or unavailable.path not in sources
                    or type(unavailable.request) is not str or type(unavailable.reason) is not str):
                return 'Invalid captured facade unavailability evidence'
    except (graph.ImportGraphError, TypeError, ValueError, AttributeError):
        return 'Invalid captured facade graph identity'
    return None


class FacadeAnalyzer:
    """Per-analysis successful caches only; never shared across candidates."""
    def __init__(self, import_graph, packages):
        self.graph = import_graph
        self.reason = _graph_reason(import_graph)
        self.cache = {}
        self.certificates = {}
        self.packages = {}
        if type(packages) is not tuple:
            self.reason = self.reason or 'Facade packages must be an immutable tuple'
            return
        for package in packages:
            if (type(package) is not PackageSource or type(package.import_path) is not str
                    or type(package.source) is not bytes or type(package.source_identity) is not str
                    or not package.source_identity or type(package.artifacts) is not tuple
                    or package.import_path in self.packages):
                self.reason = self.reason or 'Invalid or duplicate facade package identity'
                return
            self.packages[package.import_path] = package

    def consumer_reason(self, path, source):
        if self.reason:
            return self.reason
        if type(path) is not str or path not in self.graph.selected:
            return 'Facade consumer path is not a selected captured source'
        item = self.graph.source_for(path)
        if type(source) is not bytes or item is None or item.source != source:
            return 'Facade consumer source differs from the captured snapshot'
        return None

    def _package(self, path, active):
        if path in active:
            return None, 'Cyclic facade package ancestry'
        if len(active) >= graph.MAX_GRAPH_DEPTH:
            return None, 'Facade proof depth limit exceeded at package leaf'
        if path not in self.certificates:
            result = certify_initializer(self.packages.get(path))
            if not result.accepted:
                return None, result.reason
            self.certificates[path] = result
        return self.certificates[path], None

    def analyze(self, path, active=()):
        item = self.graph.source_for(path) if self.reason is None else None
        sha = item.sha256 if item else ''
        def refusal(reason):
            return FacadeEvidence(path, sha, reason=reason)
        if self.reason:
            return refusal(self.reason)
        if path in active:
            return refusal('Cyclic relative facade import graph')
        if len(active) >= graph.MAX_GRAPH_DEPTH:
            return refusal('Facade proof depth limit exceeded')
        if path in self.cache:
            result = self.cache[path]
            if set(active) & ({dep.path for dep in result.dependencies}
                              | {origin.import_path for origin in result.package_origins}):
                return refusal('Cyclic relative facade import ancestry')
            if len(active) + result.depth > graph.MAX_GRAPH_DEPTH:
                return refusal('Facade proof depth limit exceeded')
            return result
        if item is None:
            return refusal('Facade source snapshot is unavailable')
        # Capture already attempted this exact immutable source and recorded a
        # source-level syntax/resource refusal. Do not repeat that parser work
        # in every resolver and convergence pass. This is evidence, not a new
        # negative cache or an ancestry-dependent refusal retained for reuse.
        if any(u.path == path and not u.request for u in self.graph.unavailable):
            return refusal('Captured facade syntax or import request is unavailable')
        try:
            tree = parse(item.source)
        except AdapterError:
            return refusal('Facade syntax is unavailable')
        if tree.nodes[0].errors:
            return refusal('Malformed facade source syntax')
        statements = _statements(tree)
        if statements is None:
            return refusal('Facade has output or unsupported initializer effects')
        env, dependencies, origins = {}, {}, {}
        depth = 1
        for index in statements:
            statement = tree.nodes[index]
            if statement.kind == 'ModuleImport':
                shape = graph.import_shape(tree, index)
                if shape is None or shape.items is None:
                    return refusal('Wildcard, namespace, dotted or dynamic facade import is unsupported')
                if shape.request.startswith('@'):
                    certificate, reason = self._package(shape.request, (*active, path))
                    if certificate is None:
                        return refusal(reason)
                    exports = dict(certificate.exports)
                    depth = max(depth, 2)
                else:
                    target = self.graph.target_for(path, shape.request)
                    if target is None or any(u.path == path and u.request == shape.request
                                             for u in self.graph.unavailable):
                        return refusal('Relative facade dependency is unavailable')
                    evidence = self.analyze(target, (*active, path))
                    if not evidence.accepted:
                        return refusal(evidence.reason)
                    exports = dict(evidence.exports)
                    dependencies[target] = CapturedIdentity(target, evidence.source_sha256)
                    dependencies.update((dep.path, dep) for dep in evidence.dependencies)
                    origins.update((origin, origin) for origin in evidence.package_origins)
                    depth = max(depth, 1 + evidence.depth)
                if any(name not in exports for name, _ in shape.items):
                    return refusal('Requested facade export is unavailable')
                if any(alias in env for _, alias in shape.items):
                    return refusal('Duplicate or shadowed facade export name')
                for name, alias in shape.items:
                    env[alias] = exports[name]
                    origin = exports[name].package_origin
                    if origin:
                        origins[origin] = origin
            elif statement.kind == 'LetBinding':
                parts = graph.significant(tree, index)
                if (len(parts) != 4 or tree.nodes[parts[0]].kind != 'Let'
                        or tree.nodes[parts[1]].kind != 'Ident' or tree.nodes[parts[2]].kind != 'Eq'):
                    return refusal('Only simple literal or prior-binding aliases are supported in facades')
                name = graph.raw(tree, parts[1]).decode('utf-8')
                if name in env:
                    return refusal('Duplicate or shadowed facade export name')
                value = parts[3]
                kind = tree.nodes[value].kind
                literal = (kind in graph._LITERALS
                    and (kind != 'Str' or graph.literal_string(tree, value) is not None)
                    and (kind != 'Int' or graph.raw(tree, value).isdigit()
                         and len(graph.raw(tree, value)) <= 18))
                if literal:
                    env[name] = FacadeExport(path, sha, name)
                elif kind == 'Ident' and graph.raw(tree, value).decode('utf-8') in env:
                    env[name] = env[graph.raw(tree, value).decode('utf-8')]
                else:
                    return refusal('Unproved facade initializer, call, closure, content or forward alias')
            else:
                return refusal('Include, style, mutation, execution or unsupported facade statement')
        result = FacadeEvidence(path, sha, tuple(sorted(env.items())),
            tuple(dependencies[k] for k in sorted(dependencies)),
            tuple(sorted(origins, key=lambda o: (o.import_path, o.export_name))), True, depth=depth)
        self.cache[path] = result
        return result
