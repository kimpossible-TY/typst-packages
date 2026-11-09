"""Conditional paragraph hints for the literal ``#paragraph-tab`` spelling.

The selected sources and optional modern package may bind this spelling to a
marker, but this source-only report does not certify that binding or execute
the document. It never emits edits or compilation authority.
"""
from bisect import bisect_right
from collections.abc import Mapping

from conditional_highlight import _module_alias
from document_conditional_marker_tags import _has_name
from document_gap_preview import _scopes
from document_sequence_preview import _FindingMapper
import import_graph
import paragraph_rules
from suppression import resolve as resolve_suppressions
from syntax_client import AdapterError, parse
from typst_lint import InputError, SafetyError, digest, load_rules, require

RULE_IDS = (paragraph_rules.MARKER_GAP, paragraph_rules.MARKER_TARGET, paragraph_rules.CAPITALIZATION)
CONDITIONS = (
    'The literal paragraph-tab spelling resolves to the intended paragraph marker at runtime.',
    'The relevant source content executes and the intended paragraph show rule applies.',
    'For capitalization hints, the following paragraph is ordinary English prose.',
)
MAX_HINT_ITEMS = 10_000
_ASSIGNMENTS = frozenset(('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq'))


def _shadow(tree):
    nodes = tree.nodes
    def named(index):
        return _has_name(tree, index, 'paragraph-tab')
    for index, node in enumerate(nodes):
        if node.kind in ('ModuleImport', 'RenamedImportItem'):
            parts = import_graph.significant(tree, index)
            if node.kind == 'ModuleImport':
                module = _module_alias(tree, index)
                if module is not None and module[1] == 'paragraph-tab':
                    return True
                shape = import_graph.import_shape(tree, index)
                if shape is not None and shape.items is not None:
                    if any(local == 'paragraph-tab' and original != local for original, local in shape.items):
                        return True
            if any(nodes[part].kind == 'As' and nodes[target].kind == 'Ident' and named(target)
                   for part, target in zip(parts, parts[1:])):
                return True
            continue
        parts = import_graph.significant(tree, index) if node.kind in (
            'LetBinding', 'Closure', 'ForLoop', 'Binary', 'DestructAssignment') else ()
        if node.kind == 'LetBinding' and len(parts) >= 2:
            target = parts[1]
            if nodes[target].kind == 'Closure':
                inner = import_graph.significant(tree, target)
                target = inner[0] if inner else target
            if named(target):
                return True
        elif node.kind == 'Closure':
            if any(nodes[part].kind == 'Params' and named(part) for part in parts):
                return True
        elif node.kind == 'ForLoop':
            for part in parts[1:]:
                if nodes[part].kind == 'In':
                    break
                if named(part):
                    return True
        elif node.kind == 'Binary' and parts and any(nodes[part].kind in _ASSIGNMENTS for part in parts):
            if named(parts[0]):
                return True
        elif node.kind == 'DestructAssignment' and parts and named(parts[0]):
            return True
    return False


def analyze(source, path, *, catalog=None):
    """Return hints for exact parsed marker tokens from immutable source bytes."""
    if type(source) is not bytes or type(path) is not str or not path:
        raise InputError('Hyphenated marker hints require source bytes and a path')
    records = load_rules()[0]['rules'] if catalog is None else catalog
    if (type(records) not in (list, tuple) or any(not isinstance(row, Mapping)
            or type(row.get('id')) is not str or not row['id'] for row in records)):
        raise InputError('Hyphenated marker hints require rule metadata')
    metadata = {row['id']: row for row in records}
    if (len(metadata) != len(records) or any(rid not in metadata or
            any(type(metadata[rid].get(key)) is not str or not metadata[rid][key]
                for key in ('grade', 'source')) for rid in RULE_IDS)):
        raise InputError('Missing or invalid paragraph rule metadata')
    result = dict(path=path, source_sha256=digest(source), status='PARTIAL', diagnostics=[],
                  suppressed=[], unresolved=[], conditions=CONDITIONS,
                  runtime_binding_proved=False, execution_proved=False, coverage_complete=False,
                  edit_permission=False, compiler_permission=False, render_equivalence_proved=False)
    try:
        tree = parse(source)
    except (AdapterError, OSError, ValueError) as error:
        return dict(result, status='UNAVAILABLE', unresolved=[f'Source syntax unavailable: {error}'])
    if tree.mode != 'markup' or tree.nodes[0].errors:
        return dict(result, status='UNAVAILABLE', unresolved=['Malformed or non-markup source'])
    nodes = tree.nodes
    marker_starts, capitalization_markers, modified = set(), {}, bytearray(source)
    for body in nodes:
        if body.kind != 'Markup':
            continue
        siblings = body.children
        for pos in range(1, len(siblings)):
            ident, hash_node = nodes[siblings[pos]], nodes[siblings[pos - 1]]
            if (ident.kind != 'Ident' or source[ident.start:ident.end] != b'paragraph-tab'
                    or hash_node.kind != 'Hash' or hash_node.end != ident.start):
                continue
            marker_starts.add(hash_node.start)
            modified[ident.start + len(b'paragraph')] = ord('_')
            after = pos + 1
            if after < len(siblings) and nodes[siblings[after]].kind == 'Space':
                after += 1
            if after < len(siblings) and nodes[siblings[after]].kind == 'Text':
                capitalization_markers[nodes[siblings[after]].start] = hash_node.start
    if not marker_starts:
        return result
    if _shadow(tree):
        result['unresolved'].append('An explicit paragraph-tab shadow or mutation is present; source is withheld')
        return result
    try:
        virtual = parse(bytes(modified), tree.mode)
        if (virtual.nodes[0].errors or virtual.nodes != tree.nodes
                or _scopes(tree, records) != _scopes(virtual, records)):
            raise SafetyError('Virtual marker spelling changed syntax or suppression ownership')
        # The existing conditional analyzers deliberately skip root Markup.
        # Apply their same narrow standalone-marker gap policy to root only;
        # target and capitalization rules stay nested-only in this report.
        root = nodes[0]
        siblings = root.children
        scopes = resolve_suppressions(tree, set(metadata))
        newlines = [index for index, byte in enumerate(source) if byte == 10]
        for pos in range(1, len(siblings)):
            ident, hash_node = nodes[siblings[pos]], nodes[siblings[pos - 1]]
            start = hash_node.start
            if (start not in marker_starts or ident.kind != 'Ident' or hash_node.kind != 'Hash'
                    or hash_node.end != ident.start):
                continue
            line_index = bisect_right(newlines, start)
            line_start = 0 if line_index == 0 else newlines[line_index - 1] + 1
            line_end = newlines[line_index] if line_index < len(newlines) else len(source)
            if source[line_start:start].strip(b' \t') or source[ident.end:line_end].strip(b' \t\r'):
                continue
            next_pos = pos + 1
            while next_pos < len(siblings) and nodes[siblings[next_pos]].kind in (
                    'Space', 'Parbreak', 'LineComment', 'BlockComment'):
                next_pos += 1
            following = nodes[siblings[next_pos]] if next_pos < len(siblings) else None
            if following is not None and (following.kind == 'Heading'
                                          or (following.kind == 'Equation' and following.display)):
                continue
            previous_start = 0 if line_index < 2 else newlines[line_index - 2] + 1
            previous = source[previous_start:line_start]
            if previous.endswith(b'\n') and not previous.strip(b' \t\r\n'):
                continue
            ignored = any(scope.covers(paragraph_rules.MARKER_GAP, start) for scope in scopes)
            previous_pos = pos - 2
            while previous_pos >= 0 and nodes[siblings[previous_pos]].kind in (
                    'Space', 'Parbreak', 'LineComment', 'BlockComment'):
                previous_pos -= 1
            previous_node = nodes[siblings[previous_pos]] if previous_pos >= 0 else None
            if (previous_node is None or previous_node.kind != 'Text'
                    or not any(char.isalnum() for char in
                               source[previous_node.start:previous_node.end].decode('utf-8'))):
                if not ignored:
                    result['unresolved'].append(paragraph_rules.NESTED_GAP_UNRESOLVED + str(start))
                continue
            rule = metadata[paragraph_rules.MARKER_GAP]
            finding = dict(rule_id=paragraph_rules.MARKER_GAP, grade=rule['grade'], path=path,
                line=line_index + 1, column=len(source[line_start:start].decode('utf-8')) + 1,
                start=start, end=ident.end, marker_start=start, marker_spelling='paragraph-tab',
                source=rule['source'], explanation='Conditional source hint: if this standalone name is the '
                'paragraph marker, place a blank source line before it.')
            target = 'suppressed' if ignored else 'diagnostics'
            result[target].append(finding)
            require(len(result['diagnostics']) + len(result['suppressed']) <= MAX_HINT_ITEMS,
                    'Hyphenated marker hint item limit exceeded')
        mapper = _FindingMapper(source)
        analyzers = (paragraph_rules.analyze_conditional_nested_gap,
                     paragraph_rules.analyze_conditional_nested_target,
                     paragraph_rules.analyze_conditional_nested_capitalization)
        for analyzer in analyzers:
            values = analyzer(virtual, path, catalog=records)
            if values.parse_errors:
                raise SafetyError('Virtual marker rule analysis failed')
            if analyzer is paragraph_rules.analyze_conditional_nested_gap:
                prefix = paragraph_rules.NESTED_GAP_UNRESOLVED
                for reason in values.skips:
                    suffix = reason[len(prefix):] if reason.startswith(prefix) else ''
                    if suffix.isdecimal() and int(suffix) in marker_starts:
                        result['unresolved'].append(reason)
                        require(len(result['unresolved']) <= MAX_HINT_ITEMS,
                                'Hyphenated marker hint item limit exceeded')
            for target, findings in (('diagnostics', values.diagnostics), ('suppressed', values.suppressed)):
                for finding in findings:
                    offset = mapper.offset(finding)
                    marker = (capitalization_markers.get(offset) if finding.rule_id == paragraph_rules.CAPITALIZATION
                              else offset if offset in marker_starts else None)
                    if marker is None:
                        continue
                    end = offset + (1 if finding.rule_id == paragraph_rules.CAPITALIZATION else len(b'#paragraph-tab'))
                    result[target].append(dict(rule_id=finding.rule_id, grade=finding.grade, path=path,
                        line=finding.line, column=finding.column, start=offset, end=end, marker_start=marker,
                        marker_spelling='paragraph-tab', source=finding.source, explanation=finding.explanation))
                    require(len(result['diagnostics']) + len(result['suppressed']) <= MAX_HINT_ITEMS,
                            'Hyphenated marker hint item limit exceeded')
    except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
        return dict(result, status='UNAVAILABLE', diagnostics=[], suppressed=[], unresolved=[str(error)])
    return result
