# Captured text-utils paragraph repair

`captured_text_utils_paragraph_fix.py` is the protected repair bridge for
`captured-text-utils-paragraph-v1`. It applies the actual marker-gap,
prose-paragraph-marker and paragraph-capitalization rules within one bounded
root. The explicit public CLI composes this same bridge with the existing package
compiler and prepared publisher. The bridge itself neither invokes a compiler
nor publishes an artifact. Other profiles, ordinary analysis completeness,
transactions and schema-1 recovery remain unchanged.

## Selection, capture and initial admission

Use `ProtectedCapturedTextUtilsParagraphFix(root, target, package_path=...,
journal_directory=..., lease=...)` as a context manager. The caller supplies its
genuine `OfflineExclusive` assertion for the entire operation. The bridge owns
one new `CapturedTextUtilsParagraphInputSession` and privately borrows that
session's one locked transaction. Existing captures, transactions, parser Trees,
proofs or compiler receipts cannot be supplied as authority.

The sole writable input is one canonical root-relative `.typ` file. Exactly four
read-only evidence files are captured: the explicit root configuration, pinned
full catalog, and exact reviewed text-utils 0.1.2 manifest and `lib.typ`.
Configuration selects `pde`, includes exactly the target and specifies English
capitalization. An optional entrypoint must equal the target. Globs, ignores,
excludes, other languages and unknown keys are refused. Original bytes,
identities, metadata, full ancestry and evidence stay bound to the baseline;
repair never refreshes it.

Initial entry immediately certifies a distinct repair grammar and runs all five
actual obligations. Unsupported grammar or editorial first words are input
errors. Unsafe capture, evidence drift and altered or foreign authority are
transaction errors. Existing transaction/recovery failures propagate. Only
isolated fixtures are used by tests; package evidence is never repaired.

## Repair grammar and unchanged final grammar

The setup remains the exact ordered named import of `paragraph_tab` and
`apply-paragraph-tabs` from `@local/text-utils:0.1.2`, followed by the exact root
`#show: apply-paragraph-tabs`. Markers are standalone adjacent root Hash + Ident
nodes, never calls. Prose occupies one physical source line, with optional clean
closed inline math. Helpers, import, show, existing prose, indentation and math
are preserved except for the explicitly attributed changes below.

The separate `captured-text-utils-paragraph-repair-grammar-v1` admits:

- An existing standalone marker lacking its immediately preceding blank source
  line. The actual gap policy inserts one LF or CRLF at that marker's physical
  line start. The previous admitted construct may be the reviewed show or an
  ordinary prose line containing clean inline math.
- A missing marker at an already present root blank-line prose boundary. The
  actual marker policy inserts only `#paragraph_tab` and the local newline at
  the new prose line's start, preserving original indentation and casing.
- One allowlisted lowercase ASCII initial after an existing marker, repaired
  by the actual capitalization evaluator. A later pass reparses newly inserted
  markers before proposing their separately owned capitalization.

Mixed marked/unmarked paragraphs and initially zero markers are supported when
an after-first paragraph needs marker insertion. The first unmarked document
paragraph is exempt from both marker insertion and capitalization. A document
containing only that exempt paragraph still cannot produce the unchanged final
grammar, which requires at least one marked paragraph. Later missing-marker
paragraphs require an existing blank boundary; a single-newline continuation is
not promoted into a new paragraph.

No blocks, explicit parbreak calls, footnotes, continuation repair, display math,
comments, facades, aliases, other imports/styles or arbitrary calls are admitted.
The canonical final `captured-text-utils-paragraph-grammar-v1` and its original
compiler proof type are unchanged. Final certification is explicit, separate
from initial/intermediate repair admission, and requires every boundary and
capitalization obligation to be clean.

The retained package's safe-initializer certificate binds its genuine
`PackageSource`; the pure core separately reviews the invoked helper body. The
original regex show may change indentation behavior between lowercase and
uppercase branches, so these author-intended paragraph repairs do not claim
byte-identical rendering or layout.

## Five actual checks and retained policy metadata

Every baseline, intermediate and final replay performs:

- `core.parenthesized-scripts`: actual retained-catalog analyzer, clean only.
- `pde.paragraph-marker-target`: actual paragraph analyzer, clean only.
- `pde.paragraph-marker-gap`: actual shared paragraph gap policy, with exact
  source/paragraph-owned blank-line insertion when required.
- `pde.paragraph-capitalization`: actual paragraph analyzer and independent pure
  evaluator, with exact first-word ownership and one ASCII case byte only.
- `pde.prose-paragraph-marker`: exhaustive bounded backend using the actual
  shared marker diagnostic, casing-eligibility and candidate verification
  policies. It visits every admitted paragraph and boundary independently of
  the core proof and never consumes a caller-supplied repair list as authority.

`bounded_paragraph_rules.py` independently enumerates all root paragraph,
marker, Text, whitespace and inline-math owners. The bridge compares every owner
field, context, complete diagnostic and edit with independently certified
ownership. Existing marker availability and new marker availability resolve to
the captured reviewed package and active exact show. Gap policy's bounded
previous-line admission is supplied only for this independently checked root
slice; generic analysis behavior is unchanged.

All 33 selected rule obligations are present: five actual checks and 28
individual structural nonapplicability reasons. This is input-profile coverage,
not ordinary/global COMPLETE analysis. The generic `prose_marker_rules.analyze`
retains its unconditional honest PARTIAL skip; it is never stripped, waived or
reclassified. Ordinary omitted catalog arguments retain their prior behavior.
Explicit supplied catalogs, including empty or invalid ones, never fall back to
ambient disk. Protected bounded analysis requires a non-None retained catalog.
Shared marker casing preflight and candidate/directive verification use that
same retained metadata, including capitalization diagnostics.

## Genuine two-pass convergence and independent attribution

Two repair passes suffice by construction. Pass one inserts every required
blank line/marker and repairs all already marked eligible initial bytes. Marker
insertion preserves the original case. Reparsing then exposes only newly marked
lowercase initials; pass two performs their actual capitalization. No insertion
can be delayed to the second pass. Uppercase newly marked paragraphs need no
second repair pass. The transaction retains at most four genuine analyses:
initial, at most two changed states, and the final independent idempotence
analysis. A no-op has two equal analyses and zero repair passes.

Each genuine analysis gets independent finite retained facts and original
identity pins before entering history. Bounds and those original facts are
checked before and after callbacks/replay, so altered, cyclic or grown history
cannot become a new baseline. Transaction proposals include every actual rule
edit, not a synthetic successful result. Coverage, source/evidence membership,
findings, results and pass history must match a separately invoked
`candidate_convergence.converge(max_passes=2)` replay from the original bytes.
The generic convergence and transaction engines are unchanged.

The pure core independently verifies each complete pass, every inserted
newline/marker, first-byte case edit and changed AST/trivia coordinate. It binds
all original unchanged source spans, statement/content/math ownership and the
final canonical grammar using independently derived offset maps. Caller map
methods confer no authority. Moves, deletions, unowned bytes, omitted/foreign
contributions, repeated states and disconnected histories are refused.

Outside convergence, the bridge independently rechecks every Step against its
actual rule proposals and real reparsed Trees. It separately composes original
baseline envelopes from paragraph-owned insertion and case lineage, then checks
the full returned original-coordinate patch tuple and every contribution.
Insertion and a later case edit at the same original byte may share a composed
envelope; that envelope retains both distinct causal rules and pass numbers.
The core independently derives the same minimal envelopes. Returned final Tree
shape is compared with a fresh parse before candidate issuance. All final actual
results must be clean, without skips, suppression, parse errors or further edits.
A second invocation is a genuine zero-pass no-op.

## Opaque candidate, apply and truthful reporting

`validate()` issues an opaque `CapturedTextUtilsParagraphFixCandidate` with
reporting views of root sources, repair passes and final findings. Its original
underlying transaction token, source-map identity, replay, final Tree/nodes,
proof, history and summary have independently retained finite facts and identity
pins. Clones, serialization, reflective alteration, foreign candidates and
separately minted transaction tokens cannot authorize `apply(candidate)`.
Application must occur in the same live issuing context. Before exposing the
candidate, the bridge independently seals every original compound edge in the
fully verified validation DAG and its actual history container. The issuer-held
seal pins nested proofs, Trees, contexts, results, Steps, applied edits,
contributions and reporting records, once per distinct object. Equal copies,
cycles, deletion and finite-field changes are refused before structural or
semantic revalidation; a reflective replacement cannot establish a new baseline.
The same original seal is checked again after parser/core callbacks, before any
write or receipt issuance. Semantic verification runs at validation and before
and after transaction application. After all semantic and live transaction
callbacks succeed, the apply tail seals the original committed receipt. Its
seal also retains the actual original history list and issuer-held validation
binding, rather than only an equal history tuple.

A no-op produces no source journal. Following genuine successful application,
the bridge immediately records a detached historical summary with actual
modified paths and journal ID, before later guards. Post-commit drift raises a
safety failure but does not undo valid committed fixes, erase known success or
mint new authority. `summary` restores its original retained reporting facts if
an exposed summary is reflectively altered, including nested contributions.
It remains historical reporting after the context ends and grants no compiler
or publication authority.

Reports keep the canonical final `grammar_version`, and separately expose
`repair_grammar_version` and `repair_passes`. Each `applied_edits` entry describes
an original-baseline composed envelope with primary `rule_id`, all `rule_ids`
and exact `contributions`. Each contribution records `pass_number`, `rule_id`,
`start`, `end` and `replacement` in its own pass's source coordinates. Successful
reporting never calls a marker insertion a capitalization-only edit.

## Distinct committed receipt and publication ordering

`apply()` seals the bridge's original opaque
`CapturedTextUtilsParagraphCommittedSnapshot` only after its final independent
semantic and live transaction checks succeed. Original DAG bindings are checked
again after the last callback and before receipt publication. A failed postcommit
guard preserves truthful committed reporting but issues no bridge receipt;
restoring altered objects later cannot promote that failed apply to authority.
Direct transaction application and reflectively assigned success fields also
cannot issue a bridge receipt. Apply admission and final issuance require the
original unissued receipt state; final issuance also requires the unchanged
original applied-validation identity after callbacks.

`committed_snapshot()` only retrieves that already sealed original receipt.
First and repeated retrieval never invoke a parser, package loader or semantic
proof evaluator. It binds the committed postimage, independently replayed
canonical proof/Trees and all retained config/catalog/package evidence.
Historical summaries, standalone proofs and bare transaction receipts cannot
substitute. Resetting receipt fields cannot establish a new generation.

The independently verified original immutable DAG is sealed once per distinct
object. Live compiler guards check those retained finite original fields and
identities without re-certifying or recursively rebuilding shared proofs, plus
the current committed source/evidence/ancestry filesystem guard. Original DAG
bindings are checked again after live currentness callbacks before returning
authority. History container replacement, equal nested copies and issuer-binding
replacement are refused. Changed-length
candidates do not weaken these checks or add a prelaunch proof-rebuild timeout.
The public compiler's existing product timeout remains unchanged.

Publication may borrow exactly this transaction only while the original bridge
is pristine. Independently retained monotonic facts forbid preparation after any
validation/apply attempt, including a failed one. Initial read-only admission
precedes preparation, which precedes every candidate/application attempt.
See [CAPTURED_TEXT_UTILS_PARAGRAPH_COMPILE_CONTRACT.md](CAPTURED_TEXT_UTILS_PARAGRAPH_COMPILE_CONTRACT.md)
and [CAPTURED_TEXT_UTILS_PARAGRAPH_PUBLISH_CONTRACT.md](CAPTURED_TEXT_UTILS_PARAGRAPH_PUBLISH_CONTRACT.md).
