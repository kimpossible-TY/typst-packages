"""Bounded, read-only import reachability inventory for captured document sources.

This enumerates unresolved *distinct static requests* in a locally captured
relative import graph. It does not evaluate packages or dynamic imports, prove
that an include executes, or grant an edit or compiler permission.
"""
from collections import defaultdict, deque
import json

import import_graph
import include_context
from typst_lint import SafetyError

MAX_WORK = 2_000_000
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_ITEMS = 50_000


def _category(item):
    reason = item.reason
    if reason == 'Package evaluation and package-relative dependency graphs are unsupported':
        return 'package-not-evaluated'
    if reason == 'Dynamic or escaped import is unsupported':
        return 'dynamic-or-escaped-import'
    if reason == 'Explicitly imported source is missing':
        return 'relative-source-missing'
    if reason == 'Import graph depth limit exceeded':
        return 'graph-depth-limit'
    if reason == 'Malformed dependency syntax':
        return 'malformed-source'
    return 'other-unresolved-import'


def analyze(context):
    """Capture explicit project-relative imports from an issued include context."""
    include_context.assert_include_context(context)
    graph = import_graph.load_import_graph(context.project_root,
        tuple((source.path, source.source) for source in context.sources))
    include_context.assert_include_context(context)
    captured = {source.path: source for source in graph.sources}
    if any(source.path not in captured or captured[source.path].source != source.source
           for source in context.sources):
        raise SafetyError('Import graph does not match the issued include capture')
    outgoing, unavailable = defaultdict(list), defaultdict(list)
    for edge in graph.edges:
        outgoing[edge.importer].append(edge)
    for item in graph.unavailable:
        unavailable[item.path].append(item)
    work = item_count = output_bytes = 0
    rows = []
    for source in context.sources:
        pending = deque([source.path])
        # Each source gets a shortest statically captured path, not a runtime trace.
        previous = {source.path: None}
        obligations = []
        while pending:
            path = pending.popleft()
            for item in unavailable[path]:
                work += 1
                if work > MAX_WORK:
                    raise SafetyError('Import obligation graph work exceeds its budget')
                chain, cursor = [], path
                while previous[cursor] is not None:
                    prior, edge = previous[cursor]
                    chain.append(dict(source=prior, request=edge.request, target=cursor))
                    cursor = prior
                    work += 1
                    if work > MAX_WORK:
                        raise SafetyError('Import obligation graph work exceeds its budget')
                chain.reverse()
                obligations.append(dict(importer=path, request=item.request,
                                        category=_category(item), relative_import_path=chain))
                item_count += 1
                if item_count > MAX_OUTPUT_ITEMS:
                    raise SafetyError('Import obligation result item count exceeds its budget')
                output_bytes += len(json.dumps(obligations[-1], ensure_ascii=False).encode('utf-8'))
                if output_bytes > MAX_OUTPUT_BYTES:
                    raise SafetyError('Import obligation result exceeds its byte budget')
            for edge in outgoing[path]:
                work += 1
                if work > MAX_WORK:
                    raise SafetyError('Import obligation graph work exceeds its budget')
                if edge.target not in previous:
                    previous[edge.target] = (path, edge)
                    pending.append(edge.target)
        row = dict(path=source.path, source_sha256=source.identity.source_sha256,
                   reachable_source_count=len(previous), obligations=obligations)
        output_bytes += len(json.dumps({key: value for key, value in row.items() if key != 'obligations'},
                                       ensure_ascii=False).encode('utf-8'))
        if output_bytes > MAX_OUTPUT_BYTES:
            raise SafetyError('Import obligation result exceeds its byte budget')
        rows.append(row)
    report = dict(by_source=rows, relative_sources=[dict(path=source.path, source_sha256=source.sha256)
                                                   for source in graph.sources],
                  import_graph_source_count=len(graph.sources),
                  import_graph_edge_count=len(graph.edges), source_obligation_pair_count=item_count,
                  scope='captured-relative-import-graph', runtime_reachability_proved=False,
                  package_evaluation_performed=False, dynamic_imports_complete=False,
                  request_count_is_location_count=False, edit_permission=False, compiler_permission=False)
    if len(json.dumps(report, ensure_ascii=False).encode('utf-8')) > MAX_OUTPUT_BYTES:
        raise SafetyError('Import obligation result exceeds its byte budget')
    include_context.assert_include_context(context)
    return report
