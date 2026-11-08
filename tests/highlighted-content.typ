#import "@local/text-utils:0.1.2": highlighted

// A single text element has no children field. This must render without panic.
#highlighted[A short explanation.]

// Preserve support for mixed content and a single inline equation.
#highlighted[The variable $x$ is positive.]
#highlighted[$x > 0$]

// A plain string body must also work.
#highlighted("A plain string.")

// Inner size scopes must grow/shrink the background with their text.
#for size in (8pt, 10pt, 12pt, 18pt, 24pt) {
  block(highlighted(text(size: size)[Agjp Render test]))
}

#highlighted[
  Normal #text(size: 1.6em)[LARGE] #text(size: 0.6em)[small] end.
]
#highlighted[
  A #strong[bold] and #emph[italic] #text(size: 1.6em)[larger] word.
]
#block(width: 7cm)[
  #highlighted[
    This highlighted sentence wraps across several lines and includes
    #text(size: 1.6em)[larger words] and a short ending.
  ]
]
