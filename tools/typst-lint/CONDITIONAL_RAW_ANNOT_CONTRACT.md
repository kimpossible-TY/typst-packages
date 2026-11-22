# Conditional raw annotation diagnostics

`inspect-document --rule-set pde --preview-conditional-scoped-calls` includes
the independent `conditional_raw_annotations` report. It inspects only direct
`#annot(...)` calls in literal markup outside any equation, with two positional
arguments and a bounded set of named options. Explicit lexical shadows,
unsupported argument forms, deferred or dynamic execution contexts and
ambiguous ownership are withheld. Inline suppression directives are respected.

The spelling `annot` does not prove its runtime binding. Every diagnostic is
conditional on the intended reviewed mannot helper, runtime package selection,
imports and actual execution. Each source report includes its hash, conditions,
suppressed findings and unresolved cases. It is diagnostic only: it offers no
source edits or compiler permission, does not change the scoped-call or raw
overlay counts, and does not claim complete rule coverage. The shared report
item budget applies; this extra analysis does not run without the explicit
scoped-call preview option.
