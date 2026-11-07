# Internal captured relative input foundation

`captured_relative_inputs.py` is an internal capture session, not itself a
registered source profile or repair/compiler bridge. Its internal source identity
is `captured-relative-core-v1`. The separate `captured_relative_fix.py` bridge now
performs protected root-only script repairs by owning one capture session and
privately borrowing its same transaction. Capture alone never authorizes a write.
Neither existing public source profile accepts this internal identity; both still
reject imports without dependency discovery. No CLI, ordinary import graph/resolver,
compiler dispatch, PDF publication, package lookup, installation, consumer
deployment or recovery schema is added by this composition.

## Ownership and two captures

`CapturedRelativeInputSession(root, target, journal_directory=..., lease=...)`
requires one literal canonical root-relative `.typ` target and the caller's real
`OfflineExclusive` assertion. It never creates that assertion or infers editor
state. Profile, target, canonical root/storage paths, evidence overlap, limits,
and storage options are checked before discovery. The existing transaction's
same-root lock, private external same-filesystem journal store, pending-journal
refusal and explicit store-creation rules remain required. Discovery acquires no
second lock and never executes dependencies.

1. Bounded safe discovery proposes paths and an import-edge multiset. Its bytes,
   Trees, handles and fingerprints are discarded. Every component is opened
   without following symlinks. Only named minimal relative literals are followed:
   necessary leading `../` within the project is allowed; `./`, repeated
   separators, canceled components, absolute/package/escaped/dynamic paths and
   includes are refused. No directory crawl or ambient source search occurs.
2. An existing `ProtectedTransaction` owns exactly the root target as writable.
   Explicit config, the reviewed catalog and every candidate module are read-only
   `EvidenceInput`s. All authoritative bytes and file/directory identities are
   recaptured under that transaction's lock.
3. Only its immutable `GraphSnapshot.sources/evidence` bytes are parsed using the
   pinned official syntax adapter. Config is the existing explicit core-only
   singleton schema; catalog is the exact existing reviewed full catalog. Pure
   certification independently rederives the reachable graph, exhaustive inert
   scalar module language, root language, scalar values/origins and applicability
   obligations. No `ImportGraph`, `ModuleEvidence`, caller-supplied purity flag or
   discovery Tree can supply protected authority.
4. The authoritative closure must equal the proposed closure, with no unused
   modules, and the edge multiset must match. A scalar/source change before this
   capture may become a completely independently certified new baseline with the
   same closure/edges. A closure/edge change refuses; evidence is never extended
   on the fly. Every change after capture must refuse against retained records,
   including same-byte inode replacement.

Initial missing files observed before a read, invalid initial UTF-8, parser
failures and unsupported initial language use `InputError` classification.
Missing files after the initial presence check, unsafe readers, symlinks,
nonregular files, hardlinks, read races, stale captures, altered authority and
budget/closure failures use `TransactionError` classification. The ordinary
import graph's broader `ImportGraphError` classification is not reused.

## Retained identities and strict evidence

`EvidenceInput.utf8` defaults to false, preserving previous evidence behavior.
The name denotes the complete source-like reader policy, not only decoding:
source-byte bounds, UTF-8, effective-user/preservable-group checks and existing
ACL/filesystem-flag checks. This capture session deliberately opts modules,
config and catalog into that stricter policy. Read-only mode is allowed for
modules/config/catalog; only the one root target requires owner-write mode.
Special permission bits are refused on all present captured files. Dependencies
are never writable selections.

`ProtectedTransaction.captured_fingerprints` returns immutable maps constructed
only from its actual retained `_File` records. It does not refresh identities
with later file stats and grants no candidate/compile authority. Source/evidence
path overlap and duplicate canonical evidence names refuse. All present capture
file device/inode identities must be distinct, including case and Unicode
normalization aliases on filesystems that resolve them to the same inode.
Distinct project directory names sharing an actual retained directory identity
also refuse. Private storage and analysis inputs cannot overlap.

`retain_ancestry=True` is an explicit transaction opt-in; the default is false.
At each actual file capture, the reader records the directory identities of the
exact traversed no-follow descriptor chain. Later live revalidation compares the
complete old chain, including intermediate ancestors, rather than blessing a
newly statted chain. Opt-in apply paths also compare the pre-rename descriptor
chain and retain/compare the postimage chain; committed receipts revalidate those
same records. This does not add an apply path to the captured-input session.
The extra ancestry is transient, is not serialized, and is not an assertion about
ancestry-attested rollback/recovery: same-session rollback and restarted
historical recovery retain the existing schema-1 immediate-parent semantics.
No live configuration, modules, compiler or fonts are required by recovery.

## Fixed budgets

There is no public unlimited bypass. Optional `ReadLimits` may only narrow the
existing defaults. Discovery and authoritative certification share these fixed
source-language ceilings:

- 32 total source files, including the root; 1 MiB per file; 4 MiB aggregate.
- 128 import statements; 16 import edges of maximum root-to-module depth.
- 64 distinct project parent directories, including the project root and every
  logical ancestor; 512 UTF-8 bytes per logical path and import request;
  16 KiB total canonical source path bytes.
- 200,000 aggregate syntax nodes; 4,096 declarations/named-import bindings;
  8,192 scalar uses; 64 provenance steps; 128 UTF-8 bytes per binding name.

The transaction's unchanged per-view byte/metadata/journal bounds apply in
addition. These source limits reserve room beneath the separate graph compiler
workspace limits; they are not total-process-memory or parser execution-time
guarantees.

## Live receipt versus data

Inside an active context, `session.capture` returns the one opaque
`CapturedRelativeInputs`. Its immutable maps retain exact parser Trees, the
logical-module-to-absolute-evidence map and the pure proof. Its report contains
only bounded paths, hashes, sizes, edges and config/catalog digests.
`session.assert_current(receipt)` requires the identical issued receipt, owner,
seal, selection, proof and map objects, plus independent original tree/report
signatures and exact retained transaction source-byte identities. It rechecks
the pure proof and the existing transaction's exact retained records. It does
not rediscover, read later filesystem bytes as new authority or update a stale
baseline. Copies, foreign/expired receipts and altered nested Trees/Nodes/proofs
refuse. Reports, fingerprints and pure proofs are data, never live authority.

The pure proof identifies `core.parenthesized-scripts` as requiring actual
analysis; the capture module itself does not claim it was evaluated. No capture
receipt is a `ValidatedCandidate`, committed source snapshot, compiler resource
certificate or permission to write/compile. The separate existing internal repair
bridge performs complete actual script analysis, independently verifies candidate
provenance, and permits only root-only repair under the existing transaction.
Its own successful apply is required for its opaque committed graph receipt.
The separate internal graph compiler consumes only that bridge's live committed
graph and implements additional repertoire, scalar-charge, resource, dependency
and artifact obligations. See
[CAPTURED_RELATIVE_COMPILE_CONTRACT.md](CAPTURED_RELATIVE_COMPILE_CONTRACT.md).
Compilation and publication remain outside this capture module and repair bridge;
graph PDF publication and public CLI composition are not implemented here.

## Verification

Tests use temporary consumers and the real pinned parser/transaction. They cover
transitive/diamond captures, read-only module retention, same-closure new-value
certification, closure/edge drift, exact budgets, initial versus unsafe read
classification, symlink/FIFO/hardlink/root-escape/alias refusal, retained identities,
postcapture byte/inode/mode/xattr/ancestor changes, and forged/copied/foreign/
expired/altered receipts. Legacy transaction, journal/recovery, committed-snapshot
and public source-profile tests remain required regressions.

## Private repair-bridge composition

The separate internal `ProtectedCapturedRelativeFix` now owns a capture session
and privately borrows its same transaction under the same lock. Capture itself
still issues no repair/compiler authority. `_assert_binding(capture)` performs
bounded historical object/byte/proof/signature checks only, using independently
retained facts (including Path backing state and exact map identity) before
traversing potentially altered objects. Public `assert_current(capture)` always
adds the original baseline revalidation, even after a private apply. It is never
silently reinterpreted as a postimage check. `_borrow_transaction(capture)` first
runs that full original guard and does not transfer ownership. Optional `fault`
only forwards the existing transaction test hook. The repair bridge alone can
establish successful-apply provenance and guard existing committed postimages;
see `CAPTURED_RELATIVE_FIX_CONTRACT.md`. No recovery schema changes are involved.
