"""Compose supported source rules and validate their whole in-memory candidate.

The remaining rule backends and protected write/compiler contracts are absent.
Successful verification here therefore yields partial proposals only.
"""
from collections import Counter
from dataclasses import replace

from continuation_rules import RULE_ID as CONTINUATION, analyze as continuation
from edit_plan import plan
from display_rules import RULE_IDS as DISPLAY_RULES, analyze as displays
from footnote_rules import GAP as FOOTNOTE_GAP, PUNCT as FOOTNOTE_PUNCT, ALIGNED as FOOTNOTE_ALIGNED, analyze as footnotes
from paragraph_rules import RULE_IDS as PARAGRAPH_RULES, analyze as paragraphs
from reference_rules import RULE_IDS as REFERENCE_RULES, analyze as references
from script_rule import RULE_ID as SCRIPT, analyze as scripts
from static_resolver import resolve
from syntax_client import parse
from typst_lint import SafetyError


def rule_ids(pack):
    core = (SCRIPT, *REFERENCE_RULES)
    return (*core, *PARAGRAPH_RULES, *DISPLAY_RULES, CONTINUATION,
            FOOTNOTE_GAP, FOOTNOTE_PUNCT, FOOTNOTE_ALIGNED) if pack == 'pde' else core


def _analyze(tree, path, pack, fallback_language, packages, reference_catalog):
    results = {SCRIPT: scripts(tree, path, _verify=False)}
    resolution = resolve(tree, fallback_language=fallback_language, packages=packages)
    results.update(references(tree, path, resolution, reference_catalog))
    if pack == 'pde':
        results.update(paragraphs(tree, path, resolution))
        results.update(displays(tree, path, resolution))
        results[CONTINUATION] = continuation(tree, path, resolution)
        # No trusted dots_space producer exists yet. Only register the rules
        # that can detect something without synthetic spacer provenance.
        footnote_results = footnotes(tree, path, resolution)
        results.update({rid: footnote_results[rid] for rid in (FOOTNOTE_GAP, FOOTNOTE_PUNCT, FOOTNOTE_ALIGNED)})
    return results


def _offset(source, diagnostic):
    # Diagnostics use LF line counts and Unicode character columns, not bytes.
    lines = source.split(b'\n')
    start = sum(len(line) + 1 for line in lines[:diagnostic.line - 1])
    return start + len(lines[diagnostic.line - 1].decode('utf-8')[:diagnostic.column - 1].encode('utf-8'))


def _findings(source, results, ignored, transform=lambda position: position, *, suppressed=False):
    findings = Counter()
    for rid, result in results.items():
        if suppressed or rid not in ignored:
            for finding in result.suppressed if suppressed else result.diagnostics:
                findings[(rid, finding.explanation, transform(_offset(source, finding)))] += 1
    return findings


def analyze(tree, path, *, pack='core', fallback_language=None, packages=(), reference_catalog=None,
            ignored=frozenset(), _verify=True):
    """Filter config-ignored edits before verifying one composed candidate.

Baseline diagnostic offsets are mapped through every proposed edit before
comparison, so an existing unresolved finding is not mistaken for a new one
merely because an earlier script or blank line changed its coordinates.
"""
    results = _analyze(tree, path, pack, fallback_language, packages, reference_catalog)
    for rid in ignored:
        if rid in results:
            results[rid] = replace(results[rid], edits=())
    proposals = tuple(edit for result in results.values() for edit in result.edits)
    if not proposals or not _verify:
        return results
    reason = None
    try:
        planned = plan(tree.source, proposals)
        if planned.deferred:
            raise SafetyError('Conflicting supported-rule edits')
        candidate = planned.candidate()
        checked = parse(candidate, tree.mode)
        if checked.nodes[0].errors:
            raise SafetyError('Combined candidate failed to parse')
        final = analyze(checked, path, pack=pack, fallback_language=fallback_language,
                        packages=packages, reference_catalog=reference_catalog, ignored=ignored, _verify=False)
        if any(result.edits for result in final.values()):
            raise SafetyError('Combined candidate has second-pass edits')

        def mapped(position):
            return position + sum(len(edit.replacement) - (edit.end - edit.start)
                                  for edit in planned.edits if edit.end <= position)

        before = _findings(tree.source, results, ignored, mapped)
        after = _findings(candidate, final, ignored)
        if after - before:
            raise SafetyError('Combined candidate introduces a new finding')
        if _findings(tree.source, results, ignored, mapped, suppressed=True) != _findings(
                candidate, final, ignored, suppressed=True):
            raise SafetyError('Combined candidate changed directive suppression ownership')
        # Reparse and rerun independently rather than trust retained tree state.
        again = analyze(parse(candidate, tree.mode), path, pack=pack,
                        fallback_language=fallback_language, packages=packages, reference_catalog=reference_catalog,
                        ignored=ignored, _verify=False)
        if again != final:
            raise SafetyError('Combined candidate is not deterministic/idempotent')
    except SafetyError as error:
        reason = f'{error}; no combined edits proposed'
    if reason:
        return {rid: replace(result, edits=(), skips=(*result.skips, reason)) for rid, result in results.items()}
    return results
