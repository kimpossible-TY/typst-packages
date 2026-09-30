# Project reference exporter

The Typst package exposes only `project-ref("pde", <global-label>)`. This tool
prepares the shared catalog used by that function.

Run `export_references.py SOURCE --project KEY --title TITLE --pdf-url URL` from
any directory. It updates the repository's
`local/project-references/0.1.0/projects.json` by default. `--catalog PATH` can
select a different output catalog, including an installed package's catalog.
Use the source PDF build's `--font-path`, `--input`, `--root`, and
`--package-path` options. The exporter requires Typst 0.15 or later and Python 3.

After export, run `scripts/install-local.sh` and compile the consumer. A catalog
update replaces that project's entries, removing stale labels while preserving
other projects. Publish the matching PDF and refreshed shared package catalog
together when distributing notes.
Run exports sequentially when updating the same catalog.

Duplicate labels are excluded with a warning and stored in `ambiguous_labels`.
The consumer reports an ambiguous-label error instead of selecting an arbitrary
target. Give duplicate source targets unique labels and export again.

The exporter compiles the original source for labeled target positions, then a
temporary sibling copy with `probe.typ` appended for fully rendered reference
text. No source edits or numbering reconstruction are needed. Only numbered
headings, figures, equations, and footnotes with global labels are exported.
`scoped-annotations:0.3.0` emits invisible scope markers so explicitly named local
scopes are excluded as well as automatically named scopes. The probe captures
text and spaces inside hidden overlays and validates their target positions
against the original layout. The registry stores physical pages and coordinates;
the consumer links to `pdf_url#page=N`.

References are exported as plain text. A decorative drawing or non-textual math
glyph inside a custom reference is not a supported reference string. Keep the
source's reference rule textual; ordinary strong/emphasis styling is supported.
For casing, return a transformed string such as `upper("eq.")` in the source
rule. Layout-time shaping such as `upper[eq.]` is visual styling and does not
change the exported content string.
