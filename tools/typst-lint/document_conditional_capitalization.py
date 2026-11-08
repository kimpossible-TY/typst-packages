"""Independent, source-only proposals for conditional nested capitalization.

The source hint proves neither the marker's runtime identity nor the language
or execution of the paragraph. These proposals never authorize a source write.
"""
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

RULE = paragraph_rules.CAPITALIZATION
CONDITIONS = (
    'The source paragraph_tab resolves to the intended paragraph marker at runtime.',
    'The selected paragraph is ordinary English prose and this content executes.',
    'Source capitalization changes; rendered text may change depending on marker and show behavior.',
)


def preview(tree, path, *, catalog=None):
    """Propose only one-byte capitalization for existing unsuppressed hints."""
    records = load_rules()[0]['rules'] if catalog is None else catalog
    source = tree.source
    empty = dict(path=path, baseline_sha256=digest(source), candidate_sha256=None,
                 conditions=CONDITIONS, execution_proved=False, runtime_binding_proved=False,
                 render_equivalence_proved=False, edit_permission=False, compiler_permission=False, edits=[])
    try:
        if tree.mode != 'markup' or tree.nodes[0].errors:
            raise SafetyError('Requires well-formed markup source')
        before = paragraph_rules.analyze_conditional_nested_capitalization(tree, path, catalog=records)
        mapper = _FindingMapper(source)
        edits = []
        for finding in before.diagnostics:
            offset = mapper.offset(finding)
            initial = source[offset:offset + 1]
            if not (b'a' <= initial <= b'z') or len(initial) != 1:
                raise SafetyError('Conditional capitalization location is not a lowercase ASCII byte')
            edits.append(Edit(offset, offset + 1, initial.upper()))
        if not edits:
            return None
        proposal = plan(source, edits)
        if proposal.deferred or len(proposal.edits) != len(edits):
            raise SafetyError('Conflicting capitalization edits')
        candidate = proposal.candidate()
        parsed = parse(candidate, tree.mode)
        if (parsed.nodes[0].errors or _nontext_signature(tree) != _nontext_signature(parsed)
                or _scopes(tree, records, proposal.edits) != _scopes(parsed, records)):
            raise SafetyError('Capitalization preview changed non-text syntax or suppression targets')
        after = paragraph_rules.analyze_conditional_nested_capitalization(parsed, path, catalog=records)
        if (after.diagnostics or after.parse_errors
                or _keys(source, before.suppressed, proposal.edits) != _keys(candidate, after.suppressed)):
            raise SafetyError('Capitalization preview changed suppressed findings or did not converge')
        before_resolution, after_resolution = resolve(tree, packages=()), resolve(parsed, packages=())
        displays_before = analyze_displays(tree, path, catalog=records)
        displays_after = analyze_displays(parsed, path, catalog=records)
        if any(not _same_findings(tree, displays_before[rule], parsed, displays_after[rule], proposal.edits)
               for rule in displays_before):
            raise SafetyError('Capitalization preview changed display findings or suppressions')
        scripts_before = analyze_scripts(tree, path, catalog=records, _verify=False, _allow_code_call=True)
        scripts_after = analyze_scripts(parsed, path, catalog=records, _verify=False, _allow_code_call=True)
        if not _same_findings(tree, scripts_before, parsed, scripts_after, proposal.edits):
            raise SafetyError('Capitalization preview changed script findings or suppressions')
        footnotes_before = footnote_rules.analyze_conditional_nested(tree, path, before_resolution, catalog=records)
        footnotes_after = footnote_rules.analyze_conditional_nested(parsed, path, after_resolution, catalog=records)
        for rule in footnote_rules.RULE_IDS:
            if not _same_findings(tree, footnotes_before[rule], parsed, footnotes_after[rule], proposal.edits):
                raise SafetyError('Capitalization preview changed footnote findings or suppressions')
        for analyzer in (paragraph_rules.analyze_conditional_nested_gap,
                         paragraph_rules.analyze_conditional_nested_target,
                         prose_marker_rules.analyze_conditional,
                         continuation_rules.analyze_conditional):
            if not _same_findings(tree, analyzer(tree, path, catalog=records),
                                  parsed, analyzer(parsed, path, catalog=records), proposal.edits):
                raise SafetyError('Capitalization preview changed other findings or suppressions')
        if not _same_findings(tree, proof_rules.analyze_conditional(tree, path, before_resolution, catalog=records),
                              parsed, proof_rules.analyze_conditional(parsed, path, after_resolution, catalog=records),
                              proposal.edits):
            raise SafetyError('Capitalization preview changed proof findings or suppressions')
        return dict(empty, candidate_sha256=digest(candidate),
                    verification='conditional-capitalization-syntax-directives-and-reanalysis',
                    edits=[dict(start=edit.start, end=edit.end,
                                replacement=edit.replacement.decode('ascii'), rule_id=RULE)
                           for edit in proposal.edits])
    except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
        return dict(empty, verification='withheld', reason=str(error))
