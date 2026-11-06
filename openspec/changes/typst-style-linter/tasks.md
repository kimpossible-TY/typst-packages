# Tasks

This delivery is specifications only. Group 1 records the current authoring work. Groups 2–5 are a deferred implementation checklist for a separately authorized task; unchecked items do not imply any linter code exists or should be written in this session.

## 1. Specify the change (current scope)

- [x] 1.1 Initialize OpenSpec with no tool integrations and minimal config; verify `openspec/config.yaml` contains only `schema: spec-driven` and no unrelated tool folders appear.
- [x] 1.2 Read all four supplied source documents and author proposal/design; verify source paths, precedence, shared layout, pack selection, config shape, and suppression decisions are recorded.
- [x] 1.3 Author the configuration spec and all 32 style-rule contracts; verify 15 core and 17 PDE IDs each have a grade, source section, description, violation scenario, and non-violation scenario.
- [x] 1.4 Record judgment-only guidance separately; verify the design has 18 reasoned exclusions without invented thresholds or package pins.
- [x] 1.5 Run `openspec validate --all --strict` and inspect the diff for specs-only scope; write `/tmp/typst-linter-openspec-report.txt` with created files, counts, validation command/output, and open questions.

## 2. Build shared analysis and configuration (deferred)

- [ ] 2.1 Choose a parser/launcher and create the shared `tools/typst-lint/` boundary; document invocation and verify syntax fixtures distinguish code, prose, strings, raw text, inline math, and displays without modifying inputs.
- [ ] 2.2 Implement config, pack composition, glob selection, and optional entrypoint/catalog reads; document the consumer example and verify core/pde, invalid packs/IDs, excludes, malformed catalogs, and no-catalog scenarios from `typst-lint-configuration`.
- [ ] 2.3 Implement diagnostics, bounded ignores, and coverage reporting; document directive syntax and verify multiline scope, expiration, multiple IDs, required reasons, config ignores, deterministic ordering, and unsuppressible input failures.
- [ ] 2.4 Implement opt-in Markdown example extraction; verify original line positions, grouped guide examples, exactly the teaching-sentence exception, and an actual bare-script code violation outside that sentence.

## 3. Implement and document core rules (deferred)

- [ ] 3.1 Add script and scoped API checks; verify every violation/non-violation scenario for the first nine core rules, including renamed receivers, parent namespace separation, marker bindings, raw anchors, and retained `mannot-scope` compatibility, and document their coverage limits.
- [ ] 3.2 Add project-reference checks with read-only catalog fixtures; verify signature errors, literal membership, ambiguous/missing targets, and dynamic skips without guessing links or refreshing a catalog, and document optional catalog behavior.
- [ ] 3.3 Add book ownership/reset and named theme API checks; verify their core scenarios, explicit reset transfer, unrelated document isolation, context handling, and local shadowing, and document the entrypoint requirement.

## 4. Implement and document PDE extensions (deferred)

- [ ] 4.1 Add import, source-paragraph, and standalone-display checks; verify all corresponding PDE scenarios, known facade exports, `block`/`parbreak` boundaries, display continuations, proof EOF, and duplicate-finding avoidance, and document their conservative scope.
- [ ] 4.2 Add all five footnote rules; verify comma/period adjacency, no intervening whitespace, alignment by either `&` or a math line-break token, single-line unaligned math, prose spacer rejection, nested footnote math, and explicit breaks after source-line footnotes; document the context matrix.
- [ ] 4.3 Add highlight, cancellation, and flowbox-border checks; verify the remaining PDE positive/negative scenarios including intentional `cancelmark` stroke overrides and unrelated semantic figure colors; document the judgment-only exclusions.

## 5. Verify consumer integration (deferred)

- [ ] 5.1 Exercise one isolated consumer fixture selecting core and one selecting PDE; verify pack counts, stable grades/provenance, path boundaries, skipped checks, suppression counts, and read-only behavior across complete runs.
- [ ] 5.2 Reconcile the implemented catalog with all 32 contracts and 18 exclusions; verify no extra style policy slipped in, run the chosen acceptance suite and OpenSpec strict validation, and report remaining coverage gaps before proposing consumer opt-in.
