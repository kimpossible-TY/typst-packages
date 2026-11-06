# Proposal

## Why

My-Typst-style and the PDE guide contain repeatable source conventions that are currently checked by hand. Define a shared, conservative linting contract before implementing it so consumer projects can select consistent rules without copying a tool or turning editorial judgment into diagnostics.

## What Changes

- Initialize OpenSpec with the `spec-driven` schema and no AI tool integrations.
- Specify 32 source-backed style rules, each with a stable ID, error or warning grade, provenance, and positive and negative scenarios.
- Define reusable `core` and opt-in `pde` rule packs, consumer configuration, diagnostics, and suppression behavior.
- Separate judgment-only guidance and unsupported inference from executable checks.
- Record a future implementation checklist; deliver specifications only in this change.

## Capabilities

### New Capabilities

- `typst-lint-configuration`: Pack selection, input scope, diagnostics, and false-positive controls for consumers of the shared linter.
- `typst-core-style`: Mechanical math, scope, reference, book-ownership, and theme rules shared across projects (15 rules).
- `typst-pde-style`: PDE import, paragraph, footnote, highlighting, and theme-helper conventions (17 rules).

### Modified Capabilities

None. This checkout had no existing OpenSpec specifications.

## Impact

This change adds only `openspec/` planning artifacts. A later implementation will live under `tools/typst-lint/` in this package repository, alongside shared packs; consumers such as PDE will keep a small `.typst-lint.toml`. There are no package upgrades, consumer edits, runtime dependencies, linter implementation files, commits, or pushes in this delivery.
