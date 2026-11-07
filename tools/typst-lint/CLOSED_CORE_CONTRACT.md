# Closed core applicability contract

`closed_core.certify(tree, path)` derives applicability for exactly the 15 core
catalog rule IDs on a deliberately restricted, recursively closed source grammar.
This is an internal building block, not a complete-coverage claim for general
Typst, the PDE pack, or the production CLI. The global 33-rule audit stays PARTIAL.

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
SHA-256, grammar version and parser contract. It contains the one rule that must
be evaluated, the 14 structurally inapplicable IDs, and an individual reason for
each. No caller boolean or COMPLETE assertion can grant admission.

`verify_proof(proof, tree, path)` reruns admission and compares every derived
field. The bridge must invoke it against its current parsed source on every pass,
including candidate and fixed-point passes. A source/path/tree mismatch or
unissued proof is refused. These Python objects are not a sandbox against an
attacker already able to execute arbitrary Python in the trusted bridge process.

## Accepted grammar

- One markup root containing literal Text, ASCII whitespace Space/Parbreak, and
  equations. Initial UTF-8 BOM, Unicode prose and LF/CRLF are supported. Text
  cannot hide markup/code metacharacters. Nonempty roots cannot be opaque leaves.
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

There are no calls, fields, strings, content blocks, closures, code escapes,
imports/includes, resources, show/set rules, references/labels, comments,
suppression directives or other AST kinds in the admitted language. Unknown
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
handling remains a separate bridge/runtime responsibility.
