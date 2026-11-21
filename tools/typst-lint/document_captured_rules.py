"""Captured-snapshot inspection and independent candidate previews for 9 rules.

Operates strictly over already-captured document snapshots, immutable relative
import graphs, and captured reviewed package sources without rereading the
filesystem. Reuses existing single-source rule analyzers directly.

Diagnostic reporting strips all edits (edits=()). Optional candidate previews
for the 4 proposal-capable rules independently verify each source/rule via
fresh parse, in-memory context rebase, reanalysis, directive preservation, and
determinism checks. Neither mode grants edit or compiler authority.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import asdict, dataclass, replace
import os
from pathlib import Path

import anchor_overlay_rules
from book_provenance import EntrypointSnapshot
import book_rules
from candidate_convergence import AttributedEdit, Contribution, Step
from edit_plan import Edit, plan
import flowbox_rules
from import_graph import ImportEdge, ImportGraph, ImportSource, ImportUnavailable, import_request, normalize_relative, rebase_graph
import import_rules
import include_context
import marker_bounds_rules
from package_artifacts import PackageArtifact, SUPPLEMENTAL_ARTIFACTS
from package_loader import (PackageEvidence, PackageInputError, PackageSnapshot,
                            PackageUnavailable, build_reviewed_package_snapshot,
                            read_reviewed_package_from_snapshot,
                            resolve_absolute_root, validate_reviewed_manifest)
import scoped_rules
import source_analysis
from static_resolver import (MATH_BLOCKS, MATH_BOOK, PackageSource,
                             REVIEWED_NAMESPACE_DEPENDENCIES,
                             REVIEWED_NAMESPACE_PACKAGES, REVIEWED_PACKAGES,
                             imported_paths, resolve as static_resolve)
from syntax_client import AdapterError, Tree, parse
import theme_rules
from typst_lint import Diagnostic, InputError, SafetyError, digest, load_rules, require

CORE_RULE_IDS = (
    anchor_overlay_rules.ANCHOR,
    scoped_rules.REFERENCE,
    anchor_overlay_rules.LOCAL,
    marker_bounds_rules.BOUNDS,
    book_rules.SINGLE,
    book_rules.RESET,
    theme_rules.NO_SET,
)
PDE_ONLY_RULE_IDS = (
    import_rules.REDUNDANT,
    flowbox_rules.RULE_ID,
)
ALL_RULE_IDS = (*CORE_RULE_IDS, *PDE_ONLY_RULE_IDS)

PROPOSAL_CORE_RULE_IDS = (
    anchor_overlay_rules.ANCHOR,
    scoped_rules.REFERENCE,
)
PROPOSAL_PDE_RULE_IDS = (
    import_rules.REDUNDANT,
    flowbox_rules.RULE_ID,
)
PROPOSAL_RULE_IDS = (*PROPOSAL_CORE_RULE_IDS, *PROPOSAL_PDE_RULE_IDS)

VERIFICATION_LABEL = 'conditional-parse-syntax-directives-and-reanalysis'

INSPECT_CONDITIONS = (
    'Analyzes captured document snapshots only; no filesystem rereads, execution, or runtime binding proof.',
    'Book ownership rules apply only to the explicit configured entrypoint snapshot; included files remain unproved as entrypoints.',
    'Diagnostic reporting strips all edits and grants no source edit or compiler authority.',
)
PREVIEW_CONDITIONS = (
    'Independent per-source, per-rule source-only candidate previews; not composed across rules or sources.',
    'Verified by fresh parse, in-memory context rebase, reanalysis, directive preservation, and determinism checks.',
    'Runtime package selection, execution, and rendering equivalence are not proved; no edit or compiler authority.',
)

_PACKAGE_DEPENDENCIES = {
    **REVIEWED_NAMESPACE_DEPENDENCIES,
    MATH_BOOK: (MATH_BLOCKS,),
}
_CONTRACTS_BY_PATH = {
    contract.import_path: contract
    for contract in (*REVIEWED_PACKAGES, *REVIEWED_NAMESPACE_PACKAGES)
}


@dataclass(frozen=True)
class CapturedRuleResult:
    diagnostics: tuple[Diagnostic, ...] = ()
    edits: tuple[Edit, ...] = ()
    skips: tuple[str, ...] = ()
    parse_errors: tuple[tuple[int, int], ...] = ()
    suppressed: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class CapturedPackageBundle:
    roots: tuple[str, ...] = ()
    sources: tuple[PackageSource, ...] = ()
    evidence: tuple[PackageEvidence, ...] = ()
    unavailable: tuple[PackageUnavailable, ...] = ()


@dataclass(frozen=True)
class CandidateVerification:
    path: str
    rule_id: str
    baseline_sha256: str
    candidate_sha256: str | None
    verification: str
    edits: tuple[Edit, ...] = ()
    candidate_bytes: bytes | None = None
    diagnostics_before: int = 0
    diagnostics_after: int | None = None
    suppressed_count: int = 0
    reason: str | None = None
    reasons: tuple[str, ...] = ()

    def to_report_dict(self) -> dict:
        row = dict(
            path=self.path,
            rule_id=self.rule_id,
            baseline_sha256=self.baseline_sha256,
            candidate_sha256=self.candidate_sha256,
            verification=self.verification,
            diagnostics_before=self.diagnostics_before,
            diagnostics_after=self.diagnostics_after,
            suppressed_count=self.suppressed_count,
            runtime_binding_proved=False,
            execution_proved=False,
            render_equivalence_proved=False,
            edit_permission=False,
            compiler_permission=False,
            conditions=list(PREVIEW_CONDITIONS),
            edits=[
                dict(
                    start=edit.start,
                    end=edit.end,
                    replacement=edit.replacement.decode('utf-8'),
                    rule_id=self.rule_id,
                )
                for edit in self.edits
            ],
        )
        if self.reason is not None:
            row['reason'] = self.reason
        if self.reasons:
            row['reasons'] = list(self.reasons)
        return row


def rule_ids_for(rule_set: str) -> tuple[str, ...]:
    require(type(rule_set) is str and rule_set in ('core', 'pde'),
            'Captured rule inspection rule-set must be core or pde')
    return ALL_RULE_IDS if rule_set == 'pde' else CORE_RULE_IDS


def proposal_rule_ids_for(rule_set: str) -> tuple[str, ...]:
    require(type(rule_set) is str and rule_set in ('core', 'pde'),
            'Captured rule preview rule-set must be core or pde')
    return PROPOSAL_RULE_IDS if rule_set == 'pde' else PROPOSAL_CORE_RULE_IDS


def collect_import_paths(sources, import_graph=None) -> tuple[str, ...]:
    """Extract literal AST package import paths strictly from captured sources."""
    seen = []
    for item in sources:
        tree = getattr(item, 'tree', None)
        if tree is None and hasattr(item, 'source'):
            try:
                tree = parse(item.source)
            except AdapterError:
                continue
        if tree is None:
            continue
        for req in imported_paths(tree):
            if req.startswith('@') and req not in seen:
                seen.append(req)
    if type(import_graph) is ImportGraph:
        already = {getattr(item, 'path', None) for item in sources}
        for item in import_graph.sources:
            if item.path in already:
                continue
            try:
                tree = parse(item.source)
            except AdapterError:
                continue
            for req in imported_paths(tree):
                if req.startswith('@') and req not in seen:
                    seen.append(req)
    return tuple(sorted(seen))


def packages_from_namespace_capture(capture) -> tuple[PackageSource, ...]:
    """Convert already-captured namespace packages to PackageSource tuples without IO."""
    if capture is None:
        return ()
    if type(capture) is tuple:
        require(all(type(item) is PackageSource for item in capture),
                'Captured packages must be immutable PackageSource tuples')
        return capture
    if type(capture) is CapturedPackageBundle:
        return capture.sources
    from namespace_multiroot import MultiRootCapture, verify_multiroot_capture
    from namespace_packages import NamespacePackage, NamespacePackageCapture, verify_namespace_capture
    if type(capture) is NamespacePackageCapture:
        require(verify_namespace_capture(capture), 'Namespace capture failed verification')
        packages = capture.packages
        mannot_artifacts = ()
    elif type(capture) is MultiRootCapture:
        require(verify_multiroot_capture(capture), 'Multi-root namespace capture failed verification')
        packages = capture.packages
        mannot_artifacts = tuple(
            PackageArtifact(item.relative_path, item.source_identity, item.source)
            for item in capture.mannot_artifacts
        )
    else:
        raise InputError('Unsupported captured namespace package object')
    result = []
    for item in packages:
        if type(item) is not NamespacePackage:
            continue
        import_path = item.evidence.import_path
        contract = _CONTRACTS_BY_PATH.get(import_path)
        if contract is None or digest(item.source) != contract.source_sha256:
            continue
        artifacts = mannot_artifacts if import_path == '@preview/mannot:0.4.0' else ()
        result.append(PackageSource(import_path, item.evidence.source_identity, item.source, artifacts))
    return tuple(result)


def packages_from_resource_capture(snapshot) -> CapturedPackageBundle:
    """Build reviewed PackageSource inputs from a verified ResourceCapture without IO."""
    import document_resources
    document_resources.assert_capture(snapshot)
    root_map = dict(snapshot.roots)
    by_root_path = {(blob.request.root, blob.request.path): blob for blob in snapshot.files}
    ordered_roots = tuple(name for name in ('packages', 'cache') if name in root_map)
    sources, evidence, unavailable = [], [], []
    for contract in (*REVIEWED_PACKAGES, *REVIEWED_NAMESPACE_PACKAGES):
        prefix = f'{contract.namespace}/{contract.name}/{contract.version}'
        manifest_rel = f'{prefix}/typst.toml'
        entry_rel = f'{prefix}/{contract.entrypoint}'
        selected_root = None
        for root_id in ordered_roots:
            if (root_id, manifest_rel) in by_root_path or (root_id, entry_rel) in by_root_path:
                selected_root = root_id
                break
        if selected_root is None:
            continue
        manifest_blob = by_root_path.get((selected_root, manifest_rel))
        entry_blob = by_root_path.get((selected_root, entry_rel))
        if manifest_blob is None or entry_blob is None:
            unavailable.append(PackageUnavailable(
                contract.import_path, 'missing-package-artifact',
                'Captured resource snapshot lacks the required manifest or entrypoint'))
            continue
        manifest_identity = str(Path(root_map[selected_root]) / manifest_rel)
        manifest_validation = validate_reviewed_manifest(
            contract, manifest_blob.data, manifest_identity, allow_malformed=True)
        if not manifest_validation.ok:
            if manifest_validation.evidence is not None:
                evidence.append(manifest_validation.evidence)
            if manifest_validation.unavailable is not None:
                unavailable.append(manifest_validation.unavailable)
            continue
        source_identity = str(Path(root_map[selected_root]) / entry_rel)

        def _captured_artifacts(root_id=selected_root, pkg_prefix=prefix, import_path=contract.import_path):
            items = []
            for rel_art, _ in SUPPLEMENTAL_ARTIFACTS.get(import_path, ()):
                art_blob = by_root_path.get((root_id, f'{pkg_prefix}/{rel_art}'))
                if art_blob is not None:
                    art_identity = str(Path(root_map[root_id]) / f'{pkg_prefix}/{rel_art}')
                    items.append(PackageArtifact(rel_art, art_identity, art_blob.data))
            return tuple(items)

        outcome = build_reviewed_package_snapshot(
            contract, manifest_validation, entry_blob.data, source_identity,
            artifacts_loader=_captured_artifacts,
        )
        if outcome.evidence is not None:
            evidence.append(outcome.evidence)
        if outcome.unavailable is not None:
            unavailable.append(outcome.unavailable)
        if outcome.source is not None:
            sources.append(outcome.source)
    return CapturedPackageBundle(
        roots=tuple(root_map[name] for name in ordered_roots),
        sources=tuple(sources),
        evidence=tuple(evidence),
        unavailable=tuple(unavailable),
    )


def import_graph_from_resource_capture(snapshot, selected_paths, *, source_overrides=None) -> ImportGraph:
    """Build an immutable ImportGraph purely from a verified ResourceCapture without IO.

    Optional ``source_overrides`` replaces captured bytes for already-captured
    project ``.typ`` files in memory (accepting either ``{project_rel_path: bytes}``
    or a ``{('project', project_rel_path): bytes}`` candidate mapping).
    """
    import document_resources
    document_resources.assert_capture(snapshot)
    root_map = dict(snapshot.roots)
    project_root = root_map.get('project')
    require(type(project_root) is str and bool(project_root), 'Resource capture lacks project root')
    project_files = {
        blob.request.path: blob.data
        for blob in snapshot.files
        if blob.request.root == 'project' and blob.request.path.endswith('.typ')
    }
    if source_overrides is not None:
        require(isinstance(source_overrides, Mapping),
                'Import graph source overrides must be a mapping')
        for key, raw in source_overrides.items():
            if type(key) is tuple and len(key) == 2 and type(key[0]) is str and type(key[1]) is str:
                if key[0] != 'project' or not key[1].endswith('.typ'):
                    continue
                override_path = key[1]
            elif type(key) is str:
                override_path = key
            else:
                raise InputError('Invalid import graph source override key')
            require(override_path in project_files,
                    f'Import graph source override is absent from resource capture: {override_path}')
            require(type(raw) is bytes,
                    f'Import graph source override must be bytes: {override_path}')
            project_files[override_path] = raw
    selected = tuple(sorted(set(selected_paths)))
    require(all(path in project_files for path in selected),
            'Selected paths are missing from captured project resources')
    sources = {path: ImportSource(path, project_files[path]) for path in selected}
    pending = list(selected)
    scheduled = set(selected)
    edges, unavailable = [], []
    for path in pending:
        item = sources[path]
        try:
            tree = parse(item.source)
        except AdapterError as error:
            unavailable.append(ImportUnavailable(path, '', str(error)))
            continue
        if tree.nodes[0].errors:
            unavailable.append(ImportUnavailable(path, '', 'Malformed dependency syntax'))
            continue
        requests = tuple(dict.fromkeys(
            import_request(tree, i)
            for i, node in enumerate(tree.nodes)
            if node.kind == 'ModuleImport'
        ))
        for request in requests:
            if request is None:
                unavailable.append(ImportUnavailable(path, '', 'Dynamic or escaped import is unsupported'))
                continue
            if request.startswith('@'):
                unavailable.append(ImportUnavailable(
                    path, request, 'Package evaluation and package-relative dependency graphs are unsupported'))
                continue
            try:
                target = normalize_relative(path, request)
            except InputError as error:
                unavailable.append(ImportUnavailable(path, request, str(error)))
                continue
            if target not in project_files:
                unavailable.append(ImportUnavailable(path, request, 'Explicitly imported source is missing from resource capture'))
                continue
            if target not in sources:
                sources[target] = ImportSource(target, project_files[target])
            edges.append(ImportEdge(path, request, target))
            if target not in scheduled:
                scheduled.add(target)
                pending.append(target)
    return ImportGraph(
        project_root,
        tuple(sources[k] for k in sorted(sources)),
        selected,
        tuple(edges),
        tuple(unavailable),
    )


def capture_reviewed_packages(roots, import_paths) -> CapturedPackageBundle:
    """Safely capture reviewed packages across 1 or 2 explicit canonical roots.

    First root where a package's (namespace, name, version) directory exists
    wins. Missing or malformed-manifest packages become typed unavailable records
    rather than failing unrelated rules; unsafe paths, symlinks, hard links, and
    races fail closed.
    """
    raw_roots = tuple(roots)
    if not 1 <= len(raw_roots) <= 2:
        raise PackageInputError('Reviewed package capture requires one or two explicit roots')
    canonical_roots = []
    for raw in raw_roots:
        raw_str = os.fspath(raw)
        if type(raw_str) is not str or not raw_str:
            raise PackageInputError('Package root must be a nonempty canonical path')
        resolved = resolve_absolute_root(raw_str)
        if raw_str != str(resolved):
            raise PackageInputError('Package root must be a canonical absolute path')
        canonical_roots.append(resolved)
    if len(canonical_roots) == 2 and canonical_roots[0] == canonical_roots[1]:
        raise PackageInputError('Package and cache roots must be distinct')

    requested = tuple(sorted({p for p in import_paths if type(p) is str and p.startswith('@')}))
    pending = list(requested)
    scheduled = set(requested)
    sources, evidence, unavailable = [], [], []

    try:
        with ExitStack() as stack:
            snapshots = tuple(stack.enter_context(PackageSnapshot(root)) for root in canonical_roots)
            if len(snapshots) == 2:
                ids = [(os.fstat(s.directory_root.fd).st_dev, os.fstat(s.directory_root.fd).st_ino)
                       for s in snapshots]
                if ids[0] == ids[1]:
                    raise PackageInputError('Package and cache roots must not alias the same directory')

            for import_path in pending:
                contract = _CONTRACTS_BY_PATH.get(import_path)
                if contract is None:
                    unavailable.append(PackageUnavailable(
                        import_path, 'unreviewed-package',
                        'No reviewed static contract exists for this import; its files were not read'))
                    continue
                selected = None
                for snap in snapshots:
                    directory = snap.directory_root
                    for component in (contract.namespace, contract.name, contract.version):
                        if not snap.exists(directory, component):
                            break
                        directory = snap.directory(directory, component)
                    else:
                        selected = (snap, directory)
                        break
                if selected is None:
                    unavailable.append(PackageUnavailable(
                        import_path, 'missing-package-artifact',
                        'Reviewed package directory is absent from the explicit package roots'))
                    continue
                snap, pkg_dir = selected
                outcome = read_reviewed_package_from_snapshot(
                    snap, pkg_dir, contract,
                    optional=True,
                    missing_manifest=PackageUnavailable(
                        import_path, 'missing-package-artifact',
                        'Package manifest typst.toml is absent from the selected package directory'),
                    missing_entrypoint=PackageUnavailable(
                        import_path, 'missing-package-artifact',
                        'Package entrypoint is absent from the selected package directory'),
                    allow_malformed_manifest=True,
                )
                if outcome.evidence is not None:
                    evidence.append(outcome.evidence)
                if outcome.unavailable is not None:
                    unavailable.append(outcome.unavailable)
                if outcome.source is not None:
                    sources.append(outcome.source)
                    for dep in _PACKAGE_DEPENDENCIES.get(import_path, ()):
                        if dep not in scheduled:
                            scheduled.add(dep)
                            pending.append(dep)
            for snap in snapshots:
                snap.verify()
    except OSError as error:
        raise PackageInputError(f'Cannot safely read explicit package roots: {error}') from error
    return CapturedPackageBundle(
        roots=tuple(str(r) for r in canonical_roots),
        sources=tuple(sources),
        evidence=tuple(evidence),
        unavailable=tuple(unavailable),
    )


def normalize_rule_result(value, *, strip_edits: bool = False) -> CapturedRuleResult:
    """Normalize a single-rule analysis result into an immutable CapturedRuleResult."""
    return CapturedRuleResult(
        diagnostics=tuple(value.diagnostics),
        edits=() if strip_edits else tuple(value.edits),
        skips=tuple(value.skips),
        parse_errors=tuple(value.parse_errors),
        suppressed=tuple(value.suppressed),
    )


_normalize_result = normalize_rule_result


def refusal_reasons(rule_id: str, skips: tuple[str, ...]) -> tuple[str, ...]:
    """Select rule-relevant refusal or withholding reasons from skip tuples."""
    relevant = tuple(
        reason for reason in skips
        if ('refused' in reason or 'fix_unavailable' in reason or 'withheld' in reason
            or rule_id in reason)
    )
    return relevant if relevant else skips


_refusal_reasons = refusal_reasons


def analyze_source(tree: Tree, path: str, *, rule_set: str = 'core', packages=(),
                   import_graph=None, entrypoint=None, strip_edits: bool = True,
                   resolution=None) -> dict[str, CapturedRuleResult]:
    """Run the 7 (core) or 9 (pde) captured rules on one parsed source without IO."""
    active = rule_ids_for(rule_set)
    pkg_tuple = packages_from_namespace_capture(packages) if type(packages) is not tuple else packages
    require(all(type(p) is PackageSource for p in pkg_tuple),
            'Captured rule analysis requires immutable PackageSource inputs')
    if resolution is None:
        resolution = static_resolve(tree, packages=pkg_tuple, import_graph=import_graph, source_path=path)

    anchor_res = anchor_overlay_rules.analyze(tree, path, packages=pkg_tuple, import_graph=import_graph)
    scoped_res = scoped_rules.analyze(tree, path, packages=pkg_tuple, import_graph=import_graph)
    marker_res = marker_bounds_rules.analyze(tree, path, packages=pkg_tuple, import_graph=import_graph)
    book_res = book_rules.analyze(tree, path, resolution, entrypoint=entrypoint)
    theme_res = theme_rules.analyze(tree, path, resolution)

    raw = {
        anchor_overlay_rules.ANCHOR: anchor_res[anchor_overlay_rules.ANCHOR],
        scoped_rules.REFERENCE: scoped_res[scoped_rules.REFERENCE],
        anchor_overlay_rules.LOCAL: anchor_res[anchor_overlay_rules.LOCAL],
        marker_bounds_rules.BOUNDS: marker_res[marker_bounds_rules.BOUNDS],
        book_rules.SINGLE: book_res[book_rules.SINGLE],
        book_rules.RESET: book_res[book_rules.RESET],
        theme_rules.NO_SET: theme_res[theme_rules.NO_SET],
    }
    if rule_set == 'pde':
        import_res = import_rules.analyze(tree, path, graph=import_graph, _verify=not strip_edits)
        raw[import_rules.REDUNDANT] = import_res[import_rules.REDUNDANT]
        raw[flowbox_rules.RULE_ID] = flowbox_rules.analyze(tree, path, resolution)

    return {rid: normalize_rule_result(raw[rid], strip_edits=strip_edits) for rid in active}


def _reanalyze_single_rule(tree: Tree, path: str, rule_id: str, *,
                           packages: tuple[PackageSource, ...],
                           import_graph, entrypoint) -> CapturedRuleResult:
    if rule_id == anchor_overlay_rules.ANCHOR:
        res = anchor_overlay_rules.analyze(tree, path, packages=packages, import_graph=import_graph)[rule_id]
    elif rule_id == scoped_rules.REFERENCE:
        res = scoped_rules.analyze(tree, path, packages=packages, import_graph=import_graph)[rule_id]
    elif rule_id == import_rules.REDUNDANT:
        res = import_rules.analyze(tree, path, graph=import_graph, _verify=False)[rule_id]
    elif rule_id == flowbox_rules.RULE_ID:
        resolution = static_resolve(tree, packages=packages, import_graph=import_graph, source_path=path)
        res = flowbox_rules.analyze(tree, path, resolution)
    else:
        raise InputError(f'Rule is not proposal-capable: {rule_id}')
    return normalize_rule_result(res, strip_edits=False)


def verify_rule_candidate(tree: Tree, path: str, rule_id: str, baseline_result: CapturedRuleResult, *,
                          packages=(), import_graph=None, entrypoint=None,
                          catalog_rule_ids: set[str] | None = None) -> CandidateVerification | None:
    """Independently verify one proposal-capable rule's candidate for one source."""
    del catalog_rule_ids
    require(rule_id in PROPOSAL_RULE_IDS, f'Unsupported proposal rule: {rule_id}')
    pkg_tuple = packages_from_namespace_capture(packages) if type(packages) is not tuple else packages
    source = tree.source
    baseline_sha = digest(source)

    def withheld(reason: str, extra_reasons: tuple[str, ...] = ()) -> CandidateVerification:
        all_reasons = tuple(dict.fromkeys((reason, *extra_reasons)))
        return CandidateVerification(
            path=path,
            rule_id=rule_id,
            baseline_sha256=baseline_sha,
            candidate_sha256=None,
            verification='withheld',
            edits=(),
            candidate_bytes=None,
            diagnostics_before=len(baseline_result.diagnostics),
            diagnostics_after=None,
            suppressed_count=len(baseline_result.suppressed),
            reason=reason,
            reasons=all_reasons,
        )

    if baseline_result.parse_errors or tree.nodes[0].errors:
        return withheld('Malformed source syntax; candidate preview withheld', baseline_result.skips)

    if not baseline_result.edits:
        if baseline_result.diagnostics:
            refusals = refusal_reasons(rule_id, baseline_result.skips)
            primary = refusals[0] if refusals else 'Rule reported findings without eligible candidate edits'
            return withheld(primary, refusals)
        return None

    try:
        planned = plan(source, baseline_result.edits)
        if planned.deferred:
            raise SafetyError('Candidate edits overlap or conflict')
        if not planned.edits:
            raise SafetyError('Candidate plan produced no effective edits')
        candidate_bytes = planned.candidate()
        if candidate_bytes == source:
            raise SafetyError('Candidate bytes are identical to baseline')
        for edit in planned.edits:
            edit.replacement.decode('utf-8')
        candidate_tree = parse(candidate_bytes, tree.mode)
        if candidate_tree.nodes[0].errors:
            raise SafetyError('Candidate source failed fresh parse')

        candidate_graph = (rebase_graph(import_graph, path, source, candidate_bytes)
                           if import_graph is not None else None)
        if rule_id == import_rules.REDUNDANT:
            if (type(candidate_graph) is not ImportGraph
                    or candidate_graph.source_for(path) is None
                    or candidate_graph.source_for(path).source != candidate_bytes):
                raise SafetyError('Candidate lost immutable relative import graph context')

        candidate_entry = (
            replace(entrypoint, source=candidate_bytes)
            if (type(entrypoint) is EntrypointSnapshot and entrypoint.path == path
                and type(entrypoint.source) is bytes and entrypoint.source == source)
            else entrypoint
        )

        final = _reanalyze_single_rule(
            candidate_tree, path, rule_id,
            packages=pkg_tuple, import_graph=candidate_graph, entrypoint=candidate_entry,
        )
        if final.parse_errors:
            raise SafetyError('Candidate reanalysis reported parse errors')
        if final.edits:
            raise SafetyError('Candidate reanalysis still proposes edits (not idempotent)')
        if len(final.diagnostics) >= len(baseline_result.diagnostics):
            raise SafetyError('Candidate did not reduce baseline findings')

        step = Step(
            before=source,
            after=candidate_bytes,
            applied=tuple(
                AttributedEdit(edit, rule_id, (rule_id,), (Contribution(1, rule_id, edit),))
                for edit in planned.edits
            ),
            deferred=(),
            moves=(),
        )
        before_map = {rule_id: baseline_result}
        after_map = {rule_id: final}
        before_active = source_analysis._findings(source, before_map, frozenset(), step.map_offset)
        after_active = source_analysis._findings(candidate_bytes, after_map, frozenset())
        if after_active - before_active:
            raise SafetyError('Candidate introduces a new finding')

        source_analysis._validate_step(tree, before_map, candidate_tree, after_map, step)

        # Determinism check via second fresh parse
        second_tree = parse(candidate_bytes, tree.mode)
        second = _reanalyze_single_rule(
            second_tree, path, rule_id,
            packages=pkg_tuple, import_graph=candidate_graph, entrypoint=candidate_entry,
        )
        if (second.diagnostics != final.diagnostics or second.edits != final.edits
                or second.suppressed != final.suppressed or second.parse_errors != final.parse_errors
                or second.skips != final.skips):
            raise SafetyError('Candidate reanalysis is not deterministic')
    except (AdapterError, SafetyError, InputError, ValueError, UnicodeDecodeError) as error:
        return withheld(str(error), refusal_reasons(rule_id, baseline_result.skips))

    return CandidateVerification(
        path=path,
        rule_id=rule_id,
        baseline_sha256=baseline_sha,
        candidate_sha256=digest(candidate_bytes),
        verification=VERIFICATION_LABEL,
        edits=planned.edits,
        candidate_bytes=candidate_bytes,
        diagnostics_before=len(baseline_result.diagnostics),
        diagnostics_after=len(final.diagnostics),
        suppressed_count=len(final.suppressed),
    )


def preview_source_candidates(tree: Tree, path: str, *, rule_set: str = 'core', packages=(),
                              import_graph=None, entrypoint=None,
                              catalog_rule_ids: set[str] | None = None,
                              baseline_results: dict[str, CapturedRuleResult] | None = None
                              ) -> tuple[CandidateVerification, ...]:
    """Independently verify candidate previews for all proposal-capable rules on one source."""
    proposal_ids = proposal_rule_ids_for(rule_set)
    pkg_tuple = packages_from_namespace_capture(packages) if type(packages) is not tuple else packages
    if baseline_results is None:
        baseline_results = analyze_source(
            tree, path, rule_set=rule_set, packages=pkg_tuple,
            import_graph=import_graph, entrypoint=entrypoint, strip_edits=False,
        )
    previews = []
    for rid in proposal_ids:
        verified = verify_rule_candidate(
            tree, path, rid, baseline_results[rid],
            packages=pkg_tuple, import_graph=import_graph, entrypoint=entrypoint,
            catalog_rule_ids=catalog_rule_ids,
        )
        if verified is not None:
            previews.append(verified)
    return tuple(previews)


def _resolve_bundle(packages, package_bundle: CapturedPackageBundle | None) -> CapturedPackageBundle:
    if package_bundle is not None:
        require(type(package_bundle) is CapturedPackageBundle,
                'package_bundle must be a CapturedPackageBundle')
        return package_bundle
    pkg_tuple = packages_from_namespace_capture(packages)
    return CapturedPackageBundle(sources=pkg_tuple)


def inspect_captured_document(captured, *, entrypoint: str, rule_set: str = 'core',
                              packages=(), import_graph=None,
                              package_bundle: CapturedPackageBundle | None = None,
                              max_items: int | None = None) -> dict:
    """Inspect all 7 (core) or 9 (pde) captured rules over an IncludeContext without IO."""
    include_context.assert_include_context(captured)
    active_ids = rule_ids_for(rule_set)
    entry_item = captured.source_for(entrypoint)
    require(entry_item is not None, 'Configured entrypoint is absent from captured include context')
    entry_snapshot = EntrypointSnapshot(entry_item.path, entry_item.source)
    bundle = _resolve_bundle(packages, package_bundle)

    report_items = (
        1 + len(active_ids) + len(INSPECT_CONDITIONS)
        + len(bundle.roots) + len(bundle.evidence) + len(bundle.unavailable)
    )
    if max_items is not None and report_items > max_items:
        raise InputError('Document inspection report item limit exceeded')

    diagnostics_rows = []
    suppressed_rows = []
    coverage_rows = []
    diagnostics_by_rule = {rid: 0 for rid in active_ids}
    suppressed_by_rule = {rid: 0 for rid in active_ids}

    for item in captured.sources:
        results = analyze_source(
            item.tree, item.path, rule_set=rule_set, packages=bundle.sources,
            import_graph=import_graph, entrypoint=entry_snapshot, strip_edits=True,
        )
        for rid in active_ids:
            value = results[rid]
            report_items += (1 + len(value.diagnostics) + len(value.suppressed)
                             + len(value.skips) + len(value.parse_errors))
            if max_items is not None and report_items > max_items:
                raise InputError('Document inspection report item limit exceeded')
            diagnostics_by_rule[rid] += len(value.diagnostics)
            suppressed_by_rule[rid] += len(value.suppressed)
            diagnostics_rows.extend(asdict(f) for f in value.diagnostics)
            suppressed_rows.extend(asdict(f) for f in value.suppressed)
            coverage_rows.append(dict(
                path=item.path,
                source_sha256=item.identity.source_sha256,
                rule_id=rid,
                status='conditional',
                diagnostics=len(value.diagnostics),
                suppressed=len(value.suppressed),
                reasons=list(value.skips),
                parse_errors=[list(span) for span in value.parse_errors],
                edit_permission=False,
                compiler_permission=False,
            ))

    diagnostics_rows.sort(key=lambda row: (row['path'], row['line'], row['column'], row['rule_id']))
    suppressed_rows.sort(key=lambda row: (row['path'], row['line'], row['column'], row['rule_id']))
    return dict(
        rule_set=rule_set,
        rule_ids=list(active_ids),
        status='CONDITIONAL',
        entrypoint=entry_item.path,
        entrypoint_sha256=entry_item.identity.source_sha256,
        package_roots=list(bundle.roots),
        package_evidence=[asdict(e) for e in bundle.evidence],
        package_unavailable=[asdict(u) for u in bundle.unavailable],
        import_graph_captured=type(import_graph) is ImportGraph,
        diagnostics=diagnostics_rows,
        suppressed=suppressed_rows,
        suppressed_count=len(suppressed_rows),
        diagnostics_by_rule=diagnostics_by_rule,
        suppressed_by_rule=suppressed_by_rule,
        coverage=coverage_rows,
        conditions=list(INSPECT_CONDITIONS),
        runtime_binding_proved=False,
        execution_proved=False,
        coverage_complete=False,
        edit_permission=False,
        compiler_permission=False,
        report_items=report_items,
    )


def preview_captured_document(captured, *, entrypoint: str, rule_set: str = 'core',
                              packages=(), import_graph=None,
                              package_bundle: CapturedPackageBundle | None = None,
                              catalog: list | tuple | None = None,
                              max_items: int | None = None) -> dict:
    """Build independent verified candidate previews for proposal-capable rules without IO."""
    include_context.assert_include_context(captured)
    proposal_ids = proposal_rule_ids_for(rule_set)
    entry_item = captured.source_for(entrypoint)
    require(entry_item is not None, 'Configured entrypoint is absent from captured include context')
    entry_snapshot = EntrypointSnapshot(entry_item.path, entry_item.source)
    bundle = _resolve_bundle(packages, package_bundle)
    catalog_rule_ids = (
        {r['id'] for r in catalog} if catalog is not None
        else {r['id'] for r in load_rules()[0]['rules']}
    )

    report_items = (
        1 + len(proposal_ids) + len(PREVIEW_CONDITIONS)
        + len(bundle.roots) + len(bundle.evidence) + len(bundle.unavailable)
    )
    if max_items is not None and report_items > max_items:
        raise InputError('Document inspection report item limit exceeded')

    previews_rows = []
    withheld_rows = []
    by_file_rows = []
    candidate_by_rule = {rid: 0 for rid in proposal_ids}
    withheld_by_rule = {rid: 0 for rid in proposal_ids}
    planned_edits = 0

    for item in captured.sources:
        verified_items = preview_source_candidates(
            item.tree, item.path, rule_set=rule_set, packages=bundle.sources,
            import_graph=import_graph, entrypoint=entry_snapshot,
            catalog_rule_ids=catalog_rule_ids,
        )
        for record in verified_items:
            report_items += (1 + len(record.edits) + len(record.reasons)
                             + len(PREVIEW_CONDITIONS) + int(record.reason is not None))
            if max_items is not None and report_items > max_items:
                raise InputError('Document inspection report item limit exceeded')
            serialized = record.to_report_dict()
            by_file_rows.append(serialized)
            if record.verification == 'withheld':
                withheld_rows.append(serialized)
                withheld_by_rule[record.rule_id] += 1
            else:
                previews_rows.append(serialized)
                candidate_by_rule[record.rule_id] += 1
                planned_edits += len(record.edits)

    return dict(
        rule_set=rule_set,
        rule_ids=list(proposal_ids),
        status='CONDITIONAL',
        entrypoint=entry_item.path,
        entrypoint_sha256=entry_item.identity.source_sha256,
        package_roots=list(bundle.roots),
        package_evidence=[asdict(e) for e in bundle.evidence],
        package_unavailable=[asdict(u) for u in bundle.unavailable],
        import_graph_captured=type(import_graph) is ImportGraph,
        candidate_count=len(previews_rows),
        withheld_count=len(withheld_rows),
        planned_edits=planned_edits,
        candidate_count_by_rule=candidate_by_rule,
        withheld_count_by_rule=withheld_by_rule,
        previews=previews_rows,
        withheld=withheld_rows,
        by_file=by_file_rows,
        conditions=list(PREVIEW_CONDITIONS),
        runtime_binding_proved=False,
        execution_proved=False,
        render_equivalence_proved=False,
        edit_permission=False,
        compiler_permission=False,
        report_items=report_items,
    )
