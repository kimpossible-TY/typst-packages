"""Bounded, immutable evidence for explicitly referenced relative imports.

Graph inputs are read-only context, never additions to the writable selection.
No packages, include files, ambient caches, evaluator, or directory crawl are
used. Only the small pure module grammar below establishes export identity.
"""
from collections import Counter
from dataclasses import dataclass, field, replace
import os
from pathlib import PurePosixPath

from package_loader import PackageInputError, _Snapshot, _absolute_root
from syntax_client import AdapterError, MAX_INPUT, parse
from typst_lint import InputError, digest

MAX_GRAPH_FILES = 512
MAX_GRAPH_BYTES = 16 * 1024 * 1024
MAX_GRAPH_EDGES = 4096
MAX_GRAPH_DEPTH = 32
MAX_PATH_BYTES = 4096
_TRIVIA = frozenset(('Space', 'Parbreak', 'LineComment', 'BlockComment'))
_LITERALS = frozenset(('Str', 'Int', 'Bool', 'None', 'Auto'))


class ImportGraphError(InputError):
    pass


class _MissingImport(Exception):
    """Absence observed before beginning a dependency descriptor read."""


@dataclass(frozen=True)
class ImportSource:
    path: str
    source: bytes = field(repr=False)

    @property
    def sha256(self):
        return digest(self.source)


@dataclass(frozen=True)
class ImportEdge:
    importer: str
    request: str
    target: str


@dataclass(frozen=True)
class ImportUnavailable:
    path: str
    request: str
    reason: str


@dataclass(frozen=True)
class ImportGraph:
    project_root: str
    sources: tuple[ImportSource, ...]
    selected: tuple[str, ...]
    edges: tuple[ImportEdge, ...] = ()
    unavailable: tuple[ImportUnavailable, ...] = ()

    def source_for(self, path):
        return next((item for item in self.sources if item.path == path), None)

    def target_for(self, path, request):
        return next((edge.target for edge in self.edges
                     if edge.importer == path and edge.request == request), None)


@dataclass(frozen=True)
class ImportShape:
    node_id: int
    request: str
    # None means wildcard. Module aliases and dotted import items are outside
    # this first export proof, even though the loader captures their requests.
    items: tuple[tuple[str, str], ...] | None


@dataclass(frozen=True)
class ExportIdentity:
    path: str
    source_sha256: str
    name: str


@dataclass(frozen=True)
class ModuleEvidence:
    path: str
    source_sha256: str
    exports: tuple[tuple[str, ExportIdentity], ...] = ()
    dependencies: frozenset[str] = frozenset()
    reexports: frozenset[str] = frozenset()
    pure: bool = False
    reason: str | None = None
    depth: int = 1


def significant(tree, index):
    return tuple(i for i in tree.nodes[index].children if tree.nodes[i].kind not in _TRIVIA)


def raw(tree, index):
    node = tree.nodes[index]
    return tree.source[node.start:node.end]


def literal_string(tree, index):
    if tree.nodes[index].kind != 'Str':
        return None
    value = raw(tree, index)
    if len(value) < 2 or value[:1] != b'"' or value[-1:] != b'"':
        return None
    value = value[1:-1]
    if b'\\' in value or any(c < 32 or c == 127 for c in value):
        return None
    return value.decode('utf-8')


def import_request(tree, index):
    parts = significant(tree, index)
    return (literal_string(tree, parts[1]) if tree.nodes[index].kind == 'ModuleImport'
            and len(parts) >= 2 and tree.nodes[parts[0]].kind == 'Import' else None)


def import_shape(tree, index):
    request = import_request(tree, index)
    parts = significant(tree, index)
    if (request is None or len(parts) != 4 or tree.nodes[parts[2]].kind != 'Colon'):
        return None
    if tree.nodes[parts[3]].kind == 'Star':
        return ImportShape(index, request, None)
    if tree.nodes[parts[3]].kind != 'ImportItems':
        return None
    items = []
    for item in significant(tree, parts[3]):
        if tree.nodes[item].kind == 'Comma':
            continue
        name, alias = None, None
        if tree.nodes[item].kind == 'RenamedImportItem':
            values = significant(tree, item)
            if (len(values) != 3 or tree.nodes[values[1]].kind != 'As'
                    or tree.nodes[values[2]].kind != 'Ident'):
                return None
            item = values[0]
            alias = raw(tree, values[2]).decode('utf-8')
        values = significant(tree, item)
        if (tree.nodes[item].kind != 'ImportItemPath' or len(values) != 1
                or tree.nodes[values[0]].kind != 'Ident'):
            return None
        name = raw(tree, values[0]).decode('utf-8')
        items.append((name, alias or name))
    if not items or len({alias for _, alias in items}) != len(items):
        return None
    return ImportShape(index, request, tuple(items))


def normalize_relative(importer, request):
    """Lexical normalization only; loader separately verifies original traversal."""
    if (type(request) is not str or not request or request.startswith(('/', '@'))
            or '\\' in request or any(ord(c) < 32 or ord(c) == 127 for c in request)
            or len(request.encode('utf-8')) > MAX_PATH_BYTES):
        raise ImportGraphError('Only bounded relative literal import paths are supported')
    parts = list(PurePosixPath(importer).parent.parts)
    for part in request.split('/'):
        if part in ('', '.'):
            continue
        if part == '..':
            if not parts:
                raise ImportGraphError('Relative import escapes the explicit project root')
            parts.pop()
        else:
            parts.append(part)
    if not parts or not parts[-1].endswith('.typ') or request.endswith(('/', '/.', '/..')):
        raise ImportGraphError('Relative imports must target a Typst file')
    return '/'.join(parts)


def _seed_path(path):
    if (type(path) is not str or not path or path.startswith('/') or '\\' in path
            or any(part in ('', '.', '..') for part in path.split('/'))
            or any(ord(c) < 32 or ord(c) == 127 for c in path)
            or len(path.encode('utf-8')) > MAX_PATH_BYTES or not path.endswith('.typ')):
        raise ImportGraphError('Selected graph inputs require canonical project-relative Typst paths')
    return path


def _walk(snapshot, importer, request, *, missing_ok=False):
    # Do not discard a/.. before verifying a. Otherwise a symlink (or nonexistent
    # directory) would acquire an identity different from Typst's actual path.
    def directory(parent, name):
        if missing_ok:
            _initial_presence(parent, name)
        return snapshot.directory(parent, name)

    directories = [snapshot.directory_root]
    for part in PurePosixPath(importer).parent.parts:
        directories.append(directory(directories[-1], part))
    parts = request.split('/')
    for part in parts[:-1]:
        if part in ('', '.'):
            continue
        if part == '..':
            if len(directories) == 1:
                raise ImportGraphError('Relative import escapes the explicit project root')
            directories.pop()
        else:
            directories.append(directory(directories[-1], part))
    return directories[-1], parts[-1]


def _initial_presence(directory, name):
    try:
        os.stat(name, dir_fd=directory.fd, follow_symlinks=False)
    except FileNotFoundError as error:
        raise _MissingImport from error


def load_import_graph(project_root, selected_sources):
    """Capture (relative path, bytes) inputs and their explicit import closure.

    Seed bytes must come from the caller's safe selected-source capture. They
    are verified again in the same retained-descriptor transaction as graph
    dependencies. Missing/unsupported edges become explicit refusals; races,
    unsafe files/directories and resource exhaustion reject the whole capture.
    """
    try:
        root = _absolute_root(project_root)
        expected = {}
        for count, (path, source) in enumerate(selected_sources, 1):
            if count > MAX_GRAPH_FILES:
                raise ImportGraphError('Import graph file limit exceeded')
            _seed_path(path)
            if type(source) is not bytes or path in expected or len(source) > MAX_INPUT:
                raise ImportGraphError('Invalid, duplicate, mutable or oversized graph seed')
            expected[path] = source
        sources, edges, unavailable = {}, [], []
        pending = [(path, 0) for path in expected]
        scheduled = set(expected)
        total = 0
        with _Snapshot(root) as snapshot:
            def read(directory, basename, path):
                nonlocal total
                source = snapshot.read(directory, basename, MAX_INPUT)
                total += len(source)
                if total > MAX_GRAPH_BYTES:
                    raise ImportGraphError('Import graph aggregate byte limit exceeded')
                sources[path] = ImportSource(path, source)
                return source

            for path in expected:
                directory, basename = _walk(snapshot, 'root.typ', path)
                source = read(directory, basename, path)
                if source != expected[path]:
                    raise ImportGraphError(f'Selected source changed before graph capture: {path}')
            for path, depth in pending:
                item = sources.get(path)
                if item is None:
                    continue
                try:
                    tree = parse(item.source)
                except AdapterError as error:
                    unavailable.append(ImportUnavailable(path, '', str(error)))
                    continue
                if tree.nodes[0].errors:
                    unavailable.append(ImportUnavailable(path, '', 'Malformed dependency syntax'))
                    continue
                requests = tuple(dict.fromkeys(import_request(tree, i)
                    for i, node in enumerate(tree.nodes) if node.kind == 'ModuleImport'))
                for request in requests:
                    if len(edges) + len(unavailable) >= MAX_GRAPH_EDGES:
                        raise ImportGraphError('Import graph edge limit exceeded')
                    if request is None:
                        unavailable.append(ImportUnavailable(path, '', 'Dynamic or escaped import is unsupported'))
                        continue
                    if request.startswith('@'):
                        unavailable.append(ImportUnavailable(path, request,
                            'Package evaluation and package-relative dependency graphs are unsupported'))
                        continue
                    try:
                        target = normalize_relative(path, request)
                    except ImportGraphError as error:
                        unavailable.append(ImportUnavailable(path, request, str(error)))
                        continue
                    if depth >= MAX_GRAPH_DEPTH:
                        unavailable.append(ImportUnavailable(path, request, 'Import graph depth limit exceeded'))
                        continue
                    try:
                        directory, basename = _walk(snapshot, path, request, missing_ok=True)
                        _initial_presence(directory, basename)
                        if target not in sources:
                            if len(sources) >= MAX_GRAPH_FILES:
                                raise ImportGraphError('Import graph file limit exceeded')
                            read(directory, basename, target)
                    except _MissingImport:
                        unavailable.append(ImportUnavailable(path, request, 'Explicitly imported source is missing'))
                        continue
                    edges.append(ImportEdge(path, request, target))
                    if target not in scheduled:
                        scheduled.add(target)
                        pending.append((target, depth + 1))
            snapshot.verify()
        return ImportGraph(str(root), tuple(sources[k] for k in sorted(sources)), tuple(sorted(expected)),
                           tuple(edges), tuple(unavailable))
    except (PackageInputError, OSError, TypeError, ValueError) as error:
        if isinstance(error, ImportGraphError):
            raise
        raise ImportGraphError(f'Cannot safely capture relative import graph: {error}') from error


def rebase_graph(graph, path, baseline, candidate):
    """Derive candidate-only context without rereading or broadening dependencies.

    Return a stale context unchanged so analysis refuses it, never rescue wrong
    path/hash evidence. A valid candidate may reorder/remove existing imports,
    but cannot add/change imports using unverified traversal evidence.
    """
    if type(graph) is not ImportGraph or type(baseline) is not bytes or type(candidate) is not bytes:
        return graph
    item = graph.source_for(path)
    if item is None or item.source != baseline or path not in graph.selected:
        return graph
    def imports(source):
        tree = parse(source)
        return Counter(raw(tree, i) for i, n in enumerate(tree.nodes) if n.kind == 'ModuleImport')
    if imports(candidate) - imports(baseline):
        return graph
    return replace(graph, sources=tuple(replace(item, source=candidate) if item.path == path else item
                                        for item in graph.sources))


def imported_bindings(shape, module):
    if shape is None or not module.pure:
        return None
    exports = dict(module.exports)
    if shape.items is None:
        return exports
    if any(name not in exports for name, _ in shape.items):
        return None
    return {alias: exports[name] for name, alias in shape.items}


class GraphAnalyzer:
    """Source-derived pure module facts; this object has only per-analysis cache."""
    def __init__(self, graph):
        self.graph = graph
        self.cache = {}

    def module(self, path, active=()):
        item = self.graph.source_for(path)
        sha = item.sha256 if item else ''
        def refusal(reason):
            return ModuleEvidence(path, sha, reason=reason)
        if path in active:
            return refusal('Cyclic relative import graph')
        if len(active) >= MAX_GRAPH_DEPTH:
            return refusal('Import proof depth limit exceeded')
        if path in self.cache:
            result = self.cache[path]
            return (refusal('Import proof depth limit exceeded')
                    if len(active) + result.depth > MAX_GRAPH_DEPTH else result)
        if item is None:
            return refusal('Imported source snapshot is unavailable')
        tree = parse(item.source)
        if tree.nodes[0].errors:
            return refusal('Malformed imported source syntax')
        env, dependencies, reexports = {}, set(), set()
        proof_depth = 1
        siblings = tree.nodes[0].children
        position = 0
        reason = None
        while position < len(siblings):
            index = siblings[position]
            node = tree.nodes[index]
            position += 1
            if node.kind in _TRIVIA:
                continue
            if node.kind != 'Hash' or position == len(siblings):
                reason = 'Module has output or unsupported initializer effects'
                break
            index = siblings[position]
            position += 1
            statement = tree.nodes[index]
            if node.end != statement.start:
                reason = 'Ambiguous module statement boundary'
                break
            if statement.kind == 'ModuleImport':
                shape = import_shape(tree, index)
                target = self.graph.target_for(path, shape.request) if shape else None
                if target is None:
                    reason = 'Unknown, dynamic, namespace or unavailable import'
                    break
                evidence = self.module(target, (*active, path))
                incoming = imported_bindings(shape, evidence)
                if incoming is None:
                    reason = evidence.reason or 'Requested import export is unavailable'
                    break
                if set(incoming) & set(env):
                    reason = 'Duplicate or shadowed module export name'
                    break
                env.update(incoming)
                reexports.update(incoming)
                dependencies.update((target, *evidence.dependencies))
                proof_depth = max(proof_depth, 1 + evidence.depth)
            elif statement.kind == 'LetBinding':
                parts = significant(tree, index)
                if (len(parts) != 4 or tree.nodes[parts[0]].kind != 'Let'
                        or tree.nodes[parts[1]].kind != 'Ident' or tree.nodes[parts[2]].kind != 'Eq'):
                    reason = 'Only simple literal or prior-export alias bindings are pure in this contract'
                    break
                name = raw(tree, parts[1]).decode('utf-8')
                value = parts[3]
                if name in env:
                    reason = 'Duplicate or shadowed module export name'
                    break
                kind = tree.nodes[value].kind
                literal = (kind in _LITERALS
                           and (kind != 'Str' or literal_string(tree, value) is not None)
                           and (kind != 'Int' or raw(tree, value).isdigit()
                                and len(raw(tree, value)) <= 18))
                if literal:
                    env[name] = ExportIdentity(path, sha, name)
                elif tree.nodes[value].kind == 'Ident' and raw(tree, value).decode('utf-8') in env:
                    original = raw(tree, value).decode('utf-8')
                    env[name] = env[original]
                    if original in reexports:
                        reexports.add(name)
                else:
                    reason = 'Unproved initializer, call, closure, content or dynamic alias'
                    break
            else:
                reason = 'Include, style, mutation, execution or unsupported module statement'
                break
        result = refusal(reason) if reason else ModuleEvidence(path, sha, tuple(sorted(env.items())),
            frozenset(dependencies), frozenset(reexports), True, depth=proof_depth)
        # Depth/cycle refusals may depend on traversal ancestry; caching only
        # successful proofs avoids allowing a shallower cache to bypass a cycle.
        if result.pure:
            self.cache[path] = result
        return result
