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
smart-quote tokens, simple direct `@label` references, and inline or display equations can be proposed. The latter requires exactly
empty adjacent parentheses; whitespace or comments inside them are not removed.
Reference supplements and embedded calls are not selected by default; reference syntax and
its highlight ownership are rechecked after parsing. User-defined reference show
rules and runtime reference resolution are not proved and may alter rendering.
This reference extension applies only to the source-only inspection option;
compiler preview selection is unchanged.
Math containing function calls, code, labels,
comments or imports and bodies with nonliteral/nested content are skipped.
Each equation is copied unchanged outside highlighting; nonempty prose segments
are individually highlighted and whitespace bytes are preserved. Each file's
edits are independent of other preview plans. Reparse checks equation and
generated prose ownership, untouched syntax, suppression scopes and supported
cross-rule diagnostics. Existing script diagnostics inside an unchanged moved
equation are mapped to their new offsets and rechecked; ambiguous equation
ownership or changed suppression targets are withheld. When another existing
finding falls inside a proposed edit,
the tool can check edits individually, exclude failed edits, and revalidate the
combined subset against the original file, including excluded findings and
suppression targets. Any other mismatch or failed combined check withholds the
entire file. To bound work, at most 32 individual fallback checks are made per
batch; exceeding that budget withholds the source with an explicit reason.
The summary's unpreviewed count includes excluded findings, and the batch is
`INCOMPLETE` when findings remain unpreviewed.

An additional explicit `--preview-conditional-modern-scoped-references` option
requires `--preview-conditional-modern-highlights` and its modern evidence
options. It accepts exactly `#(parameter.ref)("literal")` in a source-proven
callback from the exact reviewed `@local/scoped-annotations:0.3.0` wrapper.
The selected parameter must be used only in reviewed direct field access within
the callback; shadowing, assignments, imports/includes, dictionary escapes,
unknown fields and dynamic arguments are withheld. Nested recognized callbacks
use their own innermost parameter; an unrecognized callback is not admitted.
Each source's diagnostic is independently reissued once and checked against an
immutable capture; altered findings do not authorize a proposal. After splitting,
the tool reparses and checks the ordered calls and their highlighted ownership,
as well as the ordinary suppression and cross-rule checks described above.
A batch-wide budget of 2,000,000 charged AST work units covers diagnostic
replay, scope traversal and candidate rechecking. On exhaustion, the scoped
portion is explicitly withheld, the batch is incomplete, and the original
non-scoped proposals for that source are independently recomputed and
revalidated. A separate fallback budget allows at most 32 more subset checks
per batch, in addition to the 32 ordinary scoped-profile subset checks; it does
not suppress existing refusals, conflicts, or suppression directives. The successful reference route remains conditional on the
captured package's runtime selection, successful callback execution, unchanged
scope value and label resolution. User `show ref` rules and layout may change
rendering. This is source-only: it proves neither execution nor PDF equivalence,
and grants no source-write, compilation or publication authority. The existing
composed compiler profiles do not select this option.

The JSON section contains exact byte offsets and replacement text, which can
include document content. The batch has a shared item and serialized-byte limit
including its companion modern diagnostic evidence. Nothing is applied or
compiled by this inspection. Highlight removal deliberately changes emphasis
and may alter smart-quote direction, spacing, line breaking and pagination; a Korean owned Typst
fixture produced different PDF bytes, which is not a whole-document claim.

`--preview-conditional-modern-math-highlights` is a separate, explicit
source-only path requiring the same modern evidence. It additionally accepts
math calls only when every nested call in every equation is one of the
reviewed Typst 0.15.1 standard math names `cal`, `dot`, `frac`, `frak`,
`overline`, `tilde`, `op`, `bb`, and `hat`, or the exact unshadowed
`dot.double` symbol variant, with no embedded code, ordinary function call,
label, import, or comment. Other fields and variants remain unsupported. Local shadowing and mutations are
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

The explicitly selected `--preview-conditional-modern-combined-highlights`
instead builds one source-only plan using direct references, proven scoped
references and reviewed math calls together. It requires the same modern
evidence and cannot be combined with the separate modern or math preview
flags. All selected edits for each source are reparsed and revalidated as a
union with the existing syntax, directive, finding and scope checks. The
existing bounded scoped-proof fallback rechecks eligible non-scoped candidates
when scoped evidence exhausts its work budget; the scoped portion remains
explicitly withheld and the batch incomplete. The combined unpreviewed count
does not individually classify every unsupported candidate or math binding.
Math calls inside callbacks or other dynamic/deferred ancestors remain outside
the reviewed math proof, including a math call and a scoped reference in the
same callback body. The existing aggregate item and byte limits apply to the
whole inspection; if exceeded the inspection fails rather than returning a
partial report. Preservation of existing report sections applies to successful
inspections. Runtime binding, label resolution, execution, rendering,
source edits, publication and compiler permission remain unproved.
