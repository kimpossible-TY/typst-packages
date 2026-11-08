"""Independent source-only previews of aligned equation footnote spacers."""
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

RULE = footnote_rules.ALIGNED
INSERTION = b'#dots_space '
CONDITIONS = (
    'The inserted dots_space name is bound to the intended spacer in the execution scope.',
    'The selected footnote has its intended binding and executes within this aligned display.',
    'The selected runtime sources and styles match this snapshot; rendering may change.',
)


def _select(tree, before):
    """Select literal math calls already identified by the conditional analyzer."""
    source, nodes = tree.source, tree.nodes
    mapper = _FindingMapper(source)
    active = {mapper.offset(item) for item in before[RULE].diagnostics}
    edits = []
    for body in nodes:
        if body.kind != 'Math':
            continue
        siblings = body.children
        for position in range(len(siblings) - 1):
            mark, call = nodes[siblings[position]], nodes[siblings[position + 1]]
            if (mark.kind == 'Hash' and call.kind == 'FuncCall' and mark.end == call.start
                    and mark.start in active
                    and source[mark.start:call.end].startswith(b'#footnote[')):
                edits.append(Edit(mark.start, mark.start, INSERTION))
    return tuple(edits)


def preview(tree, path, *, catalog=None):
    """Propose spacer insertion without proving binding, rendering or write permission."""
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
        edits = _select(tree, before)
        if not edits:
            return None
        proposal = plan(source, edits)
        if proposal.deferred or len(proposal.edits) != len(edits):
            raise SafetyError('Conflicting aligned spacer edits')
        candidate = proposal.candidate()
        parsed = parse(candidate, tree.mode)
        shift = 0
        inserted = []
        for edit in proposal.edits:
            position = edit.start + shift
            inserted.append(Edit(position, position + len(b'#dots_space'), b''))
            shift += len(INSERTION)
        if (parsed.nodes[0].errors
                or footnote_rules._signature_without_spacers(tree, ())
                != footnote_rules._signature_without_spacers(parsed, tuple(inserted))
                or _scopes(tree, records, proposal.edits) != _scopes(parsed, records)):
            raise SafetyError('Aligned spacer insertion changes other syntax or suppression targets')
        after_resolution = resolve(parsed, packages=())
        after = footnote_rules.analyze_conditional_nested(parsed, path, after_resolution, catalog=records)
        mapper = _FindingMapper(source)
        for rule_id in footnote_rules.RULE_IDS:
            expected = _keys(source, before[rule_id].diagnostics, proposal.edits)
            if rule_id == RULE:
                selected = [item for item in before[rule_id].diagnostics
                            if mapper.offset(item) in {edit.start for edit in proposal.edits}]
                if len(selected) != len(edits):
                    raise SafetyError('Every aligned footnote must match an active diagnostic')
                expected.subtract(_keys(source, selected, proposal.edits))
            if (any(count < 0 for count in expected.values())
                    or +expected != _keys(candidate, after[rule_id].diagnostics)
                    or _keys(source, before[rule_id].suppressed, proposal.edits)
                    != _keys(candidate, after[rule_id].suppressed)
                    or after[rule_id].parse_errors):
                raise SafetyError('Aligned spacer insertion changed another finding or suppression')
        displays_before = analyze_displays(tree, path, catalog=records)
        displays_after = analyze_displays(parsed, path, catalog=records)
        if any(not _same_findings(tree, displays_before[rid], parsed, displays_after[rid], proposal.edits)
               for rid in displays_before):
            raise SafetyError('Aligned spacer insertion changed display findings')
        if not _same_findings(tree, analyze_scripts(tree, path, catalog=records, _verify=False, _allow_code_call=True),
                              parsed, analyze_scripts(parsed, path, catalog=records, _verify=False, _allow_code_call=True),
                              proposal.edits):
            raise SafetyError('Aligned spacer insertion changed script findings')
        for analyzer in (paragraph_rules.analyze_conditional_nested_gap,
                         paragraph_rules.analyze_conditional_nested_target,
                         paragraph_rules.analyze_conditional_nested_capitalization,
                         prose_marker_rules.analyze_conditional, continuation_rules.analyze_conditional):
            if not _same_findings(tree, analyzer(tree, path, catalog=records),
                                  parsed, analyzer(parsed, path, catalog=records), proposal.edits):
                raise SafetyError('Aligned spacer insertion changed paragraph findings')
        if not _same_findings(tree, proof_rules.analyze_conditional(tree, path, before_resolution, catalog=records),
                              parsed, proof_rules.analyze_conditional(parsed, path, after_resolution, catalog=records),
                              proposal.edits):
            raise SafetyError('Aligned spacer insertion changed proof findings')
        if _select(parsed, after):
            raise SafetyError('Conditional aligned spacer preview did not converge')
        return dict(empty, candidate_sha256=digest(candidate),
                    verification='conditional-aligned-spacer-syntax-directives-and-reanalysis',
                    edits=[dict(start=edit.start, end=edit.end,
                                replacement=INSERTION.decode(), rule_id=RULE) for edit in proposal.edits])
    except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
        return dict(empty, verification='withheld', reason=str(error))
