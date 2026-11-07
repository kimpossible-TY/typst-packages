"""Exhaustive rule backend for the separately admitted root paragraph slice.

This is not ordinary/global COMPLETE analysis. It independently visits every
root paragraph and executes the ordinary gap, marker-target and capitalization
policies, plus the shared literal marker insertion policy. The generic marker
analyzer keeps its PARTIAL skip and its default behavior.
"""
from dataclasses import dataclass
import re

from capitalization import BodyKind, Context, MarkerBinding, StartKind
from edit_plan import Edit
from paragraph_rules import (CAPITALIZATION, MARKER_GAP, MARKER_TARGET,
                             analyze as analyze_paragraphs)
from prose_boundary import valid_insertion_proof
from prose_marker_rules import (RULE_ID as PROSE_MARKER, ProseMarkerResult,
    _catalog_records, _casing_eligibility, _diagnostic, _verify_candidate)
from static_resolver import TEXT_UTILS, TEXT_UTILS_SOURCE_SHA256
from typst_lint import InputError, SafetyError, digest

_MARKER = b'#paragraph_tab'
_WS = re.compile(rb'[ \t]*(?:\r?\n[ \t]*)*\Z')
_LINE_GAP = re.compile(rb'[ \t]*\r?\n[ \t]*\Z')


def _require(ok, reason):
    if not ok:
        raise SafetyError(reason)


@dataclass(frozen=True, slots=True)
class ParagraphOwner:
    ordinal: int
    span: tuple[int, int]
    first_node: int
    first_span: tuple[int, int]
    first_word: bytes
    marker_span: tuple[int, int] | None
    marker_nodes: tuple[int, int] | None
    marker_gap_span: tuple[int, int] | None
    content_nodes: tuple[int, ...]


def _owners(tree, resolution):
    """Walk root AST siblings, never infer paragraphs from text searching."""
    source, nodes = tree.source, tree.nodes
    _require(tree.mode == 'markup' and nodes[0].kind == 'Markup' and not nodes[0].errors
             and resolution.source_sha256 == digest(source)
             and len(resolution.facts) == len(nodes) and not resolution.skips,
             'Bounded paragraph backend needs exact resolved root markup')
    ids, position, paragraphs = nodes[0].children, 0, []
    if ids and nodes[ids[0]].kind == 'Text' and source[:nodes[ids[0]].end] == b'\xef\xbb\xbf':
        position += 1

    def trivia():
        nonlocal position
        while position < len(ids) and nodes[ids[position]].kind in ('Space', 'Parbreak'):
            node = nodes[ids[position]]
            _require(_WS.fullmatch(source[node.start:node.end]) is not None,
                     'Bounded paragraph whitespace changed')
            position += 1

    shown_end = None
    for kind in ('ModuleImport', 'ShowRule'):
        trivia()
        _require(position + 1 < len(ids), 'Missing bounded paragraph setup')
        prefix, expression = (nodes[i] for i in ids[position:position + 2])
        _require(prefix.kind == 'Hash' and expression.kind == kind
                 and prefix.end == expression.start, 'Unexpected bounded root setup')
        position += 2
        shown_end = expression.end
    while position < len(ids):
        trivia()
        if position == len(ids):
            break
        marker_span = marker_nodes = marker_gap_span = None
        if nodes[ids[position]].kind == 'Hash':
            _require(position + 2 < len(ids), 'Incomplete bounded marker')
            h, m = ids[position:position + 2]
            hash_node, marker = nodes[h], nodes[m]
            binding = resolution.binding_for(m)
            _require(hash_node.end == marker.start and marker.kind == 'Ident'
                     and source[hash_node.start:marker.end] == _MARKER
                     and binding is not None and binding.kind == 'api'
                     and binding.name == 'paragraph_tab' and binding.package == TEXT_UTILS
                     and binding.source_sha256 == TEXT_UTILS_SOURCE_SHA256
                     and resolution.facts[m].prose_safe and not resolution.facts[m].deferred,
                     'Bounded marker must resolve to exact reviewed paragraph helper')
            start = source.rfind(b'\n', 0, hash_node.start) + 1
            finish = source.find(b'\n', marker.end)
            _require(finish >= 0 and not source[start:hash_node.start].strip(b' \t')
                     and not source[marker.end:finish].strip(b' \t\r'),
                     'Bounded marker is not standalone')
            position += 2
            gap = nodes[ids[position]]
            _require(gap.kind == 'Space'
                     and _LINE_GAP.fullmatch(source[gap.start:gap.end]) is not None,
                     'Bounded marker must immediately precede one literal prose line')
            position += 1
            marker_nodes, marker_span = (h, m), (hash_node.start, marker.end)
        _require(position < len(ids), 'Bounded paragraph has no prose')
        first_id, content = ids[position], []
        first = nodes[first_id]
        _require(first.kind == 'Text' and resolution.facts[first_id].prose_safe
                 and not resolution.facts[first_id].deferred
                 and resolution.language_for(first_id) == 'en',
                 'Bounded paragraph must start with literal English Text')
        start, end = first.start, first.end
        while position < len(ids):
            index = ids[position]
            node = nodes[index]
            raw = source[node.start:node.end]
            if node.kind in ('Space', 'Parbreak') and b'\n' in raw:
                break
            _require(node.kind in ('Text', 'Space', 'Equation')
                     and b'\n' not in raw and b'\r' not in raw
                     and resolution.facts[index].prose_safe
                     and not resolution.facts[index].deferred
                     and (node.kind != 'Equation' or node.display is False),
                     'Bounded paragraph contains nonliteral/unsupported root content')
            content.append(index)
            end = node.end
            position += 1
        _require(bool(content), 'Empty bounded paragraph')
        if marker_span:
            marker_gap_span = (marker_span[1], first.start)
        previous_end = paragraphs[-1].span[1] if paragraphs else shown_end
        target_start = marker_span[0] if marker_span else first.start
        gap = source[previous_end:target_start]
        _require(_WS.fullmatch(gap) is not None and b'\n' in gap,
                 'Bounded root paragraphs require a source-owned line boundary')
        if paragraphs and not marker_span:
            _require(gap.count(b'\n') >= 2,
                     'Missing-marker repair requires an already chosen blank prose boundary')
        word = re.match(rb'[A-Za-z]+', source[first.start:first.end])
        word = word[0] if word is not None and (marker_span or paragraphs) else b''
        paragraphs.append(ParagraphOwner(len(paragraphs), (start, end), first_id,
            (first.start, first.start + len(word)), word, marker_span, marker_nodes,
            marker_gap_span, tuple(content)))
    _require(bool(paragraphs), 'Bounded paragraph backend found no prose')
    return tuple(paragraphs)


def analyze(tree, path, resolution, *, catalog, paragraph_analyzer=None):
    """Execute all bounded paragraph policies from retained metadata only.

    The caller separately proves the exhaustive grammar. This backend does not
    accept caller repair lists, or waive ordinary prose-marker PARTIAL results.
    """
    if catalog is None:
        raise InputError('Bounded paragraph backend requires retained explicit catalog metadata')
    records = _catalog_records(catalog)
    owners = _owners(tree, resolution)
    safe_gap_markers = frozenset(p.marker_span[0] for p in owners if p.marker_span)
    backend = analyze_paragraphs if paragraph_analyzer is None else paragraph_analyzer
    actual = backend(tree, path, resolution, catalog=records,
                                _bounded_gap_markers=safe_gap_markers)
    metadata = {record['id']: record for record in records}
    findings, edits, contexts = [], [], []
    proof = resolution.paragraph_insertion_proof
    available = valid_insertion_proof(tree, proof)
    for paragraph in owners:
        if paragraph.marker_span:
            contexts.append(Context(digest(tree.source), path,
                3 if tree.source.startswith(b'\xef\xbb\xbf') else 0, len(tree.source),
                *paragraph.marker_span, *paragraph.first_span, paragraph.first_word,
                StartKind.LITERAL_WORD, BodyKind.ORDINARY_PROSE,
                MarkerBinding.PARAGRAPH_TAB, 'en'))
            continue
        if paragraph.ordinal == 0:
            continue  # The first document paragraph never acquires a marker.
        first = tree.nodes[paragraph.first_node]
        _require(available and paragraph.first_node in proof.available,
                 'New marker lacks reviewed immutable helper/show availability')
        at = tree.source.rfind(b'\n', 0, first.start) + 1
        newline = b'\r\n' if tree.source[at - 2:at] == b'\r\n' else b'\n'
        edit = Edit(at, at, _MARKER + newline)
        _casing_eligibility(tree, resolution, paragraph.first_node, edit,
                           allow_followup_casing=True, catalog=records)
        findings.append(_diagnostic(tree.source, path, metadata[PROSE_MARKER], first.start))
        edits.append(edit)
    if edits:
        _verify_candidate(tree, tuple(edits), (), catalog=records)
    actual[PROSE_MARKER] = ProseMarkerResult(tuple(findings), tuple(edits))
    return actual, tuple(contexts), owners
