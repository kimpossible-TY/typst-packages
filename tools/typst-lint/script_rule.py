"""Real AST-backed script diagnostics and partial in-memory edit proposals."""
from dataclasses import dataclass

from edit_plan import Edit, plan
from syntax_client import AdapterError, parse
from typst_lint import Diagnostic, load_rules
from suppression import resolve

RULE_ID = 'core.parenthesized-scripts'


@dataclass(frozen=True)
class ScriptResult:
    diagnostics: tuple[Diagnostic, ...] = ()
    edits: tuple[Edit, ...] = ()
    skips: tuple[str, ...] = ()
    parse_errors: tuple[tuple[int, int], ...] = ()
    suppressed: tuple[Diagnostic, ...] = ()


def analyze(tree, path, *, _verify=True, catalog=None, _allow_code_call=False):
    """No bindings/import execution; scoped directives filter exact operands."""
    source, nodes = tree.source, tree.nodes
    if nodes[0].errors:
        errors = tuple((n.start, n.end) for n in nodes if n.kind == 'Error')
        return ScriptResult(skips=('Malformed Typst syntax; no repair proposed',), parse_errors=errors or ((0, len(source)),))
    catalog = load_rules()[0]['rules'] if catalog is None else catalog
    scopes = resolve(tree, {r['id'] for r in catalog})
    rule = next(r for r in catalog if r['id'] == RULE_ID)
    findings, operands, skips, suppressed = [], [], [], []
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
            start = operand.start
            # Document-only opt-in: a code call is an entire math operand, but
            # its leading # belongs to the attachment rather than FuncCall.
            # Parenthesize the hash as well; comments/other escapes stay partial.
            if (_allow_code_call and operand.kind == 'FuncCall' and between
                    and between[-1].kind == 'Hash' and between[-1].end == operand.start
                    and all(n.kind == 'Space' for n in between[:-1])):
                start = between[-1].start
                between = between[:-1]
            if any(n.kind not in ('Space',) for n in between) or operand.start == operand.end:
                skips.append(f'Unsupported escaped/commented/empty script operand at byte {operand.start}')
                continue
            line = source[:operand.start].count(b'\n') + 1
            column = len(source[source.rfind(b'\n', 0, operand.start)+1:operand.start].decode('utf-8')) + 1
            finding = Diagnostic(RULE_ID, rule['grade'], path, line, column,
                                 'Parenthesize the complete math script operand.', rule['source'])
            if any(scope.covers(RULE_ID, operand.start) for scope in scopes):
                suppressed.append(finding)
            else:
                findings.append(finding)
                operands.append((start, operand.end))
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
        final = analyze(checked, path, _verify=False, catalog=catalog, _allow_code_call=_allow_code_call)
        if checked.nodes[0].errors or final.edits or final.diagnostics or len(final.suppressed) != len(suppressed):
            return ScriptResult(tuple(findings), skips=tuple(skips)+('Candidate failed parse/idempotence verification; no edits proposed',), suppressed=tuple(suppressed))
    return ScriptResult(tuple(findings), edits, tuple(skips), suppressed=tuple(suppressed))
