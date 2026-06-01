#import "@local/text-utils:0.1.0": *
#import "@local/math-blocks:0.1.0": *
#import "@local/scoped-annotations:0.1.0": local-tag-scope
#import "@local/cetz-helpers:0.1.0": legend_box, description_box
#import "@preview/cetz:0.4.2": *

#let theme = light-theme
#set text(fill: theme.text)
#set page(fill: theme.page)
#set heading(numbering: "1.")

#show: apply-paragraph-tabs

= Smoke

#paragraph-tab
this paragraph should be indented and capitalized.

#definition(title: "basic definition")[
  A reusable definition block.
]

#theorem[
  A reusable theorem block.
]

#proof[
  This is a proof body.
]

#highlighted[
  $ a^2 + b^2 = c^2 $
]

#local-tag-scope(s => [
  #heading(level: 2)[Local Target] #(s.tag)("target")

  Local reference target: #(s.ref)("target").
])

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
