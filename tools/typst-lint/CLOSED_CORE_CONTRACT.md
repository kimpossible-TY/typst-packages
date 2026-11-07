# Closed core applicability contract

`closed_core.certify(tree, path, *, source_profile='closed-core-v1')` derives
applicability for exactly the 15 core catalog rule IDs on an explicitly selected,
recursively closed source grammar. The pure `source_profiles.py` mapping fixes:

- `closed-core-v1` → `closed-core-markup-v1`, unchanged original language.
- `structured-core-v1` → `closed-core-markup-v2`, the structured extension below.

Unknown names, aliases, case/whitespace variations, and `latest` refuse. Selection
does not consult files, resources, a catalog, or the OS. Both admission and proof
verification default to v1, so existing callers do not gain the broader grammar.
`closed_core.GRAMMAR_VERSION` remains the historical v1 default constant.
This is not a complete-coverage claim for general Typst or the PDE pack. The
global 33-rule audit stays PARTIAL.

## Authority and binding

The protected bridge owns the pinned official-parser invocation and passes the
resulting immutable `syntax_client.Tree`. It must never accept a caller-supplied
Tree as parser evidence. Admission rechecks exact Tree/Node/tuple types, immutable
UTF-8 bytes, 1 MiB input, node/depth bounds, preorder and exhaustive partitions,
error flags, equation display metadata, attachment roles, token bytes and grammar.
These checks do not authenticate parser provenance. No pure structural validator
can establish that an arbitrary in-process caller's fabricated Tree was produced
by the official parser. The bridge-owned parser is the authority boundary.

Admission and proof verification do not parse, read files, resolve imports, load
a catalog, or evaluate Typst. `CoverageProof` is module-issued, frozen and bound to
the exact root-relative `.typ` path, source length/SHA-256, complete validated tree
SHA-256, exact selected source profile, grammar version and parser contract. It contains the one rule that must
be evaluated, the 14 structurally inapplicable IDs, and an individual reason for
each. No caller boolean or COMPLETE assertion can grant admission.

`verify_proof(proof, tree, path, *, source_profile='closed-core-v1')` reruns
admission using the caller/session's selected profile and compares every derived
field. It never trusts the profile or grammar carried by a supplied proof to
choose the language. Even plain prose proofs from different profiles are not
interchangeable. The bridge must pass its selected profile against its current
parsed source on every pass, including candidate, independent replay, committed
snapshot and fixed-point checks. A source/path/tree/profile/grammar mismatch or
unissued proof is refused. These Python objects are not a sandbox against an
attacker already able to execute arbitrary Python in the trusted bridge process.

## Shared literal and math grammar

- One markup root containing literal Text, ASCII whitespace Space/Parbreak, and
  equations. Initial UTF-8 BOM, Unicode prose and LF/CRLF are supported. Text
  cannot hide markup/code metacharacters. One optional BOM is allowed only at
  byte zero, with no normalization or byte removal. Nonempty roots or nested
  Markup bodies cannot be opaque leaves; empty bodies are checked against their
  own zero-width span, not the length of the entire source.
- An equation has exactly Dollar, optional Space, one nonempty Math body,
  optional Space, Dollar. Its display flag must match the official typed
  `Equation::block` child-kind definition. Every child is consumed.
- Math recursively contains ordinary identifiers, ordinary math text/numbers,
  a finite explicitly listed set of operator shorthand tokens, whitespace,
  matched ASCII parenthesis/bracket/brace grouping, explicit script attachments,
  literal prime attachment, and binary slash fractions. Parser-generated Math
  parenthesized operand wrappers are checked separately from MathDelimited.
- Every explicit attachment operator must match its exact typed top/bottom
  operand. Duplicate roles, extra children, empty operands and unsupported trivia
  are refused. Nested/chained/grouped scripts, primes and fractions are supported.
- Ordinary identifier spelling is not purity evidence. A bare `read` identifier
  is not a call; every MathCall and FuncCall is refused, including `sin`/`sqrt`.
  The finite token spellings constrain grammar only, never semantic effects.

## Structured v2 extension

Only `structured-core-v1` admits the following wrappers. Every wrapper contains
exactly one recursively admitted Markup body, with exact child order and spans:

- Heading: one nonempty HeadingMarker consisting only of `=` characters,
  optional literal ASCII horizontal Space, then one Markup body.
- ListItem: exact ListMarker `-`, optional literal ASCII Space/Parbreak, then one
  Markup body. EnumItem has the same shape but requires exact EnumMarker `+`.
  Explicit numeric enumerators, such as `1.`, are excluded.
- Strong: exact leaf Star `*`, one Markup body, exact leaf Star `*`.
- Emph: exact leaf Underscore `_`, one Markup body, exact leaf Underscore `_`.

For Heading/ListItem/EnumItem, a body without separator trivia must be empty;
a body with separator trivia must be nonempty. Trailing whitespace on an empty
item is outside that wrapper in the pinned AST. List continuation separator
newlines require content indented beyond the marker's source-character column,
matching the pinned parser; Space cannot masquerade as a Parbreak. Non-ASCII
separator whitespace refuses. All nested tokens, wrappers and equations are
consumed, so an allowed wrapper cannot hide an unknown descendant. Empty inline
bodies, nested wrappers, CRLF, and Korean literal prose are supported without
widening any compiler resource repertoire.

The pinned parser treats a leading BOM as ordinary Text. Immediately following
it with `= Heading` does not produce a Heading node; the resulting Text contains
a prohibited prose metacharacter and conservatively refuses. BOM followed by
prose or a newline retains existing support.

V1 still rejects every Heading, ListItem, EnumItem, Strong and Emph wrapper.
Both versions preserve the existing equation and math grammar without change.

There are no calls, fields, strings, content blocks, closures, code escapes,
imports/includes, resources, show/set rules, references/labels, comments,
suppression directives, term lists or other AST kinds in either admitted language. Unknown
future kinds are refused, including when buried in any descendant. Empty math,
Unicode whitespace beyond the literal ASCII whitespace grammar, and other valid
but unsupported Typst are conservatively refused.

## Rule applicability

`core.parenthesized-scripts` is not declared clean by admission. The bridge must
actually evaluate it with its captured catalog, refuse skips/parse failures or
suppressed results, validate edits and reparse candidates through the pinned
adapter. Admission has no diagnostic or edit result input to relabel.

Each of the other 14 rules has an explicit structural reason stored in
`STRUCTURAL_REASONS`. Scope/drawing/overlay/reference rules have no applicable
calls or declarations. Namespace checking has no scopes and no import/include
graph extension. Project-reference argument/catalog checks have no project-ref
calls, irrespective of catalog contents. Theme rules have no set statements,
closures or theme-resolution calls. Book rules have neither shared math-book
helper use nor show/reset ownership. In particular, `core.single-book-show` is
conditional on using shared math-book helpers; ordinary standalone prose/math
does not require introducing a book owner.

Admission says nothing about compilation, general semantic equivalence, resource
safety outside this grammar, or additional rules. Protected write/compiler
handling remains a separate bridge/runtime responsibility. Parenthesis insertion
must separately preserve wrapper/equation ownership and attachment roles; a
structural grammar proof alone does not establish that preservation.
