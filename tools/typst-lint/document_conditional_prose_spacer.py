"""Independent, source-only conditional previews for prose footnote spacers."""
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

RULE = footnote_rules.PROSE
CONDITIONS = (
    'The direct dots_space and footnote names have their intended bindings at runtime.',
    'The selected content executes as ordinary prose, with the punctuation and footnote adjacent.',
    'Runtime sources and styles match the intended snapshot; rendering equivalence is not proved.',
)


def _select(tree, before):
    """Select diagnosed nested markup with literal punctuation and horizontal separators."""
    source, nodes = tree.source, tree.nodes
    mapper = _FindingMapper(source)
    active = {mapper.offset(item) for item in before[RULE].diagnostics}
    edits = []
    calls = []
    for body in nodes:
        if body.kind != 'Markup':
            continue
        siblings = body.children
        for position in range(1, len(siblings) - 3):
            left, mark, name, gap, callmark = (nodes[index] for index in
                                                siblings[position - 1:position + 4])
            if (left.kind != 'Text' or source[left.end - 1:left.end] not in (b'.', b',')
                    or mark.kind != 'Hash' or name.kind != 'Ident'
                    or source[mark.start:name.end] != b'#dots_space'
                    or left.end != mark.start or mark.end != name.start
                    or gap.kind != 'Space' or name.end != gap.start or gap.end != callmark.start
                    or source[gap.start:gap.end].strip(b' \t') != b''
                    or callmark.kind != 'Hash' or mark.start not in active):
                continue
            if position + 4 >= len(siblings):
                continue
            call = nodes[siblings[position + 4]]
            if (call.kind != 'FuncCall' or callmark.end != call.start
                    or not source[callmark.start:call.end].startswith(b'#footnote[')):
                continue
            edits.append(Edit(mark.start, callmark.start, b''))
            calls.append(callmark.start)
    return tuple(edits), tuple(calls)


def preview(tree, path, *, catalog=None):
    """Build a bounded preview; no runtime, compilation or source-write authority follows."""
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
        edits, calls = _select(tree, before)
        if not edits:
            return None
        proposal = plan(source, edits)
        if proposal.deferred or len(proposal.edits) != len(edits):
            raise SafetyError('Conflicting conditional prose spacer edits')
        candidate = proposal.candidate()
        parsed = parse(candidate, tree.mode)
        tokens = tuple(Edit(edit.start, edit.start + len(b'#dots_space'), b'') for edit in proposal.edits)
        if (parsed.nodes[0].errors
                or footnote_rules._signature_without_spacers(tree, tokens)
                != footnote_rules._signature_without_spacers(parsed, ())
                or _scopes(tree, records, proposal.edits) != _scopes(parsed, records)):
            raise SafetyError('Spacer removal changes other syntax or suppression targets')
        after_resolution = resolve(parsed, packages=())
        after = footnote_rules.analyze_conditional_nested(parsed, path, after_resolution, catalog=records)
        mapper = _FindingMapper(source)
        for rule_id in footnote_rules.RULE_IDS:
            expected = _keys(source, before[rule_id].diagnostics, proposal.edits)
            if rule_id == RULE:
                selected = [item for item in before[rule_id].diagnostics
                            if mapper.offset(item) in {edit.start for edit in proposal.edits}]
                if len(selected) != len(edits):
                    raise SafetyError('Each spacer must match one active diagnostic')
                expected.subtract(_keys(source, selected, proposal.edits))
            elif rule_id == footnote_rules.PUNCT:
                # The pre-existing punctuation becomes directly adjacent to this call.
                selected = [item for item in before[rule_id].diagnostics if mapper.offset(item) in calls]
                if len(selected) != len(edits):
                    raise SafetyError('Footnote punctuation finding is absent or suppressed')
                expected.subtract(_keys(source, selected, proposal.edits))
            if (any(count < 0 for count in expected.values())
                    or +expected != _keys(candidate, after[rule_id].diagnostics)
                    or _keys(source, before[rule_id].suppressed, proposal.edits)
                    != _keys(candidate, after[rule_id].suppressed)
                    or after[rule_id].parse_errors):
                raise SafetyError('Spacer removal changed another finding or suppression')
        displays_before = analyze_displays(tree, path, catalog=records)
        displays_after = analyze_displays(parsed, path, catalog=records)
        if any(not _same_findings(tree, displays_before[rid], parsed, displays_after[rid], proposal.edits)
               for rid in displays_before):
            raise SafetyError('Spacer removal changed display findings')
        if not _same_findings(tree, analyze_scripts(tree, path, catalog=records, _verify=False, _allow_code_call=True),
                              parsed, analyze_scripts(parsed, path, catalog=records, _verify=False, _allow_code_call=True),
                              proposal.edits):
            raise SafetyError('Spacer removal changed script findings')
        for analyzer in (paragraph_rules.analyze_conditional_nested_gap,
                         paragraph_rules.analyze_conditional_nested_target,
                         paragraph_rules.analyze_conditional_nested_capitalization,
                         prose_marker_rules.analyze_conditional, continuation_rules.analyze_conditional):
            if not _same_findings(tree, analyzer(tree, path, catalog=records),
                                  parsed, analyzer(parsed, path, catalog=records), proposal.edits):
                raise SafetyError('Spacer removal changed paragraph findings')
        if not _same_findings(tree, proof_rules.analyze_conditional(tree, path, before_resolution, catalog=records),
                              parsed, proof_rules.analyze_conditional(parsed, path, after_resolution, catalog=records),
                              proposal.edits):
            raise SafetyError('Spacer removal changed proof findings')
        if _select(parsed, after)[0]:
            raise SafetyError('Conditional prose spacer preview did not converge')
        return dict(empty, candidate_sha256=digest(candidate),
                    verification='conditional-prose-spacer-syntax-directives-and-reanalysis',
                    edits=[dict(start=edit.start, end=edit.end, replacement='', rule_id=RULE)
                           for edit in proposal.edits])
    except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
        return dict(empty, verification='withheld', reason=str(error))
