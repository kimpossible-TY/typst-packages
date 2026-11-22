"""Bounded, conditional composition of prose footnotes before paragraph markers.

This source proof is not evidence of runtime binding, execution or rendering.
A refusal or budget exhaustion leaves this optional stage unselected.
"""
from bisect import bisect_left
import continuation_rules
import document_display
import document_nested_prose_footnote_gap as gap
import footnote_rules
import paragraph_rules
from document_sequence_preview import _preserve_unresolved_paragraph_boundaries, _same_findings
from edit_plan import Edit, plan
from script_rule import analyze as analyze_script
from static_resolver import resolve
from syntax_client import parse
from typst_lint import SafetyError, digest

# This preflight bounds the known repeated AST, byte-span, ancestor and
# suppression-directive scans before running the existing source proof. It is a
# conservative selection limit, not a wall-clock guarantee.
MAX_PREFLIGHT_WORK = 100_000_000


def _within_budget(tree):
    nodes, source = tree.nodes, tree.source
    depths = [0] * len(nodes)
    parents = [None] * len(nodes)
    work = len(nodes) + len(source)
    directives = 0
    newlines = [offset for offset, byte in enumerate(source) if byte == 10]
    for index, node in enumerate(nodes):
        work += node.end - node.start + depths[index]
        # The paragraph, footnote and continuation analyzers inspect whole
        # line prefixes/suffixes for candidate nodes. Many candidates on one
        # line must not turn an apparently linear preflight into quadratic work.
        if node.kind in ('Ident', 'Text', 'FuncCall'):
            line = bisect_left(newlines, node.start)
            start = 0 if line == 0 else newlines[line - 1] + 1
            end = len(source) if line == len(newlines) else newlines[line]
            work += end - start + (len(newlines) + 1).bit_length()
        if work * 20 > MAX_PREFLIGHT_WORK:
            return False
        for child in node.children:
            parents[child] = index
            depths[child] = depths[index] + 1
        if node.kind == 'LineComment':
            text = source[node.start:node.end]
            if text[2:].lstrip(b' \t').startswith(b'typst-lint:'):
                directives += 1
                parent = parents[index]
                siblings = 0 if parent is None else len(nodes[parent].children)
                work += 2 * node.start + 2 * siblings + len(text) ** 3
                if work * 20 > MAX_PREFLIGHT_WORK:
                    return False
    # Each directive can be consulted against many candidate positions.
    return (work + directives * len(nodes)) * 20 <= MAX_PREFLIGHT_WORK


def preview(tree, path, catalog):
    """Return a separately verified stage, or None if no safe stage is selected."""
    if b'#footnote' not in tree.source or b'#paragraph_tab' not in tree.source or not _within_budget(tree):
        return None
    boundaries = paragraph_rules.analyze_conditional_nested_gap(tree, path, catalog=catalog)
    if any(reason.startswith(paragraph_rules.NESTED_GAP_UNRESOLVED) for reason in boundaries.skips):
        return None
    resolution = resolve(tree, packages=())
    notes = footnote_rules.analyze_conditional_nested(tree, path, resolution, catalog=catalog)
    expected, targets = gap._select(tree, path, catalog, resolution, notes)
    if not expected:
        return None
    expected_plan = plan(tree.source, expected)
    if expected_plan.deferred or len(expected_plan.edits) != len(expected):
        raise SafetyError('Conditional prose footnote candidates conflict')
    expected_candidate = expected_plan.candidate()
    expected_tree = parse(expected_candidate, tree.mode)
    if expected_tree.nodes[0].errors or not _within_budget(expected_tree):
        return None
    selected = gap.preview(tree, path, catalog=catalog)
    if selected is None:
        raise SafetyError('Conditional prose footnote candidates were omitted')
    if type(selected) is not dict:
        raise SafetyError('Conditional prose footnote proof is invalid')
    if selected.get('verification') == 'withheld':
        # This stage is optional: leave existing plans untouched if source
        # binding, suppression or another source proof cannot be established.
        return None
    if (selected.get('verification') != 'conditional-prose-footnote-gap-syntax-directives-and-reanalysis'
            or selected.get('conditions') != gap.CONDITIONS
            or any(selected.get(key) is not False for key in ('runtime_binding_proved',
                'render_equivalence_proved', 'edit_permission', 'compiler_permission'))
            or selected.get('execution_proved', False) is not False):
        raise SafetyError('Conditional prose footnote proof is invalid')
    rows = selected.get('edits')
    if (type(rows) is not list or len(rows) != len(expected) or len(targets) != len(expected)
            or any(type(row) is not dict or set(row) != {'start', 'end', 'replacement', 'rule_id'}
                   or row.get('rule_id') != gap.RULE or type(row.get('start')) is not int
                   or type(row.get('end')) is not int or row.get('start') != row.get('end')
                   or row.get('replacement') not in ('#parbreak()\n', '#parbreak()\r\n') for row in rows)):
        raise SafetyError('Conditional prose footnote edits are invalid')
    edits = tuple(Edit(row['start'], row['end'], row['replacement'].encode('ascii')) for row in rows)
    proposal = plan(tree.source, edits)
    candidate = proposal.candidate()
    if (proposal.deferred or tuple(proposal.edits) != expected
            or digest(tree.source) != selected.get('baseline_sha256')
            or digest(candidate) != selected.get('candidate_sha256')):
        raise SafetyError('Conditional prose footnote hash or edit mismatch')
    parsed = expected_tree
    if parsed.nodes[0].errors:
        raise SafetyError('Conditional prose footnote candidate is malformed')
    # Independent source proof predates these composable checks.
    for analyzer in (lambda t: analyze_script(t, path, catalog=catalog, _verify=False, _allow_code_call=True),
                     lambda t: continuation_rules.analyze_conditional(t, path, catalog=catalog)):
        if not _same_findings(tree, analyzer(tree), parsed, analyzer(parsed), proposal.edits):
            return None
    before, after = document_display.analyze_all(tree, path, catalog=catalog), document_display.analyze_all(parsed, path, catalog=catalog)
    if any(not _same_findings(tree, before[rid], parsed, after[rid], proposal.edits) for rid in before):
        return None
    try:
        _preserve_unresolved_paragraph_boundaries(tree, parsed, path, catalog, proposal.edits)
    except SafetyError:
        return None
    # Re-select against fresh candidate: the selected rule must converge.
    final_resolution = resolve(parsed, packages=())
    final_notes = footnote_rules.analyze_conditional_nested(parsed, path, final_resolution, catalog=catalog)
    if gap._select(parsed, path, catalog, final_resolution, final_notes)[0]:
        raise SafetyError('Conditional prose footnote stage did not converge')
    return dict(rule_id=gap.RULE, baseline_sha256=digest(tree.source), candidate_sha256=digest(candidate),
                edits=[dict(row, conditions=list(gap.CONDITIONS), runtime_binding_proved=False,
                            execution_proved=False, render_equivalence_proved=False,
                            edit_permission=False, compiler_permission=False) for row in rows])
