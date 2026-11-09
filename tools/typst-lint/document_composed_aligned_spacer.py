"""A narrow aligned-spacer stage for a resource-bound source sequence."""
from document_display import analyze_all as displays
from document_gap_preview import _scopes
from edit_plan import Edit, plan
import document_aligned_spacer_binding as binding
import document_conditional_aligned_spacer as independent
import document_composed_prose_footnote_gap as bounded_scans
import footnote_rules
import paragraph_rules
import proof_rules
import prose_marker_rules
import continuation_rules
from script_rule import analyze as scripts
from static_resolver import resolve
from syntax_client import parse
from typst_lint import SafetyError, digest

MAX_EDITS = 16
MAX_LOCAL_SCAN_WORK = 2_000_000


class AlignedSpacerWithheld(SafetyError):
    """An optional proof is unavailable; retain the earlier source plan."""


def _within_local_budget(tree):
    closures = sum(node.kind == 'Closure' for node in tree.nodes)
    return (8 * len(tree.nodes) * (closures + 1) + 2 * len(tree.source) <= MAX_LOCAL_SCAN_WORK
            and bounded_scans._within_budget(tree))


def compose(tree, path, catalog, evidence):
    """Return an independently checked step and candidate, or no candidate.

    Resource and namespace resolvers have their own fixed budgets. This local
    bound covers selected tree/closure scans, not elapsed time or all work.
    """
    from document_sequence_preview import _FindingMapper, _keys, _same_findings
    source = tree.source
    if b'#footnote[' not in source or b'&' not in source:
        return None
    if not _within_local_budget(tree):
        raise AlignedSpacerWithheld('Aligned spacer local scan budget exceeded')
    resolution = resolve(tree, packages=())
    before = footnote_rules.analyze_conditional_nested(tree, path, resolution, catalog=catalog)
    exact = independent._select(tree, before)
    if not exact:
        return None
    if len(exact) > MAX_EDITS:
        raise AlignedSpacerWithheld('Aligned spacer edit budget exceeded')
    selected = independent.preview(tree, path, catalog=catalog)
    if type(selected) is dict and selected.get('verification') == 'withheld':
        raise AlignedSpacerWithheld('Aligned spacer source preview withheld: ' + str(selected.get('reason', 'unknown')))
    if (type(selected) is not dict or selected.get('verification') !=
            'conditional-aligned-spacer-syntax-directives-and-reanalysis'
            or any(selected.get(key) is not False for key in ('runtime_binding_proved', 'execution_proved',
                'render_equivalence_proved', 'edit_permission', 'compiler_permission'))):
        raise SafetyError('Aligned spacer source preview is withheld or invalid')
    rows = selected.get('edits')
    if (type(rows) is not list or len(rows) != len(exact)
            or any(type(row) is not dict or set(row) != {'start', 'end', 'replacement', 'rule_id'}
                   or row['rule_id'] != footnote_rules.ALIGNED or row['replacement'] != '#dots_space '
                   or type(row['start']) is not int or type(row['end']) is not int for row in rows)):
        raise SafetyError('Aligned spacer edits are invalid')
    edits = tuple(Edit(row['start'], row['end'], row['replacement'].encode('utf-8')) for row in rows)
    proposal = plan(source, edits)
    if (proposal.deferred or tuple(proposal.edits) != exact or len(edits) != len(exact)
            or digest(source) != selected.get('baseline_sha256')):
        raise SafetyError('Aligned spacer edit positions or source hash changed')
    candidate = proposal.candidate()
    if digest(candidate) != selected.get('candidate_sha256'):
        raise SafetyError('Aligned spacer candidate hash changed')
    old_calls = tuple(edit.start for edit in edits)
    def require_binding(value, expected_source, expected_calls):
        if (type(value) is not dict or value.get('path') != path
                or value.get('source_sha256') != digest(expected_source)
                or value.get('offsets') != list(expected_calls)
                or any(value.get(key) is not False for key in ('runtime_binding_proved', 'execution_proved',
                       'render_equivalence_proved', 'edit_permission', 'compiler_permission'))):
            raise SafetyError('Aligned spacer binding proof is stale or invalid')
    try:
        prior_binding = binding.SpacerBindingProof.issue(evidence, path, source, old_calls)
    except SafetyError as error:
        binding.footnotes._valid(evidence)
        raise AlignedSpacerWithheld(str(error)) from error
    require_binding(prior_binding, source, old_calls)
    shift, new_calls, inserted = 0, [], []
    for edit in edits:
        position = edit.start + shift
        inserted.append(Edit(position, position + len(b'#dots_space'), b''))
        new_call = position + len(independent.INSERTION)
        if candidate[position:new_call] != independent.INSERTION or not candidate.startswith(b'#footnote[', new_call):
            raise SafetyError('Aligned spacer is not immediately before its footnote')
        new_calls.append(new_call)
        shift += len(independent.INSERTION)
    parsed = parse(candidate, tree.mode)
    if not _within_local_budget(parsed):
        raise AlignedSpacerWithheld('Aligned spacer candidate scan budget exceeded')
    if (parsed.nodes[0].errors
            or footnote_rules._signature_without_spacers(tree, ())
               != footnote_rules._signature_without_spacers(parsed, tuple(inserted))
            or _scopes(tree, catalog, edits) != _scopes(parsed, catalog)):
        raise SafetyError('Aligned spacer changed other syntax or suppression targets')
    try:
        candidate_binding = binding.SpacerBindingProof.issue(evidence, path, candidate, tuple(new_calls), require_inserted=True)
    except SafetyError as error:
        binding.footnotes._valid(evidence)
        raise AlignedSpacerWithheld(str(error)) from error
    require_binding(candidate_binding, candidate, tuple(new_calls))
    after_resolution = resolve(parsed, packages=())
    after = footnote_rules.analyze_conditional_nested(parsed, path, after_resolution, catalog=catalog)
    mapper = _FindingMapper(source)
    for rule_id in footnote_rules.RULE_IDS:
        expected = _keys(source, before[rule_id].diagnostics, edits)
        if rule_id == footnote_rules.ALIGNED:
            selected_findings = [item for item in before[rule_id].diagnostics if mapper.offset(item) in old_calls]
            if len(selected_findings) != len(edits):
                raise SafetyError('Aligned spacer has no exact active diagnostic')
            expected.subtract(_keys(source, selected_findings, edits))
        if (any(count < 0 for count in expected.values()) or +expected != _keys(candidate, after[rule_id].diagnostics)
                or _keys(source, before[rule_id].suppressed, edits) != _keys(candidate, after[rule_id].suppressed)
                or after[rule_id].parse_errors):
            raise SafetyError('Aligned spacer changed another footnote finding or suppression')
    a, b = displays(tree, path, catalog=catalog), displays(parsed, path, catalog=catalog)
    if any(not _same_findings(tree, a[rid], parsed, b[rid], edits) for rid in a):
        raise SafetyError('Aligned spacer changed display findings')
    if not _same_findings(tree, scripts(tree, path, catalog=catalog, _verify=False, _allow_code_call=True),
                          parsed, scripts(parsed, path, catalog=catalog, _verify=False, _allow_code_call=True), edits):
        raise SafetyError('Aligned spacer changed script findings')
    for check in (paragraph_rules.analyze_conditional_nested_gap, paragraph_rules.analyze_conditional_nested_target,
                  paragraph_rules.analyze_conditional_nested_capitalization, prose_marker_rules.analyze_conditional,
                  continuation_rules.analyze_conditional):
        if not _same_findings(tree, check(tree, path, catalog=catalog),
                              parsed, check(parsed, path, catalog=catalog), edits):
            raise SafetyError('Aligned spacer changed paragraph findings')
    if not _same_findings(tree, proof_rules.analyze_conditional(tree, path, resolution, catalog=catalog),
                          parsed, proof_rules.analyze_conditional(parsed, path, after_resolution, catalog=catalog), edits):
        raise SafetyError('Aligned spacer changed proof findings')
    if independent._select(parsed, after):
        raise SafetyError('Aligned spacer stage did not converge')
    step = dict(rule_id=footnote_rules.ALIGNED, baseline_sha256=digest(source), candidate_sha256=digest(candidate),
                edits=rows, conditional_footnote_calls=new_calls, conditional_aligned_spacer_calls=new_calls,
                conditions=list(independent.CONDITIONS), runtime_binding_proved=False, execution_proved=False,
                render_equivalence_proved=False, edit_permission=False, compiler_permission=False)
    return step, parsed
