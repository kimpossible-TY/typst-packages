# Protected transaction read bounds

This storage primitive underlies the explicitly selected offline closed-core
CLI profile; ordinary analysis/fix/compile routes remain read-only.
It does not implement a Typst analyzer, authorize consumer deployment, or make
the offline-exclusive assertion equivalent to detecting unsaved editor buffers.

`ProtectedTransaction(..., limits=ReadLimits(...))` accepts an immutable,
validated set of byte ceilings. The defaults are:

| Field | Default | Scope |
| --- | --- | --- |
| `max_source_bytes` | 1 MiB | Each selected source, candidate, or source staging inode |
| `max_evidence_bytes` | 4 MiB | Each read-only analysis input |
| `max_capture_bytes` | 16 MiB | Combined source/evidence payload and captured xattr names/values in each graph view |
| `max_metadata_bytes` | 256 KiB | Xattr names and values on each inspected inode |
| `max_journal_bytes` | 64 MiB | Each manifest or event JSON file |
| `max_journal_total_bytes` | 128 MiB | Combined manifest and events in each journal |
| `max_directory_entries` | 100,000 | Entries enumerated in each journal namespace or artifact directory |

Names include their NUL terminators in metadata accounting. Absent optional
evidence consumes zero payload bytes. Limits require nonnegative integers,
excluding booleans. Zero permits empty content only. The `limits` property is
read-only. A dict, mutable limits substitute, float, NaN, or infinity is refused.

## Reads and copies

The initial descriptor stat rejects an already oversized file before reading its
payload. Streaming requests are at most 64 KiB and at most the remaining ceiling
plus one byte. That extra byte detects concurrent growth; it is never accepted
or retained as captured content. Exact-size EOF is accepted. The final descriptor
and no-follow named stats must still agree with the original stable identity,
size, timestamps, ownership, mode, links, and flags. Every descriptor closes even
on an exception or process-level Python interruption. A real SIGKILL relies on
the OS to close descriptors and release locks, as before.

Xattr name lists and individual values are size-queried using system libc on
macOS/Linux. Allocation occurs only after checking the remaining metadata
ceiling. A change between querying and retrieving metadata is refused. Existing
ACL, filesystem-flag, inode, owner, group, and permission protections remain in
place. Source metadata is preserved only onto new staging inodes.

The same shared graph budget applies to initial capture, every revalidation,
and each candidate's projected sizes before constructing its changed bytes.
Staging readback is additionally limited to the exact intended byte count, so a
concurrently enlarged stage cannot turn readback into an unbounded allocation.
Neither stage verification nor final validation falls back to an unlimited read.

These are finite content bounds, not a promise that process RSS equals 16 MiB.
Immutable snapshot maps share byte objects rather than copy payloads. Convergence
can retain up to nine source versions under its existing eight-pass ceiling.
Transient bounded copies, journal JSON/base64 representations and decoding,
metadata, fingerprints, Python object overhead and the trusted analyzer have
separate costs. Directory enumeration streams up to its per-directory entry
ceiling before deterministic sorting. This API does not promise a global time,
aggregate file-count or total-process-memory limit, or constrain allocations
performed by a trusted callback itself.

## Explicit live capture options

`EvidenceInput.utf8` defaults to false. Opting in selects the entire source-like
reader policy: source-byte limits, UTF-8, owner/group and existing ACL/flag
checks, not decoding alone. Read-only evidence remains read-only.
`retain_ancestry=True` additionally retains the actual no-follow descriptor chain
at capture and compares it during live revalidation, application and committed
snapshot use. The default is false. `captured_fingerprints` exposes immutable
inspection maps from retained records, never refreshed file-stat authority.

These options support the internal relative-input capture foundation described
in [CAPTURED_RELATIVE_INPUT_CONTRACT.md](CAPTURED_RELATIVE_INPUT_CONTRACT.md).
They do not serialize new fields or upgrade rollback and historical recovery to
full ancestry attestation. Those retain schema-1 immediate-parent semantics.

## Issued candidate payloads across callbacks

Validation retains independent immutable source-byte, finding and pass facts,
separate from the caller-visible token and its source-map backing object. Apply
requires the genuine issued token with its exact retained fields and map shape,
then freezes source bytes and findings from issuance-owned facts before filesystem
operations or fault callbacks. Changed selection, manifest postimages, staging,
no-op results and successful results all use those same private inputs.

Reflective token or map changes after that boundary cannot change written bytes
or make manifest postimages disagree with staged/committed bytes. Bridge-specific
postcommit checks may still refuse an altered token while preserving correctly
committed original fixes and honest reporting. This is issued-object alias
defense, not a sandbox for arbitrary Python code. Analysis coverage, fingerprint
checks, rollback conflicts and journal/recovery schema are unchanged.

## Journals and recovery

The schema remains version 1. Manifest and event reads share a per-journal byte
budget, with the same streaming/growth checks. Journal writes also enforce both
the per-file and combined JSON ceiling. Base64 source/evidence values are checked
for bounded encoded size before decoding and checked again for decoded length.
Manifest before/after graph views each enforce their source/evidence aggregate
ceiling. Xattr decoding also accounts for name bytes before value allocation.

Recovery never relaxes a ceiling. Live source reads, repeated checks, recovery
stages and completion checks remain bounded. Oversized or corrupt journals remain
pending; an explicit recovery attempt reports its limit/schema error. Oversized
live user work is preserved and reported unresolved rather than truncated or
overwritten. Historical version-1 journals above a newly chosen ceiling may be
opened only by deliberately supplying a larger `ReadLimits` to the recovery-only
context. Increasing a bound does not waive any schema, fingerprint, mode, parent
identity, stage, or ownership requirement.

All tests use temporary consumers. They cover exact limits, oversize rejection,
combined captures, invalid/mutable limits, bounded growth reads and closing,
source/evidence revalidation, candidate expansion, metadata, stage tampering,
JSON budgets, historical journals, and explicit recovery. Existing transaction
and real SIGKILL/restart tests remain required.
