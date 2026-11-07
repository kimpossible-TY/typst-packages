# Scoped-reference namespace fixture

These files are unchanged public `@preview/mannot:0.4.0` artifacts, copied from
an existing local Typst package cache for portable offline tests. No network
fetch, dependency installation or implementation substitution was performed.
The source manifest identifies the upstream repository as
https://github.com/ryuryu-ymj/mannot and the package author as ryuryu-ymj.
The original MIT `LICENSE` is included beside the manifest.

Only these bytes are copied:

- `preview/mannot/0.4.0/typst.toml`, SHA-256
  `aef4310b36955dc98d8b03ec4abf17d45777d0a08b65d6e67214752e57647545`.
- `preview/mannot/0.4.0/src/lib.typ`, SHA-256
  `288027cea059b327bf3253c44ae975339e7afc0f149375d9d87ca1c1fe739f57`.
- The original MIT license for the copied artifact.

The complete entrypoint consists of two named imports. Its exported names are
`core-mark`, `mark`, `markhl`, `markrect`, `markub`, `markul`, `markuw`, `annot`,
`annot-cetz`, `core-annot` and `tiptoe`. It defines or imports no `ref` or `label`.
Named imports do not re-export all names from their source modules. That narrow
namespace fact prevents the scoped wrapper's wildcard import from shadowing its
builtin `ref`/`label`; it does not certify any raw mannot function's semantics.

This is deliberately NOT a runnable package copy. `mark.typ`, `annot.typ`,
`util.typ` and the tiptoe dependency are not provided or stubbed. Tests read the
manifest and entrypoint only, and never evaluate this fixture with Typst. The
linter must not follow those imports, inspect the user's cache, fetch anything,
or treat the empty semantic contract as a complete export namespace. Positive
CLI tests explicitly copy this fixture and the reviewed scoped-annotations
package into a temporary caller-supplied package root. Missing namespace bytes
leave reference diagnostics without repairs. Full rendering requires separately
reviewed and captured runtime dependencies.
