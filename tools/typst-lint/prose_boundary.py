"""Source-owned literal root paragraph boundaries shared by footnote repairs.

No evaluator, text rewriting, language policy or suppression ownership lives
here. Returned coordinates refer only to the supplied immutable syntax tree.
"""
from dataclasses import dataclass
import re

from static_resolver import ParagraphInsertionProof, TEXT_UTILS, TEXT_UTILS_SOURCE_SHA256
from typst_lint import digest

_MARKER = b'#paragraph_tab'
_SETUP = frozenset(('ModuleImport', 'LetBinding', 'SetRule', 'ShowRule'))
_BLANK = re.compile(rb'(\r?\n)(?:[ \t]*\r?\n)+[ \t]*\Z')


def literal_paragraph(tree, resolution, indices, *, setup_prefix=False, marker_prefix=False):
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



def valid_insertion_proof(tree, proof):
    return (type(proof) is ParagraphInsertionProof and proof.source_sha256 == digest(tree.source)
            and all(type(values) is frozenset and all(type(i) is int and 0 <= i < len(tree.nodes)
                                                     for i in values)
                    for values in (proof.available, proof.parbreak_available)))


@dataclass(frozen=True)
class FootnoteBoundary:
    call_id: int
    target_id: int
    gap_id: int
    marked: bool
    has_break: bool
    break_at: int
    marker_at: int
    newline: bytes


def footnote_boundaries(tree, resolution):
    """Complete root prose -> literal builtin note -> one blank boundary.

    At most one exact standalone builtin break may already occupy the next
    source line. Only whitespace and an exact immutable marker may intervene
    before the following literal paragraph. Never consume existing bytes.
    """
    source, nodes = tree.source, tree.nodes
    if tree.mode != 'markup' or nodes[0].kind != 'Markup':
        return ()
    siblings = nodes[0].children
    boundaries = []
    for position in range(2, len(siblings)):
        call_id = siblings[position]
        call = nodes[call_id]
        binding = resolution.binding_for(call_id)
        if (call.kind != 'FuncCall' or not binding or binding.kind != 'builtin' or binding.name != 'footnote'
                or not resolution.facts[call_id].prose_safe or resolution.facts[call_id].deferred
                or len(call.children) != 2 or [nodes[i].kind for i in call.children] != ['Ident', 'Args']):
            continue
        hash_node = nodes[siblings[position - 1]]
        if (hash_node.kind != 'Hash' or hash_node.end != call.start
                or source[hash_node.start:call.end][:10] != b'#footnote['):
            continue
        args = nodes[call.children[1]]
        if len(args.children) != 1 or nodes[args.children[0]].kind != 'ContentBlock':
            continue
        content = nodes[args.children[0]]
        if ([nodes[i].kind for i in content.children] != ['LeftBracket', 'Markup', 'RightBracket']
                or any(nodes[i].kind not in ('Text', 'Space', 'Parbreak')
                       for i in nodes[content.children[1]].children)):
            continue
        following = position + 1
        has_break = False
        # A preexisting canonical next-line break is preserved, not duplicated.
        if following + 2 < len(siblings) and nodes[siblings[following]].kind == 'Space':
            space, prefix, br = (nodes[i] for i in siblings[following:following + 3])
            br_binding = resolution.binding_for(siblings[following + 2])
            if (re.fullmatch(rb'\r?\n[ \t]*', source[space.start:space.end])
                    and prefix.kind == 'Hash' and prefix.end == br.start
                    and br.kind == 'FuncCall' and source[prefix.start:br.end] == b'#parbreak()'
                    and len(br.children) == 2 and [nodes[i].kind for i in br.children] == ['Ident', 'Args']
                    and [nodes[i].kind for i in nodes[br.children[1]].children] == ['LeftParen', 'RightParen']
                    and br_binding and br_binding.kind == 'builtin' and br_binding.name == 'parbreak'):
                following += 3
                has_break = True
        if following >= len(siblings):
            continue
        gap_id = siblings[following]
        gap = nodes[gap_id]
        blank = _BLANK.fullmatch(source[gap.start:gap.end])
        if gap.kind != 'Parbreak' or not blank:
            continue
        before = position - 1
        while before > 0 and nodes[siblings[before - 1]].kind != 'Parbreak':
            before -= 1
        if not literal_paragraph(tree, resolution, siblings[before:position - 1],
                                 setup_prefix=before == 0, marker_prefix=True):
            continue
        after = following + 1
        while after < len(siblings) and nodes[siblings[after]].kind != 'Parbreak':
            after += 1
        paragraph = siblings[following + 1:after]
        marked = bool(paragraph and nodes[paragraph[0]].kind == 'Hash')
        if not literal_paragraph(tree, resolution, paragraph, marker_prefix=marked):
            continue
        target_id = next((i for i in paragraph if nodes[i].kind == 'Text'), None)
        if target_id is None or not resolution.facts[gap_id].prose_safe:
            continue
        marker_at = source.rfind(b'\n', gap.start, nodes[target_id].start) + 1
        # For marked paragraphs the insertion coordinate is unused; only the
        # existing marker's source-owned literal target matters.
        boundaries.append(FootnoteBoundary(call_id, target_id, gap_id, marked, has_break,
                                           call.end + len(blank.group(1)), marker_at, blank.group(1)))
    return tuple(boundaries)
