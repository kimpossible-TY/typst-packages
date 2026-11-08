"""Conditional detail for punctuation immediately following prose footnotes.

This is an independent source-pattern diagnostic, not a replacement for the
existing footnote rule or a proposal to move punctuation or edit a document.
"""
from document_sequence_preview import _FindingMapper
from conditional_highlight import _module_alias
import footnote_rules
import import_graph
from static_resolver import resolve
from typst_lint import InputError, SafetyError, digest, load_rules

RULE_ID = footnote_rules.PUNCT
CONDITIONS = (
    'The literal footnote call resolves at runtime to the intended built-in footnote.',
    'The source call executes as prose in the captured document context.',
    'Runtime sources and imports match the analyzed snapshot; initialization and rendering are not proved.',
)


def _explicit_footnote_shadow(tree):
    """Conservatively refuse explicit local bindings, including unrelated scopes."""
    nodes = tree.nodes
    def contains_name(root):
        pending = [root]
        while pending:
            index = pending.pop()
            node = nodes[index]
            if node.kind == 'Ident' and tree.source[node.start:node.end] == b'footnote':
                return True
            pending.extend(node.children)
        return False
    for index, node in enumerate(nodes):
        parts = import_graph.significant(tree, index) if node.kind in (
            'LetBinding', 'Binary', 'DestructAssignment', 'Closure', 'ForLoop', 'ModuleImport') else ()
        if node.kind == 'LetBinding' and len(parts) >= 2:
            target = parts[1]
            if nodes[target].kind == 'Closure':
                closure = import_graph.significant(tree, target)
                if closure and nodes[closure[0]].kind == 'Ident' and contains_name(closure[0]):
                    return True
            elif contains_name(target):
                return True
        elif node.kind == 'Closure':
            for child in parts:
                if nodes[child].kind == 'Params' and contains_name(child):
                    return True
        elif node.kind == 'Binary' and parts and any(nodes[x].kind in
                ('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq') for x in parts):
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
            if module is not None and module[1] == 'footnote':
                return True
            shape = import_graph.import_shape(tree, index)
            if shape is not None and shape.items is not None and any(local == 'footnote' for _, local in shape.items):
                return True
            # A named module import can also shadow the builtin. Check its
            # syntactic destination without treating a wildcard as evidence.
            for pos, part in enumerate(parts[:-1]):
                if nodes[part].kind == 'As' and nodes[parts[pos + 1]].kind == 'Ident' \
                        and tree.source[nodes[parts[pos + 1]].start:nodes[parts[pos + 1]].end] == b'footnote':
                    return True
    return False


def analyze(tree, path, *, catalog=None):
    """Classify the adjacent trailing-punctuation subset of nested hints.

    The result is partial, source-bound and explicitly conditional. Existing
    suppressions are retained. No punctuation is moved, even for simple bodies.
    """
    records = load_rules()[0]['rules'] if catalog is None else catalog
    source, nodes = tree.source, tree.nodes
    result = dict(path=path, source_sha256=digest(source), status='CONDITIONAL',
                  diagnostics=[], suppressed=[], unresolved=[], conditions=CONDITIONS,
                  edits=[], runtime_binding_proved=False, execution_proved=False,
                  render_equivalence_proved=False, edit_permission=False, compiler_permission=False)
    try:
        if tree.mode != 'markup' or nodes[0].errors:
            raise SafetyError('Requires well-formed markup source')
        if _explicit_footnote_shadow(tree):
            result['unresolved'].append('An explicit footnote binding or mutation is present; source is withheld')
            return result
        resolution = resolve(tree, packages=())
        notes = footnote_rules.analyze_conditional_nested(tree, path, resolution, catalog=records)[RULE_ID]
        mapper = _FindingMapper(source)
        parent = {child: (index, position) for index, node in enumerate(nodes)
                  for position, child in enumerate(node.children)}
        calls = {node.start - 1: index for index, node in enumerate(nodes)
                 if node.kind == 'FuncCall' and node.start and source[node.start - 1:node.start] == b'#'}
        for findings, output in ((notes.diagnostics, result['diagnostics']),
                                 (notes.suppressed, result['suppressed'])):
            for finding in findings:
                start = mapper.offset(finding)
                index = calls.get(start)
                if index is None or index not in parent:
                    continue
                call = nodes[index]
                container_id, position = parent[index]
                container = nodes[container_id]
                if container.kind != 'Markup':
                    continue
                siblings = container.children
                if position < 2 or position + 1 >= len(siblings):
                    continue
                mark, previous, following = (nodes[siblings[position - 1]], nodes[siblings[position - 2]],
                                             nodes[siblings[position + 1]])
                if (mark.kind != 'Hash' or mark.start != start or previous.end != start
                        or previous.kind not in ('Text', 'Ref', 'Equation')
                        or (previous.kind == 'Equation' and previous.display)
                        or following.kind != 'Text' or following.start != call.end
                        or source[call.end:call.end + 1] not in (b'.', b',')):
                    continue
                before = source[start - 1:start]
                if not before or before in b' \t\r\n.,;:!?':
                    continue
                if call.end + 1 < container.end and source[call.end + 1:call.end + 2] not in (b' ', b'\t', b'\r', b'\n'):
                    continue
                output.append(dict(path=path, rule_id=RULE_ID, start=start, end=call.end,
                    punctuation_start=call.end, punctuation_end=call.end + 1,
                    punctuation=source[call.end:call.end + 1].decode('ascii'), preceding_kind=previous.kind,
                    explanation='This adjacent source footnote has a comma or period immediately after it; '
                                'its placement is conditional on the built-in footnote and prose context.'))
        return result
    except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
        result.update(status='WITHHELD', diagnostics=[], suppressed=[], unresolved=[str(error)])
        return result
