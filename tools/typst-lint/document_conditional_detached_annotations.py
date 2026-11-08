"""Conditional source-only hints for a narrow detached scoped annotation.

No wrapper binding, runtime execution, rendering, edit or compiler authority
is inferred from the source spelling. The parser is invoked on the supplied
immutable source bytes; callers cannot supply diagnostic records or AST nodes.
"""
from collections.abc import Mapping
from bisect import bisect_right

from document_conditional_scoped_calls import (_OUTER_BARRIERS, _explicit_wrapper_shadows,
                                                _parts, _scoped_body, _text)
from scoped_provenance import FIELDS
from suppression import resolve as resolve_suppressions
from syntax_client import AdapterError, parse
from typst_lint import InputError, digest, load_rules

RULE = 'core.annotation-in-display'
CONDITIONS = (
    'The wrapper resolves to the reviewed local-scope-annotations helper and invokes this literal callback.',
    'The callback parameter retains the expected scoped annotation dictionary until this call.',
    'The callback body and this annotation execute and render in the expected document context.',
)
_BARRIERS = frozenset(('LetBinding', 'Closure', 'Contextual', 'CodeBlock', 'ModuleImport',
                       'ModuleInclude', 'ForLoop', 'WhileLoop', 'Conditional', 'FuncReturn',
                       'DestructAssignment'))
_ASSIGNMENTS = frozenset(('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq'))


def _safe_prefix(tree, root, parameter, parents):
    """Exclude uncertain receiver escapes, rebinding and deferred execution."""
    nodes = tree.nodes
    pending = [root]
    while pending:
        index = pending.pop()
        node = nodes[index]
        if node.kind in _BARRIERS or (node.kind == 'Binary'
                and any(nodes[part].kind in _ASSIGNMENTS for part in _parts(tree, index))):
            return False
        if node.kind == 'Ident' and _text(tree, index) == parameter:
            parent = parents.get(index)
            fields = _parts(tree, parent) if parent is not None else ()
            if (parent is None or nodes[parent].kind != 'FieldAccess' or len(fields) != 3
                    or fields[0] != index or nodes[fields[1]].kind != 'Dot'
                    or nodes[fields[2]].kind != 'Ident' or _text(tree, fields[2]) not in FIELDS):
                return False
        pending.extend(node.children)
    return True


def _annotation_call(tree, index, parameter):
    nodes = tree.nodes
    parts = _parts(tree, index)
    if len(parts) != 2 or nodes[parts[1]].kind != 'Args':
        return False
    callee = parts[0]
    wrapped = _parts(tree, callee)
    if (nodes[callee].kind != 'Parenthesized' or len(wrapped) != 3
            or [nodes[item].kind for item in wrapped] != ['LeftParen', 'FieldAccess', 'RightParen']):
        return False
    field = _parts(tree, wrapped[1])
    if (len(field) != 3 or [nodes[item].kind for item in field] != ['Ident', 'Dot', 'Ident']
            or _text(tree, field[0]) != parameter or _text(tree, field[2]) != 'annot'):
        return False
    args = [item for item in _parts(tree, parts[1])
            if nodes[item].kind not in ('LeftParen', 'RightParen', 'Comma')]
    return len(args) == 3 and all(nodes[item].kind not in ('Named', 'Spread') for item in args)


def analyze(source, path, *, catalog=None):
    """Inspect exact captured bytes, emitting conditional non-mutating hints."""
    if type(source) is not bytes or type(path) is not str or not path:
        raise InputError('Detached annotation hints require source bytes and a source path')
    records = load_rules()[0]['rules'] if catalog is None else catalog
    if (type(records) not in (tuple, list) or any(not isinstance(row, Mapping)
            or type(row.get('id')) is not str or not row['id'] for row in records)):
        raise InputError('Detached annotation hints require rule metadata')
    selected = [row for row in records if row['id'] == RULE]
    if (len(selected) != 1 or any(type(selected[0].get(key)) is not str or not selected[0][key]
                                  for key in ('grade', 'source'))):
        raise InputError('Missing or invalid annotation rule metadata')
    result = dict(path=path, source_sha256=digest(source), status='PARTIAL', diagnostics=[],
                  suppressed=[], unresolved=[], conditions=CONDITIONS,
                  runtime_binding_proved=False, execution_proved=False, coverage_complete=False,
                  edit_permission=False, compiler_permission=False)
    try:
        tree = parse(source)
    except (AdapterError, OSError, ValueError) as error:
        return dict(result, status='UNAVAILABLE', unresolved=[f'Source syntax unavailable: {error}'])
    if tree.mode != 'markup' or tree.nodes[0].errors:
        return dict(result, status='UNAVAILABLE', unresolved=['Malformed or non-markup source'])
    nodes = tree.nodes
    scopes = resolve_suppressions(tree, {row['id'] for row in records})
    parents = {child: index for index, node in enumerate(nodes) for child in node.children}
    newlines = [index for index, byte in enumerate(source) if byte == 10]
    shadows = _explicit_wrapper_shadows(tree)
    rule = selected[0]
    for index, node in enumerate(nodes):
        if node.kind != 'FuncCall':
            continue
        parts = _parts(tree, index)
        if (len(parts) != 2 or nodes[parts[0]].kind != 'Ident'
                or _text(tree, parts[0]) != 'local-scope-annotations'):
            continue
        ancestor = parents.get(index)
        while ancestor is not None and nodes[ancestor].kind not in (_OUTER_BARRIERS | {'Equation'}):
            ancestor = parents.get(ancestor)
        if ancestor is not None or 'local-scope-annotations' in shadows:
            result['unresolved'].append(f'Wrapper binding or execution is unsupported at byte {node.start}')
            continue
        callback, reason = _scoped_body(tree, index, 'local-scope-annotations')
        if callback is None:
            result['unresolved'].append(f'{reason} at byte {node.start}')
            continue
        body, parameters = callback
        markup = [child for child in nodes[body].children if nodes[child].kind == 'Markup']
        if len(markup) != 1:
            result['unresolved'].append(f'Callback body is not direct markup at byte {node.start}')
            continue
        parameter = parameters[0]
        siblings = nodes[markup[0]].children
        prefix_safe = True
        for position, child in enumerate(siblings):
            item = nodes[child]
            mark = nodes[siblings[position - 1]] if position else None
            direct = (item.kind == 'FuncCall' and mark is not None and mark.kind == 'Hash'
                      and mark.end == item.start and _annotation_call(tree, child, parameter))
            if direct:
                if not prefix_safe:
                    result['unresolved'].append(f'Prior callback effects are unsupported at byte {item.start}')
                else:
                    line = bisect_right(newlines, mark.start) + 1
                    line_start = newlines[line - 2] + 1 if line > 1 else 0
                    column = len(source[line_start:mark.start].decode('utf-8')) + 1
                    finding = dict(rule_id=RULE, grade=rule['grade'], path=path, line=line, column=column,
                        start=mark.start, end=item.end, source=rule['source'], wrapper_start=node.start,
                        explanation='This scoped annotation is outside a display if the wrapper and callback execute as expected.')
                    target = 'suppressed' if any(scope.covers(RULE, mark.start) for scope in scopes) else 'diagnostics'
                    result[target].append(finding)
            if prefix_safe and not _safe_prefix(tree, child, parameter, parents):
                prefix_safe = False
    result['unresolved'] = list(dict.fromkeys(result['unresolved']))
    return result
