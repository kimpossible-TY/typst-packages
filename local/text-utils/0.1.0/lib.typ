// Text helpers shared by mathematical notes and book templates.

#let paragraph-tab = "\u{F000}"
#let paragraph_tab = paragraph-tab

#let apply-paragraph-tabs(body) = {
  show regex("\u{F000}\s*(\p{Ll})"): it => {
    let char = it.text.match(regex("\u{F000}\s*(\p{Ll})")).captures.first()
    h(1.5em) + upper(char)
  }

  show regex("\u{F000}"): h(1.5em)

  body
}

#let highlighted(body) = context {
  let theme = if text.fill == rgb("#e8edf3") {
    (highlight: rgb("#665c1f"))
  } else {
    (highlight: rgb("#FFFE80"))
  }

  for child in body.children {
    if repr(child.func()) == "equation" {
      box(
        fill: theme.highlight,
        outset: (y: 0.25em),
      )[$#child.at("body")$]
    } else {
      highlight(child)
    }
  }
}

#let capitalize-title(title) = {
  if title != none {
    show regex("\b\p{Ll}+\b"): it => {
      let t = it.text
      upper(t.at(0)) + t.slice(1)
    }

    show regex("\b\p{Lu}{2,}\b"): it => {
      let t = it.text
      upper(t.at(0)) + lower(t.slice(1))
    }

    show regex("\d\p{Ll}"): it => {
      it.text.at(0) + upper(it.text.at(1))
    }

    title
  }
}

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
