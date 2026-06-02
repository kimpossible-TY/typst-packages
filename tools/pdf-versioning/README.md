# PDF Versioning

Reusable helpers for Typst PDF projects that publish a `main.pdf` through a
static site such as GitHub Pages.

## Files Generated In A Consumer Project

- `build-info.typ`: imported by the Typst document to render version links.
- `version.json`: read by the web page to decide whether an opened PDF is current.

## Local Server

```sh
python3 tools/pdf-versioning/pdf_version_server.py --root . --port 8767 --watch-version
```

The server creates `build-info.typ` and `version.json` if they are missing. Once
running, it updates both files only when a tracked `.typ` source changes.

## One-Time Metadata Write

```sh
python3 tools/pdf-versioning/pdf_version_server.py --root . --write-version-once
```

Use this in CI before compiling, then run it again after compiling so
`version.json` records the final PDF hash and size.

## HTML Helper

Copy or serve `pdf-version-check.js`, then mount it from the page:

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
