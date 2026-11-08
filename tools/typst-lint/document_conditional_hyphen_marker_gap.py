"""Source-only blank-line previews for hyphenated paragraph marker hints."""
from collections import Counter

import continuation_rules
import document_conditional_hyphen_marker as hints
from document_display import analyze_all as analyze_displays
from document_gap_preview import _MappedEdits, _scopes, _syntax_signature
from document_sequence_preview import _same_findings
from edit_plan import Edit, plan
import footnote_rules
import paragraph_rules
import proof_rules
import prose_marker_rules
from script_rule import analyze as analyze_scripts
from static_resolver import resolve
from syntax_client import parse
from typst_lint import InputError, SafetyError, digest, load_rules

RULE = paragraph_rules.MARKER_GAP
CONDITIONS = (
    'The literal paragraph-tab resolves to the intended paragraph marker at runtime.',
    'The surrounding source executes as the intended prose with its intended show rule.',
    'The added source blank line may change rendering; execution and equivalence are unproved.',
)
_SAFE_PRECEDING = frozenset(('Text', 'Heading', 'Equation', 'ListItem', 'EnumItem', 'TermItem'))


def _select(tree, diagnostics):
    source, nodes = tree.source, tree.nodes
    markers = {row['marker_start']: row for row in diagnostics if row['rule_id'] == RULE}
    parents = {child: (index, position) for index, node in enumerate(nodes)
               for position, child in enumerate(node.children)}
    edits, selected, unresolved = [], [], []
    for index, mark in enumerate(nodes):
        if mark.kind != 'Hash' or mark.start not in markers or index not in parents:
            continue
        parent, position = parents[index]
        body = nodes[parent]
        if body.kind != 'Markup' or position + 1 >= len(body.children):
            raise SafetyError('Hyphen marker is not in the expected markup body')
        ident = nodes[body.children[position + 1]]
        if (ident.kind != 'Ident' or mark.end != ident.start
                or source[mark.start:ident.end] != b'#paragraph-tab'):
            raise SafetyError('Hyphen marker syntax changed')
        previous = position - 1
        while previous >= 0 and nodes[body.children[previous]].kind in ('Space', 'Parbreak'):
            previous -= 1
        if previous >= 0 and nodes[body.children[previous]].kind not in _SAFE_PRECEDING:
            unresolved.append(dict(marker_start=mark.start,
                reason='Preceding expression is not known to exclude a footnote or other ambiguous output'))
            continue
        line_start = source.rfind(b'\n', 0, mark.start) + 1
        if source[line_start:mark.start].strip(b' \t'):
            raise SafetyError('Hyphen marker is no longer standalone')
        newline = b'\r\n' if source[max(0, line_start - 2):line_start] == b'\r\n' else b'\n'
        edits.append(Edit(line_start, line_start, newline))
        selected.append(markers[mark.start])
    if len(selected) + len(unresolved) != len(markers):
        raise SafetyError('Not all hyphen marker hints matched unique parsed tokens')
    return tuple(edits), tuple(selected), tuple(unresolved)


def _keys(rows, edits=()):
    mapper = _MappedEdits(edits)
    return Counter((row['rule_id'], row['grade'], row['path'], mapper.mapped(row['start']),
                    mapper.mapped(row['end']), mapper.mapped(row['marker_start']),
                    row['marker_spelling'], row['source'], row['explanation']) for row in rows)


def preview(tree, path, *, catalog=None):
    """Preview independent blank-line insertions without proving runtime bindings."""
    records = load_rules()[0]['rules'] if catalog is None else catalog
    source = tree.source
    empty = dict(path=path, baseline_sha256=digest(source), candidate_sha256=None,
                 conditions=CONDITIONS, runtime_binding_proved=False, execution_proved=False,
                 render_equivalence_proved=False, edit_permission=False, compiler_permission=False,
                 unresolved=[], edits=[])
    try:
        if tree.mode != 'markup' or tree.nodes[0].errors:
            raise SafetyError('Requires well-formed markup source')
        before = hints.analyze(source, path, catalog=records)
        if before['status'] == 'UNAVAILABLE':
            raise SafetyError('Hyphen marker analysis is unavailable')
        edits, selected, unresolved = _select(tree, before['diagnostics'])
        empty['unresolved'] = list(unresolved)
        if not edits:
            return dict(empty, verification='withheld', reason='All candidate marker boundaries are ambiguous') if unresolved else None
        proposal = plan(source, edits)
        if proposal.deferred or len(proposal.edits) != len(edits):
            raise SafetyError('Conflicting hyphen marker gap edits')
        candidate = proposal.candidate()
        parsed = parse(candidate, tree.mode)
        if (parsed.nodes[0].errors or _syntax_signature(tree) != _syntax_signature(parsed)
                or _scopes(tree, records, proposal.edits) != _scopes(parsed, records)):
            raise SafetyError('Marker gap insertion changes syntax or suppression targets')
        after = hints.analyze(candidate, path, catalog=records)
        if after['status'] == 'UNAVAILABLE':
            raise SafetyError('Hyphen marker reanalysis is unavailable')
        expected = _keys(before['diagnostics'], proposal.edits)
        expected.subtract(_keys(selected, proposal.edits))
        if (any(count < 0 for count in expected.values()) or +expected != _keys(after['diagnostics'])
                or _keys(before['suppressed'], proposal.edits) != _keys(after['suppressed'])):
            raise SafetyError('Marker gap insertion changed other hyphen marker findings or suppressions')
        displays_before = analyze_displays(tree, path, catalog=records)
        displays_after = analyze_displays(parsed, path, catalog=records)
        if any(not _same_findings(tree, displays_before[rid], parsed, displays_after[rid], proposal.edits)
               for rid in displays_before):
            raise SafetyError('Marker gap insertion changed display findings')
        if not _same_findings(tree, analyze_scripts(tree, path, catalog=records, _verify=False, _allow_code_call=True),
                              parsed, analyze_scripts(parsed, path, catalog=records, _verify=False, _allow_code_call=True),
                              proposal.edits):
            raise SafetyError('Marker gap insertion changed script findings')
        before_resolution, after_resolution = resolve(tree, packages=()), resolve(parsed, packages=())
        footnotes_before = footnote_rules.analyze_conditional_nested(tree, path, before_resolution, catalog=records)
        footnotes_after = footnote_rules.analyze_conditional_nested(parsed, path, after_resolution, catalog=records)
        if any(not _same_findings(tree, footnotes_before[rid], parsed, footnotes_after[rid], proposal.edits)
               for rid in footnote_rules.RULE_IDS):
            raise SafetyError('Marker gap insertion changed footnote findings')
        for analyzer in (paragraph_rules.analyze_conditional_nested_gap,
                         paragraph_rules.analyze_conditional_nested_target,
                         paragraph_rules.analyze_conditional_nested_capitalization,
                         prose_marker_rules.analyze_conditional, continuation_rules.analyze_conditional):
            if not _same_findings(tree, analyzer(tree, path, catalog=records),
                                  parsed, analyzer(parsed, path, catalog=records), proposal.edits):
                raise SafetyError('Marker gap insertion changed other paragraph findings')
        if not _same_findings(tree, proof_rules.analyze_conditional(tree, path, before_resolution, catalog=records),
                              parsed, proof_rules.analyze_conditional(parsed, path, after_resolution, catalog=records),
                              proposal.edits):
            raise SafetyError('Marker gap insertion changed proof findings')
        if _select(parsed, after['diagnostics'])[0]:
            raise SafetyError('Hyphen marker gap preview did not converge')
        return dict(empty, candidate_sha256=digest(candidate),
                    verification='conditional-hyphen-marker-gap-syntax-directives-and-reanalysis',
                    edits=[dict(start=edit.start, end=edit.end, replacement=edit.replacement.decode(), rule_id=RULE)
                           for edit in proposal.edits])
    except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
        return dict(empty, verification='withheld', reason=str(error))
