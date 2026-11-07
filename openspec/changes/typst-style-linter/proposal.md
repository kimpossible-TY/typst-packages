# Proposal

## Why

My-Typst-style and the PDE guide contain repeatable source conventions that are currently checked by hand. Define a shared, syntax-aware linting and automatic-fix contract so consumer projects can enforce the author's rules before compilation without copying a tool or guessing mathematical intent.

## What Changes

- Initialize OpenSpec with the `spec-driven` schema and no AI tool integrations.
- Specify 32 source-backed style rules, each with a stable ID, error or warning grade, provenance, and positive and negative scenarios.
- Define reusable `core` and opt-in `pde` rule packs, consumer configuration, diagnostics, and suppression behavior.
- Separate judgment-only guidance and unsupported inference from executable checks.
- Define exact automatic rewrites, intentional presentation effects, source protection, and failures for every rule.
- Specify a shared compile gate and verified adapters for CLI, CI, watch, and editor builds across registered Typst projects.
- Merge the reviewed design first, then implement on a separate branch and draft PR. Consumer rollout remains separate from implementation.

## Capabilities

### New Capabilities

- `typst-lint-configuration`: Pack selection, input scope, diagnostics, and false-positive controls for consumers of the shared linter.
- `typst-core-style`: Mechanical math, scope, reference, book-ownership, and theme rules shared across projects (15 rules).
- `typst-pde-style`: PDE import, paragraph, footnote, highlighting, and theme-helper conventions (17 rules).

- `typst-lint-autofix`: Deterministic per-rule repairs, protected transactions, and verifiable failure outcomes.
- `typst-build-gate`: Precompile enforcement and a project/build-route coverage inventory.

### Modified Capabilities

None. This checkout had no existing OpenSpec specifications.

## Impact

This change adds only `openspec/` planning artifacts. A later implementation will live under `tools/typst-lint/` in this package repository, alongside shared packs; consumers such as PDE will keep a small `.typst-lint.toml`. This design-only PR changes no packages, consumer documents, editor settings, or deployed build routes. The original design PR #2 was already merged; this follow-up supersedes its read-only restriction. After this reviewed design is merged, implementation is authorized on a separate branch and draft PR. Installing the tool, enabling all-project adapters, and mass-rewriting real documents are not part of this PR.
