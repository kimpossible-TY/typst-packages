"""Conditional lexical evidence for selected calls in captured callbacks.

This inspects the exact issued source tree and package/resource capture. It
does not establish that a callback runs, execute imports, authorize an edit,
or prove a compiled/rendered result. Unsupported lexical paths are withheld.
"""
from dataclasses import dataclass, field

import conditional_footnote_binding as binding
import document_footnote_evidence as evidence
import facade_provenance
import import_graph
from syntax_client import MAX_INPUT, parse
from typst_lint import InputError, SafetyError, digest

MAX_CALLS = 1024
MAX_NODES = 100_000
_PATH_BARRIERS = frozenset(('Contextual', 'ShowRule', 'Conditional', 'ForLoop', 'WhileLoop',
                            'ModuleInclude', 'ModuleImport', 'DestructAssignment'))
_ENCLOSING_BARRIERS = frozenset(('Closure', 'Contextual', 'ShowRule', 'Conditional', 'ForLoop', 'WhileLoop'))


@dataclass(frozen=True)
class DeferredCallResult:
    path: str
    source_sha256: str
    calls: tuple = ()
    unresolved: tuple = ()
    status: str = 'INCOMPLETE'
    runtime_binding_proved: bool = field(default=False, init=False)
    execution_proved: bool = field(default=False, init=False)
    edit_permission: bool = field(default=False, init=False)
    compiler_permission: bool = field(default=False, init=False)


def analyze(proof, path, offsets, *, candidate=None):
    """Analyze selected ``#footnote`` calls against the issued source graph.

    Each offset points to the leading ``#``. Only an immediate, simple
    callback whose captured builtin is proven at declaration is inspected;
    nested closures and dynamic control flow on a selected call's path are
    left unresolved. Candidate bytes are reparsed but are not independently
    authorized: a consumer must rederive them from its issued plan and recheck
    the final source and every captured facade/package route. The result is
    conditional and non-authoritative.
    """
    resources = evidence._valid(proof)
    session = proof._session
    if (type(path) is not str or type(offsets) is not tuple or not offsets or len(offsets) > MAX_CALLS
            or any(type(offset) is not int or offset < 0 for offset in offsets)
            or len(set(offsets)) != len(offsets)):
        raise InputError('Invalid deferred footnote call selection')
    source = session._context.source_for(path)
    if source is None or resources.get(('project', path)) != source.source:
        raise SafetyError('Deferred footnote source is absent from the captured context')
    if candidate is None:
        tree = source.tree
    else:
        if type(candidate) is not bytes or len(candidate) > MAX_INPUT:
            raise InputError('Invalid deferred footnote candidate bytes')
        tree = source.tree if candidate == source.source else parse(candidate)
    if tree.nodes[0].errors or len(tree.nodes) > MAX_NODES:
        raise SafetyError('Deferred footnote source exceeds its syntax bound')
    parents = {child: index for index, node in enumerate(tree.nodes) for child in node.children}
    by_start = {}
    for index, node in enumerate(tree.nodes):
        if node.kind != 'FuncCall' or node.start <= 0 or tree.source[node.start - 1:node.start] != b'#':
            continue
        parts = import_graph.significant(tree, index)
        if (len(parts) == 2 and tree.nodes[parts[0]].kind == 'Ident'
                and import_graph.raw(tree, parts[0]) == b'footnote'):
            by_start.setdefault(node.start - 1, []).append(index)
    wanted, rejected = {}, {}
    for offset in offsets:
        matches = by_start.get(offset, ())
        if len(matches) != 1:
            raise InputError('Deferred footnote selection is absent or ambiguous')
        call = matches[0]
        current, barriers = call, []
        while current in parents:
            current = parents[current]
            kind = tree.nodes[current].kind
            if kind == 'Closure':
                break
            if kind in _PATH_BARRIERS:
                barriers.append(kind)
        if tree.nodes[current].kind != 'Closure':
            rejected[offset] = 'Call is not inside a supported callback'
        elif barriers:
            rejected[offset] = 'Call path contains unsupported dynamic control or import'
        else:
            closure = current
            while current in parents:
                current = parents[current]
                if tree.nodes[current].kind in _ENCLOSING_BARRIERS:
                    barriers.append(tree.nodes[current].kind)
            if barriers:
                rejected[offset] = 'Callback declaration is inside dynamic or repeated execution'
            else:
                wanted[offset] = (call, closure)

    engine = binding._NamespaceAnalyzer(session._graph, session._namespaces, (), False)
    engine.reason = facade_provenance._graph_reason(session._graph)
    engine.packages = {item.evidence.import_path: item for item in session._namespaces.packages}
    engine.evidence = session._namespaces.evidence
    targets = {closure for _, closure in wanted.values()}

    class Observer(binding._FootnoteResolver):
        def __init__(self):
            super().__init__(tree, path, engine)
            self.entered = set()

        def visit(self, index, state, *, output=False):
            if index not in targets:
                return super().visit(index, state, output=output)
            parts = self.sig(index)
            if (state.deferred or self.closure_depth or len(parts) != 3
                    or tuple(self.nodes[x].kind for x in parts[:2]) != ('Params', 'Arrow')):
                return super().visit(index, state, output=output)
            body = parts[2]
            if self.nodes[body].kind == 'Parenthesized':
                grouped = self.sig(body)
                if (len(grouped) != 3 or tuple(self.nodes[x].kind for x in grouped)
                        != ('LeftParen', 'ContentBlock', 'RightParen')):
                    return super().visit(index, state, output=output)
                body = grouped[1]
            if self.nodes[body].kind != 'ContentBlock':
                return super().visit(index, state, output=output)
            params = self.sig(parts[0])
            if any(self.nodes[x].kind not in ('Ident', 'LeftParen', 'RightParen', 'Comma') for x in params):
                return super().visit(index, state, output=output)
            names = [self.text(x) for x in params if self.nodes[x].kind == 'Ident']
            previous = state.lookup('footnote')
            preceding = tuple(item for frame in state.frames for item in self.evidence.get(frame, ()))
            if (len(names) != len(set(names)) or 'footnote' in names or not preceding
                    or previous is None or previous.kind != 'builtin' or previous.name != 'footnote'):
                return super().visit(index, state, output=output)
            self.entered.add(index)
            self.record(index, state)
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

    observer = Observer()
    observer.visit(0, observer.state)
    calls = []
    for offset in offsets:
        if offset in rejected:
            continue
        call, closure = wanted[offset]
        captured = observer.call_evidence.get(call)
        if closure not in observer.entered or not captured or engine.exhausted:
            rejected[offset] = 'Callback capture or lexical footnote binding is unproved'
            continue
        sources = {item.path: item for entries in captured for item in entries[0]}
        packages = {item.import_path: item for entries in captured for item in entries[1]}
        requests = {item for entries in captured for item in entries[2]}
        for package in packages.values():
            evidence._verify_package(session, resources, package)
        calls.append(binding.FootnoteBinding(path, offset, tree.nodes[call].end, digest(tree.source),
            tuple(sources[key] for key in sorted(sources)),
            tuple(packages[key] for key in sorted(packages)), tuple(sorted(requests)),
            binding.CALLBACK_CONDITIONS))
    evidence._valid(proof)
    return DeferredCallResult(path, digest(tree.source), tuple(calls),
        tuple((offset, rejected[offset]) for offset in offsets if offset in rejected),
        'CONDITIONAL' if not rejected else 'INCOMPLETE')
