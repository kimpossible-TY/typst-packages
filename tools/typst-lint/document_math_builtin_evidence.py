"""Conditional math-call evidence for source-only highlight investigation.

Only a small set of Typst 0.15.1 built-in math elements is considered. This is a bounded
static finding; runtime execution, package selection, source edits and
render equivalence are not established by this module.
"""
import json

import conditional_footnote_binding as binding
import facade_provenance
import import_graph
from static_resolver import Binding, _Resolver
from typst_lint import InputError

_BUILTINS = frozenset(('cal', 'dot', 'frac', 'frak', 'overline', 'tilde', 'op', 'bb', 'hat'))
MAX_ITEMS = 100_000
MAX_BYTES = 16 * 1024 * 1024
_FORBIDDEN_ANCESTORS = frozenset(('Closure', 'Contextual', 'ShowRule', 'Conditional', 'ForLoop', 'WhileLoop'))
_FORBIDDEN_MATH = frozenset(('FuncCall', 'CodeBlock', 'Hash', 'Contextual', 'LetBinding', 'ShowRule',
                            'ModuleImport', 'ModuleInclude', 'Label', 'LineComment', 'BlockComment'))


class _MathResolver(_Resolver):
    def __init__(self, tree, path, engine):
        super().__init__(tree, None, ())
        self.path, self.engine = path, engine
        self.exemptions = {}
        self.resolved = set()

    def _watch(self, state):
        if hasattr(state, '_math_wildcard_watched'):
            return
        original = state.unknown_wildcard
        def invalidate():
            self.exemptions.pop(state.frames[-1], None)
            original()
        state.unknown_wildcard = invalidate
        state._math_wildcard_watched = True

    def _lookup(self, state, name):
        for frame in reversed(state.frames):
            if name in frame.values:
                return frame.values[name]
            if frame.wildcard and name not in self.exemptions.get(frame, ()):
                return None
        return Binding('builtin-math', name)

    def import_node(self, index, state):
        shape = import_graph.import_shape(self.tree, index)
        frame = state.frames[-1]
        old = {name: self._lookup(state, name) for name in _BUILTINS}
        previous = {name: frame.values[name] for name in _BUILTINS if name in frame.values}
        module = None
        if shape is not None and shape.items is None:
            if shape.request.startswith('@'):
                if shape.request in self.engine.packages:
                    module = self.engine.incoming(self.tree, index, self.path, (self.path,))
            else:
                target = self.engine.graph.target_for(self.path, shape.request)
                if target is not None:
                    module = self.engine.module(target, (self.path,))
        super().import_node(index, state)
        if module is not None and not module.reason and not self.engine.exhausted:
            exports = dict(module.exports)
            for name in _BUILTINS:
                if old[name] != Binding('builtin-math', name) or name in exports:
                    continue
                if name in previous:
                    frame.values[name] = previous[name]
                else:
                    frame.values.pop(name, None)
                self.exemptions.setdefault(frame, set()).add(name)

    def visit(self, index, state, *, output=False):
        self._watch(state)
        node = self.nodes[index]
        if node.kind == 'MathIdent' and self.text(index) in _BUILTINS:
            value = self._lookup(state, self.text(index))
            super().visit(index, state, output=output)
            if state.deferred:
                value = None
            self.set_binding(index, value)
            return value
        value = super().visit(index, state, output=output)
        if node.kind == 'MathCall':
            parts = self.sig(index)
            if (len(parts) == 2 and self.nodes[parts[0]].kind == 'MathIdent'
                    and self.text(parts[0]) in _BUILTINS
                    and self.facts[index].binding == Binding('builtin-math', self.text(parts[0]))
                    and not self.facts[index].deferred):
                self.resolved.add(index)
            # Typst 0.15.1 exposes the reviewed double-dot accent as a symbol
            # variant; only the exact field on the unshadowed builtin is known.
            elif len(parts) == 2 and self.nodes[parts[0]].kind == 'MathFieldAccess':
                field = parts[0]
                segments = self.sig(field)
                if (self.text(field) == 'dot.double' and len(segments) == 3
                        and self.nodes[segments[0]].kind == 'MathIdent'
                        and self.nodes[segments[1]].kind == 'Dot'
                        and self.nodes[segments[2]].kind == 'MathIdent'
                        and self.facts[segments[0]].binding == Binding('builtin-math', 'dot')
                        and not self.facts[index].deferred):
                    self.resolved.add(index)
        return value


def analyze(analyzer, path):
    """Report active modern highlight sites whose math calls use bounded reviewed builtins.

    Existing modern evidence must be issued; suppressed findings are never
    promoted. Every unknown math call or nonliteral code within an equation
    rejects that finding. Results are diagnostics, not a candidate edit plan.
    """
    analyzer._check()
    source = analyzer._session._context.source_for(path)
    if source is None:
        raise InputError('Source is not in the issued include capture')
    report = analyzer.analyze(source.tree, path)
    result = _analyze_report(analyzer, path, report)
    analyzer._check()
    return result


def _engine(session):
    engine = binding._NamespaceAnalyzer(session._graph, session._namespaces, (), False)
    engine.reason = facade_provenance._graph_reason(session._graph)
    engine.packages = {item.evidence.import_path: item for item in session._namespaces.packages}
    engine.evidence = session._namespaces.evidence
    return engine


def _analyze_report(analyzer, path, report):
    session = analyzer._session
    source = session._context.source_for(path)
    tree = source.tree
    engine = _engine(session)
    resolver = _MathResolver(tree, path, engine)
    resolver.visit(0, resolver.state)
    parents = {c: i for i, n in enumerate(tree.nodes) for c in n.children}
    findings, unresolved = [], []
    call_by_span = {}
    for i, node in enumerate(tree.nodes):
        if node.kind == 'FuncCall':
            key = (node.start, node.end)
            call_by_span[key] = i if key not in call_by_span else None
    for finding in report['diagnostics']:
        call = call_by_span.get((finding['start'], finding['end']))
        if call is None:
            raise InputError('Modern finding has no unique source call')
        cur = parents.get(call)
        while cur is not None and tree.nodes[cur].kind not in _FORBIDDEN_ANCESTORS:
            cur = parents.get(cur)
        dynamic_ancestor = cur is not None
        equations, pending = [], [call]
        while pending:
            i = pending.pop()
            if tree.nodes[i].kind == 'Equation':
                equations.append(i)
            else:
                pending.extend(tree.nodes[i].children)
        calls, unsafe = [], False
        for equation in equations:
            pending = [equation]
            while pending:
                i = pending.pop(); node = tree.nodes[i]
                if node.kind in _FORBIDDEN_MATH:
                    unsafe = True
                if node.kind == 'MathCall':
                    calls.append(i)
                    if len(calls) > MAX_ITEMS:
                        raise InputError('Math evidence item budget exceeded')
                    if i not in resolver.resolved:
                        unsafe = True
                pending.extend(node.children)
        if calls and (unsafe or dynamic_ancestor):
            unresolved.append(dict(path=path, start=finding['start'], end=finding['end'],
                                   reason=('Dynamic or deferred ancestor' if dynamic_ancestor else
                                           'Math call binding, arguments, or code are unproved')))
        elif calls:
            findings.append(dict(path=path, start=finding['start'], end=finding['end'],
                                 math_calls=[tree.nodes[i].start for i in sorted(calls)],
                                 source_sha256=report['source_sha256'],
                                 conditions=['Typst 0.15.1 uses its standard built-in math names and reviewed symbol variants.',
                                             'The captured imports and helper are selected at runtime.'],
                                 runtime_binding_proved=False, execution_proved=False,
                                 edit_permission=False, compiler_permission=False,
                                 render_equivalence_proved=False))
    result = dict(path=path, status='INCOMPLETE' if (unresolved or report['unresolved'] or report['parse_errors'])
                  else 'CONDITIONAL', findings=findings, unresolved=unresolved,
                  upstream_unresolved=list(report['unresolved']), parse_errors=list(report['parse_errors']),
                  suppressed_count=len(report['suppressed']), coverage_complete=False,
                  runtime_binding_proved=False, edit_permission=False, compiler_permission=False)
    _check_budget((result,))
    return result


def _check_budget(results, *, max_items=None, max_bytes=None):
    max_items = MAX_ITEMS if max_items is None else max_items
    max_bytes = MAX_BYTES if max_bytes is None else max_bytes
    items = 0
    size = 256  # enclosing pretty-printed batch object and array
    for result in results:
        items += (1 + len(result['findings']) + len(result['unresolved'])
                  + len(result['upstream_unresolved']) + len(result['parse_errors'])
                  + sum(len(x['math_calls']) + len(x['conditions']) for x in result['findings']))
        if items > max_items:
            raise InputError('Math evidence item budget exceeded')
        encoded = json.dumps(result, ensure_ascii=True, indent=2)
        size += len(encoded.encode('utf-8')) + 4 * encoded.count('\n') + 16
        if size > max_bytes:
            raise InputError('Math evidence byte budget exceeded')


def analyze_all(analyzer, *, return_modern=False, max_items=None, max_bytes=None):
    """Return a bounded batch using the analyzer's already bounded report."""
    max_items = MAX_ITEMS if max_items is None else max_items
    max_bytes = MAX_BYTES if max_bytes is None else max_bytes
    if (type(return_modern) is not bool or type(max_items) is not int or not 0 < max_items <= MAX_ITEMS
            or type(max_bytes) is not int or not 0 < max_bytes <= MAX_BYTES):
        raise InputError('Invalid math evidence batch selection')
    reports = analyzer.analyze_all(max_items=max_items, max_bytes=max_bytes)
    results = []
    for report in reports['by_file']:
        if report['diagnostics'] or report['suppressed'] or report['unresolved'] or report['parse_errors']:
            results.append(_analyze_report(analyzer, report['path'], report))
            _check_budget(results, max_items=max_items, max_bytes=max_bytes)
    analyzer._check()
    result = dict(status='INCOMPLETE' if any(x['status'] == 'INCOMPLETE' for x in results) else 'CONDITIONAL',
                  by_file=results, coverage_complete=False,
                  runtime_binding_proved=False, edit_permission=False, compiler_permission=False)
    return (reports, result) if return_modern else result
