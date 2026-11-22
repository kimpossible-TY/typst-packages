# Read-only package freshness report

`typst-lint packages-status` compares explicitly selected local package copies
and preview package versions. It does not install, update, modify a checkout,
fetch Git objects, or authorize any source or package write.

```sh
typst-lint packages-status --root /absolute/project --manifest /absolute/resources.json \
  --checkout /absolute/typst-packages --upstream https://github.com/OWNER/REPOSITORY.git \
  --ref refs/heads/main --online --format json
typst-lint packages-status --root /absolute/project --manifest /absolute/font-seed.json \
  --discover-dependencies --entrypoint main.typ --checkout /absolute/typst-packages \
  --upstream https://github.com/OWNER/REPOSITORY.git --ref refs/heads/main --online --format json
```

The first form classifies local and preview paths explicitly listed in the
caller-supplied manifest. This is not proof that a compiler used those paths.
The second runs the pinned macOS compiler once with the seed's explicitly
selected fonts in the existing network-denied, outside-write-denied sandbox.
It selects paths observed in that one run and records its baseline PDF hash and
observation time. The later file comparisons are separate sampled reads; their
bytes are not proved to be the bytes read during discovery. Neither form proves
future execution, unexecuted branches, exhaustive OS reads, or compatibility.
Selected font paths and hashes are listed separately; selection does not prove
that a font was used, and the compiler dependency list does not exhaustively
represent font or operating-system reads. Font bytes are never reported.
Only paths under the explicitly supplied `packages` and `cache` roots are
classified. Unsupported namespaces and incomplete package paths are reported,
not silently treated as current.

`--online` explicitly permits one unauthenticated lookup of the caller-selected
GitHub HTTPS branch/tag ref and one lookup of the fixed official Typst preview
index. Without it no network lookup is attempted. The reference name, remote
commit, observation time and registry snapshot hash are recorded. The selected
ref is the comparison target, not necessarily a release or default branch.
An unavailable lookup or missing local Git object yields unknown; nothing is
fetched into the checkout. Preview version ordering does not verify installed
bytes, direct dependency status or compatibility with a newer version.

Local results distinguish a checkout/ref relation, the supplied installation's
whole-package snapshot against the checkout, and its tracked files and executable
modes against the locally available selected upstream Git tree. Git does not
track empty directories. Per-file comparisons cover only listed paths. All
captures are bounded and rechecked, but are sampled evidence rather than a live
filesystem lease or proof of installation origin. Global Git dirty state is
unknown: Git status is not invoked because it can run configured clean filters.
Raw checkout/HEAD and index/HEAD comparisons are separately reported and do not
claim equivalence to Git's filter-aware dirty state. Unsupported Git versions,
partial/promisor/alternate object stores and incomplete history remain unknown.

JSON returns `status=0` when a report is produced, including reports containing
unknowns or newer versions; it does not mean every package is current. Input or
unsafe capture refusal returns 2. The executable cooperatively handles SIGINT
and SIGTERM, stops later stages once cancellation is observed, and attempts to
terminate and reap its compiler, Git, and registry workers. A registry worker
cleanup failure is reported explicitly. Cancellation and process creation are
not atomic: a worker started concurrently with a signal may run briefly before
the next cancellation check. Network workers are bounded and use no inherited
interactive credentials.
