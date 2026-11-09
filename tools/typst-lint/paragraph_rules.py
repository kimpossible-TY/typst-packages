"""Bounded real-source paragraph rules over parser and static binding proofs.

Only canonical standalone markers in proven root prose are currently supported.
This is partial analysis, not permission to write or compile consumer sources.
"""
from dataclasses import dataclass
from collections.abc import Mapping
from bisect import bisect_left
import re

from capitalization import BodyKind, Context, MarkerBinding, StartKind, WORDS, evaluate
from edit_plan import Edit
from suppression import resolve as resolve_suppressions
from typst_lint import Diagnostic, InputError, digest, load_rules

CAPITALIZATION = 'pde.paragraph-capitalization'
MARKER_GAP = 'pde.paragraph-marker-gap'
MARKER_TARGET = 'pde.paragraph-marker-target'
RULE_IDS = (CAPITALIZATION, MARKER_GAP, MARKER_TARGET)
NESTED_GAP_UNRESOLVED = 'Unresolved nested paragraph marker boundary at byte '

_CONDITIONAL_EXCLUDED = frozenset(('Equation', 'Math', 'Code', 'CodeBlock',
                                   'Heading', 'ListItem', 'EnumItem', 'TermItem',
                                   'Raw', 'Str'))


@dataclass(frozen=True)
class ParagraphResult:
    diagnostics: tuple[Diagnostic, ...] = ()
    edits: tuple[Edit, ...] = ()
    skips: tuple[str, ...] = ()
    parse_errors: tuple[tuple[int, int], ...] = ()
    suppressed: tuple[Diagnostic, ...] = ()


def _diagnostic(source, path, rule, start, explanation):
    line = source[:start].count(b'\n') + 1
    column = len(source[source.rfind(b'\n', 0, start) + 1:start].decode('utf-8')) + 1
    return Diagnostic(rule['id'], rule['grade'], path, line, column, explanation, rule['source'])


def analyze_conditional_nested_gap(tree, path, *, catalog=None, _allow_first_content=False):
    """Find standalone marker-shaped tokens in nested literal markup only.

    These are source patterns: the identifier's binding, execution and prose
    ownership are not established.  No repair is proposed, and callers must not
    interpret an empty result as coverage of arbitrary rendered content.
    """
    if type(_allow_first_content) is not bool:
        raise InputError('First-content marker selection must be boolean')
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        errors = tuple((n.start, n.end) for n in nodes if n.kind == 'Error')
        return ParagraphResult(skips=('Malformed Typst syntax; no source pattern diagnosis',),
                               parse_errors=errors or ((0, len(source)),))
    if tree.mode != 'markup' or nodes[0].kind != 'Markup':
        return ParagraphResult(skips=('Only nested markup source patterns are supported',))
    records = load_rules()[0]['rules'] if catalog is None else catalog
    if (type(records) not in (list, tuple) or any(not isinstance(row, Mapping)
            or type(row.get('id')) is not str or not row['id'] for row in records)):
        raise InputError('Conditional paragraph analysis requires rule metadata records')
    selected = [row for row in records if row['id'] == MARKER_GAP]
    if (len(selected) != 1 or any(type(selected[0].get(key)) is not str or not selected[0][key]
                                   for key in ('grade', 'source'))):
        raise InputError('Missing or invalid paragraph marker gap metadata')
    scopes = resolve_suppressions(tree, {row['id'] for row in records})
    parents = {child: index for index, node in enumerate(nodes) for child in node.children}
    excluded = {}
    for index, node in enumerate(nodes):
        parent = parents.get(index)
        excluded[index] = parent is not None and (excluded[parent]
                                                   or nodes[parent].kind in _CONDITIONAL_EXCLUDED)
    newlines = [index for index, byte in enumerate(source) if byte == 10]
    findings, suppressed, unresolved = [], [], []
    for body_id, body in enumerate(nodes):
        if body_id == 0 or body.kind != 'Markup' or excluded[body_id]:
            continue
        children = body.children
        for position, index in enumerate(children):
            marker = nodes[index]
            if (position == 0 or marker.kind != 'Ident'
                    or source[marker.start:marker.end] != b'paragraph_tab'):
                continue
            hash_node = nodes[children[position - 1]]
            if hash_node.kind != 'Hash' or hash_node.end != marker.start:
                continue
            start, end = hash_node.start, marker.end
            line_index = bisect_left(newlines, start)
            line_start = 0 if line_index == 0 else newlines[line_index - 1] + 1
            line_end = newlines[line_index] if line_index < len(newlines) else len(source)
            if source[line_start:start].strip(b' \t') or source[end:line_end].strip(b' \t\r'):
                continue
            next_pos = position + 1
            while (next_pos < len(children) and nodes[children[next_pos]].kind in
                   ('Space', 'Parbreak', 'LineComment', 'BlockComment')):
                next_pos += 1
            target = nodes[children[next_pos]] if next_pos < len(children) else None
            if target is not None and (target.kind == 'Heading'
                                       or (target.kind == 'Equation' and target.display)):
                continue
            previous_start = 0 if line_index < 2 else newlines[line_index - 2] + 1
            previous = source[previous_start:line_start]
            if previous.endswith(b'\n') and not previous.strip(b' \t\r\n'):
                continue
            ignored = any(scope.covers(MARKER_GAP, start) for scope in scopes)
            prior = position - 2
            while prior >= 0 and nodes[children[prior]].kind in ('Space', 'Parbreak', 'LineComment', 'BlockComment'):
                prior -= 1
            previous_node = nodes[children[prior]] if prior >= 0 else None
            # The opening of a literal content block is not a blank source
            # line. Admit only a first marker directly after that opening and
            # one LF/CRLF, with no intervening comments or executable syntax.
            # Its binding and execution remain conditional like other hints.
            owner = parents.get(body_id)
            first_in_block = (_allow_first_content and previous_node is None and owner is not None
                              and nodes[owner].kind == 'ContentBlock'
                              and re.fullmatch(rb'\r?\n[ \t]*', source[body.start:start]) is not None)
            if (not first_in_block and (previous_node is None or previous_node.kind != 'Text'
                    or not any(char.isalnum() for char in
                               source[previous_node.start:previous_node.end].decode('utf-8')))):
                if not ignored:
                    unresolved.append(NESTED_GAP_UNRESOLVED + str(start))
                continue
            column = len(source[line_start:start].decode('utf-8')) + 1
            rule = selected[0]
            finding = Diagnostic(MARKER_GAP, rule['grade'], path, line_index + 1, column,
                'Conditional source hint: if this standalone name is the paragraph marker, place a blank source line before it.',
                rule['source'])
            (suppressed if ignored else findings).append(finding)
    return ParagraphResult(tuple(findings), (),
        ('Nested source pattern only; marker binding, execution, prose ownership and rendering are unproved.',
         *tuple(dict.fromkeys(unresolved))),
        suppressed=tuple(suppressed))


def analyze_conditional_nested_target(tree, path, *, catalog=None):
    """Hint at nested marker-shaped names before a structural sibling.

    Binding, execution, and ownership of the literal body are not proved.
    This source-only diagnostic never proposes removing the marker.
    """
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        errors = tuple((n.start, n.end) for n in nodes if n.kind == 'Error')
        return ParagraphResult(skips=('Malformed Typst syntax; no source pattern diagnosis',),
                               parse_errors=errors or ((0, len(source)),))
    if tree.mode != 'markup' or nodes[0].kind != 'Markup':
        return ParagraphResult(skips=('Only nested markup source patterns are supported',))
    records = load_rules()[0]['rules'] if catalog is None else catalog
    if (type(records) not in (list, tuple) or any(not isinstance(row, Mapping)
            or type(row.get('id')) is not str or not row['id'] for row in records)):
        raise InputError('Conditional paragraph analysis requires rule metadata records')
    selected = [row for row in records if row['id'] == MARKER_TARGET]
    if (len(selected) != 1 or any(type(selected[0].get(key)) is not str or not selected[0][key]
                                   for key in ('grade', 'source'))):
        raise InputError('Missing or invalid paragraph marker target metadata')
    scopes = resolve_suppressions(tree, {row['id'] for row in records})
    parents = {child: index for index, node in enumerate(nodes) for child in node.children}
    excluded = {}
    for index, node in enumerate(nodes):
        parent = parents.get(index)
        excluded[index] = parent is not None and (excluded[parent]
                                                   or nodes[parent].kind in _CONDITIONAL_EXCLUDED)
    newlines = [index for index, byte in enumerate(source) if byte == 10]
    findings, suppressed = [], []
    for body_id, body in enumerate(nodes):
        if body_id == 0 or body.kind != 'Markup' or excluded[body_id]:
            continue
        children = body.children
        for position, index in enumerate(children):
            marker = nodes[index]
            if (position == 0 or marker.kind != 'Ident'
                    or source[marker.start:marker.end] != b'paragraph_tab'):
                continue
            hash_node = nodes[children[position - 1]]
            if hash_node.kind != 'Hash' or hash_node.end != marker.start:
                continue
            start, end = hash_node.start, marker.end
            line_index = bisect_left(newlines, start)
            line_start = 0 if line_index == 0 else newlines[line_index - 1] + 1
            line_end = newlines[line_index] if line_index < len(newlines) else len(source)
            if source[line_start:start].strip(b' \t') or source[end:line_end].strip(b' \t\r'):
                continue
            next_pos = position + 1
            while (next_pos < len(children) and nodes[children[next_pos]].kind in
                   ('Space', 'Parbreak', 'LineComment', 'BlockComment')):
                next_pos += 1
            target = nodes[children[next_pos]] if next_pos < len(children) else None
            if target is None or not (target.kind == 'Heading'
                                      or (target.kind == 'Equation' and target.display)):
                continue
            column = len(source[line_start:start].decode('utf-8')) + 1
            rule = selected[0]
            finding = Diagnostic(MARKER_TARGET, rule['grade'], path, line_index + 1, column,
                'Conditional source hint: if this standalone name is the paragraph marker, remove it before the heading or display.',
                rule['source'])
            (suppressed if any(scope.covers(MARKER_TARGET, start) for scope in scopes)
             else findings).append(finding)
    return ParagraphResult(tuple(findings), (),
        ('Nested source pattern only; marker binding, execution and body ownership are unproved.',),
        suppressed=tuple(suppressed))


def analyze_conditional_nested_capitalization(tree, path, *, catalog=None):
    """Report literal nested marker/starter patterns without proving language or binding.

    A result is conditional on the actual marker, ordinary English prose and
    execution. This route supplies no edit or source-write authority.
    """
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        errors = tuple((n.start, n.end) for n in nodes if n.kind == 'Error')
        return ParagraphResult(skips=('Malformed Typst syntax; no source pattern diagnosis',),
                               parse_errors=errors or ((0, len(source)),))
    if tree.mode != 'markup' or nodes[0].kind != 'Markup':
        return ParagraphResult(skips=('Only nested markup source patterns are supported',))
    records = load_rules()[0]['rules'] if catalog is None else catalog
    if (type(records) not in (list, tuple) or any(not isinstance(row, Mapping)
            or type(row.get('id')) is not str or not row['id'] for row in records)):
        raise InputError('Conditional capitalization analysis requires rule metadata records')
    selected = [row for row in records if row['id'] == CAPITALIZATION]
    if (len(selected) != 1 or any(type(selected[0].get(key)) is not str or not selected[0][key]
                                   for key in ('grade', 'source'))):
        raise InputError('Missing or invalid capitalization rule metadata')
    scopes = resolve_suppressions(tree, {row['id'] for row in records})
    parents = {child: index for index, node in enumerate(nodes) for child in node.children}
    excluded = {}
    for index, node in enumerate(nodes):
        parent = parents.get(index)
        excluded[index] = parent is not None and (excluded[parent]
                                                   or nodes[parent].kind in _CONDITIONAL_EXCLUDED)
    newlines = [index for index, byte in enumerate(source) if byte == 10]
    findings, suppressed = [], []
    for body_id, body in enumerate(nodes):
        if body_id == 0 or body.kind != 'Markup' or excluded[body_id]:
            continue
        siblings = body.children
        for position, index in enumerate(siblings):
            marker = nodes[index]
            if (position == 0 or marker.kind != 'Ident'
                    or source[marker.start:marker.end] != b'paragraph_tab'):
                continue
            hash_node = nodes[siblings[position - 1]]
            if hash_node.kind != 'Hash' or hash_node.end != marker.start:
                continue
            line_start = source.rfind(b'\n', 0, hash_node.start) + 1
            if source[line_start:hash_node.start].strip(b' \t'):
                continue
            next_position = position + 1
            if next_position < len(siblings) and nodes[siblings[next_position]].kind == 'Space':
                next_position += 1
            if next_position >= len(siblings):
                continue
            first = nodes[siblings[next_position]]
            if first.kind != 'Text' or not re.fullmatch(rb'[ \t]*\r?\n[ \t]*', source[marker.end:first.start]):
                continue
            word = re.match(rb'[a-z]+', source[first.start:first.end])
            if (word is None or len(word[0]) < 2 or word[0] not in WORDS
                    or source[first.start + len(word[0]):first.start + len(word[0]) + 1] not in (b' ', b'\t', b'\r', b'\n')):
                continue
            line_index = bisect_left(newlines, first.start)
            begin = 0 if line_index == 0 else newlines[line_index - 1] + 1
            column = len(source[begin:first.start].decode('utf-8')) + 1
            rule = selected[0]
            finding = Diagnostic(CAPITALIZATION, rule['grade'], path, line_index + 1, column,
                'Conditional source hint: if this is the paragraph marker and the paragraph is ordinary English prose, capitalize its first word.',
                rule['source'])
            if any(scope.covers(CAPITALIZATION, hash_node.start) or scope.covers(CAPITALIZATION, first.start)
                   for scope in scopes):
                suppressed.append(finding)
            else:
                findings.append(finding)
    return ParagraphResult(tuple(findings), (),
        ('Nested source pattern only; marker binding, English language, execution and prose rendering are unproved.',),
        suppressed=tuple(suppressed))


def analyze(tree, path, resolution, *, catalog=None, _bounded_gap_markers=frozenset()):
    """Consume hash-bound proofs, never infer a helper from its identifier text."""
    supplied = catalog is not None
    if supplied:
        if (type(catalog) not in (tuple, list)
                or any(not isinstance(record, Mapping) or type(record.get('id')) is not str
                       or not record['id'] for record in catalog)):
            raise InputError('Paragraph analysis requires explicit rule metadata records')
        for rid in RULE_IDS:
            records = [record for record in catalog if record.get('id') == rid]
            if (len(records) != 1
                    or any(type(records[0].get(name)) is not str or not records[0][name]
                           for name in ('grade', 'source'))):
                raise InputError('Missing or invalid paragraph rule metadata')
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        errors = tuple((n.start, n.end) for n in nodes if n.kind == 'Error')
        result = ParagraphResult(skips=('Malformed Typst syntax; no repair proposed',),
                                 parse_errors=errors or ((0, len(source)),))
        return {rid: result for rid in RULE_IDS}
    if resolution.source_sha256 != digest(source) or len(resolution.facts) != len(nodes):
        result = ParagraphResult(skips=('Stale or mismatched static resolution; no repair proposed',))
        return {rid: result for rid in RULE_IDS}
    catalog_records = load_rules()[0]['rules'] if catalog is None else catalog
    catalog = {r['id']: r for r in catalog_records}
    scopes = resolve_suppressions(tree, set(catalog))
    findings = {rid: [] for rid in RULE_IDS}
    edits = {rid: [] for rid in RULE_IDS}
    suppressed = {rid: [] for rid in RULE_IDS}
    skips = {rid: list(resolution.skips) for rid in RULE_IDS}

    def skip(rid, start, reason):
        skips[rid].append(f'{reason} at byte {start}')

    def add(rid, finding, edit, marker_start, diagnostic_start):
        # The marked paragraph belongs to its marker. An ignore-next directive
        # on that marker suppresses both its gap and initial-letter repair.
        if any(s.covers(rid, marker_start) or s.covers(rid, diagnostic_start) for s in scopes):
            suppressed[rid].append(finding)
        else:
            findings[rid].append(finding)
            if edit is not None:
                edits[rid].append(edit)

    for body in nodes:
        if body.kind != 'Markup':
            continue
        siblings = body.children
        for position, index in enumerate(siblings):
            marker = nodes[index]
            if marker.kind not in ('Ident', 'FieldAccess', 'Parenthesized') or position == 0:
                continue
            hash_node = nodes[siblings[position - 1]]
            if hash_node.kind != 'Hash' or hash_node.end != marker.start:
                continue
            facts = resolution.facts[index]
            binding = facts.binding
            known = binding is not None and binding.kind == 'api' and binding.name == 'paragraph_tab'
            # The text check only identifies unsupported candidates. It never
            # grants a binding or causes a finding/edit.
            spelled = source[marker.start:marker.end] == b'paragraph_tab'
            if not known and not spelled:
                continue
            ms, me = hash_node.start, marker.end
            reason = None
            if not known:
                reason = 'Unresolved or unrelated paragraph marker'
            elif source[ms:me] != b'#paragraph_tab':
                reason = 'Resolved marker alias spelling is unsupported by the capitalization context contract'
            elif not facts.prose_safe or facts.deferred:
                reason = 'Unsupported paragraph body or deferred context'
            line_start = max(body.start, source.rfind(b'\n', body.start, ms) + 1)
            prefix = source[line_start:ms]
            if line_start == 0 and prefix.startswith(b'\xef\xbb\xbf'):
                line_start += 3
                prefix = prefix[3:]
            line_end = source.find(b'\n', me, body.end)
            line_end = body.end if line_end < 0 else line_end
            if prefix.strip(b' \t') or source[me:line_end].strip(b' \t\r'):
                reason = reason or 'Marker is not standalone on its source line'
            if reason:
                for rid in RULE_IDS:
                    skip(rid, ms, reason)
                continue

            # Heading/display ownership is structural: comments and blank
            # lines do not turn the next sibling into a prose paragraph. This
            # branch takes precedence over gap/casing diagnostics on a marker
            # which should not be present at all.
            target_position = position + 1
            while (target_position < len(siblings)
                   and nodes[siblings[target_position]].kind in
                   ('Space', 'Parbreak', 'LineComment', 'BlockComment')):
                target_position += 1
            target = nodes[siblings[target_position]] if target_position < len(siblings) else None
            if target is not None and (target.kind == 'Heading'
                                       or (target.kind == 'Equation' and target.display)):
                finding = _diagnostic(source, path, catalog[MARKER_TARGET], ms,
                                      'Remove the paragraph marker before a heading or display equation.')
                # Keep this otherwise empty physical line, including its line
                # terminator. Deleting only the token cannot merge surrounding
                # structural elements and preserves every intervening comment.
                # Any directive targeting the removed marker, even for another
                # rule, would acquire a different target after deletion.
                reassigned = any(scope.start <= ms < scope.end for scope in scopes)
                edit = None if reassigned else Edit(ms, me, b'')
                add(MARKER_TARGET, finding, edit, ms, ms)
                if reassigned and finding not in suppressed[MARKER_TARGET]:
                    skip(MARKER_TARGET, ms, 'fix_unavailable: removing marker would reassign an inline directive')
                continue

            # Gap belongs to the immediate previous physical source line.
            # A body opening or BOM is not a whitespace-only blank line.
            previous_start = max(body.start, source.rfind(b'\n', body.start, max(body.start, line_start - 1)) + 1)
            previous = source[previous_start:line_start]
            has_blank = bool(previous.endswith(b'\n')) and not previous.strip(b' \t\r\n')
            if not has_blank:
                finding = _diagnostic(source, path, catalog[MARKER_GAP], ms,
                                      'Place a whitespace-only blank source line before the standalone paragraph marker.')
                # Every syntax leaf on the previous line must be plain prose,
                # whitespace, or BOM. In particular no footnote/call/comment is
                # guessed safe: the footnote's combined repair is not implemented.
                previous_nodes = [n for n in nodes if not n.children and n.start < line_start
                                  and n.end > previous_start]
                safe = (all(n.kind in ('Text', 'Space', 'Parbreak') for n in previous_nodes)
                        or ms in _bounded_gap_markers)
                replacement = b'\r\n' if (source[me:line_end].endswith(b'\r')
                                             or previous.endswith(b'\r\n')) else b'\n'
                edit = Edit(line_start, line_start, replacement) if safe else None
                add(MARKER_GAP, finding, edit, ms, ms)
                if not safe and finding not in suppressed[MARKER_GAP]:
                    skip(MARKER_GAP, ms, 'fix_unavailable: preceding line has call/footnote/comment or other unsupported syntax')

            # Inspect exactly the next sibling after one whitespace node.
            # Do not search past comments, math, quotes, wrappers or references.
            next_position = position + 1
            if next_position < len(siblings) and nodes[siblings[next_position]].kind == 'Space':
                next_position += 1
            if next_position >= len(siblings):
                skip(CAPITALIZATION, ms, 'No following direct prose word')
                continue
            first_id = siblings[next_position]
            first = nodes[first_id]
            if first.kind != 'Text' or not resolution.facts[first_id].prose_safe:
                skip(CAPITALIZATION, ms, 'First token is not a proven direct literal prose word')
                continue
            # Lex only the beginning of one parser-certified Text leaf. This
            # does not tokenize Typst or search forward to a later word.
            word = re.match(rb'[A-Za-z]+', source[first.start:first.end])
            if word is None:
                skip(CAPITALIZATION, first.start, 'First token is not an ASCII literal word')
                continue
            context = Context(digest(source), path, max(body.start, 3 if source.startswith(b'\xef\xbb\xbf') else 0),
                              body.end, ms, me, first.start, first.start + len(word[0]), word[0],
                              StartKind.LITERAL_WORD, BodyKind.ORDINARY_PROSE,
                              MarkerBinding.PARAGRAPH_TAB, resolution.language_for(first_id))
            result = (evaluate(source, context, catalog=catalog_records)
                      if supplied else evaluate(source, context))
            if result.diagnostic is not None:
                add(CAPITALIZATION, result.diagnostic, result.edit, ms, first.start)
            elif result.skip_reason != 'already-uppercase':
                skip(CAPITALIZATION, first.start, result.skip_reason)
    return {rid: ParagraphResult(tuple(findings[rid]), tuple(edits[rid]),
                                tuple(dict.fromkeys(skips[rid])), suppressed=tuple(suppressed[rid]))
            for rid in RULE_IDS}
