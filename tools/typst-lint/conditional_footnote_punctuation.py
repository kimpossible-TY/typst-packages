"""Source-bound punctuation hints with captured conditional footnote provenance.

The caller supplies a live issued binding session, not call records or syntax.
This module diagnoses only; it never produces edits or compiler authority.
"""
from dataclasses import dataclass, field

import conditional_footnote_binding as binding
from document_nested_footnote_punctuation import _Offsets
import footnote_rules
from static_resolver import resolve
from typst_lint import InputError, digest


@dataclass(frozen=True)
class PunctuationFinding:
    diagnostic: object
    call: binding.FootnoteBinding
    conditions: tuple[str, ...]
    status: str = field(default='CONDITIONAL', init=False)
    runtime_binding_proved: bool = field(default=False, init=False)
    edit_permission: bool = field(default=False, init=False)
    compiler_permission: bool = field(default=False, init=False)


@dataclass(frozen=True)
class PunctuationResult:
    status: str
    diagnostics: tuple[PunctuationFinding, ...] = ()
    suppressed: tuple[PunctuationFinding, ...] = ()
    unresolved: tuple[str, ...] = ()
    source_sha256: str | None = None
    coverage_complete: bool = field(default=False, init=False)
    runtime_binding_proved: bool = field(default=False, init=False)
    edit_permission: bool = field(default=False, init=False)
    compiler_permission: bool = field(default=False, init=False)


@dataclass(frozen=True)
class PunctuationBatch:
    status: str
    results: tuple = ()
    unresolved: tuple[str, ...] = ()
    coverage_complete: bool = field(default=False, init=False)
    edit_permission: bool = field(default=False, init=False)
    compiler_permission: bool = field(default=False, init=False)


def analyze(session, path):
    """Join fresh nested prose findings to exact session-issued call spans."""
    if type(session) is not binding.ConditionalFootnoteBinding or type(path) is not str:
        raise InputError('An issued conditional footnote session and source path are required')
    if not binding._valid(session):
        return PunctuationResult('UNAVAILABLE', unresolved=('Footnote capture is unissued or changed',))
    try:
        result = _analyze(session, path)
    except Exception:
        if not binding._valid(session):
            return PunctuationResult('UNAVAILABLE', unresolved=('Footnote capture changed during inspection',))
        raise
    if not binding._valid(session):
        return PunctuationResult('UNAVAILABLE', unresolved=('Footnote capture changed during inspection',))
    return result


def analyze_all(session, *, max_items):
    """Inspect the selected captured sources with one pair of seal checks."""
    if type(session) is not binding.ConditionalFootnoteBinding:
        raise InputError('An issued conditional footnote session is required')
    if type(max_items) is not int or max_items < 0:
        raise InputError('Batch item limit must be a nonnegative integer')
    if not binding._valid(session):
        return PunctuationBatch('UNAVAILABLE', unresolved=('Footnote capture is unissued or changed',))
    try:
        results, count = [], 0
        for source in session._context.sources:
            result = _analyze(session, source.path)
            count += 1 + len(result.unresolved)
            for finding in (*result.diagnostics, *result.suppressed):
                count += (1 + len(finding.conditions) + len(finding.call.conditions) + len(finding.call.facade_sources)
                          + len(finding.call.package_evidence) + len(finding.call.package_requests))
            if count > max_items:
                raise InputError('Footnote punctuation report item limit exceeded')
            results.append((source.path, result))
    except Exception:
        if not binding._valid(session):
            return PunctuationBatch('UNAVAILABLE', unresolved=('Footnote capture changed during inspection',))
        raise
    if not binding._valid(session):
        return PunctuationBatch('UNAVAILABLE', unresolved=('Footnote capture changed during inspection',))
    return PunctuationBatch('PARTIAL', tuple(results))


def _analyze(session, path):
    # Both public entry points seal the entire operation before and after.
    # Calling the concrete unchecked method avoids rehashing the whole graph
    # once per file in a bounded batch; caller-supplied call records are never used.
    trace = binding.ConditionalFootnoteBinding._analyze(session, path)
    if type(trace) is not binding.BindingResult or trace.status not in ('CONDITIONAL', 'PARTIAL'):
        return PunctuationResult('UNAVAILABLE', unresolved=('Captured footnote calls unavailable',))
    item = next((source for source in session._context.sources if source.path == path), None)
    graph_source = session._graph.source_for(path)
    if item is None or graph_source is None or item.source != graph_source.source:
        return PunctuationResult('UNAVAILABLE', unresolved=('Captured source is unavailable or changed',))
    tree, source = item.tree, item.source
    if tree.source != source or tree.mode != 'markup' or tree.nodes[0].errors:
        return PunctuationResult('UNAVAILABLE', unresolved=('Captured syntax is unavailable or changed',))
    source_hash = digest(source)
    spans = {(node.start - 1, node.end) for node in tree.nodes
             if node.kind == 'FuncCall' and node.start > 0 and source[node.start - 1:node.start] == b'#'}
    calls, seen = {}, set()
    unsupported_calls = 0
    for call in trace.calls:
        if (type(call) is not binding.FootnoteBinding or call.path != path or call.source_sha256 != source_hash
                or call.status != 'CONDITIONAL' or call.edit_permission or call.compiler_permission
                or (call.start, call.end) not in spans or call.start in seen
                or not source[call.start:call.end].startswith(b'#footnote')):
            return PunctuationResult('UNAVAILABLE', unresolved=('Captured footnote call does not match source',))
        seen.add(call.start)
        if not source[call.start:call.end].startswith(b'#footnote['):
            unsupported_calls += 1
            continue
        calls[call.start] = call
    resolution = resolve(tree, packages=())
    notes = footnote_rules.analyze_conditional_nested(tree, path, resolution)[footnote_rules.PUNCT]
    if notes.parse_errors:
        return PunctuationResult('UNAVAILABLE', unresolved=('Footnote source syntax is malformed',))
    offsets = _Offsets(source)
    unsupported = 0

    def join(rows):
        nonlocal unsupported
        findings = []
        for diagnostic in rows:
            call = calls.get(offsets.offset(diagnostic))
            if call is None:
                unsupported += 1
                continue
            findings.append(PunctuationFinding(diagnostic, call, call.conditions + (
                'The nested markup executes and renders as the expected prose context.',)))
        return tuple(findings)

    diagnostics, suppressed = join(notes.diagnostics), join(notes.suppressed)
    unresolved = list(trace.unresolved)
    if unsupported_calls:
        unresolved.append(f'{unsupported_calls} captured footnote call(s) use an unsupported argument form')
    if unsupported:
        unresolved.append(f'{unsupported} punctuation hint(s) lack captured footnote binding evidence')
    return PunctuationResult('PARTIAL', diagnostics, suppressed, tuple(dict.fromkeys(unresolved)), source_hash)
