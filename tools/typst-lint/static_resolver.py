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


REVIEWED_PACKAGES = (
    ReviewedPackage(TEXT_UTILS, TEXT_UTILS_SOURCE_SHA256, TEXT_UTILS_EXPORTS,
                    'local', 'text-utils', '0.1.2', 'lib.typ'),
    # The module only defines a function. Its runtime json("projects.json")
    # operation is never executed or followed by the static package loader.
    ReviewedPackage(PROJECT_REFERENCES, PROJECT_REFERENCES_SOURCE_SHA256,
                    PROJECT_REFERENCES_EXPORTS, 'local', 'project-references', '0.1.0', 'lib.typ'),
)


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
class Resolution:
    source_sha256: str
    facts: tuple[Facts, ...]
    skips: tuple[str, ...]
    # Even with no skips, graph/evaluation coverage has not been implemented.
    complete: bool = False

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

    def set_binding(self, i, binding):
        self.facts[i] = replace(self.facts[i], binding=binding)

    def package_binding(self, path, name):
        verified = self.packages.get(path)
        if verified is None:
            return None
        source, contract = verified
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
            binding = None if state.deferred else state.lookup(self.text(i))
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
            if len(children) == 2:
                self.visit(children[1], state)
            if output and not self.plain_footnote(binding, children):
                self.unknown_output(i, state)
            # A callee binding is never evidence for the result of a call.
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
            self.note(i, 'Deferred closure body/captures are unsupported')
            nested = state.nested(deferred=True)
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
        if len(children) == 3 and self.nodes[children[1]].kind == 'Colon' and self.nodes[children[2]].kind in ('Ident', 'FieldAccess'):
            binding = self.visit(children[2], state)
            self.set_binding(i, binding)
            supported = bool(binding and binding.kind == 'api' and binding.package == TEXT_UTILS and binding.name == 'apply-paragraph-tabs')
        else:
            nested = state.nested(deferred=True)
            for child in self.nodes[i].children:
                self.visit(child, nested)
        if not supported:
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
    return Resolution(digest(tree.source), tuple(resolver.facts), tuple(dict.fromkeys(resolver.skips)))
