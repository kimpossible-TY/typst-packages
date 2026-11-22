# Typst automatic fixes

## Purpose

Apply the author's mechanical style rules automatically, including their intended presentation effects, without overwriting unrelated edits or guessing missing mathematical intent. `fix-policy.md` defines the normative repair contract for all 33 rule IDs.

## ADDED Requirements

### Requirement: Apply only declared deterministic repairs
The linter SHALL implement separate check, dry-run and fix modes. Check and dry-run SHALL NOT modify source. Fix SHALL apply all eligible, unsuppressed repairs from `fix-policy.md` without a per-finding prompt when the protected transaction is available. It SHALL report a reason for each unavailable repair. Rule grades SHALL NOT determine repair eligibility. No catalog target, namespace, mathematical expression, punctuation or helper import SHALL be invented outside an explicit repair contract.

#### Scenario: Author-intended paragraph effect
- **WHEN** a known new-prose boundary before lowercase `this consequence follows.` lacks its marker and the shared paragraph show binding is verified
- **THEN** the marker repair inserts the blank-line/marker form while preserving the original prose bytes; a separate capitalization repair may change the initial only under its own English/context policy, and rendering verifies the intended source case and indentation.

#### Scenario: Missing reference has no unique correction
- **WHEN** a configured catalog lacks the requested global label
- **THEN** no substitute label/link is written, the diagnostic records `fix_unavailable`, and the authoritative compile gate is blocked.

#### Scenario: Intentional local exception
- **WHEN** a valid suppression covers a finding
- **THEN** neither that finding nor its automatic repair is active; the suppression remains unchanged and counted.

### Requirement: Converge and validate a complete candidate before writing
The linter SHALL plan from immutable bytes, merge identical edits, defer conflicts, and reparse/re-lint each pass. Repeated states or no convergence after eight passes SHALL fail. Only the declared dictionary-call form MAY be repaired from malformed syntax. The final candidate SHALL be idempotent with complete analysis and no new findings; pre-existing unresolved findings MAY remain under the partial-fix contract. Missing implementations SHALL NOT count as clean coverage.

#### Scenario: Interacting footnote and paragraph policies
- **WHEN** a prose line ending in a footnote is followed by a blank line and paragraph marker
- **THEN** the candidate inserts `#parbreak()` before the blank line, preserves the paragraph, satisfies both gap rules and makes zero edits on the next fix.

#### Scenario: Conflicting or cyclic rewrites
- **WHEN** repair passes repeat a previous state or unresolved span conflicts remain at the pass limit
- **THEN** the run returns a safety failure, writes no candidate to source and does not invoke the compiler.

### Requirement: Protect writable scope and existing work
Only selected regular UTF-8 source files inside the canonical consumer root SHALL be writable. The linter SHALL reject symbolic/hard links and escaping write paths, preserve unedited bytes, comments, newlines, BOM and mode, and never rewrite excluded/generated inputs, packages, catalogs or external dependencies. Existing dirty-worktree changes SHALL form the baseline and SHALL NOT be stashed, reset, staged or committed. Before/after recovery journals SHALL be kept outside selected source inputs.

#### Scenario: Existing unrelated edit
- **WHEN** a selected file has an author's uncommitted paragraph edit and one eligible script repair
- **THEN** only the script span is changed and the paragraph edit and Git index remain untouched.

#### Scenario: Imported package or link
- **WHEN** a repair would target an external package, excluded file or symlink/hard-linked source
- **THEN** the write is refused with a path-specific reason and no external file is mutated.

### Requirement: Coordinate editor versions and recover incomplete writes
Mutation SHALL follow the protected transaction in `design.md`: root lock plus a verified editor versioned-edit/save lease or explicit offline-exclusive workflow, baseline validation, undoable edits and durable recovery journal. A lock or clean Git status SHALL NOT prove editor safety. Unknown buffer state and incomplete writes SHALL block compilation; rollback SHALL preserve later user changes.

#### Scenario: Stale editor version
- **WHEN** the user edits a buffer after the repair plan is calculated
- **THEN** its versioned edit is rejected, no stale disk replacement occurs, and compilation waits for a fresh generation.

#### Scenario: Partial write and competing change
- **WHEN** one file was replaced, a later write fails, and the first file now contains a newer user edit
- **THEN** recovery does not overwrite that edit, reports exactly the changed/unrestored paths, and leaves the compile gate blocked until resolved.

#### Scenario: Disk caller cannot prove exclusive access
- **WHEN** CLI fix has neither a cooperating editor lease nor an explicitly selected offline-exclusive workflow
- **THEN** it returns a proposed patch and safety failure without changing source or compiling a potentially stale tree.

### Requirement: Verify declared effects and expose outcomes
Each repair SHALL have exact-edit, refusal, idempotence and preservation fixtures, with compile/render assertions for intended presentation effects. Reports SHALL expose repairs, coverage, findings, suppressions, versions, hashes and recovery. Exit codes SHALL be 0 clean, 1 active findings, 2 input/config/parse/coverage failure and 3 safety/write/recovery failure. Successful fixing SHALL NOT claim compilation or visual correctness.

#### Scenario: Known formatting difference
- **WHEN** a cancellation repair switches the default stroke to the verified shared theme helper
- **THEN** tests verify unchanged arguments and the intended light/dark palette effect rather than reject the repair for changing pixels.

#### Scenario: Remaining warning
- **WHEN** fix cannot safely transform an active warning
- **THEN** it retains warning grade, returns active-findings status and blocks the authoritative gate rather than relabel it an error or silently continue.


### Requirement: Retain valid repairs with unresolved findings
Eligible converged parse-valid repairs SHALL be applied even when pre-existing diagnostic-only findings remain, provided complete post-fix analysis introduces no new unsuppressed findings. The result SHALL remain unsuccessful with active findings and SHALL NOT compile. Unsupported required analysis or a safety/parse failure SHALL block source application rather than approve an unchecked repair.

#### Scenario: Fixable script beside an unknown catalog label
- **WHEN** one input contains a bare script and a missing catalog label, with complete supported analysis and a protected write transaction
- **THEN** the script is repaired, the unresolved label remains unchanged, fix returns 1 and the compiler is not invoked.
