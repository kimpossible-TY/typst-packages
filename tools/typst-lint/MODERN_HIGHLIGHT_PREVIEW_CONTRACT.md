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

## Independent literal footnote plans

`--preview-conditional-modern-literal-footnotes` additionally requires the
combined preview option. It returns separate plans, each against the original
captured source; neither these plans nor edits from other reports may be merged
without independent union verification. Only one direct root builtin
`#footnote[literal content]` in a root highlighted call is considered. Its
issued captured binding, source hash, exact source bytes, subtree, owner and
single occurrence are rechecked after the candidate is reparsed. Nested
equations within the preserved footnote and equations moved outside the
highlight have separate ownership maps. Existing active and suppressed script
and footnote diagnostics are reanalyzed, and changed suppression targets or
other overlapping diagnostics withhold that plan.

Dynamic code, unproved bindings, other call forms and unsupported content
withhold the affected plan. Report item/byte and per-batch work limits apply.
Successful reports preserve the existing combined plan; global report-budget
exhaustion fails the whole inspection. A single retained call in source does
not prove how often it executes. Runtime binding, side effects, custom show
rules, reference resolution, footnote numbering, rendered output, source
writes and compilation are not proved or authorized.

## One reviewed callback math location

`--preview-conditional-modern-scoped-math PATH START` requires the combined
preview flag. `PATH` is the exact project-relative path and `START` is the
`start` byte offset from the same issued modern highlight diagnostic. It
independently replays issued modern evidence, verifies the
exact captured scoped-annotations package and callback provenance route, and
accepts only one root wrapper with a direct literal `s` callback. Its entire
body must contain no nested closures, local bindings, imports/includes,
assignments, conditionals, loops, context or show rules. Only captured reviewed
builtin math calls with no executable equation code can be proposed; source
syntax, bindings and other rule findings are rechecked on the candidate.

The resulting plan uses the original source and is not a union with the
combined or literal-footnote plan. Do not merge plans without independent union
verification. A scoped reference and a math call inside the same highlight
remain unsupported. Actual callback execution, binding, side effects, styles,
rendering, source edits and compilation are not proved. A bounded single
location and the whole inspection item and byte limits apply.

## Per-source scoped/math union

`--preview-conditional-modern-scoped-union PATH START` requires the combined
preview flag and can be repeated for up to four issued diagnostic locations
in at most two sources. Each `PATH` and `START` must come from the same issued
modern highlight diagnostic. For each source, the tool independently replays
the source evidence and existing combined plan, requires a single exact issued
callback for all included edits, and checks that each replacement matches its
separately proved plan. It reparses the entire union and rechecks ownership,
the ordered scoped references, callback math binding, syntax, suppressions and
other rule findings. Scope work is bounded and shared across the batch. If a
union is unsupported or exhausts that budget, it is withheld while the
original combined report is retained; aggregate report limit errors fail the
inspection.

Every union is an independent conditional source-only plan for captured bytes.
It does not replace the original combined report, prove current filesystem
freshness, authorize editing or compilation, or establish execution, label
resolution, side effects, styles or rendering. Do not merge it with another
source's union, footnote plans, or other reports without revalidation. Math and
a scoped reference in the same highlight remain unsupported.

## Parent scope transfer and reference preview

`--inspect-scoped-parent-transfer PATH START` requires modern highlight
evidence and reports one selected callback's conditional parent/child source
identity proof. The exact scoped-annotations and mannot namespace artifacts,
facade routes, root wrapper and lexical ownership are replayed. Parent scope
`s` and child scope `m` uses are checked throughout every admitted child
body; helper values may only be used in exact `tag` or `ref` direct calls with
one literal string. Unsupported bindings, dynamic code, escapes and work
exhaustion withhold the proof. `PATH` and `START` must match the same issued
modern highlight diagnostic.

A narrowly recognized `context` may declare `theme` from the reviewed,
captured `theme-from-text-fill()` route and non-shadowing local colors from
`theme.callouts.proposition.border` or `theme.callouts.important.border`.
The entire context body remains visible to the scope scanner. The selector
route and bindings are replayed on the candidate; other contextual declarations
remain unsupported. A direct child-scope local function with the exact reviewed
literal-tag `markub` call shape can capture `m` if its uses are direct math
calls in that same child after the declaration. Its full body remains scanned
and the ownership is replayed on the candidate. The `markub` binding and effects
are not proved. Other captured local functions remain unsupported.

`--preview-conditional-modern-parent-reference PATH START` instead requires
the combined preview and offers one independent source-only proposal. It
reparses the candidate and replays all parent/child identities and routes on a
derived graph whose selected source has changed and whose import bytes are
unchanged. It also rechecks syntax, suppressions and other rule findings. The
proof and preview options are mutually exclusive. The plan is against captured
bytes; it is not a union with other plans. Do not merge it with combined,
scoped/math union, footnote or other plans without separate verification.
Current filesystem freshness, runtime binding or execution, side effects,
reference targets, rendered output, writes and compilation remain unproved.
The full report item/byte limit and independent bounded proof work apply.

## Multi-site parent reference union

`--preview-conditional-modern-parent-union PATH START` is repeatable for two
to four distinct issued locations from one source and reviewed callback. It
independently reissues each original-source single plan and verifies the full
union against the same capture, including final candidate scope routes,
ownership, existing diagnostics and suppressions. Failed union verification
returns completed individual plans; a report byte or item limit failure rejects
the whole inspection. The combined, footnote and independent parent reports
remain separate. No cross-report composition, current filesystem freshness,
execution, rendering, write or compiler authority is established.
