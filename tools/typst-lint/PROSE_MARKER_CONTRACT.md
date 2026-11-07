# Partial prose paragraph marker contract

`prose_marker_rules.analyze(tree, path, resolution)` implements bounded
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
raw content, call, footnote or other output construct is crossed to infer a
literal paragraph from its final punctuation. Unknown ownership remains
unsupported. Document start, source-line continuations, headings, lists,
displays, figures and proof/theorem output do not imply a new prose paragraph.
Display-to-prose gaps remain owned by `pde.display-continuation-gap`. Blocks,
explicit `parbreak` calls, footnote/break composition and nested bodies remain
outside this slice. Existing marked paragraphs receive no duplicate finding.

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
establish that identity. Aliased/module access to the show function can prove
its application. An alias restored to the canonical marker spelling can prove
availability. Alias-only marker availability cannot justify inventing a new
spelling or import. The installed show captures its function value; rebinding
its alias later does not uninstall it. Marker shadowing/mutation, unknown
wildcards, unknown shows, deferred execution and absent/altered package evidence
cannot grant insertion proof. A show appearing later does not apply backward.

A proven source boundary without insertion availability remains diagnostic
with `fix_unavailable`. If unknown effects also prevent ordinary prose proof,
applicability is unsupported rather than a semantic clean result.

## Exact insertion and bounded composition

Insert only `#paragraph_tab` followed by the preceding physical line's LF or
CRLF, at the original right paragraph's physical line start. Preserve the
entire existing blank gap, the prose line's original indentation and every
original source byte. Never introduce an import, show, paragraph break,
capitalization change or synthetic idea boundary.

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
change effective language. Initially, insertion is proposed only when the
evaluator returns `already-uppercase`: explicit effective English, a complete
uppercase starter from its exact existing allowlist, and its exact source-gap
requirements. This avoids unlocking a new capitalization repair or a new
applicability skip under the current strict whole-candidate guards.

Thus `This consequence follows.` can be repaired; normative `Second idea.`,
lowercase `this`, protected names, unknown/non-English language and unsupported
initials remain diagnosed with explicit repair refusal. This restriction is a
composition limit, not a new style policy or permission to capitalize names.

The shared source analyzer still reparses and rechecks all wired rules, rejects
new findings/refusals or second-pass edits, preserves suppressed findings and
requires deterministic results. Conflicts with other proposals refuse the
combined proposal rather than weakening those checks. The rule and complete
linter acceptance both remain PARTIAL.

## Verification

Focused tests use the pinned syntax adapter and immutable reviewed sources.
They cover show ordering/capture, aliases, canonical availability, shadowing,
mutation, source replacement, stale proofs, full paragraph classification,
suppression targets, exact bytes/newlines/BOM, casing refusals, repeated
analysis and script/display/footnote composition. Real CLI tests verify check,
dry-run, fix and compile interfaces remain read-only with exit 2.

The opt-in rendering fixture applies an actual CLI proposal only to a temporary
test source. It supplies test-owned text-utils package copies and an explicitly
empty cache to Typst 0.15.1. It verifies unchanged word order/case, a 1.5em
indentation change, unchanged package bytes, no downloads and zero second-run
proposals. No existing consumer or installed package is changed.
