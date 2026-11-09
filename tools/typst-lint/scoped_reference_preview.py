"""Narrow source-only proof for scoped-reference highlight splits.

The verified namespace contract and source AST are conditional evidence. This
module does not evaluate Typst, resolve labels, certify rendering or grant
permission to apply edits. Unsupported uses fail closed.
"""
import json
import weakref

import conditional_highlight as highlight
import conditional_modern_highlight as modern
import import_graph as graph

_SCOPE_HASH = 'eb8e7ff7b31238c8fa4b996deb9502fec0ae4610240427a8b40873708e509203'
MAX_SCOPE_WORK = 2_000_000
BUDGET_REASON = 'Scoped reference AST work limit exceeded; source withheld'


class ScopedReferenceBudgetExceeded(ValueError):
    pass


def _charge(budget, amount):
    budget[0] -= amount
    if budget[0] < 0:
        raise ScopedReferenceBudgetExceeded(BUDGET_REASON)


_FIELDS = frozenset(('ref', 'tag', 'tags', 'name', 'names', 'anchor', 'node', 'mark', 'annot'))


def _reference(tree, index, parameter):
    """Exactly `(parameter.ref)("literal")`, with no executable arguments."""
    nodes = tree.nodes
    call = graph.significant(tree, index)
    if len(call) != 2 or nodes[call[0]].kind != 'Parenthesized' or nodes[call[1]].kind != 'Args':
        return False
    if graph.raw(tree, call[0]) != ('(' + parameter + '.ref)').encode('utf-8'):
        return False
    parens = graph.significant(tree, call[0])
    if len(parens) != 3 or tuple(nodes[i].kind for i in parens) != ('LeftParen', 'FieldAccess', 'RightParen'):
        return False
    field = graph.significant(tree, parens[1])
    if (len(field) != 3 or tuple(nodes[i].kind for i in field) != ('Ident', 'Dot', 'Ident')
            or graph.raw(tree, field[0]).decode('utf-8') != parameter or graph.raw(tree, field[2]) != b'ref'):
        return False
    args = graph.significant(tree, call[1])
    return (len(args) == 3 and tuple(nodes[i].kind for i in args) == ('LeftParen', 'Str', 'RightParen')
            and graph.literal_string(tree, args[1]) is not None)


def _scope(tree, diagnostic, *, override=None, budget=None):
    """Return the reference call spans iff one exact callback stays unmodified.

    Every occurrence of the parameter inside the callback must be an approved
    direct dictionary-field read. Thus rebinding, import/include, mutation,
    parameter shadowing, passing the whole dictionary to other code and use of
    unreviewed dictionary methods all fail closed, including in deferred code.
    """
    if budget is None:
        budget = [MAX_SCOPE_WORK]
    callbacks = diagnostic.get('callback_context', ())
    contexts = diagnostic.get('content_context', ())
    if not callbacks:
        return frozenset()
    callback = callbacks[-1]
    params = callback.get('parameters', ())
    ordinal = callback.get('content_context_index')
    if (len(params) != 1 or type(params[0]) is not str or type(ordinal) is not int
            or not 0 <= ordinal < len(contexts)):
        return frozenset()
    route = contexts[ordinal]
    if not route:
        return frozenset()
    first = route[0]
    if (first.get('path') != highlight.SCOPED_ANNOTATIONS or first.get('source_sha256') != _SCOPE_HASH
            or first.get('operation') != 'package-export'
            or first.get('imported_name') not in highlight._SCOPED_CALLBACKS):
        return frozenset()
    parameter = params[0]
    start, end = (callback['body_start'], callback['body_end']) if override is None else override
    nodes = tree.nodes
    _charge(budget, 3 * len(nodes))
    parents = {child: i for i, node in enumerate(nodes) for child in node.children}
    body_ids = [i for i, node in enumerate(nodes) if node.kind == 'ContentBlock' and (node.start, node.end) == (start, end)]
    if len(body_ids) != 1:
        return frozenset()
    pending = [body_ids[0]]
    while pending:
        _charge(budget, 1)
        i = pending.pop()
        node = nodes[i]
        if node.kind in ('ModuleImport', 'ModuleInclude', 'DestructAssignment'):
            return frozenset()
        if node.kind == 'Ident' and graph.raw(tree, i).decode('utf-8') == parameter:
            parent = parents.get(i)
            if parent is None or nodes[parent].kind != 'FieldAccess':
                return frozenset()
            parts = graph.significant(tree, parent)
            if (len(parts) != 3 or parts[0] != i or nodes[parts[1]].kind != 'Dot'
                    or nodes[parts[2]].kind != 'Ident'
                    or graph.raw(tree, parts[2]).decode('utf-8') not in _FIELDS):
                return frozenset()
            # Any assignment to the dictionary or a nested field is refused.
            ancestor = parent
            while ancestor in parents and nodes[parents[ancestor]].kind in ('FieldAccess', 'Parenthesized'):
                ancestor = parents[ancestor]
            outer = parents.get(ancestor)
            if outer is not None and nodes[outer].kind in ('Binary', 'DestructAssignment'):
                if nodes[outer].kind == 'DestructAssignment' or any(
                        nodes[c].kind in highlight._ASSIGNMENTS for c in graph.significant(tree, outer)):
                    return frozenset()
        pending.extend(node.children)
    return frozenset((node.start, node.end) for i, node in enumerate(nodes)
                     if node.kind == 'FuncCall' and start <= node.start < node.end <= end
                     and _reference(tree, i, parameter))


_ISSUED = {}


class CapturedScopedReferences:
    """A source's immutable analyzer findings, reissued after independent replay."""
    def __init__(self, evidence, tree, path, *, budget):
        evidence._check()
        source = evidence._session._context.source_for(path)
        if source is None or source.tree is not tree:
            raise ValueError('Scoped reference capture requires the issued source tree')
        package = next((p for p in evidence._session._namespaces.packages
                        if p.evidence.import_path == highlight.SCOPED_ANNOTATIONS), None)
        if package is None or package.evidence.source_sha256 != _SCOPE_HASH:
            raise ValueError('Scoped reference capture requires the reviewed wrapper')
        _charge(budget, 3 * len(tree.nodes))
        analyzer = modern._ModernAnalyzer(evidence._session, evidence._variant,
                                          scoped_callbacks=evidence._scoped_callbacks)
        report = evidence._analyze_one(tree, path, analyzer)
        _charge(budget, analyzer.ast_work)
        evidence._check()
        immutable = frozenset(json.dumps(item, sort_keys=True, ensure_ascii=False)
                              for item in report['diagnostics'])
        identity = id(self)
        def release(reference):
            current = _ISSUED.get(identity)
            if current is not None and current[0] is reference:
                _ISSUED.pop(identity, None)
        _ISSUED[identity] = (weakref.ref(self, release), evidence, tree, immutable)

    def selected(self, diagnostic, *, budget):
        issued = _ISSUED.get(id(self))
        if type(self) is not CapturedScopedReferences or issued is None or issued[0]() is not self:
            raise ValueError('Scoped reference capture is unissued')
        evidence, tree, immutable = issued[1:]
        evidence._check()
        try:
            key = json.dumps(diagnostic, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            return frozenset()
        if key not in immutable or b'.ref)' not in tree.source[diagnostic['start']:diagnostic['end']]:
            return frozenset()
        return _scope(tree, diagnostic, budget=budget)


def selected(evidence, tree, diagnostic, *, budget=None):
    """Replay a source's analyzer and reject findings not issued by that replay."""
    if b'.ref)' not in tree.source[diagnostic['start']:diagnostic['end']]:
        return frozenset()
    if budget is None:
        budget = [MAX_SCOPE_WORK]
    captured = CapturedScopedReferences(evidence, tree, diagnostic['path'], budget=budget)
    return captured.selected(diagnostic, budget=budget)
