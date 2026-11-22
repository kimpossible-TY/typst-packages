# Partial relative import graph and import rules

This contract adds bounded, actual-source evidence for `pde.import-order` and
`pde.no-redundant-facade-import`. It does not complete either rule, the resolver,
the project graph, protected source writing or the authoritative compile gate.
The normative facade/package scenarios remain partial when initializer or
namespace evidence is unavailable. The rule catalog and fix policy are unchanged.

## Explicit read-only context

`load_import_graph(project_root, selected_sources)` accepts safely captured
`(project_relative_path, immutable_bytes)` pairs for selected Typst files. It
returns a frozen `ImportGraph`: original selected paths, captured source bytes,
verified import-request edges, and explicit unsupported/missing-edge reasons.
These sources are analysis context, not an expansion of the writable selection.
A dependency excluded by configuration may be read only because a real AST
import requests it. It receives no style diagnostics/edits merely by appearing
in the graph. Includes are neither followed nor treated as module exports.

The loader uses the existing retained-descriptor `_Snapshot` primitive under
one explicit root. It verifies selected bytes again and holds all successfully
opened files/directories through final content and metadata verification. It
rejects symlink components, symlink files, hard links, nonregular files, unsafe
roots, root escape, source changes, same-byte inode replacement and resource
exhaustion. Reads are UTF-8, bounded and no-follow, including FIFO protection.
Relative `.` and `..` normalize to one source identity, but **original traversal
components are checked first**: `linked/../leaf.typ` cannot erase a symlink from
the safety checks, and `missing/../leaf.typ` cannot invent an existing path.

Bounds are 1 MiB per source, 16 MiB for the captured graph, 512 files, 4096
requests/refusals and depth 32. Missing dependencies and unsupported requests
produce explicit refusals. Cycles are retained as edges but never certify a
pure module. No ambient package/cache lookup, network download, directory crawl,
include discovery, evaluator, arbitrary execution or package installation runs.
Package imports are recorded as unsupported, not resolved by path/version alone.

`rebase_graph(graph, path, baseline, candidate)` derives candidate-only source
context without filesystem reads. It requires the original selected path and
exact baseline bytes. A candidate can remove/reorder already captured import
statements but cannot add or change a request and reuse unchecked traversal
proof. Stale context remains stale. Every dependency retains its original bytes.
Snapshots are evidence for these bytes, not leases over live files or proof that
a later compiler run reads the same graph.

## Source-derived export/effect evidence

`GraphAnalyzer` parses the actual captured source with the pinned syntax-only
adapter. A pure module in this initial contract contains only root whitespace,
comments, literal relative imports with wildcard or simple named/renamed items,
and simple `let name = value` declarations. Allowed values are plain literal
strings, decimal integers of at most 18 digits, booleans, `none`, `auto`, or aliases
of previously proved bindings. Each local value identity is its source path,
source SHA-256 and defining name. Re-exports/aliases retain that identity through
real import edges. Equal spelling or equal literal values in different modules
are not identical exports. Duplicate or shadowed exports refuse proof, even if
the duplicate might evaluate to the same value.

Calls, closures, content values, destructuring, module aliases, dotted import
items, dynamic/escaped paths, arbitrary expressions, unknown packages, includes,
set/show rules, mutation, output and parse errors are unsupported. A complete
supported module has no unproved initializer or namespace tail. The per-analysis
cache only retains successful source-derived facts; depth/cycle checks remain
applicable when a cached dependency is reached through a longer path.

This is separate from the existing reviewed direct-package API contracts. For
example, `scoped-annotations` has counter initializers and an external wildcard;
`math-book` has further imports; `text-utils` and `project-references` define
closures. Those real sources do not obtain a pure-module certificate from this
narrow grammar, and their direct API hashes do not imply effect-free evaluation.

## Import order

The relative `Styles/styles.typ` spelling selects the facade convention only
after its actual graph proves nonempty re-exports. A same-named file containing
unrelated constants or output is not recognized as a facade. A local
`figures.typ` needs complete pure nonempty exports. The existing exact annotation
package requests can diagnose ordering, but do not justify moving an import.
Absent optional categories are never demanded and versions are never migrated.

A repair requires one initial contiguous recognized root import group with:

- Fully proved, disjoint imported local names and effect-free module graphs.
- No repeated target and no dependency from one group target to another.
- Standalone immediate import statements, no preceding execution/unknown import,
  no unrelated import or intervening nontrivia construct to cross.
- No comments/directives in the selected source. This deliberately conservative
  subset refuses even attachments that a future contract might prove safe.

The stable sort is facade, local figures, annotation packages. Complete statement
bytes move, while original separator slots, aliases, literals and versions stay
exact. Annotation-package evaluation is currently unsupported, so the positive
repair subset contains relative facade/figures groups, not package imports.

A genuine positive fixture uses `Styles/styles.typ` importing `tag` from a
captured `leaf.typ` declaring `#let tag = 1`, plus `figures.typ` declaring
`#let diagram = 2`. Reversing those two import statements is repaired from real
namespace/effect facts, with no caller-supplied synthetic purity flags.

## Redundant compatibility imports

A relative `Styles/local_tags.typ` or `Styles/mannot_utils.typ` must itself have
proved actual re-exports. All requested local names must currently equal the
identical names/identities provided by a retained preceding styles facade. The
live namespace is tracked only through the initial proved root import run;
execution or unknown imports stop that proof. A compatibility import used alone,
a new alias, a missing requested symbol, an intervening shadowing binding, or an
otherwise needed module namespace is retained. Wildcard syntax by itself grants
no evidence.

When every binding is retained and module evaluation is proved pure, delete only
the standalone compatibility-import line. Existing other gaps remain exact.
Comments/directives prohibit deletion, while suppression still attaches to its
original import diagnostic. A genuine positive fixture has styles and
compatibility modules importing the same binding from the same captured leaf.
Two modules each defining their own `tag = 1` are explicitly not that case.

## Candidate checks and evidence

Both analyzers return diagnostics, immutable edits, explicit refusals and partial
coverage. Proposals reparse and rerun with a freshly derived candidate graph;
second-pass edits, parse failures, overlap and suppression changes refuse edits.
The composed source analyzer additionally checks every enabled backend and
preserves source-bound skip/diagnostic ownership, including moved import syntax.
All normal CLI mutation/build gates remain fail-closed while coverage is partial.

`test_import_graph.py` and `test_import_rules.py` use actual temporary files and
exercise exact positives, absent categories, byte/CRLF preservation, source
identity, normalization, missing dependencies, cycles, links, root escape, size,
depth and graph budgets, snapshot races, dynamic imports, includes, aliases,
collisions, dependencies, comments, directives, malformed syntax, selection,
stale context and candidate reanalysis. `test_import_render.py` is opt-in via
`TYPST_LINT_RENDER_TESTS=1`: with Typst 0.15.1 and empty explicit package/cache
roots, it checks identical queried exports/metadata and identical SVG bytes
before/after the combined positive repair, then verifies zero second-run edits.
It writes only fixture-owned files and never relaxes the production compile gate.
