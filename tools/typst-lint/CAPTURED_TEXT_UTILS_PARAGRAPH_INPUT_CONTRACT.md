# Internal captured text-utils paragraph inputs

`captured_text_utils_paragraph_inputs.py` owns the input boundary for the new
identity `captured-text-utils-paragraph-v1`. Capture itself grants no write,
compiler, resource, publication or helper-call capability. The separately
composed explicit CLI uses the repair bridge and package compiler/publisher;
this capture module does not register or authorize those routes. Historical profiles, graph modules,
transaction/recovery formats and package bytes are unchanged.

## One transaction and five files

`CapturedTextUtilsParagraphInputSession(root, target, package_path=...,
journal_directory=..., lease=...)` requires one literal canonical root-relative
`.typ` target and the caller's genuine `OfflineExclusive` assertion. It never
asserts editor state itself. Canonical absolute root, package and storage paths,
source profile, literal target, limits, exact evidence selection, path collisions
and transaction storage options are checked before filesystem work.

The session constructs exactly one `ProtectedTransaction`. Its sole writable
selection is the root target. Its exactly four present read-only
`EvidenceInput(..., utf8=True)` records are:

1. The root `.typst-lint.toml`.
2. The built-in full reviewed rule catalog.
3. Explicit `package_path/local/text-utils/0.1.2/typst.toml`.
4. Explicit `package_path/local/text-utils/0.1.2/lib.typ`.

No discovery, directory crawl, ambient package cache, environment lookup,
`load_packages`, installation, network request, consumer document or font input
exists here. Presence probes use no-follow descriptors and retain no file bytes
or identity authority. They classify initial absence only. Actual authoritative
bytes and identities come exclusively from the transaction snapshot under its
existing root lock, offline assertion, private same-filesystem external journal
store and pending-journal checks. `retain_ancestry=True` retains every traversed
no-follow directory identity, including intermediate ancestors.

The transaction's source-like UTF-8 evidence policy applies to all four records:
ordinary permissions, effective ownership/preservable group, bounded metadata,
ACL/filesystem-flag and hardlink checks. Read-only evidence is accepted. The
root requires owner-write permission. No dependency can become a write or
recovery selection. Canonical path collisions and retained device/inode aliases
are refused, including alternate case or normalization names. Different retained
directory names sharing one actual identity and input directories aliasing the
transaction store/namespace also refuse. Baseline revalidation checks original
bytes, inode, mode, metadata, timestamps and retained ancestry; it never blesses a
fresh replacement with equal bytes.

## Exact retained semantic evidence

The pure core owns the single strict `validate_config` implementation and frozen
`ParagraphConfig` type. Capture calls it on retained config bytes only. The
configuration requires `rule-set="pde"`, `include=[target]`, and exactly
`[capitalization] language="en"`. Only an optional matching `entrypoint` is
allowed. No other key, implicit selection, exclude, suppression or language is
admitted. This config selects obligations for the root only.

The retained manifest is parsed as TOML and independently requires name
`text-utils`, version `0.1.2`, entrypoint `lib.typ`, and the local namespace if
specified. Those values never determine another read. The manifest is also
pinned to 173 bytes and SHA-256
`e1d27052412a39d4f839dede28406159c1c9a5394592a73c2ed5666dcbc60df2`.
The entrypoint is pinned to 5,583 bytes and SHA-256
`7e151150dac47b01ebf7706457906133d74a52abee1b5c43ce824ff89279bd71`.
The existing full-catalog validator requires its reviewed digest
`955b0c721027369ec1fd100a7da00fc7304693bb40e2916904c6f3b06fae1826`,
full 33-rule matrix and immutable record representation.

A genuine `PackageSource` is constructed from the retained entrypoint bytes and
its actual canonical absolute retained evidence pathname. It has no supplemental
artifacts. The session invokes `facade_provenance.certify_initializer` on that
exact object. Its genuine certificate checks the six reviewed deferred
initialization exports and absence of package dependencies. Neither a caller
flag nor a previous loader result supplies authority. The root Tree and package
Tree are produced by the existing pinned official syntax adapter from those
same retained byte objects. Parser errors refuse the input.

The package is read-only semantic evidence, not another selected PDE source.
Initializer safety does not certify helper invocation, full-package lint
coverage or rendering equivalence. The separate repair bridge must immediately
certify the root grammar, independently review the invoked helper and discharge
all selected root obligations. Capture itself issues no root grammar proof.

## Bounded capture and accepted sizes

Optional immutable `ReadLimits` may only narrow existing transaction defaults.
The original limits and selection, including the session root/target/profile,
are retained and checked before entry and throughout the receipt lifetime. The
exact constructed transaction is separately owner-pinned; an equivalent foreign
transaction cannot be substituted. Existing transaction per-view aggregate,
metadata, journal, directory and source-like reader bounds remain in force.

Accepted source and package size ceilings are 1 MiB each; the package's exact
5,583-byte pin is stricter. The accepted config and manifest ceiling is 64 KiB
each; the manifest's exact 173-byte pin is stricter. Config/manifest accepted-size
checks happen **after transaction capture**. They are not a promise of 64 KiB
physical reads: each UTF-8 evidence record uses the unchanged transaction's
source-like physical read limit, at most its default 1 MiB, plus existing
aggregate/metadata bounds. Catalog bytes are likewise transaction-bounded and
then exact-hash checked. No shared transaction API is widened. Parser node,
depth, output, byte and execution-deadline limits are unchanged. These are not
total-process-memory guarantees.

Initially missing files, invalid initial UTF-8, malformed initial syntax and
unsupported initial config/package/catalog bytes use existing `InputError`
classification. Unsafe reads, disappearance after presence checks, aliases,
budget failures, changed capture facts or stale/foreign/altered receipts use
`TransactionError`. These internal subclasses establish no public error schema.

## Opaque receipt and private borrowing

Within an active context, `session.capture` returns its identical issued
`CapturedTextUtilsParagraphInputs`. It retains root/package Trees, the frozen
config, exact manifest bytes, exact PackageSource and initializer certificate,
immutable catalog records, fixed evidence-key map and a bounded report. The
report labels the package role `read-only-semantic-evidence`; hashes, sizes,
reports and serialized/copied objects confer no live authority.

The session pins its exact concrete owner with a weak self-reference and
independently retains the original objects, tree signatures and expected-shape
primitive facts, including Path backing state. Shallow session copies and
subclasses cannot enter, borrow, validate, close the original transaction, or
retain historical binding after the original context expires. Bounded historical
`_assert_binding(receipt)` first compares those facts before traversing altered
objects. Exact source/package byte identity, Tree/Node tuple identity,
PackageSource, certificate, config, catalog, report, owner and seal are checked.
Copied, forged, foreign, expired, deleted-field, nested-mutation and reflective
growth attempts refuse. The context cannot be reused.

Public `assert_current(receipt)` always adds the original transaction baseline
revalidation, even if a later private bridge successfully applied a repair. It
never changes meaning to a postimage check. `_borrow_transaction(receipt)` first
runs that full original guard and returns the same locked transaction privately;
there is no public transaction property, injection or ownership transfer. The
bridge alone may use the historical binding guard when composing its own genuine
committed receipt, with the transaction's separate committed-postimage guard.
No recovery format or evidence-write path is added.

## Focused tests

The input tests copy only the genuine repository manifest and entrypoint into a
private temporary package layout and use the real pinned parser. They cover
exact retained bytes/object identities, genuine initializer invocation, all four
read-only evidence records, strict config and package pins, no ambient loader or
`Path.read_bytes`, absence/encoding/race categories, source/evidence safety,
byte/inode/mode/xattr and intermediate-ancestor drift, file/directory/storage
aliases, immutable maps, original-only receipts, malformed/nested mutations,
selection/limit changes, copied/subclassed sessions and context lifetime. Capture tests perform no repair,
compilation, native rendering or source writes by the session under test.
