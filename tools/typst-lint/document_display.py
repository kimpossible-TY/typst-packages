"""Diagnostic-only display gaps in direct, possibly nested markup bodies.

These are source-style observations, independent of whether an enclosing call
or closure executes. No helper binding, rendering context or repair is proved.
"""
from display_rules import (CONTINUATION_GAP as RULE_ID, DisplayResult,
                           _diagnostic, _horizontal, _line_prefix, _line_suffix)
from suppression import resolve as resolve_suppressions
from typst_lint import load_rules

_WHITESPACE = frozenset(('Space', 'Parbreak'))
_EXCLUDED_ANCESTORS = frozenset(('Equation', 'Math', 'Code', 'CodeBlock',
    'Heading', 'ListItem', 'EnumItem', 'TermItem', 'Raw', 'Str'))
_STRUCTURAL = frozenset(('Heading', 'ListItem', 'EnumItem', 'TermItem',
                         'Raw', 'Linebreak'))


def analyze(tree, path, *, catalog=None):
    """Inspect immediate source siblings only; return no edits or candidates."""
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        errors = tuple((node.start, node.end) for node in nodes if node.kind == 'Error')
        return DisplayResult(skips=('Malformed Typst syntax; no source-gap diagnosis',),
                             parse_errors=errors or ((0, len(source)),))
    catalog = load_rules()[0]['rules'] if catalog is None else catalog
    rules = {rule['id']: rule for rule in catalog}
    scopes = resolve_suppressions(tree, set(rules))
    parents = {child: index for index, node in enumerate(nodes) for child in node.children}
    positions = {child: position for node in nodes for position, child in enumerate(node.children)}
    # Preorder parser nodes let this retain ancestor exclusions in linear time.
    excluded = {}
    for index, node in enumerate(nodes):
        parent = parents.get(index)
        excluded[index] = (parent is not None and
            (excluded[parent] or nodes[parent].kind in _EXCLUDED_ANCESTORS))
    findings, suppressed, skips = [], [], []

    def skip(equation, reason):
        skips.append(f'{reason} at byte {equation.start}')

    def following(siblings, position):
        position += 1
        while position < len(siblings) and nodes[siblings[position]].kind in _WHITESPACE:
            position += 1
        return position if position < len(siblings) else None

    for index, equation in enumerate(nodes):
        if equation.kind != 'Equation' or not equation.display:
            continue
        parent = parents.get(index)
        if (tree.mode != 'markup' or parent is None or nodes[parent].kind != 'Markup'
                or excluded[index]):
            skip(equation, 'Display is outside an ordinary source Markup body')
            continue
        siblings = nodes[parent].children
        after_position = following(siblings, positions[index])
        if after_position is None:
            continue
        end = equation.end
        after = nodes[siblings[after_position]]
        if after.kind == 'Label':
            if not _horizontal(source[end:after.start]):
                skip(equation, 'Semantic label is not attached on the display closing line')
                continue
            end = after.end
            after_position = following(siblings, after_position)
            if after_position is None:
                continue
            after = nodes[siblings[after_position]]
            if after.kind == 'Label':
                skip(equation, 'Multiple semantic labels have unresolved display ownership')
                continue
        gap = source[end:after.start]
        if after.kind in _STRUCTURAL or (after.kind == 'Equation' and after.display):
            continue
        if after.kind != 'Text':
            skip(equation, 'Following construct is not direct literal unmarked prose; no boundary crossed')
            continue
        literal = source[after.start:after.end].decode('utf-8')
        if not literal or literal[0].isspace() or literal[-1].isspace():
            skip(equation, 'Following Text does not have a direct literal prose start')
            continue
        if gap.strip(b' \t\r\n') or b'\r' in gap.replace(b'\r\n', b''):
            skip(equation, 'Display gap contains unsupported whitespace or bare CR')
            continue
        if gap.count(b'\n') < 2:
            continue
        if (_line_prefix(source, equation.start).strip(b' \t')
                or _line_suffix(source, end).strip(b' \t\r')):
            skip(equation, 'Display delimiters are not already on standalone source lines')
            continue
        finding = _diagnostic(source, path, rules[RULE_ID], equation.start,
            'Use one source newline between a standalone display and its unmarked prose continuation.')
        if any(scope.covers(RULE_ID, equation.start) for scope in scopes):
            suppressed.append(finding)
        else:
            findings.append(finding)
    return DisplayResult(diagnostics=tuple(findings), suppressed=tuple(suppressed),
                         skips=tuple(dict.fromkeys(skips)))
