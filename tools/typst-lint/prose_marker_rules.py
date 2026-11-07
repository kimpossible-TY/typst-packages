"""Partial marker insertion at already chosen literal root prose boundaries.

No imports, show rules, paragraph breaks or prose bytes are manufactured. The
shared dispatcher remains responsible for whole-candidate rule composition.
"""
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import re

from capitalization import BodyKind, Context, MarkerBinding, StartKind, evaluate
from edit_plan import Edit, plan
from static_resolver import ParagraphInsertionProof, TEXT_UTILS, TEXT_UTILS_SOURCE_SHA256
from suppression import resolve as resolve_suppressions
from syntax_client import AdapterError, parse
from typst_lint import Diagnostic, SafetyError, digest, load_rules

RULE_ID = 'pde.prose-paragraph-marker'
_MARKER = b'#paragraph_tab'
_GAP = re.compile(rb'(?:[ \t]*\r?\n){2,}[ \t]*\Z')
_SETUP = frozenset(('ModuleImport', 'LetBinding', 'SetRule', 'ShowRule'))


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


def _literal_paragraph(tree, resolution, indices, *, setup_prefix=False, marker_prefix=False):
    """Inspect the whole adjacent paragraph, never just its final text leaf.

    Only the first root paragraph can have a prefix of standalone nonoutput
    declarations. Each declaration is a complete Hash/expression pair; comments,
    includes, calls, structural output and directives are never crossed.
    """
    source, nodes = tree.source, tree.nodes
    position = 0
    if (indices and nodes[indices[0]].start == 0 and nodes[indices[0]].kind == 'Text'
            and source[:nodes[indices[0]].end] == b'\xef\xbb\xbf'):
        position += 1
    if setup_prefix:
        while position < len(indices):
            if nodes[indices[position]].kind == 'Space':
                position += 1
                continue
            if position + 1 >= len(indices):
                break
            hash_node, expression = nodes[indices[position]], nodes[indices[position + 1]]
            if hash_node.kind != 'Hash' or expression.kind not in _SETUP or hash_node.end != expression.start:
                break
            line_start = source.rfind(b'\n', 0, hash_node.start) + 1
            prefix = source[line_start:hash_node.start]
            if line_start == 0 and prefix.startswith(b'\xef\xbb\xbf'):
                prefix = prefix[3:]
            line_end = source.find(b'\n', expression.end)
            line_end = len(source) if line_end < 0 else line_end
            if prefix.strip(b' \t') or source[expression.end:line_end].strip(b' \t\r'):
                break
            position += 2
    if marker_prefix and position + 1 < len(indices):
        hash_node, marker = nodes[indices[position]], nodes[indices[position + 1]]
        binding = resolution.binding_for(indices[position + 1])
        line_start = source.rfind(b'\n', 0, hash_node.start) + 1
        line_end = source.find(b'\n', marker.end)
        if (hash_node.kind == 'Hash' and marker.kind == 'Ident' and hash_node.end == marker.start
                and source[hash_node.start:marker.end] == _MARKER
                and not source[line_start:hash_node.start].strip(b' \t')
                and line_end >= 0 and not source[marker.end:line_end].strip(b' \t\r')
                and binding and binding.kind == 'api' and binding.name == 'paragraph_tab'
                and binding.package == TEXT_UTILS and binding.source_sha256 == TEXT_UTILS_SOURCE_SHA256):
            position += 2
    paragraph = indices[position:]
    return bool(paragraph) and any(nodes[index].kind == 'Text' for index in paragraph) and all(
        nodes[index].kind in ('Text', 'Space') and resolution.facts[index].prose_safe
        and not resolution.facts[index].deferred for index in paragraph)


def _casing_eligibility(tree, resolution, index, edit, *, allow_followup_casing=False):
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
    result = evaluate(fragment, context)
    if result.edit or result.diagnostic:
        if (allow_followup_casing is True and result.skip_reason is None
                and result.diagnostic is not None and word[0].islower()
                and result.edit == Edit(start, start + 1, word[0][:1].upper())):
            return
        raise SafetyError('Marker insertion requires a later capitalization repair')
    if result.skip_reason != 'already-uppercase':
        raise SafetyError(f'Marker insertion would add capitalization nonapplicability: {result.skip_reason}')


def _verify_candidate(tree, edits, scopes):
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
    root_spans = {(checked.nodes[index].kind, checked.nodes[index].start, checked.nodes[index].end)
                  for index in checked.nodes[0].children}
    for edit in planned.edits:
        start = mapped(edit.start, end=True)
        if (('Hash', start, start + 1) not in root_spans
                or ('Ident', start + 1, start + len(_MARKER)) not in root_spans):
            raise SafetyError('Inserted marker is not a standalone root expression')
    final_scopes = resolve_suppressions(checked, {rule['id'] for rule in load_rules()[0]['rules']})
    expected_scopes = tuple((scope.rules, scope.reason, mapped(scope.start),
                             mapped(scope.end, end=True), mapped(scope.directive_start)) for scope in scopes)
    actual_scopes = tuple((scope.rules, scope.reason, scope.start, scope.end, scope.directive_start)
                          for scope in final_scopes)
    if actual_scopes != expected_scopes:
        raise SafetyError('Marker insertion would change directive targets')


def analyze(tree, path, resolution, *, allow_followup_casing=False):
    """Return marker-only proposals; opt-in casing needs an actual next pass."""
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        errors = tuple((node.start, node.end) for node in nodes if node.kind == 'Error')
        return ProseMarkerResult(skips=('Malformed Typst syntax; no repair proposed',),
                                 parse_errors=errors or ((0, len(source)),))
    if resolution.source_sha256 != digest(source) or len(resolution.facts) != len(nodes):
        return ProseMarkerResult(skips=('Stale or mismatched static resolution; no repair proposed',))
    if tree.mode != 'markup' or nodes[0].kind != 'Markup':
        return ProseMarkerResult(skips=('Only direct root markup prose boundaries are supported',))
    catalog = {rule['id']: rule for rule in load_rules()[0]['rules']}
    scopes = resolve_suppressions(tree, set(catalog))
    proof = resolution.paragraph_insertion_proof
    valid_proof = (type(proof) is ParagraphInsertionProof and proof.source_sha256 == digest(source)
                   and type(proof.available) is frozenset
                   and all(type(index) is int and 0 <= index < len(nodes) for index in proof.available))
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
                                allow_followup_casing=allow_followup_casing)
        except SafetyError as error:
            skips.append(f'fix_unavailable: {error} at byte {right.start}')
        else:
            edits.append(edit)
    if edits:
        try:
            _verify_candidate(tree, tuple(edits), scopes)
        except (AdapterError, SafetyError) as error:
            edits.clear()
            skips.append(f'fix_unavailable: {error}; no paragraph marker edits proposed')
    return ProseMarkerResult(tuple(findings), tuple(edits), tuple(dict.fromkeys(skips)),
                            suppressed=tuple(suppressed))
