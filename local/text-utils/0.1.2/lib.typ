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

// Applies background highlighting to text content.
// Handles both regular inline text and math equations differently.
// For math equations, it wraps them in a colored box with y-outset.
// For normal text, it uses the standard Typst highlight element.
#let highlighted(body) = context {
  // Determine appropriate highlight colors based on the current text color (light vs dark theme)
  let theme = if text.fill == rgb("#e8edf3") {
    (highlight: rgb("#665c1f")) // Dark mode highlight color (muted gold/green)
  } else {
    (highlight: rgb("#FFFE80")) // Light mode highlight color (bright yellow)
  }

  // Keep adjacent text in one highlight element so its background geometry is
  // calculated consistently instead of once per child (or character).
  // Equations are emitted separately because they need extra vertical room.
  let parts = ()
  let text = []
  let has-text = false

  for child in body.children {
    if repr(child.func()) == "equation" {
      if has-text {
        parts.push(highlight(text, fill: theme.highlight))
        text = []
        has-text = false
      }

      parts.push(box(
        fill: theme.highlight,
        outset: (y: 0.25em),
      )[$#child.at("body")$])
    } else {
      text = text + child
      has-text = true
    }
  }

  if has-text {
    parts.push(highlight(text, fill: theme.highlight))
  }

  for part in parts {
    part
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
