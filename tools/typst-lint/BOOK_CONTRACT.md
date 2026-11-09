# Partial configured-root book ownership contract

`book_rules.analyze(tree, path, resolution, entrypoint=...)` provides partial
diagnostics for `core.single-book-show` and `core.no-duplicate-book-reset`.
Both currently return **no edits**. Analysis does not evaluate imports, write
consumer sources, invoke a compiler, certify a complete document graph, or open
the production write/compile gates.

## Exact reviewed sources and dependency boundary

The reviewed artifacts are:

- `local/math-book/0.2.0/lib.typ`, SHA-256
  `943c400626fd66b1d80b7f95391a66b245da83db33b4a74e4a17aa6a1275e3e8`.
- `local/math-blocks/0.2.0/lib.typ`, SHA-256
  `542c7ce1ebfc6fbd51a7fefb2b55405a968a951b61d49170e49ef331948f36ff`.

The explicit package-path loader checks package manifest identity, exact source
bytes, retained file descriptors, file/directory identity and read safety. This
slice adds the locally defined `apply-math-book` and
`apply-math-block-reset` callable identities. No other math-book export or
math-blocks re-export gains semantic provenance.

Math-book imports math-blocks with `*`, text-utils' named
`apply-paragraph-tabs`, and `cover.typ`'s named `book-cover`. Its local book
function defaults `reset-math-blocks` to `true` and installs:

```typst
show: body => if reset-math-blocks { apply-math-block-reset(body) } else { body }
```

The captured reset cannot be certified from math-book bytes alone. The resolver
requires **both** reviewed package snapshots and explicit non-wildcard root
imports of both package paths. Module imports, named imports and renames are
supported. The loader does not follow package dependencies to obtain missing
snapshots. A book-only direct import remains unresolved for these diagnostics,
even if the package could compile using dependencies installed elsewhere.

Complete review of the exact blocks source establishes the captured namespace:
its only imported names are `capitalize-title`, `is-numeric-title` and
`project-ref`. Its local definitions do not replace the builtins used by the
reset or by the book function. Its reset captures locally defined
`math-block-kinds`, `heading-numbering-style` and
`reset-math-block-counters`; the latter resets counters for theorem,
proposition, lemma, definition and note. The reset installs both normal-reference
target-location formatting and heading-level 1/2 reset rules. It is not merely a
counter-update call. The equation and footnote reset options of math-book are
separate and do not establish mathematical-block reset ownership.

The named cover/text-utils imports cannot replace the reset identifier or the
relevant builtin names. Their implementations are not loaded or executed by
linting. Dependency availability, imported module evaluation, runtime catalogs,
fonts and compilation success remain separate concerns.

Both package namespaces remain **partial** in the general resolver. A wildcard
still invalidates previous bindings and builtins before establishing only
reviewed exports. In particular, importing book and then blocks `*` does not
retain an earlier book binding. Enumerating namespace names would be a separate
future attestation; recognizing these two callables does not silently provide
that attestation or a complete wildcard namespace proof.

## Source-produced transformation proof

`book_provenance` supplies immutable syntax/context records, not caller-granted
binding flags. The source-order resolver produces a source-hash-bound
`BookProof` only after checking the exact package pair and live lexical bindings.
Only `ShowRule` nodes directly in a markup root qualify. The accepted transform
is an exact bound value or one `.with(...)` call on that bound value. Named
renames, module fields, parentheses and immutable value aliases are accepted.

The transformation expression is immediate; its body is deferred. The resolver
does not infer general call-result identity, preconfigured `.with` aliases,
arbitrary factories, custom `.with` dictionary fields or deferred captures.
Mutation, destructuring, unknown wildcard imports and local shadowing invalidate
the affected live bindings. A show that already captured a value remains
registered after its variable is rebound. An earlier independent value alias
can retain identity, but shared-body diagnosis still needs the adjacency proof
below.

The first `.with` subset accepts only:

- `title`, `author`, `description`: literal string or `none`.
- `reset-math-blocks`: one literal boolean.

All other options, even literal ones, remain unresolved pending their own type
and effect contracts. Spreads, positional/body arguments, duplicate names,
dynamic values, chained `.with` calls and unknown options provide no transform
proof. Bare book transforms and empty `.with()` use the reviewed true default.
Bare reset transforms and reset `.with()` can be diagnosed; customized reset
applications remain unresolved. This is not a runtime type checker for arbitrary
Typst options, nor a claim that compilation succeeds.

## Configured entrypoint and graph limits

The CLI captures the explicit configured entrypoint with the existing bounded
retained-descriptor snapshot reader, capped at the syntax adapter input limit.
It compares that immutable snapshot against the selected input hash and the
actual selected bytes before parsing. The configured input's parse reread uses
the same retained-descriptor reader, rather than reopening an unchecked path
after a separate path check. Identical-byte symlink replacements and nonregular
replacements are refused before reading their contents. Mutation during retained-descriptor
capture is rejected; mutation between selection/capture/parsing cannot supply
mismatched book proofs. This read snapshot is not an editor lease or a future
compiler snapshot.

`EntrypointSnapshot` carries the configured relative path and immutable bytes
through the real dispatcher. The book backend requires both path and bytes to
match its tree. The composed-candidate verifier rebases the snapshot only when
it already matched the exact baseline; stale or wrong-path context is never
rescued. The same candidate context is used by both reanalysis and independent
idempotence verification.

No entrypoint is guessed from a filename. An unselected/excluded entrypoint
may be captured for configuration evidence but receives no style findings.
Other selected documents are not grouped with it. Included-file ownership,
cross-file uniqueness, local facades, conditional/contextual/deferred ownership
and missing-owner inference remain unavailable. Importing a helper alone does
not prove that a document needs a book owner. All results retain explicit partial
coverage rather than treating silence as a complete one-owner check.

## Diagnostic and repair limits

- `core.single-book-show` diagnoses each proven immediate root book registration
  after the first. It never adds, removes or chooses a book owner/options.
- `core.no-duplicate-book-reset` needs an enabled book registration and the same
  exact reset identity. The two registrations must be adjacent root transforms,
  separated only by whitespace/comments. Intervening declarations, output or
  transformations withhold the shared-body claim. This deliberately narrower
  subset does not infer overlap from a retained function binding alone.
- A literal `reset-math-blocks: false` transfers reset ownership and emits no
  duplicate-reset finding for that book. It does not certify complete caller
  reference formatting.
- Suppression, Unicode columns, parse failures, configuration ignores and stale
  proofs follow the shared backend contracts. Comments/directives are never
  moved or deleted.
- **Reset deletion is not proposed, even for the adjacent bare pair.** General
  equivalence of the full reset wrapper under arbitrary body, heading and
  reference transformations has not been certified. Each active or suppressed
  reset finding records an explicit `fix_unavailable` reason. A representative
  equal render does not establish that universal precondition.

## Verification evidence

Focused actual-parser tests cover paired bytes and requests, named/module/alias
identity, mutation and shadowing, immediate/deferred scope, `.with` refusal,
literal reset transfer, adjacency, wildcard uncertainty, suppression, malformed
source and stale proofs. CLI tests exercise check/dry-run/fix/compile with a
compiler tripwire, exact test-owned package copies, unchanged source/output,
configuration selection, source-read races and composed script proposals.

An opt-in Typst 0.15.1 test explicitly copies the real book, blocks, text-utils
and project-references sources plus the book's real `cover.typ` into a temporary
package directory. With an empty package cache it compares an adjacent default
book/reset pair against a **manually specified test-only deletion**. Word text
and bounding boxes, all three labelled theorem queries, and level 1/2 heading
queries remain identical across sections/chapters and cross-references. The
test verifies numbering `1.1.1`, `1.2.1`, `2.1.1`, unchanged package originals and
an empty cache. The CLI itself continues to offer no reset edit and never
invokes the compiler. This is representative evidence, not universal repair
acceptance or production enforcement.
