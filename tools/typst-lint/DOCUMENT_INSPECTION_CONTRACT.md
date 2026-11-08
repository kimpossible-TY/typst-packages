# Read-only document inspection

`typst-lint inspect-document --root /absolute/project --entrypoint main.typ`
provides an explicit literal-include inventory, syntax-local math-script
proposals and source display-layout diagnostics. `--format json` returns schema 1;
the default text format prints
findings with their source paths, lines and columns.

The root must be canonical and absolute; the entrypoint must be a canonical
project-relative `.typ` path. The existing safe snapshot reader captures the
entrypoint, then the include collector independently captures and checks the
same bytes and the complete bounded literal include closure. Source and tree
hashes, include-site chains, repeated occurrences and unresolved sites remain
separate. Literal includes inside a call or other opaque container are followed
as syntax. This does not establish that an occurrence executes.

The command explicitly selects the fixed `book-v1` budget: 64 sources and
400,000 aggregate syntax nodes. All other include limits remain unchanged.
Ordinary callers retain the default `standard-v1` budget. The scoped paragraph
analyzer retains its independent limits and conditional interpretation; the
command does not use its proposals.

The default `--rule-set core` inspects each unique captured source once for
`core.parenthesized-scripts`. Explicit `--rule-set pde` also enables
`pde.standalone-display` and `pde.display-continuation-gap`;
`rule_set` and `coverage.available_backends`
record that selection. Inline suppression directives are honored for both rules.
The document route additionally accepts a direct `#`-escaped function-call script
operand and groups the hash together with the call. Whitespace before the hash is
preserved; comments between the operator and operand and non-call escapes remain
unsupported. This opt-in analysis does not change the ordinary single-source or
protected-write profiles. Candidates still require fresh parsing and script
reanalysis, and the result grants no source-application authority.
Consumer configuration is deliberately not loaded, so its include/exclude and
path-ignore settings do not select or suppress this explicit document inventory.
The report states this choice. By default, import targets and packages are not
resolved. Explicit PDE selection with `--package-path` captures bounded relative
import sources and six pinned package namespace roots for separate conditional
helper hints. It does not resolve full package initialization dependencies,
reference catalogs or runtime resources. See
`CONDITIONAL_HIGHLIGHT_CONTRACT.md` for the separate evidence and refusal model.
The explicit namespace route selects conditional highlight and paragraph-marker
gap hints. Each has its own metadata, suppression and coverage reasons; neither
becomes an available source backend or a script proposal.
The namespace route explicitly enables the bounded scoped-callback context.
Callback findings add call/body byte spans and opaque parameter names in
`callback_context`, referencing the existing source-bound `content_context`
routes. Empty callback metadata is omitted to preserve previous root/literal
records. Its variable entries are included in report item accounting.

JSON includes:

- `include_context`: selected budget, source hashes/counts, edges, occurrences
  and explicit unresolved containment, missing or dynamic requests.
- `diagnostics` and `suppressed`: source-style findings over the captured ASTs.
- `conditional_diagnostics`, `conditional_suppressed` and
  `conditional_coverage`: opt-in helper namespace hints with explicit outstanding
  conditions and no edit permission. `namespace_evidence` records the retained
  package/source facts; nested hints include the admitted wrapper name routes in
  `content_context`, counted against the report budget. Conditional hints do not remove a rule from
  `coverage.uninspected` or add it to `coverage.available_backends`.
- `proposed_edits`: source-bound byte offsets, replacement strings and baseline
  hashes. These are in-memory math-script proposals only.
- With explicit `--rule-set pde --preview-display-gaps`, `display_gap_previews`
  separately provides independently verified source-bound gap edits and candidate
  hashes. Each candidate is based on the original source, not on the script
  proposal candidate; their offsets and hashes cannot be combined directly.
  Verification reparses, compares non-whitespace syntax and operand relationships,
  checks all inline directive targets, and reanalyzes the two display rules.
  A failing source is reported as withheld. These source-level checks do not
  establish execution, preserve all rendered layout, or grant write authority.
- With explicit `--rule-set pde --preview-standalone-displays`,
  `standalone_display_previews` separately supplies source-bound standalone edits
  with the same parsing, syntax and directive checks. This is another candidate
  based on the original source, independent of the script and gap proposals.
  Making a display standalone can reveal a previously inapplicable gap finding;
  the candidate's gap count is reported and must be reanalyzed before a later
  gap pass. Suppressed display findings retain their original ownership.
- `coverage.files`: per-source rule status, refusal reasons, candidate hash and
  candidate verification status. Partial source analysis withholds all proposals
  for that source. Candidates require a successful reparse and a second script
  analysis with no findings, edits, unsupported operands or suppression loss.
  `candidate_verification_rules` explicitly limits that check to the script
  rule. Both display-layout rows are diagnostic-only with no candidate hash.
- `coverage.enabled` and `coverage.uninspected` list the selected pack's rules
  and those not inspected by this command (14/15 core or 30/33 PDE).
- `coverage.global_complete=false`: other rules, imports, execution context,
  resources, source transactions and compiler snapshots remain unresolved.

An unsafe or changed input, malformed syntax, invalid directive, unavailable
parser or exhausted resource budget returns an input-phase error. Errors during
source analysis identify the source path. The command never silently truncates
an inventory. A successfully produced incomplete report returns status 2, even
when it contains zero findings. It grants no source-application or compilation
authority.

## Display-layout source inspection

A typed standalone display and following literal Text must be direct siblings
in the same Markup body. Only Space/Parbreak siblings may separate them, and
the gap must contain at least two LF or valid CRLF terminators. One actual Label
may attach on the display's closing line. Multiple or own-line labels, comments,
Hash expressions and arbitrary intervening wrappers retain explicit unresolved
coverage. The analyzer never searches past them for prose.

Nested literal callback/content bodies are checked as source style even when
their enclosing call or closure has unresolved execution. Ancestor math,
equations, code, headings, lists, raw text and strings are excluded. This is not
package identity, ordinary rendered-prose proof or repair authorization. The
diagnostic and suppression anchor is the display's opening delimiter; a
directive on later prose does not suppress the preceding display gap.

The earlier PDE gap snapshot produced 63 findings and retained 565 unsupported
contexts. Narrowing prose admission leaves all 63 findings and adds one explicit
punctuation refusal (566 total). Standalone inspection separately finds seven
displays and retains 306 unsupported contexts. Normalizing the seven matched
standalone displays in a private copy preserved every original equation and
label byte, literal Text ownership and the complete 302-page PDF. These
exploratory copy edits are not returned by the public command. See
`audit/document-layout-evidence.json` and the earlier gap evidence in
`audit/document-display-evidence.json`.
In the same private copy, a second gap-normalization phase removes the 63 gap
findings after standalone normalization. Both known source-layout findings then
reach zero, while the script findings remain explicit; the complete 302-page
PDF is byte-identical to the original snapshot. This remains a characterization
of owned copies, not public edit output or source-application authority.

`pde.standalone-display` reports one finding per typed display when direct
literal prose shares its opening or closing source line, including after its
attached semantic label. A finding on either proven side is sufficient; an
unknown construct on another side remains an explicit coverage refusal. Text
must contain at least one Unicode letter. Punctuation-only and numeric-only
Text cannot establish ordinary source prose, for either display rule. In
particular, a label's following period does not become a standalone violation.

`document_display.analyze` retains the GAP-only API, including explicit catalogs
that contain only that rule. `analyze_all` returns separate immutable results
for both source-display rules using one shared scan.

The default diagnostic backend returns no edits. The separately requested gap
preview uses the same source matches and is independently verified as above.
Explicit temporary render characterization covers
LF/CRLF, light/dark, the genuine paragraph show and scoped PDE wrappers; it does
not grant arbitrary wrappers the same rendering behavior.

## PDE characterization

The local evidence in `audit/document-inspection-evidence.json` records a
37-source, 290,452-node include closure. The command reports 5,044 findings;
one partially supported source retains diagnostics while its 402 potential
insertions are withheld. The remaining 9,686 insertions affect 26 copied files.
That historical run predates direct escaped-call support. On the same source
snapshot, the current document route reports 5,045 script findings and 10,090
insertions across 27 sources, with no partially analyzed script source. Replaying
those proposals in a private copy leaves no script findings and produces a
byte-identical complete PDF with the copied packages, cache and project fonts.
This is an observation for that snapshot, not a general rendering guarantee.

In private copies with existing package/font resources, Typst 0.15.1 compiled
the complete 302-page document before and after those public CLI proposals.
The PDFs were byte-identical. Two separate section-only candidates also
preserved every extracted word/coordinate and every page raster at 72dpi.
This is observed behavior for that snapshot, not general rendering equivalence,
an immutable compiler resource proof, full rule acceptance or consumer rollout.
The user documents and installed package cache were unchanged.
