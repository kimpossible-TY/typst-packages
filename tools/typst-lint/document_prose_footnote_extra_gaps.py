"""Conditional prose-footnote gaps before a hyphenated marker or a figure.

These independent proposals make no runtime binding, rendering, compilation or
source-write claim. Existing underscore-marker and display cases are separate.
"""
from conditional_highlight import _module_alias
from document_conditional_marker_tags import _has_name
from document_display import analyze_all as analyze_displays
from document_gap_preview import _scopes
from document_nested_prose_footnote_gap import _has_explicit_shadow, _signature, any_kind_ancestor
from document_sequence_preview import _FindingMapper, _keys, _same_findings
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
from typst_lint import InputError, SafetyError, digest, load_rules

RULE = footnote_rules.GAP
CONDITIONS = (
    'The footnote and parbreak calls resolve to the intended built-in functions.',
    'For a marker, paragraph-tab resolves to the intended marker and its show rule applies.',
    'For a figure, the source figure call resolves to the expected block-producing figure.',
    'The source executes as prose with the analyzed bytes and intended paragraph boundary.',
    'The inserted paragraph break can change rendering; equivalence and write authority are not proved.',
)
_NAMES = ('paragraph-tab', 'figure')
_ASSIGNMENTS = frozenset(('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq'))


def _extra_shadow(tree):
    nodes = tree.nodes
    def targeted(index):
        return any(_has_name(tree, index, name) for name in _NAMES)
    for index, node in enumerate(nodes):
        if node.kind in ('ModuleImport', 'RenamedImportItem'):
            parts = import_graph.significant(tree, index)
            if node.kind == 'ModuleImport':
                module = _module_alias(tree, index)
                if module is not None and module[1] in _NAMES:
                    return True
                shape = import_graph.import_shape(tree, index)
                if shape is not None and shape.items is not None:
                    for original, local in shape.items:
                        if local == 'figure' or (local == 'paragraph-tab' and original != local):
                            return True
                # Combined module-and-items imports are not represented by
                # import_shape; an explicit figure item can shadow the builtin.
                for part in parts:
                    if nodes[part].kind != 'ImportItems':
                        continue
                    for item in import_graph.significant(tree, part):
                        if nodes[item].kind != 'ImportItemPath':
                            continue
                        components = import_graph.significant(tree, item)
                        identifiers = [child for child in components if nodes[child].kind == 'Ident']
                        if identifiers and _has_name(tree, identifiers[-1], 'figure'):
                            return True
            for part, target in zip(parts, parts[1:]):
                if nodes[part].kind == 'As' and nodes[target].kind == 'Ident' and targeted(target):
                    return True
            continue
        parts = import_graph.significant(tree, index) if node.kind in (
            'LetBinding', 'Closure', 'ForLoop', 'Binary', 'DestructAssignment') else ()
        if node.kind == 'LetBinding' and len(parts) >= 2:
            target = parts[1]
            if nodes[target].kind == 'Closure':
                inner = import_graph.significant(tree, target)
                target = inner[0] if inner else target
            if targeted(target):
                return True
        elif node.kind == 'Closure':
            if any(nodes[part].kind == 'Params' and targeted(part) for part in parts):
                return True
        elif node.kind == 'ForLoop':
            for part in parts[1:]:
                if nodes[part].kind == 'In':
                    break
                if targeted(part):
                    return True
        elif node.kind == 'Binary' and parts and any(nodes[part].kind in _ASSIGNMENTS for part in parts):
            if targeted(parts[0]):
                return True
        elif node.kind == 'DestructAssignment' and parts and targeted(parts[0]):
            return True
    return False


def _select(tree, notes):
    nodes, source = tree.nodes, tree.source
    mapper = _FindingMapper(source)
    diagnosed = {mapper.offset(item): item for item in notes[RULE].diagnostics}
    parents = {child: (index, position) for index, node in enumerate(nodes)
               for position, child in enumerate(node.children)}
    edits, targets, kinds = [], [], []
    for index, call in enumerate(nodes):
        if (call.kind != 'FuncCall' or not call.start or source[call.start - 1:call.start] != b'#'
                or call.start - 1 not in diagnosed or index not in parents):
            continue
        parent, position = parents[index]
        body = nodes[parent]
        if body.kind != 'Markup' or any_kind_ancestor(nodes, parents, parents.get(parent), {'Equation', 'Math'}):
            continue
        gap = footnote_rules._BLANK_AFTER_FOOTNOTE.match(source, call.end, body.end)
        if gap is None:
            continue
        following = position + 1
        while following < len(body.children):
            item = nodes[body.children[following]]
            if item.kind not in ('Space', 'Parbreak') or source[item.start:item.end].strip(b' \t\r\n'):
                break
            following += 1
        if following + 1 >= len(body.children):
            continue
        mark, next_node = nodes[body.children[following]], nodes[body.children[following + 1]]
        if (mark.kind != 'Hash' or mark.end != next_node.start or mark.start < gap.end()
                or source[gap.end():mark.start].strip(b' \t')):
            continue
        if next_node.kind == 'Ident' and source[next_node.start:next_node.end] == b'paragraph-tab':
            line_end = source.find(b'\n', next_node.end, body.end)
            if line_end < 0 or source[next_node.end:line_end].strip(b' \t\r'):
                continue
            after = following + 2
            if (after + 1 >= len(body.children) or nodes[body.children[after]].kind != 'Space'
                    or nodes[body.children[after + 1]].kind != 'Text'):
                continue
            kind = 'hyphenated-paragraph-marker'
        elif next_node.kind == 'FuncCall':
            parts = import_graph.significant(tree, body.children[following + 1])
            if (len(parts) != 2 or nodes[parts[0]].kind != 'Ident' or nodes[parts[1]].kind != 'Args'
                    or source[nodes[parts[0]].start:nodes[parts[0]].end] != b'figure'):
                continue
            kind = 'figure'
        else:
            continue
        newline = gap.group(1)
        edits.append(Edit(call.end + len(newline), call.end + len(newline), b'#parbreak()' + newline))
        targets.append(diagnosed[call.start - 1])
        kinds.append(kind)
    return tuple(edits), tuple(targets), tuple(kinds)


def preview(tree, path, *, catalog=None):
    records = load_rules()[0]['rules'] if catalog is None else catalog
    source = tree.source
    empty = dict(path=path, baseline_sha256=digest(source), candidate_sha256=None,
                 conditions=CONDITIONS, runtime_binding_proved=False, execution_proved=False,
                 render_equivalence_proved=False, edit_permission=False, compiler_permission=False, edits=[])
    try:
        if tree.mode != 'markup' or tree.nodes[0].errors:
            raise SafetyError('Requires well-formed markup source')
        before_resolution = resolve(tree, packages=())
        before = footnote_rules.analyze_conditional_nested(tree, path, before_resolution, catalog=records)
        edits, selected, kinds = _select(tree, before)
        if not edits:
            return None
        if _has_explicit_shadow(tree) or _extra_shadow(tree):
            raise SafetyError('Explicit footnote, parbreak, marker or figure shadow')
        proposal = plan(source, edits)
        if proposal.deferred or len(proposal.edits) != len(edits) or len(selected) != len(edits):
            raise SafetyError('Conflicting prose footnote gap edits')
        candidate = proposal.candidate()
        parsed = parse(candidate, tree.mode)
        shift, inserted = 0, []
        for edit in proposal.edits:
            start = edit.start + shift
            inserted.append((start, start + len(b'#parbreak()')))
            shift += len(edit.replacement)
        if (parsed.nodes[0].errors or _signature(tree) != _signature(parsed, inserted)
                or _scopes(tree, records, proposal.edits) != _scopes(parsed, records)):
            raise SafetyError('Prose break changed other syntax or suppression targets')
        after_resolution = resolve(parsed, packages=())
        after = footnote_rules.analyze_conditional_nested(parsed, path, after_resolution, catalog=records)
        for rule_id in footnote_rules.RULE_IDS:
            expected = _keys(source, before[rule_id].diagnostics, proposal.edits)
            if rule_id == RULE:
                expected.subtract(_keys(source, selected, proposal.edits))
            if (any(count < 0 for count in expected.values()) or +expected != _keys(candidate, after[rule_id].diagnostics)
                    or _keys(source, before[rule_id].suppressed, proposal.edits)
                    != _keys(candidate, after[rule_id].suppressed) or after[rule_id].parse_errors):
                raise SafetyError('Prose break changed other footnote findings or suppressions')
        displays_before, displays_after = analyze_displays(tree, path, catalog=records), analyze_displays(parsed, path, catalog=records)
        if any(not _same_findings(tree, displays_before[rid], parsed, displays_after[rid], proposal.edits)
               for rid in displays_before):
            raise SafetyError('Prose break changed display findings')
        if not _same_findings(tree, analyze_scripts(tree, path, catalog=records, _verify=False, _allow_code_call=True),
                              parsed, analyze_scripts(parsed, path, catalog=records, _verify=False, _allow_code_call=True),
                              proposal.edits):
            raise SafetyError('Prose break changed script findings')
        for analyzer in (paragraph_rules.analyze_conditional_nested_gap, paragraph_rules.analyze_conditional_nested_target,
                         paragraph_rules.analyze_conditional_nested_capitalization, prose_marker_rules.analyze_conditional,
                         continuation_rules.analyze_conditional):
            if not _same_findings(tree, analyzer(tree, path, catalog=records),
                                  parsed, analyzer(parsed, path, catalog=records), proposal.edits):
                raise SafetyError('Prose break changed paragraph findings')
        if not _same_findings(tree, proof_rules.analyze_conditional(tree, path, before_resolution, catalog=records),
                              parsed, proof_rules.analyze_conditional(parsed, path, after_resolution, catalog=records),
                              proposal.edits):
            raise SafetyError('Prose break changed proof findings')
        if _select(parsed, after)[0]:
            raise SafetyError('Conditional prose break preview did not converge')
        kinds_by_start = {edit.start: kind for edit, kind in zip(edits, kinds)}
        return dict(empty, candidate_sha256=digest(candidate),
                    verification='conditional-prose-extra-gap-syntax-directives-and-reanalysis',
                    edits=[dict(start=edit.start, end=edit.end, replacement=edit.replacement.decode(), rule_id=RULE,
                                following_kind=kinds_by_start[edit.start],
                                following_conditions=(
                                    ['The literal paragraph-tab binding and paragraph show rule are the intended ones.']
                                    if kinds_by_start[edit.start] == 'hyphenated-paragraph-marker' else
                                    ['The literal figure binding is the intended block-producing figure and '
                                     'the preceding prose owns the intended paragraph boundary.']))
                           for edit in proposal.edits])
    except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
        return dict(empty, verification='withheld', reason=str(error))
