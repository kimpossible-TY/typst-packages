"""Conditional root marker-gap hints after a heading, display or literal call.

This recognizes source syntax only. Marker binding, execution and rendering
remain unproved, and no source edit or compilation permission is granted.
"""
from bisect import bisect_left
from collections.abc import Mapping
import re

from conditional_highlight import _module_alias
from document_conditional_marker_tags import _has_name
import import_graph
from paragraph_rules import MARKER_GAP, ParagraphResult
from suppression import resolve as resolve_suppressions
from typst_lint import Diagnostic, InputError, load_rules

_NAME = 'paragraph_tab'
_ASSIGNMENTS = frozenset(('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq'))
_CONTENT_CALLS = frozenset((b'block', b'definition', b'flowbox'))


def _direct_content_call(tree, children, position):
    """A named, block-shaped #name[...] sibling without dynamic arguments."""
    if position < 1:
        return False
    nodes = tree.nodes
    call = nodes[children[position]]
    hash_node = nodes[children[position - 1]]
    if call.kind != 'FuncCall' or hash_node.kind != 'Hash' or hash_node.end != call.start:
        return False
    parts = import_graph.significant(tree, children[position])
    if len(parts) != 2 or nodes[parts[0]].kind != 'Ident' or nodes[parts[1]].kind != 'Args':
        return False
    callee = nodes[parts[0]]
    if tree.source[callee.start:callee.end] not in _CONTENT_CALLS:
        return False
    args = import_graph.significant(tree, parts[1])
    return (len(args) == 1 and nodes[args[0]].kind == 'ContentBlock'
            and nodes[args[0]].end == call.end)


def _explicit_shadow(tree):
    """Withhold explicitly rebound names; wildcard identity is conditional."""
    nodes = tree.nodes
    def named(index):
        return _has_name(tree, index, _NAME)
    for index, node in enumerate(nodes):
        if node.kind in ('ModuleImport', 'RenamedImportItem'):
            parts = import_graph.significant(tree, index)
            if node.kind == 'ModuleImport':
                module = _module_alias(tree, index)
                if module is not None and module[1] == _NAME:
                    return True
                shape = import_graph.import_shape(tree, index)
                if shape is not None and shape.items is not None:
                    if any(local == _NAME and original != local for original, local in shape.items):
                        return True
            if any(nodes[part].kind == 'As' and nodes[target].kind == 'Ident' and named(target)
                   for part, target in zip(parts, parts[1:])):
                return True
            continue
        parts = import_graph.significant(tree, index) if node.kind in (
            'LetBinding', 'Closure', 'ForLoop', 'Binary', 'DestructAssignment') else ()
        if node.kind == 'LetBinding' and len(parts) >= 2:
            target = parts[1]
            if nodes[target].kind == 'Closure':
                inner = import_graph.significant(tree, target)
                target = inner[0] if inner else target
            if named(target):
                return True
        elif node.kind == 'Closure':
            if any(nodes[part].kind == 'Params' and named(part) for part in parts):
                return True
        elif node.kind == 'ForLoop':
            for part in parts[1:]:
                if nodes[part].kind == 'In':
                    break
                if named(part):
                    return True
        elif node.kind == 'Binary' and parts and any(nodes[part].kind in _ASSIGNMENTS for part in parts):
            if named(parts[0]):
                return True
        elif node.kind == 'DestructAssignment' and parts and named(parts[0]):
            return True
    return False


def analyze(tree, path, *, catalog=None):
    """Return source-only hints for exact root adjacency, never edits."""
    records = load_rules()[0]['rules'] if catalog is None else catalog
    if (type(records) not in (list, tuple) or any(not isinstance(row, Mapping)
            or type(row.get('id')) is not str or not row['id'] for row in records)):
        raise InputError('Conditional root marker analysis requires rule metadata')
    selected = [row for row in records if row['id'] == MARKER_GAP]
    if (len(selected) != 1 or any(type(selected[0].get(key)) is not str or not selected[0][key]
                                  for key in ('grade', 'source'))):
        raise InputError('Missing or invalid paragraph marker gap metadata')
    source, nodes = tree.source, tree.nodes
    if tree.mode != 'markup' or nodes[0].kind != 'Markup' or nodes[0].errors:
        return ParagraphResult()
    if _explicit_shadow(tree):
        return ParagraphResult(skips=('Explicit paragraph_tab shadow or mutation; root marker hints withheld',))
    children = nodes[0].children
    scopes = resolve_suppressions(tree, {row['id'] for row in records})
    newlines = [index for index, byte in enumerate(source) if byte == 10]
    findings, suppressed = [], []
    for position, index in enumerate(children):
        marker = nodes[index]
        if (position < 3 or marker.kind != 'Ident' or source[marker.start:marker.end] != b'paragraph_tab'):
            continue
        mark = nodes[children[position - 1]]
        if mark.kind != 'Hash' or mark.end != marker.start:
            continue
        start = mark.start
        line_index = bisect_left(newlines, start)
        line_start = 0 if not line_index else newlines[line_index - 1] + 1
        line_end = newlines[line_index] if line_index < len(newlines) else len(source)
        if source[line_start:start].strip(b' \t') or source[marker.end:line_end].strip(b' \t\r'):
            continue
        previous = nodes[children[position - 2]]
        if previous.kind != 'Space' or re.fullmatch(rb'\r?\n[ \t]*', source[previous.start:start]) is None:
            continue
        prior_pos = position - 3
        prior = nodes[children[prior_pos]]
        if prior.kind == 'Label':
            # The only admitted label is attached to the display on the same line.
            if prior_pos < 2:
                continue
            spacing = nodes[children[prior_pos - 1]]
            owner = nodes[children[prior_pos - 2]]
            if (spacing.kind != 'Space' or re.fullmatch(rb'[ \t]+', source[owner.end:prior.start]) is None
                    or owner.kind != 'Equation' or owner.display is not True):
                continue
            prior = owner
        if not (prior.kind == 'Heading' or prior.kind == 'Equation' and prior.display is True
                or _direct_content_call(tree, children, prior_pos)):
            continue
        # Keep target analysis separate; this path only admits following prose.
        following = position + 1
        while following < len(children) and nodes[children[following]].kind in ('Space', 'Parbreak'):
            following += 1
        if following == len(children) or nodes[children[following]].kind != 'Text':
            continue
        line = line_index + 1
        column = len(source[line_start:start].decode('utf-8')) + 1
        rule = selected[0]
        finding = Diagnostic(MARKER_GAP, rule['grade'], path, line, column,
            'Conditional source hint: if this standalone name is the paragraph marker, '
            'place a blank source line before it.', rule['source'])
        (suppressed if any(scope.covers(MARKER_GAP, start) for scope in scopes) else findings).append(finding)
    return ParagraphResult(diagnostics=tuple(findings), suppressed=tuple(suppressed))
