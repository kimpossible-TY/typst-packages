# Typst lint configuration

## Purpose

Let a consumer select shared Typst style packs and inspect actionable diagnostics while controlling false positives without copying linter code into its repository. These are future behavior contracts; this change delivers no executable tool.

## ADDED Requirements

### Requirement: Select shared packs from consumer configuration
The linter SHALL read `.typst-lint.toml` at an explicitly selected consumer root. `rule-set = "core"` SHALL select the 15 core rules; `rule-set = "pde"` SHALL select core plus the 18 PDE rules. When configuration is absent, the linter SHALL select core. Unknown pack names and rule IDs SHALL be configuration failures, not silently ignored settings.

#### Scenario: PDE opts into its conventions
- **WHEN** a consumer selects `rule-set = "pde"`
- **THEN** all 33 catalog rules are enabled at their documented grades without requiring consumer-owned rule code.

#### Scenario: Another project selects core
- **WHEN** a consumer selects core or supplies no configuration
- **THEN** only the 15 core rules are enabled and PDE paragraph and footnote policies do not apply.

#### Scenario: Misspelled selection
- **WHEN** a consumer selects `rule-set = "ped"` or names an unknown ID in an ignore directive
- **THEN** the run reports a configuration failure with the invalid value and does not claim a clean lint result.

### Requirement: Scope inputs deterministically
The linter SHALL use root-relative include/exclude globs with excludes winning and default `**/*.typ`. Excluded files SHALL emit no style findings. Optional entrypoint and read-only catalog paths SHALL bound static analysis. Check/dry-run SHALL NOT write inputs; fix MAY write only selected source through `typst-lint-autofix`. Lint analysis SHALL NOT modify or fetch catalogs/packages.

#### Scenario: Generated sources excluded
- **WHEN** include is `**/*.typ`, exclude contains `generated/**`, and both `chapter.typ` and `generated/chapter.typ` contain the same violation
- **THEN** only `chapter.typ` produces the style diagnostic.

#### Scenario: No catalog supplied
- **WHEN** no catalog is configured and a literal `project-ref` call is present
- **THEN** its argument shape is checked, catalog membership is reported as skipped, and no missing-target finding is invented.

### Requirement: Report stable diagnostics and grades
Each style diagnostic SHALL contain rule ID, catalog grade, root-relative path, one-based line and column, a short explanation, and the source document/section. Errors SHALL denote provable API or structural violations; warnings SHALL denote source style choices or conservative checks. Results SHALL be ordered by path, position, then ID. The run SHALL distinguish error findings, warning-only findings, clean results, and configuration or parse failures.

#### Scenario: A style warning
- **WHEN** real code contains `$X^i$`
- **THEN** the diagnostic identifies `core.parenthesized-scripts`, warning grade, its position, and its source; it does not claim a Typst compilation failure.

#### Scenario: A malformed known API call
- **WHEN** real code contains `#project-ref("pde", <energy>, 7)`
- **THEN** the diagnostic identifies `core.project-ref-arguments` with error grade and the call position.

### Requirement: Suppress one construct with a reason
An own-line `// typst-lint: ignore-next <rule-id>[, <rule-id>...] -- <reason>` SHALL target the next non-comment construct, including multiline bodies. It SHALL suppress only listed IDs with diagnostic starts in that construct, then expire, except the bounded marker anchor below. Missing reasons, unknown IDs or absent following constructs SHALL be configuration failures. Suppression SHALL NOT hide configuration or parse failures.

#### Scenario: Intentional raw marker
- **WHEN** the following code is scanned inside an annotation scope

```typst
// typst-lint: ignore-next core.scoped-marker-tags -- external plugin requires this public tag
$ mark(x, tag: #<external-target>) $
```

- **THEN** `core.scoped-marker-tags` is suppressed for this display, while other IDs and a later raw marker remain eligible for reporting.

#### Scenario: Multiline construct
- **WHEN** a directive precedes `#highlighted[` whose body spans multiple lines and contains a display
- **THEN** the listed highlight rule is suppressed throughout that single call, with no effect on the next call.

#### Scenario: Invalid suppression
- **WHEN** a directive omits `-- <reason>` or appears at end of file with no following construct
- **THEN** the linter reports a configuration failure rather than silently disabling checks.

### Requirement: Bound compound paragraph suppression to one marker
The sole compound-rule exception SHALL let a directive targeting a proven standalone marker suppress `pde.paragraph-capitalization` for its immediately following eligible initial word. Every binding, body, language and single-line-gap precondition SHALL still hold. The exception SHALL NOT suppress later paragraphs or other rule IDs.

#### Scenario: Intentional lowercase after one proven marker
- **WHEN** a valid `ignore-next pde.paragraph-capitalization` directive precedes a resolved standalone marker with an immediately following eligible lowercase English initial word
- **THEN** that initial-word finding and edit are suppressed; a later marked paragraph and script findings in the prose remain unaffected.

#### Scenario: Unproven marked paragraph cannot expand an ignore
- **WHEN** a same-spelled marker is shadowed, its body or language is unknown, or intervening content defeats the exact adjacency contract
- **THEN** the directive does not create a capitalization context or acquire a wider following-prose scope.

### Requirement: Support bounded configuration ignores
The linter SHALL accept `[[ignore]]` entries with root-relative `paths` globs, explicit `rules` IDs, and a nonempty `reason`. They SHALL suppress only matching style findings. Inline suppression SHALL be preferred for a single construct; config ignores SHALL handle documented recurring exceptions. Path excludes SHALL remove whole inputs. No wildcard rule ID SHALL disable all rules accidentally.

#### Scenario: Legacy file exception
- **WHEN** an ignore entry selects `legacy/*.typ`, `core.scoped-marker-tags`, and a reason
- **THEN** only that rule is suppressed in matching files and all other checks still run.

#### Scenario: Out-of-scope file
- **WHEN** `vendor/**` is excluded
- **THEN** no style checks or inline suppression parsing run on files under that path.

### Requirement: Parse code and documentation by context
The linter SHALL distinguish prose, inline/display math, code, strings, comments, raw text, and nested content. Explicitly included Markdown SHALL expose `typst` fences and inline math examples to script checking, using original file positions. Ordinary quoted code, comments, and raw text in Typst SHALL NOT be treated as executed math. The exact teaching sentence ``Do not write bare scripts such as `$X^i$` or `$X_j$`.`` SHALL be exempt from script checking in documentation only.

#### Scenario: Guide examples use grouped scripts
- **WHEN** an included Markdown guide contains a `typst` fence with `$X^i$` or an inline example `$X_j$` outside the exempt sentence
- **THEN** `core.parenthesized-scripts` warns; replacing the examples with `$X^(i)$` and `$X_(j)$` removes those warnings.

#### Scenario: Keep the documented teaching counterexample
- **WHEN** the guide contains exactly ``Do not write bare scripts such as `$X^i$` or `$X_j$`.``
- **THEN** that teaching sentence is a non-violation and surrounding runnable examples remain checked.

#### Scenario: Ordinary string and comment
- **WHEN** a Typst file contains `#let example = "$X^i$"` and `// $X_j$`
- **THEN** neither produces a script diagnostic, but subsequent actual math `$X_j$` does.

### Requirement: Bound conclusions to static evidence
Checks SHALL use static bindings, literals and configured local files. Unresolved aliases/imports/targets/wrappers SHALL be reported as skipped with reasons. Malformed input SHALL fail parsing, except the declared scoped dictionary-call repair whose candidate MUST reparse successfully. Clean style results SHALL NOT assert compilation, catalog freshness or visual correctness.

#### Scenario: Shadowed helper name
- **WHEN** a file defines its own unrelated `#let footnote(body) = body`
- **THEN** PDE footnote rules do not diagnose those calls as the built-in footnote API.

#### Scenario: Dynamic catalog target
- **WHEN** `#project-ref(project-key, target)` uses values that cannot be resolved statically
- **THEN** argument count is checked but literal type and membership checks are reported as skipped without inventing a target.


### Requirement: Declare bounded paragraph capitalization language
Configuration MAY include `[capitalization] language = "en"` with a nonempty language tag. Unknown capitalization keys/types SHALL fail. A static effective source language SHALL override this declaration; dynamic or mixed source language SHALL remain unknown. Only supported English contexts SHALL receive the new PDE casing repair, and no language SHALL be inferred from ASCII spelling.

#### Scenario: No English evidence
- **WHEN** no configuration or static source declaration establishes English, or the effective source language is non-English or dynamic
- **THEN** paragraph capitalization is skipped with a reason, not guessed or reported as checked English prose.

#### Scenario: Explicit English default
- **WHEN** capitalization language is `en` and no source override exists
- **THEN** a future syntax adapter may use that declared default for otherwise eligible marked prose; configuration alone does not prove paragraph context.
