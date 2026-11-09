# Captured original compilation with optional document diagnostics

`typst-style compile --compile-original --root /absolute/project --entrypoint
main.typ --manifest /absolute/resources.json --pdf-stdout` compiles the issued,
captured original sources and resources without applying edits. It does not run
lint by default. `--lint-document` explicitly requests read-only document
diagnostics on the same issued include context and resource capture before the
compiler runs. `--rule-set core` is the default; `--rule-set pde` adds the two
supported document display-source checks. `--rule-set` and `--lint-details`
require `--lint-document`.

The diagnostic implementations are the existing document script analyzer and,
for PDE, standalone display and display continuation gap analyzers. They inspect
literal source patterns; the report includes their actual findings, inline
suppressions, source hashes, node counts, per-file rule coverage, skip reasons
and unresolved literal-include inventory. Consumer configuration and package
resolution are not loaded. Dynamic paths, include execution, bindings and
rendered contexts are not proved. Every file/rule row is `partial`; its
`supported_scope_status` only describes that bounded source analysis. All
remaining enabled rule IDs are explicitly listed as uninspected. No conditional
helper rule, whole-document coverage, fix, source-write or publication authority
is implied by a missing finding.

`--lint-local-hints` additionally requires the explicit PDE rule set. It runs
the existing source-local analyzers on the same captured source trees and places
their findings in a separate `conditional_local_hints` section, never in the
three primary diagnostic totals. The hints depend on include-site execution,
lexical bindings and rendering context; none is proved by source-local analysis.
Shared resolution reasons, rule-specific skips, parser errors, suppressions and
their omitted counts remain separate. Each rule/file row is conditional; these
rules remain in `coverage.uninspected`, meaning uninspected by the primary
backends; this separate conditional section may inspect source patterns without
proving full rule coverage. Their hints
provide neither source-edit nor compiler permission.

The same opt-in also adds a separate `conditional_hyphen_markers` section for
the literal `#paragraph-tab` spelling, covering bounded paragraph gap, target
and capitalization patterns. It is not combined with the 14-rule local hint
counts. Source hashes, literal marker spans, rule metadata, permission flags and
the shared capture are checked before reporting. Per-source `UNAVAILABLE` and
unresolved reasons are retained rather than interpreted as no findings. These
hints remain conditional on runtime marker binding, execution and paragraph
rendering; they do not authorize edits or establish complete coverage.

The opt-in also reports `conditional_include_forwarding` separately. It uses
the same issued source and resource capture, including staged package/cache
selection, to identify bounded source-level include ownership. Original
`coverage.unresolved_includes` records are retained unchanged. Missing,
unselected or mismatching reviewed package evidence produces `UNAVAILABLE`,
not a clean finding; invalid or mutated issued captures refuse the whole lint
operation. Site samples use the selected 50/10,000 bound and report omissions.
Neither runtime execution, package selection nor live package freshness is
proved, and this evidence grants no edit, compiler or publication authority.

By default the JSON report samples at most 50 diagnostics and 50 suppressions;
`--lint-details` raises each bound to 10,000. Counts remain exact and omitted
counts are explicit. Samples are sorted by path, line, column and rule; the
implementation retains only the bounded sample during analysis. Conditional
local findings and suppressions have independent bounds at the same selected
sample limit; the hyphen-marker section has its own bounds at the same limit.
All sections are included in the aggregate item and byte budgets. Per-source
parser nodes, total report items and the formatted UTF-8 lint section are bounded
and their limits are reported. Source-level skip and parse-error counts remain
exact; each file/rule row includes at most 20 reasons and 20 error spans, with
explicit omitted counts. Exceeding an input or report bound refuses the action;
it never returns a clean lint result.

Compilation status is distinct from diagnostics and incomplete coverage. Exit
0 means the requested original PDF was compiled and streamed successfully, not
that lint passed. Reports distinguish lint attempted, completed, findings and
global coverage. An analysis, capture or compiler failure occurs before PDF
streaming and leaves stdout empty. A failed or interrupted stdout transfer may
already have delivered partial PDF bytes, which cannot be recalled.

The existing strict `typst-style compile` gate is unchanged. This explicit path
requires PDF stdout to be a pipe or socket and refuses a regular-file stderr;
reports and PDF bytes use separate streams. The shell can nevertheless create
or truncate a redirected file before the program starts, and a downstream pipe
consumer controls its own writes. A caller capturing both pipes must drain PDF
stdout and JSON stderr concurrently; detailed reports can exceed a pipe buffer.
The sandbox denies network and writes outside
its private workspace but does not prove all OS reads or future dynamic paths.
The compiler and explicitly selected fonts/resources may differ from an ordinary
local Typst environment. `--discover-dependencies` adds a baseline discovery
run and rechecks its observed dependencies and PDF hash against the captured
compile; those observations do not prove a complete dependency closure.
