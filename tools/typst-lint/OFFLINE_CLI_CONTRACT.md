# Explicit offline closed-core CLI profile

This is a narrow opt-in writer for one closed literal `.typ` input. It is not a
general project linter, a compilation gate, a dependency resolver, or consumer
rollout. Global rule acceptance remains **33 PARTIAL / 0 COMPLETE**. No compiler
is invoked by any route described here. The separate explicit compile and PDF
commands are documented in
[EXPLICIT_COMPILE_CLI_CONTRACT.md](EXPLICIT_COMPILE_CLI_CONTRACT.md).

## Commands and authority

```sh
./typst-lint fix --root ROOT --profile closed-core-v1 --offline-exclusive --target main.typ --format json
./typst-lint fix --root ROOT --profile structured-core-v1 --offline-exclusive --target main.typ --format json
./typst-lint journal-status --root ROOT --format json
./typst-lint recover --root ROOT --offline-exclusive --target main.typ --journal-id ID --format json
```

A new fix requires an exact `--profile closed-core-v1` or
`--profile structured-core-v1`, `--offline-exclusive`, and literal `--target`
options. The assertion means that editors, watchers and
all other source writers remain stopped throughout the operation. Git state and
the cooperating root lock do not establish or imply this assertion. The CLI
constructs the engine assertion only after the explicit flag and all command
combinations are validated.

The current config must explicitly contain `rule-set = "core"` and
`include = ["main.typ"]`, using the supplied literal target. Only an optional
identical `entrypoint` is additionally accepted. No unknown keys, wider
selection, PDE pack, glob, package path or catalog override is accepted.
`closed-core-v1` remains the unchanged `closed-core-markup-v1` literal prose/math
grammar. Only explicit `structured-core-v1` selects `closed-core-markup-v2`,
adding recursively literal headings, `-` list items, automatic `+` enumeration
items, strong text and emphasis. All calls, imports/includes, comments/directives,
bindings, labels, explicit numbered enumerators, term lists, resources and other
excluded constructs remain refused, including inside nested wrappers. No alias,
latest name, source-selected fallback or silent widening is available. See
[CLOSED_CORE_CONTRACT.md](CLOSED_CORE_CONTRACT.md).

`ProtectedCoreFix.validate()` and `.apply()` run in the same live context. The
bridge owns parser invocation, pinned full-catalog/config evidence, all 15 core
obligations, actual script evaluation, independent replay/history comparison,
clean final findings and session-bound token checks. General `report()` output
never authorizes this writer. Neither serialized candidates, coverage booleans,
reporting summaries, fault hooks, alternate catalogs/stores, compiler paths nor
compiler arguments are accepted as CLI authority.

New options with `check`, `--dry-run`, or `typst-style compile` are rejected,
including authority options buried in a compile tail. Duplicate options and
unknown/raw positional arguments are rejected by the offline route. The ordinary
check, dry-run, unprofiled fix and compile routes retain their read-only exit-2
behavior; ordinary compile arguments after `--` remain inert. No wrapper,
consumer AGENTS.md change, installation or deployment is performed.

## Fixed storage and retention boundary

All three commands derive exactly
`canonical_root.parent / ".typst-lint-journals"`, with the existing
`root-<sha256(canonical_root_path)>` namespace. There is no config, CLI or
environment override and no fallback location. Explicit fix may create only
that sibling store under a genuine offline assertion and cooperating root lock,
using the transaction's no-follow owner-only 0700 creation and parent fsync.
Existing stores are never chmodded. Owner, metadata, path identity and
same-filesystem restrictions remain enforced.

Retain the store, including completed journals, and the original canonical root
location while recovery might be needed. This fixed route does **not** discover
journals written to different stores by internal API callers, detect deleted or
forged stores, authenticate arbitrary journal provenance, or migrate journals
for renamed/relocated roots. An absent store or namespace is only an observed
absence, not proof that no historical transaction existed.

`journal-status` is genuinely read-only. It takes the cooperating root lock and
reuses bounded manifest/event validation without constructing an offline
assertion, creating directories, loading current config/source, or recovering
anything. It distinguishes absent store/namespace from observed retained
journals. Incomplete and unreadable/corrupt entries stay visible and pending.
It makes no assertion about editor state or current source safety.

Recovery requires the actual offline flag, one literal target allowlist and one
32-character lowercase hexadecimal journal ID. It uses the existing
`recovery_only` engine and requires an existing store/namespace. It does not
create storage, scan alternative stores, or require current config/catalog or
closed-profile admission. It reconciles only the named journal and restores
only recognized postimages. Later user changes remain untouched, with unresolved
paths and fingerprints reported. There is no force, conflict acknowledgement,
store migration or arbitrary overwrite option in this CLI profile.

## Reports and exit codes

Successful fix output contains actual selected profile/grammar_version/target,
original and final SHA-256,
exactly 15 required IDs, the actually evaluated script rule, 14 source-proven
inapplicability reasons, applied insertions, modified paths and journal ID.
The immutable reporting summary is derived only after verified independent
replay/history checks, and is exposed only after successful apply. It is never
used as write authority. No-op fixes create no journal and report no insertions
or modified paths.
Text output likewise identifies the selected source profile and grammar.
Session selection drift or retained proof mutation is a safety refusal (3),
not an invalid-input refusal (2). The verifier never selects its grammar from
supplied proof fields. Recovery never resolves or requires a current profile;
the source journal schema is unchanged.

`coverage.input_profile_complete` refers only to this input/profile.
`coverage.global_complete` remains false, with 33 global rules PARTIAL and zero
COMPLETE. `compiler_invoked` is always false and `compiler_status` is null.
Inventory appears under `inventory`; recovery results under `recovery_report`.
Conflict fingerprints are serialized explicitly without deepcopying immutable
mapping proxies.

- **0:** successful fix/no-op, complete explicit recovery, or complete observed
  inventory, including an explicitly distinguished absent store/namespace.
- **2:** invalid arguments/config/profile/target, initially absent/non-directory
  root argument, closed-grammar refusal, or
  parser/adapter failure, including initially invalid source UTF-8. A later
  source-encoding change during revalidation remains a safety failure (3).
- **3:** unsafe/unavailable lease, lock, path, storage or metadata; stale identity,
  later root identity/disappearance race, convergence/provenance disagreement,
  write failure, incomplete/corrupt journal
  or unresolved recovery.
- **1:** reserved for active findings. This profile requires final zero findings
  and has no normal successful-write status-1 outcome.

Known failures are traceback-free and contain stable `error_code` and `phase`.
`RecoveryRequired` includes its explicit journal list;
`TransactionFailure` includes its recovery report. Argument errors produce
structured JSON when `--format json` or `--format=json` was requested for the
new route. Ordinary parser presentation remains unchanged.
