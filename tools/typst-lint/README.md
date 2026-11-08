# Typst lint (partial implementation)

This is the implementation draft for the reviewed
[`typst-style-linter` design](../../openspec/changes/typst-style-linter/design.md).
**This is not a production linter or build gate.** Selected script, reference,
paragraph, display, highlight, theme and scoped-helper rules produce diagnostics and partial in-memory repair
plans from real Typst parsing and narrowly proven contexts.
All 33 rules have bounded backends; full rule acceptance and compilation remain incomplete.
Ordinary analysis/fix/compile modes return **2**, including an empty selection,
without changing source or invoking a compiler. A separate explicit offline
source profile can apply narrowly proven script or paragraph repairs. With additional
explicit output/resource opt-ins, it can compile and publish one guarded PDF
under the ASCII or narrowly reviewed Korean resource profile. These are not
all-project writers. Help/version return 0.

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

To inspect freshness for package paths listed in an explicit resource manifest,
use the separate read-only package report:

```sh
/path/to/typst-packages/tools/typst-lint/typst-lint packages-status \
  --root /absolute/project --manifest /absolute/resources.json \
  --checkout /absolute/typst-packages --upstream https://github.com/OWNER/REPOSITORY.git \
  --ref refs/heads/main --online --format json
/path/to/typst-packages/tools/typst-lint/typst-lint packages-status \
  --root /absolute/project --manifest /absolute/font-seed.json \
  --discover-dependencies --entrypoint main.typ --checkout /absolute/typst-packages \
  --upstream https://github.com/OWNER/REPOSITORY.git --ref refs/heads/main --online --format json
```

The first form treats the manifest as caller-supplied selection data; it does not
prove the compiler selected those paths. The second explicitly runs the pinned
macOS compiler once in the existing network-denied, outside-write-denied sandbox
with the manifest's selected fonts and uses that run's observed dependency paths.
It does not prove future branches, future runtime selection, exhaustive OS reads,
or that compared bytes remained unchanged since discovery. Only local and
preview paths under the explicit package/cache roots are classified. Local package directories are compared with the
explicit checkout and, when the selected upstream commit is locally available,
its Git tree. The report separates checkout branch/ref relation, package-copy
equality, raw checkout-versus-HEAD and index-versus-HEAD comparisons, and unknown
outcomes. Global Git dirty state remains unknown: the tool does not run Git
status, which can execute repository-configured clean filters. Raw and index
comparisons are not equivalent to Git's filtered dirty status. Preview package
versions are compared with the official registry index, which does not prove
installed bytes or compatibility.
Omit `--online` to avoid all network lookups. The online route reads only the
specified GitHub ref and the fixed official registry index; it does not fetch
Git objects, update packages or modify the project. Exit 0 means the report was
generated, not that every package is current or that runtime selection is proved.
See [PACKAGE_FRESHNESS_CONTRACT.md](PACKAGE_FRESHNESS_CONTRACT.md) for scope,
failure states, and the distinction between sampled files and Git dirty status.

For an entrypoint and its included chapters, use the explicit read-only document
inspection route:

```sh
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --preview-display-gaps --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --preview-standalone-displays --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --preview-sequence --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --local-rule-hints --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --preview-conditional-gaps --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --preview-conditional-punctuation --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --preview-conditional-spacers --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --preview-conditional-scoped-calls --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --preview-conditional-continuations --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --preview-conditional-capitalization --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --preview-conditional-paragraph-gaps --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --preview-conditional-block-marker-gaps --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --preview-conditional-prose-spacers --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --preview-conditional-aligned-spacers --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --preview-conditional-prose-markers --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --package-path /absolute/captured-packages --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --package-path /absolute/captured-packages --footnote-cache-path /absolute/captured-cache --modern-highlight-evidence --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --package-path /absolute/captured-packages --reference-package-path /absolute/reference-packages --reference-facade Styles/styles.typ --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --package-path /absolute/captured-packages --reference-cache-path /absolute/captured-cache --reference-package-path /absolute/reference-packages --reference-facade Styles/styles.typ --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --package-path /absolute/captured-packages --reference-cache-path /absolute/captured-cache --reference-package-path /absolute/reference-packages --reference-facade Styles/styles.typ --reference-calls --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --package-path /absolute/captured-packages --reference-cache-path /absolute/captured-cache --reference-package-path /absolute/reference-packages --reference-facade Styles/styles.typ --reference-arguments --format json
/path/to/typst-packages/tools/typst-lint/typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --package-path /absolute/captured-packages --reference-cache-path /absolute/captured-cache --reference-package-path /absolute/reference-packages --reference-facade Styles/styles.typ --reference-catalog /absolute/catalog.json --format json
```

The scoped-call option also reports conditional `core.scoped-marker-tags` hints
for a narrow literal raw marker-label form. The helper bindings and execution
remain unproved; this diagnostic-only report does not apply source edits.
It also compares direct literal scope prefixes across captured include occurrences
as conditional namespace-collision hints, without asserting execution order.
Its separate `mannot-scope` annotation section counts supported raw calls
lexically inside displays and conditionally reports calls outside them, including
wrappers in deferred outer contexts. Runtime bindings, execution and display
ownership remain unproved; hints can duplicate the independent raw annotation
section and never authorize edits or compilation.

The separately selected `--modern-highlight-evidence` uses both explicit package
roots and recognizes only the reviewed main-c161707 `text-utils` 0.1.2 source
and manifest. It reports literal math inside supported `highlighted` bodies,
distinguishing default flow, explicit flow and explicit phrase mode. Unknown
versions, dynamic options and unsupported bodies remain unavailable or unresolved;
the selected higher-priority root is never silently replaced by the cache. This
is conditional diagnostic evidence, not a claim that runtime binding or rendering
is proven, nor permission to change sources. See
[the highlight contract](CONDITIONAL_HIGHLIGHT_CONTRACT.md).

The independent `--preview-conditional-gaps` inspection also offers separate
source-only footnote-gap candidates before a literal `paragraph-tab` marker or
direct `figure` call; their bindings, execution and rendering remain conditional.
With explicit modern highlight evidence, `--preview-conditional-modern-highlights`
also offers limited independent source splits that leave literal math outside
highlighting. They are not applied or compiled and can change layout; see the
[modern highlight preview contract](MODERN_HIGHLIGHT_PREVIEW_CONTRACT.md).

The `--preview-conditional-punctuation` inspection reports adjacent trailing
punctuation moves independently from moves whose footnote has preceding spaces.
Each adjacent move has inseparable insertion and deletion edits; its source-only
proposal does not establish binding, rendering equivalence or write permission.

This route captures literal relative includes with the fixed `book-v1` budget
(64 source files and 400,000 syntax nodes), retains separate repeated include
occurrences, and checks math scripts once per captured source. This document route
also recognizes a directly embedded code call such as `x^#footnote[...]` and
proposes `x^(#footnote[...])`, including the hash in the grouped operand.
Direct code identifiers and static field chains, such as `x^#value` and
`x_#record.field`, are also grouped with their leading hash. Block comments between
an operator and operand are preserved. Line comments, computed field receivers
and other code escapes remain unresolved. The ordinary
single-source and protected-write profiles keep their previous script behavior.
The explicit
`--rule-set pde` selection also checks standalone display lines and source
display-continuation gaps; omission
retains the script-only `core` inspection. Consumer configuration is not loaded.
Its byte offsets, baseline/candidate hashes and in-memory edit proposals can be
inspected even when includes occur inside `book-part` or other opaque wrappers.
`pde.display-continuation-gap` also diagnoses a standalone typed display followed
by a blank source line and direct literal prose in the same root or nested
Markup body. Comments, markers and wrappers are never crossed. These are
diagnostic-only source patterns by default: math scripts have in-memory edit proposals,
with their verified rule IDs stated in the report. The explicit
`--preview-display-gaps` option separately previews gap edits per source, checking
a fresh parse, unchanged non-whitespace syntax and directive targets, and display
rule reanalysis. The continuation line's indentation is preserved. Each preview
is based on the original source hash and is independent of the script proposals;
do not combine offsets or candidate hashes as if they were
one plan. These previews can alter paragraph layout and do not certify rendering or
authorize source writes. Those wrappers retain
unresolved execution context. Imports, other style rules, runtime resources and
actual rendering are outside this inspection.
`pde.standalone-display` diagnoses literal prose sharing the display's source
line, retaining one attached semantic label. Text without a Unicode letter,
such as a label's following period, stays unresolved rather than being moved
onto its own line.
The separate `--preview-standalone-displays` option proposes source newlines for
these proven sides, preserves indentation and labels, reparses the candidate,
and checks syntax, directive ownership and display findings. This can expose a
new continuation-gap finding, reported for the candidate; inspect the resulting
source again before producing another rule's edits. Each preview uses the
original source hash, is independent of all other proposals, and has no write or
general rendering authority.
For a combined candidate, `--preview-sequence` instead emits a per-source ordered
sequence: standalone display, math scripts, then continuation gaps. Each stage's
offsets apply only to that stage's baseline hash; its output hash is the next
stage's baseline. The entire source sequence is withheld if a stage fails, a
script operand is unsupported, a directive target changes or final reanalysis
fails. Display-context refusals and uninspected rules remain explicit; this is
still a read-only preview, not an automatic write or a compiler resource proof.
With explicit PDE selection, `--package-path` additionally captures six exact
package namespace contracts and relative import sources. This can trace
`highlighted` through the actual PDE wildcard facade, including combined module
alias/item imports. It reports literal math inside root helper calls and the
direct literal bodies of captured `proof`, `theorem` and `note` wrappers. Local
shadowing, aliases and scope boundaries remain explicit.
The same opt-in route also reports a missing blank source line before a
standalone captured `paragraph_tab`/`paragraph-tab` value or immutable alias.
File/body openings do not replace that blank line; a following heading/display
is withheld for the separate marker-target rule. These marker hints additionally
require the paragraph-tab transformation to be active at rendering time.
It also follows direct single-parameter literal callbacks of the exact captured
`local-tag-scope` and `local-scope-annotations` exports, with omitted or literal
`auto` options. Parameters shadow captured names. Computed options, complex
callback bodies and the existing block/context barriers retain explicit refusals.
Inside an admitted callback, a `context` body remains unresolved but does not
erase later known lexical bindings when the syntactic scan detects no assignment
or return. This is not a claim that the context has no other effects.
`conditional_diagnostics` records source/package
hashes, ordered name routes and the outstanding initialization, runtime package
selection and rendering conditions. These hints have no edits, stay separate
from source findings and leave that rule in `coverage.uninspected`. Only an
explicit package root is read; installed caches are not searched. See
[CONDITIONAL_HIGHLIGHT_CONTRACT.md](CONDITIONAL_HIGHLIGHT_CONTRACT.md).
The separate `--local-rule-hints` option analyzes each included file as a
standalone lexical environment for fourteen paragraph, continuation, footnote,
proof, theme, import-order and cancellation rules. Its findings and per-file refusals are grouped
under `local_rule_hints` and remain conditional on the actual include-site
bindings, styles and execution; it does not propose or apply these rules' edits.
Continuation-line hints also inspect literal nested Markup bodies. Footnote hints
include nested prose and displays. Nested paragraph capitalization hints cover only
a bounded set of lowercase starters after literal standalone markers; nested
marker-target hints cover directly following headings or display math. These hints
retain exclusions for unrelated math, code, raw text, headings and list structures.
Common resolver refusals are reported once per source. These rules remain in
`coverage.uninspected` because the document context has not been established.
The separate `conditional_local_scope_raw_annotations` section counts raw
`annot` calls lexically inside displays in supported literal
`local-scope-annotations` callbacks and reports outside patterns as conditional
hints. Runtime bindings, execution and display ownership are unproved. This
section is currently specific to inspection and is not included in the separate
original-compile `--lint-local-hints` summary.
The separate `conditional_hyphen_markers` section covers literal
`#paragraph-tab`: root and nested gap hints and nested target/capitalization
hints. It is diagnostic-only; marker binding and execution remain conditional.
The independent `--preview-conditional-capitalization` option also offers a
separate source-only casing candidate for its supported nested hyphenated marker.
This changes source casing and can change rendered text; it does not establish
the marker binding, show behavior or language.
The paragraph-gap preview option separately reports eligible root and nested
`#paragraph-tab` edits, withholding ambiguous preceding calls. The candidates
are independent of underscore-marker previews and may change rendering.
The separately opted-in block-marker-gap preview handles two additional literal
call forms under explicit per-edit `pagebreak` or `flowbox` binding and output
conditions. It is independent of the conservative plan and may change rendering.
See [document rule coverage](DOCUMENT_RULE_COVERAGE.md) for each rule's current
single-source, document-hint and compile-preview scope.
To inspect conditional builtin-footnote bindings through captured relative
wildcard facades, add `--package-path /absolute/packages --footnote-cache-path
/absolute/cache` with `--rule-set pde`. These source-bound hints use explicit
package-before-cache evidence; they do not authorize compilation or edits.
Add `--footnote-punctuation-evidence` to separately connect eligible nested
prose-footnote punctuation hints to those captured calls. This performs another
analysis pass and remains diagnostic-only.
With the additional explicit `--reference-package-path` and `--reference-facade`,
`reference_provenance` separately traces the facade's `project-ref` export using
captured package hashes and namespace evidence. It reports either conditional
provenance or an unavailable reason, even when the reference package itself was
captured successfully. It does not prove a call's binding or runtime initialization,
or propose edits. Catalog membership is checked only with an explicitly supplied
`--reference-catalog` snapshot and remains conditional and incomplete.
An optional `--reference-cache-path` supplies a second explicit namespace root:
the package root takes precedence, even when its selected version is incomplete.
For this route the reviewed reference package must be captured from that first
root; a separate or cache-only reference package remains unavailable.
The separately reviewed text-utils variant contributes only an opaque export
name set; it cannot acquire highlighted-helper or paragraph-marker identity.
The report keeps global coverage incomplete and exits 2; it never applies the
proposals or invokes Typst compilation. Unsafe paths, malformed sources and
exhausted budgets return an input error rather than a truncated document report.
See [DOCUMENT_INSPECTION_CONTRACT.md](DOCUMENT_INSPECTION_CONTRACT.md) and the
[local script characterization](audit/document-inspection-evidence.json) and
[display-gap characterization](audit/document-display-evidence.json), plus the
[combined source-layout characterization](audit/document-layout-evidence.json).
An internal [explicit binary resource capture](DOCUMENT_RESOURCE_CAPTURE_CONTRACT.md)
can bind a sequence's source candidates to caller-selected package, font and
other resource bytes for private staging experiments. It neither proves complete
compiler resources nor invokes the compiler or writes a consumer.

The explicit macOS [`preview-compile` route](DOCUMENT_PREVIEW_COMPILE_CONTRACT.md)
can compile the captured baseline and candidate in a private network-denied
workspace using a caller-supplied resource manifest. It optionally streams the
candidate PDF to stdout, never changes consumer sources and still exits 2 because
global rule coverage is incomplete. The same preview is available explicitly as
`typst-style compile --preview --root /absolute/project --entrypoint main.typ
--manifest /absolute/font-seed.json --discover-dependencies --format json`.
Explicit `--conditional-gaps` also composes the conditional proof and nested
math-footnote gap stages in that preview; it does not authorize source writes.
Adding `--conditional-punctuation` appends the separate conditional prose
footnote spacing stage; it requires `--conditional-gaps`.
For the separately opted-in spacer and facade profile,
`--partial-unsupported-preview` can retain recognized unsupported files in
their captured original form and compare a partial whole-document candidate.
Other safety refusals still block the preview; source application remains disabled.
The further explicit `--partial-unproven-binding-preview` can also withhold an
entire source with an unproven footnote binding. Unknown or dynamic transitive
imports and other safety failures still block the preview. Both options retain
the withheld source unchanged and do not authorize source application.
The separately explicit, repeatable `--withhold-source PROJECT_RELATIVE_PATH`
skips every preview rule stage for the selected captured source, independently
of whether conditional spacers are selected. The source and its dependencies
can still execute in the PDF preview; included sources are not automatically
excluded. Use it only with non-adversarial inputs. The sandbox
denies network and writes outside its workspace, but does not prevent every read
or transient input modification within it. See the preview contract for limits.
The ordinary, unprofiled compile gate remains blocked; the preview neither
accepts compiler-tail arguments nor publishes a PDF. The separate explicit
[`--compile-original --pdf-stdout` route](DOCUMENT_ORIGINAL_COMPILE_CONTRACT.md)
compiles captured original bytes without applying source edits. Add the explicit
`--lint-document --rule-set pde` option to report the same three partial source
checks on that capture before compiling; the default performs no lint. Add
`--lint-local-hints` for separate conditional local-rule, hyphenated-marker and
include-ownership evidence; unresolved includes and incomplete coverage remain
explicit. See the [captured lint contract](ORIGINAL_COMPILE_LINT_CONTRACT.md).
The command returns
PDF bytes through a pipe and reports on stderr. Compilation success does not
mean lint coverage is complete. Shell redirection is caller-managed and can
truncate files before the process starts. Optional
`--discover-dependencies` derives current dependency paths from a sandboxed
read-only baseline using the manifest's explicit font selection, and refuses if
the captured baseline's PDF or observed dependencies differ from that probe.
Add `--review-edits --format json` to include file hashes and ordered per-rule
edit stages in the same report as the compiled preview. The explicit source-apply
gate lists why these results cannot yet authorize writing original files.

The ordinary modes above remain read-only. Reports may include AST-backed proposed edits
with baseline hashes, but label them `partial-not-applicable-transaction`.
They are **not** permission or proof that the complete protected write contract
has passed. Incomplete required analysis prevents application. The ordinary `compile`
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
alone is insufficient. Other versions, modified source and general local facades remain unresolved,
not version-style violations. The separately certified named text-utils facade
subset below is the only current local helper-forwarding exception. Missing/unsafe/malformed requested
inputs fail; unsupported valid artifacts are reported with reasons. Package
files are never changed, fetched, evaluated or installed. Reports record manifest
and source identities/hashes. These read snapshots are not proof that a future
compiler sees the same imported resources. Their immutable compiler capture
remains pending; the closed-core compiler excludes imports and packages.

## Captured helper forwarding through local files

A bounded local facade such as `styles.typ` can forward real text-utils 0.1.2
helpers through explicit named/renamed imports and immutable aliases. This uses
a separate complete export-identity proof, plus an exact source/AST certificate
that the dependency-free package initializer only creates immutable values and
deferred functions. It does not prove those function calls pure or let the
import-order/removal rules reorder package-bearing facades.

The facade must account for every binding and statement. Unsupported unused
calls, functions, output, sets/shows, mutation, missing exports, wildcards or
module namespaces invalidate the entire proof. Other reviewed packages do not
inherit initializer safety. Ordinary constants never become helper APIs and
still shadow imported builtin names. Canonical marker spelling and an already
installed reviewed show remain required for insertion.

Relative dependencies are safely captured before one explicit package load.
Package requests found only in a facade come from those retained bytes, not a
new filesystem lookup. The core pack now performs this graph capture when
needed as well, including its path, race and graph-budget refusals. Captured
syntax/resource failures remain negative evidence and are not retried merely
because a package root was supplied. Markdown example imports remain data.
Candidate passes use freshly rebased immutable graphs; all resolver variants
share the same captured input boundary, without extending scoped/book package
authority. No consumer dependency is reread during candidate authentication.

The package loader may still report a local file name as `unreviewed-package`:
that is its package-contract result, not a facade capability or per-rule
coverage claim. No local file is promoted merely by its spelling. See
[FACADE_PROVENANCE_CONTRACT.md](FACADE_PROVENANCE_CONTRACT.md).

## Read-only document occurrence context

Two separate internal modules prepare analysis of an entrypoint, its included
chapters and scoped paragraph bodies. They add no CLI profile or repair/compiler
permission and leave the existing resolver and ordinary routes unchanged.

`include_context.load_include_context` captures explicitly supplied entrypoints
and bounded literal relative includes under retained no-follow file/directory
snapshots. Exact source/tree/node identities and ordered include-site chains
distinguish repeated inclusions and different entrypoints. Unsafe traversal,
identity changes and exhausted budgets refuse capture; missing or opaque
requests remain explicit. Includes do not import parent lexical names or prove
that content executes. A pure origin assertion authenticates the original
parser snapshot in memory, not the current filesystem.

`scoped_paragraph_context.analyze_scoped_paragraph_context` consumes that issued
capture and explicit package Trees without parsing, filesystem access or Typst
execution. It derives separate conditional binding, language/show, callback
ownership and invocation-effect facts for a narrow direct include/scope route.
The initial editorial suggestions require child-owned named helper imports,
an explicit literal prefix, an owned canonical paragraph marker with a real
callback-internal paragraph break, and an explicit English/exact paragraph-show
context. Callback openers, metadata and enclosing includes supply no paragraph
boundary. Suggestions are informational records,
not actionable edits or transaction candidates.

Default/auto scope prefixes are modeled as counter/context effects with
unresolved execution and collision obligations, not immediate callbacks.
Package definition identity does not prove runtime package selection or complete
initialization. The scoped package's additional dependencies remain unresolved;
the existing root profile's structural rule discharges cannot be reused for
these inputs. Arbitrary wrappers, wildcard facades, book shows, diagrams and
unknown context do not become supported through these modules. No actual
consumer, editor setting, package installation or build route is changed.

See [INCLUDE_CONTEXT_CONTRACT.md](INCLUDE_CONTEXT_CONTRACT.md) and
[SCOPED_PARAGRAPH_CONTEXT_CONTRACT.md](SCOPED_PARAGRAPH_CONTEXT_CONTRACT.md) for
the exact records, fixed bounds and evidence limitations.

## Captured relative scalar graphs

A separate internal proof/capture layer now accounts for named relative imports
and their entire inert scalar dependency graph. It checks exact declaration and
alias origins, imported scalar uses, every unused initializer, canonical paths,
closure and fixed graph budgets. Discovery proposes paths only. An existing
protected transaction recaptures root, modules, config and catalog, then all
proofs are rebuilt from those retained bytes and actual file identities.

Dependencies stay read-only. Source-like evidence reads and complete live
ancestor retention are explicit transaction opt-ins; default transactions and
schema-1 historical recovery keep their previous semantics. Live capture receipts
reject altered, stale, copied, foreign or expired evidence. Their reports and
pure proofs are not candidate, committed-source or compiler authority.

The separate internal `ProtectedCapturedRelativeFix` now runs actual root/module
script analysis and an independent original-source replay through that same
transaction. Only exact root parentheses may change; dependency bytes, import
prefixes, scalar origins, alias routes and use ownership stay bound. Opaque
candidates and committed graph receipts retain independent facts; capture/proof
reports cannot substitute for the bridge's own successful apply. Valid fixes and
honest successful reports survive a later rejected handoff.

The separate internal `compile_committed_graph` now accepts only that same active
bridge's genuine committed graph. It checks the selected repertoire across every
source and a fixed 1 MiB aggregate imported scalar-text charge before resource
capture. It exclusively stages the exact bounded graph, checks actual dependency
bytes for complete membership, retains their observed order, and guards original
and private source/resource identities through compilation and cleanup. Its sealed
artifact expires with the bridge; valid source fixes survive compiler failures.
Private native tests exercise ASCII and dependency-only Korean graphs, actual PDF
text/boxes/raster/fonts, and zero-launch rejection under the ASCII resource profile.

The separate internal `PreparedCapturedRelativePdfPublication` prepares one
output baseline before any validation or application attempt, under the same
source transaction and a separate output-exclusivity assertion. It owns one graph
compile and publishes only its genuine guarded artifact through the unchanged
PDF store. All dependencies participate in collision checks and live authority.
Failure handling retains original session ownership and actual compiler/resource
facts. Output-only recovery preserves committed source repairs and later edits;
durable publication is final. Existing schema-1 historical recovery does not
require current graph sources or resources.

The exact public offline selection `captured-relative-core-v1` now chooses this
separate graph composition. The old single-source grammar registry, compiler and
publisher remain closed to it, and both older profiles still refuse imports.
Ordinary routes remain read-only. No consumer documents or installed build
routes are changed.
See [CAPTURED_RELATIVE_CORE_CONTRACT.md](CAPTURED_RELATIVE_CORE_CONTRACT.md),
[CAPTURED_RELATIVE_INPUT_CONTRACT.md](CAPTURED_RELATIVE_INPUT_CONTRACT.md),
[CAPTURED_RELATIVE_FIX_CONTRACT.md](CAPTURED_RELATIVE_FIX_CONTRACT.md),
[CAPTURED_RELATIVE_COMPILE_CONTRACT.md](CAPTURED_RELATIVE_COMPILE_CONTRACT.md),
[CAPTURED_RELATIVE_PUBLISH_CONTRACT.md](CAPTURED_RELATIVE_PUBLISH_CONTRACT.md) and
[CAPTURED_RELATIVE_CLI_CONTRACT.md](CAPTURED_RELATIVE_CLI_CONTRACT.md).

## Captured paragraph repairs

A separate `ProtectedCapturedTextUtilsParagraphFix` can repair a deliberately
bounded root document using the exact reviewed text-utils 0.1.2 paragraph marker
and show. It retains the root, explicit English configuration, catalog and exact
package manifest/source in one transaction. Its independent whole-root proof
accounts for all 33 selected obligations: five actual evaluations and 28
structural discharges, not a claim that all global rule contracts are complete.

The bounded repair grammar admits a missing blank line before a standalone
marker, a missing marker at an existing root blank-line paragraph boundary, and
one-byte ASCII capitalization of eligible first words. Marker insertion preserves
original casing; a later actual reparse/evaluator pass owns newly required
capitalization. At most two repair passes are allowed. Mixed and initially
unmarked paragraphs are supported when an after-first paragraph needs a marker;
the first unmarked paragraph is exempt. A final document still requires at least
one marked paragraph. Single-newline continuations, blocks, footnotes and display
math are not admitted by this profile.

Independent byte/owner maps, replay, original validation/history identity seals,
immutable evidence and current filesystem guards protect application. All other
original source bytes and all package bytes remain unchanged. Reports distinguish
the repair grammar from the unchanged canonical final grammar and retain causal
rule/pass/contribution records. Successful repairs and honest historical summaries
survive later rejected operations. Existing transaction recovery remains separate
from live input authority.

The explicit offline selection `captured-text-utils-paragraph-v1` requires a
canonical absolute `--package-path` and the exact PDE/English configuration in
the contracts below. Its separate live committed receipt, private package
compiler and prepared publisher support only `embedded-ascii-v1`. The compiler
stages the retained root and exact manifest/lib bytes, requires all three actual
observed dependencies, and never loads an ambient package. Output preparation
precedes every validation/application attempt. Ordinary routes and old profiles
remain unchanged; no package installation or consumer rollout is performed.

For the exact admitted root and PDE/English configuration:

```sh
/path/to/typst-packages/tools/typst-lint/typst-lint fix --root /path/to/project --profile captured-text-utils-paragraph-v1 --package-path /path/to/typst-packages --target main.typ --offline-exclusive --format json
/path/to/typst-packages/tools/typst-lint/typst-lint compile --root /path/to/project --profile captured-text-utils-paragraph-v1 --package-path /path/to/typst-packages --target main.typ --output main.pdf --resource-profile embedded-ascii-v1 --offline-exclusive --offline-output-exclusive --format json
```

These assertions require the caller to stop all source/output writers. They do
not establish editor coordination or authorize a general all-project wrapper.
Private public-CLI tests verify nested targets and space-containing paths,
source-only/no-op repair, existing/new PDF publication, exact observed package
dependencies and resource-independent recovery under the unchanged 10-second
compiler deadline. A repertoire refusal preserves committed source fixes and
the prior PDF without launching a compiler.

Boundary insertion intentionally changes paragraph structure and indentation.
Private public-CLI tests compare mixed LF and initially unmarked CRLF/BOM repairs
with independently authored canonical documents. Their PDFs, text/word boxes and
rasters match exactly, including 1.5em indentation, intended capitalization and
unchanged protected names. Changed-length crash/recovery tests preserve later
edits and leave an unobserved postrename state explicitly unresolved.

Earlier case-only LF/CRLF and horizontal-spacing characterization observes
lowercase original rendering and uppercase after repair, unchanged indentation
and line positions, and subsequent words shifted only by the changed first-word
advance. These are tested author-intended effects, not a general original/final
rendering-equivalence claim or substitute for live compiler authority.
See [CAPTURED_TEXT_UTILS_PARAGRAPH_INPUT_CONTRACT.md](CAPTURED_TEXT_UTILS_PARAGRAPH_INPUT_CONTRACT.md),
[CAPTURED_TEXT_UTILS_PARAGRAPH_CORE_CONTRACT.md](CAPTURED_TEXT_UTILS_PARAGRAPH_CORE_CONTRACT.md),
[CAPTURED_TEXT_UTILS_PARAGRAPH_FIX_CONTRACT.md](CAPTURED_TEXT_UTILS_PARAGRAPH_FIX_CONTRACT.md),
[CAPTURED_TEXT_UTILS_PARAGRAPH_COMPILE_CONTRACT.md](CAPTURED_TEXT_UTILS_PARAGRAPH_COMPILE_CONTRACT.md) and
[CAPTURED_TEXT_UTILS_PARAGRAPH_PUBLISH_CONTRACT.md](CAPTURED_TEXT_UTILS_PARAGRAPH_PUBLISH_CONTRACT.md).

## Explicit offline source profiles

`protected_fix.py` connects actual parser/script analysis to the existing
protected transaction for deliberately closed input languages. The separately
selected CLI profiles below expose that engine. Ordinary `check`, dry-run,
`fix` without the profile, and unprofiled `compile` remain read-only with exit 2.

The two single-source profiles below require one literal `.typ` target in an explicit core-only config,
with no broader globs, excludes, ignores, packages or catalogs. An optional
entrypoint must be that target. The explicit source selection determines the
recursive official-AST grammar:

- `closed-core-v1` retains its existing literal prose and ordinary mathematical
  grouping/scripts. Its previous refusal of document structure remains unchanged.
- `structured-core-v1` adds literal headings, bullet lists using `-`, automatic
  numbered lists using `+`, strong `*…*` and emphasis `_…_`. Their bodies can
  contain the same recursively checked literal markup and equations.

Explicit numeric enumerators, term lists, imports, calls, code, resources, labels,
comments, directives and unknown syntax remain refused at any depth. The pinned
parser must recognize the exact supported structure; no source normalization or
fallback broadens admission. Existing internal calls default to the old profile.
The selected source profile and grammar version remain bound through proof,
independent replay, protected application and compiler snapshots. The
actual script rule executes. Each of the other 14 core rules has an explicit
source-derived inapplicability proof, re-established for every candidate. This
does not change the global 33-rule PARTIAL audit status.

The caller must explicitly assert offline exclusivity for the whole operation.
The engine never infers editor safety or creates that assertion itself. The CLI
requires the flag below and safely prepares its one fixed private journal store. Config and the exact reviewed full rule catalog are immutable
transaction evidence; callbacks parse their retained bytes without rereading
inputs. All 15 required core IDs are fixed in implementation. The transaction's
actual one-pass script repair must agree with an independent source-origin
convergence replay, preserve every original byte and finish with zero findings.
Only that same live session's independently verified candidate can be applied.

Source/evidence, metadata and journal reads have explicit bounds, including
revalidation, staging and recovery. Existing journals retain schema 1; an
oversized historical journal refuses rather than truncating, with deliberately
larger explicit limits available for recovery. This is process-crash recovery,
not a claim of power-loss durability or arbitrary editor exclusion. The internal
committed-source compiler below is reached only through the separately selected
compile route described next. No consumer project, installed package or existing
build route is changed automatically.
See [CLOSED_CORE_CONTRACT.md](CLOSED_CORE_CONTRACT.md),
[PROTECTED_CORE_CONTRACT.md](PROTECTED_CORE_CONTRACT.md) and
[TRANSACTION_BOUNDS_CONTRACT.md](TRANSACTION_BOUNDS_CONTRACT.md).

### Explicit commands and recovery

For a document containing only the admitted literal prose/math language, use an
explicit config such as `rule-set = "core"` and `include = ["main.typ"]`.
Only after all editors, watchers and other writers are stopped for the entire
operation, select both opt-ins:

```sh
tools/typst-lint/typst-lint fix --root /absolute/project --profile closed-core-v1 --offline-exclusive --target main.typ --format json
tools/typst-lint/typst-lint journal-status --root /absolute/project --format json
tools/typst-lint/typst-lint recover --root /absolute/project --offline-exclusive --target main.typ --journal-id JOURNAL_ID --format json
```

For supported headings, lists or emphasis, explicitly replace
`--profile closed-core-v1` with `--profile structured-core-v1` on fix or compile.
The same document remains refused under the old profile. Reports identify the
actual source profile and grammar version; selecting the new profile does not
claim any additional global rule is complete.

The fixed store is the canonical project's sibling `.typst-lint-journals`, with
a root-specific namespace. There is no option, config key or environment override.
Fix may create private storage under the root lock; existing unsafe permissions,
symlinks, unwritable parents or cross-device layouts refuse without chmod or a
fallback store. Status creates nothing, reads no current source/config and does
not assert editor safety. An absent store is an observation, not proof that no
historical transaction existed.

Keep the store and original canonical root location. Deleted/forged stores,
relocated roots and journals created by internal callers elsewhere cannot be
discovered by this route. Incomplete or corrupt journals block new fixes. Recover
only the named journal with its explicit target, even if current config is gone.
Later edits remain preserved and unresolved; there is no force, conflict
acknowledgment or migration switch. Recovery never compiles.

Successful fix/no-op, complete inventory or completed recovery returns 0.
Invalid arguments/profile/config/grammar/parser return 2. Unsafe storage, stale
identities, ownership/convergence disagreement, write failure, pending/corrupt
journals or unresolved recovery return 3. Status 1 remains reserved for active
findings; this profile requires a clean final candidate. Reports distinguish its
15-rule per-input proof from the global 33 PARTIAL rules. Source-only options do
not authorize compilation. Check, dry-run and typst-style compile reject these
opt-ins. See [OFFLINE_CLI_CONTRACT.md](OFFLINE_CLI_CONTRACT.md).

## Explicit relative scalar graph commands

The separate `captured-relative-core-v1` selection admits a bounded named relative
import graph while preserving the old two profiles. For example, `main.typ`:

```typst
#import "values.typ": count, label
= Example
#label $x_count$
```

Its `values.typ` contains inert scalar declarations:

```typst
#let count = 7
#let label = "captured"
```

Use `.typst-lint.toml` with `rule-set = "core"` and
`include = ["main.typ"]`. After stopping all source writers, select:

```sh
tools/typst-lint/typst-lint fix --root /absolute/project --profile captured-relative-core-v1 --offline-exclusive --target main.typ --format json
```

For compile/publication, also stop output writers and ensure `build/` already
exists with safe ownership and metadata:

```sh
tools/typst-lint/typst-lint compile --root /absolute/project --profile captured-relative-core-v1 --resource-profile embedded-ascii-v1 --offline-exclusive --offline-output-exclusive --target main.typ --output build/main.pdf --format json
```

Only independently verified root parentheses may change; all imported modules
stay read-only. Root imports must be an initial named minimal relative prefix.
The entire dependency graph must contain supported inert scalar definitions and
immutable alias/rename forwarding, including unused declarations. Packages,
helper/function calls, includes, resources, effects, comments/directives,
wildcard/namespace/dynamic imports and unknown syntax remain unsupported. All
sources pass the selected resource repertoire; the fixed imported scalar-text
budget and production 10-second compile deadline remain enforced. A size-admitted
input is not guaranteed to finish within that deadline.

Reports identify separate root/module grammar versions and distinguish captured
source membership from actual compiler-observed dependencies. A later compile or
publication failure preserves valid source fixes and their reports. Source/PDF
status and recovery commands remain independent and unchanged. Korean resource
selection retains the Regular/upright styling limitation below. This explicit
route does not install an all-project wrapper or change consumer build settings.
See [CAPTURED_RELATIVE_CLI_CONTRACT.md](CAPTURED_RELATIVE_CLI_CONTRACT.md).

## Explicit closed-core compile and PDF recovery

This route requires the same core-only configuration and an explicitly selected
source grammar, the
reviewed Mac arm64 Typst 0.15.1 binary, and one explicit resource profile:

- `embedded-ascii-v1` admits ASCII source with optionally one leading UTF-8 BOM
  and uses only the compiler's embedded fonts. It never inspects external fonts.
- `embedded-applegothic-ko-v1` adds modern precomposed Hangul syllables
  U+AC00–U+D7A3, modern compatibility jamo U+3131–U+3163, and exactly these 21
  prose punctuation characters: `· – — ‘ ’ “ ” … ₩ 、 。 〈 〉 《 》 「 」 『 』 【 】`.
  It requires the exact reviewed local AppleGothic font and uses a verified
  private copy, with system-font discovery disabled.

The Korean resource profile does not normalize text or broaden the selected
source grammar.
Conjoining/decomposed jamo, fillers (including U+3164), emoji, combining marks,
variation selectors, bidi controls and other non-ASCII source remain refused.
ASCII-spelled math names remain governed by the existing grammar. Unicode file
names are independent of this source-character restriction. Neither profile
supports imports, packages, arbitrary fonts or general Typst projects.

The captured AppleGothic resource has only a Regular face. In reviewed
structured fixtures, Korean strong/emphasis (including their combination)
renders as regular upright text; headings increase size without a bold face.
Latin and math can still use styled embedded fonts. Korean characters remain
legible and script repairs preserve the captured environment's render, but this
profile does **not** establish Korean bold/italic fidelity. A different reviewed
resource profile is needed before claiming that support.
An explicit Korean compile request reports this as a known profile limitation
in JSON and text, separate from actual captured resource evidence. Historical
recovery does not invent current resource observations or repeat this note.
Stop all source writers and separately all exporters/output writers for the
whole operation before making both assertions:

```sh
tools/typst-lint/typst-lint compile --root /absolute/project --profile closed-core-v1 --resource-profile embedded-ascii-v1 --offline-exclusive --offline-output-exclusive --target main.typ --output build/main.pdf --format json
tools/typst-lint/typst-lint output-status --root /absolute/project --format json
tools/typst-lint/typst-lint recover-output --root /absolute/project --offline-output-exclusive --output build/main.pdf --journal-id JOURNAL_ID --format json
```

Select `--resource-profile embedded-applegothic-ko-v1` instead of the ASCII
profile only for its admitted Korean repertoire. Missing, changed or unsafe
required font files refuse safely; there is no fallback to ambient fonts.
Font bytes are copied only into private local temporary storage and are not
bundled in this repository, CI fixtures, releases or uploads.
Use `typst-lint compile --profile closed-core-v1 --help` for the explicit
route's options and resource-profile names. Bare compile help remains the
ordinary route's help and does not enable compilation.

The output is one safe root-relative PDF with parents already present. Both
assertions, both profiles, target and output are explicit; no raw compiler tail,
custom compiler path, package path, watch/eval, template or extra output is
accepted. The combined session scans pending source/PDF journals and captures
the previous output before source application and compilation. Source and PDF
stores are separate. Existing source-only fix/status/recover commands still do
not scan PDF journals, and no recovery is automatic.

Successful publication returns 0. Input/unsupported-profile failures return 2;
unsafe or unresolved work returns 3. A natural compiler exit or fatal signal is
preserved only after proven-safe cleanup. Timeout, cancellation and stream-limit
cleanup cannot masquerade as a natural compiler signal. Compiler status 0 followed
by unsafe publication remains gate 3. Valid committed source fixes remain even
if compilation fails; missing apply receipts never prove source was untouched.

Output status is genuinely read-only and reports historical journals, not current
PDF conformance. Output recovery uses the exact named journal and output, without
needing current source/config/compiler/fonts, and preserves later edits. Reports separate
source recovery, output recovery, actual compiler facts and historical evidence.
Original reports retain actual captured font identities, hashes, sizes and face
evidence after temporary compilation storage closes. Historical journals retain
the selected profile but not those original resource details, and say so rather
than consulting current fonts. Updated readers still accept unchanged ASCII
journals; older readers can safely reject a new profile they do not know.
The executable defers received SIGINT/SIGTERM through bounded source application
and cleanup; safe cleanup can preserve the original signal, while unknown or
unresolved cleanup remains 3. Imported Python main never changes handlers or
signals its caller. See
[EXPLICIT_COMPILE_CLI_CONTRACT.md](EXPLICIT_COMPILE_CLI_CONTRACT.md).

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

The explicit entrypoint is safely captured for bounded book ownership. The
PDE pack, and core inputs with literal non-package imports, seed a read-only
graph of explicitly referenced relative imports from selected Typst bytes. It is limited to 512 files, 16 MiB, 4096 edges and
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
  backend; a separate named text-utils facade capability is described below.
  General import/include graphs and other facade API provenance remain unsupported. Unknown wildcard imports, bindings, dynamic language, show/output
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
These Markdown CLI paths remain read-only with exit 2 and never invoke the compiler.


## Protected offline transaction component

`transactions.py` is an independently tested storage component, connected only
to the explicit source profiles above. Its trusted-engine API requires an explicit assertion that
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
The original snapshot remains a baseline. After successful durable apply (including
no-op), a separate session-bound receipt retains the already verified postimage
records. Its guard revalidates the exact source, evidence, parents and metadata;
it never refreshes authority from later bytes. Bridge-owned compile snapshots
also require the actual independently validated applied candidate. Post-commit
handoff failures cannot roll back committed fixes. See
[COMMITTED_SNAPSHOT_CONTRACT.md](COMMITTED_SNAPSHOT_CONTRACT.md).

## Internal committed-source compiler experiment

`closed_core_compile.py` is the internal compiler engine used by the explicit
CLI route. It accepts only a live bridge-owned committed snapshot and one of the
two required resource profiles described above. Embedded-only fonts did not
preserve Korean text in characterization; the additional profile therefore
captures a specifically reviewed font rather than enabling ambient discovery.

The experiment uses the exact reviewed Mac arm64 Typst 0.15.1 executable hash,
a private verified executable/input copy, an empty environment with allowlisted
private directories, and the selected captured resource closure. Unknown
compiler bytes refuse before probing. Source/config/catalog/compiler and selected
font identities are checked before and after execution. Root-to-leaf descriptor
capture, retained output descriptors,
bounded diagnostics/artifacts, cancellation and exact relative dependency evidence
protect the internal result. Trusted OS libraries and sampled workspace limits
are explicit boundaries, not a complete OS or network sandbox.

A successful invocation returns immutable, session-bound PDF bytes and execution
evidence. It does not accept a user PDF destination or publish a file. Artifact
access rechecks live authority; valid committed source fixes survive compiler
failure or later staleness. Actual temporary native fixtures verify admitted
ASCII/Korean prose and math, including nested non-ASCII/space filenames. General
font/resource support, output publication, editor version integration and project
writer coverage remain separate work. The separately scoped
internal publication session below is the only new output-writing composition.
See [CLOSED_CORE_COMPILE_CONTRACT.md](CLOSED_CORE_COMPILE_CONTRACT.md).

The Korean resource is one complete, single-face TrueType AppleGothic file at
`/System/Library/Fonts/Supplemental/AppleGothic.ttf`, pinned by exact bytes and
metadata. No font redistribution permission is inferred. The private font file
is read-only in a private directory and has a 16 MiB capture bound. Actual
installed/private evidence is part of the sealed artifact; using it again also
rechecks the installed font. Typst's observed dependency list remains source-only
because the compiler omits fonts from that list. Explicit font capture is
separate evidence, not a fabricated dependency entry. These checks do not claim
equivalence to normal system-font discovery or unrestricted Unicode fidelity.

Compiler attempts retain immutable reporting separately from artifact authority.
Actual probe/compile launch, exit/signal and uncertain interruption facts survive
later safety refusals. A compiler exit of 0 is not overwritten by the gate's
failure status, and a version probe is not reported as an actual compilation.

## Internal single-PDF publication and recovery

`closed_core_publish.py` provides the `PreparedPdfPublication` engine used by
the explicit compile route. It requires its own explicit offline-output assertion in addition
to the protected source session. It checks pending publication journals and
captures the previous output or absence before source validation/application
and compilation. It owns the compiler call and cannot accept an arbitrary
previously generated artifact or bytes as publication authority.

The destination is one explicit root-relative PDF on the same filesystem, with
existing parents. Existing output may contain any bytes, but only the precisely
supported ownership, ordinary modes and metadata are admitted. The Mac's automatic
provenance metadata is retained and checked exactly, not stripped or ignored;
other unsupported metadata refuses. Links, unsafe ancestors, protected-path
collisions and later file replacements fail closed.

A separate fixed sibling publication store retains bounded binary backups,
stages and durable state records. After a visible destination replacement,
failures use guarded restoration or report recovery required; they never undo
valid committed source fixes or overwrite later output edits. An uncertain final
completion acknowledgment is not a successful build. Explicit output-only
recovery is independent of later source/config/compiler/font changes, and terminal
history is not a new observation of a user's current PDF.

Read-only publication inventory is separate from its writer/recovery paths.
The new combined session strengthens observed terminal records before treating
them as nonblocking. Existing standalone offline CLI `fix`, `journal-status` and
`recover` do not inspect publication journals, and ordinary CLI `compile` remains
inert. Neither these resource profiles nor SIGKILL/restart tests establish
general Unicode/system-font fidelity, universal power-loss durability, editor exclusion
or all-project enforcement. See
[PDF_PUBLICATION_CONTRACT.md](PDF_PUBLICATION_CONTRACT.md).

## Bounded prose footnote boundaries

A literal root paragraph ending in a proven builtin `#footnote[...]` can now
receive its required next-line `#parbreak()` when the next literal paragraph
already has the reviewed canonical marker. The footnote gap rule owns that
break-only proposal. If the marker is missing, the marker rule owns the complete
break-plus-marker repair, preserving every prose byte; eligible capitalization
is still produced by the real later rule pass. Ignoring the marker rule cannot
leave a gap rule's half repair.

Both paths require an existing whitespace-only paragraph gap, exact reviewed
text-utils marker/show evidence, canonical builtin-break availability, and no
lost directive/comment ownership. Unknown shows, shadowed bindings, nested or
structural boundaries and opaque footnote content remain refused. An existing
break is not duplicated; multiple breaks are never deduplicated. The narrow
root builtin-break resolver exception preserves state but never restores
unknown prose safety. Renderer fixtures verify unchanged footnote/word geometry
for marked prose and the intended 1.5em indent for a newly marked paragraph.
See [FOOTNOTE_COMPOSITION_CONTRACT.md](FOOTNOTE_COMPOSITION_CONTRACT.md).
Production source writes and compiler dispatch remain closed.

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
marker/required paragraph-break insertions and the package owns indentation. Actual text-utils 0.1.2 with
Typst 0.15.1 indents but does not uppercase as its comments claim, due to show-rule
precedence. No broad package recasing fix was made. The optional real-render test
verifies explicit `This`, 1.5em indentation and unchanged `iPhone` source/rendering.
Package/consumer source and installed copies are not changed by the tests.

## Bounded reference and display rules

The two `core.project-ref-*` backends inspect proven direct root calls and
direct literal bodies of proven builtin footnotes, including verified callee
aliases and parenthesized literal argument types. Footnote-body calls are
diagnostic-only. Extra/named arguments and
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
rather than acquiring a different target. Nested bodies, general facades and
noncanonical marker spellings remain unsupported; a certified named text-utils
facade can retain the existing canonical marker identity.

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

These are partial implementations, not 33 fully accepted rules. Their ordinary
CLI plans remain unapplied; the separate source profiles admit only their
strict input languages. General compiler dispatch and all-project rollout remain
disabled; the explicit resource-profile compile route above is separately scoped.

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

CI uses four workers per interpreter with the explicit `measured-v1` balance
profile in [ci-balance-v1.json](ci-balance-v1.json). It assigns estimated longer work first using
versioned GitHub log timings. Modules stay together except the reviewed
`CapturedTextUtilsParagraphBoundaryFixTests` and `CapturedTextUtilsParagraphFixTests`
methods, which use independent temporary consumers. Those methods can run in
separate worker processes, retaining
their original TestCase objects, per-method setup/cleanup and indivisible
subtests. Exact source/helper pins and fixture/hook checks reject unreviewed
changes to that exception. All discovered cases remain selected exactly once;
the default runner selection still uses whole-module round-robin.

```sh
python3 tools/typst-lint/run_test_shard.py --shard-index 1 --shard-count 4 --balance-profile measured-v1
```

The frozen baseline model estimates a longest test interval of about 12 minutes
instead of 22 minutes. The first [measured-profile CI run](https://github.com/kimpossible-TY/typst-packages/actions/runs/42440322213)
completed in 14m52s versus the previous 21m59s: about 32% shorter. The model
underestimated that run's slowest test interval; buffered log timestamps and
runner variance limit predictions for later runs. The following marker-gap
run passed in 18m14s, with the same shard still longest. Job counts, test and
compiler deadlines, adapter checks and both aggregate gates retain their
existing settings. The [partition evidence](audit/ci-balance-evidence.json)
records the earlier partition, not a fresh result for the current profile.
After the second reviewed class was split, replaying completed Python 3.15
[CI run 42475379713](https://github.com/kimpossible-TY/typst-packages/actions/runs/42475379713)
timestamps against the new assignment yields an approximate longest interval
of 768s versus 897s before splitting. This is an estimate, not a new CI run:
later added tests are not included, and buffering and runner variance apply.

Next work: extend only provable directive scopes and Markdown extraction contexts; expand
verified package/local-facade graphs and paragraph ownership; implement
the remaining rule contracts; validate protected writer/editor transactions and
broader compiler resources and general output publication; then verify
CI/watch/Tinymist adapters. AGENTS.md
or a shell wrapper alone cannot claim native editor enforcement.
