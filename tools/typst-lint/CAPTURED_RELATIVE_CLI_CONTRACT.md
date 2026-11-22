# Explicit captured-relative graph CLI

The exact offline selection `captured-relative-core-v1` exposes the separate
captured-relative root repair and graph compile/publication composition. It does
not enable ordinary fix/compile routes, broaden either single-source grammar,
complete any global rule, install a wrapper, or modify a consumer build route.
The global audit remains **33 PARTIAL / 0 COMPLETE**.

## Supported input and explicit commands

Use an explicit `.typst-lint.toml` in the project root:

```toml
rule-set = "core"
include = ["main.typ"]
```

A small admitted `main.typ` is:

```typst
#import "values.typ": count, label
= Example
#label $x_count$
```

Its `values.typ` contains only inert declarations:

```typst
#let count = 7
#let label = "captured"
```

After stopping all source writers for the entire operation, the explicit repair
command is:

```sh
./typst-lint fix --root ROOT --profile captured-relative-core-v1 --offline-exclusive --target main.typ --format json
```

To repair, compile and publish, also stop all output writers, including exporters,
watchers and writers to output ancestors/journal storage, and create the intended
output parent directory beforehand:

```sh
./typst-lint compile --root ROOT --profile captured-relative-core-v1 --resource-profile embedded-ascii-v1 --offline-exclusive --offline-output-exclusive --target main.typ --output build/main.pdf --format json
```

These assertions are independent; neither Git state nor a cooperating root lock
proves them. Unknown selectors, aliases, duplicate/mixed flags, incompatible
commands, compiler tails, custom executables, limits, deadlines and fault options
are refused. Graph target spelling is checked by the pure canonical graph path
validator before root access or lease creation. Existing single-source target
and argument behavior remains unchanged. The public compile deadline remains
10 seconds; language and size admission do not guarantee completion within it.

The root admits structured literal prose/math, headings, bullet/automatic lists,
strong/emphasis and an initial prefix of explicit named minimal relative imports.
The full bounded dependency graph contains only supported inert scalar
initializers and immutable alias/rename forwarding. Rendered imported values must
be admitted integers or strings. All unused modules and declarations are checked.
Packages, helper/function calls, includes, dynamic/wildcard/namespace imports,
resources, code with effects, comments/directives and unknown syntax remain
unsupported. Necessary leading relative parent steps are allowed only within the
captured project; nonminimal spellings and root escape are refused.

Only the root's independently proved script parentheses can change. Dependencies
remain read-only, with exact bytes, identities, metadata and live ancestry
retained. The same core-only config restrictions and 15 accounted obligations
apply; this is not permission to run or fix arbitrary Typst projects.

## Distinct selection and lifetime

Public offline selection has its own exact set. `KNOWN_SOURCE_PROFILES`,
`GRAMMAR_VERSIONS`, `validate_source_profile()` and
`grammar_for_source_profile()` remain restricted to the two single-source
profiles. The graph selector is never passed to `ProtectedCoreFix`, the old core
certifier, single-source compiler or `PreparedPdfPublication`. Existing profiles
still refuse imports, without silently choosing another language.

Source-only fix enters the graph bridge under the existing fixed sibling source
store and source lease, validates, then applies. It does not inspect PDF history
or grant output authority. Graph compile enters the graph bridge first, then the
prepared graph publisher before any validation/application attempt. Source entry
checks source journals; output entry inventories and durably prepares the output
under the same borrowed transaction lock. No preliminary output scan or second
lock is introduced. Source entry failure does not claim output history was read.

After actual apply, the CLI retains the successful source summary before any
later graph guard. Only that bridge's genuine committed graph enters its prepared
publisher's one compile call. Source, dependency, configuration, catalog,
compiler, selected fonts, output and journal guards remain active. Valid source
repairs survive subsequent compile/publication failure. Output-only rollback
preserves later edits; durable publication is final.

## Honest source and compiler observations

Graph source reports identify the selected source profile, distinct root/module
grammar versions and parser contract, original/final root hashes, actual modified
paths, insertion/rule evidence, source journal, and bounded source/edge records.
They distinguish the writable committed root from unchanged read-only modules.
A genuine no-op apply is committed with no modifications and a null source
journal. Compile reports retain `source_commit=committed`; source-only fix keeps
its existing success, modifications and journal fields without adding a separate
commit-status key. PDF publication can still create its independent journal.

Captured/committed source membership is not compiler observation. A separate
report contains actual dependency paths in the compiler's observed order,
retained dependency evidence, source manifest and scalar-render budget only after
a genuine returned artifact passed its guard. Failure before that point leaves
these observations unavailable. Later publication failures retain observations
already captured. Fresh historical copies cannot authorize a write or compile
and do not expose the artifact or PDF bytes.

Actual invocation, raw status and resource facts remain independently retained
through context cleanup and later refusals. Graph summary absence is handled by
its narrow bridge error type, without masking an original apply failure or its
recovery details. Input failures and authority failures keep their existing
error classes; no message-text guesses invent a detailed error category.

## Resource, status and recovery limits

Resource selection is independent of source selection. ASCII checks every source,
including unused module text and import literals. The explicit Korean profile
uses only its pinned local AppleGothic Regular face and reviewed repertoire; it
does not promise Korean bold/italic styling, arbitrary Unicode or system-font
fidelity. No installed font is modified, bundled or uploaded. Graph scalar-text,
source, path, dependency, workspace and output bounds remain unchanged.

The shared status, cancellation and executable-signal machinery is unchanged.
Input refusal is 2; unsafe, uncertain or unresolved work is 3. Natural compiler
status or fatal signal is propagated only under existing known-cleanup rules.
Compiler success followed by unsafe publication is not success. Imported Python
`main()` changes no signal handlers; only the executable can re-signal itself.
Both contexts close before final reporting or self-signalling.

Source and output status/recovery commands retain their existing schema-1 paths.
They require no live module/configuration/compiler/font state. Historical
recovery does not reconstruct unpersisted graph observations or infer that an
absent source receipt proves nothing changed. Current sources and outputs remain
separate from historical journal facts. No consumer installation, AGENTS update,
wrapper rollout, merge or deployment follows from selecting this profile.

See [CAPTURED_RELATIVE_CORE_CONTRACT.md](CAPTURED_RELATIVE_CORE_CONTRACT.md),
[CAPTURED_RELATIVE_INPUT_CONTRACT.md](CAPTURED_RELATIVE_INPUT_CONTRACT.md),
[CAPTURED_RELATIVE_FIX_CONTRACT.md](CAPTURED_RELATIVE_FIX_CONTRACT.md),
[CAPTURED_RELATIVE_COMPILE_CONTRACT.md](CAPTURED_RELATIVE_COMPILE_CONTRACT.md),
[CAPTURED_RELATIVE_PUBLISH_CONTRACT.md](CAPTURED_RELATIVE_PUBLISH_CONTRACT.md),
[OFFLINE_CLI_CONTRACT.md](OFFLINE_CLI_CONTRACT.md) and
[EXPLICIT_COMPILE_CLI_CONTRACT.md](EXPLICIT_COMPILE_CLI_CONTRACT.md).
