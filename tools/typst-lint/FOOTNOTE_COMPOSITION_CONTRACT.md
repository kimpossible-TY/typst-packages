# Literal prose footnote paragraph composition

This is a partial source-rule extension. It does not open the production source
writer, transactions, compiler, installed-package updates, graph completeness,
or consumer-document rollout. `source_analysis.py` and
`candidate_convergence.py` are unchanged. Every accepted composed proposal still
requires actual parsing and resolution, source-owned edit attribution, retained
coverage, unchanged suppression ownership, no new final findings/refusals, and
an independent zero-edit rerun.

## Policy ownership

For an existing reviewed next-paragraph marker, `pde.footnote-trailing-gap`
owns only insertion of `#parbreak()` on the source line following the footnote:

```typst
First idea.#footnote[Reason.]

#paragraph_tab
This holds.
```

becomes:

```typst
First idea.#footnote[Reason.]
#parbreak()

#paragraph_tab
This holds.
```

For a missing marker, `pde.prose-paragraph-marker` owns the complete policy
repair: break insertion and marker insertion. GAP retains its diagnostic but
proposes no edit. Ignoring MARKER therefore cannot leave a GAP half repair;
ignoring GAP still permits MARKER's complete policy repair. The two insertions
are never attributed to GAP merely because its finding disappears.

```typst
First idea.#footnote[Reason.]

this consequence follows.
```

The first genuine parser/resolver/rule pass produces only:

```typst
First idea.#footnote[Reason.]
#parbreak()

#paragraph_tab
this consequence follows.
```

The existing capitalization backend, on a real later pass, separately proposes
`this` to `This`. The marker backend's casing preflight does not return that
repair or fabricate later-rule attribution. Default direct marker analysis
still refuses lowercase when follow-up casing has not been explicitly enabled.
The shared dispatcher enables its existing verified convergence path.

## Shared boundary proof

`prose_boundary.py` certifies only a narrow direct-root source form:

- The complete preceding ordinary paragraph is literal Text/Space, optionally
  with the existing standalone reviewed marker and, for the first paragraph,
  the already-supported standalone nonoutput setup prefix.
- Its final expression is a direct builtin literal-form `#footnote[...]` with
  actual `Ident`, `Args`, `ContentBlock`, bracket and markup syntax. The footnote
  markup contains only Text/Space/Parbreak. Comments and executable or styled
  nested content do not inherit this proof.
- The closing bracket ends its source line. An existing LF or CRLF blank
  boundary contains only whitespace. All original blank lines and indentation
  remain unchanged.
- The complete following paragraph is ordinary literal Text/Space, optionally
  prefixed by exactly one standalone canonical `#paragraph_tab` resolving to
  the reviewed immutable text-utils export.
- No comments, directives, setup, structural output, nested bodies, calls, or
  mutable bindings intervene. A single preexisting canonical builtin
  `#parbreak()` on the next line may be preserved; it receives no extra break.
  Multiple intervening breaks and noncanonical spellings are refused, never
  deduplicated.

`ParagraphInsertionProof` remains a frozen source-bound resolver contract.
Its existing `available` set names root Text IDs where the actual canonical
reviewed marker and already-active reviewed paragraph show are proven. New
`parbreak_available`, defaulting to an empty frozenset, additionally names those
same target IDs where canonical `parbreak` resolves to the builtin. Invalid,
stale, nonimmutable or out-of-range evidence yields no repair. Only the shared
whitespace/immutable-marker boundary transports that lexical availability to
the earlier break insertion coordinate. No import or show is created.

An existing marked paragraph gains no language or initial-word restrictions.
Existing casing/language refusals must remain unchanged after offset mapping.
A new marker reuses `_casing_eligibility` and the current English/editorial
allowlist unchanged. `Second`, unsafe/protected starters, unknown/non-English
language, shadowed `parbreak`, unknown shows and unsafe body ownership remain
unsupported. Other rule-owned fixes remain independently verified.

## Canonical builtin break preservation

The resolver recognizes only a standalone direct-root `#parbreak()` whose
binding is the actual builtin, spelling is exact, and `FuncCall` has `Ident`
and `Args` containing exactly LeftParen/RightParen. Arguments, argument trivia,
options, aliases, inline calls, comments on the line, nesting and deferred
execution are not this exception. Surrounding horizontal line indentation is
allowed. Recognizing the call preserves current state and never restores safe
facts after an unknown show/output effect.

The marker backend reparses its full insertion batch once, checks all original
root Text spans and directive targets, and verifies each typed insertion as an
actual root marker expression or canonical empty break call. The main composer
then performs its unchanged whole-candidate checks.

## Evidence and limits

`tests/test_footnote_composition.py` exercises actual syntax and reviewed package
snapshots: ownership, real follow-up casing, ignores, LF/CRLF/BOM/indentation,
existing marked language/initial refusals, absent/stale/shadowed proofs,
canonical root break facts, existing/duplicate break refusal, structural and
comment/directive exclusions, candidate reinterpretation, and zero-edit final
analysis. Two legacy negative expectations now admit only this new supported
literal-footnote subset; their other structural cases remain unchanged.

`tests/test_footnote_composition_render.py` is opt-in isolated Typst 0.15.1
render evidence. It loads explicit reviewed package bytes into a temporary
package tree and uses an empty cache, never installs packages or downloads:

- Marked break insertion preserves every extracted word and bounding box,
  including footnote placement.
- New marker insertion shifts the following paragraph from x=30 to x=48pt at
  12pt text, exactly 1.5em, with vertical and footnote placement unchanged.
- The marker-only stage stays lowercase. Only the genuine capitalization pass
  visibly changes it.
- Consecutive breaks happen to render equally under the tested default, but
  `#show parbreak: [BREAK]` visibly produces `BREAKBREAK`. This is not evidence
  for generally collapsing explicit breaks or bypassing unknown-show refusals.

All coverage remains partial and `Resolution.complete` remains false.
