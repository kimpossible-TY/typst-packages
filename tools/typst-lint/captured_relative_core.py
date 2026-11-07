"""Internal pure proof for captured relative scalar graphs, not a source profile.

Callers own parser/capture provenance. This module never reads, parses, evaluates,
loads catalogs, or authenticates a constructible ImportGraph. Only exact original
Trees are admitted. No existing writer or compiler consumes these proofs.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
import hashlib
from itertools import islice
import json
import re

import closed_core
from source_profiles import STRUCTURED_CORE_GRAMMAR_VERSION
from syntax_client import AdapterError, MAX_NODES, Node, Tree, validate
from typst_lint import SafetyError

SOURCE_PROFILE = 'captured-relative-core-v1'
ROOT_GRAMMAR_VERSION = 'captured-relative-root-grammar-v1'
MODULE_GRAMMAR_VERSION = 'captured-relative-module-grammar-v1'
PARSER_CONTRACT = closed_core.PARSER_CONTRACT
EVALUATED_RULE_IDS = closed_core.EVALUATED_RULE_IDS
STRUCTURAL_REASONS = tuple((rule, {
    'core.unique-scope-namespace': 'Every reachable import is exhaustively inert; no scope declaration exists in any root or module node.',
    'core.single-book-show': 'Every reachable import is exhaustively inert; no include, call, or shared math-book helper use exists; its using-helper precondition is false.',
}.get(rule, reason)) for rule, reason in closed_core.STRUCTURAL_REASONS)
NOT_APPLICABLE_RULE_IDS = tuple(rule for rule, _ in STRUCTURAL_REASONS)
CORE_RULE_IDS = EVALUATED_RULE_IDS + NOT_APPLICABLE_RULE_IDS

# Fixed, deliberately smaller than the eventual compiler workspace envelope.
MAX_FILES = 32
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024
MAX_IMPORT_EDGES = 128
MAX_IMPORT_DEPTH = 16  # number of edges from root, not number of vertices
MAX_PROJECT_DIRECTORIES = 64  # includes project root and every ancestor
MAX_PATH_BYTES = 512
MAX_TOTAL_PATH_BYTES = 16 * 1024
MAX_TOTAL_NODES = 200_000
MAX_BINDINGS = 4096  # declarations + each named import, across all files
MAX_USES = 8192
MAX_PROVENANCE_STEPS = 64
MAX_NAME_BYTES = 128
MAX_INTEGER = (1 << 63) - 1
_ISSUER = object()
_NAME = re.compile(r'[A-Za-z][A-Za-z0-9_-]*\Z', re.ASCII)


class CapturedRelativeCoreError(SafetyError):
    """Base refusal; neither a compiler result nor complete rule execution."""


class CapturedRelativeSyntaxError(CapturedRelativeCoreError):
    """Malformed syntax or a construct outside this exact inert language."""


class CapturedRelativeAuthorityError(CapturedRelativeCoreError):
    """Invalid/changed capture, closure, path, tree, budget, scope, or proof."""


def _authority(condition, message):
    if not condition:
        raise CapturedRelativeAuthorityError(message)


def _syntax(condition, message):
    if not condition:
        raise CapturedRelativeSyntaxError(message)


def canonical_path(path):
    """Validate an already canonical project-relative source identity, purely."""
    _authority(type(path) is str and 0 < len(path) <= MAX_PATH_BYTES,
               'Expected a bounded project-relative path')
    _authority(not path.startswith(('/', '@'))
               and '\\' not in path and all(p not in ('', '.', '..') for p in path.split('/'))
               and not any(ord(c) < 32 or ord(c) == 127 for c in path)
               and path.endswith('.typ'), 'Expected a canonical project-relative .typ path')
    try:
        size = len(path.encode('utf-8'))
    except UnicodeError as error:
        raise CapturedRelativeAuthorityError('Path is not UTF-8') from error
    _authority(size <= MAX_PATH_BYTES, 'Path byte budget exceeded')
    return path


def resolve_relative(importer, request):
    """Only necessary leading ../ is allowed; never consult the filesystem."""
    canonical_path(importer)
    _syntax(type(request) is str and bool(request), 'Relative import request must be a string')
    _authority(len(request) <= MAX_PATH_BYTES, 'Import request character budget exceeded')
    _syntax(not request.startswith(('/', '@'))
            and '\\' not in request and not any(ord(c) < 32 or ord(c) == 127 for c in request),
            'Only plain bounded relative import paths are admitted')
    try:
        request_bytes = len(request.encode('utf-8'))
    except UnicodeError as error:
        raise CapturedRelativeAuthorityError('Import request is not UTF-8') from error
    _authority(request_bytes <= MAX_PATH_BYTES, 'Import request byte budget exceeded')
    parts = request.split('/')
    _syntax(all(p not in ('', '.') for p in parts), 'Nonminimal relative import spelling')
    parent = importer.split('/')[:-1]
    position = 0
    while position < len(parts) and parts[position] == '..':
        _authority(bool(parent), 'Relative import escapes the captured project root')
        parent.pop()
        position += 1
    _syntax(position < len(parts) and '..' not in parts[position:],
            'Only leading parent traversal is admitted')
    _syntax(parts[-1].endswith('.typ'), 'Relative imports must target .typ sources')
    target = canonical_path('/'.join((*parent, *parts[position:])))
    original_parent, target_parts = importer.split('/')[:-1], target.split('/')
    common = 0
    while (common < min(len(original_parent), len(target_parts) - 1)
           and original_parent[common] == target_parts[common]):
        common += 1
    minimal = ['..'] * (len(original_parent) - common) + target_parts[common:]
    _syntax(parts == minimal, 'Unnecessary leading parent traversal is not minimal')
    return target


def _validate_tree(tree, path):
    canonical_path(path)
    _authority(type(tree) is Tree and type(tree.source) is bytes
               and len(tree.source) <= MAX_FILE_BYTES and tree.mode == 'markup'
               and type(tree.mode) is str and type(tree.nodes) is tuple
               and 0 < len(tree.nodes) <= MAX_NODES,
               'Exact bounded immutable markup Tree required')
    links = 0
    for node in tree.nodes:
        _authority(type(node) is Node and type(node.kind) is str and len(node.kind) < 80
                   and type(node.start) is int and type(node.end) is int
                   and 0 <= node.start <= node.end <= len(tree.source)
                   and type(node.children) is tuple and type(node.operands) is tuple
                   and len(node.operands) <= 2,
                   'Exact immutable bounded syntax Node fields required')
        links += len(node.children)
        _authority(links <= len(tree.nodes) - 1, 'Syntax child-link budget exceeded')
        _authority(all(type(p) is tuple and len(p) == 2 for p in node.operands),
                   'Exact immutable syntax operand fields required')
    records = []
    for node in tree.nodes:
        records.append(dict(kind=node.kind, start=node.start, end=node.end,
                            children=list(node.children), errors=node.errors,
                            operands=[dict(role=r, node=i) for r, i in node.operands],
                            display=node.display))
    payload = dict(schema=2, adapter_version='0.2.0', syntax_version='0.15.1',
                   mode='markup', source_bytes=len(tree.source),
                   source_sha256=hashlib.sha256(tree.source).hexdigest(), nodes=records)
    try:
        checked = validate(payload, tree.source, 'markup')
    except (AdapterError, UnicodeError, TypeError, ValueError, KeyError) as error:
        raise CapturedRelativeAuthorityError(f'Invalid captured syntax authority: {error}') from error
    _authority(checked == tree, 'Captured Tree changed during representation validation')
    # Actual parser error flags are input syntax only after the representation
    # and flag propagation have been validated. They cannot mask forged spans.
    _syntax(not any(node.errors for node in tree.nodes), 'Malformed captured Typst syntax')
    return records


@dataclass(frozen=True, slots=True)
class DeclarationOrigin:
    path: str
    source_sha256: str
    name: str
    node_id: int
    span: tuple[int, int]
    initializer_node_id: int
    initializer_span: tuple[int, int]


@dataclass(frozen=True, slots=True)
class BindingStep:
    action: str
    path: str
    source_sha256: str
    node_id: int
    span: tuple[int, int]
    name: str
    referenced_name: str | None = None
    target_path: str | None = None


@dataclass(frozen=True, slots=True)
class ScalarBinding:
    name: str
    kind: str
    value: int | str | bool | None
    origin: DeclarationOrigin
    provenance: tuple[BindingStep, ...]


@dataclass(frozen=True, slots=True)
class ImportEdge:
    importer: str
    request: str
    target: str
    node_id: int
    span: tuple[int, int]
    items: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    path: str
    source_sha256: str
    source_bytes: int
    tree_sha256: str
    nodes: int
    grammar_version: str


@dataclass(frozen=True, slots=True)
class ModuleProof:
    path: str
    exports: tuple[ScalarBinding, ...]


@dataclass(frozen=True, slots=True)
class ImportedUse:
    path: str
    node_id: int
    span: tuple[int, int]
    syntax_kind: str
    name: str
    binding: ScalarBinding
    ordinal: int
    # These owners survive script-only grouping, unlike byte spans/node IDs.
    owners: tuple[tuple, ...]
    operands: tuple[tuple[int, str], ...]

    @property
    def semantic_identity(self):
        binding = (self.binding.kind, self.binding.value, self.binding.origin,
                   tuple((s.action, s.path, s.name, s.referenced_name, s.target_path)
                         for s in self.binding.provenance))
        return (self.path, self.ordinal, self.syntax_kind, self.name,
                binding, self.owners, self.operands)


@dataclass(frozen=True, slots=True, init=False)
class CapturedRelativeCoreProof:
    source_profile: str
    target_path: str
    root_grammar_version: str
    module_grammar_version: str
    parser_contract: tuple[str, ...]
    sources: tuple[SourceIdentity, ...]
    edges: tuple[ImportEdge, ...]
    modules: tuple[ModuleProof, ...]
    root_bindings: tuple[ScalarBinding, ...]
    imported_uses: tuple[ImportedUse, ...]
    evaluated_rule_ids: tuple[str, ...]
    not_applicable_rule_ids: tuple[str, ...]
    structural_reasons: tuple[tuple[str, str], ...]
    _issuer: object = field(repr=False, compare=False)

    def __init__(self, *args, **kwargs):
        raise TypeError('CapturedRelativeCoreProof is privately issued by certify_captured_relative_core')


class _Graph:
    def __init__(self, target, captures):
        self.target = canonical_path(target)
        _authority(isinstance(captures, Mapping) and 0 < len(captures) <= MAX_FILES,
                   'Exact bounded captured Tree mapping required')
        try:
            items = tuple(islice(captures.items(), MAX_FILES + 1))
        except (TypeError, ValueError, RuntimeError) as error:
            raise CapturedRelativeAuthorityError('Captured mapping changed or is malformed') from error
        _authority(len(items) == len(captures) and len(items) <= MAX_FILES
                   and all(type(item) is tuple and len(item) == 2 for item in items),
                   'Captured path membership changed or is unbounded')
        for path, _ in items:
            canonical_path(path)
        _authority(len({p for p, _ in items}) == len(items), 'Duplicate captured path')
        self.trees, self.identities = {}, {}
        sizes = paths = nodes = 0
        directories = {''}
        for path, tree in items:
            canonical_path(path)
            _authority(type(tree) is Tree and type(tree.source) is bytes
                       and len(tree.source) <= MAX_FILE_BYTES and type(tree.nodes) is tuple,
                       'Each capture must retain a bounded immutable original Tree')
            sizes += len(tree.source)
            paths += len(path.encode('utf-8'))
            nodes += len(tree.nodes)
            _authority(sizes <= MAX_SOURCE_BYTES and paths <= MAX_TOTAL_PATH_BYTES
                       and nodes <= MAX_TOTAL_NODES, 'Aggregate source/path/node budget exceeded')
            parts = path.split('/')[:-1]
            directories.update('/'.join(parts[:i]) for i in range(1, len(parts) + 1))
            _authority(len(directories) <= MAX_PROJECT_DIRECTORIES, 'Project parent-directory budget exceeded')
            records = _validate_tree(tree, path)
            sha = hashlib.sha256(tree.source).hexdigest()
            fingerprint = hashlib.sha256(json.dumps(records, sort_keys=True,
                separators=(',', ':')).encode()).hexdigest()
            self.trees[path] = tree
            self.identities[path] = SourceIdentity(path, sha, len(tree.source), fingerprint,
                len(tree.nodes), ROOT_GRAMMAR_VERSION if path == target else MODULE_GRAMMAR_VERSION)
        _authority(target in self.trees, 'Writable target is absent from exact capture')
        self.edges, self.modules, self.active = [], {}, []
        self.heights = {}
        self.reached = {target}
        self.binding_count = 0

    def step(self, path, node_id, action, name, referenced=None, target=None):
        node = self.trees[path].nodes[node_id]
        return BindingStep(action, path, self.identities[path].source_sha256,
                           node_id, (node.start, node.end), name, referenced, target)

    def add(self, env, binding):
        _syntax(binding.name not in env, 'Duplicate or ambiguous captured binding')
        self.binding_count += 1
        _authority(self.binding_count <= MAX_BINDINGS, 'Captured binding budget exceeded')
        _authority(len(binding.provenance) <= MAX_PROVENANCE_STEPS, 'Alias provenance budget exceeded')
        env[binding.name] = binding

    def import_into(self, grammar, index, env):
        request, items = grammar.import_statement(index)
        target = resolve_relative(grammar.path, request)
        _authority(len(self.edges) < MAX_IMPORT_EDGES, 'Import edge budget exceeded')
        node = grammar.nodes[index]
        self.edges.append(ImportEdge(grammar.path, request, target, index,
                                      (node.start, node.end), items))
        exports = self.module(target)
        for remote, local in items:
            _syntax(remote in exports, 'Named imported scalar is not exported')
            value = exports[remote]
            self.add(env, ScalarBinding(local, value.kind, value.value, value.origin,
                (*value.provenance, self.step(grammar.path, index, 'import', local, remote, target))))

    def module(self, path):
        _authority(path in self.trees, 'Reachable import is missing from exact capture')
        _syntax(path != self.target and path not in self.active, 'Cyclic captured relative import graph')
        # Check every route, even through cached diamond vertices.
        _authority(len(self.active) + 1 <= MAX_IMPORT_DEPTH, 'Import depth budget exceeded')
        self.reached.add(path)
        if path not in self.modules:
            self.active.append(path)
            grammar = _ModuleGrammar(self, path)
            grammar.module()
            self.active.pop()
            self.modules[path] = grammar.env
            self.heights[path] = 1 + max((self.heights[e.target] for e in self.edges
                                        if e.importer == path), default=0)
        _authority(len(self.active) + self.heights[path] <= MAX_IMPORT_DEPTH,
                   'Import depth budget exceeded')
        return self.modules[path]



class _Statements(closed_core._Grammar):
    def __init__(self, graph, path):
        super().__init__(graph.trees[path], STRUCTURED_CORE_GRAMMAR_VERSION)
        self.graph, self.path, self.env = graph, path, {}

    def name(self, index):
        name = self.token(index, 'Ident')
        _syntax(len(name) <= MAX_NAME_BYTES and bool(_NAME.fullmatch(name))
                and len(name.encode('utf-8')) <= MAX_NAME_BYTES,
                'Unsupported captured binding name')
        return name

    def parts(self, index, kind):
        node = self.enter(index)
        _syntax(node.kind == kind and bool(node.children), f'Expected exhaustive {kind} structure')
        parts = []
        for child in node.children:
            if self.nodes[child].kind == 'Space':
                self.space(child)
                _syntax(all(c in ' \t' for c in self.raw(child)), 'Statements require horizontal literal whitespace')
            else:
                parts.append(child)
        return tuple(parts)

    def separated(self, left, right):
        _syntax(self.nodes[left].end < self.nodes[right].start,
                'Statement keywords/names require literal whitespace separation')

    def string(self, index):
        raw = self.token(index, 'Str')
        _syntax(len(raw) >= 2 and raw[0] == raw[-1] == '"'
                and '\\' not in raw and '"' not in raw[1:-1]
                and all(ord(c) >= 32 and ord(c) != 127 for c in raw[1:-1]),
                'Only plain unescaped single-line strings are admitted')
        return raw[1:-1]

    def import_statement(self, index):
        parts = self.parts(index, 'ModuleImport')
        _syntax(len(parts) == 4, 'Only standalone named relative imports are admitted')
        keyword, path, colon, items = parts
        self.token(keyword, 'Import', 'import')
        self.separated(keyword, path)
        request = self.string(path)
        self.token(colon, 'Colon', ':')
        entries = self.parts(items, 'ImportItems')
        _syntax(bool(entries), 'Empty import list')
        result, expect_item = [], True
        for item in entries:
            if not expect_item:
                self.token(item, 'Comma', ',')
                expect_item = True
                continue
            if self.nodes[item].kind == 'RenamedImportItem':
                renamed = self.parts(item, 'RenamedImportItem')
                _syntax(len(renamed) == 3, 'Invalid named import rename')
                original, as_id, local = renamed
                self.token(as_id, 'As', 'as')
                self.separated(original, as_id)
                self.separated(as_id, local)
                local = self.name(local)
            else:
                original, local = item, None
            original_parts = self.parts(original, 'ImportItemPath')
            _syntax(len(original_parts) == 1, 'Dotted or namespace import item refused')
            remote = self.name(original_parts[0])
            result.append((remote, local or remote))
            expect_item = False
        _syntax(bool(result) and len({name for _, name in result}) == len(result),
                'Duplicate named import destination')
        return request, tuple(result)

    def standalone(self, hash_id, statement):
        self.token(hash_id, 'Hash', '#')
        h, n = self.nodes[hash_id], self.nodes[statement]
        _syntax(h.end == n.start, 'Hash must be adjacent to its statement')
        before = self.tree.source[:h.start]
        start = max(before.rfind(b'\n'), before.rfind(b'\r')) + 1
        prefix = before[start:]
        if start == 0 and prefix.startswith(b'\xef\xbb\xbf'):
            prefix = prefix[3:]
        end = n.end
        while end < len(self.tree.source) and self.tree.source[end:end+1] not in (b'\r', b'\n'):
            end += 1
        _syntax(not prefix.strip(b' \t') and not self.tree.source[n.end:end].strip(b' \t'),
                'Import/declaration must occupy its own line')

    def bom(self, index):
        return self.nodes[index].kind == 'Text' and self.nodes[index].start == 0 and self.raw(index) == '\ufeff'


class _ModuleGrammar(_Statements):
    def module(self):
        root = self.enter(0)
        _syntax(root.kind == 'Markup' and (root.children or root.start == root.end), 'Invalid inert module root')
        siblings = root.children
        position = 0
        while position < len(siblings):
            index = siblings[position]
            kind = self.nodes[index].kind
            position += 1
            if self.bom(index):
                self.token(index, 'Text', '\ufeff')
            elif kind in ('Space', 'Parbreak'):
                self.space(index, kind)
            else:
                _syntax(kind == 'Hash' and position < len(siblings), 'Module output or unsupported statement')
                statement = siblings[position]
                position += 1
                self.standalone(index, statement)
                kind = self.nodes[statement].kind
                if kind == 'ModuleImport':
                    self.graph.import_into(self, statement, self.env)
                elif kind == 'LetBinding':
                    self.declaration(statement)
                else:
                    raise CapturedRelativeSyntaxError('Module initializer/output/style/execution is not inert')
        _syntax(len(self.visited) == len(self.nodes), 'Unvisited module node')

    def declaration(self, index):
        parts = self.parts(index, 'LetBinding')
        _syntax(len(parts) == 4, 'Only simple inert scalar declarations are admitted')
        keyword, name_id, eq, value_id = parts
        self.token(keyword, 'Let', 'let')
        self.separated(keyword, name_id)
        name = self.name(name_id)
        self.token(eq, 'Eq', '=')
        kind = self.nodes[value_id].kind
        if kind == 'Ident':
            reference = self.name(value_id)
            _syntax(reference in self.env, 'Alias must refer to an earlier proved binding')
            previous = self.env[reference]
            binding = ScalarBinding(name, previous.kind, previous.value, previous.origin,
                (*previous.provenance, self.graph.step(self.path, index, 'alias', name, reference)))
        else:
            if kind == 'Str':
                scalar_kind, value = 'string', self.string(value_id)
            elif kind == 'Int':
                raw = self.token(value_id, 'Int')
                _syntax(bool(re.fullmatch(r'[0-9]{1,19}', raw, re.ASCII)), 'Only bounded nonnegative decimal integers are admitted')
                value = int(raw)
                _syntax(value <= MAX_INTEGER, 'Integer exceeds signed-64-bit scalar range')
                scalar_kind = 'integer'
            elif kind in ('Bool', 'None', 'Auto'):
                raw = self.token(value_id, kind)
                _syntax(raw in {'Bool': ('true', 'false'), 'None': ('none',), 'Auto': ('auto',)}[kind],
                        'Invalid inert literal spelling')
                scalar_kind, value = {'Bool': ('boolean', raw == 'true'), 'None': ('none', None), 'Auto': ('auto', None)}[kind]
            else:
                raise CapturedRelativeSyntaxError('Unproved initializer, call, closure, content, resource or expression')
            node, initializer = self.nodes[index], self.nodes[value_id]
            origin = DeclarationOrigin(self.path, self.graph.identities[self.path].source_sha256,
                name, index, (node.start, node.end), value_id, (initializer.start, initializer.end))
            binding = ScalarBinding(name, scalar_kind, value, origin,
                (self.graph.step(self.path, index, 'declaration', name),))
        self.graph.add(self.env, binding)


class _RootGrammar(_Statements):
    def __init__(self, graph):
        super().__init__(graph, graph.target)
        self.use_nodes = {}

    def body(self, index):
        body = self.enter(index)
        _syntax(body.kind == 'Markup' and (body.children or body.start == body.end), 'Invalid root markup body')
        prefix = index == 0
        siblings, position = body.children, 0
        while position < len(siblings):
            child = siblings[position]
            kind = self.nodes[child].kind
            position += 1
            if kind in ('Space', 'Parbreak'):
                self.space(child, kind)
                continue
            if prefix and self.bom(child):
                self.token(child, 'Text', '\ufeff')
                continue
            if kind == 'Hash':
                _syntax(position < len(siblings), 'Unpaired scalar hash')
                statement = siblings[position]
                position += 1
                if self.nodes[statement].kind == 'ModuleImport':
                    _syntax(prefix, 'Imports are confined to the initial root prefix')
                    self.standalone(child, statement)
                    self.graph.import_into(self, statement, self.env)
                    continue
                prefix = False
                self.token(child, 'Hash', '#')
                _syntax(self.nodes[child].end == self.nodes[statement].start, 'Scalar hash must be adjacent')
                name = self.name(statement)
                self.use(statement, name, 'Ident')
                continue
            prefix = False
            if kind == 'Text':
                text = self.token(child, 'Text')
                if self.nodes[child].start == 0 and text.startswith('\ufeff'):
                    text = text[1:]
                _syntax(all((c == ' ' or c.isprintable()) and c not in closed_core._PROSE_META for c in text),
                        'Nonliteral root prose')
            elif kind == 'Equation':
                self.equation(child)
            elif kind in ('Heading', 'ListItem', 'EnumItem'):
                self.line_wrapper(child)
            elif kind in ('Strong', 'Emph'):
                self.inline_wrapper(child)
            else:
                raise CapturedRelativeSyntaxError(f'Unsupported root markup kind {kind}')

    def expr(self, index):
        node = self.nodes[index]
        if node.kind == 'MathIdent':
            name = self.raw(index)
            # Existing token grammar is checked first. In particular neither a
            # one-character MathText nor foo-bar becomes an imported identifier.
            super().expr(index)
            if name in self.env:
                self.use(index, name, 'MathIdent')
        else:
            super().expr(index)

    def use(self, index, name, kind):
        _syntax(name in self.env and self.env[name].kind in ('integer', 'string'),
                'Rendered imports must resolve to proved integer/string scalars')
        _authority(len(self.use_nodes) < MAX_USES, 'Imported use budget exceeded')
        _syntax(index not in self.use_nodes, 'Repeated imported use')
        self.use_nodes[index] = (kind, name, self.env[name])

    def ownership(self):
        counts = {'wrapper': 0, 'equation': 0, 'attachment': 0}
        wrappers = frozenset(('Heading', 'ListItem', 'EnumItem', 'Strong', 'Emph'))
        result = []
        def visit(index, owners=(), operands=()):
            node = self.nodes[index]
            if node.kind in wrappers:
                counts['wrapper'] += 1
                owners += ((node.kind, counts['wrapper']),)
            elif node.kind == 'Equation':
                counts['equation'] += 1
                owners += (('Equation', counts['equation'], node.display),)
            if index in self.use_nodes:
                kind, name, binding = self.use_nodes[index]
                result.append(ImportedUse(self.path, index, (node.start, node.end), kind,
                                          name, binding, len(result) + 1, owners, operands))
            if node.kind == 'MathAttach':
                counts['attachment'] += 1
                ordinal = counts['attachment']
                roles = dict((child, role) for role, child in node.operands)
                for child in node.children:
                    visit(child, owners, (*operands, (ordinal, roles.get(child, 'base'))))
            else:
                for child in node.children:
                    visit(child, owners, operands)
        visit(0)
        return tuple(result)


def certify_captured_relative_core(target_path, captures, *, source_profile=SOURCE_PROFILE):
    """Derive a proof from an exact protected-capture mapping of original Trees.

    Structural validation is not parser authenticity. The caller must obtain
    these original Trees from its owned pinned-parser/capture transaction.
    """
    _authority(type(source_profile) is str and source_profile == SOURCE_PROFILE,
               'Caller must select the exact internal captured-relative scope')
    try:
        graph = _Graph(target_path, captures)
        grammar = _RootGrammar(graph)
        grammar.markup()
        _authority(graph.reached == set(graph.trees), 'Extra unreachable sources in exact capture')
        uses = grammar.ownership()
    except closed_core.ClosedCoreError as error:
        raise CapturedRelativeSyntaxError(str(error)) from error
    except CapturedRelativeCoreError:
        raise
    except (AttributeError, TypeError, ValueError, KeyError, RuntimeError) as error:
        raise CapturedRelativeAuthorityError('Malformed or changed captured representation') from error
    values = dict(source_profile=source_profile, target_path=target_path,
        root_grammar_version=ROOT_GRAMMAR_VERSION, module_grammar_version=MODULE_GRAMMAR_VERSION,
        parser_contract=PARSER_CONTRACT,
        sources=tuple(graph.identities[path] for path in sorted(graph.identities)),
        edges=tuple(sorted(graph.edges, key=lambda e: (e.importer, e.span))),
        modules=tuple(ModuleProof(path, tuple(env[name] for name in sorted(env)))
                      for path, env in sorted(graph.modules.items())),
        root_bindings=tuple(grammar.env[name] for name in sorted(grammar.env)),
        imported_uses=uses, evaluated_rule_ids=EVALUATED_RULE_IDS,
        not_applicable_rule_ids=NOT_APPLICABLE_RULE_IDS,
        structural_reasons=STRUCTURAL_REASONS, _issuer=_ISSUER)
    proof = object.__new__(CapturedRelativeCoreProof)
    for name, value in values.items():
        object.__setattr__(proof, name, value)
    return proof


def _same_exact(left, right):
    # Dataclass/tuple equality treats True == 1 == 1.0. A changed proof must
    # preserve exact scalar/metadata types as well as values at every depth.
    if type(left) is not type(right):
        return False
    if type(right) is tuple:
        return len(left) == len(right) and all(_same_exact(a, b) for a, b in zip(left, right))
    if is_dataclass(right):
        try:
            return all(_same_exact(getattr(left, f.name), getattr(right, f.name))
                       for f in fields(right))
        except AttributeError:
            return False
    return left == right


def verify_captured_relative_core(proof, *, target_path, captures, source_profile=SOURCE_PROFILE):
    """Re-derive within caller-selected scope, never proof-selected authority."""
    _authority(type(proof) is CapturedRelativeCoreProof and getattr(proof, '_issuer', None) is _ISSUER,
               'Foreign/unissued captured-relative proof')
    try:
        expected = certify_captured_relative_core(target_path, captures, source_profile=source_profile)
    except CapturedRelativeCoreError as error:
        raise CapturedRelativeAuthorityError(f'Captured-relative proof authority changed: {error}') from error
    _authority(_same_exact(proof, expected), 'Captured-relative proof differs from current source/path/tree/scope')


certify = certify_captured_relative_core
verify_proof = verify_captured_relative_core


def discover_relative_requests(tree, path):
    """Return bounded import candidates, never proof or capture authority.

    Inspect every node, refusing all include/dynamic/package/namespace imports.
    This permits capture to open named dependencies before full grammar proof;
    unsupported placement/other code is still refused by final certification.
    """
    requests = []
    try:
        _validate_tree(tree, path)
        class Discovery:
            trees = {path: tree}
        grammar = _Statements(Discovery(), path)
        for index, node in enumerate(tree.nodes):
            _syntax(node.kind != 'ModuleInclude', 'Includes are outside captured relative discovery')
            if node.kind == 'ModuleImport':
                request, _ = grammar.import_statement(index)
                _authority(len(requests) < MAX_IMPORT_EDGES, 'Import edge budget exceeded')
                requests.append((request, resolve_relative(path, request)))
    except closed_core.ClosedCoreError as error:
        raise CapturedRelativeSyntaxError(str(error)) from error
    except CapturedRelativeCoreError:
        raise
    except (AttributeError, TypeError, ValueError, KeyError, RuntimeError) as error:
        raise CapturedRelativeAuthorityError('Malformed or changed discovery representation') from error
    return tuple(requests)
