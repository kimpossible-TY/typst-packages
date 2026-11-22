# Partial flowbox border contract

`flowbox_rules.analyze(tree, path, resolution)` implements
`pde.flowbox-colored-border` only while analyzing a complete statically proven
variant of the reviewed math-blocks module. It returns a diagnostic and an
in-memory byte insertion proposal. It never evaluates Typst, reads dependencies,
discovers packages, changes package sources or applies a source transaction.

## Authoritative implementation and variant proof

The reviewed artifact is `local/math-blocks/0.2.0/lib.typ`, SHA-256
`542c7ce1ebfc6fbd51a7fefb2b55405a968a951b61d49170e49ef331948f36ff`.
It already has a colored flowbox border. A new helper named `flowbox`, including
the short example previously used by the audit fixture, is not this owner.

The backend consumes the selected input's actual pinned-parser AST. It locates
one candidate slot under the immediate root flowbox closure, contextual body,
returned block call and its named stroke argument. These names merely locate a
candidate. Ownership requires the independently pinned structural/token digest
of the entire module after replacing that one expression subtree with a fixed
placeholder. That digest is
`0b83998386e7c5152ce8bd927031b4a9e4ff5b65b6113cf82c38ed0eb1cfe26b`.
The artifact test regenerates it only from the independently SHA-verified source.
Caller-supplied booleans, package paths, import spellings or helper names cannot
establish it. No imported flowbox API is added to the general resolver.

The digest retains every other semantic leaf's exact bytes, node kind, nesting,
display property and typed math-attachment roles. Code whitespace/comments and
root definition-separator trivia can vary only while preserving that parsed
structure. This allows source comments, standalone directives, LF/CRLF and one
leading BOM. Nested content and math trivia remain pinned, except equivalent
LF/CRLF whitespace transport. In particular, changing text inside proof content,
math spacing, another helper, an imported symbol, body alignment, a palette, the
selector, parameter bindings or block ownership invalidates the proof. Adding
an unrelated executable root construct also invalidates it.

This is a proof of this module's reviewed flowbox implementation, modulo the
explicitly bounded variants, not a general source-copy detector. Full-module
copies with identical semantic ASTs have the same local implementation. Short
copied helpers, facades, different implementations or otherwise modified modules
remain unresolved. Manifest identity is not inferred from a filename: only the
analyzed complete module bytes establish this local owner.

The unmasked module proves the local `theme` binding, the surrounding `context`,
the zero-argument local selector, its `text.fill` test, both literal palettes
with `rule` fields, the builtin block and original body structure. The module's
two explicit named dependency imports cannot replace those names. Those imports'
runtime behavior is not evaluated or certified, and their contents are not
needed to prove the local flowbox definition. Static CLI dependency evidence,
when requested, still uses the unchanged explicit package loader.

## Border and repair limits

The only accepted masked expressions are:

- A nonnegative decimal `Numeric` literal using `pt`, `mm`, `cm`, `in` or `em`,
  at most 80 source bytes. It is diagnosed as a width-only border.
- That same literal followed by `+ theme.rule`, with code trivia permitted.
  It is a non-violation.

Other color expressions, parentheses, relative percentages, dictionaries,
negative widths, dynamic values and computed strokes remain unresolved. The
backend never classifies arbitrary figure strokes or decides color semantics.

The repair inserts exactly ` + theme.rule` at the literal's end. The actual
existing local identifier is used. The literal's spelling and width, comments,
line endings, BOM, inset, alignment, body and every other source byte are
unchanged. No palette, global theme, import, color literal or option is created.
Renaming the palette binding is not supported by this exact-artifact contract.

The diagnostic anchor is the border's named stroke argument. An ignore-next on
the enclosing flowbox definition or returned block suppresses both diagnosis
and repair. A directive targeting another statement cannot suppress this one.
After insertion, the backend reparses, re-establishes the complete owner and
colored variant, checks the exact original width and compares all suppression
targets after mapping their offsets. The shared composer independently reparses
and reanalyzes the entire candidate, checks directive ownership and requires
second-pass zero edits. Invalid syntax, stale resolution or lost candidate proof
produces no edit. Unsupported directive placement remains a configuration error
under the shared suppression rules; it is not silently ignored.

## Verified evidence and remaining boundary

- `tests/test_flowbox_rules.py`: reviewed-source/hash linkage, literal widths,
  exact insertion, unchanged colored variants, fragment/arbitrary-figure
  refusals, altered semantic bytes, shadows/mutations, dynamic strokes,
  comments, CRLF/BOM, suppression targets, stale resolution, candidate-proof
  failure, reparsing and second-pass zero findings/edits.
- `tests/test_flowbox_integration.py`: genuine full-module source-to-CLI
  positive through check, dry-run, fix and compile-wrapper modes; exact copied
  dependency fingerprints; core exclusion, configured ignore and inline
  suppression; read-only source/package/output preservation; compiler-spy
  non-invocation; and continued partial coverage and gate closure.
- `tests/test_flowbox_render.py`: opt-in Typst 0.15.1 rendering of the real CLI
  proposal applied only by the test harness to its temporary module copy.
  Exact repository text-utils/project-references copies are the only package
  inputs, with an explicit empty cache. In light and dark contexts the border
  changes from default black to the reviewed `#2c3e50` and `#94a9c4` rule colors.
  Its SVG width remains 1.75pt. After masking only that single color, the complete
  SVG tree is identical, including body glyphs, placement and border geometry.
  Label queries are identical, dependency originals/copies remain unchanged and
  the cache remains empty. Independent final CLI analysis has zero proposals.

Run `python3 -m unittest discover -s tools/typst-lint/tests -p 'test_flowbox*.py'`.
Set `TYPST_LINT_RENDER_TESTS=1` to run the rendering test when the pinned syntax
adapter and Typst 0.15.1 are already available. No installation or download is
performed by these tests.

General modified implementations, aliases/renames within the module, source
facades, different artifacts, computed strokes and dependency/evaluation graphs
are not implemented. All accepted and refused cases retain partial coverage;
these tests do not enable production source writes or compilation.
