# Typst PDE style

## Purpose

Specify mechanically observable PDE content conventions layered on the core pack, especially source paragraph boundaries and the distinct footnote policies for prose, aligned displays, and unaligned displays.

Source keys **My**, **Packages**, **Projects**, and **PDE** have the meanings recorded in `design.md` and the core spec. These 17 rules apply only to the `pde` pack. Scenarios assume resolved helper bindings; non-violation means no finding from the named rule. Snippets can be fragments within the indicated surrounding content.

## ADDED Requirements

### Requirement: pde.import-order
- **Grade:** warning
- **Source:** My / PDE book conventions; Packages / Ownership and imports; PDE / the order of import.
- **Description:** Among present, recognized imports in a PDE content file, the linter SHALL require the relative `Styles/styles.typ` facade before local figure imports, and those before annotation package imports. Unrelated imports SHALL retain their order; absent optional categories SHALL not be required.

#### Scenario: Violation with a reversed import group
- **WHEN** content begins with these recognized imports

```typst
#import "@preview/mannot:0.4.0": *
#import "figures.typ": *
#import "../Styles/styles.typ": *
```

- **THEN** this rule reports the out-of-order imports.

#### Scenario: Non-violation with the existing grouping
- **WHEN** the facade precedes local figures and optional annotation imports

```typst
#import "../Styles/styles.typ": *
#import "figures.typ": *
#import "@preview/mannot:0.4.0": *
```

- **THEN** this rule emits no finding and does not demand a package-version migration.

### Requirement: pde.no-redundant-facade-import
- **Grade:** warning
- **Source:** Packages / Ownership and imports.
- **Description:** Where `Styles/styles.typ` already provides the requested symbols, the linter SHALL report redundant `Styles/mannot_utils.typ` or `Styles/local_tags.typ` imports. A compatibility facade used alone or exporting otherwise unavailable requested symbols SHALL remain accepted.

#### Scenario: Violation with redundant compatibility imports
- **WHEN** a file imports `../Styles/styles.typ: *` and also imports `../Styles/local_tags.typ: *`, with known overlapping exports
- **THEN** this rule reports the redundant compatibility import.

#### Scenario: Non-violation preserving compatibility
- **WHEN** a file imports only `../Styles/local_tags.typ: *`, or selectively imports an actually missing symbol from a compatibility facade
- **THEN** this rule emits no finding.

### Requirement: pde.paragraph-marker-gap
- **Grade:** warning
- **Source:** My / Paragraphs and reading rhythm, PDE book conventions; PDE / spaceing role(including `#paragraph_tab` role).
- **Description:** The linter SHALL require a whitespace-only blank source line immediately before a standalone `#paragraph_tab`. Start of file or content-block opening alone SHALL not substitute for that blank line.

#### Scenario: Violation with an adjacent marker
- **WHEN** source contains

```typst
First idea.
#paragraph_tab
Second idea.
```

- **THEN** this rule reports the missing blank line before the marker.

#### Scenario: Non-violation with the required source gap
- **WHEN** source contains

```typst
First idea.

#paragraph_tab
Second idea.
```

- **THEN** this rule emits no finding.

### Requirement: pde.paragraph-marker-target
- **Grade:** warning
- **Source:** My / PDE book conventions; PDE / spaceing role(including `#paragraph_tab` role).
- **Description:** The linter SHALL report `#paragraph_tab` whose next substantive sibling is a heading or display equation, including markers between consecutive displays. Intervening whitespace and comments SHALL not change that classification.

#### Scenario: Violation before structural content
- **WHEN** either fragment occurs

```typst

#paragraph_tab
= A heading
```

```typst
$ A = B $

#paragraph_tab
$ B = C $
```

- **THEN** this rule reports the marker before the heading or display.

#### Scenario: Non-violation before prose
- **WHEN** a blank line and `#paragraph_tab` precede `We now study the boundary.`
- **THEN** this rule emits no finding even if that prose later introduces a display.

### Requirement: pde.prose-paragraph-marker
- **Grade:** warning
- **Source:** My / Paragraphs and reading rhythm, Completion and verification; PDE / paragraph break.
- **Description:** At a new prose boundary after existing prose, including boundaries introduced by `block` or `parbreak`, the linter SHALL require `#paragraph_tab` for the new paragraph. Headings, lists, displays, figures, proof/theorem blocks, and document start SHALL not themselves imply an indented prose paragraph.

#### Scenario: Violation with an unmarked new paragraph
- **WHEN** source contains `First idea.`, a blank line, then `Second idea.` without a marker, or wraps the second prose in `#block[Second idea.]`
- **THEN** this rule reports the new unmarked prose paragraph, without deciding whether the ideas actually differ.

#### Scenario: Non-violation with continuity or an explicit marker
- **WHEN** prose is `First idea. Its consequence follows.` on one source line, or the second paragraph begins as below

```typst
First idea.

#paragraph_tab
Second idea.
```

- **THEN** this rule emits no finding; a `block` starting prose after existing prose likewise accepts a blank line and marker inside its body.

### Requirement: pde.continuation-line
- **Grade:** warning
- **Source:** My / Paragraphs and reading rhythm.
- **Description:** Within an ordinary prose run without a new-paragraph marker, the linter SHALL report adjacent prose sentences split across source lines. Structural blocks, display boundaries, list items, raw/code bodies, and explicit new marked paragraphs SHALL be excluded.

#### Scenario: Violation with split continuation sentences
- **WHEN** ordinary prose is

```typst
It is smooth and compactly supported.
The calculation can therefore be written as follows.
```

- **THEN** this rule requests joining the prose continuation onto the same source line.

#### Scenario: Non-violation with continuous source prose
- **WHEN** source is `It is smooth and compactly supported. The calculation can therefore be written as follows.`
- **THEN** this rule emits no finding; rendered automatic wrapping is irrelevant.

### Requirement: pde.display-continuation-gap
- **Grade:** warning
- **Source:** My / Paragraphs and reading rhythm, Completion and verification.
- **Description:** The linter SHALL report a blank source line between a standalone display's closing delimiter and subsequent unmarked prose in the same content body. Immediate prose on the next source line and explicitly marked new prose paragraphs SHALL remain accepted.

#### Scenario: Violation with an unmarked gap
- **WHEN** source contains

```typst
$ A = B $

Hence the identity holds.
```

- **THEN** this rule reports the blank gap; the general prose-boundary rule does not duplicate this display-specific finding.

#### Scenario: Non-violation for continuation or a new marked idea
- **WHEN** prose immediately follows the display on the next source line, or follows a blank line and `#paragraph_tab`
- **THEN** this rule emits no finding.

### Requirement: pde.proof-trailing-gap
- **Grade:** warning
- **Source:** My / PDE book conventions; PDE / Equations / Equations blocks / proof.
- **Description:** After a resolved `proof` content block, the linter SHALL require a blank source line before the next paragraph, equation, heading, or other block in that content body. End of file or end of the containing body SHALL not require a synthetic trailing paragraph.

#### Scenario: Violation with adjacent content
- **WHEN** source contains

```typst
#proof[The claim follows.]
$ A = B $
```

- **THEN** this rule reports the missing blank line after the proof.

#### Scenario: Non-violation with a source gap
- **WHEN** source contains

```typst
#proof[The claim follows.]

$ A = B $
```

- **THEN** this rule emits no finding, as it also does for a proof ending the file.

### Requirement: pde.footnote-trailing-gap
- **Grade:** warning
- **Source:** My / PDE book conventions; PDE / spaceing role(including `#paragraph_tab` role).
- **Description:** The linter SHALL report a blank source line immediately after a source-line footnote's closing bracket when more content follows, including within math. An explicit paragraph break on the next source line SHALL be accepted in prose. End of file SHALL not require additional content.

#### Scenario: Violation after a source-line footnote
- **WHEN** source contains

```typst
This holds.#footnote[Reason.]

#paragraph_tab
A new idea.
```

- **THEN** this rule reports the blank source line immediately after the footnote.

#### Scenario: Non-violation using an explicit break
- **WHEN** source contains

```typst
This holds.#footnote[Reason.]
#parbreak()

#paragraph_tab
A new idea.
```

- **THEN** this rule emits no finding; a prose continuation on the very next source line or end of file also introduces no forbidden blank line.

### Requirement: pde.prose-footnote-punctuation
- **Grade:** warning
- **Source:** PDE / Equations / Footnote placement.
- **Description:** In prose, the linter SHALL require `#footnote[...]` immediately after a comma or period, with no intervening space, newline, or comment. Math footnotes SHALL be classified separately using their enclosing math context.

#### Scenario: Violation with whitespace or misplaced punctuation
- **WHEN** prose contains `This holds #footnote[Reason.].`, `This holds. #footnote[Reason.]`, or `This holds;#footnote[Reason.]`
- **THEN** this rule reports the missing adjacent comma or period before the footnote.

#### Scenario: Non-violation after either permitted punctuation mark
- **WHEN** prose contains `This holds,#footnote[Reason.] and continues.#footnote[More detail.]`
- **THEN** this rule emits no finding for either footnote.

### Requirement: pde.aligned-footnote-spacer
- **Grade:** warning
- **Source:** PDE / Equations / Footnotes in equation, Footnote placement.
- **Description:** In a display containing an actual alignment `&` or math line-break `\` token, the linter SHALL require `#dots_space #footnote[...]` for each equation-level footnote, with only whitespace between the spacer and footnote. Tokens nested in footnote bodies, strings, or comments SHALL not classify the outer display.

#### Scenario: Violation in either kind of aligned display
- **WHEN** either display is present

```typst
$ A &= B #footnote[Reason.] $
```

```typst
$
  A = B \
  B = C #footnote[Reason.]
$
```

- **THEN** this rule reports each missing `#dots_space` before the equation-level footnote.

#### Scenario: Non-violation with the required spacer
- **WHEN** source contains

```typst
$
  A &= B \
  &= C #dots_space #footnote[Reason.]
$
```

- **THEN** this rule emits no finding; a display with only `&` or only a line-break token is also accepted when its footnotes use that spacer.

### Requirement: pde.unaligned-footnote-no-spacer
- **Grade:** warning
- **Source:** PDE / Equations / Footnotes in equation, Footnote placement.
- **Description:** In a display with no alignment or math line-break tokens, the linter SHALL forbid `#dots_space` immediately before an equation-level footnote. This includes single-line unaligned displays; physical source wrapping alone SHALL not make a display aligned.

#### Scenario: Violation in a single-line unaligned display
- **WHEN** source contains `$ omega = sqrt(det(g)) #dots_space #footnote[Reason.] $`
- **THEN** this rule reports the forbidden spacer.

#### Scenario: Non-violation without a spacer
- **WHEN** source contains `$ omega = sqrt(det(g)) #footnote[Reason.] $`, even if the footnote body includes `$a & b$`
- **THEN** this rule emits no finding and the nested body does not make the outer equation aligned.

### Requirement: pde.prose-footnote-no-spacer
- **Grade:** warning
- **Source:** My / PDE book conventions; PDE / Equations / Footnote placement.
- **Description:** In prose, the linter SHALL forbid `#dots_space` before `#footnote[...]`. An inline math fragment elsewhere in the sentence SHALL not turn its prose footnote into an equation-level footnote.

#### Scenario: Violation with a prose spacer
- **WHEN** prose contains `This holds.#dots_space #footnote[Reason.]`
- **THEN** this rule reports the prose spacer.

#### Scenario: Non-violation with an ordinary prose footnote
- **WHEN** prose contains `For $x^(2)$, this holds.#footnote[Reason.]`
- **THEN** this rule emits no finding.

### Requirement: pde.standalone-display
- **Grade:** warning
- **Source:** My / Mathematical presentation, Paragraphs and reading rhythm; PDE / highlight.
- **Description:** The linter SHALL report prose sharing a source line with a display math delimiter. A display SHALL stand on its own source line or lines; attached semantic labels and equation-overlay calls within the display SHALL remain permitted. Inline math SHALL not be reclassified as display math.

#### Scenario: Violation with prose on the display line
- **WHEN** source contains `Thus $ A = B $ and the claim follows.` where the spaced delimiters form display math
- **THEN** this rule reports the prose sharing the display's source line.

#### Scenario: Non-violation with separate source lines
- **WHEN** source contains

```typst
Thus
$ A = B $ <identity>
and the claim follows.
```

- **THEN** this rule emits no finding, as it also does for ordinary inline math `Thus $A=B$ and the claim follows.`

### Requirement: pde.text-only-highlight
- **Grade:** warning
- **Source:** My / Mathematical presentation; PDE / highlight.
- **Description:** The linter SHALL report math inside resolved `highlight` or `highlighted` content, including inline and display equations. Text-only highlights SHALL remain accepted. It SHALL not judge whether prose deserves emphasis.

#### Scenario: Violation highlighting math
- **WHEN** source contains `#highlight[The term $x^(2)$ matters.]` or `#highlighted[$ A &= B $]`
- **THEN** this rule reports math enclosed by the prose-highlight helper.

#### Scenario: Non-violation with separate math
- **WHEN** source contains

```typst
#highlight[The convention is the key observation.]

$ A &= B $
```

- **THEN** this rule emits no finding.

### Requirement: pde.theme-cancelmark
- **Grade:** warning
- **Source:** My / PDE book conventions (algebraic cancellations).
- **Description:** In PDE content, the linter SHALL report resolved bare math `cancel(...)` calls and recommend the facade's theme-aware `cancelmark`. Explicit intentional `stroke:` overrides on `cancelmark` SHALL remain permitted.

#### Scenario: Violation using the bare cancellation element
- **WHEN** math contains `$ cancel(x) + y $`
- **THEN** this rule reports the bare cancellation call.

#### Scenario: Non-violation with the shared helper
- **WHEN** math contains `$ cancelmark(x) + cancelmark(y, cross: true, stroke: blue) $`
- **THEN** this rule emits no finding; it does not decide whether both terms of a mathematical cancellation pair were selected.

### Requirement: pde.flowbox-colored-border
- **Grade:** warning
- **Source:** PDE / Equations / Equations blocks / flowbox.
- **Description:** Inside the resolved `flowbox` implementation, the linter SHALL report a width-only literal `stroke:` on its border, such as `1pt`, without a color. This check SHALL not extend to arbitrary figures or infer the semantic role of colors in other helpers.

#### Scenario: Violation with a width-only border
- **WHEN** the `flowbox` implementation contains `block(stroke: 1pt, body)`
- **THEN** this rule reports the border and recommends a theme-aware stroke.

#### Scenario: Non-violation using the active palette
- **WHEN** the implementation contains `context { let theme = theme-from-text-fill(); block(stroke: 1pt + theme.rule, body) }`
- **THEN** this rule emits no finding.
