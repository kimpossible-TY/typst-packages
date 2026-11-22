"""Independent conditional preview for prose footnotes followed by displays."""
from document_display import analyze_all as analyze_displays
from document_gap_preview import _scopes
from document_nested_prose_footnote_gap import _has_explicit_shadow, _signature, any_kind_ancestor
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

RULE = footnote_rules.GAP
CONDITIONS = (
    'The selected footnote and inserted parbreak have their intended builtin bindings at runtime.',
    'The footnote executes as prose and the following display is a separate rendered block.',
    'The explicit paragraph break can change rendering; equivalence and execution are unproved.',
)


def _select(tree, notes):
    source, nodes = tree.source, tree.nodes
    mapper = _FindingMapper(source)
    diagnosed = {mapper.offset(item): item for item in notes[RULE].diagnostics}
    parents = {child: (index, position) for index, node in enumerate(nodes)
               for position, child in enumerate(node.children)}
    edits, selected = [], []
    for index, call in enumerate(nodes):
        if (call.kind != 'FuncCall' or not call.start or source[call.start - 1:call.start] != b'#'
                or call.start - 1 not in diagnosed or index not in parents):
            continue
        parent, position = parents[index]
        body = nodes[parent]
        if body.kind != 'Markup' or any_kind_ancestor(nodes, parents, parents.get(parent), {'Equation', 'Math'}):
            continue
        gap = footnote_rules._BLANK_AFTER_FOOTNOTE.match(source, call.end, body.end)
        if gap is None:
            continue
        following = position + 1
        while following < len(body.children):
            trivia = nodes[body.children[following]]
            if (trivia.kind not in ('Space', 'Parbreak')
                    or source[trivia.start:trivia.end].strip(b' \t\r\n')):
                break
            following += 1
        if following == len(body.children):
            continue
        display = nodes[body.children[following]]
        if (display.kind != 'Equation' or not display.display or display.start < gap.end()
                or source[gap.end():display.start].strip(b' \t')):
            continue
        newline = gap.group(1)
        edits.append(Edit(call.end + len(newline), call.end + len(newline), b'#parbreak()' + newline))
        selected.append(diagnosed[call.start - 1])
    return tuple(edits), tuple(selected)


def preview(tree, path, *, catalog=None):
    """Propose an explicit prose break only before an existing direct display."""
    records = load_rules()[0]['rules'] if catalog is None else catalog
    source = tree.source
    empty = dict(path=path, baseline_sha256=digest(source), candidate_sha256=None,
                 conditions=CONDITIONS, runtime_binding_proved=False, execution_proved=False,
                 render_equivalence_proved=False, edit_permission=False, compiler_permission=False, edits=[])
    try:
        if tree.mode != 'markup' or tree.nodes[0].errors:
            raise SafetyError('Requires well-formed markup source')
        before_resolution = resolve(tree, packages=())
        before = footnote_rules.analyze_conditional_nested(tree, path, before_resolution, catalog=records)
        edits, selected = _select(tree, before)
        if not edits:
            return None
        if _has_explicit_shadow(tree):
            return dict(empty, verification='withheld', reason='Explicit footnote, marker or parbreak shadow')
        proposal = plan(source, edits)
        if proposal.deferred or len(proposal.edits) != len(edits) or len(selected) != len(edits):
            raise SafetyError('Conflicting prose footnote display gap edits')
        candidate = proposal.candidate()
        parsed = parse(candidate, tree.mode)
        shift = 0
        inserted = []
        for edit in proposal.edits:
            start = edit.start + shift
            inserted.append((start, start + len(b'#parbreak()')))
            shift += len(edit.replacement)
        if (parsed.nodes[0].errors or _signature(tree) != _signature(parsed, inserted)
                or _scopes(tree, records, proposal.edits) != _scopes(parsed, records)):
            raise SafetyError('Prose break changed other syntax or suppression targets')
        after_resolution = resolve(parsed, packages=())
        after = footnote_rules.analyze_conditional_nested(parsed, path, after_resolution, catalog=records)
        for rule_id in footnote_rules.RULE_IDS:
            expected = _keys(source, before[rule_id].diagnostics, proposal.edits)
            if rule_id == RULE:
                expected.subtract(_keys(source, selected, proposal.edits))
            if (any(count < 0 for count in expected.values())
                    or +expected != _keys(candidate, after[rule_id].diagnostics)
                    or _keys(source, before[rule_id].suppressed, proposal.edits)
                    != _keys(candidate, after[rule_id].suppressed) or after[rule_id].parse_errors):
                raise SafetyError('Prose break changed other footnote findings or suppressions')
        displays_before = analyze_displays(tree, path, catalog=records)
        displays_after = analyze_displays(parsed, path, catalog=records)
        if any(not _same_findings(tree, displays_before[rid], parsed, displays_after[rid], proposal.edits)
               for rid in displays_before):
            raise SafetyError('Prose break changed display findings')
        if not _same_findings(tree, analyze_scripts(tree, path, catalog=records, _verify=False, _allow_code_call=True),
                              parsed, analyze_scripts(parsed, path, catalog=records, _verify=False, _allow_code_call=True),
                              proposal.edits):
            raise SafetyError('Prose break changed script findings')
        for analyzer in (paragraph_rules.analyze_conditional_nested_gap,
                         paragraph_rules.analyze_conditional_nested_target,
                         paragraph_rules.analyze_conditional_nested_capitalization,
                         prose_marker_rules.analyze_conditional, continuation_rules.analyze_conditional):
            if not _same_findings(tree, analyzer(tree, path, catalog=records),
                                  parsed, analyzer(parsed, path, catalog=records), proposal.edits):
                raise SafetyError('Prose break changed paragraph findings')
        if not _same_findings(tree, proof_rules.analyze_conditional(tree, path, before_resolution, catalog=records),
                              parsed, proof_rules.analyze_conditional(parsed, path, after_resolution, catalog=records),
                              proposal.edits):
            raise SafetyError('Prose break changed proof findings')
        if _select(parsed, after)[0]:
            raise SafetyError('Conditional prose break preview did not converge')
        return dict(empty, candidate_sha256=digest(candidate),
                    verification='conditional-prose-display-gap-syntax-directives-and-reanalysis',
                    edits=[dict(start=edit.start, end=edit.end, replacement=edit.replacement.decode(), rule_id=RULE)
                           for edit in proposal.edits])
    except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
        return dict(empty, verification='withheld', reason=str(error))
