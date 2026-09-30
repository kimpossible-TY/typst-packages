#import "@local/math-book:0.2.0": *
#import "@local/math-blocks:0.2.0": *
#import "@local/scoped-annotations:0.3.0": local-tag-scope

#show: apply-math-book.with(title: "Reference Source", font: "New Computer Modern")
#set math.equation(supplement: "eq.")

// A custom rule with ordinary spaces must survive export unchanged.
#show ref.where(target: <custom-text>): it => [plain reference 1]
#show ref.where(target: <custom-linked>): it => context {
  let element = it.element
  if element == none { it } else {
    link(element.location(), [custom eq. #counter(math.equation).at(element.location()).first()])
  }
}

#book-part(prefix: "P", single-chapter: true)[
  = Preliminaries
  == First Section
  $ x = 1 $ <prelim-equation>
]
#book-part(prefix: "S", single-chapter: true)[
  = Supplement
  == First Section
  #theorem[A global theorem.] <supplement-theorem>
  $ y = 2 $ <supplement-equation>
]
#pagebreak()
#book-part[
  = Main Chapter <main-chapter>
  == First Section <main-section>
  $ E = m c^2 $ <energy>
  $ z = 3 $ <custom-text>
  $ w = 4 $ <custom-linked>
  #heading(level: 3, numbering: none)[Unnumbered] <unnumbered>
  #figure(rect(width: 1cm, height: 1cm), caption: [A global figure.]) <global-figure>
  #local-tag-scope(s => [
    $ a = 5 $ #(s.tag)("automatic-energy")
  ])
  #local-tag-scope(s => [
    $ b = 6 $ #(s.tag)("energy")
  ], prefix: "explicit")
  #local-tag-scope(s => [
    $ c = 7 $ #(s.tag)("energy")
  ], namespace: "custom-local")
]
