"""Optional, conditional source binding evidence for parent-preview markub calls.

The ordinary parent plan is issued first and retained if this extra proof is
withheld. Neither runtime package selection nor effects or rendering are proved.
"""
from dataclasses import asdict, replace
import json

import conditional_footnote_binding as binding
import conditional_highlight as highlight
import conditional_modern_highlight as modern
import import_graph as graph
import namespace_multiroot as namespaces
import scoped_parent_transfer_prototype as parent
from document_gap_preview import _MappedEdits
from edit_plan import Edit, plan
from syntax_client import parse
from typst_lint import InputError, SafetyError, digest, require

# Reserve the existing parent proof's budget plus both of its bounded
# analyzer replays; the independent attestation then reserves three more
# analyzer replay bounds before starting them. This is charged work, not a
# timing measurement. The preexisting parent plan has its own separate budget.
MAX_WORK = 16_000_000
_PARENT_RESERVE = parent.MAX_WORK + 2 * highlight.MAX_AST_WORK
_REPLAY_RESERVE = highlight.MAX_AST_WORK
MAX_BYTES = 256 * 1024
CONDITIONS = (
    'The selected captured package and its facade initialize and bind names as represented by the source routes.',
    'The reviewed markub definition and util artifact are captured from the selected package root.',
    'Runtime selection, execution, transitive effects, rendering and source writes are not proved.',
    'This evidence does not authorize combining plans, applying edits or compiling.',
    'Unavailable or unsafe optional artifacts withhold this evidence; a changed capture rejects the report.',
)


def _report_bytes(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    return len(encoded.encode('utf-8')) + 2 * encoded.count('\n')


class _Tracer(parent._Tracer):
    def incoming(self, tree, index, path, active):
        result = super().incoming(tree, index, path, active)
        shape = graph.import_shape(tree, index)
        if (result.reason or shape is None or shape.request != namespaces.MANNOT
                or shape.items is not None):
            return result
        package = self.packages.get(namespaces.MANNOT)
        if package is None or 'markub' not in package.exports:
            return result
        values = dict(result.exports)
        route = (highlight.NameRoute(namespaces.MANNOT, package.evidence.source_sha256,
                 'package-export', namespaces.MANNOT, 'markub', 'markub'),
                 highlight.NameRoute(path, digest(tree.source), 'wildcard-import',
                 namespaces.MANNOT, 'markub', 'markub'))
        values['markub'] = highlight._Value(route, 'markub')
        return replace(result, exports=tuple(sorted(values.items())))


def _route(evidence, tree, path, outer, origin, remaining, *, graph_override=None):
    nodes = tree.nodes
    remaining -= _REPLAY_RESERVE + 3 * len(nodes) + len(tree.source)
    if remaining <= 0:
        raise SafetyError('Mannot binding work budget exceeded')
    # The parent proof already rejects unreviewed local bindings. Palette
    # contexts are the sole allowed declaration that might shadow markub.
    for i, node in enumerate(nodes):
        if node.kind != 'LetBinding' or not outer['body_start'] <= node.start < node.end <= outer['body_end']:
            continue
        pieces = graph.significant(tree, i)
        if (len(pieces) >= 2 and nodes[pieces[1]].kind == 'Ident'
                and graph.raw(tree, pieces[1]) == b'markub'):
            raise SafetyError('Local markub binding shadows the captured route')
    tracer = _Tracer(evidence._session, evidence._variant, tree, frozenset(), origin['imported_name'])
    if graph_override is not None:
        tracer.graph = graph_override
    bindings, remaining = parent._root_bindings(tree, path, outer, tracer, remaining)
    if tracer.exhausted:
        raise SafetyError('Mannot binding work budget exceeded')
    value = bindings.get('markub')
    if (type(value) is not highlight._Value or not value.route
            or value.route[0].path != namespaces.MANNOT
            or value.route[0].source_sha256 != parent._MANNOT_SOURCE
            or value.route[0].imported_name != 'markub'):
        raise SafetyError('Mannot markub root binding is unresolved')
    return [asdict(step) for step in value.route], remaining


def preview_with_evidence(evidence, path, start, *, max_work=MAX_WORK, max_bytes=MAX_BYTES):
    """Return the unchanged parent plan and an independent bounded evidence row."""
    require(type(max_work) is int and 0 < max_work <= MAX_WORK
            and type(max_bytes) is int and 0 < max_bytes <= MAX_BYTES,
            'Invalid mannot evidence budget')
    baseline = parent.preview_one(evidence, path, start)
    withheld = dict(path=path, start=start, status='WITHHELD', conditions=CONDITIONS,
                    runtime_binding_proved=False, execution_proved=False, coverage_complete=False,
                    render_equivalence_proved=False, edit_permission=False, compiler_permission=False)
    try:
        modern.ModernHighlightAnalyzer._check(evidence)
        capture = evidence._session._namespaces
        if not namespaces.verify_multiroot_capture(capture):
            raise InputError('Selected namespace capture changed or is unissued')
        if not capture.mannot_artifacts:
            raise SafetyError('Reviewed selected mannot definition artifacts are unavailable: '
                              + capture.mannot_status.lower())
        source = evidence._session._context.source_for(path)
        tree = source.tree
        remaining = max_work - _PARENT_RESERVE
        if remaining <= 0:
            raise SafetyError('Mannot binding work budget exceeded')
        proof = parent.analyze_one(evidence, path, start, max_work=parent.MAX_WORK)
        if not proof['captured_child_helpers']:
            raise SafetyError('No reviewed child helper captures a scope')
        remaining -= _REPLAY_RESERVE + 2 * len(tree.nodes) + len(tree.source)
        if remaining <= 0:
            raise SafetyError('Mannot binding work budget exceeded')
        replay = modern._ModernAnalyzer(evidence._session, evidence._variant,
                                        scoped_callbacks=evidence._scoped_callbacks)
        issued = modern.ModernHighlightAnalyzer._analyze_one(evidence, tree, path, replay)
        if replay.exhausted:
            raise SafetyError('Mannot binding work budget exceeded')
        findings = [d for d in issued['diagnostics'] if d['start'] == start]
        if len(findings) != 1 or len(findings[0].get('callback_context') or ()) != 1:
            raise SafetyError('Selected callback provenance is unresolved')
        diagnostic = findings[0]
        outer = diagnostic['callback_context'][0]
        origin = diagnostic['content_context'][outer['content_context_index']][0]
        edits = tuple(Edit(e['start'], e['end'], e['replacement'].encode('utf-8'))
                      for e in baseline['edits'])
        proposal = plan(tree.source, edits)
        if proposal.deferred or len(proposal.edits) != 1:
            raise SafetyError('Parent plan is not a unique issued edit')
        candidate = proposal.candidate()
        if baseline['baseline_sha256'] != digest(tree.source) or baseline['candidate_sha256'] != digest(candidate):
            raise SafetyError('Parent plan source hash changed')
        remaining -= len(candidate)
        if remaining <= 0:
            raise SafetyError('Mannot binding work budget exceeded')
        parsed = parse(candidate, tree.mode)
        mapper = _MappedEdits(edits)
        mapped = dict(outer)
        for key in ('body_start', 'body_end', 'call_start', 'call_end'):
            mapped[key] = mapper.mapped(outer[key])
        original_graph = evidence._session._graph
        graph_source = original_graph.source_for(path)
        if graph_source is None or graph_source.source != tree.source:
            raise SafetyError('Captured source graph changed')
        derived = replace(original_graph, sources=tuple(
            replace(item, source=candidate) if item.path == path else item for item in original_graph.sources))
        binding._graph_signature(derived)
        before, remaining = _route(evidence, tree, path, outer, origin, remaining)
        after, remaining = _route(evidence, parsed, path, mapped, origin, remaining, graph_override=derived)
        expected = [dict(step, source_sha256=digest(candidate)) if step['path'] == path else step for step in before]
        if expected != after:
            raise SafetyError('Candidate mannot binding route changed')
        artifacts = [dict(relative_path=x.relative_path, source_identity=x.source_identity,
                          source_sha256=digest(x.source)) for x in capture.mannot_artifacts]
        result = dict(path=path, start=start, status='CONDITIONAL', baseline_sha256=digest(tree.source),
                      candidate_sha256=digest(candidate), captured_helpers=proof['captured_child_helpers'],
                      route=before, artifacts=artifacts, work_used=max_work - remaining, conditions=CONDITIONS,
                      runtime_binding_proved=False, execution_proved=False, coverage_complete=False,
                      render_equivalence_proved=False, edit_permission=False, compiler_permission=False)
        if _report_bytes(result) > max_bytes:
            raise SafetyError('Mannot binding report byte budget exceeded')
        modern.ModernHighlightAnalyzer._check(evidence)
        return baseline, result
    except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
        modern.ModernHighlightAnalyzer._check(evidence)
        withheld['reason'] = str(error)
        if _report_bytes(withheld) > max_bytes:
            raise SafetyError('Mannot binding report byte budget exceeded')
        return baseline, withheld
