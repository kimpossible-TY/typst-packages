"""Source-derived applicability for one deliberately closed core-only language.

This is not global rule coverage and does not evaluate scripts. The bridge must
actually evaluate ``core.parenthesized-scripts`` and re-certify every candidate.
No parsing, resource lookup, catalog loading, or filesystem access occurs here.
The protected bridge must obtain Tree from its owned official-parser invocation;
structural checks do not authenticate arbitrary caller-fabricated parser output.
"""
from dataclasses import dataclass, field
import hashlib
import json
import re
import unicodedata

from syntax_client import AdapterError, MAX_INPUT, MAX_NODES, Node, Tree, validate
from typst_lint import SafetyError

GRAMMAR_VERSION = 'closed-core-markup-v1'
PARSER_CONTRACT = ('typst-syntax:0.15.1', 'adapter:0.2.0', 'schema:2')
EVALUATED_RULE_IDS = ('core.parenthesized-scripts',)
# Each statement discharges the actual specification's applicability condition.
# In particular, no book helper use means no book-owner requirement, rather than
# treating an ordinary document's missing book show application as a violation.
STRUCTURAL_REASONS = (
    ('core.scoped-function-call', 'No calls, fields, scope creation, or receiver bindings exist.'),
    ('core.scoped-marker-tags', 'No annotation scope, mark call, or label argument exists.'),
    ('core.scoped-anchor-access', 'No annotation drawing, marker declaration, or drawing call exists.'),
    ('core.scoped-reference-access', 'No document reference, generated tag, or reference call exists.'),
    ('core.local-overlay-api', 'No local annotation scope or overlay call exists.'),
    ('core.annotation-in-display', 'No equation-overlay call exists inside or outside a display.'),
    ('core.annotation-marker-bounds', 'No overlay target, marker binding, or label exists.'),
    ('core.unique-scope-namespace', 'No scope declaration exists, and no import/include can extend the graph.'),
    ('core.project-ref-arguments', 'No project-ref call or other call exists.'),
    ('core.project-ref-catalog-target', 'No project-ref call or target exists, regardless of catalog contents.'),
    ('core.single-book-show', 'No import, include, call, or shared math-book helper use exists; its using-helper precondition is false.'),
    ('core.no-duplicate-book-reset', 'No book show owner or separate reset application exists.'),
    ('core.no-set-theme', 'No set rule or custom theme application exists.'),
    ('core.theme-in-context', 'No reusable helper, closure, or theme-resolution call exists.'),
)
NOT_APPLICABLE_RULE_IDS = tuple(rid for rid, _ in STRUCTURAL_REASONS)
CORE_RULE_IDS = EVALUATED_RULE_IDS + NOT_APPLICABLE_RULE_IDS
_ISSUER = object()


class ClosedCoreError(SafetyError):
    """The exact source/tree/path is outside the certified closed language."""


def _require(condition, message):
    if not condition:
        raise ClosedCoreError(message)


@dataclass(frozen=True, slots=True, init=False)
class CoverageProof:
    path: str
    source_sha256: str
    source_bytes: int
    tree_sha256: str
    grammar_version: str
    parser_contract: tuple[str, ...]
    evaluated_rule_ids: tuple[str, ...]
    not_applicable_rule_ids: tuple[str, ...]
    structural_reasons: tuple[tuple[str, str], ...]
    _issuer: object = field(repr=False, compare=False)

    def __init__(self, *args, **kwargs):
        raise TypeError('CoverageProof is issued only by certify(tree, path)')


def _validated(tree, path):
    _require(type(path) is str and bool(path) and path.endswith('.typ')
             and not path.startswith('/') and '\\' not in path and '\x00' not in path
             and all(p not in ('', '.', '..') for p in path.split('/')),
             'Closed-core path must be a canonical root-relative .typ path')
    _require(type(tree) is Tree and type(tree.source) is bytes
             and len(tree.source) <= MAX_INPUT and type(tree.mode) is str
             and tree.mode == 'markup' and type(tree.nodes) is tuple
             and 0 < len(tree.nodes) <= MAX_NODES,
             'Closed-core requires an immutable bounded markup Tree')
    records = []
    for node in tree.nodes:
        _require(type(node) is Node and type(node.children) is tuple
                 and type(node.operands) is tuple
                 and all(type(p) is tuple and len(p) == 2 for p in node.operands),
                 'Closed-core requires exact immutable Node fields')
        records.append(dict(kind=node.kind, start=node.start, end=node.end,
                            children=list(node.children), errors=node.errors,
                            operands=[dict(role=r, node=i) for r, i in node.operands],
                            display=node.display))
    payload = dict(schema=2, adapter_version='0.2.0', syntax_version='0.15.1',
                   mode='markup', source_bytes=len(tree.source),
                   source_sha256=hashlib.sha256(tree.source).hexdigest(), nodes=records)
    try:
        # Recheck types, UTF-8 boundaries, exhaustive preorder/partition, depth,
        # and typed equation/operand metadata, even for caller-constructed Tree.
        checked = validate(payload, tree.source, 'markup')
    except (AdapterError, UnicodeError, TypeError, ValueError, KeyError) as error:
        raise ClosedCoreError(f'Invalid closed-core syntax tree: {error}') from error
    _require(checked == tree, 'Closed-core syntax tree changed during validation')
    _require(all(not node.errors for node in tree.nodes), 'Malformed Typst syntax is outside closed core')
    return records


# These are grammatical token constraints, not an allowlist of allegedly pure
# function names. Every MathCall/FuncCall is refused, including familiar builtins.
_SHORTHANDS = frozenset(('-', '*', '~', '<=', '>=', '!=', '->', '<-', '<->', '=>', '<=>', '...'))
_PROSE_META = frozenset('#$\\/*_`@<>[]{}^~=+')
_MATH_PUNCTUATION = frozenset('+=<>|:,;.!?%')
_MATH_FORBIDDEN = frozenset('#$\\/_`@[]{}^()')
_PAIRS = {'(': ')', '[': ']', '{': '}'}
_EXPR_KINDS = frozenset(('MathText', 'MathIdent', 'MathShorthand', 'Math',
                         'MathDelimited', 'MathAttach', 'MathFrac'))


class _Grammar:
    def __init__(self, tree):
        self.tree, self.nodes = tree, tree.nodes
        self.visited = set()

    def raw(self, i):
        n = self.nodes[i]
        return self.tree.source[n.start:n.end].decode('utf-8')

    def enter(self, i):
        _require(i not in self.visited, 'Repeated closed-core grammar node')
        self.visited.add(i)
        n = self.nodes[i]
        _require(not n.operands or n.kind == 'MathAttach', 'Operand metadata outside attachment')
        return n

    def token(self, i, kind, exact=None):
        n = self.enter(i)
        _require(n.kind == kind and not n.children and n.start < n.end
                 and not n.operands, f'Invalid {kind} token at byte {n.start}')
        text = self.raw(i)
        if exact is not None:
            _require(text == exact, f'Incorrect {kind} token bytes at byte {n.start}')
        return text

    def space(self, i, kind='Space'):
        text = self.token(i, kind)
        _require(all(c in ' \t\r\n' for c in text), 'Nonliteral whitespace outside closed core')
        if kind == 'Parbreak':
            _require(text.count('\n') + text.count('\r') - text.count('\r\n') >= 2,
                     'Paragraph break needs at least two literal newlines')

    def surrounded(self, ids, opening_kind, closing_kind, opening, closing):
        _require(len(ids) >= 3, 'Incomplete closed-core grouping')
        self.token(ids[0], opening_kind, opening)
        self.token(ids[-1], closing_kind, closing)
        middle = list(ids[1:-1])
        if middle and self.nodes[middle[0]].kind == 'Space':
            self.space(middle.pop(0))
        if middle and self.nodes[middle[-1]].kind == 'Space':
            self.space(middle.pop())
        _require(len(middle) == 1 and self.nodes[middle[0]].kind == 'Math',
                 'Grouping must contain exactly one ordinary Math body')
        self.math(middle[0])

    def markup(self):
        root = self.enter(0)
        _require(root.kind == 'Markup', 'Closed-core root must be Markup')
        _require(bool(root.children) or not self.tree.source, 'Nonempty markup root cannot be an opaque leaf')
        for i in root.children:
            kind = self.nodes[i].kind
            if kind == 'Text':
                text = self.token(i, 'Text')
                if self.nodes[i].start == 0 and text.startswith('\ufeff'):
                    text = text[1:]
                _require(all((c == ' ' or c.isprintable()) and c not in _PROSE_META for c in text),
                         f'Nonliteral prose at byte {self.nodes[i].start}')
            elif kind in ('Space', 'Parbreak'):
                self.space(i, kind)
            elif kind == 'Equation':
                self.equation(i)
            else:
                raise ClosedCoreError(f'Unsupported markup kind {kind} at byte {self.nodes[i].start}')
        _require(len(self.visited) == len(self.nodes), 'Unvisited descendant outside closed core')

    def equation(self, i):
        n = self.enter(i)
        ids = n.children
        _require(len(ids) >= 3, 'Incomplete closed-core equation')
        # Match the pinned official Equation::block typed definition exactly.
        display = self.nodes[ids[1]].kind == 'Space' and self.nodes[ids[-2]].kind == 'Space'
        _require(n.display is display, 'Equation typed display property disagrees with its children')
        self.surrounded(ids, 'Dollar', 'Dollar', '$', '$')

    def math(self, i):
        n = self.enter(i)
        _require(n.kind == 'Math' and bool(n.children), 'Empty or non-Math body outside closed core')
        if self.nodes[n.children[0]].kind == 'LeftParen':
            self.surrounded(n.children, 'LeftParen', 'RightParen', '(', ')')
            return
        _require(any(self.nodes[c].kind != 'Space' for c in n.children), 'Whitespace-only math body')
        for child in n.children:
            if self.nodes[child].kind == 'Space':
                self.space(child)
            else:
                self.expr(child)

    def expr(self, i):
        n = self.nodes[i]
        _require(n.kind in _EXPR_KINDS, f'Unsupported math kind {n.kind} at byte {n.start}')
        if n.kind == 'Math':
            self.math(i)
        elif n.kind == 'MathDelimited':
            self.enter(i)
            ids = n.children
            _require(len(ids) >= 3, 'Incomplete math delimiter')
            opening, closing = self.raw(ids[0]), self.raw(ids[-1])
            _require(opening in _PAIRS and closing == _PAIRS[opening], 'Unsupported or mismatched math delimiters')
            self.surrounded(ids, 'MathText', 'MathText', opening, closing)
        elif n.kind == 'MathAttach':
            self.attachment(i)
        elif n.kind == 'MathFrac':
            self.enter(i)
            parts = list(n.children)
            _require(bool(parts), 'Empty fraction')
            self.expr(parts.pop(0))
            if parts and self.nodes[parts[0]].kind == 'Space':
                self.space(parts.pop(0))
            _require(bool(parts), 'Fraction missing slash')
            self.token(parts.pop(0), 'Slash', '/')
            if parts and self.nodes[parts[0]].kind == 'Space':
                self.space(parts.pop(0))
            _require(len(parts) == 1, 'Fraction must have exactly two operands')
            self.expr(parts[0])
        else:
            text = self.token(i, n.kind)
            if n.kind == 'MathIdent':
                _require(len(text) >= 2 and text[0].isalpha() and all(c.isalnum() for c in text),
                         'Unsupported ordinary math identifier token')
            elif n.kind == 'MathShorthand':
                _require(text in _SHORTHANDS, 'Unsupported math shorthand token')
            else:
                _require(bool(re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', text))
                         or (len(text) == 1 and (text.isalpha() or text in _MATH_PUNCTUATION
                             or unicodedata.category(text) == 'Sm') and text not in _MATH_FORBIDDEN),
                         'Unsupported ordinary math text token')

    def attachment(self, i):
        n = self.enter(i)
        parts = list(n.children)
        _require(bool(parts), 'Empty attachment')
        self.expr(parts.pop(0))
        if parts and self.nodes[parts[0]].kind == 'MathPrimes':
            text = self.token(parts.pop(0), 'MathPrimes')
            _require(text and set(text) == {"'"}, 'Invalid literal math primes')
        expected = []
        while parts:
            if self.nodes[parts[0]].kind == 'Space':
                self.space(parts.pop(0))
            _require(bool(parts), 'Trailing attachment whitespace')
            op = parts.pop(0)
            kind = self.nodes[op].kind
            _require(kind in ('Hat', 'Underscore'), 'Unsupported attachment operator')
            self.token(op, kind, '^' if kind == 'Hat' else '_')
            if parts and self.nodes[parts[0]].kind == 'Space':
                self.space(parts.pop(0))
            _require(bool(parts), 'Missing attachment operand')
            operand = parts.pop(0)
            self.expr(operand)
            expected.append(('top' if kind == 'Hat' else 'bottom', operand))
        _require(tuple(expected) == n.operands and len({r for r, _ in expected}) == len(expected),
                 'Attachment typed operands disagree with exact explicit operators')
        _require(bool(expected) or any(self.nodes[c].kind == 'MathPrimes' for c in n.children),
                 'Attachment has no script or primes')


def certify(tree, path):
    """Derive applicability from a bridge-owned official-parser Tree.

    A caller cannot supply a COMPLETE flag or instantiate the returned proof.
    This pure function validates its input representation, not parser provenance.
    """
    records = _validated(tree, path)
    _Grammar(tree).markup()
    values = dict(path=path, source_sha256=hashlib.sha256(tree.source).hexdigest(),
                  source_bytes=len(tree.source),
                  tree_sha256=hashlib.sha256(json.dumps(records, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                  grammar_version=GRAMMAR_VERSION, parser_contract=PARSER_CONTRACT,
                  evaluated_rule_ids=EVALUATED_RULE_IDS,
                  not_applicable_rule_ids=NOT_APPLICABLE_RULE_IDS,
                  structural_reasons=STRUCTURAL_REASONS, _issuer=_ISSUER)
    proof = object.__new__(CoverageProof)
    for name, value in values.items():
        object.__setattr__(proof, name, value)
    return proof


def verify_proof(proof, tree, path):
    """Re-derive admission; reject unissued, stale, or mismatched proofs.

    As with certify, tree must come from the bridge-owned official parser.
    """
    _require(type(proof) is CoverageProof and getattr(proof, '_issuer', None) is _ISSUER,
             'Closed-core proof was not issued by certify')
    expected = certify(tree, path)
    _require(proof == expected, 'Closed-core proof does not bind the current source/path/tree')
