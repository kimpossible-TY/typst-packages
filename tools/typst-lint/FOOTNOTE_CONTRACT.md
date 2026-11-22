# Footnote rule integration contract

`footnote_rules.py` implements five **partial** footnote backends. The PDE
source dispatcher supplies `resolution.spacer_proof`, produced by
`static_resolver.resolve` from real AST facts and explicitly loaded, reviewed
package bytes. This does not authorize user-file writes or compilation. The
common candidate planner still requires reparsing, reanalysis, suppression
ownership, conflict, retained-coverage and convergence checks.

`analyze(tree, path, resolution, spacer_proof)` returns the existing
rule diagnostic/edit/skip/suppressed shape. Malformed syntax, stale resolution,
or a stale or invalid proof yields no edits. Omitting a proof supplies empty
spacer evidence, not a spelling-based fallback.

## Reviewed helper and dependency boundary

The exact reviewed artifact is `local/math-blocks/0.2.0/lib.typ`, SHA-256
`542c7ce1ebfc6fbd51a7fefb2b55405a968a951b61d49170e49ef331948f36ff`.
It locally defines the immutable content value:

```typst
#let dots_space = {
  $& wide dots.h.c thin$
}
```

The value contains only built-in math alignment, spacing and dots. The module's
three named dependency imports (`capitalize-title`, `is-numeric-title`,
`project-ref`) cannot shadow those names; none is used in this definition.
No other top-level definition or mutation replaces `dots_space`. This review
therefore certifies **only this spacer export**. The same artifact also has
three independent theme contracts and a separate [proof contract](PROOF_CONTRACT.md).
It does not certify dependent package execution, other math-blocks exports,
math-book facades, wildcard completeness or a whole document graph. Runtime
compilation and dependency availability remain separate, unproven gates.

The explicit package-path loader verifies the manifest and immutable source
snapshot with retained descriptors and exact hashing. No cache, installed
package, network, dependency traversal or evaluator is consulted. Other
versions, changed bytes and same-spelled local helpers never inherit this
contract. A wildcard import of this partial namespace invalidates all earlier
bindings and builtins before establishing the independently reviewed exports. In
particular it does not prove the remaining `footnote` builtin.

## Resolver-produced binding evidence

`SpacerProof` is defined in `static_resolver` and re-exported by `footnote_rules`
for compatibility. `Resolution.spacer_proof` is source-bound and remains a
trusted producer contract, not a complete evaluator or proof about future files.

- `source_sha256` hashes the exact source bytes associated with AST node IDs.
- `existing` includes nondeferred identifier, module-field and parenthesized
  expressions whose value resolves to the exact reviewed helper. Direct and
  renamed imports, immutable local aliases and reviewed module aliases use the
  resolver's lexical scope and source-order facts. A function call's bound
  callee is never treated as the returned spacer value.
- `available` includes proven builtin footnote calls where the literal
  `dots_space` name resolves to that helper **before evaluating the call's
  arguments**. A helper imported only under another name can prove its existing
  use, but cannot authorize insertion of `#dots_space` until that canonical name
  is independently bound to the proven value.
- Local shadows, direct/compound/destructuring/receiver mutations, unknown
  imports and possible conditional mutations invalidate the affected bindings.
  An earlier immutable alias retains its own value. Unknown wildcards cannot
  preserve old builtin or helper evidence. Deferred closures/context expressions
  never produce spacer proof, including through the theme-only module-capture
  exception. Unknown output or styles cannot certify insertion.

The consumer still owns supported source context. A proof for a nested lexical
value does not make its body eligible for edits. Only direct root prose and
root display calls are inspected; nested bodies, facade exports and deferred
execution remain partial. Unresolved preceding expressions, including aliases,
facade fields or calls, suppress related findings rather than guess whether
there is a spacer. Exact provenance is not inferred from a matching spelling,
path, copied formula or synthetic test proof.

## Implemented subset and remaining work

| Rule | Supported subset | Deferred |
| --- | --- | --- |
| Punctuation | Direct root prose, proven built-in literal-form call. Remove horizontal space after adjacent punctuation or move one trailing comma/period after literal prose across a literal footnote body. | Nested bodies, ambiguous punctuation, comments/newlines, calls/side effects and remaining multi-rule composition. |
| Trailing gap | Diagnose source-line calls followed by blank lines and more content; inside a root display remove surplus empty lines. For a proven literal root footnote followed by an existing reviewed marker, insert only the canonical next-line builtin break. | Missing-marker boundaries use the marker rule's complete break-plus-marker repair. Unknown shows/bindings, nonliteral ownership, comments and broader contexts refuse. EOF and an already existing break are preserved. |
| Aligned spacer | In root displays, inspect syntax `MathAlignPoint` or `Linebreak` outside nested calls/bodies; diagnose missing spacer, insert only with canonical `available` proof. | Nested equations, richer math contexts, absent helper or unknown context. |
| Unaligned spacer | Remove an immediately preceding `existing`-proven helper expression and its separator in a root display. | Unresolved helpers/facades and non-direct equation calls. |
| Prose spacer | Remove a proven helper, including preceding horizontal gap only when necessary to restore already-existing punctuation adjacency. | Ambiguous punctuation; do not leave an adjacency violation after a spacer-only edit. |

Inline math is not a display, and physical source wrapping is not alignment.
Outer alignment is not inferred from tokens nested inside footnote bodies,
comments, strings or code. Original formula, footnote content, comments and
Unicode bytes are preserved except the rule-owned helper/separator spans.
Absence of a finding is not complete coverage.

`tests/test_spacer_provenance.py` tests the actual producer with exact on-disk
package snapshots: direct imports, renamed/module/parenthesized aliases,
canonical availability, immutable alias retention, scopes, mutations,
wildcards, stale snapshots, false bindings, deferred/nested contexts, suppression
and second-run zero edits. `tests/test_footnote_rules.py` additionally retains
synthetic proofs solely to exercise the consumer contract. Isolated render
fixtures verify only their explicit before/after placement effects; they do not
certify graph coverage or open the production write/compiler gate.

The bounded prose composition contract is documented in
[FOOTNOTE_COMPOSITION_CONTRACT.md](FOOTNOTE_COMPOSITION_CONTRACT.md). It keeps
rule ownership, genuine followup capitalization, source-bound builtin-break
availability and independent candidate authentication distinct. It never
deduplicates existing breaks or guesses transparency under unknown shows.

Remaining work includes nested/facade resolution, broader prose/paragraph
ownership, remaining punctuation interactions and complete supported
context/render coverage. All five backends remain partial.
