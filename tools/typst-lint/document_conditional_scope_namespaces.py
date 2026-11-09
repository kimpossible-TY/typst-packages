"""Conditional literal scope-prefix collision hints for a captured include graph.

Only issued syntactic include occurrences are used. Occurrence order does not
establish execution order, wrapper identity or actual document membership.
"""
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Mapping
import json

from document_conditional_scoped_calls import _explicit_wrapper_shadows, _parts, _scoped_body, _text
from include_context import assert_include_context
from static_resolver import _literal_string
from suppression import resolve as resolve_suppressions
from typst_lint import InputError, load_rules, require

RULE = 'core.unique-scope-namespace'
CONDITIONS = (
    'Both wrapper calls resolve to the reviewed scoped-annotations constructors.',
    'Both selected syntactic include occurrences and their literal callbacks execute in the same document graph.',
    'The runtime namespace configuration and explicit prefix match the captured source.',
    'When either site is suppressed and order is unproved, that pair is conservatively suppressed; a hint may be hidden.',
)
MAX_NAMESPACE_OCCURRENCES = 10_000
MAX_NAMESPACE_REPORT_ITEMS = 100_000
MAX_NAMESPACE_REPORT_BYTES = 16 * 1024 * 1024
_WRAPPERS = frozenset(('local-tag-scope', 'local-scope-annotations'))


def _prefix(tree, index):
    nodes = tree.nodes
    parts = _parts(tree, index)
    if len(parts) != 2 or nodes[parts[1]].kind != 'Args':
        return None, False
    for part in _parts(tree, parts[1]):
        if nodes[part].kind != 'Named':
            continue
        fields = _parts(tree, part)
        if len(fields) == 3 and nodes[fields[0]].kind == 'Ident' and _text(tree, fields[0]) == 'prefix':
            value = _literal_string(tree, fields[2])
            return value, value is None and nodes[fields[2]].kind == 'Str'
    return None, False


def _chain(occurrence):
    return [dict(path=node.source.path, start=node.start, end=node.end) for node in occurrence.edge_chain]


def _serialized(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


class _ReportBudget:
    """Count the exact pretty-JSON bytes of this section nested once in a report."""
    def __init__(self, result):
        self.result = result
        encoded = _serialized(result)
        # The section is nested one level inside the document report.
        self.bytes = len(encoded.encode('utf-8')) + 2 * encoded.count('\n')
        self.items = 1 + len(CONDITIONS)
        self._check()

    def _check(self):
        require(self.items <= MAX_NAMESPACE_REPORT_ITEMS, 'Conditional namespace report item limit exceeded')
        require(self.bytes <= MAX_NAMESPACE_REPORT_BYTES, 'Conditional namespace report byte limit exceeded')

    def append(self, name, value, items=1):
        encoded = _serialized(value)
        # Array entries are nested three levels inside the full JSON report.
        length = len(encoded.encode('utf-8')) + 6 * encoded.count('\n')
        # Convert [] to a multiline array on first insertion; later additions
        # add a comma, newline and six spaces, preserving the closing line.
        self.bytes += length + (8 if self.result[name] else 12)
        self.items += items
        self._check()
        self.result[name].append(value)


def analyze(context, *, catalog=None):
    """Compare direct literal prefix declarations across issued graph occurrences."""
    assert_include_context(context)
    records = load_rules()[0]['rules'] if catalog is None else catalog
    if (type(records) not in (tuple, list) or any(not isinstance(row, Mapping)
            or type(row.get('id')) is not str or not row['id'] for row in records)):
        raise InputError('Namespace hints require rule metadata')
    selected = [row for row in records if row['id'] == RULE]
    if (len(selected) != 1 or any(type(selected[0].get(key)) is not str or not selected[0][key]
                                  for key in ('grade', 'source'))):
        raise InputError('Missing or invalid namespace rule metadata')
    rule = selected[0]
    declarations = defaultdict(list)
    result = dict(status='PARTIAL', diagnostics=[], suppressed=[], unresolved=[],
                  conditions=CONDITIONS, runtime_binding_proved=False, execution_proved=False,
                  execution_order_proved=False, coverage_complete=False, edit_permission=False,
                  compiler_permission=False)
    budget = _ReportBudget(result)
    unresolved = set()

    def note(reason):
        if reason not in unresolved:
            budget.append('unresolved', reason)
            unresolved.add(reason)

    for item in context.sources:
        tree, source, nodes = item.tree, item.source, item.tree.nodes
        scopes = resolve_suppressions(tree, {row['id'] for row in records})
        shadows = _explicit_wrapper_shadows(tree)
        parents = {child: index for index, node in enumerate(nodes) for child in node.children}
        positions = {child: position for node in nodes for position, child in enumerate(node.children)}
        newlines = [index for index, byte in enumerate(source) if byte == 10]
        for index, node in enumerate(nodes):
            parts = _parts(tree, index) if node.kind == 'FuncCall' else ()
            if len(parts) != 2 or nodes[parts[0]].kind != 'Ident':
                continue
            name = _text(tree, parts[0])
            if name not in _WRAPPERS:
                continue
            if parents.get(index) != 0:
                continue
            siblings, position = nodes[0].children, positions[index]
            mark = nodes[siblings[position - 1]] if position else None
            if mark is None or mark.kind != 'Hash' or mark.end != node.start:
                continue
            if name in shadows:
                note(f'{item.path}: wrapper name is explicitly shadowed at byte {node.start}')
                continue
            callback, reason = _scoped_body(tree, index, name)
            if callback is None:
                note(f'{item.path}: {reason} at byte {node.start}')
                continue
            prefix, unsupported_literal = _prefix(tree, index)
            if unsupported_literal:
                note(f'{item.path}: escaped or non-ASCII prefix is unsupported at byte {node.start}')
                continue
            if prefix is None:
                continue
            line = bisect_right(newlines, node.start) + 1
            line_start = newlines[line - 2] + 1 if line > 1 else 0
            applied = [dict(directive_start=scope.directive_start, start=scope.start, end=scope.end,
                            rule_ids=sorted(scope.rules), reason=scope.reason)
                       for scope in scopes if scope.covers(RULE, node.start)]
            declaration = dict(path=item.path, source_sha256=item.identity.source_sha256,
                start=node.start, end=node.end, line=line,
                column=len(source[line_start:node.start].decode('utf-8')) + 1,
                prefix=prefix, wrapper=name,
                suppressions=applied)
            declarations[item.path].append(declaration)
    seen = {}
    count = 0
    for occurrence in context.occurrences:
        if 'include-cycle' in occurrence.unresolved_codes:
            continue
        for declaration in declarations[occurrence.source.path]:
            count += 1
            require(count <= MAX_NAMESPACE_OCCURRENCES, 'Conditional namespace occurrence limit exceeded')
            key = (occurrence.entrypoint.path, declaration['prefix'])
            site = dict(declaration)
            site.update(entrypoint=occurrence.entrypoint.path, include_chain=_chain(occurrence),
                        include_unresolved=list(occurrence.unresolved_codes))
            if key not in seen:
                seen[key] = (site, None if site['suppressions'] else site)
                continue
            first_site, first_unsuppressed = seen[key]
            comparison_site = (first_unsuppressed if not site['suppressions'] and first_unsuppressed
                               else first_site)
            comparison_suppressed = bool(comparison_site['suppressions'])
            finding = dict(rule_id=RULE, grade=rule['grade'], source=rule['source'], **site,
                           comparison_site=comparison_site,
                           suppression_ambiguous=(comparison_suppressed != bool(site['suppressions'])),
                           explanation='These source occurrences use the same literal scope prefix if both '
                                       'wrappers resolve and execute in the same document graph; order is unproved.')
            report_items = (1 + len(site['include_chain']) + len(site['include_unresolved'])
                             + len(comparison_site['include_chain']) + len(comparison_site['include_unresolved'])
                             + sum(1 + len(item['rule_ids']) for item in site['suppressions'])
                             + sum(1 + len(item['rule_ids']) for item in comparison_site['suppressions']))
            # Capture traversal is file-preorder, not execution order. Either
            # side may be the user's ignored later collision, so retain the
            # result as suppressed until a stronger ordering proof exists.
            target = 'suppressed' if site['suppressions'] or comparison_suppressed else 'diagnostics'
            budget.append(target, finding, report_items)
            if first_unsuppressed is None and not site['suppressions']:
                seen[key] = (first_site, site)
    assert_include_context(context)
    return result
