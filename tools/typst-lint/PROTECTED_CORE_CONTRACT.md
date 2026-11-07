# Internal protected closed-core bridge

`protected_fix.py` is a trusted in-process engine bridge, not a CLI, complete
project linter, serialized authorization format, compiler gate or deployment.
All existing CLI modes remain read-only and incomplete. Nothing here installs
packages, invokes Typst compilation, selects output files, or edits real consumer
documents as part of testing.

## Narrow selection and authority

The caller supplies a canonical root, one literal root-relative `.typ` target,
an existing private journal directory outside the root, and its real
`OfflineExclusive` assertion. The bridge never creates that assertion itself.
The assertion means editors, watchers and other writers are stopped for the
whole context; the transaction lock does not establish that fact.

A present `.typst-lint.toml` must explicitly contain `rule-set = "core"` and
`include = ["the/exact/target.typ"]`. The only optional key is an `entrypoint`
with the same literal target. Default selection, other packs, glob breadth,
excludes, ignores, reference catalogs, capitalization and unknown keys fail.
Unselected files are neither parsed nor claimed to have been analyzed.

The source, configuration and entire fixed built-in rule catalog are captured
by `ProtectedTransaction`, with retained bytes and fingerprinted identities.
The reviewed catalog digest is compiled into this first profile. Exact catalog
schema, complete rule IDs and core grade/repair policies are independently
checked from those immutable bytes. All 15 required core IDs are compiled into
the bridge, never selected from mutable catalog/config metadata. The 18 PDE
records are retained and pinned as evidence, but are not claimed evaluated.

## Per-input proof and repair

For every transaction analysis invocation, the graph must contain exactly the
single selected source and those two present evidence inputs. Both evidence
values are parsed from retained bytes. The callback never rereads source,
configuration, catalog or dependencies. The official pinned syntax adapter is
actually invoked on the supplied source bytes. A fresh source/path/tree-bound
closed-grammar proof is certified and verified for that exact parser tree.
The admission contract is in `CLOSED_CORE_CONTRACT.md`; it structurally rules out
imports, calls, code, comments/directives, labels, references and resources.
Its 14 explicit inapplicability reasons discharge those core rules only for this
input. The script rule is actually analyzed using an immutable captured catalog,
with `_verify=False`, once per callback. Any skipped, suppressed or parse-error
script result fails. Reports and their partial/evaluated booleans are not used.

Finding identities use attachment preorder and operand role, not byte offsets.
These identities are checked to survive the inserted grouping. This is not an
identity scheme for arbitrary rewrites or retained unresolved diagnostics.
This first profile permits at most one actual repair pass and requires zero
final findings. An unexpected second repair pass fails before application.
The transaction's eight-pass ceiling, repeated-state defense, independent fresh
final parsing/reanalysis, source-byte limits and stale-baseline defenses remain
active and are not replaced by this narrower condition.

A separate `candidate_convergence.converge(max_passes=1)` run uses the same raw
admitted backend outside the transaction callback. It independently reparses,
reanalyzes, composes original-source edits and retains causal attribution. Its
exact bytes, pass history, initial/final rule results and script-only attributed
edits must match the transaction candidate. Every applied edit is a parentheses
insertion, so all original source bytes remain in order. Fresh evidence and
source fingerprints are rechecked again after this replay and before apply.
No source_analysis nested convergence loop is used.

## Session and storage boundary

Use `ProtectedCoreFix` as a context manager, then `validate()` and
`apply(candidate)` in that same context. Validation returns the underlying
transaction's opaque `ValidatedCandidate`, but apply accepts it only after this
bridge's own independent comparison succeeded. A token minted directly through
the trusted internal `transaction` inspection property is not accepted by the
bridge. Arbitrary trees, proofs, GraphAnalysis objects, final-byte maps, JSON or
boolean attestations are not inputs to bridge validation or application.

Default `ReadLimits` bound source files at 1 MiB, each evidence file at 4 MiB,
each graph capture at 16 MiB, metadata at 256 KiB, and journals at 64 MiB per
file / 128 MiB per journal. The transaction applies limits to capture,
revalidation, candidate growth, staging and recovery. The same source/config/
catalog bytes with changed inodes, deleted evidence, modified metadata, source
races and config/catalog changes fail revalidation. The caller must retain the
configured journal store. Recovery is explicit through `ProtectedTransaction`,
never automatically inferred or hidden by the bridge. Fault injection exists
only as the transaction's internal test hook.

Tests create isolated temporary documents, evidence copies and private journal
stores and deliberately assert offline exclusivity only for those fixtures.
No global core-completion claim, production writer wiring, consumer migration,
compiler execution or installation follows from this internal profile.
