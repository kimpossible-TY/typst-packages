# Internal captured text-utils paragraph repair

`captured_text_utils_paragraph_fix.py` is an internal, in-process protected
repair bridge for `captured-text-utils-paragraph-v1`. It adds one genuinely
applied rule, `pde.paragraph-capitalization`, without registering a public source
profile, changing ordinary analysis completeness, invoking a compiler, publishing
an artifact, or admitting another package or project grammar. Existing profiles,
transactions and schema-1 recovery are unchanged.

## Selection, capture and initial admission

Use `ProtectedCapturedTextUtilsParagraphFix(root, target, package_path=...,
journal_directory=..., lease=...)` as a context manager. The caller must supply
its genuine `OfflineExclusive` assertion for the entire operation. The bridge
creates exactly one `CapturedTextUtilsParagraphInputSession` and privately
borrows that session's one locked transaction. It accepts no existing capture,
transaction, compiler receipt, parser tree or proof as authority.

The sole writable input is one canonical root-relative `.typ` file. The session
captures exactly four read-only evidence files: the explicit root configuration,
the pinned full catalog, and the exact reviewed text-utils 0.1.2 manifest and
`lib.typ`. Configuration must select `pde`, include exactly the target, and set
`capitalization.language = "en"`; an optional entrypoint must be the same target.
No globs, ignores, excludes, other language settings or unknown keys are admitted.
The source, evidence, identities, metadata and full ancestry remain tied to the
original baseline. The bridge never refreshes that baseline after repair.

Initial entry immediately certifies the root grammar and performs the five
actual checks below. Unsupported initial configuration, package, root syntax,
editorial first word or another required repair is an `InputError`. Unsafe or
over-budget capture, evidence drift and altered/foreign in-process authority
are `TransactionError`s. Existing transaction failure/recovery types propagate.
Tests use only isolated temporary fixtures and copied package evidence.

## Exhaustive grammar and five actual obligations

The pure proof engine admits exactly the ordered named import of `paragraph_tab`
and `apply-paragraph-tabs` from `@local/text-utils:0.1.2`, then the exact root
`#show: apply-paragraph-tabs`. Markers are root adjacent `Hash` + `Ident` nodes,
not calls. Correct standalone marker spacing, single-line literal paragraphs and
optional clean closed inline math are required. At least one marked paragraph
must occur. A first unmarked literal paragraph is allowed; every subsequent
prose boundary must already have its owned marker. Comments, aliases, facades,
unknown calls/styles, other imports and other repairs are not admitted.

The package's reviewed safe-initializer certificate binds the retained
`PackageSource`. Its invoked helper body is separately examined by the pure
package proof. Pinning the initializer is not a substitute for reviewing the
helper that the root show actually invokes. The original `lib.typ` regex rules
may change indentation behavior between lowercase and uppercase branches, so
this deliberate editorial repair does not claim byte-identical output or layout.

Exactly five rules are actually checked on every baseline/candidate/final replay:

- `core.parenthesized-scripts`: actual retained-catalog analyzer, clean only.
- `pde.paragraph-marker-gap`: actual paragraph analyzer, clean only.
- `pde.paragraph-marker-target`: actual paragraph analyzer, clean only.
- `pde.paragraph-capitalization`: actual paragraph analyzer, the sole editable rule.
- `pde.prose-paragraph-marker`: separately invoked exhaustive bounded boundary
  checker, clean only.

The remaining 28 selected core/PDE rule IDs have individual structural reasons
in the exhaustive proof. All 33 obligations must be present. The transaction's
coverage contract counts both dispositions as discharged obligations; the
historical summary distinguishes the five actual checks from 28 structural
reasons. It does not promote global or ordinary analysis to `COMPLETE`.
The generic `prose_marker_rules` analyzer is unchanged and retains its honest
partial skip; no result or skip is deleted or waived.

`paragraph_rules.analyze(..., catalog=None)` and
`capitalization.evaluate(..., catalog=None)` retain ordinary behavior when the
keyword is omitted. An explicit retained catalog supplies all diagnostic
metadata, including the capitalization evaluator used by the paragraph analyzer.
Empty, missing, duplicate or invalid supplied required metadata is rejected;
only `None` selects the ordinary ambient catalog. Protected analysis uses only
the transaction-retained validated catalog, never a fresh catalog read.

## Independent edit authority and replay

The bridge independently enumerates every root marker and first Text owner,
checks captured helper binding and English prose ownership, and compares these
contexts with the exhaustive proof. It calls the pure capitalization evaluator
with the retained catalog and explicit English for every context. The actual
paragraph analyzer must return exactly the independently expected diagnostics
and edits. `already-uppercase` is verified as an actual clean result, not waived
as a generic skip.

Each permitted edit is exactly one ASCII `a` through `z` byte replaced by its
uppercase at that marker's first word. No insertion, deletion, movement, overlap,
other attribution or other changed source byte is permitted. The package,
configuration, catalog and unselected files are never repair targets. Reparsed
candidate nodes retain exact kinds, spans, child edges, operands, display state,
import/show records, paragraph boundaries and package/helper ownership, except
for the explicitly approved literal Text bytes.

Only one repair pass is allowed. Every actual final result must be clean and
idempotent, with no skips, suppression or parse errors. A separate
`candidate_convergence.converge(max_passes=1)` invocation outside the transaction
callback reparses and analyzes from the retained original bytes. Its exact
candidate, initial/final results, history, pass count, findings and causal
capitalization-only attribution must match the transaction history. Each genuine
pass receives independent expected-shape facts and original identity pins before
it enters the bounded history. Guards check those facts before and after later
callbacks/replay, so changed or cyclic historical objects cannot become a fresh
baseline. Returned replay patches, single-rule contributions and sole-pass
attribution are checked again outside convergence; the returned final Tree is
compared against a freshly parsed trusted shape. Live input identities and bytes
are checked again before issuing the bridge candidate.

## Candidate, application and historical reporting

`validate()` returns an opaque `CapturedTextUtilsParagraphFixCandidate` exposing
only reporting views of root sources, pass count and final findings. Its
original underlying transaction token, source-map identity, replay, final Tree
and node tuple, proof, history and summary have independent bounded retained
facts and original identity pins. Clones, serialization, reflective mutation,
foreign candidates and tokens minted outside bridge validation cannot authorize
`apply(candidate)`. Apply must occur in the same live issuing context.

The transaction's existing frozen apply-input protections remain unchanged.
A genuine no-op has zero passes and produces no source journal. After successful
actual application, the bridge immediately records an unexposed copy of the
validated historical summary with the actual modified paths and journal ID,
before later guards. Drift detected after commit raises a safety failure but
does not undo valid committed fixes, erase known success or mint new authority.
The `summary` property restores its original finite reporting facts if an exposed
summary is reflectively altered and remains available after the context ends.
It provides no compiler or artifact receipt and no permission for subsequent work.
