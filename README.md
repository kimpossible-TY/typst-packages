# Custom Typst Packages

Personal Typst packages extracted from the PDE notes project.

## Packages

- `@local/text-utils:0.1.0`: text helpers, title capitalization, paragraph markers, custom highlighting.
- `@local/math-blocks:0.1.0`: theorem-like blocks, callouts, themes, proof helpers, flow boxes.
- `@local/scoped-annotations:0.1.0`: local label scopes and mannot/CeTZ annotation helpers.
- `@local/cetz-helpers:0.1.0`: reusable CeTZ legend and description boxes.
- `@local/math-book:0.1.0`: mathematical book/lecture-note template.

## Install Locally

From this repository root:

```sh
./scripts/install-local.sh
```

This copies `local/*` into Typst's local package path:

```text
~/Library/Application Support/typst/packages/local
```

After installation, other Typst projects can import the packages like this:

```typst
#import "@local/text-utils:0.1.0": *
#import "@local/math-blocks:0.1.0": *
#import "@local/scoped-annotations:0.1.0": *
#import "@local/cetz-helpers:0.1.0": *
```

## Smoke Test

```sh
./scripts/test.sh
```

The test installs the local packages and compiles `tests/package-smoke.typ`.

## Template Test

```sh
typst init @local/math-book:0.1.0 /tmp/math-book-test
typst compile /tmp/math-book-test/main.typ /tmp/math-book-test.pdf
```

## Repository Layout

```text
local/
  text-utils/0.1.0/
  math-blocks/0.1.0/
  scoped-annotations/0.1.0/
  cetz-helpers/0.1.0/
  math-book/0.1.0/
scripts/
tests/
```
