# Internal captured text-utils paragraph proof

`captured_text_utils_paragraph_core` provides a pure, private proof for the exact
profile `captured-text-utils-paragraph-v1`. The proof itself adds no CLI,
compiler, transaction, source store, installation or publication authority.
A separate explicit offline selection composes this proof with the protected
repair and package compiler/publisher. The older single-source grammar registry
remains unchanged. All 33 global rule statuses remain PARTIAL.
A proof does not authorize a consumer write, establish parser provenance, prove
that actual analyzers ran, authorize compilation, or waive an ordinary skip.

## Explicit caller-selected evidence

`certify(tree, path, *, config_bytes, catalog_bytes, package_source,
manifest_bytes, package_tree, source_profile=SOURCE_PROFILE)` consumes original
immutable markup Trees, never stripped imports or a reconstructed body. The
caller independently owns the pinned parser and protected capture transaction.
The pure module performs no filesystem access, parser subprocess work, live
catalog loading, package lookup, Typst evaluation, or source mutation.

`validate_config(raw, target_path)` is the sole strict config parser shared with
capture. It returns a frozen `ParagraphConfig` with target, optional matching
entrypoint, English language and exact raw config hash. Required keys are
`rule-set="pde"`, `include=[one canonical literal .typ target]`, and
`capitalization.language="en"`. Only an optional equal entrypoint is allowed.
Other keys, glob targets, other languages/tags, implicit language, boolean
lookalikes, malformed TOML and duplicate keys are refused.

The catalog must be the complete exact reviewed bytes with SHA-256
`955b0c721027369ec1fd100a7da00fc7304693bb40e2916904c6f3b06fae1826`.
The catalog parser independently verifies the pack composition and the proof
checks all 33 selected IDs: 15 core and 18 PDE. Every obligation retains its
catalog record hash and the selected root source/tree hashes.

The exact `static_resolver.PackageSource` must have no supplemental artifacts,
import `@local/text-utils:0.1.2`, a canonical absolute source identity ending
`/local/text-utils/0.1.2/lib.typ`, and the exact 5,583 source bytes with SHA-256
`7e151150dac47b01ebf7706457906133d74a52abee1b5c43ce824ff89279bd71`.
The proof binds that actual absolute identity, not only its suffix. The exact
173 manifest bytes must have SHA-256
`e1d27052412a39d4f839dede28406159c1c9a5394592a73c2ed5666dcbc60df2`,
and metadata name=text-utils, version=0.1.2, entrypoint=lib.typ. The package Tree
must bind the identical source bytes and pass the full representation validator.
Its complete node facts and fingerprint are retained. The package is semantic
evidence, not another selected PDE source.

Capture separately derives and retains the genuine six-export initializer
certificate from its own transaction bytes. Neither a caller's accepted flag nor
that initializer certificate substitutes for this module's independently pinned
bytes/tree/metadata facts. Conversely, this pure proof does not replace capture's
initializer, file-identity, no-follow, race, alias or transaction checks.

### Invoked helper review is separate from initializer safety

The exact `apply-paragraph-tabs` closure at lib.typ lines 11–22 is separately
reviewed and retained by exact byte span and hash. It installs two fixed regex
show rules: one examines the private marker followed by optional whitespace and
a lowercase capture, extracts captured text and emits `h(1.5em) + upper(char)`;
the other replaces the standalone marker with `h(1.5em)`. It then emits `body`.
There are no external reads or persistent mutations in this invoked closure.
The private character itself is forbidden in selected consumer literal text.
The linter's only permitted casing change remains the source's first ASCII byte.
Unused `highlighted`, title and numeric-title helper bodies are never invoked.
Initializer/deferred-body safety alone grants no call safety to those helpers.

## Exhaustive final canonical root language

`certify` and the unchanged final grammar
`captured-text-utils-paragraph-grammar-v1` require only:

- Optional initial BOM and literal horizontal/LF/CRLF whitespace.
- Exactly one root standalone direct named import of `paragraph_tab` followed
  by `apply-paragraph-tabs` from the exact package. No alias, namespace,
  wildcard, facade, extra item or extra import is admitted.
- Exactly one standalone root `#show: apply-paragraph-tabs` on a later physical
  line, before every prose paragraph. Calls, parenthesized expressions,
  customized options or other show transformations are refused.
- At least one marked paragraph, with an optional one document-start unmarked
  paragraph. Every paragraph is direct literal Text/Space and optionally already
  clean closed inline math with typed `display=False`, on one physical line.

Markers are adjacent root `Hash('#')` plus leaf `Ident('paragraph_tab')`, never
calls. Their physical line contains only horizontal whitespace around them.
The immediately preceding physical line is whitespace-only and LF/CRLF
terminated. Marker-to-first-word bytes are exactly horizontal whitespace, one
LF/CRLF, and horizontal whitespace. Every paragraph after existing prose has
its own canonical immediately associated marker and an existing blank boundary.
No marker is invented, removed or moved by this grammar.

Marked starts are complete ASCII words from the existing 26-word editorial
allowlist, immediately followed by ordinary whitespace. Lowercase starters
record exactly one uppercase-byte replacement. Title case and all uppercase
are verified clean no-ops. Unknown words, mixed case, single-letter notation,
non-ASCII starts, missing word boundaries and every other capitalization skip
are refused. No searching forward through earlier content occurs. The optional
initial unmarked paragraph is exempt from the marker/capitalization policy.

Inline math reuses the existing closed structured math grammar, with exhaustive
node/operand/delimiter checks. Every explicit script operand must already be
parenthesized. No dirty scripts are repaired by this slice. Every AST token and
descendant must be consumed, including import/show descendants and trivia.
Unknown tokens are not filtered away. Headings, lists, strong/emphasis,
references, raw, escapes, nested content, comments/directives, sets,
declarations, arbitrary expressions/calls and private marker characters are
outside this language. Literal whitespace admits LF and CRLF, not lone CR.

The existing static resolver independently resolves actual marker and show
identifiers to the exact package/import/source identity and proves root prose,
English language and nondeferred ownership. Helper spelling alone grants no
semantic identity.

## Separate initial/intermediate repair admission

`certify_repair(tree, path, **explicit_authority)` uses the same public source
profile and exact capture/config/catalog/package semantics. It privately issues
`CapturedTextUtilsParagraphRepairProof` with the distinct internal grammar
`captured-text-utils-paragraph-repair-grammar-v1`. This is not a new public source
profile, final compiler proof, completed actual rule evaluation or transaction
lease. `verify_proof` rejects this different proof type. Final `certify`, its
proof type, grammar version and `compare_candidate` behavior remain unchanged.
All previously admitted canonical forms retain their original clean proofs.

Repair admission additionally permits only these source-owned root cases:

- A standalone existing canonical marker without its immediately preceding
  terminated whitespace-only blank line, including the first marker immediately
  after the exact show declaration.
- An after-existing-prose whitespace-only boundary with a parser-owned root
  `Parbreak`, but without its required canonical marker.
- Any mixture of those cases and correct paragraphs. Initially zero markers are
  supported when at least a second paragraph provides an existing boundary for
  marker insertion. A lone unmarked first paragraph remains unsupported by the
  unchanged canonical final language; document start never creates a marker.

All other restrictions above still apply. In particular every paragraph remains
one physical line, and every future marked first word must already satisfy the
same exact allowlist, case, completeness and effective-English eligibility.
The initial unmarked paragraph stays exempt. No block, explicit parbreak call,
footnote, continuation, display, comment/directive, arbitrary call, wrapper,
facade or package behavior is newly admitted. No boundary is discovered by
stripping setup text or regex-scanning otherwise unconsumed syntax. The entire
validated AST is exhaustively consumed, and an unmarked boundary additionally
requires its actually consumed whitespace `Parbreak` sibling. The resolver must
independently prove canonical marker availability and the active reviewed show
at every insertion target.

A repair proof retains source-bound `Repair(rule_id, paragraph_ordinal, start,
end, replacement)` records in `.repairs`, always exact insertions. Repair
boundaries retain their previous/next paragraph owners, exact original gap bytes,
first node, and optional original marker nodes/spans. Only existing marked
paragraphs have capitalization contexts and pending first-byte edits on this
pass. An inserted marker cannot retroactively authorize same-pass casing.

The exact insertion policy follows the normative fix catalog and reviewed rule
implementations:

- Missing marker gap: insert one LF/CRLF at the marker's physical line start,
  before any original indentation. Choose CRLF if that marker line's terminator
  is CRLF or the immediately previous line ends CRLF; otherwise choose LF.
- Missing marker: insert exactly `#paragraph_tab` plus LF/CRLF at the following
  prose line's physical start, before and preserving its original indentation.
  Use CRLF exactly when the preceding source terminator is CRLF. The existing
  blank boundary, every prose byte, BOM and all original newlines are preserved.
- The later separately attributed capitalization pass may change only the
  eligible first ASCII byte. Existing marked casing and unrelated insertions
  can occur together on the first pass. No import/show/package bytes change.

The bridge must report repair grammar and final canonical grammar separately,
and obtain actual independently derived rule proposals. `.repairs` is expected
ownership evidence, not proof those analyzers ran. A distinct exhaustive bounded
backend may share the reviewed paragraph policies while independently deriving
the four actual paragraph-rule results; the actual script evaluation and 28
structural discharges separately account for the full 33-rule matrix. The generic
prose-marker analyzer's unconditional partial skip remains real and cannot be removed or waived.

## Five required actual obligations and 28 structural discharges

The pure proof lists these five obligations as requiring later actual execution,
not as completed analyzer calls:

- `core.parenthesized-scripts`: actual script analyzer must be clean.
- `pde.paragraph-marker-gap`: actual bounded gap evaluation must independently
  derive exactly the admitted missing-blank insertions; the final actual result
  must be clean.
- `pde.paragraph-marker-target`: actual target analyzer must be clean.
- `pde.paragraph-capitalization`: the only owner of source casing edits; final
  analyzer result must be clean, including verified already-uppercase no-ops.
- `pde.prose-paragraph-marker`: actual exhaustive bounded marker evaluation
  must independently derive all paragraph owners and existing boundaries,
  propose exactly the admitted missing-marker insertions, and confirm every
  required marker is present on the final source.

The protected bridge executes `bounded_paragraph_rules.analyze` on the initial,
intermediate and final Trees, checking independently derived owners and actual
rule results against admission expectations. Initial/intermediate gaps or
markers may need the exact recorded repairs; final actual evaluation must have
no diagnostics, edits, skips, parse errors or suppressions. Those actual calls
are distinct from this pure proof module.

The unchanged canonical-only legacy API
`analyze_boundaries(tree, path, **explicit_authority)` independently rewalks the
original clean grammar and every prose boundary rather than consuming a caller
proof.
It returns frozen source-bound boundary records and empty diagnostic/edit/skip/
parse-error/suppression fields on success. It refuses any uncovered boundary.
`check_boundaries` returns the same independently rederived boundary records.
They remain available for canonical clients, but neither is an additional
required duplicate call in the bounded repair bridge. Canonical proof obligation
reasons remain unchanged; repair proof reasons separately describe admitted
proposals followed by final clean actual evaluation.
The ordinary `prose_marker_rules.analyze` generic partial skip is not stripped,
filtered or declared complete. The bridge must retain this distinction.

The precise remaining applicability conditions are disproved as follows. Each
row is retained in the proof with root source/tree and catalog bindings:

- `core.scoped-function-call`: no root calls/fields/scope receivers; no package
  scope API.
- `core.scoped-marker-tags`: no annotation scope, mark call, label/tag argument.
- `core.scoped-anchor-access`: no annotation drawing, marker declaration or
  anchor/drawing call.
- `core.scoped-reference-access`: no reference or generated scoped tag/name.
- `core.local-overlay-api`: no local scope, raw annotation or overlay call.
- `core.annotation-in-display`: no annotation call; every equation is inline.
- `core.annotation-marker-bounds`: no overlay target/scoped marker/label.
- `core.unique-scope-namespace`: exhaustive root and exact dependency-free
  package contain no scope creation/API; no other source extends the graph.
- `core.project-ref-arguments`: no project-ref binding or call.
- `core.project-ref-catalog-target`: no project/label target, independent of
  external reference catalog contents.
- `core.single-book-show`: text-utils is not a shared math-book helper, so the
  rule's using-shared-book-helper precondition is false.
- `core.no-duplicate-book-reset`: no book owner or reset application.
- `core.no-set-theme`: no root set or custom theme application.
- `core.theme-in-context`: no root reusable helper/closure/theme-resolution
  call; unused reviewed package closures remain uninvoked.
- `pde.import-order`: the single text-utils import provides no recognized
  styles/figure/annotation category pair to order.
- `pde.no-redundant-facade-import`: no styles/compatibility facade import.
- `pde.continuation-line`: every prose paragraph occupies one physical line.
- `pde.display-continuation-gap`: no display/prose boundary.
- `pde.proof-trailing-gap`: no proof binding/call/body/ending boundary.
- `pde.footnote-trailing-gap`: no footnote body/call/ending line.
- `pde.prose-footnote-punctuation`: no prose footnote.
- `pde.aligned-footnote-spacer`: no display/alignment/footnote/spacer.
- `pde.unaligned-footnote-no-spacer`: no display/footnote/spacer.
- `pde.prose-footnote-no-spacer`: no prose footnote/spacer.
- `pde.standalone-display`: no typed display equation.
- `pde.text-only-highlight`: no imported/called highlight helper; the unused
  reviewed package export is not invoked.
- `pde.theme-cancelmark`: no math call/cancel/facade/cancelmark binding.
- `pde.flowbox-colored-border`: no flowbox implementation or flowbox
  closure/call/border/stroke argument in root or package.

## Replay, candidate comparison, limits and error types

Proofs are frozen and privately issued. `verify_proof(proof, tree, path,
**explicit_authority)` rederives everything from caller-selected inputs and uses
recursive type-exact comparison. Boolean/integer equality cannot hide changed
positions, counts or metadata. Missing fields, foreign proofs and oversized
altered tuple structures are rejected against the bounded derived shape.

`compare_candidate(before, after, *, before_tree, after_tree, path,
**explicit_authority)` re-verifies both proofs. It requires exact Node kinds,
spans, edges, errors, operand roles/owners and typed display metadata. ASCII
one-byte replacements preserve all offsets. It derives the full expected
candidate from the original recorded edits and rejects every other changed
byte, omitted change, residual edit, or statement/boundary/package ownership
change. The bridge separately executes actual analyzers and owns write authority.

`verify_repair_proof` independently rederives every repair admission field,
with exact types and expected tuple lengths, and additionally checks weak
identity-based private issuance. A copied repair proof, even with an equal
issuer/field payload, is not issued. Boolean coordinates, missing/changed fields,
forged/cyclic records, oversized tuples and foreign proofs are rejected without
using caller object equality as evidence. Registry entries retain no proof once
the original proof is no longer live.

`compare_repair_step(before, after, *, before_tree, after_tree, path, step=None,
**explicit_authority)` verifies both admissions from selected authority and
independently derives the complete current pass: every recorded insertion plus
only pre-existing marked capitalization edits. It replays original bytes exactly,
rejects growth above the root bound, and derives a one-to-one original-to-new
semantic node map. Import/show subtrees, original markers, ordered paragraph
content/first words and every inline-math descendant retain mapped spans,
child structure, operand roles/owners and typed metadata. Original whitespace
nodes may split or merge after insertion, but every original whitespace byte is
preserved by exact replay and both grammars still consume every AST token.
No caller-provided offset map, move record or equal-looking copied text proves
original-byte survival.

When supplied, the genuine convergence `Step` must have exactly these proposals,
one exact separately attributed contribution per edit, one typed pass number,
no deferred edit and no move. Duplicate marker ownership, alternate spelling,
deletion/replacement, recasing newly marked prose on the insertion contribution,
changed math/package/statements and omitted or extra changes are rejected.

`compare_repair_candidate(initial, clean_final, *, before_tree, after_tree, path,
candidate=None, step_trees=(), **explicit_authority)` requires the unchanged clean
final proof, zero pending casing, independently replayed exact original-to-final
bytes and the same semantic node ownership map. Any changed candidate also
requires genuine convergence evidence and the bridge's exact parser Tree pair
for each pass. The pure module never reparses. It re-certifies every supplied
intermediate, verifies the connected strictly progressing history, and checks
typed causal rule/pass attribution and minimal composed baseline envelopes.
Widened replacements containing equal copied original bytes are rejected even
when they replay to the right final source. Candidate/Step mapping methods are
never trusted or invoked. Actual final reanalysis and independent zero-edit
idempotence remain mandatory bridge responsibilities, not a consequence of a
pure structural proof.

Retained immutable records bind exact import/show node identities and spans,
marker Hash/Ident ownership, each paragraph's first word/Text span and content
nodes, every after-prose boundary's previous/next owners and gap bytes, package
identity and every rule obligation. Package and root AST fingerprints bind the
complete validated metadata rather than only selected syntax kinds.

There are no caller budget overrides:

- One canonical root target, at most 512 UTF-8 path bytes, no glob metacharacters.
- One original root payload at most 1 MiB, at most 100,000 nodes, maximum depth
  256, checked by the existing strict parser representation validator.
- At most 256 prose paragraphs, including the optional unmarked first one.
- At most 512 independently derived edits per pass and two actual edit passes.
  All current proposals apply on each pass, so only newly marked lowercase
  starters can need the second pass. Histories contain at most two Tree pairs;
  semantic node maps contain at most the original 100,000 nodes. Source growth
  remains bounded by the same 1-MiB root limit, not by a caller override.
- Configuration and catalog each at most 64 KiB.
- Exactly 5,583 package source and 173 manifest bytes; package identity at most
  4,096 UTF-8 bytes. Package Tree uses the same strict node/depth bounds.

Tree links/operand shapes are preflighted before copying; tree validation checks
all UTF-8 boundaries, parent partitions, preorder links, depth, error propagation,
Equation display types and MathAttach operands. The proof's root source digest and AST
fingerprint are each derived once per certification, never once per marker.
The fixed-count representation and resolver checks may hash the payload again. Paragraph/boundary traversals are
bounded by the node/paragraph limits; retained gaps are disjoint source slices.
Contexts share the one source digest. Proof comparison walks only the bounded
expected immutable shape and rejects unequal tuple sizes before traversal.
Repeated explicit verification is deliberate independent replay, not a claim
that arbitrary 1-MiB sources have a trivial proof cost.

`CapturedTextUtilsParagraphSyntaxError` refuses a representation-valid source
outside this language. `CapturedTextUtilsParagraphAuthorityError` refuses invalid
or changed config/catalog/package/tree/path/profile/proof authority and budget
exhaustion. Both derive from `CapturedTextUtilsParagraphError`, `SafetyError`
and `InputError` for controlled bridge/capture translation. Verification converts
a new admission refusal into an authority-change error. Native rendering and
full aggregate validation belong to the parent and are not established by pure
module tests.
