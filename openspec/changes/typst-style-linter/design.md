# Design

## Context

See `proposal.md` for motivation. The original `openspec/typst-style-linter` design was merged in PR #2 (`ae117126`). This follow-up is based on that merged design and adds the author's requested automatic fixes and precompile enforcement. Existing `tools/project-references/` and `tools/pdf-versioning/` remain untouched. This change defines a future linter and does not implement or run one.

The following supplied texts are the complete style authority for this change:

| Key used in rule specs | Document | Supplied location |
| --- | --- | --- |
| My | My-Typst-style skill | `/Users/taeyoung/Developer/agent_skills/my-typst-style/SKILL.md` |
| Packages | My-Typst-style package reference | `/Users/taeyoung/Developer/agent_skills/my-typst-style/references/typst-packages.md` |
| Projects | My-Typst-style project-reference guide | `/Users/taeyoung/Developer/agent_skills/my-typst-style/references/project-references.md` |
| PDE | PDE style guide, supplied latest-main snapshot | `/tmp/pde-typst-code-style-guide.md` |

Each rule cites its document key and exact section heading. The PDE heading `spaceing role(including #paragraph_tab role)` retains the source's spelling. Paths above identify the reviewed evidence, not runtime dependencies. The future packs must ship their own provenance metadata; consumer machines must not need these personal absolute paths.

The user's requested automatic fixes, footnote behavior, and teaching-line exception take precedence. Automatic fixing is not restricted to identical rendering: indentation, capitalization through paragraph markers, spacing, highlighting, and theme changes specified by a rule are intended effects. Never invent missing reference targets or mathematical content. PDE's latest explicit Footnote placement and Superscripts and subscripts sections specialize older general advice and examples. In particular, alignment means an actual `&` **or** math `\` line-break token, not simply a multiline source string. New annotations use `local-scope-annotations`; working `mannot-scope` calls remain supported, as the skill/package reference explicitly requires. No package-version recommendations are converted into pinned-version lint rules.

## Goals / Non-Goals

Goals are an inspectable catalog of 32 mechanical rules, stable grades and provenance, bounded source analysis, deterministic automatic repairs, small consumer configuration, local exceptions with reasons, and observable precompile enforcement for every registered build route. The core spec owns 15 rules and the PDE spec owns 17. Configuration requirements describe tool behavior and are not additional style rules.

This design PR contains only OpenSpec artifacts. Its reviewed merge is authorized, followed by implementation in a separate branch and draft PR. It does not deploy the tool, install packages, change consumer source/configuration or editor settings, or rewrite all projects. Tests use isolated fixtures. The historical specification-only/no-commit restriction is superseded by this workflow.

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

Keep shared logic and rule packs together in typst-packages. Copying the tool or packs into PDE would fragment updates. Consumers may invoke a locally installed or checkout-based launcher; packaging and launcher language can be chosen in implementation without changing this contract. A pinned tool version and pack version must be reproducible in CI.

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

1. Revise and strictly validate this design; independently review safety, rule coverage, and route guarantees. Merge the design-only follow-up PR after checking its exact head and repository requirements. Preserve the existing main and old design worktrees.
2. Create a separate implementation branch from the merged main. Build and test incrementally under `tools/typst-lint/`, publish a draft PR, and keep incomplete rule/adapter coverage explicit. Do not advertise the gate as production-ready while enabled rules are unimplemented.
3. Before a separately authorized rollout, inventory each Typst project and every CLI/script, CI, watch, preview, and export route. Record old settings, exact tool/compiler versions, entrypoints, writable selections, generated/external read-only dependencies, editor safety adapter, and route tests. Shared tool/pack code stays in typst-packages; each project gets only config and small invocation instructions. `AGENTS.md` (not a machine-enforced hook) tells agents to use the gate but is not proof of enforcement.
4. Opt projects in only when their actual routes pass the acceptance matrix. PDE uses Tinymist compile-on-save, so a shell wrapper alone cannot finish its onboarding. Routes not intercepted must be visibly disabled for authoritative export or marked unmanaged, and coverage must not say all projects are enforced until none remain unmanaged. No global PATH shadowing or silent editor-setting rewrite.
5. Roll back adapters using recorded prior settings. Restore individual source edits only if the current content still matches that transaction's postimage. Never overwrite later work or refresh catalogs as rollback.

## Open Questions

Implementation language, parser adapter, distribution, and the exact Tinymist/editor integration remain implementation choices to validate. A supported parser must preserve Typst syntax and source spans; an unverified editor hook is a rollout blocker, not a reason to claim wrapper coverage. Exact safety, rule repair, exit, and route contracts are defined below and in the new capability specs.

## Automatic fixing and compilation

### Modes and rule repair contract

`typst-lint check --root ROOT` never writes. `typst-lint fix --root ROOT --dry-run` reports the exact prospective patch and blocked repairs. `typst-lint fix --root ROOT` applies eligible repairs only through the protected transaction below. The shared `typst-style compile --root ROOT -- <compiler arguments>` gate fixes first, re-parses and re-lints, then compiles the verified snapshot. It invokes a configured absolute real compiler path as an argument vector, never through shell interpolation or recursive PATH lookup. Preserve Typst arguments, working directory, fonts, package resolution, root, inputs, environment, output format, and output destination; wrapper options precede `--`. Version/help and non-compiling subcommands pass through only by explicit supported command dispatch. Unknown subcommands/options, stdin input, and multi-entrypoint ambiguity fail with an actionable explanation until supported, never silently bypass lint.

`fix-policy.md` is the normative repair catalog for all 32 IDs. Every repair declares its exact replacement, static preconditions, unchanged data, intentional effects, and refusal cases. A rule may be diagnostic-only when no unique repair follows from the source. Safe means within that declared contract, not pixel-identical. A valid suppression removes both the diagnostic and the automatic repair. A diagnostic's grade does not determine repair eligibility. Unresolved repairs are reported with the rule ID and reason.

In particular, the verified shared text-utils implementation turns `paragraph_tab` into a 1.5em indent and uppercases a following lowercase letter through its show rule. Inserting the marker must retain the author's original text bytes and intentionally allow that rendered capitalization. Fixture tests must assert the intended new indentation/capitalization; a blanket render-equality test would incorrectly reject the requested behavior.

### Protected source transaction

- Resolve the selected root, include/exclude set, local dependencies, config, catalogs and helper provenance first. Only selected regular UTF-8 source files may be rewritten. Reject symlinks, root escapes, hard-linked write targets, missing parents, unsupported encodings and nonregular files; dependencies outside the root, generated/excluded inputs, packages and catalogs are read-only. Never execute imported code during analysis.
- A dirty Git worktree is not permission to discard edits and is not itself a rejection: baseline exact current bytes, file identity, permissions, content hashes and editor document versions. Never stash, reset, checkout, stage, or commit consumer work. Keep the patch limited to declared source spans, preserve comments, unedited bytes, newline style, BOM and mode, and record a recoverable before/after journal outside selected inputs.
- Hold a root-scoped cooperating-writer lock through planning, application, validation and snapshot handoff. The lock only coordinates this tool's runs; hashes plus atomic rename do not provide compare-and-swap against arbitrary editors. Writable operation therefore additionally requires a verified editor adapter applying versioned edits and maintaining an exclusive save lease, or an explicitly selected offline-exclusive workflow with editors/watchers closed and no other writers. Without that guarantee, produce a patch and stop before writes/compilation. A clean Git status does not prove no unsaved buffers exist.
- Editor requests operate on versioned buffers via the editor's edit API, never overwrite their disk files behind the editor. Dirty-buffer fixes remain undoable, preserve unrelated unsaved edits, and use the editor's save sequence only when that route authorizes saving. Cancellation, stale buffer versions, unavailable lease, or unknown buffer state blocks authoritative compilation. Other files' versions and disk identities are checked too. A disk CLI cannot assert editor cleanliness by itself.
- Build all edits in memory from one immutable baseline. Sort deterministically, coalesce identical edits, and defer overlapping/conflicting edits to a reparse. Re-run analysis for at most 8 passes; repeated states, nonconvergence, parser failure or unresolved edit conflicts abort before source replacement. Require a zero-edit second fix on the converged candidate. Syntax repair is allowed only for the specifically recognized scoped field-call form, not arbitrary malformed input.
- Stage complete replacements and journal durably before writing. Validate all inputs again while holding the writer lease, then apply per-file atomic replacements (or editor-versioned transaction). Multi-file replacement is not inherently atomic: journal every completed file, roll back only matching postimages on failure, and preserve conflicting user edits for recovery. A crash/incomplete journal blocks subsequent compile until recovery succeeds. Report modified, restored and unresolved paths honestly; never claim a partial write was atomic.
- Reparse and re-lint the complete candidate graph. Compile an immutable snapshot of the same checked inputs/dependencies and build resources, with the original logical root and flags. Fail if a resource cannot be snapshotted faithfully. Publish the requested output only after a successful compile and a final baseline/version check; concurrent changes invalidate the output and trigger a fresh gated run, never publish a stale PDF. Preserve the previous output on failure. Ordinary compiler failure keeps successful source fixes and their undo journal, reports the compiler error, and does not claim to undo mathematics; validation/write failures follow the conflict-checked recovery path above.

### Gate failures and coverage

Checks return exit 0 for no active findings (coverage metadata may still contain documented optional skips), 1 for active style findings of either grade, 2 for config/parse/input/unsupported coverage failures, and 3 for unsafe/concurrent/nonconvergent/write/recovery failures. Fix uses the same statuses after repairs. Warnings retain their warning grade; making warnings build-blocking is a gate decision, not a severity mutation. Eligible converged, parse-valid repairs are retained even if pre-existing diagnostic-only findings remain, provided complete post-fix analysis introduces no new unsuppressed findings. Unsupported required analysis or safety/parse failure blocks source application. All unsuppressed findings block authoritative compilation after the fix phase, including diagnostic-only warnings. This prevents a successful compile being mistaken for conformance. There is no silent `--force` or fail-open mode; explicit scoped rule exceptions are the supported way to accept intentional style deviations.

The wrapper returns the lint status if no compiler ran and preserves a failed compiler's status/signal. It returns 0 after compiler success only when final validation and output publication also succeed. A stale generation, failed publication or postcompile recovery failure returns gate status 3 even when the compiler returned 0. Structured `phase`, `compiler_invoked` and `compiler_status` fields keep compiler results distinct from gate/publication outcomes. It forwards cancellation/signals and cleans temporary outputs without deleting previous published artifacts. Tool missing/version mismatch, timeouts, enabled-but-unimplemented checks, unsafe snapshots and stale/incomplete transactions all fail closed. Optional catalog absence or a contractually dynamic check is reported as a bounded coverage limitation; it is not the same as an unimplemented rule. A report lists implemented rules, evaluated/skipped checks, suppressed findings, applied fixes, unresolved findings, versions, hashes and actual build routes. No full-coverage claim follows from zero findings alone.

### Route integration and verification

| Route | Required integration | Evidence needed before claiming enforcement |
| --- | --- | --- |
| Agent/manual CLI | Project task/script calls shared gate; AGENTS.md documents it | Spy compiler proves fix and final check run first; failed lint never invokes compiler; direct real compiler remains an explicitly unmanaged bypass until workflow migration |
| CI | Pin tool/packs/compiler; gate check is required before artifact build | Check mode blocks needed fixes without changing the checkout; optional fixes run only in a disposable CI copy and their diff is retained; never auto-push source from CI |
| Watch | Gate owns each rebuild, debounces, serializes by project, observes dependencies/config/catalog | Every change reruns fix/check; ignore only the exact own-write event/hash; external edit during fix is requeued; no infinite fix loop; do not just start native `typst watch` after one lint |
| Tinymist/editor | Verified adapter runs on the exact versioned document graph before authoritative compile/export and applies edits through editor APIs | Probe actual Tinymist save/export/preview behavior; `compileOnSave` and internal compiler do not necessarily call `typst` on PATH; prevent native export racing the gate |
| Preview without precompile hook | Explicit nonauthoritative draft preview, or disabled native compilation until a gate-capable adapter exists | Report this route unmanaged; never call it lint-enforced. Literal all-compilations enforcement requires intercepting/turning off this route, not an AGENTS.md promise |

The rollout inventory is bounded to user-selected project roots, not a recursive home-directory scan. New projects must adopt the standard template/config and route tests. Coverage is per project, entrypoint and route, including supported themes. A completion claim lists actual passing routes and remaining gaps; writing one AGENTS.md in every directory is insufficient.

### Acceptance matrix

In addition to every existing positive/negative detection scenario: each eligible repair needs exact before/after fixtures, refusal fixtures, idempotence, preserved-comments/newlines/Unicode checks, and compile/render assertions appropriate to its declared intended effect. Test false helper bindings and excluded inputs. Test multiple interacting rules, especially paragraph/footnote gaps and display boundaries. Verify dry-run/check never mutate sources, and use fake compiler spies for argument forwarding and no-compile-on-failure tests. Exercise locks, unsaved buffers, stale versions, edits between read/write/compile/publish, partial I/O, interrupted journals, rollback conflicts, symlink/hardlink paths, output paths with spaces, signals, nonconvergence and dependency/config changes. Real isolated Typst fixtures verify paragraph capitalization/indentation, footnote placement, scoped annotations and both supported themes. Do not install shared packages or change real consumers to run these tests; use an explicit fixture package path.
