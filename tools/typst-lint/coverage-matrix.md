# All-rule coverage audit

**This is an incomplete implementation, not 33 passing rules.**

- Tested commit: `9d82fed7450e6e0ca50392ebc0452bb1fd296020`.
- Working-tree content fingerprint: `a449e72ab487549194c14a59c96761eb95f0db5decb73cbef21ed9af57acc407` (exact file hashes in the JSON).
- Generated: 2026-11-07T04:47:14.197562+00:00.
- Rule status: {'PARTIAL': 3, 'NOT_IMPLEMENTED': 30}. No rule is classified COMPLETE.
- 98 fixture executions; 109 real CLI invocations including isolated candidate reanalysis and mode probes.
- Observation counts: {'OBSERVED_SUBSET': 12, 'NOT_IMPLEMENTED': 60, 'UNSUPPORTED_CASE': 2, 'EXPECTED_PARSE_REFUSAL': 1, 'PARTIAL_CASE': 21, 'BLOCKED_COMPOSITION': 1, 'SPEC_CONFLICT': 1}.
- Audit issues: []. Revision stable during run: True.

## Reproduce

```sh
python3 tools/typst-lint/audit_rules.py
```

The official syntax adapter must already be built as documented in the tool README. This runner never builds, evaluates Typst imports, invokes a real compiler, or edits a consumer. Exit **2** means the audit finished with incomplete rule implementation/acceptance; it is not a success code. Exit 1 indicates an audit invariant failure, parse-blocked representative, or mismatch in claimed supported behavior.

The runner creates isolated temporary consumers from [fixtures.json](audit/fixtures.json), supplies the explicit repository package path except in its missing-path refusal case, and captures the full normalized CLI reports in [latest-evidence.json](audit/latest-evidence.json). `<repo>` and `<fixture>` are portable path placeholders, not missing evidence. Every input/config/catalog hash, proposed edit baseline, package hash, runtime/spec fingerprint and report is retained. Negative fixtures with absent/partial coverage are **not semantic passes**.

Only proposed-edit bytes are composed in memory and copied into a different temporary consumer for candidate reanalysis. Idempotence is verified only when all repaired rule/file pairs retain supported coverage, baseline-supported pairs have no downgrade or new refusal after mapping byte offsets, no unexpected active findings appear, and no edits remain. Missing/unsupported reanalysis is explicitly CANDIDATE_ANALYSIS_BLOCKED, never verified. This does not exercise protected source application, editor leases, convergence of all 33 rules, or the authoritative compiler gate. A PATH tripwire plus the reported compiler flag/output sentinel checks that these CLI invocations remain inert; it is not a proof about arbitrary external compiler paths.

The fixture inventory contains representative snippets of each normative positive/negative scenario. Most non-text-utils helper provenance cannot yet be resolved, and reports retain the exact unavailable-package reasons. Synthetic facade re-exports and flowbox fragments are not represented as trusted package implementations. No unresolved helper is promoted to a semantic pass.

## Known specification conflict

The repair row for `core.project-ref-arguments` spells `project-ref("pde", @energy)` as repairable. The official 0.15.1 syntax parser reports an error at `@` in that code argument, while the global parse-recovery policy allows only the declared dictionary-field call form. The `spec-conflict-ref-recovery` fixture records **SPEC_CONFLICT** and parser spans separately. The ordinary positive fixture uses the syntax-valid string target `"1.1.1"`; it remains NOT_IMPLEMENTED. A contract correction and its review are required before claiming this repair can run.

## 33-rule matrix

| Rule and normative spec | Repair contract | Status / implementation | Actual CLI smoke evidence | Remaining acceptance work |
| --- | --- | --- | --- | --- |
| [core.parenthesized-scripts](../../openspec/changes/typst-style-linter/specs/typst-core-style/spec.md#L11) | [automatic](../../openspec/changes/typst-style-linter/fix-policy.md#L11) | **PARTIAL**. [script_rule.py](script_rule.py), [syntax_client.py](syntax_client.py), [source_analysis.py](source_analysis.py) | positive: OBSERVED_SUBSET, findings=3, edits=6; negative: OBSERVED_SUBSET, findings=0, edits=0. Full records keyed by `core.parenthesized-scripts--…` in JSON. | Markdown extraction/teaching exemption and ambiguous escaped/commented operands are unsupported. Protected runtime apply/compile unavailable. |
| [core.scoped-function-call](../../openspec/changes/typst-style-linter/specs/typst-core-style/spec.md#L33) | [automatic](../../openspec/changes/typst-style-linter/fix-policy.md#L12) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `core.scoped-function-call--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [core.scoped-marker-tags](../../openspec/changes/typst-style-linter/specs/typst-core-style/spec.md#L46) | [diagnostic-only](../../openspec/changes/typst-style-linter/fix-policy.md#L13) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `core.scoped-marker-tags--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [core.scoped-anchor-access](../../openspec/changes/typst-style-linter/specs/typst-core-style/spec.md#L59) | [conditional](../../openspec/changes/typst-style-linter/fix-policy.md#L14) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `core.scoped-anchor-access--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [core.scoped-reference-access](../../openspec/changes/typst-style-linter/specs/typst-core-style/spec.md#L72) | [conditional](../../openspec/changes/typst-style-linter/fix-policy.md#L15) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `core.scoped-reference-access--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [core.local-overlay-api](../../openspec/changes/typst-style-linter/specs/typst-core-style/spec.md#L85) | [diagnostic-only](../../openspec/changes/typst-style-linter/fix-policy.md#L16) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `core.local-overlay-api--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [core.annotation-in-display](../../openspec/changes/typst-style-linter/specs/typst-core-style/spec.md#L98) | [diagnostic-only](../../openspec/changes/typst-style-linter/fix-policy.md#L17) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `core.annotation-in-display--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [core.annotation-marker-bounds](../../openspec/changes/typst-style-linter/specs/typst-core-style/spec.md#L111) | [diagnostic-only](../../openspec/changes/typst-style-linter/fix-policy.md#L18) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `core.annotation-marker-bounds--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [core.unique-scope-namespace](../../openspec/changes/typst-style-linter/specs/typst-core-style/spec.md#L124) | [diagnostic-only](../../openspec/changes/typst-style-linter/fix-policy.md#L19) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `core.unique-scope-namespace--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [core.project-ref-arguments](../../openspec/changes/typst-style-linter/specs/typst-core-style/spec.md#L137) | [conditional](../../openspec/changes/typst-style-linter/fix-policy.md#L20) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `core.project-ref-arguments--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [core.project-ref-catalog-target](../../openspec/changes/typst-style-linter/specs/typst-core-style/spec.md#L150) | [diagnostic-only](../../openspec/changes/typst-style-linter/fix-policy.md#L21) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `core.project-ref-catalog-target--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [core.single-book-show](../../openspec/changes/typst-style-linter/specs/typst-core-style/spec.md#L163) | [diagnostic-only](../../openspec/changes/typst-style-linter/fix-policy.md#L22) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `core.single-book-show--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [core.no-duplicate-book-reset](../../openspec/changes/typst-style-linter/specs/typst-core-style/spec.md#L176) | [conditional](../../openspec/changes/typst-style-linter/fix-policy.md#L23) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `core.no-duplicate-book-reset--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [core.no-set-theme](../../openspec/changes/typst-style-linter/specs/typst-core-style/spec.md#L189) | [diagnostic-only](../../openspec/changes/typst-style-linter/fix-policy.md#L24) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `core.no-set-theme--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [core.theme-in-context](../../openspec/changes/typst-style-linter/specs/typst-core-style/spec.md#L202) | [conditional](../../openspec/changes/typst-style-linter/fix-policy.md#L25) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `core.theme-in-context--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [pde.import-order](../../openspec/changes/typst-style-linter/specs/typst-pde-style/spec.md#L11) | [conditional](../../openspec/changes/typst-style-linter/fix-policy.md#L31) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `pde.import-order--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [pde.no-redundant-facade-import](../../openspec/changes/typst-style-linter/specs/typst-pde-style/spec.md#L38) | [conditional](../../openspec/changes/typst-style-linter/fix-policy.md#L32) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `pde.no-redundant-facade-import--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [pde.paragraph-marker-gap](../../openspec/changes/typst-style-linter/specs/typst-pde-style/spec.md#L51) | [automatic](../../openspec/changes/typst-style-linter/fix-policy.md#L33) | **PARTIAL**. [paragraph_rules.py](paragraph_rules.py), [static_resolver.py](static_resolver.py), [package_loader.py](package_loader.py), [source_analysis.py](source_analysis.py) | positive: OBSERVED_SUBSET, findings=1, edits=1; negative: OBSERVED_SUBSET, findings=0, edits=0. Full records keyed by `pde.paragraph-marker-gap--…` in JSON. | Only proven root prose with reviewed direct text-utils binding. Nested/body-start contexts and footnote-combined repair unsupported; comments can make repair unavailable. |
| [pde.paragraph-marker-target](../../openspec/changes/typst-style-linter/specs/typst-pde-style/spec.md#L79) | [automatic](../../openspec/changes/typst-style-linter/fix-policy.md#L34) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `pde.paragraph-marker-target--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [pde.prose-paragraph-marker](../../openspec/changes/typst-style-linter/specs/typst-pde-style/spec.md#L106) | [conditional](../../openspec/changes/typst-style-linter/fix-policy.md#L35) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `pde.prose-paragraph-marker--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [pde.continuation-line](../../openspec/changes/typst-style-linter/specs/typst-pde-style/spec.md#L127) | [conditional](../../openspec/changes/typst-style-linter/fix-policy.md#L36) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `pde.continuation-line--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [pde.display-continuation-gap](../../openspec/changes/typst-style-linter/specs/typst-pde-style/spec.md#L146) | [automatic](../../openspec/changes/typst-style-linter/fix-policy.md#L37) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `pde.display-continuation-gap--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [pde.proof-trailing-gap](../../openspec/changes/typst-style-linter/specs/typst-pde-style/spec.md#L166) | [automatic](../../openspec/changes/typst-style-linter/fix-policy.md#L38) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `pde.proof-trailing-gap--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [pde.footnote-trailing-gap](../../openspec/changes/typst-style-linter/specs/typst-pde-style/spec.md#L192) | [conditional](../../openspec/changes/typst-style-linter/fix-policy.md#L39) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `pde.footnote-trailing-gap--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [pde.prose-footnote-punctuation](../../openspec/changes/typst-style-linter/specs/typst-pde-style/spec.md#L222) | [conditional](../../openspec/changes/typst-style-linter/fix-policy.md#L40) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `pde.prose-footnote-punctuation--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [pde.aligned-footnote-spacer](../../openspec/changes/typst-style-linter/specs/typst-pde-style/spec.md#L235) | [conditional](../../openspec/changes/typst-style-linter/fix-policy.md#L41) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `pde.aligned-footnote-spacer--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [pde.unaligned-footnote-no-spacer](../../openspec/changes/typst-style-linter/specs/typst-pde-style/spec.md#L268) | [automatic](../../openspec/changes/typst-style-linter/fix-policy.md#L42) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `pde.unaligned-footnote-no-spacer--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [pde.prose-footnote-no-spacer](../../openspec/changes/typst-style-linter/specs/typst-pde-style/spec.md#L281) | [automatic](../../openspec/changes/typst-style-linter/fix-policy.md#L43) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `pde.prose-footnote-no-spacer--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [pde.standalone-display](../../openspec/changes/typst-style-linter/specs/typst-pde-style/spec.md#L294) | [automatic](../../openspec/changes/typst-style-linter/fix-policy.md#L44) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `pde.standalone-display--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [pde.text-only-highlight](../../openspec/changes/typst-style-linter/specs/typst-pde-style/spec.md#L314) | [conditional](../../openspec/changes/typst-style-linter/fix-policy.md#L45) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `pde.text-only-highlight--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [pde.theme-cancelmark](../../openspec/changes/typst-style-linter/specs/typst-pde-style/spec.md#L334) | [conditional](../../openspec/changes/typst-style-linter/fix-policy.md#L46) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `pde.theme-cancelmark--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [pde.flowbox-colored-border](../../openspec/changes/typst-style-linter/specs/typst-pde-style/spec.md#L347) | [conditional](../../openspec/changes/typst-style-linter/fix-policy.md#L47) | **NOT_IMPLEMENTED**. No backend | positive: NOT_IMPLEMENTED, findings=0, edits=0; negative: NOT_IMPLEMENTED, findings=0, edits=0. Full records keyed by `pde.flowbox-colored-border--…` in JSON. | Detection/repair backend absent; required binding/graph proof, exact edits/refusals, preservation, idempotence and applicable render assertions remain unverified. |
| [pde.paragraph-capitalization](../../openspec/changes/typst-style-linter/specs/typst-pde-style/spec.md#L361) | [conditional](../../openspec/changes/typst-style-linter/fix-policy.md#L49) | **PARTIAL**. [capitalization.py](capitalization.py), [paragraph_rules.py](paragraph_rules.py), [static_resolver.py](static_resolver.py), [package_loader.py](package_loader.py), [source_analysis.py](source_analysis.py) | positive: OBSERVED_SUBSET, findings=1, edits=1; negative: OBSERVED_SUBSET, findings=0, edits=0. Full records keyed by `pde.paragraph-capitalization--…` in JSON. | Only proven root prose, canonical marker spelling, bounded starter/language policy. Nested/dynamic contexts and marker-insertion convergence unsupported. |

## Normative scenario inventory

All scenario headings are indexed below and in the JSON with exact source lines. SMOKE_ONLY means at least one variant was run, **not** that all alternatives/preconditions in that scenario were exercised. Every scenario acceptance remains NOT_COMPLETE. Required exact-edit/refusal/idempotence/preservation/render dimensions are separately reported per rule; none is inferred from a zero-diagnostic run.

### core.parenthesized-scripts
- **Violation in actual mathematics**: SMOKE_ONLY; {'OBSERVED_SUBSET': 1}; fixtures `core.parenthesized-scripts--positive`. Acceptance NOT_COMPLETE.
- **Non-violation with grouped scripts**: SMOKE_ONLY; {'OBSERVED_SUBSET': 1}; fixtures `core.parenthesized-scripts--negative`. Acceptance NOT_COMPLETE.
- **Non-violation in the teaching line**: SMOKE_ONLY; {'UNSUPPORTED_CASE': 1}; fixtures `core.parenthesized-scripts--markdown-teaching`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): [tests/test_syntax.py](tests/test_syntax.py), [tests/test_suppression.py](tests/test_suppression.py), [tests/test_source_rules.py](tests/test_source_rules.py)

### core.scoped-function-call
- **Violation with field-call syntax**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.scoped-function-call--positive`. Acceptance NOT_COMPLETE.
- **Non-violation with receiver binding**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.scoped-function-call--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### core.scoped-marker-tags
- **Violation with a manual marker label**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.scoped-marker-tags--positive`. Acceptance NOT_COMPLETE.
- **Non-violation with scoped marker**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.scoped-marker-tags--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### core.scoped-anchor-access
- **Violation with a known raw marker anchor**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.scoped-anchor-access--positive`. Acceptance NOT_COMPLETE.
- **Non-violation with helpers**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.scoped-anchor-access--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### core.scoped-reference-access
- **Violation using the generated label**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.scoped-reference-access--positive`. Acceptance NOT_COMPLETE.
- **Non-violation using the public local API**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.scoped-reference-access--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### core.local-overlay-api
- **Violation with the raw API**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.local-overlay-api--positive`. Acceptance NOT_COMPLETE.
- **Non-violation with supported APIs**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.local-overlay-api--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### core.annotation-in-display
- **Violation after the equation**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.annotation-in-display--positive`. Acceptance NOT_COMPLETE.
- **Non-violation inside the equation**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.annotation-in-display--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### core.annotation-marker-bounds
- **Violation with only a plain label**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.annotation-marker-bounds--positive`. Acceptance NOT_COMPLETE.
- **Non-violation with either marker API**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.annotation-marker-bounds--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### core.unique-scope-namespace
- **Violation with explicit collisions**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.unique-scope-namespace--positive`. Acceptance NOT_COMPLETE.
- **Non-violation across automatic scopes**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.unique-scope-namespace--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### core.project-ref-arguments
- **Violation with extra or wrong arguments**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1, 'SPEC_CONFLICT': 1}; fixtures `core.project-ref-arguments--positive`, `core.project-ref-arguments--spec-conflict-ref-recovery`. Acceptance NOT_COMPLETE.
- **Non-violation with a global label**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.project-ref-arguments--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### core.project-ref-catalog-target
- **Violation against a supplied snapshot**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.project-ref-catalog-target--positive`. Acceptance NOT_COMPLETE.
- **Non-violation against a supplied snapshot**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.project-ref-catalog-target--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### core.single-book-show
- **Violation with layered document show rules**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.single-book-show--positive`. Acceptance NOT_COMPLETE.
- **Non-violation with one owner**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.single-book-show--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### core.no-duplicate-book-reset
- **Violation with duplicate reset ownership**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.no-duplicate-book-reset--positive`. Acceptance NOT_COMPLETE.
- **Non-violation with an explicit transfer**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.no-duplicate-book-reset--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### core.no-set-theme
- **Violation with a nonsettable target**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.no-set-theme--positive`. Acceptance NOT_COMPLETE.
- **Non-violation with settable elements**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.no-set-theme--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### core.theme-in-context
- **Violation without contextual resolution**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.theme-in-context--positive`. Acceptance NOT_COMPLETE.
- **Non-violation with contextual resolution**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `core.theme-in-context--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### pde.import-order
- **Violation with a reversed import group**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.import-order--positive`. Acceptance NOT_COMPLETE.
- **Non-violation with the existing grouping**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.import-order--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### pde.no-redundant-facade-import
- **Violation with redundant compatibility imports**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.no-redundant-facade-import--positive`. Acceptance NOT_COMPLETE.
- **Non-violation preserving compatibility**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.no-redundant-facade-import--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### pde.paragraph-marker-gap
- **Violation with an adjacent marker**: SMOKE_ONLY; {'OBSERVED_SUBSET': 1}; fixtures `pde.paragraph-marker-gap--positive`. Acceptance NOT_COMPLETE.
- **Non-violation with the required source gap**: SMOKE_ONLY; {'OBSERVED_SUBSET': 1}; fixtures `pde.paragraph-marker-gap--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): [tests/test_source_rules.py](tests/test_source_rules.py), [tests/test_source_rules_render.py](tests/test_source_rules_render.py)

### pde.paragraph-marker-target
- **Violation before structural content**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.paragraph-marker-target--positive`. Acceptance NOT_COMPLETE.
- **Non-violation before prose**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.paragraph-marker-target--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### pde.prose-paragraph-marker
- **Violation with an unmarked new paragraph**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.prose-paragraph-marker--positive`. Acceptance NOT_COMPLETE.
- **Non-violation with continuity or an explicit marker**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.prose-paragraph-marker--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### pde.continuation-line
- **Violation with split continuation sentences**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.continuation-line--positive`. Acceptance NOT_COMPLETE.
- **Non-violation with continuous source prose**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.continuation-line--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### pde.display-continuation-gap
- **Violation with an unmarked gap**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.display-continuation-gap--positive`. Acceptance NOT_COMPLETE.
- **Non-violation for continuation or a new marked idea**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.display-continuation-gap--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### pde.proof-trailing-gap
- **Violation with adjacent content**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.proof-trailing-gap--positive`. Acceptance NOT_COMPLETE.
- **Non-violation with a source gap**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.proof-trailing-gap--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### pde.footnote-trailing-gap
- **Violation after a source-line footnote**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.footnote-trailing-gap--positive`. Acceptance NOT_COMPLETE.
- **Non-violation using an explicit break**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.footnote-trailing-gap--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### pde.prose-footnote-punctuation
- **Violation with whitespace or misplaced punctuation**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.prose-footnote-punctuation--positive`. Acceptance NOT_COMPLETE.
- **Non-violation after either permitted punctuation mark**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.prose-footnote-punctuation--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### pde.aligned-footnote-spacer
- **Violation in either kind of aligned display**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.aligned-footnote-spacer--positive`. Acceptance NOT_COMPLETE.
- **Non-violation with the required spacer**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.aligned-footnote-spacer--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### pde.unaligned-footnote-no-spacer
- **Violation in a single-line unaligned display**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.unaligned-footnote-no-spacer--positive`. Acceptance NOT_COMPLETE.
- **Non-violation without a spacer**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.unaligned-footnote-no-spacer--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### pde.prose-footnote-no-spacer
- **Violation with a prose spacer**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.prose-footnote-no-spacer--positive`. Acceptance NOT_COMPLETE.
- **Non-violation with an ordinary prose footnote**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.prose-footnote-no-spacer--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### pde.standalone-display
- **Violation with prose on the display line**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.standalone-display--positive`. Acceptance NOT_COMPLETE.
- **Non-violation with separate source lines**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.standalone-display--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### pde.text-only-highlight
- **Violation highlighting math**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.text-only-highlight--positive`. Acceptance NOT_COMPLETE.
- **Non-violation with separate math**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.text-only-highlight--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### pde.theme-cancelmark
- **Violation using the bare cancellation element**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.theme-cancelmark--positive`. Acceptance NOT_COMPLETE.
- **Non-violation with the shared helper**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.theme-cancelmark--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### pde.flowbox-colored-border
- **Violation with a width-only border**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.flowbox-colored-border--positive`. Acceptance NOT_COMPLETE.
- **Non-violation using the active palette**: SMOKE_ONLY; {'NOT_IMPLEMENTED': 1}; fixtures `pde.flowbox-colored-border--negative`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): No implementation test module yet.

### pde.paragraph-capitalization
- **Supported English paragraph starter**: SMOKE_ONLY; {'OBSERVED_SUBSET': 1}; fixtures `pde.paragraph-capitalization--positive`. Acceptance NOT_COMPLETE.
- **Preserve math, names and language boundaries**: SMOKE_ONLY; {'PARTIAL_CASE': 11}; fixtures `pde.paragraph-capitalization--math-first`, `pde.paragraph-capitalization--name-iphone`, `pde.paragraph-capitalization--name-ebay`, `pde.paragraph-capitalization--non-ascii`, `pde.paragraph-capitalization--unknown-starter`, `pde.paragraph-capitalization--quote-first`, `pde.paragraph-capitalization--raw-first`, `pde.paragraph-capitalization--call-first`, `pde.paragraph-capitalization--reference-first`, `pde.paragraph-capitalization--single-letter`, `pde.paragraph-capitalization--unknown-language`. Acceptance NOT_COMPLETE.
- **Explicit intentional lowercase**: SMOKE_ONLY; {'OBSERVED_SUBSET': 1}; fixtures `pde.paragraph-capitalization--intentional-lowercase`. Acceptance NOT_COMPLETE.
- **Source language overrides configuration**: SMOKE_ONLY; {'PARTIAL_CASE': 2}; fixtures `pde.paragraph-capitalization--non-english`, `pde.paragraph-capitalization--dynamic-language`. Acceptance NOT_COMPLETE.
- **A new marker and capitalization converge**: SMOKE_ONLY; {'BLOCKED_COMPOSITION': 1}; fixtures `pde.paragraph-capitalization--new-marker-convergence`. Acceptance NOT_COMPLETE.
- Existing test sources (not rerun or counted as passes by this audit): [tests/test_capitalization.py](tests/test_capitalization.py), [tests/test_source_rules.py](tests/test_source_rules.py), [tests/test_source_rules_render.py](tests/test_source_rules_render.py)

## Work still required, in priority order

1. Keep fail-closed coverage reporting while implementing the missing 30 detection backends. Establish reviewed facade/import/include, builtin/shadowing, content-context and document-graph proofs before rules that depend on them. Do not count registry metadata or catalog validation as rule execution.
2. Complete interacting paragraph/display/proof/footnote rules, including marker insertion, body-start gaps, explicit prose parbreak composition and source-language/context proof. Verify all lowercase/name/math/non-English preservation and multi-pass convergence cases.
3. Implement annotation scope ownership/marker bounds, namespace graphs, project-reference/catalog lookups, book/reset ownership and theme rules. Diagnostic-only contracts must remain nonmutating; unavailable targets/names must never be invented.
4. Add Markdown extraction/source mapping and exact teaching-line exemption; complete every listed branch, refusal, suppression, exact edit, byte/newline/BOM/comment preservation and idempotence fixture for each repair row. Add the required combined highlight/display/paragraph and light/dark palette render assertions.
5. Independently verify protected transactions, editor generations, recovery journals and immutable compile snapshots before enabling source application or authoritative builds. Current check, dry-run, fix and compile invocations remain exit 2, source unchanged, compiler not invoked.

No consumer documentation, editor settings, actual document sources or real compilation outputs were changed by this audit.
