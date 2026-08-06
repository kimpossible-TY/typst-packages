#import "@local/math-blocks:0.1.0": *
#import "@local/text-utils:0.1.1": apply-paragraph-tabs

// Sets up standard book-style configurations and themes for mathematical documents.
// It configures page margins, background, running footers, default typography (Times New Roman),
// document metadata, and text indentation handlers.
//
// Parameters:
//   - body: The document content to format.
//   - theme: A theme configuration dictionary containing colors for text, background, and rules.
//   - title: Document title metadata.
//   - author: Document author metadata.
//   - description: Document description/abstract metadata.
#let apply-math-book(
  body,
  theme: light-theme,
  title: none,
  author: none,
  description: none,
) = {
  // Justify paragraphs for clean reading layout
  set par(justify: true)

  // Configure base typography font, size and color matching the theme
  set text(font: "Times New Roman", size: 12pt, fill: theme.text)

  // Style tables with theme-appropriate rules
  set table(stroke: theme.rule)

  // Configure page properties including margins and page number progress indicators in the footer
  set page(
    fill: theme.page,
    margin: auto,
    footer: context [
      #align(right)[
        #counter(page).display("1") / #counter(page).final().last()
      ]
    ],
  )

  // Set document metadata if provided
  if title != none or author != none or description != none {
    set document(
      title: title,
      author: author,
      description: description,
    )
  }

  // Parse paragraph tabs to format indents and capitalization
  show: apply-paragraph-tabs
  body
}
