# Partial theme rule contract

`theme_rules.analyze(tree, path, resolution)` implements diagnostics for
`core.no-set-theme` and `core.theme-in-context`. It proposes no edits. Source
files, package files and consumer documents are never modified or evaluated.

## Provenance

The reviewed artifact is `local/math-blocks/0.2.0/lib.typ`, SHA-256
`542c7ce1ebfc6fbd51a7fefb2b55405a968a951b61d49170e49ef331948f36ff`.
The existing explicit package-path loader verifies manifest identity, retained
file descriptors and exact source bytes. There is no cache search or import
execution. Versions, paths or spellings alone never establish semantics.

The theme contract exports only `light-theme`, `dark-theme` and
`theme-from-text-fill`. The separate [footnote contract](FOOTNOTE_CONTRACT.md)
certifies the locally defined `dots_space` value from this same artifact, and
the [proof contract](PROOF_CONTRACT.md) certifies its local `proof` helper. The palettes are literal dictionaries. The zero-argument selector
reads `text.fill` and returns one of those dictionaries. Its imported helpers
(`capitalize-title`, `is-numeric-title`, `project-ref`) are neither used by nor
allowed to redefine those palette bindings. Their dependency contents are not
loaded or certified. Other exports remain unresolved. A wildcard import thus
invalidates all existing names before establishing just these five independently reviewed exports.

Direct imports, renamed imports, module access, parentheses and local immutable
aliases reuse the resolver's lexical binding machinery. The palette result is
inferred only for a zero-argument call to the exact proven selector. Field and
other mutations invalidate receiver provenance. Unknown imports/shadowing do
not produce theme diagnoses.

Typst closures capture values at definition. Deferred scopes retain copies of
only these reviewed theme bindings/module aliases, never general prose/style
proofs. Parameters (including identifiers in defaults and destructuring) mask
captures conservatively. Hypothetical closure mutations do not escape into the
outer resolver state. This is a theme-specific exception, not general closure
analysis.

## Diagnostic and repair limits

- Set targets proven to be palette helpers/dictionaries are diagnosed regardless
  of alias spelling. `set text` and `set page` are not palette targets.
- Selector calls under a closure body, outside lexical `context`, are diagnosed.
  Parameter defaults and root calls are excluded. Arbitrary colors are ignored.
- Suppression, parse failures, stale resolution and Unicode locations follow the
  other AST backends. Unsupported imports/facades and versions remain partial.
- **No context insertion is proposed.** Captured palette provenance does not
  prove a reusable helper's content-return contract, all content-accepting call
  sites, or the absence of early return/mutation. The OpenSpec conditional fix
  requires all of these. Each finding records `fix_unavailable` explicitly.
- No compiler/render validation is claimed. The tests invoke only the pinned
  syntax adapter and static analyses against immutable package snapshots.
