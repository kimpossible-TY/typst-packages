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
The document route additionally accepts a direct `#`-escaped function call,
identifier or static identifier-field chain as a script operand and groups the
hash together with it. Whitespace and block comments before the operand are
preserved; line comments, computed receivers and other escapes remain unsupported.
This opt-in analysis does not change the ordinary single-source or
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

The independent `--rule-set pde --preview-conditional-scoped-calls` option
inspects direct scope-field calls in narrowly supported literal
`local-tag-scope` and `local-scope-annotations` callbacks. Its per-file report
contains source hashes, conditional diagnostics, suppressions, byte edits and
unresolved contexts. It does not inspect package bindings or prove callback
execution, rendering or authority to write or compile. Complex calls, mutation,
deferred scopes and unsupported options are conservatively skipped. Its edits
are independent of all other previews, and the global rule remains uninspected.

The independent `--rule-set pde --preview-conditional-continuations` option
proposes replacing the single newline and indentation between narrowly
recognized adjacent prose sentences with one space. Per-file rows retain the
baseline and candidate hashes, exact edits, withheld reasons and conditions.
Comments, blank lines, ambiguous abbreviations and unsupported structure do
not become proposals. Runtime prose context and rendering are not proved; the
plans are not composed or applied and do not grant compiler authority or
increase global coverage.
The independent `--rule-set pde --preview-conditional-capitalization` option
proposes capitalizing the first ASCII byte of a narrowly recognized nested
paragraph starter. Per-file rows retain exact edits and source/candidate hashes;
reasoned suppressions and other checked source findings are preserved. The marker
binding, English prose context and execution remain conditions. The intended
rendered text changes, and the source-only proposals are not composed or applied
and do not grant compiler authority or increase global coverage.

The independent `--rule-set pde --preview-conditional-prose-spacers` option
proposes removing a literal `#dots_space` and horizontal separator immediately
between existing prose punctuation and a direct footnote. Ambiguous punctuation,
comments, line breaks and suppressed cases are withheld. Each proposal assumes
both runtime bindings and prose execution; it can change rendering, grants no
compiler or source-write authority, is not composed with other plans, and does
not increase global coverage.

The independent `--rule-set pde --preview-conditional-aligned-spacers`
option proposes inserting a literal `#dots_space` before a supported direct
footnote in a nested aligned display. A finding and its suppression are
rechecked against the source and candidate. The intended spacer and footnote
bindings, the display's execution and runtime context are conditions, and the
inserted spacer may change rendering. The plan is not composed or applied,
does not grant compiler or source-write authority, and does not increase global
coverage.

The scoped-call option separately reports `conditional_detached_annotations` for
direct scoped annotation calls outside a display in supported literal callback
bodies. These are diagnostic-only hints with independent suppression and
unresolved context; they do not produce edits or complete rule coverage.
The separate `conditional_raw_overlays` report covers a narrow direct
`annot-cetz` spelling outside displays. Its helper identity and execution are
likewise conditional; alias/shadow and unsupported contexts are withheld.
The separate `conditional_marker_tags` report detects literal global labels in
direct math `mark` calls within supported `local-scope-annotations` callbacks.
Wrapper and raw marker binding, callback execution, and scope ownership remain
conditions. Dynamic calls, nested effects, aliases and known shadowing remain
unsupported; no label is changed and global rule coverage remains incomplete.

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
- With `--rule-set pde --local-rule-hints`, fourteen paragraph, continuation,
  footnote, proof, theme, import-order and cancellation rule subsets inspect each captured file independently.
  Continuation and footnotes additionally inspect nested literal markup as source patterns.
  Findings and refusals are separate conditional hints, without edits; actual
  include-site bindings, execution, styles and rendering remain unproved.
  The report retains these rules in `coverage.uninspected` because their document
  context is not established. Shared resolver refusals appear once per source.
- With explicit `--rule-set pde --preview-conditional-gaps`, report independent
  source-only previews for proof boundaries and nested display-math footnote
  gaps. Each candidate is freshly parsed, checked for non-whitespace syntax and
  suppression changes, and reanalyzed by its own rule subset. A withheld
  preview is reported as such. These previews use the original bytes separately;
  they are not composed with each other or the ordinary preview sequence, and
  do not prove binding, execution, PDF equivalence or source-write permission.
  A separate `conditional_prose_footnote_gap_previews` list additionally
  proposes a paragraph break for the bounded nested prose-footnote pattern
  followed by an existing standalone marker. These edits are not composed
  with the other gap proposals and may change rendered layout.
- `--preview-conditional-punctuation` independently previews horizontal
  whitespace removal between an existing period/comma and a nested prose
  footnote. A separate `conditional_punctuation_move_previews` list proposes
  moving one immediately following period/comma before a simple nested prose
  footnote with a locally proven builtin binding. Neither candidate is composed
  with the other or with other plans; actual include bindings, execution,
  render equivalence and source-write permission remain unproved.
  The separate per-file `conditional_punctuation_details` also classifies
  adjacent `text#footnote[...].` and `text#footnote[...],` patterns, including
  inline-math and reference predecessors and complex literal bodies. It gives
  the footnote and punctuation byte spans, conditions, suppressions and
  unresolved cases. These rows refine existing conditional hints and are not
  counted as new findings or coverage; they propose no edits and grant neither
  source-write nor compiler permission. Explicit source-local footnote
  bindings or mutations withhold this classification conservatively.
- `--preview-conditional-spacers` independently previews exact `#dots_space`
  token removal before a footnote in an unaligned nested display. The token's
  binding and effects are unproved: removing it can change the rendered PDF.
  An empty preview does not establish complete inspection or absence of other
  violations. This preview is never automatically applied or composed.
- With `--rule-set pde --package-path /absolute/packages --footnote-cache-path
  /absolute/cache`, `conditional_footnote_bindings` reports source-bound builtin
  footnote hints through complete captured relative wildcard-facade exports.
  Its two explicit roots use package-before-cache selection. Calls include
  captured facade and package evidence and outstanding runtime conditions;
  unresolved imports, shadows and unsupported contexts are not proven. These
  hints are independent of preview edits and grant neither compiler nor write
  permission. Source bytes and package selection at runtime remain conditional.
- `--footnote-punctuation-evidence` additionally joins nested prose punctuation
  hints to these exact captured calls in a separate `punctuation_by_file` report.
  It requires `--footnote-cache-path`, performs an additional analysis pass, and
  neither promotes the hints to complete coverage nor proposes edits.
- With `--rule-set pde`, `--package-path`, `--reference-package-path` and
  `--reference-facade`, the separately captured reference package and graph
  namespace evidence trace a selected facade's `project-ref` export. A
  CONDITIONAL result is not proof of a particular call's binding or execution;
  an UNAVAILABLE result retains the separately captured package evidence and
  explains the refusal. Runtime selection, catalog membership and editing remain
  unproved. No package roots are inferred from ambient caches.
- With the same explicit reference options, `--reference-calls` separately
  traces supported direct calls in each captured source. Each file reports
  `CONDITIONAL`, `PARTIAL`, or `UNAVAILABLE` and includes outstanding refusal
  reasons; a partial result is never a complete call inventory. Direct literal
  `#footnote[...]` bodies in display equations are traced conditionally; other
  equation code, deferred functions and unsupported execution remain uninspected.
  Trace evidence does not prove runtime execution, catalog membership or edit
  safety, and analysis budget exhaustion discards that file's partial calls.
- `--reference-arguments` uses that same conditional trace to report wrong
  arity, named arguments and statically known wrong argument types. Spread and
  unknown values stay unresolved; it neither checks catalog membership nor
  proposes edits. It requires the explicit reference provenance options and
  can be combined with `--reference-calls` without retracing each file.
- `--reference-catalog /absolute/catalog.json` checks literal project/label
  pairs from those conditional calls against an explicitly captured validated
  catalog. Unknown values and unsupported contexts stay partial. The catalog
  snapshot hash is reported; its freshness, PDF correspondence and runtime
  selection are not established. Catalog, argument and call reports share one
  trace per source when requested together. A literal missing project key can
  also be diagnosed conditionally when its target is unresolved; it does not
  count as a checked literal pair. `catalog_checked` means at least
  one conditional literal pair was compared; `catalog_check_complete` remains
  false even if every observed pair matches.
  Reference sessions reject changes to their captured graph and package evidence
  after construction. Python callers remain responsible for the trustworthiness
  of the initial graph they supply; the CLI constructs it through safe capture.
- Optional `--reference-cache-path` adds a second explicit namespace root for
  this reference-export trace only. The first existing package version wins,
  including an incomplete or unreviewed one. For this multi-root route the
  reviewed reference package must currently be captured from the first root;
  a separate or cache-only reference root stays unavailable. The independently reviewed
  text-utils variant supplies opaque export names only; it grants no helper or
  paragraph-marker identity. Ordinary conditional helper analysis retains its
  original single-root contracts.
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
- With `--rule-set pde --preview-sequence`, `sequence_previews` reports a
  per-source ordered standalone/script/gap sequence. Each step contains edits
  relative to its own baseline and the resulting candidate hash. The next step
  uses that result as its baseline, not the original source offsets. A failure,
  incomplete script analysis, changed directive target or unsuccessful final
  three-rule reanalysis withholds the entire source sequence. The final record
  includes counts of unresolved display contexts; other enabled rules and runtime
  obligations are still incomplete. No source or compiler execution authority
  follows from this report.
  The internal pure `document_sequence_preview.replay` helper lets a downstream
  consumer recompute the source-bound report, reject stale or changed serialized
  data with exact primitive types, and independently replay its hash chain in
  memory. It returns candidate bytes only; it does not capture compiler resources
  or create a protected write receipt.
  The internal `replay_document` helper accepts the original locally issued
  include capture and a complete plan set, recomputes every source plan, and
  returns an immutable path-to-bytes map. Missing, duplicate, foreign and
  withheld records reject the whole map. Include capture does not prove import,
  image, font, package or compiler-resource closure; the map is not compiler
  or write authority and does not revalidate live paths.
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
