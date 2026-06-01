#import "@local/math-blocks:0.1.0": *
#import "@local/text-utils:0.1.0": apply-paragraph-tabs

#let apply-math-book(
  body,
  theme: light-theme,
  title: none,
  author: none,
  description: none,
) = {
  set par(justify: true)
  set text(font: "Times New Roman", size: 12pt, fill: theme.text)
  set table(stroke: theme.rule)
  set page(
    fill: theme.page,
    margin: auto,
    footer: context [
      #align(right)[
        #counter(page).display("1") / #counter(page).final().last()
      ]
    ],
  )

  if title != none or author != none or description != none {
    set document(
      title: title,
      author: author,
      description: description,
    )
  }

  show: apply-paragraph-tabs
  body
}
