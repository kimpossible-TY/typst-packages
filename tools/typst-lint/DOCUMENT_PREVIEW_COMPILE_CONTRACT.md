# Explicit document PDF preview

`typst-lint preview-compile` is an explicit macOS preview route. It captures an
include inventory, recomputes the three-rule sequence, captures an explicitly
listed resource set, and compiles baseline and candidate in a private temporary
workspace. It does not modify consumer sources, publish a PDF, or turn the
ordinary `typst-style compile` gate into a complete lint/build gate.

```sh
typst-lint preview-compile --root /absolute/project --entrypoint main.typ \
  --manifest /absolute/resources.json --format json
typst-lint preview-compile --root /absolute/project --entrypoint main.typ \
  --manifest /absolute/font-seed.json --discover-dependencies --format json
typst-lint preview-compile --root /absolute/project --entrypoint main.typ \
  --manifest /absolute/font-seed.json --discover-dependencies --conditional-gaps --format json
typst-lint preview-compile --root /absolute/project --entrypoint main.typ \
  --manifest /absolute/font-seed.json --discover-dependencies --conditional-gaps --conditional-punctuation --format json
typst-lint preview-compile --root /absolute/project --entrypoint main.typ \
  --manifest /absolute/font-seed.json --discover-dependencies --conditional-gaps --conditional-spacers --format json
typst-style compile --preview --root /absolute/project --entrypoint main.typ \
  --manifest /absolute/font-seed.json --discover-dependencies --format json
typst-style compile --preview --root /absolute/project --entrypoint main.typ \
  --manifest /absolute/font-seed.json --discover-dependencies --review-edits --format json
typst-lint preview-compile --root /absolute/project --entrypoint main.typ \
  --manifest /absolute/resources.json --pdf-stdout > /path/to/new-preview.pdf
```

The opt-in `--conditional-gaps` profile appends source-local nested math
footnote and proof gap stages to the standard sequence. Every step is replayed
from the captured source with the same profile, hash and suppression checks;
the final candidate is reanalyzed against the affected rules. These stages
remain conditional on runtime binding and execution, and do not grant source
application authority. Their rules remain in `uninspected_rule_ids` for full
coverage. Adding `--conditional-punctuation` explicitly appends the separate
prose-footnote punctuation-spacing stage; it requires `--conditional-gaps`.
Its binding, execution and rendering remain conditional as well.

`--conditional-spacers` requires `--conditional-gaps` and appends removal of
the exact `#dots_space` token before an unaligned nested display footnote. It
can also be combined with `--conditional-punctuation`. The source-local
resolver must identify the footnote as the built-in; an unresolved or shadowed
footnote with a proposed edit withholds that source's whole plan. Spacer binding,
include-site execution and rendered context remain unproved. The candidate is
compiled privately; PDF differences are reported and may be intentional under
the selected style policy. They do not authorize applying edits. This stage does
not claim complete inspection, and the rule remains in `uninspected_rule_ids`.

The `typst-style compile --preview` form explicitly selects the same preview
engine and cancellation handling. It does not accept an ordinary compiler tail
or an output path. The option must precede `--`; a `--preview` appearing in the
ordinary inert compiler tail does not select this route. Without this opt-in,
the existing compile gate continues to refuse incomplete coverage without
invoking the compiler. Preview success is not normal build-gate success.

The report lists the three partially inspected rules and the other catalog rules
not inspected by this route. `source_apply_gate.eligible` is always false: global
rule coverage and future dependency closure are incomplete, current live-file
freshness is not proved, and this route has no protected source-write transaction.
If the preview PDFs differ, that is an additional blocker.

`--review-edits` includes each captured source's baseline and candidate hashes
and the same ordered edit stages independently replayed for the compiler. Each
stage's byte offsets apply to its own baseline hash, not the original file after
earlier stages. Unchanged files have no steps; their unresolved display contexts
are not counted. Replacement text can include user content. The JSON review is
bounded to 16 MiB, is output data rather than write authority, and cannot establish
that live files remain unchanged after capture. It requires `--format json` or
`--pdf-stdout` (where the report goes to stderr).

The JSON report describes the preview; it does not contain PDF bytes. With
`--pdf-stdout`, stdout contains only the candidate PDF and stderr contains JSON
metadata. Shell redirection is controlled by the caller and can overwrite an
existing destination, so choose a new path outside the consumer project. Both
successful preview forms exit 2 because global lint coverage remains incomplete;
`phase=preview` and `pdf_generated=true` distinguish a completed preview from
an input or compiler refusal.

Warnings or other compiler stderr still refuse the preview. For this refusal,
the report includes `compiler_stage` (`discovery`, `baseline` or `candidate`), the observed
`compiler_invoked` value and `compiler_diagnostics`: up to the first 8192 stderr
bytes decoded as UTF-8 with replacement, like compiler failures. Diagnostics
may contain source excerpts or private paths; they are report data, not proof of
live source state. Text output prints them, while `--pdf-stdout` keeps all report
data on stderr and emits no PDF for a refused preview. The workspace and output
identities are forcibly rechecked before diagnostics can be reported. The same
checks precede compiler failure diagnostics when a compile may have started;
a confirmed prelaunch cancellation preserves its original failure. Cleanup
and safety failures retain precedence over compiler diagnostics.

`compiler_invoked` combines actual discovery, baseline and candidate compile
starts, and `compiler_stages_started` gives those three states individually.
`compiler_version_probe_invoked` separately reports version probes. These are
tri-state values: null means that the available launch evidence cannot decide.
On process failure, `compiler_stage` is set only for a recognized compile
invocation purpose and is otherwise null. A started version probe alone does
not mean that a document compile started.

The caller supplies every resource explicitly. For example, the following is a
minimal manifest for a dependency-free document; real projects must also list
all included sources and compiler-observed imports, packages, images, data,
WASM and required font files:

```json
{
  "roots": {
    "project": "/absolute/project",
    "packages": "/absolute/local-packages",
    "cache": "/absolute/preview-packages"
  },
  "files": [
    {"root": "project", "path": "main.typ", "role": "source"}
  ],
  "allow_apple_asset_fonts": false
}
```

Alternatively, `--discover-dependencies` accepts a seed manifest with explicit
roots and selected fonts (and optionally other explicit files). It first compiles
the live baseline read-only in the same network-denied, outside-write-denied
sandbox, using only the selected font files and embedded fonts. It adds that
run's observed dependency paths and the literal-include inventory to the file
list, then captures the resulting resources. Selected font hashes must remain
unchanged, and the captured baseline must reproduce both the discovery run's
PDF hash and its observed dependency membership before candidate compilation.
The seed `files` list may be empty when embedded fonts are sufficient. Discovery
observes the current execution only; it neither selects system fonts for the
caller nor proves unexecuted branches, future inputs, network effects or all OS
reads. Stale explicitly listed files still cause a refusal.

The `project` root must equal `--root`; all root values and the manifest path
are canonical absolute paths. File paths are canonical relative to their root.
For Apple MobileAsset fonts, use the exact asset root and opt in as described in
[DOCUMENT_RESOURCE_CAPTURE_CONTRACT.md](DOCUMENT_RESOURCE_CAPTURE_CONTRACT.md).
Project font files must be under `fonts/` and marked `font`; external font files
must also be explicitly marked `font`. The preview disables system-font discovery
and searches selected external font files before captured project fonts. Embedded
fonts remain part of the pinned Typst binary. Caller-provided font selections
may differ from the normal build's selection.

The compiler is the existing hash-pinned macOS Typst 0.15.1 binary, copied and
version-checked in the temporary workspace. A fixed macOS sandbox denies network
access and writes outside that workspace. The environment is constructed without
inherited caches, credentials or font settings. Time, stream, workspace, PDF and
dependency output are bounded; warnings, missing captured resources, unexpected
workspace entries or changed observed dependency membership refuse the preview.
SIGINT/SIGTERM request cancellation and the executable reaps its compiler child
before reproducing the signal. An interrupted stdout stream can be incomplete.

The two PDFs and their actual compiler-observed dependency membership are
compared. Equal PDF hashes establish equality only for those two captured-input
preview runs. The compiler's dependency list is not exhaustive OS-read tracing,
and a successful preview does not establish future dynamic paths, complete rule
coverage, a source-write transaction, or publication authority. System libraries
and the operating system remain trusted runtime components.
