#import "@local/text-utils:0.1.0": capitalize-title, is-numeric-title

#let math_font = "New Computer Modern Math"
#let title_size = 10.8pt

// Shared document palettes. Pick one in the document, then apply it through
// #set page(fill: ...) and #set text(fill: ...).
#let light-theme = (
  page: white,
  text: luma(0%),
  rule: rgb("#2c3e50"),
  subtle-text: rgb("#34495e"),
  muted-text: rgb("#7f8c8d"),
  highlight: rgb("#FFFE80"),
  callouts: (
    note: (bg: rgb("#f8f9fa"), border: rgb("#2c3e50")),
    warning: (bg: rgb("#fef5f5"), border: rgb("#d63031")),
    important: (bg: rgb("#f0ebf8"), border: rgb("#6c5ce7")),
    tip: (bg: rgb("#ebf5e6"), border: rgb("#27ae60")),
    theorem: (bg: rgb("#fdf2f2"), border: rgb("#d9534f")),
    proposition: (bg: rgb("#f0f5ff"), border: rgb("#4a90e2")),
    definition: (bg: rgb("#fffbe6"), border: rgb("#f5a623")),
    lemma: (bg: rgb("#f0fff0"), border: rgb("#50c878")),
    emphasis: (bg: none, border: black),
  ),
)

#let dark-theme = (
  page: rgb("#10151f"),
  text: rgb("#e8edf3"),
  rule: rgb("#94a9c4"),
  subtle-text: rgb("#c9d4e2"),
  muted-text: rgb("#95a3b5"),
  highlight: rgb("#665c1f"),
  callouts: (
    note: (bg: rgb("#18202c"), border: rgb("#89a6c7")),
    warning: (bg: rgb("#2a171b"), border: rgb("#ff8f9a")),
    important: (bg: rgb("#211a32"), border: rgb("#b9a3ff")),
    tip: (bg: rgb("#152517"), border: rgb("#75d58a")),
    theorem: (bg: rgb("#281b20"), border: rgb("#ff9aa2")),
    proposition: (bg: rgb("#152033"), border: rgb("#83b7ff")),
    definition: (bg: rgb("#2a2415"), border: rgb("#f2c166")),
    lemma: (bg: rgb("#142617"), border: rgb("#78d89a")),
    emphasis: (bg: none, border: rgb("#c9d4e2")),
  ),
)

#let theme-from-text-fill() = {
  if text.fill == dark-theme.text {
    dark-theme
  } else {
    light-theme
  }
}

#let callout(
  type: "note",
  title: none,
  inline-title: false,
  body,
) = context {
  let colors = theme-from-text-fill().callouts
  let color-info = colors.at(type, default: colors.note)

  block(
    width: 100%,
    fill: color-info.bg,
    stroke: (left: 3pt + color-info.border),
    inset: (left: 1.2em, right: 1em, top: 1em, bottom: 1em),
    radius: 2pt,
    [
      #if title != none {
        if inline-title {
          text(weight: 600, size: title_size, fill: color-info.border, font: math_font)[#title]
        } else {
          block(width: 100%, below: 0.8em)[
            #text(weight: 600, size: title_size, fill: color-info.border, font: math_font)[#title]
          ]
        }
      }#text(size: 12pt, font: math_font)[#body]
    ],
  )
}

#let heading-numbering-style = state("heading-numbering-style", "1.1")

#let scoped-numbering(item-counter) = {
  context numbering(
    heading-numbering-style.get(),
    counter(heading).get().first(),
    item-counter.get().first(),
  )
}

#let math-block-kinds = ("theorem", "proposition", "lemma", "definition", "note")

#let scoped-figure-numbering(..nums) = {
  let n = nums.pos().first()
  context {
    let h = counter(heading).get().first()
    let style = heading-numbering-style.get()
    numbering(style, h, n)
  }
}

#let math-block-title(label, kind, title) = context {
  let c = counter(figure.where(kind: kind))
  let num = scoped-numbering(c)

  if title != none {
    strong[#label #num (#capitalize-title(title)) : ]
  } else {
    strong[#label #num : ]
  }
}

#let numbered-math-block(kind, label, body, title: none, callout-type: none) = {
  let box-type = if callout-type == none { kind } else { callout-type }

  figure(
    kind: kind,
    supplement: label,
    numbering: scoped-figure-numbering,
    caption: none,
    callout(
      type: box-type,
      title: math-block-title(label, kind, title),
      inline-title: is-numeric-title(title),
      body,
    ),
  )
}

#let reset-math-block-counters() = {
  for kind in math-block-kinds {
    counter(figure.where(kind: kind)).update(0)
  }
}

#let apply-math-block-reset(body) = {
  show heading.where(level: 1): it => {
    reset-math-block-counters()
    it
  }

  body
}

#let theorem(body, title: none) = numbered-math-block("theorem", "Theorem", body, title: title)
#let proposition(body, title: none) = numbered-math-block("proposition", "Proposition", body, title: title)
#let lemma(body, title: none) = numbered-math-block("lemma", "Lemma", body, title: title)
#let definition(body, title: none) = numbered-math-block("definition", "Definition", body, title: title)
#let note(body, title: none) = numbered-math-block("note", "Note", body, title: title)

#let emphasis(body, title: none) = pad(left: 2em, callout(
  type: "emphasis",
  title: if title != none { strong(capitalize-title(title)) } else { none },
  inline-title: is-numeric-title(title),
  body,
))

#let qed = h(1fr) + sym.qed

#let proof(body) = {
  parbreak()
  text(weight: "bold", font: math_font)[Proof. ]
  body
  parbreak()
  qed
}

#let dots_space = {
  $& wide dots.h.c thin$
}

#let flowbox(body) = context {
  let theme = theme-from-text-fill()

  block(
    width: 100%,
    stroke: 1pt + theme.rule,
    inset: (left: 1em, right: 1em, top: 1em, bottom: 1em),
    align(center, body),
  )
}
