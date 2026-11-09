"""Bounded independent capitalization stage; runtime and rendering are unproved."""
import re
from document_composed_prose_footnote_gap import _within_budget
from document_sequence_preview import _FindingMapper, _preserve_unresolved_paragraph_boundaries
from edit_plan import Edit, plan
import document_conditional_capitalization as underscore
import document_hyphen_marker_capitalization as hyphen
import document_conditional_hyphen_marker as marker
import paragraph_rules
import import_graph
from conditional_highlight import _module_alias, _combined_import
from syntax_client import parse
from typst_lint import SafetyError, digest


def _marker_shadow(tree, name):
    """Reject source-local marker definitions; wildcard binding remains conditional."""
    nodes, source = tree.nodes, tree.source
    encoded_name = name.encode('ascii')
    trivia = frozenset(('Space', 'Parbreak', 'LineComment', 'BlockComment'))
    marked = set()
    pending = [(0, False)]
    while pending:
        i, inherited = pending.pop()
        node = nodes[i]
        if node.kind == 'ModuleImport':
            module = _module_alias(tree, i)
            if module is not None and module[1] == name:
                return True
            shape = import_graph.import_shape(tree, i)
            if shape is None:
                combined = _combined_import(tree, i)
                if combined is not None:
                    shape, alias = combined
                    if alias == name:
                        return True
                else:
                    # Unknown import shapes with a relevant identifier are not
                    # assumed to be a harmless same-name direct import.
                    queue = list(node.children)
                    while queue:
                        child = nodes[queue.pop()]
                        if child.kind == 'Ident' and source[child.start:child.end] == encoded_name:
                            return True
                        queue.extend(child.children)
            if shape is not None and shape.items is not None:
                if any(local == name and original != local for original, local in shape.items):
                    return True
        inherited = inherited or i in marked
        if inherited and node.kind == 'Ident' and source[node.start:node.end] == encoded_name:
            return True
        if node.kind in ('LetBinding', 'ForLoop'):
            parts = [x for x in node.children if nodes[x].kind not in trivia]
            if len(parts) >= 2:
                target = parts[1]
                if node.kind == 'LetBinding' and nodes[target].kind == 'Closure':
                    head = [x for x in nodes[target].children if nodes[x].kind not in trivia]
                    if head:
                        marked.add(head[0])
                else:
                    marked.add(target)
        elif node.kind == 'Closure':
            marked.update(x for x in node.children if nodes[x].kind == 'Params')
        elif node.kind in ('Binary', 'DestructAssignment'):
            children = [x for x in node.children if nodes[x].kind not in trivia]
            if children and (node.kind == 'DestructAssignment' or any(nodes[x].kind in
                ('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq') for x in children)):
                marked.add(children[0])
        pending.extend((child, inherited) for child in reversed(node.children))
    return False


def _literal_targets(tree, catalog, spelling):
    """Recompute direct marker/text ownership and suppressions without hint rows."""
    from suppression import resolve as resolve_suppressions
    nodes, source = tree.nodes, tree.source
    token = b'paragraph_tab' if spelling == 'underscore' else b'paragraph-tab'
    parents = {child: index for index, node in enumerate(nodes) for child in node.children}
    excluded = {}
    for index, node in enumerate(nodes):
        parent = parents.get(index)
        excluded[index] = parent is not None and (excluded[parent]
            or nodes[parent].kind in paragraph_rules._CONDITIONAL_EXCLUDED)
    scopes = resolve_suppressions(tree, {row['id'] for row in catalog})
    targets = []
    for body_id, body in enumerate(nodes):
        if body_id == 0 or body.kind != 'Markup' or excluded[body_id]:
            continue
        siblings = body.children
        for position in range(1, len(siblings)):
            node, mark = nodes[siblings[position]], nodes[siblings[position - 1]]
            if (node.kind != 'Ident' or source[node.start:node.end] != token
                    or mark.kind != 'Hash' or mark.end != node.start):
                continue
            line_start = source.rfind(b'\n', 0, mark.start) + 1
            if source[line_start:mark.start].strip(b' \t'):
                continue
            next_position = position + 1
            if next_position < len(siblings) and nodes[siblings[next_position]].kind == 'Space':
                next_position += 1
            if next_position >= len(siblings):
                continue
            first = nodes[siblings[next_position]]
            if (first.kind != 'Text'
                    or re.fullmatch(rb'[ \t]*\r?\n[ \t]*', source[node.end:first.start]) is None):
                continue
            word = re.match(rb'[a-z]+', source[first.start:first.end])
            if (word is None or len(word[0]) < 2 or word[0] not in paragraph_rules.WORDS
                    or source[first.start + len(word[0]):first.start + len(word[0]) + 1]
                        not in (b' ', b'\t', b'\r', b'\n')):
                continue
            if any(scope.covers(paragraph_rules.CAPITALIZATION, mark.start)
                   or scope.covers(paragraph_rules.CAPITALIZATION, first.start) for scope in scopes):
                continue
            targets.append(first.start)
    return targets


def _select(tree, path, catalog, *, spelling):
    """Independently select bounded, unsuppressed exact-byte targets."""
    if spelling not in ('underscore', 'hyphen'):
        raise SafetyError('Invalid capitalization marker spelling')
    source = tree.source
    token = b'#paragraph_tab' if spelling == 'underscore' else b'#paragraph-tab'
    if token not in source or not _within_budget(tree):
        return None
    if spelling == 'underscore':
        if _marker_shadow(tree, 'paragraph_tab'):
            return None
        before = paragraph_rules.analyze_conditional_nested_capitalization(tree, path, catalog=catalog)
        mapping = _FindingMapper(source)
        selected = [mapping.offset(row) for row in before.diagnostics]
        module = underscore
    else:
        if _marker_shadow(tree, 'paragraph-tab'):
            return None
        before = marker.analyze(source, path, catalog=catalog)
        if (before['status'] != 'PARTIAL' or any('explicit paragraph-tab shadow' in reason
                for reason in before['unresolved'])):
            return None
        selected = [row['start'] for row in before['diagnostics'] if row['rule_id'] == hyphen.RULE]
        module = hyphen
    expected = _literal_targets(tree, catalog, spelling)
    if selected != expected:
        raise SafetyError('Capitalization hint target differs from literal marker ownership')
    if not selected:
        return None
    edits = tuple(Edit(pos, pos + 1, source[pos:pos+1].upper()) for pos in selected)
    if any(not b'a' <= source[e.start:e.end] <= b'z' for e in edits):
        raise SafetyError('Invalid capitalization target')
    proposed = plan(source, edits)
    if proposed.deferred or len(proposed.edits) != len(edits):
        raise SafetyError('Capitalization targets overlap')
    return proposed


def preview(tree, path, catalog, *, spelling):
    """Reissue the selected proof against this exact intermediate source."""
    proposed = _select(tree, path, catalog, spelling=spelling)
    if proposed is None:
        return None
    source = tree.source
    module = underscore if spelling == 'underscore' else hyphen
    candidate = proposed.candidate()
    parsed = parse(candidate, tree.mode)
    if parsed.nodes[0].errors or not _within_budget(parsed):
        return None
    proof = module.preview(tree, path, catalog=catalog)
    if proof is None:
        raise SafetyError('Capitalization source proof omitted selected edits')
    if proof.get('verification') == 'withheld':
        return None
    expected_verification = ('conditional-capitalization-syntax-directives-and-reanalysis' if spelling == 'underscore'
                             else 'conditional-hyphen-capitalization-syntax-directives-and-reanalysis')
    if (proof.get('verification') != expected_verification or proof.get('conditions') != module.CONDITIONS
            or any(proof.get(k) is not False for k in ('runtime_binding_proved', 'execution_proved',
                    'render_equivalence_proved', 'edit_permission', 'compiler_permission'))):
        raise SafetyError('Capitalization source proof invalid')
    expected_rows = [dict(start=e.start, end=e.end, replacement=e.replacement.decode('ascii'),
                          rule_id=paragraph_rules.CAPITALIZATION) for e in proposed.edits]
    if (proof.get('edits') != expected_rows or proof.get('baseline_sha256') != digest(source)
            or proof.get('candidate_sha256') != digest(candidate)):
        raise SafetyError('Capitalization source proof changed')
    try:
        _preserve_unresolved_paragraph_boundaries(tree, parsed, path, catalog, proposed.edits)
    except SafetyError:
        return None
    final = module.preview(parsed, path, catalog=catalog)
    if final is not None:
        raise SafetyError('Capitalization candidate did not converge')
    return dict(rule_id=paragraph_rules.CAPITALIZATION, baseline_sha256=digest(source),
                candidate_sha256=digest(candidate), edits=[dict(row, conditions=list(module.CONDITIONS),
                runtime_binding_proved=False, execution_proved=False, render_equivalence_proved=False,
                edit_permission=False, compiler_permission=False) for row in expected_rows])


def verify_transition(before, after, path, catalog, edits, spelling):
    """Independently verify the stage's own candidate and other diagnostics."""
    from document_conditional_continuation import _nontext_signature
    from document_display import analyze_all as displays
    from document_gap_preview import _scopes
    from document_sequence_preview import _keys, _same_findings
    from script_rule import analyze as scripts
    from static_resolver import resolve
    import continuation_rules
    import footnote_rules
    import proof_rules
    import prose_marker_rules

    if (after.nodes[0].errors or _nontext_signature(before) != _nontext_signature(after)
            or _scopes(before, catalog, edits) != _scopes(after, catalog)):
        raise SafetyError('Capitalization changed syntax or suppression ownership')
    if spelling == 'underscore':
        left = paragraph_rules.analyze_conditional_nested_capitalization(before, path, catalog=catalog)
        right = paragraph_rules.analyze_conditional_nested_capitalization(after, path, catalog=catalog)
        if right.diagnostics or right.parse_errors or _keys(before.source, left.suppressed, edits) != _keys(after.source, right.suppressed):
            raise SafetyError('Capitalization findings or suppression changed')
    else:
        left = marker.analyze(before.source, path, catalog=catalog)
        right = marker.analyze(after.source, path, catalog=catalog)
        selected = [r for r in left['diagnostics'] if r['rule_id'] == hyphen.RULE]
        expected = hyphen._keys(left['diagnostics']); expected.subtract(hyphen._keys(selected))
        if (right['status'] != 'PARTIAL' or left['unresolved'] != right['unresolved']
                or +expected != hyphen._keys(right['diagnostics'])
                or hyphen._keys(left['suppressed']) != hyphen._keys(right['suppressed'])):
            raise SafetyError('Hyphen marker findings or suppressions changed')
    lhs, rhs = displays(before, path, catalog=catalog), displays(after, path, catalog=catalog)
    if any(not _same_findings(before, lhs[r], after, rhs[r], edits) for r in lhs):
        raise SafetyError('Capitalization changed display findings')
    if not _same_findings(before, scripts(before, path, catalog=catalog, _verify=False, _allow_code_call=True),
                          after, scripts(after, path, catalog=catalog, _verify=False, _allow_code_call=True), edits):
        raise SafetyError('Capitalization changed script findings')
    old_res, new_res = resolve(before, packages=()), resolve(after, packages=())
    old_notes = footnote_rules.analyze_conditional_nested(before, path, old_res, catalog=catalog)
    new_notes = footnote_rules.analyze_conditional_nested(after, path, new_res, catalog=catalog)
    if any(not _same_findings(before, old_notes[r], after, new_notes[r], edits) for r in footnote_rules.RULE_IDS):
        raise SafetyError('Capitalization changed footnote findings')
    analyzers = [paragraph_rules.analyze_conditional_nested_gap, paragraph_rules.analyze_conditional_nested_target,
                 prose_marker_rules.analyze_conditional, continuation_rules.analyze_conditional]
    if spelling == 'hyphen':
        analyzers.append(paragraph_rules.analyze_conditional_nested_capitalization)
    for analyze in analyzers:
        if not _same_findings(before, analyze(before, path, catalog=catalog),
                              after, analyze(after, path, catalog=catalog), edits):
            raise SafetyError('Capitalization changed other paragraph findings')
    if not _same_findings(before, proof_rules.analyze_conditional(before, path, old_res, catalog=catalog),
                          after, proof_rules.analyze_conditional(after, path, new_res, catalog=catalog), edits):
        raise SafetyError('Capitalization changed proof findings')
    _preserve_unresolved_paragraph_boundaries(before, after, path, catalog, edits)
