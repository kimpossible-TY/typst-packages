"""Conditional CeTZ draw wildcard namespace proof for private preview only."""
import json
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


def _check_named_callback(tree, closure, import_spans):
    descendants, pending = set(), list(tree.nodes[closure].children)
    while pending:
        index = pending.pop()
        descendants.add(index)
        pending.extend(tree.nodes[index].children)
    for index in descendants | {closure}:
        node = tree.nodes[index]
        if node.kind in ('ModuleInclude', 'DestructAssignment', 'ForLoop', 'WhileLoop'):
            raise SafetyError('Callback contains an unsupported lexical mutation')
        if node.kind == 'LetBinding':
            parts = import_graph.significant(tree, index)
            if (len(parts) != 4 or tree.nodes[parts[1]].kind != 'Ident'
                    or import_graph.raw(tree, parts[1]) in (b'draw', b'footnote')
                    or tree.nodes[parts[2]].kind != 'Eq'):
                raise SafetyError('Callback declaration may shadow a required lexical name')
        if node.kind == 'Binary' and any(tree.nodes[child].kind in
                ('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq') for child in node.children):
            raise SafetyError('Callback contains an unsupported assignment')
        if node.kind == 'Params':
            for child in import_graph.significant(tree, index):
                parameter = tree.nodes[child]
                if (parameter.kind not in ('Ident', 'LeftParen', 'RightParen', 'Comma')
                        or (parameter.kind == 'Ident' and import_graph.raw(tree, child) in (b'draw', b'footnote'))):
                    raise SafetyError('Callback parameter may shadow a required lexical name')
    imports = [i for i in descendants if tree.nodes[i].kind == 'ModuleImport']
    if {(tree.nodes[i].start, tree.nodes[i].end) for i in imports} != set(import_spans):
        raise SafetyError('Captured callback imports differ from the selection')
    for index in imports:
        parts = import_graph.significant(tree, index)
        if (tuple(tree.nodes[i].kind for i in parts) != ('Import', 'Ident', 'Colon', 'Star')
                or import_graph.raw(tree, parts[1]) != b'draw'):
            raise SafetyError('Callback import is not an exact named draw wildcard')


def _named_observer(tree, path, session, resources, package, closures, engine=None):
    if engine is None:
        engine = binding._NamespaceAnalyzer(session._graph, session._namespaces, (), False)
        engine.reason = facade_provenance._graph_reason(session._graph)
        engine.packages = {item.evidence.import_path: item for item in session._namespaces.packages}
        engine.evidence = session._namespaces.evidence
    expected = Binding('module', 'cetz.draw', CETZ, package.source_identity,
                       dict(CETZ_ARTIFACTS)['src/draw.typ'])

    class Observer(binding._FootnoteResolver):
        found = set()

        def import_node(self, index, state):
            before = state.lookup('draw')
            shape = import_graph.import_shape(self.tree, index)
            super().import_node(index, state)
            if shape is None:
                return
            if shape.request == CETZ and shape.items is not None:
                if (shape.items.count(('draw', 'draw')) == 1
                        and sum(local == 'draw' for _, local in shape.items) == 1):
                    state.bind('draw', expected)
                return
            if shape.items is not None:
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
            if not module.reason and 'draw' not in dict(module.exports):
                for package_evidence in module.packages:
                    footnote_evidence._verify_package(session, resources, package_evidence)
                state.bind('draw', before)

        def visit(self, index, state, *, output=False):
            if index in closures and not state.deferred and state.lookup('draw') == expected:
                self.found.add(index)
            return super().visit(index, state, output=output)

    observer = Observer(tree, path, engine)
    observer.visit(0, observer.state)
    return observer, engine


def supports_named_draw_imports(evidence, path, closure_start, closure_end, import_spans):
    """Prove one unchanged captured callback; this never grants execution or edits."""
    resources = footnote_evidence._valid(evidence)
    session = evidence._session
    if (type(path) is not str or type(closure_start) is not int or type(closure_end) is not int
            or type(import_spans) is not tuple or not import_spans
            or any(type(row) is not tuple or len(row) != 2
                   or any(type(value) is not int for value in row) for row in import_spans)
            or len(set(import_spans)) != len(import_spans)):
        raise SafetyError('Invalid named CeTZ draw proof request')
    source = session._graph.source_for(path)
    if source is None or resources.get(('project', path)) != source.source:
        raise SafetyError('Named CeTZ draw source is not in the captured import graph')
    tree = parse(source.source)
    if tree.nodes[0].errors:
        raise SafetyError('Malformed captured source for named CeTZ draw evidence')
    closures = [i for i, node in enumerate(tree.nodes) if node.kind == 'Closure'
                and (node.start, node.end) == (closure_start, closure_end)]
    if len(closures) != 1:
        raise SafetyError('Captured callback identity is absent or ambiguous')
    _check_named_callback(tree, closures[0], import_spans)
    exports, package = _draw_exports(evidence)
    if 'draw' in exports:
        raise SafetyError('CeTZ draw namespace may shadow its receiver')
    observer, engine = _named_observer(tree, path, session, resources, package, set(closures))
    footnote_evidence._valid(evidence)
    return bool(closures[0] in observer.found and not observer.unresolved and not engine.exhausted)


def analyze_named_draw_batch(evidence, requests):
    """Inspect explicitly selected callbacks in captured sources without mutation.

    Each request is ``(path, closure_start, closure_end, ((start, end), ...))``.
    Every location is resolved against the issued snapshot; malformed or
    duplicated selections fail the entire batch. Unsupported callback syntax
    is returned as UNRESOLVED, never as a negative finding or write authority.
    """
    resources = footnote_evidence._valid(evidence)
    session = evidence._session
    if type(requests) is not tuple or not requests or len(requests) > 512:
        raise SafetyError('Named CeTZ draw batch request count exceeds its budget')
    seen, seen_imports, paths = set(), set(), {}
    span_count = path_bytes = 0
    for request in requests:
        if type(request) is not tuple or len(request) != 4:
            raise SafetyError('Malformed named CeTZ draw batch request')
        path, start, end, spans = request
        if (type(path) is not str or len(path) > 65536 or type(start) is not int or type(end) is not int
                or start < 0 or end <= start or type(spans) is not tuple or not spans
                or len(spans) > 4096 - span_count
                or any(type(row) is not tuple or len(row) != 2
                       or any(type(value) is not int for value in row)
                       or row[0] < start or row[1] > end or row[1] <= row[0] for row in spans)
                or len(set(spans)) != len(spans)):
            raise SafetyError('Malformed named CeTZ draw batch request')
        key = (path, start, end)
        if key in seen or any((path, *span) in seen_imports for span in spans):
            raise SafetyError('Duplicate named CeTZ draw batch selection')
        seen.add(key)
        seen_imports.update((path, *span) for span in spans)
        span_count += len(spans)
        try:
            path_bytes += len(path.encode('utf-8'))
        except UnicodeEncodeError as error:
            raise SafetyError('Invalid named CeTZ draw batch path') from error
        if span_count > 4096 or path_bytes > 65536:
            raise SafetyError('Named CeTZ draw batch selection exceeds its budget')
        paths.setdefault(path, []).append((start, end, spans))
    exports, package = _draw_exports(evidence)
    if 'draw' in exports:
        raise SafetyError('CeTZ draw namespace may shadow its receiver')
    results = {}
    work = 0
    engine = None
    for path, selected in paths.items():
        source = session._graph.source_for(path)
        if source is None or resources.get(('project', path)) != source.source:
            raise SafetyError('Named CeTZ draw source is not in the captured import graph')
        tree = parse(source.source)
        if tree.nodes[0].errors:
            raise SafetyError('Malformed captured source for named CeTZ draw evidence')
        work += len(tree.nodes)
        if work > 4_000_000:
            raise SafetyError('Named CeTZ draw batch source work exceeds its budget')
        closure_ids = {}
        for i, node in enumerate(tree.nodes):
            if node.kind == 'Closure':
                key = (node.start, node.end)
                if key in closure_ids:
                    raise SafetyError('Captured callback identity is ambiguous')
                closure_ids[key] = i
        selected_ids = set()
        for start, end, spans in selected:
            index = closure_ids.get((start, end))
            if index is None:
                raise SafetyError('Captured callback identity is absent or ambiguous')
            # Bound repeated subtree walks independently of the result size.
            # One whole source is a conservative upper bound for each walk.
            work += len(tree.nodes)
            if work > 4_000_000:
                raise SafetyError('Named CeTZ draw batch callback work exceeds its budget')
            try:
                _check_named_callback(tree, index, spans)
                selected_ids.add(index)
                reason = None
            except SafetyError as error:
                reason = str(error)
            results[(path, start, end)] = dict(path=path, closure_start=start, closure_end=end,
                import_spans=[list(span) for span in spans], source_sha256=source.sha256,
                status='UNRESOLVED', reason=reason)
        if selected_ids:
            observer, engine = _named_observer(tree, path, session, resources, package, selected_ids, engine)
            if engine.exhausted:
                raise SafetyError('Named CeTZ draw batch namespace work exceeds its budget')
            for start, end, spans in selected:
                row = results[(path, start, end)]
                if row['reason'] is not None:
                    continue
                index = closure_ids[(start, end)]
                if observer.unresolved or engine.exhausted:
                    row['reason'] = 'Other wildcard namespace or analysis budget remains unresolved'
                elif index not in observer.found:
                    row['reason'] = 'Named draw binding at callback declaration is not proven'
                else:
                    row.update(status='CONDITIONAL', reason=None)
    report = dict(status='CONDITIONAL' if all(row['status'] == 'CONDITIONAL' for row in results.values())
                  else 'INCOMPLETE', results=[results[(path, start, end)] for path, start, end, _ in requests],
                  conditions=['Captured CeTZ package and source remain the ones staged',
                              'Callback execution and module initialization are not established'],
                  runtime_binding_proved=False, edit_permission=False, compiler_permission=False,
                  other_transitive_imports_proved=False)
    if len(json.dumps(report, ensure_ascii=False).encode('utf-8')) > 1_048_576:
        raise SafetyError('Named CeTZ draw batch result exceeds its byte budget')
    footnote_evidence._valid(evidence)
    return report
