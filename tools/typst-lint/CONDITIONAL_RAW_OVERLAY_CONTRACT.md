# Conditional raw overlay diagnostics

`inspect-document --rule-set pde --preview-conditional-scoped-calls` includes
the independent `conditional_raw_overlays` report. This report looks only for
direct `#annot-cetz(...)` calls with three positional arguments in literal
markup outside any equation. Known lexical shadows, unsupported arguments,
deferred or dynamic execution contexts and ambiguous ownership are withheld.
Inline suppression directives are preserved.

The source spelling does not prove the runtime helper identity or that the
surrounding content executes. Every finding is conditional on those facts and
the intended runtime imports and package selection. The per-file report
includes the source hash, conditions and unresolved cases. It is diagnostic
only: it offers no source edits or compiler permission, does not change the
scoped-call counts and does not claim complete document rule coverage. The
shared report item budget applies; the analysis does not run without the
explicit option.
