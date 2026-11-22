# Anchor/overlay source fixture

These are unchanged selected public `@preview/cetz:0.4.2` artifacts retrieved
through the authenticated GitHub contents API for `typst/packages` on
2026-11-07. Their public source is
https://github.com/typst/packages/tree/main/packages/preview/cetz/0.4.2 .
Every returned Git blob SHA-1 was recomputed from the exact saved bytes before
using the fixture. The manifest identifies the upstream repository as
https://github.com/cetz-package/cetz and the license as LGPL-3.0-or-later.
The original `LICENSE` is included. No source was modified or substituted.

The immutable identities are:

| File | SHA-256 | Git blob SHA-1 |
| --- | --- | --- |
| typst.toml | 2ccea70cc14ad8d79897179d3c8946758e610903e59f154acdb0f9b9ff81ac02 | c6bd91096009e2abf6bc7316ade597644be7b8e3 |
| src/lib.typ | 53380cd9de16c1f845f325e9d8be8e0f03493c2073fba1c54d546e1906a8f8ed | 7bd907247b17c22cdfe2f6f1bf78f4d74417d6ed |
| src/draw.typ | 2ab63ab5c20e875c0f12a3630bb99606e47aa076b5b94769f6930f7a24270258 | 3443d926b76b120d0319426ddb57d41a32e499a8 |
| src/draw/shapes.typ | 8001363447a69292df8a5210c3963987c65e203e7d0c6981759e7d4bf21e4db1 | cb556157a67ddf402ca3c48668c7642eb4adcd8f |
| src/draw/transformations.typ | 98479838bee69d302d8ab394c9240c7ab1eb4794395927133350f433cfac8fc1 | 216b570991c115d54b54859f7528c5aa7022d227 |
| src/draw/styling.typ | 83c0bbbc53e10c974776440355df41add4329436a78ad033fdcc2682f7a2e33a | 0e7b14717ea473fd973bb288f8de85c3ab829b3f |
| src/draw/grouping.typ | 82611e93ad2f6fccde4a313db02eb8a6aeb8d596ce959b747d4830f25d855da0 | a6c8c7ef5890373a38812b24f479b96792ac4bcc |
| src/coordinate.typ | c59ec4660d510f847d7ea245376a6eb1489db722d328c20b4d34c1fc3ff84216 | 42495dd0bc531e595065b8256511fb0a50470acf |
| src/anchor.typ | bcb7edfaa727b3d5b0be4f9003761b4446a5ff96ed374d64d836fde624b60a08 | 553639466a9e711c66f9f19603e1be264064437b |
| LICENSE | da7eabb7bafdf7d3ae5e9f223aa5bdc1eece45ac569dc21b3b037520b4464768 | 65c5ca88a67c30becee01c5a8816d964b03862f9 |

The complete entrypoint imports `draw.typ` as the `draw` module. Its named
`line` import is from `draw/shapes.typ`. In that exact file, `line` takes
`..pts-style`, calls `pts-style.pos()` to obtain the complete coordinate list,
and passes those points to `coordinate.resolve`. Named arguments are styling,
not positional coordinates. The three wildcard imports before the definition
are included to review namespace capture: none exports `line`, and the shared
`coordinate` module name denotes the same `/src/coordinate.typ` module.
`coordinate.typ` resolves raw anchor strings, while `anchor.typ` enumerates the
eight compass direction names supported by the rectangle preamble in the
reviewed local scoped wrapper.

This is selected syntax/namespace/coordinate-role evidence, not a runnable
package graph and not a rendering-equivalence claim. Omitted imports are not
replaced with fake modules. Tests do not invoke Typst on this incomplete package.
Runtime analysis must consume immutable snapshots from a caller-supplied
explicit package root. It must never search installed packages, home caches or
the network, and it must not evaluate or traverse imports to discover files.
