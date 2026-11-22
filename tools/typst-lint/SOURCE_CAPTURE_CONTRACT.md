# Selected-source capture contract

The read-only linter captures only `.typ` and `.md` inputs chosen by the existing
root-relative include/exclude rules. Selection order, directory exclusions and
the conservative rejection of unexcluded symlink directories are unchanged.
Excluded files are not read. No import/include graph, sibling-project discovery,
package cache, network source or compiler resource capture is added.

## Bytes and evidence

Each internal selected-source record is frozen and retains immutable UTF-8 bytes
and the SHA-256 derived from those exact bytes. BOMs and newline forms are not
normalized. The collection is a sorted tuple. `collect()` and report JSON retain
their existing metadata-only records: `path`, `sha256` and byte count `bytes`.
Raw source bytes and internal capture records are not added to JSON.

Capture and before-parse rereads use the existing explicit-file snapshot reader.
Every directory component is opened without following symlinks. Inputs must be
regular, single-link files. The reader uses descriptor-relative no-follow,
nonblocking opens, checks identity and metadata, bounds streamed bytes, and
verifies content by rereading the original retained descriptor. Handles close
after each file snapshot, including on failure. An unchecked `Path.read_bytes()`
is never used for selected-source bytes.

Before parsing each selected Typst input, a second safe snapshot must equal its
captured bytes and hash. The parser receives the original captured bytes, not the
reread buffer. A changed or unsafe reread produces unsupported per-file coverage
without findings or proposals from that file. Configured entrypoints retain their
additional separate snapshot, selection-consistency check and protected reread;
book evidence and parsed bytes must agree. Markdown remains unsupported for
extraction and rule analysis, but receives the same initial safe capture.

## Resource and compatibility boundaries

The parser's actual 1 MiB limit applies to every selected input, including
Markdown. A file larger than 1,048,576 bytes fails at input collection before
parsing; it is never truncated or silently omitted. This intentionally changes
oversized Typst files from later unsupported parse coverage to input-stage
failure, and adds a bound to previously unbounded selected Markdown reads.

At most 64 MiB (67,108,864 bytes) of selected-source payload is retained per
collection. A file that would exceed that aggregate cap fails the whole input
collection. Empty selected files remain allowed at the exact byte cap. The
aggregate cap excludes per-file read/verification buffers, which remain bounded
by the 1 MiB file limit, and metadata/tree objects. It is not a total-process
memory guarantee or a new limit on the number of selected files.

Invalid selected UTF-8 now raises an explicit `InputError` containing a UTF-8
reason instead of exposing a direct `UnicodeError` from `collect()`. Input
failures remain CLI status 2 with structured JSON. Check, fix, dry-run and compile
gate modes remain read-only; none enable source writes or compiler invocation.

These per-file snapshots do not constitute an atomic directory/dependency graph,
an editor lease, a transaction baseline or evidence that a later compiler sees
the same resources. Concurrent changes may prevent collection or analysis.
Consumer configuration and the bundled rule catalog retain their existing read
paths in this change. The optional reference catalog already uses its separate
bounded descriptor-verified loader.

## Verification

`tests/test_source_capture.py` uses only isolated test-owned temporary files for
source, ancestor, symlink, hardlink, FIFO, growth and mutation races. It checks
retained-descriptor rereads, exact and excessive resource limits, immutable
evidence, unchanged selection/JSON/BOM/newlines, parse-source identity and
status-2/no-write behavior. Foundation and book integration tests retain their
existing behavior assertions with capture-aware injection points.
