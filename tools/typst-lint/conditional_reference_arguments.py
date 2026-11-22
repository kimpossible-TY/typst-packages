"""Conditional argument diagnostics for captured project-reference calls.

Only calls freshly traced by the supplied reference provenance session are
considered.  No caller-supplied call records, package evaluation, catalog
lookup, edits or runtime-binding claims are accepted or produced.
"""
from __future__ import annotations

from bisect import bisect_right
from array import array
from collections.abc import Mapping
from dataclasses import dataclass, field

import conditional_reference_provenance as provenance
import namespace_packages
import reference_rules as rules
import scoped_provenance
from static_resolver import (MANNOT_NAMESPACE, MANNOT_NAMESPACE_SOURCE_SHA256,
                             PROJECT_REFERENCE_LOCAL_PREFIXES, PackageSource, _literal_string)
from suppression import resolve as resolve_suppressions
from syntax_client import AdapterError, parse
from typst_lint import InputError, digest, load_rules

RULE_ID = rules.ARGUMENTS


def _scoped_tag(resolver, index):
    """Recognize only a direct, reviewed scope.tag with one literal name."""
    nodes = resolver.nodes
    if nodes[index].kind != 'FuncCall':
        return False
    parts = resolver.sig(index)
    binding = resolver.facts[index].binding
    if not (len(parts) == 2 and nodes[parts[1]].kind == 'Args' and binding
            and binding.kind == 'scope-field' and binding.name == 'tag'
            and binding.package == scoped_provenance.PACKAGE
            and binding.source_sha256 == scoped_provenance.SOURCE_SHA256):
        return False
    args = scoped_provenance.arguments(resolver, parts[1])
    if len(args) != 1 or _literal_string(resolver.tree, args[0]) is None:
        return False
    callee = parts[0]
    grouped = resolver.sig(callee)
    if (nodes[callee].kind != 'Parenthesized' or len(grouped) != 3
            or [nodes[i].kind for i in grouped] != ['LeftParen', 'FieldAccess', 'RightParen']):
        return False
    field = grouped[1]
    members = resolver.sig(field)
    if (len(members) != 3 or [nodes[i].kind for i in members] != ['Ident', 'Dot', 'Ident']
            or resolver.text(members[2]) != 'tag' or resolver.facts[field].binding != binding):
        return False
    receiver = resolver.facts[members[0]].binding
    return bool(receiver and receiver.kind in ('tag-scope', 'annotation-scope')
                and receiver.package == scoped_provenance.PACKAGE
                and receiver.source_sha256 == scoped_provenance.SOURCE_SHA256
                and receiver.source_identity == binding.source_identity)


def _scoped_resolver(analyzer, tree):
    """Reuse only the sealed session's reviewed scoped and wildcard packages."""
    if not provenance._valid_session(analyzer):
        return None
    packages = []
    for path, expected in ((scoped_provenance.PACKAGE, scoped_provenance.SOURCE_SHA256),
                           (MANNOT_NAMESPACE, MANNOT_NAMESPACE_SOURCE_SHA256)):
        item = analyzer._engine.packages.get(path)
        if (type(item) is not namespace_packages.NamespacePackage
                or item.evidence.import_path != path or item.evidence.source_sha256 != expected
                or digest(item.source) != expected):
            return None
        packages.append(PackageSource(path, item.evidence.source_identity, item.source))
    resolver = scoped_provenance.resolve(tree, tuple(packages))
    # The general scope resolver tracks rebinding, not arbitrary dictionary
    # methods or escaping a scope value. Do not infer an unchanged tag field
    # when a scope value is used for anything except direct tag access.
    parents = {child: index for index, node in enumerate(tree.nodes) for child in node.children}
    for index, node in enumerate(tree.nodes):
        binding = resolver.facts[index].binding
        if node.kind != 'Ident' or not binding or binding.kind not in ('tag-scope', 'annotation-scope'):
            continue
        parent = parents.get(index)
        parts = resolver.sig(parent) if parent is not None else ()
        if (parent is None or tree.nodes[parent].kind != 'FieldAccess' or len(parts) != 3
                or parts[0] != index or resolver.text(parts[2]) != 'tag'):
            return None
    return resolver


@dataclass(frozen=True)
class ArgumentFinding:
    rule_id: str
    grade: str
    path: str
    line: int
    column: int
    explanation: str
    source: str
    call_start: int
    call_end: int
    source_sha256: str
    route: tuple
    source_hashes: tuple
    package_evidence: tuple
    package_requests: tuple
    conditions: tuple[str, ...]
    status: str = field(default='CONDITIONAL', init=False)
    edit_permission: bool = field(default=False, init=False)


@dataclass(frozen=True)
class ArgumentResult:
    status: str
    diagnostics: tuple[ArgumentFinding, ...] = ()
    suppressed: tuple[ArgumentFinding, ...] = ()
    unresolved: tuple[str, ...] = ()
    trace: provenance.ReferenceCalls | None = None
    edit_permission: bool = field(default=False, init=False)
    catalog_checked: bool = field(default=False, init=False)


def analyze(analyzer, path, *, catalog=None):
    """Inspect exactly the session's traced calls; unresolved values stay unknown.

    The analyzer remains conditional on its captured graph and namespace
    evidence, module initialization, binding stability and execution.  A
    supported call can coexist with unrelated uninspected contexts, so a
    PARTIAL result may carry valid conditional findings.
    """
    if type(analyzer) is not provenance.ConditionalReferenceProvenance:
        raise InputError('Conditional reference arguments require a provenance session')
    if type(path) is not str:
        raise InputError('Conditional reference arguments require a source path')
    if (type(getattr(analyzer, '_engine', None)) is not provenance._ReferenceNamespace
            or not hasattr(analyzer, '_reference_capture') or not hasattr(analyzer, '_selection_reason')):
        return ArgumentResult('UNAVAILABLE', unresolved=('Reference provenance session is uninitialized',))
    if not provenance._valid_session(analyzer):
        return ArgumentResult('UNAVAILABLE', unresolved=('Session capture changed or is unissued',))
    try:
        result = _analyze_unchecked(analyzer, path, catalog=catalog)
    except Exception:
        if not provenance._valid_session(analyzer):
            return ArgumentResult('UNAVAILABLE', unresolved=('Session capture changed during inspection',))
        raise
    if not provenance._valid_session(analyzer):
        return ArgumentResult('UNAVAILABLE', unresolved=('Session capture changed during inspection',))
    return result


def _analyze_unchecked(analyzer, path, *, catalog=None):
    records = load_rules()[0]['rules'] if catalog is None else catalog
    if (type(records) not in (tuple, list)
            or any(not isinstance(row, Mapping) or type(row.get('id')) is not str or not row['id']
                   for row in records)):
        raise InputError('Conditional reference arguments require rule metadata')
    selected = [row for row in records if row['id'] == RULE_ID]
    if (len(selected) != 1 or any(type(selected[0].get(key)) is not str or not selected[0][key]
                                   for key in ('grade', 'source'))):
        raise InputError('Missing or invalid reference argument rule metadata')
    # Call the concrete class method: a serialized record or an overriding
    # instance attribute cannot provide candidate calls.
    traced = provenance.ConditionalReferenceProvenance.calls(analyzer, path)
    if type(traced) is not provenance.ReferenceCalls or traced.status not in ('CONDITIONAL', 'PARTIAL', 'UNAVAILABLE'):
        return ArgumentResult('UNAVAILABLE', unresolved=('Invalid reference provenance result',))
    if traced.status == 'UNAVAILABLE':
        return ArgumentResult('UNAVAILABLE', unresolved=traced.unresolved, trace=traced)
    item = analyzer._engine.graph.source_for(path)
    if item is None or path not in analyzer._engine.graph.selected or digest(item.source) != item.sha256:
        return ArgumentResult('UNAVAILABLE', unresolved=('Captured source is unavailable or changed',))
    try:
        tree = parse(item.source)
    except (AdapterError, OSError, ValueError) as error:
        return ArgumentResult('UNAVAILABLE', unresolved=(f'Source syntax unavailable: {error}',))
    if tree.mode != 'markup' or tree.nodes[0].errors:
        return ArgumentResult('UNAVAILABLE', unresolved=('Malformed or non-markup source',))
    nodes, source = tree.nodes, tree.source
    calls_by_span = {}
    for index, node in enumerate(nodes):
        if node.kind == 'FuncCall':
            span = node.start, node.end
            if span in calls_by_span:
                return ArgumentResult('UNAVAILABLE', unresolved=('Ambiguous captured call span',))
            calls_by_span[span] = index
    scopes = resolve_suppressions(tree, {row['id'] for row in records})
    newlines = [i for i, byte in enumerate(source) if byte == 10]
    column_offsets = {}
    findings, suppressed, unresolved = [], [], list(traced.unresolved)
    rule = selected[0]
    scoped = None
    scoped_checked = False

    def add(call, start, explanation):
        line = bisect_right(newlines, start) + 1
        previous = newlines[line - 2] + 1 if line > 1 else 0
        columns = column_offsets.get(line)
        if columns is None:
            line_end = newlines[line - 1] if line <= len(newlines) else len(source)
            columns = array('I', [0])
            length = 0
            for char in source[previous:line_end].decode('utf-8'):
                length += len(char.encode('utf-8'))
                columns.append(length)
            column_offsets[line] = columns
        offset = start - previous
        column = bisect_right(columns, offset)
        if column < 1 or columns[column - 1] != offset:
            raise InputError('Reference argument location is not a UTF-8 boundary')
        finding = ArgumentFinding(RULE_ID, rule['grade'], path, line, column, explanation, rule['source'],
            call.start, call.end, item.sha256, call.route, call.source_hashes, call.package_evidence,
            call.package_requests, call.conditions)
        (suppressed if any(scope.covers(RULE_ID, start) for scope in scopes) else findings).append(finding)

    for call in traced.calls:
        if (type(call) is not provenance.ReferenceCall or call.path != path or call.source_sha256 != item.sha256
                or type(call.start) is not int or type(call.end) is not int
                or not 0 <= call.start < call.end <= len(source)
                or call.status != 'CONDITIONAL' or call.edit_permission
                or (call.start, call.end) not in calls_by_span):
            return ArgumentResult('UNAVAILABLE', unresolved=('Reference call does not match captured syntax',), trace=None)
        index = calls_by_span[(call.start, call.end)]
        parts = rules._sig(tree, index)
        if len(parts) != 2 or nodes[parts[1]].kind != 'Args':
            unresolved.append(f'Unsupported argument syntax at byte {call.start}')
            continue
        args = [i for i in rules._sig(tree, parts[1]) if nodes[i].kind not in rules._ARG_PUNCTUATION]
        named = [i for i in args if nodes[i].kind == 'Named']
        spread = any(nodes[i].kind == 'Spread' for i in args)
        positional = [i for i in args if nodes[i].kind not in ('Named', 'Spread')]
        if named or len(positional) > 2 or (not spread and len(positional) != 2):
            add(call, call.start, 'If this call binds project-ref, it requires exactly two positional arguments and no named arguments.')
            continue
        if spread:
            unresolved.append(f'Spread argument arity and values are unresolved at byte {call.start}')
            continue
        project_id, target_id = positional
        project = rules._ungroup(tree, project_id)
        target = rules._ungroup(tree, target_id)
        project_type = rules._KNOWN_TYPES.get(nodes[project].kind)
        target_type = rules._KNOWN_TYPES.get(nodes[target].kind)
        if project_type is None:
            unresolved.append(f'Project argument type is unresolved at byte {nodes[project_id].start}')
        elif project_type != 'string':
            add(call, nodes[project_id].start, 'If this call binds project-ref, its project must be a string alias.')
        if target_type is None:
            if not scoped_checked:
                scoped = _scoped_resolver(analyzer, tree)
                scoped_checked = True
            if scoped is not None and _scoped_tag(scoped, target):
                add(call, nodes[target_id].start,
                    'If this call binds project-ref and the reviewed scoped callback executes, '
                    'its scope-generated target is not a global label.')
            else:
                unresolved.append(f'Target type and global identity are unresolved at byte {nodes[target_id].start}')
        elif target_type != 'label':
            add(call, nodes[target_id].start,
                f'If this call binds project-ref, its target must be a global Typst label, not {target_type}.')
        else:
            label = rules._label(tree, target)
            if label is not None and label.startswith(PROJECT_REFERENCE_LOCAL_PREFIXES):
                add(call, nodes[target_id].start,
                    'If this call binds project-ref, its target uses a rejected local-scope label prefix.')
    return ArgumentResult('PARTIAL' if traced.status == 'PARTIAL' or unresolved else 'CONDITIONAL',
                          tuple(findings), tuple(suppressed),
                          tuple(dict.fromkeys(unresolved)), trace=traced)
