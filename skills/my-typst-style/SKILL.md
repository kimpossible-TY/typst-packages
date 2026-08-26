---
name: my-typst-style
description: Apply the user's reusable Typst conventions when editing, reviewing, or generating mathematical Typst documents; defer to a project's own style guide and defined macros when they are more specific.
---

# My Typst Style

Use this skill for the user's Typst writing, especially mathematical prose,
equations, theorem/proof structure, diagrams, and annotations.

## Establish the document's local capabilities

- Inspect the target file's imports and any repository style guide before a
  non-trivial edit. Project-local conventions and publisher requirements take
  precedence over this personal style.
- Use a macro only when the target document or its imported styles define it.
  Names such as `#paragraph_tab`, `#flowbox`, `#highlighted`,
  `#mannot-scope`, and `#local-tag-scope` are not assumed to exist globally.
- When a project provides theme fields, use those fields for reusable neutral
  colors, borders, and callouts. Resolve context-dependent themes inside
  `context`; do not attempt `#set theme(...)`.

## Partial Differential Equations book profile

Apply this profile only when the target is the user's
`Partial_Differential_Equations` project (identified by
`rules/typst-code-style-guide.md` or its `Styles/styles.typ` imports). Before a
non-trivial edit, read `rules/typst-code-style-guide.md`; it is the canonical,
detailed project guide.

- Preserve the import order: `Styles/styles.typ`, then a local `figures.typ`
  when relevant, then `@preview/mannot`.
- Use `#paragraph_tab` for prose breaks, but never immediately before a
  heading or display equation, or between adjacent display equations. Do not
  leave a blank source line immediately after `#footnote[...]`.
- The project's `#highlighted[...]` is prose-only, `#flowbox[...]` holds a
  genuinely complex equation chain, and `#definition[...]`, `#note[...]`,
  `#special-lemma[...]`, `#special-proposition[...]`, and
  `#special-definition[...]` number automatically. Add textbook numbers
  manually to `#lemma[...]`, `#proposition[...]`, and `#theorem[...]` when
  needed.
- For local references and mannot/CeTZ work, use `#local-tag-scope` and
  `#mannot-scope`. Call dictionary-held helpers with parenthesized access such
  as `(s.tag)("name")`, `(s.ref)("name")`, `(s.node)("name", "north")`,
  and `(s.annot)(...)`; use `annot-cetz-local` rather than raw
  `#annot-cetz`. Keep annotation calls inside the annotated `$ ... $` block.

## Mathematical writing defaults

- Keep display equations standalone. Do not wrap a display equation in a prose
  highlight or callout merely for emphasis.
- Use highlights for short explanatory prose only. Keep prose containing
  substantial inline mathematics readable without decorative wrapping.
- Write proofs in the document's proof environment when one exists. State a
  named result only when it is actually being established; otherwise use normal
  explanatory prose.
- Prefer ordinary display equations for routine calculations. Use a styled
  definition, lemma, proposition, theorem, or note only when the conceptual
  role benefits from visual structure.
- Keep logical chains and their arrows together in a dedicated flow box only
  when the project supplies one and the derivation is genuinely complicated.
- Use Typst math syntax and alignment directly; do not mechanically import
  LaTeX environments or alignment assumptions.
- Make script scope visually unambiguous. Prefer forms such as `$X^(i)$`,
  `$X_(j)$`, and `$T^(i)_(j)$` over bare scripts.
- Place equation footnotes according to the active style system. If the project
  defines a spacer such as `#dots_space`, use it only in the equation contexts
  for which it was designed.

## Diagrams and annotations

- Use a flowchart or commutative-diagram tool such as Fletcher for directed
  structural relationships. Do not turn a single equation into a flowchart.
- Add CeTZ or mannot only when a drawing reveals a non-obvious geometric,
  spatial, or structural relationship. Do not use it as decoration for a
  coordinate axis, elementary vector, familiar waveform, or self-explanatory
  formula.
- When scoped-label helpers are available, use their local names and helper
  calls rather than manually reusing raw global labels. Keep an annotation's
  marks and its annotation code near the same equation or figure.
- Prefer relative annotation placement with modest offsets. Reflow annotation
  text before using a large absolute offset that risks page-margin overflow.

## Layout and validation

- Respect the project’s paragraph, import-order, theorem-numbering, and theme
  conventions when they exist. Do not introduce a personal macro or style API
  into a document merely to satisfy this skill.
- Leave a blank source line after a proof block when the local style expects
  block separation.
- Compile the affected Typst document after content changes. For diagram or
  annotation changes, render the affected pages and inspect the visual result;
  iterate on overlaps, clipping, contrast, and page breaks.
