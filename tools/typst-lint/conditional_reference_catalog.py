"""Conditional catalog checks for freshly traced project-reference calls.

Catalogs are explicit locally issued snapshots. This module neither evaluates
Typst nor proves catalog freshness, runtime execution, or permission to edit.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from array import array
from collections.abc import Mapping
from dataclasses import dataclass, field
import os
from pathlib import Path
import weakref

import conditional_reference_arguments as arguments
import conditional_reference_provenance as provenance
from package_loader import read_snapshot_file
from reference_catalog import MAX_CATALOG_BYTES, CatalogProject, ReferenceCatalog, parse_reference_catalog
import reference_rules as rules
from static_resolver import PROJECT_REFERENCE_LOCAL_PREFIXES
from suppression import resolve as resolve_suppressions
from syntax_client import AdapterError, parse
from typst_lint import InputError, digest, load_rules, require

RULE_ID = rules.CATALOG_TARGET
_ISSUED = {}


@dataclass(frozen=True, eq=False)
class CatalogCapture:
    catalog: ReferenceCatalog


def _signature(capture):
    catalog = capture.catalog
    return (type(catalog), catalog.source_identity, catalog.sha256, type(catalog.projects),
            tuple((type(project), project.alias, type(project.labels), frozenset(project.labels),
                   type(project.ambiguous_labels), frozenset(project.ambiguous_labels))
                  for project in catalog.projects))


def capture_catalog(raw, source_identity):
    """Validate explicit immutable bytes and issue one in-process catalog capture."""
    capture = CatalogCapture(parse_reference_catalog(raw, source_identity))
    key = id(capture)
    def release(ref):
        current = _ISSUED.get(key)
        if current is not None and current[0] is ref:
            _ISSUED.pop(key, None)
    _ISSUED[key] = (weakref.ref(capture, release), _signature(capture))
    return capture


def capture_catalog_path(path):
    """Safely read one explicit bounded path and capture its catalog bytes."""
    try:
        raw_path = os.fspath(path)
    except TypeError as error:
        raise InputError('Catalog path must be canonical and absolute') from error
    require(type(raw_path) is str and raw_path.startswith('/') and str(Path(raw_path)) == raw_path,
            'Catalog path must be canonical and absolute')
    identity, raw = read_snapshot_file(raw_path, MAX_CATALOG_BYTES)
    return capture_catalog(raw, identity)


def verify_catalog(capture):
    try:
        issued = _ISSUED.get(id(capture))
        if (type(capture) is not CatalogCapture or issued is None or issued[0]() is not capture
                or type(capture.catalog) is not ReferenceCatalog
                or type(capture.catalog.source_identity) is not str or type(capture.catalog.sha256) is not str
                or type(capture.catalog.projects) is not tuple
                or any(type(project) is not CatalogProject or type(project.alias) is not str
                       or type(project.labels) is not frozenset or type(project.ambiguous_labels) is not frozenset
                       for project in capture.catalog.projects)):
            return False
        return issued[1] == _signature(capture)
    except (AttributeError, TypeError, ValueError):
        return False


@dataclass(frozen=True)
class CatalogFinding:
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
    catalog_identity: str
    catalog_sha256: str
    route: tuple
    source_hashes: tuple
    package_evidence: tuple
    package_requests: tuple
    conditions: tuple[str, ...]
    status: str = field(default='CONDITIONAL', init=False)
    edit_permission: bool = field(default=False, init=False)


@dataclass(frozen=True)
class CatalogResult:
    status: str
    diagnostics: tuple[CatalogFinding, ...] = ()
    suppressed: tuple[CatalogFinding, ...] = ()
    unresolved: tuple[str, ...] = ()
    checked_calls: int = 0
    catalog_identity: str | None = None
    catalog_sha256: str | None = None
    trace: provenance.ReferenceCalls | None = None
    argument_result: arguments.ArgumentResult | None = None
    edit_permission: bool = field(default=False, init=False)
    freshness_proved: bool = field(default=False, init=False)
    runtime_selection_proved: bool = field(default=False, init=False)


def analyze(analyzer, path, capture, *, catalog=None):
    """Check literal project/label arguments against the exact issued snapshot."""
    require(verify_catalog(capture), 'Conditional reference catalog requires an issued unchanged capture')
    if type(analyzer) is not provenance.ConditionalReferenceProvenance:
        raise InputError('Conditional reference arguments require a provenance session')
    if type(path) is not str:
        raise InputError('Conditional reference arguments require a source path')
    if not provenance._valid_session(analyzer):
        return CatalogResult('UNAVAILABLE', unresolved=('Session capture changed or is unissued',))
    try:
        result = _analyze_unchecked(analyzer, path, capture, catalog=catalog)
    except Exception:
        if not provenance._valid_session(analyzer):
            return CatalogResult('UNAVAILABLE', unresolved=('Session capture changed during inspection',))
        if not verify_catalog(capture):
            return CatalogResult('UNAVAILABLE', unresolved=('Catalog capture changed during inspection',))
        raise
    if not provenance._valid_session(analyzer):
        return CatalogResult('UNAVAILABLE', unresolved=('Session capture changed during inspection',))
    if not verify_catalog(capture):
        return CatalogResult('UNAVAILABLE', unresolved=('Catalog capture changed during inspection',))
    return result


def _analyze_unchecked(analyzer, path, capture, *, catalog=None):
    records = load_rules()[0]['rules'] if catalog is None else catalog
    require(type(records) in (tuple, list) and all(isinstance(row, Mapping) and type(row.get('id')) is str
            and row['id'] for row in records), 'Conditional reference catalog requires rule metadata')
    selected = [row for row in records if row['id'] == RULE_ID]
    require(len(selected) == 1 and all(type(selected[0].get(key)) is str and selected[0][key]
            for key in ('grade', 'source')), 'Missing or invalid reference catalog rule metadata')
    evidence = capture.catalog
    inspected = arguments.analyze(analyzer, path, catalog=records)
    trace = inspected.trace
    if trace is None or trace.status == 'UNAVAILABLE':
        return CatalogResult('UNAVAILABLE', unresolved=inspected.unresolved,
                             catalog_identity=evidence.source_identity, catalog_sha256=evidence.sha256,
                             trace=trace, argument_result=inspected)
    item = analyzer._engine.graph.source_for(path)
    if item is None or digest(item.source) != item.sha256:
        return CatalogResult('UNAVAILABLE', unresolved=('Captured source unavailable or changed',),
                             catalog_identity=evidence.source_identity, catalog_sha256=evidence.sha256)
    try:
        tree = parse(item.source)
    except (AdapterError, OSError, ValueError) as error:
        return CatalogResult('UNAVAILABLE', unresolved=(f'Source syntax unavailable: {error}',),
                             catalog_identity=evidence.source_identity, catalog_sha256=evidence.sha256)
    if tree.nodes[0].errors or tree.mode != 'markup':
        return CatalogResult('UNAVAILABLE', unresolved=('Malformed or non-markup source',),
                             catalog_identity=evidence.source_identity, catalog_sha256=evidence.sha256)
    nodes, source = tree.nodes, tree.source
    by_span = {}
    for i, node in enumerate(nodes):
        if node.kind == 'FuncCall':
            span = (node.start, node.end)
            if span in by_span:
                return CatalogResult('UNAVAILABLE', unresolved=('Ambiguous captured call span',),
                                     catalog_identity=evidence.source_identity, catalog_sha256=evidence.sha256)
            by_span[span] = i
    scopes = resolve_suppressions(tree, {row['id'] for row in records})
    newlines = [i for i, byte in enumerate(source) if byte == 10]
    columns = {}
    findings, suppressed, unresolved, checked = [], [], list(trace.unresolved), 0
    invalid_calls = {(finding.call_start, finding.call_end)
                     for finding in (*inspected.diagnostics, *inspected.suppressed)}

    def add(call, start, explanation):
        line = bisect_right(newlines, start) + 1
        previous = newlines[line - 2] + 1 if line > 1 else 0
        offsets = columns.get(line)
        if offsets is None:
            end = newlines[line - 1] if line <= len(newlines) else len(source)
            offsets = array('I', [0])
            length = 0
            for char in source[previous:end].decode('utf-8'):
                length += len(char.encode('utf-8'))
                offsets.append(length)
            columns[line] = offsets
        column = bisect_right(offsets, start - previous)
        require(column >= 1 and offsets[column - 1] == start - previous, 'Catalog diagnostic is not on a UTF-8 boundary')
        rule = selected[0]
        finding = CatalogFinding(RULE_ID, rule['grade'], path, line, column, explanation, rule['source'],
            call.start, call.end, item.sha256, evidence.source_identity, evidence.sha256, call.route,
            call.source_hashes, call.package_evidence, call.package_requests, call.conditions + (
                'Catalog membership is based only on the explicit captured snapshot; its freshness and PDF correspondence are unproved.',))
        (suppressed if any(scope.covers(RULE_ID, start) for scope in scopes) else findings).append(finding)

    for call in trace.calls:
        if (type(call) is not provenance.ReferenceCall or call.path != path or call.source_sha256 != item.sha256
                or call.edit_permission or (call.start, call.end) not in by_span):
            return CatalogResult('UNAVAILABLE', unresolved=('Reference call does not match captured syntax',),
                                 catalog_identity=evidence.source_identity, catalog_sha256=evidence.sha256)
        parts = rules._sig(tree, by_span[(call.start, call.end)])
        if len(parts) != 2 or nodes[parts[1]].kind != 'Args':
            unresolved.append(f'Unsupported argument syntax at byte {call.start}')
            continue
        values = [i for i in rules._sig(tree, parts[1]) if nodes[i].kind not in rules._ARG_PUNCTUATION]
        if len(values) != 2 or any(nodes[i].kind in ('Named', 'Spread') for i in values):
            unresolved.append(f'Catalog membership requires exactly two direct positional arguments at byte {call.start}')
            continue
        project_id, target_id = values
        project = rules._string(tree, rules._ungroup(tree, project_id))
        label = rules._label(tree, rules._ungroup(tree, target_id))
        if project is None or label is None or label.startswith(PROJECT_REFERENCE_LOCAL_PREFIXES):
            # A literal key's absence is independent of an unresolved target.
            # Known invalid arguments are already reported by the argument
            # checker; do not imply that those calls can reach catalog lookup.
            target = rules._ungroup(tree, target_id)
            if (project is not None and label is None and (call.start, call.end) not in invalid_calls
                    and rules._KNOWN_TYPES.get(nodes[target].kind) is None):
                project_index = bisect_left(evidence.projects, project, key=lambda p: p.alias)
                if project_index == len(evidence.projects) or evidence.projects[project_index].alias != project:
                    add(call, nodes[project_id].start,
                        f'If this call binds project-ref and its target is a valid global label, '
                        f'the captured catalog has no project {project!r}.')
            unresolved.append(f'Catalog membership requires a literal project and global label at byte {call.start}')
            continue
        checked += 1
        membership = evidence.membership(project, label)
        if membership == 'unknown-project':
            add(call, nodes[project_id].start, f'If this call binds project-ref, the captured catalog has no project {project!r}.')
        elif membership == 'ambiguous-label':
            add(call, nodes[target_id].start, f'If this call binds project-ref, label <{label}> is ambiguous in captured project {project!r}.')
        elif membership == 'missing-label':
            add(call, nodes[target_id].start, f'If this call binds project-ref, the captured catalog lacks label <{label}> in project {project!r}.')
        elif membership != 'unique':
            return CatalogResult('UNAVAILABLE', unresolved=('Invalid catalog membership result',),
                                 catalog_identity=evidence.source_identity, catalog_sha256=evidence.sha256)
    if not verify_catalog(capture):
        return CatalogResult('UNAVAILABLE', unresolved=('Catalog capture changed during inspection',))
    return CatalogResult('PARTIAL' if unresolved else 'CONDITIONAL', tuple(findings), tuple(suppressed),
                         tuple(dict.fromkeys(unresolved)), checked, evidence.source_identity, evidence.sha256,
                         trace, inspected)
