"""One reviewed module's flowbox border, proved from the complete parsed source.

No spelling, caller flag, import path or arbitrary block establishes ownership.
The whole-module structural/token fingerprint below is independently pinned to
the reviewed artifact. Only its unique border slot and harmless code trivia may
vary. This module never reads dependencies, evaluates Typst or writes a source.
"""
from dataclasses import dataclass
import hashlib
import json
import re

from edit_plan import Edit, plan
from suppression import resolve as resolve_suppressions
from syntax_client import AdapterError, parse
from typst_lint import Diagnostic, SafetyError, digest, load_rules

RULE_ID = 'pde.flowbox-colored-border'
REVIEWED_SOURCE_SHA256 = '542c7ce1ebfc6fbd51a7fefb2b55405a968a951b61d49170e49ef331948f36ff'
# Derived from that exact source with the pinned 0.15.1 parser, never learned
# from the caller's source. See FLOWBOX_CONTRACT.md and the artifact-pin test.
REVIEWED_OWNER_SIGNATURE_SHA256 = '0b83998386e7c5152ce8bd927031b4a9e4ff5b65b6113cf82c38ed0eb1cfe26b'
_TRIVIA = frozenset(('Space', 'Parbreak', 'LineComment', 'BlockComment'))
_BOM = b'\xef\xbb\xbf'
_LIMIT = ('Only the complete reviewed math-blocks 0.2.0 module, modulo its one '
          'literal flowbox border slot and code trivia, has proven ownership; '
          'fragments, changed semantic tokens, facades and dynamic strokes remain unresolved')
_WIDTH = re.compile(rb'(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:pt|mm|cm|in|em)\Z')


@dataclass(frozen=True)
class FlowboxResult:
    diagnostics: tuple[Diagnostic, ...] = ()
    edits: tuple[Edit, ...] = ()
    skips: tuple[str, ...] = ()
    parse_errors: tuple[tuple[int, int], ...] = ()
    suppressed: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class FlowboxOwner:
    """Derived whole-module evidence, not a trusted input to analyze()."""
    source_sha256: str
    signature_sha256: str
    border_node: int
    width_node: int
    palette_identifier: str
    colored: bool


def _raw(tree, index):
    node = tree.nodes[index]
    return tree.source[node.start:node.end]


def _significant(tree, index):
    return tuple(i for i in tree.nodes[index].children if tree.nodes[i].kind not in _TRIVIA)


def _border_slot(tree):
    """Locate one candidate; only the independently pinned digest certifies it."""
    nodes = tree.nodes
    candidates = []
    for index in nodes[0].children:
        if nodes[index].kind != 'LetBinding':
            continue
        parts = _significant(tree, index)
        if len(parts) != 2 or nodes[parts[1]].kind != 'Closure':
            continue
        closure = _significant(tree, parts[1])
        if (len(closure) == 4 and nodes[closure[0]].kind == 'Ident'
                and _raw(tree, closure[0]) == b'flowbox'
                and nodes[closure[3]].kind == 'Contextual'):
            candidates.append(closure[3])
    if len(candidates) != 1:
        return None
    context = _significant(tree, candidates[0])
    if len(context) != 2 or nodes[context[1]].kind != 'CodeBlock':
        return None
    block = _significant(tree, context[1])
    if len(block) != 3 or nodes[block[1]].kind != 'Code':
        return None
    body = _significant(tree, block[1])
    if (len(body) != 2 or nodes[body[0]].kind != 'LetBinding'
            or nodes[body[1]].kind != 'FuncCall'):
        return None
    call = _significant(tree, body[1])
    if (len(call) != 2 or nodes[call[0]].kind != 'Ident' or _raw(tree, call[0]) != b'block'
            or nodes[call[1]].kind != 'Args'):
        return None
    slots = []
    for index in _significant(tree, call[1]):
        if nodes[index].kind == 'Named':
            parts = _significant(tree, index)
            if (len(parts) == 3 and nodes[parts[0]].kind == 'Ident'
                    and _raw(tree, parts[0]) == b'stroke' and nodes[parts[1]].kind == 'Colon'):
                slots.append((index, parts[2]))
    return slots[0] if len(slots) == 1 else None


def _signature(tree, slot):
    """Retain AST relationships and every semantic token outside one slot.

    Code trivia is semantically inert only when its removal preserves this
    parsed structure. Content/math trivia stays exact (LF/CRLF transport aside).
    Root whitespace separates only the pinned module's definitions/comments.
    """
    def visit(index, literal=False):
        node = tree.nodes[index]
        if index == slot:
            return ('ReviewedFlowboxBorderSlot',)
        if (index != 0 and node.start == 0 and node.kind == 'Text'
                and _raw(tree, index) == _BOM):
            return None
        literal = literal or node.kind in ('ContentBlock', 'Equation', 'Math')
        if not literal and node.kind in _TRIVIA:
            return None
        children = []
        positions = {}
        for child in node.children:
            value = visit(child, literal)
            if value is not None:
                positions[child] = len(children)
                children.append(value)
        roles = tuple((role, positions[child]) for role, child in node.operands)
        leaf = _raw(tree, index) if not node.children else b''
        if node.kind in _TRIVIA:
            leaf = leaf.replace(b'\r\n', b'\n')
        return (node.kind, node.display, roles, leaf.hex(), tuple(children))
    payload = json.dumps(visit(0), separators=(',', ':'), ensure_ascii=True).encode('ascii')
    return hashlib.sha256(payload).hexdigest()


def prove_owner(tree):
    """Certify only the bounded reviewed module and supported border variants."""
    if tree.mode != 'markup' or tree.nodes[0].kind != 'Markup' or tree.nodes[0].errors:
        return None
    slot = _border_slot(tree)
    if slot is None:
        return None
    border, expression = slot
    signature = _signature(tree, expression)
    if signature != REVIEWED_OWNER_SIGNATURE_SHA256:
        return None
    nodes = tree.nodes
    colored = nodes[expression].kind == 'Binary'
    width = expression
    if colored:
        parts = _significant(tree, expression)
        if (len(parts) != 3 or nodes[parts[1]].kind != 'Plus'
                or nodes[parts[2]].kind != 'FieldAccess'):
            return None
        field = _significant(tree, parts[2])
        if (len(field) != 3 or tuple(nodes[i].kind for i in field) != ('Ident', 'Dot', 'Ident')
                or tuple(_raw(tree, i) for i in field) != (b'theme', b'.', b'rule')):
            return None
        width = parts[0]
    if (nodes[width].kind != 'Numeric' or len(_raw(tree, width)) > 80
            or not _WIDTH.fullmatch(_raw(tree, width))):
        return None
    # This actual local identifier, its context, selector implementation and
    # both literal rule-bearing palettes are all inside the unmasked digest.
    return FlowboxOwner(digest(tree.source), signature, border, width, 'theme', colored)


def analyze(tree, path, resolution):
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        errors = tuple((n.start, n.end) for n in nodes if n.kind == 'Error') or ((0, len(source)),)
        return FlowboxResult(skips=('Malformed Typst syntax; no flowbox ownership proof',), parse_errors=errors)
    if resolution.source_sha256 != digest(source) or len(resolution.facts) != len(nodes):
        return FlowboxResult(skips=('Stale or mismatched static resolution; no flowbox repair proposed',))
    proof = prove_owner(tree)
    if proof is None:
        return FlowboxResult(skips=(_LIMIT,))
    skips = (_LIMIT,)
    if proof.colored:
        return FlowboxResult(skips=skips)
    rules = {r['id']: r for r in load_rules()[0]['rules']}
    scopes = resolve_suppressions(tree, set(rules))
    start = nodes[proof.border_node].start
    line = source[:start].count(b'\n') + 1
    column = len(source[source.rfind(b'\n', 0, start) + 1:start].decode('utf-8')) + 1
    rule = rules[RULE_ID]
    finding = Diagnostic(RULE_ID, rule['grade'], path, line, column,
                         'Use the existing contextual theme.rule color on this flowbox border; retain its literal width.',
                         rule['source'])
    if any(scope.covers(RULE_ID, start) for scope in scopes):
        return FlowboxResult(skips=skips, suppressed=(finding,))
    at = nodes[proof.width_node].end
    edit = Edit(at, at, b' + ' + proof.palette_identifier.encode('ascii') + b'.rule')
    try:
        proposed = plan(source, (edit,))
        candidate = parse(proposed.candidate(), tree.mode)
        final = prove_owner(candidate)
        if (proposed.deferred or final is None or not final.colored
                or _raw(candidate, final.width_node) != _raw(tree, proof.width_node)):
            raise SafetyError('Flowbox candidate lost the reviewed owner, color or original literal width')
        after_scopes = resolve_suppressions(candidate, set(rules))
        def mapped(position):
            return position + len(edit.replacement) if at <= position else position
        before = tuple((s.rules, s.reason, mapped(s.start), mapped(s.end), mapped(s.directive_start)) for s in scopes)
        after = tuple((s.rules, s.reason, s.start, s.end, s.directive_start) for s in after_scopes)
        if before != after:
            raise SafetyError('Flowbox candidate changed directive target ownership')
    except (AdapterError, SafetyError) as error:
        return FlowboxResult((finding,), skips=(*skips, f'fix_unavailable: {error}'))
    return FlowboxResult((finding,), (edit,), skips)
