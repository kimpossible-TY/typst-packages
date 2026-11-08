# Conditional modern highlight split preview

`inspect-document --preview-conditional-modern-highlights` requires explicit
`--rule-set pde --package-path ... --footnote-cache-path ...` and
`--modern-highlight-evidence`. It uses the same issued source graph and
first-selected exact modern text-utils artifact as the conditional diagnostic.
Missing or different artifact bytes produce an unavailable section. Runtime
selection, initialization, execution, global coverage and source-write or
compiler permission remain unproved.

Only an unsuppressed proven source route to a direct no-option literal
`#highlighted[...]` or `#highlighted()[...]` with ordinary text, whitespace,
smart-quote tokens, and inline or display equations can be proposed. The latter requires exactly
empty adjacent parentheses; whitespace or comments inside them are not removed.
Math containing function calls, code, labels,
comments or imports and bodies with nonliteral/nested content are skipped.
Each equation is copied unchanged outside highlighting; nonempty prose segments
are individually highlighted and whitespace bytes are preserved. Each file's
edits are independent of other preview plans. Reparse checks equation and
generated prose ownership, untouched syntax, suppression scopes and supported
cross-rule diagnostics. When an existing finding falls inside a proposed edit,
the tool can check edits individually, exclude failed edits, and revalidate the
combined subset against the original file, including excluded findings and
suppression targets. Any other mismatch or failed combined check withholds the
entire file. To bound work, at most 32 individual fallback checks are made per
batch; exceeding that budget withholds the source with an explicit reason.
The summary's unpreviewed count includes excluded findings, and the batch is
`INCOMPLETE` when findings remain unpreviewed.

The JSON section contains exact byte offsets and replacement text, which can
include document content. The batch has a shared item and serialized-byte limit
including its companion modern diagnostic evidence. Nothing is applied or
compiled by this inspection. Highlight removal deliberately changes emphasis
and may alter smart-quote direction, spacing, line breaking and pagination; a Korean owned Typst
fixture produced different PDF bytes, which is not a whole-document claim.

`--preview-conditional-modern-math-highlights` is a separate, explicit
source-only path requiring the same modern evidence. It additionally accepts
math calls only when every nested call in every equation is one of the
reviewed Typst 0.15.1 standard elements `cal`, `dot`, `frac`, `frak`,
`overline`, `tilde`, `op`, `bb`, and `hat`, with no embedded code, ordinary
function call, label, import, or comment. Local shadowing and mutations are
tracked; wildcard imports preserve these bindings only when the captured,
complete namespace excludes the relevant name. Unknown imports, unresolved
bindings and delayed or dynamic ancestors are withheld. Upstream unresolved
and suppressed results are preserved in the accompanying math evidence.
Exact equation structure and ownership are checked after parsing the candidate,
and its math bindings are analyzed again. Existing script diagnostics inside an
unchanged moved equation are mapped to its new byte offset and compared again;
ambiguous ownership or changed suppression targets are withheld. Other
overlapping diagnostics remain withheld. This is conditional on the runtime
using the reviewed builtins and captured namespace; it does not prove runtime
binding, execution, visual equivalence, or grant permission to edit or compile.
A small owned nested-`cal(frac(...))` example compiled before and after, with
different 72 dpi renderings; that is not a whole-document comparison.

Both source-preview flags can be requested together. They are separate plans
against the original captured source; do not concatenate their edits without
independent combined revalidation. Existing compilation profiles do not select
this math-call path.
