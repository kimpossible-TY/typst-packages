# Custom Typst Packages

These packages provide a unified design system, mathematical note templates, theorem/proof structures, and advanced layout/drawing tools.

---

## Repository Layout

```text
local/
  text-utils/0.1.2/          # Text manipulation & paragraph indentation helpers
  math-blocks/0.2.0/         # Theorem, Lemma, Definition environments & callout styles
  scoped-annotations/0.3.0/  # Local-scope reference targets and CeTZ drawing overlays
  cetz-helpers/0.2.0/        # Reusable legend and description boxes for CeTZ diagrams
  pdf-versioning/0.1.0/      # PDF version-check links for static publishing workflows
  fletcher-helpers/0.1.0/   # Theme-aware Fletcher diagram defaults
  math-book/0.2.0/           # Mathematical book/lecture-note document template
tools/pdf-versioning/         # Python server and browser helper for PDF freshness checks
scripts/                     # Utility installation and test scripts
tests/                       # Smoke tests for verifying package compilation
```

---

## Install Locally

To install the packages locally on your machine, run the following command from the repository root:

```sh
./scripts/install-local.sh
```

This copies the packages under `local/*` into Typst's local package directory:
- **macOS / Linux**: `~/Library/Application Support/typst/packages/local/` or `~/.local/share/typst/packages/local/`
- **Windows**: `%APPDATA%\typst\packages\local\`

Once installed, they can be imported into any Typst project via:

```typst
#import "@local/text-utils:0.1.2": *
#import "@local/math-blocks:0.2.0": *
#import "@local/scoped-annotations:0.3.0": *
#import "@local/cetz-helpers:0.2.0": *
#import "@local/pdf-versioning:0.1.0": *
```

### Updating an installed package

After pulling changes from this repository, run the installer again to refresh
the packages available to Typst:

```sh
git pull --ff-only
./scripts/install-local.sh
```

The installer replaces the installed directory for each package in `local/`,
so it also picks up source changes and new package versions. If the package
version changed, update the import in your Typst project as well—for example,
use `@local/scoped-annotations:0.3.0` after upgrading from `0.1.0`.

By default, the installer uses
`~/Library/Application Support/typst/packages`. If Typst uses a different
package root, set `TYPST_PACKAGE_PATH` when running it:

```sh
TYPST_PACKAGE_PATH="$HOME/.local/share/typst/packages" ./scripts/install-local.sh
```

---

## Packages Usage Guide

### 1. `@local/text-utils:0.1.2`
Provides text helpers, title capitalization, and custom paragraph markers.

* **Paragraph Tabs (`apply-paragraph-tabs`)**: Renders custom paragraph markers. If `#paragraph-tab` is followed by a lowercase letter, it automatically capitalizes the letter and indents by `1.5em`.
* **Highlighter (`highlighted`)**: Intelligently highlights background colors for both inline text and mathematical equations, automatically adjusting color based on whether light or dark theme is active.

```typst
#import "@local/text-utils:0.1.2": *

// Apply the indentation rule to the document
#show: apply-paragraph-tabs

#paragraph-tab
this paragraph starts with a tab and will be automatically capitalized and indented.

We can highlight text and inline formulas:
#highlighted[
  This is highlighted text containing a formula $a^2 + b^2 = c^2$.
]
```

---

### 2. `@local/math-blocks:0.2.0`
Provides beautiful, themeable, numbered theorem-like blocks, proofs, and general callouts.

* **Environments**: `#theorem`, `#proposition`, `#lemma`, `#definition`, `#note`, `#emphasis`, and `#proof`.
* **Counter resets**: Calling `#show: apply-math-block-reset` will reset block numbering counters automatically at each level-1 or level-2 heading (chapter or section). Numbers follow the `chapter.section.order` pattern.
* **Themes**: Comes with predefined `light-theme` and `dark-theme` colors.

```typst
#import "@local/math-blocks:0.2.0": *

// Reset math block numbers at every Chapter or Section.
#show: apply-math-block-reset

= Chapter 1
== Section 1

#definition(title: "Vector Space")[
  A vector space is a set of objects called vectors...
]

#theorem(title: "Pythagorean Theorem")[
  In a right-angled triangle, the square of the hypotenuse is equal to...
]

#proof[
  The proof proceeds by geometric dissection:
  $ a^2 + b^2 = c^2. $
]
```

---

### 3. `@local/scoped-annotations:0.3.0`

One implementation provides ordinary scoped labels (`local-tag-scope`) and
marker-based annotations (`local-scope-annotations`). `mannot-scope` is a
compatibility wrapper; `annot-cetz-local` is the low-level overlay function.
Existing `prefix`, `parent`, and `name` arguments remain available. Automatic
scopes share a counter; explicit prefixes must be unique within a document.

A plain `(s.tag)("name")` labels an equation or figure for `(s.ref)("name")`.
An annotation requires a mannot marker with bounds: use `(s.mark)("name")` or
`mark(..., tag: #(s.tag)("name"))`. Keep the annotation inside that equation.
Dictionary functions are called with parentheses around the field access.

```typst
#import "@local/scoped-annotations:0.3.0": local-scope-annotations
#import "@preview/cetz:0.4.2"

#local-scope-annotations(s => [
  #let term = (s.mark)("term")
  $ term(x^2)
    #(s.annot)("term", cetz, {
      import cetz.draw: *
      line((s.node)("term", "south"), (rel: (0, -0.3)), stroke: red)
    })
  $
])
```

---

### 4. `@local/cetz-helpers:0.2.0`
Provides helpers for drawing legend boxes and description boxes inside CeTZ canvas drawings.

* **`legend_box`**: Draws a legend at the specified coordinates containing series names, line strokes, and line marks.
* **`description_box`**: Places a styled text info box on the canvas.

```typst
#import "@local/cetz-helpers:0.2.0": legend_box, description_box
#import "@preview/cetz:0.4.2": *

#canvas({
  import draw: *
  // Draw some paths...
  line((0, 0), (2, 2), stroke: 1.5pt + blue, name: "series1")
  
  // Render a clean legend box
  legend_box(
    x: 3,
    y: 2,
    width: 3.5,
    items: (
      (text: [Series 1], stroke: 1.5pt + blue),
    )
  )

  // Render a description box next to it
  description_box(
    x: 3,
    y: 0,
    width: 4cm,
    body: [This chart illustrates the linear relationship.]
  )
})
```

---

### 5. `@local/math-book:0.2.0`

`apply-math-book` configures typography, theme, paragraph markers, stable
cross-part theorem references, and counter resets. Chapter opening pages and
section running headers are opt-in. Defaults reset equations at heading levels
1 and 2, footnotes at level 2, and mathematical blocks at levels 1 and 2.

```typst
#import "@local/math-book:0.2.0": apply-math-book, book-part, book-cover
#import "@local/math-blocks:0.2.0": *

#show: apply-math-book.with(
  title: "Advanced Real Analysis", author: "Author",
  theme: light-theme, chapter-pages: true, running-header: true,
)
#book-cover(title: [Advanced Real Analysis], author: [Author])
#outline(title: "Contents")
#book-part(prefix: "P", center-sections: true)[
  = Preliminaries
  == Notation
  #definition[A prefixed definition.] <prelim-definition>
]
#book-part[
  = Measure Theory
  == Measures
  See @prelim-definition.
]
```

Use `font`, `size`, `margin`, `header`, `footer`, `chapter-font`, `chapter-size`,
`equation-numbering`, `equation-reset-levels`, `footnote-reset-levels`, and
`reset-math-blocks` to customize the layout. `header`/`footer: none` hides them;
`auto` selects package defaults. When `reset-math-blocks: false`, the caller
owns both resets and mathematical-block reference formatting.

`book-part` scopes heading styles, resets headings by default, and restores the
previous mathematical-block numbering style afterward. Numbers are
`chapter.section.order`, optionally preceded by one literal prefix (for example
`P.1.2.3`). `reset-heading: false` continues the previous heading counter.
For a part containing one chapter, use `single-chapter: true` with a prefix.
The prefix replaces the chapter number: headings are `P`, `P.1`, `P.1.1`,
and mathematical blocks and equations use `P.section.order`.

`book-cover` accepts `title`, `subtitle`, `author`, `affiliation`, `year`,
`back-content`, `back-footer`, `theme`, `margin`, and `font`. A back page is
created when back content, affiliation, or a footer is supplied. Pass version
links as `back-footer`. Include project files, notation lists, and outlines in
the caller, never inside the package.

---

### 6. `@local/pdf-versioning:0.1.0`
Provides Typst helpers for rendering version-check links in PDFs that are
published through a static site.

```typst
#import "@local/pdf-versioning:0.1.0": pdf-version-links
#import "build-info.typ": *

#pdf-version-links(
  document-branch,
  document-built-at,
  document-base-url,
  document-source-url,
)
```

The companion Python and JavaScript tools live under `tools/pdf-versioning/`.
Use the Python server in a consuming project to generate `build-info.typ` and
`version.json`:

Install the CLI once from this repo:

```sh
./scripts/install-tools.sh
```

Then run it from a consuming project:

```sh
pdf-versioning --root . --watch-version
```

Use `pdf-version-check.js` from the HTML page to compare a PDF's
`pdfBranch`/`pdfBuiltAt` query parameters with the current `version.json`.

---

## Tests

### Smoke Test
Runs local installation and verifies general syntax compliance:

```sh
./scripts/test.sh
```

### Template Initialization Test
Verifies initializing a new project from the math-book template works:

```sh
typst init @local/math-book:0.2.0 /tmp/math-book-test
typst compile /tmp/math-book-test/main.typ /tmp/math-book-test.pdf
```

### Drawing helpers

`@local/cetz-helpers:0.2.0` retains the existing legend/description API and adds
`sample-curve(point, start, end, segments: 60)` and
`sample-function(f, start, end, segments: 60)`. Both return coordinates including
both endpoints; `segments` must be a positive integer. They do not render figures.

`@local/fletcher-helpers:0.1.0` exports `themed-diagram`. It forwards positional
nodes/edges and named Fletcher options, providing theme-aware stroke/fill defaults.
Explicit options override defaults. Pass `theme:` to override the contextual
light/dark theme. Keep mathematical content and figure captions in the project.

### Migration from project-local styles

- Use `text-utils:0.1.2`, `math-blocks:0.2.0`, `scoped-annotations:0.3.0`,
  `cetz-helpers:0.2.0`, and `math-book:0.2.0` consistently.
- Replace copied helper implementations with imports. Old project filenames may
  remain as small compatibility modules.
- Replace manual paragraph regex rules with `apply-paragraph-tabs`, or apply
  `apply-math-book`, which includes it.
- Replace raw `"P.1"` numbering patterns with `book-part(prefix: "P")`. The latter
  avoids repeating the literal prefix when formatting multiple counter levels.
- Do not add a second reset show rule around `apply-math-book`; it installs the
  mathematical-block reset/reference rules itself.
- Existing package versions remain in the repository. Install the packages before
  switching imports; publish the package changes before updating downstream CI.

`tests/book-integration.typ` asserts counter resets, prefix formatting, state
restoration, and sampling endpoints, and exercises cross-part references, repeated
local names, nested annotation scopes, and diagram overrides in both themes.
