# Marker tags and annotation bounds contract

`core.scoped-marker-tags` and `core.annotation-marker-bounds` are diagnostic
only. They never migrate public labels, invent marker regions, rename helpers,
change mathematical content or propose edits. Production source transactions
and compilation remain disabled; these partial source backends do not certify
a complete document graph or rendered geometry.

## Authentic definitions and captured namespace

The existing exact `@local/scoped-annotations:0.3.0` wrapper has SHA-256
`eb8e7ff7b31238c8fa4b996deb9502fec0ae4610240427a8b40873708e509203`.
Its callback identities and immutable aliases come only from the actual scoped
resolver. A same-spelled function or a caller-created dictionary is not a scope.

Raw `@preview/mannot:0.4.0` functions additionally require the actual reviewed
definition files and their captured namespace. The authentic public fixture is
`tests/fixtures/marker-bounds-packages`; its original MIT license, immutable
public registry commit, Git blob identities and SHA-256 values are documented
in that fixture's `PROVENANCE.md`. The already-pinned manifest and two-line
entrypoint match those independently fetched public registry bytes exactly.
The source snapshots are never evaluated and runtime code does not discover
test folders, installed packages, caches or network dependencies.

The namespace entrypoint alone remains insufficient for a raw function
binding. Raw `mark` has the exact `mark.typ` definition hash and requires its
named `util.typ` dependency. Raw `annot`, `annot-cetz` and `core-annot` have the
exact `annot.typ` definition hash and require that dependency. Its tiptoe import
is a named module binding, not a wildcard that could replace `label` or another
captured builtin. This contract does not claim that the omitted tiptoe/CeTZ
runtime graph has been captured or rendered.

The local `s.mark` factory captures raw mannot `mark`. A `scope-marker` result
therefore exists only after the resolver establishes that actual capability.
Its literal target name and scope identity survive immutable aliases, while an
unapplied factory supplies no bounds. Actual applications accept one body and
no additional/named/spread arguments in this bounded contract. Target override
or customized factories are refused rather than assigned their old target.

## Raw global marker tags

Inside a recognized `local-scope-annotations` or compatible `mannot-scope`
callback, an authentic raw `mark` call with exactly one body and one named
`tag` argument is reported when that argument is an actual Typst `Label` node.
Both ordinary code calls and math calls are parsed by the official AST, with
the math escape `#` treated as syntax rather than part of the label value.
Renamed imports, reviewed module members and immutable function aliases use
real binding identity. Ordinary semantic labels outside marker calls and
scope-generated tags are not reported. The narrower tag-only scope is not
treated as an annotation callback for this rule.

## Ordered plain-tag bounds proof

The bounds rule requires authentic mannot namespace and `core-annot`
definition capabilities. It handles statically known same-scope targets of
`s.annot`, `annot-cetz-local` and raw `annot`/`annot-cetz`. The supported call
shapes have exactly their positional arguments, with no options/spreads.
Targets are unescaped ASCII string literals or literal arrays thereof;
generated target labels must be the actual same-scope `tag`/`tags` helpers.
Computed strings, unknown aliases of generated values and escaped/Unicode
names remain explicit partial coverage rather than guessed equivalence.

A negative claim needs more than failing to find a marker in source text:

- The display must be direct output of a literal reviewed callback, either a
  content block or a final returned content block preceded only by immutable
  lets. The scope call itself must be direct root output or recursively direct
  callback output. Stored content and arbitrary wrapper arguments are refused.
- Actual output is classified in source order. Literal math/text atoms are
  known not to emit marker metadata. A plain tag must immediately attach to a
  proven marker-free atom. A tag after an opaque producer, or directly after
  another marker, does not gain a plain-target attestation.
- Authentic raw marker applications and applied resolver-issued scope-marker
  bindings produce marker events. Factories alone do not. Unknown calls,
  emitted variables/content, dynamic targets, nested callbacks, control flow,
  mutation, includes and unclassified output defeat the display proof.
- The nearest preceding known event for an overlay target must be its plain
  tag. An older marker cannot repair a later plain tag that becomes the last
  matching query result. Conversely a later actual marker before the overlay
  replaces the plain event and prevents this finding. A marker in another
  display is never borrowed.
- Known duplicate explicit prefixes or same-parent child names, different
  target scopes, and any source show/set rule refuse the diagnosis. General
  namespace evaluation and style interpretation are not implemented.

This ordering follows the actual reviewed `core-annot` call to
`query(selector(tag).before(here())).last().value`. The local CeTZ overlay
checks `anchor-bounds` before invoking any CeTZ drawing implementation. Reading
inert identifier/literal arguments cannot execute a later drawer before that
check. Unknown argument calls are refused. The only executable drawing
argument exception is an actual captured CeTZ `line` definition returning an
inert drawing instruction, with inert argument evaluation and no mutation,
bindings, spreads or unknown calls. This is not a general statement that
arbitrary drawable contents cannot emit markers later. Raw `annot` measures
its annotation before reading target bounds, so that separate argument must be
literal marker-free content; a content identifier cannot hide executable work
under this exception.

`display_proof` preserves duplicate marker events for the anchor rule;
`scope_ownership` exposes the same direct-output/collision boundary. These are
pure AST classifiers over resolver facts, not synthetic trusted bindings.

## Tests and limitations

`tests/test_marker_bounds_rules.py` uses actual parser trees and an explicit
temporary package root containing authentic public source snapshots and the
reviewed local wrapper. It covers positive diagnostics, marker applications
and aliases, same-scope ordering, older-marker/later-plain collisions, unknown
output/targets, missing/changed definitions, shadowing, stored content, styles,
explicit namespace collisions and original directive ownership.

`tests/test_supplemental_artifacts.py` independently checks fixed safe capture,
separate marker/annotation/util capabilities, namespace-only compatibility,
immutable resolver records, duplicate/altered inputs, direct dictionary-call
refusal, aliases/shadowing, and supplemental links, nonregular files, permission
failures and deterministic capture races.

CLI tests exercise check, dry-run, fix and compile routes. Both findings remain
diagnostic-only, status remains 2 for incomplete analysis, inputs/package bytes
and output sentinels remain unchanged, cache stays empty, and a compiler
tripwire is never invoked. No before/after rendering claim is made because no
edits exist and a complete runtime graph is not supplied.
