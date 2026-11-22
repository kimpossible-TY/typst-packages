"""Conditional hints for a literal raw marker tag in an annotation scope.

Spelling is not provenance: these source patterns do not prove wrapper or
marker binding, callback execution, rendering, or any edit authority.
"""
from bisect import bisect_right
from collections.abc import Mapping

from document_conditional_scoped_calls import (_OUTER_BARRIERS, _explicit_wrapper_shadows,
                                                _parts, _scoped_body, _text)
from conditional_highlight import _module_alias
from suppression import resolve as resolve_suppressions
from syntax_client import AdapterError, parse
from typst_lint import InputError, digest, load_rules

RULE = 'core.scoped-marker-tags'
CONDITIONS = (
    'The wrapper resolves to the reviewed local-scope-annotations helper and invokes this callback.',
    'The mark call resolves to the raw mannot marker helper at runtime.',
    'The callback and marker execute in the expected annotation scope.',
)
_ASSIGNMENTS = frozenset(('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq'))
_CALLBACK_CONTAINERS = frozenset(('Markup', 'ContentBlock', 'Equation', 'Math'))


def _has_name(tree, index, name):
    pending = [index]
    while pending:
        item = pending.pop()
        node = tree.nodes[item]
        if node.kind in ('Ident', 'MathIdent') and _text(tree, item) == name:
            return True
        pending.extend(node.children)
    return False


def _explicit_mark_binding(tree):
    """Decline explicit local bindings, parameter shadows, and mutation."""
    nodes = tree.nodes
    for index, node in enumerate(nodes):
        if node.kind in ('ModuleImport', 'RenamedImportItem'):
            parts = _parts(tree, index)
            if node.kind == 'ModuleImport':
                module = _module_alias(tree, index)
                if module is not None and module[1] == 'mark':
                    return True
            for position, part in enumerate(parts[:-1]):
                target = parts[position + 1]
                if (nodes[part].kind == 'As' and nodes[target].kind == 'Ident'
                        and _text(tree, target) == 'mark'):
                    return True
            continue
        parts = _parts(tree, index) if node.kind in (
            'LetBinding', 'Closure', 'ForLoop', 'Binary', 'DestructAssignment') else ()
        if node.kind == 'LetBinding' and len(parts) >= 2:
            target = parts[1]
            if nodes[target].kind == 'Closure':
                closure = _parts(tree, target)
                target = closure[0] if closure else target
            if _has_name(tree, target, 'mark'):
                return True
        elif node.kind == 'Closure':
            if any(nodes[part].kind == 'Params' and _has_name(tree, part, 'mark') for part in parts):
                return True
        elif node.kind == 'ForLoop':
            for part in parts[1:]:
                if nodes[part].kind == 'In':
                    break
                if _has_name(tree, part, 'mark'):
                    return True
        elif node.kind == 'Binary' and parts and any(nodes[part].kind in _ASSIGNMENTS for part in parts):
            if _has_name(tree, parts[0], 'mark'):
                return True
        elif node.kind == 'DestructAssignment' and parts and _has_name(tree, parts[0], 'mark'):
            return True
    return False


def _raw_marker(tree, index):
    nodes = tree.nodes
    parts = _parts(tree, index)
    if (nodes[index].kind != 'MathCall' or len(parts) != 2 or nodes[parts[0]].kind != 'MathIdent'
            or _text(tree, parts[0]) != 'mark' or nodes[parts[1]].kind != 'MathArgs'):
        return None
    args = [part for part in _parts(tree, parts[1])
            if nodes[part].kind not in ('LeftParen', 'RightParen', 'Comma')]
    if len(args) != 2 or nodes[args[0]].kind in ('Named', 'Spread') or nodes[args[1]].kind != 'Named':
        return None
    named = _parts(tree, args[1])
    if (len(named) != 4 or nodes[named[0]].kind != 'Ident' or _text(tree, named[0]) != 'tag'
            or nodes[named[1]].kind != 'Colon' or nodes[named[2]].kind != 'Hash'
            or nodes[named[3]].kind != 'Label'):
        return None
    return named[3]


def analyze(source, path, *, catalog=None):
    """Report captured source patterns; no helper or source edits are evaluated."""
    if type(source) is not bytes or type(path) is not str or not path:
        raise InputError('Marker tag hints require source bytes and a source path')
    records = load_rules()[0]['rules'] if catalog is None else catalog
    if (type(records) not in (tuple, list) or any(not isinstance(row, Mapping)
            or type(row.get('id')) is not str or not row['id'] for row in records)):
        raise InputError('Marker tag hints require rule metadata')
    selected = [row for row in records if row['id'] == RULE]
    if (len(selected) != 1 or any(type(selected[0].get(key)) is not str or not selected[0][key]
                                  for key in ('grade', 'source'))):
        raise InputError('Missing or invalid marker tag rule metadata')
    result = dict(path=path, source_sha256=digest(source), status='PARTIAL', diagnostics=[],
                  suppressed=[], unresolved=[], conditions=CONDITIONS, runtime_binding_proved=False,
                  execution_proved=False, coverage_complete=False, edit_permission=False, compiler_permission=False)
    try:
        tree = parse(source)
    except (AdapterError, OSError, ValueError) as error:
        return dict(result, status='UNAVAILABLE', unresolved=[f'Source syntax unavailable: {error}'])
    if tree.mode != 'markup' or tree.nodes[0].errors:
        return dict(result, status='UNAVAILABLE', unresolved=['Malformed or non-markup source'])
    if _explicit_mark_binding(tree):
        result['unresolved'].append('An explicit mark binding or mutation is present; source is withheld')
        return result
    nodes = tree.nodes
    scopes = resolve_suppressions(tree, {row['id'] for row in records})
    parents = {child: index for index, node in enumerate(nodes) for child in node.children}
    newlines = [index for index, byte in enumerate(source) if byte == 10]
    wrapper_shadows = _explicit_wrapper_shadows(tree)
    rule = selected[0]
    for index, node in enumerate(nodes):
        parts = _parts(tree, index) if node.kind == 'FuncCall' else ()
        if (len(parts) != 2 or nodes[parts[0]].kind != 'Ident'
                or _text(tree, parts[0]) != 'local-scope-annotations'):
            continue
        ancestor = parents.get(index)
        while ancestor is not None and nodes[ancestor].kind not in _OUTER_BARRIERS:
            ancestor = parents.get(ancestor)
        if ancestor is not None or 'local-scope-annotations' in wrapper_shadows:
            result['unresolved'].append(f'Wrapper binding or execution is unsupported at byte {node.start}')
            continue
        callback, reason = _scoped_body(tree, index, 'local-scope-annotations')
        if callback is None:
            result['unresolved'].append(f'{reason} at byte {node.start}')
            continue
        body, _ = callback
        pending = [body]
        while pending:
            current = pending.pop()
            item = nodes[current]
            if item.kind in _CALLBACK_CONTAINERS:
                pending.extend(reversed(item.children))
                continue
            label = _raw_marker(tree, current)
            if label is None:
                continue
            line = bisect_right(newlines, item.start) + 1
            line_start = newlines[line - 2] + 1 if line > 1 else 0
            finding = dict(rule_id=RULE, grade=rule['grade'], path=path, line=line,
                           column=len(source[line_start:item.start].decode('utf-8')) + 1,
                           start=item.start, end=item.end, label_start=nodes[label].start,
                           label_end=nodes[label].end, source=rule['source'], wrapper_start=node.start,
                           explanation='This marker uses a literal global label if the wrapper and raw marker bindings are as expected.')
            target = 'suppressed' if any(scope.covers(RULE, item.start) for scope in scopes) else 'diagnostics'
            result[target].append(finding)
    result['unresolved'] = list(dict.fromkeys(result['unresolved']))
    return result
