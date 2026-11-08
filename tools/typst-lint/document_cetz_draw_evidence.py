"""Conditional CeTZ draw wildcard namespace proof for private preview only."""
import conditional_footnote_binding as binding
import document_footnote_evidence as footnote_evidence
import facade_provenance
import import_graph
from package_artifacts import CETZ, CETZ_ARTIFACTS
from static_resolver import Binding
from syntax_client import parse
from typst_lint import SafetyError, digest


def _draw_exports(evidence):
    """Derive names from the exact pinned draw file and captured dependencies."""
    resources = footnote_evidence._valid(evidence)
    session = evidence._session
    package = next((item for item in session._namespaces.packages
                    if item.evidence.import_path == CETZ), None)
    if package is None:
        raise SafetyError('Reviewed CeTZ namespace is unavailable')
    footnote_evidence._verify_package(session, resources, package.evidence)
    choices = {item.import_path: item.root_index for item in session._namespaces.selections}
    index = choices.get(CETZ)
    if index not in (0, 1):
        raise SafetyError('CeTZ package selection is unavailable')
    root = ('packages', 'cache')[index]
    prefix = 'preview/cetz/0.4.2/'
    raw = resources.get((root, prefix + 'src/draw.typ'))
    if raw is None or digest(raw) != dict(CETZ_ARTIFACTS)['src/draw.typ']:
        raise SafetyError('Captured CeTZ draw namespace differs from the reviewed artifact')
    tree = parse(raw)
    if tree.nodes[0].errors:
        raise SafetyError('CeTZ draw namespace syntax is malformed')
    exports = set()
    for index in tree.nodes[0].children:
        node = tree.nodes[index]
        if node.kind in import_graph._TRIVIA or node.kind in ('Hash', 'Semicolon'):
            continue
        if node.kind != 'ModuleImport':
            raise SafetyError('CeTZ draw namespace contains an unsupported declaration')
        shape = import_graph.import_shape(tree, index)
        if shape is None or shape.items is None or shape.request.startswith('@'):
            raise SafetyError('CeTZ draw namespace is not a complete named import list')
        relative = import_graph.normalize_relative('src/draw.typ', shape.request)
        dependency = resources.get((root, prefix + relative))
        if dependency is None or parse(dependency).nodes[0].errors:
            raise SafetyError('CeTZ draw dependency is absent or malformed in the preview capture')
        exports.update(local for _, local in shape.items)
    if not exports or 'footnote' in exports or 'cetz' in exports:
        raise SafetyError('CeTZ draw namespace may shadow a required lexical name')
    footnote_evidence._valid(evidence)
    return frozenset(exports), package.evidence


def supports_imports(evidence, path, candidate, closure_start, closure_end, import_spans):
    """Reparse one candidate and prove exact captured cetz.draw wildcards.

    A positive result is conditional on actual module initialization and callback
    execution. It never grants a source edit or compilation right.
    """
    resources = footnote_evidence._valid(evidence)
    if (type(path) is not str or path not in evidence._session._graph.selected or type(candidate) is not bytes
            or type(closure_start) is not int or type(closure_end) is not int
            or type(import_spans) is not tuple or not import_spans):
        raise SafetyError('Invalid CeTZ draw proof request')
    tree = parse(candidate)
    if tree.nodes[0].errors:
        raise SafetyError('Malformed candidate for CeTZ draw evidence')
    closures = [i for i, node in enumerate(tree.nodes) if node.kind == 'Closure'
                and (node.start, node.end) == (closure_start, closure_end)]
    if len(closures) != 1:
        raise SafetyError('Candidate callback identity is absent or ambiguous')
    if any(type(row) is not tuple or len(row) != 2
           or any(type(value) is not int for value in row) for row in import_spans):
        raise SafetyError('Invalid CeTZ draw import selection')
    wanted = set(import_spans)
    if len(wanted) != len(import_spans):
        raise SafetyError('Invalid CeTZ draw import selection')
    descendants, pending = set(), list(tree.nodes[closures[0]].children)
    while pending:
        index = pending.pop()
        descendants.add(index)
        pending.extend(tree.nodes[index].children)
    for index in descendants | {closures[0]}:
        node = tree.nodes[index]
        if node.kind in ('LetBinding', 'ModuleInclude', 'DestructAssignment'):
            raise SafetyError('Callback contains an unsupported lexical mutation')
        if node.kind == 'Binary' and any(tree.nodes[child].kind in
                ('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq') for child in node.children):
            raise SafetyError('Callback contains an unsupported assignment')
        if node.kind == 'Params':
            for child in import_graph.significant(tree, index):
                parameter = tree.nodes[child]
                if (parameter.kind not in ('Ident', 'LeftParen', 'RightParen', 'Comma')
                        or (parameter.kind == 'Ident' and import_graph.raw(tree, child) in (b'cetz', b'footnote'))):
                    raise SafetyError('Callback parameter may shadow a required lexical name')
    imports = [i for i in descendants if tree.nodes[i].kind == 'ModuleImport']
    if {(tree.nodes[i].start, tree.nodes[i].end) for i in imports} != wanted:
        raise SafetyError('Candidate callback imports changed')
    for index in imports:
        parts = import_graph.significant(tree, index)
        if (len(parts) != 4 or tuple(tree.nodes[i].kind for i in parts)
                != ('Import', 'FieldAccess', 'Colon', 'Star')):
            raise SafetyError('Callback import is not an exact CeTZ draw wildcard')
        fields = import_graph.significant(tree, parts[1])
        if (len(fields) != 3 or tuple(tree.nodes[i].kind for i in fields) != ('Ident', 'Dot', 'Ident')
                or tuple(import_graph.raw(tree, i) for i in fields) != (b'cetz', b'.', b'draw')):
            raise SafetyError('Callback import receiver is not the captured CeTZ module')
    _draw_exports(evidence)
    session = evidence._session
    engine = binding._NamespaceAnalyzer(session._graph, session._namespaces, (), False)
    engine.reason = facade_provenance._graph_reason(session._graph)
    engine.packages = {item.evidence.import_path: item for item in session._namespaces.packages}
    engine.evidence = session._namespaces.evidence

    class Observer(binding._FootnoteResolver):
        found = False
        def import_node(self, index, state):
            before = state.lookup('cetz')
            parts = import_graph.significant(self.tree, index)
            shape = import_graph.import_shape(self.tree, index)
            super().import_node(index, state)
            if (len(parts) == 2 and import_graph.import_request(self.tree, index) == CETZ
                    and self.nodes[parts[1]].kind == 'Str'):
                state.bind('cetz', Binding('module', 'cetz', CETZ, package.evidence.source_identity,
                                           package.evidence.source_sha256))
                return
            if shape is None or shape.items is not None:
                return
            if shape.request.startswith('@'):
                if shape.request not in self.engine.packages:
                    return
                module = self.engine.incoming(self.tree, index, path, (path,))
            else:
                target = self.engine.graph.target_for(path, shape.request)
                if target is None:
                    return
                module = self.engine.module(target, (path,))
            if not module.reason and 'cetz' not in dict(module.exports):
                for package_evidence in module.packages:
                    footnote_evidence._verify_package(session, resources, package_evidence)
                state.bind('cetz', before)

        def visit(self, index, state, *, output=False):
            if index == closures[0]:
                value = state.lookup('cetz')
                self.found = bool(not state.deferred and value and value.kind == 'module'
                                  and value.name == 'cetz' and value.package == CETZ
                                  and value.source_sha256 == package.evidence.source_sha256)
            return super().visit(index, state, output=output)

    package = next(item for item in session._namespaces.packages if item.evidence.import_path == CETZ)
    observer = Observer(tree, path, engine)
    observer.visit(0, observer.state)
    footnote_evidence._valid(evidence)
    return bool(observer.found and not observer.unresolved and not engine.exhausted)
