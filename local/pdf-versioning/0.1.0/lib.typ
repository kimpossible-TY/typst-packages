// PDF version-check link helpers for generated Typst documents.

#let version-check-url(base-url, branch, built-at) = {
  base-url + "/?pdfBranch=" + branch + "&pdfBuiltAt=" + built-at
}

#let pdf-url(base-url) = base-url + "/main.pdf"

#let pdf-version-links(
  branch,
  built-at,
  base-url,
  source-url,
  text-size: 8.5pt,
  fill: auto,
) = {
  text(size: text-size, fill: fill)[
    Version: #branch @ #built-at \
    #link(version-check-url(base-url, branch, built-at))[Check latest version] | #link(pdf-url(base-url))[Download latest PDF] | #link(source-url)[Source]
  ]
}
