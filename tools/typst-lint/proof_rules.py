"""Partial proof source separation over reviewed bindings and real root calls.

Only insertion proposals are returned. No package evaluation, source writes or
compiler invocation happens here. See PROOF_CONTRACT.md for the exact boundary.
"""
from dataclasses import dataclass

from edit_plan import Edit, plan
from static_resolver import is_proof_binding
from suppression import resolve as resolve_suppressions
from syntax_client import AdapterError, parse
from typst_lint import Diagnostic, SafetyError, digest, load_rules

RULE_ID = 'pde.proof-trailing-gap'
_WHITESPACE = frozenset(('Space', 'Parbreak'))
_STRUCTURAL = frozenset(('Heading', 'ListItem', 'EnumItem', 'TermItem', 'Raw'))
_BLOCK_CALLS = frozenset(('block', 'figure', 'heading', 'list', 'enum', 'terms'))
_BOM = b'\xef\xbb\xbf'


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
