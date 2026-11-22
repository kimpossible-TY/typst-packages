"""Bind modern highlight diagnostics to an issued, unchanged resource capture.

This is evidence only: it does not authorize edits, rendering or publication.
Baseline analysis requires unchanged resources; candidate analysis permits only
internally rederived highlight candidates and unchanged dependent routes.
"""
from dataclasses import asdict
from types import MappingProxyType
import json
import weakref

import conditional_modern_highlight as modern
import document_modern_highlight_preview as preview
import document_footnote_evidence as footnote
import document_resources
from edit_plan import Edit, plan
from typst_lint import InputError, SafetyError, digest

_ISSUED = {}


def _valid(proof):
    issued = _ISSUED.get(id(proof))
    if (type(proof) is not CapturedModernHighlightEvidence or issued is None
            or issued[0]() is not proof or issued[1] is not proof._analyzer
            or issued[2] is not proof._snapshot):
        raise SafetyError('Modern highlight resource evidence is unissued or changed')
    proof._analyzer._check()
    session = proof._analyzer._session
    resources = footnote._verify_base(session, proof._snapshot)
    # The issued modern analyzer separately verifies the exact modern artifact;
    # this binds those same bytes and first-root selection to the snapshot.
    footnote._verify_package(session, resources, proof._analyzer._variant.evidence)
    document_resources.stage_original(session._context, proof._snapshot)
    return resources


def _verify_final(resources, final_resources):
    if type(final_resources) not in (dict, MappingProxyType):
        raise InputError('An explicit final resource mapping is required')
    if (len(final_resources) != len(resources) or final_resources.keys() != resources.keys()
            or any(type(final_resources[key]) is not bytes or final_resources[key] != value
                   for key, value in resources.items())):
        raise SafetyError('Final resources differ from the captured baseline')


def _verify_routes(analyzer, resources, report):
    session = analyzer._session
    packages = {item.evidence.import_path: item for item in session._namespaces.packages}
    checked = set()
    for row in report['by_file']:
        for finding in (*row['diagnostics'], *row['suppressed']):
            for item in finding['source_hashes']:
                data = resources.get(('project', item['path']))
                if data is None or digest(data) != item['source_sha256']:
                    raise SafetyError('Highlight route source differs from the captured resource')
            for item in finding['package_evidence']:
                package = packages.get(item['import_path'])
                if package is None or asdict(package.evidence) != item:
                    raise SafetyError('Highlight route package is not the selected capture')
                footnote._verify_package(session, resources, package.evidence, checked)
            for route in (finding['route'], *finding['content_context']):
                for step in route:
                    path, sha = step['path'], step['source_sha256']
                    if path.startswith('@'):
                        package = packages.get(path)
                        if package is None or package.evidence.source_sha256 != sha:
                            raise SafetyError('Highlight route package identity changed')
                        footnote._verify_package(session, resources, package.evidence, checked)
                    else:
                        data = resources.get(('project', path))
                        if data is None or digest(data) != sha:
                            raise SafetyError('Highlight route source identity changed')


class CapturedModernHighlightEvidence:
    """Exact modern analyzer and complete resource snapshot, never write authority."""
    def __init__(self, analyzer, snapshot):
        if type(analyzer) is not modern.ModernHighlightAnalyzer:
            raise InputError('An issued modern highlight analyzer is required')
        analyzer._check()
        footnote._verify_base(analyzer._session, snapshot)
        self._analyzer, self._snapshot = analyzer, snapshot
        identity = id(self)
        def release(reference):
            item = _ISSUED.get(identity)
            if item is not None and item[0] is reference:
                _ISSUED.pop(identity, None)
        _ISSUED[identity] = (weakref.ref(self, release), analyzer, snapshot)
        try:
            _valid(self)
        except Exception:
            _ISSUED.pop(identity, None)
            raise

    def analyze(self, final_resources, *, max_items=modern.MAX_BATCH_ITEMS, max_bytes=modern.MAX_BATCH_BYTES):
        resources = _valid(self)
        _verify_final(resources, final_resources)
        report = self._analyzer.analyze_all(max_items=max_items, max_bytes=max_bytes)
        _verify_routes(self._analyzer, resources, report)
        _verify_final(_valid(self), final_resources)
        # Do not convert conditional source routes into runtime or edit proof.
        report['resource_capture_bound'] = True
        report['report_items'] += 1
        encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
        if (report['report_items'] > max_items
                or len(encoded.encode('utf-8')) + 2 * encoded.count('\n') > max_bytes):
            raise InputError('Modern highlight resource evidence budget exceeded')
        return report

    def analyze_candidates(self, final_resources, *, max_items=modern.MAX_BATCH_ITEMS, max_bytes=modern.MAX_BATCH_BYTES):
        """Validate exact internally rederived candidates against a supplied final map.

        The supplied map is not a staged-workspace identity, and this method
        does not grant compiler or edit authority. Unselected files must remain
        byte-identical to the capture; selected files must match entire previews.
        """
        resources = _valid(self)
        if type(final_resources) not in (dict, MappingProxyType):
            raise InputError('An explicit final resource mapping is required')
        if (len(final_resources) != len(resources) or final_resources.keys() != resources.keys()
                or any(type(final_resources[key]) is not bytes for key in resources)):
            raise SafetyError('Final resource membership differs from the capture')
        diagnostics, proposals = preview.preview_all(self._analyzer, max_items=max_items,
                                                     max_bytes=max_bytes, return_evidence=True)
        _verify_routes(self._analyzer, resources, diagnostics)
        findings = {row['path']: {(d['start'] - 1, d['end']): d for d in row['diagnostics']}
                    for row in diagnostics['by_file']}
        expected = dict(resources)
        changed, selected = {}, []
        for row in proposals['by_file']:
            key = ('project', row['path'])
            if key not in resources:
                raise SafetyError('Highlight candidate source is absent from the capture')
            if final_resources[key] == resources[key]:
                continue
            if row['verification'] == 'withheld':
                raise SafetyError('A withheld highlight source changed')
            edits = [Edit(item['start'], item['end'], item['replacement'].encode('utf-8'))
                     for item in row['edits']]
            candidate = plan(resources[key], edits).candidate()
            if (digest(resources[key]) != row['baseline_sha256'] or digest(candidate) != row['candidate_sha256']
                    or final_resources[key] != candidate):
                raise SafetyError('Final highlight candidate differs from its rederived preview')
            for edit in edits:
                finding = findings.get(row['path'], {}).get((edit.start, edit.end))
                if finding is None:
                    raise SafetyError('Highlight edit has no captured diagnostic route')
                selected.append((row['path'], finding))
            expected[key] = candidate
            changed[row['path']] = len(edits)
        _verify_final(expected, final_resources)
        for path, finding in selected:
            dependencies = {item['path'] for item in finding['source_hashes']}
            for route in (finding['route'], *finding['content_context']):
                dependencies.update(step['path'] for step in route if not step['path'].startswith('@'))
            if (dependencies - {path}) & changed.keys():
                raise SafetyError('A different highlight route source changed in the final candidate')
        _verify_final(expected, final_resources)
        _valid(self)
        _verify_final(expected, final_resources)
        unpreviewed = proposals['diagnostics_count'] - len(selected)
        incomplete = unpreviewed or proposals['unresolved_reasons_count'] or proposals['withheld_source_count']
        result = dict(status='INCOMPLETE' if incomplete else 'CONDITIONAL',
                      source_count=len(changed), candidate_count=len(selected),
                      sources=[dict(path=path, edits=changed[path]) for path in sorted(changed)],
                      unpreviewed_diagnostics=unpreviewed,
                      unresolved_reasons_count=proposals['unresolved_reasons_count'],
                      withheld_source_count=proposals['withheld_source_count'],
                      resource_capture_bound=True, staged_workspace_proved=False,
                      runtime_binding_proved=False, execution_proved=False,
                      coverage_complete=False, render_equivalence_proved=False, edit_permission=False,
                      compiler_permission=False)
        used = diagnostics['report_items'] + proposals['report_items'] + len(result) + 3 * len(changed)
        # preview_all already bounds both internal sections together; include
        # this returned section in the same aggregate byte/item budget.
        sections = (diagnostics, proposals, result)
        size = sum(len(raw.encode('utf-8')) + 2 * raw.count('\n')
                   for raw in (json.dumps(x, ensure_ascii=False, sort_keys=True, indent=2) for x in sections))
        if used > max_items or size > max_bytes:
            raise InputError('Modern highlight candidate evidence budget exceeded')
        return result


def stage_candidates(proof):
    """Derive and validate a private candidate resource map from issued bytes.

    The returned mapping has no edit, compiler or publication authority; a
    compiler must use these exact bytes and perform its own staging checks.
    """
    resources = _valid(proof)
    result = preview.preview_all(proof._analyzer)
    staged = dict(resources)
    for row in result['by_file']:
        if row['verification'] == 'withheld':
            continue
        key = ('project', row['path'])
        if key not in resources:
            raise SafetyError('Highlight candidate source is absent from the capture')
        edits = [Edit(item['start'], item['end'], item['replacement'].encode('utf-8'))
                 for item in row['edits']]
        staged[key] = plan(resources[key], edits).candidate()
    staged = MappingProxyType(staged)
    evidence = proof.analyze_candidates(staged)
    _valid(proof)
    return staged, evidence
