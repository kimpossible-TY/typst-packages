"""Recheck selected reference splits against a derived final source graph.

This is conditional static evidence only. It does not execute wrappers or
reference show rules and never grants compiler, publication or write authority.
"""
from bisect import bisect_right
from dataclasses import asdict
from types import SimpleNamespace
import json

import conditional_highlight as high
import conditional_modern_highlight as modern
import document_intermediate_context as intermediate
import document_math_builtin_evidence as math_evidence
import document_modern_highlight_preview as preview
import document_sequence_preview as sequence
from document_gap_preview import _MappedEdits
from edit_plan import Edit
import import_graph
import scoped_reference_preview as scoped
from syntax_client import parse
from typst_lint import SafetyError, digest, load_rules

MAX_FINAL_ROUTE_WORK = 100_000_000


class RouteBudgetExceeded(ValueError):
    """The optional final candidate route proof exhausted its own budget."""


class _Tracer(modern._ModernAnalyzer):
    def __init__(self, session, variant, tree, targets, remaining):
        super().__init__(session, variant, scoped_callbacks=True)
        self.target_tree, self.targets = tree, targets
        self.remaining = remaining

    def _spend(self, previous):
        self.remaining[0] -= self.ast_work - previous
        if self.remaining[0] < 0:
            raise RouteBudgetExceeded('Final reference route work budget exceeded')

    def charge_ast(self, tree, *, passes=3):
        before = self.ast_work
        result = super().charge_ast(tree, passes=passes)
        self._spend(before)
        return result

    def charge_scope(self, env, sources, packages, requests, context, *, callbacks=(), parameters=()):
        before = self.ast_work
        result = super().charge_scope(env, sources, packages, requests, context,
                                      callbacks=callbacks, parameters=parameters)
        self._spend(before)
        return result

    def body_math(self, tree, call_id):
        node = tree.nodes[call_id]
        # Emit route evidence for exact generated helper fragments; this is not
        # a claim that each fragment itself still contains math.
        return (tree is self.target_tree and (node.start, node.end) in self.targets), None


def _json(value):
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _normalized_route(value, path):
    value = _json(value)
    def visit(item):
        if isinstance(item, list):
            return [visit(x) for x in item]
        if isinstance(item, dict):
            return {key: ('<edited-source>' if key == 'source_sha256' and item.get('path') == path else visit(child))
                    for key, child in item.items()}
        return item
    return visit(value)


def _reanalysis_work(tree):
    """Precharge the repeated source analyzers' known nonlinear scans."""
    nodes, source = tree.nodes, tree.source
    newlines = [i for i, byte in enumerate(source) if byte == 10]
    parents = [None] * len(nodes)
    work, directives = len(nodes) + len(source), 0
    for index, node in enumerate(nodes):
        work += node.end - node.start
        for child in node.children:
            parents[child] = index
        if node.kind in ('Ident', 'Text', 'FuncCall'):
            line = bisect_right(newlines, node.start - 1)
            start = 0 if line == 0 else newlines[line - 1] + 1
            end = len(source) if line == len(newlines) else newlines[line]
            work += end - start + (len(newlines) + 1).bit_length()
        if node.kind == 'LineComment':
            text = source[node.start:node.end]
            if text[2:].lstrip(b' \t').startswith(b'typst-lint:'):
                directives += 1
                parent = parents[index]
                siblings = 0 if parent is None else len(nodes[parent].children)
                work += 2 * node.start + 2 * siblings + len(text) ** 3
        if work * 20 > MAX_FINAL_ROUTE_WORK:
            raise RouteBudgetExceeded('Final reference route work budget exceeded')
    return (work + directives * len(nodes)) * 20


def verify(view, analyzer, final, edits_by_path, baseline, diagnostics):
    """Reparse final candidates and replay each added helper's captured route."""
    intermediate.assert_intermediate(view)
    analyzer._check()
    baseline_by_path = {row['path']: row for row in baseline['by_file'] if row['verification'] != 'withheld'}
    diag_by_path = {row['path']: {(item['start'] - 1, item['end']): item for item in row['diagnostics']}
                    for row in diagnostics['by_file']}
    targets_by_path = {}
    for path, edits in edits_by_path.items():
        original = baseline_by_path.get(path, {})
        prior = {(e['start'], e['end'], e['replacement'], e['rule_id']) for e in original.get('edits', ())}
        extra = [e for e in edits if (e.start, e.end, e.replacement.decode('utf-8'), high.RULE_ID) not in prior]
        if extra:
            targets_by_path[path] = extra
    if not targets_by_path:
        return dict(paths=(), generated_calls=0, work=0)

    # Existing graph derivation validates every source/import/include identity,
    # exact step bytes and hash chain before the tracer sees a candidate.
    candidates = {item.path: final['project', item.path] for item in view.context.sources}
    changed = {}
    for path, edits in edits_by_path.items():
        before, after = view.sources['project', path], final['project', path]
        changed[path] = dict(baseline_sha256=digest(before), candidate_sha256=digest(after),
            steps=[dict(baseline_sha256=digest(before), candidate_sha256=digest(after),
            edits=[dict(start=e.start, end=e.end, replacement=e.replacement.decode('utf-8')) for e in edits])])
    work = len(view.context.sources) * len(view.context.edges)
    if (sum(len(item.tree.nodes) for item in view.context.sources) * 3 > MAX_FINAL_ROUTE_WORK
            or sum(map(len, candidates.values())) > intermediate.inc.MAX_CONTEXT_BYTES):
        raise RouteBudgetExceeded('Final reference route work budget exceeded')
    # Both old and candidate include/import sites walk their entire owner
    # ancestry during derivation. Parse candidates first and precharge those
    # walks, all source bytes, and every FuncCall's target-range lookup.
    for item in view.context.sources:
        raw = candidates[item.path]
        candidate_tree = parse(raw)
        if candidate_tree.nodes[0].errors:
            raise SafetyError('Final reference candidate is malformed')
        for tree in (item.tree, candidate_tree):
            depths = [0] * len(tree.nodes)
            work += len(tree.source) + len(tree.nodes)
            for index, node in enumerate(tree.nodes):
                for child in node.children:
                    depths[child] = depths[index] + 1
                if node.kind in ('ModuleInclude', 'ModuleImport'):
                    work += 4 * depths[index] + 2 * (node.end - node.start)
                if item.path in targets_by_path and node.kind == 'FuncCall':
                    work += (len(targets_by_path[item.path]) + 1).bit_length()
                if work > MAX_FINAL_ROUTE_WORK:
                    raise RouteBudgetExceeded('Final reference route work budget exceeded')
    context = intermediate._derived_include(view.context, candidates, changed)
    graph = intermediate._derived_import(view.graph, view.context, context)
    session = SimpleNamespace(_graph=graph, _namespaces=analyzer._session._namespaces)
    remaining, call_count = [MAX_FINAL_ROUTE_WORK - work], 0
    scope_budget = [scoped.MAX_SCOPE_WORK]
    catalog = load_rules()[0]['rules']
    # Independent per-source analyzers each start with empty module caches.
    # Charge the full captured graph for both the namespace and math replay
    # on every target instead of assuming their facade walks are shared.
    graph_work = (len(view.graph.sources) * len(view.context.sources)
                  + sum(len(item.source) + len(item.manifest)
                        for item in analyzer._session._namespaces.packages))
    for item in view.graph.sources:
        captured = view.context.source_for(item.path)
        graph_tree = captured.tree if captured is not None and captured.source == item.source else parse(item.source)
        depths = [0] * len(graph_tree.nodes)
        graph_work += 4 * len(graph_tree.nodes) + len(graph_tree.source)
        for index, node in enumerate(graph_tree.nodes):
            for child in node.children:
                depths[child] = depths[index] + 1
            if node.kind in ('ModuleImport', 'ModuleInclude'):
                graph_work += 4 * depths[index] + 2 * (node.end - node.start)
            if graph_work > MAX_FINAL_ROUTE_WORK:
                raise RouteBudgetExceeded('Final reference route work budget exceeded')
    for path, extra in sorted(targets_by_path.items()):
        tree = context.source_for(path).tree
        all_edits = edits_by_path[path]
        original = view.context.source_for(path).tree
        diagnostic_row = next(row for row in diagnostics['by_file'] if row['path'] == path)
        findings = diag_by_path[path]
        selected = [findings.get((edit.start, edit.end)) for edit in all_edits]
        if any(item is None for item in selected) or len({(x['start'], x['end']) for x in selected}) != len(selected):
            raise SafetyError('Reference edits do not uniquely match issued diagnostics')
        # Independently reissue source selection, math binding, candidate
        # syntax, suppression and scoped reference checks. Do not trust a
        # self-consistent but forged batch report.
        remaining[0] -= _reanalysis_work(original) + 2 * graph_work
        if remaining[0] < 0:
            raise RouteBudgetExceeded('Final reference route work budget exceeded')
        independently_issued = analyzer.analyze(original, path)
        if not sequence._same_data(independently_issued, diagnostic_row):
            raise SafetyError('Reference source diagnostics or suppressions changed on independent replay')
        math = math_evidence._analyze_report(analyzer, path, diagnostic_row)
        spans = frozenset((item['start'], item['end']) for item in math['findings'])
        try:
            captured = (scoped.CapturedScopedReferences(analyzer, original, path, budget=scope_budget)
                        if any(b'.ref)' in original.source[x['start']:x['end']] for x in selected) else None)
            checked = preview._preview_source(analyzer, original, path, selected, catalog,
                proven_math_spans=spans, allow_direct_references=True, allow_scoped_references=True,
                scoped_budget=scope_budget, scoped_capture=captured)
        except scoped.ScopedReferenceBudgetExceeded as error:
            raise RouteBudgetExceeded('Final reference scoped work budget exceeded') from error
        if checked is not None and checked.get('reason') == scoped.BUDGET_REASON:
            raise RouteBudgetExceeded('Final reference scoped work budget exceeded')
        expected_rows = [dict(start=e.start, end=e.end, replacement=e.replacement.decode('utf-8'), rule_id=high.RULE_ID)
                         for e in all_edits]
        if (type(checked) is not dict or checked.get('verification') !=
                'conditional-modern-highlight-syntax-directives-and-reanalysis'
                or checked.get('edits') != expected_rows or checked.get('baseline_sha256') != digest(original.source)
                or checked.get('candidate_sha256') != digest(tree.source)):
            raise SafetyError('Reference candidate failed independent source replay')
        mapper = _MappedEdits(all_edits)
        ranges = []
        for edit in extra:
            finding = diag_by_path.get(path, {}).get((edit.start, edit.end))
            if finding is None:
                raise SafetyError('Added reference split has no unique issued diagnostic')
            start = mapper.mapped(edit.start)
            ranges.append((start, start + len(edit.replacement), finding))
        ranges.sort(key=lambda row: row[0]); starts = [row[0] for row in ranges]
        targets, matched = {}, set()
        for index, node in enumerate(tree.nodes):
            if node.kind != 'FuncCall' or not node.start or tree.source[node.start - 1:node.start] != b'#':
                continue
            position = bisect_right(starts, node.start - 1) - 1
            if position < 0 or not (ranges[position][0] <= node.start - 1 < node.end <= ranges[position][1]):
                continue
            parts = import_graph.significant(tree, index)
            if (len(parts) == 2 and tree.nodes[parts[0]].kind == 'Ident'
                    and import_graph.raw(tree, parts[0]) == b'highlighted'):
                targets[(node.start, node.end)] = ranges[position][2]
                matched.add(position)
        if len(matched) != len(extra):
            raise SafetyError('Added reference split has no generated helper route to replay')
        remaining[0] -= 2 * len(tree.nodes) + sum(span[0] for span in targets)
        if remaining[0] < 0:
            raise RouteBudgetExceeded('Final reference route work budget exceeded')
        tracer = _Tracer(session, analyzer._variant, tree, frozenset(targets), remaining)
        report = high._analyze(tree, path, tracer)
        if tracer.exhausted:
            raise RouteBudgetExceeded('Final reference route work budget exceeded')
        actual = {(f.start, f.end): f for f in report.diagnostics}
        if len(actual) != len(report.diagnostics) or set(actual) != set(targets) or report.suppressed:
            raise SafetyError('Final reference helper routes are unavailable or suppressed')
        for span, old in targets.items():
            new = _json(asdict(actual[span]))
            for key in ('route', 'content_context', 'source_hashes', 'package_evidence', 'package_requests'):
                if _normalized_route(new[key], path) != _normalized_route(old[key], path):
                    raise SafetyError('Final reference helper route identity changed')
            mapped_callbacks = []
            for callback in old['callback_context']:
                value = dict(callback)
                for name in ('call_start', 'call_end', 'body_start', 'body_end'):
                    value[name] = mapper.mapped(value[name])
                mapped_callbacks.append(value)
            if _json(new['callback_context']) != _json(mapped_callbacks):
                raise SafetyError('Final reference callback owner changed')
        call_count += len(targets)
    intermediate.assert_intermediate(view)
    analyzer._check()
    return dict(paths=tuple(sorted(targets_by_path)), generated_calls=call_count,
                work=MAX_FINAL_ROUTE_WORK - remaining[0])
