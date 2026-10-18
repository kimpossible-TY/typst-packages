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

  // Plain strings have no `.has`, so normalize them to content first.
  let body = if type(body) == str { [#body] } else { body }
  let children = if body.has("children") { body.children } else { (body,) }
  let is-eq(child) = repr(child.func()) == "equation"

  // The highlight uses one common extent above and below the baseline: the
  // plain-text ascender/descender, grown to the tallest equation in the body.
  // Every piece then gets the same background height. The background is only
  // painted; it does not change line spacing.
  let base-ascent = measure(text(top-edge: "ascender", bottom-edge: "baseline")[x]).height
  let base-descent = measure(text(top-edge: "baseline", bottom-edge: "descender")[x]).height
  let ascent = base-ascent
  let descent = base-descent
  for child in children.filter(is-eq) {
    let eq = box($#child.at("body")$)
    ascent = calc.max(ascent, measure({
      set text(top-edge: "bounds", bottom-edge: "baseline")
      eq
    }).height)
    descent = calc.max(descent, measure({
      set text(top-edge: "baseline", bottom-edge: "bounds")
      eq
    }).height)
  }

  // Backgrounds of adjacent lines are painted line by line, so a later line
  // would paint over the previous line's equation. Never let the extent exceed
  // the line pitch: backgrounds then at most touch, so the colour stays
  // consistent with no overlap. Extra room beyond the text is shared in
  // proportion to what the equations need.
  let pitch = base-ascent + base-descent + par.leading.to-absolute()
  let extra = (ascent - base-ascent) + (descent - base-descent)
  let room = pitch - base-ascent - base-descent
  if extra > room and extra > 0pt {
    let ratio = room / extra
    ascent = base-ascent + (ascent - base-ascent) * ratio
    descent = base-descent + (descent - base-descent) * ratio
  }

  // Keep adjacent text in one highlight element so its background geometry is
  // calculated consistently instead of once per child (or character).
  // Equations are emitted separately as boxes pinned to the common extent.
  let parts = ()
  let buf = []
  let has-text = false

  let fill-text(content) = highlight(
    content,
    fill: theme.highlight,
    top-edge: ascent,
    bottom-edge: -descent,
  )

  for child in children {
    if is-eq(child) {
      if has-text {
        parts.push(fill-text(buf))
        buf = []
        has-text = false
      }

      // The frame keeps the normal line height (so line spacing is unchanged);
      // only the background is extended with `outset`.
      parts.push(box(
        fill: theme.highlight,
        outset: (top: ascent - base-ascent, bottom: descent - base-descent),
      )[
        #set text(top-edge: base-ascent, bottom-edge: -base-descent)
        #box($#child.at("body")$)
      ])
    } else {
      buf = buf + child
      has-text = true
    }
  }

  if has-text {
    parts.push(fill-text(buf))
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
