"""Conditional builtin-footnote evidence after a captured facade wildcard.

This capability is diagnostic-only. Captures do not prove initialization,
runtime package selection, include execution, rendering, or permission to edit.
"""
from dataclasses import dataclass, field
import weakref

import conditional_highlight as conditional
import facade_provenance
import import_graph
import include_context
import namespace_multiroot
import namespace_packages
from static_resolver import _Resolver
from typst_lint import InputError, digest


CONDITIONS = (
    'Every captured facade and package initializes successfully.',
    'Runtime imports select the captured package roots, versions and bytes.',
    'Runtime relative imports select the captured facade paths and source bytes.',
    'This literal source call executes in the expected include context.',
)
CALLBACK_CONDITIONS = CONDITIONS + (
    'The literal closure executes with the footnote binding captured at its declaration.',
)
_CLOSURE_UNSAFE = frozenset(('Closure', 'Contextual', 'ShowRule', 'Conditional', 'ForLoop', 'WhileLoop',
                             'ModuleInclude', 'ModuleImport', 'DestructAssignment'))
_ASSIGNMENTS = frozenset(('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq'))


@dataclass(frozen=True)
class FootnoteBinding:
    path: str
    start: int
    end: int
    source_sha256: str
    facade_sources: tuple
    package_evidence: tuple
    package_requests: tuple
    conditions: tuple = CONDITIONS
    status: str = field(default='CONDITIONAL', init=False)
    edit_permission: bool = field(default=False, init=False)
    compiler_permission: bool = field(default=False, init=False)


@dataclass(frozen=True)
class BindingResult:
    status: str
    calls: tuple = ()
    unresolved: tuple = ()
    coverage_complete: bool = field(default=False, init=False)
    runtime_binding_proved: bool = field(default=False, init=False)
    edit_permission: bool = field(default=False, init=False)
    compiler_permission: bool = field(default=False, init=False)


@dataclass(frozen=True)
class BindingBatch:
    status: str
    results: tuple = ()
    unresolved: tuple = ()
    edit_permission: bool = field(default=False, init=False)
    compiler_permission: bool = field(default=False, init=False)


class _NamespaceAnalyzer(conditional._Analyzer):
    def incoming(self, tree, index, path, active):
        shape = import_graph.import_shape(tree, index)
        combined = conditional._combined_import(tree, index) if shape is None else None
        if combined is not None:
            shape, alias = combined
        else:
            alias = None
        module_alias = conditional._module_alias(tree, index) if shape is None else None
        request = shape.request if shape is not None else (module_alias[0] if module_alias else None)
        package = self.packages.get(request)
        if type(package) is not namespace_packages.CollisionOnlyNamespace:
            return super().incoming(tree, index, path, active)
        # This reviewed variant proves the *set of names*, not helper origins.
        if shape is None:
            return conditional._Module(((module_alias[1], None),), packages=(package.evidence,),
                                       requests=(request,))
        items = tuple((name, name) for name in package.exports) if shape.items is None else shape.items
        if any(name not in package.exports for name, _ in items):
            return conditional._Module(reason='Requested collision namespace export unavailable',
                                       intrinsic_refusal=True)
        exports = ([(alias, None)] if alias is not None else []) + [(local, None) for _, local in items]
        if not self.bindings(exports):
            return self.exhaust('Conditional namespace binding limit exceeded')
        return conditional._Module(tuple(exports), packages=(package.evidence,), requests=(request,))


_ISSUED = {}


def _graph_signature(graph):
    if facade_provenance._graph_reason(graph):
        raise InputError('Invalid captured import graph')
    return (graph.project_root, graph.selected,
            tuple((x.path, x.source) for x in graph.sources),
            tuple((x.importer, x.request, x.target) for x in graph.edges),
            tuple((x.path, x.request, x.reason) for x in graph.unavailable))


def _valid(session):
    try:
        issued = _ISSUED.get(id(session))
        return bool(type(session) is ConditionalFootnoteBinding and issued and issued[0]() is session
                    and issued[1] is session._context and issued[2] is session._namespaces
                    and issued[3] == _graph_signature(session._graph)
                    and include_context.assert_include_context(session._context)
                    and namespace_multiroot.verify_multiroot_capture(session._namespaces))
    except Exception:
        return False


class _FootnoteResolver(_Resolver):
    def __init__(self, tree, path, engine):
        super().__init__(tree, None, ())
        self.path = path
        self.engine = engine
        self.evidence = {}
        self.call_evidence = {}
        self.closure_calls = set()
        self.closure_depth = 0
        self.unresolved = []

    def _literal_closure(self, index, state):
        """Identify one non-dynamic literal callback with a proven capture."""
        if state.deferred or self.closure_depth:
            return None
        previous = state.lookup('footnote')
        preceding = tuple(proof for frame in state.frames for proof in self.evidence.get(frame, ()))
        if not (preceding and previous and previous.kind == 'builtin' and previous.name == 'footnote'):
            return None
        parts = self.sig(index)
        if (len(parts) != 3 or tuple(self.nodes[i].kind for i in parts[:2]) != ('Params', 'Arrow')):
            return None
        body = parts[2]
        if self.nodes[body].kind == 'Parenthesized':
            grouped = self.sig(body)
            if (len(grouped) != 3 or tuple(self.nodes[i].kind for i in grouped)
                    != ('LeftParen', 'ContentBlock', 'RightParen')):
                return None
            body = grouped[1]
        if self.nodes[body].kind != 'ContentBlock':
            return None
        params = self.sig(parts[0])
        if any(self.nodes[i].kind not in ('Ident', 'LeftParen', 'RightParen', 'Comma') for i in params):
            return None
        names = [self.text(i) for i in params if self.nodes[i].kind == 'Ident']
        if 'footnote' in names or len(names) != len(set(names)):
            return None
        pending = [body]
        while pending:
            item = self.nodes[pending.pop()]
            if item.kind in _CLOSURE_UNSAFE or (item.kind == 'Binary' and any(
                    self.nodes[child].kind in _ASSIGNMENTS for child in item.children)):
                return None
            pending.extend(item.children)
        return body, names, previous, preceding

    def import_node(self, index, state):
        shape = import_graph.import_shape(self.tree, index)
        before = state.lookup('footnote')
        # Let the existing resolver handle *all* other names and import effects.
        super().import_node(index, state)
        if shape is None or shape.items is not None:
            return
        if shape.request.startswith('@'):
            if shape.request not in self.engine.packages:
                self.unresolved.append('Wildcard package namespace unavailable')
                return
            module = self.engine.incoming(self.tree, index, self.path, (self.path,))
        else:
            target = self.engine.graph.target_for(self.path, shape.request)
            if target is None:
                self.unresolved.append('Relative wildcard source unavailable')
                return
            module = self.engine.module(target, (self.path,))
        if module.reason or self.engine.exhausted:
            self.unresolved.append(module.reason or self.engine.exhausted)
            return
        if 'footnote' in dict(module.exports):
            self.unresolved.append('Wildcard namespace exports footnote')
            return
        # A wildcard lacking the name cannot restore an earlier shadow.
        state.bind('footnote', before)
        self.evidence.setdefault(state.frames[-1], []).append((module.sources, module.packages, module.requests))

    def visit(self, index, state, *, output=False):
        if self.nodes[index].kind == 'Closure':
            literal = self._literal_closure(index, state)
            if literal is not None:
                body, names, previous, preceding = literal
                self.record(index, state)
                # The normal resolver discards all captures in deferred bodies.
                # Retain only the independently proved immutable builtin; never
                # share the parent's mutable lexical frames with this callback.
                nested = state.nested(deferred=True)
                nested.deferred = False
                nested.bind('footnote', previous)
                for name in names:
                    nested.bind(name, None)
                self.evidence[nested.frames[0]] = list(preceding)
                self.closure_depth += 1
                try:
                    self.visit(body, nested)
                finally:
                    self.closure_depth -= 1
                return None
        preceding = tuple(proof for frame in state.frames for proof in self.evidence.get(frame, ()))
        value = super().visit(index, state, output=output)
        node = self.nodes[index]
        fact = self.facts[index]
        if (preceding and node.kind == 'FuncCall' and not fact.deferred
                and fact.binding and fact.binding.kind == 'builtin' and fact.binding.name == 'footnote'):
            parts = self.sig(index)
            if (len(parts) == 2 and self.nodes[parts[0]].kind == 'Ident'
                    and self.text(parts[0]) == 'footnote' and node.start > 0
                    and self.tree.source[node.start - 1:node.start] == b'#'):
                self.call_evidence[index] = preceding
                if self.closure_depth:
                    self.closure_calls.add(index)
        return value


class ConditionalFootnoteBinding:
    """Issue a graph from an issued include capture and sealed multi-root data."""
    def __init__(self, context, namespaces):
        include_context.assert_include_context(context)
        if not namespace_multiroot.verify_multiroot_capture(namespaces):
            raise InputError('A locally issued multi-root namespace capture is required')
        selected = tuple((item.path, item.source) for item in context.sources)
        graph = import_graph.load_import_graph(context.project_root, selected)
        include_context.assert_include_context(context)
        if not namespace_multiroot.verify_multiroot_capture(namespaces):
            raise InputError('Namespace capture changed during graph capture')
        self._context, self._namespaces, self._graph = context, namespaces, graph
        signature = _graph_signature(graph)
        identity = id(self)
        def release(reference):
            item = _ISSUED.get(identity)
            if item is not None and item[0] is reference:
                _ISSUED.pop(identity, None)
        _ISSUED[identity] = (weakref.ref(self, release), context, namespaces, signature)

    def analyze(self, path):
        if not _valid(self):
            return BindingResult('UNAVAILABLE', unresolved=('Capture changed or is unissued',))
        try:
            result = ConditionalFootnoteBinding._analyze(self, path)
        except Exception:
            if not _valid(self):
                return BindingResult('UNAVAILABLE', unresolved=('Capture changed during inspection',))
            raise
        if not _valid(self):
            return BindingResult('UNAVAILABLE', unresolved=('Capture changed during inspection',))
        return result

    def analyze_all(self, *, max_items):
        """Inspect the issued source set as a batch with two capture checks.

        Intermediate results are never exposed if any retained evidence changed.
        This batches verification only; each source still gets an independent
        namespace analyzer and its ordinary bounded lexical analysis.
        """
        if type(max_items) is not int or max_items < 0:
            raise InputError('Batch item limit must be a nonnegative integer')
        if not _valid(self):
            return BindingBatch('UNAVAILABLE', unresolved=('Capture changed or is unissued',))
        try:
            results = []
            item_count = 0
            for source in self._context.sources:
                result = ConditionalFootnoteBinding._analyze(self, source.path)
                item_count += 1 + len(result.unresolved)
                item_count += sum(1 + len(call.facade_sources) + len(call.package_evidence)
                                  + len(call.package_requests) + len(call.conditions)
                                  for call in result.calls)
                if item_count > max_items:
                    raise InputError('Document inspection report item limit exceeded')
                results.append((source.path, result))
            results = tuple(results)
        except Exception:
            if not _valid(self):
                return BindingBatch('UNAVAILABLE', unresolved=('Capture changed during inspection',))
            raise
        if not _valid(self):
            return BindingBatch('UNAVAILABLE', unresolved=('Capture changed during inspection',))
        return BindingBatch('PARTIAL' if any(result.status != 'CONDITIONAL' for _, result in results)
                            else 'CONDITIONAL', results)

    def _analyze(self, path):
        if type(path) is not str or path not in self._graph.selected:
            return BindingResult('UNAVAILABLE', unresolved=('Source is not selected',))
        source = next((item for item in self._context.sources if item.path == path), None)
        item = self._graph.source_for(path)
        if source is None or item is None or source.source != item.source or source.tree.nodes[0].errors:
            return BindingResult('UNAVAILABLE', unresolved=('Captured source is unavailable or malformed',))
        engine = _NamespaceAnalyzer(self._graph, self._namespaces, (), False)
        engine.reason = facade_provenance._graph_reason(self._graph)
        engine.packages = {item.evidence.import_path: item for item in self._namespaces.packages}
        engine.evidence = self._namespaces.evidence
        resolver = _FootnoteResolver(source.tree, path, engine)
        resolver.visit(0, resolver.state)
        calls = []
        for index, evidence in sorted(resolver.call_evidence.items(), key=lambda pair: source.tree.nodes[pair[0]].start):
            node = source.tree.nodes[index]
            sources = {item.path: item for entries in evidence for item in entries[0]}
            packages = {item.import_path: item for entries in evidence for item in entries[1]}
            requests = {item for entries in evidence for item in entries[2]}
            calls.append(FootnoteBinding(path, node.start - 1, node.end, digest(source.source),
                tuple(sources[key] for key in sorted(sources)),
                tuple(packages[key] for key in sorted(packages)), tuple(sorted(requests)),
                CALLBACK_CONDITIONS if index in resolver.closure_calls else CONDITIONS))
        unresolved = tuple(dict.fromkeys(resolver.unresolved))
        return BindingResult('PARTIAL' if unresolved else 'CONDITIONAL', tuple(calls), unresolved)
