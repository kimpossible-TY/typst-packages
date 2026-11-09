"""Bounded review-only inventory of paragraph starters outside the policy.

These source shapes are not rule violations and never produce edits. In
particular, marker binding, English prose, execution and author intent remain
unknown. No current filesystem, compiler or rendering authority is implied.
"""
from bisect import bisect_left
import json
import re

import include_context
from capitalization import RULE_ID, WORDS
from paragraph_rules import _CONDITIONAL_EXCLUDED
from suppression import resolve as resolve_suppressions
from typst_lint import InputError, digest, require

MAX_WORK = 4_000_000
MAX_ITEMS = 100_000
MAX_BYTES = 16 * 1024 * 1024
CONDITIONS = (
    'These are review-only source patterns outside the versioned starter policy, not rule violations.',
    'Marker binding, ordinary English prose, execution and intended capitalization are not proved.',
    'Source hashes describe captured bytes; current filesystem freshness is not established, and no edits or compilation are authorized.',
)


def analyze(context, catalog, *, max_work=MAX_WORK, max_items=MAX_ITEMS, max_bytes=MAX_BYTES):
    require(type(max_work) is int and 0 < max_work <= MAX_WORK
            and type(max_items) is int and 0 < max_items <= MAX_ITEMS
            and type(max_bytes) is int and 0 < max_bytes <= MAX_BYTES,
            'Invalid capitalization review budget')
    include_context.assert_include_context(context)
    require(type(catalog) in (tuple, list) and all(type(row) is dict and type(row.get('id')) is str
            for row in catalog) and sum(row['id'] == RULE_ID for row in catalog) == 1,
            'Capitalization review requires the rule catalog')
    remaining = max_work
    result = dict(status='INCOMPLETE' if context.unresolved else 'CONDITIONAL',
                  unresolved_include_count=len(context.unresolved), findings=[], suppressed_count=0, uninspected_sources=[],
                  conditions=CONDITIONS, coverage_complete=False, violation_proved=False,
                  edit_permission=False, compiler_permission=False, render_equivalence_proved=False)
    for item in context.sources:
        tree, source, nodes = item.tree, item.source, item.tree.nodes
        remaining -= 4 * len(nodes) + len(source)
        if remaining < 0:
            raise InputError('Capitalization review work budget exceeded')
        if nodes[0].errors or tree.mode != 'markup' or nodes[0].kind != 'Markup':
            result['uninspected_sources'].append(item.path)
            result['status'] = 'INCOMPLETE'
            continue
        parents = {child: index for index, node in enumerate(nodes) for child in node.children}
        # The shared resolver scans prefixes and siblings per directive. Its
        # directive regex can also backtrack cubically on malformed input.
        # Charge these worst cases before invoking it.
        for index, comment in enumerate(nodes):
            if comment.kind != 'LineComment':
                continue
            text = source[comment.start:comment.end]
            if not text[2:].lstrip(b' \t').startswith(b'typst-lint:'):
                continue
            parent = parents.get(index)
            sibling_count = 0 if parent is None else len(nodes[parent].children)
            remaining -= 2 * comment.start + 2 * sibling_count + len(text) ** 3
            if remaining < 0:
                raise InputError('Capitalization review work budget exceeded')
        scopes = resolve_suppressions(tree, {row['id'] for row in catalog})
        excluded = {}
        for index, node in enumerate(nodes):
            parent = parents.get(index)
            excluded[index] = parent is not None and (excluded[parent] or nodes[parent].kind in _CONDITIONAL_EXCLUDED)
        newlines, line_first = [], []
        need_first = True
        for offset, byte in enumerate(source):
            if need_first and byte not in (32, 9):
                line_first.append(offset)
                need_first = False
            if byte == 10:
                newlines.append(offset)
                need_first = True
        source_hash = digest(source)
        for body_id, body in enumerate(nodes):
            if body.kind != 'Markup' or excluded[body_id]:
                continue
            siblings = body.children
            for position, index in enumerate(siblings):
                marker = nodes[index]
                if position == 0 or marker.kind != 'Ident' or source[marker.start:marker.end] != b'paragraph_tab':
                    continue
                mark = nodes[siblings[position - 1]]
                if mark.kind != 'Hash' or mark.end != marker.start:
                    continue
                remaining -= (len(newlines) + 1).bit_length()
                if remaining < 0:
                    raise InputError('Capitalization review work budget exceeded')
                marker_line = bisect_left(newlines, mark.start)
                if marker_line >= len(line_first) or line_first[marker_line] != mark.start:
                    continue
                following = position + 1
                if following < len(siblings) and nodes[siblings[following]].kind == 'Space':
                    following += 1
                if following >= len(siblings):
                    continue
                first = nodes[siblings[following]]
                if first.kind != 'Text' or re.fullmatch(rb'[ \t]*\r?\n[ \t]*', source[marker.end:first.start]) is None:
                    continue
                word = re.match(rb'[a-z]+', source[first.start:first.end])
                if word is None or len(word[0]) < 2 or word[0] in WORDS:
                    continue
                end = first.start + len(word[0])
                if end >= len(source) or source[end:end + 1] not in (b' ', b'\t', b'\r', b'\n'):
                    continue
                remaining -= 1 + len(scopes)
                if remaining < 0:
                    raise InputError('Capitalization review work budget exceeded')
                if any(scope.covers(RULE_ID, mark.start) or scope.covers(RULE_ID, first.start) for scope in scopes):
                    result['suppressed_count'] += 1
                    continue
                remaining -= (len(newlines) + 1).bit_length()
                if remaining < 0:
                    raise InputError('Capitalization review work budget exceeded')
                line = bisect_left(newlines, first.start)
                begin = 0 if not line else newlines[line - 1] + 1
                result['findings'].append(dict(path=item.path, source_sha256=source_hash,
                    start=first.start, end=end, line=line + 1,
                    column=len(source[begin:first.start].decode('utf-8')) + 1,
                    starter=word[0].decode('ascii'), classification='outside-versioned-starter-policy',
                    status='REVIEW_ONLY', violation_proved=False, edit_permission=False))
                if 1 + len(CONDITIONS) + len(result['findings']) + len(result['uninspected_sources']) > max_items:
                    raise InputError('Capitalization review report item budget exceeded')
    result['report_items'] = 1 + len(CONDITIONS) + len(result['findings']) + len(result['uninspected_sources'])
    require(result['report_items'] <= max_items, 'Capitalization review report item budget exceeded')
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
    require(len(encoded.encode('utf-8')) + 2 * encoded.count('\n') <= max_bytes,
            'Capitalization review report byte budget exceeded')
    include_context.assert_include_context(context)
    return result
