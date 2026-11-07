"""Real AST-backed script diagnostics and partial in-memory edit proposals."""
from dataclasses import dataclass

from edit_plan import Edit, plan
from syntax_client import AdapterError, parse
from typst_lint import Diagnostic, load_rules

RULE_ID = 'core.parenthesized-scripts'


@dataclass(frozen=True)
class ScriptResult:
    diagnostics: tuple[Diagnostic, ...] = ()
    edits: tuple[Edit, ...] = ()
    skips: tuple[str, ...] = ()
    parse_errors: tuple[tuple[int, int], ...] = ()


def analyze(tree, path, *, _verify=True):
    """No bindings or import execution. Directives conservatively block the file."""
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        errors = tuple((n.start, n.end) for n in nodes if n.kind == 'Error')
        return ScriptResult(skips=('Malformed Typst syntax; no repair proposed',), parse_errors=errors or ((0, len(source)),))
    if any(n.kind in ('LineComment', 'BlockComment') and b'typst-lint:' in source[n.start:n.end] for n in nodes):
        return ScriptResult(skips=('Inline directive scoping is unsupported; no findings or edits for this file',))
    rule = next(r for r in load_rules()[0]['rules'] if r['id'] == RULE_ID)
    findings, operands, skips = [], [], []
    for attach in nodes:
        for role, operand_id in attach.operands:
            operand = nodes[operand_id]
            # Parenthesized script operands are Math nodes with real paren tokens.
            children = [nodes[i] for i in operand.children]
            if operand.kind == 'Math' and children and children[0].kind == 'LeftParen' and children[-1].kind == 'RightParen':
                continue
            siblings = [nodes[i] for i in attach.children]
            position = attach.children.index(operand_id)
            preceding = siblings[:position]
            operator = 'Hat' if role == 'top' else 'Underscore'
            operator_index = next((i for i in range(len(preceding)-1, -1, -1) if preceding[i].kind == operator), None)
            if operator_index is None:
                raise AdapterError('Attachment operand has no matching operator')
            between = preceding[operator_index+1:]
            if any(n.kind not in ('Space',) for n in between) or operand.start == operand.end:
                skips.append(f'Unsupported escaped/commented/empty script operand at byte {operand.start}')
                continue
            line = source[:operand.start].count(b'\n') + 1
            column = len(source[source.rfind(b'\n', 0, operand.start)+1:operand.start].decode('utf-8')) + 1
            findings.append(Diagnostic(RULE_ID, rule['grade'], path, line, column,
                                       'Parenthesize the complete math script operand.', rule['source']))
            operands.append((operand.start, operand.end))
    # Coalesce parentheses at shared boundaries, preserving nested operand trees.
    opens, closes = {}, {}
    for start, end in sorted(set(operands)):
        opens[start] = opens.get(start, 0) + 1
        closes[end] = closes.get(end, 0) + 1
    edits = tuple(Edit(p, p, b')' * closes.get(p, 0) + b'(' * opens.get(p, 0))
                  for p in sorted(opens.keys() | closes.keys()))
    if edits and _verify:
        candidate = plan(source, edits).candidate()
        checked = parse(candidate, tree.mode)
        final = analyze(checked, path, _verify=False)
        if checked.nodes[0].errors or final.edits or final.diagnostics:
            return ScriptResult(tuple(findings), skips=tuple(skips)+('Candidate failed parse/idempotence verification; no edits proposed',))
    return ScriptResult(tuple(findings), edits, tuple(skips))
