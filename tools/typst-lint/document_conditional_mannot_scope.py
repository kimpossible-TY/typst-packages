"""Conditional raw annotation placement in literal ``mannot-scope`` callbacks.

This is a source-pattern report. It does not resolve or execute the wrapper,
its forwarded helper, or the raw annotation function, and grants no authority
to compile or modify sources.
"""
from bisect import bisect_right
from collections.abc import Mapping

from conditional_highlight import _module_alias
from document_conditional_marker_tags import _has_name
from document_conditional_raw_annot import _arguments, _equation_constructor, _explicit_shadow
from document_conditional_scoped_calls import _OUTER_BARRIERS, _parts, _scoped_body, _text
from suppression import resolve as resolve_suppressions
from syntax_client import AdapterError, parse
from typst_lint import InputError, digest, load_rules, require

RULE = 'core.annotation-in-display'
MAX_ITEMS = 10_000
CONDITIONS = (
    'The wrapper resolves at runtime to the reviewed scoped-annotations mannot-scope helper.',
    'That helper forwards the literal callback to its reviewed local-scope-annotations implementation.',
    'Each annot call resolves to the reviewed raw mannot annotation helper at runtime.',
    'The wrapper, callback and annotation execute in the expected document and display context.',
    'No enclosing helper or show rule changes the runtime display ownership.',
)
_ASSIGNMENTS = frozenset(('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq'))
_NESTED_BARRIERS = _OUTER_BARRIERS | {'LetBinding', 'ModuleImport', 'ModuleInclude',
                                      'DestructAssignment', 'Raw', 'Str'}


def _contains_annot(tree, index):
    pending = [index]
    while pending:
        current = pending.pop()
        node = tree.nodes[current]
        parts = _parts(tree, current) if node.kind in ('FuncCall', 'MathCall') else ()
        if (parts and tree.nodes[parts[0]].kind in ('Ident', 'MathIdent')
                and _text(tree, parts[0]) == 'annot'):
            return True
        pending.extend(node.children)
    return False


def _wrapper_shadow(tree):
    """Refuse explicit declarations, aliases and mutations of the wrapper name."""
    nodes = tree.nodes
    for index, node in enumerate(nodes):
        if node.kind in ('ModuleImport', 'RenamedImportItem'):
            parts = _parts(tree, index)
            if node.kind == 'ModuleImport':
                module = _module_alias(tree, index)
                if module is not None and module[1] == 'mannot-scope':
                    return True
            if any(nodes[part].kind == 'As' and nodes[target].kind == 'Ident'
                   and _text(tree, target) == 'mannot-scope'
                   for part, target in zip(parts, parts[1:])):
                return True
            continue
        parts = _parts(tree, index) if node.kind in (
            'LetBinding', 'Closure', 'ForLoop', 'Binary', 'DestructAssignment') else ()
        if node.kind == 'LetBinding' and len(parts) >= 2:
            target = parts[1]
            if nodes[target].kind == 'Closure':
                values = _parts(tree, target)
                target = values[0] if values else target
            if _has_name(tree, target, 'mannot-scope'):
                return True
        elif node.kind == 'Closure':
            if any(nodes[part].kind == 'Params' and _has_name(tree, part, 'mannot-scope') for part in parts):
                return True
        elif node.kind == 'ForLoop':
            for part in parts[1:]:
                if nodes[part].kind == 'In':
                    break
                if _has_name(tree, part, 'mannot-scope'):
                    return True
        elif node.kind == 'Binary' and parts and any(nodes[part].kind in _ASSIGNMENTS for part in parts):
            if _has_name(tree, parts[0], 'mannot-scope'):
                return True
        elif node.kind == 'DestructAssignment' and parts and _has_name(tree, parts[0], 'mannot-scope'):
            return True
    return False


def _callback(tree, index):
    # mannot-scope forwards prefix, parent and name, but does not accept the
    # additional namespace option accepted by local-scope-annotations.
    parts = _parts(tree, index)
    if len(parts) != 2 or tree.nodes[parts[1]].kind != 'Args':
        return None, 'Wrapper requires direct arguments'
    for part in _parts(tree, parts[1]):
        if tree.nodes[part].kind != 'Named':
            continue
        fields = _parts(tree, part)
        if (len(fields) != 3 or tree.nodes[fields[0]].kind != 'Ident'
                or _text(tree, fields[0]) not in ('prefix', 'parent', 'name')):
            return None, 'Unsupported mannot-scope option'
    return _scoped_body(tree, index, 'local-scope-annotations')


def analyze(source, path, *, catalog=None):
    """Report raw-annotation placement and reviewed-shape lexical display counts."""
    return _analyze(source, path, catalog=catalog)


def _analyze_captured_tree(tree, path, *, catalog=None):
    """Inspect an already validated capture without reparsing its source."""
    return _analyze(tree.source, path, catalog=catalog, captured_tree=tree)


def _analyze(source, path, *, catalog=None, captured_tree=None):
    if type(source) is not bytes or type(path) is not str or not path:
        raise InputError('mannot-scope hints require source bytes and a source path')
    records = load_rules()[0]['rules'] if catalog is None else catalog
    if (type(records) not in (tuple, list) or any(not isinstance(row, Mapping)
            or type(row.get('id')) is not str or not row['id'] for row in records)):
        raise InputError('mannot-scope hints require rule metadata')
    selected = [row for row in records if row['id'] == RULE]
    if (len(selected) != 1 or any(type(selected[0].get(key)) is not str or not selected[0][key]
                                  for key in ('grade', 'source'))):
        raise InputError('Missing or invalid annotation rule metadata')
    result = dict(path=path, source_sha256=digest(source), status='PARTIAL', diagnostics=[],
                  suppressed=[], unresolved=[], conditions=CONDITIONS, wrappers_examined=0,
                  wrappers_in_dynamic_context=0, lexical_display_calls=0,
                  runtime_binding_proved=False, execution_proved=False,
                  coverage_complete=False, edit_permission=False, compiler_permission=False)
    def check_budget():
        require(len(result['diagnostics']) + len(result['suppressed']) + len(result['unresolved'])
                + result['lexical_display_calls'] + result['wrappers_examined'] <= MAX_ITEMS,
                'mannot-scope hint item limit exceeded')

    def note(reason):
        result['unresolved'].append(reason)
        check_budget()
    try:
        tree = parse(source) if captured_tree is None else captured_tree
    except (AdapterError, OSError, ValueError) as error:
        return dict(result, status='UNAVAILABLE', unresolved=[f'Source syntax unavailable: {error}'])
    if tree.mode != 'markup' or tree.nodes[0].errors:
        return dict(result, status='UNAVAILABLE', unresolved=['Malformed or non-markup source'])
    wrappers = []
    for index, node in enumerate(tree.nodes):
        parts = _parts(tree, index) if node.kind == 'FuncCall' else ()
        if len(parts) == 2 and tree.nodes[parts[0]].kind == 'Ident' and _text(tree, parts[0]) == 'mannot-scope':
            wrappers.append(index)
    if not wrappers:
        return result
    if _wrapper_shadow(tree) or _explicit_shadow(tree):
        result['unresolved'].append('Wrapper or raw annot has an explicit shadow or mutation')
        return result
    nodes = tree.nodes
    scopes = resolve_suppressions(tree, {row['id'] for row in records})
    parents = {child: index for index, node in enumerate(nodes) for child in node.children}
    positions = {child: position for node in nodes for position, child in enumerate(node.children)}
    newlines = [index for index, byte in enumerate(source) if byte == 10]
    rule = selected[0]
    for index in wrappers:
        node = nodes[index]
        ancestor, outer_dynamic = parents.get(index), False
        while ancestor is not None and nodes[ancestor].kind != 'Equation':
            # Only a direct child of root markup has no enclosing owner.
            # Stored content, arbitrary helper arguments and show rules may
            # defer, duplicate, or change the context in which it executes.
            outer_dynamic |= ancestor != 0
            ancestor = parents.get(ancestor)
        if ancestor is not None:
            note(f'Wrapper inside an equation is unsupported at byte {node.start}')
            continue
        callback, reason = _callback(tree, index)
        if callback is None:
            note(f'{reason} at byte {node.start}')
            continue
        result['wrappers_examined'] += 1
        result['wrappers_in_dynamic_context'] += int(outer_dynamic)
        check_budget()
        body, _ = callback
        pending = [body]
        while pending:
            current = pending.pop()
            item = nodes[current]
            if current != body and (item.kind in _NESTED_BARRIERS or
                    (item.kind == 'Binary' and any(nodes[x].kind in _ASSIGNMENTS for x in _parts(tree, current)))):
                if _contains_annot(tree, current):
                    note(f'Nested dynamic or deferred annotation is unsupported at byte {item.start}')
                continue
            if item.kind == 'MathCall':
                math_parts = _parts(tree, current)
                if (math_parts and nodes[math_parts[0]].kind == 'MathIdent'
                        and _text(tree, math_parts[0]) == 'annot'):
                    note(f'Raw math annotation call is unsupported at byte {item.start}')
            parts = _parts(tree, current) if item.kind == 'FuncCall' else ()
            if (current != body and parts and nodes[parts[0]].kind == 'Ident'
                    and _text(tree, parts[0]) == 'mannot-scope'):
                # A nested wrapper is visited separately; do not count its
                # callback's calls as belonging to this outer scope as well.
                continue
            pending.extend(reversed(item.children))
            call = parts
            if len(call) != 2 or nodes[call[0]].kind != 'Ident' or _text(tree, call[0]) != 'annot':
                continue
            parent = parents.get(current)
            siblings = nodes[parent].children if parent is not None else ()
            position = positions.get(current, 0)
            mark = nodes[siblings[position - 1]] if position else None
            if (parent is None or nodes[parent].kind not in ('Markup', 'Math') or mark is None
                    or mark.kind != 'Hash' or mark.end != item.start or nodes[call[1]].kind != 'Args'
                    or not _arguments(tree, call[1])):
                note(f'Raw annotation call shape is unsupported at byte {item.start}')
                continue
            cursor, equation, unknown = parent, None, False
            while cursor is not None and cursor != body:
                if nodes[cursor].kind == 'Equation':
                    equation = nodes[cursor]
                    break
                if _equation_constructor(tree, cursor):
                    unknown = True
                    break
                cursor = parents.get(cursor)
            if unknown or (equation is not None and equation.display is None):
                note(f'Runtime display ownership is unsupported at byte {item.start}')
                continue
            if equation is not None and equation.display is True:
                result['lexical_display_calls'] += 1
                check_budget()
                continue
            line = bisect_right(newlines, mark.start) + 1
            line_start = newlines[line - 2] + 1 if line > 1 else 0
            finding = dict(rule_id=RULE, grade=rule['grade'], path=path, line=line,
                           column=len(source[line_start:mark.start].decode('utf-8')) + 1,
                           start=mark.start, end=item.end, source=rule['source'], wrapper_start=node.start,
                           outer_dynamic_context=outer_dynamic,
                           explanation='This raw annotation is outside a display if the wrapper, helper bindings and execution match the stated conditions.')
            target = 'suppressed' if any(scope.covers(RULE, mark.start) for scope in scopes) else 'diagnostics'
            result[target].append(finding)
            check_budget()
    result['unresolved'] = list(dict.fromkeys(result['unresolved']))
    check_budget()
    return result
