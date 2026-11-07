# Typst lint (partial implementation)

This is the implementation draft for the reviewed
[`typst-style-linter` design](../../openspec/changes/typst-style-linter/design.md).
**This is not a production linter or build gate.** Selected script, reference,
paragraph, display, highlight, theme and scoped-helper rules produce diagnostics and partial in-memory repair
plans from real Typst parsing and narrowly proven contexts.
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
returns schema 2/adapter 0.2.0/mode/source-hash evidence and a tree of SyntaxKind
byte spans with typed MathAttach operands and `Equation.block()` display flags.
The client checks the complete tree,
UTF-8 boundaries, root partitioning, links, errors and versions before rule use.
Limits are 1 MiB input, 16 MiB stdout, 64 KiB stderr, 100,000 nodes, depth 256 and
a default five-second process deadline. Pipes are bounded while streaming.
Exceeding a limit fails explicitly; it does not approve an incompletely read file.

## Run without modifying a consumer

```sh
/path/to/typst-packages/tools/typst-lint/typst-lint check --root /path/to/project --format json
/path/to/typst-packages/tools/typst-lint/typst-lint fix --root /path/to/project --dry-run --format json
/path/to/typst-packages/tools/typst-lint/typst-lint fix --root /path/to/project --format json
/path/to/typst-packages/tools/typst-lint/typst-lint fix --root /path/to/project --package-path /path/to/typst-packages --dry-run --format json
/path/to/typst-packages/tools/typst-lint/typst-style compile --root /path/to/project --format json -- main.typ output.pdf
```

All modes are currently read-only. Reports may include AST-backed proposed edits
with baseline hashes, but label them `partial-not-applicable-transaction`.
They are **not** permission or proof that the complete protected write contract
has passed. Incomplete required analysis prevents application. The `compile`
interface remains inert: arguments after `--` are never executed, no compiler is
resolved and existing outputs are never replaced. There is no fail-open option.

`--package-path` explicitly supplies Typst's local-package directory layout. The
bounded resolver reads only AST-requested reviewed artifacts below it. There is
no implicit lookup in the package owner's checkout, home directory, installed
packages, caches or environment variable. Reviewed contracts cover the exact
`@local/text-utils:0.1.2` and `@local/project-references:0.1.0` definition sources
shipped here; both manifest identity and actual source bytes are checked. The text-utils and project-references definition sources import no other module.
Bounded contracts also recognize math-blocks 0.2.0 theme/spacer values and the locally
defined scoped-annotations 0.3.0 wrappers. Those packages import other modules;
only the reviewed local definitions are certified, not arbitrary re-exports or
the runtime dependency graph. The reference function's runtime
`projects.json` is never loaded or evaluated by this analyzer. A matching package name/version
alone is insufficient. Other versions, modified source and local facades remain
unresolved, not version-style violations. Missing/unsafe/malformed requested
inputs fail; unsupported valid artifacts are reported with reasons. Package
files are never changed, fetched, evaluated or installed. Reports record manifest
and source identities/hashes. These read snapshots are not proof that a future
compiler sees the same resources; immutable compiler capture remains pending.

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
finite representable numbers. One immutable snapshot is shared by all checks and
candidate reanalysis. Reads are bounded to 16 MiB, descriptor/identity verified,
and duplicate JSON keys or nonstandard NaN/Infinity values are rejected, including
in unused metadata. URLs are never fetched. Missing/malformed catalogs
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
- Own-line `// typst-lint: ignore-next <rule-id>[, <rule-id>...] -- <reason>`
  directives scope to the next complete AST construct in a proven Markup/Code
  sibling sequence. Blank lines and comments may intervene; a whole multiline
  display or `#` plus call is one target. The directive expires at that target's
  end, and suppresses both findings and their operand-specific edits.
- Unknown/empty IDs, missing reasons, trailing directives, missing targets and
  unsupported expression-internal ownership are unsuppressible configuration
  failures with no edits for that file. Strings, raw text and block comments
  are data, not directive carriers. Leading comments directly under an Equation
  or CodeBlock wrapper remain unsupported rather than receiving guessed scope.
- Escaped/comment-interleaved operands are reported as unsupported rather than
  rewritten. Other proven spans may appear only as a partial proposal.
- Markdown extraction is not implemented; included Markdown cannot count as
  evaluated script coverage.
- Config path/rule ignores suppress both script findings and their file's edits.
- Direct reviewed package imports, explicit renames/module aliases, simple aliases,
  lexical shadowing and literal builtin text-language settings have a bounded
  static resolver. General import/include graphs and local facade exports remain
  unsupported. Unknown wildcard imports, bindings, dynamic language, show/output
  effects and deferred bodies cannot certify paragraph prose or language.
- Root prose with proven canonical paragraph markers supports three paragraph
  rules. Reviewed root project-reference calls, typed displays, bounded theme APIs,
  scope callbacks and text highlighting have the additional backends described
  below. Availability never implies full graph or rule-contract coverage.
  Zero diagnostics never implies full rule coverage.

Reports include deterministic findings, suppressions, file-specific coverage,
parse-failure spans, proposed edits, input/config/catalog hashes, versions and
actual unmanaged routes. `coverage.available_backends` lists backends wired for
the selected pack, which may be partial or unavailable for these inputs. The
existing `implemented` list contains only backends with at least one evaluated
input in this run; it does not certify their complete rule contracts. A rule can
have evaluated `.typ` inputs while other
selected files remain partial; `coverage.complete` stays false. Overall status 2
continues to block writes and compilation even when one file's script rule passes.

The intended final statuses are 0 clean, 1 active findings (warnings included),
2 input/config/parse/coverage failure and 3 safety/write/recovery failure.
Pure edit/convergence primitives validate immutable bytes, UTF-8 span boundaries,
conflicts, cycles, eight-pass limits and idempotence. They do not establish editor
safety or complete Typst analysis and are not a filesystem writer.

## Protected offline transaction component

`transactions.py` is an independently tested storage component, **not connected
to CLI writes**. Its trusted-engine API requires an explicit assertion that
editors, watchers and every other writer are stopped throughout the transaction.
It cannot inspect unsaved buffers or lock arbitrary editors. A root directory
lock serializes this library's runs; fingerprint checks narrow races but are not
an operating-system compare-and-swap guarantee.

The caller provides existing allowlisted, owner-writable source paths, read-only dependency and
configuration evidence, and one fixed private journal directory outside the root
on the same filesystem. Missing optional evidence is recorded too. Validation
invokes a trusted in-process analyzer over immutable graph views and requires
complete declared rule/source/evidence coverage, no parse/unsupported failures,
eight-pass bounded convergence, no new findings and idempotence before minting a
session-bound apply token. Synthetic fixture analyzers test this protocol only;
they cannot establish coverage for the unfinished real linter. Preexisting
diagnostic-only findings may remain, but continue to prohibit compilation.

Application rechecks every source and evidence baseline, stages exact bytes and
supported metadata, fsyncs durable recovery records, then atomically replaces
each file. A multi-file transaction is not atomic as a whole. Fault tests cover
partial writes, crashes and later user edits. Explicit recovery restores only
recognized postimages and preserves conflicting later changes for resolution.
The process-crash suite also sends real SIGKILL to a separate interpreter at
pre/post-rename boundaries, restarts recovery in fresh interpreters, interrupts
recovery itself, and checks later content edits and same-byte replacement inodes.
It verifies lock release, the recovery gate, idempotence, and exact conflict
acknowledgment. These tests establish process-death behavior, not power-loss
durability, arbitrary filesystem failures, or production analyzer completeness.
Missing, corrupt or truncated journal records fail closed for manual investigation.
Every caller must use the same configured store and retain required journals;
this library cannot discover deleted journals or ones hidden in a different store.

The component supports tested macOS/Linux metadata paths. It preserves ownership,
ordinary mode bits and exact extended attributes, and refuses ACLs, special mode
bits, unsupported filesystem flags, symlinks, hard links and nonregular targets.
Read-only source modes are refused for normal mutation without chmod; read-only
evidence and reconciliation of existing recovery journals remain supported.
Replacement timestamps intentionally change. Staged bytes and metadata are checked
again after content writes and fsync, including before recovery replacement.
The original snapshot is a baseline, not a compiler-ready snapshot; validated
candidate sources describe postimages. Compiler resource capture, output
publication, editor version integration and production CLI wiring remain absent.

## Proven source paragraph rules

`pde.paragraph-capitalization` now consumes real AST/hash-bound static proofs for
the bounded root-prose subset. The pure component still requires marker
provenance, paragraph ownership, first-token classification, effective language
and suppression. Spelling `paragraph_tab` in a file or setting English in config
is insufficient by itself. Without verified package input, no package helper is
certified. Aliased imports can establish provenance, but the current source-rule
contract supports only the canonical standalone `#paragraph_tab` spelling.
Nested/captured bodies and unverified wrappers are not guessed to be prose.

Only the design's exact 26 supported English starters are eligible for a one-byte
initial uppercase edit. The resolved standalone marker must precede its literal
prose word by exactly one LF/CRLF plus optional horizontal whitespace. Blank
lines/comments, first math/code/refs/calls/quotes/wrappers, unknown/non-English
language, non-ASCII casing, mixed-case names such as iPhone/eBay, unknown words
and single-letter notation are unchanged. It never searches past first math for
later prose. Supported English tags are case-insensitive `en` with optional
2–8 ASCII-alphanumeric subtags; effective source language overrides the configured
default, and dynamic/mixed language stays unknown.

The exact resolved `#show: apply-paragraph-tabs` form preserves language proof
because its audited source has no language setting. Unknown show rules invalidate
that proof; a later English setting does not erase the unknown wrapper effect.
A reasoned inline ignore targeting the proven marker may suppress its immediate
capitalization finding/edit, even though the diagnostic points to the first word.
That compound anchor cannot suppress another paragraph or an unlisted rule.

`pde.paragraph-marker-gap` diagnoses a missing whitespace-only blank line before
the same proven standalone marker. It proposes one newline only when the previous
line is proven safe literal prose/whitespace, retaining nearby LF/CRLF style.
Unsupported footnote/call/comment combinations are never guessed or repaired with
an incomplete paragraph-break transformation. The source must already contain the
marker; automatic marker insertion and other paragraph rules remain unimplemented.
Supported script and paragraph proposals are composed before a whole-candidate
reparse and repeated analysis, checking zero second-pass edits, deterministic
results, unchanged suppression ownership and no newly introduced finding.

The linter owns this explicit source-casing edit; marker insertion owns only the
marker change and the package owns indentation. Actual text-utils 0.1.2 with
Typst 0.15.1 indents but does not uppercase as its comments claim, due to show-rule
precedence. No broad package recasing fix was made. The optional real-render test
verifies explicit `This`, 1.5em indentation and unchanged `iPhone` source/rendering.
Package/consumer source and installed copies are not changed by the tests.

## Bounded reference and display rules

The two `core.project-ref-*` backends inspect proven direct root calls, including
verified callee aliases, and literal argument types. Extra/named arguments and
known nonlabel targets are diagnosed. Configured catalog lookups distinguish
unknown projects, missing labels and ambiguous labels without guessing another
target or fetching a URL. Dynamic/captured values and general document graphs
remain partial. No catalog is an explicit optional membership skip.

The only reference repair replaces an exact literal `[@label]` second argument
with `<label>` when the resolved API has exactly two positional arguments, the
project key is literal and the configured catalog proves a unique nonambiguous
target. Whitespace, supplements, comments, options or extra content defeat the
repair. Bare `@label` is invalid in a code argument and remains an unrepaired
parse error. The audit exposed and prompted correction of the original malformed
repair example. Identity is preserved; no catalog, page or destination is edited.
An opt-in isolated render/query fixture applies the actual proposed repair to a
temporary source and verifies the exact link destination and displayed reference
text against a fabricated catalog, without network lookup or package installation.

`pde.standalone-display` uses the parser's typed display property, never whitespace
guessing, to separate adjacent proven root literal prose with single newlines.
`pde.display-continuation-gap` reduces a whitespace-only blank gap after an already
standalone display and optional same-line semantic label to one source newline.
Inline equations, comments, markers, explicit breaks, unknown wrappers and
ambiguous/multiple labels are not crossed. Equation contents, labels and text
remain unchanged. Candidate AST checks also reject a newline that would reinterpret
literal prose as a heading, list or term item, despite syntactically valid output.

Full multi-pass cross-rule planning remains pending. If a standalone-display fix
only exposes a continuation-gap fix on the next pass, current composition refuses
the entire proposed edit set instead of applying an incompletely checked result.
The isolated display render/query test verifies text order and that the semantic
label remains attached to the block equation. No existing consumer document is compiled.

## Structural markers, highlights, themes and scoped helpers

`pde.paragraph-marker-target` diagnoses proven standalone root markers before
heading or display siblings, skipping only whitespace and comments. It removes
only the marker token, keeping its original line, comments, CRLF/BOM and structural
separation. It avoids redundant gap/casing proposals for a marker that should be
removed. A directive targeting that marker, even for another rule, refuses removal
rather than acquiring a different target. Nested bodies and facade/alias forms
remain unsupported by this bounded paragraph contract.

`pde.text-only-highlight` diagnoses direct rendered math inside builtin `highlight`
or the reviewed text-utils `highlighted` helper. The simple content-only repair
changes wrapper bytes, retaining every prose/math byte and allowing independent
script operand edits. It never treats math in option expressions or stored code
values as highlighted content. Options, nested styles/calls, labels, comments,
directives and ambiguous arguments block extraction. Extracting a display that
needs a second formatting pass still refuses the combined proposal.
An isolated real-compiler test verifies both helper forms retain their prose
highlights and equation structure after extraction. It uses an empty package
cache and a private copy of the reviewed text-utils source; no existing consumer
or installed package is changed. Exact render geometry is not claimed.

The two theme rules use exact math-blocks 0.2.0 source evidence, aliases and a
narrow immutable capture proof. They diagnose setting a custom palette as an
element and using `theme-from-text-fill` outside lexical context within a reusable
helper. Shadowing, parameter bindings and mutation invalidate evidence. They do
not insert `context`: content-return and all-call-site proof remains unavailable.
See [the theme contract](THEME_CONTRACT.md).

Four scoped-helper backends use exact scoped-annotations 0.3.0 wrapper evidence
and a literal one-parameter callback. They diagnose dictionary function-call
syntax, generated scoped targets passed directly to `ref`, overlays outside a
display, and duplicate literal namespaces in proven direct output. Only dictionary
callee parenthesization is proposed automatically. Arguments and their comments
are retained. Stored content and arbitrary wrappers cannot establish namespace
graph membership. Wildcard re-exports, raw mannot APIs, dynamic execution,
mutations, facades, general document graphs and reference repairs remain unsupported.
No caller receives a scope binding based on variable spelling alone.

## Proven footnote spacer proposals

All five footnote backends now run with bounded root-context coverage. The
resolver supplies exact source-bound evidence for the locally defined
math-blocks 0.2.0 `dots_space` value. Direct imports, immutable aliases and module
access can prove an existing spacer; insertion additionally requires the
canonical `dots_space` name to be in scope. A name alone, unknown wildcard,
modified package, facade or deferred capture supplies no such proof.

Direct aligned display footnotes can propose insertion. Direct unaligned
footnotes can propose removal, and root prose removal preserves existing comma
or period adjacency. Ambiguous punctuation, nested context and paragraph-marker
composition stay explicitly unsupported. These proposals go through the common
candidate parser, coverage-retention and idempotence checks. See the
[footnote contract](FOOTNOTE_CONTRACT.md) for precise scope and render limits.

## Verification

The [all-rule matrix](coverage-matrix.md) tracks every catalog ID, its normative
scenarios, implementation, representative fixtures, actual CLI evidence and
remaining gaps. It does not equate passing component tests with 33 completed
rules. Reproduce the isolated audit with the already-built parser:

```sh
python3 tools/typst-lint/audit_rules.py
```

Audit exit 2 means implementation or acceptance remains incomplete; exit 1 means
an audit invariant or claimed supported result mismatched. Missing/unsupported
backends and empty diagnostics are never semantic passes. The runner retains
portable hashes and normalized reports, does not invoke a real compiler, and
applies proposed bytes only in a separate temporary fixture for reanalysis.

```sh
cargo fmt --check --manifest-path tools/typst-lint/syntax-adapter/Cargo.toml
cargo clippy --locked --manifest-path tools/typst-lint/syntax-adapter/Cargo.toml -- -D warnings
cargo test --locked --manifest-path tools/typst-lint/syntax-adapter/Cargo.toml
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools/typst-lint/tests -v
```

Tests use temporary consumers. Adapter tests require the explicit build above.
They include protocol corruption, timeouts/streamed-output limits, tree/spans,
actual source parsing, bounded suppressions and explicit unsupported scopes,
non-mutating CLI plans, protected-storage fault injection, compiler spies and
old-output preservation. Synthetic contexts do not
replace real parser coverage. With existing Typst/pdftotext, opt into rendering:

```sh
TYPST_LINT_RENDER_TESTS=1 PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tools/typst-lint/tests -v
```

The repository test workflow (PRs and relevant main changes) builds the pinned
adapter and tests Python 3.11/3.15. It neither builds consumer documents nor
publishes/deploys anything. Renderer-dependent characterization is separate.

Next work: extend only provable directive scopes and Markdown/source extraction; expand
verified package/local-facade graphs and paragraph ownership; implement
the remaining rule contracts; validate protected writer/editor transactions and
immutable compiler snapshots; then verify CI/watch/Tinymist adapters. AGENTS.md
or a shell wrapper alone cannot claim native editor enforcement.
