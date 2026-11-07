# Typst lint (partial implementation)

This is the implementation draft for the reviewed
[`typst-style-linter` design](../../openspec/changes/typst-style-linter/design.md).
**This is not a production linter or build gate.** Selected script, reference,
paragraph, display, highlight, theme and scoped-helper rules produce diagnostics and partial in-memory repair
plans from real Typst parsing and narrowly proven contexts.
All 33 rules now have bounded backends; full rule acceptance, protected runtime writes and compilation remain incomplete.
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
Selected-source capture separately caps each .typ/.md at 1 MiB and the total
captured payload at 64 MiB. Immutable bytes, hashes and lengths are retained and
safely rechecked before parsing; no truncated input is accepted. See
[SOURCE_CAPTURE_CONTRACT.md](SOURCE_CAPTURE_CONTRACT.md).

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
bounded resolver reads AST-requested reviewed artifacts below it and one fixed
optional namespace artifact requested by an exact reviewed scoped wrapper. There is
no implicit lookup in the package owner's checkout, home directory, installed
packages, caches or environment variable. Reviewed contracts cover the exact
`@local/text-utils:0.1.2` and `@local/project-references:0.1.0` definition sources
shipped here; both manifest identity and actual source bytes are checked. The text-utils and project-references definition sources import no other module.
Bounded contracts also recognize math-blocks 0.2.0 theme/spacer values and its
local proof helper, plus locally defined scoped-annotations 0.3.0 wrappers. Those packages import other modules;
only the reviewed local definitions are certified, not arbitrary re-exports or
the runtime dependency graph. Scoped reference repairs additionally require the
exact mannot 0.4.0 manifest and two-line public entrypoint from that explicit
package path. This attests that wildcard imports do not shadow `ref`/`label`;
namespace evidence alone does not certify raw APIs. Additional fixed, safely
captured mark/annot/util definition artifacts can certify selected raw mannot
APIs. CeTZ line coordinates require all seven pinned supplemental artifacts,
not just a package name or root entrypoint. No arbitrary dependency traversal
or runtime geometry is inferred from those capability proofs.
Absent optional namespace evidence leaves the reference finding diagnostic-only;
a directly requested missing artifact remains an input error. The reference function's runtime
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

The explicit entrypoint is safely captured for bounded book ownership. For the
PDE pack, selected Typst sources also seed a read-only graph of explicitly
referenced relative imports. It is limited to 512 files, 16 MiB, 4096 edges and
depth 32, with the same 1 MiB file bound. Imported files remain evidence only,
never expand the source selection, and receive no style findings unless selected.
General includes, packages, initializer effects and computed imports are not
certified. See [IMPORT_GRAPH_CONTRACT.md](IMPORT_GRAPH_CONTRACT.md).
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
- Explicitly included Markdown uses the bounded extraction contract below;
  other Markdown contexts remain explicit partial coverage.
- Config path/rule ignores suppress both script findings and their file's edits.
- Direct reviewed package imports, explicit renames/module aliases, simple aliases,
  lexical shadowing and literal builtin text-language settings have a bounded
  static resolver. Pure relative re-export graphs have a separate bounded import
  backend; general import/include graphs and facade API provenance remain unsupported. Unknown wildcard imports, bindings, dynamic language, show/output
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


## Bounded Markdown script examples

Markdown stays opt-in through `include`, for example
`include = ["**/*.typ", "docs/**/*.md"]`. Only
`core.parenthesized-scripts` applies to selected Markdown. Other rule coverage
rows are explicitly `not-applicable`, not guessed Typst analysis. Imports inside
examples are syntax only: they do not load packages, traverse the dependency
graph or execute code.

The initial supported extraction is column-zero backtick/tilde fences with exact
`typst` info, same-line inline mathematics and a same-line code span containing
one equation. Each extracted source slice is parsed by the pinned real adapter;
the math closing delimiter comes from its AST, not a guessed Typst regular
expression. Original byte spans and Unicode diagnostic columns are retained,
including BOM and CRLF. Source input is bounded to 1 MiB, an example/probe to
64 KiB, and extraction to a shared budget of 256 probes/examples. Partial
reasons are capped at 256 plus an omission notice; adversarial delimiter runs
use bounded scanning.

The exact documentation sentence
``Do not write bare scripts such as `$X^i$` or `$X_j$`.``
is exempt. Other sentences describing an incorrect example are still checked.
Inside a Typst fence the normal Typst string/comment/raw semantics apply, along
with actual parser-scoped `ignore-next` directives. Inline examples use only
path/rule config ignores. Parse/config failures cannot be ignored.

Only actual script parentheses insertions can be proposed. Before exposing them,
the whole Markdown candidate is extracted and parsed again to preserve example
identity, suppression ownership and zero-edit idempotence. The rule audit captures
an independent immutable `MarkdownContext` and recomputes exact verified repairs;
serialized hashes or fabricated pass history alone never certify a candidate.

Ambiguous containers, indentation, HTML/link or bracket-bearing paragraphs,
multiline or unclosed inline constructs and display-dollar runs are deliberately
unsupported and quarantined. This includes bracket-bearing inline Typst math
for now; unknown raw HTML/fence recovery never exposes its opaque contents.
Column-zero code spans with three or more backticks are also conservatively
refused as ambiguous fences. This is not a general CommonMark parser or full
documentation acceptance.
See [MARKDOWN_SCRIPTS_CONTRACT.md](MARKDOWN_SCRIPTS_CONTRACT.md).
The actual CLI remains read-only with exit 2 and never invokes the compiler.


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
marker; bounded insertion is now described in the prose marker section below,
while broader paragraph ownership remains incomplete.
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

Bounded multi-pass planning now reparses and reanalyzes up to eight edit passes.
Standalone-display changes can expose a later continuation-gap repair, and
highlight extraction can expose display formatting. Repeated states, unresolved
conflicts, lost coverage, new refusals or new final findings reject the entire plan.
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
directives and ambiguous arguments block extraction. Extracting a display may require a separately parsed formatting pass; the
composed baseline patch retains both contributing rule identities.
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
display, and duplicate literal namespaces in proven direct output. Dictionary
callee parenthesization preserves argument comments. The exact builtin
`ref((scope.tag)("target"))` form can also become `(scope.ref)("target")` when
the receiver, literal target and package-local reference namespace are proven,
with no options, extra arguments or internal comments. Related namespace locations
are mapped through preceding edits while literal project names and other diagnostic
data remain untouched. Stored content and arbitrary wrappers cannot establish
namespace graph membership. Wildcard re-exports, raw mannot APIs, dynamic execution,
mutations, facades, general document graphs and other reference forms remain unsupported.
No caller receives a scope binding based on variable spelling alone.

`pde.theme-cancelmark` diagnoses verified builtin cancellation calls in real
mathematical source. It distinguishes math fallback from code lookup and keeps
lexical/module aliases, shadowing and wildcard uncertainty explicit. There is
no verified `cancelmark` definition in this checkout, so replacement and palette
behavior remain unavailable. See the [cancellation contract](CANCEL_CONTRACT.md).

Two book-ownership backends require an explicit configured entrypoint snapshot
and exact paired math-book/math-blocks package evidence. They diagnose proven
duplicate root book shows and adjacent reset ownership with bounded literal
options. Unknown transforms, dynamic options and broader document graphs are
not guessed. Reset deletion remains unavailable because whole-body reference
and heading transformation equivalence is not yet certified. See the
[book ownership contract](BOOK_CONTRACT.md).

`pde.prose-paragraph-marker` diagnoses existing blank-line boundaries between
literal root prose paragraphs. A marker proposal requires the canonical reviewed
text-utils value and an already installed reviewed paragraph show, unchanged
directive targets and a stable capitalization preflight. The combined pipeline
may admit exactly the existing policy's eligible lowercase starter, then obtains
the actual one-byte uppercase edit from an independent second parse/resolution.
The direct backend remains opt-in for this followup. It does not add imports or
show rules, guess nested/structural boundaries, or recase protected names. See the [prose marker contract](PROSE_MARKER_CONTRACT.md).

`pde.proof-trailing-gap` recognizes the reviewed math-blocks proof helper only
at a proven standalone root call. It proposes a blank source line before the
next supported prose or structural sibling, after one unambiguous attached label.
EOF, nested/deferred output, unknown style effects and ambiguous comments/labels
are not guessed. Candidate parsing must preserve all non-whitespace syntax and
attachments. See the [proof contract](PROOF_CONTRACT.md).

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

## Additional bounded rule backends

- `core.scoped-marker-tags` diagnoses proven manual raw marker labels inside
  recognized local annotation callbacks. `core.annotation-marker-bounds` only
  diagnoses a proven nearest preceding plain label on a nonmetadata atom in a
  complete direct display. Unknown output, target ordering and collisions refuse;
  neither rule invents marker bounds or proposes a repair. See
  [MARKER_BOUNDS_CONTRACT.md](MARKER_BOUNDS_CONTRACT.md).
- `core.scoped-anchor-access` can replace eight supported CeTZ line coordinate
  suffixes with an actual scope.node call only with genuine artifact evidence,
  a unique preceding applied marker, exact same-display target ownership and a
  safe dot-free scope prefix. Opaque drawing and runtime geometry are not proved.
  `core.local-overlay-api` diagnoses proven raw annotation calls in a local scope
  without inventing wrapper replacement. See
  [ANCHOR_OVERLAY_CONTRACT.md](ANCHOR_OVERLAY_CONTRACT.md).
- `pde.import-order` reorders only pure, disjoint, immediate styles/figures import
  groups. `pde.no-redundant-facade-import` removes a compatibility import only when
  the retained facade already supplies identical captured leaf bindings. Exact
  unchanged statement spans map candidate locations through reorderings. Comments,
  directives, dependency effects and name-only facade assumptions refuse repair.
- `pde.flowbox-colored-border` uses the complete reviewed math-blocks module's
  AST/token fingerprint with one permitted border expression slot. A literal
  width-only stroke can acquire its proven theme.rule color; copied fragments
  and modified selectors/themes/bodies are not ownership evidence. Isolated
  light/dark renders preserve width, geometry and body content. See
  [FLOWBOX_CONTRACT.md](FLOWBOX_CONTRACT.md).

These are partial implementations, not 33 fully accepted rules. Production
writes, compiler execution and all-project rollout remain disabled.

## Multi-pass proposal evidence

The candidate planner retains each pass's actual edits and exact original-byte
mapping. It composes nonoverlapping baseline patches without diff-based identity
guesses. A merged patch retains its first causal `rule_id` and all contributing
`rule_ids`; `repair_passes` identifies followup work even when that rule had no
finding in the original source. Original findings remain original-source findings.

Multi-pass reports include `convergence_evidence` with baseline/candidate hashes,
per-pass hashes, applied attributed edits and independently checked AST import
movement proofs. Serialization alone is not a semantic proof. The audit captures
an independent immutable analysis context from the same isolated fixture and
recomputes actual per-pass rules and convergence, including a final independent
zero-edit analysis. Stripped or changed history, false rule attribution, malformed
primitive types, source hash mismatches and wrong pass counts block verification.

Every step preserves analysis coverage, existing refusal ownership and directive
scope, syntax kind and a surviving target-origin token. Final findings must be a
subset of original mapped findings; suppressed findings cannot silently change.
Packages, catalogs, configured entrypoint and relative import evidence are rebased
from the same captured bytes without per-pass filesystem rereads. See
[CANDIDATE_CONVERGENCE_CONTRACT.md](CANDIDATE_CONVERGENCE_CONTRACT.md).

These remain in-memory partial proposals. Eight-pass success does not certify
complete required analysis, an editor lease, protected CLI writes or compilation.

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

Next work: extend only provable directive scopes and Markdown extraction contexts; expand
verified package/local-facade graphs and paragraph ownership; implement
the remaining rule contracts; validate protected writer/editor transactions and
immutable compiler snapshots; then verify CI/watch/Tinymist adapters. AGENTS.md
or a shell wrapper alone cannot claim native editor enforcement.
