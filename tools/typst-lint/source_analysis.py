"""Compose supported source rules and validate their whole in-memory candidate.

Rule coverage remains bounded and protected write/compiler contracts are absent.
Successful verification here therefore yields partial proposals only.
"""
from collections import Counter
from dataclasses import dataclass, replace
import re

from anchor_overlay_rules import RULE_IDS as ANCHOR_RULES, analyze as anchors
from marker_bounds_rules import RULE_IDS as MARKER_RULES, analyze as markers
from candidate_convergence import converge
from book_provenance import EntrypointSnapshot
from book_rules import RULE_IDS as BOOK_RULES, analyze as books
from continuation_rules import RULE_ID as CONTINUATION, analyze as continuation
from cancel_rules import RULE_ID as CANCEL, analyze as cancellations
from edit_plan import plan
from import_graph import rebase_graph
from import_rules import RULE_IDS as IMPORT_RULES, ORDER as IMPORT_ORDER, analyze as imports
from highlight_rules import RULE_ID as HIGHLIGHT, analyze as highlights
from display_rules import RULE_IDS as DISPLAY_RULES, analyze as displays
from footnote_rules import RULE_IDS as FOOTNOTE_RULES, analyze as footnotes
from flowbox_rules import RULE_ID as FLOWBOX, analyze as flowboxes
from paragraph_rules import RULE_IDS as PARAGRAPH_RULES, analyze as paragraphs
from proof_rules import RULE_ID as PROOF, analyze as proofs
from prose_marker_rules import RULE_ID as PROSE_MARKER, analyze as prose_markers
from reference_rules import RULE_IDS as REFERENCE_RULES, analyze as references
from script_rule import RULE_ID as SCRIPT, analyze as scripts
from scoped_rules import RULE_IDS as SCOPED_RULES, analyze as scoped
from static_resolver import resolve
from syntax_client import parse
from theme_rules import RULE_IDS as THEME_RULES, analyze as themes
from typst_lint import SafetyError, load_rules
from suppression import resolve as directive_scopes


def rule_ids(pack):
    core = (SCRIPT, *REFERENCE_RULES, *THEME_RULES, *SCOPED_RULES, *BOOK_RULES, *MARKER_RULES, *ANCHOR_RULES)
    return (*core, *PARAGRAPH_RULES, *DISPLAY_RULES, CONTINUATION,
            *FOOTNOTE_RULES, HIGHLIGHT, PROOF, PROSE_MARKER, CANCEL, FLOWBOX, *IMPORT_RULES) if pack == 'pde' else core


def _analyze(tree, path, pack, fallback_language, packages, reference_catalog, entrypoint, import_graph=None):
    results = {SCRIPT: scripts(tree, path, _verify=False)}
    resolution = resolve(tree, fallback_language=fallback_language, packages=packages)
    results.update(references(tree, path, resolution, reference_catalog))
    results.update(themes(tree, path, resolution))
    results.update(scoped(tree, path, packages=packages))
    results.update(markers(tree, path, packages=packages))
    results.update(anchors(tree, path, packages=packages))
    results.update(books(tree, path, resolution, entrypoint=entrypoint))
    if pack == 'pde':
        results.update(imports(tree, path, graph=import_graph))
        results.update(paragraphs(tree, path, resolution))
        results[PROOF] = proofs(tree, path, resolution)
        results[PROSE_MARKER] = prose_markers(tree, path, resolution, allow_followup_casing=True)
        results[HIGHLIGHT] = highlights(tree, path, resolution)
        results[CANCEL] = cancellations(tree, path, resolution)
        results[FLOWBOX] = flowboxes(tree, path, resolution)
        results.update(displays(tree, path, resolution))
        results[CONTINUATION] = continuation(tree, path, resolution)
        results.update(footnotes(tree, path, resolution, resolution.spacer_proof))
    return results


def _import_statement_moves(before, after, edits, order_edits):
    """Map only unique unchanged root import syntax inside a proven reorder.

    Generic offsets cannot describe reordered statements. Exact AST spans and
    byte identity certify movement; no string replacements in arbitrary source
    or diagnostics are used to infer ownership.
    """
    def statements(tree):
        result = []
        siblings = tree.nodes[0].children
        for offset, i in enumerate(siblings):
            node = tree.nodes[i]
            if node.kind != 'ModuleImport' or offset == 0:
                continue
            prefix = tree.nodes[siblings[offset - 1]]
            if prefix.kind == 'Hash' and prefix.end == node.start:
                result.append((prefix.start, node.end, tree.source[prefix.start:node.end]))
        return result
    old, new = statements(before), statements(after)
    moves = []
    for edit in order_edits:
        if edit not in edits:
            continue
        target_start = edit.start + sum(len(e.replacement) - (e.end - e.start)
                                       for e in edits if e.end <= edit.start)
        left = [(a, b, raw) for a, b, raw in old if edit.start <= a and b <= edit.end]
        right = [(a, b, raw) for a, b, raw in new
                 if target_start <= a and b <= target_start + len(edit.replacement)]
        if Counter(raw for _, _, raw in left) != Counter(raw for _, _, raw in right):
            raise SafetyError('Import movement lost exact statement identity')
        if any(count != 1 for count in Counter(raw for _, _, raw in left).values()):
            raise SafetyError('Import movement has ambiguous statement identity')
        targets = {raw: a for a, _, raw in right}
        moves.extend((a, b, targets[raw]) for a, b, raw in left)
    return tuple(moves)


def _offset(source, diagnostic):
    # Diagnostics use LF line counts and Unicode character columns, not bytes.
    lines = source.split(b'\n')
    start = sum(len(line) + 1 for line in lines[:diagnostic.line - 1])
    return start + len(lines[diagnostic.line - 1].decode('utf-8')[:diagnostic.column - 1].encode('utf-8'))


def _findings(source, results, ignored, transform=lambda position: position, *, suppressed=False):
    findings = Counter()
    for rid, result in results.items():
        if suppressed or rid not in ignored:
            for finding in result.suppressed if suppressed else result.diagnostics:
                explanation = finding.explanation
                related = re.fullmatch(r'Duplicate literal scope namespace; first declaration at byte (\d+)', explanation)
                if rid == 'core.unique-scope-namespace' and related:
                    explanation = f'Duplicate literal scope namespace; first declaration at byte {transform(int(related.group(1)))}'
                findings[(rid, explanation, transform(_offset(source, finding)))] += 1
    return findings


@dataclass(frozen=True)
class AnalysisContext:
    """Already captured inputs; candidate analysis never rereads the filesystem."""
    tree: object
    path: str
    pack: str = 'core'
    fallback_language: str | None = None
    packages: tuple = ()
    reference_catalog: object = None
    entrypoint: object = None
    import_graph: object = None
    ignored: frozenset = frozenset()

    def analyze(self, checked):
        if checked.mode != self.tree.mode:
            raise SafetyError('Candidate context changed parser mode')
        entry = self.entrypoint
        candidate_entry = (replace(entry, source=checked.source)
            if type(entry) is EntrypointSnapshot and entry.path == self.path
            and type(entry.source) is bytes and entry.source == self.tree.source else entry)
        graph = rebase_graph(self.import_graph, self.path, self.tree.source, checked.source)
        results = _analyze(checked, self.path, self.pack, self.fallback_language,
                           self.packages, self.reference_catalog, candidate_entry, graph)
        return {rid: replace(value, edits=()) if rid in self.ignored else value
                for rid, value in results.items()}


def _validate_final(tree, results, ignored, candidate):
    before = _findings(tree.source, results, ignored, candidate.map_offset)
    after = _findings(candidate.content, candidate.final_results, ignored)
    if after - before:
        raise SafetyError('Combined candidate introduces a new finding')
    if _findings(tree.source, results, ignored, candidate.map_offset, suppressed=True) != _findings(
            candidate.content, candidate.final_results, ignored, suppressed=True):
        raise SafetyError('Combined candidate changed directive suppression ownership')


def _movement_proofs(before_tree, after_tree, edits, before):
    return _import_statement_moves(before_tree, after_tree, edits,
                                   before[IMPORT_ORDER].edits if IMPORT_ORDER in before else ())


@dataclass(frozen=True)
class ComposedResult:
    diagnostics: tuple = ()
    edits: tuple = ()
    skips: tuple = ()
    parse_errors: tuple = ()
    suppressed: tuple = ()
    attributed_edits: tuple = ()
    repair_passes: int = 0
    repair_steps: tuple = ()


def _mapped_reasons(reasons, transform):
    result = Counter()
    for reason in reasons:
        consumed = False
        def offset(match):
            nonlocal consumed
            position = transform(int(match.group(1)))
            if position is None:
                consumed = True
            return f'at byte {position}'
        mapped = re.sub(r'at byte (\d+)', offset, reason)
        if not consumed:
            result[mapped] += 1
    return result


def _scope_owners(tree, mapping=None):
    rules = {r['id'] for r in load_rules()[0]['rules']}
    result = []
    for scope in directive_scopes(tree, rules):
        target = next((n for n in tree.nodes if n.start == scope.start and n.end == scope.end), None)
        if target is None:
            target = next((n for n in tree.nodes if n.start == scope.start + 1 and n.end == scope.end
                           and tree.source[scope.start:scope.start + 1] == b'#'), None)
        if target is None:
            raise SafetyError('Directive target has no exact AST owner')
        # Outer bounds and syntax kind cannot authenticate an entirely replaced
        # target. Retain a meaningful original token, not just a surviving hash.
        anchor = target.start
        if target.kind == 'FuncCall':
            part = target.children[0]
            while tree.nodes[part].kind == 'Parenthesized':
                children = [i for i in tree.nodes[part].children
                            if tree.nodes[i].kind not in ('Space', 'LeftParen', 'RightParen')]
                if len(children) != 1:
                    raise SafetyError('Directive callee anchor is ambiguous')
                part = children[0]
            anchor = tree.nodes[part].start
        elif target.kind == 'Text':
            # The casing rule may replace only the initial byte; retain the
            # end of the original literal token as its source-origin anchor.
            anchor = target.end - 1
        anchor = mapping.map_offset(anchor) if mapping else anchor
        if anchor is None:
            raise SafetyError('Combined candidate consumed directive target origin')
        start = mapping.map_boundary(scope.start, side='right') if mapping else scope.start
        end = mapping.map_boundary(scope.end, side='left') if mapping else scope.end
        directive = mapping.map_offset(scope.directive_start) if mapping else scope.directive_start
        if start is None or end is None or directive is None:
            raise SafetyError('Combined candidate consumed directive ownership')
        result.append((scope.rules, scope.reason, start, end, directive, target.kind, anchor))
    return tuple(result)


def _validate_step(before_tree, before, after_tree, after, step):
    if before.keys() != after.keys():
        raise SafetyError('Combined candidate changed available rule coverage')
    for rid, original in before.items():
        if after[rid].parse_errors:
            raise SafetyError('Combined candidate has rule parse failures')
        if Counter(after[rid].skips) - _mapped_reasons(original.skips, step.map_offset):
            raise SafetyError('Combined candidate introduces an analysis refusal')
    if _scope_owners(before_tree, step) != _scope_owners(after_tree):
        raise SafetyError('Combined candidate changed directive suppression ownership')
    if _findings(before_tree.source, before, frozenset(), step.map_offset, suppressed=True) != _findings(
            after_tree.source, after, frozenset(), suppressed=True):
        raise SafetyError('Combined candidate changed directive suppression ownership')


def analyze(tree, path, *, pack='core', fallback_language=None, packages=(), reference_catalog=None,
            entrypoint=None, import_graph=None, ignored=frozenset(), _verify=True):
    """Filter config-ignored edits before verifying one composed candidate.

Baseline diagnostic offsets are mapped through every proposed edit before
comparison, so an existing unresolved finding is not mistaken for a new one
merely because an earlier script or blank line changed its coordinates.
"""
    results = _analyze(tree, path, pack, fallback_language, packages, reference_catalog, entrypoint, import_graph)
    for rid in ignored:
        if rid in results:
            results[rid] = replace(results[rid], edits=())
    proposals = tuple(edit for result in results.values() for edit in result.edits)
    if not proposals or not _verify:
        return results
    try:
        context = AnalysisContext(tree, path, pack, fallback_language, tuple(packages),
                                  reference_catalog, entrypoint, import_graph, ignored)
        candidate = converge(tree, results, context.analyze, _validate_step,
            lambda value: _validate_final(tree, results, ignored, value),
            movement_proofs=_movement_proofs)
    except SafetyError as error:
        reason = f'{error}; no combined edits proposed'
        return {rid: replace(result, edits=(), skips=(*result.skips, reason)) for rid, result in results.items()}
    grouped = {rid: [] for rid in results}
    for edit in candidate.edits:
        grouped[edit.primary_rule_id].append(edit)
    return {rid: ComposedResult(result.diagnostics, tuple(item.edit for item in grouped[rid]),
                 result.skips, result.parse_errors, result.suppressed,
                 tuple(grouped[rid]), candidate.passes, candidate.steps)
            for rid, result in results.items()}
