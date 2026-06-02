# PDF Versioning

Reusable helpers for Typst PDF projects that publish a `main.pdf` through a
static site such as GitHub Pages.

## Files Generated In A Consumer Project

- `build-info.typ`: imported by the Typst document to render version links.
- `version.json`: read by the web page to decide whether an opened PDF is current.

## Local Server

```sh
pdf-versioning --root . --port 8767 --watch-version
```

The server creates `build-info.typ` and `version.json` if they are missing. Once
running, it updates both files only when a tracked `.typ` source changes.

## One-Time Metadata Write

```sh
pdf-versioning --root . --write-version-once
```

Use this in CI before compiling, then run it again after compiling so
`version.json` records the final PDF hash and size.

## Install CLI

From the `typst-packages` repo:

```sh
./scripts/install-tools.sh
```

This installs a `pdf-versioning` command into `~/.local/bin` by default. Set
`PDF_VERSIONING_BIN_DIR` to choose another install directory.

## HTML Helper

Serve `pdf-version-check.js` through `pdf-versioning`, then mount it from the
page:

```html
<script src="pdf-version-check.js"></script>
<script>
  PdfVersionCheck.mount({
    statusId: "version-status",
    downloadId: "btn-download",
    previewId: "pdf-preview"
  });
</script>
```
