#import "@local/math-book:0.2.0": *
#import "@local/math-blocks:0.2.0": *
#import "@local/scoped-annotations:0.3.0": *
#import "@local/cetz-helpers:0.2.0": sample-function
#import "@local/fletcher-helpers:0.1.0": themed-diagram
#import "@preview/fletcher:0.5.8": node, edge
#import "@preview/cetz:0.4.2"
#import "@preview/mannot:0.4.0": mark

#let theme = if sys.inputs.at("theme", default: "light") == "dark" { dark-theme } else { light-theme }
#show: apply-math-book.with(theme: theme, title: "Package integration", running-header: true)

#assert.eq(sample-function(x => x * x, -1, 1, segments: 2), ((-1.0, 1.0), (0.0, 0.0), (1.0, 1.0)))

#assert.eq(prefixed-numbering("P", single-chapter: true, 1), "P")
#assert.eq(prefixed-numbering("S", single-chapter: true, 1, 2, 3), "S.2.3")

#book-part(prefix: "P", single-chapter: true)[
  = Single Preliminaries
  == First Section
  #definition[Single chapter definition.] <single-prelim>
  $ x = 1 $ <single-equation>
  #book-part(prefix: "N")[
    = Nested Part
    == Nested Section
    #definition[Nested definition.] <nested-definition>
  ]
  #definition[Restored function formatter.] <restored-definition>
]
#book-part(prefix: "S", single-chapter: true)[
  = Single Supplement
  == First Section
  #definition[Single supplement definition.] <single-supplement>
  == Second Section
  #definition[Reset supplement definition.] <single-supplement-second>
  References: @single-prelim; @single-equation; @single-supplement.
]

#book-part(prefix: "P", center-sections: true)[
  = Preliminaries
  == First Section
  === Nested Heading
  #definition[First prefixed definition.] <prefixed-first>
  $ a = 1 $ <equation-first>
  First footnote.#footnote[First.]
  == Second Section
  #definition[Second prefixed definition.] <prefixed-second>
  $ b = 2 $ <equation-second>
  Second footnote.#footnote[Second.]
]

#book-part(prefix: "S")[
  = Supplement
  == First Section
  #definition[Supplement definition.] <supplement-first>
]

#book-part[
  = Main Chapter
  == First Section
  #definition[Main definition.] <main-first>
  $ c = 3 $ <equation-main>
  Cross-part references: @prefixed-first; @prefixed-second; @supplement-first; @main-first.
  Equation references: @equation-first; @equation-second; @equation-main.
  == Second Section
  #definition[Main second section.] <main-second>
]

// Check counters at their targets, including resets and restored part state.
#context {
  for (tag, expected) in (
    (<single-prelim>, "P.1.1"), (<nested-definition>, "N.1.1.1"),
    (<restored-definition>, "P.1.2"), (<single-supplement>, "S.1.1"),
    (<single-supplement-second>, "S.2.1"),
    (<prefixed-first>, "P.1.1.1"), (<prefixed-second>, "P.1.2.1"),
    (<supplement-first>, "S.1.1.1"), (<main-first>, "1.1.1"), (<main-second>, "1.2.1"),
  ) {
    let target = query(tag).first()
    let loc = target.location()
    let h = counter(heading).at(loc)
    assert.eq(numbering(heading-numbering-style.at(loc), h.at(0), h.at(1), target.counter.at(loc).first()), expected)
  }
  assert.eq(heading-numbering-style.get(), "1.1.1")
  for tag in (<equation-first>, <equation-second>, <equation-main>) {
    assert.eq(counter(math.equation).at(query(tag).first().location()).first(), 1)
  }
  for note in query(footnote) {
    assert.eq(counter(footnote).at(note.location()).first(), 1)
  }
}

// Two automatic parent scopes may reuse the same names without label collisions.
#for i in range(2) {
  local-tag-scope(s => [
    $ x = #i $ #(s.tag)("same-name")
    Local reference: #(s.ref)("same-name").
    #mannot-scope(m => [
      #let marker = (m.mark)("x")
      $ marker(x) + 1
        #(m.annot)("x", cetz, {
          import cetz.draw: *
          line((m.node)("x", "south"), (rel: (0, -0.2)), stroke: theme.rule)
        })
      $
    ], parent: s, name: "nested")
  ])
}

// The modern API and compatibility API share the same annotation implementation.
#local-scope-annotations(s => [
  #let marker = (s.mark)("x")
  $ marker(x)
    #annot-cetz-local((s.tag)("x"), cetz, {
      import cetz.draw: *
      line((s.anchor)("x", "south"), (rel: (0, -0.2)), stroke: theme.rule)
    })
  $
])

#themed-diagram(
  spacing: 15mm,
  node-inset: 6pt,
  node((0, 0), [$V$]), node((1, 0), [$W$], fill: theme.callouts.tip.bg),
  edge((0, 0), (1, 0), "->", [$T$]),
)
