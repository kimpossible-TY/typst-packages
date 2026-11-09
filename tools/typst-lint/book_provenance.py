"""Immutable configured-root context and bounded document-transform syntax.

These helpers do not grant callable identity. Only the source-order resolver,
after checking exact package bytes and lexical bindings, issues BookProof.
"""
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from static_resolver import Binding


@dataclass(frozen=True)
class EntrypointSnapshot:
    path: str
    source: bytes


@dataclass(frozen=True)
class ShowShape:
    expression: int
    receiver: int
    arguments: int | None = None


@dataclass(frozen=True)
class BookApplication:
    node_id: int
    binding: 'Binding'
    reset_binding: 'Binding'
    reset_enabled: bool | None
    bare: bool


@dataclass(frozen=True)
class BookProof:
    source_sha256: str
    applications: tuple[BookApplication, ...] = ()


_TRIVIA = frozenset(('Space', 'Parbreak', 'LineComment', 'BlockComment'))
_METADATA_OPTIONS = frozenset(('title', 'author', 'description'))


def significant(tree, index):
    return tuple(c for c in tree.nodes[index].children if tree.nodes[c].kind not in _TRIVIA)


def raw(tree, index):
    node = tree.nodes[index]
    return tree.source[node.start:node.end]


def _unparenthesized(tree, index):
    while tree.nodes[index].kind == 'Parenthesized':
        parts = tuple(c for c in significant(tree, index)
                      if tree.nodes[c].kind not in ('LeftParen', 'RightParen'))
        if len(parts) != 1:
            return None
        index = parts[0]
    return index


def document_show_shape(tree, index):
    """Accept a bare value or one .with call, never a selector or call result."""
    parts = significant(tree, index)
    if (tree.nodes[index].kind != 'ShowRule' or len(parts) != 3
            or tree.nodes[parts[0]].kind != 'Show' or tree.nodes[parts[1]].kind != 'Colon'):
        return None
    expression = parts[2]
    value = _unparenthesized(tree, expression)
    if value is None:
        return None
    if tree.nodes[value].kind in ('Ident', 'FieldAccess'):
        return ShowShape(expression, value)
    if tree.nodes[value].kind != 'FuncCall':
        return None
    call = significant(tree, value)
    if len(call) != 2 or tree.nodes[call[1]].kind != 'Args':
        return None
    member = significant(tree, call[0])
    if (tree.nodes[call[0]].kind != 'FieldAccess' or len(member) != 3
            or tree.nodes[member[1]].kind != 'Dot' or tree.nodes[member[2]].kind != 'Ident'
            or raw(tree, member[2]) != b'with'):
        return None
    return ShowShape(expression, member[0], call[1])


def value_binding(tree, index, lookup, package_binding):
    """Read only pure lexical values using the resolver's live environment."""
    index = _unparenthesized(tree, index)
    if index is None:
        return None
    if tree.nodes[index].kind == 'Ident':
        return lookup(raw(tree, index).decode('utf-8'))
    if tree.nodes[index].kind != 'FieldAccess':
        return None
    parts = significant(tree, index)
    if (len(parts) != 3 or tree.nodes[parts[1]].kind != 'Dot'
            or tree.nodes[parts[2]].kind != 'Ident'):
        return None
    receiver = value_binding(tree, parts[0], lookup, package_binding)
    if receiver is None or receiver.kind != 'module':
        return None
    return package_binding(receiver.package, raw(tree, parts[2]).decode('utf-8'))


def literal_options(tree, arguments, *, book):
    """Return (supported, reset-enabled); reject every dynamic/positional value.

    Only string/none metadata and a boolean reset option are certified. Other
    options need their own type/default/effect contracts, even if literal.
    """
    if arguments is None:
        return True, True if book else None
    parts = significant(tree, arguments)
    if len(parts) < 2 or tree.nodes[parts[0]].kind != 'LeftParen' or tree.nodes[parts[-1]].kind != 'RightParen':
        return False, None
    options = tuple(c for c in parts[1:-1] if tree.nodes[c].kind != 'Comma')
    if not book:
        return not options, None
    seen = set()
    enabled = True
    for option in options:
        named = significant(tree, option)
        if (tree.nodes[option].kind != 'Named' or len(named) != 3
                or tree.nodes[named[0]].kind != 'Ident' or tree.nodes[named[1]].kind != 'Colon'):
            return False, None
        name = raw(tree, named[0]).decode('utf-8')
        if name in seen:
            return False, None
        seen.add(name)
        if name == 'reset-math-blocks':
            if tree.nodes[named[2]].kind != 'Bool' or raw(tree, named[2]) not in (b'true', b'false'):
                return False, None
            enabled = raw(tree, named[2]) == b'true'
        elif name not in _METADATA_OPTIONS or tree.nodes[named[2]].kind not in ('Str', 'None'):
            return False, None
    return True, enabled
