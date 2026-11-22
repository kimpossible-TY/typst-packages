# Partial proof trailing-gap contract

`proof_rules.analyze(tree, path, resolution)` implements the partial
`pde.proof-trailing-gap` backend. It returns one immutable `ProofResult` with
`diagnostics`, `edits`, `skips`, `parse_errors` and `suppressed`. It only proposes
in-memory newline insertions. It does not evaluate packages, write source,
invoke the compiler, certify graph completeness or open any production gate.
The PDE dispatcher composes these proposals with the other rules, reparses,
checks retained coverage and suppression ownership, and requires a stable
second pass. The core pack does not run this rule.

## Reviewed callable and dependency boundary

The exact reviewed entrypoint is `local/math-blocks/0.2.0/lib.typ`, SHA-256
`542c7ce1ebfc6fbd51a7fefb2b55405a968a951b61d49170e49ef331948f36ff`.
Its relevant definitions are:

```typst
#let math_font = "New Computer Modern Math"
#let qed = h(1fr) + sym.qed
#let proof(body) = {
  parbreak()
  text(weight: "bold", font: math_font)[Proof. ]
  body
  parbreak()
  qed
}
```

The complete entrypoint review establishes these binding facts:

- `math_font` is a locally defined string and `qed` is a locally defined content
  value made from built-in `h` and `sym.qed`. Neither is reassigned. The `proof`
  closure captures these values and built-in `parbreak` and `text` in its module
  scope; caller-local shadowing cannot substitute its own values for them.
- The only imports introduce the names `capitalize-title`, `is-numeric-title`
  from text-utils and `project-ref` from project-references. They are named
  imports, never wildcards, and no imported name is referenced by `proof` or
  its captured values. No other top-level definition shadows those builtins,
  replaces `proof`, or mutates its captures.
- The local dependency sources were also read during contract review. Their
  imported functions define separate lexical closures. Their internal show
  rules, local `text` variable and runtime `json("projects.json")` do not become
  math-blocks bindings. The linter does not load or execute these dependencies,
  inspect the runtime catalog, or claim that package evaluation will succeed.
  Dependency availability and runtime effects remain separate compiler gates.

This adds only `proof` to the independently reviewed theme and `dots_space`
exports. Other helpers, facade re-exports and changed versions/bytes remain
unresolved. The explicit package-path loader retains its manifest/file identity
checks and exact content hashes. No installed package search, cache discovery,
network download or dependency traversal supplies static evidence. The CLI
integration fixture intentionally omits dependency files to verify that linting
requires only the explicit immutable reviewed entrypoint, not execution.

## Binding and output preconditions

Direct named imports, renamed imports, module fields, parenthesized callees and
immutable lexical aliases use the existing source-order resolver. A same-spelled
local helper, dynamic result or unreviewed facade never establishes identity.
Local shadows, wildcard uncertainty and direct/compound/destructuring/receiver
mutations invalidate affected bindings. An earlier independently captured alias
can retain its value; it still needs a safe immediate output context.

A wildcard of this partially reviewed package invalidates previous names and
builtins before establishing the five reviewed exports. It can prove `proof`
but cannot also prove an unreviewed following `block` or another builtin.

The backend requires a complete `FuncCall`, directly preceded by its adjacent
root `Hash`, in a markup root. The hash must start its physical line apart from
ASCII indentation or the initial UTF-8 BOM. The incoming resolver facts must
certify root prose/style ownership and nondeferred execution. The call must
have exactly one literal positional content block, either `#proof[body]` or
`#proof([body])`, including proven renamed/module/parenthesized callees. Options,
named or dynamic arguments, extra arguments, comments around the argument,
outer wrappers, captured/conditional/deferred contexts and nested content
bodies remain unsupported. Comments and arbitrary original content *inside*
the literal body are retained byte-for-byte, not inspected or rewritten by
this backend.

Callable identity does **not** extend the resolver's general output-safety
contract. In particular, the existing resolver makes following root prose/style
facts conservative after outputting a proof. A later proof may therefore remain
unresolved even when the first proof has a repair. This deliberate limitation
avoids certifying arbitrary body effects or broader paragraph behavior.

## Sibling, label and exact edit contract

The next non-whitespace sibling must be a real root prose `Text` node, a complete
heading/list/term/raw construct, a typed display equation, another proven proof
call, or a proven builtin `block`/`figure`/`heading`/`list`/`enum`/`terms` call
with the same bounded content-only argument shape. No search crosses unknown
code, setup statements, inline wrappers, explicit break calls, comments or
suppression directives. A closing body or EOF is never a new sibling.

One semantic `Label` may follow the proof on the closing call's physical source
line, separated only by ASCII spaces/tabs. The label is included before the gap
insertion. Cross-line labels, multiple labels or intervening comments are
ambiguous and receive no proposal. The label name is never changed. Isolated
Typst 0.15.1 query evidence confirms that this supported label attaches to the
complete returned proof sequence and retains exactly the same target content
before and after the edit.

For an eligible next sibling:

- With one existing LF/CRLF, insert one matching newline immediately after it,
  before the next line's original indentation. Preserve the entire original
  closing line and every existing byte.
- With only horizontal separation, insert two matching newlines after the
  complete call/label to create exactly one blank source line. Use the next
  physical line terminator, then the preceding one, then LF. Preserve the
  original horizontal separator.
- With two or more existing newlines, do nothing. Bare CR and non-ASCII
  separators are unsupported. EOF, including trailing whitespace or one
  attached label, gets no synthetic paragraph.

All proposals have `start == end`. Call, body, label, comment, Unicode, BOM and
original newline bytes are never removed or replaced. A second parse must
retain the full preorder syntax and literal bytes of every non-whitespace root
construct, including all its nested trivia. Only root whitespace nodes may
change. This refuses same-line text such as `= Heading`, `- item` and `/ term:`
when inserting newlines would reinterpret it as structural syntax. Parse errors,
stale resolution, reparse failure or attachment changes produce no edits.

The diagnostic/suppression anchor is the proof's opening hash. An ignore-next
attached to that call suppresses both its finding and edit. A directive on the
next sibling cannot suppress or move onto the proof. The shared composer also
checks suppression targets for other rules inside the preserved proof body.

## Evidence and remaining work

- `tests/test_proof_rules.py`: exact imports/aliases, partial wildcard behavior,
  stale/unreviewed packages, source-order bindings, shadows/mutations,
  structural/prose siblings, same-line and multiline calls, labels, comments,
  nested/deferred/body-end/EOF refusals, LF/CRLF/BOM/Unicode, suppression,
  structural-reinterpretation refusal and second-pass zero edits.
- `tests/test_proof_integration.py`: real check, dry-run, fix and compile-wrapper
  invocations; source/output preservation; explicit package fingerprints;
  composition with script repairs; core isolation, config ignores, suppression
  ownership, idempotence and continued incomplete-coverage/gate closure.
- `tests/test_proof_render.py`: opt-in Typst 0.15.1 before/after PDF bounding boxes
  and label queries. Temporary explicit copies of math-blocks, text-utils and
  project-references are the only package inputs; the explicit cache starts
  and remains empty. The proof text retains its positions, the QED reaches the
  right margin and the next prose starts below it at the left margin. Label
  query JSON is identical. Package originals/copies remain unchanged.

Run focused tests with
`python3 -m unittest discover -s tools/typst-lint/tests -p 'test_proof*.py'`.
Set `TYPST_LINT_RENDER_TESTS=1` to enable the render fixture when Typst 0.15.1,
pdftotext and the pinned syntax adapter are already available. Only the test
harness applies candidate bytes to its own temporary fixtures and compiles
those fixtures. Passing these tests is not complete backend coverage or
permission for production writes/compilation.

Nested/theorem bodies, facades, general call signatures, successive proofs
after unknown output and wider document/style/dependency reasoning remain
unimplemented. Every backend and the production build gate remain partial.
