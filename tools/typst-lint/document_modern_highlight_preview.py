"""Conditional source-only splitting of very simple modern highlights.

Only the exact reviewed modern helper evidence is admitted. No original
source, runtime binding, render equivalence or compiler authority is implied.
"""
from bisect import bisect_right
import json

import conditional_modern_highlight as modern
from document_display import analyze_all as analyze_displays
from document_gap_preview import _MappedEdits, _scopes
from document_sequence_preview import _same_findings
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
    'Splitting highlighting can change emphasis, spacing and page layout; PDF equivalence is not proved.',
)
_MATH_EXCLUDED = frozenset(('FuncCall', 'MathCall', 'CodeBlock', 'Hash', 'Contextual', 'LetBinding',
                            'ShowRule', 'ModuleImport', 'ModuleInclude', 'Label', 'LineComment', 'BlockComment'))
MAX_PREVIEW_ITEMS = 100_000
MAX_PREVIEW_BYTES = 16 * 1024 * 1024


def _math_signature(tree, root):
    nodes, source = tree.nodes, tree.source
    base = nodes[root].start
    order, pending = [], [root]
    while pending:
        index = pending.pop()
        node = nodes[index]
        if node.kind in _MATH_EXCLUDED:
            return None
        order.append(index)
        pending.extend(reversed(node.children))
    remap = {old: new for new, old in enumerate(order)}
    return tuple((nodes[i].kind, nodes[i].start - base, nodes[i].end - base,
                  nodes[i].display, nodes[i].errors, tuple(remap[c] for c in nodes[i].children),
                  tuple((name, remap[target]) for name, target in nodes[i].operands),
                  source[nodes[i].start:nodes[i].end] if not nodes[i].children else None)
                 for i in order)


def _select(tree, diagnostics):
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
        if len(args) != 1 or nodes[args[0]].kind != 'ContentBlock':
            continue
        block = nodes[args[0]]
        inner = [c for c in block.children if nodes[c].kind == 'Markup']
        if len(inner) != 1:
            continue
        body = nodes[inner[0]]
        children = [nodes[c] for c in body.children]
        if not children or any(c.kind not in ('Text', 'Space', 'Equation') for c in children):
            continue
        parent, position = parents.get(index), positions.get(index, 0)
        if parent is None or nodes[parent].kind != 'Markup' or not position:
            continue
        mark = nodes[nodes[parent].children[position - 1]]
        if mark.kind != 'Hash' or mark.end != node.start:
            continue
        equations = [c for c in body.children if nodes[c].kind == 'Equation']
        signatures = [_math_signature(tree, c) for c in equations]
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
            spans.append(('equation', start, len(output), _math_signature(tree, child_id)))
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
            if kind == 'equation':
                found = by_span.get(('Equation', begin, end), ())
                if len(found) != 1 or parents.get(found[0]) != owner or _math_signature(candidate, found[0]) != signature:
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


def _preview_source(evidence, tree, path, diagnostics, records):
    edits, plans = _select(tree, diagnostics)
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
        before_resolution, after_resolution = resolve(tree, packages=()), resolve(parsed, packages=())
        before_display, after_display = analyze_displays(tree, path, catalog=records), analyze_displays(parsed, path, catalog=records)
        if any(not _same_findings(tree, before_display[rid], parsed, after_display[rid], proposal.edits)
               for rid in before_display):
            raise SafetyError('Highlight split changes display findings')
        if not _same_findings(tree, analyze_scripts(tree, path, catalog=records, _verify=False, _allow_code_call=True),
                              parsed, analyze_scripts(parsed, path, catalog=records, _verify=False, _allow_code_call=True),
                              proposal.edits):
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
