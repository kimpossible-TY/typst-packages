"""Bind conditional footnote evidence to a private preview resource capture.

This module neither applies edits nor authorizes compiling or publishing. Each
candidate is parsed afresh; any facade later changed by another source edit
must be rejected when the complete candidate map becomes available.
"""
from dataclasses import dataclass, field
from pathlib import Path
import weakref

import conditional_footnote_binding as bindings
import document_resources
import facade_provenance
import include_context
import namespace_packages
import unicodedata
from syntax_client import parse
from typst_lint import InputError, SafetyError, digest


@dataclass(frozen=True)
class CandidateCalls:
    path: str
    source_sha256: str
    calls: tuple
    unresolved: tuple
    runtime_binding_proved: bool = field(default=False, init=False)
    edit_permission: bool = field(default=False, init=False)
    compiler_permission: bool = field(default=False, init=False)


_ISSUED = {}


def _resources(snapshot):
    document_resources.assert_capture(snapshot)
    return {(item.request.root, item.request.path): item.data for item in snapshot.files}


def _verify_base(session, snapshot):
    if not bindings._valid(session):
        raise SafetyError('Conditional footnote capture is invalid or changed')
    resources = _resources(snapshot)
    roots = dict(snapshot.roots)
    if (roots.get('project') != session._context.project_root
            or (roots.get('packages'), roots.get('cache')) != session._namespaces.roots):
        raise SafetyError('Preview roots differ from the conditional evidence roots')
    for item in session._graph.sources:
        if resources.get(('project', item.path)) != item.source:
            raise SafetyError('Captured import graph differs from preview resources')
    return resources


def _verify_package(session, resources, evidence, checked=None):
    if checked is None:
        checked = set()
    if evidence.import_path in checked:
        return
    checked.add(evidence.import_path)
    selections = {row.import_path: row.root_index for row in session._namespaces.selections}
    item = next((row for row in session._namespaces.packages
                 if row.evidence.import_path == evidence.import_path), None)
    index = selections.get(evidence.import_path)
    if item is None or item.evidence != evidence or index not in (0, 1):
        raise SafetyError('Package is not the selected captured namespace')
    root = Path(session._namespaces.roots[index])
    root_id = ('packages', 'cache')[index]
    try:
        manifest = Path(evidence.manifest_identity).relative_to(root).as_posix()
        source = Path(evidence.source_identity).relative_to(root).as_posix()
    except (TypeError, ValueError) as error:
        raise SafetyError('Package evidence is outside its selected root') from error
    if (resources.get((root_id, manifest)) != item.manifest
            or resources.get((root_id, source)) != item.source):
        raise SafetyError('Selected package source or manifest differs from preview resources')
    version_path = unicodedata.normalize('NFC', f'{evidence.namespace}/{evidence.name}/{evidence.version}').casefold()
    def interferes(key):
        if key[0] != 'packages':
            return False
        path = unicodedata.normalize('NFC', key[1]).casefold()
        return (path == version_path or path.startswith(version_path + '/')
                or version_path.startswith(path + '/'))
    if index == 1 and any(interferes(key) for key in resources):
        raise SafetyError('A staged higher-priority package would shadow the selected cache package')
    contract = next((row for row in namespace_packages._CONTRACTS
                     if row.import_path == evidence.import_path), None)
    if contract is None:
        raise SafetyError('Captured package has no reviewed namespace dependency contract')
    for dependency in contract.dependencies:
        nested = next((row for row in session._namespaces.packages
                       if row.evidence.import_path == dependency), None)
        if nested is None:
            raise SafetyError('A transitive namespace dependency is unavailable')
        _verify_package(session, resources, nested.evidence, checked)


def _valid(proof):
    item = _ISSUED.get(id(proof))
    if (type(proof) is not PreviewFootnoteEvidence or item is None or item[0]() is not proof
            or item[1] is not proof._session or item[2] is not proof._snapshot):
        raise SafetyError('Preview footnote evidence is unissued or changed')
    return _verify_base(proof._session, proof._snapshot)


class PreviewFootnoteEvidence:
    """An issued binding of both source and package captures to a snapshot."""
    def __init__(self, session, snapshot):
        _verify_base(session, snapshot)
        self._session, self._snapshot = session, snapshot
        identity = id(self)
        def release(reference):
            item = _ISSUED.get(identity)
            if item is not None and item[0] is reference:
                _ISSUED.pop(identity, None)
        _ISSUED[identity] = (weakref.ref(self, release), session, snapshot)

    def candidate_calls(self, path, candidate):
        resources = _valid(self)
        if (type(path) is not str or path not in self._session._graph.selected
                or type(candidate) is not bytes):
            raise InputError('Candidate must be bytes for a selected source')
        tree = parse(candidate)
        if tree.nodes[0].errors or tree.mode != 'markup':
            raise SafetyError('Candidate syntax is invalid')
        engine = bindings._NamespaceAnalyzer(self._session._graph, self._session._namespaces, (), False)
        engine.reason = facade_provenance._graph_reason(self._session._graph)
        engine.packages = {item.evidence.import_path: item for item in self._session._namespaces.packages}
        engine.evidence = self._session._namespaces.evidence
        resolver = bindings._FootnoteResolver(tree, path, engine)
        resolver.visit(0, resolver.state)
        calls = []
        for index, evidence in sorted(resolver.call_evidence.items(), key=lambda pair: tree.nodes[pair[0]].start):
            node = tree.nodes[index]
            sources = {item.path: item for entries in evidence for item in entries[0]}
            packages = {item.import_path: item for entries in evidence for item in entries[1]}
            requests = {item for entries in evidence for item in entries[2]}
            for item in packages.values():
                _verify_package(self._session, resources, item)
            calls.append(bindings.FootnoteBinding(path, node.start - 1, node.end, digest(candidate),
                tuple(sources[key] for key in sorted(sources)),
                tuple(packages[key] for key in sorted(packages)), tuple(sorted(requests)),
                bindings.CALLBACK_CONDITIONS if index in resolver.closure_calls else bindings.CONDITIONS))
        result = CandidateCalls(path, digest(candidate), tuple(calls), tuple(dict.fromkeys(resolver.unresolved)))
        _valid(self)
        return result

    def validate_staged_facades(self, candidates, issued_calls, *, allow_deferred=False):
        """Recheck dependency bytes after all source candidates are known.

        Caller-supplied reports are never accepted as proof: recompute every
        referenced call against its final candidate before inspecting facades.
        """
        _valid(self)
        if type(candidates) is not dict or type(issued_calls) is not dict or type(allow_deferred) is not bool:
            raise InputError('Expected source candidate and call-offset mappings')
        for path, offsets in issued_calls.items():
            if (path not in candidates or type(offsets) is not tuple
                    or any(type(offset) is not int or offset < 0 for offset in offsets)):
                raise InputError('Invalid source call selection')
            result = PreviewFootnoteEvidence.candidate_calls(self, path, candidates[path])
            # When this optional extension is selected, independently check
            # every closure capture, including calls accepted by the older
            # resolver. A loop enclosing declaration can change its capture.
            by_start = {call.start: call for call in result.calls
                        if not (allow_deferred and call.conditions == bindings.CALLBACK_CONDITIONS)}
            missing = tuple(offset for offset in offsets if offset not in by_start)
            if missing and allow_deferred:
                import document_deferred_footnote_evidence
                deferred = document_deferred_footnote_evidence.analyze(self, path, missing, candidate=candidates[path])
                if deferred.source_sha256 != digest(candidates[path]) or deferred.unresolved:
                    raise SafetyError('Final candidate deferred footnote binding is not proven')
                by_start.update((call.start, call) for call in deferred.calls)
            for offset in offsets:
                call = by_start.get(offset)
                if call is None:
                    raise SafetyError('Final candidate no longer has the conditional footnote binding')
                for source in call.facade_sources:
                    original = self._session._graph.source_for(source.path)
                    if original is None or candidates.get(source.path, original.source) != original.source:
                        raise SafetyError('A captured facade changes in the final candidate')
        _valid(self)
