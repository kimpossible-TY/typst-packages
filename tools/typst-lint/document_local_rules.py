"""Conditional, source-local hints for literal includes in a document.

Each included file is analyzed as a standalone lexical environment. Its actual
include site can supply different bindings, styles or execution context, so
these hints are not unconditional findings or edit authority.
"""
from cancel_rules import RULE_ID as CANCEL, analyze as cancel
from continuation_rules import RULE_ID as CONTINUATION, analyze_conditional as continuation
from footnote_rules import RULE_IDS as FOOTNOTES, analyze as footnotes
from paragraph_rules import RULE_IDS as PARAGRAPHS, analyze as paragraphs
from static_resolver import resolve

RULE_IDS = (*PARAGRAPHS, CONTINUATION, *FOOTNOTES, CANCEL)


def analyze(tree, path, catalog):
    resolution = resolve(tree, packages=())
    results = paragraphs(tree, path, resolution, catalog=catalog)
    results[CONTINUATION] = continuation(tree, path, catalog=catalog)
    results.update(footnotes(tree, path, resolution, resolution.spacer_proof))
    results[CANCEL] = cancel(tree, path, resolution)
    return resolution.skips, results
