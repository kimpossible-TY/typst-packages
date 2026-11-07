# Captured local-facade export provenance

## Capability and authority boundary

`facade_provenance.py` proves a bounded **export-access capability** over an
immutable `ImportGraph` plus explicit immutable `PackageSource` snapshots. It
does not evaluate Typst, read files, discover packages, expand the writable
selection, install anything, or enable compilation/source writes.

The evidence is deliberately independent of `import_graph.ModuleEvidence`.
`FacadeEvidence.accepted` says that the supported module initializer safely
exposes a complete set of identified exports. It has no `pure` field. In
particular, a deferred function's identity and safe initializer do not prove
purity or output/style safety when that function is called. Import ordering,
redundant-import removal and `GraphAnalyzer` retain their existing authority and
refuse facades that contain package imports.

## The first and only package initializer certificate

Only `@local/text-utils:0.1.2`, whose actual entrypoint bytes have SHA-256
`7e151150dac47b01ebf7706457906133d74a52abee1b5c43ce824ff89279bd71`,
has a safe-initializer certificate. The existing reviewed contract must also
match namespace `local`, name `text-utils`, version `0.1.2`, entrypoint `lib.typ`
and the complete six-export namespace.

The official parser must show exactly six root declarations, with only
whitespace/comments between them:

- `paragraph-tab` is the exact escaped U+F000 literal string.
- `paragraph_tab` is an immutable alias of that prior value.
- `apply-paragraph-tabs` and `highlighted` are deferred functions with exactly
  one identifier parameter `body`.
- `capitalize-title` and `is-numeric-title` are deferred functions with exactly
  one identifier parameter `title`.

There are no parameter defaults/destructuring and no root imports, includes,
calls, output, mutation, set or show rules. The exact-byte pin also fixes all
deferred bodies and proves the package has no dependencies. Calls inside those
bodies are not executed or treated as pure by this certificate. No other
reviewed package, version, modified source, closure or initializer inherits it.

`PackageSource` keeps its established trusted-loader input contract: the package
loader validates actual manifest namespace/name/version/entrypoint metadata,
safe descriptor reads and source identity before constructing the snapshot.
The facade layer receives no manifest bytes or metadata flags and invents none.
It checks the actual source bytes/AST and matches the resulting package origin
to the resolver's already-validated package binding, including import path,
canonical export name, source identity and source hash. A path/version or
caller-supplied completeness annotation alone never proves a helper's identity.

## Facade grammar and complete export accounting

Supported root statements are:

- Literal relative imports with explicit simple named or renamed items.
- Explicit simple named or renamed imports from the certified package above.
- The same bounded literal values as the existing relative-module contract:
  unescaped strings, nonnegative decimal integers of at most 18 bytes,
  booleans, `none` and `auto`.
- Simple immutable aliases of prior bindings.

Adjacent root hash statements may be terminated by a semicolon. Every binding
is an export and is accounted for, including unused ordinary literal values.
Local literals retain the original local path/hash/name identity. An equal
U+F000 local string is not the reviewed marker. Renames and immutable aliases
retain the original value identity; both reviewed marker spellings refer to the
same value.

The whole facade is refused for any unknown/unsafe statement, even if unused.
This includes wildcard imports anywhere, namespace/dotted imports, dynamic
imports, parenthesized initializers, calls, closures/content, forward aliases,
duplicate/shadowed exports, destructuring, includes, mutations, output, set/show
rules, loops and conditionals. Malformed syntax, unavailable dependencies,
cycles and over-budget graphs also refuse. An unavailable requested export
refuses the whole import proof, not just that individual item.

## Captured graph and resolver use

`resolve` and its internal resolver accept optional `import_graph=None` and
`source_path=None` parameters. Existing direct imports are unchanged. Without a
graph no facade capability is created, and a path alone adds no new skip.
Construction is lazy until the first relative named consumer import; direct
imports do not scan the graph or parse an initializer certificate. A captured
source-level syntax/resource refusal is rejected before any repeat parse.

A consumer must be a selected path in the graph and its analyzed bytes must
equal the captured bytes exactly. A relative target is obtained only from
captured `target_for` edges. The facade analyzer validates immutable graph
structure, canonical identities and the existing budgets: 512 source files,
16 MiB aggregate source, 4096 edges/unavailability records, 1 MiB per file and
proof depth 32. Certified package leaves count toward proof depth. Captured
unavailability never broadens the relative lookup boundary.

Frozen evidence includes a complete sorted export map, source hashes,
transitive relative dependency path/hash identities and genuine reviewed package
origins. The analyzer caches only successful proofs within that analysis, and
checks both ancestry and total depth before reusing them. It has no ambient or
persistent cache. A rebased candidate receives a fresh analyzer against its
candidate-only graph, never a baseline proof rescued by path/hash guesses.

Only explicit named consumer relative imports can translate a genuine package
origin through existing `package_binding` validation. Ordinary exports bind
unknown/non-API, so an imported literal called `text` shadows builtin `text`.
Refused wildcard consumers preserve the existing all-binding/builtin
invalidation. Module aliases and dotted accesses do not receive facade APIs.

Canonical marker insertion still requires the canonical `paragraph_tab` name
to be available with the reviewed show already installed. An alias-only marker
can identify an existing value but cannot invent an insertion spelling.
Rebinding/mutating the marker removes later availability; rebinding a show's
former alias does not uninstall the function value it already captured. Other
package, book, theme, spacer and scope proofs are unchanged.

## Verification

`tests/test_facade_provenance.py` uses genuine loader snapshots and the official
parser with temporary consumers. It covers the exact initializer and structural
checks, complete local/package identities, multi-hop aliases, language/marker
availability, equal-looking local literals, swapped helper kinds, consumer
shadow/mutation/wildcards, missing exports/packages/graphs, stale/wrong source
paths/bytes, unsafe unused statements, unsupported packages/closures/defaults,
cycles, ancestry-sensitive cached depth, resource budgets, immutable history and
fresh rebased contexts. It also verifies that the separate capability cannot
grant import-reordering purity. CLI composition/candidate evidence is
tested independently by the integration suites.


The opt-in `tests/test_facade_render.py` uses actual Typst 0.15.1 and pdftotext
in isolated temporary consumers. Direct, one-hop and two-hop variants match
extracted text and bounding boxes at corresponding original/intermediate/final
stages across 18 renders. Actual CLI history separates marker/required-break
insertions from capitalization, with the intended 18pt indent at 12pt and
unchanged protected prose, math and footnote geometry. Production CLI calls
remain read-only with exit 2; compiler dispatch is a separate test-only action.
Package/dependency bytes, an explicitly empty cache and an output sentinel stay
unchanged. These fixtures do not prove arbitrary facade or document equivalence.
