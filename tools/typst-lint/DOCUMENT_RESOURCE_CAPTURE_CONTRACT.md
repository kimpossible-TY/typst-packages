# Explicit document resource capture

`document_resources.capture` is an internal read-only experiment for capturing
caller-selected binary resources under explicit absolute roots. It does not
discover dependencies, inspect fonts, run Typst, write files, or establish a
complete resource closure. Its result is not compiler or write authority.

Every `ResourceRequest` has an explicit root ID, canonical relative path and
role (`source`, `package`, `resource` or `font`). The existing bounded no-follow
binary reader rejects symbolic links, non-regular or multiply linked files,
unsafe mode/owner and changes while reading. A second verification of every
captured file detects changes observed during the batch. This is a sampled
consistency check, not an atomic filesystem snapshot or a live lock. Duplicate,
case/NFC-colliding paths and repeated file identities refuse the batch. The
fixed bounds are 512 files, 64 MiB per file, 192 MiB aggregate bytes, 64 path
components and 128 KiB aggregate relative path bytes. Font bytes remain in
memory and are never serialized by this module.

An issued frozen capture retains hashes, sizes, original file/parent evidence
and immutable bytes. `assert_capture` rejects copies and altered issued data;
it does not reread the filesystem. `stage_document` accepts an original issued
include context and a complete source preview sequence, verifies that every
captured source exactly matches the project-root resource bytes, and combines
the recomputed candidate sources with the other captured bytes in an immutable
in-memory mapping. No source or resource is written. Missing or mismatched source
bytes, stale/tampered plans and unissued captures refuse the whole mapping.

Local characterization of an owned PDE copy captured 193 explicitly selected
files: 177 compiler-observed dependencies and 16 project font files. A private
minimal copy built only from those bytes compiled to the same PDF as the larger
copy, and its candidate compiled to the same PDF as its baseline. These are
observations for that snapshot. The compiler's dependency list is not exhaustive
OS-read tracing, system fonts were not captured, dynamic execution and future
inputs may select other resources, and no general rendering-equivalence claim is
made. Compiler integration requires its own checked resource closure, runtime
and process lifecycle; this module deliberately does not widen existing compiler
or publication authority.
