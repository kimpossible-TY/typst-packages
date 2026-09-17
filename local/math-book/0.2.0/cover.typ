#import "@local/math-blocks:0.2.0": theme-from-text-fill

// Front/back cover. Content, bibliography, notation, and outline stay with the caller.
#let book-cover(
  title: [], subtitle: [], author: [], affiliation: none,
  back-content: none, back-footer: none,
  year: datetime.today().display("[year]"),
  theme: auto, margin: (x: 2in, y: 2in), font: "New Computer Modern",
) = context {
  let theme = if theme == auto { theme-from-text-fill() } else { theme }
  set page(margin: margin, fill: theme.page, footer: none, header: none)
  set par(justify: false)
  v(1fr)

  align(center)[
    // Top decoration
    #line(length: 100%, stroke: (thickness: 2pt, paint: theme.rule))
    #v(0.2em)
    #line(length: 100%, stroke: (thickness: 0.5pt, paint: theme.rule))

    #v(2.5em)

    // Title
    #text(font: font, size: 38pt, weight: "bold", fill: theme.rule)[
      #title
    ]

    #v(2.5em)

    // Bottom decoration
    #line(length: 100%, stroke: (thickness: 0.5pt, paint: theme.rule))
    #v(0.2em)
    #line(length: 100%, stroke: (thickness: 2pt, paint: theme.rule))

    #v(4em)

    // Subtitle
    #text(font: font, size: 16pt, style: "italic", fill: theme.subtle-text)[
      #subtitle
    ]

    #v(5em)

    // Author
    #text(font: font, size: 20pt)[#strong(author)]
  ]

  if back-content != none or affiliation != none or back-footer != none {
    pagebreak()

    align(center)[
      #v(1fr)

      #text(font: font, size: 22pt, fill: theme.muted-text)[
        #set math.equation(numbering: none)
        #back-content
      ]

      #v(1.5fr)

      // Publisher / Affiliation
      #text(font: font, size: 14pt, weight: "bold", tracking: 0.1em, fill: theme.rule)[
        #affiliation
      ]
      #v(0.5em)
      #text(font: font, size: 12pt, fill: theme.muted-text)[
        #year
      ]
      #v(1em)
      #back-footer
    ]
  }
  pagebreak()
}
