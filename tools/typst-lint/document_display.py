"""Diagnostic-only display source style in direct, possibly nested markup bodies.

These are source-style observations, independent of whether an enclosing call
or closure executes. No helper binding, rendering context or repair is proved.
"""
from display_rules import (CONTINUATION_GAP as RULE_ID, STANDALONE, DisplayResult,
                           _diagnostic, _horizontal, _line_prefix, _line_suffix, _newline)
from edit_plan import Edit
from suppression import resolve as resolve_suppressions
from typst_lint import load_rules

_WHITESPACE = frozenset(('Space', 'Parbreak'))
_EXCLUDED_ANCESTORS = frozenset(('Equation', 'Math', 'Code', 'CodeBlock',
    'Heading', 'ListItem', 'EnumItem', 'TermItem', 'Raw', 'Str'))
_STRUCTURAL = frozenset(('Heading', 'ListItem', 'EnumItem', 'TermItem',
                         'Raw', 'Linebreak'))
RULE_IDS = (STANDALONE, RULE_ID)
_BOM = b'\xef\xbb\xbf'


def analyze(tree, path, *, catalog=None):
    """Return the existing gap-only result, including with a gap-only catalog."""
    return _analyze(tree, path, catalog=catalog, rule_ids=(RULE_ID,))[RULE_ID]


def analyze_all(tree, path, *, catalog=None):
    """Inspect both source rules in one scan; return no edits or candidates."""
    return _analyze(tree, path, catalog=catalog, rule_ids=RULE_IDS)


def preview_gap_edits(tree, path, *, catalog=None):
    """Return source-only gap proposals; callers must independently verify them.

    Unlike the diagnostic API, this opt-in result contains edits. It establishes
    neither execution context nor permission to modify a consumer document.
    """
    return _analyze(tree, path, catalog=catalog, rule_ids=(RULE_ID,),
                    include_gap_edits=True)[RULE_ID]


def preview_standalone_edits(tree, path, *, catalog=None):
    """Return source-only standalone proposals for independent verification."""
    return _analyze(tree, path, catalog=catalog, rule_ids=(STANDALONE,),
                    include_standalone_edits=True)[STANDALONE]


def _analyze(tree, path, *, catalog, rule_ids, include_gap_edits=False,
             include_standalone_edits=False):
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        errors = tuple((node.start, node.end) for node in nodes if node.kind == 'Error')
        return {rid: DisplayResult(
                    skips=(('Malformed Typst syntax; no source-gap diagnosis' if rid == RULE_ID
                            else 'Malformed Typst syntax; no source-standalone diagnosis'),),
                    parse_errors=errors or ((0, len(source)),))
                for rid in rule_ids}
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
    findings = {rid: [] for rid in rule_ids}
    suppressed = {rid: [] for rid in rule_ids}
    edits = {rid: [] for rid in rule_ids}
    skips = {rid: [] for rid in rule_ids}

    def skip(rid, equation, reason):
        skips[rid].append(f'{reason} at byte {equation.start}')

    def neighbor(siblings, position, direction):
        position += direction
        while 0 <= position < len(siblings) and nodes[siblings[position]].kind in _WHITESPACE:
            position += direction
        return position if 0 <= position < len(siblings) else None

    def literal_prose(index):
        node = nodes[index]
        if node.kind != 'Text':
            return False
        raw = source[node.start:node.end]
        if node.start == 0 and raw.startswith(_BOM):
            raw = raw[len(_BOM):]
        text = raw.decode('utf-8')
        # A punctuation-only Text (for example a period after an attached
        # equation label) does not establish adjacent prose for this rule.
        return (bool(text) and not text[0].isspace() and not text[-1].isspace()
                and any(character.isalpha() for character in text))

    def unknown_neighbor(index):
        node = nodes[index]
        return not (node.kind in _STRUCTURAL or (node.kind == 'Equation' and node.display)
                    or literal_prose(index))

    def add(rid, equation, explanation, proposal=None):
        finding = _diagnostic(source, path, rules[rid], equation.start, explanation)
        if any(scope.covers(rid, equation.start) for scope in scopes):
            suppressed[rid].append(finding)
        else:
            findings[rid].append(finding)
            if proposal is not None:
                edits[rid].extend(proposal)

    for index, equation in enumerate(nodes):
        if equation.kind != 'Equation' or not equation.display:
            continue
        parent = parents.get(index)
        if (tree.mode != 'markup' or parent is None or nodes[parent].kind != 'Markup'
                or excluded[index]):
            for rid in rule_ids:
                skip(rid, equation, 'Display is outside an ordinary source Markup body')
            continue
        siblings = nodes[parent].children
        after_position = neighbor(siblings, positions[index], 1)
        end = equation.end
        if after_position is not None and nodes[siblings[after_position]].kind == 'Label':
            after = nodes[siblings[after_position]]
            if not _horizontal(source[end:after.start]):
                for rid in rule_ids:
                    skip(rid, equation, 'Semantic label is not attached on the display closing line')
                continue
            end = after.end
            after_position = neighbor(siblings, after_position, 1)
            if after_position is not None and nodes[siblings[after_position]].kind == 'Label':
                for rid in rule_ids:
                    skip(rid, equation, 'Multiple semantic labels have unresolved display ownership')
                continue

        if STANDALONE in rule_ids:
            violation = False
            proposals = []
            prefix = _line_prefix(source, equation.start)
            indentation = prefix[:len(prefix) - len(prefix.lstrip(b' \t'))]
            before_position = neighbor(siblings, positions[index], -1)
            if before_position is not None:
                before_id = siblings[before_position]
                before = nodes[before_id]
                separator = source[before.end:equation.start]
                if _horizontal(separator):
                    if literal_prose(before_id):
                        violation = True
                        if include_standalone_edits:
                            proposals.append(Edit(before.end, equation.start,
                                                  _newline(source, equation.start) + indentation))
                    elif (_line_prefix(source, equation.start).strip(b' \t')
                          and unknown_neighbor(before_id)):
                        skip(STANDALONE, equation,
                             'Preceding inline construct/comment is not direct literal prose; no boundary crossed')
                elif b'\n' not in separator:
                    skip(STANDALONE, equation, 'Display separator contains unsupported whitespace or bare CR')
            if after_position is not None:
                after_id = siblings[after_position]
                after = nodes[after_id]
                separator = source[end:after.start]
                if _horizontal(separator):
                    if literal_prose(after_id):
                        violation = True
                        if include_standalone_edits:
                            proposals.append(Edit(end, after.start, _newline(source, end) + indentation))
                    elif unknown_neighbor(after_id):
                        skip(STANDALONE, equation,
                             'Following inline construct/comment is not direct literal prose; no boundary crossed')
                elif b'\n' not in separator:
                    skip(STANDALONE, equation, 'Display separator contains unsupported whitespace or bare CR')
            if violation:
                add(STANDALONE, equation,
                    'Place display delimiters on source lines separate from adjacent prose.', proposals)

        if RULE_ID not in rule_ids or after_position is None:
            continue
        after = nodes[siblings[after_position]]
        gap = source[end:after.start]
        if after.kind in _STRUCTURAL or (after.kind == 'Equation' and after.display):
            continue
        if after.kind != 'Text':
            skip(RULE_ID, equation, 'Following construct is not direct literal unmarked prose; no boundary crossed')
            continue
        literal = source[after.start:after.end].decode('utf-8')
        if not literal or literal[0].isspace() or literal[-1].isspace():
            skip(RULE_ID, equation, 'Following Text does not have a direct literal prose start')
            continue
        if not any(character.isalpha() for character in literal):
            skip(RULE_ID, equation, 'Following Text contains no Unicode letter; prose continuation is unresolved')
            continue
        if gap.strip(b' \t\r\n') or b'\r' in gap.replace(b'\r\n', b''):
            skip(RULE_ID, equation, 'Display gap contains unsupported whitespace or bare CR')
            continue
        if gap.count(b'\n') < 2:
            continue
        if (_line_prefix(source, equation.start).strip(b' \t')
                or _line_suffix(source, end).strip(b' \t\r')):
            skip(RULE_ID, equation, 'Display delimiters are not already on standalone source lines')
            continue
        first_lf = gap.find(b'\n')
        newline = b'\r\n' if first_lf and gap[first_lf - 1:first_lf] == b'\r' else b'\n'
        indentation = gap[gap.rfind(b'\n') + 1:]
        add(RULE_ID, equation,
            'Use one source newline between a standalone display and its unmarked prose continuation.',
            (Edit(end, after.start, newline + indentation),) if include_gap_edits else None)
    return {rid: DisplayResult(diagnostics=tuple(findings[rid]), edits=tuple(edits[rid]), suppressed=tuple(suppressed[rid]),
                               skips=tuple(dict.fromkeys(skips[rid])))
            for rid in rule_ids}
