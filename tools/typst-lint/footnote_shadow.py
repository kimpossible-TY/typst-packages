"""Conservative explicit source shadows of the canonical builtin footnote."""
from conditional_highlight import _module_alias
import import_graph


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
            'LetBinding', 'Binary', 'DestructAssignment', 'Closure', 'ForLoop', 'ModuleImport',
            'RenamedImportItem') else ()
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
            # Unaliased field-access module imports bind their last field as
            # the local module name. An item import or an explicit alias has
            # different destinations and is handled below.
            if len(parts) == 2 and nodes[parts[1]].kind == 'FieldAccess':
                field = import_graph.significant(tree, parts[1])
                if (len(field) == 3 and nodes[field[1]].kind == 'Dot'
                        and nodes[field[2]].kind == 'Ident' and contains_name(field[2])):
                    return True
            shape = import_graph.import_shape(tree, index)
            if shape is not None and shape.items is not None and any(local == 'footnote' for _, local in shape.items):
                return True
            # Combined ``as module: items`` imports are not import_shape().
            # Inspect each unrenamed destination; renamed items are checked
            # independently below, so ``footnote as other`` stays distinct.
            for part in parts:
                if nodes[part].kind != 'ImportItems':
                    continue
                for item in import_graph.significant(tree, part):
                    if nodes[item].kind != 'ImportItemPath':
                        continue
                    names = [child for child in import_graph.significant(tree, item)
                             if nodes[child].kind == 'Ident']
                    if names and contains_name(names[-1]):
                        return True
            # A named module import can also shadow the builtin. Check its
            # syntactic destination without treating a wildcard as evidence.
            for pos, part in enumerate(parts[:-1]):
                if nodes[part].kind == 'As' and nodes[parts[pos + 1]].kind == 'Ident' \
                        and tree.source[nodes[parts[pos + 1]].start:nodes[parts[pos + 1]].end] == b'footnote':
                    return True
        elif node.kind == 'RenamedImportItem':
            for pos, part in enumerate(parts[:-1]):
                target = nodes[parts[pos + 1]]
                if (nodes[part].kind == 'As' and target.kind == 'Ident'
                        and tree.source[target.start:target.end] == b'footnote'):
                    return True
    return False
