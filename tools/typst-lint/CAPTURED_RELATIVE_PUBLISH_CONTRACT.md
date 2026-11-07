# Internal captured-relative PDF publication

`captured_relative_publish.py` composes the live captured-relative repair bridge,
its graph-only compiler and the existing output-only publication store. It adds
no CLI, wrapper, profile registry entry, installation, deployment or recovery
schema. `captured-relative-core-v1` remains unregistered. Existing compiler and
publisher accepted types and ordering are unchanged. No font bytes belong in the
repository, fixtures, CI, releases, uploads or cloud storage.

## API and required order

`PreparedCapturedRelativePdfPublication(fix, *, destination, lease,
limits=DEFAULT_PUBLICATION_LIMITS, fault=None)` requires the exact original active
`ProtectedCapturedRelativeFix`. Enter the bridge, enter the publication, then
call `fix.validate()`, `fix.apply(candidate)`, and
`fix.committed_graph_snapshot()`. Finally call
`publication.compile_and_publish(snapshot, *, profile,
deadline_seconds=10.0, cancel_event=None)` while both contexts remain active.
The publication owns exactly one compiler call. There is no publish-precompiled-
artifact, PDF-bytes, transaction-injection or transaction-property route.

The source bridge's genuine offline lease does not replace the separately minted
`OfflineOutputExclusive` assertion. The caller must actually close exporters,
watchers and all other output writers, including writers to ancestors and journal
storage. Neither lease detects external writers or establishes an OS sandbox.

## Pre-validation admission and preparation

The bridge's private `_borrow_publication_transaction()` returns only its original
session-owned locked transaction, after active original-object, capture,
selection, original-input and pristine-state checks. Shallow copies and unissued
objects refuse. Monotonic validation-attempt and apply-attempt flags are set at
method entry, before even early checks, and never reset. Any earlier attempt,
candidate, history, retained validation, applied state, committed receipt or
transaction-issued candidate refuses preparation. Transaction ownership never
moves to the publisher. After store preparation and all its hooks, the identical
borrow and complete pristine checks run again.

Preparation inventories existing output journals and refuses pending or corrupt
history before capturing the destination or starting validation, application or
compilation. Existing terminal journals are strengthened before proceeding. The
literal destination and its anchored ancestry, original arbitrary output bytes,
metadata and identity, or original absence, are captured durably. Collision checks
protect every transaction target and evidence input, including transitive and
unused modules and external config/catalog evidence, the source journal store,
and existing runtime compiler and launcher paths. The output store protects its
own paths and refuses aliasing, not merely equal path spellings.

Preparation is resource-neutral. Only the explicitly selected resource profile
adds its font collision checks before a launch-intent event or compiler call.
ASCII does not stat an unselected AppleGothic font. Profiles remain exactly
`embedded-ascii-v1` and `embedded-applegothic-ko-v1`; the graph compiler retains all
its source, scalar-render, repertoire, resource, invocation and workspace limits.
The deadline must be a finite number in (0, 120] seconds, excluding booleans, and
cancellation must be absent or an exact `threading.Event`.

## Live committed authority and chronology

After genuine application, the publisher retains its original publication,
bridge, owner, capture, selection/session and transaction identities. Immutable
identity records are independently registered, so replacing instance fields
cannot redirect failure reporting, reconciliation or closure to another store.
Registration is dropped on closure or failed preparation. Binding checks do not call the original capture's filesystem-baseline assertion after
apply. Instead the exact committed graph guard rechecks the retained repaired
root and unchanged dependencies/config/catalog. Reports grant no authority.

The publication checks graph authority and the original output, validates the
explicit resource profile and selected-font collisions, writes a durable launch
intent, then calls `compile_committed_graph` exactly once. The launch event's
`source_sha256` is derived from the guarded retained
`snapshot.sources[snapshot.target]`; a committed graph has no such scalar field.
Its `profile` field remains a resource-profile identity, never a source-profile
name. Source journal identifiers are reporting metadata from the retained source
transaction, not a repaired-source authorization token.

The actual compiler report is snapshotted independently at that call's return or
exception boundary, before later callbacks. Later empty safety reports, changed
shared artifact/report records and public report mutation cannot erase or rebase
actual invocation, raw status, failure or resource observations. `compile_report`
returns fresh historical values from retained original facts. Artifact, graph
and resource guards run before the compiled event and guarded PDF access. After
staging, the existing store's replacement callback repeatedly rechecks the
original publication/bridge/transaction/artifact binding, full graph and installed
resources, cancellation and publication deadline before rename and completion.
No prior compiler artifact or caller-provided PDF bytes can establish chronology.

## Failure, output-only recovery and source independence

The existing `PublicationReport`, `PublicationError` and `PublicationPending`
shapes are reused. Unsupported requests use gate status 2; safety, compiler and
publication failures use gate status 3 while retaining any observed raw compiler
status. Reports describe source commitment separately from output disposition.

Before rename, failure preserves the original output or a conflicting external
change. A recognized completed rename allows only conditional output rollback
from that exact observed postimage. Unknown rename effects and uncertain durable
completion remain unresolved and require explicit recovery. After a durable
published event, later callback/reporting failures never roll back the output.
Compiler or publication failure never undoes a committed source repair. Exiting
an unused prepared context records an unpublished abort when safely possible.

Recovery remains the existing schema-1 output-only historical protocol. It does
not rediscover, parse, compile or read live source/config/catalog/font resources,
and it never restores source files. Terminal recovery strengthens historical
journal durability without overwriting subsequent user output changes. Process-
death tests are evidence for process-death behavior, not a power-loss guarantee.
