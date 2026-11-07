# Internal captured-relative graph compiler

`captured_relative_compile.py` compiles only a genuine live committed graph from
`ProtectedCapturedRelativeFix`. It adds no public source/resource selector,
registry entry, CLI route, consumer wrapper, installation, publication or recovery
schema. `captured-relative-core-v1` remains unregistered. Existing closed and
structured source profiles remain import-refusing and the existing compiler and
publisher retain their exact accepted types and ordering. In particular,
`PreparedPdfPublication` does not accept this new artifact.

## Authority and API

`compile_committed_graph(fix, snapshot, *, profile, deadline_seconds=10.0,
cancel_event=None)` requires the exact `ProtectedCapturedRelativeFix` and its
identical exact `CapturedRelativeCommittedGraph` issued after its own successful
apply. Reports, pure proofs, bare transactions, direct transaction receipts,
copies, subclasses, duck types and foreign/expired sessions grant no authority.
`fix.assert_committed_graph_current(snapshot)` rechecks retained postimage root,
unchanged dependency/config/catalog bytes and identities, ancestry, candidate,
independent replay, complete actual rule analysis, grammar and parser selection.
It is used around staging, invocations, output capture, failures, cleanup and
issuance. No discovery, parser call or fresh source read establishes a compilation
baseline. A compiler failure never rolls back a valid committed repair.

The module reuses the existing private pinned compiler/font capture, bounded
runner, attempt ledger, environment, anchored writes/captures, output descriptors,
PDF checks and 96 MiB / 128-entry workspace bounds. It does not widen the older
compiler guard or artifact. Profiles remain exactly `embedded-ascii-v1` and
`embedded-applegothic-ko-v1`; source bytes for every retained file, including
unused declarations and import literals, pass the selected full repertoire.
Korean in any dependency refuses under ASCII before resources or processes.
Unicode filename identity is separately checked by the path/layout contract.
AppleGothic remains the reviewed private local Regular face only, with no Korean
bold/italic fidelity claim and no permission to redistribute font bytes. No font
bytes enter fixtures, repository, CI, releases, uploads or cloud storage.

## Fixed compile-only scalar admission

After genuine graph admission, before compiler/font capture, workspace creation
or a probe, the fixed policy `captured-relative-scalar-render-budget-v1` limits
imported scalar-text charge to 1,048,576 bytes. There is no caller override or
public selector. It is bound to `captured-relative-module-grammar-v1`, whose quoted
module strings are unescaped. Future escaped-string grammar requires reviewed
new accounting. Capture/repair grammar and resource profiles remain unchanged.

Every bounded `proof.imported_uses` occurrence counts, including markup, math,
nested attachments, renamed/aliased and diamond-imported uses. The cache keys the
entire `DeclarationOrigin`, never equal values. Each origin's source hash and
bounded initializer endpoints are checked against retained source bytes. String
charge is initializer byte-span length minus its two actual ASCII quotes, without
repeated payload encoding or expanded-text construction. Integer charge is the
canonical `len(str(value))` for an exact nonnegative integer no larger than
2^63-1, at most 19 digits. Equal origins must keep the same exact scalar kind and
value. Boolean, none and auto render uses are inconsistent authority and refuse.
Empty strings charge zero but still count; unused literals have zero render
charge while still receiving complete source/repertoire checks. At most 8,192
uses and 4,096 origins are traversed. The total is occurrence count times origin
charge. Exceeding the fixed ceiling raises `CompileSafetyError`, with zero
attempts and no fabricated resource report, preserving committed fixes.

The artifact seals `ScalarRenderBudget(policy, limit, total_charge, use_count,
origin_count)`. This conservative logical payload admission is not a bound on
actual PDF/output size, shaping/layout work, pages, native RSS or OS resources.

## Exact private graph layout

An immutable manifest derives directly from `snapshot.sources`, retaining every
exact canonical logical source path and unchanged bytes, BOM, CRLF and imports.
Only source files are copied. Config, catalog and unrelated files/trees are not.
Independent limits are 32 files, 1 MiB per file, 4 MiB total source bytes, 64 project
directories including the project root and logical ancestors, 512 UTF-8 bytes per
path, and 16 KiB total source-path bytes. File/directory prefix collisions and
case/NFC-equivalent logical spellings refuse without normalization or renaming.

The complete logical directory plan is computed first. Reuse means the identical
logical directory only; creation is exclusive and anchored/no-follow, never an
`exist_ok=True` merge. Actual directory identities/ancestors and each source's
actual private `FileEvidence` are retained. Distinct logical names may not share
private device/inode identity. Repeated membership, mode, identity and hash checks
cover staging, invocations, output capture, failure and issuance. Unexpected
entries, aliases, symlinks, unsafe files or replacement refuse. The unchanged
workspace limits include all actual files and directories. Worst reviewed Korean
graph layout is 110 entries and 80,984,872 bytes, including the compiler, full
font, 4 MiB sources, 16 MiB PDF and 64 KiB dependencies. The limits are sampled
operational checks and bounded reads, not kernel quotas or a hostile-OS sandbox.

## Invocation and observed dependencies

The compiler is the existing pinned Darwin arm64 Typst 0.15.1, SHA-256
`08d01e5a434e821c4a1ce30dd1593e1bbc57c9b1203c4d760d8912ef4ce6a621`, with its
unchanged exact version banner. One private copied executable runs a version
probe and the existing fixed PDF invocation at the private project-root cwd.
The environment is built from nothing. No user compiler path, flags, font path,
resource lookup, packages, normalization, installation or discovery are added.
The Korean profile adds only the existing private captured font directory.
Trusted system runtime libraries remain outside the resource-copy claim.

The actual `--deps-format zero` output is bounded to 64 KiB and 32 records before
path traversal, nonempty strict UTF-8 with one NUL per entry and a final NUL.
Empty/malformed/noncanonical/duplicate entries refuse. Decoded membership must
exactly equal committed sources, including the root, transitive and unused
imports, and shared modules exactly once. Ordering is arbitrary and retained
exactly as observed; it is not sorted or fabricated from expected source names.
Paths may contain literal spaces. The fixed cwd is essential: no guessing or
normalization of another cwd's `../../` output occurs. Missing/extra evidence is
`CompileSafetyError` even with compiler status 0. Artifact `dependency_bytes`,
`dependency_paths` and `dependencies` preserve actual raw bytes, observed order,
and captured deps-file identity/hash. These source dependencies are not exhaustive
OS-read tracing. Fonts remain separate actual resource evidence.

## Reports, artifact and lifetime

The existing `CompileReport`, invocation lifecycle, raw status, typed process
failure and actual `ResourceReport` survive later safety failures. Returned probe
and compile metadata are checked type-exactly against the actual attempt ledger
before retention, so boolean/integer lookalikes cannot change observed status.
An independent original resource-report snapshot preserves truthful historical
reporting if a shared record is altered or replaced; later assignment never
establishes a newer resource baseline. No probe or
compile attempt is invented before launch. A resource report is absent until
complete actual installed/private capture; ASCII then has an explicit empty font
tuple. Safety can supersede compiler failure without erasing its observed facts.
The existing deadline range (0, 120] seconds and exact `threading.Event` type
remain required. Late cancellation/deadline expiry blocks issuance.

The new opaque frozen `CapturedRelativeCompileArtifact` retains exact resource
and source-profile/root/module grammar/parser identities, target/root hash,
explicit `aggregate_source_bytes`, frozen per-source logical path/hash/size and
actual private evidence, project directory evidence, config/catalog/evidence
hashes, installed/private compiler, output/dependency evidence, observed dependency
bytes/order, actual probe/compile/report/resource records, scalar-budget record,
and PDF bytes/hash. Every new nested record is included in a bounded type-exact
seal. Primitive facts are pinned before later process/resource callbacks, so
shared reflective mutation cannot become a newly blessed issuance baseline.
Later checks follow the retained finite shape, rejecting wrong types, missing
fields, cycles or oversized altered tuple/container shapes before traversal.

Artifact issuance is identity-registered to exactly its bridge and graph with a
weak bridge reference. `assert_artifact_current(fix, artifact)`, `pdf_bytes`,
ordinary private PDF attribute reads and ordinary dataclass serialization recheck
that association and all original graph identities, installed compiler/fonts,
nested content and PDF hash. Copies and reconstructions refuse. The context's
close or bridge collection expires access. The artifact does not keep the bridge
alive. Already-read bytes cannot be revoked, and this cooperative trusted Python
API is not a defense against arbitrary malicious reflection or an adversarial OS.
The private workspace is removed before return. Its file and directory evidence
remains historical report data and is never reopened as current after cleanup.
PDF publication and destination selection remain separate future work.
