"""Experimental source-only union of existing scoped and callback math plans.

No CLI or mutation authority.  Every target is independently recomputed against
one captured baseline; a failed union preserves the baseline report verbatim.
"""
import json

import conditional_modern_highlight as modern
import document_math_builtin_evidence as math_evidence
import document_modern_highlight_preview as preview
import scoped_math_capture_prototype as math
import scoped_reference_preview as scoped
from document_gap_preview import _MappedEdits
from edit_plan import Edit, plan
from syntax_client import parse
from typst_lint import InputError, SafetyError, digest, load_rules, require

MAX_SOURCES = 2
MAX_SITES = 4
MAX_UNION_SCOPE_WORK = scoped.MAX_SCOPE_WORK
CONDITIONS = math.CONDITIONS + preview.SCOPED_REFERENCE_CONDITION + (
    'The source-only union is limited to one captured callback and one source.',
    'Other sources, runtime execution, label resolution, side effects and rendering are not proved.',
    'Source hashes describe captured bytes; current filesystem state is not revalidated.',
)


def _withheld(path, reason):
    return dict(path=path, verification='withheld', reason=str(reason), edits=[],
                runtime_binding_proved=False, execution_proved=False, render_equivalence_proved=False,
                edit_permission=False, compiler_permission=False)


def _union(evidence, path, starts, diagnostic_row, baseline_row, budget):
    source = evidence._session._context.source_for(path)
    if source is None:
        raise SafetyError('Source is not in the issued capture')
    tree = source.tree
    if (baseline_row is None or baseline_row.get('verification') !=
            'conditional-modern-highlight-syntax-directives-and-reanalysis'
            or baseline_row.get('scoped_reference_withheld')
            or baseline_row.get('baseline_sha256') != digest(tree.source)):
        raise SafetyError('No complete baseline source plan is available')
    analyzer = modern._ModernAnalyzer(evidence._session, evidence._variant,
                                      scoped_callbacks=evidence._scoped_callbacks)
    replay = modern.ModernHighlightAnalyzer._analyze_one(evidence, tree, path, analyzer)
    modern.ModernHighlightAnalyzer._check(evidence)
    if replay != diagnostic_row:
        raise SafetyError('Issued source diagnostics changed')
    by_start = {d['start']: d for d in replay['diagnostics']}
    if len(by_start) != len(replay['diagnostics']):
        raise SafetyError('Ambiguous highlight diagnostic')
    baseline_edits = tuple(Edit(e['start'], e['end'], e['replacement'].encode('utf-8'))
                           for e in baseline_row['edits'])
    base_plan = plan(tree.source, baseline_edits)
    if (base_plan.deferred or len(base_plan.edits) != len(baseline_edits)
            or digest(base_plan.candidate()) != baseline_row.get('candidate_sha256')):
        raise SafetyError('Baseline plan no longer matches its source')
    baseline_diagnostics = []
    for edit in baseline_edits:
        matches = [d for d in replay['diagnostics'] if d['start'] - 1 == edit.start and d['end'] == edit.end]
        if len(matches) != 1:
            raise SafetyError('Baseline edit has no unique issued diagnostic')
        baseline_diagnostics.append(matches[0])
    selected = []
    math_edits = []
    for start in starts:
        d = by_start.get(start)
        if d is None:
            raise SafetyError('Scoped math diagnostic is unavailable')
        result = math.preview_one(evidence, path, start)
        if result.get('verification') != 'conditional-modern-highlight-syntax-directives-and-reanalysis':
            raise SafetyError('Scoped math candidate was withheld: ' + result.get('reason', 'unsupported'))
        if len(result['edits']) != 1:
            raise SafetyError('Scoped math candidate is ambiguous')
        e = result['edits'][0]
        math_edits.append(Edit(e['start'], e['end'], e['replacement'].encode('utf-8')))
        selected.append(d)
    diagnostics = baseline_diagnostics + selected
    if len({(d['start'], d['end']) for d in diagnostics}) != len(diagnostics):
        raise SafetyError('Baseline and scoped math edits overlap')
    callbacks = [d.get('callback_context') or () for d in diagnostics]
    if not callbacks or any(len(c) != 1 or c[0] != callbacks[0][0] for c in callbacks):
        raise SafetyError('Plans do not share one exact issued callback')
    callback = callbacks[0][0]
    capture = scoped.CapturedScopedReferences(evidence, tree, path, budget=budget)
    scoped_before = {}
    for d in baseline_diagnostics:
        refs = capture.selected(d, budget=budget)
        if refs:
            scoped_before[(d['start'], d['end'])] = refs
    if not scoped_before:
        raise SafetyError('Baseline contains no proven scoped reference')
    scoped_spans = frozenset(v for refs in scoped_before.values() for v in refs)
    math_spans = frozenset((d['start'], d['end']) for d in selected)
    edits, plans = preview._select(tree, diagnostics, allow_direct_references=True,
                                   proven_scoped_references=scoped_spans, proven_math_spans=math_spans)
    expected = sorted(baseline_edits + tuple(math_edits), key=lambda e: (e.start, e.end))
    ordered = sorted(zip(edits, plans), key=lambda pair: (pair[0].start, pair[0].end))
    edits = tuple(item[0] for item in ordered)
    plans = tuple(item[1] for item in ordered)
    if list(edits) != expected:
        raise SafetyError('Combined edits differ from independently verified plans')
    proposal = plan(tree.source, edits)
    if proposal.deferred or len(proposal.edits) != len(edits):
        raise SafetyError('Combined source edits conflict')
    candidate = proposal.candidate()
    parsed = parse(candidate, tree.mode)
    records = load_rules()[0]['rules']
    if parsed.nodes[0].errors or preview._scopes(tree, records, edits) != preview._scopes(parsed, records):
        raise SafetyError('Union changed syntax or suppression targets')
    preview._verify_insertions(tree, parsed, edits, plans)
    mapper = _MappedEdits(edits)
    mapped_callback = dict(callback)
    for key in ('body_start', 'body_end', 'call_start', 'call_end'):
        mapped_callback[key] = mapper.mapped(callback[key])
    math_after = math._ScopedMathResolver(parsed, path, math_evidence._engine(evidence._session), mapped_callback)
    math_after.visit(0, math_after.state)
    if not math_after.visited or math_after.engine.exhausted:
        raise SafetyError('Candidate math callback binding was not established')
    for edit, _, _, spans in plans:
        base = mapper.mapped(edit.start)
        for kind, begin, end, _ in spans:
            if kind != 'reviewed-math-equation':
                continue
            matches = [i for i, n in enumerate(parsed.nodes) if n.kind == 'Equation'
                       and (n.start, n.end) == (base + begin, base + end)]
            if len(matches) != 1:
                raise SafetyError('Union math equation is ambiguous')
            pending = matches[:]
            while pending:
                i = pending.pop()
                n = parsed.nodes[i]
                if n.kind == 'MathCall' and i not in math_after.resolved:
                    raise SafetyError('Union math binding changed')
                pending.extend(n.children)
    # All scoped references are revalidated in their mapped callback as a
    # complete ordered byte sequence, with the same shared work budget.
    for d in baseline_diagnostics:
        before = scoped_before.get((d['start'], d['end']))
        if not before:
            continue
        bounds = (mapped_callback['body_start'], mapped_callback['body_end'])
        after = scoped._scope(parsed, d, override=bounds, budget=budget)
        old_values = tuple(tree.source[a:b] for a, b in sorted(before))
        new_values = tuple(parsed.source[a:b] for a, b in sorted(after))
        if old_values != new_values:
            raise SafetyError('Scoped reference binding, order or call count changed')
    before_resolution, after_resolution = preview.resolve(tree, packages=()), preview.resolve(parsed, packages=())
    before_display = preview.analyze_displays(tree, path, catalog=records)
    after_display = preview.analyze_displays(parsed, path, catalog=records)
    if any(not preview._same_findings(tree, before_display[rid], parsed, after_display[rid], edits)
           for rid in before_display):
        raise SafetyError('Union changes display findings')
    before_scripts = preview.analyze_scripts(tree, path, catalog=records, _verify=False, _allow_code_call=True)
    after_scripts = preview.analyze_scripts(parsed, path, catalog=records, _verify=False, _allow_code_call=True)
    if not preview._same_script_findings_with_moved_equations(tree, before_scripts, parsed, after_scripts, edits, plans):
        raise SafetyError('Union changes script findings')
    before_notes = preview.footnote_rules.analyze_conditional_nested(tree, path, before_resolution, catalog=records)
    after_notes = preview.footnote_rules.analyze_conditional_nested(parsed, path, after_resolution, catalog=records)
    if any(not preview._same_findings(tree, before_notes[rid], parsed, after_notes[rid], edits)
           for rid in preview.footnote_rules.RULE_IDS):
        raise SafetyError('Union changes footnote findings')
    for check in (preview.paragraph_rules.analyze_conditional_nested_gap,
                  preview.paragraph_rules.analyze_conditional_nested_target,
                  preview.paragraph_rules.analyze_conditional_nested_capitalization,
                  preview.prose_marker_rules.analyze_conditional, preview.continuation_rules.analyze_conditional):
        if not preview._same_findings(tree, check(tree, path, catalog=records),
                                      parsed, check(parsed, path, catalog=records), edits):
            raise SafetyError('Union changes paragraph findings')
    if not preview._same_findings(tree, preview.proof_rules.analyze_conditional(tree, path, before_resolution, catalog=records),
                                  parsed, preview.proof_rules.analyze_conditional(parsed, path, after_resolution, catalog=records), edits):
        raise SafetyError('Union changes proof findings')
    modern.ModernHighlightAnalyzer._check(evidence)
    return dict(path=path, verification='conditional-scoped-math-union-source-only',
                baseline_sha256=digest(tree.source), candidate_sha256=digest(candidate),
                edits=[dict(start=e.start, end=e.end, replacement=e.replacement.decode('utf-8'), rule_id=preview.RULE)
                       for e in edits], conditions=CONDITIONS, runtime_binding_proved=False, execution_proved=False,
                coverage_complete=False, render_equivalence_proved=False, edit_permission=False, compiler_permission=False)


def preview_batch(evidence, targets, *, max_items=preview.MAX_PREVIEW_ITEMS, max_bytes=preview.MAX_PREVIEW_BYTES,
                  return_evidence=False):
    if type(evidence) is not modern.ModernHighlightAnalyzer or type(targets) not in (tuple, list):
        raise InputError('Issued modern evidence and target list are required')
    require(type(return_evidence) is bool and 0 < len(targets) <= MAX_SOURCES and type(max_items) is int and 0 < max_items <= preview.MAX_PREVIEW_ITEMS
            and type(max_bytes) is int and 0 < max_bytes <= preview.MAX_PREVIEW_BYTES,
            'Invalid union source count or report budget')
    parsed_targets = []
    for item in targets:
        require(type(item) in (tuple, list) and len(item) == 2 and type(item[0]) is str
                and type(item[1]) in (tuple, list) and 0 < len(item[1]) <= MAX_SITES
                and all(type(v) is int and v >= 0 for v in item[1])
                and len(set(item[1])) == len(item[1]), 'Invalid scoped math target')
        parsed_targets.append((item[0], tuple(item[1])))
    require(len({p for p, _ in parsed_targets}) == len(parsed_targets)
            and sum(len(starts) for _, starts in parsed_targets) <= MAX_SITES, 'Duplicate targets or too many sites')
    modern.ModernHighlightAnalyzer._check(evidence)
    diagnostics, baseline = preview.preview_all(evidence, max_items=max_items, max_bytes=max_bytes,
                return_evidence=True, allow_direct_references=True, allow_scoped_references=True, reviewed_math=True)
    diagnostics_by_path = {row['path']: row for row in diagnostics['by_file']}
    baseline_by_path = {row['path']: row for row in baseline['by_file']}
    result = dict(status='CONDITIONAL' if baseline['status'] == 'CONDITIONAL' else 'INCOMPLETE',
                  baseline=baseline, unions=[], conditions=CONDITIONS,
                  runtime_binding_proved=False, execution_proved=False, coverage_complete=False,
                  render_equivalence_proved=False, edit_permission=False, compiler_permission=False)
    budget = [MAX_UNION_SCOPE_WORK]
    for path, starts in parsed_targets:
        try:
            if path not in diagnostics_by_path:
                raise SafetyError('Source diagnostic is unavailable')
            item = _union(evidence, path, starts, diagnostics_by_path[path], baseline_by_path.get(path), budget)
        except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
            item = _withheld(path, error)
            result['status'] = 'INCOMPLETE'
        result['unions'].append(item)
    modern.ModernHighlightAnalyzer._check(evidence)
    count = (baseline['report_items'] + diagnostics['report_items'] + 1 + len(CONDITIONS)
             + sum(1 + len(row['edits']) + len(row.get('conditions', ())) + int('reason' in row)
                   for row in result['unions']))
    require(count <= max_items, 'Union preview report item limit exceeded')
    result['report_items'] = count
    # The baseline evidence was used to issue the plans and remains part of the
    # budget even though the caller already received it through the baseline.
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
    diag_json = json.dumps(diagnostics, ensure_ascii=False, sort_keys=True, indent=2)
    require(len(encoded.encode('utf-8')) + 2 * encoded.count('\n')
            + len(diag_json.encode('utf-8')) + 2 * diag_json.count('\n') <= max_bytes,
            'Union preview report byte limit exceeded')
    if return_evidence:
        section = dict(result)
        del section['baseline']
        return diagnostics, baseline, section
    return result
