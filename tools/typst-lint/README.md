# Typst lint (partial implementation)

This is the implementation draft for the reviewed
[`typst-style-linter` design](../../openspec/changes/typst-style-linter/design.md).
**This is not a production linter or build gate.** Real Typst parsing and one
syntax-only rule now produce diagnostics and partial in-memory repair plans.
Other required rules, protected runtime writes and compilation remain incomplete.
Every real CLI run still returns **2**, including an empty selection. No source
is changed and no compiler is invoked. Help/version return 0.

## Build the official syntax adapter

Python 3.11+ provides orchestration with no third-party Python dependencies.
The syntax-only Rust bridge pins official `typst-syntax =0.15.1`; Cargo.lock pins
its dependency graph. MSRV is 1.92; local and CI verification use Rust 1.94.1.
Build explicitly from this checkout:

```sh
cargo build --locked --manifest-path tools/typst-lint/syntax-adapter/Cargo.toml
```

The runtime uses only the known absolute, locally built
`syntax-adapter/target/debug/typst-lint-syntax` executable. It never builds,
installs or downloads dependencies itself. Missing or mismatched adapters produce
unsupported-analysis results, not a regex fallback or clean lint result. No
consumer copies, global PATH changes or package installation are needed.

The bridge parses immutable UTF-8 bytes in markup/code/math modes without
Typst evaluation, import execution, package resolution or network access. It
returns schema/version/mode/source-hash evidence and a tree of SyntaxKind byte
spans with typed MathAttach operand spans. The client checks the complete tree,
UTF-8 boundaries, root partitioning, links, errors and versions before rule use.
Limits are 1 MiB input, 16 MiB stdout, 64 KiB stderr, 100,000 nodes, depth 256 and
a default five-second process deadline. Pipes are bounded while streaming.
Exceeding a limit fails explicitly; it does not approve an incompletely read file.

## Run without modifying a consumer

```sh
/path/to/typst-packages/tools/typst-lint/typst-lint check --root /path/to/project --format json
/path/to/typst-packages/tools/typst-lint/typst-lint fix --root /path/to/project --dry-run --format json
/path/to/typst-packages/tools/typst-lint/typst-lint fix --root /path/to/project --format json
/path/to/typst-packages/tools/typst-lint/typst-style compile --root /path/to/project --format json -- main.typ output.pdf
```

All modes are currently read-only. Reports may include AST-backed proposed edits
with baseline hashes, but label them `partial-not-applicable-transaction`.
They are **not** permission or proof that the complete protected write contract
has passed. Incomplete required analysis prevents application. The `compile`
interface remains inert: arguments after `--` are never executed, no compiler is
resolved and existing outputs are never replaced. There is no fail-open option.

## Consumer configuration

Only `.typst-lint.toml` in the explicit root is read. There is no ancestor
configuration discovery, sibling-project scan or dependency execution.

```toml
rule-set = "pde" # default core; pde includes core
include = ["**/*.typ", "rules/**/*.md"]
exclude = ["vendor/**", "generated/**"]
entrypoint = "main.typ"
# catalog = "references/projects.json"

[capitalization]
language = "en"

[[ignore]]
paths = ["legacy/*.typ"]
rules = ["core.parenthesized-scripts"]
reason = "Preserve these documented legacy source examples"
```

Unknown keys/packs/IDs, invalid types, blank reasons and unsupported globs fail.
Globs are case-sensitive root-relative POSIX paths: `*` stays within a segment;
whole-segment `**` matches zero or more segments. Excludes win; `**/*.typ`
includes root files. `?`, bracket patterns and empty include lists are unsupported.
Only selected `.typ`/`.md` files are read as UTF-8 bytes. Excluded files are not
read. BOM/newlines are retained in hashes and byte plans.

Selected symlinks, hard links and nonregular files are rejected. Unexcluded
symlink directories conservatively fail even if they might contain no selected
files; `directory/**` exclusions prune their subtrees. Config/entrypoint/catalog
paths must be regular files without symlink components. The root is canonicalized.
These read checks do not establish an editor lease or race-safe write snapshot.

An entrypoint is currently read and hashed, not followed for imports or ownership.
The optional read-only reference catalog requires schema 1, a projects object,
per-project references and optional ambiguous-label lists. Entries require a
positive integer page and nonempty reference text; optional coordinates must be
finite representable numbers. URLs are never fetched. Missing/malformed catalogs
fail; absence is an explicit optional skip. Validation proves neither reference
freshness nor published-PDF correspondence.

## Implemented analysis and remaining coverage

The shipped catalog has **15 core + 18 PDE rules** with stable IDs, grades,
source sections and repair contracts; tests reconcile it against OpenSpec.

`core.parenthesized-scripts` now analyzes real `.typ` files using the official
AST, not textual math guessing. It ignores strings/comments/raw content, accepts
already-parenthesized operands, and proposes parentheses around complete typed
script operands. Nested shared-boundary parentheses are counted, not accidentally
deduplicated. A proposed candidate is reparsed and must produce zero second-pass
script findings/edits. Exact fixtures include nesting/chains, primes, fractions,
function operands, Unicode, BOM and CRLF.

Limitations remain explicit:

- Malformed Typst yields parse failures and no proposed fixes for that file.
- Any parsed comment containing `typst-lint:` blocks that file until bounded
  inline-directive scoping is implemented. Directives are never silently ignored.
- Escaped/comment-interleaved operands are reported as unsupported rather than
  rewritten. Other proven spans may appear only as a partial proposal.
- Markdown extraction is not implemented; included Markdown cannot count as
  evaluated script coverage.
- Config path/rule ignores suppress both script findings and their file's edits.
- All binding/graph-dependent rules and source capitalization discovery remain
  unsupported. Zero diagnostics never implies full rule coverage.

Reports include deterministic findings, suppressions, file-specific coverage,
parse-failure spans, proposed edits, input/config/catalog hashes, versions and
actual unmanaged routes. A rule can have evaluated `.typ` inputs while other
selected files remain partial; `coverage.complete` stays false. Overall status 2
continues to block writes and compilation even when one file's script rule passes.

The intended final statuses are 0 clean, 1 active findings (warnings included),
2 input/config/parse/coverage failure and 3 safety/write/recovery failure.
Pure edit/convergence primitives validate immutable bytes, UTF-8 span boundaries,
conflicts, cycles, eight-pass limits and idempotence. They do not establish editor
safety or complete Typst analysis and are not a filesystem writer.

## Paragraph capitalization component

`pde.paragraph-capitalization` is a tested pure rule component, **not yet connected
to real source/binding discovery**. A trusted parser/resolver must supply a
hash-bound context proving marker provenance, paragraph ownership, first-token
classification, effective language and suppression. Spelling `paragraph_tab` in
a file or setting English in config is insufficient by itself.

Only the design's exact 26 supported English starters are eligible for a one-byte
initial uppercase edit. The resolved standalone marker must precede its literal
prose word by exactly one LF/CRLF plus optional horizontal whitespace. Blank
lines/comments, first math/code/refs/calls/quotes/wrappers, unknown/non-English
language, non-ASCII casing, mixed-case names such as iPhone/eBay, unknown words
and single-letter notation are unchanged. It never searches past first math for
later prose. Supported English tags are case-insensitive `en` with optional
2–8 ASCII-alphanumeric subtags; effective source language overrides the configured
default, and dynamic/mixed language stays unknown.

The linter owns this explicit source-casing edit; marker insertion owns only the
marker change and the package owns indentation. Actual text-utils 0.1.2 with
Typst 0.15.1 indents but does not uppercase as its comments claim, due to show-rule
precedence. No broad package recasing fix was made. The optional real-render test
verifies explicit `This`, 1.5em indentation and unchanged `iPhone` source/rendering.
Package/consumer source and installed copies are not changed by the tests.

## Verification

```sh
cargo fmt --check --manifest-path tools/typst-lint/syntax-adapter/Cargo.toml
cargo clippy --locked --manifest-path tools/typst-lint/syntax-adapter/Cargo.toml -- -D warnings
cargo test --locked --manifest-path tools/typst-lint/syntax-adapter/Cargo.toml
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools/typst-lint/tests -v
```

Tests use temporary consumers. Adapter tests require the explicit build above.
They include protocol corruption, timeouts/streamed-output limits, tree/spans,
actual source parsing, diagnostics and suppression refusal, non-mutating CLI
plans, compiler spies and old-output preservation. Synthetic contexts do not
replace real parser coverage. With existing Typst/pdftotext, opt into rendering:

```sh
TYPST_LINT_RENDER_TESTS=1 PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools/typst-lint/tests -v
```

The repository test workflow (PRs and relevant main changes) builds the pinned
adapter and tests Python 3.11/3.15. It neither builds consumer documents nor
publishes/deploys anything. Renderer-dependent characterization is separate.

Next work: complete inline suppressions and Markdown/source extraction; establish
static helper/import/language provenance before wiring capitalization; implement
the remaining rule contracts; validate protected writer/editor transactions and
immutable compiler snapshots; then verify CI/watch/Tinymist adapters. AGENTS.md
or a shell wrapper alone cannot claim native editor enforcement.
