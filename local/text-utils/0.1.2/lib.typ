// Text helpers shared by mathematical notes and book templates.

// A special private Unicode character used to represent a paragraph tab.
#let paragraph-tab = "\u{F000}"
#let paragraph_tab = paragraph-tab

// Show rule that parses paragraph tabs (paragraph-tab) in the document body.
// When a paragraph-tab is followed by a lowercase letter, it automatically
// capitalizes that letter and adds an indent of 1.5em.
// Otherwise, it simply replaces the tab with a 1.5em horizontal spacing.
#let apply-paragraph-tabs(body) = {
  // Pattern: tab followed by optional whitespace and a lowercase letter
  show regex("\u{F000}\s*(\p{Ll})"): it => {
    let char = it.text.match(regex("\u{F000}\s*(\p{Ll})")).captures.first()
    h(1.5em) + upper(char)
  }

  // Pattern: standalone tab
  show regex("\u{F000}"): h(1.5em)

  body
}

// Highlights flowing prose, or one explicitly selected inline phrase.
// Use adjacent flow / phrase / flow calls rather than nesting the modes.
#let highlighted(body, mode: "flow") = context {
  assert(("flow", "phrase").contains(mode),
    message: "highlighted: mode must be \"flow\" or \"phrase\".")

  let fill = if text.fill == rgb("#e8edf3") {
    rgb("#665c1f")
  } else {
    rgb("#FFFE80")
  }
  let body = if type(body) == str { [#body] } else { body }

  // Preserve the original inline equation and its inherited math styles.
  // Natural glyph bounds reserve room for tall formulas instead of clipping
  // them to the prose's height or painting over the following line.
  let natural-equations(body, fill: none) = {
    show math.equation.where(block: false): it => box(
      fill: fill,
      inset: if fill == none { 0pt } else { (x: 0.05em, y: 0.08em) },
    )[
      #set text(top-edge: "bounds", bottom-edge: "bounds")
      #it
    ]
    body
  }

  if mode == "flow" {
    highlight(
      fill: fill, top-edge: "ascender", bottom-edge: "descender",
      natural-equations(body, fill: fill),
    )
  } else {
    // One box moves as a unit in its paragraph; its contents can still wrap.
    // Include the prose's descenders, including larger text at the end.
    box(
      fill: fill, radius: 0.08em, inset: (x: 0.15em, y: 0.1em),
    )[
      #set text(top-edge: "ascender", bottom-edge: "descender")
      #natural-equations(body)
    ]
  }
}

// Formats a title by capitalising each word.
//
// Parameters:
//   - title: The input title content or string to capitalize.
#let capitalize-title(title) = {
  if title != none {
    // Capitalize single lowercase words
    show regex("\b\p{Ll}+\b"): it => {
      let t = it.text
      upper(t.at(0)) + t.slice(1)
    }

    // Capitalize acronyms or uppercase words by keeping first letter uppercase and lowercasing the rest
    show regex("\b\p{Lu}{2,}\b"): it => {
      let t = it.text
      upper(t.at(0)) + lower(t.slice(1))
    }

    // Capitalize letters immediately following a number (e.g. 1a -> 1A)
    show regex("\d\p{Ll}"): it => {
      it.text.at(0) + upper(it.text.at(1))
    }

    title
  }
}

// Checks if the given title is purely numeric (e.g. "1.1", "2.1.3").
// Useful for distinguishing numbered theorems/sections from named ones.
//
// Parameters:
//   - title: The title string or content to verify.
#let is-numeric-title(title) = {
  if title == none { return true }

  let text = if type(title) == str {
    title
  } else if type(title) == content and title.has("text") {
    title.text
  } else {
    return false
  }

  text.match(regex("^[0-9.]+$")) != none
}
