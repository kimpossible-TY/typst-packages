# Internal captured-relative scalar core proof

This module provides pure proof infrastructure. The exact public offline selector
`captured-relative-core-v1` chooses a separate graph composition; it is not a
member of the old single-source grammar registry. Pure certification does not
perform CLI dispatch. The root-only repair bridge consumes these proofs only with
its own transaction and independent validation authority; see
[CAPTURED_RELATIVE_FIX_CONTRACT.md](CAPTURED_RELATIVE_FIX_CONTRACT.md). Both
existing public source profiles and every PARTIAL rule status remain unchanged.
The internal graph compiler separately consumes only the bridge's own live
committed graph, never a pure proof; see
[CAPTURED_RELATIVE_COMPILE_CONTRACT.md](CAPTURED_RELATIVE_COMPILE_CONTRACT.md).
Certification alone is not script execution, repair authority, compilation,
resource/repertoire approval or permission to publish a PDF.

## Pure boundary and authority

`certify_captured_relative_core(target_path, captures, *, source_profile=SOURCE_PROFILE)`
accepts one canonical project-relative writable `.typ` target and an exact mapping
from every reachable source path to its original immutable markup `Tree`.
`Tree.source` is the bounded immutable original UTF-8 byte payload. No stripped
import source, reconstructed body, caller-supplied export map, ordinary
`ImportGraph`, discovery result, or constructible `ModuleEvidence(pure=True)` is
authority. The mapping includes the root and no unreachable sources.

The function does no filesystem access, parser subprocess work, catalog reread,
resource lookup, evaluation, Typst execution, or Python `eval`. It reuses the
existing syntax-client representation validator to check every exact node field,
UTF-8 boundary, exhaustive parent partition, preorder/link/depth property,
error-flag propagation, typed Equation display property, and MathAttach operand
metadata. Grammar traversal visits every original node. Structural validation
cannot authenticate a fabricated but structurally plausible parser tree. A future
consumer must obtain the original bytes and Trees through its owned pinned-parser
protected capture transaction and retain that authority independently.

`verify_captured_relative_core(proof, *, target_path, captures, source_profile=SOURCE_PROFILE)`
rederives all facts from the caller-selected scope using recursive type-exact
comparison, so Python boolean/integer/float equality cannot hide changed values
or metadata types. It never selects a language,
target, source map, or grammar using fields supplied by the proof. Proofs are
frozen, privately issued, and bound to exact path membership, source length/hash,
tree fingerprint, parser contract, separate root/module grammar versions, edges,
bindings, scalar origins, provenance, imported uses, and all rule reasons.

`CapturedRelativeSyntaxError` denotes a fully representation-validated parser
syntax error or a construct outside this exact language, including missing named
exports and duplicate/ambiguous bindings. `CapturedRelativeAuthorityError`
denotes invalid or changed tree/capture/path/profile/proof authority, missing or
extra captured closure members, and budget exhaustion. Verification turns any
new certification refusal into changed authority. Initial capture may classify a
missing dependency observed before descriptor acquisition as an input error; a
later missing captured member is an authority failure. These distinctions are
intended for a future bridge, not a new public error/fallback policy.

`discover_relative_requests(tree, path)` returns bounded `(request, target)`
pairs in parser preorder. It validates the original Tree and scans every node for
include/import constructs, rejecting includes, dynamic/package imports, wildcard,
namespace, and dotted named items. It does not prove statement placement, module
purity, completeness, closure, source ownership, or write applicability. Discovery
is only a way for protected capture to acquire candidates before full proof.

## Exact root language

The root grammar version is `captured-relative-root-grammar-v1`. It reuses the
existing `structured-core` literal prose, equations, math fractions, attachments,
parenthesized/delimited groups, headings, lists, enumeration, Strong, and Emph
checks, without altering that public grammar. It adds only:

- An initial root-level prefix of standalone named relative import lines.
- Plain `#name` references to integer/string scalars proved in the fixed imported
  environment. These can occur in admitted nested markup wrappers.
- Imported names in existing `MathIdent` tokens. The existing MathIdent spelling
  restrictions are unchanged. A proved imported scalar may shadow a builtin.

Imports are never allowed inside wrappers or after the first body construct.
Leading literal whitespace and the existing optional leading BOM are preserved
where the pinned parser represents the BOM as its initial Text token. Statement
markers are exact, `#` is adjacent, and import/declaration lines contain no other
output. Semicolon statement termination and multiline statements are outside this
initial grammar. Original bytes, CRLF, whitespace, and BOM are not normalized.

The import environment is complete before any root body or nested wrapper is
visited. Duplicate destinations, shadowing, and ambiguous names are refused.
`#` code inside math, calls, fields, arbitrary expressions, functions, content/code
blocks, packages, include, styles, mutation, comments, and directives are refused.
One-character `MathText` and the tokens in `foo-bar` are never reinterpreted as
references to a similarly spelled imported binding. Code binding names use
`[A-Za-z][A-Za-z0-9_-]*`, at most 128 UTF-8 bytes. Root MathIdent retains the
existing more restrictive alphanumeric spelling. Unimported ordinary MathIdent
continues to mean exactly the existing structured-core token, not a new resolver.

## Exact module language and scalar identity

The separate module grammar is `captured-relative-module-grammar-v1`. A module
contains only literal whitespace, an optional initial BOM, standalone named
relative import lines, and standalone `#let name = initializer` declarations.
Imports and declarations can interleave; alias initializers can reference only
an earlier proved binding in that module. Every initializer is exhaustively
checked even if its declaration is unused by the root or by any import item.

Literal initializers are nonnegative decimal integers with 1–19 ASCII digits and
value at most 9223372036854775807, plain unescaped single-line strings, exact
`true`/`false`, `none`, or `auto`. Signed/radix/unit/floating literals, expressions,
parenthesized initializers, calls, resources, arrays, dictionaries, closures,
content, style rules, output, and mutation are refused. Boolean/none/auto values
are inert and may be imported/aliased but may not be rendered through root
MathIdent or `#name`. Only integer/string render uses are admitted initially.
No ordinary `static_resolver` semantics are broadened.

Each literal has a declaration origin containing exact path, original source
SHA-256, declared name, declaration node/span, and initializer node/span. A binding
retains scalar kind/value, that origin, and the full ordered declaration,
transitive alias, named-import, and rename provenance, with source-bound
node/spans for every step. Two equal-valued declarations stay distinct origins.
A diamond re-export of the same declaration retains one origin and distinct
binding routes. Unused imports are traversed and certified too.

The reachable import closure must be exact, acyclic, and complete. Cache reuse
retains longest dependency suffix depth so a shallow diamond traversal cannot
bypass a deeper route's bound. No exponential path enumeration is needed.

## Pure relative paths and fixed budgets

Source paths are canonical root-relative `.typ` identities. Requests admit only
necessary leading `../` components followed by ordinary components, remaining
inside the explicit project root. `./`, canceled components such as
`nested/../leaf.typ`, unnecessary parent re-entry such as `../a/leaf.typ`
from `a/main.typ`, repeated separators, absolute/package paths, backslashes,
escapes, control characters, and non-UTF-8 paths are refused. Resolution is purely
lexical and never asks the OS to normalize or inspect a path. Capture must still
check every actual directory entry without following symlinks.

There is no public budget override:

- 32 captured files including the root.
- 4 MiB aggregate original source bytes; 1 MiB per source.
- 128 import statement edges, including repeated imports of one target.
- 16 import edges of maximum root-to-module depth.
- 64 distinct project parent directories including project root and every ancestor.
- 512 UTF-8 bytes per source path and per import request; 16 KiB total source-path bytes.
- 200,000 aggregate original AST nodes, also subject to existing 100,000 nodes and
  depth 256 per Tree.
- 4,096 total bindings, counting each declaration and each named import destination
  once per reachable source, not once per path through a diamond.
- 8,192 rendered imported uses.
- 64 provenance steps per binding, counting the literal declaration and every alias
  or named import, including the final root import.

The file/path/byte/parent limits fit inside the internal graph compiler's 128-entry,
64 KiB dependency framing, and 96 MiB workspace envelope. These source limits alone
do not establish compiler authority or prove resource admission.

## Imported uses and independent replay

Every imported root MathIdent or `#name` occurrence records its exact node/span,
name, kind/value/origin/full binding provenance, preorder use ordinal, enclosing
wrapper/equation owners, and nested attachment operand owners. Grouping is
transparent to the semantic owner identity. The convenience semantic identity
retains exact declaration origin and the named provenance route, but omits use
byte/node offsets and provenance-step hashes/spans. It is not sufficient repair
authority. The exact proof separately retains every source-bound hash and span.
The internal bridge compares exact module proofs and full provenance, normalizing
only the root import step's necessarily changed whole-source hash. Initial import
prefix bytes, node identities and spans remain exact; body use offsets change
only through independently proven parentheses insertions.

Focused real-parser tests apply actual `script_rule.analyze` proposals through
`edit_plan` and confirm that original root/module bytes and Trees are not mutated,
only script-boundary parentheses are inserted, and imported-use semantic owners
survive nested scripts, fractions, wrappers, builtin shadowing, and punctuation
strings. These pure tests alone do not authorize source writes.

The separate internal bridge independently rederives insertions, enforces exact
dependency bytes/edges and root imports, compares owners/origins/provenance,
re-certifies final candidate structure and actually executes the script rule.
The separate graph compiler additionally validates its full resource repertoire
across root and modules and a fixed imported scalar-render charge limit.
Equal scalar values or equal use records alone never authorize
rewriting, compilation or publication.

## Core rule applicability

The proof identifies `core.parenthesized-scripts` as the rule requiring actual
later execution. It does not claim to have executed it. Module grammar contains
no math operands. All 14 other core preconditions are discharged across every
root/module node: no calls/fields/receiver or scope creation, annotation scopes,
markers/labels/drawings/references/overlays, project-ref calls/catalog targets,
book helper applications/reset/show owners, set-theme rules, reusable closures,
or theme-resolution calls exist. Graph-sensitive reasons state that every
reachable import is **exhaustively inert**, not that imports do not exist.
