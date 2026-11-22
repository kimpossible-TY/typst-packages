# Internal committed-source compiler experiment

`closed_core_compile.py` is an internal compiler engine. It accepts no requested
PDF destination and publishes no artifact itself. The separately selected CLI in
[EXPLICIT_COMPILE_CLI_CONTRACT.md](EXPLICIT_COMPILE_CLI_CONTRACT.md) reaches it
through the guarded publication session. Ordinary unprofiled check/fix/compile
behavior and the global partial rule audit are unchanged.
A successful compiler exit is not a complete build gate.

## Authority and lifetime

The caller must already have entered a real `ProtectedCoreFix`, independently
validated its candidate, and successfully applied that candidate. Only that same
bridge's opaque `compile_snapshot()` is accepted. Baseline transaction snapshots,
reporting summaries, serialized JSON, caller-provided source bytes, and tokens
from another session cannot authorize compilation.

`compile_committed_snapshot(fix, snapshot, *, profile, deadline_seconds=10.0,
cancel_event=None)` compiles the retained **committed postimage**, not a fresh
source read. The real bridge guard verifies the original committed file identity,
bytes, metadata, source parents, original config/catalog, applied candidate,
independent provenance, selected source profile/grammar and session. It runs
before/after probes and compilation,
after output capture, on failure, and again before artifact issuance. Source or
evidence drift blocks the artifact. It does not undo valid committed fixes.

The opaque frozen artifact is registered by identity to a weak reference to the
issuing bridge and its exact snapshot. `assert_artifact_current(fix, artifact)`
and the `artifact.pdf_bytes` property recheck that live authority and installed
compiler plus every captured installed font identity/hash. Foreign, forged,
modified, expired or stale artifacts refuse. Artifact repr/stringification omits the PDF payload. Ordinary private
payload attribute reads and generic dataclass serialization also pass through
the live guard, so expiry cannot be bypassed through normal logging or asdict/
astuple. Explicit reflection into trusted internals remains out of scope.
Private workspace evidence is historical because the workspace is removed
before return. Installed and private font identity/hash checks surround each
invocation, output capture, failure cleanup and issuance. Installed-font checks
continue through artifact access after the workspace is gone. Already-read bytes
cannot be revoked. This is a cooperative trusted Python API, not isolation from malicious Python reflection, a hostile user/OS,
or writers that violate the required offline-exclusive assertion.

## Explicit resource profile

`profile` is a required resource keyword with no default, distinct from source
admission selection `closed-core-v1` or `structured-core-v1`. The source session
and its guarded committed snapshot own that selection; resource selection never
widens it. The pure `resource_profiles.py` module exposes
exact historical names and source-repertoire validation independently of live
platform/compiler/font availability. Neither imports nor name validation read
fonts, execute probes, normalize text, or make an availability claim.

- **embedded-ascii-v1** retains its existing ASCII-only source behavior and exact
  invocation. One optional leading UTF-8 BOM is accepted. It never resolves,
  reads, captures or adds an external font directory, even if a Korean font is
  missing or unsafe. Unicode filenames remain independent of source repertoire.
- **embedded-applegothic-ko-v1** must be selected explicitly. It accepts the same
  ASCII/BOM handling plus U+AC00..U+D7A3 modern precomposed Hangul syllables,
  U+3131..U+3163 modern compatibility jamo, and exactly these 21 characters:
  `· – — ‘ ’ “ ” … ₩ 、 。 〈 〉 《 》 「 」 『 』 【 】`.

The captured AppleGothic file has one Regular face. Native structured fixtures
preserve Korean glyphs in headings, lists, strong and emphasis, but strong/emphasis
Korean text remains regular upright, including their combination. Headings use
a larger size without a Korean bold face. The actual PDF font, glyph codes,
rendering mode and unskewed matrices agree with this observed fallback. Latin/math
may use styled embedded fonts. Before/after repair bounding boxes and page
rasters match in the same captured environment; this is not a claim of Korean
bold/italic fidelity or equivalence to ambient font discovery.

There is no automatic normalization. Conjoining/NFD jamo, fillers U+115F,
U+1160 and U+3164, archaic/extended jamo, combining accents, joiners, variation
selectors, bidi controls, emoji, private-use/unassigned codepoints, and literal
Unicode Greek/math refuse. ASCII-spelled math such as `alpha` remains available.
The repertoire only restricts source: it never widens or replaces the existing
closed-core AST grammar. In particular, punctuation script operands still refuse
there. Admitted `$x_(한글)$` can fail with the normal compiler's unknown-variable
error; it is not silently rewritten or treated as a font failure. All eleven
thousand modern syllables, compatibility jamo, the punctuation list, and selected
Korean script operands were characterized with the pinned local resources, but
cmap coverage, successful process status or extracted text alone never authorize
a wider repertoire or establish visual fidelity for arbitrary source.

The Korean profile binds the same exact reviewed compiler below and only:

- `/System/Library/Fonts/Supplemental/AppleGothic.ttf`
- 15,253,080 bytes, SHA-256
  `69071005e523e3a2bf276b35b0066f68a7ec0d942c860941ad188ef15ef69c51`
- TrueType, sole face index 0; family/PostScript name `AppleGothic`; full name
  `AppleGothic Regular`; version `22.0d2e1`; unique name
  `AppleGothic Regular;13.0d1e3;2017-07-12`.

This immutable private declaration is not a public font path/hash/family/argv
approval facility. Safe bounded anchored capture verifies the installed font's
actual size, hash and file identity, then copies the entire file unchanged to a
0700 private fonts directory with its sole leaf 0400. No subsetting or face
extraction occurs. Evidence records the actual installed/private captures and
reviewed metadata, not hashes fabricated from the registry. A missing, changed,
unsafe, unreviewed or unsupported-platform resource is a safety refusal, with no
fallback, download or alternative font selection. Unsupported names/repertoires
are input refusals before font capture or any compiler probe.

AppleGothic contains glyf/morx and has no OS/2 embedding-permission table. That
absence is **not redistribution permission**. Font files and captured font bytes
are for private local use on this Mac only and must never enter repository
fixtures, releases, uploads or cloud storage. This profile grants no permission
to share font files or compiler output; PDF output/publication requires its own
explicit authorization. The compiler does not change installed fonts or consumer
files.

The fixed reviewed native artifact is Darwin arm64 Typst 0.15.1 at:

- `/opt/homebrew/Cellar/typst/0.15.1/bin/typst`
- SHA-256 `08d01e5a434e821c4a1ce30dd1593e1bbc57c9b1203c4d760d8912ef4ce6a621`
- Exact version output `typst 0.15.1 (unknown commit)` followed by newline.

A private immutable platform registry supplies that trust, never a caller-supplied
hash, approval boolean, executable path, PATH search or command. Unknown compiler
bytes are refused **before version probing**. Installed bytes are captured with a
bounded no-follow/nonblocking regular-file descriptor anchored by root-to-leaf
no-follow directory descriptors with retained parent identities, stable identity/size/hash,
single link, safe owner/mode and canonical path. Launcher path and inode aliases
are rejected. The exact bytes are copied into a private executable and checked
before and after each invocation. Both installed and private copies must remain
unchanged. No installation, downloads, dynamic compiler selection or public test
bypass exists. Tests patch private trusted seams with owned temporary programs.

The copied executable includes Typst's embedded fonts. Only the explicitly
selected Korean profile adds the reviewed complete external font above. The
reviewed native artifact also uses trusted macOS system libraries/frameworks. Those OS runtime
libraries are explicitly **outside** the compiler-resource copy claim. This is
not a general OS file-read or network sandbox.

## Private invocation

One private workspace contains the executable, one exact relative target path
(including nesting/spaces), empty private home/cache/config/data/temp/package
locations, fixed PDF output and dependency evidence. The Korean profile alone
adds the one private fonts directory and its one allowlisted font file. No
consumer resource tree is copied. The subprocess receives an environment built from nothing: HOME,
XDG_CACHE_HOME, XDG_CONFIG_HOME, XDG_DATA_HOME, TMPDIR, LANG=C, LC_ALL=C, TZ=UTC.
No PATH, inherited TYPST variables, system-font/package variables,
SOURCE_DATE_EPOCH, loader injection, proxy settings or credentials are retained.

A separately reported `--version` probe must match the reviewed bytes. Actual
compilation uses only `compile --format pdf --root PRIVATE_PROJECT
--ignore-system-fonts --package-path PRIVATE_PACKAGES --package-cache-path
PRIVATE_PACKAGE_CACHE --creation-timestamp 0 --jobs 1 --diagnostic-format short
--deps PRIVATE_DEPS --deps-format zero EXACT_PRIVATE_INPUT PRIVATE_PDF`.
The Korean profile adds only `--font-path PRIVATE_FONTS` immediately before
the exact input/output arguments, retaining `--ignore-system-fonts`. There is no
shell, stdin source, custom argument facility, output template, stdout output,
caller-selected font path, features, watch/eval or viewer launch. Cwd is the
private project. Subprocess stdin is DEVNULL; umask is 077; it has its own process
group for termination on interruption/failure.

The NUL-separated dependency output must be exactly the retained target relative
path plus NUL, interpreted only at the fixed private project cwd. This is the
characterized output of the reviewed binary; no normalization or path guessing
is permitted. The copied input evidence separately records its absolute path.
It is retained as checked evidence, **not proof of every OS read**. Admission
forbids resource-bearing source constructs; this prototype neither resolves nor
copies imports, packages, arbitrary user fonts, images or filesystem resources.
Typst dependency output omits this font. The separately captured font is explicit
resource evidence, never fabricated as an observed dependency.

## Bounds, failure and artifact contents

Default limits are 64 MiB executable, 1 MiB source, 16 MiB PDF, 64 KiB per output
stream, 64 KiB dependency output, 96 MiB private workspace and 128 entries.
External fonts are limited to one file, 16 MiB per file and 16 MiB total. The reviewed compiler
plus full AppleGothic uses 59,947,816 bytes before source/output overhead. A
single bounded deadline covers capture/probes/compile/final checks, defaults to
10 seconds and can be explicitly chosen only within (0, 120] seconds. Stdout and
stderr are drained while running, with retained buffers never exceeding bounds.
The process group is killed and reaped for timeout, cancellation, signals,
nonzero exit or interrupted work. A `threading.Event` supports cancellation.
Late cancellation/deadline expiration also blocks issuance.

Workspace file sizes/counts, modes, allowed names and private directory identities
are checked during execution and after it. These are sampled operational limits,
not kernel disk quotas or a hostile-compiler security sandbox. Output reads are
strictly descriptor-bounded regardless of sampled growth. PDF and dependency
outputs are precreated with retained descriptors before any compiler invocation.
The reviewed binary preserves those inodes. Their device/inode/mode/link/owner
binding is checked throughout execution and capture, so a replacement after the
child exits cannot become newly blessed. Same-inode concurrent writes by another
actor during authorized compiler execution are outside the private-workspace
assumption; inode binding alone does not prove the provenance of each written
byte. Same-inode drift after a captured output baseline is also refused.
Missing, symlinked,
hardlinked, unsafe, replaced or oversized output refuses. Basic PDF checks require
a PDF header, final startxref/EOF trailer and plausible cross-reference offset;
these are not a complete PDF parser or fidelity validator.

The immutable artifact retains PDF bytes/hash; target/source/config/catalog
hashes; original evidence path/hash pairs anchored privately to the real receipt;
installed/private compiler, copied input, output and dependency identities/hashes;
explicit resource profile and frozen font/resource report; checked dependency
paths; separate probe metadata; and actual compile argv/environment/cwd/returncode/stdout/stderr. No requested
user-destination file exists. Unsupported profile/source uses
`UnsupportedCompileProfile`, stale/unsafe state uses `CompileSafetyError`, and
probe/compile nonzero/signal/timeout/cancellation/stream failures use
`CompilerFailure` with invocation metadata when available. Safety drift takes
precedence over an otherwise successful or failed compiler exit.

## Attempt reporting, separate from authority

The compiler owns a private attempt ledger. `CompilerFailure.report`,
`CompileSafetyError.report`, and a successful artifact's `report` expose frozen
`CompileReport` snapshots. Their `attempts` tuple (also named
`attempted_invocations`) contains each attempted version probe and compile in
order. A prelaunch deadline, cancellation, profile/admission refusal, or safety
failure does not create an attempt. A successful version probe alone never means
the compile was invoked. Reports are bounded historical output, not an input to
compiler trust, source admission, snapshot verification, or artifact issuance.
Constructing or copying a report grants no authority. The artifact's report and
all nested reporting fields participate in its existing identity/content seal.
Direct post-issuance guard exceptions may carry the existing empty refusal
report; original facts remain in `artifact.report` and in the publisher's
retained report, rather than being reconstructed from the later exception.

`CompileReport(attempts, safety_gate, compiler_failure, resource_report=None)`
preserves the existing three-positional constructor. Its optional frozen
`ResourceReport(profile, repertoire_version, fonts)` is `None` before resources
have been fully captured, rather than claiming empty fonts when unavailable.
After verified capture ASCII has an explicit empty fonts tuple; Korean retains
one frozen `FontEvidence(face_index, installed, private, identity)` whose
`installed` and `private` are actual `FileEvidence` captures and `identity` is
reviewed `FontIdentity` metadata. The artifact exposes this same resource report.
Reports retain original captured facts on later compiler failures, final safety
refusals and installed-resource staleness, without claiming current validity.
Every new evidence dataclass is recursively frozen into the artifact's content
seal, including nested font metadata and installed/private evidence. Reports
never contain font bytes and constructing them never authorizes resource capture.

Each immutable `Invocation` retains the existing purpose, argv, cwd, environment,
raw returncode and bounded stdout/stderr, plus:

- `launch_attempted`: true once recorded immediately before calling `Popen`.
- `started`: true only after `Popen` returns a process, false for a known launch
  failure, or `None` if interruption leaves launch outcome uncertain.
- `lifecycle`: `launch_failed`, `running`, `exited`, `signaled`, or `uncertain`.
  Exit/signal states require an observed actual process returncode.
- `termination_reason`: the cancellation, timeout, stream-limit, nonzero/signal,
  interruption, launch, runtime, or in-run safety reason, when one occurred.
  A later safety refusal does not rewrite a successfully exited attempt's reason.

At most two attempts are retained. Each argv has at most 64 strings, the private
environment at most 16 pairs, and each retained text item at most 8192 encoded
bytes. Output buffers retain the existing 64 KiB per-stream bound. Immutable
snapshots do not hold process objects, mutable buffers, exceptions/tracebacks,
callbacks, bridge references, or authority tokens.

`CompileReport.compiler_invoked` concerns only actual compilation: true if a
compile is known to have started, false if none started (including a known launch
failure), and `None` for an uncertain compile launch. `compiler_status` is only
the observed raw compile returncode, or `None` when none was observed. In
particular, compiler status 0 remains 0 when a later safety check refuses output;
a gate result is never substituted for that process status.

The independent `safety_gate` is `passed` after compiler control flow completes
its safety checks, `refused` for a safety refusal, or `pending` when interrupted
work did not complete the gate. `passed` does not mean a successful compile or an
issued artifact. `compiler_failure`, when present, is frozen
`CompilerFailureInfo(reason, invocation, kind)` retaining the original failure
before a final guard can supersede it. Its frozen `kind` and the read-only
`CompilerFailure.kind` use `CompilerFailureKind`: `process_exit` only for an
observed nonzero or natural signal completion, `cancelled`, `timeout`,
`stream_limit`, or conservative default `unknown`. Failure sites assign the kind;
reason text and cleanup-observed returncodes never infer it. A failed version
probe can have `process_exit` but retains purpose `version-probe`. Existing
reason/invocation constructors and `CompilerFailure.reason`,
`CompilerFailure.invocation`, and reports remain available. Late cancellation
or timeout retains its distinct kind even after compiler status 0. A final guard
preserves all probe and compile attempts and original failure kind, even when it
supersedes a compiler failure or output capture/artifact issuance. That retained
history does not make a safety refusal an immediate compiler failure. No artifact
is issued on refusal.

Started-process failure and `KeyboardInterrupt` cleanup kill/reap the owned
process group and retain any observed exit/signal status. An interrupted or
failed cleanup never invents a returncode or claims a known process did not
start. Interruption during `Popen` may leave no process handle, so reporting is
explicitly uncertain. Process death (including killing this Python process with
SIGKILL) cannot promise a final in-memory report or postmortem facts. Durable
restart/publication recovery is outside this ledger's scope.

Deterministic adapter tests run without a native Typst binary. Real bridge tests
require the existing official syntax adapter. Real pinned Mac compiler tests are
explicitly opt-in; Linux must not silently substitute another compiler or claim
that native characterization ran.
