#import "@local/text-utils:0.1.0": *
#import "@local/math-blocks:0.1.0": *
#import "@local/scoped-annotations:0.1.0": local-scope-annotations
#import "@local/cetz-helpers:0.1.0": legend_box, description_box
#import "@local/pdf-versioning:0.1.0": pdf-version-links, version-check-url
#import "@preview/cetz:0.4.2": *

// Setup page theme, text color and page background for testing
#let theme = light-theme
#set text(fill: theme.text)
#set page(fill: theme.page)
#set heading(numbering: "1.")

// Apply the paragraph formatting rule
#show: apply-paragraph-tabs

= Smoke

// Verify paragraph tab indentation and capitalization rule works
#paragraph-tab
this paragraph should be indented and capitalized.

// Verify mathematical definition blocks
#definition(title: "basic definition")[
  A reusable definition block.
]

// Verify mathematical theorem blocks
#theorem[
  A reusable theorem block.
]

// Verify proof block format and QED symbol
#proof[
  This is a proof body.
]

// Verify highlighted math block
#highlighted[
  $ a^2 + b^2 = c^2 $
]

// Verify local scoped annotations.
// It assigns a unique local reference to a label/node and verifies
// that references link properly within that localized block.
#local-scope-annotations(s => [
  #heading(level: 2)[Local Target] #(s.tag)("target")

  Local reference target: #(s.ref)("target").
])

// Verify CeTZ legend and description drawings
#canvas({
  import draw: *
  legend_box(
    x: 0,
    y: 0,
    items: (
      (text: [First], stroke: 1pt + red),
    ),
  )
  description_box(x: 0, y: -1.2, width: 4cm, body: [Description])
})

// Verify PDF versioning links compile and expose the expected branch/time URL.
#let check-url = version-check-url(
  "https://example.com/notes",
  "main",
  "2026-06-02T00:00:00Z",
)
#link(check-url)[Version URL]

#pdf-version-links(
  "main",
  "2026-06-02T00:00:00Z",
  "https://example.com/notes",
  "https://example.com/source",
  fill: theme.muted-text,
)
