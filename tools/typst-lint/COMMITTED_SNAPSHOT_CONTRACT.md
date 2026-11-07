# Internal committed-source handoff

This component retains already checked source postimages for a later internal
compiler adapter. It adds no CLI route, compiler execution, resource profile,
output publication, installation, consumer change or all-project enforcement.
The original protected-source/offline-exclusive contract remains required.

## Transaction receipt

After a successful `ProtectedTransaction.apply(candidate)`, including a no-op,
`committed_snapshot()` returns an opaque immutable `CommittedSnapshot`:

- `root`: the original canonical logical root.
- `sources`: immutable root-relative path-to-bytes view of the exact postimage.
- `evidence`: immutable absolute path-to-original-bytes/absence view.

`assert_current(receipt)` checks a genuine receipt from this same active
transaction and revalidates against its retained records under the existing root
lock. Constructors, copied values, caller byte maps, hashes, fingerprints, JSON,
reports and other sessions cannot confer receipt authority. The baseline
`snapshot` property and `ApplyResult` are unchanged.

Changed application retains the existing `expected` map of descriptor-verified
postimages. It does not reread arbitrary live files to create a new baseline.
No-op application retains the original verified source records. Original and
postimage bytes are immutable and shared, rather than copied for each receipt.
Source/evidence fingerprints, parent identities, modification/change timestamps
and xattrs remain private immutable records, including original evidence absence.

Retrieval and every currentness assertion use the existing no-follow descriptor
reader, directory identity checks and source/evidence/metadata/shared graph read
budgets. They compare the complete records and never refresh expectations. No
input selection, sibling search, dependency discovery or live reanalysis occurs.
As with existing revalidation, unsafe filesystem operations may raise `OSError`
alongside typed `TransactionError` refusals.

No receipt is usable before successful application, after failed application,
during recovery, after context exit or in a different session. A no-op has no
journal, but has the same active-session and currentness requirements.

## Commit and failure boundary

The successful durable `committed` journal event ends the source-commit path.
Receipt construction and all later handoff/currentness checks are outside the
rollback exception handler. A later edit, allocation failure or stale input
refuses handoff without undoing valid committed fixes, replacing later user work,
adding recovery markers or removing the retained journal. Existing failures
before commit and explicit conflict-checked recovery remain unchanged.

## Independently verified bridge receipt

`ProtectedCoreFix.compile_snapshot()` returns an opaque immutable
`ClosedCoreCompileSnapshot` only after that bridge's own independently validated
candidate actually applies. `assert_compile_current(snapshot)` guards that exact
receipt while its original context/root lock remains active.

Public fields are `root`, `target`, `source`, `source_sha256`, `evidence`,
`required_rule_ids`, `evaluated_rule_ids`, `structural_reasons`,
`config_sha256`, `catalog_sha256`, `source_profile` and `grammar_version`.
The source is the exact retained transaction
postimage. Its 15 compiled core obligations, actual script evaluation and 14
structural reasons are bound to the retained independent replay/final proof,
candidate, reviewed catalog/config and bridge/transaction session.
Additional immutable `source_profile` and `grammar_version` fields identify the
original session selection. The default `closed-core-v1` remains grammar v1;
only explicit `structured-core-v1` selects grammar v2. Selection is never
inferred from a proof or snapshot. Retained proofs are independently rechecked
under the original selection before apply and on every compile handoff/guard.
Changing selection fields, retained proof profile/grammar, or snapshot fields
refuses authority as a safety failure without undoing committed source fixes.
Neither the transaction receipt nor source/PDF journals need a schema migration,
and recovery remains independent of current source-profile admission.

The bridge does not consult `ProtectedCoreSummary` as authority or parse changed
live files to manufacture a new proof. Direct application through the internal
`transaction` inspection property, even after bridge validation, cannot mint a
bridge compile receipt. Copied receipts and stale summaries are not inputs to
either issuer. Existing reporting remains output-only.

These are trusted in-process engine APIs, not a sandbox against arbitrary Python
code rewriting private implementation state. No serialization format grants
authority. Retained Unicode bytes are exact source evidence, not proof of a
compiler's font coverage, rendering fidelity, resources or successful output.

## Tests

`tests/test_committed_snapshot.py` uses the real parser and temporary sources,
catalogs, configuration and journals. It covers changed/no-op records, immutable
views, baseline preservation, protocol/session/forgery boundaries, transaction
bypass, post-commit failures, source/evidence identity and metadata mutations,
links/FIFOs/deletion/invalid UTF-8, absent evidence and parent/root replacement,
shared budgets and descriptor read races. Refusals retain committed fixes,
later edits and journals. Existing transaction, crash/recovery, bridge and
offline CLI suites remain required; this component is not a compile gate.
