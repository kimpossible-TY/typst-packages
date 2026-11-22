# Scoped anchor and local overlay contract

This is a bounded direct-source implementation of `core.scoped-anchor-access`
and `core.local-overlay-api`. All analysis consumes actual pinned-parser ASTs
and immutable package snapshots supplied by the explicit-root loader. There is
no caller-supplied list of marker names, fabricated receiver, namespace-spelling
heuristic, package execution, ambient cache discovery or network access at
analysis time. It does not claim complete graph coverage or rendering equality.

## Exact authentic callable evidence

The local scope helper is the unchanged reviewed
`@local/scoped-annotations:0.3.0/lib.typ`, SHA-256
`eb8e7ff7b31238c8fa4b996deb9502fec0ae4610240427a8b40873708e509203`.
Its `node` and `anchor` fields denote the local `scope.anchor` helper, which
combines that exact scope's generated name with a literal dot and direction.
Its `annot` converts names with the same scope's `tag`/`tags` functions before
calling `annot-cetz-local`. The latter places a rectangle named from each
actual marked target's scoped tag into the CeTZ canvas. The genuine mannot
namespace/mark/core-annot artifacts are necessary to prove the captured
namespace and actual marker applications. A scope-marker factory alone is not
an applied marker and supplies no anchor ownership proof.

The raw `annot-cetz` diagnosis requires the shared resolver's
`is_mannot_binding` predicate. It identifies the actual canonical binding from
unchanged mannot 0.4.0 `src/annot.typ`, SHA-256
`12ea7288aeb7378bb6cdf8747d0d94fb3828def856521540a881edf25f4d42bd`,
through its genuine entrypoint and fixed captured artifacts. Merely calling a
function named `annot-cetz` proves nothing. See the marker-bounds fixture's
provenance for these authentic public artifacts.

The coordinate role requires exact `@preview/cetz:0.4.2` root-module, draw-module
and `shapes.line` definition bindings from the shared resolver, not a function
named `line` or a receiver named `cetz`. The fixed capture includes the genuine
manifest, entrypoint, draw entrypoint, shapes, three shapes-wildcard namespace
files, coordinate and anchor modules. Their hashes, Git blob identities and
namespace argument are in
`tests/fixtures/anchor-overlay-packages/PROVENANCE.md`. No implementation is
replaced by an empty or synthetic module. The source proves that all positional
`line` arguments are coordinates, while named options are not coordinates.

## Supported conditional anchor repair

An eligible overlay is a direct output expression of one display Equation in
an owned literal local callback. It has exactly three positional arguments:

- The actual scope's `annot` field receives one unescaped ASCII local name or a
  literal array of such names. Alternatively, actual `annot-cetz-local` receives
  that scope's exact `tag` or `tags` call with literal names.
- The second argument resolves to the genuine reviewed CeTZ module, including
  immutable/module aliases, never an arbitrary dictionary.
- The drawable is one literal code block whose direct code statements are
  genuine CeTZ `line` calls. A line has at least two positional arguments,
  without spreads, duplicate named arguments, arbitrary nested calls or
  non-line drawing operations. Named options are restricted to `name`, `close`,
  `stroke` and `fill`. Unreviewed custom mark/style callbacks are not inferred
  marker-free. A direct proven scope `node`/`anchor` helper in a coordinate is
  accepted and does not produce a raw-anchor finding.

The strict display proof must be complete. Every matched marker target is
identified by the same resolver-issued scope identity and literal local name,
not by a reused parameter spelling. It must have exactly one actual marker
application preceding that overlay in the same display and no conflicting
plain-target output. The actual `core-annot` queries the last matching label
before its location, so a marker appearing only later is not ownership proof.
Unknown calls, stored/opaque output, duplicate applications, conflicting
explicit namespace ownership and styles that may replace content withhold the
proof. An unmatched, dynamic or ambiguous overlay target remains untouched.

Only direct AST `Str` coordinate arguments are considered. A raw string must
equal the complete known local name plus one exact reviewed compass suffix:
`east`, `north-east`, `north`, `north-west`, `west`, `south-west`, `south`, or
`south-east`. Unrelated ordinary node names, unknown directions, named style
strings and escaped/computed strings are not interpreted. A known ordinary
line-node name that collides with the local marker or its raw anchor defeats
the diagnosis rather than guessing which node the user intended.

The repair obtains the actual direct identifier receiver from the original
`annot`, `tag` or `tags` field access. The receiver must have the same immutable
scope identity and reviewed local source hash. The replacement uses its `node`
field for annotation scopes and `anchor` for tag-only scopes. A function alias
that loses the receiver remains diagnostic-only; the analyzer never invents a
receiver from a nearest callback parameter.

The raw marker and direction bytes are preserved. The edit replaces only the
original string token, leaving its surrounding comments, argument punctuation,
line endings, BOM, Unicode receiver, markup `#`, suppression directive and all
other source bytes in place. For example, `"x.north"` becomes
`(actual.node)("x", "north")` when `actual` is the proven original receiver.
Dotted marker names are compared against complete known names, never split
blindly. Although an exact binding can support a finding, repair is explicitly
refused: CeTZ's reviewed raw string resolver splits the first dot, and the local
helper provides no reviewed disambiguation for dotted node names.

For the same reason, a conditional repair also proves the generated scope
prefix is dot-free from the actual constructor AST. It accepts a plain literal
dot-free explicit prefix, or auto generation with the reviewed default/literal
dot-free namespace. A parent/name-derived prefix requires the actual resolved
parent scope recursively to satisfy that proof and a dot-free literal child
name. Dynamic, escaped or dotted prefixes/namespaces remain diagnostic-only.
A scope identity or friendly-looking parameter spelling is not prefix evidence.

The combined dispatcher reparses the entire candidate and requires zero
second-pass edits, deterministic independent analysis, retained proof coverage,
no new finding and unchanged suppression ownership. Configuration ignores remove
proposals before that validation. The standalone tests also verify exact bytes,
parsing and a second independent rule pass.

## Diagnostic-only raw overlay use

A real raw `annot-cetz` call in a recognized literal local callback emits
`core.local-overlay-api`. Imported/immutable aliases are recognized through
actual lexical bindings; shadowing or an unknown/custom same-spelled function
cannot acquire that identity. A raw call outside a known local callback is not
this rule's finding. The rule never renames the API or changes arguments,
geometry, targets, wrappers or bytes. `annot-cetz-local`, scope `annot` and the
supported compatibility `mannot-scope` wrapper remain accepted.

## Test and runtime limit

`tests/test_anchor_overlay_rules.py` has 20 test methods and uses real ASTs and explicit loader captures
of genuine selected package bytes. It checks positive repairs, aliases, scopes,
all supported directions, actual marker application, same-display ownership,
ordinary-node collisions, unknown geometry/output, literal/dotted-name refusals,
comments, Unicode/BOM/CRLF, suppression, changed artifacts and raw-overlay
no-edit behavior. Composed script/anchor repairs retain exact suppressed calls
and directive ownership, and configuration ignores remove only the ignored
rule's proposals before full candidate validation. Its subprocess CLI scenario checks check/dry-run/fix/compile,
exact proposal bytes, source hashes, no source/output writes, no compiler call,
then an isolated test-applied candidate with zero further edits.

Portable fixtures are intentionally incomplete runtime graphs. They provide
source/namespace/argument-role evidence, not a successful Typst render. No
rendering regression is claimed until a complete verified runtime graph can be
captured and rendered in isolation without downloads or substitutions. Existing
consumer projects, installed packages, and the authoritative production gate are
not changed by this work.

The independent `tests/test_cetz_provenance.py` capability suite has 13 test
methods. It checks the real fixture AST namespace chain, all seven missing and
changed supplemental artifacts, namespace-only/root-line refusal, exact safe
snapshot paths and hashes, named/module/function aliases, actual import source
identifier spelling, partial-wildcard masking, dynamic/shadowed/deferred/mutated
bindings, nested supplemental symlinks and post-capture races. Every capability
is produced by the actual loader and resolver; no test constructs a Binding or
Facts object to stand in for package provenance.
