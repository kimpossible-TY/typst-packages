# Internal single-PDF publication and output-only recovery

`closed_core_publish.py` composes the existing protected closed-core source fix
and committed-source compiler with a separate bounded PDF journal. This is an
internal trusted Python engine API. The separately selected CLI in
[EXPLICIT_COMPILE_CLI_CONTRACT.md](EXPLICIT_COMPILE_CLI_CONTRACT.md) calls it.
Existing source-only status/recover commands, project configuration, consumers,
wrappers, editor/watch routes, shared packages, installation and deployment remain
unchanged. Ordinary unprofiled compilation remains inert. Tests publish only
inside owned temporary consumers.

## Ordering, authority and explicit exclusivity

The caller enters a real `ProtectedCoreFix`, then enters
`PreparedPdfPublication(fix, destination='build/document.pdf', lease=output_lease)`
**before** calling that fix's `validate()` or `apply()`. Inside the publication
context, validate/apply normally and call
`publication.compile_and_publish(fix.compile_snapshot(), profile='embedded-ascii-v1')`.
The profile remains explicitly required. `embedded-ascii-v1` keeps its exact ASCII
plus optional leading UTF-8 BOM and embedded-font behavior. The separate opt-in
`embedded-applegothic-ko-v1` admits only its versioned Korean source repertoire
and the compiler's pinned local AppleGothic resource, without widening the
closed-core grammar or claiming general Unicode/system-font support.

The output lease must be independently created with
`OfflineOutputExclusive.assert_exporters_watchers_closed_and_no_other_writers()`.
It asserts that exporters, previews/watchers, every other output writer, output
ancestors and publication storage remain exclusive throughout this context.
Source exclusivity, the CLI's source-only assertion and the root flock do not
create or infer this lease. No API detects unsaved buffers or external writers.
The root lock coordinates cooperating engine runs; no sequence of checks and
rename is a compare-and-swap against arbitrary uncoordinated writers.

The new context borrows the actual active source transaction's already-held
root flock. It never flocks an independently opened root descriptor. Additional
no-follow descriptors retain and recheck absolute directory edges. Standalone
inventory and recovery independently acquire the same root flock.

Entry scans all retained publication records before source validation/application,
checks protected-path collisions, captures previous output or explicit absence,
and durably prepares backup and recovery information before compilation. Every
pending or corrupt record blocks a new combined session. Entering after source
validation/application is refused. There is exactly one compile attempt per
prepared context and no public method accepting arbitrary artifact bytes,
previously compiled artifacts, serialized receipts, caller executables or flags.
Preparation is resource-neutral: no font path is inspected before profile
selection. After pure profile-name validation, only the selected Korean font is
checked for destination path/inode collisions, before launch intent, resource
capture or a compiler probe. ASCII preparation and compilation do not stat fonts.

The session calls the actual committed-snapshot compiler itself. Its genuine
artifact must remain bound to the same live bridge/snapshot and unchanged pinned
compiler and selected captured fonts. `assert_artifact_current` is checked after
compilation, before staging,
immediately before replacement and before final publication commit. Cancellation
and the bounded deadline are checked before staging/replacement and final commit.
A later stale source/config/catalog/compiler/font refuses publication without
undoing
committed source fixes. Reports, hashes and journals never confer authority.

## Destination and metadata

Only one explicit canonical root-relative `.pdf` is supported, within the same
root/filesystem, with all parents already existing. No stdout, templates,
multiple outputs, external path, cross-volume copy or parent creation. Literal
paths may contain spaces and nested directories. Empty, absolute, dot/dot-dot,
repeated-separator, wildcard and non-`.pdf` selections fail.

Existing output may contain **any bytes**, not necessarily a valid PDF. It must
be a regular, single-link file owned by the current effective user, mode exactly
`0600` or `0644`. Absent output uses `0600`; supported original mode, owner,
group and metadata are preserved for replacement/restoration. Symlinks anywhere
in the absolute ancestor chain, directories, FIFO/device/socket outputs,
hardlinks, cross-device output, changed root/parents, owner/mode/flags/ACL changes,
newly appeared files, same-byte new-inode replacements and other stale baselines
fail closed. A mismatch never refreshes the retained baseline.

The initial metadata profile is empty xattrs on Linux, or empty / exactly one
`com.apple.provenance` xattr on macOS. The selected Mac automatically synthesizes
non-removable provenance even on newly created temporary files. Its **exact
bounded bytes** are therefore retained, compared and faithfully copied; merely
recognizing its name is insufficient. Every other xattr, ACL or unsupported
filesystem flag is refused. Existing output/store/namespace metadata is never
stripped, normalized or repaired. Stage/backup/restoration copies must verify
exact desired metadata; a platform refusing exact copying is unsupported and
leaves old output intact. Native temporary characterization verifies same-value
provenance copying, refusal of mismatched values and exact publish/restore
fidelity. Store, namespace and attempt directory metadata values are retained in
the manifest and rechecked, not accepted afresh by name.

Protected source/config/catalog, source journal storage, PDF journal storage,
compiler and launcher collisions are rejected by literal/case-folded paths and
available inode identities. Publication storage's reserved component is not a
valid selected source root. All selected output and journal edges are no-follow,
with retained device/inode/mode/owner/group/flags directory identities. Directory
size and timestamps are not pinned because sibling writes legitimately change
them. Output fingerprints additionally retain size, mtime, ctime and xattrs.

## Separate storage, inventory and bounds

The fixed private sibling namespace is:

`root.parent/.typst-lint-pdf-journals/root-<SHA256(canonical root)>/<32hex attempt>/`

The schema is independently discriminated by `kind='typst-pdf-publication'` and
integer `schema=1`. It is **not** the source transaction's schema 1. Source
transaction/bridge implementations, source-only CLI routes and their original
journals are unchanged.
The existing standalone offline CLI does not discover PDF journals.

Each attempt retains a strict manifest, ordered exclusive-create event JSON,
optional binary `backup.bin`, binary `publish.bin` and binary `restore.bin`.
PDF bytes are never base64-encoded into JSON. Records retain canonical root and
ancestor identities, literal destination/parents, previous presence/fingerprint/
hash/size/metadata, backup and stage identities, source target/hash/config/catalog
generation, original source journal ID, explicit profile and compiler hash.
Invocation summaries are bounded inert outcome evidence. They are never executed.

`inventory_publications(root, limits=...)` is read-only and takes the root flock.
Absent stores/namespaces return an empty inventory without creating anything.
The internal already-locked entrance takes the real transaction. Inventory
reports visible historical terminal events as completed and every malformed,
unsafe, excessive, contradictory or incomplete record as pending/corrupt. It
does not claim a previous caller received a durable acknowledgment or that a
historically published output remains the current user file.
Before accepting completed records as nonblocking, a new combined writer also
validates and fsyncs each terminal record and its attempt directory under its
borrowed root lock. A strengthening failure blocks entry before source validation
or application. Historical output is neither read nor rewritten.

Immutable `PublicationLimits` defaults:

- Old output: 16 MiB; new output: 16 MiB.
- Metadata: 256 KiB per inspected inode, including names and values.
- One JSON file: 256 KiB; all JSON in an attempt: 1 MiB.
- Binary payload in an attempt: 48 MiB, at most backup/publish/restoration stages.
- Events: 64; entries in a journal directory: 128.
- Retained attempts per selected-root namespace: 128.
- Aggregate retained bytes in that namespace: 256 MiB.

Nonnegative integers only, excluding booleans. Initial old capture uses its own
old ceiling. Revalidation/readback additionally uses exact retained sizes. Reads
reject initial oversize before payload allocation, stream at most 64 KiB or the
remaining bound plus one, and compare descriptor/named identity, metadata and
size before/after reading. Inventory/recovery enforce the same finite bounds.
Corrupt contents cannot silently count as zero toward a subsequent write budget.
No automatic cleanup, pruning, retention deletion or force recovery exists.
Completed and pending recovery material must be retained at the fixed root-derived
location. Deleted stores/journals and relocated roots/stores cannot be discovered
automatically; absence is not proof that no historical attempt ever existed. Only
the new combined publication session scans these PDF records before its source
fix/compile. The existing offline CLI fix/status/recover still does not. These payload bounds
are not a total Python RSS/time guarantee or a general hostile-OS sandbox.

## Durable states and failure boundary

Normal ordered events are `prepared`, `compile_launch_intent`, `compiled`,
`staged`, `replace_intent`, `replaced`, `published`. Failure/recovery uses
`aborted_unpublished`, `rollback_intent`, `restored`, `recovery_required`.
Both readers and writers enforce closed field schemas, legal transitions and
cross-event content/inode/metadata consistency. Schema remains 1 with identical
manifest/event fieldsets and transitions; only the strict historical profile-name
allowlist adds `embedded-applegothic-ko-v1`. Updated readers still accept existing
ASCII journals. Older readers may safely reject Korean journals as unknown. No
font identities or resource bytes are added to durable records. An observed
compiler zero must not become gate success without successful final guards and publication.

Preparation fsyncs backup, manifest/events and containing directories. Staging
writes new bytes and supported metadata, fsyncs, reads back and verifies the exact
stage. A durable `replace_intent` identifies that stage before mutation.

The visible output mutation boundary is **`os.replace`**, not later fsync.
Immediately after rename the engine verifies and retains the actual postimage
identity before recording `replaced`. It fsyncs both the destination parent and
the attempt directory from which the stage moved, rechecks the exact named
postimage and live artifact/source/compiler authority, then writes/fsyncs the
final event and its directory. Reporting/allocation after a genuinely durable
completion is outside rollback handling.

Before replacement, failure does not touch the output and reports whether the
old baseline remains recognized or later work is preserved as a conflict.
After replacement, failure attempts output-only restoration **only** from the
exact recognized owned postimage: stage retained original bytes/metadata and
atomically replace, or unlink the exact new output for original absence.
Restoration intent is durable before mutation; destination and moved-stage
journal directories are fsynced afterward. Any conflict, changed parent,
unsafe current file, failed restoration or uncertain mutation preserves evidence
and reports recovery required. It never overwrites later edits, invokes source
rollback or discards successfully committed source fixes.

A final completion event write/fsync can fail after valid JSON becomes visible.
That is an uncertain acknowledgment, not grounds to claim success, unchanged old
output, or perform an unjournaled rollback contradicting a possible commit.
Inventory is deliberately only historical visible evidence. Explicit recovery
strengthens a visible terminal event's durability by validating and fsyncing its
exact retained record and attempt directory before acknowledging completion.
Failure of recovery's own sync remains explicit unresolved/uncertain. No terminal
reconciliation rereads/replaces later user output or promotes an old PDF anew.

## Independent recovery and truthful reports

`recover_publication(root, destination=..., journal_id=..., lease=fresh_output_lease)`
requires existing storage, one exact ID, the exact destination allowlist and root
lock. It never parses or changes source, consults current config/catalog/compiler,
recompiles, resolves current font resources, executes recorded argv or reissues an artifact. Source/config/compiler
may legitimately have changed or disappeared since the old attempt.

For a nonterminal record:

- Exact original output/absence still recognized: reconcile without rewriting.
- Exact durably observed publication postimage: restore backup or old absence.
- Exact durably observed restoration: finish idempotently.
- Later edit/new inode, unsafe/oversized file, changed parents, corrupt evidence or
  insufficient interrupted identity evidence: preserve unresolved.

Rename can change ctime. Only the live process's immediately observed own rename
may explain that change. Recovery never discards ctime or guesses from matching
bytes/inode/mtime. A SIGKILL immediately after rename but before its actual
postimage observation is durable can remain unresolved. The same conservative
rule applies to restoration rename interrupted before durable observation.
Restoration promises bytes and supported metadata, not the original inode or all
timestamps. Terminal `published` is historical completion, never a rollback
target. Historical terminal reports leave `observed_sha256=None`, with an explicit
historical explanation, rather than mislabeling an old hash as a fresh observation.

Immutable result reports, also attached to errors, separate `phase`, `gate_status`, `compiler_invoked`
(true/false/unknown), raw observed `compiler_status`, full original bounded
`attempted_invocations`, `source_commit`, `output_disposition`, `recovery_required`,
`journal_id`, `source_journal_id`, reason and a currently observed hash when known.
The optional `compile_report` retains the exact immutable compiler report from
the actual call boundary, including its `resource_report`, on success or later
publication failure. A captured ASCII resource report has an empty font tuple;
`None` means evidence was not captured or is unavailable. Installed/private file
identity, hash, size, face index and font names are original observations, not
facts refreshed from a live registry. Private workspaces may already be deleted
when the report is read. The compiler report's `safety_gate` describes its original
compile outcome; `PublicationReport.gate_status`, reason and the final CLI status
separately describe later safety/publication refusals. Reports are consumed at the actual call boundary: a later artifact
safety error with an empty report cannot erase known compile zero/nonzero/signal
or attempted invocations. Restart after durable launch intent without a durable
outcome reports unknown, never false or zero. Recovery's full-detail
`attempted_invocations` tuple is empty because argv/streams were not persisted,
not because no attempt occurred. Its `compiler_invoked`/`compiler_status` derive
from retained bounded purpose/start/returncode/lifecycle summaries. Recovery
never fabricates missing invocation details. Historical CLI recovery and
inventory entries also report `resource_details_available=false`: original font
identities were not persisted, and live resources are never consulted to fill
them in. Source commit and output commit
are independent facts. `source_commit='not_applied'` means no successful bridge
apply receipt was obtained, not proof that source bytes were untouched. A
BaseException/SIGINT during source application may leave source recovery journals;
the source outcome can remain unknown until source inventory/recovery checks.
Source and PDF recovery are separate: output-only PDF recovery never resolves
source recovery journals.

Tests cover native metadata and pinned compiler publication only in temporary
consumers, failure injection, later edits, malformed/bounded journals, fresh
interpreter recovery and real SIGKILL barriers. SIGKILL tests establish process
crash behavior, not universal power-loss safety on every filesystem or OS.
