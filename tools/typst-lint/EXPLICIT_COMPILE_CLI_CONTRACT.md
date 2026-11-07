# Explicit closed-core fix, compile and PDF publication CLI

This opt-in route composes the existing protected source bridge, committed-source
compiler and independent PDF publication journal. It admits only the exact
closed-core configuration and grammar, not general Typst projects. Global rule
acceptance remains **33 PARTIAL / 0 COMPLETE**. It does not install a wrapper,
change a consumer project or AGENTS.md, merge, deploy or roll out anything.

## Commands and authority

```sh
./typst-lint compile --root ROOT --profile closed-core-v1 --resource-profile embedded-ascii-v1 --offline-exclusive --offline-output-exclusive --target main.typ --output build/main.pdf --format json
./typst-lint output-status --root ROOT --format json
./typst-lint recover-output --root ROOT --offline-output-exclusive --output build/main.pdf --journal-id ID --format json
```

Use `./typst-lint compile --profile closed-core-v1 --help` for the explicit
route's options and both exact resource-profile names. Bare `compile --help`
retains the ordinary read-only route's help and dispatch behavior.

Every authority option shown for compile is required. The source assertion means
editors, watchers and all other source writers remain stopped. The independent
output assertion covers exporters, watchers, destination ancestors and output
journal storage. Neither assertion is inferred from the other, a root lock or
Git state. Both leases are minted only after all CLI syntax/combinations and the
literal destination have passed validation, followed by root classification.

The source target is one literal canonical root-relative `.typ` file. The output
is one literal canonical root-relative `.pdf` path with existing safe parents.
Absolute, dot/dotdot, glob and template destinations are refused. No compiler
argv/tail, positional source/output pair, stdin, watch, eval, custom executable,
flags, package directory, unknown resource profile, custom
store, limit, deadline or fault injection is accepted. The internal deadline is
10 seconds. Options do not abbreviate; duplicate options, including mixed
`--option value` and `--option=value`, are refused. Global options before the
command remain supported. An explicit route does not interpret `--` as permission
to execute compiler arguments.

Only typst-lint opts into this route. Ordinary check, fix, dry-run and unprofiled
compile remain read-only, blocked with status 2. Ordinary compiler tails remain
inert; bare `--output` is not an authority trigger. Source-only offline fix,
journal-status and recover retain their existing behavior. typst-style compile
cannot acquire source/output authority through these options.

## Ordered lifetime and separate recovery

Compile enters ProtectedCoreFix using the existing fixed sibling source store,
then enters PreparedPdfPublication before validate or apply. Source entry checks
source journals first. PDF entry scans its separate store under the borrowed root
lock, captures and journals the exact old output, then validation and protected
source application run. No preliminary standalone scan is added to the mutation
path. A source-entry refusal does not imply the PDF store was checked.

Only the successfully applied bridge's live compile_snapshot() is compiled with
an explicit known resource profile. `embedded-ascii-v1` retains unchanged ASCII
plus an optional single UTF-8 BOM behavior. `embedded-applegothic-ko-v1` separately
admits the compiler's exact versioned Korean repertoire and pinned local
AppleGothic capture. It does not widen the closed-core grammar. General
Unicode/system-font fidelity, imports/packages and other resources remain
unsupported. Preparation remains resource-neutral; the selected Korean font
collision check follows profile validation and precedes launch intent/capture/probe.
No font path or bytes are selected by CLI arguments. The sole supported installed
font is `/System/Library/Fonts/Supplemental/AppleGothic.ttf`; any private copy is
local and temporary, never bundled or uploaded. See CLOSED_CORE_CONTRACT.md,
CLOSED_CORE_COMPILE_CONTRACT.md and PDF_PUBLICATION_CONTRACT.md for the unchanged
source, compiler and output engines. A successful immutable source summary is
retained independently of any subsequent compile/publication failure. Both
contexts exit before printing, returning a process status or terminating.

No automatic recovery is performed. Successfully committed source fixes remain
committed if compilation or PDF publication fails. PDF rollback/recovery does not
roll back source. `source_commit=not_applied` means no successful receipt was
obtained; it is not proof that source was untouched. After a failed/interrupted
apply without a receipt, the CLI checks source inventory after the contexts end,
or reports its source outcome unknown if inventory cannot be checked. Pending or
unknown source cleanup keeps status 3. Source and PDF recovery remain separately
reported and separately authorized.

output-status calls only inventory_publications. It acquires no offline leases,
loads no config/source/parser/compiler, creates no storage, fsyncs nothing and
recovers nothing. Its API returns historical entry tuples, so the CLI reports
entries, pending IDs and historical_only=true. Empty means **no PDF journals
observed**. It does not invent store_present/namespace_present or claim absent
storage proves no past attempt.

recover-output requires only a fresh output lease, the root, exact literal output
allowlist and one 32-character lowercase hexadecimal ID. It calls
recover_publication directly. It does not require a source assertion, target,
profile, current configuration, source existence or compiler identity. Later
source/config/compiler changes do not prevent output-only historical recovery.
Source and PDF journal IDs are never interchangeable.

## Status and serialization

- 0: durable PDF publication, complete explicit output recovery, or observed
  inventory with no pending records.
- 2: invalid arguments/literal destination/profile, initial absent/non-directory
  root, source config/grammar/parser or initial encoding refusal, or unsupported
  resource profile/repertoire, provided cleanup is safe.
- 3: source/output safety, pending/corrupt journals, unsafe storage, unresolved or
  unknown recovery/cleanup, probe failure, uncertain launch, compiler timeout,
  internal cancellation or stream-limit failure, or absent/unsafe/drifted required
  font. Compiler zero followed by a later safety/publication failure remains 3. A safe abort's internal gate 3
  does not by itself change an original input refusal from 2 to 3.
- A natural nonzero compiler exit propagates its exact positive code only when
  the immediate known PublicationError cause is CompilerFailure with typed
  kind=process_exit, the actual compile invocation is known started with observed
  nonzero status, compiler safety_gate=passed, PDF ended aborted_unpublished with
  untouched output/no recovery, and both source/output contexts closed safely.
  A natural compiler signal under the same conditions is a signed logical status
  in Python and actual same-signal termination at the executable boundary.

Classification uses typed failure kinds, never reason strings or arbitrary cause
chain searches. A killed child from timeout/cancellation/stream overflow can have
raw status -9 without making -9 the gate result. The distinct kinds are
process_exit, cancelled, timeout, stream_limit and unknown.

JSON uses explicit primitive serializers, ensure_ascii=true and byte streams as
stdout_hex/stderr_hex. It includes signed status/optional termination_signal,
tri-state compiler_invoked (true/false/null), raw compiler_status, compiler safety
and typed failure, the original captured resource report, source_summary and
source journal ID, publication_report with
output disposition/PDF journal/recovery, and separate source_recovery and
output_recovery. It includes no PDF bytes or full source text. Reporting values
and summaries are never consumed as write authority.

The actual immutable compiler report survives both contexts, without synthesis
from invocation summaries. Its resource evidence contains profile/repertoire,
face index, installed/private path, file and ancestor identities, SHA-256 and
size, plus font format/family/PostScript/full/version/unique names. It includes
no font bytes. A verified ASCII report has `fonts=[]`; a null resource report
means not captured/unavailable. A later safety error does not erase known
compiler evidence. The original `compile_report.safety_gate` remains the compiler
call's fact; `publication_report.gate_status`, reason and final status separately
report a later publication/safety failure. A safe natural compiler exit 3 can
still have compiler safety passed, so numeric status alone is not a safety gate.

Invocation details distinguish original_invocation from historical_recovery.
Historical recovery's empty attempted_invocations means argv/streams were not
persisted, not that no past execution occurred. Historical recovery and each
inventory entry report `resource_details_available=false` and explain that
original font identities were not persisted. Their resource report is null;
current compiler/font registry data never fills historical gaps. Schema-1
manifest/event fields and transitions remain unchanged. Updated readers accept
old ASCII journals, while older readers may safely reject the new Korean name.
Empty inventory retains its existing shape. Historical terminal reports leave
observed_sha256=null: the current output was not examined. Text presentation
likewise distinguishes compiler invoked/not invoked/unknown and output
published/restored/untouched/unresolved dispositions.

## Executable signal boundary

Imported main() never installs handlers or signals itself. It returns a negative
logical status only for an eligible observed natural compiler signal. The
executable launcher, direct typst_lint.py and module entrypoints run through
executable_main(), flush the report, restore handlers, reset/unblock the selected
signal and signal only their own PID. They never signal a parent/group or turn a
negative status into SystemExit(-signal).
Negative process status also requires a reviewed fatal-default signal, not merely
membership in valid_signals(). Stop/continue and default-ignored signals are
refused as status 3. The portable fatal set includes ordinary termination,
fault, alarm, profiling, CPU/file-limit and user signals. Darwin additionally
admits SIGEMT, but not its default-ignored SIGIO. Linux additionally admits
SIGIO/SIGPOLL, SIGPWR, SIGSTKFLT and valid runtime SIGRTMIN through SIGRTMAX.
Unknown platforms get only the portable fatal set. The executable rechecks this
policy before any handler/mask changes or self-signal. Tests use pure malformed
reports for nonterminating signals and never actually stop a test process.


Only the explicit compile executable scopes SIGINT/SIGTERM coordination. Ordinary
and source-only routes, output-status and recover-output keep their original
signal handling. The coordinator records the first actual received CLI signal
separately from child status/failure kind, sets the owned compiler cancellation
Event and defers repeated signals through cleanup. Source application is bounded
and cooperatively deferred, not immediately interrupted: it may finish a commit
before cancellation is observed at the next boundary. The compiler kills/reaps
its owned child when cancellation is observed. Safe untouched, restored or
published output plus known source/context/compiler cleanup permits termination
by the original received signal. Unknown child launch/cleanup, pending source or
unresolved PDF recovery instead remains status 3. No signal handlers are changed
for Python callers, and SIGKILL cannot promise a final report.

Tests mutate only temporary consumers, use existing private trusted test seams,
and do not introduce public compiler/artifact authority bypasses. Native and
subprocess crash tests characterize the selected platform, not universal OS or
power-loss guarantees.
