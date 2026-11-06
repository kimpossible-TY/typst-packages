# Design

## Context

See `proposal.md` for motivation. This is the isolated `openspec/typst-style-linter` checkout of typst-packages. There was no `openspec/` tree. Existing `tools/project-references/` and `tools/pdf-versioning/` remain untouched. This change defines a future linter and does not implement or run one.

The following supplied texts are the complete style authority for this change:

| Key used in rule specs | Document | Supplied location |
| --- | --- | --- |
| My | My-Typst-style skill | `/Users/taeyoung/Developer/agent_skills/my-typst-style/SKILL.md` |
| Packages | My-Typst-style package reference | `/Users/taeyoung/Developer/agent_skills/my-typst-style/references/typst-packages.md` |
| Projects | My-Typst-style project-reference guide | `/Users/taeyoung/Developer/agent_skills/my-typst-style/references/project-references.md` |
| PDE | PDE style guide, supplied latest-main snapshot | `/tmp/pde-typst-code-style-guide.md` |

Each rule cites its document key and exact section heading. The PDE heading `spaceing role(including #paragraph_tab role)` retains the source's spelling. Paths above identify the reviewed evidence, not runtime dependencies. The future packs must ship their own provenance metadata; consumer machines must not need these personal absolute paths.

The user's requested footnote behavior and teaching-line exception take precedence. PDE's latest explicit Footnote placement and Superscripts and subscripts sections specialize older general advice and examples. In particular, alignment means an actual `&` **or** math `\` line-break token, not simply a multiline source string. New annotations use `local-scope-annotations`; working `mannot-scope` calls remain supported, as the skill/package reference explicitly requires. No package-version recommendations are converted into pinned-version lint rules.

## Goals / Non-Goals

Goals are an inspectable catalog of 32 mechanical rules, stable grades and provenance, bounded source analysis, small consumer configuration, and local exceptions with reasons. The core spec owns 15 rules and the PDE spec owns 17. Configuration requirements describe tool behavior and are not additional style rules.

This delivery contains only OpenSpec initialization, proposal, design, delta specs, checklist, and a temporary report. It excludes linter source files under `tools/`, parser dependencies, executable fixtures, autofixes, package edits, catalog exports, consumer configuration changes, CI integration, builds, deployment, archive, commits, and pushes. The future implementation checklist below does not authorize starting implementation in this task.

## Decisions

### Shared ownership and consumer shape

The future layout is conceptual; none of these implementation paths are created now:

```text
typst-packages/
  tools/typst-lint/
    <launcher and syntax analysis>
    rules/core/                 # shared rule definitions and provenance
    rules/pde/                  # PDE extensions
    <rule acceptance fixtures>
consumer-project/
  .typst-lint.toml              # selection and local exceptions only
```

Keep shared logic and rule packs together in typst-packages. Copying the tool or packs into PDE would fragment updates. Consumers may invoke a locally installed or checkout-based launcher; packaging and launcher language can be chosen later without changing this contract.

Conceptual consumer configuration (a documentation example, not a file added to PDE):

```toml
rule-set = "pde"                # core + pde; default is core
include = ["**/*.typ", "rules/**/*.md"]
exclude = ["vendor/**", "generated/**"]
entrypoint = "main.typ"         # optional static document graph
# catalog = "references/projects.json" # optional existing read-only snapshot

[[ignore]]
paths = ["legacy/*.typ"]
rules = ["core.scoped-marker-tags"]
reason = "External plugin consumes these existing public marker labels"
```

Globs match paths relative to the selected consumer root, with `**` matching directories and `*` staying within one path segment. Excludes win over includes. Entrypoint analysis can read explicitly referenced local files to establish bindings and ownership, but style findings are emitted only for selected, nonexcluded inputs. An imported helper outside the selected inputs is not itself linted. The linter does not scan sibling projects or the package owner's main checkout implicitly. Catalog reads use the configured path and never invoke compilation, export, installation, or network access. A requested catalog that is missing or malformed is a configuration failure; no catalog configured means membership checks are explicitly skipped.

### Packs, grades, and diagnostics

`core` covers reusable API/structure checks and parenthesized math scripts. `pde` extends core with PDE-specific policies. The full set is exactly the `### Requirement:` entries in the two style specs (15 + 17); their IDs remain stable even if source headings change. Source documents describe intended style; the grades are this design's enforcement decision.

An **error** is a provable API or structural violation, such as a bad public-reference signature, a known namespace collision, or an overlay detached from its display. A **warning** is a style preference or narrowly scoped heuristic, such as source prose wrapping or an intentional raw marker label. No severity override is introduced initially: scoped ignores make exceptions explicit without altering the catalog's meaning. Every finding includes position, ID, grade, explanation, and source section. Suppressed findings are not counted as active findings; a summary records how many were suppressed and which checks were skipped. Warning-only results remain distinguishable from a clean run. Errors and configuration/parse failures are unsuccessful results; neither silence nor warnings prove compilation or rendering success.

### Syntax-aware, conservative analysis

Use syntax structure and resolved helper provenance rather than file-wide regexes. A token-only approach cannot reliably distinguish inline math from displays, nested footnotes, comments, dictionary access, or raw examples. No specific parser/library or language is selected here. A future parser adapter must retain malformed-but-recognizable dictionary-call syntax long enough to report `core.scoped-function-call`; unrecoverable syntax still produces a parse failure.

Track imports, local shadowing, literal arguments, the actual callback receiver, and static marker bindings. Resolve relative PDE facade paths by their normalized target, not by a fixed count of `../`. Recognize local figure imports by `figures.typ` or `figures/figures.typ`, and annotation imports by the supplied scoped-annotations/mannot APIs. Redundant-import checks require verified exports; wildcard syntax alone is not proof. Dynamic exports and unresolved wrappers are skipped with reasons. Differentiate ordinary semantic labels from mannot marker tags; global labels are not generally forbidden.

Configured entrypoint analysis follows statically resolvable includes and imports without executing Typst. Book-show/reset rules apply only where shared math-book use and the relevant ownership path are established. Duplicate checks do not compare unrelated input files as if they were one document. Without an entrypoint, uniqueness can be checked within a known local scope/file; cross-file ownership and namespace checks are skipped. Provenance and literal target analysis also bound raw anchor and generated-reference checks: unrelated CeTZ node names are not flagged. `theme-in-context` checks the lexical presence of `context` around the named resolution call only; a caller that supplies context indirectly can document an exception.

For prose, classify ordinary markup text runs with inline math/footnotes as prose, while respecting headings, lists, figures, raw/code content, and known theorem/proof bodies. In those bodies, apply paragraph rules to their internal prose, not to the outer structural boundary. Check observable blank lines, source sentence boundaries, and explicit `block`/`parbreak` boundaries; never infer an idea change from words such as “Since” or “Therefore.” `pde.continuation-line` applies at adjacent sentence boundaries identifiable by terminal `.`, `?`, or `!` followed by source whitespace and ordinary prose on the next line; abbreviations and other unclear cases are skipped. `pde.display-continuation-gap` owns the display-to-prose blank-line case, avoiding a duplicate general prose-boundary finding. A new paragraph inside a bare `block` must have its own blank line and marker before its prose. The proof-trailing-gap rule still applies before any following structural sibling; closing the containing body or EOF is not a new sibling.

For footnotes, classify the footnote by its nearest enclosing math/prose content. Inspect alignment tokens only in the enclosing display's own math, not strings, comments, or nested footnote bodies. An inline math expression in a prose sentence does not classify a following prose footnote as math. Apply spacer checks to the immediately preceding `dots_space` construct, not a spacer elsewhere in the formula. Prose punctuation requires source adjacency, not rendered proximity. The footnote-trailing-gap rule concerns a source line ending in a footnote call, including a line inside math. In prose, an explicit `#parbreak()` before the blank line permits a new marked paragraph without leaving a blank immediately after the footnote.

Markdown is opt-in through include globs. Check math scripts in `typst` fences and inline math examples, mapping diagnostics back to the original Markdown lines. Guide examples must group scripts just like real code. Preserve the supplied exact sentence:

```text
Do not write bare scripts such as `$X^i$` or `$X_j$`.
```

This narrowly defined documentation exemption neither exempts arbitrary “bad” examples nor disables script checking in a guide. Other deliberate counterexamples can use explicit ignores. Comments, strings, and raw literal Typst text are data, not real math. Documentation-only snippets with unresolved imports can still receive syntax-local script checks; other binding-dependent checks are skipped.

### False-positive suppression

Primary mechanism: a single source comment on its own line:

```typst
// typst-lint: ignore-next core.scoped-marker-tags -- plugin requires its existing public marker
$ mark(x, tag: #<external-target>) $
```

It targets the next non-comment syntax construct, not merely the next physical line. A multiline display or function call is one construct, so an exception can cover its body. Only listed IDs whose diagnostic start lies within that construct are suppressed; the directive then expires. Multiple IDs use commas. A blank reason, unknown ID, or missing next construct is a configuration failure. There is no file-wide inline disable or implicit permanent disable. This is chosen over unrestricted disable/enable ranges because bounded exceptions are reviewable and cannot accidentally leak to the rest of a chapter.

Configuration `[[ignore]]` supports recurring, path-scoped exceptions with explicit IDs and a reason. `exclude` removes entire irrelevant/generated paths. These are secondary controls, not an additional set of style rules. Configuration and parse failures cannot be suppressed by style ignores. For Markdown `typst` fences, use the same source comment inside the fence; for an intentional inline documentation example other than the exact teaching sentence, use a path/rule config ignore. No ignore is automatically inferred from words such as “incorrect.”

### Excluded guidance (18 items)

These remain human review or build responsibilities. Each exclusion names the uncheckable part, without exempting the related mechanical rules above.

| # | Guidance and source | Why excluded |
| --- | --- | --- |
| 1 | My / opening guidance: preserve notation, voice, and logical readability | Mathematical intent and authorial voice have no source-only correctness predicate. |
| 2 | My / Paragraphs and reading rhythm: decide where ideas or substantial proof steps change | The relation between arguments requires reading the mathematics; the linter only checks the chosen source boundary. |
| 3 | My / Mathematical presentation; PDE / Definitions, theorems, lemmas, notes, and propositions; proof: select the right environment and supply a proof | Whether text is a definition, routine calculation, recalled identity, or sufficient proof is semantic. |
| 4 | My / Mathematical presentation; PDE / highlight: emphasize only prose that deserves it | Need for emphasis and an appropriate length depend on the exposition. |
| 5 | PDE / CeTZ selection rule: use diagrams only for nontrivial relations that reduce effort | Reader effort, familiarity, and spatial insight cannot be decided from tokens. |
| 6 | My / Mathematical presentation; PDE / Fletcher diagrams: choose Fletcher versus geometry or equation flow | Identifying a diagram's mathematical purpose requires interpretation, even when arrows are visible. |
| 7 | PDE / flowbox: use for complicated derivations and keep connecting arrows with their argument | Complexity and which arrow belongs to which derivation are not determined by arrow syntax alone. |
| 8 | My / PDE book conventions; PDE / mannot / Practical rules: modest offsets, nearby annotations, reflow long labels | No numeric threshold is supplied; suitable placement depends on rendered geometry and page breaks. |
| 9 | My / PDE book conventions: cross out both members of a cancellation pair | Algebraic pairing and whether two terms actually cancel require mathematical reasoning. |
| 10 | Packages / Shared drawing helpers; PDE / theme and dark mode: semantic versus neutral colors and light-only styles | A literal color's semantic role and appearance across themes are not encoded in general Typst syntax. |
| 11 | My / Shared package ownership; Packages / Ownership and imports: put reusable behavior in the shared package | Reusability, compatibility necessity, and copied implementation ownership require architectural judgment. |
| 12 | My / Mathematical presentation; PDE / Definitions, theorems, lemmas, notes, and propositions: distinguish textbook numbers from duplicate automatic numbers | The same numeric title can intentionally cite a source; no source marker defines which meaning it has. |
| 13 | My / Mentions within a document: reconnect a symbol, result, and property; read the citation target | A correct API call cannot establish mathematical justification or whether repetition is helpful. |
| 14 | Projects / Register or refresh a catalog: keep catalog and published PDF on the same build | Source lint cannot establish snapshot freshness or published PDF page correspondence; static membership remains checked. |
| 15 | My / Use the local context; Packages / Verification and dependency changes: choose package migrations and verify installed APIs | Version labels do not prove installed implementations match; migration appropriateness depends on deployment state. |
| 16 | Packages / Book configuration: choose P/S/main parts, reset overrides, and template versus local ownership | The mathematical/document role of a part and intent to continue a counter are not inferable reliably from titles. |
| 17 | My / Completion and verification; Packages / Verification and dependency changes: compile entrypoints, inspect layout and themes, report evidence | Build execution, convergence, clipping, and visual inspection are workflow evidence, not source style predicates. |
| 18 | My / References between projects; Projects / Mention an existing target: choose a stable public label for the right result and avoid manually numbered substitute links | Stability across edits and equivalence of a link's mathematical target cannot be inferred without project history and intent. |

There is no invented offset cutoff, maximum highlight length, package pin, mandatory diagram count, blanket hard-coded-color ban, or prose keyword heuristic. Unresolved dynamic behavior is a coverage limitation, not a new editorial exclusion or a false success.

## Risks / Trade-offs

- Syntax and binding analysis is more work than regex scanning → preserve exact positive/negative scenarios as the acceptance contract and skip unsupported inferences visibly.
- Strict source wrapping may warn on intentional author formatting → use warning grade, conservative sentence boundaries, and a reasoned local suppression.
- A broad ignored construct can hide later findings inside its body → require exact IDs and reasons; prefer the smallest following construct and report suppression counts.
- A valid catalog may still be stale → never claim freshness and leave export/publication checks to the source project's workflow.
- The PDE guide contains older broad advice alongside newer precise rules → use the precedence above and do not migrate compatibility wrappers or dependencies as a side effect.

## Migration Plan

There is no runtime migration in this change. Validate the OpenSpec delta and leave it reviewable and uncommitted. Later work can implement the checklist, select a parser and launcher, verify the catalog scenarios, then let PDE opt in with its own config in a separately authorized task. Until that opt-in, no consumer workflow changes. Future rollback is removal of consumer configuration/invocation; no source rewrite or catalog mutation is part of linting.

## Open Questions

No unresolved rule semantics block this specification. Implementation language, parser adapter, and distribution/launcher packaging remain deferrable choices; they must satisfy these same contracts and do not authorize implementation now.
