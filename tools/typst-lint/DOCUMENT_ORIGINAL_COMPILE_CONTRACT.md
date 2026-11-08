# Captured original-only compilation

`typst-style compile --compile-original` is an explicit macOS-only route that
compiles captured original bytes without proposing or applying source edits.
By default it runs **no lint analysis**. The explicit `--lint-document` option
adds [partial source diagnostics](ORIGINAL_COMPILE_LINT_CONTRACT.md) on the same
capture. A successful status of 0 means only that the captured PDF was compiled,
validated and streamed; it does not mean that any rule passed or that document
coverage is complete. The ordinary strict
`typst-style compile` gate remains unchanged.

For example, send the PDF through a pipe to a newly created file outside the
project. The JSON report remains on stderr:

```sh
typst_output_dir=$(mktemp -d)
(set -o pipefail; typst-style compile --compile-original --root /absolute/project \
  --entrypoint main.typ --manifest /absolute/resources.json --pdf-stdout \
  | cat > "$typst_output_dir/original.pdf")
```

The root, entrypoint and explicit resource manifest are required. The route
uses the pinned Typst compiler, captured package roots and selected fonts,
not necessarily the caller's usual Typst or system-font configuration. Without
`--discover-dependencies` it performs one baseline compile after the version
probe; dependency discovery adds a separate preliminary compilation. It checks
that source bytes match the issued include and resource captures, compiler and
staged input identities are intact, the PDF is valid, and observed dependencies
belong to the capture. These observations do not prove future or unobserved
dynamic dependencies, live-source freshness, runtime safety of arbitrary
packages, or complete isolation from operating-system reads. The private macOS
sandbox denies network and writes outside its temporary workspace; it is not a
general-purpose hostile-code sandbox.

The command writes PDF bytes only to a pipe or socket on stdout and JSON only
to stderr; it refuses regular files on either stream and known shared stream
identities before emitting the PDF. **Shell redirection is caller-managed:**
the shell may truncate a chosen
stdout or stderr target before this program starts, and the command cannot
reverse that external action. Never redirect either stream onto a project source
or required resource. A failed or interrupted stream may already contain a
partial PDF; validation and compile failures before streaming emit no PDF bytes.
The tool does not publish a PDF or authorize applying changes to source files.
