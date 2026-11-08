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
cross-rule diagnostics. A mismatch withholds the entire file.

The JSON section contains exact byte offsets and replacement text, which can
include document content. The batch has a shared item and serialized-byte limit
including its companion modern diagnostic evidence. Nothing is applied or
compiled by this inspection. Highlight removal deliberately changes emphasis
and may alter smart-quote direction, spacing, line breaking and pagination; a Korean owned Typst
fixture produced different PDF bytes, which is not a whole-document claim.
