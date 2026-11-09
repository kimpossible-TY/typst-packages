# Read-only literal include occurrence context

`include_context.py` is an independent source-capture layer. It changes no
import/export graph, resolver, public source profile, CLI, transaction, package
lookup or compiler gate. It never executes a document, follows imports, loads
packages, searches directories or consults a cache. Tests use generic temporary
projects, not consumer documents or copied private prose.

## Capture API and retained reads

`load_include_context(project_root, entrypoint_sources, *, budget='standard-v1')` takes a canonical
absolute project root and a nonempty bounded iterable of immutable
`(canonical_project_relative_typ_path, exact_bytes)` tuples. Entrypoint bytes
must be immutable UTF-8 and are checked against the actual retained read before
the parser receives them. Each entrypoint and every reachable supported literal
include target is read with the existing `package_loader._Snapshot` no-follow
descriptor reader. Files and directories remain open until **after** occurrence
derivation and final `_Snapshot.verify()` content and identity checks.

The optional budget selects exactly `standard-v1` or `book-v1`. The standard
default retains the original 32-source / 200,000 aggregate-node limits. The
explicit book budget permits 64 sources / 400,000 aggregate nodes for finite
book-sized inventory; every other bound is identical. Unknown names, mutable
options, string subclasses and arbitrary numerical limits fail before root
capture or parsing. This selects capture resources, not a linter source profile
or downstream analysis permission.

The bounded directory cache checks the original parent-relative name and retained
descriptor identity before reuse. Every original request traversal is checked,
including requests whose normalized target was already captured. A canceled
`name/..` component must exist as a safe actual directory. Symlinks, hardlinks,
nonregular files, FIFO races, changed directory chains, changed bytes/metadata,
same-byte inode replacements and distinct logical file/directory names resolving
to one filesystem identity reject the whole capture. Final verification rereads
retained file descriptors and checks all retained names and directories. No
unchecked pathname byte reads are used.

Supported requests are direct unescaped string operands targeting relative
`.typ` files within the explicit root. Spaces and Unicode are ordinary bounded
path characters. `.`/`..` and repeated separators may normalize to one source,
but original traversal is never discarded before safety checks. Absolute,
package, root-escaping, non-Typst, control-character and empty requests are not
followed. Escaped strings and arbitrary expressions are not decoded/evaluated.

An initial missing include component observed before its descriptor read is an
explicit unresolved edge. Disappearance after that presence observation, or
later loss of any retained component, is a fatal capture error. A source already
captured does not become safely missing when a later site loses its name.
Initially missing entrypoints are fatal, not unresolved include edges.

## Exact immutable records

All returned records are frozen dataclasses containing tuples and immutable
bytes. `IncludeContext` exposes `project_root`, `entrypoints`, `sources`, `edges`,
`occurrences`, `unresolved`, and `source_for(canonical_path)`.
Its `budget` field retains the exact selected budget name (default
`standard-v1`) and is covered by original-parser issuance verification.

- `IncludeSourceIdentity(path, source_sha256, tree_sha256)` identifies exact
  logical source bytes and the complete pinned parser tree.
- `IncludeSource(identity, source, tree, fingerprint)` retains the exact bytes
  and validated `syntax_client.Tree`. Its `path` property forwards identity.path.
  The fingerprint is the original retained tuple `(st_dev, st_ino, st_mode,
  st_nlink, st_uid, st_gid, st_size, st_mtime_ns, st_ctime_ns)`, never a fresh stat.
- `IncludeNodeIdentity(source, node_id, kind, start, end)` binds a preorder node
  and exact UTF-8 byte span to both source and tree hashes. A node ID or span
  without its source/tree identity is insufficient.
- `IncludeEdge(node, literal, request, raw_request, target, ancestry,
  content_ancestry, call_ancestry, unresolved_codes)` represents **each** actual
  `ModuleInclude` node. `literal` identifies a direct `Str` operand, including an
  unsupported escaped string; otherwise it is `None`. `request` is its decoded
  unescaped content or `None`. `raw_request` is the operand expression's exact
  bytes, including quotes for a string, or empty for an unrecognized shape.
  `target` is a captured source identity or `None`, never just a path spelling.
  `ancestry` contains every parent root-first, excluding the include itself.
  The two filtered ancestry tuples retain content-block and function/math-call
  parents respectively. Sibling order is recoverable from the retained tree.
- `IncludeOccurrence(entrypoint, source, edge_chain, unresolved_codes)` is a
  syntactic expansion opportunity. Both source fields are source identities.
  Its identity is the entrypoint snapshot plus the ordered exact include-node
  chain. Root occurrences have empty chains. Repeated sites, diamond paths and
  different entrypoints never collapse merely because their source is shared.
- `IncludeUnresolved(node, codes, entrypoint=None, edge_chain=())` records an
  unresolved edge or a cycle-closing occurrence. Edge records omit entrypoint
  and chain; cycle records include both.

Source hashes use SHA-256 of exact bytes. Tree hashes use SHA-256 of ASCII bytes
from `json.dumps(value, separators=(',', ':'), ensure_ascii=True)`, where value is
`('include-tree-v1', 'typst-syntax-0.15.1', 'adapter-0.2.0-schema-2', tree.mode,
tuple((n.kind, n.start, n.end, n.children, n.errors, n.operands, n.display)
for n in tree.nodes))`. The pinned adapter client validates protocol, source,
every field/link/span, preorder, partitioning and tree depth before issuance.
Malformed trees or parser failure reject the capture rather than returning
incomplete syntax with a successful-looking source identity.

Sources and entrypoints are sorted by canonical path. Edges sort by source path
then preorder node ID. Occurrences use entrypoint order and depth-first preorder
include-site order. Unresolved edge records use edge order, followed by
cycle-closing records in occurrence order.

## Explicit uncertainty and occurrence completeness

All safely captured literal sites are traversed, even inside opaque containment.
Capture never proves that any site executes, how often it executes, whether an
import initializer is inert, or whether a content value is rendered.

Stable request codes are `dynamic-request`, `escaped-request`, `package-request`,
`absolute-request`, `invalid-request`, `root-escape`, `non-typst-request` and
`missing-source`. Containment codes are `conditional-containment` for
`Conditional`/`ForLoop`/`WhileLoop` ancestry and `deferred-containment` for
`Closure`/`Contextual`/`ShowRule` ancestry. Both may occur. If neither applies,
any ancestry beyond the root `Markup`, including calls, content blocks and code
blocks, yields `unknown-containment`. Root-direct syntax has no containment code,
which is still not an execution certificate.

Edge codes propagate along occurrence chains. A target already active on that
same chain produces one cycle-closing occurrence with `include-cycle`, then no
further expansion. The edge itself is retained: cycle status belongs to the
occurrence path, not to a deduplicated importer/target pair. Unsupported edges
with no target have no invented child occurrence. Budget exhaustion is fatal;
no depth/width-truncated context is returned as complete.

## Parser-origin assertion, not a live receipt

Frozen dataclasses and matching public hashes alone cannot prove a parser
produced a caller-constructed tree. `assert_include_context(context)` therefore
requires the **identical** object issued locally by `load_include_context`.
An identity-keyed weak-reference registry retains an independent bounded
primitive signature of its original source/tree/edge/occurrence facts. It never
hashes dataclass graphs or retains the context through its cleanup callback.
Garbage collection removes the registry entry. Constructor-built, copied,
serialized or foreign-module values do not acquire origin evidence. Altered
nested data, deleted fields, wrong primitive types and oversized/cyclic links
are refused before unbounded signature traversal. The assertion does not parse,
stat, open or read a path and does not refresh the historical capture.

The assertion establishes original parser-produced snapshot facts only. It does
not establish current filesystem identity, editor exclusivity, lexical bindings,
render language/show state, pure evaluation, package authenticity, repair scope,
candidate/committed provenance, resource availability or compilation authority.
There is no candidate rebase, write, compile or installation API in this module.

A later pure scoped-context layer must call this origin assertion, independently
rederive its allowed grammar/occurrence chains, inspect surrounding AST and
derive occurrence-specific provenance. An include does not export names or
import the parent's lexical bindings. Each child resolves its own module scope.
Any permitted render-context inheritance requires that separate certificate;
capture deliberately provides no lexical/style fields to guess from.

## Fixed bounds and failures

There is no arbitrary public limit override. The only explicit selection is
the fixed `budget='book-v1'` alternative. Module constants name the exact
denominators:

- `MAX_CONTEXT_FILES`: 32 unique sources including all entrypoints under
  `standard-v1`; `BOOK_MAX_CONTEXT_FILES`: 64 under `book-v1`.
- `MAX_SOURCE_BYTES`: 1 MiB per source; `MAX_CONTEXT_BYTES`: 4 MiB aggregate.
- `MAX_CONTEXT_NODES`: 200,000 aggregate parser nodes under `standard-v1`;
  `BOOK_MAX_CONTEXT_NODES`: 400,000 under `book-v1`. The adapter's unchanged
  100,000-node/depth-256/output/deadline limits also apply to each parse.
- `MAX_INCLUDE_NODES`: 128 include nodes, including unsupported requests.
- `MAX_INCLUDE_DEPTH`: 16 edges per occurrence chain, roots at depth zero.
- `MAX_OCCURRENCES`: 1,024 occurrences, including roots and cycle closures.
- `MAX_CONTEXT_DIRECTORIES`: 64 distinct traversed project directories,
  including project root and canceled traversal components. Absolute root
  ancestors remain retained and separately bounded by the root path ceiling.
- `MAX_PATH_BYTES`: 512 UTF-8 bytes per canonical source path or literal request;
  `MAX_TOTAL_PATH_BYTES`: 16 KiB summed over unique canonical source paths.
- `MAX_ROOT_BYTES`: 4,096 UTF-8 bytes and `MAX_ROOT_COMPONENTS`: 128 path parts.
- `MAX_REQUEST_EXPRESSION_BYTES`: 4,096 bytes per raw operand expression;
  `MAX_TOTAL_REQUEST_BYTES`: 4 MiB summed over all include operand expressions.
- `MAX_ANCESTRY_NODES`: 32,768 summed parent references over include nodes.

The selected budget is retained in the context and its issuance signature;
changing that field does not reissue a context. Larger inventory does not widen
the independent scoped paragraph analyzer, whose 200,000 retained-source-node
limit still explicitly refuses a larger capture. Callers can inspect the include
graph or use independently bounded syntax-local analysis without mistaking the
inventory budget for complete document semantics.

All fatal errors derive from `IncludeContextError` (`InputError`). Invalid
explicit arguments/entrypoint encoding use `IncludeInputError`; parser
unavailability/malformed syntax use `IncludeSyntaxError`; unsafe/raced reads,
invalid dependency encoding and altered/unissued context evidence use
`IncludeCaptureError`; this layer's explicit resource exhaustion uses
`IncludeLimitError`. Adapter-internal resource failures remain syntax failures,
not successful partial captures. File content changing during a read remains
a capture error, even if that change also exceeds a reader budget.

## Focused verification

`tests/test_include_context.py` uses temporary fixture-owned projects and the
actual pinned syntax adapter. It checks exact source/tree/node identities,
ancestry, repeated/diamond/multiple-entrypoint occurrences, explicit unsupported
requests/containment/cycles, original and cached traversal, all named budget
denominators, retained final checks, byte/inode/directory races, links and aliases.
Generic temporary book fixtures verify the original default still refuses a
64-source closure, the explicit budget captures it, source 65 refuses, a dense
220,000-plus-node closure is captured only with the book budget, and unchanged
byte/include ceilings plus downstream analysis limits remain enforced.
Origin checks cover copied/constructed/deep-copied values, nested tampering,
deleted fields, primitive-type substitutions, oversized/self-linked fields,
parser/filesystem-free assertions and weak-registry garbage-collection cleanup.
These are focused source-capture tests, not an aggregate or compiler/native
render pass and not evidence of production document rollout.
