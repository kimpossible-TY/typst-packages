# Bounded Markdown script examples

This backend implements syntax-local `core.parenthesized-scripts` checking for
explicitly selected Markdown inputs. It follows the OpenSpec documentation
requirement and the script repair policy. It does not enable capitalization,
paragraph, binding-dependent, or other prose/style rules in Markdown. It is
not a CommonMark implementation, a Typst evaluator, a source writer, or proof
that an example compiles. Production writes and compiler gates remain closed.

## Captured context and exact positions

`MarkdownContext(source, path, ignored=frozenset())` is a frozen context holding
the actual immutable captured UTF-8 bytes, a canonical relative `.md` path, and
immutable config-ignore IDs. Its `analyze()` reruns analysis against that exact
source without rereading the file or any consumer dependency. No synthetic
tree, serialized convergence history, or invented parser facts are accepted.

`analyze(source, path, *, ignored=frozenset(), _verify=True)` returns a frozen
`MarkdownResult` with `diagnostics`, `edits`, `skips`, `parse_errors`,
`suppressed`, and `extraction`. The extraction has immutable `examples`,
`exemptions`, and `skips`. Each `MarkdownExample(kind, start, end)` identifies
one exact contiguous original-byte span. Kinds are `typst-fence`, `inline-math`,
and `inline-code-math`. Exemptions are original-byte `(start, end)` pairs.
Fence spans contain content only; inline spans retain their dollar delimiters
and exclude Markdown backticks. No transformed source or stripped indentation
is used to manufacture original positions.

Every selected example is parsed in markup mode through the existing local
official `typst-syntax` 0.15.1 adapter and checked by `script_rule.analyze`.
Parser-certified attachment operands determine complete script spans, including
chains, primes, fractions, strings, escapes, and Unicode. Diagnostics are mapped
from the real snippet byte offset to the original LF-counted line and Unicode
character column. Parse-error spans are mapped to original bytes, including
zero-width spans. BOM, CRLF/LF, all delimiters, comments, and untouched source
bytes stay unchanged.

## Supported bounded Markdown

- Column-zero backtick or tilde fences with at least three identical delimiter
  characters and exactly `typst` as the trimmed info string. The closer uses
  the same character, at least the opener length, and only trailing horizontal
  whitespace. A closed unrelated-language fence is opaque Markdown data.
- Same-line single-dollar math. A bounded official-parser probe certifies the
  first closed `Equation` and its exact dollar tokens. Internal string dollars
  and escaped dollars therefore do not require a hand-written Typst lexer.
  Only the resulting exact equation span is selected and reparsed for checking.
- Single-line equal-length backtick code spans whose entire content is one
  closed dollar-delimited Typst equation. Ordinary code spans are not treated
  as executable Typst. A mixed-content math/code example is explicitly partial.

A code span starting at column zero with three or more backticks is currently
classified conservatively as a possible fence, even if it closes on that same
line. It remains partial and unchanged. A preceding prose prefix disambiguates
that form as an inline code span. This initial refusal is not full CommonMark
fence-versus-code-span support.

The only authored counterexample exemption is the exact byte sentence:

```text
Do not write bare scripts such as `$X^i$` or `$X_j$`.
```

Only that complete sentence in eligible documentation prose is exempted, with
a sentence/whitespace boundary. Surrounding examples remain checked. Changes to
its words, case, or terminal punctuation do not inherit the exemption. No
exemption is inferred from words such as “incorrect,” “bad,” or “counterexample.”
This mechanism is not applied inside Typst fences or by the ordinary Typst
backend. Real Typst raw/string/comment semantics remain authoritative there.

## Ambiguity and resource limits

Unsupported contexts are quarantined before checking their contents and produce
explicit partial-coverage reasons. Initial limits include indentation and
containers, bracket-bearing/link/reference paragraphs and multiline link
destinations, HTML, YAML front matter, display-dollar runs, multiline/unclosed
code spans, extended-info fences, unclosed fences, bare CR line endings, and
backtick-bearing bare math. Bracket-bearing inline Typst math is conservatively
unsupported too. A bracket/link paragraph is opaque through its next blank
line, so earlier label math is never collected before discovering the link.

HTML comments, CDATA, processing instructions, and raw `pre`, `script`, `style`,
and `textarea` tags remain opaque across blank lines through their explicit
closing token. Raw-tag close matching is case-insensitive and exact, so
`</prefix>` cannot terminate `<pre>`. Unreviewed close spellings may quarantine
more following content rather than guessing. Other HTML contexts are opaque
through a blank line. Unclosed bare math quarantines the remaining document;
a later dollar inside a string/comment is not guessed to be a recovery point.
An indented/container fence also quarantines the remaining document because
the backend does not prove container ownership; unrelated list/quote prefixes
are never stripped to invent a matching close.

The existing source limit is 1 MiB. Each example/probe is at most 64 KiB, with
UTF-8-safe probe endpoints. Extraction has a shared budget of 256 attempted
parser probes and accepted examples, including failed code-span probes; at
most 256 examples can be retained. Exhaustion leaves the remainder unsupported.
Each accepted example is then parsed once for checking, with at most one whole
candidate verification pass. Extraction reasons are capped at 256 plus one omission
notice, with set membership and no further prefix line recounts after the cap.
Backslash escaping is checked only at delimiters, and container prefixes use a
single forward cursor rather than repeatedly slicing the remaining source.
No missing region is represented as successfully checked Markdown.

## Suppression and conditional proposals

Inside fences, actual `LineComment` nodes use the existing reviewed
`ignore-next` parser scopes. A multiline syntax construct is one target, nested
body boundaries remain intact, and scopes never cross into another example.
Malformed, unknown, or dangling directives are configuration failures, with
their line mapped to the original Markdown. Inline examples do not acquire
comment-based suppression; other intentional inline counterexamples require a
path/rule configuration ignore.

As in the source composer, `ignored` withholds matching edit proposals while
retaining findings for the report's config filtering and suppression accounting.
`suppressed` contains actual inline-directive findings only. Neither type of
ignore suppresses parsing or configuration errors. A parse failure in any
selected example withholds all Markdown edits, including edits from other
otherwise valid examples in that file.

Proposals are a single pass of insertion-only parentheses obtained from the
real script backend. Each proposal must lie in its example and insert only
parentheses; conflicting proposals fail. Before exposing edits, the entire
Markdown candidate is re-extracted and reparsed. Exact mapped example and
exemption spans, unchanged suppression identities, unchanged applicability
reasons, no new parse error, no active script finding, and zero further edits
are required. Exceeding the source limit or failing verification withholds the
edits with a reason. No prose, mathematical token, comment, import, fence,
backtick, BOM, or newline is replaced.

`test_markdown_scripts.py` exercises this contract with the real pinned parser:
positive/negative forms, exact examples and positions, protected nonexample
bytes, teaching near-matches, actual suppression ownership and expiration,
unsuppressible errors, candidate idempotence, adversarial link/HTML recovery,
bounded attempts/reasons, source/example limits, and immutable audit context.
Tests never build/fetch the adapter or execute consumer imports.
