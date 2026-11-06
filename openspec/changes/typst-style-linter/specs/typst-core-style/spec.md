# Typst core style

## Purpose

Define mechanical conventions for mathematical scripts, shared scoped annotations, public project references, book setup, and theme APIs without imposing PDE prose conventions on other consumers.

Source keys identify the supplied documents: **My** is My-Typst-style `SKILL.md`; **Packages** and **Projects** are its `references/typst-packages.md` and `references/project-references.md`; **PDE** is the supplied latest-main PDE guide. Full paths and precedence are recorded in `design.md`. All scenarios assume the relevant shared helper is resolved and the named rule is enabled; a non-violation means no diagnostic from that rule, not blanket conformance.

## ADDED Requirements

### Requirement: core.parenthesized-scripts
- **Grade:** warning
- **Source:** My / Mathematical presentation; PDE / Equations / Superscripts and subscripts.
- **Description:** The linter SHALL report each explicit `^` or `_` math script whose operand is not parenthesized, in real code and included guide examples, except the documented teaching sentence.

#### Scenario: Violation in actual mathematics
- **WHEN** code contains `$X^i + X_j + partial^alpha u$`
- **THEN** this rule reports the three bare script operands.

#### Scenario: Non-violation with grouped scripts
- **WHEN** code or a guide example contains `$X^(i) + X_(j) + T^(i)_(j) + partial^(alpha) u$`
- **THEN** this rule emits no finding.

#### Scenario: Non-violation in the teaching line
- **WHEN** included documentation contains exactly the following teaching sentence

```text
Do not write bare scripts such as `$X^i$` or `$X_j$`.
```

- **THEN** this rule emits no finding for that sentence; the exemption does not apply to real Typst code.

### Requirement: core.scoped-function-call
- **Grade:** error
- **Source:** Packages / Labels and annotations; PDE / mannot / Dictionary function call rule.
- **Description:** The linter SHALL report unparenthesized dictionary-function calls on a known scope receiver for `tag`, `tags`, `name`, `names`, `anchor`, `ref`, `node`, `annot`, or `mark`.

#### Scenario: Violation with field-call syntax
- **WHEN** a known scope callback contains `s.tag("x")` or `s.node("x", "north")`
- **THEN** this rule reports each call and requests `(s.tag)(...)` or `(s.node)(...)`.

#### Scenario: Non-violation with receiver binding
- **WHEN** code contains `#local-tag-scope(t => [$ x $ #(t.tag)("x") See #(t.ref)("x").])`
- **THEN** this rule emits no finding; receiver names are not restricted to `s`.

### Requirement: core.scoped-marker-tags
- **Grade:** warning
- **Source:** My / PDE book conventions; Packages / Labels and annotations; PDE / mannot / Basic rule.
- **Description:** Within a known local annotation scope, the linter SHALL report a literal raw global label supplied to mannot `mark(..., tag: ...)`, recommending a scope-generated tag. Documented special external-label cases can suppress this rule.

#### Scenario: Violation with a manual marker label
- **WHEN** a `mannot-scope` body contains `$ mark(x, tag: #<x>) $`
- **THEN** this rule reports the raw marker label.

#### Scenario: Non-violation with scoped marker
- **WHEN** that body contains `$ mark(x, tag: #(s.tag)("x")) $`
- **THEN** this rule emits no finding; ordinary global semantic labels outside marker calls are not forbidden.

### Requirement: core.scoped-anchor-access
- **Grade:** warning
- **Source:** PDE / mannot / How to refer to CeTZ anchors; Packages / Labels and annotations.
- **Description:** In a scoped annotation drawing, the linter SHALL report a raw anchor string matching a known local marker name and anchor direction. It SHALL accept scope node/anchor helpers and unrelated literal drawing-node names.

#### Scenario: Violation with a known raw marker anchor
- **WHEN** scope marker `"x"` is known and its drawing calls `line("x.north", "x.south")`
- **THEN** this rule reports the raw anchors.

#### Scenario: Non-violation with helpers
- **WHEN** the drawing calls `line((s.node)("x", "north"), (s.node)("x", "south"))` or uses `(s.anchor)` with `annot-cetz-local`
- **THEN** this rule emits no finding.

### Requirement: core.scoped-reference-access
- **Grade:** warning
- **Source:** My / Mentions within a document; PDE / Local equation and figure references.
- **Description:** The linter SHALL report a known generated scoped tag/name passed directly to a document reference instead of using the corresponding scope's `ref` helper. Stable global `@label` references SHALL remain permitted.

#### Scenario: Violation using the generated label
- **WHEN** a known scope body contains `#ref((s.tag)("energy"))`
- **THEN** this rule recommends `#(s.ref)("energy")` for the local mention.

#### Scenario: Non-violation using the public local API
- **WHEN** code contains `#(s.ref)("energy")` or `@global-energy`
- **THEN** this rule emits no finding.

### Requirement: core.local-overlay-api
- **Grade:** warning
- **Source:** Packages / Labels and annotations; PDE / mannot / Practical rules.
- **Description:** The linter SHALL report calls to resolved raw `annot-cetz` in local scopes, recommending `annot-cetz-local` or the scope's `annot` helper. It SHALL preserve supported `mannot-scope` wrappers.

#### Scenario: Violation with the raw API
- **WHEN** a known local scope calls `#annot-cetz(...)` for its marked display
- **THEN** this rule reports use of the raw overlay API; `...` denotes otherwise unchanged arguments.

#### Scenario: Non-violation with supported APIs
- **WHEN** it calls `#annot-cetz-local((s.tags)(("x",)), cetz, draw)` or `#(s.annot)("x", cetz, draw)` inside its display
- **THEN** this rule emits no finding, including in an existing `mannot-scope`.

### Requirement: core.annotation-in-display
- **Grade:** error
- **Source:** My / PDE book conventions; Packages / Labels and annotations; PDE / mannot / Practical rules.
- **Description:** The linter SHALL report resolved equation-overlay calls (`annot`, `annot-cetz`, `annot-cetz-local`, or a scope's `annot`) outside a display math ancestor. Annotation code nested inside the marked display SHALL be accepted.

#### Scenario: Violation after the equation
- **WHEN** an annotation scope contains `$ mark(x, tag: #(s.tag)("x")) $` followed outside the display by `#(s.annot)("x", cetz, draw)`
- **THEN** this rule reports the detached annotation call.

#### Scenario: Non-violation inside the equation
- **WHEN** the scope contains `$ mark(x, tag: #(s.tag)("x")) #(s.annot)("x", cetz, draw) $`
- **THEN** this rule emits no finding.

### Requirement: core.annotation-marker-bounds
- **Grade:** error
- **Source:** My / PDE book conventions; Packages / Labels and annotations.
- **Description:** For statically known overlay targets, the linter SHALL report a target supplied only by a plain scoped tag, with no matching `mark` or applied scope-marker binding in that display. Unknown target production SHALL be skipped rather than declared missing.

#### Scenario: Violation with only a plain label
- **WHEN** an annotation scope contains `$ x #(s.tag)("x") #(s.annot)("x", cetz, draw) $` and no marker for `"x"`
- **THEN** this rule reports that the known plain label supplies no annotation bounds.

#### Scenario: Non-violation with either marker API
- **WHEN** the display uses `mark(x, tag: #(s.tag)("x"))`, or the scope binds `#let term = (s.mark)("x")` and displays `$ term(x) #(s.annot)("x", cetz, draw) $`
- **THEN** this rule emits no finding for the marked target.

### Requirement: core.unique-scope-namespace
- **Grade:** error
- **Source:** Packages / Labels and annotations.
- **Description:** The linter SHALL report duplicate literal explicit scope prefixes in the selected document graph and duplicate literal sibling names under the same known parent scope. Automatically generated scopes SHALL allow repeated local names. Unrelated document graphs SHALL not be compared.

#### Scenario: Violation with explicit collisions
- **WHEN** two scopes in the selected document graph use `prefix: "detail"`, or two nested scopes use the same known `parent: s, name: "detail"`
- **THEN** this rule reports the later conflicting namespace and identifies the first declaration.

#### Scenario: Non-violation across automatic scopes
- **WHEN** two independent `#local-tag-scope(s => [$ x $ #(s.tag)("x")])` instances reuse local `"x"`, or children with name `"detail"` have distinct parents
- **THEN** this rule emits no finding.

### Requirement: core.project-ref-arguments
- **Grade:** error
- **Source:** My / References between projects; Projects / Mention an existing target.
- **Description:** The linter SHALL require exactly two positional arguments and no named arguments for resolved `project-ref`: a string project key and a global Typst label. Known strings, reference content, displayed numbers, or scope-generated labels in the second slot SHALL be rejected. Unresolved values SHALL not be guessed.

#### Scenario: Violation with extra or wrong arguments
- **WHEN** code contains `#project-ref("pde", <energy>, 3)`, `#project-ref("pde", "1.1.1")`, `#project-ref("pde", @energy)`, or `#project-ref("pde", (s.tag)("energy"))`
- **THEN** this rule reports each invalid call shape or known nonglobal-label argument.

#### Scenario: Non-violation with a global label
- **WHEN** code contains `#project-ref("pde", <zero_acceleration>)`
- **THEN** this rule emits no finding; catalog membership is checked separately.

### Requirement: core.project-ref-catalog-target
- **Grade:** error
- **Source:** Projects / Mention an existing target.
- **Description:** With a configured catalog, the linter SHALL check literal project keys and global labels against that catalog and report unknown projects, missing labels, and entries listed as ambiguous. It SHALL never infer aliases, select duplicate targets, guess pages, or refresh the catalog.

#### Scenario: Violation against a supplied snapshot
- **WHEN** a valid catalog fixture contains project `pde`, omits `missing`, and lists `duplicate` in `ambiguous_labels`, and code references either label or an absent project key
- **THEN** this rule reports the corresponding missing, ambiguous, or unknown lookup without a fallback link.

#### Scenario: Non-violation against a supplied snapshot
- **WHEN** the configured fixture contains a unique numbered global `pde` target `energy` and code uses `#project-ref("pde", <energy>)`
- **THEN** this rule emits no finding; it does not claim the fixture matches the current published PDF.

### Requirement: core.single-book-show
- **Grade:** error
- **Source:** Packages / Book configuration.
- **Description:** For a configured entrypoint using shared math-book helpers, the linter SHALL require one `apply-math-book` document show application at that entrypoint and report proven missing, duplicate, or included-file applications. Conditional execution that cannot be resolved SHALL be skipped.

#### Scenario: Violation with layered document show rules
- **WHEN** the entrypoint unconditionally applies `#show: apply-math-book.with(...)` twice, or its included chapter installs it again
- **THEN** this rule reports the duplicate application or wrong owner; `...` denotes the consumer's actual options.

#### Scenario: Non-violation with one owner
- **WHEN** the entrypoint has one `#show: apply-math-book.with(title: "Notes")` and included content has none
- **THEN** this rule emits no finding.

### Requirement: core.no-duplicate-book-reset
- **Grade:** error
- **Source:** Packages / Book configuration.
- **Description:** On a proven path where `apply-math-book` owns mathematical-block resets, the linter SHALL report a separate application of `apply-math-block-reset`. Explicit `reset-math-blocks: false` SHALL allow caller-owned resets.

#### Scenario: Violation with duplicate reset ownership
- **WHEN** `#show: apply-math-book.with(title: "Notes")` and `#show: apply-math-block-reset` both apply to the document
- **THEN** this rule reports the separately installed block reset.

#### Scenario: Non-violation with an explicit transfer
- **WHEN** the book show uses `reset-math-blocks: false` and the caller installs `apply-math-block-reset`
- **THEN** this rule emits no finding and does not assert that the caller's reference formatting is complete.

### Requirement: core.no-set-theme
- **Grade:** error
- **Source:** My / PDE book conventions; PDE / theme and dark mode.
- **Description:** The linter SHALL report `set theme(...)` when `theme` is the known custom palette helper or value rather than a settable Typst element. Theme values applied through `set text` or `set page` SHALL be accepted.

#### Scenario: Violation with a nonsettable target
- **WHEN** the palette binding is known and code contains `#set theme(fill: red)`
- **THEN** this rule reports the invalid custom set target.

#### Scenario: Non-violation with settable elements
- **WHEN** code contains `#set page(fill: theme.page)` and `#set text(fill: theme.text)`
- **THEN** this rule emits no finding.

### Requirement: core.theme-in-context
- **Grade:** warning
- **Source:** Packages / Shared drawing helpers; PDE / theme and dark mode.
- **Description:** The linter SHALL report a resolved `theme-from-text-fill()` call outside a lexical `context` in a reusable helper. It SHALL check this named API only, without guessing whether arbitrary literal colors are semantic or neutral.

#### Scenario: Violation without contextual resolution
- **WHEN** code contains `#let mybox(body) = { let theme = theme-from-text-fill(); block(fill: theme.page, body) }`
- **THEN** this rule reports the theme resolution outside lexical context.

#### Scenario: Non-violation with contextual resolution
- **WHEN** code contains `#let mybox(body) = context { let theme = theme-from-text-fill(); block(fill: theme.page, body) }`
- **THEN** this rule emits no finding.
