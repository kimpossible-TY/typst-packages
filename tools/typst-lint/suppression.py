"""Bounded directives over real syntax nodes, never source-wide Typst parsing."""
from dataclasses import dataclass
import re

from typst_lint import InputError


class SuppressionError(InputError):
    """Unsuppressible invalid directive or unsupported construct ownership."""


@dataclass(frozen=True)
class Scope:
    rules: frozenset[str]
    reason: str
    start: int
    end: int
    directive_start: int

    def covers(self, rule_id, diagnostic_start):
        return rule_id in self.rules and self.start <= diagnostic_start < self.end


TRIVIA = frozenset({'Space', 'Parbreak', 'LineComment', 'BlockComment'})
# Reviewed complete sibling constructs in Markup/Code, not arbitrary descendants.
CONSTRUCTS = frozenset('''Text Linebreak Escape Shorthand SmartQuote Strong Emph Raw Link Label Ref
Heading ListItem EnumItem TermItem Equation Ident Bool Int Float Numeric Str ContentBlock CodeBlock
Parenthesized Array Dict Unary Binary FieldAccess FuncCall Closure LetBinding DestructAssignment
SetRule ShowRule Conditional WhileLoop ForLoop ModuleImport ModuleInclude LoopBreak LoopContinue
FuncReturn'''.split())


def resolve(tree, known_rule_ids):
    """Resolve directives without crossing their syntax sequence/body boundary.

    Markup and Code sequences have proven sibling construct boundaries. Other
    parent kinds (including math-internal trivia) currently fail explicitly.
    """
    source, nodes = tree.source, tree.nodes
    parents = {child: index for index, node in enumerate(nodes) for child in node.children}
    scopes = []
    for index, comment in enumerate(nodes):
        if comment.kind != 'LineComment':
            continue
        text = source[comment.start:comment.end].decode('utf-8')
        content = text[2:].lstrip(' \t')
        if not content.startswith('typst-lint:'):
            continue
        line = source[:comment.start].count(b'\n') + 1
        def fail(message):
            raise SuppressionError(f'Inline directive at line {line}: {message}')
        prefix_start = source.rfind(b'\n', 0, comment.start) + 1
        prefix = source[prefix_start:comment.start]
        if prefix_start == 0 and prefix.startswith(b'\xef\xbb\xbf'):
            prefix = prefix[3:]
        if prefix.strip(b' \t'):
            fail('directive must be on its own source line')
        match = re.fullmatch(r'typst-lint:[ \t]*ignore-next[ \t]+(.+?)[ \t]+--(?:[ \t]*(.*))?', content)
        if match is None:
            fail('expected ignore-next <rule-id>[, <rule-id>...] -- <reason>')
        ids = tuple(part.strip() for part in match[1].split(','))
        if not ids or any(not rid or rid not in known_rule_ids for rid in ids):
            fail('unknown or empty rule ID')
        reason = (match[2] or '').strip()
        if not reason:
            fail('nonempty reason is required')
        parent = nodes[parents[index]] if index in parents else None
        if parent is None or parent.kind not in ('Markup', 'Code'):
            fail('unsupported construct ownership outside a Markup/Code sequence')
        siblings = parent.children
        position = siblings.index(index) + 1
        while position < len(siblings) and nodes[siblings[position]].kind in TRIVIA:
            position += 1
        if position == len(siblings):
            fail('missing next construct in this body')
        target = nodes[siblings[position]]
        start = target.start
        if target.kind == 'Hash':
            position += 1
            if position == len(siblings):
                fail('missing expression after hash')
            expression = nodes[siblings[position]]
            if expression.start != target.end:
                fail('unsupported gap after hash')
            target = expression
        if target.kind not in CONSTRUCTS or target.end <= start or target.errors:
            fail(f'unsupported next construct: {target.kind}')
        scopes.append(Scope(frozenset(ids), reason, start, target.end, comment.start))
    return tuple(scopes)
