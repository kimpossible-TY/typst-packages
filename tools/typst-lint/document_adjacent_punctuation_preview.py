"""Conditional punctuation moves around adjacent prose footnotes, source only."""
from document_adjacent_footnote_punctuation import analyze as analyze_adjacent
from document_conditional_continuation import _nontext_signature
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

RULE = footnote_rules.PUNCT
CONDITIONS = (
    'The footnote resolves to the intended builtin and executes as prose.',
    'The adjacent period or comma belongs to the prose before this footnote.',
    'Moving punctuation may change the rendered result; execution and equivalence are unproved.',
    'The insertion and deletion in each punctuation move are inseparable.',
)


def _select(tree, path, records):
    details = analyze_adjacent(tree, path, catalog=records)
    if details['status'] == 'WITHHELD':
        raise SafetyError('Adjacent footnote inspection was withheld')
    source = tree.source
    edits, selected = [], []
    for item in details['diagnostics']:
        start, end = item['start'], item['end']
        punctuation = source[end:end + 1]
        if (punctuation not in (b'.', b',') or item['punctuation_start'] != end
                or item['punctuation_end'] != end + 1
                or source[start:start + len(b'#footnote[')] != b'#footnote['):
            raise SafetyError('Adjacent punctuation evidence no longer matches source bytes')
        edits.extend((Edit(start, start, punctuation), Edit(end, end + 1, b'')))
        selected.append(start)
    return tuple(edits), tuple(selected)


def preview(tree, path, *, catalog=None):
    """Move only existing punctuation; preserve the entire original footnote bytes."""
    records = load_rules()[0]['rules'] if catalog is None else catalog
    source = tree.source
    empty = dict(path=path, baseline_sha256=digest(source), candidate_sha256=None,
                 conditions=CONDITIONS, runtime_binding_proved=False, execution_proved=False,
                 render_equivalence_proved=False, edit_permission=False, compiler_permission=False,
                 atomic_edits=True, edits=[])
    try:
        if tree.mode != 'markup' or tree.nodes[0].errors:
            raise SafetyError('Requires well-formed markup source')
        edits, targets = _select(tree, path, records)
        if not edits:
            return None
        proposal = plan(source, edits)
        if proposal.deferred or len(proposal.edits) != len(edits) or len(edits) != 2 * len(targets):
            raise SafetyError('Conflicting adjacent punctuation edits')
        before_resolution = resolve(tree, packages=())
        before = footnote_rules.analyze_conditional_nested(tree, path, before_resolution, catalog=records)
        candidate = proposal.candidate()
        parsed = parse(candidate, tree.mode)
        if (parsed.nodes[0].errors or _nontext_signature(tree) != _nontext_signature(parsed)
                or _scopes(tree, records, proposal.edits) != _scopes(parsed, records)):
            raise SafetyError('Punctuation move changes non-text syntax or suppression targets')
        after_resolution = resolve(parsed, packages=())
        after = footnote_rules.analyze_conditional_nested(parsed, path, after_resolution, catalog=records)
        mapper = _FindingMapper(source)
        for rule_id in footnote_rules.RULE_IDS:
            expected = _keys(source, before[rule_id].diagnostics, proposal.edits)
            if rule_id == RULE:
                selected = [item for item in before[rule_id].diagnostics if mapper.offset(item) in targets]
                if len(selected) != len(targets):
                    raise SafetyError('Each punctuation move must match an active footnote finding')
                expected.subtract(_keys(source, selected, proposal.edits))
            if (any(count < 0 for count in expected.values())
                    or +expected != _keys(candidate, after[rule_id].diagnostics)
                    or _keys(source, before[rule_id].suppressed, proposal.edits)
                    != _keys(candidate, after[rule_id].suppressed) or after[rule_id].parse_errors):
                raise SafetyError('Punctuation move changed other footnote findings or suppressions')
        displays_before, displays_after = (analyze_displays(tree, path, catalog=records),
                                           analyze_displays(parsed, path, catalog=records))
        if any(not _same_findings(tree, displays_before[rid], parsed, displays_after[rid], proposal.edits)
               for rid in displays_before):
            raise SafetyError('Punctuation move changed display findings')
        if not _same_findings(tree, analyze_scripts(tree, path, catalog=records, _verify=False, _allow_code_call=True),
                              parsed, analyze_scripts(parsed, path, catalog=records, _verify=False, _allow_code_call=True),
                              proposal.edits):
            raise SafetyError('Punctuation move changed script findings')
        for analyzer in (paragraph_rules.analyze_conditional_nested_gap,
                         paragraph_rules.analyze_conditional_nested_target,
                         paragraph_rules.analyze_conditional_nested_capitalization,
                         prose_marker_rules.analyze_conditional, continuation_rules.analyze_conditional):
            if not _same_findings(tree, analyzer(tree, path, catalog=records),
                                  parsed, analyzer(parsed, path, catalog=records), proposal.edits):
                raise SafetyError('Punctuation move changed paragraph findings')
        if not _same_findings(tree, proof_rules.analyze_conditional(tree, path, before_resolution, catalog=records),
                              parsed, proof_rules.analyze_conditional(parsed, path, after_resolution, catalog=records),
                              proposal.edits):
            raise SafetyError('Punctuation move changed proof findings')
        if _select(parsed, path, records)[0]:
            raise SafetyError('Conditional adjacent punctuation preview did not converge')
        return dict(empty, candidate_sha256=digest(candidate),
                    verification='conditional-adjacent-punctuation-syntax-directives-and-reanalysis',
                    edits=[dict(start=edit.start, end=edit.end, replacement=edit.replacement.decode(), rule_id=RULE)
                           for edit in proposal.edits])
    except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
        return dict(empty, verification='withheld', reason=str(error))
