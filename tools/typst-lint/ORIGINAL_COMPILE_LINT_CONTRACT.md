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

By default the JSON report samples at most 50 diagnostics and 50 suppressions;
`--lint-details` raises each bound to 10,000. Counts remain exact and omitted
counts are explicit. Samples are sorted by path, line, column and rule; the
implementation retains only the bounded sample during analysis. Per-source
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
