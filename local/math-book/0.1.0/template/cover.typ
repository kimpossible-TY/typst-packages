#import "@local/math-blocks:0.1.0": theme-from-text-fill

// Context block to dynamically draw the book cover page using the active theme.
#context {
  let theme = theme-from-text-fill()

  // Set cover page margins, backgrounds, and disable running headers/footers
  set page(margin: (x: 2in, y: 2in), fill: theme.page, footer: none, header: none)
  set par(justify: false)

  // Push the title text down
  v(1fr)
  align(center)[
    // Top border lines
    #line(length: 100%, stroke: (thickness: 2pt, paint: theme.rule))
    #v(0.2em)
    #line(length: 100%, stroke: (thickness: 0.5pt, paint: theme.rule))
    #v(2.5em)
    
    // Main book title
    #text(font: "New Computer Modern", size: 38pt, weight: "bold", fill: theme.rule)[
      Mathematical Notes
    ]
    #v(2.5em)
    
    // Bottom border lines
    #line(length: 100%, stroke: (thickness: 0.5pt, paint: theme.rule))
    #v(0.2em)
    #line(length: 100%, stroke: (thickness: 2pt, paint: theme.rule))
    #v(5em)
    
    // Author block
    #text(font: "New Computer Modern", size: 20pt)[*Author*]
    #v(1.5fr)
    
    // Display current year
    #text(font: "New Computer Modern", size: 12pt, fill: theme.muted-text)[
      #datetime.today().display("[year]")
    ]
  ]

  // End of cover page; transition to standard page styling and insert Table of Contents
  pagebreak()
  set page(margin: auto, fill: theme.page)
  outline(title: "Contents", depth: 3)
}

