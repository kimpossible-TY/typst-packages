"""Read-only, source-bound literal include sites and syntactic occurrences.

This is separate from import/export evidence and every protected input profile.
Reachability here is syntax, never execution, inherited style or write authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import PurePosixPath
import weakref

from package_loader import (PackageInputError, _Snapshot, _absolute_root,
                            _directory_signature, _file_signature)
from syntax_client import AdapterError, MAX_INPUT, Node, Tree, parse
from typst_lint import InputError, digest

MAX_CONTEXT_FILES = 32
MAX_CONTEXT_BYTES = 4 * 1024 * 1024
MAX_SOURCE_BYTES = MAX_INPUT
MAX_INCLUDE_NODES = 128
MAX_INCLUDE_DEPTH = 16
MAX_OCCURRENCES = 1024
MAX_CONTEXT_NODES = 200_000
# Explicit inventory-only headroom for book-sized include closures. All other
# resource/safety bounds remain identical to the standard capture budget.
BOOK_MAX_CONTEXT_FILES = 64
BOOK_MAX_CONTEXT_NODES = 400_000
MAX_CONTEXT_DIRECTORIES = 64
MAX_PATH_BYTES = 512
MAX_TOTAL_PATH_BYTES = 16 * 1024
MAX_ROOT_BYTES = 4096
MAX_ROOT_COMPONENTS = 128
MAX_REQUEST_EXPRESSION_BYTES = 4096
MAX_TOTAL_REQUEST_BYTES = 4 * 1024 * 1024
MAX_ANCESTRY_NODES = 32_768

_TRIVIA = frozenset(('Space', 'Parbreak', 'LineComment', 'BlockComment'))
_CONDITIONAL = frozenset(('Conditional', 'ForLoop', 'WhileLoop'))
_DEFERRED = frozenset(('Closure', 'Contextual', 'ShowRule'))
_CALLS = frozenset(('FuncCall', 'MathCall'))


class IncludeContextError(InputError):
    """Base class for a capture that did not produce a complete bounded value."""


class IncludeInputError(IncludeContextError):
    """Invalid explicit root/entrypoint inputs, before dependency discovery."""


class IncludeSyntaxError(IncludeContextError):
    """The pinned parser is unavailable or a captured source is malformed."""


class IncludeCaptureError(IncludeContextError):
    """Unsafe files, aliases, changed bytes/identities or failed safe reads."""


class IncludeLimitError(IncludeContextError):
    """A fixed capture, syntax, path or occurrence budget was exceeded."""


class _MissingInclude(Exception):
    """Absence observed before starting a particular descriptor read."""


@dataclass(frozen=True, slots=True)
class IncludeSourceIdentity:
    path: str
    source_sha256: str
    tree_sha256: str


@dataclass(frozen=True, slots=True)
class IncludeNodeIdentity:
    source: IncludeSourceIdentity
    node_id: int
    kind: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class IncludeSource:
    identity: IncludeSourceIdentity
    source: bytes = field(repr=False)
    tree: Tree = field(repr=False)
    fingerprint: tuple[int, ...]

    @property
    def path(self):
        return self.identity.path


@dataclass(frozen=True, slots=True)
class IncludeEdge:
    node: IncludeNodeIdentity
    literal: IncludeNodeIdentity | None
    request: str | None
    raw_request: bytes = field(repr=False)
    target: IncludeSourceIdentity | None
    ancestry: tuple[IncludeNodeIdentity, ...]
    content_ancestry: tuple[IncludeNodeIdentity, ...]
    call_ancestry: tuple[IncludeNodeIdentity, ...]
    unresolved_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IncludeOccurrence:
    entrypoint: IncludeSourceIdentity
    source: IncludeSourceIdentity
    edge_chain: tuple[IncludeNodeIdentity, ...]
    unresolved_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IncludeUnresolved:
    node: IncludeNodeIdentity
    codes: tuple[str, ...]
    entrypoint: IncludeSourceIdentity | None = None
    edge_chain: tuple[IncludeNodeIdentity, ...] = ()


@dataclass(frozen=True, slots=True, weakref_slot=True)
class IncludeContext:
    project_root: str
    entrypoints: tuple[IncludeSourceIdentity, ...]
    sources: tuple[IncludeSource, ...]
    edges: tuple[IncludeEdge, ...]
    occurrences: tuple[IncludeOccurrence, ...]
    unresolved: tuple[IncludeUnresolved, ...]
    budget: str = 'standard-v1'

    def source_for(self, path):
        return next((source for source in self.sources if source.path == path), None)


def _limit(condition, message):
    if not condition:
        raise IncludeLimitError(message)


def _capture_bounds(budget):
    if type(budget) is not str or budget not in ('standard-v1', 'book-v1'):
        raise IncludeInputError('Include budget must be exactly standard-v1 or book-v1')
    if budget == 'book-v1':
        return BOOK_MAX_CONTEXT_FILES, BOOK_MAX_CONTEXT_NODES
    return MAX_CONTEXT_FILES, MAX_CONTEXT_NODES


def _seed_path(path):
    if type(path) is not str:
        raise IncludeInputError('Entrypoints require string project-relative paths')
    _limit(len(path) <= MAX_PATH_BYTES, 'Include context path byte limit exceeded')
    if (not path or path.startswith('/') or '\\' in path
            or any(part in ('', '.', '..') for part in path.split('/'))
            or any(ord(char) < 32 or ord(char) == 127 for char in path)
            or not path.endswith('.typ')):
        raise IncludeInputError('Entrypoints require canonical project-relative Typst paths')
    try:
        size = len(path.encode('utf-8'))
    except UnicodeError as error:
        raise IncludeInputError('Entrypoint paths must be UTF-8') from error
    _limit(size <= MAX_PATH_BYTES, 'Include context path byte limit exceeded')
    return path


def _tree_sha256(tree):
    # The adapter client already validated the complete pinned protocol/tree.
    # Retain an identity for every field, not merely a node count or byte span.
    value = ('include-tree-v1', 'typst-syntax-0.15.1', 'adapter-0.2.0-schema-2',
             tree.mode, tuple((node.kind, node.start, node.end, node.children,
                 node.errors, node.operands, node.display) for node in tree.nodes))
    return digest(json.dumps(value, separators=(',', ':'),
                             ensure_ascii=True).encode('ascii'))


class _IncludeSnapshot(_Snapshot):
    """The existing no-follow reader with bounded, identity-checked dir reuse."""

    def __init__(self, root):
        super().__init__(root)
        self._directories_by_path = {}
        self._directory_names = {}
        self._project_directories = set()
        self._file_names = {}

    def __enter__(self):
        try:
            super().__enter__()
            # _Snapshot inserts '/' directly rather than calling directory().
            if str(self.root) not in self._project_directories:
                _limit(len(self._project_directories) < MAX_CONTEXT_DIRECTORIES,
                       'Include context directory limit exceeded')
                self._project_directories.add(str(self.root))
            return self
        except BaseException:
            self.stack.close()
            raise

    def directory(self, parent, name):
        path = parent.path / name
        key = str(path)
        cached = self._directories_by_path.get(key)
        if cached is not None:
            current = os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
            expected = _directory_signature(cached.before)
            if (expected != _directory_signature(current)
                    or expected != _directory_signature(os.fstat(cached.fd))):
                raise IncludeCaptureError(f'Include directory changed while reading: {path}')
            return cached
        if path.is_relative_to(self.root):
            _limit(len(self._project_directories) < MAX_CONTEXT_DIRECTORIES,
                   'Include context directory limit exceeded')
        directory = super().directory(parent, name)
        identity = directory.before.st_dev, directory.before.st_ino
        previous = self._directory_names.get(identity)
        if previous is not None and previous != key:
            raise IncludeCaptureError('Distinct include directory paths share one filesystem identity')
        self._directory_names[identity] = key
        self._directories_by_path[key] = directory
        if path.is_relative_to(self.root):
            self._project_directories.add(key)
        return directory

    def read(self, parent, name, limit):
        info = os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
        _limit(0 <= info.st_size <= limit, 'Include source byte limit exceeded')
        source = super().read(parent, name, limit)
        record = self.files[-1]
        identity = record.before.st_dev, record.before.st_ino
        previous = self._file_names.get(identity)
        key = str(record.path)
        if previous is not None and previous != key:
            raise IncludeCaptureError('Distinct include source paths share one filesystem identity')
        self._file_names[identity] = key
        return source


def _initial_presence(directory, name):
    try:
        os.stat(name, dir_fd=directory.fd, follow_symlinks=False)
    except FileNotFoundError as error:
        raise _MissingInclude from error


def _walk(snapshot, importer, request, *, missing_ok=False):
    """Verify original components before canceling any ``name/..`` traversal."""
    def directory(parent, name):
        if missing_ok:
            _initial_presence(parent, name)
        return snapshot.directory(parent, name)

    directories = [snapshot.directory_root]
    for part in PurePosixPath(importer).parent.parts:
        directories.append(directory(directories[-1], part))
    for part in request.split('/')[:-1]:
        if part in ('', '.'):
            continue
        if part == '..':
            if len(directories) == 1:
                raise IncludeCaptureError('Include traversal escapes the explicit project root')
            directories.pop()
        else:
            directories.append(directory(directories[-1], part))
    return directories[-1], request.split('/')[-1]


def _normalize_request(importer, request):
    if not request or '\\' in request or any(ord(c) < 32 or ord(c) == 127 for c in request):
        return None, 'invalid-request'
    _limit(len(request.encode('utf-8')) <= MAX_PATH_BYTES,
           'Include request path byte limit exceeded')
    if request.startswith('@'):
        return None, 'package-request'
    if request.startswith('/'):
        return None, 'absolute-request'
    parts = list(PurePosixPath(importer).parent.parts)
    for part in request.split('/'):
        if part in ('', '.'):
            continue
        if part == '..':
            if not parts:
                return None, 'root-escape'
            parts.pop()
        else:
            parts.append(part)
    if not parts or not parts[-1].endswith('.typ') or request.endswith(('/', '/.', '/..')):
        return None, 'non-typst-request'
    target = '/'.join(parts)
    _limit(len(target.encode('utf-8')) <= MAX_PATH_BYTES,
           'Include normalized path byte limit exceeded')
    return target, None


def _containment(ancestry):
    kinds = {node.kind for node in ancestry}
    codes = []
    if kinds & _CONDITIONAL:
        codes.append('conditional-containment')
    if kinds & _DEFERRED:
        codes.append('deferred-containment')
    # Even content blocks and familiar calls are opaque without a separate
    # source-derived certificate. Only root-direct Markup ancestry is unmarked.
    if not codes and any(node.node_id != 0 or node.kind != 'Markup' for node in ancestry):
        codes.append('unknown-containment')
    return tuple(codes)


def _occurrences(entrypoints, edges):
    outgoing = {}
    for edge in edges:
        outgoing.setdefault(edge.node.source.path, []).append(edge)
    result, unresolved = [], []
    slots = len(entrypoints)
    _limit(slots <= MAX_OCCURRENCES, 'Include occurrence limit exceeded')
    pending = [(entrypoint, entrypoint, (), (entrypoint.path,), (), False)
               for entrypoint in reversed(entrypoints)]
    while pending:
        entrypoint, source, chain, active, codes, cyclic = pending.pop()
        result.append(IncludeOccurrence(entrypoint, source, chain, codes))
        if cyclic:
            unresolved.append(IncludeUnresolved(chain[-1], ('include-cycle',), entrypoint, chain))
            continue
        for edge in reversed(outgoing.get(source.path, ())):
            if edge.target is None:
                continue
            child_chain = (*chain, edge.node)
            _limit(len(child_chain) <= MAX_INCLUDE_DEPTH, 'Include occurrence depth limit exceeded')
            slots += 1
            _limit(slots <= MAX_OCCURRENCES, 'Include occurrence limit exceeded')
            child_codes = tuple(dict.fromkeys((*codes, *edge.unresolved_codes)))
            cyclic = edge.target.path in active
            if cyclic:
                child_codes = (*child_codes, 'include-cycle')
            pending.append((entrypoint, edge.target, child_chain,
                (*active, edge.target.path), child_codes, cyclic))
    return tuple(result), tuple(unresolved)


_CODES = frozenset(('dynamic-request', 'escaped-request', 'package-request',
    'absolute-request', 'invalid-request', 'root-escape', 'non-typst-request',
    'missing-source', 'conditional-containment', 'deferred-containment',
    'unknown-containment', 'include-cycle'))
# The registry stores no strong reference to the issued object or mutable Tree.
# Its independent primitive signature attests original parser-produced facts,
# not current filesystem state or any protected authority.
_ISSUED = {}
# A derived in-memory context additionally depends on its original capture.
# Weak references ensure holding only the context cannot prolong that proof.
_DERIVED = {}


def _context_signature(context):
    """Bound altered objects before traversing/serializing their known fields."""
    def require(condition):
        if not condition:
            raise IncludeCaptureError('Issued include context representation changed')

    def string(value, limit):
        require(type(value) is str and len(value) <= limit)
        require(len(value.encode('utf-8')) <= limit)
        return value

    def integer(value, limit):
        require(type(value) is int and 0 <= value <= limit)
        return value

    def sequence(value, limit):
        require(type(value) is tuple and len(value) <= limit)
        return value

    def identity(value):
        require(type(value) is IncludeSourceIdentity)
        path = string(value.path, MAX_PATH_BYTES)
        for sha in (value.source_sha256, value.tree_sha256):
            require(type(sha) is str and len(sha) == 64
                    and all(c in '0123456789abcdef' for c in sha))
        return path, value.source_sha256, value.tree_sha256

    def node(value):
        require(type(value) is IncludeNodeIdentity)
        return (identity(value.source), integer(value.node_id, 100_000),
            string(value.kind, 80), integer(value.start, MAX_SOURCE_BYTES),
            integer(value.end, MAX_SOURCE_BYTES))

    def codes(value):
        sequence(value, len(_CODES))
        require(all(type(code) is str and len(code) <= 32 and code in _CODES for code in value))
        return value

    require(type(context) is IncludeContext)
    require(type(context.budget) is str and context.budget in ('standard-v1', 'book-v1'))
    max_files, max_nodes = _capture_bounds(context.budget)
    root = string(context.project_root, MAX_ROOT_BYTES)
    entrypoints = tuple(identity(value) for value in sequence(context.entrypoints, max_files))
    sources = []
    total_bytes = total_nodes = total_paths = 0
    for source in sequence(context.sources, max_files):
        require(type(source) is IncludeSource and type(source.source) is bytes)
        require(len(source.source) <= MAX_SOURCE_BYTES)
        total_bytes += len(source.source)
        require(total_bytes <= MAX_CONTEXT_BYTES)
        source_identity = identity(source.identity)
        total_paths += len(source_identity[0].encode('utf-8'))
        require(total_paths <= MAX_TOTAL_PATH_BYTES)
        tree = source.tree
        require(type(tree) is Tree and type(tree.source) is bytes
                and tree.source == source.source and type(tree.mode) is str
                and tree.mode == 'markup')
        sequence(tree.nodes, 100_000)
        total_nodes += len(tree.nodes)
        require(total_nodes <= max_nodes)
        child_count = 0
        for item in tree.nodes:
            if type(item) is not Node:
                raise IncludeCaptureError('Issued include context representation changed')
            kind = item.kind
            if type(kind) is not str or len(kind) > 80 or len(kind.encode('utf-8')) > 80:
                raise IncludeCaptureError('Issued include context representation changed')
            start = item.start
            start_limit = len(source.source)
            if type(start) is not int or not 0 <= start <= start_limit:
                raise IncludeCaptureError('Issued include context representation changed')
            end = item.end
            end_limit = len(source.source)
            if type(end) is not int or not 0 <= end <= end_limit:
                raise IncludeCaptureError('Issued include context representation changed')
            if not (type(item.errors) is bool and (item.display is None or type(item.display) is bool)):
                raise IncludeCaptureError('Issued include context representation changed')
            children = item.children
            children_limit = len(tree.nodes)
            if type(children) is not tuple or len(children) > children_limit:
                raise IncludeCaptureError('Issued include context representation changed')
            child_count += len(item.children)
            if child_count > len(tree.nodes):
                raise IncludeCaptureError('Issued include context representation changed')
            for child in item.children:
                child_limit = len(tree.nodes)
                if type(child) is not int or not 0 <= child <= child_limit:
                    raise IncludeCaptureError('Issued include context representation changed')
            operands = item.operands
            if type(operands) is not tuple or len(operands) > 2:
                raise IncludeCaptureError('Issued include context representation changed')
            for operand in operands:
                if (type(operand) is not tuple or len(operand) > 2 or len(operand) != 2
                        or type(operand[0]) is not str or operand[0] not in ('top', 'bottom')):
                    raise IncludeCaptureError('Issued include context representation changed')
                operand_node = operand[1]
                operand_limit = len(tree.nodes)
                if type(operand_node) is not int or not 0 <= operand_node <= operand_limit:
                    raise IncludeCaptureError('Issued include context representation changed')
        sequence(source.fingerprint, 9)
        require(len(source.fingerprint) == 9)
        for value in source.fingerprint:
            # Filesystem integers need not be unsigned (e.g. old timestamps).
            require(type(value) is int and -(1 << 128) < value < (1 << 128))
        sources.append((source_identity, digest(source.source),
                        _tree_sha256(tree), source.fingerprint))
    edges = []
    ancestry_count = request_bytes = 0
    for edge in sequence(context.edges, MAX_INCLUDE_NODES):
        require(type(edge) is IncludeEdge)
        if edge.request is not None:
            string(edge.request, MAX_PATH_BYTES)
        require(type(edge.raw_request) is bytes
                and len(edge.raw_request) <= MAX_REQUEST_EXPRESSION_BYTES)
        request_bytes += len(edge.raw_request)
        require(request_bytes <= MAX_TOTAL_REQUEST_BYTES)
        ancestry = tuple(node(value) for value in sequence(edge.ancestry, 256))
        ancestry_count += len(ancestry)
        require(ancestry_count <= MAX_ANCESTRY_NODES)
        edges.append((node(edge.node), node(edge.literal) if edge.literal is not None else None,
            edge.request, edge.raw_request.hex(), identity(edge.target) if edge.target is not None else None,
            ancestry, tuple(node(value) for value in sequence(edge.content_ancestry, 256)),
            tuple(node(value) for value in sequence(edge.call_ancestry, 256)), codes(edge.unresolved_codes)))
    occurrences = []
    for occurrence in sequence(context.occurrences, MAX_OCCURRENCES):
        require(type(occurrence) is IncludeOccurrence)
        occurrences.append((identity(occurrence.entrypoint), identity(occurrence.source),
            tuple(node(value) for value in sequence(occurrence.edge_chain, MAX_INCLUDE_DEPTH)),
            codes(occurrence.unresolved_codes)))
    unresolved = []
    for value in sequence(context.unresolved, MAX_INCLUDE_NODES + MAX_OCCURRENCES):
        require(type(value) is IncludeUnresolved)
        unresolved.append((node(value.node), codes(value.codes),
            identity(value.entrypoint) if value.entrypoint is not None else None,
            tuple(node(item) for item in sequence(value.edge_chain, MAX_INCLUDE_DEPTH))))
    return json.dumps((context.budget, root, entrypoints, sources, edges, occurrences, unresolved),
                      separators=(',', ':'), ensure_ascii=True).encode('ascii')


def _issue(context):
    signature = _context_signature(context)
    key = id(context)
    def expired(reference):
        retained = _ISSUED.get(key)
        if retained is not None and retained[0] is reference:
            _ISSUED.pop(key, None)
    reference = weakref.ref(context, expired)
    _ISSUED[key] = reference, signature
    return context


def assert_include_context(context):
    """Pure original-parser-facts check, with no parser or filesystem access.

    Only the identical locally issued object is accepted. Independently retained
    primitive facts reject altered nested Trees/Nodes/records. Copies, serialized
    reports and constructor-built values cannot establish parser authenticity.
    This does not revalidate live paths or confer analysis/repair/compile rights.
    """
    retained = _ISSUED.get(id(context))
    if retained is None or retained[0]() is not context:
        raise IncludeCaptureError('Include context was not issued by this capture module')
    try:
        if _context_signature(context) != retained[1]:
            raise IncludeCaptureError('Issued include context facts changed')
    except IncludeContextError:
        raise
    except (AttributeError, TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise IncludeCaptureError('Issued include context representation changed') from error
    derived = _DERIVED.get(id(context))
    if derived is not None:
        if derived[0]() is not context or derived[1]() is None:
            raise IncludeCaptureError('Derived include context backing has expired')
        import document_intermediate_context
        document_intermediate_context._assert_backing(derived[1](), context)
    return context


def _bind_derived(context, owner):
    """Tie a newly derived context to its independently issued owner."""
    key = id(context)
    def expired(reference):
        retained = _DERIVED.get(key)
        if retained is not None and retained[0] is reference:
            _DERIVED.pop(key, None)
    _DERIVED[key] = weakref.ref(context, expired), weakref.ref(owner)


def load_include_context(project_root, entrypoint_sources, *, budget='standard-v1'):
    """Capture explicit entrypoint bytes and their literal include-only closure.

    The fixed book-v1 budget permits 64 sources / 400,000 aggregate nodes for
    explicit book inventory. The default standard-v1 stays at 32 / 200,000.
    Neither choice changes downstream analysis budgets or establishes execution.

    Every safe literal site is followed as syntax, including opaque containment.
    Unsupported requests/missing targets remain explicit edge records. Unsafe
    reads, stale entrypoint bytes, malformed syntax and exhausted budgets reject
    the whole capture; no truncated context is returned. This value is not a live
    transaction receipt, editor lease, certificate, repair or compiler input.
    """
    try:
        max_files, max_nodes = _capture_bounds(budget)
        try:
            raw_root = os.fspath(project_root)
            if type(raw_root) is not str or not raw_root:
                raise IncludeInputError('Include project root must be a nonempty string path')
            _limit(len(raw_root) <= MAX_ROOT_BYTES, 'Include project root path limit exceeded')
            root = _absolute_root(project_root)
        except IncludeContextError:
            raise
        except (TypeError, ValueError, PackageInputError) as error:
            raise IncludeInputError(f'Invalid include project root: {error}') from error
        if raw_root != str(root):
            raise IncludeInputError('Include context requires a canonical absolute project root')
        _limit(len(str(root).encode('utf-8')) <= MAX_ROOT_BYTES
               and len(root.parts) <= MAX_ROOT_COMPONENTS,
               'Include project root path limit exceeded')
        if isinstance(entrypoint_sources, (str, bytes)):
            raise IncludeInputError('Include entrypoints require an iterable of path/bytes pairs')
        try:
            iterator = iter(entrypoint_sources)
        except TypeError as error:
            raise IncludeInputError('Include entrypoints must be iterable') from error
        expected = {}
        for count, row in enumerate(iterator, 1):
            _limit(count <= max_files, 'Include context file limit exceeded')
            if type(row) is not tuple or len(row) != 2:
                raise IncludeInputError('Include entrypoints require immutable path/bytes pairs')
            path, source = row
            _seed_path(path)
            if type(source) is not bytes or path in expected:
                raise IncludeInputError('Invalid, duplicate or mutable include entrypoint')
            _limit(len(source) <= MAX_SOURCE_BYTES, 'Include source byte limit exceeded')
            try:
                source.decode('utf-8')
            except UnicodeError as error:
                raise IncludeInputError('Include entrypoint is not UTF-8') from error
            expected[path] = source
        if not expected:
            raise IncludeInputError('At least one explicit include entrypoint is required')
        sources, records, edges = {}, {}, []
        source_bytes = syntax_nodes = path_bytes = request_bytes = ancestry_nodes = 0
        with _IncludeSnapshot(root) as snapshot:
            def read_source(directory, basename, path):
                nonlocal source_bytes, syntax_nodes, path_bytes
                _limit(len(sources) < max_files, 'Include context file limit exceeded')
                path_bytes += len(path.encode('utf-8'))
                _limit(path_bytes <= MAX_TOTAL_PATH_BYTES, 'Include total path byte limit exceeded')
                source = snapshot.read(directory, basename, MAX_SOURCE_BYTES)
                source_bytes += len(source)
                _limit(source_bytes <= MAX_CONTEXT_BYTES, 'Include aggregate source byte limit exceeded')
                if path in expected and source != expected[path]:
                    raise IncludeCaptureError(f'Entrypoint source changed before include capture: {path}')
                try:
                    tree = parse(source)
                except AdapterError as error:
                    raise IncludeSyntaxError(f'Include syntax unavailable for {path}: {error}') from error
                if tree.nodes[0].errors:
                    raise IncludeSyntaxError(f'Malformed include source syntax: {path}')
                syntax_nodes += len(tree.nodes)
                _limit(syntax_nodes <= max_nodes, 'Include aggregate syntax node limit exceeded')
                identity = IncludeSourceIdentity(path, digest(source), _tree_sha256(tree))
                record = snapshot.files[-1]
                sources[path] = IncludeSource(identity, source, tree, _file_signature(record.before))
                records[path] = record
                return sources[path]

            for path in sorted(expected):
                directory, basename = _walk(snapshot, 'root.typ', path)
                item = read_source(directory, basename, path)
                if item.source != expected[path]:
                    raise IncludeCaptureError(f'Entrypoint source changed before include capture: {path}')
            pending = sorted(expected)
            for path in pending:
                item = sources[path]
                tree = item.tree
                parents = {}
                for parent, node in enumerate(tree.nodes):
                    for child in node.children:
                        parents[child] = parent
                identities = {}

                def node_identity(index):
                    if index not in identities:
                        node = tree.nodes[index]
                        identities[index] = IncludeNodeIdentity(item.identity, index,
                            node.kind, node.start, node.end)
                    return identities[index]

                for index, node in enumerate(tree.nodes):
                    if node.kind != 'ModuleInclude':
                        continue
                    _limit(len(edges) < MAX_INCLUDE_NODES, 'Include node limit exceeded')
                    ancestors = []
                    parent = parents.get(index)
                    while parent is not None:
                        ancestors.append(node_identity(parent))
                        parent = parents.get(parent)
                    ancestry = tuple(reversed(ancestors))
                    ancestry_nodes += len(ancestry)
                    _limit(ancestry_nodes <= MAX_ANCESTRY_NODES, 'Include ancestry node limit exceeded')
                    codes = list(_containment(ancestry))
                    parts = tuple(child for child in node.children if tree.nodes[child].kind not in _TRIVIA)
                    operand = parts[1] if (len(parts) == 2 and tree.nodes[parts[0]].kind == 'Include') else None
                    literal = (node_identity(operand) if operand is not None
                               and tree.nodes[operand].kind == 'Str' else None)
                    raw_request = (item.source[tree.nodes[operand].start:tree.nodes[operand].end]
                                   if operand is not None else b'')
                    _limit(len(raw_request) <= MAX_REQUEST_EXPRESSION_BYTES,
                           'Include request expression byte limit exceeded')
                    request_bytes += len(raw_request)
                    _limit(request_bytes <= MAX_TOTAL_REQUEST_BYTES, 'Include total request byte limit exceeded')
                    request, target = None, None
                    if literal is None:
                        codes.append('dynamic-request')
                    elif b'\\' in raw_request:
                        codes.append('escaped-request')
                    elif len(raw_request) < 2 or raw_request[:1] != b'"' or raw_request[-1:] != b'"':
                        codes.append('invalid-request')
                    else:
                        request = raw_request[1:-1].decode('utf-8')
                        target_path, code = _normalize_request(path, request)
                        if code:
                            codes.append(code)
                        else:
                            try:
                                # Always walk this original request, even if a
                                # different spelling already captured the target.
                                directory, basename = _walk(snapshot, path, request, missing_ok=True)
                                _initial_presence(directory, basename)
                                if target_path not in sources:
                                    read_source(directory, basename, target_path)
                                    pending.append(target_path)
                                else:
                                    snapshot._verify_file(records[target_path])
                                target = sources[target_path].identity
                            except _MissingInclude:
                                codes.append('missing-source')
                    edges.append(IncludeEdge(node_identity(index), literal, request, raw_request,
                        target, ancestry,
                        tuple(parent for parent in ancestry if parent.kind == 'ContentBlock'),
                        tuple(parent for parent in ancestry if parent.kind in _CALLS), tuple(codes)))
            ordered_edges = tuple(sorted(edges, key=lambda edge: (edge.node.source.path, edge.node.node_id)))
            entrypoints = tuple(sources[path].identity for path in sorted(expected))
            occurrences, cycle_records = _occurrences(entrypoints, ordered_edges)
            snapshot.verify()
        unresolved = tuple(IncludeUnresolved(edge.node, edge.unresolved_codes)
                           for edge in ordered_edges if edge.unresolved_codes)
        return _issue(IncludeContext(str(root), entrypoints,
            tuple(sources[path] for path in sorted(sources)), ordered_edges,
            occurrences, (*unresolved, *cycle_records), budget))
    except IncludeContextError:
        raise
    except (PackageInputError, OSError, TypeError, ValueError, UnicodeError) as error:
        raise IncludeCaptureError(f'Cannot safely capture include context: {error}') from error
