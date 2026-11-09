"""Conditionally inspect scoped annotations with a captured CeTZ draw import.

The result never proves callback execution, runtime package selection or display
ownership and grants no authority to write or compile consumer documents.
"""
from bisect import bisect_right

import document_cetz_draw_evidence as cetz_evidence
import document_conditional_detached_annotations as detached
import document_footnote_evidence as resources_evidence
from package_artifacts import CETZ, CETZ_ARTIFACTS
from suppression import resolve as resolve_suppressions
from typst_lint import InputError, SafetyError, digest, load_rules, require

RULE = detached.RULE
MAX_CALLBACKS = 256
MAX_NODE_WORK = 2_000_000
CONDITIONS = detached.CONDITIONS + (
    'The runtime cetz binding selects the captured, reviewed CeTZ 0.4.2 artifact.',
    'Runtime draw bindings and behavior match the captured sources; transitive effects remain unproved.',
)


def _supported_args(tree, call, parameter, exports):
    """Require one direct draw import followed only by captured draw calls."""
    nodes, source = tree.nodes, tree.source
    parts = detached._parts(tree, call)
    args = [i for i in detached._parts(tree, parts[1])
            if nodes[i].kind not in ('LeftParen', 'RightParen', 'Comma')]
    if len(args) != 3 or nodes[args[1]].kind != 'Ident' or detached._text(tree, args[1]) != 'cetz':
        return None
    if nodes[args[2]].kind != 'CodeBlock':
        return None
    # The target list must be literal; arbitrary evaluation could have effects.
    pending = [args[0]]
    while pending:
        index = pending.pop()
        node = nodes[index]
        if node.kind not in ('Array', 'Str', 'LeftParen', 'RightParen', 'Comma', 'Space'):
            return None
        pending.extend(node.children)
    imports, calls, pending = [], [], [args[2]]
    parents = {child: i for i, node in enumerate(nodes) for child in node.children}
    while pending:
        index = pending.pop()
        node = nodes[index]
        if node.kind == 'ModuleImport':
            fields = detached._parts(tree, index)
            if (tuple(nodes[i].kind for i in fields) != ('Import', 'FieldAccess', 'Colon', 'Star')
                    or source[nodes[fields[1]].start:nodes[fields[1]].end] != b'cetz.draw'):
                return None
            imports.append(index)
            continue
        if (node.kind in detached._BARRIERS and node.kind != 'CodeBlock'
                or node.kind == 'Binary' and any(nodes[i].kind in detached._ASSIGNMENTS
                                                  for i in detached._parts(tree, index))):
            return None
        if node.kind == 'FuncCall':
            call_parts = detached._parts(tree, index)
            if len(call_parts) != 2:
                return None
            callee = call_parts[0]
            if nodes[callee].kind == 'Ident':
                if detached._text(tree, callee) not in exports:
                    return None
            else:
                wrapped = detached._parts(tree, callee)
                if (nodes[callee].kind != 'Parenthesized' or len(wrapped) != 3
                        or tuple(nodes[i].kind for i in wrapped) != ('LeftParen', 'FieldAccess', 'RightParen')):
                    return None
                field = detached._parts(tree, wrapped[1])
                if (len(field) != 3 or tuple(nodes[i].kind for i in field) != ('Ident', 'Dot', 'Ident')
                        or detached._text(tree, field[0]) != parameter
                        or detached._text(tree, field[2]) != 'node'):
                    return None
                node_args = [i for i in detached._parts(tree, call_parts[1])
                             if nodes[i].kind not in ('LeftParen', 'RightParen', 'Comma')]
                if len(node_args) != 2 or any(nodes[i].kind != 'Str' for i in node_args):
                    return None
            calls.append(index)
        if node.kind == 'Ident' and detached._text(tree, index) == parameter:
            field = parents.get(index)
            fields = detached._parts(tree, field) if field is not None else ()
            if (field is None or nodes[field].kind != 'FieldAccess' or len(fields) != 3
                    or fields[0] != index or detached._text(tree, fields[2]) != 'node'):
                return None
            wrapped = parents.get(field)
            caller = parents.get(wrapped) if wrapped is not None else None
            if (wrapped is None or nodes[wrapped].kind != 'Parenthesized'
                    or caller is None or nodes[caller].kind != 'FuncCall'
                    or not detached._parts(tree, caller)
                    or detached._parts(tree, caller)[0] != wrapped):
                return None
        pending.extend(node.children)
    if len(imports) != 1 or any(nodes[i].start < nodes[imports[0]].end for i in calls):
        return None
    return nodes[imports[0]].start, nodes[imports[0]].end


def analyze(proof, path, *, catalog=None):
    """Report narrowly supported calls in the exact issued source snapshot."""
    resources = resources_evidence._valid(proof)
    if type(path) is not str or not path:
        raise InputError('A captured source path is required')
    item = proof._session._context.source_for(path)
    if item is None or resources.get(('project', path)) != item.source:
        raise SafetyError('Scoped annotation source is absent from the captured context')
    records = load_rules()[0]['rules'] if catalog is None else catalog
    baseline = detached._analyze_captured_tree(item.tree, path, catalog=records)
    result = dict(path=path, source_sha256=digest(item.source), status='PARTIAL', diagnostics=[], suppressed=[],
                  unresolved=[], conditions=CONDITIONS, runtime_binding_proved=False, execution_proved=False,
                  coverage_complete=False, edit_permission=False, compiler_permission=False)
    if baseline['status'] == 'UNAVAILABLE':
        result['status'] = 'UNAVAILABLE'
        result['unresolved'] = list(baseline['unresolved'])
        return result
    # Only improve the exact unresolved source-pattern class. Preserve all
    # existing unsupported paths in the original diagnostic report.
    prefix = 'Callback or annotation argument effects are unsupported at byte '
    offsets = []
    for reason in baseline['unresolved']:
        if reason.startswith(prefix):
            value = reason[len(prefix):]
            if value.isdecimal() and str(int(value)) == value:
                offsets.append(int(value))
                continue
        result['unresolved'].append(reason)
    require(len(offsets) <= MAX_CALLBACKS, 'CeTZ scoped annotation candidate limit exceeded')
    if not offsets:
        resources_evidence._valid(proof)
        return result
    tree, nodes, source = item.tree, item.tree.nodes, item.source
    require(len(nodes) * len(offsets) <= MAX_NODE_WORK, 'CeTZ scoped annotation work limit exceeded')
    parents = {child: i for i, node in enumerate(nodes) for child in node.children}
    index_by_start = {node.start: i for i, node in enumerate(nodes) if node.kind == 'FuncCall'}
    exports, _ = cetz_evidence._draw_exports(proof)
    choices = {item.import_path: item.root_index for item in proof._session._namespaces.selections}
    root_index = choices.get(CETZ)
    if root_index not in (0, 1):
        raise SafetyError('Captured CeTZ selection is unavailable')
    root_name = ('packages', 'cache')[root_index]
    artifacts = dict(CETZ_ARTIFACTS)
    for relative in ('src/draw/shapes.typ', 'src/draw/styling.typ'):
        raw = resources.get((root_name, 'preview/cetz/0.4.2/' + relative))
        if raw is None or digest(raw) != artifacts[relative]:
            raise SafetyError('Captured CeTZ draw implementation differs from the reviewed artifact')
    # Other draw modules and the reviewed files' transitive effects are not
    # established here. Keep the supported function set deliberately small.
    exports = exports & frozenset(('line', 'bezier-through', 'set-style'))
    scopes = resolve_suppressions(tree, {row['id'] for row in records})
    selected_rule = next(row for row in records if row['id'] == RULE)
    newlines = [i for i, byte in enumerate(source) if byte == 10]
    for offset in offsets:
        index = index_by_start.get(offset)
        cursor = index
        while cursor is not None and nodes[cursor].kind != 'Closure':
            cursor = parents.get(cursor)
        closure = cursor
        wrapper = parents.get(closure) if closure is not None else None
        while wrapper is not None and nodes[wrapper].kind != 'FuncCall':
            wrapper = parents.get(wrapper)
        if index is None or closure is None or wrapper is None:
            result['unresolved'].append(f'Callback ownership is unavailable at byte {offset}')
            continue
        ancestor = parents.get(wrapper)
        unsafe_ancestor = False
        while ancestor is not None:
            if nodes[ancestor].kind in ('Closure', 'ForLoop', 'WhileLoop', 'Conditional', 'Contextual', 'ShowRule'):
                unsafe_ancestor = True
                break
            ancestor = parents.get(ancestor)
        if unsafe_ancestor:
            result['unresolved'].append(f'Enclosing callback context is unsupported at byte {offset}')
            continue
        callback, _ = detached._scoped_body(tree, wrapper, 'local-scope-annotations')
        if callback is None:
            result['unresolved'].append(f'Callback shape is unavailable at byte {offset}')
            continue
        body, names = callback
        parameter = names[0]
        if parameter in exports or not detached._annotation_call(tree, index, parameter):
            result['unresolved'].append(f'Callback receiver is unavailable at byte {offset}')
            continue
        markup = [i for i in nodes[body].children if nodes[i].kind == 'Markup']
        siblings = nodes[markup[0]].children if len(markup) == 1 else ()
        position = siblings.index(index) if index in siblings else -1
        if (position <= 0 or nodes[siblings[position - 1]].kind != 'Hash'
                or nodes[siblings[position - 1]].end != nodes[index].start
                or not all(detached._safe_prefix(tree, i, parameter, parents) for i in siblings[:position - 1])):
            result['unresolved'].append(f'Prior callback effects are unsupported at byte {offset}')
            continue
        # Never accept a nested deferred or repeated execution context within
        # this callback merely because the draw import itself looks literal.
        stack = list(nodes[closure].children)
        if any_kind(tree, stack, {'Closure', 'ForLoop', 'WhileLoop', 'Conditional', 'Contextual', 'ShowRule'}):
            result['unresolved'].append(f'Nested callback context is unsupported at byte {offset}')
            continue
        selected = _supported_args(tree, index, parameter, exports)
        if selected is None:
            result['unresolved'].append(f'CeTZ draw argument shape is unsupported at byte {offset}')
            continue
        try:
            proven = cetz_evidence.supports_imports(proof, path, source, nodes[closure].start,
                                                    nodes[closure].end, (selected,))
        except SafetyError as error:
            result['unresolved'].append(f'CeTZ draw capture is unresolved at byte {offset}: {error}')
            continue
        if not proven:
            result['unresolved'].append(f'CeTZ draw binding is unresolved at byte {offset}')
            continue
        start = nodes[siblings[position - 1]].start
        line = bisect_right(newlines, start) + 1
        line_start = newlines[line - 2] + 1 if line > 1 else 0
        finding = dict(rule_id=RULE, grade=selected_rule['grade'], path=path, line=line,
                       column=len(source[line_start:start].decode('utf-8')) + 1,
                       start=start, end=nodes[index].end, source=selected_rule['source'],
                       wrapper_start=nodes[wrapper].start,
                       certainty='conditional',
                       explanation='This scoped annotation is outside a display if the captured CeTZ and callback conditions hold.')
        key = 'suppressed' if any(scope.covers(RULE, start) for scope in scopes) else 'diagnostics'
        result[key].append(finding)
    resources_evidence._valid(proof)
    return result


def any_kind(tree, stack, kinds):
    while stack:
        index = stack.pop()
        if tree.nodes[index].kind in kinds:
            return True
        stack.extend(tree.nodes[index].children)
    return False
