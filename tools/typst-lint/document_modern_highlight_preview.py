"""Conditional source-only splitting of very simple modern highlights.

Only the exact reviewed modern helper evidence is admitted. No original
source, runtime binding, render equivalence or compiler authority is implied.
"""
from bisect import bisect_right
from collections import Counter
import json

import conditional_modern_highlight as modern
from document_display import analyze_all as analyze_displays
from document_gap_preview import _MappedEdits, _scopes
from document_sequence_preview import _same_findings, _FindingMapper, _keys
from edit_plan import Edit, plan
import continuation_rules
import footnote_rules
import import_graph
import paragraph_rules
import proof_rules
import prose_marker_rules
from script_rule import analyze as analyze_scripts
from static_resolver import resolve
from syntax_client import parse
from typst_lint import InputError, SafetyError, digest, load_rules, require

RULE = 'pde.text-only-highlight'
CONDITIONS = (
    'Runtime resolves highlighted to the exact reviewed modern text-utils helper.',
    'The captured source executes in the same intended prose and math context.',
    'Splitting highlighting can change emphasis, smart-quote direction, spacing and page layout; PDF equivalence is not proved.',
)
_MATH_EXCLUDED = frozenset(('FuncCall', 'MathCall', 'CodeBlock', 'Hash', 'Contextual', 'LetBinding',
                            'ShowRule', 'ModuleImport', 'ModuleInclude', 'Label', 'LineComment', 'BlockComment'))
MAX_PREVIEW_ITEMS = 100_000
MAX_PREVIEW_BYTES = 16 * 1024 * 1024
# Every fallback reanalyses an entire source; keep work bounded independently
# of the output size. Exhaustion never permits an unverified partial plan.
MAX_SUBSET_CHECKS = 32
_OVERLAPPING_FINDING = 'An existing conditional finding lies inside an edit'


def _math_signature(tree, root, *, allow_reviewed_calls=False):
    nodes, source = tree.nodes, tree.source
    base = nodes[root].start
    order, pending = [], [root]
    while pending:
        index = pending.pop()
        node = nodes[index]
        if node.kind in _MATH_EXCLUDED and not (allow_reviewed_calls and node.kind == 'MathCall'):
            return None
        order.append(index)
        pending.extend(reversed(node.children))
    remap = {old: new for new, old in enumerate(order)}
    return tuple((nodes[i].kind, nodes[i].start - base, nodes[i].end - base,
                  nodes[i].display, nodes[i].errors, tuple(remap[c] for c in nodes[i].children),
                  tuple((name, remap[target]) for name, target in nodes[i].operands),
                  source[nodes[i].start:nodes[i].end] if not nodes[i].children else None)
                 for i in order)


def _select(tree, diagnostics, *, proven_math_spans=frozenset()):
    nodes, source = tree.nodes, tree.source
    parents = {child: index for index, node in enumerate(nodes) for child in node.children}
    positions = {child: pos for node in nodes for pos, child in enumerate(node.children)}
    by_span = {(node.start, node.end): i for i, node in enumerate(nodes) if node.kind == 'FuncCall'}
    edits, plans = [], []
    for diagnostic in diagnostics:
        index = by_span.get((diagnostic['start'], diagnostic['end']))
        if index is None or diagnostic.get('mode_source') != 'default':
            continue
        node = nodes[index]
        parts = import_graph.significant(tree, index)
        if (len(parts) != 2 or nodes[parts[0]].kind != 'Ident'
                or import_graph.raw(tree, parts[0]) != b'highlighted' or nodes[parts[1]].kind != 'Args'):
            continue
        args = import_graph.significant(tree, parts[1])
        kinds = tuple(nodes[arg].kind for arg in args)
        if kinds == ('ContentBlock',):
            body_arg = args[0]
        elif (kinds == ('LeftParen', 'RightParen', 'ContentBlock')
              and source[nodes[parts[0]].end:nodes[args[2]].start] == b'()'):
            # An exact empty argument list is still one no-option body. Refuse
            # whitespace and comments rather than deleting them implicitly.
            body_arg = args[2]
        else:
            continue
        block = nodes[body_arg]
        inner = [c for c in block.children if nodes[c].kind == 'Markup']
        if len(inner) != 1:
            continue
        body = nodes[inner[0]]
        children = [nodes[c] for c in body.children]
        if not children or any(c.kind not in ('Text', 'Space', 'SmartQuote', 'Equation') for c in children):
            continue
        parent, position = parents.get(index), positions.get(index, 0)
        if parent is None or nodes[parent].kind != 'Markup' or not position:
            continue
        mark = nodes[nodes[parent].children[position - 1]]
        if mark.kind != 'Hash' or mark.end != node.start:
            continue
        equations = [c for c in body.children if nodes[c].kind == 'Equation']
        proven_math = (node.start, node.end) in proven_math_spans
        signatures = [_math_signature(tree, c, allow_reviewed_calls=proven_math) for c in equations]
        if not equations or any(signature is None for signature in signatures):
            continue
        # Text and whitespace are copied byte-for-byte. Math token subtrees
        # must remain intact and become direct siblings of the new calls.
        output, spans = bytearray(), []
        text_start, text_nodes = None, []
        def flush_text(end):
            nonlocal text_start, text_nodes
            if text_start is None:
                return
            text = source[text_start:end]
            if text.strip(b' \t\r\n'):
                start = len(output)
                output.extend(b'#highlighted[' + text + b']')
                spans.append(('highlight', start, len(output), tuple(
                    (nodes[child].kind, source[nodes[child].start:nodes[child].end]) for child in text_nodes)))
            else:
                output.extend(text)
            text_start = None
            text_nodes = []
        for child_id in body.children:
            child = nodes[child_id]
            if child.kind != 'Equation':
                if text_start is None:
                    text_start = child.start
                text_nodes.append(child_id)
                continue
            flush_text(child.start)
            start = len(output)
            output.extend(source[child.start:child.end])
            spans.append(('reviewed-math-equation' if proven_math else 'equation', start, len(output),
                          _math_signature(tree, child_id, allow_reviewed_calls=proven_math)))
        flush_text(body.end)
        edit = Edit(mark.start, node.end, bytes(output))
        edits.append(edit)
        plans.append((edit, nodes[parent].start, nodes[parent].end, tuple(spans)))
    return tuple(edits), tuple(plans)


def _verify_insertions(original, candidate, edits, plans):
    mapper = _MappedEdits(edits)
    nodes = candidate.nodes
    parents = {child: index for index, node in enumerate(nodes) for child in node.children}
    by_span = {}
    for index, node in enumerate(nodes):
        by_span.setdefault((node.kind, node.start, node.end), []).append(index)
    for edit, owner_start, owner_end, spans in plans:
        start = mapper.mapped(edit.start)
        owners = by_span.get(('Markup', mapper.mapped(owner_start), mapper.mapped(owner_end)), ())
        if len(owners) != 1:
            raise SafetyError('Highlight split changed its enclosing markup ownership')
        owner = owners[0]
        for kind, begin, end, signature in spans:
            begin, end = start + begin, start + end
            if kind in ('equation', 'reviewed-math-equation'):
                found = by_span.get(('Equation', begin, end), ())
                if len(found) != 1 or parents.get(found[0]) != owner or _math_signature(candidate, found[0], allow_reviewed_calls=kind == 'reviewed-math-equation') != signature:
                    raise SafetyError('Unwrapped equation syntax or owner changed')
            else:
                found = by_span.get(('FuncCall', begin + 1, end), ())
                if len(found) != 1 or parents.get(found[0]) != owner:
                    raise SafetyError('Generated highlight call ownership changed')
                parts = import_graph.significant(candidate, found[0])
                if (len(parts) != 2 or nodes[parts[0]].kind != 'Ident'
                        or import_graph.raw(candidate, parts[0]) != b'highlighted'
                        or candidate.source[begin:begin + 1] != b'#'):
                    raise SafetyError('Generated highlight call syntax changed')
                marks = by_span.get(('Hash', begin, begin + 1), ())
                args = import_graph.significant(candidate, parts[1])
                if (len(marks) != 1 or parents.get(marks[0]) != owner or len(args) != 1
                        or nodes[args[0]].kind != 'ContentBlock'):
                    raise SafetyError('Generated highlight call shape or escape changed')
                bodies = [child for child in nodes[args[0]].children if nodes[child].kind == 'Markup']
                if (len(bodies) != 1 or tuple((nodes[child].kind,
                        candidate.source[nodes[child].start:nodes[child].end]) for child in nodes[bodies[0]].children)
                        != signature):
                    raise SafetyError('Generated highlight prose syntax changed')

    # Untouched syntax must preserve its exact boundaries, bytes, parent-kind,
    # and direct child/operand layout. This rejects a split that accidentally
    # joins adjacent source tokens outside the replaced call.
    original_nodes = original.nodes
    original_parents = {child: index for index, node in enumerate(original_nodes) for child in node.children}
    ends = [edit.end for edit in edits]
    matched = set()
    for index, node in enumerate(original_nodes):
        if node.start == node.end:
            continue
        pos = bisect_right(ends, node.start)
        if pos < len(edits) and edits[pos].start < node.end:
            continue
        start, end = mapper.mapped(node.start), mapper.mapped(node.end)
        matches = by_span.get((node.kind, start, end), ())
        before_parent = original_parents.get(index)
        def equivalent(other_index):
            other = nodes[other_index]
            after_parent = parents.get(other_index)
            return ((original_nodes[before_parent].kind if before_parent is not None else None)
                    == (nodes[after_parent].kind if after_parent is not None else None)
                    and node.display == other.display and node.errors == other.errors
                    and original.source[node.start:node.end] == candidate.source[start:end]
                    and [(original_nodes[c].kind, original_nodes[c].start - node.start,
                          original_nodes[c].end - node.start) for c in node.children]
                        == [(nodes[c].kind, nodes[c].start - start, nodes[c].end - start) for c in other.children]
                    and [(name, original_nodes[c].start - node.start, original_nodes[c].end - node.start)
                         for name, c in node.operands]
                        == [(name, nodes[c].start - start, nodes[c].end - start) for name, c in other.operands])
        # Nested parser nodes can legitimately share kind and byte span.
        # Match equal structures once each instead of requiring a unique span.
        selected = next((other for other in matches if other not in matched and equivalent(other)), None)
        if selected is None:
            raise SafetyError('Highlight split changed syntax outside its selected calls')
        matched.add(selected)


def _same_script_findings_with_moved_equations(before_tree, before, after_tree, after, edits, plans):
    """Map only script findings inside exactly preserved moved equations."""
    mapper = _MappedEdits(edits)
    moved = []
    for edit, _, _, spans in plans:
        originals = sorted((n for n in before_tree.nodes if n.kind == 'Equation'
                            and edit.start < n.start and n.end <= edit.end), key=lambda n: n.start)
        replacements = [(begin, end) for kind, begin, end, _ in spans
                        if kind in ('equation', 'reviewed-math-equation')]
        if len(originals) != len(replacements):
            raise SafetyError('Cannot map script findings through ambiguous equations')
        base = mapper.mapped(edit.start)
        for original, (begin, end) in zip(originals, replacements):
            if before_tree.source[original.start:original.end] != after_tree.source[base + begin:base + end]:
                raise SafetyError('Cannot map script findings through changed equation bytes')
            moved.append((original.start, original.end, base + begin))
    old_mapper = _FindingMapper(before_tree.source, edits)
    def mapped_keys(findings):
        keys = Counter()
        for finding in findings:
            position = old_mapper.offset(finding)
            matches = [(begin, end, target) for begin, end, target in moved if begin <= position < end]
            if len(matches) > 1:
                raise SafetyError('Ambiguous equation owning an existing script finding')
            if matches:
                begin, _, target = matches[0]
                position = target + position - begin
            else:
                position = old_mapper.mapped(finding)
            keys[(finding.rule_id, finding.grade, finding.path, position,
                  finding.explanation, finding.source)] += 1
        return keys
    return (mapped_keys(before.diagnostics) == _keys(after_tree.source, after.diagnostics)
            and mapped_keys(before.suppressed) == _keys(after_tree.source, after.suppressed))


def _preview_source(evidence, tree, path, diagnostics, records, *, proven_math_spans=frozenset()):
    edits, plans = _select(tree, diagnostics, proven_math_spans=proven_math_spans)
    if not edits:
        return None
    empty = dict(path=path, baseline_sha256=digest(tree.source), candidate_sha256=None,
                 verification='withheld', conditions=CONDITIONS, edits=[], edit_permission=False,
                 compiler_permission=False, runtime_binding_proved=False, render_equivalence_proved=False)
    try:
        proposal = plan(tree.source, edits)
        if proposal.deferred or len(proposal.edits) != len(edits):
            raise SafetyError('Conflicting modern highlight splits')
        candidate = proposal.candidate()
        parsed = parse(candidate, tree.mode)
        if parsed.nodes[0].errors or _scopes(tree, records, proposal.edits) != _scopes(parsed, records):
            raise SafetyError('Highlight split changed syntax or suppression targets')
        _verify_insertions(tree, parsed, proposal.edits, plans)
        if proven_math_spans:
            # Re-run the binding analysis on the actual reparsed candidate.
            # The structural check above proves imported code and all tokens
            # outside the selected replacements are unchanged.
            import document_math_builtin_evidence as math_evidence
            resolver = math_evidence._MathResolver(parsed, path, math_evidence._engine(evidence._session))
            resolver.visit(0, resolver.state)
            mapper = _MappedEdits(proposal.edits)
            for edit, _, _, spans in plans:
                base = mapper.mapped(edit.start)
                for kind, begin, end, _ in spans:
                    if kind != 'reviewed-math-equation':
                        continue
                    matches = [i for i, node in enumerate(parsed.nodes) if node.kind == 'Equation'
                               and (node.start, node.end) == (base + begin, base + end)]
                    if len(matches) != 1:
                        raise SafetyError('Reviewed math candidate equation changed')
                    pending = [matches[0]]
                    while pending:
                        i = pending.pop()
                        node = parsed.nodes[i]
                        if node.kind == 'MathCall' and i not in resolver.resolved:
                            raise SafetyError('Candidate math binding is no longer supported')
                        pending.extend(node.children)
        before_resolution, after_resolution = resolve(tree, packages=()), resolve(parsed, packages=())
        before_display, after_display = analyze_displays(tree, path, catalog=records), analyze_displays(parsed, path, catalog=records)
        if any(not _same_findings(tree, before_display[rid], parsed, after_display[rid], proposal.edits)
               for rid in before_display):
            raise SafetyError('Highlight split changes display findings')
        before_scripts = analyze_scripts(tree, path, catalog=records, _verify=False, _allow_code_call=True)
        after_scripts = analyze_scripts(parsed, path, catalog=records, _verify=False, _allow_code_call=True)
        scripts_match = (_same_script_findings_with_moved_equations(tree, before_scripts, parsed, after_scripts,
                                                                   proposal.edits, plans)
                         if proven_math_spans else _same_findings(tree, before_scripts, parsed, after_scripts,
                                                                 proposal.edits))
        if not scripts_match:
            raise SafetyError('Highlight split changes script findings')
        notes_before = footnote_rules.analyze_conditional_nested(tree, path, before_resolution, catalog=records)
        notes_after = footnote_rules.analyze_conditional_nested(parsed, path, after_resolution, catalog=records)
        if any(not _same_findings(tree, notes_before[rid], parsed, notes_after[rid], proposal.edits)
               for rid in footnote_rules.RULE_IDS):
            raise SafetyError('Highlight split changes footnote findings')
        for analyzer in (paragraph_rules.analyze_conditional_nested_gap, paragraph_rules.analyze_conditional_nested_target,
                         paragraph_rules.analyze_conditional_nested_capitalization, prose_marker_rules.analyze_conditional,
                         continuation_rules.analyze_conditional):
            if not _same_findings(tree, analyzer(tree, path, catalog=records),
                                  parsed, analyzer(parsed, path, catalog=records), proposal.edits):
                raise SafetyError('Highlight split changes paragraph findings')
        if not _same_findings(tree, proof_rules.analyze_conditional(tree, path, before_resolution, catalog=records),
                              parsed, proof_rules.analyze_conditional(parsed, path, after_resolution, catalog=records),
                              proposal.edits):
            raise SafetyError('Highlight split changes proof findings')
        evidence._check()
        return dict(empty, verification='conditional-modern-highlight-syntax-directives-and-reanalysis',
                    candidate_sha256=digest(candidate),
                    edits=[dict(start=edit.start, end=edit.end, replacement=edit.replacement.decode('utf-8'), rule_id=RULE)
                           for edit in proposal.edits])
    except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
        return dict(empty, reason=str(error))


def _partial_preview(evidence, tree, path, diagnostics, records, original, budget, *, proven_math_spans=frozenset()):
    """Retry independent edits, then revalidate their union on the original tree."""
    if original['verification'] != 'withheld' or original.get('reason') != _OVERLAPPING_FINDING:
        return original
    edits, _ = _select(tree, diagnostics, proven_math_spans=proven_math_spans)
    if len(edits) < 2:
        return original
    if len(edits) > budget[0]:
        return dict(original, reason='Partial highlight verification budget exceeded; entire source withheld')
    budget[0] -= len(edits)
    by_span = {(item['start'] - 1, item['end']): item for item in diagnostics}
    accepted = []
    def run_subset(subset):
        if proven_math_spans:
            return _preview_source(evidence, tree, path, subset, records, proven_math_spans=proven_math_spans)
        return _preview_source(evidence, tree, path, subset, records)
    for edit in edits:
        diagnostic = by_span.get((edit.start, edit.end))
        if diagnostic is None:
            return original
        candidate = run_subset([diagnostic])
        if candidate is not None and candidate['verification'] != 'withheld':
            accepted.append(diagnostic)
    if not accepted or len(accepted) == len(edits):
        # A combination may fail even when all its members pass separately.
        return original
    # The final result is based on the original source, not independently
    # generated candidates. This catches interactions among accepted edits and
    # rechecks all existing diagnostics and suppression scopes, including the
    # excluded source. If the union fails, retain the original withheld result.
    combined = run_subset(accepted)
    if combined is None or combined['verification'] == 'withheld':
        return original
    return combined


def preview_all(evidence, *, max_items=MAX_PREVIEW_ITEMS, max_bytes=MAX_PREVIEW_BYTES, return_evidence=False):
    """Recompute pinned evidence and return an all-or-nothing source-only batch."""
    if type(evidence) is not modern.ModernHighlightAnalyzer:
        raise InputError('Issued modern highlight evidence is required')
    require(type(return_evidence) is bool and type(max_items) is int and 0 < max_items <= MAX_PREVIEW_ITEMS
            and type(max_bytes) is int and 0 < max_bytes <= MAX_PREVIEW_BYTES,
            'Invalid modern highlight preview budget')
    evidence._check()
    diagnostics = evidence.analyze_all(max_items=max_items, max_bytes=max_bytes)
    records = load_rules()[0]['rules']
    result = dict(status='CONDITIONAL', by_file=[], conditions=CONDITIONS, coverage_complete=False, report_items=0,
                  diagnostics_count=0, previewed_diagnostics=0, unpreviewed_diagnostics=0,
                  withheld_source_count=0, unresolved_reasons_count=0,
                  runtime_binding_proved=False, execution_proved=False, render_equivalence_proved=False,
                  edit_permission=False, compiler_permission=False)
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
    evidence_encoded = json.dumps(diagnostics, ensure_ascii=False, sort_keys=True, indent=2)
    size = (len(encoded.encode('utf-8')) + 2 * encoded.count('\n')
            + len(evidence_encoded.encode('utf-8')) + 2 * evidence_encoded.count('\n'))
    items = 6 + len(CONDITIONS)
    require(items + diagnostics['report_items'] <= max_items and size <= max_bytes,
            'Modern highlight preview report budget exceeded')
    subset_budget = [MAX_SUBSET_CHECKS]
    for row in diagnostics['by_file']:
        result['diagnostics_count'] += len(row['diagnostics'])
        result['unresolved_reasons_count'] += len(row['unresolved'])
        if not row['diagnostics']:
            continue
        source = evidence._session._context.source_for(row['path'])
        record = _preview_source(evidence, source.tree, source.path, row['diagnostics'], records)
        if record is None:
            result['unpreviewed_diagnostics'] += len(row['diagnostics'])
            continue
        record = _partial_preview(evidence, source.tree, source.path, row['diagnostics'], records, record, subset_budget)
        if record['verification'] == 'withheld':
            result['withheld_source_count'] += 1
            result['unpreviewed_diagnostics'] += len(row['diagnostics'])
        else:
            result['previewed_diagnostics'] += len(record['edits'])
            result['unpreviewed_diagnostics'] += len(row['diagnostics']) - len(record['edits'])
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2)
        size += len(encoded.encode('utf-8')) + 6 * encoded.count('\n') + (8 if result['by_file'] else 12)
        items += 1 + len(record['edits']) + len(record['conditions']) + int('reason' in record)
        require(items + diagnostics['report_items'] <= max_items and size <= max_bytes,
                'Modern highlight preview report budget exceeded')
        result['by_file'].append(record)
    evidence._check()
    if result['withheld_source_count'] or result['unpreviewed_diagnostics'] or result['unresolved_reasons_count']:
        result['status'] = 'INCOMPLETE'
    result['report_items'] = items
    # Updating the count can increase its JSON width; count final serialized
    # bytes independently before releasing either part of the batch.
    final = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
    require(len(final.encode('utf-8')) + 2 * final.count('\n')
            + len(evidence_encoded.encode('utf-8')) + 2 * evidence_encoded.count('\n') <= max_bytes,
            'Modern highlight preview report budget exceeded')
    return (diagnostics, result) if return_evidence else result


MATH_CONDITIONS = CONDITIONS + (
    'Runtime uses Typst 0.15.1 standard reviewed math elements and the captured import namespaces.',
    'All nested math calls and arguments retain the verified source bindings and no code side effects.',
)


def preview_reviewed_math_all(evidence, *, max_items=MAX_PREVIEW_ITEMS, max_bytes=MAX_PREVIEW_BYTES,
                              return_evidence=False):
    """Explicit source-only previews using independently verified math calls.

    This is not selected by any existing preview or compiler profile.
    """
    import document_math_builtin_evidence as math_evidence
    require(type(evidence) is modern.ModernHighlightAnalyzer and type(return_evidence) is bool
            and type(max_items) is int and 0 < max_items <= MAX_PREVIEW_ITEMS
            and type(max_bytes) is int and 0 < max_bytes <= MAX_PREVIEW_BYTES,
            'Invalid reviewed math preview request')
    modern_report, math_report = math_evidence.analyze_all(evidence, return_modern=True, max_items=max_items, max_bytes=max_bytes)
    math_by_path = {row['path']: row for row in math_report['by_file']}
    records = load_rules()[0]['rules']
    result = dict(status='CONDITIONAL', by_file=[], conditions=MATH_CONDITIONS, coverage_complete=False,
                  diagnostics_count=0, previewed_diagnostics=0, unpreviewed_diagnostics=0,
                  withheld_source_count=0, unresolved_reasons_count=0, report_items=0,
                  runtime_binding_proved=False, execution_proved=False, render_equivalence_proved=False,
                  edit_permission=False, compiler_permission=False)
    items = 10
    subset_budget = [MAX_SUBSET_CHECKS]
    for row in modern_report['by_file']:
        reviewed = math_by_path.get(row['path'])
        if reviewed is None:
            continue
        result['unresolved_reasons_count'] += (len(reviewed['unresolved'])
                                              + len(reviewed['upstream_unresolved']) + len(reviewed['parse_errors']))
        spans = frozenset((entry['start'], entry['end']) for entry in reviewed['findings'])
        selected = [entry for entry in row['diagnostics'] if (entry['start'], entry['end']) in spans]
        result['diagnostics_count'] += len(selected)
        items += (1 + len(reviewed['findings']) + len(reviewed['unresolved'])
                  + len(reviewed['upstream_unresolved']) + len(reviewed['parse_errors'])
                  + sum(len(x['math_calls']) + len(x['conditions']) for x in reviewed['findings']))
        require(items + modern_report['report_items'] <= max_items, 'Reviewed math preview report item budget exceeded')
        if not selected:
            continue
        source = evidence._session._context.source_for(row['path'])
        record = _preview_source(evidence, source.tree, source.path, selected, records, proven_math_spans=spans)
        if record is None:
            result['unpreviewed_diagnostics'] += len(selected)
            continue
        record = _partial_preview(evidence, source.tree, source.path, selected, records, record,
                                  subset_budget, proven_math_spans=spans)
        record['conditions'] = MATH_CONDITIONS
        if record['verification'] == 'withheld':
            result['withheld_source_count'] += 1
            result['unpreviewed_diagnostics'] += len(selected)
        else:
            result['previewed_diagnostics'] += len(record['edits'])
            result['unpreviewed_diagnostics'] += len(selected) - len(record['edits'])
        items += 1 + len(record['edits']) + len(record['conditions']) + int('reason' in record)
        require(items + modern_report['report_items'] <= max_items, 'Reviewed math preview report item budget exceeded')
        result['by_file'].append(record)
    evidence._check()
    if result['withheld_source_count'] or result['unpreviewed_diagnostics'] or result['unresolved_reasons_count']:
        result['status'] = 'INCOMPLETE'
    result['report_items'] = items
    # Include the separately returned evidence and nesting whitespace in the
    # aggregate byte bound even when the caller only requests the preview.
    def bounded_size(obj):
        encoded = json.dumps(obj, ensure_ascii=True, sort_keys=True, indent=2)
        return len(encoded.encode('utf-8')) + 4 * encoded.count('\n') + 64
    require(sum(bounded_size(x) for x in (modern_report, math_report, result)) <= max_bytes,
            'Reviewed math preview report byte budget exceeded')
    return (modern_report, math_report, result) if return_evidence else result
