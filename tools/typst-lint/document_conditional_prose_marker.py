"""Independent source-only previews for existing literal prose boundaries."""
from document_display import analyze_all as analyze_displays
from document_gap_preview import _scopes
from document_sequence_preview import _FindingMapper, _keys, _same_findings
from edit_plan import Edit, plan
import continuation_rules
import footnote_rules
import paragraph_rules
import proof_rules
import prose_marker_rules
from script_rule import analyze as analyze_scripts
from static_resolver import resolve
from syntax_client import parse
from typst_lint import InputError, SafetyError, digest, load_rules

RULE = prose_marker_rules.RULE_ID
MARKER = b'#paragraph_tab'
CONDITIONS = (
    'Both source segments execute as ordinary prose paragraphs at this existing boundary.',
    'The inserted paragraph_tab has the intended binding and the intended paragraph show is active.',
    'The marker intentionally changes rendered indentation; execution and rendering are not proved.',
)


def _signature_without_markers(tree, markers):
    """Remove only newly inserted canonical marker token pairs from syntax comparison."""
    from document_gap_preview import _syntax_signature
    if not markers:
        return _syntax_signature(tree)
    expected = set(markers)
    if len(expected) != len(markers):
        raise SafetyError('Duplicate proposed marker positions')
    nodes, source = tree.nodes, tree.source
    removed = set()
    for i, node in enumerate(nodes):
        if node.kind != 'Hash' or node.start not in expected:
            continue
        if (i + 1 >= len(nodes) or nodes[i + 1].kind != 'Ident'
                or nodes[i + 1].start != node.end or node.children or nodes[i + 1].children
                or source[node.start:nodes[i + 1].end] != MARKER):
            raise SafetyError('Inserted marker is not an exact syntax token pair')
        removed.update((i, i + 1))
    if len(removed) != 2 * len(expected):
        raise SafetyError('Inserted marker is missing from parsed candidate')
    kept = [i for i, node in enumerate(nodes) if i not in removed and node.kind not in ('Space', 'Parbreak')]
    remap = {old: new for new, old in enumerate(kept)}
    parents = {child: parent for parent, node in enumerate(nodes) for child in node.children}
    result = []
    for i in kept:
        node = nodes[i]
        parent = parents.get(i)
        if parent is not None and parent not in remap:
            raise SafetyError('Unexpected descendant of inserted marker or whitespace')
        if any(target not in remap for _, target in node.operands):
            raise SafetyError('A syntax operand refers to an inserted marker')
        result.append((node.kind, remap.get(parent), node.display, node.errors,
                       tuple((name, remap[target]) for name, target in node.operands),
                       source[node.start:node.end] if not node.children else None))
    return tuple(result)


def _select(tree, before):
    source = tree.source
    mapper = _FindingMapper(source)
    active = {mapper.offset(item) for item in before.diagnostics}
    edits, anchors = [], []
    for node in tree.nodes:
        if node.kind != 'Text' or node.start not in active:
            continue
        # Do not create a marker directly ahead of unsupported/case-ambiguous text.
        if node.start >= len(source) or not 65 <= source[node.start] <= 90:
            continue
        line_start = source.rfind(b'\n', 0, node.start) + 1
        indent = source[line_start:node.start]
        if any(byte not in b' \t' for byte in indent):
            continue
        newline = b'\r\n' if line_start >= 2 and source[line_start - 2:line_start] == b'\r\n' else b'\n'
        edits.append(Edit(node.start, node.start, MARKER + newline + indent))
        anchors.append(node.start)
    return tuple(edits), tuple(anchors)


def preview(tree, path, *, catalog=None):
    """Propose only marker insertions; bindings, rendering and write authority are not proved."""
    records = load_rules()[0]['rules'] if catalog is None else catalog
    source = tree.source
    empty = dict(path=path, baseline_sha256=digest(source), candidate_sha256=None,
                 conditions=CONDITIONS, runtime_binding_proved=False, execution_proved=False,
                 render_equivalence_proved=False, edit_permission=False, compiler_permission=False, edits=[])
    try:
        if tree.mode != 'markup' or tree.nodes[0].errors:
            raise SafetyError('Requires well-formed markup source')
        before = prose_marker_rules.analyze_conditional(tree, path, catalog=records)
        edits, anchors = _select(tree, before)
        if not edits:
            return None
        proposal = plan(source, edits)
        if proposal.deferred or len(proposal.edits) != len(edits):
            raise SafetyError('Conflicting paragraph marker edits')
        candidate = proposal.candidate()
        parsed = parse(candidate, tree.mode)
        shift = 0
        positions = []
        for edit in proposal.edits:
            positions.append(edit.start + shift)
            shift += len(edit.replacement)
        if (parsed.nodes[0].errors or _signature_without_markers(tree, ())
                != _signature_without_markers(parsed, tuple(positions))
                or _scopes(tree, records, proposal.edits) != _scopes(parsed, records)):
            raise SafetyError('Paragraph marker insertion changes other syntax or suppression targets')
        after = prose_marker_rules.analyze_conditional(parsed, path, catalog=records)
        mapper = _FindingMapper(source)
        selected = [item for item in before.diagnostics if mapper.offset(item) in anchors]
        if len(selected) != len(edits):
            raise SafetyError('Every marker must match one active paragraph diagnostic')
        expected = _keys(source, before.diagnostics, proposal.edits)
        expected.subtract(_keys(source, selected, proposal.edits))
        if (any(count < 0 for count in expected.values()) or +expected != _keys(candidate, after.diagnostics)
                or _keys(source, before.suppressed, proposal.edits) != _keys(candidate, after.suppressed)
                or after.parse_errors):
            raise SafetyError('Paragraph marker insertion changed another finding or suppression')
        displays_before = analyze_displays(tree, path, catalog=records)
        displays_after = analyze_displays(parsed, path, catalog=records)
        if any(not _same_findings(tree, displays_before[rid], parsed, displays_after[rid], proposal.edits)
               for rid in displays_before):
            raise SafetyError('Paragraph marker insertion changed display findings')
        if not _same_findings(tree, analyze_scripts(tree, path, catalog=records, _verify=False, _allow_code_call=True),
                              parsed, analyze_scripts(parsed, path, catalog=records, _verify=False, _allow_code_call=True),
                              proposal.edits):
            raise SafetyError('Paragraph marker insertion changed script findings')
        for analyzer in (paragraph_rules.analyze_conditional_nested_gap,
                         paragraph_rules.analyze_conditional_nested_target,
                         paragraph_rules.analyze_conditional_nested_capitalization,
                         continuation_rules.analyze_conditional):
            if not _same_findings(tree, analyzer(tree, path, catalog=records),
                                  parsed, analyzer(parsed, path, catalog=records), proposal.edits):
                raise SafetyError('Paragraph marker insertion changed another paragraph finding')
        before_resolution, after_resolution = resolve(tree, packages=()), resolve(parsed, packages=())
        footnotes_before = footnote_rules.analyze_conditional_nested(tree, path, before_resolution, catalog=records)
        footnotes_after = footnote_rules.analyze_conditional_nested(parsed, path, after_resolution, catalog=records)
        if any(not _same_findings(tree, footnotes_before[rid], parsed, footnotes_after[rid], proposal.edits)
               for rid in footnote_rules.RULE_IDS):
            raise SafetyError('Paragraph marker insertion changed footnote findings')
        if not _same_findings(tree, proof_rules.analyze_conditional(tree, path, before_resolution, catalog=records),
                              parsed, proof_rules.analyze_conditional(parsed, path, after_resolution, catalog=records),
                              proposal.edits):
            raise SafetyError('Paragraph marker insertion changed proof findings')
        if _select(parsed, after)[0]:
            raise SafetyError('Conditional paragraph marker preview did not converge')
        return dict(empty, candidate_sha256=digest(candidate),
                    verification='conditional-prose-marker-syntax-directives-and-reanalysis',
                    edits=[dict(start=edit.start, end=edit.end, replacement=edit.replacement.decode(), rule_id=RULE)
                           for edit in proposal.edits])
    except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
        return dict(empty, verification='withheld', reason=str(error))
