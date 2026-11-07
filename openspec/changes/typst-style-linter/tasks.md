# Tasks

The design follow-up is reviewed and merged first. Groups 2–7 are authorized implementation work on a separate branch/draft PR after that merge; unchecked items are not claims of existing code or completed rollout. Real-consumer enablement and bulk document rewrites are outside this implementation PR.

## 1. Specify the change (design scope)

- [x] 1.1 Initialize OpenSpec with no tool integrations and minimal config; verify `openspec/config.yaml` contains only `schema: spec-driven` and no unrelated tool folders appear.
- [x] 1.2 Read all four supplied source documents and author proposal/design; verify source paths, precedence, shared layout, pack selection, config shape, and suppression decisions are recorded.
- [x] 1.3 Author the original configuration spec and 32 style-rule contracts; verify 15 core and 17 PDE IDs each have a grade, source section, description, violation scenario, and non-violation scenario.
- [x] 1.4 Record judgment-only guidance separately; verify the design has 18 reasoned exclusions without invented thresholds or package pins.
- [x] 1.5 Run `openspec validate --all --strict` and inspect the diff for specs-only scope; write `/tmp/typst-linter-openspec-report.txt` with created files, counts, validation command/output, and open questions.

## 2. Build shared analysis and configuration (separate implementation branch)

- [ ] 2.1 Choose a parser/launcher and create the shared `tools/typst-lint/` boundary; document invocation and verify syntax fixtures distinguish code, prose, strings, raw text, inline math, and displays without modifying inputs.
- [ ] 2.2 Implement config, pack composition, glob selection, and optional entrypoint/catalog reads; document the consumer example and verify core/pde, invalid packs/IDs, excludes, malformed catalogs, and no-catalog scenarios from `typst-lint-configuration`.
- [ ] 2.3 Implement diagnostics, bounded ignores, and coverage reporting; document directive syntax and verify multiline scope, expiration, multiple IDs, required reasons, config ignores, deterministic ordering, and unsuppressible input failures.
- [ ] 2.4 Implement opt-in Markdown example extraction; verify original line positions, grouped guide examples, exactly the teaching-sentence exception, and an actual bare-script code violation outside that sentence.

## 3. Implement and document core rules (separate implementation branch)

- [ ] 3.1 Add script and scoped API checks; verify every violation/non-violation scenario for the first nine core rules, including renamed receivers, parent namespace separation, marker bindings, raw anchors, and retained `mannot-scope` compatibility, and document their coverage limits.
- [ ] 3.2 Add project-reference checks with read-only catalog fixtures; verify signature errors, literal membership, ambiguous/missing targets, and dynamic skips without guessing links or refreshing a catalog, and document optional catalog behavior.
- [ ] 3.3 Add book ownership/reset and named theme API checks; verify their core scenarios, explicit reset transfer, unrelated document isolation, context handling, and local shadowing, and document the entrypoint requirement.

## 4. Implement and document PDE extensions (separate implementation branch)

- [ ] 4.1 Add import, source-paragraph, and standalone-display checks; verify all corresponding PDE scenarios, known facade exports, `block`/`parbreak` boundaries, display continuations, proof EOF, and duplicate-finding avoidance, and document their conservative scope.
- [ ] 4.2 Add all five footnote rules; verify comma/period adjacency, no intervening whitespace, alignment by either `&` or a math line-break token, single-line unaligned math, prose spacer rejection, nested footnote math, and explicit breaks after source-line footnotes; document the context matrix.
- [ ] 4.3 Add highlight, cancellation, and flowbox-border checks; verify the remaining PDE positive/negative scenarios including intentional `cancelmark` stroke overrides and unrelated semantic figure colors; document the judgment-only exclusions.

## 5. Verify consumer integration (separate implementation branch)

- [ ] 5.1 Exercise one isolated consumer fixture selecting core and one selecting PDE; verify pack counts, stable grades/provenance, path boundaries, skipped checks, suppression counts, check/dry-run read-only behavior, protected fixes and accurate partial-coverage reporting across complete runs.
- [ ] 5.2 Reconcile the implemented catalog with all 33 contracts and 18 exclusions; verify no extra style policy slipped in, run the chosen acceptance suite and OpenSpec strict validation, and report remaining coverage gaps before proposing separately authorized consumer rollout.


## 6. Implement automatic repairs and source protection

- [ ] 6.1 Implement every row of `fix-policy.md`, including diagnostic-only outcomes and refusal reasons; test exact edits, original comments/newlines/BOM/Unicode, suppressions and all preconditions.
- [ ] 6.2 Add deterministic edit composition, eight-pass convergence, idempotence and post-fix complete analysis; verify footnote/paragraph, highlight/display and import interactions without introducing extra policies.
- [ ] 6.3 Add canonical path boundaries, nonregular/symlink/hardlink rejection, editor-version/exclusive-writer contract, root locks, durable journals and conflict-safe recovery; inject partial write, crash and concurrent user-edit failures.
- [ ] 6.4 Compile/render isolated effect fixtures for paragraph indent/capitalization, footnotes, scoped helpers, cancellation and light/dark borders using explicit package paths, without installation or real consumer edits.

## 7. Implement and verify precompile integration

- [ ] 7.1 Implement shared compile dispatch, absolute real-compiler recursion checks, snapshot/resource consistency, output publication guards, signal/status forwarding and fail-closed coverage. Test paths with spaces, arguments/themes, missing tools, unsafe input and compiler failures with spies and real isolated fixtures.
- [ ] 7.2 Add CI clean-source check and explicitly configured disposable fix-first examples; verify neither changes/pushes real source or emits an artifact on failed checks.
- [ ] 7.3 Add serialized/debounced watch generations, dependency/config/catalog invalidation, own-write filtering and stale-generation cancellation; verify each rebuild is gated rather than delegating to raw watch.
- [ ] 7.4 Prototype a versioned editor/Tinymist save/export adapter and prove actual compile-on-save/preview/export interception. Keep unsupported routes visibly unmanaged; do not enable it on real projects in this PR.
- [ ] 7.5 Provide a per-project/entrypoint/route inventory, AGENTS.md guidance and rollback template for later all-project onboarding. Verify claims match tested coverage, including explicit draft-preview exceptions.

## 8. Review the automatic-fix design follow-up

- [x] 8.1 Verify original PR #2 is already merged and base this follow-up on current origin/main; preserve existing worktrees.
- [x] 8.2 Replace historical read-only restrictions, add all 32 repair policies and protected-fix/build-gate capabilities, retaining 15 core + 17 PDE IDs and 18 judgment exclusions.
- [x] 8.3 Independently review the exact design diff, resolve findings, and pass OpenSpec strict validation and catalog consistency checks; final review confirmed the compiler/publication status and partial-fix policies.
- [x] 8.5 Merge the design follow-up after checking the remote head and repository requirements (PR #3, `b8c8f186`).
- [x] 8.4 Branch implementation from merged main, start a tested foundation and draft PR #4, and report implemented versus pending coverage without deploying or mass-editing consumers.


## 9. Initial implementation foundation (partial, not a working linter)

- [x] 9.1 Add shared Python launchers and the initial 32-rule metadata/repair catalog, strict config and read-only selection/catalog reporting. All real runs explicitly fail unsupported analysis with exit 2.
- [x] 9.2 Add tested pure diagnostic ordering/path-ignore and immutable byte-edit/convergence primitives. Do not treat these as syntax analysis, actual repairs or protected filesystem writes.
- [x] 9.3 Verify 22 isolated foundation tests, including compiler non-invocation and previous-output preservation; document 0 implemented rules and remaining parser, transaction, snapshot and route work.
- [x] 9.4 Correct the design's factual capitalization claim after an isolated Typst 0.15.1 characterization: current text-utils indents but does not uppercase; preserve source bytes and record the compatibility gap without changing packages or consumers.


## 10. Add the requested paragraph capitalization rule

- [x] 10.1 Specify the 33rd rule, `pde.paragraph-capitalization`: bounded marked English prose, explicit starter set, source-only initial-byte repair, context/language exclusions and source-vs-helper ownership.
- [x] 10.2 Implement and test the pure parser-context rule component, explicit language config and preservation/refusal cases. Do not count this as end-to-end rule coverage before a real parser and protected writer are connected.
- [x] 10.3 Verify marker-plus-capitalization convergence and isolated render fixtures for explicit uppercase source, indentation and protected names; pass independent review and all 33 tests with opt-in rendering enabled. The draft PR remains incomplete end-to-end.


## 11. Wire official syntax analysis (partial coverage)

- [x] 11.1 Pin/build official typst-syntax 0.15.1 with a bounded, syntax-only versioned adapter; validate hashes, byte spans, tree links, limits and timeout on the Python side. No runtime dependency fetch/evaluation.
- [x] 11.2 Analyze real .typ script operands, produce exact reparsed/idempotent partial plans, and test nesting/chains/primes/fractions/Unicode and data-context exclusions. Keep malformed syntax, directives, escaped/commented operands and Markdown limits explicit.
- [x] 11.3 Preserve status 2/no mutation/no compiler while required rule coverage is incomplete; expose per-file coverage and partial-plan baseline evidence rather than advertise a working all-project gate.
- [x] 11.4 Pass independent parser review, rustfmt/Clippy, three Rust tests and 51 Python tests including opt-in rendering; preserve all existing consumers.
- [x] 11.6 Verify repository-only remote CI on Python 3.11/3.15 for exact commit 452484c; both passed run 42338058080. Retain the Linux Cargo hardlink regression without relaxing writable-source protection.
- [x] 11.5 Implement and independently review a protected offline transaction component against isolated fixtures, without exposing writes through incomplete CLI analysis. Verify staged metadata after writes/fsync and preserve later edits during recovery; this is not compiler snapshotting or an editor lease implementation.
- [x] 11.7 Resolve own-line inline suppressions through proven Markup/Code AST sibling scopes; verify full multiline targets, expiration, reasons/IDs, balanced partial fixes and explicit refusal of ambiguous ownership.

## 12. Connect bounded static provenance and paragraph rules

- [x] 12.1 Resolve direct imports, aliases, shadowing and literal effective language from actual AST nodes. Require explicit immutable reviewed package bytes; refuse unknown versions/content, local facades, dynamic effects and deferred prose rather than infer bindings from spelling.
- [x] 12.2 Add explicit read-only `--package-path` loading with manifest/source hashes, bounded reads, no-follow paths and concurrent-replacement checks. Never discover or install packages implicitly.
- [x] 12.3 Connect root-prose capitalization and paragraph-marker-gap to those proofs, including source-language precedence, suppression-anchor expiration, LF/CRLF/BOM preservation and unresolved combined footnote repairs.
- [x] 12.4 Verify combined script/paragraph proposals by reparsing and repeated analysis; run real source-to-render fixtures proving uppercase, indentation and protected names. Preserve status 2/no CLI writes/compiler while required coverage is incomplete. Independent final suite: 183 tests including both real-render tests passed.
- [x] 12.5 Independently review the implementation and exact pushed-commit Linux matrix; both Python 3.11/3.15 jobs passed for 9d82fed (run 42339489217). Document remaining facade/graph, nested-body and rule coverage without claiming all-project enforcement.

## 13. Audit every rule without hiding missing implementation

- [x] 13.1 Index all 33 IDs and 70 then-current normative scenarios, with representative positive/negative fixtures, implementation links and explicit missing acceptance work.
- [x] 13.2 Run 98 isolated fixtures through 109 actual CLI calls at runtime baseline 9d82fed; retain portable hashes/reports and verify no source/config/catalog changes or compiler invocation. Report 30 NOT_IMPLEMENTED, 3 PARTIAL and 0 COMPLETE rather than count unsupported negatives as passes.
- [x] 13.3 Independently review candidate reanalysis classification; require retained per-file/rule coverage, no new refusal or finding, and zero subsequent edits. Ten harness regressions plus the full 193-test suite passed; audit commit 58111d8 passed both Linux jobs (run 42340126288).
- [x] 13.4 Record and independently verify the project-ref bare-@ syntax conflict. Correct the repair contract to the exact valid `[@label]` content form, with unique catalog proof; leave malformed bare references as parse failures.

## 14. Implement the project-reference rules

- [x] 14.1 Add explicitly verified project-references package provenance and a single immutable validated catalog snapshot, retaining bounded read-only behavior and digest compatibility. Reject duplicate keys and nonstandard NaN/Infinity, including unused metadata.
- [x] 14.2 Implement bounded root argument shape/type and catalog membership checks without guessing unresolved aliases, missing/ambiguous labels or external destinations; broader graph/capture coverage remains partial.
- [x] 14.3 Implement only the corrected exact content-reference-to-label repair, its refusal cases, candidate reparse/idempotence and suppression ownership. An isolated actual-CLI-to-render/query fixture verifies the exact reference URL/page/text and unchanged target identity.
- [x] 14.4 Independently review, run the complete suite and regenerate the all-rule audit before publishing the next implementation checkpoint. No consumer rollout or compiler gate enablement.

## 15. Add typed display analysis and safe source-layout proposals

- [x] 15.1 Extend the pinned parser bridge to schema 2 / adapter 0.2.0 with typed Equation.block display flags and strict field validation. Preserve dependency versions; pass rustfmt, Clippy and four Rust tests.
- [x] 15.2 Implement bounded root standalone-display and display-continuation-gap backends. Preserve same-line semantic labels, formula/text bytes, comments, BOM/Unicode and newline style; refuse ambiguous/nested ownership.
- [x] 15.3 Reparse and verify retained literal prose remains prose, refusing newline-induced heading/list/term reinterpretation. Preserve directive ownership and explicitly refuse combinations that require an unsupported second repair pass.
- [x] 15.4 Independently review and verify seven-backend composition through the real read-only CLI. Isolated display compilation/query preserves the equation's semantic label and text order. Final local suite: 262 tests, including four opt-in render tests, passed.

## 16. Refresh the complete rule audit for the checkpoint

- [x] 16.1 Run 136 isolated fixtures through 159 real CLI invocations, indexing all 72 current normative scenario headings. Report 26 NOT_IMPLEMENTED, 7 PARTIAL and 0 COMPLETE, with no unsupported negative case counted as a semantic pass.
- [x] 16.2 Separate four expected invalid-catalog input refusals and two expected parser refusals. The corrected reference contract removes the earlier SPEC_CONFLICT classification; no destination or malformed repair is guessed.
- [x] 16.3 Retain immutable candidate-proof checks, all package artifact fingerprints, available-backend versus evaluated-coverage distinctions, and no source writes or compiler invocation. Regenerate final evidence after this status update before committing the checkpoint.


## 17. Extend bounded rule coverage and recovery evidence

- [x] 17.1 Verify real SIGKILL transaction recovery in separate interpreters at pre/post-rename boundaries and during recovery; preserve later edits and same-byte replacement conflicts. These tests do not establish power-loss durability or an editor lease.
- [x] 17.2 Wire bounded continuation, footnote, structural-marker, theme, highlight and four scoped-helper backends through the real CLI, while leaving all write/compile routes fail-closed.
- [x] 17.3 Preserve diagnostic and directive ownership across byte shifts, and require candidate rule/parse/refusal coverage retention instead of accepting a zero-edit result that lost analysis facts.
- [x] 17.4 Verify builtin and reviewed text-utils highlight candidates with isolated real compile/query evidence. Certify only text-only generated fragments as bounded transparent output; retain unknown nested/option/show cases.
- [x] 17.5 Add actual resolver-produced math-blocks spacer identity and canonical insertion proofs, verify aliases/shadowing/mutation, integrate all five footnote backends and independently review exact proposals.
- [x] 17.6 Regenerate all 33-rule evidence and run final local checks before publishing the draft checkpoint. Final local checks: 356 Python tests (355 passed, one filesystem setuid skip), four Rust tests, formatting/Clippy, strict OpenSpec and whitespace checks. Audit: 202 CLI invocations, 21 PARTIAL / 12 NOT_IMPLEMENTED / 0 COMPLETE, with no audit invariant failures. Consumer rollout and complete rule acceptance remain outside this checkpoint.


## 18. Add proof boundaries and scoped reference repairs

- [x] 18.1 Add exact reviewed proof-helper identity and bounded standalone-root source-gap proposals. Preserve labels, original bytes and non-whitespace syntax; refuse ambiguous comments, unknown ownership and synthetic EOF/body-end paragraphs. Verify isolated actual-CLI and render effects.
- [x] 18.2 Retain the first namespace declaration in diagnostics while mapping only that trusted related-location format through preceding edits. Never reinterpret literal catalog project text as a source offset.
- [x] 18.3 Require a separately reviewed mannot public namespace snapshot before repairing the exact local scoped-reference form. Keep its fixed optional loader evidence separate from raw API bindings and retain safe descriptor traversal, refusal and race tests.
- [x] 18.4 Complete independent review and final local checks: 411 Python tests (410 passed, one filesystem setuid skip), four Rust tests, formatting/Clippy, strict OpenSpec and whitespace checks. Refreshed audit: 172 fixtures, 212 CLI invocations, 37 verified candidate reanalyses, 22 PARTIAL / 11 NOT_IMPLEMENTED / 0 COMPLETE, with no invariant failures. Preserve fail-closed source writes and compiler dispatch; keep consumer rollout separate.


## 19. Prove bounded prose marker insertion

- [x] 19.1 Track exact canonical marker availability and an already installed reviewed paragraph-tab show transformation in source-bound immutable resolver evidence. Preserve lexical alias/shadowing/mutation and unknown-output refusals.
- [x] 19.2 Diagnose literal root prose boundaries separated by existing source paragraph breaks and propose only composition-stable insertions. Preserve source bytes, indentation, effective language and every directive target; do not manufacture imports, show rules or casing edits.
- [x] 19.3 Integrate explicit backend coverage, focused CLI/render regressions and all-rule audit fixtures without weakening candidate coverage checks. Keep nested/structural/footnote boundaries and unsupported starter composition explicit.
- [x] 19.4 Complete independent review, 438 Python tests (437 passed, one filesystem setuid skip), four Rust tests, formatting/Clippy, strict OpenSpec and whitespace checks. Audit: 177 fixtures, 219 CLI invocations, 39 verified candidate reanalyses, 23 PARTIAL / 10 NOT_IMPLEMENTED / 0 COMPLETE, with no invariant failures. A 1,000-paragraph independent probe completed backend analysis in 0.287s and fully verified shared analysis in 0.94s. Production source writes and compiler dispatch remain closed.
- [x] 19.5 Publish checkpoint b28e29aa3f018375b1c53783f9f9d1d36dd27029 and verify both Python 3.11/3.15 CI jobs for that exact PR head (run 42351656010).


## 20. Establish bounded book ownership

- [x] 20.1 Add exact paired math-book and math-blocks source provenance, immediate root show-transform evidence and literal reset-option ownership without broadening wildcard or call-result assumptions.
- [x] 20.2 Carry an explicit immutable configured-entrypoint snapshot through selected source analysis and validated candidate reanalysis. Do not infer ownership for unrelated, excluded or included files.
- [x] 20.3 Diagnose proven duplicate book owners and separate reset ownership; retain an explicit reset-removal refusal because full-body heading/reference transformation equivalence remains unproven. Keep missing-owner and graph-wide claims unavailable.
- [x] 20.4 Complete independent review and focused parser/CLI/render evidence, including secure configured-entrypoint symlink/FIFO reread regressions. Combined local verification and all-rule audit are recorded in section 21. Keep production source writes and compiler dispatch closed.


## 21. Recognize builtin mathematical cancellation

- [x] 21.1 Implement distinct mathematical/code lookup and actual builtin module/member identity, preserving lexical aliases, shadowing, wildcard uncertainty and source-order effects. Do not infer a global math module inside math fallback.
- [x] 21.2 Diagnose proven cancellation calls without inventing cancelmark replacement or palette semantics. Verify unaliased builtin-module imports preserve their actual source identifier, including the shadow-module regression.
- [x] 21.3 Review the independent worktree implementation and integrate it with book provenance while retaining math-book partial-wildcard safeguards. Add explicit audit fixtures and no-edit contract documentation.
- [x] 21.4 Complete combined independent review, 483 Python tests (482 passed, one filesystem setuid skip), four Rust tests, formatting/Clippy, strict OpenSpec and whitespace checks. Audit: 191 fixtures, 233 CLI invocations, 39 verified candidate reanalyses, 26 PARTIAL / 7 NOT_IMPLEMENTED / 0 COMPLETE, with no invariant failures. Keep consumer rollout and production gates separate.
- [x] 21.5 Publish bedcf2e2d6de612cc472fcc6df44bc1f6dc7e167 and verify both Python 3.11/3.15 CI jobs for that exact draft PR head (run 42353340232).


## 22. Capture selected sources and cover remaining bounded rules

- [x] 22.1 Capture immutable selected source bytes with retained-descriptor safety checks, 1 MiB per-file and 64 MiB aggregate bounds, and safe rereads before parsing. Preserve metadata-only collect reports, entrypoint identity and UTF-8 refusals. Independent 55-test capture/foundation/book review passed.
- [x] 22.2 Add fixed authentic mannot/CeTZ supplemental artifacts and definition-specific provenance. Preserve namespace-only refusal, no ambient package discovery and partial wildcard invalidation.
- [x] 22.3 Add bounded marker-tag/bounds diagnostics, conditional same-display scoped anchor proposals and local-overlay diagnostics without inferring arbitrary runtime geometry.
- [x] 22.4 Add immutable relative-import graph capture and pure export identity, conditional disjoint import ordering and exact redundant-facade removal. Preserve original traversal checks, race refusal, resource budgets and exact moved-statement ownership in candidate checks.
- [x] 22.5 Add whole-module flowbox fingerprint and width-preserving theme color proposals, with isolated light/dark rendering evidence. Never infer ownership from copied helper fragments.
- [x] 22.6 Complete independent feature review, 637 Python tests with rendering enabled (636 passed, one filesystem setuid skip), four Rust tests, formatting/Clippy, strict OpenSpec and whitespace checks. Frozen audit: 209 fixtures, 255 actual CLI calls, 43 verified candidate reanalyses, 33 PARTIAL / 0 NOT_IMPLEMENTED / 0 COMPLETE, with zero invariant issues. All backends remain bounded; no production write/compiler route is enabled.
- [x] 22.7 Publish d3feb24d5b2cb5fe0207b5febe5f1078aeb6d8dd and verify both Python 3.11/3.15 jobs for exact head CI run 42355901861. PR description, merge, deployment and consumer rollout remain unchanged.


## 23. Converge real automatic-repair interactions

- [x] 23.1 Add at most eight actual parse/reanalysis passes with repeated-state/conflict/refusal checks, source-origin mappings, exact baseline replay and retained multi-rule attribution. Never substitute synthetic complete-analysis flags.
- [x] 23.2 Compose highlight/display, display/continuation and scoped-call/reference repairs while preserving directive targets and surviving source-origin anchors. Keep new final findings or suppression changes fail-closed.
- [x] 23.3 Permit only the pure capitalization policy's exact eligible lowercase initial to follow marker insertion; real subsequent parsing/resolution supplies the edit. Default direct marker behavior and protected-name/language/provenance refusals remain unchanged.
- [x] 23.4 Authenticate serialized repair evidence against independent captured raw-analysis contexts. Recompute actual rule proposals, composition, attribution and final zero-edit analysis; reject stripped/forged histories, wrong hashes/types/pass counts, cycles and lost ownership.
- [x] 23.5 Complete independent review, 678 Python tests with rendering enabled (677 passed, one filesystem setuid skip), four Rust tests, formatting/Clippy, strict OpenSpec and whitespace checks. Frozen audit: 213 fixtures, 265 actual CLI calls, 49 verified candidate reanalyses, 33 PARTIAL / 0 NOT_IMPLEMENTED / 0 COMPLETE, with zero invariant issues. Full rule acceptance, production source writes, compiler dispatch and consumer rollout remain separate.
- [x] 23.6 Publish 5bcb84960e8dd5c714f4506231e5a41efdd7eedb and verify both Python 3.11/3.15 jobs for exact head CI run 42358393262. PR metadata, merge and rollout remain unchanged.


## 24. Check bounded Markdown math examples

- [x] 24.1 Extract explicitly included Typst fences and inline math examples with exact original-byte mapping, bounded ambiguity refusals and the exact documentation teaching exemption.
- [x] 24.2 Integrate script-only Markdown analysis, actual fence directive scopes and config ignores without applying other Typst style rules or resolving documentation imports.
- [x] 24.3 Authenticate Markdown repair candidates from independent immutable captured contexts and preserve source mapping, parsing, suppression ownership and zero-edit idempotence.
- [x] 24.4 Complete independent adversarial/resource review, 730 Python tests with rendering enabled (729 passed, one filesystem setuid skip), four Rust tests, formatting/Clippy, strict OpenSpec and whitespace checks. Frozen audit: 219 fixtures, 276 actual CLI calls, 54 verified candidate reanalyses, 33 PARTIAL / 0 NOT_IMPLEMENTED / 0 COMPLETE, with zero invariant issues. Full acceptance and production writes/compiler routes remain separate.
- [x] 24.5 Publish 6ecca8f1dd54fae2d92808ce6df20fbd99c46598 and verify both Python 3.11/3.15 jobs for exact head CI run 42359676777. PR metadata, merge and rollout remain unchanged.


## 25. Compose bounded prose-footnote paragraph repairs

- [x] 25.1 Prove canonical standalone builtin paragraph-break availability without restoring state after unknown shows or output, and preserve immutable source-bound resolver evidence.
- [x] 25.2 Share exact literal root paragraph/footnote ownership. Let the gap rule insert a missing break before an existing marker, and the marker rule own the complete break-plus-marker repair when a marker is missing.
- [x] 25.3 Preserve comments, directive targets, ignored-rule ownership, original prose bytes, existing breaks and language/casing refusals. Require actual followup capitalization and authenticated zero-edit reanalysis.
- [x] 25.4 Complete independent exact-source review, 764 Python tests with rendering enabled (763 passed, one filesystem setuid skip), four Rust tests, formatting/Clippy, strict OpenSpec and whitespace checks. Frozen audit: 225 fixtures, 286 actual CLI calls, 58 verified candidate reanalyses, 33 PARTIAL / 0 NOT_IMPLEMENTED / 0 COMPLETE, with zero invariant issues. Production writes, compilation and rollout remain closed.
- [x] 25.5 Publish 1532f16bc2879355ab6ac17ab79c528a23f17786 and verify both Python 3.11/3.15 jobs for exact head CI run 42360602612. PR metadata, merge and rollout remain unchanged.


## 26. Recognize helpers through bounded local facades

- [x] 26.1 Add a separate exact-hash text-utils initializer certificate and immutable complete facade export identities without expanding import-order/removal purity authority.
- [x] 26.2 Preserve named/renamed import, alias, shadowing and wildcard-refusal semantics in source-bound resolver evidence, including fresh candidate graph rebasing.
- [x] 26.3 Capture required relative dependencies before the single explicit package load in both packs, using only captured bytes for transitive package requests and preserving safety/resource refusals.
- [x] 26.4 Complete independent adversarial/source review, 814 Python tests with rendering enabled (813 passed, one filesystem setuid skip), four Rust tests, formatting/Clippy, strict OpenSpec and whitespace checks. Frozen audit: 232 fixtures, 297 actual CLI calls, 62 verified candidate reanalyses, 33 PARTIAL / 0 NOT_IMPLEMENTED / 0 COMPLETE, with zero invariant issues. Production writes, compilation and rollout remain closed.
- [ ] 26.5 Publish this checkpoint and verify both Python 3.11/3.15 jobs for the exact draft PR head. Keep PR metadata, merge and rollout unchanged.
