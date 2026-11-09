"""Bounded display-source rules over official typed Equation properties.

Only direct equations and literal prose in statically proven root markup are
supported. Nested/captured bodies, unknown inline wrappers and ambiguous labels
remain explicit partial coverage. This module proposes bytes, never writes.

Both rules belong to the equation: the diagnostic and suppression anchor is its
opening delimiter, including for a continuation after its attached label. An
ignore-next on following prose does not acquire ownership of the display gap.
Parent composition must reparse and verify all rules and suppression ownership.
"""
from dataclasses import dataclass

from edit_plan import Edit, plan
from suppression import resolve as resolve_suppressions
from syntax_client import AdapterError, parse
from typst_lint import Diagnostic, SafetyError, digest, load_rules

STANDALONE = 'pde.standalone-display'
CONTINUATION_GAP = 'pde.display-continuation-gap'
RULE_IDS = (STANDALONE, CONTINUATION_GAP)
_WHITESPACE = frozenset(('Space', 'Parbreak'))
_STRUCTURAL = frozenset(('Heading', 'ListItem', 'EnumItem', 'TermItem', 'Raw', 'Linebreak'))
_BOM = b'\xef\xbb\xbf'


@dataclass(frozen=True)
class DisplayResult:
    diagnostics: tuple[Diagnostic, ...] = ()
    edits: tuple[Edit, ...] = ()
    skips: tuple[str, ...] = ()
    parse_errors: tuple[tuple[int, int], ...] = ()
    suppressed: tuple[Diagnostic, ...] = ()


def _diagnostic(source, path, rule, start, explanation):
    line = source[:start].count(b'\n') + 1
    column = len(source[source.rfind(b'\n', 0, start) + 1:start].decode('utf-8')) + 1
    return Diagnostic(rule['id'], rule['grade'], path, line, column, explanation, rule['source'])


def _line_prefix(source, position):
    start = source.rfind(b'\n', 0, position) + 1
    prefix = source[start:position]
    return prefix[len(_BOM):] if start == 0 and prefix.startswith(_BOM) else prefix


def _line_suffix(source, position):
    end = source.find(b'\n', position)
    return source[position:] if end < 0 else source[position:end]


def _horizontal(raw):
    return not raw.strip(b' \t')


def _newline(source, position):
    """Use this physical line's terminator, then the preceding one, then LF."""
    end = source.find(b'\n', position)
    if end < 0:
        end = source.rfind(b'\n', 0, position)
    return b'\r\n' if end > 0 and source[end - 1:end] == b'\r' else b'\n'


def analyze(tree, path, resolution):
    """Return rule-ID results compatible with the shared source-analysis plan."""
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        errors = tuple((n.start, n.end) for n in nodes if n.kind == 'Error')
        result = DisplayResult(skips=('Malformed Typst syntax; no repair proposed',),
                               parse_errors=errors or ((0, len(source)),))
        return {rid: result for rid in RULE_IDS}
    if resolution.source_sha256 != digest(source) or len(resolution.facts) != len(nodes):
        result = DisplayResult(skips=('Stale or mismatched static resolution; no repair proposed',))
        return {rid: result for rid in RULE_IDS}
    catalog = {r['id']: r for r in load_rules()[0]['rules']}
    scopes = resolve_suppressions(tree, set(catalog))
    parents = {child: index for index, node in enumerate(nodes) for child in node.children}
    root_positions = {index: position for position, index in enumerate(nodes[0].children)}
    findings = {rid: [] for rid in RULE_IDS}
    edits = {rid: [] for rid in RULE_IDS}
    suppressed = {rid: [] for rid in RULE_IDS}
    skips = {rid: list(resolution.skips) for rid in RULE_IDS}
    prose_guards = []

    def skip(rid, start, reason):
        skips[rid].append(f'{reason} at byte {start}')

    def safe(index):
        facts = resolution.facts[index]
        return facts.prose_safe and not facts.deferred

    def literal_prose(index):
        node = nodes[index]
        if node.kind != 'Text' or not safe(index):
            return False
        raw = source[node.start:node.end]
        if node.start == 0 and raw.startswith(_BOM):
            raw = raw[len(_BOM):]
        text = raw.decode('utf-8')
        return bool(text) and not text[0].isspace() and not text[-1].isspace()

    def neighbor(siblings, position, direction):
        position += direction
        while 0 <= position < len(siblings) and nodes[siblings[position]].kind in _WHITESPACE:
            position += direction
        return position if 0 <= position < len(siblings) else None

    def add(rid, equation, explanation, proposals):
        finding = _diagnostic(source, path, catalog[rid], equation.start, explanation)
        if any(scope.covers(rid, equation.start) for scope in scopes):
            suppressed[rid].append(finding)
        else:
            findings[rid].append(finding)
            edits[rid].extend(proposals)

    def unknown_neighbor(index):
        node = nodes[index]
        if node.kind in _STRUCTURAL or (node.kind == 'Equation' and node.display):
            return False
        return not literal_prose(index)

    for index, equation in enumerate(nodes):
        if equation.kind != 'Equation' or not equation.display:
            continue
        parent_id = parents.get(index)
        if parent_id != 0 or tree.mode != 'markup' or nodes[0].kind != 'Markup' or not safe(index):
            for rid in RULE_IDS:
                skip(rid, equation.start, 'Unsupported display body, deferred context or unproven ordinary prose')
            continue
        siblings = nodes[0].children
        position = root_positions[index]
        previous = neighbor(siblings, position, -1)
        following = neighbor(siblings, position, 1)
        end = equation.end

        # A single direct label on the closing delimiter's source line is the
        # bounded proven attachment. Never move a label onto a prose line, or
        # guess through a comment, newline, multiple labels or another wrapper.
        if following is not None and nodes[siblings[following]].kind == 'Label':
            label_id = siblings[following]
            label = nodes[label_id]
            if not _horizontal(source[end:label.start]) or not safe(label_id):
                for rid in RULE_IDS:
                    skip(rid, equation.start, 'Unsupported semantic-label attachment across a newline or unknown context')
                continue
            end = label.end
            following = neighbor(siblings, following, 1)
            if following is not None and nodes[siblings[following]].kind == 'Label':
                for rid in RULE_IDS:
                    skip(rid, equation.start, 'Ambiguous multiple semantic labels; no repair proposed')
                continue

        proposals = []
        if previous is not None:
            before_id = siblings[previous]
            before = nodes[before_id]
            separator = source[before.end:equation.start]
            if _horizontal(separator):
                if literal_prose(before_id):
                    proposals.append(Edit(before.end, equation.start, _newline(source, equation.start)))
                    prose_guards.append(before_id)
                elif _line_prefix(source, equation.start).strip(b' \t') and unknown_neighbor(before_id):
                    skip(STANDALONE, equation.start, 'Unsupported preceding inline construct/comment; no boundary guessed')
            elif b'\n' not in separator:
                skip(STANDALONE, equation.start, 'Unsupported non-ASCII or bare-CR delimiter separator')
        if following is not None:
            after_id = siblings[following]
            after = nodes[after_id]
            separator = source[end:after.start]
            if _horizontal(separator):
                if literal_prose(after_id):
                    proposals.append(Edit(end, after.start, _newline(source, end)))
                    prose_guards.append(after_id)
                elif unknown_neighbor(after_id):
                    skip(STANDALONE, equation.start, 'Unsupported following inline construct/comment; no boundary guessed')
            elif b'\n' not in separator:
                skip(STANDALONE, equation.start, 'Unsupported non-ASCII or bare-CR delimiter separator')
        if proposals:
            add(STANDALONE, equation,
                'Place display delimiters on source lines separate from adjacent prose.', proposals)

        if following is None:
            continue
        after_id = siblings[following]
        after = nodes[after_id]
        gap = source[end:after.start]
        # Only whitespace sibling nodes were skipped. A literal blank line is
        # necessary; physical wrapping inside the equation is never the gap.
        if gap.count(b'\n') < 2:
            if after.kind in ('LineComment', 'BlockComment'):
                skip(CONTINUATION_GAP, equation.start, 'Comment interrupts display-to-prose ownership; no gap crossed')
            elif b'\n' in gap and after.kind == 'Text' and not literal_prose(after_id):
                skip(CONTINUATION_GAP, equation.start, 'Unsupported whitespace or unproven prose after display')
            continue
        if gap.strip(b' \t\r\n') or b'\r' in gap.replace(b'\r\n', b''):
            skip(CONTINUATION_GAP, equation.start, 'Unsupported non-ASCII or bare-CR display gap')
            continue
        if not literal_prose(after_id):
            # Markers, explicit breaks and structural siblings are boundaries,
            # never invitations to search forward to some later prose token.
            known_marker = False
            if after.kind == 'Hash' and following + 1 < len(siblings):
                marker = nodes[siblings[following + 1]]
                binding = resolution.facts[siblings[following + 1]].binding
                known_marker = marker.start == after.end and binding is not None and binding.kind == 'api' and binding.name == 'paragraph_tab'
            if not known_marker and unknown_neighbor(after_id):
                skip(CONTINUATION_GAP, equation.start, 'Following construct is not proven direct unmarked prose')
            continue
        if (_line_prefix(source, equation.start).strip(b' \t')
                or _line_suffix(source, end).strip(b' \t\r')):
            skip(CONTINUATION_GAP, equation.start, 'Display must already be standalone before a continuation-gap repair')
            continue
        first_lf = gap.index(b'\n')
        newline = b'\r\n' if first_lf and gap[first_lf - 1:first_lf] == b'\r' else b'\n'
        add(CONTINUATION_GAP, equation,
            'Use one source newline between a standalone display and its unmarked prose continuation.',
            (Edit(end, after.start, newline),))

    if edits[STANDALONE]:
        # A newly inserted line can turn inline "= title", "- item" or "1."
        # into a structural construct. Reparse once and require each preserved
        # literal prose span to remain a direct root Text node (possibly merged
        # into a larger Text). Byte preservation alone does not prove this.
        try:
            planned = plan(source, edits[STANDALONE])
            if planned.deferred:
                raise SafetyError('Conflicting display boundary edits')
            candidate = parse(planned.candidate(), tree.mode)
            if candidate.nodes[0].errors:
                raise SafetyError('Display boundary candidate failed to parse')

            def mapped(position, *, end=False):
                return position + sum(len(e.replacement) - (e.end - e.start)
                                      for e in planned.edits if e.end < position
                                      or (e.end == position and (not end or e.start < e.end)))

            text_nodes = [candidate.nodes[i] for i in candidate.nodes[0].children
                          if candidate.nodes[i].kind == 'Text']
            for guarded in prose_guards:
                node = nodes[guarded]
                start, end = mapped(node.start), mapped(node.end, end=True)
                if not any(n.start <= start < end <= n.end for n in text_nodes):
                    raise SafetyError('Display newline would change literal prose into structural syntax')
        except (AdapterError, SafetyError) as error:
            edits[STANDALONE].clear()
            skips[STANDALONE].append(f'fix_unavailable: {error}; no standalone-display edits proposed')

    return {rid: DisplayResult(tuple(findings[rid]), tuple(edits[rid]),
                              tuple(dict.fromkeys(skips[rid])), suppressed=tuple(suppressed[rid]))
            for rid in RULE_IDS}
