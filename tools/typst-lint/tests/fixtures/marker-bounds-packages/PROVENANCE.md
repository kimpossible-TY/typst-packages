# Public mannot definition snapshots

The unchanged files under `preview/mannot/0.4.0/` were retrieved with the
authorized GitHub read connector from the public `typst/packages` repository.
The repository's package-path history identifies commit
`163cacf06d1bb32dedf1602f5ba5c4a9002d929c` (`mannot:0.4.0 (#5128)`). Its package
directory and source tree were read again at that immutable commit, and their
Git blob identities match every copied file. The existing pinned manifest and
entrypoint SHA-256 values also match exactly. The original MIT license is
included unchanged.

Source directory:
https://github.com/typst/packages/tree/163cacf06d1bb32dedf1602f5ba5c4a9002d929c/packages/preview/mannot/0.4.0

| File | SHA-256 | Git blob SHA-1 |
| --- | --- | --- |
| typst.toml | aef4310b36955dc98d8b03ec4abf17d45777d0a08b65d6e67214752e57647545 | 746072d9feb0d3a1258c0c3113546bd06d683933 |
| src/lib.typ | 288027cea059b327bf3253c44ae975339e7afc0f149375d9d87ca1c1fe739f57 | 525541bb6b2ecea89769a56444b1b1b29d0552d2 |
| src/mark.typ | 86f7653d76aa4574f811c8b63a31f79b0a90c4989e24ac1277857334b6d35882 | cc4aaf702b40a128211a2e7a6fe01eecc8144307 |
| src/annot.typ | 12ea7288aeb7378bb6cdf8747d0d94fb3828def856521540a881edf25f4d42bd | 798f42d05b42116a12007a652110d48f5ebaf2ff |
| src/util.typ | b88ea6e8d207c611925822d9d6ae7cb83bcf0486ba68af57807a9e4539b98583 | 2b90e6934f4bbf847af9c0652ca3845dc587d8d8 |
| LICENSE | 5aba3493920d5fb3d7b095f3e0c4a0311cf1feb24274789dbe16c5182febf1c9 | 72330967fa06c3ad7d2544982feddfad8a8ab287 |

The manifest names https://github.com/ryuryu-ymj/mannot as the author's source
repository. These are authenticated public registry-repository bytes, not a
claim that a separately checked author-repository tag matches them.

The entrypoint imports `mark` from `mark.typ` by name. That file imports only
`coerce-outset` and `default-stroke` from `util.typ`, also by name. Its reviewed
`mark` function forwards its explicit `tag` to local `core-mark`, which emits
metadata containing `anchor-bounds` under that tag. No external dependency is
needed to establish this local definition and its captured namespace.

The `annot`/`annot-cetz` definitions are local to `annot.typ`. Their first
positional parameter is `tag`. `annot-cetz` takes exactly `tag, cetz, drawable`;
its local overlay reads `anchor-bounds`, then calls `core-annot(tag, overlay)`.
`annot` takes `tag, annotation` and optional named styling parameters. That file
imports three util functions by name and `@preview/tiptoe:0.4.0` as the module
binding `tiptoe`, not via a wildcard. This snapshot does not include tiptoe,
CeTZ or a complete runnable dependency graph, and no package is evaluated.

Runtime code never discovers this test folder, a user's package cache, an
installed package or the network. CLI tests copy the reviewed bytes to a
temporary explicit caller-supplied package root. No installed package is
changed. These are source-analysis fixtures, not rendering fixtures or fake
runtime substitutes.
