# Conditional footnote punctuation evidence

Use `inspect-document --rule-set pde --package-path <root>
--footnote-cache-path <cache> --footnote-punctuation-evidence` to request this
additional diagnostic-only analysis. The flag is optional, requires the
explicit footnote cache path, and is separate from punctuation repair previews.
The extra batch is not run without the flag.

`conditional_footnote_bindings.punctuation_by_file` reports conditional nested
prose punctuation hints only when the same source span has freshly traced
conditional built-in footnote evidence. Each hint retains the source hash,
captured facade and package evidence, package requests, and execution and
runtime-selection conditions. Suppressed findings and unresolved cases remain
separate. Unsupported call shapes are reported as unresolved per call; they do
not hide independently verified hints in the same file.

The result neither proves that imports select those packages at runtime nor
that the source executes or renders as expected. It provides no edit or
compiler permission, source candidate, complete document coverage, or global
lint pass. It does not add its findings to existing hint or coverage counts.
The source-bound session is checked before and after inspection; a changed or
unissued session produces no results. Batch output is subject to the document
report item budget.
