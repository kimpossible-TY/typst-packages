# Read-only document inspection

`typst-lint inspect-document --root /absolute/project --entrypoint main.typ`
provides an explicit literal-include inventory, syntax-local math-script
proposals and source display-gap diagnostics. `--format json` returns schema 1;
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
`pde.display-continuation-gap`; `rule_set` and `coverage.available_backends`
record that selection. Inline suppression directives are honored for both rules.
Consumer configuration is deliberately not loaded, so its include/exclude and
path-ignore settings do not select or suppress this explicit document inventory.
The report states this choice. Import targets, package definitions, reference
catalogs and runtime resources are not resolved by this route.

JSON includes:

- `include_context`: selected budget, source hashes/counts, edges, occurrences
  and explicit unresolved containment, missing or dynamic requests.
- `diagnostics` and `suppressed`: source-style findings over the captured ASTs.
- `proposed_edits`: source-bound byte offsets, replacement strings and baseline
  hashes. These are in-memory math-script proposals only.
- `coverage.files`: per-source rule status, refusal reasons, candidate hash and
  candidate verification status. Partial source analysis withholds all proposals
  for that source. Candidates require a successful reparse and a second script
  analysis with no findings, edits, unsupported operands or suppression loss.
  `candidate_verification_rules` explicitly limits that check to the script
  rule. The display-gap row is always diagnostic-only with no candidate hash.
- `coverage.enabled` and `coverage.uninspected` list the selected pack's rules
  and those not inspected by this command (14/15 core or 31/33 PDE).
- `coverage.global_complete=false`: other rules, imports, execution context,
  resources, source transactions and compiler snapshots remain unresolved.

An unsafe or changed input, malformed syntax, invalid directive, unavailable
parser or exhausted resource budget returns an input-phase error. Errors during
source analysis identify the source path. The command never silently truncates
an inventory. A successfully produced incomplete report returns status 2, even
when it contains zero findings. It grants no source-application or compilation
authority.

## Display-gap source inspection

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

The PDE snapshot produces 63 findings and retains 565 unsupported display
contexts explicitly. Normalizing only the 63 matched gaps in a private copy
produced a byte-identical 302-page PDF; the second gap analysis had zero findings
and the same mapped unsupported contexts. These exploratory copy edits are not
returned by the public command. See `audit/document-display-evidence.json`.

The backend returns no edits. Explicit temporary render characterization covers
LF/CRLF, light/dark, the genuine paragraph show and scoped PDE wrappers; it does
not grant arbitrary wrappers the same rendering behavior.

## PDE characterization

The local evidence in `audit/document-inspection-evidence.json` records a
37-source, 290,452-node include closure. The command reports 5,044 findings;
one partially supported source retains diagnostics while its 402 potential
insertions are withheld. The remaining 9,686 insertions affect 26 copied files.

In private copies with existing package/font resources, Typst 0.15.1 compiled
the complete 302-page document before and after those public CLI proposals.
The PDFs were byte-identical. Two separate section-only candidates also
preserved every extracted word/coordinate and every page raster at 72dpi.
This is observed behavior for that snapshot, not general rendering equivalence,
an immutable compiler resource proof, full rule acceptance or consumer rollout.
The user documents and installed package cache were unchanged.
