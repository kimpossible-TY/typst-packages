# Internal committed-source compiler experiment

`closed_core_compile.py` is an internal prototype. Nothing imports it into CLI
dispatch, no requested PDF destination is accepted, and no artifact is published.
Ordinary check/fix/compile behavior and the global partial rule audit are unchanged.
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
independent provenance and session. It runs before/after probes and compilation,
after output capture, on failure, and again before artifact issuance. Source or
evidence drift blocks the artifact. It does not undo valid committed fixes.

The opaque frozen artifact is registered by identity to a weak reference to the
issuing bridge and its exact snapshot. `assert_artifact_current(fix, artifact)`
and the `artifact.pdf_bytes` property recheck that live authority and installed
compiler identity/hash. Foreign, forged, modified, expired or stale artifacts
refuse. Artifact repr/stringification omits the PDF payload. Ordinary private
payload attribute reads and generic dataclass serialization also pass through
the live guard, so expiry cannot be bypassed through normal logging or asdict/
astuple. Explicit reflection into trusted internals remains out of scope.
Private workspace evidence is historical because the workspace is removed
before return. Already-read bytes cannot be revoked. This is a cooperative trusted
Python API, not isolation from malicious Python reflection, a hostile user/OS,
or writers that violate the required offline-exclusive assertion.

## Explicit resource profile

`profile` is a required keyword with no default. The only experimental resource
profile is **embedded-ascii-v1**, separately named from source admission profile
closed-core-v1. It accepts ASCII source bytes, optionally preceded by exactly one
UTF-8 BOM. Ordinary ASCII math identifiers are retained. Broader Unicode source
is refused before compiler capture or probing, even if the closed-core grammar
admits it. Embedded-only rendering previously changed Korean extracted PDF text;
this experiment makes no Unicode or ordinary system-font fidelity claim.
External font capture and full ordinary-font semantics remain future work.

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

The copied executable includes Typst's embedded fonts. The reviewed native
artifact also uses trusted macOS system libraries/frameworks. Those OS runtime
libraries are explicitly **outside** the compiler-resource copy claim. This is
not a general OS file-read or network sandbox.

## Private invocation

One private workspace contains the executable, one exact relative target path
(including nesting/spaces), empty private home/cache/config/data/temp/package
locations, fixed PDF output and dependency evidence. No consumer resource tree is
copied. The subprocess receives an environment built from nothing: HOME,
XDG_CACHE_HOME, XDG_CONFIG_HOME, XDG_DATA_HOME, TMPDIR, LANG=C, LC_ALL=C, TZ=UTC.
No PATH, inherited TYPST variables, system-font/package variables,
SOURCE_DATE_EPOCH, loader injection, proxy settings or credentials are retained.

A separately reported `--version` probe must match the reviewed bytes. Actual
compilation uses only `compile --format pdf --root PRIVATE_PROJECT
--ignore-system-fonts --package-path PRIVATE_PACKAGES --package-cache-path
PRIVATE_PACKAGE_CACHE --creation-timestamp 0 --jobs 1 --diagnostic-format short
--deps PRIVATE_DEPS --deps-format zero EXACT_PRIVATE_INPUT PRIVATE_PDF`.
There is no shell, stdin source, custom argument facility, output template,
stdout output, custom font path, features, watch/eval or viewer launch. Cwd is the
private project. Subprocess stdin is DEVNULL; umask is 077; it has its own process
group for termination on interruption/failure.

The NUL-separated dependency output must be exactly the retained target relative
path plus NUL, interpreted only at the fixed private project cwd. This is the
characterized output of the reviewed binary; no normalization or path guessing
is permitted. The copied input evidence separately records its absolute path.
It is retained as checked evidence, **not proof of every OS read**. Admission
forbids resource-bearing source constructs; this prototype neither resolves nor
copies imports, packages, user fonts, images or arbitrary filesystem resources.

## Bounds, failure and artifact contents

Default limits are 64 MiB executable, 1 MiB source, 16 MiB PDF, 64 KiB per output
stream, 64 KiB dependency output, 96 MiB private workspace and 128 entries. A
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
explicit resource profile; checked dependency paths; separate probe metadata; and
actual compile argv/environment/cwd/returncode/stdout/stderr. No requested
user-destination file exists. Unsupported profile/source uses
`UnsupportedCompileProfile`, stale/unsafe state uses `CompileSafetyError`, and
probe/compile nonzero/signal/timeout/cancellation/stream failures use
`CompilerFailure` with invocation metadata when available. Safety drift takes
precedence over an otherwise successful or failed compiler exit.

Deterministic adapter tests run without a native Typst binary. Real bridge tests
require the existing official syntax adapter. Real pinned Mac compiler tests are
explicitly opt-in; Linux must not silently substitute another compiler or claim
that native characterization ran.
