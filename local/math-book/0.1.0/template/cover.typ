#import "@local/math-blocks:0.1.0": theme-from-text-fill

#context {
  let theme = theme-from-text-fill()

  set page(margin: (x: 2in, y: 2in), fill: theme.page, footer: none, header: none)
  set par(justify: false)

  v(1fr)
  align(center)[
    #line(length: 100%, stroke: (thickness: 2pt, paint: theme.rule))
    #v(0.2em)
    #line(length: 100%, stroke: (thickness: 0.5pt, paint: theme.rule))
    #v(2.5em)
    #text(font: "New Computer Modern", size: 38pt, weight: "bold", fill: theme.rule)[
      Mathematical Notes
    ]
    #v(2.5em)
    #line(length: 100%, stroke: (thickness: 0.5pt, paint: theme.rule))
    #v(0.2em)
    #line(length: 100%, stroke: (thickness: 2pt, paint: theme.rule))
    #v(5em)
    #text(font: "New Computer Modern", size: 20pt)[*Author*]
    #v(1.5fr)
    #text(font: "New Computer Modern", size: 12pt, fill: theme.muted-text)[
      #datetime.today().display("[year]")
    ]
  ]

  pagebreak()
  set page(margin: auto, fill: theme.page)
  outline(title: "Contents", depth: 3)
}
