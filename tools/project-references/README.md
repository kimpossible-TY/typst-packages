# Project reference exporter

The Typst package exposes only `project-ref("pde", <global-label>)`. This tool
prepares the shared catalog used by that function.

For CI consumers, verify_references.py checks literal calls in a source tree,
the installed catalog SHA-256, matching published PDF bytes/pages, and optionally
the compiled consumer's reference text and PDF link destinations. It requires
pypdf (CI currently pins 6.10.0). Each verified project's published_pdf
entry records sha256, pages, and an HTTPS download_url returning PDF bytes.
After re-exporting a project, verify the matching published PDF and renew this
entry; the exporter replaces the old project snapshot. Compile and export with
identical source/build options before publication. A successful export alone
does not establish that the URL serves that build.

~~~sh
python3 tools/project-references/verify_references.py \
  --catalog /path/to/packages/local/project-references/0.1.0/projects.json \
  --catalog-sha256 VERIFIED_CATALOG_SHA256 \
  --source-root /path/to/consumer \
  --published-dir /tmp/verified-source-pdfs \
  --consumer-pdf /path/to/consumer/main.pdf
~~~

Use a fresh published-PDF directory for each CI run. The first invocation
downloads and verifies the sources; later checks in the same run reuse those
verified bytes. This does not change the two-argument Typst API or make Typst
fetch external sources during compilation. PDF page-fragment support still
depends on the destination viewer.

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
