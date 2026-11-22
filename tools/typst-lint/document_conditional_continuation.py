"""Independent conditional preview of a direct prose continuation pattern.

Only existing conditional continuation diagnostics select edits. This source
analysis proves neither execution, rendering equivalence, nor write authority.
"""
from document_display import analyze_all as analyze_displays
from document_gap_preview import _scopes
from document_sequence_preview import _FindingMapper, _keys, _same_findings
from edit_plan import Edit, plan
import continuation_rules
import footnote_rules
import paragraph_rules
import proof_rules
import prose_marker_rules
from script_rule import analyze as analyze_scripts
from static_resolver import resolve
from syntax_client import parse
from typst_lint import InputError, SafetyError, digest, load_rules

RULE = continuation_rules.RULE_ID
CONDITIONS = (
    'Both source sentences execute and render as one ordinary prose paragraph.',
    'The selected source newline is not significant to another runtime transformation.',
    'Runtime sources and include context match this snapshot; rendering equivalence is not proved.',
)


def _nontext_signature(tree):
    """Compare all non-text syntax while allowing adjacent text nodes to merge."""
    nodes, source = tree.nodes, tree.source
    parents = {child: index for index, node in enumerate(nodes) for child in node.children}
    kept = [index for index, node in enumerate(nodes) if node.kind not in ('Text', 'Space')]
    remap = {old: new for new, old in enumerate(kept)}
    result = []
    for index in kept:
        node = nodes[index]
        parent = parents.get(index)
        if parent is not None and parent not in remap:
            raise SafetyError('Unexpected syntax descendant of a text or space leaf')
        if any(target not in remap for _, target in node.operands):
            raise SafetyError('A syntax operand points into a merged text leaf')
        result.append((node.kind, remap.get(parent), node.display, node.errors,
                       tuple((name, remap[target]) for name, target in node.operands),
                       source[node.start:node.end] if not node.children else None))
    return tuple(result)


def _select(tree, notes):
    nodes, source = tree.nodes, tree.source
    mapper = _FindingMapper(source)
    diagnosed = {}
    for item in notes.diagnostics:
        diagnosed.setdefault(mapper.offset(item), []).append(item)
    edits, selected = [], []
    for body in nodes:
        if body.kind != 'Markup':
            continue
        siblings = body.children
        for position in range(1, len(siblings) - 1):
            left, gap, right = (nodes[index] for index in siblings[position - 1:position + 2])
            if ((left.kind, gap.kind, right.kind) != ('Text', 'Space', 'Text')
                    or not continuation_rules._GAP.fullmatch(source[gap.start:gap.end])):
                continue
            matches = diagnosed.get(left.end - 1, ())
            if len(matches) == 1:
                edits.append(Edit(gap.start, gap.end, b' '))
                selected.append(matches[0])
    return tuple(edits), tuple(selected)


def preview(tree, path, *, catalog=None):
    """Propose only exact whitespace edits for existing conditional findings."""
    records = load_rules()[0]['rules'] if catalog is None else catalog
    source = tree.source
    empty = dict(path=path, baseline_sha256=digest(source), candidate_sha256=None,
                 conditions=CONDITIONS, execution_proved=False, render_equivalence_proved=False,
                 edit_permission=False, compiler_permission=False, edits=[])
    try:
        if tree.mode != 'markup' or tree.nodes[0].errors:
            raise SafetyError('Requires well-formed markup source')
        before = continuation_rules.analyze_conditional(tree, path, catalog=records)
        edits, targets = _select(tree, before)
        if not edits:
            return None
        proposal = plan(source, edits)
        if proposal.deferred or len(proposal.edits) != len(edits) or len(targets) != len(edits):
            raise SafetyError('Conflicting continuation edits')
        candidate = proposal.candidate()
        parsed = parse(candidate, tree.mode)
        if (parsed.nodes[0].errors or _nontext_signature(tree) != _nontext_signature(parsed)
                or _scopes(tree, records, proposal.edits) != _scopes(parsed, records)):
            raise SafetyError('Continuation preview changed non-text syntax or suppression targets')
        after = continuation_rules.analyze_conditional(parsed, path, catalog=records)
        expected = _keys(source, before.diagnostics, proposal.edits)
        expected.subtract(_keys(source, targets, proposal.edits))
        if (any(count < 0 for count in expected.values())
                or +expected != _keys(candidate, after.diagnostics)
                or _keys(source, before.suppressed, proposal.edits) != _keys(candidate, after.suppressed)
                or after.parse_errors):
            raise SafetyError('Continuation preview changed other findings or suppressions')
        before_resolution, after_resolution = resolve(tree, packages=()), resolve(parsed, packages=())
        displays_before = analyze_displays(tree, path, catalog=records)
        displays_after = analyze_displays(parsed, path, catalog=records)
        if any(not _same_findings(tree, displays_before[rule], parsed, displays_after[rule], proposal.edits)
               for rule in displays_before):
            raise SafetyError('Continuation preview changed display findings or suppressions')
        scripts_before = analyze_scripts(tree, path, catalog=records, _verify=False, _allow_code_call=True)
        scripts_after = analyze_scripts(parsed, path, catalog=records, _verify=False, _allow_code_call=True)
        if not _same_findings(tree, scripts_before, parsed, scripts_after, proposal.edits):
            raise SafetyError('Continuation preview changed script findings or suppressions')
        footnotes_before = footnote_rules.analyze_conditional_nested(tree, path, before_resolution, catalog=records)
        footnotes_after = footnote_rules.analyze_conditional_nested(parsed, path, after_resolution, catalog=records)
        for rule in footnote_rules.RULE_IDS:
            if not _same_findings(tree, footnotes_before[rule], parsed, footnotes_after[rule], proposal.edits):
                raise SafetyError('Continuation preview changed footnote findings or suppressions')
        for analyzer in (paragraph_rules.analyze_conditional_nested_gap,
                         paragraph_rules.analyze_conditional_nested_target,
                         paragraph_rules.analyze_conditional_nested_capitalization,
                         prose_marker_rules.analyze_conditional):
            if not _same_findings(tree, analyzer(tree, path, catalog=records),
                                  parsed, analyzer(parsed, path, catalog=records), proposal.edits):
                raise SafetyError('Continuation preview changed paragraph findings or suppressions')
        if not _same_findings(tree, proof_rules.analyze_conditional(tree, path, before_resolution, catalog=records),
                              parsed, proof_rules.analyze_conditional(parsed, path, after_resolution, catalog=records),
                              proposal.edits):
            raise SafetyError('Continuation preview changed proof findings or suppressions')
        if _select(parsed, after)[0]:
            raise SafetyError('Continuation preview did not converge')
        return dict(empty, candidate_sha256=digest(candidate),
                    verification='conditional-continuation-syntax-directives-and-reanalysis',
                    edits=[dict(start=edit.start, end=edit.end, replacement=' ', rule_id=RULE)
                           for edit in proposal.edits])
    except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
        return dict(empty, verification='withheld', reason=str(error))
