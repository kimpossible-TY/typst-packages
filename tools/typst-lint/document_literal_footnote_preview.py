"""Independent source-only proposals for literal root footnotes in highlights.

These plans are against the original captured bytes.  They are never applied,
compiled or merged with any other preview plan.
"""
from collections import Counter
import json

import conditional_footnote_binding as binding
import continuation_rules
import document_math_builtin_evidence as math_evidence
import document_modern_highlight_preview as modern_preview
import footnote_rules
import import_graph
import paragraph_rules
import proof_rules
import prose_marker_rules
from document_display import analyze_all as analyze_displays
from document_gap_preview import _MappedEdits, _scopes
from document_sequence_preview import _FindingMapper, _keys, _same_findings
from edit_plan import plan
from script_rule import analyze as analyze_scripts
from static_resolver import resolve
from syntax_client import parse
from typst_lint import InputError, SafetyError, digest, load_rules, require

MAX_CHECKS = 32
CONDITIONS = modern_preview.CONDITIONS + (
    'The captured builtin footnote and imports are selected in the intended runtime context.',
    'The source retains one literal footnote call; runtime execution counts and side effects are not proved.',
    'Custom show rules, reference resolution, footnote numbering and rendering are not proved.',
    'This independent plan is not combined with other preview plans and grants no edit or compiler permission.',
)
_LITERAL_KINDS = frozenset(('ContentBlock', 'LeftBracket', 'RightBracket', 'Markup', 'Text', 'Space',
                            'SmartQuote', 'Ref', 'RefMarker', 'Equation', 'Dollar', 'Math', 'MathText',
                            'MathIdent', 'MathAttach', 'Underscore', 'Hat', 'MathShorthand', 'MathAlignPoint',
                            'LeftParen', 'RightParen', 'Comma', 'Plus', 'Minus', 'Slash', 'Star', 'Prime'))


def _signature(tree, root):
    nodes = tree.nodes
    base = nodes[root].start
    order, pending = [], [root]
    while pending:
        i = pending.pop()
        n = nodes[i]
        order.append(i)
        pending.extend(reversed(n.children))
    remap = {old: new for new, old in enumerate(order)}
    return tuple((nodes[i].kind, nodes[i].start - base, nodes[i].end - base, nodes[i].display,
                  nodes[i].errors, tuple(remap[c] for c in nodes[i].children),
                  tuple((name, remap[c]) for name, c in nodes[i].operands),
                  tree.source[nodes[i].start:nodes[i].end] if not nodes[i].children else None)
                 for i in order)


def _literal(tree, body):
    pending = [body]
    while pending:
        i = pending.pop()
        n = tree.nodes[i]
        if n.kind not in _LITERAL_KINDS or n.errors:
            return False
        if n.kind == 'Ref' and (len(n.children) != 1 or tree.nodes[n.children[0]].kind != 'RefMarker'):
            return False
        pending.extend(n.children)
    return True


def _footnote(tree, diagnostic, issued):
    nodes, source = tree.nodes, tree.source
    parents = {child: i for i, n in enumerate(nodes) for child in n.children}
    matches = [i for i, n in enumerate(nodes) if n.kind == 'FuncCall'
               and (n.start, n.end) == (diagnostic['start'], diagnostic['end'])]
    if len(matches) != 1:
        return None
    highlight = matches[0]
    # Only direct root markup, never closure, show/context or nested wrappers.
    if parents.get(highlight) != 0 or nodes[0].kind != 'Markup':
        return None
    parts = import_graph.significant(tree, highlight)
    if len(parts) != 2:
        return None
    args = import_graph.significant(tree, parts[1])
    blocks = [i for i in args if nodes[i].kind == 'ContentBlock']
    if len(blocks) != 1:
        return None
    body = [i for i in nodes[blocks[0]].children if nodes[i].kind == 'Markup']
    if len(body) != 1:
        return None
    body = body[0]
    calls = [i for i in nodes[body].children if nodes[i].kind == 'FuncCall']
    if len(calls) != 1:
        return None
    call = calls[0]
    n = nodes[call]
    parts = import_graph.significant(tree, call)
    if (len(parts) != 2 or nodes[parts[0]].kind != 'Ident'
            or source[nodes[parts[0]].start:nodes[parts[0]].end] != b'footnote'
            or n.start == 0 or source[n.start - 1:n.start] != b'#'):
        return None
    children = nodes[body].children
    pos = children.index(call)
    if not pos or nodes[children[pos - 1]].kind != 'Hash' or nodes[children[pos - 1]].end != n.start:
        return None
    args = import_graph.significant(tree, parts[1])
    if len(args) != 1 or nodes[args[0]].kind != 'ContentBlock' or not _literal(tree, args[0]):
        return None
    proofs = [p for p in issued.calls if (p.start, p.end, p.source_sha256) ==
              (n.start - 1, n.end, digest(source))]
    if len(proofs) != 1:
        return None
    return call


def _mapped_findings(before_tree, before, after_tree, after, edits, moved):
    mapper = _FindingMapper(before_tree.source, edits)
    def keys(findings):
        out = Counter()
        for f in findings:
            pos = mapper.offset(f)
            owners = [(a, b, target) for a, b, target in moved if a <= pos < b]
            if len(owners) > 1:
                raise SafetyError('Ambiguous moved diagnostic ownership')
            if owners:
                a, _, target = owners[0]
                pos = target + pos - a
            else:
                pos = mapper.mapped(f)
            out[(f.rule_id, f.grade, f.path, pos, f.explanation, f.source)] += 1
        return out
    return (keys(before.diagnostics) == _keys(after_tree.source, after.diagnostics)
            and keys(before.suppressed) == _keys(after_tree.source, after.suppressed))


def _candidate(evidence, tree, path, diagnostic, issued, records):
    call = _footnote(tree, diagnostic, issued)
    if call is None:
        return None
    n = tree.nodes[call]
    edits, plans = modern_preview._select(tree, [diagnostic], proven_literal_footnotes=frozenset({(n.start, n.end)}))
    if len(edits) != 1:
        return None
    proposal = plan(tree.source, edits)
    if proposal.deferred or len(proposal.edits) != 1:
        raise SafetyError('Conflicting highlight split')
    candidate = proposal.candidate()
    parsed = parse(candidate, tree.mode)
    if parsed.nodes[0].errors or _scopes(tree, records, proposal.edits) != _scopes(parsed, records):
        raise SafetyError('Highlight split changed syntax or suppression targets')
    modern_preview._verify_insertions(tree, parsed, proposal.edits, plans)
    edit = edits[0]
    start = _MappedEdits(edits).mapped(edit.start)
    new_end = start + len(edit.replacement)
    expected = tree.source[n.start - 1:n.end]
    matches = [i for i, item in enumerate(parsed.nodes) if item.kind == 'FuncCall'
               and start < item.start and item.end <= new_end
               and parsed.source[item.start - 1:item.end] == expected]
    if len(matches) != 1 or _signature(tree, call) != _signature(parsed, matches[0]):
        raise SafetyError('Footnote subtree, source count or order changed')
    after_call = matches[0]
    # The proof must be re-established with the same sealed capture on the
    # reparsed candidate; unchanged imported sources were checked above.
    engine = math_evidence._engine(evidence._session)
    original = binding._FootnoteResolver(tree, path, engine)
    original.visit(0, original.state)
    engine2 = math_evidence._engine(evidence._session)
    after = binding._FootnoteResolver(parsed, path, engine2)
    after.visit(0, after.state)
    if (call not in original.call_evidence or after_call not in after.call_evidence
            or original.call_evidence[call] != after.call_evidence[after_call]
            or original.unresolved != after.unresolved
            or engine.exhausted or engine2.exhausted):
        raise SafetyError('Footnote binding or captured namespace changed')
    moved = [(n.start - 1, n.end, parsed.nodes[after_call].start - 1)]
    # Only direct highlighted-body equations move out. Equations nested in the
    # preserved footnote are owned by its single copied source interval.
    direct_equations = sorted((item for item in tree.nodes if item.kind == 'Equation'
                               and edit.start < item.start and item.end <= edit.end
                               and not (n.start <= item.start and item.end <= n.end)), key=lambda x: x.start)
    equation_spans = [(a, b) for kind, a, b, _ in plans[0][3] if kind == 'equation']
    if len(direct_equations) != len(equation_spans):
        raise SafetyError('Ambiguous moved equation ownership')
    for old, (a, b) in zip(direct_equations, equation_spans):
        if tree.source[old.start:old.end] != candidate[start + a:start + b]:
            raise SafetyError('Moved equation bytes changed')
        moved.append((old.start, old.end, start + a))
    before_resolution, after_resolution = resolve(tree, packages=()), resolve(parsed, packages=())
    before_display = analyze_displays(tree, path, catalog=records)
    after_display = analyze_displays(parsed, path, catalog=records)
    if any(not _same_findings(tree, before_display[rid], parsed, after_display[rid], edits) for rid in before_display):
        raise SafetyError('Display findings changed')
    before_scripts = analyze_scripts(tree, path, catalog=records, _verify=False, _allow_code_call=True)
    after_scripts = analyze_scripts(parsed, path, catalog=records, _verify=False, _allow_code_call=True)
    if not _mapped_findings(tree, before_scripts, parsed, after_scripts, edits, moved):
        raise SafetyError('Script findings changed')
    before_notes = footnote_rules.analyze_conditional_nested(tree, path, before_resolution, catalog=records)
    after_notes = footnote_rules.analyze_conditional_nested(parsed, path, after_resolution, catalog=records)
    if any(not _mapped_findings(tree, before_notes[rid], parsed, after_notes[rid], edits, moved)
           for rid in footnote_rules.RULE_IDS):
        raise SafetyError('Footnote findings changed')
    for analyzer in (paragraph_rules.analyze_conditional_nested_gap, paragraph_rules.analyze_conditional_nested_target,
                     paragraph_rules.analyze_conditional_nested_capitalization, prose_marker_rules.analyze_conditional,
                     continuation_rules.analyze_conditional):
        if not _same_findings(tree, analyzer(tree, path, catalog=records),
                              parsed, analyzer(parsed, path, catalog=records), edits):
            raise SafetyError('Paragraph findings changed')
    if not _same_findings(tree, proof_rules.analyze_conditional(tree, path, before_resolution, catalog=records),
                          parsed, proof_rules.analyze_conditional(parsed, path, after_resolution, catalog=records), edits):
        raise SafetyError('Proof findings changed')
    evidence._check()
    return dict(path=path, baseline_sha256=digest(tree.source), candidate_sha256=digest(candidate),
                verification='conditional-literal-footnote-source-only',
                edits=[dict(start=edit.start, end=edit.end, replacement=edit.replacement.decode('utf-8'),
                            rule_id=modern_preview.RULE)], conditions=CONDITIONS,
                runtime_binding_proved=False, execution_proved=False, render_equivalence_proved=False,
                edit_permission=False, compiler_permission=False)


def preview_all(evidence, *, max_items=modern_preview.MAX_PREVIEW_ITEMS,
                max_bytes=modern_preview.MAX_PREVIEW_BYTES, return_evidence=False):
    """Return independent, individually verified plans; never compose edits."""
    if type(evidence) is not modern_preview.modern.ModernHighlightAnalyzer:
        raise InputError('Issued modern highlight evidence is required')
    require(type(return_evidence) is bool and type(max_items) is int and 0 < max_items <= modern_preview.MAX_PREVIEW_ITEMS
            and type(max_bytes) is int and 0 < max_bytes <= modern_preview.MAX_PREVIEW_BYTES,
            'Invalid literal footnote preview budget')
    evidence._check()
    diagnostics = evidence.analyze_all(max_items=max_items, max_bytes=max_bytes)
    records = load_rules()[0]['rules']
    evidence_json = json.dumps(diagnostics, ensure_ascii=False, sort_keys=True, indent=2)
    evidence_bytes = len(evidence_json.encode('utf-8')) + 2 * evidence_json.count('\n')
    result = dict(status='CONDITIONAL' if diagnostics['status'] == 'CONDITIONAL' else 'INCOMPLETE',
                  upstream_status=diagnostics['status'], plans=[], withheld=[], conditions=CONDITIONS,
                  independent_plans=True, coverage_complete=False, truncated=False, report_items=0,
                  runtime_binding_proved=False, execution_proved=False, render_equivalence_proved=False,
                  edit_permission=False, compiler_permission=False)
    checks = 0
    def check_budget():
        count = 1 + len(result['conditions']) + sum(1 + len(p['edits']) + len(p['conditions'])
                                                    for p in result['plans']) + len(result['withheld'])
        require(count + diagnostics['report_items'] <= max_items,
                'Literal footnote preview report item limit exceeded')
        result['report_items'] = count
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
        require(len(encoded.encode('utf-8')) + 2 * encoded.count('\n') + evidence_bytes <= max_bytes,
                'Literal footnote preview report byte limit exceeded')
    check_budget()
    for row in diagnostics['by_file']:
        source = evidence._session._context.source_for(row['path'])
        candidates = [d for d in row['diagnostics'] if b'#footnote' in source.tree.source[d['start']:d['end']]]
        if not candidates:
            continue
        if checks >= MAX_CHECKS:
            result['withheld'].append(dict(path=row['path'], start=candidates[0]['start'],
                                           reason='Literal footnote preview work budget exceeded'))
            result['status'] = 'INCOMPLETE'
            result['truncated'] = True
            check_budget()
            break
        issued = evidence._session.analyze(row['path'])
        for diagnostic in candidates:
            checks += 1
            if checks > MAX_CHECKS:
                result['withheld'].append(dict(path=row['path'], start=diagnostic['start'],
                                               reason='Literal footnote preview work budget exceeded'))
                result['status'] = 'INCOMPLETE'
                result['truncated'] = True
                check_budget()
                break
            try:
                if issued.status != 'CONDITIONAL':
                    raise SafetyError('Captured footnote binding is unresolved')
                record = _candidate(evidence, source.tree, source.path, diagnostic, issued, records)
                if record is None:
                    raise SafetyError('Literal root footnote shape or binding is unsupported')
                result['plans'].append(record)
            except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
                result['withheld'].append(dict(path=row['path'], start=diagnostic['start'], reason=str(error)))
                result['status'] = 'INCOMPLETE'
            check_budget()
        if result['truncated']:
            break
    evidence._check()
    if result['withheld']:
        result['status'] = 'INCOMPLETE'
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
    require(len(encoded.encode('utf-8')) + 2 * encoded.count('\n') + evidence_bytes <= max_bytes,
            'Literal footnote preview report byte limit exceeded')
    return (diagnostics, result) if return_evidence else result
