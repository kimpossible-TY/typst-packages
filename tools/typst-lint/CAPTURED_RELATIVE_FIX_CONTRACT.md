# Internal protected captured-relative repair bridge

`captured_relative_fix.py` adds an internal root-only script repair bridge for
`captured-relative-core-v1`. It does not register a public profile, change either
existing public grammar, expose CLI selection, accept imports in closed/structured
core, stage compiler sources, execute Typst/modules, resolve resources, or publish
artifacts. The separate internal graph compiler accepts its genuine committed
graph; older single-source compiler entrypoints do not. See
[CAPTURED_RELATIVE_COMPILE_CONTRACT.md](CAPTURED_RELATIVE_COMPILE_CONTRACT.md).
Ordinary analysis remains PARTIAL and read-only.

## Exact API and ownership

`ProtectedCapturedRelativeFix(root, target, *, journal_directory, lease,
limits=DEFAULT_READ_LIMITS, fault=None, create_journal_directory=False,
source_profile=core.SOURCE_PROFILE)` is a single-use context manager.

- `validate()` returns one opaque `CapturedRelativeFixCandidate` with immutable
  root-only `sources`, `passes`, and `findings` inspection fields. Its private
  transaction token is not independently sufficient bridge authority.
- `apply(candidate)` accepts only this active bridge's identical unused candidate
  and returns the existing actual `ApplyResult`.
- `summary` is reporting only, available after successful actual apply.
- `committed_graph_snapshot()` returns an opaque live
  `CapturedRelativeCommittedGraph` only after this bridge's own successful apply.
- `assert_committed_graph_current(snapshot)` guards that identical receipt against
  retained committed root/dependency/config/catalog filesystem records.

The constructor creates and owns exactly one `CapturedRelativeInputSession`.
That session retains ownership of its one authoritative `ProtectedTransaction` and
root lock. The bridge privately borrows the same transaction; it never accepts an
existing capture, proof, transaction, report, or committed receipt, reopens the
transaction, acquires another lock, or transfers ownership. Capture's constructor
preflight remains pure and does not acquire a lock. The caller must provide its
real `OfflineExclusive` assertion; the bridge never asserts editor/watcher state.
`fault` is solely the existing transaction test hook, forwarded by the owned
capture session.

Capture's private `_assert_binding(capture)` checks immutable historical capture
identities, independently retained primitive facts, original parser Trees/proof,
selection, byte bindings, and report signatures without live-file revalidation.
`assert_current(capture)` always adds the original transaction `_revalidate()`.
Its meaning never changes after apply: a changed original root is stale for this
public capture method. The private borrow helper performs the full original
baseline guard. Only the repair bridge switches to existing retained committed
postimages after its own actual success.

## Complete analysis and exact replay

Every transaction callback requires exactly one writable root source, the exact
retained evidence membership and byte-object identities, and the same root Path.
The retained explicit singleton core configuration and exact reviewed full catalog
are parsed without ambient rereads. Candidate graph construction replaces only
the root `Tree`; every dependency retains its original source bytes and Tree.

Each candidate graph is certified and independently verified in the fixed source
profile, separate root/module grammar versions and pinned parser contract. This
rechecks every source/path/edge/depth/byte/node/binding/use/provenance budget after
parentheses insertion too. Every one of the 15 core obligations is accounted for:
14 are discharged by exhaustive root/module structural reasons, and the actual
`core.parenthesized-scripts` backend runs with the retained immutable catalog.
Every module must produce an entirely empty actual script result. Root findings
must exactly enumerate all unparenthesized operand identities and actual edits
must equal independently enumerated original operand-boundary parentheses. Parse
errors, skips, suppression, incomplete evidence/rule coverage, deferred edits and
second repair passes refuse.

Outside the transaction callback, independent replay reparses exact original
captured root bytes and uses `converge(max_passes=1)`. Transaction and replay must
agree on exact candidate bytes, zero/one pass counts, complete callback source and
result history, edit attribution, and final clean/idempotent analyses. The replay
retains original-source attributed insertions, each owned only by the script rule
in pass 1. Replaying those exact insertions must reproduce the final content.
Every original raw byte survives, including BOM, CRLF, whitespace and punctuation;
only independently enumerated operand-boundary `(` and `)` bytes may be inserted.
No string search, reconstructed body or diff guesses insertion ownership.

Semantic comparison preserves exact dependency source identities and module
proofs, edges/import items, origins, scalar kinds/values, full alias/import/rename
routes, imported-use syntax/name/order, enclosing wrappers/equations and nested
attachment operand owners. Attachment roles and script ownership are also checked
independently. Equal scalar values cannot substitute for declaration identity.
Root imports precede every repair site: their original spans/node IDs and edge
records remain exact. Only root-import provenance steps' whole-root source hashes
are normalized for this semantic comparison. Dependency provenance hashes/spans
are never normalized. Render-use node IDs/spans necessarily move and are compared
through their semantic owners instead. The separately verified complete final
proof still binds every exact candidate source/hash/span/node field.

## Retained authority and success provenance

Candidates wrap private transaction tokens. Before apply and every later handoff,
independent primitive facts and identity pins guard the wrapper/token source maps,
bytes, passes, findings, owners/seals, independent replay baseline/content/history,
actual results and attribution, final Trees/proof, coverage, selection, evidence,
configuration and catalog. The existing small `protected_fix` authority helpers
pin map identity and Path backing facts. They check exact expected types, lengths
and fields before bounded traversal, refusing missing fields, bool/int changes,
foreign containers, cycles, oversized alterations, shared in-place mutation, and
coherent token/replay/new-proof substitution before writes. This is a trusted
Python runtime contract, not a sandbox against arbitrary malicious Python changes.

Bare captures, pure proofs, reports, direct `transaction.apply` calls and bare
committed transaction receipts never establish repair-bridge provenance. Both
real changes and actual no-op apply must return successfully through the bridge.
Failed apply or recovery grants no bridge receipt. Successful apply immediately
records its actual result and an honest summary before any later guard. Summary
fields and nested insertion records are copied privately before entering apply,
so mid-apply reflection cannot falsify the successful report. Later state drift
refuses handoff without rolling back a valid committed repair or erasing summary.
The reporting summary never authorizes any action.

A committed graph's canonical logical source map consists of the existing
`CommittedSnapshot`'s retained root postimage and unchanged dependency evidence.
The postimage bytes must exactly equal the independently proved final content;
object identity with the replay's root bytes is not assumed after an actual
postimage reread. Dependencies keep their original byte-object identities. The
snapshot retains exact profile/grammar/parser/coverage/proof/config/catalog and
same-session apply provenance. It does not certify resource repertoire or grant
compiler authority. Every candidate and graph authority expires on context close.

## Filesystem and recovery boundary

Before apply the original capture is revalidated. After successful apply only
existing committed source/evidence records supply live filesystem authority.
Root, dependencies, configuration and catalog retain no-follow byte/inode/mode/
metadata and full transient ancestor checks. Stale postcommit generations are
never reparsed or recaptured as new authority. Recovery retains unchanged schema 1
and its historical immediate-parent semantics; transient live full ancestry is
not retroactively claimed for rollback or restarted recovery. No dependencies
are selected for writes, including on failure and recovery, and later external
edits are preserved by the existing recovery rules.

Tests use only temporary consumers with the real pinned parser and transaction.
They cover nested scripts/fractions/wrappers, diamond renames, builtin shadowing,
punctuation strings, BOM/CRLF/no-op, exact callback evidence, actual rule failures,
replay and graph-budget disagreement, reflective/copy/expiry/bypass attacks,
preapply/postcommit source/evidence/identity drift, and existing transaction fault
and recovery behavior. Graph compiler acceptance is tested separately under its
own contract. Public CLI composition remains separate.


## Private prepare-before-validation publication composition

The separate graph publisher privately borrows the original session-owned
transaction through `_borrow_publication_transaction()`. The bridge checks its
original issued identity, active capture/selection binding, current original
inputs and pristine state. Monotonic validation/application attempt flags refuse
late preparation even when an early failed attempt produced no history or token.
The publisher rechecks pristine ownership after preparation callbacks. No public
transaction property, transaction injection, ownership transfer or second lock is
introduced. After successful source application, committed graph authority, not
original-baseline capture currentness, guards compilation and publication. See
[CAPTURED_RELATIVE_PUBLISH_CONTRACT.md](CAPTURED_RELATIVE_PUBLISH_CONTRACT.md).
