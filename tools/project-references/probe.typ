// The exporter appends this source to a temporary sibling of the main file.
// Keeping the main source (rather than including it in another file) retains
// its top-level show rules for the synthetic references below.
#context {
  // The exporter replaces this token with names selected in the original
  // document. Fixed label queries do not feed on synthetic probe elements.
  let names = __PROJECT_REFERENCE_TARGETS__

  // The zero-size overlay leaves existing pages and target locations intact.
  // hide retains metadata and show-rule realization without painting the refs.
  place(top + left, hide(box(width: 0pt, height: 0pt)[
    #for name in names {
      let tag = label(name)
      let element = query(tag).first()
      let position = element.location().position()
      metadata((
        kind: "project-reference-target",
        label: name,
        page: position.page,
        x: position.x / 1pt,
        y: position.y / 1pt,
      ))
      {
        // Realized text includes the source project's numbering and ref rules,
        // including Typst's built-in reference rendering and custom show ref.
        show text: item => {
          metadata((kind: "project-reference-text", label: name, text: item.text))
          item
        }
        // The space element has no public constructor; obtain its selector
        // from a literal space. This also catches spaces beside styled text.
        // Consume it to avoid also capturing its realized text twice.
        show [ ].func(): item => metadata((
          kind: "project-reference-text", label: name, text: " ",
        ))
        show linebreak: item => metadata((
          kind: "project-reference-text", label: name, text: " ",
        ))
        ref(tag)
      }
    }
  ]))
}
