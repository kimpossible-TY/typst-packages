"""Partial proof source separation over reviewed bindings and real root calls.

Only insertion proposals are returned. No package evaluation, source writes or
compiler invocation happens here. See PROOF_CONTRACT.md for the exact boundary.
"""
from dataclasses import dataclass
from bisect import bisect_left, bisect_right
from collections.abc import Mapping

from edit_plan import Edit, plan
from static_resolver import is_proof_binding, resolve
from suppression import resolve as resolve_suppressions
from syntax_client import AdapterError, parse
from typst_lint import Diagnostic, InputError, SafetyError, digest, load_rules

RULE_ID = 'pde.proof-trailing-gap'
_WHITESPACE = frozenset(('Space', 'Parbreak'))
_STRUCTURAL = frozenset(('Heading', 'ListItem', 'EnumItem', 'TermItem', 'Raw'))
_BLOCK_CALLS = frozenset(('block', 'figure', 'heading', 'list', 'enum', 'terms'))
_BOM = b'\xef\xbb\xbf'
_CONDITIONAL_EXCLUDED = frozenset(('Equation', 'Math', 'Code', 'CodeBlock',
                                   'Heading', 'ListItem', 'EnumItem', 'TermItem', 'Raw', 'Str'))
_CONDITIONAL_BLOCK_NAMES = frozenset((b'proof', b'note', b'theorem', b'lemma', b'definition',
                                     b'proposition', b'block', b'figure', b'heading', b'list', b'enum', b'terms'))


@dataclass(frozen=True)
class ProofResult:
    diagnostics: tuple[Diagnostic, ...] = ()
    edits: tuple[Edit, ...] = ()
    skips: tuple[str, ...] = ()
    parse_errors: tuple[tuple[int, int], ...] = ()
    suppressed: tuple[Diagnostic, ...] = ()


def _content_call(nodes, index):
    """One literal positional content argument; no options or outside trivia."""
    call = nodes[index]
    if call.kind != 'FuncCall' or len(call.children) != 2:
        return False
    args = nodes[call.children[1]]
    if args.kind != 'Args':
        return False
    kinds = [nodes[i].kind for i in args.children]
    return kinds in (['ContentBlock'], ['LeftParen', 'ContentBlock', 'RightParen'])


def _signature(tree):
    # Only root whitespace may change. Retain every nested token, byte and typed
    # property. This detects e.g. literal "= Heading" becoming heading syntax
    # after inserting a newline, and labels/call continuations changing owners.
    root_trivia = {i for i in tree.nodes[0].children if tree.nodes[i].kind in _WHITESPACE}
    return tuple((item.kind, item.display, len(item.children),
                  b'' if item.children else tree.source[item.start:item.end])
                 for i, item in enumerate(tree.nodes) if i and i not in root_trivia)


def analyze(tree, path, resolution):
    """Inspect immediate root proof output; return a source-analysis result."""
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        errors = tuple((n.start, n.end) for n in nodes if n.kind == 'Error') or ((0, len(source)),)
        return ProofResult(skips=('Malformed Typst syntax; no proof repair proposed',), parse_errors=errors)
    if resolution.source_sha256 != digest(source) or len(resolution.facts) != len(nodes):
        return ProofResult(skips=('Stale or mismatched static resolution; no repair proposed',))
    rules = {r['id']: r for r in load_rules()[0]['rules']}
    scopes = resolve_suppressions(tree, set(rules))
    findings, edits, suppressed = [], [], []
    skips = [*resolution.skips,
             'Only exact reviewed root proof calls with unambiguous next siblings are supported']
    parents = {child: i for i, node in enumerate(nodes) for child in node.children}
    siblings = nodes[0].children
    positions = {index: position for position, index in enumerate(siblings)}

    def skip(start, reason):
        skips.append(f'{reason} at byte {start}')

    def next_sibling(position):
        position += 1
        while position < len(siblings) and nodes[siblings[position]].kind in _WHITESPACE:
            position += 1
        return position if position < len(siblings) else None

    def structural_or_prose(position):
        index = siblings[position]
        node = nodes[index]
        if node.kind == 'Text':
            value = source[node.start:node.end].decode('utf-8')
            return bool(value) and not value[0].isspace() and not value[-1].isspace()
        if node.kind in _STRUCTURAL or (node.kind == 'Equation' and node.display):
            return True
        if node.kind == 'Hash' and position + 1 < len(siblings):
            call_id = siblings[position + 1]
            binding = resolution.facts[call_id].binding
            return (node.end == nodes[call_id].start and _content_call(nodes, call_id)
                    and (is_proof_binding(binding) or bool(binding and binding.kind == 'builtin'
                                                         and binding.name in _BLOCK_CALLS)))
        return False

    for index, call in enumerate(nodes):
        fact = resolution.facts[index]
        if call.kind != 'FuncCall' or not is_proof_binding(fact.binding):
            continue
        if (tree.mode != 'markup' or nodes[0].kind != 'Markup' or parents.get(index) != 0
                or fact.deferred or not fact.prose_safe):
            skip(call.start, 'Unsupported proof body, deferred context or unknown output/style ownership')
            continue
        position = positions[index]
        if (position == 0 or nodes[siblings[position - 1]].kind != 'Hash'
                or nodes[siblings[position - 1]].end != call.start or not _content_call(nodes, index)):
            skip(call.start, 'Unsupported proof call signature; one literal content argument is required')
            continue
        start = nodes[siblings[position - 1]].start
        line_start = source.rfind(b'\n', 0, start) + 1
        prefix = source[line_start:start]
        if line_start == 0 and prefix.startswith(_BOM):
            prefix = prefix[len(_BOM):]
        if prefix.strip(b' \t'):
            skip(start, 'Proof is not a standalone root output call')
            continue
        end = call.end
        following = next_sibling(position)
        if following is not None and nodes[siblings[following]].kind == 'Label':
            label = nodes[siblings[following]]
            if source[end:label.start].strip(b' \t'):
                skip(start, 'Ambiguous semantic label across a newline; no repair proposed')
                continue
            end = label.end
            following = next_sibling(following)
            if following is not None and nodes[siblings[following]].kind == 'Label':
                skip(start, 'Ambiguous multiple semantic labels; no repair proposed')
                continue
        if following is None:
            # EOF is never an invitation to manufacture another paragraph.
            continue
        after = nodes[siblings[following]]
        gap = source[end:after.start]
        if gap.strip(b' \t\r\n') or b'\r' in gap.replace(b'\r\n', b''):
            skip(start, 'Unsupported non-ASCII or bare-CR proof separator')
            continue
        if gap.count(b'\n') >= 2:
            continue
        if after.kind in ('LineComment', 'BlockComment'):
            skip(start, 'Comment or directive interrupts proof attachment; no boundary crossed')
            continue
        if not structural_or_prose(following):
            skip(start, 'Following construct is not a proven complete structural or prose sibling')
            continue
        rule = rules[RULE_ID]
        line = source[:start].count(b'\n') + 1
        column = len(source[line_start:start].decode('utf-8')) + 1
        finding = Diagnostic(RULE_ID, rule['grade'], path, line, column,
                             'Insert a blank source line after the complete proof and its attached label.',
                             rule['source'])
        if any(scope.covers(RULE_ID, start) for scope in scopes):
            suppressed.append(finding)
            continue
        findings.append(finding)
        if b'\n' in gap:
            # Preserve the complete closing line and existing line ending. The
            # new blank line precedes the next line's original indentation.
            first_lf = gap.index(b'\n')
            at = end + first_lf + 1
            newline = b'\r\n' if first_lf and gap[first_lf - 1:first_lf] == b'\r' else b'\n'
            edits.append(Edit(at, at, newline))
        else:
            # Same-line prose needs both a line ending and an empty source line.
            # Reparse below rejects any reinterpretation as structural syntax.
            lf = source.find(b'\n', end)
            if lf < 0:
                lf = source.rfind(b'\n', 0, end)
            newline = b'\r\n' if lf > 0 and source[lf - 1:lf] == b'\r' else b'\n'
            edits.append(Edit(end, end, newline * 2))
    if edits:
        try:
            proposed = plan(source, edits)
            if proposed.deferred:
                raise SafetyError('Conflicting proof boundary insertions')
            candidate = parse(proposed.candidate(), tree.mode)
            if candidate.nodes[0].errors or _signature(candidate) != _signature(tree):
                raise SafetyError('Proof separation would change non-whitespace syntax or attachment')
        except (AdapterError, SafetyError) as error:
            edits.clear()
            skips.append(f'fix_unavailable: {error}; no proof edits proposed')
    return ProofResult(tuple(findings), tuple(edits), tuple(dict.fromkeys(skips)),
                       suppressed=tuple(suppressed))


def analyze_conditional(tree, path, resolution, *, catalog=None, _preview_candidates=None):
    """Report literal proof-boundary source patterns without proposing edits.

    The include-site binding, execution, output semantics and rendered context
    remain conditions. A known different local binding is not treated as proof.
    """
    source, nodes = tree.source, tree.nodes
    reason = ('Source-local proof pattern only; include-site binding, execution, '
              'styles and rendered context are unproved.')
    if nodes[0].errors:
        errors = tuple((n.start, n.end) for n in nodes if n.kind == 'Error') or ((0, len(source)),)
        return ProofResult(skips=('Malformed Typst syntax; no proof source diagnosis',), parse_errors=errors)
    if resolution.source_sha256 != digest(source) or len(resolution.facts) != len(nodes):
        return ProofResult(skips=('Stale or mismatched static resolution',))
    if tree.mode != 'markup' or nodes[0].kind != 'Markup':
        return ProofResult(skips=('Only markup source patterns are supported',))
    records = load_rules()[0]['rules'] if catalog is None else catalog
    if (type(records) not in (tuple, list) or any(not isinstance(item, Mapping)
            or type(item.get('id')) is not str for item in records)):
        raise InputError('Conditional proof analysis requires rule metadata records')
    selected = [item for item in records if item['id'] == RULE_ID]
    if (len(selected) != 1 or any(type(selected[0].get(key)) is not str
            or not selected[0][key] for key in ('grade', 'source'))):
        raise InputError('Missing or invalid proof rule metadata')
    rule = selected[0]
    scopes = resolve_suppressions(tree, {item['id'] for item in records})
    parents = {child: index for index, node in enumerate(nodes) for child in node.children}
    obvious_shadows = []
    for node in nodes:
        if node.kind != 'LetBinding':
            continue
        parts = [nodes[i] for i in node.children if nodes[i].kind not in
                 ('Space', 'Parbreak', 'LineComment', 'BlockComment')]
        if len(parts) < 2 or parts[0].kind != 'Let':
            continue
        declared = parts[1]
        if declared.kind == 'Closure':
            children = [nodes[i] for i in declared.children if nodes[i].kind not in
                        ('Space', 'Parbreak', 'LineComment', 'BlockComment')]
            declared = children[0] if children else declared
        if declared.kind == 'Ident' and source[declared.start:declared.end] == b'proof':
            obvious_shadows.append(node.start)
    obvious_shadows.sort()
    excluded = {}
    for index, node in enumerate(nodes):
        parent = parents.get(index)
        excluded[index] = (parent is not None and
                           (excluded[parent] or nodes[parent].kind in _CONDITIONAL_EXCLUDED))
    newlines = [index for index, byte in enumerate(source) if byte == 10]
    findings, suppressed = [], []

    def conditional_block(siblings, position):
        """Recognize only direct literal named block calls as a source pattern."""
        mark = nodes[siblings[position]]
        if mark.kind != 'Hash' or position + 1 >= len(siblings):
            return False
        call = nodes[siblings[position + 1]]
        if mark.end != call.start or call.kind != 'FuncCall' or len(call.children) != 2:
            return False
        callee, args = (nodes[i] for i in call.children)
        if (callee.kind != 'Ident' or source[callee.start:callee.end] not in _CONDITIONAL_BLOCK_NAMES
                or args.kind != 'Args'):
            return False
        parts = [nodes[i] for i in args.children if nodes[i].kind != 'Space']
        if [part.kind for part in parts] == ['ContentBlock']:
            return True
        if (len(parts) not in (4, 5) or parts[0].kind != 'LeftParen'
                or parts[-2].kind != 'RightParen' or parts[-1].kind != 'ContentBlock'
                or parts[1].kind != 'Named'):
            return False
        if len(parts) == 5 and parts[2].kind != 'Comma':
            return False
        named = [nodes[i] for i in parts[1].children if nodes[i].kind != 'Space']
        return (len(named) == 3 and named[0].kind == 'Ident'
                and source[named[0].start:named[0].end] == b'title'
                and named[1].kind == 'Colon' and named[2].kind in ('Str', 'ContentBlock'))

    for body_id, body in enumerate(nodes):
        if body.kind != 'Markup' or excluded[body_id]:
            continue
        siblings = body.children
        for position, index in enumerate(siblings):
            call = nodes[index]
            if (position == 0 or call.kind != 'FuncCall' or not _content_call(nodes, index)
                    or nodes[call.children[0]].kind != 'Ident'
                    or source[nodes[call.children[0]].start:nodes[call.children[0]].end] != b'proof'):
                continue
            mark = nodes[siblings[position - 1]]
            if mark.kind != 'Hash' or mark.end != call.start:
                continue
            binding = resolution.facts[index].binding
            if binding is not None and not is_proof_binding(binding):
                continue
            # Resolver facts do not distinguish an unresolved name from an
            # arbitrary local definition. Refuse the obvious earlier direct
            # definitions rather than suggesting a condition known false.
            if binding is None and bisect_left(obvious_shadows, call.start):
                continue
            start = mark.start
            line_start = source.rfind(b'\n', 0, start) + 1
            prefix = source[line_start:start]
            if line_start == 0 and prefix.startswith(_BOM):
                prefix = prefix[len(_BOM):]
            if prefix.strip(b' \t'):
                continue
            following = position + 1
            while following < len(siblings) and nodes[siblings[following]].kind in _WHITESPACE:
                following += 1
            end = call.end
            if following < len(siblings) and nodes[siblings[following]].kind == 'Label':
                label = nodes[siblings[following]]
                if source[end:label.start].strip(b' \t'):
                    continue
                end = label.end
                following += 1
                while following < len(siblings) and nodes[siblings[following]].kind in _WHITESPACE:
                    following += 1
            if following == len(siblings):
                continue
            after = nodes[siblings[following]]
            gap = source[end:after.start]
            if (gap.strip(b' \t\r\n') or b'\r' in gap.replace(b'\r\n', b'')
                    or gap.count(b'\n') >= 2):
                continue
            if after.kind == 'Text':
                value = source[after.start:after.end].decode('utf-8')
                if not value or value[0].isspace() or value[-1].isspace():
                    continue
            elif after.kind not in _STRUCTURAL and not (after.kind == 'Equation' and after.display):
                if after.kind != 'Hash' or not conditional_block(siblings, following):
                    continue
            line_index = bisect_left(newlines, start)
            finding = Diagnostic(RULE_ID, rule['grade'], path, line_index + 1,
                len(source[line_start:start].decode('utf-8')) + 1,
                'Insert a blank line if this is the reviewed proof and the following source renders as the expected block or prose.',
                rule['source'])
            if any(scope.covers(RULE_ID, start) for scope in scopes):
                suppressed.append(finding)
            else:
                findings.append(finding)
                if _preview_candidates is not None:
                    _preview_candidates.append((start, end, after.start))
    return ProofResult(tuple(findings), (), (reason,), suppressed=tuple(suppressed))


def _preview_signature(tree, gaps):
    """Ignore only the specifically selected markup separator whitespace."""
    starts = tuple(start for start, _ in gaps)

    def ignored(node):
        pos = bisect_right(starts, node.start) - 1
        return (node.kind in _WHITESPACE and pos >= 0
                and gaps[pos][0] <= node.start <= node.end <= gaps[pos][1])

    def visit(index):
        node = tree.nodes[index]
        children, positions = [], {}
        for child in node.children:
            if node.kind == 'Markup' and ignored(tree.nodes[child]):
                continue
            positions[child] = len(children)
            children.append(visit(child))
        if any(child not in positions for _, child in node.operands):
            raise SafetyError('Selected whitespace is a typed syntax operand')
        roles = tuple((role, positions[child]) for role, child in node.operands)
        raw = tree.source[node.start:node.end] if not node.children else b''
        return node.kind, node.display, roles, raw, tuple(children)

    return visit(0)


def preview_conditional(tree, path, resolution, *, catalog=None):
    """Return source-only conditional proof-separation proposals, never apply.

    Every candidate is reparsed as a batch; only its selected separator can
    differ in the syntax signature. Suppression targets must map identically,
    and the candidates must disappear on independent source reanalysis.
    Neither proof binding, execution nor PDF equivalence follows from this.
    """
    candidates = []
    initial = analyze_conditional(tree, path, resolution, catalog=catalog,
                                  _preview_candidates=candidates)
    if not candidates:
        return initial
    source = tree.source
    edits, gaps = [], []
    for start, end, following in candidates:
        gap = source[end:following]
        gaps.append((end, following))
        if b'\n' in gap:
            first_lf = gap.index(b'\n')
            newline = b'\r\n' if first_lf and gap[first_lf - 1:first_lf] == b'\r' else b'\n'
            at = end + first_lf + 1
            edits.append(Edit(at, at, newline))
        else:
            lf = source.find(b'\n', end)
            if lf < 0:
                lf = source.rfind(b'\n', 0, end)
            newline = b'\r\n' if lf > 0 and source[lf - 1:lf] == b'\r' else b'\n'
            edits.append(Edit(end, end, newline * 2))
    try:
        gaps.sort()
        planned = plan(source, edits)
        if planned.deferred:
            raise SafetyError('Conflicting conditional proof boundary proposals')
        proposed = planned.candidate()
        candidate = parse(proposed, tree.mode)
        if candidate.nodes[0].errors:
            raise SafetyError('Conditional proof candidate failed to parse')
        positions = [edit.start for edit in planned.edits]
        shifts = [0]
        for edit in planned.edits:
            shifts.append(shifts[-1] + len(edit.replacement))

        def mapped(position, *, end=False):
            return position + shifts[(bisect_left if end else bisect_right)(positions, position)]

        candidate_gaps = tuple((mapped(start, end=True), mapped(end)) for start, end in gaps)
        if _preview_signature(tree, tuple(gaps)) != _preview_signature(candidate, candidate_gaps):
            raise SafetyError('Proof separation would change non-separator syntax or attachment')
        records = load_rules()[0]['rules'] if catalog is None else catalog
        ids = {record['id'] for record in records}
        before = resolve_suppressions(tree, ids)
        after = resolve_suppressions(candidate, ids)
        expected = tuple((scope.rules, scope.reason, mapped(scope.start),
                          mapped(scope.end, end=True), mapped(scope.directive_start)) for scope in before)
        actual = tuple((scope.rules, scope.reason, scope.start, scope.end, scope.directive_start) for scope in after)
        if actual != expected:
            raise SafetyError('Proof separation would retarget a suppression directive')
        remaining = []
        checked = analyze_conditional(candidate, path, resolve(candidate, packages=()), catalog=catalog,
                                       _preview_candidates=remaining)
        original_starts = {mapped(original) for original, _, _ in candidates}
        if checked.parse_errors or any(start in original_starts for start, _, _ in remaining):
            raise SafetyError('Conditional proof candidate did not converge')
    except (AdapterError, SafetyError, RecursionError) as error:
        return ProofResult(initial.diagnostics, (),
                           (*initial.skips, f'preview_unavailable: {error}'),
                           initial.parse_errors, initial.suppressed)
    return ProofResult(initial.diagnostics, tuple(planned.edits),
                       (*initial.skips, 'Source-only preview; PDF equivalence and binding remain unproved.'),
                       initial.parse_errors, initial.suppressed)
