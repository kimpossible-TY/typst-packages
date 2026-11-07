# Partial prose paragraph marker contract

`prose_marker_rules.analyze(tree, path, resolution, *,
allow_followup_casing=False)` implements bounded
`pde.prose-paragraph-marker` findings and in-memory insertion proposals. It
never changes files, installs dependencies or invokes a compiler. Production
source writes and compiler dispatch remain closed.

## Existing source boundaries

The supported boundary is an existing whitespace-only root `Parbreak` between
two complete literal prose paragraphs. Each adjacent paragraph is inspected up
to its bounding root `Parbreak`, start or end. Only `Text` and `Space` are
accepted. The first root paragraph may begin with a BOM and standalone
Hash/expression pairs for nonoutput imports, definitions, sets and shows before
its literal text. Existing resolver facts must certify its ordinary prose.
The preceding prose paragraph may have one exact reviewed standalone canonical
marker prefix. This keeps an existing later boundary/refusal visible when an
earlier paragraph receives its marker. No other inline output prefix is allowed.

No comment, directive, inline equation, strong/emphasized content, reference,
raw content, general call or other output construct is crossed to infer a
literal paragraph from its final punctuation. The separately proven terminal
literal-footnote boundary below is the only added call exception. Unknown ownership remains
unsupported. Document start, source-line continuations, headings, lists,
displays, figures and proof/theorem output do not imply a new prose paragraph.
Display-to-prose gaps remain owned by `pde.display-continuation-gap`. Blocks,
general explicit break/call boundaries and nested bodies remain outside this
slice. Existing marked paragraphs receive no duplicate marker finding.

A complete ordinary root paragraph ending in a direct builtin literal-content
footnote can use an existing whitespace-only blank boundary when both reviewed
marker/show ownership and canonical builtin break availability are proven.
The marker rule owns the whole next-line break plus marker insertion for an
unmarked target. An already marked target instead belongs to the footnote gap
rule. No comments/directives or arbitrary calls are crossed, and an existing
break is never duplicated. See
[FOOTNOTE_COMPOSITION_CONTRACT.md](FOOTNOTE_COMPOSITION_CONTRACT.md).

## Source-bound insertion proof

The resolver produces immutable `ParagraphInsertionProof` evidence containing
the input SHA-256 and root Text IDs where both conditions hold at that lexical
point:

- Canonical `paragraph_tab` resolves to the exact reviewed text-utils 0.1.2
  value, with source SHA-256
  `7e151150dac47b01ebf7706457906133d74a52abee1b5c43ce824ff89279bd71`.
- The exact reviewed `apply-paragraph-tabs` show function has already been
  applied in the same root body, and unknown effects have not invalidated
  prose/style ownership.

Only explicit package snapshots supplied through the existing safe loader
establish that identity. A captured, completely accounted named local facade
may forward it under [FACADE_PROVENANCE_CONTRACT.md](FACADE_PROVENANCE_CONTRACT.md);
this does not authorize unknown package initializers or wildcard namespaces. Aliased/module access to the show function can prove
its application. An alias restored to the canonical marker spelling can prove
availability. Alias-only marker availability cannot justify inventing a new
spelling or import. The installed show captures its function value; rebinding
its alias later does not uninstall it. Marker shadowing/mutation, unknown
wildcards, unknown shows, deferred execution and absent/altered package evidence
cannot grant insertion proof. A show appearing later does not apply backward.

The default-empty `parbreak_available` proof set additionally records only
root target Text IDs with canonical builtin `parbreak` availability. Only an
exact standalone root zero-argument `#parbreak()` preserves the resolver state;
it never restores safety after unknown shows/output. Aliases, options, argument
trivia and nested/deferred calls receive no transparent-break assumption.

A proven source boundary without insertion availability remains diagnostic
with `fix_unavailable`. If unknown effects also prevent ordinary prose proof,
applicability is unsupported rather than a semantic clean result.

## Exact insertion and bounded composition

Insert only `#paragraph_tab` followed by the preceding physical line's LF or
CRLF, at the original right paragraph's physical line start. Preserve the
entire existing blank gap, the prose line's original indentation and every
original source byte. Never introduce an import, show, capitalization change
or synthetic idea boundary. Only the proven terminal-footnote case also inserts
the exact canonical next-line `#parbreak()` required by its existing boundary.

Eligible insertions are collected using a bounded source-extracted context for
the existing pure casing evaluator, then the complete batch is reparsed once.
Offset mapping uses ordered insertion positions; original/candidate Text spans
are matched in source order rather than by repeated whole-tree searches.
Every original root Text span must remain direct root
Text. Every existing suppression scope retains its rules, reason, directive
and target byte span after insertion mapping, even when it currently suppresses
no diagnostic. Any scope targeting the insertion's right prose refuses the
repair regardless of its rule IDs. Comments/directives in either adjacent
paragraph are already excluded from this literal subset.

The separate capitalization policy is reused through its pure evaluator, not
copied or weakened. The original parser and insertion/language proofs certify
the local marker/first-word preflight; the final real parse verifies its syntax.
The immutable marker value cannot
change effective language. By default, insertion is proposed only when the
pure evaluator returns `already-uppercase`: explicit effective English, a
complete uppercase starter from its exact existing allowlist, and its exact
source-gap requirements. Existing direct callers retain that behavior.

The explicit `allow_followup_casing=True` opt-in also admits an evaluator result
with its actual diagnostic, no skip, and exactly one replacement of the first
lowercase ASCII source byte by its uppercase byte. The marker backend still
returns only insertion. Its preflight neither returns the capitalization edit
nor claims a subsequent parser/resolver pass has occurred. No new word list,
language policy, protected-name recasing or broad ownership inference is added.
The original complete paragraphs, package value/show proof, literal Text spans
and directive targets remain required and verified exactly as above.

Thus `This consequence follows.` is eligible by default. Opt-in composition
can also insert before `this consequence follows.`, preserving that lowercase
byte until a real next pass. Normative `Second idea.`, protected names such as
`iPhone` and `eBay`, mixed case, unknown/non-English language and unsupported
initials remain diagnosed with explicit repair refusal. This restriction is a
composition limit, not a new style policy or permission to capitalize names.

The shared source analyzer opts into the preflight and then reparses, resolves
and runs the actual paragraph backend on each candidate. Only its real
capitalization result can supply the second-pass byte edit. Bounded convergence
retains each pass's actual rule contributors and exact source replay, preserves
suppressed findings and directive ownership, and requires independent final
zero-edit reanalysis. A new finding must be resolved by the final candidate;
new refusals, changed directive targets, nonconvergence or conflicting edits
without provable progress refuse the combined proposal. Config ignores remain
in effect in every pass: ignoring capitalization can permit marker-only
insertion without recasing the intentionally ignored word. The rule and
complete linter acceptance both remain PARTIAL.

## Verification

Focused tests use the pinned syntax adapter and immutable reviewed sources.
They cover show ordering/capture, aliases, canonical availability, shadowing,
mutation, source replacement, stale proofs, full paragraph classification,
suppression targets, exact bytes/newlines/BOM, casing refusals, repeated
analysis and script/display/footnote composition. `test_marker_followup.py`
adds all 26 lowercase starters, authentic subsequent capitalization proposals,
two-pass source replay/attribution, unchanged default and uppercase behavior,
source/fallback languages, protected names, inline/config ignores, immutable
and stale provenance, candidate Text reinterpretation, BOM/CRLF and exact
source bytes. Real CLI tests verify check,
dry-run, fix and compile interfaces remain read-only with exit 2.

The opt-in rendering fixture applies an actual CLI proposal only to a temporary
test source. It supplies test-owned text-utils package copies and an explicitly
empty cache to Typst 0.15.1. It verifies unchanged word order/case, a 1.5em
indentation change, unchanged package bytes, no downloads and zero second-run
proposals. No existing consumer or installed package is changed.
