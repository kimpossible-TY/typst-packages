# Scoped reference repair contract

`core.scoped-reference-access` has a **partial**, syntax-backed repair for the
OpenSpec `fix-policy.md` contract:

```typst
#ref((s.tag)("energy"))
```

becomes:

```typst
#(s.ref)("energy")
```

The rule proposes an in-memory byte edit. The production CLI remains read-only,
returns status 2 for incomplete analysis, and never invokes the compiler. A
successful proposal is not an authoritative dependency-graph, write-transaction
or runtime equivalence certificate.

## Reviewed local definition and scope evidence

The existing resolver accepts the exact `@local/scoped-annotations:0.3.0`
entrypoint, SHA-256
`eb8e7ff7b31238c8fa4b996deb9502fec0ae4610240427a8b40873708e509203`, from
explicit loader-verified `PackageSource` inputs. The production package loader
also checks the manifest and source identity. A matching spelling, path/version,
facade or altered entrypoint is not sufficient.

A reference repair additionally requires the authentic namespace artifact for
`@preview/mannot:0.4.0`. Its exact manifest hash is
`aef4310b36955dc98d8b03ec4abf17d45777d0a08b65d6e67214752e57647545`, and its
`src/lib.typ` hash is
`288027cea059b327bf3253c44ae975339e7afc0f149375d9d87ca1c1fe739f57`.
That entire entrypoint has two explicit named imports. It exports neither `ref`
nor `label`, so the wrapper's wildcard import cannot shadow those builtin names.
Named imports do not leak the other names of their dependency modules. The
namespace attestation says nothing about raw mannot API behavior.

In that exact local definition, `local-tag-scope` creates `tag` as
`local-name => label((name)(local-name))` and `reference` as
`local-name => ref(label((name)(local-name)))`, then exports `reference` as the
same dictionary's `ref` field. `local-scope-annotations` forwards that scope's
`tag` and `ref` without replacing them; `mannot-scope` forwards the annotation
wrapper. This is the same reviewed local-definition boundary already used by
`scoped_provenance.py`, strengthened for this repair by the explicit namespace
attestation, not a new inference about raw mannot re-exports.

A recognized scope constructor must have a literal single-parameter callback,
without unsupported constructor arguments or deferred execution. Both the
original field and its direct identifier receiver must carry the exact package
hash and the same resolver-produced scope identity. The replacement uses that
original receiver, never a guessed nearest callback parameter or a generated
namespace. Nested scopes therefore keep their distinct targets.

Named/renamed constructor imports, reviewed module aliases, immutable constructor
and receiver aliases, and immutable aliases of the caller's builtin `ref` use
actual lexical binding evidence. A shadowed receiver or builtin does not inherit
its old name's meaning. An alias of `s.tag` alone cannot identify an available
corresponding receiver, so it is diagnostic-only. The resolver withholds scope
proof for malformed syntax, mutation, includes, conditionals/loops and deferred
ordinary closures/context expressions. Those cases retain explicit analysis
refusals, rather than gaining spelling-based repairs.

## Exact supported source shape

- The outer call is a proven builtin `ref` identifier, including a verified
  immutable alias, with exactly one positional generated-target call. There are
  no options, named arguments, spreads or additional arguments.
- The inner callee is a direct parenthesized field access `(receiver.tag)`.
  Its receiver is an identifier bound to a reviewed tag/annotation scope.
- The inner call has exactly one actual AST `Str` argument. Its complete source
  bytes are preserved, including Unicode and escapes; it is never interpreted,
  normalized or reconstructed from a guessed target. An optional trailing comma
  and whitespace in the retained call remain unchanged.
- There is no comment anywhere inside the outer reference call. Existing
  comments before/after the call remain untouched. Line breaks in the removed
  outer `ref(...)` scaffolding are refused rather than removed or relocated.
  Newlines, including CRLF, inside the retained inner call are preserved.
- The edit spans only the outer `FuncCall`, leaving any markup `#` outside it.
  It retains the original inner call, changes the proven `tag` field token to
  `ref`, and removes the enclosing builtin-call scaffolding. Surrounding prose,
  target bytes, labels, other references, BOM and unrelated line endings remain
  unchanged.

Unsupported but proven generated references still produce their original
finding and a `core.scoped-reference-access repair refused: ... at byte ...`
reason. Examples include `s.name`, a variable/computed target, a receiver-losing
function alias, options, extra arguments, comments and outer line breaks.
Unknown/custom same-spelled helpers do not become proven scoped findings; their
missing package/resolution evidence stays an analysis refusal.

## Suppression and candidate composition

Own-line valid directives suppress the original call's finding and edit.
Configuration ignores remove the rule's proposals before combined validation.
No directive is moved, deleted, reattached or broadened. The whole candidate
passes the existing source dispatcher checks: parsing, nonconflicting edits,
zero second-pass edits, retained analysis coverage, no newly introduced finding,
unchanged suppression ownership and deterministic independent reanalysis.

The unparenthesized form `ref(s.tag("energy"))` is deliberately not repaired by
this reference contract. Although the separate scoped-function-call rule can
propose parentheses, that would unlock a new reference repair on a second pass.
The current combined candidate verifier refuses all such combined edits with
`Combined candidate has second-pass edits`. It does not apply an overlapping
rewrite or silently leave a supposedly converged candidate needing another fix.

`tests/test_scoped_reference_repairs.py` exercises actual ASTs and reviewed source
bytes, exact candidates and idempotence, constructor/builtin/receiver aliases,
shadowing, nested scope identity, Unicode/BOM/CRLF, comments, suppressed calls,
missing/altered provenance, literal and generated-name refusals, and the
unparenthesized interaction. Its subprocess CLI fixture composes reference,
scoped-function-call and script repairs with a retained duplicate-namespace
finding and suppression. Check, dry-run, fix and compile modes all retain status
2, no writes and no compiler invocation, then the test alone applies the exact
candidate to its temporary input and verifies zero further edits.

## Explicit optional namespace loading

`static_resolver.MANNOT_NAMESPACE`, `MANNOT_NAMESPACE_SOURCE_SHA256` and
`MANNOT_NAMESPACE_MANIFEST_SHA256` identify the artifact. Its separate
`REVIEWED_NAMESPACE_PACKAGES` registry is never used to create ordinary resolver
API/module bindings. In particular, a direct user wildcard import from mannot
still invalidates unknown names; the empty semantic API list must not be treated
as a complete empty namespace.

`package_loader.load_packages(explicit_root, imported_paths)` requests this fixed
artifact only after verifying the exact scoped-annotations package. Both the
manifest and source are copied into immutable `PackageSource`/`PackageEvidence`
snapshots under that same explicit package root. A dependency is not discovered
through package evaluation, arbitrary imports, a user's home/cache, installed
packages or the network. The analyzer checks actual supplied bytes, not a
boolean or test-only attestation. Tests use the authentic public manifest and
two-line entrypoint in `tests/fixtures/scoped-reference-packages`, with original
MIT license and provenance, never a fake implementation.

An initially absent optional component/file yields a
`missing-optional-namespace` record. Reference findings remain diagnostic-only
with an explicit ref/label-shadowing refusal, while other bounded rules may
still propose their own edits. Direct user-requested artifacts remain required.
Malformed manifests, unsafe paths, permission errors, disappearance after an
existence check and changes during capture fail closed. Changed manifest/source
hashes never produce namespace evidence. Every component of `src/lib.typ` is
opened with retained no-follow directory descriptors before reading the final
basename; no slash-containing path bypasses the existing safe read contract.

`tests/test_scoped_namespace_loader.py` covers exact evidence, no traversal,
each optional absence, mandatory direct requests, unverified wrappers, raw API
and wildcard isolation, altered/malformed inputs, links/nonregular files,
permissions and file/directory races. The CLI regression also proves missing
namespace evidence does not cancel an unrelated scoped-function-call proposal.

## Runtime dependency and rendering limit

The namespace snapshot establishes only the selected entrypoint's exports. It
does not evaluate `mark.typ`/`annot.typ`, certify their behavior or capture the
runtime tiptoe dependency. Existing dependencies are neither fetched, installed
nor changed by this repair. Broader facade/generated-name forms, full graph
capture and authoritative compilation remain outside the current contract.

No scoped-reference rendering test is claimed at this checkpoint. The portable
namespace fixture intentionally omits the remaining runtime graph. A future
isolated before/after fixture must pin and verify the complete selected graph,
copy only those verified inputs into a temporary explicit package path, forbid
network/download fallback, and compare target/ref query results and rendering.
It must not replace a dependency with a fake module or modify the package source
to obtain a successful render.
