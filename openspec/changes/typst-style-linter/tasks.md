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
