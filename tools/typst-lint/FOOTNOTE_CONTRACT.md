# Footnote rule integration contract

`footnote_rules.py` is a partial backend. `source_analysis` registers only its
gap, punctuation, and aligned-spacer checks, without a spacer proof. The
unaligned and prose spacer rules remain unregistered until provenance exists.
It cannot authorize user-file writes or compilation. Its
`analyze(tree, path, resolution, spacer_proof)` uses the existing parser tree,
source-bound static resolution, rule catalog, suppression scopes, diagnostics,
and byte-edit type. Its results use the same diagnostic/edit/skip/suppressed
shape as paragraph rules. Malformed syntax, stale resolution, or a stale or
invalid proof yields no edits.

## Binding and context evidence

`SpacerProof` is a **trusted producer contract**, not a binding resolver.
`source_sha256` must hash the exact input bytes. `existing` contains syntax node
IDs for expressions whose value has been proved to be the reviewed
`dots_space` helper at that point in execution. `available` contains footnote
call node IDs where inserting the literal `#dots_space` is proved to resolve to
that helper. Evidence must account for imports/re-exports, scopes, shadowing,
wildcards, mutations, deferred execution and package source identity. A matching
spelling, path, or mocked proof does not constitute that evidence. No producer is
implemented here; omitted proof gives no spacer insertion/removal. Unresolved
preceding expressions, including aliases or facade access, suppress related spacer findings rather than assume either
binding. Tests using synthetic proof demonstrate the contract only.

The repository contains `dots_space` definitions in local/math-blocks 0.1.0 and
0.2.0; math-book 0.1.0 and 0.2.0 wildcard-import the respective versions. This
observation is not a proof of facade exports or of any document's binding. A
producer must review the actual versioned package/facade semantics and immutable
source bytes, and prove provenance at the call site before integration.

## Implemented subset and remaining work

| Rule | Subset | Deferred |
| --- | --- | --- |
| Punctuation | Direct root prose, proven built-in literal-form call. Remove horizontal space after adjacent punctuation or move one trailing comma/period after literal prose across a literal footnote body. | Nested bodies, ambiguous punctuation, comments/newlines, calls/side effects and multi-rule composition. |
| Trailing gap | Diagnose source-line calls followed by blank lines and more content; inside a root display remove surplus empty lines. | Prose correction needs proven following paragraph-marker composition; EOF and explicit next-line parbreak are accepted. |
| Aligned spacer | For direct calls in root displays, inspect syntax `MathAlignPoint` or `Linebreak` outside nested calls/bodies; diagnose missing spacer, insert only with `available` proof. | Nested equations and richer math contexts; missing helper provenance. |
| Unaligned spacer | Remove an immediately preceding proven helper and its separator in a root display. | Missing helper provenance and non-direct equation calls. |
| Prose spacer | Remove a proven helper, including preceding horizontal gap only when necessary to restore already-existing punctuation adjacency. | Missing helper provenance and ambiguous punctuation; do not leave an adjacency violation as the result of a spacer-only edit. |

Inline math is not a display, and physical source wrapping is not alignment.
Outer alignment is not inferred from tokens nested inside footnote bodies,
comments, strings or code. Only root prose and root displays with supported
static context are examined; absence of a finding is not complete coverage.

Before enabling spacer repairs, add resolver-produced evidence. The registered
subset uses the common planner and candidate reparse/reanalysis, suppression
ownership, conflict and convergence checks. Compose prose gap fixes atomically with the
paragraph marker rule and compose remaining spacer/punctuation cases. Add
integration, explicit package provenance, false-binding, nested-context,
interaction and isolated Typst compile/render tests; update the audit matrix
only for evidence actually established. `tests/test_footnote_rules.py` exercises
this module with real parser/resolver facts and synthetic spacer proofs; it does
not prove actual helper binding or render behavior.
