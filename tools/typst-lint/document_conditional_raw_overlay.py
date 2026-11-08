"""Conditional source-only diagnostics for a detached raw annot-cetz call.

Spelling is not package provenance. Neither runtime binding nor execution is
proved, and this module produces no edit, publication or compiler authority.
"""
from bisect import bisect_right
from collections.abc import Mapping

from conditional_highlight import _combined_import, _module_alias
from document_conditional_scoped_calls import _OUTER_BARRIERS
from document_conditional_raw_annot import _equation_constructor
import import_graph
from suppression import resolve as resolve_suppressions
from syntax_client import AdapterError, parse
from typst_lint import InputError, digest, load_rules

RULE = 'core.annotation-in-display'
NAME = 'annot-cetz'
CONDITIONS = (
    'This direct call resolves to the reviewed raw annot-cetz helper at runtime.',
    'The enclosing literal source content and this call actually execute and render as expected.',
    'No enclosing wrapper or show rule places the call inside a display equation at runtime.',
    'Runtime imports, package selection and initialization match the intended source context.',
)
_ASSIGNMENTS = frozenset(('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq'))


def _explicit_shadow(tree):
    nodes, source = tree.nodes, tree.source
    def contains_name(root):
        pending = [root]
        while pending:
            node = nodes[pending.pop()]
            if node.kind == 'Ident' and source[node.start:node.end] == NAME.encode():
                return True
            pending.extend(node.children)
        return False
    for index, node in enumerate(nodes):
        if node.kind not in ('LetBinding', 'Binary', 'DestructAssignment', 'Closure', 'ForLoop', 'ModuleImport'):
            continue
        parts = import_graph.significant(tree, index)
        if node.kind == 'LetBinding' and len(parts) >= 2:
            target = parts[1]
            if nodes[target].kind == 'Closure':
                closure = import_graph.significant(tree, target)
                if closure and nodes[closure[0]].kind == 'Ident' and contains_name(closure[0]):
                    return True
            elif contains_name(target):
                return True
        elif node.kind == 'Closure':
            if any(nodes[child].kind == 'Params' and contains_name(child) for child in parts):
                return True
        elif node.kind == 'Binary' and parts and any(nodes[part].kind in _ASSIGNMENTS for part in parts):
            if contains_name(parts[0]):
                return True
        elif node.kind == 'DestructAssignment' and parts and contains_name(parts[0]):
            return True
        elif node.kind == 'ForLoop':
            for child in parts[1:]:
                if nodes[child].kind == 'In':
                    break
                if contains_name(child):
                    return True
        elif node.kind == 'ModuleImport':
            module = _module_alias(tree, index)
            if module is not None and module[1] == NAME:
                return True
            shape = import_graph.import_shape(tree, index)
            combined = _combined_import(tree, index) if shape is None else None
            if combined is not None:
                shape, alias = combined
                if alias == NAME:
                    return True
            if shape is not None and shape.items is not None and any(local == NAME for _, local in shape.items):
                return True
            if shape is None:
                # Unsupported import-item syntax cannot prove that an explicit
                # identifier in the import is bound under some other name.
                for part in parts:
                    if nodes[part].kind == 'ImportItems' and contains_name(part):
                        return True
            for pos, part in enumerate(parts[:-1]):
                alias = nodes[parts[pos + 1]]
                if nodes[part].kind == 'As' and alias.kind == 'Ident' and source[alias.start:alias.end] == NAME.encode():
                    return True
    return False


def analyze(source, path, *, catalog=None):
    """Inspect immutable bytes without evaluating their helpers or imports."""
    return _analyze(source, path, catalog=catalog)


def _analyze_captured_tree(tree, path, *, catalog=None):
    """Inspect a validated captured tree without reparsing."""
    return _analyze(tree.source, path, catalog=catalog, captured_tree=tree)


def _analyze(source, path, *, catalog=None, captured_tree=None):
    if type(source) is not bytes or type(path) is not str or not path:
        raise InputError('Raw overlay hints require source bytes and a source path')
    records = load_rules()[0]['rules'] if catalog is None else catalog
    if (type(records) not in (tuple, list) or any(not isinstance(row, Mapping)
            or type(row.get('id')) is not str or not row['id'] for row in records)):
        raise InputError('Raw overlay hints require rule metadata')
    selected = [row for row in records if row['id'] == RULE]
    if (len(selected) != 1 or any(type(selected[0].get(key)) is not str or not selected[0][key]
                                  for key in ('grade', 'source'))):
        raise InputError('Missing or invalid annotation rule metadata')
    result = dict(path=path, source_sha256=digest(source), status='PARTIAL', diagnostics=[],
                  suppressed=[], unresolved=[], conditions=CONDITIONS, runtime_binding_proved=False,
                  execution_proved=False, coverage_complete=False, edit_permission=False, compiler_permission=False)
    try:
        tree = parse(source) if captured_tree is None else captured_tree
    except (AdapterError, OSError, ValueError) as error:
        return dict(result, status='UNAVAILABLE', unresolved=[f'Source syntax unavailable: {error}'])
    if tree.mode != 'markup' or tree.nodes[0].errors:
        return dict(result, status='UNAVAILABLE', unresolved=['Malformed or non-markup source'])
    if _explicit_shadow(tree):
        result['unresolved'].append('An explicit annot-cetz binding or mutation is present; source is withheld')
        return result
    nodes = tree.nodes
    parents = {child: index for index, node in enumerate(nodes) for child in node.children}
    positions = {child: position for node in nodes for position, child in enumerate(node.children)}
    scopes = resolve_suppressions(tree, {row['id'] for row in records})
    newlines = [index for index, byte in enumerate(source) if byte == 10]
    barriers = _OUTER_BARRIERS | {'Equation', 'LetBinding', 'Raw', 'Str'}
    for index, node in enumerate(nodes):
        if node.kind != 'FuncCall':
            continue
        parts = import_graph.significant(tree, index)
        if (len(parts) != 2 or nodes[parts[0]].kind != 'Ident'
                or source[nodes[parts[0]].start:nodes[parts[0]].end] != b'annot-cetz'
                or nodes[parts[1]].kind != 'Args'):
            continue
        parent = parents.get(index)
        if parent is None or nodes[parent].kind != 'Markup':
            result['unresolved'].append(f'Overlay call is not direct markup at byte {node.start}')
            continue
        siblings = nodes[parent].children
        position = positions[index]
        mark = nodes[siblings[position - 1]] if position else None
        if mark is None or mark.kind != 'Hash' or mark.end != node.start:
            result['unresolved'].append(f'Overlay call is not directly escaped at byte {node.start}')
            continue
        ancestor = parent
        while (ancestor is not None and nodes[ancestor].kind not in barriers
               and not _equation_constructor(tree, ancestor)):
            ancestor = parents.get(ancestor)
        if ancestor is not None:
            result['unresolved'].append(f'Overlay display ownership or execution is unsupported at byte {node.start}')
            continue
        args = [child for child in import_graph.significant(tree, parts[1])
                if nodes[child].kind not in ('LeftParen', 'RightParen', 'Comma')]
        if len(args) != 3 or any(nodes[child].kind in ('Named', 'Spread') for child in args):
            result['unresolved'].append(f'Overlay arguments are unsupported at byte {node.start}')
            continue
        line = bisect_right(newlines, mark.start) + 1
        start = newlines[line - 2] + 1 if line > 1 else 0
        finding = dict(rule_id=RULE, grade=selected[0]['grade'], path=path, line=line,
                       column=len(source[start:mark.start].decode('utf-8')) + 1,
                       start=mark.start, end=node.end, source=selected[0]['source'],
                       explanation='This raw overlay is outside a display if the helper and source execute as expected.')
        target = 'suppressed' if any(scope.covers(RULE, mark.start) for scope in scopes) else 'diagnostics'
        result[target].append(finding)
    result['unresolved'] = list(dict.fromkeys(result['unresolved']))
    return result
