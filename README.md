# Custom Typst Packages

These packages provide a unified design system, mathematical note templates, theorem/proof structures, and advanced layout/drawing tools.

---

## Repository Layout

```text
local/
  text-utils/0.1.1/          # Text manipulation & paragraph indentation helpers
  math-blocks/0.2.0/         # Theorem, Lemma, Definition environments & callout styles
  scoped-annotations/0.2.0/  # Local-scope reference targets and CeTZ drawing overlays
  cetz-helpers/0.1.0/        # Reusable legend and description boxes for CeTZ diagrams
  pdf-versioning/0.1.0/      # PDF version-check links for static publishing workflows
  math-book/0.1.0/           # Mathematical book/lecture-note document template
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
#import "@local/text-utils:0.1.1": *
#import "@local/math-blocks:0.2.0": *
#import "@local/scoped-annotations:0.2.0": *
#import "@local/cetz-helpers:0.1.0": *
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
use `@local/scoped-annotations:0.2.0` after upgrading from `0.1.0`.

By default, the installer uses
`~/Library/Application Support/typst/packages`. If Typst uses a different
package root, set `TYPST_PACKAGE_PATH` when running it:

```sh
TYPST_PACKAGE_PATH="$HOME/.local/share/typst/packages" ./scripts/install-local.sh
```

---

## Packages Usage Guide

### 1. `@local/text-utils:0.1.1`
Provides text helpers, title capitalization, and custom paragraph markers.

* **Paragraph Tabs (`apply-paragraph-tabs`)**: Renders custom paragraph markers. If `#paragraph-tab` is followed by a lowercase letter, it automatically capitalizes the letter and indents by `1.5em`.
* **Highlighter (`highlighted`)**: Intelligently highlights background colors for both inline text and mathematical equations, automatically adjusting color based on whether light or dark theme is active.

```typst
#import "@local/text-utils:0.1.1": *

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

### 3. `@local/scoped-annotations:0.2.0`
Allows you to create a localized reference scope. This prevents name clashes in labels and lets you draw overlay annotations (lines, arrows, shapes) on top of target document elements using CeTZ.

* **Usage**: Wrap sections in `local-scope-annotations(s => [ ... ])`.
* **`s.tag(name)`**: Defines a label locally in the scope.
* **`s.mark(name)`**: Creates a `mannot` marker with the scoped label and geometry metadata.
* **`s.ref(name)`**: References a label defined in the scope.
* **`s.annot(targets, cetz, canvas-drawings)`**: Draws custom shapes/lines overlaying the targeted elements.

```typst
#import "@local/scoped-annotations:0.2.0": local-scope-annotations
#import "@preview/cetz:0.4.2": *

#local-scope-annotations(s => [
  #let termone = (s.mark)("term1")
  We label this term $ termone(x^2) $.
  Later, we can refer to #(s.ref)("term1").

  // Draw an annotation arrow overlaying the term
  #s.annot("term1", cetz, {
    import cetz.draw: *
    // "term1" maps to coordinates matching the location of the label
    line("term1.south", (0, -0.5), mark: (end: "stealth"), stroke: red + 1pt)
    content((0, -0.7), [Target Term], anchor: "north")
  })
])
```

---

### 4. `@local/cetz-helpers:0.1.0`
Provides helpers for drawing legend boxes and description boxes inside CeTZ canvas drawings.

* **`legend_box`**: Draws a legend at the specified coordinates containing series names, line strokes, and line marks.
* **`description_box`**: Places a styled text info box on the canvas.

```typst
#import "@local/cetz-helpers:0.1.0": legend_box, description_box
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

### 5. `@local/math-book:0.1.0`
A document template designed for mathematics lecture notes, books, and thesis documents. It sets up page dimensions, clean margins, fonts (Times New Roman), custom running headers/footers, and integrates text/math block styling.

```typst
#import "@local/math-book:0.1.0": *
#import "@local/math-blocks:0.1.0": *

// Setup entry point with document metadata
#show: apply-math-book.with(
  title: "Advanced Real Analysis",
  author: "Isaac Newton",
  description: "Lecture notes covering measure theory and integration."
)

// Include a cover page and Table of Contents
#include "template/cover.typ"

= Measure Theory

#definition[
  A sigma-algebra on a set $X$ is a collection of subsets...
]
```

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
typst init @local/math-book:0.1.0 /tmp/math-book-test
typst compile /tmp/math-book-test/main.typ /tmp/math-book-test.pdf
```
