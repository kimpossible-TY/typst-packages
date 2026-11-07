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

## Exhaustive root language

The original root contains only:

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

## Five required actual obligations and 28 structural discharges

The pure proof lists these five obligations as requiring later actual execution,
not as completed analyzer calls:

- `core.parenthesized-scripts`: actual script analyzer must be clean.
- `pde.paragraph-marker-gap`: actual gap analyzer must be clean.
- `pde.paragraph-marker-target`: actual target analyzer must be clean.
- `pde.paragraph-capitalization`: the only allowed actual edits; final analyzer
  result must be clean, including verified already-uppercase no-ops.
- `pde.prose-paragraph-marker`: actual `analyze_boundaries` invocation must pass
  the new exhaustive bounded checker.

`analyze_boundaries(tree, path, **explicit_authority)` independently rewalks the
original grammar and every prose boundary rather than consuming a caller proof.
It returns frozen source-bound boundary records and empty diagnostic/edit/skip/
parse-error/suppression fields on success. It refuses any uncovered boundary.
`check_boundaries` returns the same independently rederived boundary records.
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
