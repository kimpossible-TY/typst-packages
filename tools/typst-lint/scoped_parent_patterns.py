"""Bounded syntactic inventory of scoped-parent transfer shapes.

This is not a helper-binding or side-effect analyzer. It reads issued source
ASTs only and does not imply that a reported call executes or needs an edit.
"""
from bisect import bisect_right
import json

import conditional_modern_highlight as modern
import import_graph as graph
from typst_lint import InputError, digest, require

MAX_WORK = 8_000_000
MAX_ITEMS = 10_000
MAX_BYTES = 4 * 1024 * 1024
MAX_CALLS_PER_SITE = 12
_CONDITIONS = (
    'This inventory describes source syntax, not runtime helper binding, execution, or side effects.',
    'Some listed parent calls may be unrelated to reported highlights or to a missing preview candidate.',
    'A parent transfer and an enclosing source callback do not prove a highlight candidate is executable or fixable.',
    'Imported implementation, reference targets, and rendered output are not proved by this inventory.',
)
_LITERAL = frozenset(('ContentBlock', 'Markup', 'LeftBracket', 'RightBracket', 'Text', 'Space',
                      'SmartQuote', 'Strong', 'Emph', 'Star', 'Underscore', 'Linebreak', 'Parbreak'))


class _Limit(Exception):
    pass


def _charge(remaining, amount):
    remaining[0] -= amount
    if remaining[0] < 0:
        raise _Limit('Scoped parent inspection work budget exceeded; remaining sources are uninspected')


def _significant(tree, index, remaining):
    _charge(remaining, 1 + len(tree.nodes[index].children))
    return graph.significant(tree, index)


def _parameter(tree, closure, remaining):
    nodes = tree.nodes
    parts = _significant(tree, closure, remaining)
    if len(parts) != 3 or nodes[parts[0]].kind != 'Params' or nodes[parts[1]].kind != 'Arrow':
        return None
    params = _significant(tree, parts[0], remaining)
    if (len(params) == 3 and nodes[params[0]].kind == 'LeftParen'
            and nodes[params[2]].kind == 'RightParen'):
        params = (params[1],)
    if len(params) != 1 or nodes[params[0]].kind != 'Ident':
        return None
    _charge(remaining, nodes[params[0]].end - nodes[params[0]].start)
    return graph.raw(tree, params[0]).decode('utf-8')


def _head(tree, call, remaining):
    node = tree.nodes[call]
    if not node.children:
        return '[unknown]'
    head = tree.nodes[node.children[0]]
    if head.kind not in ('Ident', 'MathIdent', 'Parenthesized', 'FieldAccess', 'MathFieldAccess'):
        return '[dynamic]'
    _charge(remaining, min(80, head.end - head.start))
    data = tree.source[head.start:min(head.end, head.start + 80)]
    return data.decode('utf-8', 'replace') + ('…' if head.end - head.start > 80 else '')


def _line(starts, source, offset, remaining):
    index = bisect_right(starts, offset) - 1
    _charge(remaining, offset - starts[index])
    return index + 1, len(source[starts[index]:offset].decode('utf-8', 'replace')) + 1


def _source(tree, path, remaining, add):
    nodes, source = tree.nodes, tree.source
    _charge(remaining, 3 * len(nodes) + 2 * len(source))
    source_sha256 = digest(source)
    parents = {child: index for index, node in enumerate(nodes) for child in node.children}
    starts = [0] + [index + 1 for index, byte in enumerate(source) if byte == 10]
    for index, node in enumerate(nodes):
        if node.kind != 'Named':
            continue
        parts = _significant(tree, index, remaining)
        if (len(parts) != 3 or nodes[parts[0]].kind != 'Ident' or graph.raw(tree, parts[0]) != b'parent'
                or nodes[parts[2]].kind != 'Ident'):
            continue
        _charge(remaining, nodes[parts[2]].end - nodes[parts[2]].start)
        parameter = graph.raw(tree, parts[2]).decode('utf-8')
        args = parents.get(index)
        call = parents.get(args) if args is not None else None
        if args is None or call is None or nodes[args].kind != 'Args' or nodes[call].kind != 'FuncCall':
            continue
        # Find a syntactic enclosing callback of the same parameter. Bindings
        # and dynamic shadowing are intentionally not inferred from this shape.
        cursor, owner, owner_call = call, None, None
        while cursor in parents:
            _charge(remaining, 1)
            cursor = parents[cursor]
            if nodes[cursor].kind != 'Closure' or _parameter(tree, cursor, remaining) != parameter:
                continue
            parent = cursor
            while parent in parents and nodes[parents[parent]].kind in ('Args', 'Parenthesized'):
                _charge(remaining, 1)
                parent = parents[parent]
            enclosing = parents.get(parent)
            if enclosing is not None and nodes[enclosing].kind == 'FuncCall':
                name = _head(tree, enclosing, remaining)
                if name in ('local-tag-scope', 'local-scope-annotations'):
                    owner, owner_call = cursor, enclosing
            break
        if owner is None:
            continue
        target = _head(tree, call, remaining)
        reasons = ['parent-binding-not-proved']
        if target != 'local-scope-annotations':
            reasons.append('unreviewed-wrapper')
        positional = [c for c in _significant(tree, args, remaining)
                      if nodes[c].kind not in ('Named', 'LeftParen', 'RightParen', 'Comma')]
        calls, other_kinds, total_calls = [], set(), 0
        if len(positional) != 1 or nodes[positional[0]].kind != 'Closure':
            reasons.append('callback-shape-unreviewed')
        else:
            closure = _significant(tree, positional[0], remaining)
            if len(closure) != 3 or nodes[closure[2]].kind != 'ContentBlock':
                reasons.append('callback-shape-unreviewed')
            else:
                pending = [closure[2]]
                while pending:
                    _charge(remaining, 1)
                    part = pending.pop()
                    item = nodes[part]
                    if item.kind in ('FuncCall', 'MathCall'):
                        total_calls += 1
                        if len(calls) < MAX_CALLS_PER_SITE:
                            calls.append((item.start, item.end, item.kind, _head(tree, part, remaining)))
                    elif item.kind not in _LITERAL:
                        other_kinds.add(item.kind)
                    _charge(remaining, len(item.children))
                    pending.extend(reversed(item.children))
                if total_calls:
                    reasons.append('unreviewed-child-calls')
                if other_kinds:
                    reasons.append('child-body-not-literal-prose')
        line, col = _line(starts, source, node.start, remaining)
        outer_line, _ = _line(starts, source, nodes[owner_call].start, remaining)
        call_details = []
        for start, end, kind, name in calls:
            call_line, call_col = _line(starts, source, start, remaining)
            call_details.append(dict(start=start, end=end, line=call_line, column=call_col,
                                     kind=kind, callee_syntax=name))
        display_parameter = parameter[:80] + ('…' if len(parameter) > 80 else '')
        add(dict(path=path, source_sha256=source_sha256, start=node.start, end=node.end, line=line, column=col,
                 parameter_syntax=display_parameter, wrapper_syntax=target, outer_callback_line=outer_line,
                 reasons=reasons, child_call_count=total_calls, child_calls=call_details,
                 child_calls_truncated=max(0, total_calls - len(call_details)),
                 other_syntax_kinds=sorted(other_kinds), status='SOURCE_PATTERN',
                 runtime_binding_proved=False, execution_proved=False, edit_permission=False))


def analyze(evidence, *, max_work=MAX_WORK, max_items=MAX_ITEMS, max_bytes=MAX_BYTES):
    """Inventory observed source shapes, fail closed on any bounded-work limit."""
    require(type(evidence) is modern.ModernHighlightAnalyzer, 'Issued modern highlight evidence is required')
    require(type(max_work) is int and 0 < max_work <= MAX_WORK and type(max_items) is int
            and 0 < max_items <= MAX_ITEMS and type(max_bytes) is int and 0 < max_bytes <= MAX_BYTES,
            'Invalid scoped parent inspection budget')
    evidence._check()
    result = dict(status='CONDITIONAL', findings=[], conditions=_CONDITIONS, source_count=0,
                  complete_source_count=0, work_used=0, report_items=2 + len(_CONDITIONS),
                  truncated=False, runtime_binding_proved=False, execution_proved=False,
                  coverage_complete=False, edit_permission=False, compiler_permission=False,
                  render_equivalence_proved=False)
    remaining = [max_work]
    # Charge a conservative serialization allowance for the enclosing report.
    size = len(json.dumps(result, ensure_ascii=False).encode('utf-8')) + 2048
    if result['report_items'] > max_items or size > max_bytes:
        raise InputError('Scoped parent inspection report budget is too small')
    def add(row):
        nonlocal size
        encoded = json.dumps(row, ensure_ascii=False, sort_keys=True, indent=2)
        needed = len(encoded.encode('utf-8')) + 8 * encoded.count('\n') + 16
        items = 1 + len(row['reasons']) + len(row['child_calls']) + len(row['other_syntax_kinds'])
        if result['report_items'] + items > max_items or size + needed > max_bytes:
            raise _Limit('Scoped parent inspection report budget exceeded; remaining sources are uninspected')
        result['findings'].append(row)
        result['report_items'] += items
        size += needed
    try:
        for source in evidence._session._context.sources:
            result['source_count'] += 1
            _source(source.tree, source.path, remaining, add)
            result['complete_source_count'] += 1
    except _Limit as error:
        result['status'] = 'INCOMPLETE'
        result['truncated'] = True
        result['reason'] = str(error)
    result['work_used'] = min(max_work, max_work - remaining[0])
    evidence._check()
    return result
