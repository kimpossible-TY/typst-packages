# In-memory intermediate document context

`document_intermediate_context.derive` takes an issued baseline
`PreviewFootnoteEvidence` and source-sequence records. It independently
recomputes and replays the selected sequence against that baseline; callers
cannot supply arbitrary intermediate source bytes. The bounded record copy has
a 16 MiB serialized-byte limit and a one-million-chunk encoding limit. Existing
profile, suppression, conflict and withheld-source checks remain in force.

The resulting immutable resource mapping and newly parsed include context stay
in memory. Include and import statements must retain their source bytes,
sequence-mapped site positions and mapped enclosing-owner boundaries; unknown
changes are refused. Import graph sources are rebuilt from the verified
intermediate bytes while captured package selections and package bytes remain
bound to the baseline. A changed source's zero-sentinel fingerprint is **not**
a filesystem identity. The derived context's ordinary include-context check,
and any binding issued from it, remain tied to the original issued proof and
snapshot. If that backing expires or changes, validation fails. Derivation
from a derived binding is refused, bounding provenance depth to one.

`issue_binding` enables conditional analysis on this view without reading
intermediate sources from the live filesystem. It does not issue a new
`ResourceCapture`; normal baseline staging rejects changed intermediate bytes.
Runtime package selection, include execution, rendering and complete coverage
remain unproved. The view does not authorize compilation, source application
or publication. Combining later edits still requires a new checked final
resource mapping, cross-source route validation and the existing compiler
staging/dependency checks. Validation is not a concurrent-write lock.
The separate opt-in `preview-compile --compose-modern-highlights` uses this
view for its intermediate analysis, and independently validates the final
resource map before private staging; it does not make this context itself a
compiler or source-write authority.
