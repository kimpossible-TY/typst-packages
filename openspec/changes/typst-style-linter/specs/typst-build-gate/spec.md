# Typst precompile gate

## Purpose

Run the shared linter and eligible automatic fixes before authoritative compilation on each registered Typst project and build route. Agent instructions alone are not enforcement.

## ADDED Requirements

### Requirement: Compile the exact successfully checked candidate
The gate SHALL fix, reparse and check inputs before compiling their immutable snapshot under the snapshot/publication contract in `design.md`. It SHALL preserve logical paths, resources and compiler configuration, fail closed on unsupported resources, and publish only a successful, still-current generation. Compiler failure SHALL retain valid fixes and previous output; validation/write failures SHALL use protected recovery.

#### Scenario: Failure before compiler invocation
- **WHEN** a finding remains, required analysis is unimplemented, input is invalid, tool version mismatches, or a source transaction is unsafe
- **THEN** the compiler spy records zero invocations, the gate returns the corresponding lint failure and no requested output is replaced.

#### Scenario: Edit during compilation
- **WHEN** source or a dependency/config/catalog changes after snapshot validation
- **THEN** the stale generation's output is not published, the previous output remains unchanged, and the wrapper returns nonzero gate status 3 even if `compiler_status` is 0; a new gated generation is required.

#### Scenario: Compiler rejects checked source
- **WHEN** the real compiler fails after successful fixing/checking
- **THEN** the wrapper reports compiler phase and its real status, retains the valid source fixes, and preserves any previous output rather than report lint failure or success.

### Requirement: Preserve compiler invocation and failure semantics
The gate SHALL use an absolute nonrecursive compiler path and an argument vector separated from wrapper options by `--`, without shell interpolation. It SHALL preserve supported compiler options and forward signals. Unsupported compiling modes SHALL fail closed. Explicit help/version or non-compiling dispatch MAY pass through. Reports SHALL include `phase` and `compiler_invoked`; no silent force/fail-open mode SHALL exist.

#### Scenario: Spaces and multiple theme inputs
- **WHEN** a supported compile uses paths with spaces, a font path and `--input theme=dark`
- **THEN** the compiler receives the corresponding unchanged argument values and compiles the checked snapshot with the same theme/fonts and requested output format.

#### Scenario: Missing tool or unsupported stdin
- **WHEN** the required linter is missing or stdin compilation cannot be safely snapshotted
- **THEN** compilation is blocked with an actionable message, never silently delegated to plain `typst`.

### Requirement: Verify each project's actual compilation routes
Rollout SHALL inventory selected roots, entrypoints and CLI, CI, watch, editor save/preview/export routes with managed/unmanaged status. Consumers SHALL reuse the pinned shared tool and small config. AGENTS.md SHALL NOT count as interception. All-project enforcement SHALL require every inventoried route to be verified or disabled, including new-project templates; unmanaged draft preview SHALL remain a disclosed exception.

#### Scenario: Shell wrapper with native editor compiler
- **WHEN** CLI uses the wrapper but Tinymist compile-on-save uses an embedded compiler
- **THEN** the editor route remains unmanaged until a verified precompile adapter intercepts it or authoritative native compilation is disabled, and the project is not reported fully enforced.

#### Scenario: No automatic rollout in implementation PR
- **WHEN** the shared linter is developed and tested
- **THEN** tests use isolated consumer fixtures and no real project source, editor setting or global PATH is rewritten as a side effect.

### Requirement: Gate every watch generation and handle unsaved buffers
A supported watch adapter SHALL own each rebuild, observe source/import/config/catalog changes, debounce and serialize per root, ignore only its exact own-write events, and requeue independent edits. It SHALL NOT run lint once and then hand control to ungated native watch. Editor adapters SHALL operate on versioned buffers and verified save/export sequences, never blind disk writes or silently saved dirty buffers. Unavailable editor precompile hooks SHALL remain explicit rollout blockers.

#### Scenario: Fix triggers another filesystem event
- **WHEN** a watch generation writes its own converged repair
- **THEN** the matching own-write event does not cause an infinite loop, but a subsequent differing external edit starts a new gated generation.

#### Scenario: Unsaved preview change
- **WHEN** the editor has unsaved content different from disk
- **THEN** an authoritative export uses the validated versioned-buffer snapshot through its adapter or is blocked; a successful disk lint is not presented as validation of that preview.

### Requirement: Enforce CI without committing generated repairs
CI SHALL pin compiler/tool/pack versions and gate artifacts using the same catalog. Default CI check mode SHALL fail when fixes are needed, without mutating its source checkout. An explicitly configured fix-first CI route MAY repair only a disposable copy, retain its patch as evidence and compile that verified snapshot. Neither route SHALL commit/push source or publish artifacts after failed lint. Tests SHALL distinguish source-clean CI from disposable fix-first CI.

#### Scenario: CI finds an automatic repair
- **WHEN** default CI sees a bare script operand
- **THEN** it reports the required diff and fails before the compiler; in the explicitly configured disposable fix-first mode it repairs the copy, records the diff and compiles only if final checks pass.


### Requirement: Distinguish compiler success from publication success
The wrapper SHALL return 0 only after compiler success, final input/version validation and output publication all succeed. Postcompile stale-generation, publication or recovery failures SHALL return gate status 3 and preserve the previous output. It SHALL retain `compiler_status` separately from its overall status. A failed compiler SHALL preserve its status or termination signal.

#### Scenario: Output cannot be safely published
- **WHEN** the compiler returns 0 but final publication fails
- **THEN** the wrapper reports publication phase, compiler status 0 and overall gate status 3, leaves the previous artifact unchanged, and does not claim build success.
