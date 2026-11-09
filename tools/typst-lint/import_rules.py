"""Partial import ordering/redundancy proposals from actual relative source graphs.

Path spelling classifies a convention only after the source graph establishes
pure exports/re-exports. It never establishes symbol identity or initializer
safety. These rules never write files or extend the source selection.
"""
from collections import Counter
from collections.abc import Mapping
from bisect import bisect_left
from dataclasses import dataclass, replace
import re
from pathlib import PurePosixPath

from edit_plan import Edit, plan
from import_graph import (GraphAnalyzer, ImportGraph, import_request, import_shape,
                          imported_bindings, rebase_graph)
from static_resolver import MANNOT_NAMESPACE, SCOPED_ANNOTATIONS
from suppression import resolve as resolve_suppressions
from syntax_client import parse
from typst_lint import Diagnostic, InputError, SafetyError, load_rules

ORDER = 'pde.import-order'
REDUNDANT = 'pde.no-redundant-facade-import'
RULE_IDS = (ORDER, REDUNDANT)
_TRIVIA = frozenset(('Space', 'Parbreak', 'LineComment', 'BlockComment'))
_LIMIT = ('Only captured pure relative facades, literal/alias exports and immediate root import groups '
          'are proved; package evaluation, includes, dynamic imports, module aliases and initializer effects remain unsupported')


@dataclass(frozen=True)
class ImportResult:
    diagnostics: tuple[Diagnostic, ...] = ()
    edits: tuple[Edit, ...] = ()
    skips: tuple[str, ...] = ()
    parse_errors: tuple[tuple[int, int], ...] = ()
    suppressed: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class _Statement:
    node_id: int
    start: int
    end: int
    request: str | None
    target: str | None
    category: int | None
    incoming: tuple | None
    evidence: object
    prefix: bool


def _style_path(path, names):
    parts = PurePosixPath(path).parts if path else ()
    return len(parts) >= 2 and parts[-2] == 'Styles' and parts[-1] in names


def analyze_conditional_order(tree, path, *, catalog=None):
    """Report out-of-order literal root import spellings as conditional hints.

    Paths and the recognized annotation requests classify source conventions
    only. No graph, module ownership, initializer, execution, or edit safety is
    proved. Imports inside blocks or dynamic requests are not inspected.
    """
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        errors = tuple((n.start, n.end) for n in nodes if n.kind == 'Error')
        return ImportResult(skips=('Malformed Typst syntax; no source pattern diagnosis',),
                            parse_errors=errors or ((0, len(source)),))
    if tree.mode != 'markup' or nodes[0].kind != 'Markup':
        return ImportResult(skips=('Only direct root markup imports are inspected',))
    records = load_rules()[0]['rules'] if catalog is None else catalog
    if (type(records) not in (tuple, list) or any(not isinstance(row, Mapping)
            or type(row.get('id')) is not str or not row['id'] for row in records)):
        raise InputError('Conditional import order requires rule metadata records')
    selected = [row for row in records if row['id'] == ORDER]
    if (len(selected) != 1 or any(type(selected[0].get(key)) is not str or not selected[0][key]
                                   for key in ('grade', 'source'))):
        raise InputError('Missing or invalid import order metadata')
    scopes = resolve_suppressions(tree, {row['id'] for row in records})
    siblings = nodes[0].children
    findings, suppressed = [], []
    highest = -1
    newlines = [-1] + [i for i, byte in enumerate(source) if byte == 10]
    for position, index in enumerate(siblings):
        node = nodes[index]
        if node.kind != 'ModuleImport' or position == 0:
            continue
        previous = nodes[siblings[position - 1]]
        if previous.kind != 'Hash' or previous.end != node.start:
            continue
        request = import_request(tree, index)
        if request is None:
            continue
        category = None
        if request in (MANNOT_NAMESPACE, SCOPED_ANNOTATIONS):
            category = 2
        elif not request.startswith(('@', '/')) and _style_path(request, ('styles.typ',)):
            category = 0
        elif not request.startswith(('@', '/')) and PurePosixPath(request).name == 'figures.typ':
            category = 1
        if category is None:
            continue
        if category < highest:
            start = previous.start
            line = bisect_left(newlines, start)
            column = len(source[newlines[line - 1] + 1:start].decode('utf-8')) + 1
            rule = selected[0]
            finding = Diagnostic(ORDER, rule['grade'], path, line, column,
                'Conditional source hint: if these imports are the facade, figure and annotation modules, '
                'place the facade before figures and annotations.', rule['source'])
            (suppressed if any(scope.covers(ORDER, start) for scope in scopes) else findings).append(finding)
        highest = max(highest, category)
    return ImportResult(tuple(findings), (),
        ('Source spellings only; import target identity, initialization, execution and reorder safety are unproved.',),
        suppressed=tuple(suppressed))


def analyze(tree, path, *, graph=None, _verify=True):
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        errors = tuple((n.start, n.end) for n in nodes if n.kind == 'Error') or ((0, len(source)),)
        return {rid: ImportResult(skips=('Malformed Typst syntax; no import proof',), parse_errors=errors)
                for rid in RULE_IDS}
    if tree.mode != 'markup' or type(graph) is not ImportGraph:
        return {rid: ImportResult(skips=('Explicit immutable relative import graph is unavailable',)) for rid in RULE_IDS}
    snapshot = graph.source_for(path)
    if path not in graph.selected or snapshot is None or snapshot.source != source:
        return {rid: ImportResult(skips=('Graph source path/bytes do not match this selected input',)) for rid in RULE_IDS}
    rules = {rule['id']: rule for rule in load_rules()[0]['rules']}
    scopes = resolve_suppressions(tree, set(rules))
    diagnostics, edits, suppressed = ({rid: [] for rid in RULE_IDS} for _ in range(3))
    skips = {rid: [_LIMIT] for rid in RULE_IDS}
    analyzer = GraphAnalyzer(graph)
    statements = []
    siblings = nodes[0].children
    prefix, position = True, 0
    while position < len(siblings):
        index = siblings[position]
        node = nodes[index]
        position += 1
        if node.kind in _TRIVIA:
            continue
        if node.kind != 'Hash' or position == len(siblings):
            prefix = False
            continue
        statement_id = siblings[position]
        statement = nodes[statement_id]
        position += 1
        if statement.kind != 'ModuleImport' or node.end != statement.start:
            prefix = False
            continue
        request = import_request(tree, statement_id)
        shape = import_shape(tree, statement_id)
        target = graph.target_for(path, request)
        evidence = analyzer.module(target) if target else None
        incoming = imported_bindings(shape, evidence) if evidence else None
        category = None
        if (evidence and evidence.pure and evidence.reexports and evidence.exports
                and _style_path(target, ('styles.typ',))):
            category = 0
        elif (evidence and evidence.pure and evidence.exports
              and PurePosixPath(target).name == 'figures.typ'):
            category = 1
        elif request in (MANNOT_NAMESPACE, SCOPED_ANNOTATIONS):
            # Recognized annotation requests can diagnose order, but are NEVER
            # certified pure by a package name/version or direct API contract.
            category = 2
        statements.append(_Statement(statement_id, node.start, statement.end, request,
            target, category, tuple(sorted(incoming.items())) if incoming is not None else None,
            evidence, prefix))
        if incoming is None:
            prefix = False
            reason = (evidence.reason if evidence else next((item.reason for item in graph.unavailable
                      if item.path == path and item.request == (request or '')), 'Unresolved import request'))
            for rid in RULE_IDS:
                skips[rid].append(f'Unproved import at byte {node.start}: {reason}')
    root_imports = {statement.node_id for statement in statements}
    if any(node.kind == 'ModuleImport' and i not in root_imports for i, node in enumerate(nodes)):
        for rid in RULE_IDS:
            skips[rid].append('Nested import groups are not analyzed')

    def add(rid, statement, explanation):
        start = statement.start
        line = source[:start].count(b'\n') + 1
        column = len(source[source.rfind(b'\n', 0, start) + 1:start].decode('utf-8')) + 1
        rule = rules[rid]
        finding = Diagnostic(rid, rule['grade'], path, line, column, explanation, rule['source'])
        hidden = any(scope.covers(rid, start) for scope in scopes)
        (suppressed if hidden else diagnostics)[rid].append(finding)
        return not hidden

    def refuse(rid, statement, reason):
        skips[rid].append(f'fix_unavailable at byte {statement.start}: {reason}')

    comments = any(node.kind in ('LineComment', 'BlockComment') for node in nodes)

    def order_comments_detached(group):
        """Allow only comments after a complete independent root construct.

        An immediately preceding, intervening, or following comment may attach
        to a moved import. A later comment cannot attach to the import group
        once a complete root construct intervenes. Reject comments nested in
        that construct rather than guessing their ownership.
        """
        comment_starts = [node.start for node in nodes
                          if node.kind in ('LineComment', 'BlockComment')]
        if not comment_starts:
            return True
        first_comment = min(comment_starts)
        if first_comment < group[-1].end:
            return False
        after = [nodes[index] for index in siblings
                 if nodes[index].start >= group[-1].end and nodes[index].kind not in _TRIVIA]
        if not after:
            return False
        first = after[0]
        if first.kind == 'Hash':
            if len(after) < 2 or after[1].start != first.end:
                return False
            first = after[1]
        return first.end <= first_comment

    def standalone(statement):
        start = source.rfind(b'\n', 0, statement.start) + 1
        end = source.find(b'\n', statement.end)
        end = len(source) if end < 0 else end
        return (not source[start:statement.start].strip(b' \t')
                and not source[statement.end:end].strip(b' \t\r'))

    recognized = [item for item in statements if item.category is not None]
    categories = [item.category for item in recognized]
    if categories != sorted(categories):
        first = recognized[0]
        active = add(ORDER, first, 'Place the proven styles facade before local figures and annotation imports.')
        group = statements[statements.index(first):statements.index(recognized[-1]) + 1]
        reason = None
        if any(item.category is None for item in group):
            reason = 'An unrelated import would be moved or crossed'
        elif not all(item.prefix and item.incoming is not None for item in group):
            reason = 'Unknown imports, prior execution or module initialization are not proved safe'
        elif not order_comments_detached(group):
            reason = 'Comment/directive attachment is not certified for moving imports'
        elif not all(standalone(item) for item in group):
            reason = 'Only standalone immediate import statements can move'
        elif any(source[left.end:right.start].strip(b' \t\r\n')
                 for left, right in zip(group, group[1:])):
            reason = 'Intervening source constructs cannot be crossed'
        elif any(scope.start < group[-1].end and scope.end > group[0].start for scope in scopes):
            reason = 'A directive scope would move'
        else:
            names = [name for item in group for name, _ in item.incoming]
            targets = [item.target for item in group]
            if len(names) != len(set(names)):
                reason = 'Import names collide or shadow each other'
            elif len(targets) != len(set(targets)) or any(
                    other.target in item.evidence.dependencies
                    for item in group for other in group if other is not item):
                reason = 'Imports depend on each other'
        if reason:
            refuse(ORDER, first, reason)
        elif active:
            ordered = sorted(group, key=lambda item: item.category)
            # Keep separator slots byte-for-byte. Only complete import syntax
            # changes position; no comments, literals, versions or gaps change.
            replacement = source[ordered[0].start:ordered[0].end]
            for offset, item in enumerate(ordered[1:]):
                replacement += source[group[offset].end:group[offset + 1].start]
                replacement += source[item.start:item.end]
            edits[ORDER].append(Edit(group[0].start, group[-1].end, replacement))

    # Track the live namespace only across the initial, whitespace-separated,
    # fully known import run. A shadowing/import/execution barrier clears proof.
    def trivia_gap(left, right):
        return all(nodes[i].kind in _TRIVIA for i in siblings
                   if nodes[i].start >= left.end and nodes[i].end <= right.start)

    env, facade = {}, {}
    previous = None
    for item in statements:
        if (not item.prefix or item.incoming is None or previous is not None
                and not trivia_gap(previous, item)):
            env, facade = {}, {}
        incoming = dict(item.incoming) if item.incoming is not None else {}
        compatibility = (item.evidence is not None and item.evidence.pure and item.evidence.reexports
                         and _style_path(item.target, ('local_tags.typ', 'mannot_utils.typ')))
        redundant = (compatibility and bool(incoming) and all(
            facade.get(name) == identity and env.get(name) == identity for name, identity in incoming.items()))
        if redundant:
            active = add(REDUNDANT, item, 'Every requested compatibility binding is already provided by the retained styles facade.')
            reason = None
            if comments:
                reason = 'Comment/directive attachment is not certified for deleting this import'
            elif not standalone(item):
                reason = 'Only a standalone immediate compatibility import can be removed'
            if reason:
                refuse(REDUNDANT, item, reason)
            elif active:
                start = source.rfind(b'\n', 0, item.start) + 1
                end = source.find(b'\n', item.end)
                end = len(source) if end < 0 else end + 1
                edits[REDUNDANT].append(Edit(start, end, b''))
        env.update(incoming)
        if item.category == 0:
            facade.update(incoming)
        previous = item
    results = {rid: ImportResult(tuple(diagnostics[rid]), tuple(edits[rid]), tuple(dict.fromkeys(skips[rid])),
                                 suppressed=tuple(suppressed[rid])) for rid in RULE_IDS}
    proposals = tuple(edit for result in results.values() for edit in result.edits)
    if proposals and _verify:
        try:
            planned = plan(source, proposals)
            if planned.deferred:
                raise SafetyError('Import proposals overlap')
            candidate = planned.candidate()
            checked = parse(candidate, tree.mode)
            if checked.nodes[0].errors:
                raise SafetyError('Import candidate did not parse')
            rebased = rebase_graph(graph, path, source, candidate)
            if rebased.source_for(path) is None or rebased.source_for(path).source != candidate:
                raise SafetyError('Import candidate lost its source-bound graph context')
            final = analyze(checked, path, graph=rebased, _verify=False)
            if any(result.edits for result in final.values()):
                raise SafetyError('Import candidate has second-pass proposals')
            def mapped(position):
                return position + sum(len(edit.replacement) - (edit.end - edit.start)
                                      for edit in planned.edits if edit.end <= position)

            for rid in RULE_IDS:
                previous = Counter(re.sub(r'at byte (\d+)',
                    lambda match: f'at byte {mapped(int(match.group(1)))}', reason)
                    for reason in results[rid].skips)
                if final[rid].parse_errors or Counter(final[rid].skips) - previous:
                    raise SafetyError('Import candidate introduces an analysis refusal')
                if len(final[rid].diagnostics) > len(results[rid].diagnostics):
                    raise SafetyError('Import candidate introduces findings')
                if final[rid].suppressed != results[rid].suppressed:
                    raise SafetyError('Import candidate changes suppression ownership')
            if analyze(parse(candidate, tree.mode), path, graph=rebased, _verify=False) != final:
                raise SafetyError('Import candidate is not deterministic')
        except SafetyError as error:
            return {rid: replace(result, edits=(), skips=(*result.skips, str(error))) for rid, result in results.items()}
    return results
