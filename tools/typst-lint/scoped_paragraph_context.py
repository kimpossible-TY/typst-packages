"""Pure, conditional occurrence facts for a bounded scoped paragraph grammar.

This module is deliberately not a resolver, writer, profile or completeness
certificate. No parser, filesystem, cache, evaluator or compiler is called.
Retained Trees and source bytes are checked independently before consumption.
"""
from __future__ import annotations

from dataclasses import dataclass
from bisect import bisect_left
import hashlib
import json
import re
import include_context as include_capture

from syntax_client import Tree, Node
from capitalization import WORDS
from static_resolver import (TEXT_UTILS, TEXT_UTILS_SOURCE_SHA256,
    SCOPED_ANNOTATIONS, SCOPED_ANNOTATIONS_SOURCE_SHA256,
    MANNOT_NAMESPACE, MANNOT_NAMESPACE_SOURCE_SHA256)

MAX_SOURCES = 128
MAX_OCCURRENCES = 1024
MAX_EDGES = 4096
MAX_SOURCE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_NODES = 100_000
MAX_DEPTH = 128
MAX_PACKAGES = 16
MAX_FACTS = 100_000
MAX_PACKAGE_NODES = 200_000
MAX_ANALYZED_NODES = 1_000_000
MAX_ANALYZED_BYTES = 16 * 1024 * 1024
MAX_SHOW_CHAIN = 64
CAPITALIZATION = 'pde.paragraph-capitalization'
MARKER_GAP = 'pde.paragraph-marker-gap'
MARKER_TARGET = 'pde.paragraph-marker-target'
_MARKER = 'paragraph_tab'
_UNKNOWN_NAMESPACE = '\x00unknown-namespace'
_TRIVIA = frozenset(('Space', 'Parbreak'))
_WS = re.compile(rb'[ \t\r\n]*\Z')
_LINE = re.compile(rb'[ \t]*\r?\n[ \t]*\Z')
_IDENT = re.compile(rb'[A-Za-z_][A-Za-z0-9_-]*\Z')
_WORD = re.compile(rb'[A-Za-z]+')
_EN = re.compile(r'en(?:-[A-Za-z0-9]{2,8})*\Z', re.I | re.ASCII)
_HASHES = {TEXT_UTILS: TEXT_UTILS_SOURCE_SHA256,
           SCOPED_ANNOTATIONS: SCOPED_ANNOTATIONS_SOURCE_SHA256,
           MANNOT_NAMESPACE: MANNOT_NAMESPACE_SOURCE_SHA256}


class ScopedParagraphContextEvidenceError(ValueError):
    """Retained evidence has an invalid shape, identity or relationship."""


EvidenceError = ScopedParagraphContextEvidenceError


def _require(ok, reason):
    if not ok:
        raise ScopedParagraphContextEvidenceError(reason)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class PackageTreeEvidence:
    import_path: str
    source_identity: str
    tree: Tree


@dataclass(frozen=True, slots=True)
class SourceReference:
    path: str
    source_sha256: str
    tree_sha256: str


@dataclass(frozen=True, slots=True)
class OccurrenceReference:
    occurrence_id: str
    entrypoint: str
    edge_chain: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NodeReference:
    source: SourceReference
    node: int
    span: tuple[int, int]


@dataclass(frozen=True, slots=True)
class BindingOrigin:
    occurrence: OccurrenceReference
    declaration: NodeReference
    local_name: str
    exported_name: str
    package: str
    package_source_identity: str | None
    package_source_sha256: str | None
    package_tree_sha256: str | None
    identity_status: str


@dataclass(frozen=True, slots=True)
class LanguageOrigin:
    declaration: NodeReference | None
    occurrence: OccurrenceReference | None
    value: str | None
    status: str


@dataclass(frozen=True, slots=True)
class ShowOrigin:
    declaration: NodeReference
    occurrence: OccurrenceReference
    installed_binding: BindingOrigin | None
    status: str


@dataclass(frozen=True, slots=True)
class ScopeReference:
    occurrence: OccurrenceReference
    source: SourceReference
    call_node: int
    call_span: tuple[int, int]
    callback_node: int
    callback_span: tuple[int, int]
    parameter: str


@dataclass(frozen=True, slots=True)
class InvocationFact:
    scope: ScopeReference
    binding: BindingOrigin | None
    prefix_mode: str
    literal_prefix: str | None
    namespace: str
    body_status: str
    execution_status: str
    initializer_status: str


@dataclass(frozen=True, slots=True)
class RenderContextFact:
    scope: ScopeReference
    language_origin: LanguageOrigin
    show_origin: ShowOrigin | None
    show_chain: tuple[ShowOrigin, ...]
    transparency: str
    conditions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParagraphRegion:
    scope: ScopeReference
    ordinal: int
    span: tuple[int, int]
    content_nodes: tuple[int, ...]
    first_word_span: tuple[int, int] | None
    first_word: bytes
    marker: NodeReference | None
    marker_hash_node: int | None
    marker_binding: BindingOrigin | None
    marker_gap_span: tuple[int, int] | None
    immediate_target: bool
    ownership_status: str
    boundary_status: str


@dataclass(frozen=True, slots=True)
class EffectFact:
    scope: ScopeReference
    ordinal: int
    kind: str
    phase: str
    status: str


@dataclass(frozen=True, slots=True)
class UnresolvedObligation:
    code: str
    reason: str
    occurrence: OccurrenceReference | None = None
    node: NodeReference | None = None
    scope: ScopeReference | None = None


@dataclass(frozen=True, slots=True)
class ConditionalDiagnosticProposal:
    rule_id: str
    paragraph: ParagraphRegion
    target_span: tuple[int, int]
    reason: str
    conditions: tuple[str, ...]
    status: str = 'conditional-informational'


@dataclass(frozen=True, slots=True)
class ScopedParagraphContextReport:
    sources: tuple[SourceReference, ...]
    occurrences: tuple[OccurrenceReference, ...]
    bindings: tuple[BindingOrigin, ...]
    invocations: tuple[InvocationFact, ...]
    contexts: tuple[RenderContextFact, ...]
    paragraphs: tuple[ParagraphRegion, ...]
    effects: tuple[EffectFact, ...]
    unresolved: tuple[UnresolvedObligation, ...]
    proposals: tuple[ConditionalDiagnosticProposal, ...]
    config_sha256: str | None = None
    catalog_records: tuple[tuple[str, str, str], ...] = ()


def _tree(tree):
    """Recheck complete immutable trees; no trusted validation flag is used."""
    _require(type(tree) is Tree and type(tree.source) is bytes
             and len(tree.source) <= MAX_SOURCE_BYTES and type(tree.mode) is str and tree.mode == 'markup'
             and type(tree.nodes) is tuple and 0 < len(tree.nodes) <= MAX_NODES,
             'Invalid immutable retained markup Tree')
    try:
        decoded = tree.source.decode('utf-8')
    except UnicodeError as error:
        raise EvidenceError('Retained Tree is not UTF-8') from error
    boundary, cursor = {0}, 0
    for char in decoded:
        cursor += len(char.encode('utf-8'))
        boundary.add(cursor)
    nodes = tree.nodes
    root = nodes[0]
    _require(type(root) is Node and type(root.kind) is str and root.kind == 'Markup'
             and type(root.start) is int and root.start == 0
             and type(root.end) is int and root.end == len(tree.source), 'Invalid complete Tree root')
    links = 0
    for n in nodes:
        _require(type(n) is Node and type(n.kind) is str and len(n.kind) < 80 and n.kind.isascii()
                 and n.kind.isidentifier()
                 and type(n.start) is int and type(n.end) is int
                 and n.start in boundary and n.end in boundary and n.start <= n.end
                 and type(n.children) is tuple and len(n.children) < len(nodes)
                 and all(type(c) is int and 0 <= c < len(nodes) for c in n.children)
                 and type(n.errors) is bool and type(n.operands) is tuple and len(n.operands) <= 2,
                 'Invalid retained Tree node/span/link')
        links += len(n.children)
        _require(links <= len(nodes) - 1, 'Retained Tree child-link budget exceeded')
        _require((type(n.display) is bool if n.kind == 'Equation' else n.display is None),
                 'Invalid retained Tree display flag')
        _require(all(type(p) is tuple and len(p) == 2 and type(p[0]) is str and p[0] in ('top', 'bottom')
                     and type(p[1]) is int and p[1] in n.children and n.kind == 'MathAttach'
                     for p in n.operands)
                 and len({r for r, _ in n.operands}) == len(n.operands),
                 'Invalid retained Tree operands')
    seen, stack = set(), [(0, 0)]
    while stack:
        i, depth = stack.pop()
        _require(i not in seen and depth <= MAX_DEPTH, 'Shared/cyclic/deep retained Tree')
        seen.add(i)
        n, cursor = nodes[i], nodes[i].start
        for c in n.children:
            child = nodes[c]
            _require(c > i and child.start == cursor and child.end <= n.end
                     and (not child.errors or n.errors), 'Retained Tree partition/error mismatch')
            cursor = child.end
            stack.append((c, depth + 1))
        _require(not n.children or cursor == n.end, 'Retained Tree uncovered bytes')
        _require(n.kind != 'Error' or n.errors, 'Unflagged retained Error node')
    _require(len(seen) == len(nodes), 'Unreachable retained Tree nodes')
    _require(not nodes[0].errors, 'Malformed retained Tree syntax')
    serial = tuple((n.kind, n.start, n.end, n.children, n.errors, n.operands, n.display) for n in nodes)
    value = ('include-tree-v1', 'typst-syntax-0.15.1', 'adapter-0.2.0-schema-2', tree.mode, serial)
    return _sha(json.dumps(value, separators=(',', ':'), ensure_ascii=True).encode('ascii'))


class _Syntax:
    def __init__(self, tree, ref):
        self.tree, self.ref, self.nodes, self.raw = tree, ref, tree.nodes, tree.source

    def text(self, i):
        n = self.nodes[i]
        return self.raw[n.start:n.end]

    def sig(self, i):
        return tuple(c for c in self.nodes[i].children if self.nodes[c].kind not in _TRIVIA)

    def kinds(self, ids):
        return tuple(self.nodes[i].kind for i in ids)

    def token(self, i, kind, raw):
        return self.nodes[i].kind == kind and not self.nodes[i].children and self.text(i) == raw

    def ident(self, i):
        if self.nodes[i].kind == 'Ident' and not self.nodes[i].children:
            value = self.text(i).decode('utf-8')
            if value and value.replace('-', '_').isidentifier():
                return value
        return None

    def string(self, i):
        if self.nodes[i].kind != 'Str' or self.nodes[i].children:
            return None
        raw = self.text(i)
        # No escape interpretation, interpolation, raw strings or synthetic value.
        if len(raw) >= 2 and raw[:1] == raw[-1:] == b'"' and not any(c in raw[1:-1] for c in (b'"', b'\\', b'\r', b'\n')):
            return raw[1:-1].decode('utf-8')
        return None

    def node_ref(self, i):
        n = self.nodes[i]
        return NodeReference(self.ref, i, (n.start, n.end))

    def trivia_valid(self, i):
        return self.nodes[i].kind in _TRIVIA and not self.nodes[i].children and bool(_WS.fullmatch(self.text(i)))

    def clean(self, i):
        pending = [i]
        while pending:
            n = self.nodes[pending.pop()]
            if n.errors or n.kind in ('LineComment', 'BlockComment'):
                return False
            for c in n.children:
                if self.nodes[c].kind in _TRIVIA and not self.trivia_valid(c):
                    return False
            pending.extend(n.children)
        return True

    def args(self, i):
        ids = self.sig(i)
        if self.nodes[i].kind != 'Args' or len(ids) < 2 or not self.token(ids[0], 'LeftParen', b'(') or not self.token(ids[-1], 'RightParen', b')'):
            return None
        result, expect = [], True
        for c in ids[1:-1]:
            if expect:
                if self.nodes[c].kind == 'Comma':
                    return None
                result.append(c)
            elif not self.token(c, 'Comma', b','):
                return None
            expect = not expect
        return tuple(result)  # A final comma is legal.

    def named(self, i):
        ids = self.sig(i)
        if self.nodes[i].kind == 'Named' and len(ids) == 3 and self.token(ids[1], 'Colon', b':'):
            name = self.ident(ids[0])
            return (name, ids[2]) if name else None
        return None


def _configuration(config, catalog):
    config_digest = None
    if config is not None:
        _require(type(config) is bytes and len(config) <= 65536, 'Config evidence must be bounded immutable bytes')
        try:
            config.decode('utf-8')
        except UnicodeError as error:
            raise EvidenceError('Config evidence is not UTF-8') from error
        config_digest = _sha(config)
    records = []
    if catalog is not None:
        _require(type(catalog) in (tuple, list) and len(catalog) <= 256, 'Invalid bounded explicit catalog')
        for record in catalog:
            _require(type(record) is dict and len(record) <= 64
                     and all(type(key) is str and len(key) <= 4096 for key in record),
                     'Catalog records need bounded exact-string keys')
            _require(all(type(record.get(k)) is str
                and 0 < len(record[k]) <= 4096 for k in ('id', 'grade', 'source')), 'Invalid catalog identity fields')
            records.append(tuple(record[k] for k in ('id', 'grade', 'source')))
        _require(len({r[0] for r in records}) == len(records), 'Duplicate catalog rule identity')
    return config_digest, tuple(records)


def _node_key(node):
    return node.source.path, node.node_id


def _edge_id(node):
    return _sha(json.dumps((node.source.path, node.source.source_sha256,
        node.source.tree_sha256, node.node_id, node.kind, node.start, node.end),
        separators=(',', ':')).encode())


def _occurrence_ref(occ):
    chain = tuple(_edge_id(n) for n in occ.edge_chain)
    identity = _sha(json.dumps((occ.entrypoint.path, occ.entrypoint.source_sha256,
        occ.entrypoint.tree_sha256, occ.source.path, occ.source.source_sha256,
        occ.source.tree_sha256, chain), separators=(',', ':')).encode())
    return OccurrenceReference(identity, occ.entrypoint.path, chain)


def _validate_capture(capture):
    _require(type(capture) is include_capture.IncludeContext, 'Expected an issued IncludeContext')
    # This is a pure capture-issuance assertion, not a live filesystem check or
    # package cache lookup. Issuance proves provenance, never execution/style.
    try:
        include_capture.assert_include_context(capture)
    except (AttributeError, ValueError, TypeError, include_capture.IncludeContextError) as error:
        raise EvidenceError(f'Invalid or unissued include capture: {error}') from error
    for name, bound in (('sources', MAX_SOURCES), ('entrypoints', MAX_SOURCES),
                        ('edges', MAX_EDGES), ('occurrences', MAX_OCCURRENCES),
                        ('unresolved', MAX_FACTS)):
        rows = getattr(capture, name)
        _require(type(rows) is tuple and len(rows) <= bound, 'Include capture tuple/budget mismatch')
    _require(bool(capture.sources) and bool(capture.entrypoints), 'Empty include capture')
    sources, refs, total, total_nodes = {}, {}, 0, 0
    for row in capture.sources:
        _require(type(row) is include_capture.IncludeSource
                 and type(row.identity) is include_capture.IncludeSourceIdentity
                 and type(row.identity.path) is str and row.identity.path
                 and not row.identity.path.startswith('/')
                 and not any(p in ('', '.', '..') for p in row.identity.path.split('/'))
                 and '\\' not in row.identity.path and len(row.identity.path.encode()) <= 512
                 and row.identity.path.endswith('.typ') and row.identity.path not in sources
                 and type(row.source) is bytes and row.source == row.tree.source,
                 'Invalid retained include source identity')
        tree_hash = _tree(row.tree)
        _require(row.identity.source_sha256 == _sha(row.source)
                 and row.identity.tree_sha256 == tree_hash, 'Retained source/Tree identity mismatch')
        total += len(row.source)
        total_nodes += len(row.tree.nodes)
        _require(total <= MAX_TOTAL_BYTES and total_nodes <= 200_000,
                 'Retained include aggregate byte/node budget exceeded')
        sources[row.path] = row
        refs[row.path] = SourceReference(row.path, _sha(row.source), tree_hash)
    _require(all(type(e) is include_capture.IncludeSourceIdentity
                 and e.path in sources and e == sources[e.path].identity for e in capture.entrypoints)
             and len(set(capture.entrypoints)) == len(capture.entrypoints), 'Invalid entrypoint identity')
    parents = {path: {c: p for p, n in enumerate(row.tree.nodes) for c in n.children}
               for path, row in sources.items()}

    def check_node(node):
        _require(type(node) is include_capture.IncludeNodeIdentity
                 and type(node.source) is include_capture.IncludeSourceIdentity
                 and node.source.path in sources and node.source == sources[node.source.path].identity
                 and type(node.node_id) is int and 0 <= node.node_id < len(sources[node.source.path].tree.nodes),
                 'Invalid include node source/index')
        actual = sources[node.source.path].tree.nodes[node.node_id]
        _require((node.kind, node.start, node.end) == (actual.kind, actual.start, actual.end),
                 'Include node span/kind mismatch')

    edges = {}
    for edge in capture.edges:
        _require(type(edge) is include_capture.IncludeEdge, 'Invalid include edge record')
        check_node(edge.node)
        key = _node_key(edge.node)
        _require(edge.node.kind == 'ModuleInclude' and key not in edges, 'Duplicate/noninclude edge')
        for name in ('ancestry', 'content_ancestry', 'call_ancestry', 'unresolved_codes'):
            _require(type(getattr(edge, name)) is tuple and len(getattr(edge, name)) <= MAX_NODES,
                     'Invalid edge ancestry/codes shape')
        for n in (*edge.ancestry, *edge.content_ancestry, *edge.call_ancestry):
            check_node(n)
        expected, parent = [], parents[key[0]].get(key[1])
        while parent is not None:
            expected.append(parent)
            parent = parents[key[0]].get(parent)
        _require(tuple(n.node_id for n in edge.ancestry) == tuple(reversed(expected))
                 and all(n.source == edge.node.source for n in edge.ancestry)
                 and edge.content_ancestry == tuple(n for n in edge.ancestry if n.kind == 'ContentBlock')
                 and edge.call_ancestry == tuple(n for n in edge.ancestry if n.kind in ('FuncCall', 'MathCall')),
                 'Include ancestry mismatch')
        syntax = _Syntax(sources[key[0]].tree, refs[key[0]])
        # Capture records dynamic and comment-containing expressions too. Their
        # retained raw operand remains checked, but they are never admitted here.
        ids = tuple(c for c in syntax.nodes[key[1]].children if syntax.nodes[c].kind not in
                    ('Space', 'Parbreak', 'LineComment', 'BlockComment'))
        operand = ids[1] if len(ids) == 2 and syntax.nodes[ids[0]].kind == 'Include' else None
        _require(type(edge.raw_request) is bytes and edge.raw_request ==
                 (syntax.text(operand) if operand is not None else b''), 'Include operand bytes mismatch')
        if edge.literal is not None:
            check_node(edge.literal)
            _require(operand == edge.literal.node_id and edge.literal.source == edge.node.source
                     and edge.literal.kind == 'Str', 'Include literal node mismatch')
        if edge.request is not None:
            _require(operand is not None and syntax.string(operand) == edge.request, 'Include literal value mismatch')
        if edge.target is not None:
            _require(type(edge.target) is include_capture.IncludeSourceIdentity
                     and edge.target.path in sources and edge.target == sources[edge.target.path].identity,
                     'Include target identity mismatch')
            # Pure path normalization only; never stat/resolve/open anything.
            parts = key[0].split('/')[:-1]
            _require(type(edge.request) is str, 'Target without literal request')
            for part in edge.request.split('/'):
                if part in ('', '.'):
                    continue
                if part == '..':
                    _require(bool(parts), 'Include path escapes root')
                    parts.pop()
                else:
                    parts.append(part)
            _require('/'.join(parts) == edge.target.path, 'Include target path mismatch')
        _require(all(type(c) is str and 0 < len(c) < 128 for c in edge.unresolved_codes),
                 'Invalid edge unresolved code')
        edges[key] = edge
    expected_sites = {(p, i) for p, row in sources.items() for i, n in enumerate(row.tree.nodes)
                      if n.kind == 'ModuleInclude'}
    _require(set(edges) == expected_sites, 'Include site coverage mismatch')
    outgoing = {p: [] for p in sources}
    for key in sorted(edges):
        outgoing[key[0]].append(edges[key])
    expected_occ, pending = [], [(e, e, (), (e.path,), (), False) for e in reversed(capture.entrypoints)]
    while pending:
        entry, source, chain, active, codes, cyclic = pending.pop()
        expected_occ.append((entry, source, chain, codes))
        _require(len(expected_occ) <= MAX_OCCURRENCES, 'Include occurrence expansion budget exceeded')
        if cyclic:
            continue
        for edge in reversed(outgoing[source.path]):
            if edge.target is None:
                continue
            child_chain = (*chain, edge.node)
            _require(len(child_chain) <= 16, 'Include occurrence depth exceeded')
            child_codes = tuple(dict.fromkeys((*codes, *edge.unresolved_codes)))
            cycle = edge.target.path in active
            if cycle:
                child_codes = (*child_codes, 'include-cycle')
            pending.append((entry, edge.target, child_chain, (*active, edge.target.path), child_codes, cycle))
        _require(len(pending) + len(expected_occ) <= MAX_OCCURRENCES, 'Include occurrence pending budget exceeded')
    _require(len(capture.occurrences) == len(expected_occ), 'Include occurrence count mismatch')
    for occ, expected in zip(capture.occurrences, expected_occ):
        _require(type(occ) is include_capture.IncludeOccurrence and
                 (occ.entrypoint, occ.source, occ.edge_chain, occ.unresolved_codes) == expected,
                 'Include occurrence chain/order mismatch')
    return sources, refs, edges


class _Analyzer:
    def __init__(self, capture, package_trees, config, catalog):
        self.capture = capture
        self.sources, self.refs, self.edges = _validate_capture(capture)
        self.config_digest, self.catalog = _configuration(config, catalog)
        _require(type(package_trees) is tuple and len(package_trees) <= MAX_PACKAGES,
                 'Package Trees require a bounded immutable tuple')
        self.packages = {}
        self.package_tree_hashes = {}
        self.package_source_hashes = {}
        package_bytes = package_nodes = 0
        for package in package_trees:
            _require(type(package) is PackageTreeEvidence and type(package.import_path) is str and 0 < len(package.import_path) <= 512
                     and type(package.source_identity) is str and 0 < len(package.source_identity) <= 4096
                     and package.import_path not in self.packages, 'Invalid/duplicate package Tree evidence')
            tree_hash = _tree(package.tree)
            self.package_tree_hashes[package.import_path] = tree_hash
            self.package_source_hashes[package.import_path] = _sha(package.tree.source)
            package_bytes += len(package.tree.source)
            package_nodes += len(package.tree.nodes)
            _require(package_bytes <= MAX_TOTAL_BYTES and package_nodes <= MAX_PACKAGE_NODES, 'Package Tree aggregate byte/node budget exceeded')
            self.packages[package.import_path] = package
        self.bindings, self.invocations, self.contexts = [], [], []
        self.paragraphs, self.effects, self.unresolved, self.proposals = [], [], [], []
        self.occ_refs = {_occurrence_ref(o): o for o in capture.occurrences}
        self.occ_by_chain = {(o.entrypoint.path, tuple(_node_key(n) for n in o.edge_chain)): o
                             for o in capture.occurrences}
        _require(sum(len(self.sources[o.source.path].tree.nodes) for o in capture.occurrences) <= MAX_ANALYZED_NODES,
                 'Scoped occurrence analysis work budget exceeded')
        _require(sum(len(self.sources[o.source.path].source) for o in capture.occurrences) <= MAX_ANALYZED_BYTES,
                 'Scoped occurrence analysis byte-work budget exceeded')
        self.visited = set()

    def unresolved_at(self, code, reason, occurrence=None, node=None, scope=None):
        self.unresolved.append(UnresolvedObligation(code, reason, occurrence, node, scope))
        _require(len(self.unresolved) <= MAX_FACTS, 'Unresolved fact budget exceeded')

    def package(self, path):
        supplied = self.packages.get(path)
        return supplied if supplied is not None and self.package_source_hashes[path] == _HASHES.get(path) else None

    def run(self):
        self.unresolved_at('initializer-coverage-unresolved',
            'Whole scoped initializer/dependency coverage is unproved: wildcard mannot, immediate defaults and tiptoe initialization remain obligations.')
        self.unresolved_at('runtime-package-selection-unresolved',
            'Supplied package bytes and Trees do not establish actual runtime package resolution or builtin identities.')
        self.unresolved_at('scope-collision-unresolved',
            'Graph membership, repeated occurrences/prefixes, auto versus literal prefixes and keyed-counter changes do not prove namespace uniqueness.')
        self.unresolved_at('selected-rule-gate-closed',
            'No selected-rule completeness or repair authority is derived. Root-only structural discharges do not apply.')
        self.unresolved_at('package-tree-provenance-conditional',
            'Package Trees are supplied structural evidence, not capture-issued parser provenance or whole initializer proof.')
        if self.config_digest:
            self.unresolved_at('config-not-render-language', 'Configuration policy is not evidence of actual rendered text.lang.')
        for edge in self.capture.edges:
            for code in edge.unresolved_codes:
                self.unresolved_at('include-' + code, 'Captured include obligation remains unresolved.',
                                   node=_Syntax(self.sources[edge.node.source.path].tree,
                                                self.refs[edge.node.source.path]).node_ref(edge.node.node_id))
        for entry in self.capture.entrypoints:
            occ = self.occ_by_chain[(entry.path, ())]
            self.visit_occurrence(occ, LanguageOrigin(None, None, None, 'missing'), (), False)
        for ref, occ in self.occ_refs.items():
            for code in occ.unresolved_codes:
                self.unresolved_at('occurrence-' + code, 'Syntactic occurrence has unproved execution/containment.', ref)
            if ref.occurrence_id not in self.visited:
                self.unresolved_at('occurrence-render-context-unresolved',
                    'Occurrence was captured syntactically but is not a direct admitted literal include execution path.', ref)
        rows = (self.bindings, self.invocations, self.contexts, self.paragraphs, self.effects, self.unresolved, self.proposals)
        _require(sum(map(len, rows)) <= MAX_FACTS, 'Scoped context report fact budget exceeded')
        return ScopedParagraphContextReport(tuple(self.refs[p] for p in self.sources),
            tuple(self.occ_refs), *(tuple(r) for r in rows), self.config_digest, self.catalog)

    def visit_occurrence(self, occ, language, shows, poisoned):
        ref = _occurrence_ref(occ)
        if ref.occurrence_id in self.visited:
            return
        self.visited.add(ref.occurrence_id)
        if occ.unresolved_codes:
            return
        syntax = _Syntax(self.sources[occ.source.path].tree, self.refs[occ.source.path])
        # Lexical namespace is fresh for every module occurrence. Render origins
        # are separate values carried at the direct include's actual position.
        env = {}
        ids, pos = syntax.nodes[0].children, 0
        while pos < len(ids):
            i = ids[pos]
            if syntax.nodes[i].kind == 'Text' and not syntax.nodes[i].children:
                # Literal root prose emits no set/show changes. It is never a
                # paragraph boundary owner for any callback region.
                pos += 1
                continue
            if syntax.nodes[i].kind in _TRIVIA and syntax.trivia_valid(i):
                pos += 1
                continue
            if syntax.token(i, 'Hash', b'#') and pos + 1 < len(ids) and syntax.nodes[i].end == syntax.nodes[ids[pos + 1]].start:
                node = ids[pos + 1]
                kind = syntax.nodes[node].kind
                clean = syntax.clean(node)
                if kind == 'ModuleImport' and clean:
                    ok = self.imports(syntax, node, ref, env)
                    poisoned = poisoned or not ok
                elif kind == 'SetRule' and clean:
                    language, ok = self.language(syntax, node, ref, env)
                    poisoned = poisoned or not ok
                elif kind == 'ShowRule' and clean:
                    show = self.show(syntax, node, ref, env)
                    _require(len(shows) < MAX_SHOW_CHAIN, 'Scoped show-chain budget exceeded')
                    shows = (*shows, show)
                    poisoned = poisoned or show.status != 'exact-apply-paragraph-tabs'
                elif kind == 'LetBinding' and clean and self.rebinding(syntax, node, env):
                    pass
                elif kind == 'ModuleInclude':
                    edge = self.edges[(occ.source.path, node)]
                    include_parts = syntax.sig(node)
                    literal_include = (len(include_parts) == 2
                        and syntax.token(include_parts[0], 'Include', b'include')
                        and syntax.string(include_parts[1]) == edge.request)
                    if (clean and literal_include and edge.target is not None and not edge.unresolved_codes
                            and len(edge.ancestry) == 1 and edge.ancestry[0].node_id == 0):
                        chain = (*tuple(_node_key(n) for n in occ.edge_chain), _node_key(edge.node))
                        child = self.occ_by_chain.get((occ.entrypoint.path, chain))
                        _require(child is not None, 'Missing direct include occurrence')
                        self.visit_occurrence(child, language, shows, poisoned)
                    else:
                        poisoned = True
                        self.unresolved_at('include-render-context-unresolved',
                            'Only direct literal include with known containment may carry conditional render origins.', ref, syntax.node_ref(node))
                elif kind == 'FuncCall':
                    if not clean:
                        poisoned = True
                        self.unresolved_at('suppression-or-comment-unresolved',
                            'Comment/suppression or malformed invocation has unresolved ownership.', ref, syntax.node_ref(node))
                    if not self.scope(syntax, node, ref, env, language, shows, poisoned):
                        poisoned = True
                        env.clear()
                        env[_UNKNOWN_NAMESPACE] = None
                else:
                    poisoned = True
                    self.unresolved_at('unsupported-root-syntax',
                        'Unsupported root syntax/effects cannot establish render transparency.', ref, syntax.node_ref(node))
                    env.clear()
                    env[_UNKNOWN_NAMESPACE] = None
                pos += 2
            else:
                poisoned = True
                code = 'suppression-or-comment-unresolved' if syntax.nodes[i].kind in ('LineComment', 'BlockComment') else 'unsupported-root-output'
                self.unresolved_at(code, 'Root output, comments and suppression retain explicit ownership obligations.', ref, syntax.node_ref(i))
                pos += 1

    def imports(self, s, i, occ, env):
        ids = s.sig(i)
        if (len(ids) != 4 or not s.token(ids[0], 'Import', b'import')
                or not s.token(ids[2], 'Colon', b':') or s.nodes[ids[3]].kind != 'ImportItems'):
            env.clear()
            env[_UNKNOWN_NAMESPACE] = None
            self.unresolved_at('import-namespace-unresolved', 'Only direct literal named imports establish binding origins.', occ, s.node_ref(i))
            return False
        path = s.string(ids[1])
        names, expect = [], True
        for item in s.sig(ids[3]):
            if not expect:
                if not s.token(item, 'Comma', b','):
                    names = []
                    break
            else:
                parts = s.sig(item)
                if s.nodes[item].kind == 'ImportItemPath' and len(parts) == 1 and s.ident(parts[0]):
                    names.append((s.ident(parts[0]), s.ident(parts[0]), item))
                elif s.nodes[item].kind == 'RenamedImportItem' and len(parts) == 3 and s.nodes[parts[0]].kind == 'ImportItemPath' and s.token(parts[1], 'As', b'as') and s.ident(parts[2]):
                    origin = s.sig(parts[0])
                    if len(origin) != 1 or not s.ident(origin[0]):
                        names = []
                        break
                    names.append((s.ident(origin[0]), s.ident(parts[2]), item))
                else:
                    names = []
                    break
            expect = not expect
        allowed = {TEXT_UTILS: {'paragraph_tab', 'apply-paragraph-tabs'},
                   SCOPED_ANNOTATIONS: {'local-tag-scope'}}
        if not names or path not in allowed or len({n[1] for n in names}) != len(names):
            env.clear()
            env[_UNKNOWN_NAMESPACE] = None
            self.unresolved_at('import-namespace-unresolved', 'Wildcard, facade, dynamic, field or unsupported package imports are not admitted.', occ, s.node_ref(i))
            return False
        package = self.package(path)
        namespace = path != SCOPED_ANNOTATIONS or self.package(MANNOT_NAMESPACE) is not None
        if not namespace:
            self.unresolved_at('scope-dependency-namespace-unresolved',
                'Exact retained mannot export-namespace evidence is required; it still does not prove dependency initialization.', occ, s.node_ref(i))
        for exported, local, item in names:
            supported = exported in allowed[path]
            binding = BindingOrigin(occ, s.node_ref(item), local, exported, path,
                package.source_identity if package else None,
                self.package_source_hashes[path] if package else None,
                self.package_tree_hashes[path] if package else None,
                'pinned-definition-conditional' if package and supported and namespace else 'unresolved')
            self.bindings.append(binding)
            env[local] = binding if supported else None
            if binding.identity_status == 'unresolved':
                self.unresolved_at('binding-origin-unresolved', 'Named import lacks an exact supported package definition Tree.', occ, s.node_ref(item))
        return package is not None and namespace and all(n[0] in allowed[path] for n in names)

    def language(self, s, i, occ, env):
        ids = s.sig(i)
        value = None
        ok = False
        if (len(ids) == 3 and s.token(ids[0], 'Set', b'set') and s.ident(ids[1]) == 'text' and 'text' not in env and _UNKNOWN_NAMESPACE not in env):
            args = s.args(ids[2])
            pair = s.named(args[0]) if args is not None and len(args) == 1 else None
            if pair and pair[0] == 'lang':
                value = s.string(pair[1])
                ok = value is not None and 0 < len(value) <= 64
        origin = LanguageOrigin(s.node_ref(i), occ, value, 'literal' if ok else 'unresolved')
        if not ok:
            self.unresolved_at('language-unresolved', 'Actual literal builtin text.lang is unproved; config does not supply it.', occ, s.node_ref(i))
        return origin, ok

    def show(self, s, i, occ, env):
        ids = s.sig(i)
        binding = env.get(s.ident(ids[2])) if len(ids) == 3 else None
        exact = (len(ids) == 3 and s.token(ids[0], 'Show', b'show')
                 and s.token(ids[1], 'Colon', b':') and s.ident(ids[2]) is not None
                 and binding is not None and binding.package == TEXT_UTILS
                 and binding.exported_name == 'apply-paragraph-tabs'
                 and binding.identity_status == 'pinned-definition-conditional')
        show = ShowOrigin(s.node_ref(i), occ, binding, 'exact-apply-paragraph-tabs' if exact else 'unresolved')
        if not exact:
            self.unresolved_at('show-transparency-unresolved', 'Unknown show can transform metadata/counter/context and callback content.', occ, s.node_ref(i))
        return show

    def rebinding(self, s, i, env):
        ids = s.sig(i)
        if len(ids) != 4 or not s.token(ids[0], 'Let', b'let') or s.ident(ids[1]) is None or not s.token(ids[2], 'Eq', b'='):
            return False
        value, name = ids[3], s.ident(ids[1])
        scalar = (not s.nodes[value].children and (
            s.token(value, 'None', b'none') or s.token(value, 'Auto', b'auto')
            or (s.nodes[value].kind == 'Bool' and s.text(value) in (b'true', b'false'))
            or (s.nodes[value].kind == 'Int' and re.fullmatch(rb'[0-9]+', s.text(value)) is not None)
            or (s.nodes[value].kind == 'Str' and s.string(value) is not None)))
        closure = s.sig(value) if s.nodes[value].kind == 'Closure' else ()
        # A single identity closure has no immediate defaults or body evaluation.
        identity = (len(closure) == 3 and s.nodes[closure[0]].kind == 'Params'
                    and s.token(closure[1], 'Arrow', b'=>')
                    and len(s.sig(closure[0])) == 1
                    and s.ident(s.sig(closure[0])[0]) is not None
                    and s.ident(s.sig(closure[0])[0]) == s.ident(closure[2]))
        if not scalar and not identity:
            env[name] = None
            return False
        env[name] = None
        return True

    def scope(self, s, i, occ, env, language, shows, poisoned):
        parts = s.sig(i)
        binding = env.get(s.ident(parts[0])) if len(parts) == 2 else None
        if (len(parts) != 2 or binding is None or binding.package != SCOPED_ANNOTATIONS
                or binding.exported_name != 'local-tag-scope'):
            self.unresolved_at('invocation-binding-unresolved',
                'Only a directly named locally imported local-tag-scope invocation is modeled.', occ, s.node_ref(i))
            return False
        args = s.args(parts[1])
        positional, named = [], {}
        if args is not None:
            for arg in args:
                if s.nodes[arg].kind == 'Named':
                    pair = s.named(arg)
                    if not pair or pair[0] in named:
                        args = None
                        break
                    named[pair[0]] = pair[1]
                else:
                    positional.append(arg)
        if args is None or len(positional) != 1 or set(named) - {'prefix', 'namespace'}:
            self.unresolved_at('scope-shape-unresolved', 'Scope requires one literal callback and no spread/extra arguments.', occ, s.node_ref(i))
            return False
        closure_id = positional[0]
        closure = s.sig(closure_id)
        callback = None
        parameter = None
        if s.nodes[closure_id].kind == 'Closure' and len(closure) == 3 and s.nodes[closure[0]].kind == 'Params' and s.token(closure[1], 'Arrow', b'=>'):
            params = s.sig(closure[0])
            parameter = s.ident(params[0]) if len(params) == 1 else None
            content = s.sig(closure[2])
            if (parameter is not None and s.nodes[closure[2]].kind == 'ContentBlock' and len(content) == 3
                    and s.token(content[0], 'LeftBracket', b'[') and s.token(content[2], 'RightBracket', b']')
                    and s.nodes[content[1]].kind == 'Markup'):
                callback = content[1]
        if callback is None:
            self.unresolved_at('callback-ownership-unresolved',
                'Callback must be one literal single-parameter content block; wrappers/stored content are excluded.', occ, s.node_ref(i))
            return False
        node, body = s.nodes[i], s.nodes[callback]
        scope = ScopeReference(occ, s.ref, i, (node.start, node.end), callback,
                               (body.start, body.end), parameter)
        prefix, mode = None, 'default-auto'
        if 'prefix' in named:
            p = named['prefix']
            prefix = s.string(p)
            mode = 'literal' if prefix is not None else ('explicit-auto' if s.token(p, 'Auto', b'auto') else 'dynamic')
        namespace = 'local-scope'
        if 'namespace' in named:
            namespace = s.string(named['namespace'])
        shape_ok = mode != 'dynamic' and namespace is not None
        if prefix is not None and (not prefix or len(prefix.encode()) > 512):
            shape_ok = False
        if namespace is None or len(namespace.encode()) > 512:
            shape_ok = False
        body_status = ('pinned-source-body-conditional' if binding.identity_status == 'pinned-definition-conditional'
                       else 'definition-unresolved')
        execution = ('direct-body-call-conditional' if mode == 'literal'
                     else 'deferred-context-expansion-unresolved' if mode in ('default-auto', 'explicit-auto')
                     else 'branch-unresolved')
        self.invocations.append(InvocationFact(scope, binding, mode, prefix,
            namespace if namespace is not None else '', body_status, execution, 'unresolved'))
        conditions = ('whole-package-initialization', 'runtime-package-selection',
                      'package-tree-provenance', 'wildcard-builtin-identity',
                      'actual-rendered-occurrence', 'callback-entry-exit-paragraph-boundaries',
                      'namespace-collision-and-counter-effects',
                      'selected-rule-coverage-and-suppression')
        if mode in ('default-auto', 'explicit-auto'):
            conditions = (*conditions, 'context-expansion-order-and-multiplicity')
            self.unresolved_at('auto-context-execution-unresolved',
                'Counter step precedes contextual counter read; source does not prove a single engine/layout evaluation.', occ, s.node_ref(i), scope)
        if not shape_ok:
            self.unresolved_at('scope-prefix-unresolved', 'Dynamic/invalid prefix or namespace has no supported invocation branch.', occ, s.node_ref(i), scope)
        self.unresolved_at('callback-entry-exit-boundary-unresolved',
            'Callback brackets, metadata/counter/context prefixes and adjacent root/include content prove no paragraph boundary; only owned interior boundaries are modeled.', occ, s.node_ref(i), scope)
        self.unresolved_at('scope-render-context-conditional',
            'Language/show origins are conditional at this direct occurrence, not evaluated render facts.', occ, s.node_ref(i), scope)
        self.unresolved_at('scope-namespace-collision-unresolved',
            'An occurrence identity does not prove a unique runtime namespace or absence of generated labels.', occ, s.node_ref(i), scope)
        show = shows[-1] if shows else None
        transparent = (not poisoned and bool(shows) and all(x.status == 'exact-apply-paragraph-tabs' for x in shows))
        self.contexts.append(RenderContextFact(scope, language, show, shows,
            'conditional-exact-helper-chain' if transparent else 'unresolved', conditions))
        # Effects are document-ordered facts, never erased as whitespace.
        effect_kinds = (('counter-step', 'outer-content'), ('counter-context-read', 'contextual-expansion')) if mode in ('default-auto', 'explicit-auto') else ()
        effect_kinds = (*effect_kinds, ('helper-closure-construction', 'make-scope'),
                        ('scope-metadata', 'make-scope-content'), ('callback-body', 'make-scope-content'))
        for ordinal, (kind, phase) in enumerate(effect_kinds):
            self.effects.append(EffectFact(scope, ordinal, kind, phase,
                'conditional-pinned-body' if body_status == 'pinned-source-body-conditional' and shape_ok else 'unresolved'))
        if not language.value or not _EN.fullmatch(language.value):
            self.unresolved_at('english-render-language-unresolved',
                'Missing, non-English or dynamic actual text.lang cannot yield English proposals.', occ, s.node_ref(i), scope)
        if not transparent:
            self.unresolved_at('scope-show-transparency-unresolved',
                'An exact applicable show chain with no unknown transformation is required.', occ, s.node_ref(i), scope)
        body_env = dict(env)
        body_env[parameter] = None  # The callback parameter shadows lexical imports.
        eligible = (mode == 'literal' and transparent and language.status == 'literal' and language.value is not None
                    and bool(_EN.fullmatch(language.value)) and shape_ok
                    and binding.identity_status == 'pinned-definition-conditional')
        self.paragraph_regions(s, callback, scope, body_env, eligible, conditions)
        return True

    def paragraph_regions(self, s, callback, scope, env, eligible, conditions):
        ids = s.nodes[callback].children
        parbreaks = tuple((s.nodes[c].start, s.nodes[c].end) for c in ids
                          if s.nodes[c].kind == 'Parbreak' and s.trivia_valid(c))
        regions, pos, pending = [], 0, None
        invalid = False
        while pos < len(ids):
            i, node = ids[pos], s.nodes[ids[pos]]
            if node.kind in _TRIVIA and s.trivia_valid(i):
                pos += 1
                continue
            if s.token(i, 'Hash', b'#') and pos + 1 < len(ids):
                marker_id = ids[pos + 1]
                name = s.ident(marker_id)
                binding = env.get(name)
                exact = (binding is not None and binding.package == TEXT_UTILS
                    and binding.exported_name == _MARKER
                    and binding.identity_status == 'pinned-definition-conditional')
                if s.nodes[i].end == s.nodes[marker_id].start and exact:
                    if pending is not None:
                        invalid = True
                        self.unresolved_at('marker-target-unresolved', 'Consecutive markers do not share an owned immediate prose target.', scope.occurrence, s.node_ref(marker_id), scope)
                    if name != _MARKER:
                        self.unresolved_at('marker-alias-spelling-unresolved',
                            'Named marker alias retains its origin but the canonical capitalization policy requires paragraph_tab spelling.', scope.occurrence, s.node_ref(marker_id), scope)
                    pending = (i, marker_id, binding)
                    pos += 2
                    continue
            if node.kind != 'Text' or node.children or b'\n' in s.text(i) or b'\r' in s.text(i):
                invalid = True
                code = 'suppression-or-comment-unresolved' if node.kind in ('LineComment', 'BlockComment') else 'callback-syntax-unresolved'
                self.unresolved_at(code, 'Callback contains unsupported syntax; no boundary crosses comments, wrappers, nested scope, metadata or include.', scope.occurrence, s.node_ref(i), scope)
                if pending is not None:
                    self.unresolved_at('marker-target-unresolved', 'Marker immediate target is not owned literal ordinary prose.', scope.occurrence, s.node_ref(pending[1]), scope)
                    pending = None
                pos += 1
                continue
            first, content = i, []
            while pos < len(ids):
                c = ids[pos]
                n = s.nodes[c]
                if n.kind in _TRIVIA and (n.kind == 'Parbreak' or b'\n' in s.text(c)):
                    break
                if (n.kind not in ('Text', 'Space') or n.children or b'\n' in s.text(c) or b'\r' in s.text(c)
                        or (n.kind == 'Space' and not s.trivia_valid(c))):
                    break
                content.append(c)
                pos += 1
            if not content:
                # Defensive bound: unsupported source cannot stall the scan.
                pos += 1
                invalid = True
                continue
            end = s.nodes[content[-1]].end
            word = _WORD.match(s.text(first))
            first_word = word[0] if word else b''
            word_span = (s.nodes[first].start, s.nodes[first].start + len(first_word)) if first_word else None
            marker, marker_hash, marker_binding, gap, immediate = None, None, None, None, False
            if pending is not None:
                marker_hash, marker_id, marker_binding = pending
                marker = s.node_ref(marker_id)
                gap = (s.nodes[marker_id].end, s.nodes[first].start)
                marker_line = s.raw[s.raw.rfind(b'\n', 0, s.nodes[marker_hash].start) + 1:s.nodes[marker_hash].start]
                # The callback opener is not a paragraph/line boundary. The
                # first marker is supported only on its own interior line.
                interior_line = s.raw.rfind(b'\n', s.nodes[callback].start, s.nodes[marker_hash].start) >= s.nodes[callback].start
                immediate = (bool(_LINE.fullmatch(s.raw[gap[0]:gap[1]]))
                             and not marker_line.strip(b' \t') and interior_line)
                if not immediate:
                    self.unresolved_at('marker-target-unresolved',
                        'Marker must occupy its own callback-interior line immediately before one literal prose line.', scope.occurrence, marker, scope)
                pending = None
            previous = regions[-1] if regions else None
            boundary_start = previous.span[1] if previous else s.nodes[callback].start
            target_start = s.nodes[marker_hash].start if marker_hash is not None else s.nodes[first].start
            boundary_bytes = s.raw[boundary_start:target_start]
            boundary_index = bisect_left(parbreaks, (boundary_start, -1))
            has_parbreak = boundary_index < len(parbreaks) and parbreaks[boundary_index][1] <= target_start
            boundary_owned = bool(has_parbreak and _WS.fullmatch(boundary_bytes)
                                  and boundary_bytes.count(b'\n') >= 2)
            if not boundary_owned:
                self.unresolved_at('callback-entry-boundary-unresolved' if previous is None else 'paragraph-start-boundary-unresolved',
                    'No blank source boundary wholly inside this callback proves this region starts a paragraph; opener, metadata and include edges cannot supply one.', scope.occurrence, s.node_ref(first), scope)
            if previous is not None and marker is None:
                gap_bytes = s.raw[previous.span[1]:s.nodes[first].start]
                if not _WS.fullmatch(gap_bytes) or gap_bytes.count(b'\n') < 2:
                    self.unresolved_at('paragraph-boundary-unresolved', 'Only an existing blank source boundary wholly inside one callback owns another unmarked paragraph.', scope.occurrence, s.node_ref(first), scope)
                    invalid = True
            region = ParagraphRegion(scope, len(regions), (s.nodes[first].start, end),
                tuple(content), word_span, first_word, marker, marker_hash, marker_binding,
                gap, immediate, 'literal-callback-owned',
                'internal-blank-source-boundary' if boundary_owned else 'unresolved')
            regions.append(region)
        if pending is not None:
            invalid = True
            self.unresolved_at('marker-target-unresolved', 'Trailing marker cannot target the callback closer or later root content.', scope.occurrence, s.node_ref(pending[1]), scope)
        self.paragraphs.extend(regions)
        _require(len(self.paragraphs) <= MAX_FACTS, 'Paragraph fact budget exceeded')
        if invalid:
            self.unresolved_at('callback-region-coverage-unresolved',
                'Unsupported callback syntax prevents a conditional diagnostic proposal for this callback.', scope.occurrence, scope=scope)
            return
        if not eligible:
            return
        for region in regions:
            if (region.marker is not None and region.marker_binding is not None
                    and region.marker_binding.local_name == _MARKER
                    and region.boundary_status == 'internal-blank-source-boundary'
                    and region.immediate_target and region.first_word
                    and region.first_word.islower() and region.first_word in WORDS
                    and len(region.first_word) > 1
                    and s.raw[region.first_word_span[1]:region.first_word_span[1]+1] in (b' ', b'\t', b'\r', b'\n')):
                self.proposals.append(ConditionalDiagnosticProposal(CAPITALIZATION, region,
                    region.first_word_span, 'Canonical marker owns a lowercase editorial-allowlisted literal English paragraph start.', conditions))
            # Do not infer a missing first marker from metadata/opener/include
            # boundaries. Missing later markers may be reported only as a
            # conditional policy question, not an insertion plan.
            elif (region.marker is None and region.ordinal > 0 and region.first_word
                    and region.boundary_status == 'internal-blank-source-boundary'):
                available = env.get(_MARKER)
                if (available is not None and available.package == TEXT_UTILS
                        and available.exported_name == _MARKER
                        and available.identity_status == 'pinned-definition-conditional'):
                    self.proposals.append(ConditionalDiagnosticProposal('pde.prose-paragraph-marker', region,
                        region.first_word_span, 'Later callback paragraph has an owned existing blank source boundary and no marker.', conditions))
                else:
                    self.unresolved_at('marker-binding-unresolved',
                        'A missing-marker policy proposal needs this callback own canonical imported marker binding.', scope.occurrence, scope=scope)


def analyze_scoped_paragraph_context(capture, *, package_trees=(), config=None, catalog=None):
    """Derive immutable conditional facts from original captured evidence.

    Config, when supplied, is retained bounded UTF-8 bytes, never render-language
    proof. Catalog is optional explicit metadata and is copied into immutable
    strings. No output grants editing, candidate, compilation or coverage power.
    """
    try:
        return _Analyzer(capture, package_trees, config, catalog).run()
    except ScopedParagraphContextEvidenceError:
        raise
    except (AttributeError, TypeError, UnicodeError, RecursionError) as error:
        raise ScopedParagraphContextEvidenceError('Malformed retained scoped context evidence') from error


analyze = analyze_scoped_paragraph_context
