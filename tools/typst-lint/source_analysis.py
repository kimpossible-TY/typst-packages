"""Compose supported source rules and validate their whole in-memory candidate.

Rule coverage remains bounded and protected write/compiler contracts are absent.
Successful verification here therefore yields partial proposals only.
"""
from collections import Counter
from dataclasses import replace
import re

from anchor_overlay_rules import RULE_IDS as ANCHOR_RULES, analyze as anchors
from marker_bounds_rules import RULE_IDS as MARKER_RULES, analyze as markers
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
from typst_lint import SafetyError


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
        results[PROSE_MARKER] = prose_markers(tree, path, resolution)
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
    reason = None
    try:
        planned = plan(tree.source, proposals)
        if planned.deferred:
            raise SafetyError('Conflicting supported-rule edits')
        candidate = planned.candidate()
        checked = parse(candidate, tree.mode)
        if checked.nodes[0].errors:
            raise SafetyError('Combined candidate failed to parse')
        # Rebase only a context that already matches this exact baseline. A
        # stale/wrong-path snapshot cannot be rescued by candidate analysis.
        candidate_entrypoint = (replace(entrypoint, source=candidate)
            if type(entrypoint) is EntrypointSnapshot and entrypoint.path == path
            and type(entrypoint.source) is bytes and entrypoint.source == tree.source else entrypoint)
        candidate_graph = rebase_graph(import_graph, path, tree.source, candidate)
        final = analyze(checked, path, pack=pack, fallback_language=fallback_language,
                        packages=packages, reference_catalog=reference_catalog, entrypoint=candidate_entrypoint, import_graph=candidate_graph,
                        ignored=ignored, _verify=False)
        if any(result.edits for result in final.values()):
            raise SafetyError('Combined candidate has second-pass edits')

        moves = _import_statement_moves(tree, checked, planned.edits,
                                        results.get(IMPORT_ORDER).edits if IMPORT_ORDER in results else ())

        def mapped(position):
            for start, end, target in moves:
                if start <= position < end:
                    return target + position - start
            return position + sum(len(edit.replacement) - (edit.end - edit.start)
                                  for edit in planned.edits if edit.end <= position)

        # A zero-edit candidate cannot certify idempotence if it merely lost
        # the static facts needed to analyze a rule. Preserve every baseline
        # rule and refuse newly unsupported regions, mapping existing offsets
        # through the same byte plan used for diagnostic ownership.
        if final.keys() != results.keys():
            raise SafetyError('Combined candidate changed available rule coverage')
        for rid, original in results.items():
            if final[rid].parse_errors:
                raise SafetyError('Combined candidate has rule parse failures')
            previous = Counter(re.sub(r'at byte (\d+)',
                lambda match: f'at byte {mapped(int(match.group(1)))}', reason)
                for reason in original.skips)
            if Counter(final[rid].skips) - previous:
                raise SafetyError('Combined candidate introduces an analysis refusal')

        before = _findings(tree.source, results, ignored, mapped)
        after = _findings(candidate, final, ignored)
        if after - before:
            raise SafetyError('Combined candidate introduces a new finding')
        if _findings(tree.source, results, ignored, mapped, suppressed=True) != _findings(
                candidate, final, ignored, suppressed=True):
            raise SafetyError('Combined candidate changed directive suppression ownership')
        # Reparse and rerun independently rather than trust retained tree state.
        again = analyze(parse(candidate, tree.mode), path, pack=pack,
                        fallback_language=fallback_language, packages=packages, reference_catalog=reference_catalog,
                        entrypoint=candidate_entrypoint, import_graph=candidate_graph, ignored=ignored, _verify=False)
        if again != final:
            raise SafetyError('Combined candidate is not deterministic/idempotent')
    except SafetyError as error:
        reason = f'{error}; no combined edits proposed'
    if reason:
        return {rid: replace(result, edits=(), skips=(*result.skips, reason)) for rid, result in results.items()}
    return results
