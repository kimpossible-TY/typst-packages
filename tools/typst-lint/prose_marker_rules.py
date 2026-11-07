"""Partial marker insertion at already chosen literal root prose boundaries.

No imports, show rules or prose bytes are manufactured. A missing marker after
a literal footnote owns its required explicit break as one policy repair. The
shared dispatcher remains responsible for whole-candidate rule composition.
"""
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from collections.abc import Mapping
import re

from capitalization import BodyKind, Context, MarkerBinding, StartKind, evaluate
from edit_plan import Edit, plan
from prose_boundary import (footnote_boundaries, literal_paragraph as _literal_paragraph,
                            valid_insertion_proof)
from suppression import resolve as resolve_suppressions
from syntax_client import AdapterError, parse
from typst_lint import Diagnostic, InputError, SafetyError, digest, load_rules

RULE_ID = 'pde.prose-paragraph-marker'
_MARKER = b'#paragraph_tab'
_PARBREAK = b'#parbreak()'
_GAP = re.compile(rb'(?:[ \t]*\r?\n){2,}[ \t]*\Z')


@dataclass(frozen=True)
class ProseMarkerResult:
    diagnostics: tuple[Diagnostic, ...] = ()
    edits: tuple[Edit, ...] = ()
    skips: tuple[str, ...] = ()
    parse_errors: tuple[tuple[int, int], ...] = ()
    suppressed: tuple[Diagnostic, ...] = ()


def _diagnostic(source, path, rule, start):
    line = source[:start].count(b'\n') + 1
    column = len(source[source.rfind(b'\n', 0, start) + 1:start].decode('utf-8')) + 1
    return Diagnostic(RULE_ID, rule['grade'], path, line, column,
                      'Begin this existing new prose paragraph with the resolved paragraph marker.',
                      rule['source'])


def _casing_eligibility(tree, resolution, index, edit, *, allow_followup_casing=False, catalog=None):
    """Reuse the pure casing policy on a bounded exact source extraction.

    This is only a preflight. The complete batch must subsequently pass real
    parsing and original-Text/marker/target checks before any edit is returned.
    No allowlist, language policy or case behavior is reimplemented here.
    Opt-in admission of an exact evaluator repair does not return that repair
    or certify a second pass. The caller must reparse/reanalyze the candidate.
    """
    node = tree.nodes[index]
    word = re.match(rb'[A-Za-z]+', tree.source[node.start:node.end])
    if word is None:
        raise SafetyError('New marker would expose an unsupported capitalization initial')
    # Include exactly the original indentation, first word and following byte,
    # so complete-word/whitespace and EOF refusals remain the evaluator's own.
    fragment = b'\n' + edit.replacement + tree.source[edit.start:node.start + len(word[0]) + 1]
    start = 1 + len(edit.replacement) + node.start - edit.start
    context = Context(digest(fragment), 'candidate.typ', 0, len(fragment),
                      1, 1 + len(_MARKER), start, start + len(word[0]), word[0],
                      StartKind.LITERAL_WORD, BodyKind.ORDINARY_PROSE,
                      MarkerBinding.PARAGRAPH_TAB, resolution.language_for(index))
    result = evaluate(fragment, context, catalog=catalog)
    if result.edit or result.diagnostic:
        if (allow_followup_casing is True and result.skip_reason is None
                and result.diagnostic is not None and word[0].islower()
                and result.edit == Edit(start, start + 1, word[0][:1].upper())):
            return
        raise SafetyError('Marker insertion requires a later capitalization repair')
    if result.skip_reason != 'already-uppercase':
        raise SafetyError(f'Marker insertion would add capitalization nonapplicability: {result.skip_reason}')


def _verify_candidate(tree, edits, scopes, *, catalog=None):
    """Certify unchanged root text/targets and real standalone marker syntax.

    The original resolver proof certifies the inserted immutable marker value
    and active show. That exact marker cannot change effective language. This
    narrowly permits reusing the original prose/language facts with the pure
    casing evaluator before real candidate parsing, without inventing a second
    binding resolver or broadening the capitalization policy.
    """
    planned = plan(tree.source, edits)
    if planned.deferred:
        raise SafetyError('Conflicting paragraph marker insertions')
    candidate = planned.candidate()
    checked = parse(candidate, tree.mode)
    if checked.nodes[0].errors:
        raise SafetyError('Paragraph marker candidate failed to parse')

    positions = [edit.start for edit in planned.edits]
    shifts = [0]
    for edit in planned.edits:
        shifts.append(shifts[-1] + len(edit.replacement))

    def mapped(position, *, end=False):
        return position + shifts[(bisect_left if end else bisect_right)(positions, position)]

    text_nodes = [checked.nodes[i] for i in checked.nodes[0].children
                  if checked.nodes[i].kind == 'Text']
    cursor = 0
    for index in tree.nodes[0].children:
        node = tree.nodes[index]
        if node.kind == 'Text':
            start, end = mapped(node.start), mapped(node.end, end=True)
            while cursor < len(text_nodes) and text_nodes[cursor].end <= start:
                cursor += 1
            if cursor == len(text_nodes) or not text_nodes[cursor].start <= start < end <= text_nodes[cursor].end:
                raise SafetyError('Marker insertion would reinterpret original literal prose')
    root_nodes = {(checked.nodes[index].kind, checked.nodes[index].start, checked.nodes[index].end):
                  checked.nodes[index] for index in checked.nodes[0].children}
    for edit in planned.edits:
        start = mapped(edit.start, end=True)
        if edit.start != edit.end:
            raise SafetyError('Paragraph repair must contain only typed insertions')
        expression = edit.replacement.rstrip(b'\r\n')
        newline = edit.replacement[len(expression):]
        if expression not in (_MARKER, _PARBREAK) or newline not in (b'\n', b'\r\n'):
            raise SafetyError('Unknown paragraph repair insertion')
        kind = 'Ident' if expression == _MARKER else 'FuncCall'
        if (('Hash', start, start + 1) not in root_nodes
                or (kind, start + 1, start + len(expression)) not in root_nodes):
            raise SafetyError('Inserted marker or break is not a standalone root expression')
        if expression == _PARBREAK:
            call = root_nodes[('FuncCall', start + 1, start + len(expression))]
            if ([checked.nodes[i].kind for i in call.children] != ['Ident', 'Args']
                    or [checked.nodes[i].kind for i in checked.nodes[call.children[1]].children]
                    != ['LeftParen', 'RightParen']):
                raise SafetyError('Inserted paragraph break is not a canonical empty call')
    final_scopes = resolve_suppressions(checked, {rule['id'] for rule in _catalog_records(catalog)})
    expected_scopes = tuple((scope.rules, scope.reason, mapped(scope.start),
                             mapped(scope.end, end=True), mapped(scope.directive_start)) for scope in scopes)
    actual_scopes = tuple((scope.rules, scope.reason, scope.start, scope.end, scope.directive_start)
                          for scope in final_scopes)
    if actual_scopes != expected_scopes:
        raise SafetyError('Marker insertion would change directive targets')


def _catalog_records(catalog):
    if catalog is None:
        return load_rules()[0]['rules']
    if (type(catalog) not in (tuple, list)
            or any(not isinstance(record, Mapping) or type(record.get('id')) is not str
                   or not record['id'] for record in catalog)):
        raise InputError('Prose marker analysis requires explicit rule metadata records')
    for rid in (RULE_ID, 'pde.paragraph-capitalization'):
        records = [record for record in catalog if record.get('id') == rid]
        if (len(records) != 1
                or any(type(records[0].get(name)) is not str or not records[0][name]
                       for name in ('grade', 'source'))):
            raise InputError('Missing or invalid prose marker rule metadata')
    return catalog


def analyze(tree, path, resolution, *, allow_followup_casing=False, catalog=None):
    """Return marker/boundary insertions; opt-in casing needs an actual next pass."""
    catalog_records = _catalog_records(catalog) if catalog is not None else None
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        errors = tuple((node.start, node.end) for node in nodes if node.kind == 'Error')
        return ProseMarkerResult(skips=('Malformed Typst syntax; no repair proposed',),
                                 parse_errors=errors or ((0, len(source)),))
    if resolution.source_sha256 != digest(source) or len(resolution.facts) != len(nodes):
        return ProseMarkerResult(skips=('Stale or mismatched static resolution; no repair proposed',))
    if tree.mode != 'markup' or nodes[0].kind != 'Markup':
        return ProseMarkerResult(skips=('Only direct root markup prose boundaries are supported',))
    if catalog_records is None:
        catalog_records = _catalog_records(None)
    catalog = {rule['id']: rule for rule in catalog_records}
    scopes = resolve_suppressions(tree, set(catalog))
    proof = resolution.paragraph_insertion_proof
    valid_proof = valid_insertion_proof(tree, proof)
    findings, suppressed, edits = [], [], []
    skips = [*resolution.skips, 'Only complete literal root Text/Space paragraphs at existing whitespace-only Parbreak boundaries are supported']
    siblings = nodes[0].children
    for position in range(1, len(siblings) - 1):
        left_id, gap_id, right_id = siblings[position - 1:position + 2]
        left, gap, right = nodes[left_id], nodes[gap_id], nodes[right_id]
        if (left.kind, gap.kind, right.kind) != ('Text', 'Parbreak', 'Text'):
            continue
        if not _GAP.fullmatch(source[gap.start:gap.end]):
            continue
        before = position - 1
        while before > 0 and nodes[siblings[before - 1]].kind != 'Parbreak':
            before -= 1
        after = position + 2
        while after < len(siblings) and nodes[siblings[after]].kind != 'Parbreak':
            after += 1
        if (not _literal_paragraph(tree, resolution, siblings[before:position], setup_prefix=before == 0,
                                   marker_prefix=True)
                or not _literal_paragraph(tree, resolution, siblings[position + 1:after])):
            skips.append(f'Adjacent paragraph contains nonliteral or unproven root content at byte {right.start}')
            continue
        if not all(resolution.facts[index].prose_safe and not resolution.facts[index].deferred
                   for index in (left_id, gap_id, right_id)):
            skips.append(f'Unproven ordinary root prose boundary at byte {right.start}')
            continue
        finding = _diagnostic(source, path, catalog[RULE_ID], right.start)
        if any(scope.covers(RULE_ID, right.start) for scope in scopes):
            suppressed.append(finding)
            continue
        findings.append(finding)
        reason = None
        if not valid_proof or right_id not in proof.available:
            reason = 'canonical marker availability and active reviewed paragraph show are unproven'
        elif any(scope.start <= right.start < scope.end for scope in scopes):
            reason = 'inserting a marker would retarget an inline directive'
        if reason:
            skips.append(f'fix_unavailable: {reason} at byte {right.start}')
            continue
        at = source.rfind(b'\n', gap.start, right.start) + 1
        newline = b'\r\n' if source[at - 2:at] == b'\r\n' else b'\n'
        edit = Edit(at, at, _MARKER + newline)
        try:
            _casing_eligibility(tree, resolution, right_id, edit,
                                allow_followup_casing=allow_followup_casing, catalog=catalog_records)
        except SafetyError as error:
            skips.append(f'fix_unavailable: {error} at byte {right.start}')
        else:
            edits.append(edit)
    for boundary in footnote_boundaries(tree, resolution):
        if boundary.marked:
            continue
        right_id = boundary.target_id
        right = nodes[right_id]
        finding = _diagnostic(source, path, catalog[RULE_ID], right.start)
        if any(scope.covers(RULE_ID, right.start) for scope in scopes):
            suppressed.append(finding)
            continue
        findings.append(finding)
        reason = None
        if not valid_proof or right_id not in proof.available:
            reason = 'canonical marker availability and active reviewed paragraph show are unproven'
        elif right_id not in proof.parbreak_available:
            reason = 'canonical builtin parbreak availability is unproven'
        elif any(scope.start <= right.start < scope.end for scope in scopes):
            reason = 'inserting a marker would retarget an inline directive'
        if reason:
            skips.append(f'fix_unavailable: {reason} at byte {right.start}')
            continue
        marker = Edit(boundary.marker_at, boundary.marker_at, _MARKER + boundary.newline)
        try:
            _casing_eligibility(tree, resolution, right_id, marker,
                                allow_followup_casing=allow_followup_casing, catalog=catalog_records)
        except SafetyError as error:
            skips.append(f'fix_unavailable: {error} at byte {right.start}')
        else:
            if not boundary.has_break:
                edits.append(Edit(boundary.break_at, boundary.break_at, _PARBREAK + boundary.newline))
            edits.append(marker)
    if edits:
        try:
            _verify_candidate(tree, tuple(edits), scopes, catalog=catalog_records)
        except (AdapterError, SafetyError) as error:
            edits.clear()
            skips.append(f'fix_unavailable: {error}; no paragraph marker edits proposed')
    return ProseMarkerResult(tuple(findings), tuple(edits), tuple(dict.fromkeys(skips)),
                            suppressed=tuple(suppressed))
