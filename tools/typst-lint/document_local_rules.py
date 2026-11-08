"""Conditional, source-local hints for literal includes in a document.

Each included file is analyzed as a standalone lexical environment. Its actual
include site can supply different bindings, styles or execution context, so
these hints are not unconditional findings or edit authority.
"""
from cancel_rules import RULE_ID as CANCEL, analyze as cancel
from continuation_rules import RULE_ID as CONTINUATION, analyze_conditional as continuation
from dataclasses import replace
from footnote_rules import (RULE_IDS as FOOTNOTES, analyze as footnotes,
                            analyze_conditional_nested as nested_footnotes)
from paragraph_rules import RULE_IDS as PARAGRAPHS, analyze as paragraphs
from proof_rules import RULE_ID as PROOF, analyze_conditional as proof
from prose_marker_rules import RULE_ID as PROSE_MARKER, analyze_conditional as prose_marker
from static_resolver import resolve
from theme_rules import CONTEXT as THEME_CONTEXT, analyze_conditional as theme_context

RULE_IDS = (*PARAGRAPHS, CONTINUATION, *FOOTNOTES, CANCEL, PROOF, PROSE_MARKER, THEME_CONTEXT)


def analyze(tree, path, catalog):
    resolution = resolve(tree, packages=())
    results = paragraphs(tree, path, resolution, catalog=catalog)
    results[CONTINUATION] = continuation(tree, path, catalog=catalog)
    root_footnotes = footnotes(tree, path, resolution, resolution.spacer_proof)
    nested = nested_footnotes(tree, path, resolution, catalog=catalog)
    for rid in FOOTNOTES:
        first, second = root_footnotes[rid], nested[rid]
        results[rid] = replace(first, diagnostics=first.diagnostics + second.diagnostics, edits=(),
                               suppressed=first.suppressed + second.suppressed,
                               skips=tuple(dict.fromkeys(first.skips + second.skips)),
                               parse_errors=tuple(dict.fromkeys(first.parse_errors + second.parse_errors)))
    results[CANCEL] = cancel(tree, path, resolution)
    results[PROOF] = proof(tree, path, resolution, catalog=catalog)
    results[PROSE_MARKER] = prose_marker(tree, path, catalog=catalog)
    results[THEME_CONTEXT] = theme_context(tree, path, resolution, catalog=catalog)
    return resolution.skips, results
