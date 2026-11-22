# Conditional scoped paragraph occurrence context

`scoped_paragraph_context.py` is a separate pure read-only analysis layer. It does
not change a resolver, CLI, source profile, writer or compiler bridge. Its report
is not COMPLETE coverage, an actionable repair, a candidate, a receipt, an editor
lease or permission to invoke a compiler. The selected 33-rule gate stays closed.
The existing root profile's structural discharges do not apply to this grammar.

## Inputs and trust boundaries

`analyze_scoped_paragraph_context(capture, *, package_trees=(), config=None,
catalog=None)` returns frozen records and tuples. `analyze` is an alias.
Malformed, foreign, altered or over-budget evidence raises
`ScopedParagraphContextEvidenceError` (`EvidenceError`); analysis never truncates
silently. Unsupported valid source syntax instead creates explicit unresolved
obligations and cannot create proposals through that unsupported context.

The consumer input must be the identical locally issued `IncludeContext` from
`include_context.load_include_context`. The analyzer invokes its pure issuance
assertion, then independently checks complete Tree structure, immutable bytes,
UTF-8 byte spans, source and full-Tree digests, all include-site coverage,
literal operands, full node ancestry, target normalization, and every ordered
entrypoint/edge-chain occurrence, including cycle endpoints. The assertion
checks original retained parser facts in memory. It never reopens files, calls a
parser or looks up a package/cache. Issuance authenticates the original captured
parser snapshot only. It proves neither current files, rendered execution,
style inheritance, runtime package selection, initialization nor write authority.
A full-Tree hash and structural validation alone cannot authenticate a parser.

Package input is an immutable tuple of `PackageTreeEvidence(import_path,
source_identity, tree)`. Complete package Trees receive independent structural
checks. Only exact pinned source hashes for text-utils 0.1.2 and
scoped-annotations 0.3.0 can establish conditional supported definition origins.
Exact known mannot namespace entrypoint evidence is also required for conditional
scoped binding identity; it remains separate from initializer coverage.
Package Tree constructors are public, unlike consumer capture issuance: their
parser provenance is explicitly conditional. No package manifest, live resource
resolution or whole dependency initialization is inferred from a path, hash or
Tree. Package identity, initializer coverage, invoked-body behavior and actual
occurrence render context are four distinct proof questions.

Config is optional bounded immutable UTF-8 bytes; only its digest is retained.
It never proves rendered language. Optional explicit catalog rows are copied to
immutable `(id, grade, source)` tuples. Empty supplied metadata stays empty;
there is no ambient catalog/config read and no ordinary Diagnostic construction.

The analyzer calls no Typst parser, filesystem reader, cache/package resolver,
evaluator, initializer certifier, compiler or mutation API. Supplied Trees and
bytes are never changed. No real consumers, fonts or runtime resources are
required by this API.

## Bounded grammar and declaration order

Each entrypoint starts with an empty lexical namespace. Direct literal includes
carry separate conditional language/show origins at the exact include position,
never parent lexical imports. A child must import its own helpers. Each include
site and entrypoint has a distinct occurrence identity even for identical
source bytes and spans. Sibling includes receive the parent state at their own
positions; child-local declarations do not leak back into the parent. Later
source declarations cannot establish earlier context.

The initial useful grammar admits direct named imports, including explicit
named renames, from the two exact local helper packages. Wildcards, facades,
namespace/field imports and other packages remain unresolved. Unknown namespace
state cannot establish builtin `text`. A set rule must be exactly builtin
`text(lang: "literal")` with one argument. Missing, dynamic, non-English,
conflicting or shadowed language cannot yield English proposals. English tags
use the existing case-insensitive `en` or `en-...` bounded tag convention.

A show rule must be exactly `#show: imported-apply-paragraph-tabs`. Its
`ShowOrigin` retains the binding value installed at that point, so a later
scalar or literal identity-closure name rebind does not rewrite that origin.
These inert rebinds still shadow future lookups, including builtin `text`.
Unknown/selector/book/arbitrary shows remain explicit and poison transparency;
later language or exact helper declarations do not erase an earlier unknown
show. Exact helper show rules add no language setting.

Literal root Text, BOM and whitespace preserve style facts but never supply a
callback paragraph boundary. Unknown root output, dynamic effects, stored
content, arbitrary wrappers and unsupported declarations remain unresolved.

A scope invocation must be a root-direct call to an independently imported
`local-tag-scope`, with one literal single-identifier callback whose body is a
literal content block. The callback parameter may be renamed, including supported
Unicode identifiers; it shadows the same-named lexical import in the callback.
Only `prefix` and `namespace` named options are admitted. Literal prefix,
default auto, explicit auto and dynamic prefix are distinct recorded modes.
Dynamic/invalid options and unsupported call shapes cannot create proposals.
Nested scopes, theorem/diagram/annotation helpers, field calls, spreads,
conditional/stored callbacks and unknown containers have no transparency proof.

## Owned paragraph regions and conditional policy proposals

`ScopeReference.callback_node` and `callback_span` refer to the exact interior
Markup of the callback, excluding its opening/closing brackets. Paragraph
regions are source-owned one-line literal Text/Space regions. A later unmarked
region requires an existing blank source boundary wholly within this callback.
Multiline continuation and any unsupported content retain unresolved coverage.

A marker records its original Hash node, Ident node/span, named-import binding
origin and immediate target gap. It must occupy its own interior source line,
with exactly one LF or CRLF plus horizontal whitespace before its literal target.
No target search crosses unknown content, comments, nested content, metadata,
callback brackets or an include boundary. A trailing, detached, consecutive or
non-prose marker remains an explicit unresolved obligation. Marker aliases keep
their origin but do not expand the canonical `paragraph_tab` capitalization
policy. Suppression/comment syntax is explicit unresolved ownership, never
silently treated as trivia or permission to propose a diagnostic.

Only a literal-prefix invocation in a conditional exact helper-show chain with
explicit literal English and a blank source boundary containing an actual direct callback-child
Parbreak node wholly within the callback can produce conditional informational proposals. `boundary_status` distinguishes
that owned internal boundary from an unresolved first or later region. A single
newline after an opening bracket cannot supply it, including for a marked first
region. A
capitalization proposal follows the existing lowercase multi-letter ASCII
editorial allowlist and requires a complete first word followed by whitespace.
Already uppercase, mixed-case, single-letter and unsupported words are excluded.
Later owned blank-boundary paragraphs may yield a conditional missing-marker
policy proposal. No proposal has replacement bytes or an Edit. Every scope explicitly retains
unproved entry/exit paragraph boundaries: even a first marker with a literal
immediate target does not prove that the opener or emitted prefix starts a
rendered paragraph. The first
callback paragraph never obtains a missing-marker proposal from opener,
metadata, counter/context content or neighboring root prose.

## Effects and deliberately open obligations

Exact pinned source supports conditional modeling of the explicit-prefix body:
construct helper closures, emit scope metadata, then call the callback. Default
and explicit auto record counter step, contextual counter read, helper creation,
metadata and callback in that order. These are content/effect records, not
removable lexical trivia. Auto mode does not create diagnostic proposals.
One callback call in source does not prove a single engine/layout evaluation.
Unknown shows invalidate transparency of metadata, counter/context and prose.

Every proposal retains conditions for whole package initialization, actual
runtime package selection, package Tree provenance, builtin identities across
the wildcard dependency, actual rendered occurrence, callback entry/exit paragraph boundaries,
namespace/counter collision
analysis, and selected-rule/suppression coverage. Every report also retains
these unresolved obligations without claiming overall completeness.

The scoped package wildcard-imports mannot and creates keyed counter handles.
Mannot has eager declarations and an eagerly imported tiptoe dependency; a
namespace-only entrypoint or supplemental hashes do not close those initializers.
Missing tiptoe source is not replaced by a fake module or a trusted boolean.
Collision obligations include repeated include occurrences/explicit prefixes,
auto versus literal `local-scope-1`, keyed counter resets/updates, generated
labels, graph membership and contextual output duplication/reordering. No
source-position identity proves a unique runtime namespace.

## Resource bounds

Consumer evidence is bounded to 128 sources, 8 MiB original bytes, 200,000
aggregate nodes, 4,096 edges and 1,024 occurrences, with include chains at most
16 edges. Each Tree is at most 1 MiB and 100,000 nodes, depth 128, exactly
`nodes - 1` child links and at most two typed math operands per node. Inputs
remain subject to the include capture's stricter acquisition limits too.
Packages are at most 16 Trees, 8 MiB and 200,000 aggregate nodes. The sum of
source-node counts over all syntactic occurrences is at most 1,000,000 and
occurrence-weighted original bytes at most 16 MiB before scanning, and installed show chains to 64 rules. Report facts are bounded to 100,000. Strings and explicit config
and catalog evidence have fixed bounds. Exceeding a bound raises a typed error;
it never returns a partial report mislabeled as exhaustive.
