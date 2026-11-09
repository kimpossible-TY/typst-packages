"""Verify independent, read-only previews of standalone display edits."""
from document_display import RULE_ID, STANDALONE, analyze_all, preview_standalone_edits
from document_gap_preview import _scopes, _syntax_signature
from edit_plan import plan
from syntax_client import parse
from typst_lint import InputError, SafetyError, digest


def preview(tree, path, catalog, *, baseline_displays=None):
    """Produce an independently verified source candidate without writing it."""
    result = preview_standalone_edits(tree, path, catalog=catalog)
    if not result.edits:
        return None
    try:
        candidate_plan = plan(tree.source, result.edits)
        if candidate_plan.deferred or len(candidate_plan.edits) != len(result.edits):
            raise SafetyError('Overlapping or ineffective standalone proposals')
        candidate = candidate_plan.candidate()
        parsed = parse(candidate, tree.mode)
        if parsed.nodes[0].errors or _syntax_signature(tree) != _syntax_signature(parsed):
            raise SafetyError('Candidate changes non-whitespace syntax or fails to parse')
        if _scopes(tree, catalog, candidate_plan.edits) != _scopes(parsed, catalog):
            raise SafetyError('Candidate changes an inline directive target')
        original = baseline_displays if baseline_displays is not None else analyze_all(tree, path, catalog=catalog)
        final = analyze_all(parsed, path, catalog=catalog)
        if (final[STANDALONE].diagnostics or
                len(final[STANDALONE].suppressed) != len(original[STANDALONE].suppressed) or
                len(final[RULE_ID].suppressed) != len(original[RULE_ID].suppressed)):
            raise SafetyError('Candidate changes suppression or fails standalone reanalysis')
        # A newly standalone display can legitimately expose a subsequent gap.
        return dict(path=path, baseline_sha256=digest(tree.source), candidate_sha256=digest(candidate),
                    verification='parse-syntax-directives-and-display-reanalysis',
                    candidate_gap_diagnostics=len(final[RULE_ID].diagnostics),
                    edits=[dict(start=e.start, end=e.end, replacement=e.replacement.decode('utf-8'),
                                rule_id=STANDALONE) for e in candidate_plan.edits])
    except (InputError, SafetyError, OSError, ValueError) as error:
        return dict(path=path, baseline_sha256=digest(tree.source), candidate_sha256=None,
                    verification='withheld', edits=[], reason=str(error))
