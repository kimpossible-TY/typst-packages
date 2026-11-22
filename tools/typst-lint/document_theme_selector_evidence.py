"""Conditional proof for a reviewed theme selector in an unchanged callback."""
from dataclasses import replace
import unicodedata

import conditional_footnote_binding as binding
import conditional_highlight as conditional
import document_footnote_evidence as footnote_evidence
import facade_provenance
import import_graph
import namespace_packages
from package_loader import _manifest
from static_resolver import (Binding, MATH_BLOCKS, MATH_BLOCKS_SOURCE_SHA256, _Resolver, is_theme_binding,
                             PROJECT_REFERENCES, PROJECT_REFERENCES_SOURCE_SHA256)
from syntax_client import parse
from typst_lint import SafetyError, digest

_THEME = 'theme-from-text-fill'


def _snapshot_package(resources, namespace, name, version, source_sha):
    """Select a staged package without assuming absence in a higher root."""
    version_path = unicodedata.normalize('NFC', f'{namespace}/{name}/{version}').casefold()
    for root in ('packages', 'cache'):
        def affects(key):
            if key[0] != root:
                return False
            value = unicodedata.normalize('NFC', key[1]).casefold()
            return value == version_path or value.startswith(version_path + '/') or version_path.startswith(value + '/')
        if not any(affects(key) for key in resources):
            continue
        prefix = f'{namespace}/{name}/{version}/'
        manifest = resources.get((root, prefix + 'typst.toml'))
        source = resources.get((root, prefix + 'lib.typ'))
        if manifest is None or source is None or digest(source) != source_sha:
            raise SafetyError('Selected transitive package is absent or differs from the reviewed source')
        metadata = _manifest(manifest, prefix)
        if (metadata['name'] != name or metadata['version'] != version or metadata['entrypoint'] != 'lib.typ'
                or metadata.get('namespace', namespace) != namespace):
            raise SafetyError('Selected transitive package manifest differs from the expected identity')
        return
    raise SafetyError('A required transitive package is absent from the preview snapshot')


def _verify_selector(evidence, resources):
    session = evidence._session
    selected = {item.evidence.import_path: item for item in session._namespaces.packages}
    math = selected.get(MATH_BLOCKS)
    text = selected.get(namespace_packages.TEXT_UTILS)
    if math is None or text is None or digest(math.source) != MATH_BLOCKS_SOURCE_SHA256:
        raise SafetyError('Reviewed math-blocks selector or its namespace dependency is unavailable')
    footnote_evidence._verify_package(session, resources, math.evidence)
    footnote_evidence._verify_package(session, resources, text.evidence)
    tree = parse(math.source)
    if tree.nodes[0].errors:
        raise SafetyError('Reviewed selector source is malformed')
    imports = []
    for index in tree.nodes[0].children:
        if tree.nodes[index].kind == 'ModuleImport':
            shape = import_graph.import_shape(tree, index)
            if shape is None or shape.items is None:
                raise SafetyError('Selector package has unsupported import dependencies')
            imports.append(shape.request)
    if set(imports) != {namespace_packages.TEXT_UTILS, PROJECT_REFERENCES}:
        raise SafetyError('Selector package dependency set differs from the reviewed source')
    _snapshot_package(resources, 'local', 'project-references', '0.1.0', PROJECT_REFERENCES_SOURCE_SHA256)
    return math.evidence


def _palette_paths(source):
    """Parse paths present in both complete pinned palette dictionaries."""
    tree = parse(source)
    if tree.nodes[0].errors:
        raise SafetyError('Reviewed palette source is malformed')
    palettes = {}

    def scalar(index):
        kind = tree.nodes[index].kind
        if kind == 'None':
            return True
        if kind == 'Ident':
            return import_graph.raw(tree, index) in (b'black', b'white')
        if kind != 'FuncCall':
            return False
        call = import_graph.significant(tree, index)
        if (len(call) != 2 or tree.nodes[call[0]].kind != 'Ident'
                or tree.nodes[call[1]].kind != 'Args'):
            return False
        callee = import_graph.raw(tree, call[0])
        args = import_graph.significant(tree, call[1])
        return (len(args) == 3 and tree.nodes[args[0]].kind == 'LeftParen'
                and tree.nodes[args[-1]].kind == 'RightParen'
                and ((callee == b'rgb' and tree.nodes[args[1]].kind == 'Str')
                     or (callee == b'luma' and tree.nodes[args[1]].kind == 'Numeric')))

    def visit(index, prefix):
        if tree.nodes[index].kind != 'Dict':
            raise SafetyError('Reviewed palette is not a literal dictionary')
        result = set()
        parts = import_graph.significant(tree, index)
        if (len(parts) < 2 or tree.nodes[parts[0]].kind != 'LeftParen'
                or tree.nodes[parts[-1]].kind != 'RightParen'):
            raise SafetyError('Reviewed palette dictionary syntax is unsupported')
        names = set()
        for item in parts[1:-1]:
            if tree.nodes[item].kind == 'Comma':
                continue
            if tree.nodes[item].kind != 'Named':
                raise SafetyError('Reviewed palette contains a nonliteral field')
            named = import_graph.significant(tree, item)
            if (len(named) != 3 or tuple(tree.nodes[x].kind for x in named[:2]) != ('Ident', 'Colon')):
                raise SafetyError('Reviewed palette field syntax is unsupported')
            name = import_graph.raw(tree, named[0]).decode('utf-8')
            if name in names:
                raise SafetyError('Reviewed palette contains duplicate fields')
            names.add(name)
            path = prefix + (name,)
            if tree.nodes[named[2]].kind == 'Dict':
                result.update(visit(named[2], path))
            elif scalar(named[2]):
                result.add(path)
            else:
                raise SafetyError('Reviewed palette contains an unsupported non-scalar value')
        return result

    for index in tree.nodes[0].children:
        if tree.nodes[index].kind != 'LetBinding':
            continue
        parts = import_graph.significant(tree, index)
        if (len(parts) == 4 and tree.nodes[parts[1]].kind == 'Ident'
                and import_graph.raw(tree, parts[1]) in (b'light-theme', b'dark-theme')):
            name = import_graph.raw(tree, parts[1])
            if name in palettes:
                raise SafetyError('Reviewed palette declaration is ambiguous')
            palettes[name] = visit(parts[3], ())
    if set(palettes) != {b'light-theme', b'dark-theme'}:
        raise SafetyError('Reviewed palette declarations are incomplete')
    return palettes[b'light-theme'] & palettes[b'dark-theme']


def _palette_field(tree, index):
    names = []
    while tree.nodes[index].kind == 'FieldAccess':
        parts = import_graph.significant(tree, index)
        if (len(parts) != 3 or tree.nodes[parts[1]].kind != 'Dot'
                or tree.nodes[parts[2]].kind != 'Ident'):
            return None
        names.append(import_graph.raw(tree, parts[2]).decode('utf-8'))
        index = parts[0]
    if tree.nodes[index].kind != 'Ident' or import_graph.raw(tree, index) != b'theme' or not names:
        return None
    return tuple(reversed(names))


class _ThemeNamespace(binding._NamespaceAnalyzer):
    def incoming(self, tree, index, path, active):
        result = super().incoming(tree, index, path, active)
        shape = import_graph.import_shape(tree, index)
        combined = conditional._combined_import(tree, index) if shape is None else None
        if combined is not None:
            shape = combined[0]
        if result.reason or shape is None or shape.request != MATH_BLOCKS:
            return result
        package = self.packages.get(MATH_BLOCKS)
        if package is None or digest(package.source) != MATH_BLOCKS_SOURCE_SHA256:
            return result
        items = ((_THEME, _THEME),) if shape.items is None else shape.items
        exports = dict(result.exports)
        for original, local in items:
            if original == _THEME and local in exports:
                exports[local] = conditional._Value((), _THEME)
        return replace(result, exports=tuple(sorted(exports.items())))


def supports_theme_lets(evidence, path, candidate, closure_start, closure_end, let_spans):
    """Reprove exact selector calls and lexical origin from one captured graph."""
    resources = footnote_evidence._valid(evidence)
    if (type(path) is not str or path not in evidence._session._graph.selected or type(candidate) is not bytes
            or type(closure_start) is not int or type(closure_end) is not int or type(let_spans) is not tuple
            or not let_spans or any(type(row) is not tuple or len(row) != 2
                                    or any(type(value) is not int for value in row) for row in let_spans)):
        raise SafetyError('Invalid selector proof request')
    wanted = set(let_spans)
    if len(wanted) != len(let_spans):
        raise SafetyError('Duplicate selector proof request')
    tree = parse(candidate)
    if tree.nodes[0].errors:
        raise SafetyError('Malformed candidate for selector proof')
    closures = [i for i, node in enumerate(tree.nodes) if node.kind == 'Closure'
                and (node.start, node.end) == (closure_start, closure_end)]
    if len(closures) != 1:
        raise SafetyError('Candidate callback identity is absent or ambiguous')
    pending = list(tree.nodes[closures[0]].children)
    descendants = set()
    while pending:
        index = pending.pop()
        descendants.add(index)
        pending.extend(tree.nodes[index].children)
    lets = [index for index in descendants if tree.nodes[index].kind == 'LetBinding']
    if {(tree.nodes[i].start, tree.nodes[i].end) for i in lets} != wanted:
        raise SafetyError('Candidate callback declarations changed')
    for index in descendants | {closures[0]}:
        node = tree.nodes[index]
        if node.kind in ('ModuleImport', 'ModuleInclude', 'DestructAssignment'):
            raise SafetyError('Selector callback has unsupported imports or mutation')
        if node.kind == 'Binary' and any(tree.nodes[child].kind in
                ('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq') for child in node.children):
            raise SafetyError('Selector callback contains an assignment')
        if node.kind == 'Params':
            for child in import_graph.significant(tree, index):
                part = tree.nodes[child]
                if (part.kind not in ('Ident', 'LeftParen', 'RightParen', 'Comma')
                        or (part.kind == 'Ident' and import_graph.raw(tree, child)
                            in (b'footnote', b'cetz', b'theme-from-text-fill', b'theme'))):
                    raise SafetyError('Callback parameter may shadow a selector or protected name')
    palette_lets = {}
    selector_count = 0
    for index in lets:
        parts = import_graph.significant(tree, index)
        if (len(parts) != 4 or tuple(tree.nodes[i].kind for i in parts[:3]) != ('Let', 'Ident', 'Eq')):
            raise SafetyError('Callback declaration is not a reviewed selector or palette field')
        target = import_graph.raw(tree, parts[1])
        if tree.nodes[parts[3]].kind == 'FieldAccess':
            field = _palette_field(tree, parts[3])
            if target in (b'theme', b'footnote', b'cetz', b'theme-from-text-fill') or field is None:
                raise SafetyError('Callback palette declaration has an unsupported target or field')
            palette_lets[index] = field
            continue
        if tree.nodes[parts[3]].kind != 'FuncCall' or target != b'theme':
            raise SafetyError('Callback declaration is not the reviewed selector form')
        call = import_graph.significant(tree, parts[3])
        if (len(call) != 2 or tree.nodes[call[0]].kind != 'Ident'
                or import_graph.raw(tree, call[0]) != _THEME.encode() or tree.nodes[call[1]].kind != 'Args'
                or tuple(tree.nodes[i].kind for i in import_graph.significant(tree, call[1]))
                   != ('LeftParen', 'RightParen')):
            raise SafetyError('Selector call is not the reviewed zero-argument form')
        selector_count += 1
    if not selector_count:
        raise SafetyError('Callback contains no reviewed selector declaration')
    math_evidence = _verify_selector(evidence, resources)
    session = evidence._session
    engine = _ThemeNamespace(session._graph, session._namespaces, (), False)
    engine.reason = facade_provenance._graph_reason(session._graph)
    engine.packages = {item.evidence.import_path: item for item in session._namespaces.packages}
    engine.evidence = session._namespaces.evidence

    class Observer(binding._FootnoteResolver):
        found = False
        def import_node(self, index, state):
            before = state.lookup(_THEME)
            shape = import_graph.import_shape(self.tree, index)
            super().import_node(index, state)
            if shape is None:
                return
            module = self.engine.incoming(self.tree, index, path, (path,))
            if module.reason:
                return
            exports = dict(module.exports)
            if _THEME in exports:
                value = exports[_THEME]
                if value is not None and value.canonical_name == _THEME:
                    for package in module.packages:
                        footnote_evidence._verify_package(session, resources, package)
                    state.bind(_THEME, Binding('api', _THEME, MATH_BLOCKS,
                                               math_evidence.source_identity, math_evidence.source_sha256))
                else:
                    state.bind(_THEME, None)
            elif shape.items is None:
                for package in module.packages:
                    footnote_evidence._verify_package(session, resources, package)
                state.bind(_THEME, before)

        def visit(self, index, state, *, output=False):
            if index == closures[0]:
                value = state.lookup(_THEME)
                self.found = bool(not state.deferred and value and value.kind == 'api'
                                  and value.name == _THEME and value.package == MATH_BLOCKS
                                  and value.source_sha256 == MATH_BLOCKS_SOURCE_SHA256)
            return super().visit(index, state, output=output)

    observer = Observer(tree, path, engine)
    observer.visit(0, observer.state)
    theme_uses = {}
    parents = {child: index for index, node in enumerate(tree.nodes) for child in node.children}
    for index in descendants:
        node = tree.nodes[index]
        if node.kind == 'MathIdent' and import_graph.raw(tree, index) == b'theme':
            raise SafetyError('Math-mode palette use is not covered by the lexical field proof')
        if node.kind != 'Ident' or import_graph.raw(tree, index) != b'theme':
            continue
        parent = parents.get(index)
        if parent in lets:
            parts = import_graph.significant(tree, parent)
            if len(parts) == 4 and parts[1] == index and tree.nodes[parts[3]].kind == 'FuncCall':
                continue
        if parent is None or tree.nodes[parent].kind != 'FieldAccess':
            raise SafetyError('Callback leaks or aliases the theme palette')
        top = parent
        while top in parents and tree.nodes[parents[top]].kind == 'FieldAccess':
            higher = parents[top]
            fields = import_graph.significant(tree, higher)
            if not fields or fields[0] != top:
                break
            top = higher
        field = _palette_field(tree, top)
        if field is None:
            raise SafetyError('Callback contains an unsupported palette access')
        theme_uses[top] = field

    if palette_lets or theme_uses:
        math = next(item for item in session._namespaces.packages if item.evidence.import_path == MATH_BLOCKS)
        allowed = _palette_paths(math.source)

        class PaletteValidator(_Resolver):
            def __init__(self):
                super().__init__(tree, None, ())
                self.state.bind(_THEME, Binding('api', _THEME, MATH_BLOCKS,
                                                math_evidence.source_identity, math_evidence.source_sha256))
                self.checked = set()
                self.checked_uses = set()

            def visit(self, index, state, *, output=False):
                if index in theme_uses:
                    value = state.lookup('theme')
                    if (not is_theme_binding(value) or value.kind != 'palette'
                            or value.name != 'resolved-theme' or theme_uses[index] not in allowed):
                        raise SafetyError('Palette access lacks an exact in-scope reviewed selector and scalar key')
                    self.checked_uses.add(index)
                return super().visit(index, state, output=output)

            def let_node(self, index, state):
                if index in palette_lets:
                    value = state.lookup('theme')
                    if (not is_theme_binding(value) or value.kind != 'palette'
                            or value.name != 'resolved-theme' or palette_lets[index] not in allowed):
                        raise SafetyError('Palette field lacks an exact in-scope reviewed selector and key')
                    self.checked.add(index)
                return super().let_node(index, state)

        validator = PaletteValidator()
        validator.visit(closures[0], validator.state)
        if validator.checked != set(palette_lets) or validator.checked_uses != set(theme_uses):
            raise SafetyError('Palette field lexical scope was not fully analyzed')
    footnote_evidence._valid(evidence)
    return bool(observer.found and not observer.unresolved and not engine.exhausted)
