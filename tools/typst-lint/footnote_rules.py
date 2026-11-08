"""Partial footnote checks over verified syntax and explicitly supplied bindings.

SpacerProof must be created by a resolver which proves the actual helper and
insertion scope; mere identifier spelling or a package path is not such proof.
The dispatcher uses Resolution.spacer_proof, produced from reviewed package
bytes and lexical AST facts. It supplies no synthetic proof. See FOOTNOTE_CONTRACT.md.
"""
from dataclasses import dataclass
from bisect import bisect_left
from collections.abc import Mapping
import re

from edit_plan import Edit
from static_resolver import SpacerProof
from prose_boundary import footnote_boundaries, valid_insertion_proof
from suppression import resolve as resolve_suppressions
from typst_lint import Diagnostic, InputError, digest, load_rules

GAP = 'pde.footnote-trailing-gap'
PUNCT = 'pde.prose-footnote-punctuation'
ALIGNED = 'pde.aligned-footnote-spacer'
UNALIGNED = 'pde.unaligned-footnote-no-spacer'
PROSE = 'pde.prose-footnote-no-spacer'
RULE_IDS = (GAP, PUNCT, ALIGNED, UNALIGNED, PROSE)
_NESTED_EXCLUDED = frozenset(('Equation', 'Math', 'Code', 'CodeBlock', 'Heading',
                              'ListItem', 'EnumItem', 'TermItem', 'Raw', 'Str'))
_BLANK_AFTER_FOOTNOTE = re.compile(rb'(\r?\n)([ \t]*\r?\n)+')


@dataclass(frozen=True)
class FootnoteResult:
    diagnostics: tuple[Diagnostic, ...] = ()
    edits: tuple[Edit, ...] = ()
    skips: tuple[str, ...] = ()
    parse_errors: tuple[tuple[int, int], ...] = ()
    suppressed: tuple[Diagnostic, ...] = ()


def _diagnostic(source, path, rule, start, message):
    line = source[:start].count(b'\n') + 1
    column = len(source[source.rfind(b'\n', 0, start) + 1:start].decode('utf-8')) + 1
    return Diagnostic(rule['id'], rule['grade'], path, line, column, message, rule['source'])


def _subtree_size(nodes, root):
    # Validated parser nodes are preorder. Count exactly this subtree.
    pending, count = [root], 0
    while pending:
        i = pending.pop()
        count += 1
        pending.extend(nodes[i].children)
    return count


def analyze(tree, path, resolution, spacer_proof=None):
    """Inspect only direct root prose and equation-level calls in root displays.

    All offsets are byte offsets. Caller must reparse/reanalyze composed edits,
    check conflicts and suppression ownership, and enforce protected writes.
    """
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        errors = tuple((n.start, n.end) for n in nodes if n.kind == 'Error') or ((0, len(source)),)
        return {r: FootnoteResult(skips=('Malformed Typst syntax; no repair proposed',), parse_errors=errors) for r in RULE_IDS}
    if resolution.source_sha256 != digest(source) or len(resolution.facts) != len(nodes):
        return {r: FootnoteResult(skips=('Stale or mismatched static resolution',)) for r in RULE_IDS}
    if spacer_proof is None:
        spacer_proof = SpacerProof(digest(source))
    if (type(spacer_proof) is not SpacerProof or spacer_proof.source_sha256 != digest(source)
            or type(spacer_proof.existing) is not frozenset or type(spacer_proof.available) is not frozenset
            or any(type(i) is not int or i < 0 or i >= len(nodes)
                   for i in spacer_proof.existing | spacer_proof.available)):
        return {r: FootnoteResult(skips=('Stale or invalid spacer proof',)) for r in RULE_IDS}
    catalog = {r['id']: r for r in load_rules()[0]['rules']}
    scopes = resolve_suppressions(tree, set(catalog))
    findings, edits, suppressed = ({r: [] for r in RULE_IDS} for _ in range(3))
    skips = {r: [*resolution.skips, 'Only bounded root footnote forms are supported'] for r in RULE_IDS}
    paragraph_proof = resolution.paragraph_insertion_proof
    valid_paragraph_proof = valid_insertion_proof(tree, paragraph_proof)
    boundaries = {boundary.call_id: boundary for boundary in footnote_boundaries(tree, resolution)}

    def add(rid, start, message, edit=None):
        finding = _diagnostic(source, path, catalog[rid], start, message)
        if any(s.covers(rid, start) for s in scopes):
            suppressed[rid].append(finding)
        else:
            findings[rid].append(finding)
            if edit is not None:
                edits[rid].append(edit)

    def skip(rid, start, message):
        skips[rid].append(f'{message} at byte {start}')

    def alignment(i):
        stack = list(nodes[i].children)
        while stack:
            n = nodes[stack.pop()]
            # Code/call contents and nested markup are not equation-level math.
            if n.kind in ('FuncCall', 'ContentBlock', 'CodeBlock', 'Str', 'LineComment', 'BlockComment', 'Equation'):
                continue
            if n.kind in ('MathAlignPoint', 'Linebreak'):
                return True
            stack.extend(n.children)
        return False

    def inspect(container_id, math=False, aligned=False):
        container = nodes[container_id]
        siblings = container.children
        for pos, call_id in enumerate(siblings):
            call = nodes[call_id]
            if call.kind != 'FuncCall' or pos == 0 or nodes[siblings[pos - 1]].kind != 'Hash':
                continue
            start = nodes[siblings[pos - 1]].start
            if nodes[siblings[pos - 1]].end != call.start:
                continue
            binding = resolution.facts[call_id].binding
            if not binding or binding.kind != 'builtin' or binding.name != 'footnote':
                continue
            # Restrict to exactly #footnote[content], avoiding dynamic arguments,
            # aliases and ambiguous call forms until their interactions are proven.
            if (not source[start:call.end].startswith(b'#footnote[') or source[call.end-1:call.end] != b']'
                    or len(call.children) != 2 or nodes[call.children[0]].kind != 'Ident'
                    or nodes[call.children[1]].kind != 'Args'):
                continue
            args = nodes[call.children[1]]
            if len(args.children) != 1 or nodes[args.children[0]].kind != 'ContentBlock':
                continue
            before_pos = pos - 2
            if before_pos >= 0 and nodes[siblings[before_pos]].kind == 'Space':
                before_pos -= 1
            prev_id = siblings[before_pos] if before_pos >= 0 else None
            prev = nodes[prev_id] if prev_id is not None else None
            spacer = (prev is not None and prev_id in spacer_proof.existing and before_pos >= 1
                      and nodes[siblings[before_pos - 1]].kind == 'Hash'
                      and nodes[siblings[before_pos - 1]].end == prev.start
                      and source[prev.end:start].strip(b' \t\r\n') == b'')
            spacer_start = nodes[siblings[before_pos - 1]].start if spacer else None
            # A nearby unresolved expression could be an alias or facade
            # access. Its spelling cannot prove presence or absence of spacer.
            unknown_spacer = (prev is not None and prev_id not in spacer_proof.existing
                              and prev.kind in ('Ident', 'FieldAccess', 'Parenthesized', 'FuncCall')
                              and before_pos >= 1 and nodes[siblings[before_pos - 1]].kind == 'Hash'
                              and nodes[siblings[before_pos - 1]].end == prev.start
                              and not (prev.kind == 'FuncCall' and resolution.facts[prev_id].binding
                                       and resolution.facts[prev_id].binding.kind == 'builtin'
                                       and resolution.facts[prev_id].binding.name == 'footnote'))
            if not math:
                anchor = spacer_start if spacer else start
                if not resolution.facts[call_id].prose_safe:
                    # A proven preceding helper may itself have made the legacy
                    # resolver conservative; context must be safe before it.
                    if not spacer or not resolution.facts[siblings[before_pos - 1]].prose_safe:
                        continue
                if unknown_spacer:
                    skip(PROSE, start, 'Unresolved possible spacer')
                    skip(PUNCT, start, 'Unresolved preceding expression')
                else:
                    adjacent = source[anchor-1:anchor] in (b'.', b',') if anchor else False
                    if spacer:
                        # Do not leave whitespace between punctuation and the
                        # footnote. Only combine horizontal-space removal when
                        # the immediately preceding source byte is punctuation.
                        spacer_edit = Edit(spacer_start, start, b'') if adjacent else None
                        left = spacer_start
                        while left > container.start and source[left-1:left] in (b' ', b'\t'):
                            left -= 1
                        if (not adjacent and left > container.start and left < spacer_start
                                and source[left-1:left] in (b'.', b',')):
                            spacer_edit = Edit(left, start, b'')
                        add(PROSE, spacer_start, 'Remove the spacer before a prose footnote.', spacer_edit)
                        if spacer_edit is None:
                            skip(PROSE, start, 'fix_unavailable: spacer and punctuation require combined repair')
                    if not adjacent:
                        edit = None
                        if not spacer:
                            left = anchor
                            while left > container.start and source[left-1:left] in (b' ', b'\t'):
                                left -= 1
                            if left > container.start and left < anchor and source[left-1:left] in (b'.', b','):
                                edit = Edit(left, anchor, b'')
                            elif (left < anchor and prev is not None and prev.kind == 'Text'
                                  and all(n.kind in ('FuncCall', 'Ident', 'Args', 'ContentBlock',
                                                     'LeftBracket', 'Markup', 'Text', 'Space', 'RightBracket')
                                          for n in nodes[call_id:call_id + _subtree_size(nodes, call_id)])
                                  and call.end < container.end
                                  and source[call.end:call.end+1] in (b'.', b',')
                                  and (call.end+1 == container.end or source[call.end+1:call.end+2] not in b'.,;:!?')
                                  and left > container.start and source[left-1:left] not in b'.,;:!? \t\r\n'):
                                # One atomic edit moves the existing punctuation.
                                edit = Edit(left, call.end+1, source[call.end:call.end+1] + source[anchor:call.end])
                        add(PUNCT, start, 'Place the footnote immediately after a comma or period.', edit)
                        if edit is None:
                            skip(PUNCT, start, 'fix_unavailable: punctuation or context is ambiguous')
            else:
                if unknown_spacer:
                    skip(ALIGNED if aligned else UNALIGNED, start, 'Unresolved possible spacer')
                elif aligned and not spacer:
                    edit = Edit(start, start, b'#dots_space ') if call_id in spacer_proof.available else None
                    add(ALIGNED, start, 'Add the proven spacer before this aligned equation footnote.', edit)
                    if edit is None:
                        skip(ALIGNED, start, 'fix_unavailable: spacer binding in scope is unproven')
                elif not aligned and spacer:
                    # Retain the whitespace already before the spacer, if any.
                    add(UNALIGNED, spacer_start, 'Remove the spacer from this unaligned equation footnote.',
                        Edit(spacer_start, start, b''))

            # A source-line-ending footnote followed by at least one empty line.
            # Whitespace/comments after the closing bracket are not moved.
            rest = source[call.end:container.end]
            gap = re.match(rb'(\r?\n)([ \t]*\r?\n)+', rest)
            if gap and rest[gap.end():].strip(b' \t\r\n'):
                gap_edit = None
                if math and not rest[gap.end():].startswith((b'//', b'/*')):
                    gap_edit = Edit(call.end, call.end + gap.end(), gap.group(1))
                elif math:
                    skip(GAP, call.end, 'fix_unavailable: comment attachment is ambiguous')
                else:
                    boundary = boundaries.get(call_id)
                    if (boundary is not None and boundary.marked and not boundary.has_break
                            and valid_paragraph_proof
                            and boundary.target_id in paragraph_proof.available
                            and boundary.target_id in paragraph_proof.parbreak_available):
                        gap_edit = Edit(boundary.break_at, boundary.break_at,
                                        b'#parbreak()' + boundary.newline)
                    else:
                        # A missing marker owns its entire break-plus-marker
                        # policy repair; GAP must never leave a half repair.
                        skip(GAP, call.end, 'fix_unavailable: paragraph marker composition is unproven')
                add(GAP, start, 'Avoid a blank source line immediately after a footnote.', gap_edit)

    root = nodes[0]
    if tree.mode == 'markup' and root.kind == 'Markup':
        inspect(0)
        for i in root.children:
            equation = nodes[i]
            if equation.kind != 'Equation' or not resolution.facts[i].prose_safe:
                continue
            if not equation.display:
                continue
            for j in equation.children:
                if nodes[j].kind == 'Math':
                    inspect(j, True, alignment(j))
    return {r: FootnoteResult(tuple(findings[r]), tuple(edits[r]), tuple(dict.fromkeys(skips[r])),
                              suppressed=tuple(suppressed[r])) for r in RULE_IDS}


def analyze_conditional_nested(tree, path, resolution, *, catalog=None, _math_gap_edits=None):
    """Inspect nested literal markup patterns without asserting execution or edits.

    Root markup is owned by ``analyze``. These additional hints are conditional
    on the named functions having their intended bindings and the surrounding
    markup executing and rendering in the expected context. No result carries
    a proposed edit or proves absence of other violations.
    """
    source, nodes = tree.source, tree.nodes
    reason = ('Nested source pattern only; include-site bindings, execution, '
              'styles and rendered context are unproved.')
    if nodes[0].errors:
        errors = tuple((n.start, n.end) for n in nodes if n.kind == 'Error') or ((0, len(source)),)
        return {rid: FootnoteResult(skips=('Malformed Typst syntax; no source pattern diagnosis',),
                                   parse_errors=errors) for rid in RULE_IDS}
    if resolution.source_sha256 != digest(source) or len(resolution.facts) != len(nodes):
        return {rid: FootnoteResult(skips=('Stale or mismatched static resolution',)) for rid in RULE_IDS}
    if tree.mode != 'markup' or nodes[0].kind != 'Markup':
        return {rid: FootnoteResult(skips=('Only markup source patterns are supported',)) for rid in RULE_IDS}
    records = load_rules()[0]['rules'] if catalog is None else catalog
    if (type(records) not in (tuple, list) or any(not isinstance(item, Mapping)
            or type(item.get('id')) is not str for item in records)):
        raise InputError('Conditional footnote analysis requires rule metadata records')
    metadata = {item['id']: item for item in records}
    if (len(metadata) != len(records) or any(rid not in metadata or
            any(type(metadata[rid].get(key)) is not str or not metadata[rid][key]
                for key in ('grade', 'source')) for rid in RULE_IDS)):
        raise InputError('Missing or invalid footnote rule metadata')
    scopes = resolve_suppressions(tree, set(metadata))
    parents = {child: index for index, node in enumerate(nodes) for child in node.children}
    excluded = {}
    for index, node in enumerate(nodes):
        parent = parents.get(index)
        excluded[index] = (parent is not None and
                           (excluded[parent] or nodes[parent].kind in _NESTED_EXCLUDED))
    findings, suppressed = ({rid: [] for rid in RULE_IDS} for _ in range(2))
    newlines = [index for index, byte in enumerate(source) if byte == 10]

    def add(rid, start, explanation):
        line_index = bisect_left(newlines, start)
        line_start = 0 if not line_index else newlines[line_index - 1] + 1
        column = len(source[line_start:start].decode('utf-8')) + 1
        rule = metadata[rid]
        finding = Diagnostic(rid, rule['grade'], path, line_index + 1, column, explanation, rule['source'])
        is_suppressed = any(scope.covers(rid, start) for scope in scopes)
        (suppressed if is_suppressed else findings)[rid].append(finding)
        return not is_suppressed

    def aligned(math_id):
        pending = list(nodes[math_id].children)
        while pending:
            item = nodes[pending.pop()]
            if item.kind in ('FuncCall', 'ContentBlock', 'CodeBlock', 'Str',
                             'LineComment', 'BlockComment', 'Equation'):
                continue
            if item.kind in ('MathAlignPoint', 'Linebreak'):
                return True
            pending.extend(item.children)
        return False

    def inspect(container_id, *, math=False, has_alignment=False):
        container = nodes[container_id]
        siblings = container.children
        for position, call_id in enumerate(siblings):
            call = nodes[call_id]
            if (call.kind != 'FuncCall' or position == 0
                    or nodes[siblings[position - 1]].kind != 'Hash'):
                continue
            mark = nodes[siblings[position - 1]]
            if (mark.end != call.start or not source[mark.start:call.end].startswith(b'#footnote[')
                    or source[call.end - 1:call.end] != b']' or len(call.children) != 2
                    or nodes[call.children[0]].kind != 'Ident'
                    or nodes[call.children[1]].kind != 'Args'):
                continue
            args = nodes[call.children[1]]
            if len(args.children) != 1 or nodes[args.children[0]].kind != 'ContentBlock':
                continue
            binding = resolution.facts[call_id].binding
            if binding is not None and (binding.kind != 'builtin' or binding.name != 'footnote'):
                continue
            previous = position - 2
            if previous >= 0 and nodes[siblings[previous]].kind == 'Space':
                previous -= 1
            before = nodes[siblings[previous]] if previous >= 0 else None
            has_hash = (previous >= 1 and nodes[siblings[previous - 1]].kind == 'Hash'
                        and nodes[siblings[previous - 1]].end == before.start)
            adjacent_expression = (has_hash and
                not source[before.end:mark.start].strip(b' \t\r\n'))
            spacer = (adjacent_expression and before.kind == 'Ident'
                      and source[before.start:before.end] == b'dots_space')
            unknown_expression = (adjacent_expression and not spacer and before.kind in
                                  ('Ident', 'FieldAccess', 'Parenthesized', 'FuncCall'))
            if not math:
                if spacer:
                    add(PROSE, nodes[siblings[previous - 1]].start,
                        'Remove this spacer if it and the footnote have their expected bindings and render as prose.')
                if not unknown_expression and (mark.start == 0 or source[mark.start - 1:mark.start] not in (b'.', b',')):
                    add(PUNCT, mark.start,
                        'Place this footnote immediately after a comma or period if it renders as prose.')
            elif not unknown_expression:
                if has_alignment and not spacer:
                    add(ALIGNED, mark.start,
                        'Add the proven spacer if this aligned display and footnote render as written.')
                elif not has_alignment and spacer:
                    add(UNALIGNED, nodes[siblings[previous - 1]].start,
                        'Remove this spacer if this unaligned display and footnote render as written.')
            gap = _BLANK_AFTER_FOOTNOTE.match(source, call.end, container.end)
            if gap:
                following = gap.end()
                while following < container.end and source[following] in b' \t\r\n':
                    following += 1
                if following < container.end:
                    active = add(GAP, mark.start,
                        'Avoid this blank source line if the footnote and following content render as written.')
                    # Internal preview collector: source-only, never edits prose
                    # paragraphs or changes a comment's attachment.
                    if (active and math and _math_gap_edits is not None
                            and not source[following:following + 2] in (b'//', b'/*')):
                        _math_gap_edits.append(Edit(call.end, gap.end(), gap.group(1)))

    for body_id, body in enumerate(nodes):
        if body_id == 0 or body.kind != 'Markup' or excluded[body_id]:
            continue
        inspect(body_id)
        for child_id in body.children:
            equation = nodes[child_id]
            if equation.kind == 'Equation' and equation.display:
                for math_id in equation.children:
                    if nodes[math_id].kind == 'Math':
                        inspect(math_id, math=True, has_alignment=aligned(math_id))
    return {rid: FootnoteResult(tuple(findings[rid]), (), (reason,),
                                suppressed=tuple(suppressed[rid])) for rid in RULE_IDS}


def preview_conditional_math_gaps(tree, path, resolution, *, catalog=None):
    """Construct a reanalyzed in-memory preview for nested math gaps only.

    Conditional binding, execution and rendering remain unproved. The returned
    edits have no source-write authority and must not be treated as safe fixes.
    """
    from document_gap_preview import _scopes, _syntax_signature
    from edit_plan import plan
    from static_resolver import resolve
    from syntax_client import parse
    from typst_lint import SafetyError

    records = load_rules()[0]['rules'] if catalog is None else catalog
    edits = []
    baseline = analyze_conditional_nested(tree, path, resolution, catalog=records, _math_gap_edits=edits)
    if not edits:
        return None
    try:
        candidate_plan = plan(tree.source, edits)
        if candidate_plan.deferred or len(candidate_plan.edits) != len(edits):
            raise SafetyError('Overlapping or ineffective conditional footnote gaps')
        candidate = candidate_plan.candidate()
        parsed = parse(candidate, tree.mode)
        if parsed.nodes[0].errors or _syntax_signature(tree) != _syntax_signature(parsed):
            raise SafetyError('Candidate changes non-whitespace syntax or fails to parse')
        if _scopes(tree, records, candidate_plan.edits) != _scopes(parsed, records):
            raise SafetyError('Candidate changes an inline directive target')
        final = analyze_conditional_nested(parsed, path, resolve(parsed), catalog=records)
        for rule_id in RULE_IDS:
            before, after = baseline[rule_id], final[rule_id]
            expected = len(before.diagnostics) - (len(edits) if rule_id == GAP else 0)
            if (len(after.diagnostics) != expected or len(after.suppressed) != len(before.suppressed)
                    or sorted(d.explanation for d in after.suppressed) !=
                       sorted(d.explanation for d in before.suppressed)
                    or (rule_id != GAP and sorted(d.explanation for d in after.diagnostics) !=
                        sorted(d.explanation for d in before.diagnostics))):
                raise SafetyError('Conditional footnote findings changed or gap reanalysis failed')
        return dict(path=path, baseline_sha256=digest(tree.source), candidate_sha256=digest(candidate),
                    verification='conditional-parse-syntax-directives-and-footnote-reanalysis',
                    runtime_binding_proved=False, render_equivalence_proved=False,
                    edit_permission=False, edits=[dict(start=e.start, end=e.end,
                      replacement=e.replacement.decode('utf-8'), rule_id=GAP) for e in candidate_plan.edits])
    except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
        return dict(path=path, baseline_sha256=digest(tree.source), candidate_sha256=None,
                    verification='withheld', runtime_binding_proved=False,
                    render_equivalence_proved=False, edit_permission=False, edits=[], reason=str(error))
