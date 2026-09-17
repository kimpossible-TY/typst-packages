#import "@local/math-blocks:0.2.0": *
#import "@local/text-utils:0.1.2": apply-paragraph-tabs
#import "cover.typ": book-cover

// Keep prefixes outside Typst's repeating numbering patterns.
#let prefixed-numbering(prefix, ..nums) = {
  prefix + "." + nums.pos().map(str).join(".")
}

#let prefixed-heading-numbering(prefix, ..nums) = {
  if nums.pos().len() == 0 { [#prefix :] }
  else { [#prefixed-numbering(prefix, ..nums) :] }
}

// A document part owns its heading style and restores the caller's block style.
// Include files inside the body; package code never resolves project file paths.
#let book-part(body, prefix: none, center-sections: false, reset-heading: true) = context {
  let previous = heading-numbering-style.get()
  heading-numbering-style.update(
    if prefix == none { "1.1.1" } else { prefixed-numbering.with(prefix) },
  )
  set heading(numbering: if prefix == none { "1.1 :" } else { prefixed-heading-numbering.with(prefix) })
  show heading.where(level: 2): it => {
    if center-sections { align(center, it) } else { it }
  }
  if reset-heading { counter(heading).update(0) }
  body
  heading-numbering-style.update(previous)
}

// Show the first section on this page, or the latest section in this chapter.
#let section-running-header() = context {
    let page_num = here().page()
    if calc.even(page_num) {
      let h_all = query(selector(heading.where(level: 2)))
      let h_valid = h_all.filter(h => h.location().page() <= page_num)

      if h_valid.len() > 0 {
        let h_on_page = h_valid.filter(h => h.location().page() == page_num)
        let current = if h_on_page.len() > 0 { h_on_page.first() } else { h_valid.last() }

        let ch_all = query(selector(heading.where(level: 1)))
        let ch_here = ch_all.filter(c => c.location().page() <= page_num)
        let ch_heading = ch_all.filter(c => c.location().page() <= current.location().page())

        let same_chapter = false
        if ch_here.len() > 0 and ch_heading.len() > 0 {
          if ch_here.last().location() == ch_heading.last().location() {
            same_chapter = true
          }
        } else if ch_here.len() == 0 and ch_heading.len() == 0 {
          same_chapter = true
        }

        if same_chapter {
          let num = if current.numbering != none {
            let nums = counter(heading).at(current.location())
            if type(current.numbering) == function {
              (current.numbering)(..nums)
            } else {
              numbering(current.numbering, ..nums)
            }
          }
          align(left)[
            #text(size: 10pt, style: "italic")[
              #num #current.body
            ]
          ]
        }
      }
    }
  }

#let book-equation-numbering(..nums) = {
  let headings = counter(heading).get()
  [#("(" + numbering(
    heading-numbering-style.get(),
    headings.at(0, default: 0), headings.at(1, default: 0),
    ..nums.pos(),
  ) + ")")]
}

// Opt-in book layout. Reset levels and equation formatting are configurable.
#let apply-math-book(
  body,
  theme: light-theme,
  title: none,
  author: none,
  description: none,
  font: "Times New Roman",
  size: 12pt,
  margin: auto,
  running-header: false,
  header: auto,
  footer: auto,
  chapter-pages: false,
  chapter-font: "New Computer Modern",
  chapter-size: 25pt,
  equation-numbering: book-equation-numbering,
  equation-reset-levels: (1, 2),
  footnote-reset-levels: (2,),
  reset-math-blocks: true,
) = {
  set par(justify: true)
  set text(font: font, size: size, fill: theme.text)
  set table(stroke: theme.rule)
  set page(
    fill: theme.page,
    margin: margin,
    header: if header != auto { header }
      else if running-header { section-running-header() } else { none },
    footer: if footer != auto { footer } else { context [
      #align(right)[#counter(page).display("1") / #counter(page).final().last()]
    ] },
  )
  let metadata = (:)
  if title != none { metadata.insert("title", title) }
  if author != none { metadata.insert("author", author) }
  if description != none { metadata.insert("description", description) }
  set document(..metadata)
  set math.equation(numbering: equation-numbering)
  show: apply-paragraph-tabs
  show: body => if reset-math-blocks { apply-math-block-reset(body) } else { body }

  show heading: it => {
    if it.level in equation-reset-levels { counter(math.equation).update(0) }
    if it.level in footnote-reset-levels { counter(footnote).update(0) }
    if chapter-pages and it.level == 1 {
      if it.numbering == none {
        pagebreak(weak: true)
        it
      } else {
        set page(fill: theme.page, footer: none)
        set text(font: chapter-font, size: chapter-size, fill: theme.text)
        pagebreak(weak: true)
        align(center + horizon, it)
        pagebreak()
      }
    } else { it }
  }
  show figure: it => {
    if it.kind in math-block-kinds {
      set align(start)
      it
    } else { it }
  }
  body
}
