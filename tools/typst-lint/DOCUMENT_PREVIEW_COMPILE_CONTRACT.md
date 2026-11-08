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
  --manifest /absolute/resources.json --pdf-stdout > /path/to/new-preview.pdf
```

The JSON report describes the preview; it does not contain PDF bytes. With
`--pdf-stdout`, stdout contains only the candidate PDF and stderr contains JSON
metadata. Shell redirection is controlled by the caller and can overwrite an
existing destination, so choose a new path outside the consumer project. Both
successful preview forms exit 2 because global lint coverage remains incomplete;
`phase=preview` and `pdf_generated=true` distinguish a completed preview from
an input or compiler refusal.

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
