"""Conservative, syntax-only binding and language proofs over immutable ASTs.

No evaluation, filesystem reads, package search or import traversal happens here.
PackageSource is explicit trusted loader input: the loader must verify its selected
package manifest, entrypoint identity and read safety. We additionally match the
entrypoint bytes to the reviewed contract below. A path/version alone is not proof.

This is deliberately a direct-import subset, not a complete document-graph or
Typst evaluator. Only root markup may be certified ordinary prose. Nested content,
closures, dynamic execution, includes and unknown show/output effects are not
certified. All unresolved cases remain partial coverage; absence of a binding is
never evidence that an arbitrary same-spelled helper has the reviewed semantics.
"""
from dataclasses import dataclass, replace

from book_provenance import BookApplication, BookProof, document_show_shape, literal_options, value_binding
from syntax_client import Tree
from typst_lint import InputError, digest

TEXT_UTILS = '@local/text-utils:0.1.2'
TEXT_UTILS_SOURCE_SHA256 = '7e151150dac47b01ebf7706457906133d74a52abee1b5c43ce824ff89279bd71'
# The complete public namespace of this exact, dependency-free source. Both
# marker spellings are the same immutable value. No future version is inferred.
TEXT_UTILS_EXPORTS = (
    ('paragraph-tab', 'paragraph_tab'), ('paragraph_tab', 'paragraph_tab'),
    ('apply-paragraph-tabs', 'apply-paragraph-tabs'), ('highlighted', 'highlighted'),
    ('capitalize-title', 'capitalize-title'), ('is-numeric-title', 'is-numeric-title'),
)
MATH_BLOCKS = '@local/math-blocks:0.2.0'
MATH_BLOCKS_SOURCE_SHA256 = '542c7ce1ebfc6fbd51a7fefb2b55405a968a951b61d49170e49ef331948f36ff'
# Deliberately partial namespace. Wildcards invalidate other names below.
MATH_BLOCKS_EXPORTS = tuple((name, name) for name in
                           ('light-theme', 'dark-theme', 'theme-from-text-fill', 'dots_space', 'proof',
                            'apply-math-block-reset'))
MATH_BOOK = '@local/math-book:0.2.0'
MATH_BOOK_SOURCE_SHA256 = '943c400626fd66b1d80b7f95391a66b245da83db33b4a74e4a17aa6a1275e3e8'
MATH_BOOK_EXPORTS = (('apply-math-book', 'apply-math-book'),)


def is_book_binding(binding):
    return bool(binding and binding.kind == 'api' and binding.name == 'apply-math-book'
                and binding.package == MATH_BOOK and binding.source_sha256 == MATH_BOOK_SOURCE_SHA256)


def is_reset_binding(binding):
    return bool(binding and binding.kind == 'api' and binding.name == 'apply-math-block-reset'
                and binding.package == MATH_BLOCKS and binding.source_sha256 == MATH_BLOCKS_SOURCE_SHA256)


def is_spacer_binding(binding):
    # This exact entrypoint defines an immutable content value using only math
    # builtins: $& wide dots.h.c thin$. Its named dependency imports cannot
    # redefine those names. No facade, other version or function result is
    # inferred. See FOOTNOTE_CONTRACT.md for the reviewed dependency boundary.
    return bool(binding and binding.kind == 'api' and binding.name == 'dots_space'
                and binding.package == MATH_BLOCKS
                and binding.source_sha256 == MATH_BLOCKS_SOURCE_SHA256)


def is_proof_binding(binding):
    # The exact local closure captures math_font/qed and builtin parbreak/text,
    # never an imported dependency helper. This establishes callable identity,
    # not general output/style safety. See PROOF_CONTRACT.md.
    return bool(binding and binding.kind == 'api' and binding.name == 'proof'
                and binding.package == MATH_BLOCKS
                and binding.source_sha256 == MATH_BLOCKS_SOURCE_SHA256)


def is_theme_binding(binding):
    return bool(binding and binding.package == MATH_BLOCKS
                and binding.source_sha256 == MATH_BLOCKS_SOURCE_SHA256
                and ((binding.kind == 'api' and binding.name in
                      ('light-theme', 'dark-theme', 'theme-from-text-fill'))
                     or binding.kind in ('module', 'palette')))


SCOPED_ANNOTATIONS = '@local/scoped-annotations:0.3.0'
SCOPED_ANNOTATIONS_SOURCE_SHA256 = 'eb8e7ff7b31238c8fa4b996deb9502fec0ae4610240427a8b40873708e509203'
# Only the locally defined wrappers are reviewed. Re-exported @preview/mannot
# symbols do not inherit this provenance.
SCOPED_ANNOTATIONS_EXPORTS = tuple((name, name) for name in
                                 ('local-tag-scope', 'local-scope-annotations',
                                  'mannot-scope', 'annot-cetz-local'))
MANNOT_NAMESPACE = '@preview/mannot:0.4.0'
MANNOT_NAMESPACE_SOURCE_SHA256 = '288027cea059b327bf3253c44ae975339e7afc0f149375d9d87ca1c1fe739f57'
MANNOT_NAMESPACE_MANIFEST_SHA256 = 'aef4310b36955dc98d8b03ec4abf17d45777d0a08b65d6e67214752e57647545'

PROJECT_REFERENCES = '@local/project-references:0.1.0'
PROJECT_REFERENCES_SOURCE_SHA256 = '94b5f60db26e97faa511285fecd92a0f2afed708c8d56b1ed252cce3458a1720'
PROJECT_REFERENCES_EXPORTS = (('project-ref', 'project-ref'),)
# These literal prefixes are rejected by the exact reviewed project-ref body.
# They are not a general inference about arbitrary label-generating functions.
PROJECT_REFERENCE_LOCAL_PREFIXES = (
    'local-scope-', 'local-scope-annotations-', 'mannot-scope-', '_mannot-',
)


@dataclass(frozen=True)
class PackageSource:
    import_path: str
    source_identity: str
    source: bytes


@dataclass(frozen=True)
class ReviewedPackage:
    import_path: str
    source_sha256: str
    exports: tuple[tuple[str, str], ...]
    namespace: str
    name: str
    version: str
    entrypoint: str
    manifest_sha256: str | None = None


REVIEWED_PACKAGES = (
    ReviewedPackage(SCOPED_ANNOTATIONS, SCOPED_ANNOTATIONS_SOURCE_SHA256, SCOPED_ANNOTATIONS_EXPORTS,
                    'local', 'scoped-annotations', '0.3.0', 'lib.typ'),
    ReviewedPackage(MATH_BLOCKS, MATH_BLOCKS_SOURCE_SHA256, MATH_BLOCKS_EXPORTS,
                    'local', 'math-blocks', '0.2.0', 'lib.typ'),
    ReviewedPackage(MATH_BOOK, MATH_BOOK_SOURCE_SHA256, MATH_BOOK_EXPORTS,
                    'local', 'math-book', '0.2.0', 'lib.typ'),
    ReviewedPackage(TEXT_UTILS, TEXT_UTILS_SOURCE_SHA256, TEXT_UTILS_EXPORTS,
                    'local', 'text-utils', '0.1.2', 'lib.typ'),
    # The module only defines a function. Its runtime json("projects.json")
    # operation is never executed or followed by the static package loader.
    ReviewedPackage(PROJECT_REFERENCES, PROJECT_REFERENCES_SOURCE_SHA256,
                    PROJECT_REFERENCES_EXPORTS, 'local', 'project-references', '0.1.0', 'lib.typ'),
)

# Namespace evidence only: the exact two-line entrypoint imports named exports
# that include neither ref nor label. It supplies NO raw mannot API or module
# bindings. This fixed optional artifact is requested only after the loader has
# verified scoped-annotations itself, never by cache or dependency discovery.
REVIEWED_NAMESPACE_PACKAGES = (
    ReviewedPackage(MANNOT_NAMESPACE, MANNOT_NAMESPACE_SOURCE_SHA256, (),
                    'preview', 'mannot', '0.4.0', 'src/lib.typ', MANNOT_NAMESPACE_MANIFEST_SHA256),
)
REVIEWED_NAMESPACE_DEPENDENCIES = {SCOPED_ANNOTATIONS: (MANNOT_NAMESPACE,)}


@dataclass(frozen=True)
class Binding:
    kind: str
    name: str
    package: str | None = None
    source_identity: str | None = None
    source_sha256: str | None = None


@dataclass(frozen=True)
class Facts:
    binding: Binding | None = None
    effective_language: str | None = None
    in_context: bool = False
    deferred: bool = False
    prose_safe: bool = False


@dataclass(frozen=True)
class SpacerProof:
    """Source-bound resolver attestations, not a complete evaluation proof.

    existing names expressions whose values are the reviewed spacer, never
    function calls merely invoking a same-bound callee. available names builtin
    footnote calls where the canonical spelling is in scope before evaluation.
    Deferred execution and unknown output/style effects cannot prove insertion.
    """
    source_sha256: str
    existing: frozenset[int] = frozenset()
    available: frozenset[int] = frozenset()


@dataclass(frozen=True)
class ParagraphInsertionProof:
    """Root Text nodes where the canonical marker and reviewed show are ready.

    This attests availability at that lexical point, not the existence of a
    paragraph boundary, casing eligibility or permission to apply an edit.
    """
    source_sha256: str
    available: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Resolution:
    source_sha256: str
    facts: tuple[Facts, ...]
    skips: tuple[str, ...]
    # Even with no skips, graph/evaluation coverage has not been implemented.
    complete: bool = False
    spacer_proof: SpacerProof | None = None
    paragraph_insertion_proof: ParagraphInsertionProof | None = None
    book_proof: BookProof | None = None

    def binding_for(self, node_id: int) -> Binding | None:
        return self.facts[node_id].binding

    def language_for(self, node_id: int) -> str | None:
        return self.facts[node_id].effective_language


_TRIVIA = frozenset(('Space', 'Parbreak', 'LineComment', 'BlockComment'))
_ASSIGNMENTS = frozenset(('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq'))
_BUILTINS = frozenset(('text', 'block', 'parbreak', 'footnote', 'highlight', 'figure',
                       'heading', 'list', 'enum', 'terms', 'page', 'context'))
# Their source content cannot change the enclosing lexical environment.
_STRUCTURAL = frozenset(('Heading', 'ListItem', 'EnumItem', 'TermItem', 'Strong',
                         'Emph', 'Raw', 'Equation', 'Link', 'Ref'))
_LITERAL_KINDS = frozenset(('Text', 'Space', 'Parbreak', 'LineComment', 'BlockComment',
                           'Hash', 'Semicolon', 'Label', 'Escape', 'Shorthand',
                           'Linebreak', 'SmartQuote'))


def _significant(tree, node_id):
    return [i for i in tree.nodes[node_id].children if tree.nodes[i].kind not in _TRIVIA]


def _raw(tree, node_id):
    node = tree.nodes[node_id]
    return tree.source[node.start:node.end]


def _literal_string(tree, node_id):
    """A bounded subset of actual Str nodes, never a source-code regex parser."""
    if tree.nodes[node_id].kind != 'Str':
        return None
    raw = _raw(tree, node_id)
    if len(raw) < 2 or raw[:1] != b'"' or raw[-1:] != b'"':
        return None
    value = raw[1:-1]
    if b'\\' in value or not value.isascii() or any(c < 32 for c in value):
        return None
    return value.decode('ascii')


def imported_paths(tree: Tree) -> tuple[str, ...]:
    """Direct literal import requests only, including lexical nested imports.

    This does not resolve includes, dynamic expressions or escaped strings and
    does not grant provenance to any request it returns.
    """
    if tree.nodes[0].errors:
        return ()
    paths = []
    for i, node in enumerate(tree.nodes):
        if node.kind == 'ModuleImport':
            children = _significant(tree, i)
            if len(children) >= 2:
                value = _literal_string(tree, children[1])
                if value is not None and value not in paths:
                    paths.append(value)
    return tuple(paths)


class _Frame:
    def __init__(self):
        self.values = {}
        self.wildcard = False


class _State:
    def __init__(self, language=None):
        self.frames = [_Frame()]
        self.language = language
        self.language_supported = True
        self.styles_unknown = False
        self.prose_safe = True
        self.in_context = False
        self.deferred = False
        self.paragraph_tabs_active = False

    def nested(self, *, deferred=False, contextual=False):
        new = _State()
        new.frames = self.frames + [_Frame()]
        new.language = None  # Content may be rendered with different inherited styles.
        new.language_supported = False
        new.styles_unknown = self.styles_unknown
        new.prose_safe = False
        new.in_context = self.in_context or contextual
        new.deferred = self.deferred or deferred
        if deferred:
            # Bodies are not executed. Do not let hypothetical mutations leak
            # back into the actual lexical flow, nor infer closure captures.
            new.frames = [_Frame()]
            new.frames[0].wildcard = True
            # Typst closures capture values at definition. Only the reviewed
            # immutable theme APIs/modules survive this bounded proof. Copy
            # frames so speculative body mutations cannot affect the caller.
            visible = {}
            for frame in self.frames:
                if frame.wildcard:
                    visible.clear()
                visible.update(frame.values)
            new.frames[0].values = {name: value for name, value in visible.items()
                                    if is_theme_binding(value)}
        return new

    def lookup(self, name):
        for frame in reversed(self.frames):
            if name in frame.values:
                return frame.values[name]
            if frame.wildcard:
                return None
        return Binding('builtin', name) if name in _BUILTINS else None

    def bind(self, name, value):
        self.frames[-1].values[name] = value

    def mutate(self, name):
        for frame in reversed(self.frames):
            if name in frame.values:
                frame.values[name] = None
                return
            if frame.wildcard:
                break
        self.bind(name, None)

    def unknown_wildcard(self):
        frame = self.frames[-1]
        frame.values = dict.fromkeys(frame.values)
        frame.wildcard = True


class _Resolver:
    def __init__(self, tree, fallback_language, packages):
        self.tree = tree
        self.nodes = tree.nodes
        self.facts = [Facts() for _ in tree.nodes]
        self.available_spacers = set()
        self.book_applications = []
        self.book_imports = set()
        for child in tree.nodes[0].children:
            if tree.nodes[child].kind == 'ModuleImport':
                parts = _significant(tree, child)
                if len(parts) >= 2 and not any(tree.nodes[c].kind == 'Star' for c in parts):
                    self.book_imports.add(_literal_string(tree, parts[1]))
        self.available_paragraph_markers = set()
        self.root_children = frozenset(tree.nodes[0].children) if tree.mode == 'markup' else frozenset()
        self.skips = []
        self.packages = {}
        seen = set()
        for package in packages:
            if type(package) is not PackageSource or type(package.source) is not bytes:
                raise InputError('Resolver package input must be immutable PackageSource')
            if type(package.import_path) is not str or type(package.source_identity) is not str or not package.source_identity:
                raise InputError('Resolver package identity must be explicit')
            if package.import_path in seen:
                raise InputError('Duplicate resolver package identity')
            seen.add(package.import_path)
            namespace = next((p for p in REVIEWED_NAMESPACE_PACKAGES
                              if p.import_path == package.import_path), None)
            if namespace is not None:
                if digest(package.source) != namespace.source_sha256:
                    self.skips.append(f'Unreviewed package namespace: {package.import_path}')
                continue
            contract = next((p for p in REVIEWED_PACKAGES if p.import_path == package.import_path), None)
            if contract is None or digest(package.source) != contract.source_sha256:
                self.skips.append(f'Unreviewed package content or version: {package.import_path}')
                continue
            self.packages[package.import_path] = (package, contract)
        self.state = _State(fallback_language)

    def text(self, i):
        return _raw(self.tree, i).decode('utf-8')

    def sig(self, i):
        return _significant(self.tree, i)

    def note(self, i, message):
        self.skips.append(f'{message} at byte {self.nodes[i].start}')

    def record(self, i, state, binding=None):
        self.facts[i] = Facts(binding, None if state.styles_unknown or state.deferred or not state.language_supported else state.language,
                              state.in_context, state.deferred, state.prose_safe and not state.styles_unknown and not state.deferred)
        marker = state.lookup('paragraph_tab')
        if (i in self.root_children and self.nodes[i].kind == 'Text'
                and self.facts[i].prose_safe and state.paragraph_tabs_active
                and marker and marker.kind == 'api' and marker.name == 'paragraph_tab'
                and marker.package == TEXT_UTILS
                and marker.source_sha256 == TEXT_UTILS_SOURCE_SHA256):
            self.available_paragraph_markers.add(i)

    def set_binding(self, i, binding):
        self.facts[i] = replace(self.facts[i], binding=binding)

    def package_binding(self, path, name):
        verified = self.packages.get(path)
        if verified is None:
            return None
        source, contract = verified
        # math-book captures its wildcard-imported reset helper. Its own bytes
        # alone cannot establish that dependency's callable identity/namespace.
        if path == MATH_BOOK and MATH_BLOCKS not in self.packages:
            return None
        canonical = dict(contract.exports).get(name)
        if canonical is None:
            return None
        return Binding('api', canonical, path, source.source_identity, contract.source_sha256)

    def module_binding(self, path):
        verified = self.packages.get(path)
        if verified is None:
            return None
        source, contract = verified
        return Binding('module', path.split('/')[1].split(':')[0], path,
                       source.source_identity, contract.source_sha256)

    def identifier_names(self, i):
        """Binding-pattern identifiers, excluding dictionary keys and defaults."""
        kind = self.nodes[i].kind
        children = self.sig(i)
        if kind == 'Ident':
            return (self.text(i),)
        if kind == 'Named':
            # Destructuring (key: local-name) uses its value as the pattern.
            return self.identifier_names(children[-1]) if children else ()
        if kind in ('Destructuring', 'Spread', 'Parenthesized'):
            return tuple(name for child in children for name in self.identifier_names(child))
        return ()

    def visit(self, i, state, *, output=False):
        node = self.nodes[i]
        kind = node.kind
        self.record(i, state)
        children = self.sig(i)
        if kind == 'Ident':
            binding = state.lookup(self.text(i))
            if state.deferred and not is_theme_binding(binding):
                binding = None
            self.set_binding(i, binding)
            if output and not (binding and binding.kind == 'api' and binding.name == 'paragraph_tab'):
                self.unknown_output(i, state)
            return binding
        if kind == 'FieldAccess':
            binding = None
            if len(children) == 3 and self.nodes[children[1]].kind == 'Dot' and self.nodes[children[2]].kind == 'Ident':
                receiver = self.visit(children[0], state)
                self.record(children[1], state)
                self.record(children[2], state)
                if receiver and receiver.kind == 'module':
                    binding = self.package_binding(receiver.package, self.text(children[2]))
            self.set_binding(i, binding)
            if output and not (binding and binding.kind == 'api' and binding.name == 'paragraph_tab'):
                self.unknown_output(i, state)
            return binding
        if kind == 'Parenthesized':
            inner = [c for c in children if self.nodes[c].kind not in ('LeftParen', 'RightParen')]
            binding = self.visit(inner[0], state) if len(inner) == 1 else None
            self.set_binding(i, binding)
            if output:
                self.unknown_output(i, state)
            return binding
        if kind in ('Markup', 'Code'):
            root_markup = kind == 'Markup' and i == 0
            for child in node.children:
                child_kind = self.nodes[child].kind
                self.visit(child, state, output=root_markup and child_kind not in _LITERAL_KINDS
                           and child_kind not in ('ModuleImport', 'LetBinding', 'SetRule', 'ShowRule'))
            return None
        if kind == 'ModuleImport':
            self.import_node(i, state)
            return None
        if kind == 'LetBinding':
            self.let_node(i, state)
            return None
        if kind == 'SetRule':
            self.set_node(i, state)
            return None
        if kind == 'ShowRule':
            self.show_node(i, state)
            return None
        if kind == 'FuncCall':
            binding = self.visit(children[0], state) if len(children) == 2 and self.nodes[children[1]].kind == 'Args' else None
            self.set_binding(i, binding)
            # The inserted expression executes before this call's arguments.
            # Capture the lexical value here, not after walking a content body
            # which may invalidate an outer binding. Aliases of dots_space are
            # sufficient for removal, but not for inserting its canonical name.
            if (not state.deferred and not state.styles_unknown
                    and binding and binding.kind == 'builtin' and binding.name == 'footnote'
                    and is_spacer_binding(state.lookup('dots_space'))):
                self.available_spacers.add(i)
            if len(children) == 2:
                self.visit(children[1], state)
            if output and not (self.plain_footnote(binding, children)
                               or self.plain_highlight(binding, children)):
                self.unknown_output(i, state)
            # Only this exact reviewed zero-argument palette selector has a
            # known result. Arbitrary calls still never establish provenance.
            if (is_theme_binding(binding) and binding.kind == 'api'
                    and binding.name == 'theme-from-text-fill'
                    and len(children) == 2
                    and all(self.nodes[c].kind in ('LeftParen', 'RightParen')
                            for c in self.sig(children[1]))):
                return replace(binding, kind='palette', name='resolved-theme')
            return None
        if kind in ('ContentBlock', 'CodeBlock'):
            nested = state.nested()
            for child in node.children:
                self.visit(child, nested)
            if output:
                self.unknown_output(i, state)
            return None
        if kind == 'Contextual':
            nested = state.nested(contextual=True, deferred=True)
            for child in node.children:
                self.visit(child, nested)
            if output:
                self.unknown_output(i, state)
            return None
        if kind == 'Closure':
            self.note(i, 'Deferred closure body/captures are unsupported except reviewed theme values')
            nested = state.nested(deferred=True)
            # Parameters, including defaults/destructuring, mask captures.
            # Mask every identifier in Params conservatively (also default
            # expressions); none of them can invent reviewed provenance.
            for child in node.children:
                if self.nodes[child].kind == 'Params':
                    pending = [child]
                    while pending:
                        part = pending.pop()
                        if self.nodes[part].kind == 'Ident':
                            nested.bind(self.text(part), None)
                        pending.extend(self.nodes[part].children)
                elif self.nodes[child].kind == 'Ident':
                    nested.bind(self.text(child), None)
            for child in node.children:
                self.visit(child, nested)
            return None
        if kind == 'Binary' and any(self.nodes[c].kind in _ASSIGNMENTS for c in children):
            for child in node.children:
                self.visit(child, state)
            if children:
                self.mutation(i, children[0], state)
            return None
        if kind == 'DestructAssignment':
            for child in node.children:
                self.visit(child, state)
            if children:
                self.destruct_mutation(children[0], state)
                self.note(i, 'Destructuring assignment invalidates target bindings')
            return None
        if kind in ('Conditional', 'ForLoop', 'WhileLoop'):
            # Potential assignments in any branch invalidate outer bindings, but
            # new locals are scoped and never become unconditional evidence.
            nested = state.nested()
            if kind == 'ForLoop':
                try:
                    end = next(j for j, c in enumerate(children) if self.nodes[c].kind == 'In')
                    for pattern in children[1:end]:
                        for name in self.identifier_names(pattern):
                            nested.bind(name, None)
                except StopIteration:
                    pass
            for child in node.children:
                self.visit(child, nested)
            self.note(i, 'Conditional/loop execution is unsupported')
            if output:
                self.unknown_output(i, state)
            return None
        if kind == 'ModuleInclude':
            for child in node.children:
                self.visit(child, state)
            self.note(i, 'Include graph/effective styles are unsupported')
            state.styles_unknown = True
            state.prose_safe = False
            return None
        if kind in _STRUCTURAL:
            nested = state.nested()
            for child in node.children:
                self.visit(child, nested)
            return None
        if kind == 'Named':
            # A named argument's key is not a variable reference.
            if children:
                self.record(children[0], state)
            for child in children[1:]:
                self.visit(child, state)
            return None
        for child in node.children:
            self.visit(child, state)
        if output and kind not in _LITERAL_KINDS:
            self.unknown_output(i, state)
        return None

    def unknown_output(self, i, state):
        self.note(i, 'Output effect/body ownership is unsupported')
        state.prose_safe = False
        state.styles_unknown = True

    def plain_footnote(self, binding, children):
        # A proven builtin footnote with nothing but literal markup has scoped
        # note content and no executable source capable of mutating the caller.
        # This narrow exception supports subsequent root paragraph-gap checks.
        if not binding or binding.kind != 'builtin' or binding.name != 'footnote' or len(children) != 2:
            return False
        args = self.sig(children[1])
        if len(args) != 1 or self.nodes[args[0]].kind != 'ContentBlock':
            return False
        content = self.sig(args[0])
        if len(content) != 3 or [self.nodes[c].kind for c in content] != ['LeftBracket', 'Markup', 'RightBracket']:
            return False
        return all(self.nodes[c].kind in ('Text', 'Space', 'Parbreak')
                   for c in self.nodes[content[1]].children)

    def plain_highlight(self, binding, children):
        # Only called for immediate root output. The builtin and reviewed
        # text-utils wrapper scope their presentation to literal body content;
        # neither changes the following root's bindings or text language.
        supported = bool(binding and (
            (binding.kind == 'builtin' and binding.name == 'highlight') or
            (binding.kind == 'api' and binding.name == 'highlighted'
             and binding.package == TEXT_UTILS
             and binding.source_sha256 == TEXT_UTILS_SOURCE_SHA256)))
        if not supported or len(children) != 2:
            return False
        # Deliberately reject options, parentheses, comments and extra arguments.
        args = self.nodes[children[1]].children
        if len(args) != 1 or self.nodes[args[0]].kind != 'ContentBlock':
            return False
        content = self.nodes[args[0]].children
        if len(content) != 3 or [self.nodes[c].kind for c in content] != ['LeftBracket', 'Markup', 'RightBracket']:
            return False
        # Certify only the text-only fragments generated by extraction. The
        # original math-containing call remains unknown; even literal equations
        # do not broaden this bounded output contract. Existing unknown styles
        # are never reset by this exception.
        return all(self.nodes[c].kind in ('Text', 'Space')
                   for c in self.nodes[content[1]].children)

    def destruct_mutation(self, target, state):
        kind = self.nodes[target].kind
        parts = self.sig(target)
        if kind in ('Ident', 'FieldAccess', 'FuncCall'):
            self.mutation(target, target, state)
        elif kind == 'Named' and parts:
            self.destruct_mutation(parts[-1], state)
        elif kind in ('Destructuring', 'Spread', 'Parenthesized'):
            for part in parts:
                self.destruct_mutation(part, state)

    def mutation(self, i, target, state):
        # Includes field/array mutation: an earlier alias retains its value, but
        # the mutated receiver itself cannot retain module/API provenance.
        while self.nodes[target].kind in ('FieldAccess', 'FuncCall', 'Parenthesized'):
            children = self.sig(target)
            if self.nodes[target].kind == 'Parenthesized':
                children = [c for c in children if self.nodes[c].kind not in ('LeftParen', 'RightParen')]
            if not children:
                break
            target = children[0]
        if self.nodes[target].kind == 'Ident':
            state.mutate(self.text(target))
        else:
            state.unknown_wildcard()
        self.note(i, 'Assignment invalidates target provenance')

    def let_node(self, i, state):
        children = self.sig(i)
        if len(children) >= 2 and self.nodes[children[1]].kind == 'Closure':
            closure = children[1]
            parts = self.sig(closure)
            if parts and self.nodes[parts[0]].kind == 'Ident':
                state.bind(self.text(parts[0]), None)
            self.visit(closure, state)
            return
        if len(children) < 2:
            state.unknown_wildcard()
            self.note(i, 'Unsupported binding pattern')
            return
        pattern = children[1]
        names = self.identifier_names(pattern)
        rhs = children[3] if len(children) == 4 and self.nodes[children[2]].kind == 'Eq' else None
        value = self.visit(rhs, state) if rhs is not None else None
        for name in names:
            state.bind(name, value if self.nodes[pattern].kind == 'Ident' else None)
        if self.nodes[pattern].kind != 'Ident':
            self.note(i, 'Destructured values are not inferred')
        if not names:
            state.unknown_wildcard()
            self.note(i, 'Unsupported binding pattern')

    def import_node(self, i, state):
        children = self.sig(i)
        path = _literal_string(self.tree, children[1]) if len(children) >= 2 else None
        if len(children) >= 2:
            # A dynamic import source can itself contain assignments. Reading
            # its AST effects is not importing or executing the source.
            self.visit(children[1], state)
        module = self.module_binding(path)
        if module is None:
            self.note(i, 'Import has no verified direct-package contract')
        items = next((c for c in children if self.nodes[c].kind == 'ImportItems'), None)
        if any(self.nodes[c].kind == 'Star' for c in children):
            if module:
                if path in (MATH_BLOCKS, MATH_BOOK, SCOPED_ANNOTATIONS):
                    state.unknown_wildcard()
                    self.note(i, 'Reviewed package contract has a partial export namespace')
                for name, _ in self.packages[path][1].exports:
                    state.bind(name, self.package_binding(path, name))
            else:
                state.unknown_wildcard()
                self.note(i, 'Unknown wildcard may shadow every visible name/builtin')
        elif items is not None:
            for item in self.sig(items):
                kind = self.nodes[item].kind
                if kind not in ('ImportItemPath', 'RenamedImportItem'):
                    continue
                parts = self.sig(item)
                import_item = parts[0] if kind == 'RenamedImportItem' else item
                names = [c for c in self.sig(import_item) if self.nodes[c].kind == 'Ident']
                if not names:
                    state.unknown_wildcard()
                    continue
                local = self.text(parts[-1]) if kind == 'RenamedImportItem' else self.text(names[-1])
                binding = self.package_binding(path, self.text(names[0])) if len(names) == 1 else None
                state.bind(local, binding)
                if binding is None:
                    self.note(item, 'Imported item is unresolved or outside reviewed exports')
        else:
            if len(children) == 4 and self.nodes[children[2]].kind == 'As' and self.nodes[children[3]].kind == 'Ident':
                name = self.text(children[3])
            elif path is not None:
                name = path.rsplit('/', 1)[-1].split(':', 1)[0]
                if not path.startswith('@'):
                    name = name.rsplit('.', 1)[0]
            else:
                name = None
            if name:
                state.bind(name, module)
            else:
                state.unknown_wildcard()

    def set_node(self, i, state):
        children = self.sig(i)
        target = children[1] if len(children) > 1 else None
        binding = self.visit(target, state) if target is not None else None
        self.set_binding(i, binding)
        args = children[2] if len(children) > 2 and self.nodes[children[2]].kind == 'Args' else None
        if args is not None:
            self.visit(args, state)
        # Conditions are expressions too: a code block here can mutate an
        # outer alias even though whether the set rule applies is unknown.
        for child in children[3:]:
            self.visit(child, state)
        if not binding or binding.kind != 'builtin':
            state.language = None
            state.styles_unknown = True
            self.note(i, 'Set target is not a proven builtin')
            return
        if binding.name != 'text':
            return
        if args is None:
            state.language = None
            self.note(i, 'Unsupported text set arguments')
            return
        values = []
        spread = False
        for argument in self.sig(args):
            kind = self.nodes[argument].kind
            if kind == 'Spread':
                spread = True
            if kind == 'Named':
                parts = self.sig(argument)
                if len(parts) == 3 and self.text(parts[0]) == 'lang':
                    values.append(_literal_string(self.tree, parts[2]))
        if not values and not spread:
            return
        conditional = any(self.nodes[c].kind == 'If' for c in children)
        if len(values) != 1 or values[0] is None or spread or conditional:
            state.language = None
            self.note(i, 'Dynamic/mixed/conditional source language')
        else:
            state.language = values[0]

    def show_node(self, i, state):
        children = self.sig(i)
        supported = False
        book_application = False
        shape = document_show_shape(self.tree, i)
        if (shape is not None and self.tree.mode == 'markup' and i in self.nodes[0].children
                and {MATH_BOOK, MATH_BLOCKS} <= self.book_imports):
            owner = value_binding(self.tree, shape.receiver, state.lookup, self.package_binding)
            if is_book_binding(owner) or is_reset_binding(owner):
                valid, enabled = literal_options(self.tree, shape.arguments, book=is_book_binding(owner))
                reset = self.package_binding(MATH_BLOCKS, 'apply-math-block-reset')
                if valid and is_reset_binding(reset):
                    # The transformation expression is evaluated immediately;
                    # only its body is deferred. No generic call-result binding
                    # or captured/deferred ownership proof is manufactured.
                    self.visit(shape.expression, state)
                    self.set_binding(i, owner)
                    self.book_applications.append(BookApplication(i, owner, reset, enabled,
                                                                   shape.arguments is None))
                    book_application = True
                else:
                    self.note(i, 'Dynamic/customized book or reset show options are unsupported')
        if book_application:
            pass  # Its general prose/styles remain unknown below.
        elif len(children) == 3 and self.nodes[children[1]].kind == 'Colon' and self.nodes[children[2]].kind in ('Ident', 'FieldAccess'):
            binding = self.visit(children[2], state)
            self.set_binding(i, binding)
            supported = bool(binding and binding.kind == 'api' and binding.package == TEXT_UTILS
                             and binding.source_sha256 == TEXT_UTILS_SOURCE_SHA256
                             and binding.name == 'apply-paragraph-tabs')
        else:
            nested = state.nested(deferred=True)
            for child in self.nodes[i].children:
                self.visit(child, nested)
        if supported:
            # The show captures its current function value. Rebinding its name
            # later does not uninstall it; unknown effects still block proofs.
            state.paragraph_tabs_active = True
        else:
            state.paragraph_tabs_active = False
            state.styles_unknown = True
            state.prose_safe = False
            self.note(i, 'Unknown show transformation may change language/body ownership')


def resolve(tree: Tree, *, fallback_language=None, packages=()) -> Resolution:
    """Return conservative facts aligned to immutable ``tree.nodes`` IDs.

    Fallback is used only before any source language override. Unknown/dynamic
    source lang is never rescued by fallback. A later unconditional literal text
    setting can supersede a dynamic setting, but cannot defeat an unknown show.
    The package loader, not this resolver, owns manifest and file identity checks.
    All facts describe the supplied snapshot, never the compiler's future files.
    """
    if type(tree) is not Tree or type(tree.source) is not bytes or type(tree.nodes) is not tuple:
        raise InputError('Resolver requires immutable validated syntax tree')
    if fallback_language is not None and (type(fallback_language) is not str or not fallback_language.strip()):
        raise InputError('Invalid fallback language')
    if type(packages) is not tuple:
        raise InputError('Resolver package sources must be an immutable tuple')
    resolver = _Resolver(tree, fallback_language, packages)
    if tree.nodes[0].errors:
        return Resolution(digest(tree.source), tuple(resolver.facts), ('Malformed syntax; resolver has no proofs',))
    if tree.mode != 'markup':
        resolver.state.prose_safe = False
        resolver.state.language_supported = False
    resolver.visit(0, resolver.state)
    existing = frozenset(i for i, fact in enumerate(resolver.facts)
                         if tree.nodes[i].kind in ('Ident', 'FieldAccess', 'Parenthesized')
                         and not fact.deferred and is_spacer_binding(fact.binding))
    proof = SpacerProof(digest(tree.source), existing, frozenset(resolver.available_spacers))
    paragraph_proof = ParagraphInsertionProof(digest(tree.source), frozenset(resolver.available_paragraph_markers))
    return Resolution(digest(tree.source), tuple(resolver.facts), tuple(dict.fromkeys(resolver.skips)),
                      spacer_proof=proof, paragraph_insertion_proof=paragraph_proof,
                      book_proof=BookProof(digest(tree.source), tuple(resolver.book_applications)))
