# Partial builtin math cancellation contract

This diagnostic-only backend implements a bounded part of
`pde.theme-cancelmark`. It follows the OpenSpec description and conditional
replacement policy, but does not implement that replacement. The checkout has
no reviewed `cancelmark` implementation or facade binding. No helper, import,
package migration, replacement signature or light/dark stroke effect is invented.
`CancelResult.edits` is always empty. Source writing and compiler production
gates remain closed, and all CLI modes still report incomplete coverage.

## Binding evidence, not spelling

The syntax adapter pins the official `typst-syntax` crate to 0.15.1 and distinguishes
`MathCall`/`MathIdent` from `FuncCall`/`Ident`. The resolver separately models the
reviewed builtin math module and its `cancel` element. The ordinary code-mode
builtin set does **not** contain `cancel`.

- A math identifier searches lexical frames before the math-library fallback.
  Only builtin `cancel` is reviewed in that fallback. `$cancel(x)$` therefore
  receives builtin cancellation identity unless a visible local or unresolved
  wildcard masks it. An unrelated local `cancel` never inherits this identity.
- Code mode can resolve the builtin module `math`, its field `cancel`, and
  simple immutable aliases of those values. For example,
  `#let crossout = math.cancel` followed by `$crossout(x)$` is a builtin call.
  Parenthesized value aliases, module aliases and named builtin module imports
  such as `#import math: cancel as crossout` retain that exact identity.
  An unaliased identifier import preserves its source identifier: `#import maths`
  never invents a lexical `math` binding. Other unaliased dynamic source forms
  have no reviewed destination-name proof and invalidate namespace evidence.
- Math lookup does not use the global-library fallback. `$math.cancel(x)$` is
  unresolved unless a lexical `math` binding explicitly captures that module.
  `#cancel[...]` is not a builtin merely because it is spelled `cancel`, and
  `$#cancel($x$)$` does not receive math fallback for its code-mode identifier.
- A one-letter alias in `$c(x)$` is math text, not a callable identifier in the
  official tree. An escaped code expression `$#c($x$)$` can resolve a known alias.
- Builtin-module wildcard imports certify `cancel` only and invalidate other
  namespace facts because the remaining exports are unreviewed. Unknown
  wildcards invalidate both aliases and builtin fallback. A complete reviewed
  package namespace can prove that its wildcard does not mask cancellation;
  partial reviewed package namespaces cannot.
- Local declarations, destructuring and selective imports mask their actual
  names. Assignments and possible branch/loop mutations invalidate the affected
  alias/module. Earlier copied immutable function values remain distinct.
  The callee is resolved before walking argument expressions, so a later
  argument assignment does not retroactively change that captured function.

A binding is source-snapshot evidence produced from the real syntax tree, not a
claim that a document successfully evaluates. Unsupported arguments or other
compiler errors do not become safe edits. No dependency, installed cache or
consumer source is executed to establish these facts.

## Diagnostic boundary and refusals

The backend reports resolver-certified builtin cancellation calls in mathematical
source: `MathCall` nodes, including nested calls, and escaped `FuncCall` nodes
inside real math. Simple immediate content/code scopes preserve lexical lookup.
Code-only calls outside mathematical source are outside this backend, even when
their builtin function identity is known.

Deferred closure/context/show bodies and conditional/loop bodies are explicitly
refused. Unknown function results, higher-order wrappers, `.with` results,
local facade exports, arbitrary module values, shadowed names and stale or
malformed source facts do not prove cancellation identity. Refusal reasons are
reported, and every result remains partial even when no diagnostic is found.
This is not general Typst evaluation, closure-capture analysis or document-graph
analysis.

The spelling `cancelmark` is not a trusted helper contract. Unrelated local or
unresolved imported helpers, including calls carrying deliberate `stroke:` or
`cross:` options, are left untouched. Conversely, naming a proven alias of the
builtin `cancelmark` does not change that function into the absent facade helper.
Arguments, comments, Unicode, BOM and newlines are never rewritten. Algebraic
pairing, desired colors and which terms should cancel are not inferred.

Valid `ignore-next` directives use the existing next-syntax-construct ownership.
A directive before an equation can suppress all cancellation diagnostics within
that equation, and never leaks into the next equation. Configuration ignores
also suppress findings. Suppressed calls receive no repair-unavailable entry.
Every unsuppressed diagnostic explains that a repair is unavailable until the
exact helper implementation, lexical availability and compatible signature can
be established independently.

## Verification

- `test_cancel_rules.py` uses the real pinned adapter for aliases, module imports,
  shadowing, wildcard namespaces, math/code lookup, nested/deferred/dynamic scopes,
  mutation, snapshot refusals, intentional helper options, BOM/Unicode/CRLF and
  suppression ownership. Combined script proposals retain cancellation findings
  or suppressions without treating byte-coordinate shifts as new evidence.
- `test_cancel_integration.py` invokes the genuine CLI in check, dry-run, fix and
  compile modes against isolated temporary consumers. It verifies partial
  coverage, no cancellation edits, unchanged source/config/output/package files,
  configuration/inline ignores and core-pack exclusion. A compiler tripwire
  verifies that production compile remains uninvoked.
- `test_cancel_render.py`, opt-in with `TYPST_LINT_RENDER_TESTS=1`, requires Typst
  0.15.1. It compiles isolated unchanged fixtures with explicit empty package and
  cache directories and queries real equation content. It confirms builtin
  cancellation identity, shadow behavior, aliases/imports, math/code fallback,
  single-letter syntax and exact retained builtin `stroke:`/`cross:` options.
  It also demonstrates genuine runtime cancellation inside explicitly refused
  deferred/dynamic contexts, so refusal is never described as a non-violation.

These are syntax/identity and unchanged-output probes. They do not certify a
`cancelmark` replacement, theme-aware light/dark rendering, production safety,
full rule acceptance or a complete linter.
