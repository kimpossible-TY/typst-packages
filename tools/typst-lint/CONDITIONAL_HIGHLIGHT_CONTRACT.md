# Conditional PDE highlight inspection

```sh
typst-lint inspect-document --root /absolute/project --entrypoint main.typ --rule-set pde --package-path /absolute/captured-packages --format json
```

This explicit read-only route adds `pde.text-only-highlight` hints for a
root-level call whose literal content body contains typed math. It follows the
helper name through captured relative modules, named/renamed imports, wildcard
imports and immutable aliases. Calls inside callbacks, deferred functions or
unknown scopes retain explicit unresolved execution context. Math in options,
raw strings or a nonliteral body does not establish this finding.

The result is always `CONDITIONAL`, with `edit_permission=false`. It is separate
from the existing source diagnostics, candidate hashes and script proposals.
The report still has three source backends, 30 uninspected PDE rules and
`global_complete=false`. The highlight rule remains uninspected for full rule
acceptance. No compiler or writer runs and no consumer configuration is read.

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

Only `text-utils.highlighted` has a recognized helper origin. All other exported
values remain opaque. A dependent wildcard namespace requires its captured
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
charged syntax nodes, 128 name-route entries and 1,024 lexical bindings. An
exhausted session withholds the current result and refuses later analyses;
it never reports complete coverage or grants repair authority.

## Local characterization

The actual 37-source PDE entrypoint retains 5,044 script findings, seven
standalone-display findings, 63 continuation-gap findings and 9,686 script
insertions. Opt-in namespace inspection additionally reports 11 conditional
highlight findings across five sources. Reusing common facade facts and fixed
refusals reduces charged namespace syntax work from 2,034,597 to 1,176,399;
the final five sources no longer hit the existing 2,000,000-node work limit.
Dynamic imports, nested/deferred calls and unknown root scopes still retain
their specific unresolved reasons. This does not establish full rule coverage.

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
