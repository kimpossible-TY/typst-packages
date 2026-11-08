"""Explicit original-only, captured Typst compilation to stdout.

Optional document lint examines the captured sources without modifying them.
The compiled PDF is emitted only after capture and compiler checks succeed.
"""
import argparse
from bisect import bisect_right
from dataclasses import asdict
import heapq
import io
import json
import os
from pathlib import Path
import select
import stat
import sys
import unicodedata

import closed_core_compile as runtime
import document_dependencies
import document_preview_compile
import document_preview_discover
import document_resources
import include_context
from document_preview_cli import MAX_MANIFEST_BYTES, parse_manifest_bytes
from package_loader import PackageInputError, _absolute_root, read_snapshot_file
from typst_lint import InputError, SafetyError, VERSION, checked_path, load_rules, relative, require

_LINT_DEFAULT_SAMPLE = 50
_LINT_DETAIL_SAMPLE = 10_000
_LINT_ITEM_LIMIT = 100_000
_LINT_REPORT_BYTES = 8 * 1024 * 1024
_LOCAL_RAW_OWNER_WORK = 2_000_000
_LOCAL_RAW_EXPLANATION = ('This raw annotation is outside a display if the wrapper, helper bindings '
                          'and execution match the stated conditions.')


class _DescendingKey:
    __slots__ = ('key',)

    def __init__(self, key):
        self.key = key

    def __lt__(self, other):
        return self.key > other.key

    def __eq__(self, other):
        return type(other) is _DescendingKey and self.key == other.key


def _retain_sample(heap, finding, sequence, limit):
    key = (finding.path, finding.line, finding.column, finding.rule_id)
    entry = (_DescendingKey(key), -sequence, finding)
    if len(heap) < limit:
        heapq.heappush(heap, entry)
    elif key < heap[0][0].key:
        heapq.heapreplace(heap, entry)


def _finish_sample(heap):
    return [asdict(row) for key, sequence, row in
            sorted(heap, key=lambda item: (item[0].key, -item[1]))]


def _retain_mapping_sample(heap, finding, sequence, limit):
    key = (finding['path'], finding['line'], finding['column'], finding['rule_id'])
    entry = (_DescendingKey(key), -sequence, finding)
    if len(heap) < limit:
        heapq.heappush(heap, entry)
    elif key < heap[0][0].key:
        heapq.heapreplace(heap, entry)


def _finish_mapping_sample(heap):
    return [dict(row) for key, sequence, row in
            sorted(heap, key=lambda item: (item[0].key, -item[1]))]


def _hyphen_marker_positions(tree):
    """Recover exact marker and following text positions from the issued tree."""
    nodes, source, markers, following_text = tree.nodes, tree.source, set(), {}
    for body in nodes:
        if body.kind != 'Markup':
            continue
        siblings = body.children
        for position in range(1, len(siblings)):
            ident, mark = nodes[siblings[position]], nodes[siblings[position - 1]]
            if (ident.kind != 'Ident' or source[ident.start:ident.end] != b'paragraph-tab'
                    or mark.kind != 'Hash' or mark.end != ident.start):
                continue
            markers.add(mark.start)
            following = position + 1
            if following < len(siblings) and nodes[siblings[following]].kind == 'Space':
                following += 1
            if following < len(siblings) and nodes[siblings[following]].kind == 'Text':
                following_text[nodes[siblings[following]].start] = mark.start
    return markers, following_text


def _local_raw_annotation_positions(tree, *, max_work, wrapper="local-scope-annotations"):
    """Recover direct raw-annot spans and their syntactic wrapper owners."""
    from document_conditional_raw_annot import _arguments, _equation_constructor
    from document_conditional_scoped_calls import _parts, _scoped_body, _text

    nodes = tree.nodes
    parents = {child: (index, position) for index, node in enumerate(nodes)
               for position, child in enumerate(node.children)}
    owners, work = {}, len(nodes)
    require(work <= max_work, 'Local scoped annotation ownership work limit exceeded')
    for index, node in enumerate(nodes):
        if node.kind != 'FuncCall':
            continue
        parts = _parts(tree, index)
        if (len(parts) != 2 or nodes[parts[0]].kind != 'Ident'
                or _text(tree, parts[0]) != wrapper):
            continue
        if wrapper == 'mannot-scope':
            from document_conditional_mannot_scope import _callback
            callback, _ = _callback(tree, index)
        else:
            callback, _ = _scoped_body(tree, index, 'local-scope-annotations')
        if callback is None:
            continue
        body, _ = callback
        outer = parents.get(index)
        dynamic, inside_equation = False, False
        while outer is not None:
            work += 1
            require(work <= max_work, 'Local scoped annotation ownership work limit exceeded')
            ancestor = outer[0]
            if nodes[ancestor].kind == 'Equation':
                inside_equation = True
                break
            dynamic |= ancestor != 0
            outer = parents.get(ancestor)
        if inside_equation:
            continue
        pending = [(body, False)]
        while pending:
            current, inside_display = pending.pop()
            work += 1
            require(work <= max_work, 'Local scoped annotation ownership work limit exceeded')
            child = nodes[current]
            # Nested dynamic bodies or wrappers do not belong to this callback
            # in the bounded source analyzer.
            if current != body and child.kind in ('LetBinding', 'Closure', 'Contextual', 'CodeBlock',
                    'ModuleImport', 'ModuleInclude', 'ForLoop', 'WhileLoop', 'Conditional',
                    'FuncReturn', 'DestructAssignment', 'Raw', 'Str'):
                continue
            if current != body and child.kind == 'Binary' and any(
                    nodes[part].kind in ('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq')
                    for part in _parts(tree, current)):
                continue
            inside_display |= ((child.kind == 'Equation' and child.display is not False)
                               or _equation_constructor(tree, current))
            call = _parts(tree, current) if child.kind == 'FuncCall' else ()
            if (current != body and len(call) == 2 and nodes[call[0]].kind == 'Ident'
                    and _text(tree, call[0]) in ('local-scope-annotations', 'mannot-scope')):
                continue
            pending.extend((descendant, inside_display) for descendant in child.children)
            if (len(call) != 2 or nodes[call[0]].kind != 'Ident' or _text(tree, call[0]) != 'annot'
                    or nodes[call[1]].kind != 'Args' or not _arguments(tree, call[1])
                    or current not in parents or inside_display):
                continue
            parent, position = parents[current]
            if nodes[parent].kind not in ('Markup', 'Math') or not position:
                continue
            mark = nodes[nodes[parent].children[position - 1]]
            if mark.kind == 'Hash' and mark.end == child.start:
                owners.setdefault(mark.start, set()).add((child.end, node.start, dynamic))
    return owners, work


def _local_raw_shape_counts(tree, wrapper="local-scope-annotations"):
    """Bound claimed callback and annotation counts by captured syntax."""
    from document_conditional_scoped_calls import _parts, _text

    wrappers = calls = 0
    for index, node in enumerate(tree.nodes):
        if node.kind != 'FuncCall':
            continue
        parts = _parts(tree, index)
        if len(parts) != 2 or tree.nodes[parts[0]].kind != 'Ident':
            continue
        name = _text(tree, parts[0])
        wrappers += name == wrapper
        calls += name == 'annot'
    return wrappers, calls


def _direct_raw_positions(tree, *, max_work):
    """Independently verify direct raw calls and their lexical ownership."""
    from document_conditional_raw_annot import _arguments, _equation_constructor, _explicit_shadow
    from document_conditional_scoped_calls import _OUTER_BARRIERS, _parts, _text
    nodes = tree.nodes
    if _explicit_shadow(tree):
        return {}, len(nodes)
    parents = {child: (index, position) for index, node in enumerate(nodes)
               for position, child in enumerate(node.children)}
    positions, work = {}, len(nodes)
    barriers = _OUTER_BARRIERS | {'Equation', 'LetBinding', 'Raw', 'Str'}
    for index, node in enumerate(nodes):
        if node.kind != 'FuncCall':
            continue
        parts = _parts(tree, index)
        if (len(parts) != 2 or nodes[parts[0]].kind != 'Ident'
                or _text(tree, parts[0]) != 'annot' or nodes[parts[1]].kind != 'Args'
                or not _arguments(tree, parts[1]) or index not in parents):
            continue
        parent, position = parents[index]
        if nodes[parent].kind != 'Markup' or not position:
            continue
        mark = nodes[nodes[parent].children[position - 1]]
        if mark.kind != 'Hash' or mark.end != node.start:
            continue
        cursor, valid = parent, True
        while cursor is not None:
            work += 1
            require(work <= max_work, 'Raw annotation ownership work limit exceeded')
            if nodes[cursor].kind in barriers or _equation_constructor(tree, cursor):
                valid = False
                break
            cursor = parents.get(cursor, (None,))[0]
        if valid:
            positions[mark.start] = node.end
    require(work <= max_work, 'Raw annotation ownership work limit exceeded')
    return positions, work


def _conditional_raw_sections(context, catalog, metadata, cap, items):
    """Validate source-only analyzers against captured syntax before reporting."""
    import document_conditional_raw_annot as direct
    import document_conditional_mannot_scope as scoped
    from suppression import resolve as resolve_suppressions

    sections = {}
    work = 0
    for name, module, wrapper in (('conditional_raw_annotations', direct, None),
                                  ('conditional_mannot_scope_raw_annotations', scoped, 'mannot-scope')):
        findings, hidden, files = [], [], []
        seen = suppressed = wrappers = dynamic = display = 0
        for item in context.sources:
            value = module._analyze_captured_tree(item.tree, item.path, catalog=catalog['rules'])
            require(type(value) is dict and value.get('path') == item.path
                    and value.get('source_sha256') == item.identity.source_sha256
                    and value.get('status') in ('PARTIAL', 'UNAVAILABLE')
                    and value.get('conditions') == module.CONDITIONS
                    and all(value.get(k) is False for k in ('runtime_binding_proved', 'execution_proved',
                        'coverage_complete', 'edit_permission', 'compiler_permission'))
                    and all(type(value.get(k)) is list for k in ('diagnostics', 'suppressed', 'unresolved'))
                    and all(type(reason) is str and bool(reason.strip()) for reason in value['unresolved']),
                    'Conditional raw annotation result does not match captured source')
            if wrapper:
                require(all(type(value.get(k)) is int and value[k] >= 0 for k in
                            ('wrappers_examined', 'wrappers_in_dynamic_context', 'lexical_display_calls'))
                        and value['wrappers_in_dynamic_context'] <= value['wrappers_examined']
                        and (not value['lexical_display_calls'] or value['wrappers_examined'] > 0),
                        'Conditional annotation wrapper counts are invalid')
                shape_wrappers, shape_calls = _local_raw_shape_counts(item.tree, wrapper)
                require(value['wrappers_examined'] <= shape_wrappers
                        and value['lexical_display_calls'] + len(value['diagnostics'])
                            + len(value['suppressed']) <= shape_calls,
                        'Conditional annotation counts exceed captured syntax')
                wrappers += value['wrappers_examined']
                dynamic += value['wrappers_in_dynamic_context']
                display += value['lexical_display_calls']
            if value['status'] == 'UNAVAILABLE':
                require(not value['diagnostics'] and not value['suppressed'] and value['unresolved']
                        and (not wrapper or not value['wrappers_examined'] and not value['lexical_display_calls']),
                        'Unavailable annotation result contains partial findings')
            items += (1 + sum(len(value[k]) for k in ('diagnostics', 'suppressed', 'unresolved'))
                      + len(value['conditions']) + (value['wrappers_examined'] + value['lexical_display_calls']
                                                    if wrapper else 0))
            require(items <= _LINT_ITEM_LIMIT, 'Document lint item limit exceeded')
            positions, starts, farthest = {}, [], []
            line_starts = ()
            if value['diagnostics'] or value['suppressed']:
                if wrapper:
                    require(not scoped._wrapper_shadow(item.tree) and not direct._explicit_shadow(item.tree),
                            'Conditional annotation bindings are shadowed')
                    positions, charged = _local_raw_annotation_positions(
                        item.tree, max_work=_LOCAL_RAW_OWNER_WORK - work, wrapper=wrapper)
                else:
                    positions, charged = _direct_raw_positions(item.tree, max_work=_LOCAL_RAW_OWNER_WORK - work)
                work += charged
                line_starts = (0, *(n + 1 for n, byte in enumerate(item.source) if byte == 10))
                scopes = resolve_suppressions(item.tree, {row['id'] for row in catalog['rules']})
                for start, end in sorted((scope.start, scope.end) for scope in scopes if module.RULE in scope.rules):
                    starts.append(start)
                    farthest.append(max(end, farthest[-1] if farthest else end))
            offsets, found_wrappers, found_dynamic = set(), set(), set()
            for key, heap in (('diagnostics', findings), ('suppressed', hidden)):
                for row in value[key]:
                    keys = {'rule_id', 'grade', 'path', 'line', 'column', 'start', 'end', 'source', 'explanation'}
                    if wrapper:
                        keys |= {'wrapper_start', 'outer_dynamic_context'}
                    explanation = (_LOCAL_RAW_EXPLANATION if wrapper else
                        'This source call has no explicit display ancestor; wrapper and show-rule rendering remain conditional.')
                    require(type(row) is dict and set(row) == keys and row.get('rule_id') == module.RULE
                            and row.get('grade') == metadata['grade'] and row.get('source') == metadata['source']
                            and row.get('path') == item.path and type(row.get('line')) is int and row['line'] > 0
                            and type(row.get('column')) is int and row['column'] > 0
                            and type(row.get('start')) is int and type(row.get('end')) is int
                            and 0 <= row['start'] < row['end'] <= len(item.source)
                            and row['start'] not in offsets and row.get('explanation') == explanation,
                            'Conditional raw annotation finding is invalid')
                    if wrapper:
                        require(type(row.get('wrapper_start')) is int and type(row.get('outer_dynamic_context')) is bool
                                and (row['end'], row['wrapper_start'], row['outer_dynamic_context'])
                                    in positions.get(row['start'], ()),
                                'Conditional annotation wrapper ownership is invalid')
                        found_wrappers.add(row['wrapper_start'])
                        if row['outer_dynamic_context']:
                            found_dynamic.add(row['wrapper_start'])
                    else:
                        require(positions.get(row['start']) == row['end'],
                                'Conditional raw annotation ownership is invalid')
                    offsets.add(row['start'])
                    line = bisect_right(line_starts, row['start']) - 1
                    require(row['line'] == line + 1 and row['column'] ==
                            len(item.source[line_starts[line]:row['start']].decode('utf-8')) + 1,
                            'Conditional annotation source position is invalid')
                    scope = bisect_right(starts, row['start']) - 1
                    require((scope >= 0 and row['start'] < farthest[scope]) == (key == 'suppressed'),
                            'Conditional annotation suppression does not match captured directives')
                    if key == 'diagnostics':
                        _retain_mapping_sample(heap, row, seen, cap)
                        seen += 1
                    else:
                        _retain_mapping_sample(heap, row, suppressed, cap)
                        suppressed += 1
            if wrapper:
                require(len(found_wrappers) <= value['wrappers_examined']
                        and len(found_dynamic) <= value['wrappers_in_dynamic_context'],
                        'Conditional annotation wrapper counts contradict findings')
            file = dict(path=item.path, source_sha256=item.identity.source_sha256, status=value['status'],
                        diagnostics=len(value['diagnostics']), suppressed=len(value['suppressed']),
                        unresolved_count=len(value['unresolved']), unresolved=value['unresolved'][:20],
                        unresolved_omitted=max(0, len(value['unresolved']) - 20))
            if wrapper:
                file.update({k: value[k] for k in ('wrappers_examined', 'wrappers_in_dynamic_context',
                                                   'lexical_display_calls')})
            files.append(file)
        rows, hidden_rows = _finish_mapping_sample(findings), _finish_mapping_sample(hidden)
        section = dict(rule_id=module.RULE, diagnostics_count=seen, diagnostics=rows,
                       diagnostics_omitted=seen-len(rows), suppressed_count=suppressed, suppressed=hidden_rows,
                       suppressed_omitted=suppressed-len(hidden_rows), sample_limit=cap, by_file=files,
                       unavailable_files=sum(row['status'] == 'UNAVAILABLE' for row in files),
                       conditions=list(module.CONDITIONS), duplicates_existing_hints_possible=True,
                       runtime_binding_proved=False, execution_proved=False, coverage_complete=False,
                       edit_permission=False, compiler_permission=False)
        if wrapper:
            section.update(wrappers_examined=wrappers, wrappers_in_dynamic_context=dynamic,
                           lexical_display_calls=display)
        sections[name] = section
    return sections, items


def _include_forwarding(context, snapshot, cap):
    """Check conditional forwarding evidence against the issued compile inputs."""
    import include_forwarding_evidence as forwarding
    from static_resolver import MATH_BOOK, MATH_BOOK_SOURCE_SHA256
    include_context.assert_include_context(context)
    document_resources.assert_capture(snapshot)
    roots = dict(snapshot.roots)
    resources = {(item.request.root, item.request.path): item for item in snapshot.files}
    prefix = 'local/math-book/0.2.0'
    folded = unicodedata.normalize('NFC', prefix).casefold()
    def has_prefix(root):
        for name, path in resources:
            normalized = unicodedata.normalize('NFC', path).casefold()
            if name == root and (normalized == folded or normalized.startswith(folded + '/')
                                 or folded.startswith(normalized + '/')):
                return True
        return False
    selected = 'packages' if has_prefix('packages') or not has_prefix('cache') else 'cache'
    value = forwarding.analyze(context, roots.get(selected, ''), snapshot=snapshot)
    include_context.assert_include_context(context)
    document_resources.assert_capture(snapshot)
    require(type(value) is dict and value.get('status') in ('CONDITIONAL', 'UNAVAILABLE')
            and value.get('original_unresolved_retained') is True
            and all(type(value.get(key)) is int for key in ('unresolved_sites_before',
                'unresolved_occurrences_before', 'recognized_site_count', 'conditionally_explained_occurrences'))
            and value.get('unresolved_sites_before') == len(context.unresolved)
            and value.get('unresolved_occurrences_before') == sum(bool(item.unresolved_codes) for item in context.occurrences)
            and all(value.get(key) is False for key in ('runtime_execution_proved', 'runtime_package_selection_proved',
                'live_package_freshness_proved', 'coverage_complete', 'edit_permission', 'compiler_permission',
                'publication_authority')) and type(value.get('sites')) is list,
            'Include forwarding result does not match the captured context')
    sites = value['sites']
    if value['status'] == 'UNAVAILABLE':
        require(not sites and value.get('resource_capture_bound') is False
                and value.get('recognized_site_count') == 0 and value.get('conditionally_explained_occurrences') == 0
                and type(value.get('reason')) is str and bool(value['reason'].strip()),
                'Unavailable include forwarding result contains evidence')
    else:
        require(len(sites) <= forwarding.MAX_EVIDENCE_ITEMS, 'Include forwarding evidence limit exceeded')
        manifest, source = (resources.get((selected, prefix + suffix)) for suffix in ('/typst.toml', '/lib.typ'))
        require(manifest is not None and source is not None and value.get('resource_capture_bound') is True
                and manifest.request.role == source.request.role == 'package'
                and manifest.sha256 == forwarding.MATH_BOOK_MANIFEST_SHA256
                and source.sha256 == MATH_BOOK_SOURCE_SHA256
                and value.get('package_evidence') == dict(import_path=MATH_BOOK, root_id=selected,
                    manifest_identity=manifest.evidence.path, manifest_sha256=manifest.sha256,
                    source_identity=source.evidence.path, source_sha256=source.sha256),
                'Include forwarding package evidence does not match the capture')
        calls = {item.path: forwarding._book_calls(item.tree) for item in context.sources}
        expected = {}
        for edge in context.edges:
            if edge.target is None or edge.literal is None:
                continue
            item = context.source_for(edge.node.source.path)
            match = forwarding._matches(edge, item, calls[item.path])
            if match is not None:
                kind, conditions, owner = match
                key = (item.path, edge.node.start, edge.node.end)
                require(key not in expected, 'Ambiguous include forwarding site')
                expected[key] = (edge, item, kind, conditions, owner)
        recognized = set()
        for row in sites:
            require(type(row) is dict and all(type(row.get(key)) is int
                    for key in ('line', 'start', 'end', 'owner_start', 'owner_end')),
                    'Invalid include forwarding site')
            key = (row.get('path'), row.get('start'), row.get('end'))
            require(key in expected and key not in recognized, 'Unexpected or duplicate include forwarding site')
            recognized.add(key)
            edge, item, kind, conditions, owner = expected[key]
            require(row.get('source_sha256') == item.identity.source_sha256
                    and row.get('line') == item.source.count(b'\n', 0, edge.node.start) + 1
                    and row.get('target') == edge.target.path and row.get('target_sha256') == edge.target.source_sha256
                    and row.get('owner_start') == item.tree.nodes[owner].start
                    and row.get('owner_end') == item.tree.nodes[owner].end
                    and row.get('evidence_kind') == kind and row.get('conditions') == conditions
                    and row.get('retained_unresolved_codes') == list(edge.unresolved_codes),
                    'Include forwarding site does not match captured source facts')
        require(recognized == set(expected) and value.get('recognized_site_count') == len(sites),
                'Include forwarding site set is incomplete')
        identified = {(expected[key][0].node.source.path, expected[key][0].node.node_id) for key in recognized}
        edges_by_node = {(edge.node.source.path, edge.node.node_id): edge for edge in context.edges}
        explained = 0
        for occurrence in context.occurrences:
            if not occurrence.unresolved_codes or 'include-cycle' in occurrence.unresolved_codes:
                continue
            unresolved = [node for node in occurrence.edge_chain
                          if edges_by_node[(node.source.path, node.node_id)].unresolved_codes]
            if unresolved and all((node.source.path, node.node_id) in identified for node in unresolved):
                explained += 1
        require(value.get('conditionally_explained_occurrences') == explained,
                'Include forwarding occurrence count does not match the capture')
    include_context.assert_include_context(context)
    document_resources.assert_capture(snapshot)
    result = dict(value)
    result['sites'] = sites[:cap]
    result['sites_omitted'] = len(sites) - len(result['sites'])
    result['sample_limit'] = cap
    return result, 1 + len(sites) + sum(len(row['conditions']) for row in sites)


def lint_captured_document(context, snapshot, *, rule_set, details=False, local_hints=False, cancel_event=None):
    """Run existing document source diagnostics against the issued compile capture."""
    import document_display
    import script_rule
    import syntax_client
    require(rule_set in ('core', 'pde') and type(details) is bool and type(local_hints) is bool
            and (not local_hints or rule_set == 'pde'), 'Invalid document lint selection')
    document_resources.stage_original(context, snapshot)
    catalog, catalog_hash = load_rules()
    enabled = [rule['id'] for rule in catalog['rules']
               if rule['id'].split('.')[0] in catalog['packs'][rule_set]]
    backends = [script_rule.RULE_ID]
    if rule_set == 'pde':
        backends += [document_display.STANDALONE, document_display.RULE_ID]
    cap = _LINT_DETAIL_SAMPLE if details else _LINT_DEFAULT_SAMPLE
    diagnostics, suppressed, files = [], [], []
    seen_diagnostics = seen_suppressed = 0
    counts = {rid: 0 for rid in backends}
    suppressed_counts = {rid: 0 for rid in backends}
    skipped_counts = {rid: 0 for rid in backends}
    parse_error_counts = {rid: 0 for rid in backends}
    if local_hints:
        import document_local_rules
        import document_conditional_hyphen_marker
        import document_conditional_local_raw_annot
        local_rules = tuple(document_local_rules.RULE_IDS)
        local_counts = {rid: 0 for rid in local_rules}
        local_hidden_counts = {rid: 0 for rid in local_rules}
        local_diagnostics, local_suppressed, local_files, local_contexts = [], [], [], []
        local_seen_diagnostics = local_seen_suppressed = 0
        hyphen_rules = tuple(document_conditional_hyphen_marker.RULE_IDS)
        hyphen_metadata = {row['id']: row for row in catalog['rules'] if row['id'] in hyphen_rules}
        require(len(hyphen_metadata) == len(hyphen_rules) and set(hyphen_rules).issubset(set(enabled)),
                'Hyphenated marker metadata is unavailable')
        hyphen_counts = {rid: 0 for rid in hyphen_rules}
        hyphen_hidden_counts = {rid: 0 for rid in hyphen_rules}
        hyphen_diagnostics, hyphen_suppressed, hyphen_files = [], [], []
        hyphen_seen_diagnostics = hyphen_seen_suppressed = 0
        scoped_raw_rule = document_conditional_local_raw_annot.RULE
        scoped_raw_metadata = next((row for row in catalog['rules'] if row['id'] == scoped_raw_rule), None)
        require(scoped_raw_metadata is not None and scoped_raw_rule in enabled,
                'Local scoped raw annotation metadata is unavailable')
        scoped_raw_diagnostics, scoped_raw_suppressed, scoped_raw_files = [], [], []
        scoped_raw_seen = scoped_raw_hidden = scoped_raw_wrappers = scoped_raw_dynamic = scoped_raw_display = 0
        scoped_raw_owner_work = 0
    items = len(context.sources) + len(context.edges) + len(context.occurrences) + len(context.unresolved)
    for item in context.sources:
        if cancel_event is not None and cancel_event.is_set():
            raise runtime.CompilerFailure('cancelled', kind=runtime.CompilerFailureKind.CANCELLED)
        results = {script_rule.RULE_ID: script_rule.analyze(item.tree, item.path,
            catalog=catalog['rules'], _verify=False, _allow_code_call=True)}
        if rule_set == 'pde':
            results.update(document_display.analyze_all(item.tree, item.path, catalog=catalog['rules']))
        for rid in backends:
            value = results[rid]
            errors = tuple(value.parse_errors)
            reasons = tuple(value.skips)
            items += 1 + len(value.diagnostics) + len(value.suppressed) + len(reasons) + len(errors)
            require(items <= _LINT_ITEM_LIMIT, 'Document lint item limit exceeded')
            counts[rid] += len(value.diagnostics)
            suppressed_counts[rid] += len(value.suppressed)
            skipped_counts[rid] += len(reasons)
            parse_error_counts[rid] += len(errors)
            for row in value.diagnostics:
                _retain_sample(diagnostics, row, seen_diagnostics, cap)
                seen_diagnostics += 1
            for row in value.suppressed:
                _retain_sample(suppressed, row, seen_suppressed, cap)
                seen_suppressed += 1
            files.append(dict(path=item.path, source_sha256=item.identity.source_sha256,
                tree_sha256=item.identity.tree_sha256, nodes=len(item.tree.nodes), rule_id=rid,
                status='partial', supported_scope_status='partial' if reasons or errors else 'evaluated',
                partial_reason=('Source-pattern analysis does not cover all syntax, runtime execution, '
                                'imports, or rendered contexts'),
                diagnostics=len(value.diagnostics), suppressed=len(value.suppressed),
                skip_count=len(reasons), skip_reasons=list(reasons[:20]),
                skip_reasons_omitted=max(0, len(reasons) - 20),
                parse_error_count=len(errors), parse_errors=[list(span) for span in errors[:20]],
                parse_errors_omitted=max(0, len(errors) - 20)))
        if local_hints:
            shared_reasons, local_results = document_local_rules.analyze(item.tree, item.path, catalog['rules'])
            shared = set(shared_reasons)
            items += 1 + len(shared_reasons)
            require(items <= _LINT_ITEM_LIMIT, 'Document lint item limit exceeded')
            local_contexts.append(dict(path=item.path, source_sha256=item.identity.source_sha256,
                shared_reason_count=len(shared_reasons), shared_resolution_reasons=list(shared_reasons[:20]),
                shared_reasons_omitted=max(0, len(shared_reasons) - 20)))
            for rid in local_rules:
                value = local_results[rid]
                reasons = tuple(reason for reason in value.skips if reason not in shared)
                errors = tuple(value.parse_errors)
                items += 1 + len(value.diagnostics) + len(value.suppressed) + len(reasons) + len(errors)
                require(items <= _LINT_ITEM_LIMIT, 'Document lint item limit exceeded')
                local_counts[rid] += len(value.diagnostics)
                local_hidden_counts[rid] += len(value.suppressed)
                for row in value.diagnostics:
                    _retain_sample(local_diagnostics, row, local_seen_diagnostics, cap)
                    local_seen_diagnostics += 1
                for row in value.suppressed:
                    _retain_sample(local_suppressed, row, local_seen_suppressed, cap)
                    local_seen_suppressed += 1
                local_files.append(dict(path=item.path, source_sha256=item.identity.source_sha256,
                    rule_id=rid, status='conditional', diagnostics=len(value.diagnostics),
                    suppressed=len(value.suppressed), skip_count=len(reasons), skip_reasons=list(reasons[:20]),
                    skip_reasons_omitted=max(0, len(reasons) - 20), parse_error_count=len(errors),
                    parse_errors=[list(span) for span in errors[:20]],
                    parse_errors_omitted=max(0, len(errors) - 20), edit_permission=False))
            hyphen = document_conditional_hyphen_marker.analyze(item.source, item.path, catalog=catalog['rules'])
            require(type(hyphen) is dict and hyphen.get('path') == item.path
                    and hyphen.get('source_sha256') == item.identity.source_sha256
                    and hyphen.get('status') in ('PARTIAL', 'UNAVAILABLE')
                    and all(hyphen.get(key) is False for key in ('runtime_binding_proved', 'execution_proved',
                        'coverage_complete', 'edit_permission', 'compiler_permission', 'render_equivalence_proved'))
                    and hyphen.get('conditions') == document_conditional_hyphen_marker.CONDITIONS
                    and all(type(hyphen.get(key)) is list for key in ('diagnostics', 'suppressed', 'unresolved'))
                    and all(type(reason) is str and bool(reason.strip()) for reason in hyphen['unresolved']),
                    'Hyphenated marker result does not match the captured source')
            if hyphen['status'] == 'UNAVAILABLE':
                require(not hyphen['diagnostics'] and not hyphen['suppressed'] and bool(hyphen['unresolved']),
                        'Unavailable hyphenated marker result contains partial findings')
            items += 1 + sum(len(hyphen[key]) for key in ('diagnostics', 'suppressed', 'unresolved')) + len(hyphen['conditions'])
            require(items <= _LINT_ITEM_LIMIT, 'Document lint item limit exceeded')
            marker_positions, marker_text = _hyphen_marker_positions(item.tree)
            for key, heap, counts_by_rule in (('diagnostics', hyphen_diagnostics, hyphen_counts),
                                               ('suppressed', hyphen_suppressed, hyphen_hidden_counts)):
                for row in hyphen[key]:
                    require(type(row) is dict and row.get('rule_id') in hyphen_metadata
                            and row.get('path') == item.path and type(row.get('line')) is int and row['line'] > 0
                            and type(row.get('column')) is int and row['column'] > 0
                            and type(row.get('start')) is int and type(row.get('end')) is int
                            and 0 <= row['start'] <= row['end'] <= len(item.source)
                            and type(row.get('marker_start')) is int and row['marker_start'] >= 0
                            and row['marker_start'] in marker_positions
                            and row.get('marker_spelling') == 'paragraph-tab'
                            and type(row.get('explanation')) is str and bool(row['explanation'].strip())
                            and row.get('grade') == hyphen_metadata[row['rule_id']]['grade']
                            and row.get('source') == hyphen_metadata[row['rule_id']]['source'],
                            'Hyphenated marker finding does not match the captured rule or source')
                    line_start = item.source.rfind(b'\n', 0, row['start']) + 1
                    require(row['line'] == item.source.count(b'\n', 0, row['start']) + 1
                            and row['column'] == len(item.source[line_start:row['start']].decode('utf-8')) + 1,
                            'Hyphenated marker finding position does not match captured bytes')
                    if row['rule_id'] == 'pde.paragraph-capitalization':
                        require(row['end'] == row['start'] + 1
                                and b'a' <= item.source[row['start']:row['end']] <= b'z'
                                and marker_text.get(row['start']) == row['marker_start'],
                                'Hyphenated capitalization finding does not match marker ownership')
                    else:
                        require(row['start'] == row['marker_start']
                                and row['end'] == row['start'] + len(b'#paragraph-tab'),
                                'Hyphenated marker finding span does not match its token')
                    counts_by_rule[row['rule_id']] += 1
                    if key == 'diagnostics':
                        _retain_mapping_sample(heap, row, hyphen_seen_diagnostics, cap)
                        hyphen_seen_diagnostics += 1
                    else:
                        _retain_mapping_sample(heap, row, hyphen_seen_suppressed, cap)
                        hyphen_seen_suppressed += 1
            hyphen_files.append(dict(path=item.path, source_sha256=item.identity.source_sha256,
                status=hyphen['status'], diagnostics=len(hyphen['diagnostics']),
                suppressed=len(hyphen['suppressed']), unresolved_count=len(hyphen['unresolved']),
                unresolved=list(hyphen['unresolved'][:20]), unresolved_omitted=max(0, len(hyphen['unresolved']) - 20)))
            scoped_raw = document_conditional_local_raw_annot.analyze(item.source, item.path, catalog=catalog['rules'])
            require(type(scoped_raw) is dict and scoped_raw.get('path') == item.path
                    and scoped_raw.get('source_sha256') == item.identity.source_sha256
                    and scoped_raw.get('status') in ('PARTIAL', 'UNAVAILABLE')
                    and all(scoped_raw.get(key) is False for key in ('runtime_binding_proved', 'execution_proved',
                        'coverage_complete', 'edit_permission', 'compiler_permission'))
                    and scoped_raw.get('conditions') == document_conditional_local_raw_annot.CONDITIONS
                    and all(type(scoped_raw.get(key)) is list for key in ('diagnostics', 'suppressed', 'unresolved'))
                    and all(type(reason) is str and bool(reason.strip()) for reason in scoped_raw['unresolved'])
                    and all(type(scoped_raw.get(key)) is int and scoped_raw[key] >= 0
                        for key in ('wrappers_examined', 'wrappers_in_dynamic_context', 'lexical_display_calls'))
                    and scoped_raw['wrappers_in_dynamic_context'] <= scoped_raw['wrappers_examined']
                    and (not scoped_raw['lexical_display_calls'] or scoped_raw['wrappers_examined'] > 0),
                    'Local scoped raw annotation result does not match the captured source')
            if scoped_raw['status'] == 'UNAVAILABLE':
                require(not scoped_raw['diagnostics'] and not scoped_raw['suppressed']
                        and not scoped_raw['wrappers_examined'] and not scoped_raw['lexical_display_calls']
                        and bool(scoped_raw['unresolved']),
                        'Unavailable local scoped annotation result contains partial findings')
            raw_shape_wrappers, raw_shape_calls = _local_raw_shape_counts(item.tree)
            require(scoped_raw['wrappers_examined'] <= raw_shape_wrappers
                    and scoped_raw['lexical_display_calls'] + len(scoped_raw['diagnostics'])
                        + len(scoped_raw['suppressed']) <= raw_shape_calls,
                    'Local scoped annotation counts exceed captured source syntax')
            items += (1 + sum(len(scoped_raw[key]) for key in ('diagnostics', 'suppressed', 'unresolved'))
                      + len(scoped_raw['conditions']) + scoped_raw['wrappers_examined']
                      + scoped_raw['lexical_display_calls'])
            require(items <= _LINT_ITEM_LIMIT, 'Document lint item limit exceeded')
            owners = {}
            line_starts = ()
            raw_scope_starts, raw_scope_farthest = [], []
            if scoped_raw['diagnostics'] or scoped_raw['suppressed']:
                from suppression import resolve as resolve_suppressions
                owners, charged = _local_raw_annotation_positions(
                    item.tree, max_work=_LOCAL_RAW_OWNER_WORK - scoped_raw_owner_work)
                scoped_raw_owner_work += charged
                line_starts = (0, *(index + 1 for index, byte in enumerate(item.source) if byte == 10))
                raw_scopes = resolve_suppressions(item.tree, {row['id'] for row in catalog['rules']})
                for start, end in sorted((scope.start, scope.end) for scope in raw_scopes
                                         if scoped_raw_rule in scope.rules):
                    raw_scope_starts.append(start)
                    raw_scope_farthest.append(max(end, raw_scope_farthest[-1] if raw_scope_farthest else end))
            seen_offsets, found_wrappers, found_dynamic_wrappers = set(), set(), set()
            for key, heap in (('diagnostics', scoped_raw_diagnostics), ('suppressed', scoped_raw_suppressed)):
                for row in scoped_raw[key]:
                    require(type(row) is dict and row.get('rule_id') == scoped_raw_rule
                            and row.get('path') == item.path and type(row.get('line')) is int and row['line'] > 0
                            and type(row.get('column')) is int and row['column'] > 0
                            and type(row.get('start')) is int and type(row.get('end')) is int
                            and 0 <= row['start'] < row['end'] <= len(item.source)
                            and type(row.get('wrapper_start')) is int and type(row.get('outer_dynamic_context')) is bool
                            and (row['end'], row['wrapper_start'], row['outer_dynamic_context'])
                                in owners.get(row['start'], ())
                            and row['start'] not in seen_offsets
                            and set(row) == {'rule_id', 'grade', 'path', 'line', 'column', 'start', 'end',
                                            'source', 'wrapper_start', 'outer_dynamic_context', 'explanation'}
                            and row.get('explanation') == _LOCAL_RAW_EXPLANATION
                            and row.get('grade') == scoped_raw_metadata['grade']
                            and row.get('source') == scoped_raw_metadata['source'],
                            'Local scoped annotation finding does not match the captured rule or source')
                    seen_offsets.add(row['start'])
                    found_wrappers.add(row['wrapper_start'])
                    if row['outer_dynamic_context']:
                        found_dynamic_wrappers.add(row['wrapper_start'])
                    line_index = bisect_right(line_starts, row['start']) - 1
                    line_start = line_starts[line_index]
                    require(row['line'] == line_index + 1
                            and row['column'] == len(item.source[line_start:row['start']].decode('utf-8')) + 1,
                            'Local scoped annotation finding position does not match captured bytes')
                    scope_index = bisect_right(raw_scope_starts, row['start']) - 1
                    is_suppressed = scope_index >= 0 and row['start'] < raw_scope_farthest[scope_index]
                    require(is_suppressed == (key == 'suppressed'),
                            'Local scoped annotation suppression does not match captured directives')
                    if key == 'diagnostics':
                        _retain_mapping_sample(heap, row, scoped_raw_seen, cap)
                        scoped_raw_seen += 1
                    else:
                        _retain_mapping_sample(heap, row, scoped_raw_hidden, cap)
                        scoped_raw_hidden += 1
            require(len(found_wrappers) <= scoped_raw['wrappers_examined']
                    and len(found_dynamic_wrappers) <= scoped_raw['wrappers_in_dynamic_context'],
                    'Local scoped annotation wrapper counts contradict captured findings')
            scoped_raw_wrappers += scoped_raw['wrappers_examined']
            scoped_raw_dynamic += scoped_raw['wrappers_in_dynamic_context']
            scoped_raw_display += scoped_raw['lexical_display_calls']
            scoped_raw_files.append(dict(path=item.path, source_sha256=item.identity.source_sha256,
                status=scoped_raw['status'], diagnostics=len(scoped_raw['diagnostics']),
                suppressed=len(scoped_raw['suppressed']), wrappers_examined=scoped_raw['wrappers_examined'],
                wrappers_in_dynamic_context=scoped_raw['wrappers_in_dynamic_context'],
                lexical_display_calls=scoped_raw['lexical_display_calls'],
                unresolved_count=len(scoped_raw['unresolved']), unresolved=list(scoped_raw['unresolved'][:20]),
                unresolved_omitted=max(0, len(scoped_raw['unresolved']) - 20)))
    if local_hints:
        raw_sections, items = _conditional_raw_sections(context, catalog, scoped_raw_metadata, cap, items)
        include_forwarding, forwarding_items = _include_forwarding(context, snapshot, cap)
        items += forwarding_items
        require(items <= _LINT_ITEM_LIMIT, 'Document lint item limit exceeded')
    document_resources.stage_original(context, snapshot)
    total, hidden = sum(counts.values()), sum(suppressed_counts.values())
    diagnostics = _finish_sample(diagnostics)
    suppressed = _finish_sample(suppressed)
    report = dict(rule_set=rule_set, catalog_sha256=catalog_hash,
        diagnostics_present=bool(total), diagnostics_count=total, diagnostics_by_rule=counts,
        diagnostics=diagnostics, diagnostics_omitted=total - len(diagnostics),
        suppressed_count=hidden, suppressed_by_rule=suppressed_counts, suppressed=suppressed,
        suppressed_omitted=hidden - len(suppressed), sample_limit=cap, details_requested=details,
        limits=dict(per_source_nodes=syntax_client.MAX_NODES, aggregate_items=_LINT_ITEM_LIMIT,
                    aggregate_items_used=items, report_bytes=_LINT_REPORT_BYTES),
        coverage=dict(global_complete=False, enabled=enabled, inspected_backends=backends,
            uninspected=[rid for rid in enabled if rid not in backends], files=files,
            uninspected_scope=('Not inspected by the primary backends; separately requested conditional '
                               'sections may inspect source patterns without proving full rule coverage'),
            skips_by_rule=skipped_counts, parse_errors_by_rule=parse_error_counts,
            unresolved_includes=[asdict(row) for row in context.unresolved],
            runtime_execution_proved=False, package_bindings_proved=False,
            consumer_config_loaded=False, fixes_applied=False))
    if local_hints:
        local_diagnostics = _finish_sample(local_diagnostics)
        local_suppressed = _finish_sample(local_suppressed)
        local_total, local_hidden = sum(local_counts.values()), sum(local_hidden_counts.values())
        report['conditional_local_hints'] = dict(rule_ids=list(local_rules),
            diagnostics_present=bool(local_total), diagnostics_count=local_total, diagnostics_by_rule=local_counts,
            diagnostics=local_diagnostics, diagnostics_omitted=local_total - len(local_diagnostics),
            suppressed_count=local_hidden, suppressed_by_rule=local_hidden_counts,
            suppressed=local_suppressed, suppressed_omitted=local_hidden - len(local_suppressed),
            sample_limit=cap, source_contexts=local_contexts, coverage=local_files,
            runtime_binding_proved=False, execution_proved=False, coverage_complete=False,
            edit_permission=False, compiler_permission=False)
        hyphen_diagnostics = _finish_mapping_sample(hyphen_diagnostics)
        hyphen_suppressed = _finish_mapping_sample(hyphen_suppressed)
        hyphen_total, hyphen_hidden = sum(hyphen_counts.values()), sum(hyphen_hidden_counts.values())
        report['conditional_hyphen_markers'] = dict(rule_ids=list(hyphen_rules),
            diagnostics_count=hyphen_total, diagnostics_by_rule=hyphen_counts,
            diagnostics=hyphen_diagnostics, diagnostics_omitted=hyphen_total - len(hyphen_diagnostics),
            suppressed_count=hyphen_hidden, suppressed_by_rule=hyphen_hidden_counts,
            suppressed=hyphen_suppressed, suppressed_omitted=hyphen_hidden - len(hyphen_suppressed),
            sample_limit=cap, by_file=hyphen_files,
            unavailable_files=sum(row['status'] == 'UNAVAILABLE' for row in hyphen_files),
            conditions=list(document_conditional_hyphen_marker.CONDITIONS),
            runtime_binding_proved=False, execution_proved=False, coverage_complete=False,
            render_equivalence_proved=False, edit_permission=False, compiler_permission=False)
        report['conditional_include_forwarding'] = include_forwarding
        report.update(raw_sections)
        raw_diagnostics = _finish_mapping_sample(scoped_raw_diagnostics)
        raw_suppressed = _finish_mapping_sample(scoped_raw_suppressed)
        report['conditional_local_scope_raw_annotations'] = dict(rule_id=scoped_raw_rule,
            diagnostics_count=scoped_raw_seen, diagnostics=raw_diagnostics,
            diagnostics_omitted=scoped_raw_seen - len(raw_diagnostics),
            suppressed_count=scoped_raw_hidden, suppressed=raw_suppressed,
            suppressed_omitted=scoped_raw_hidden - len(raw_suppressed), sample_limit=cap,
            wrappers_examined=scoped_raw_wrappers, wrappers_in_dynamic_context=scoped_raw_dynamic,
            lexical_display_calls=scoped_raw_display, by_file=scoped_raw_files,
            unavailable_files=sum(row['status'] == 'UNAVAILABLE' for row in scoped_raw_files),
            conditions=list(document_conditional_local_raw_annot.CONDITIONS),
            duplicates_existing_hints_possible=True, runtime_binding_proved=False,
            execution_proved=False, coverage_complete=False, edit_permission=False, compiler_permission=False)
    # Use the actual encoding and indentation of the nested stderr section.
    require(len(json.dumps({'lint': report}, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8'))
            <= _LINT_REPORT_BYTES,
            'Document lint report exceeds its byte limit')
    return report


class _Parser(argparse.ArgumentParser):
    def print_help(self, file=None):
        super().print_help(file or sys.stderr)


def _stream_identity(stream):
    try:
        info = os.fstat(stream.fileno())
    except (AttributeError, io.UnsupportedOperation):
        return None
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))


def _same_streams():
    output = getattr(sys.stdout, 'buffer', sys.stdout)
    error = getattr(sys.stderr, 'buffer', sys.stderr)
    if output is error:
        return True
    left, right = _stream_identity(output), _stream_identity(error)
    return left is not None and left == right


def _regular_stream_id(stream):
    identity = _stream_identity(stream)
    return identity[:2] if identity is not None and identity[2] == stat.S_IFREG else None


def _path_id(path):
    try:
        info = os.stat(path)
    except (OSError, ValueError):
        return None
    return (info.st_dev, info.st_ino) if stat.S_ISREG(info.st_mode) else None


def _stream_collisions(protected):
    protected = {item for item in protected if item is not None}
    return (_regular_stream_id(getattr(sys.stdout, 'buffer', sys.stdout)) in protected,
            _regular_stream_id(getattr(sys.stderr, 'buffer', sys.stderr)) in protected)


def _write_pdf(data, cancel):
    """Stream validated bytes with cooperative cancellation for pipe consumers."""
    output = sys.stdout.buffer
    try:
        fd = output.fileno()
    except (AttributeError, io.UnsupportedOperation):
        if cancel is not None and cancel.is_set():
            raise runtime.CompilerFailure('cancelled', kind=runtime.CompilerFailureKind.CANCELLED)
        if output.write(data) != len(data):
            raise OSError('stdout accepted only part of the PDF')
        output.flush()
        return
    original_blocking = os.get_blocking(fd)
    try:
        os.set_blocking(fd, False)
        view = memoryview(data)
        offset = 0
        while offset < len(view):
            if cancel is not None and cancel.is_set():
                raise runtime.CompilerFailure('cancelled', kind=runtime.CompilerFailureKind.CANCELLED)
            _, ready, _ = select.select([], [fd], [], 0.1)
            if not ready:
                continue
            try:
                count = os.write(fd, view[offset:offset + 65536])
            except BlockingIOError:
                continue
            if count <= 0:
                raise OSError('stdout accepted no PDF bytes')
            offset += count
        output.flush()
    finally:
        os.set_blocking(fd, original_blocking)


def main(arguments=None):
    # Reporting on the same file descriptor would corrupt the PDF stream.
    # A refusal must be silent because stderr itself aliases stdout.
    if _same_streams():
        return 2
    # Before discovery, the complete input set is not known. Never append an
    # error report to a regular file which might be an uncaptured project input.
    if _regular_stream_id(getattr(sys.stderr, 'buffer', sys.stderr)) is not None:
        return 2
    parser = _Parser(prog='typst-style compile --compile-original', description=__doc__, allow_abbrev=False)
    parser.add_argument('--root', required=True)
    parser.add_argument('--entrypoint', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--discover-dependencies', action='store_true')
    parser.add_argument('--lint-document', action='store_true',
                        help='Run partial document diagnostics on the same captured sources before compiling')
    parser.add_argument('--rule-set', choices=('core', 'pde'),
                        help='Explicit document lint rule set (requires --lint-document)')
    parser.add_argument('--lint-details', action='store_true',
                        help='Include up to 10,000 diagnostics and suppressed entries instead of 50')
    parser.add_argument('--lint-local-hints', action='store_true',
                        help='Include separate conditional source-local PDE hints (requires --lint-document --rule-set pde)')
    parser.add_argument('--pdf-stdout', action='store_true', required=True,
                        help='Emit the verified original PDF bytes on stdout; report JSON on stderr')
    words = list(sys.argv[1:] if arguments is None else arguments)
    seen = set()
    for word in words:
        option = word.split('=', 1)[0]
        if option in parser._option_string_actions and option not in ('-h', '--help'):
            if option in seen:
                parser.error(f'Duplicate option: {option}')
            seen.add(option)
    args = parser.parse_args(words)
    if (args.rule_set is not None or args.lint_details) and not args.lint_document:
        parser.error('--rule-set and --lint-details require --lint-document')
    if args.lint_local_hints and (not args.lint_document or args.rule_set != 'pde'):
        parser.error('--lint-local-hints requires --lint-document --rule-set pde')
    output_identity = _stream_identity(getattr(sys.stdout, 'buffer', sys.stdout))
    if output_identity is not None and output_identity[2] not in (stat.S_IFIFO, stat.S_IFSOCK):
        print(json.dumps(dict(schema=1, mode='original-compile', status=2,
                              error='PDF stdout must be a pipe or socket; regular-file aliases are refused',
                              pdf_streamed=False, lint_performed=False, global_coverage_complete=False,
                              output_stream_note=('Shell redirection may already have truncated its target '
                                                  'before this program starts.'))), file=sys.stderr)
        return 2
    import offline_compile_cli
    signals = offline_compile_cli._active_signals
    cancel = signals.cancel_event if signals is not None else None
    starts = document_preview_compile.PreviewCompilerStarts()
    phase = 'input'
    result = dict(schema=1, tool_version=VERSION, mode='original-compile', status=2,
                  lint_attempted=False, lint_completed=False, lint_performed=False, global_coverage_complete=False,
                  source_files_opened_for_write_by_tool=False, source_application_authority=False,
                  publication_authority=False, dependency_closure_proved=False,
                  live_source_freshness_proved=False, pdf_streamed=False,
                  output_stream_note=('Stdout and stderr are caller-managed; shell redirection can truncate files '
                                      'before this program starts. Stdout requires a pipe or socket. '
                                      'A broken stdout stream can contain a partial PDF. '
                                      'Stderr regular files are refused without a report.'))
    try:
        root = _absolute_root(args.root)
        require(args.root == str(root), 'Compile root must be canonical and absolute')
        relative(args.entrypoint)
        require(args.entrypoint.endswith('.typ'), 'Compile entrypoint must be a Typst path')
        # Reject known aliases before reading these paths. The shell may already
        # have opened or truncated either stream's target before our process ran.
        out_collision, err_collision = _stream_collisions((_path_id(args.manifest), _path_id(root / args.entrypoint)))
        if err_collision:
            return 2  # Do not append even an error report to a protected input.
        require(not out_collision, 'PDF stdout aliases the manifest or entrypoint')
        require(type(args.manifest) is str and args.manifest.startswith('/')
                and str(Path(args.manifest)) == args.manifest, 'Manifest must be a canonical absolute path')
        raw_manifest, manifest_evidence = runtime._capture(Path(args.manifest), MAX_MANIFEST_BYTES)
        roots, requests, allow = parse_manifest_bytes(raw_manifest, root, allow_empty=args.discover_dependencies)
        known = [manifest_evidence.identity[:2]]
        for request in requests:
            if (type(request.root) is str and request.root in roots
                    and type(roots[request.root]) is str and type(request.path) is str):
                document_resources._relative(request.path)
                known.append(_path_id(Path(roots[request.root]) / request.path))
        out_collision, err_collision = _stream_collisions(known)
        if err_collision:
            return 2
        require(not out_collision, 'PDF stdout aliases a requested input')
        _, source = read_snapshot_file(checked_path(root, args.entrypoint), include_context.MAX_SOURCE_BYTES)
        context = include_context.load_include_context(root, ((args.entrypoint, source),), budget='book-v1')
        observed = expected_pdf = None
        fonts = {}
        if args.discover_dependencies:
            phase = 'discovery'
            raw_deps, observed, fonts, expected_pdf = document_preview_discover.discover(
                context, roots, requests, entrypoint=args.entrypoint,
                allow_apple_asset_fonts=allow, cancel_event=cancel, starts=starts)
            selected = {(item.root, item.path): item for item in requests}
            for root_id, path in observed:
                role = ('source' if root_id == 'project' and path.endswith('.typ') else
                        'resource' if root_id == 'project' else 'package')
                selected.setdefault((root_id, path), document_resources.ResourceRequest(root_id, path, role))
            for item in context.sources:
                selected.setdefault(('project', item.path),
                                    document_resources.ResourceRequest('project', item.path, 'source'))
            requests = list(selected.values())
        phase = 'capture'
        snapshot = document_resources.capture(roots, requests, allow_apple_asset_fonts=allow)
        protected = [manifest_evidence.identity[:2]] + [item.evidence.identity[:2] for item in snapshot.files]
        out_collision, err_collision = _stream_collisions(protected)
        if err_collision:
            return 2
        require(not out_collision, 'PDF stdout aliases a captured input')
        if observed is not None:
            captured_fonts = {(item.request.root, item.request.path): item.sha256 for item in snapshot.files
                              if item.request.role == 'font'}
            require(captured_fonts == fonts, 'Selected fonts changed after dependency discovery')
            comparison = document_dependencies.compare(snapshot, raw_deps, cwd=str(root), roots=roots)
            require(not comparison.missing, 'A discovered dependency is absent from the resource capture')
        if args.lint_document:
            phase = 'lint'
            result['lint_attempted'] = True
            result['lint'] = lint_captured_document(context, snapshot, rule_set=args.rule_set or 'core',
                                                    details=args.lint_details, local_hints=args.lint_local_hints,
                                                    cancel_event=cancel)
            result['lint_completed'] = result['lint_performed'] = True
        phase = 'compile'
        compiled = document_preview_compile.compile_original(context, snapshot, entrypoint=args.entrypoint,
            cancel_event=cancel, expected_dependencies=observed, expected_baseline_sha256=expected_pdf, starts=starts)
        if cancel is not None and cancel.is_set():
            raise runtime.CompilerFailure('cancelled', kind=runtime.CompilerFailureKind.CANCELLED)
        result.update(status=0, phase='complete', pdf_sha256=compiled.pdf_sha256,
            observed_dependencies=compiled.dependency_count, unobserved_captured_resources=compiled.unobserved_capture_count,
            selected_system_font_files=compiled.system_font_files, compiler_sha256=compiled.compiler_sha256,
            resource_count=len(snapshot.files), source_count=len(context.sources),
            discovery_performed=observed is not None,
            note=('Compilation succeeded; document lint coverage is incomplete' if args.lint_document else
                  'Compilation succeeded; lint was not performed and rule coverage is incomplete'))
        phase = 'stream'
        out_collision, err_collision = _stream_collisions(protected)
        if (_same_streams() or err_collision
                or _regular_stream_id(getattr(sys.stderr, 'buffer', sys.stderr)) is not None):
            return 2
        require(not out_collision, 'PDF stdout aliases a captured input')
        require(_stream_identity(getattr(sys.stdout, 'buffer', sys.stdout)) == output_identity,
                'PDF stdout stream changed before transfer')
        _write_pdf(compiled.pdf, cancel)
        if cancel is not None and cancel.is_set():
            raise runtime.CompilerFailure('cancelled', kind=runtime.CompilerFailureKind.CANCELLED)
        result['pdf_streamed'] = True
    except (InputError, SafetyError, PackageInputError, OSError, ValueError, runtime.CompilerFailure) as error:
        result.update(status=3 if phase == 'stream' else 2, phase=phase, error=str(error), pdf_streamed=False)
        if phase == 'stream':
            result['stdout_may_be_partial'] = True
        if isinstance(error, runtime.CompilerFailure) and error.invocation is not None:
            starts.observe(error.invocation)
            if error.invocation.stderr:
                result['compiler_diagnostics'] = error.invocation.stderr[:8192].decode('utf-8', errors='replace')
        elif isinstance(error, document_preview_compile.PreviewCompilerDiagnostics):
            starts.observe(error.invocation)
            if error.invocation.stderr:
                result['compiler_diagnostics'] = error.invocation.stderr[:8192].decode('utf-8', errors='replace')
    stages = starts.stages()
    result['compiler_stages_started'] = stages
    result['compiler_invoked'] = starts.compiler_invoked()
    result['compiler_version_probe_invoked'] = starts.version_probe_invoked()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2), file=sys.stderr)
    if signals is not None and signals.first_signal is not None:
        return -signals.first_signal
    return result['status']


if __name__ == '__main__':
    sys.exit(main())
