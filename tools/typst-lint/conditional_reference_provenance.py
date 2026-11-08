"""Conditional project-ref facade provenance from explicit package snapshots.

This traces an exported name, not a call, and never creates a static-resolver
binding, evaluates a module, reads a catalog or grants source-edit authority.
The reviewed project-references package may be in a separately supplied root.
"""
from dataclasses import dataclass, field, replace
import os

import conditional_highlight as conditional
import import_graph as graph
from package_loader import PackageInputError, _absolute_root, load_packages
from static_resolver import MATH_BLOCKS, MATH_BLOCKS_SOURCE_SHA256
from static_resolver import PROJECT_REFERENCES, PROJECT_REFERENCES_SOURCE_SHA256
from typst_lint import digest

NAME = 'project-ref'
CONDITIONS = (
    'The runtime selects the exact captured packages and source files.',
    'Live paths may have changed since capture; the snapshot is not a filesystem lease.',
    'All referenced imports load and initialize successfully.',
    'This export is used at the intended call site without later shadowing.',
)


@dataclass(frozen=True)
class ReferenceExport:
    path: str
    name: str
    status: str
    reason: str | None = None
    route: tuple = ()
    source_hashes: tuple = ()
    package_evidence: tuple = ()
    package_requests: tuple = ()
    conditions: tuple[str, ...] = CONDITIONS
    edit_permission: bool = field(default=False, init=False)


class _ReferenceNamespace(conditional._Analyzer):
    def __init__(self, context, namespaces, reference_package):
        super().__init__(context, namespaces, (conditional.RULE_ID,), False)
        self.reference_package = reference_package
        self.reference_evidence = reference_package.evidence[0]

    def _selection(self, tree, index):
        shape = graph.import_shape(tree, index)
        combined = conditional._combined_import(tree, index) if shape is None else None
        if combined is not None:
            return combined[0], combined[1], True
        return shape, None, False

    def incoming(self, tree, index, path, active):
        if self.exhausted:
            return conditional._Module(reason=self.exhausted)
        shape, alias, combined = self._selection(tree, index)
        request = shape.request if shape is not None else None
        if request == PROJECT_REFERENCES:
            items = ((NAME, NAME),) if shape.items is None else shape.items
            if len(items) + int(combined) > conditional.MAX_BINDINGS:
                return self.exhaust('Conditional namespace binding limit exceeded')
            if any(original != NAME for original, _ in items):
                return conditional._Module(reason='Requested project-reference export unavailable', intrinsic_refusal=True)
            exports = [(alias, None)] if combined else []
            for original, local in items:
                value = conditional._Value((conditional.NameRoute(PROJECT_REFERENCES,
                    PROJECT_REFERENCES_SOURCE_SHA256, 'package-export', PROJECT_REFERENCES, NAME, NAME),), NAME)
                value = self.route(value, conditional.NameRoute(path, digest(tree.source),
                    ('combined-' if combined else '') + ('wildcard-import' if shape.items is None else 'named-import'),
                    request, original, local))
                if self.exhausted:
                    return conditional._Module(reason=self.exhausted)
                exports.append((local, value))
            if not self.bindings(exports):
                return conditional._Module(reason=self.exhausted)
            return conditional._Module(tuple(exports), packages=(self.reference_evidence,), requests=(request,))

        result = super().incoming(tree, index, path, active)
        if result.reason or request != MATH_BLOCKS or shape is None:
            return result
        package = self.packages.get(MATH_BLOCKS)
        if (package is None or package.evidence.source_sha256 != MATH_BLOCKS_SOURCE_SHA256
                or NAME not in package.exports):
            return result
        items = ((NAME, NAME),) if shape.items is None else shape.items
        matches = [(original, local) for original, local in items if original == NAME]
        if not matches:
            return result
        exports = dict(result.exports)
        for original, local in matches:
            # The pinned math-blocks bytes explicitly import this exact named
            # export from project-references and do not redefine it.
            value = conditional._Value((conditional.NameRoute(PROJECT_REFERENCES,
                PROJECT_REFERENCES_SOURCE_SHA256, 'package-export', PROJECT_REFERENCES, NAME, NAME),
                conditional.NameRoute(MATH_BLOCKS, MATH_BLOCKS_SOURCE_SHA256,
                    'named-import', PROJECT_REFERENCES, NAME, NAME)), NAME)
            value = self.route(value, conditional.NameRoute(path, digest(tree.source),
                ('combined-' if combined else '') + ('wildcard-import' if shape.items is None else 'named-import'),
                request, original, local))
            if self.exhausted:
                return conditional._Module(reason=self.exhausted)
            exports[local] = value
        packages = {evidence.import_path: evidence for evidence in result.packages}
        packages[PROJECT_REFERENCES] = self.reference_evidence
        return replace(result, exports=tuple(sorted(exports.items())),
                       packages=tuple(packages[key] for key in sorted(packages)),
                       requests=tuple(sorted(set(result.requests) | {PROJECT_REFERENCES})))


class ConditionalReferenceProvenance:
    """Trace final facade exports from one graph and two explicit package roots."""

    def __init__(self, import_graph, namespace_capture, reference_package_root):
        raw = os.fspath(reference_package_root)
        root = _absolute_root(raw)
        if type(raw) is not str or raw != str(root):
            raise PackageInputError('Reference package root must be canonical and absolute')
        captured = load_packages(root, (PROJECT_REFERENCES,))
        if (len(captured.sources) != 1 or len(captured.evidence) != 1 or captured.unavailable
                or captured.sources[0].import_path != PROJECT_REFERENCES
                or digest(captured.sources[0].source) != PROJECT_REFERENCES_SOURCE_SHA256
                or captured.evidence[0].source_sha256 != PROJECT_REFERENCES_SOURCE_SHA256):
            raise PackageInputError('Exact reviewed project-references package is unavailable')
        self._reference_capture = captured
        self._engine = _ReferenceNamespace(import_graph, namespace_capture, captured)

    @property
    def package_path(self):
        """The explicit root used to capture project-references, even on a refusal."""
        return self._reference_capture.package_path

    @property
    def package_evidence(self):
        """Immutable manifest/source evidence, not a live package selection proof."""
        return self._reference_capture.evidence

    def export(self, path, name=NAME):
        engine = self._engine
        if type(path) is not str or type(name) is not str or not name:
            return ReferenceExport(str(path), str(name), 'UNAVAILABLE', 'Invalid source path or export name')
        if engine.reason or engine.exhausted:
            return ReferenceExport(path, name, 'UNAVAILABLE', engine.reason or engine.exhausted)
        module = engine.module(path, ())
        if module.reason:
            return ReferenceExport(path, name, 'UNAVAILABLE', module.reason)
        value = dict(module.exports).get(name)
        if value is None or value.canonical_name != NAME:
            return ReferenceExport(path, name, 'UNAVAILABLE', 'Export identity is absent, shadowed or opaque')
        return ReferenceExport(path, name, 'CONDITIONAL', route=value.route,
            source_hashes=module.sources, package_evidence=module.packages,
            package_requests=module.requests)
