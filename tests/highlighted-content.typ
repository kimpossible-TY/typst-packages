#import "@local/text-utils:0.1.2": highlighted

// A single text element has no children field. This must render without panic.
#highlighted[A short explanation.]

// Preserve support for mixed content and a single inline equation.
#highlighted[The variable $x$ is positive.]
#highlighted[$x > 0$]

// A plain string body must also work.
#highlighted("A plain string.")
#highlighted()[A parenthesized call with a content body.]
#highlighted("A plain string phrase.", mode: "phrase")
#highlighted(mode: "phrase")[$x > 0$]

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

// Nested equations retain their styles and receive natural glyph bounds.
#highlighted[
  *Bold $x^2$* and _italic $1/(1+x^2)$_ with
  #link("https://example.com")[$sqrt(1+x^2)$],
  $(a/(b+c))/(1+d^2)$ and $integral_0^1 f(x) dif x$.
]

// The last large descenders must remain inside the phrase background.
#highlighted(mode: "phrase")[Normal #text(size: 1.6em)[Agjp gyqp]]

// Adjacent modes compose without nesting their equation rules or padding.
#block(width: 220pt)[
  #highlighted[This explanation contains ]#highlighted(mode: "phrase")[#text(size: 1.6em)[Agjp] $1/(1+x^2)$]#highlighted[ before continuing with enough ordinary text to wrap over several lines.]
]
