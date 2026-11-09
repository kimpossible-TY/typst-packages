# Captured text-utils paragraph publication and explicit CLI

`captured_text_utils_paragraph_publish.py` composes the original paragraph repair
bridge, its separate package compiler and the unchanged schema-1 PDF store.
It accepts no graph artifact, old single-source snapshot, caller PDF bytes or
precompiled artifact. It adds no consumer wrapper, installation, deployment,
package mutation or recovery schema. Global 33-rule coverage remains PARTIAL.

## Same transaction, prepare before repair

Enter `ProtectedCapturedTextUtilsParagraphFix(root, target,
package_path=explicit_canonical_absolute_path, ...)`, then enter
`PreparedCapturedTextUtilsParagraphPdfPublication(fix, destination=literal_pdf,
lease=separate_output_lease)`. Only afterward call `fix.validate()`,
`fix.apply(candidate)` and `fix.committed_snapshot()`. Finally call
`publication.compile_and_publish(snapshot, profile="embedded-ascii-v1",
deadline_seconds=10.0, cancel_event=None)` while both contexts remain active.

The bridge's entry already performs read-only initial admission, parsing and
actual-rule cleanliness analysis. Preparation is before every validation,
candidate or apply attempt, not before all analysis. Its private pristine borrower
requires the exact original active bridge/session and locked source transaction,
original unchanged input capture, no history, candidate, validation, applied state
or committed receipt, and independently monotonic validation/apply-attempt flags.
A failed earlier attempt also refuses preparation. The identical borrower runs
again after preparation hooks, so a callback cannot reorder source work into an
apparently pristine publication. No transaction is transferred or newly locked.

The caller's source-exclusive assertion does not replace the separate genuine
`OfflineOutputExclusive` assertion. The caller must stop exporters, watchers and
all writers to output, its ancestors and journal storage. These assertions do not
detect writers or establish an OS sandbox.

Preparation inventories and strengthens historical output journals. Pending or
corrupt history refuses before destination capture, validation, source apply or
compiler invocation. The original arbitrary output bytes, identity and metadata,
or original absence, plus anchored ancestry are durably prepared. Collisions
protect the root, all four read-only config/catalog/manifest/lib evidence paths,
the source transaction store, output stores and reserved storage paths, runtime
launchers and pinned compiler paths. Checks include aliases, not only spellings.
Preparation is resource-neutral. The selected resource's protected paths are
checked before launch intent; ASCII does not inspect AppleGothic or other fonts.

## Original ownership and one compiler call

Independent weak registries retain original immutable construction and binding
records: publication, bridge, owner, session, capture, baseline graph, transaction,
store, destination, output lease, limits and fault callback. Replacing instance
fields, copying a publication, rebinding a foreign equivalent transaction/store
or swapping its callback cannot redirect authority, rollback or closure. Closing
or failed entry removes active registrations; historical report facts remain
available while the publication itself is alive. Contexts cannot be reused.

An independently retained monotonic attempt record allows exactly one compile
attempt. Resetting exposed instance flags does not reset it. The genuine committed
snapshot is guarded before output comparison, launch intent, the compiler call,
PDF access and each publication replacement authority callback. A later source or
read-only dependency change refuses without minting a new baseline. The original
capture binding check is distinct from its original-input filesystem assertion:
a genuine repaired root must not be compared to its pre-repair source bytes.

Only `embedded-ascii-v1` is accepted here. Existing single-source and graph Korean
routes retain their prior behavior. The API deadline is a finite number in
(0, 120] seconds excluding booleans, and cancellation is absent or an exact
`threading.Event`. Production CLI always passes the unchanged 10-second deadline.
The paragraph compiler retains its separate exact root/package dependency
validator and original private staging, isolation and process bounds.

The launch-intent event records the guarded repaired root digest, original source
journal identifier and retained config/catalog hashes. Its `profile` continues to
mean resource profile. The compiler must return a genuine artifact owned by this
bridge and snapshot, and its artifact guard must pass before any observed package
evidence is recorded. The publisher never broadens a compiler's accepted types.

## Honest historical observations

At the actual compiler call's return or exception boundary, bounded immutable
compiler report facts are copied independently. Later empty safety reports,
changed shared artifact/report objects or exposed flag changes cannot erase
actual attempts, raw compiler status, failure details or resource observations.
`compile_report` and `report` return newly reconstructed historical values from
independently retained original fields, including publication finality. Source commitment
and its journal identifier are retained once live committed authority is admitted;
a later callback that corrupts current bridge state cannot relabel committed fixes
as unapplied. Cancellation immediately after apply can use the bridge's retained
historical successful summary without granting it compile authority.

`package_observation` is absent until a returned genuine artifact passes its
current guard. It then returns fresh historical records containing source profile,
root grammar/parser contract, config/catalog hashes, all four original evidence
hashes, actual observed dependency path order and dependency-file evidence,
staged root and two staged package file hashes/sizes/private-file evidence, and
package directory evidence. The root's logical staged name is `project/<target>`;
package logical names are `packages/local/text-utils/0.1.2/{typst.toml,lib.typ}`.
Actual dependency membership is exactly the root's original relative target and
absolute private lib/manifest paths, in any observed order, encoded as UTF-8 NUL
records with a final NUL. Expected membership is never manufactured as an observed
probe. There is no invented graph edge or scalar render budget for this profile.

The publisher retains only primitive projections plus independently retained
compiler evidence records, not source or PDF bytes. Publisher-specific report
records are reconstructed explicitly; they do not widen compiler artifact types.
Reports, hashes, detached JSON and historical observations grant no live authority.

## Failure and durable output finality

Compiler or publication failure never rolls back committed source fixes. Before
rename, the original output or a later conflicting edit is preserved. Only a
recognized completed rename's exact observed postimage permits conditional
output rollback. An unknown rename effect, unrecognized newer output or uncertain
durable completion remains unresolved and requires explicit recovery. A durable
published event is final: later callback, allocation, reporting or teardown
failure does not roll back it. An unused prepared context records an unpublished
abort when the original output can still be confirmed.

The unchanged `PublicationReport` separates gate status, raw compiler status,
source commitment and output disposition. Unsupported requests use gate 2;
safety/compiler/publication failures use gate 3. KeyboardInterrupt/SystemExit
reconcile permitted output-only cleanup and retain interruption semantics.
Executable cancellation/signal coordination, cleanup checks and raw natural
compiler exit/signal propagation reuse the existing CLI classifier unchanged.

Recovery and status remain the existing schema-1 historical output-only protocol.
They do not parse or compile sources, inspect current package/config/catalog or
fonts, or restore sources. Terminal recovery strengthens historical durability
without overwriting subsequent output changes. Original argv/streams/resources
that were not persisted are explicitly unavailable during historical recovery.

## Explicit CLI selection and reports

The source profile `captured-text-utils-paragraph-v1` is registered only in
`KNOWN_OFFLINE_SOURCE_PROFILES`. It never enters `KNOWN_SOURCE_PROFILES`, the
old single-source grammar map, compiler or publisher. The explicit routes are:

- `fix --root ROOT --profile captured-text-utils-paragraph-v1
  --offline-exclusive --target main.typ --package-path /absolute/packages`
- `compile --root ROOT --profile captured-text-utils-paragraph-v1
  --offline-exclusive --target main.typ --package-path /absolute/packages
  --resource-profile embedded-ascii-v1 --offline-output-exclusive
  --output build/main.pdf`

Both require one canonical bounded literal root-relative `.typ` target and the
explicit canonical absolute package root. The original package-path spelling is
checked before `Path` normalization, root access or lease construction, including
UTF-8, controls/backslashes and the full fixed lib identity's 4096-byte limit.
No ambient package path, environment lookup or normalization grants admission.
Target/output/profile/command/option pairings and duplicates are likewise checked
before root access wherever purely decidable. Source-only fix accepts no output
or resource options. Compile accepts no source recovery journal identifier.

Old profiles, journal/output status and both recovery commands reject
`--package-path`. Recovery needs no current profile or package selection. Ordinary
check, dry-run, unprofiled fix and typst-style/unprofiled compile remain read-only
exit-2 routes; the new explicit profile never makes them repair or compile.

Paragraph source reports preserve actual repaired byte spans as `applied_edits`,
not insertions. They expose actual original/final/config/catalog/lib hashes, the
admitted local text-utils 0.1.2 identity and retained manifest/lib file hashes,
root grammar/parser contract, all 33 required obligations, five evaluated actual
rules and 28 structural discharges. No-op fixes report empty edits/modified paths
and no new source journal. Global completeness remains false, all 33 rules PARTIAL.
Existing profiles keep their prior output formats. The CLI detaches a successful
source summary immediately after apply, before compiler/cleanup callbacks.

Compile results use `package_observation`, its availability flag and an explicit
unavailability note when no guarded artifact was obtained. Original attempt and
resource details remain in the existing compile/publication reports. Historical
status/recovery never invents those observations. The actual source fix may be
committed even if compilation or PDF publication later fails.
