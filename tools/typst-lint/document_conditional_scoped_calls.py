"""Read-only conditional previews of direct scoped-dictionary function calls.

This source-pattern experiment does not prove wrapper or package bindings,
execution, rendering, compilation, or permission to modify a document.
"""
from conditional_highlight import _SCOPED_CALLBACKS, _module_alias
from document_gap_preview import _MappedEdits, _scopes
from edit_plan import Edit, plan
import import_graph
from scoped_provenance import FIELDS
from suppression import resolve as suppressions
from syntax_client import parse
from typst_lint import InputError, SafetyError, digest, load_rules

RULE = 'core.scoped-function-call'
CONDITIONS = (
    'The literal wrapper resolves at runtime to the reviewed scoped-annotations helper with its reviewed callback contract.',
    'The wrapper invokes this literal callback with its intended scope dictionary.',
    'The source, dependencies and runtime context match the reviewed snapshot; execution and rendering are not proved.',
)
_WRAPPERS = {'local-tag-scope': FIELDS - {'node', 'annot', 'mark'},
             'local-scope-annotations': FIELDS}
_BARRIERS = frozenset(('LetBinding', 'Closure', 'Contextual', 'CodeBlock', 'ModuleImport',
                       'ModuleInclude', 'ForLoop', 'WhileLoop', 'Conditional', 'FuncReturn',
                       'DestructAssignment'))
_OUTER_BARRIERS = frozenset(('Closure', 'Contextual', 'CodeBlock', 'ForLoop',
                             'WhileLoop', 'Conditional'))
_ASSIGNMENTS = frozenset(('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq'))


def _parts(tree, index):
    return import_graph.significant(tree, index)


def _text(tree, index):
    node = tree.nodes[index]
    return tree.source[node.start:node.end].decode('utf-8')


def _scoped_body(tree, index, wrapper):
    parts = _parts(tree, index)
    if len(parts) != 2 or tree.nodes[parts[1]].kind != 'Args':
        return None, 'Scoped wrapper requires a direct callback'
    positional, named = [], set()
    for part in _parts(tree, parts[1]):
        kind = tree.nodes[part].kind
        if kind in ('LeftParen', 'RightParen', 'Comma'):
            continue
        if kind != 'Named':
            positional.append(part)
            continue
        fields = _parts(tree, part)
        if len(fields) != 3 or tree.nodes[fields[0]].kind != 'Ident':
            return None, 'Unsupported scoped-wrapper option'
        name = _text(tree, fields[0])
        kind = tree.nodes[fields[2]].kind
        allowed = ('Auto', 'Ident') if name == 'parent' else ('Auto', 'Str')
        if name not in _SCOPED_CALLBACKS[wrapper] or name in named or kind not in allowed:
            return None, 'Scoped-wrapper options are not inert supported literals or parent identifiers'
        named.add(name)
    if len(positional) != 1 or tree.nodes[positional[0]].kind != 'Closure':
        return None, 'Scoped wrapper requires one literal callback'
    closure = _parts(tree, positional[0])
    if (len(closure) != 3 or tree.nodes[closure[0]].kind != 'Params'
            or tree.nodes[closure[1]].kind != 'Arrow'):
        return None, 'Scoped callback requires an anonymous arrow closure'
    params = _parts(tree, closure[0])
    if (len(params) == 3 and tree.nodes[params[0]].kind == 'LeftParen'
            and tree.nodes[params[2]].kind == 'RightParen'):
        params = (params[1],)
    if len(params) != 1 or tree.nodes[params[0]].kind != 'Ident':
        return None, 'Scoped callback requires one identifier parameter'
    body = closure[2]
    while tree.nodes[body].kind == 'Parenthesized':
        inner = _parts(tree, body)
        if (len(inner) != 3 or tree.nodes[inner[0]].kind != 'LeftParen'
                or tree.nodes[inner[2]].kind != 'RightParen'):
            return None, 'Scoped callback body is unsupported'
        body = inner[1]
    if tree.nodes[body].kind != 'ContentBlock':
        return None, 'Scoped callback body must be literal content'
    return (body, (_text(tree, params[0]),)), None


def _explicit_wrapper_shadows(tree):
    shadows = set()
    for node_id, node in enumerate(tree.nodes):
        if node.kind in ('ModuleImport', 'RenamedImportItem'):
            parts = _parts(tree, node_id)
            if node.kind == 'ModuleImport':
                module = _module_alias(tree, node_id)
                if module is not None and module[1] in _WRAPPERS:
                    shadows.add(module[1])
            for position, part in enumerate(parts[:-1]):
                target = parts[position + 1]
                if tree.nodes[part].kind == 'As' and tree.nodes[target].kind == 'Ident':
                    name = _text(tree, target)
                    if name in _WRAPPERS:
                        shadows.add(name)
            continue
        parts = _parts(tree, node_id) if node.kind in ('LetBinding', 'Binary', 'DestructAssignment') else ()
        if node.kind == 'LetBinding' and len(parts) >= 2:
            target = parts[1]
            if tree.nodes[target].kind == 'Closure':
                values = _parts(tree, target)
                target = values[0] if values else target
        elif (node.kind == 'Binary' and parts
              and any(tree.nodes[part].kind in _ASSIGNMENTS for part in parts)):
            target = parts[0]
        elif node.kind == 'DestructAssignment' and parts:
            target = parts[0]
        else:
            continue
        pending = [target]
        while pending:
            item = pending.pop()
            if tree.nodes[item].kind == 'Ident':
                name = _text(tree, item)
                if name in _WRAPPERS:
                    shadows.add(name)
            pending.extend(tree.nodes[item].children)
    return shadows


def _scan(tree, path, records):
    nodes, source = tree.nodes, tree.source
    scopes = suppressions(tree, {record['id'] for record in records})
    found, suppressed, reasons = [], [], []
    shadows = _explicit_wrapper_shadows(tree)
    parents = {child: index for index, node in enumerate(nodes) for child in node.children}
    for index, node in enumerate(nodes):
        parts = _parts(tree, index) if node.kind == 'FuncCall' else ()
        if len(parts) != 2 or nodes[parts[0]].kind != 'Ident':
            continue
        wrapper = _text(tree, parts[0])
        if wrapper not in _WRAPPERS:
            continue
        ancestor = parents.get(index)
        while ancestor is not None and nodes[ancestor].kind not in _OUTER_BARRIERS:
            ancestor = parents.get(ancestor)
        if ancestor is not None:
            reasons.append(f'Wrapper is inside an unsupported deferred or dynamic scope at byte {node.start}')
            continue
        if wrapper in shadows:
            reasons.append(f'Wrapper name is explicitly shadowed at byte {node.start}')
            continue
        callback, reason = _scoped_body(tree, index, wrapper)
        if callback is None:
            reasons.append(f'{reason} at byte {node.start}')
            continue
        body, parameters = callback
        parameter = parameters[0]
        pending = [body]
        candidates = []
        unsafe = False
        while pending:
            current = pending.pop()
            item = nodes[current]
            if item.kind in _BARRIERS or (item.kind == 'Binary'
                    and any(nodes[c].kind in _ASSIGNMENTS for c in _parts(tree, current))):
                unsafe = True
                break
            if item.kind in ('FuncCall', 'MathCall'):
                call_parts = _parts(tree, current)
                if (item.kind != 'FuncCall' or len(call_parts) != 2
                        or nodes[call_parts[1]].kind != 'Args'):
                    unsafe = True
                    break
                field = call_parts[0]
                already_wrapped = nodes[field].kind == 'Parenthesized'
                if already_wrapped:
                    wrapped = _parts(tree, field)
                    if (len(wrapped) != 3 or [nodes[x].kind for x in wrapped]
                            != ['LeftParen', 'FieldAccess', 'RightParen']):
                        unsafe = True
                        break
                    field = wrapped[1]
                if nodes[field].kind != 'FieldAccess':
                    unsafe = True
                    break
                field_parts = _parts(tree, field)
                if (len(field_parts) != 3 or [nodes[x].kind for x in field_parts] != ['Ident', 'Dot', 'Ident']
                        or _text(tree, field_parts[0]) != parameter
                        or _text(tree, field_parts[2]) not in _WRAPPERS[wrapper]):
                    unsafe = True
                    break
                if not already_wrapped:
                    candidates.append((current, field))
            pending.extend(reversed(item.children))
        if unsafe:
            reasons.append(f'Callback contains unsupported nested calls, mutation or deferred scope at byte {node.start}')
            continue
        for call, field in candidates:
            target = nodes[call]
            entry = dict(path=path, start=target.start, end=target.end, rule_id=RULE,
                         wrapper=wrapper, parameter=parameter,
                         explanation='Parenthesize this scope field if the wrapper and callback bindings are as expected.')
            (suppressed if any(scope.covers(RULE, target.start) for scope in scopes) else found).append((entry, field))
    return found, suppressed, tuple(dict.fromkeys(reasons))


def _signature(tree, wrappers=()):
    nodes, source = tree.nodes, tree.source
    parents = {child: index for index, node in enumerate(nodes) for child in node.children}
    removed, replacements = set(), {}
    expected = set(wrappers)
    for index, node in enumerate(nodes):
        if node.kind != 'Parenthesized' or (node.start, node.end) not in expected:
            continue
        children = _parts(tree, index)
        if (len(children) != 3 or [nodes[x].kind for x in children]
                != ['LeftParen', 'FieldAccess', 'RightParen']):
            raise SafetyError('Expected scoped field wrapper has changed syntax')
        removed.update((index, children[0], children[2]))
        replacements[index] = children[1]
        expected.remove((node.start, node.end))
    if expected:
        raise SafetyError('Expected scoped field wrapper is missing')
    kept = [i for i in range(len(nodes)) if i not in removed]
    remap = {old: new for new, old in enumerate(kept)}
    result = []
    for index in kept:
        node = nodes[index]
        parent = parents.get(index)
        if parent in replacements:
            parent = parents.get(parent)
        if parent is not None and parent not in remap:
            raise SafetyError('Unexpected removed syntax ancestor')
        if any(target not in remap for _, target in node.operands):
            raise SafetyError('Unexpected removed syntax operand')
        result.append((node.kind, remap.get(parent), node.display, node.errors,
                       tuple((name, remap[target]) for name, target in node.operands),
                       source[node.start:node.end] if not node.children else None))
    return tuple(result)


def analyze(tree, path, *, catalog=None):
    """Return conditional source-pattern findings and independent in-memory edits."""
    records = load_rules()[0]['rules'] if catalog is None else catalog
    source = tree.source
    empty = dict(path=path, source_sha256=digest(source), diagnostics=[], suppressed=[], edits=[],
                 conditions=CONDITIONS, runtime_binding_proved=False, execution_proved=False,
                 edit_permission=False, compiler_permission=False)
    try:
        if tree.mode != 'markup' or tree.nodes[0].errors:
            raise SafetyError('Requires well-formed markup source')
        found, suppressed, reasons = _scan(tree, path, records)
        result = dict(empty, status='CONDITIONAL', diagnostics=[entry for entry, _ in found],
                      suppressed=[entry for entry, _ in suppressed], unresolved=list(reasons))
        if not found:
            return result
        edits = tuple(Edit(tree.nodes[field].start, tree.nodes[field].end,
                           b'(' + source[tree.nodes[field].start:tree.nodes[field].end] + b')')
                      for _, field in found)
        proposal = plan(source, edits)
        if proposal.deferred or len(proposal.edits) != len(edits):
            raise SafetyError('Conflicting scoped-field proposals')
        candidate = proposal.candidate()
        parsed = parse(candidate, tree.mode)
        offsets = _MappedEdits(proposal.edits)
        wrappers = [(offsets.mapped(edit.start), offsets.mapped(edit.start) + len(edit.replacement))
                    for edit in proposal.edits]
        if (parsed.nodes[0].errors or _signature(tree) != _signature(parsed, wrappers)
                or _scopes(tree, records, proposal.edits) != _scopes(parsed, records)):
            raise SafetyError('Scoped-field candidate changes other syntax or suppression targets')
        remaining, after_suppressed, _ = _scan(parsed, path, records)
        if remaining:
            raise SafetyError('Scoped-field preview did not converge')
        expected_suppressed = [(offsets.mapped(entry['start']), offsets.mapped(entry['end']))
                               for entry, _ in suppressed]
        if expected_suppressed != [(entry['start'], entry['end']) for entry, _ in after_suppressed]:
            raise SafetyError('Scoped-field preview changes suppressed candidates')
        result.update(candidate_sha256=digest(candidate), verification='conditional-scoped-syntax-and-suppression-reanalysis',
                      edits=[dict(start=e.start, end=e.end, replacement=e.replacement.decode('utf-8'),
                                  rule_id=RULE) for e in proposal.edits])
        return result
    except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
        return dict(empty, status='WITHHELD', candidate_sha256=None, unresolved=[str(error)])
