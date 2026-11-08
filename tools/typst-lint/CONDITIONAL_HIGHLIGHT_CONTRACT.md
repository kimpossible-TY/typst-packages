# Conditional PDE namespace inspection

```sh
typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --package-path /absolute/captured-packages --format json
```

This explicit read-only route adds `pde.text-only-highlight` hints for a
source call whose literal content body contains typed math. It follows the
helper name through captured relative modules, named/renamed imports, wildcard
imports and immutable aliases. The conditional grammar also accepts the exact
combined `import literal as module: names` or wildcard form. The module alias is
bound first and imported names then override collisions, matching native Typst;
the trusted import-graph grammar and resolver remain unchanged.

Root calls and calls in the direct literal content bodies of captured
`math-blocks.proof`, `theorem` and `note` are inspected. Each admitted body has
its own ordered lexical namespace: local declarations and imports stay local,
aliases preserve their origin, and active assignments/default mutations can
invalidate outer names. Wrapper aliases are traced; shadowed or unknown wrappers
do not establish this context. Only one positional literal body is admitted.
Proof accepts no named options; theorem/note accept at most one `title` option.
Unknown, duplicate or named-body options are refused. Other callbacks, deferred functions, code
or unknown scopes retain explicit unresolved execution context. Math in options,
raw strings or a nonliteral body does not establish this finding.

The result is always `CONDITIONAL`, with `edit_permission=false`. It is separate
from the existing source diagnostics, candidate hashes and script proposals.
The report still has three source backends, 30 uninspected PDE rules and
`global_complete=false`. The highlight rule remains uninspected for full rule
acceptance. No compiler or writer runs and no consumer configuration is read.

The Python analyzer and one-shot `analyze` retain highlight-only defaults.
Explicit `rule_ids=(RULE_ID, MARKER_GAP)` selects both supported rules in the same
capture session. `result.reasons_for(rule_id)` separates each rule's refusals;
the old `unresolved` field remains their combined record. The document CLI selects
both under explicit PDE/package-path inspection and counts each serialized
coverage reason/error and context route against the report budget.
`scoped_callbacks=False` preserves the previous Python scope default. The
explicit document namespace route selects `True` for the bounded callback
extension described below.

## Retained namespace facts

An explicitly selected canonical package root is captured with retained
no-follow file/directory snapshots. The six contracts pin manifest and
entrypoint bytes, package metadata and complete exported name sets:

- `@local/text-utils:0.1.2`
- `@local/math-blocks:0.2.0`
- `@local/scoped-annotations:0.3.0`
- `@local/cetz-helpers:0.2.0`
- `@preview/mannot:0.4.0`
- `@preview/cetz:0.4.2`

Only `text-utils.highlighted` has a recognized highlight-helper origin. The three
selected math-blocks wrapper names carry conditional content-context routes;
the marker rule recognizes the two exact constants described below. The two
scoped callback exports are recognized only by the explicit option below;
remaining exported values stay opaque. A dependent wildcard namespace requires its
captured
namespace prerequisite: scoped-annotations requires mannot, and cetz-helpers
requires CeTZ. Missing or mismatched packages retain explicit unavailable
records; unsafe paths, aliases or capture races refuse the input. Root spelling
is bounded to 4,096 UTF-8 bytes and 128 components before filesystem access.
At most six packages, 12 files and 8 MiB are captured. There is no ambient
package/cache lookup, download, evaluation or package installation.

These are successful-import namespace contracts. They do not certify every
transitive dependency or initializer. A named import from an otherwise unknown
package introduces only its explicitly named opaque bindings, conditional on
successful loading. Ordinary output and unrelated helper calls do not erase
lexical names; active assignments, unknown wildcard imports, dynamic imports,
unresolved modules and unknown namespace scopes withhold affected hints.
Function parameter defaults are checked for immediate mutation or namespace
injection while their deferred bodies supply no root binding facts.

Each hint records the root hash, relative source hashes, pinned package
evidence, package requests, ordered name route and these outstanding conditions:

1. Every referenced module loads and initializes successfully; initializer
   purity has not been proved.
2. Runtime imports select the exact captured package versions and bytes;
   ambient package selection has not been proved.
3. The call actually executes and renders its literal body in the intended
   document context.

Nested hints also retain each wrapper's source-bound name route in
`content_context` and require every enclosing admitted wrapper to execute and
render that body. This is conditional evidence, not initializer purity or a
rendering certificate. Inline suppressions remain anchored at the nested helper
call, independently of the wrapper opener.

Suppression is anchored at the helper call and exported separately from source
rule suppression. Captured source/graph mismatches, stale package facts,
malformed syntax or prior parser/resource refusals cannot establish a hint.
Retained dependency trees are parsed once per session, including parse failures.
Successful namespace facts are reused only within that immutable capture
session. Each carries its complete source dependency closure and maximum import
height; reuse checks the caller's entire active ancestry and depth limit.
Fixed-capture syntax, scope, mutation and import/export availability refusals
may also be reused as refusals. Cycles, depth failures and exhausted budgets
never enter that cache, so a failure under one ancestry cannot poison a later
valid ancestry or become a successful namespace fact.
The namespace walk has explicit session bounds of 512 module visits, 2,000,000
charged syntax/scope work units, 128 name-route entries and 1,024 lexical bindings. An
exhausted session withholds the current result and refuses later analyses;
it never reports complete coverage or grants repair authority.
Admitted content bodies are excluded from their enclosing mutation/descendant
walk and scanned once in their own scope. Scope-copy work is charged separately,
and the public report item limit includes all nested context routes.

## Paragraph-marker source gap

The exact text-utils bytes define `paragraph-tab` and `paragraph_tab` as the same
immutable U+F000 marker value. This rule additionally traces both exports and
immutable aliases, without trusting a matching source spelling. It requires
adjacent same-Markup Hash/Ident nodes and a marker occupying its entire physical
source line. Wrapper delimiters and same-line comments cannot be discarded to
make the marker standalone.

The immediately previous complete physical source line must be whitespace-only
and end in LF or CRLF. File/body openings and a BOM are not blank lines. A
previous heading, display, call or comment may therefore produce a gap hint.
The following substantive sibling is checked separately: a heading/display
withholds the gap for marker-target precedence, including across comments.
Bare-CR boundaries and unknown/deferred contexts remain unresolved.

Marker hints use their Hash span as the suppression/diagnostic anchor and add
conditions that the marker is emitted in the intended paragraph context and
the captured paragraph-tab transformation is active. They grant no edits,
capitalization proof or paragraph/rendering certificate. The marker rule stays
among the 30 uninspected PDE obligations.

The actual snapshot has 549 typed export spellings (404 underscore and 145
hyphen), including 186 at root. Of 21 source-gap candidates, eight root calls
are reported and 13 callback-contained candidates remain deferred. Per-rule
gap coverage records 31 common namespace barriers and 307 deferred typed
expressions; these 338 reasons are not 338 missing blank lines. The existing
29 highlight records and every source diagnostic/script proposal are unchanged.
See `audit/conditional-marker-gap-evidence.json`.

One owned genuine-facade heading/marker/prose fixture reaches one-to-zero hints
after one manual newline insertion. Two native compilations preserve word
order and the observed 18pt marker spacing; its word boxes also happen to match.
This is a snapshot observation, not a general geometry/PDF guarantee or public
repair output.

## Captured scoped callbacks

Only the exact pinned `scoped-annotations:0.3.0` exports `local-tag-scope` and
`local-scope-annotations`, including immutable aliases, admit callbacks. The
call must supply one direct closure with a single simple positional identifier
and a literal ContentBlock return, optionally parenthesized. Options may be
omitted or use literal `auto` for the wrapper's declared prefix/parent/name
options. Computed options, namespace options, spread/destructuring/default or
multiple required parameters, code bodies and the compatibility `mannot-scope`
remain unresolved.

Both wrappers pass one dictionary. The analysis copies the namespace at closure
creation, then shadows its parameter with an opaque local value. Callback-local
declarations and mutations cannot alter the caller's captured bindings. Parameter
defaults are checked for immediate outer effects before admission; deferred
inner function bodies do not supply immediate effects. These rules agree with
the [pinned closure evaluator](https://github.com/typst/typst/blob/v0.15.1/crates/typst-eval/src/call.rs)
and [capture bindings](https://github.com/typst/typst/blob/v0.15.1/crates/typst-library/src/foundations/scope.rs),
and the owned native probe observes capture-by-value and parameter shadowing.

This is not a general control-flow evaluator. An active return, including one
inside an ordinary literal wrapper or code block, abandons that scoped
callback's earlier/suppressed hints and prevents later imports from restarting
its scan. It stops at the nearest callback; an inner callback return does not
invalidate its caller. Existing block/Contextual barriers remain conservative.
Return detection shares the existing effects pass and does not raise budgets.

`content_context` retains each wrapper route once. Additional `callback_context`
records identify its route index, call/body spans and parameter names under the
finding's root source hash. Conditions require the exact wrapper to invoke the
callback with its documented dictionary and render its literal result. They do
not certify invocation, dictionary values, initialization or rendering.

Independent actual inspection reports 73 highlight and 19 marker-gap hints,
adding 44 and 11 respectively. All previous 29/8 records remain identical, as
do source findings, script proposals and all 44 retained source hashes. Remaining
math candidates in chapter 3.5 cross existing block/Contextual refusals; the
extension does not broaden those boundaries to meet a count. The actual 37
source trees contain no active return, so the final return guard preserves this
report. See `audit/conditional-callback-evidence.json`.

One owned nested genuine-wrapper fixture observes both dictionary arguments and
context routes, then reaches one-to-zero hints for each selected rule with a
manual candidate. Equation/label bytes, equation order, label target and word
order are preserved. Paragraph and highlighting geometry may change; no generic
PDF-equivalence or public repair claim follows from this characterization.

## Local characterization

The initial namespace checkpoint (`baf1ae6`) retains 5,044 script findings, seven
standalone-display findings, 63 continuation-gap findings and 9,686 script
insertions. Opt-in namespace inspection additionally reports 11 conditional
highlight findings across five sources. Reusing common facade facts and fixed
refusals reduces charged namespace syntax work from 2,034,597 to 1,176,399;
the final five sources no longer hit the existing 2,000,000-node work limit.
Dynamic imports, nested/deferred calls and unknown root scopes still retain
their specific unresolved reasons. This does not establish full rule coverage.

The subsequent combined-import and direct-content extension reports 29
conditional findings: the original 11, two newly resolved root calls and 16
calls in literal proof/theorem/note bodies. All 37 included sources and 44
retained include/import source hashes remain identical. There are 93 explicit
unresolved contexts: 31 include/context sites and 62 deferred/nested calls. More
calls become observable after fixing import syntax; these newly visible
refusals are retained rather than hidden. No import-grammar or work-budget
refusals remain, and the source findings/script proposals are unchanged. See
`audit/conditional-content-evidence.json` for this later checkpoint.

The genuine PDE facade exports 75 names. A private native query confirmed its
`highlighted`, paragraph marker/show and scoped helper identities against the
captured packages. This observes the selected runtime only; it does not replace
the conditions above. A separate owned fixture moved math outside highlighted
content and reached zero conditional findings. Two compilations and four native
queries preserved the original equation/label bytes, equation contents/order,
sentinel label target and extracted word order. This is not a claim that the
geometry or PDF bytes remain identical, and the fixture was specified manually
rather than generated by a public repair API.
`audit/conditional-highlight-evidence.json` records the
source inspection and native characterization without redistributing package
cache or font bytes. User documents and installed packages remain unchanged.
