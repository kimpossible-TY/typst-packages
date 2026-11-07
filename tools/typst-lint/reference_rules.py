"""Bounded project-reference checks over official AST and package proofs.

Only direct root calls with proven callee identity are supported. No package
function, dynamic argument, catalog URL or scope-label generator is evaluated.
Proposals are in-memory only; source_analysis owns composed-candidate validation.
"""
from bisect import bisect_left
from dataclasses import dataclass

from edit_plan import Edit
from reference_catalog import ReferenceCatalog
from static_resolver import (PROJECT_REFERENCES, PROJECT_REFERENCES_SOURCE_SHA256,
                             PROJECT_REFERENCE_LOCAL_PREFIXES)
from suppression import resolve as resolve_suppressions
from typst_lint import Diagnostic, digest, load_rules, require

ARGUMENTS = 'core.project-ref-arguments'
CATALOG_TARGET = 'core.project-ref-catalog-target'
RULE_IDS = (ARGUMENTS, CATALOG_TARGET)
_TRIVIA = frozenset(('Space', 'Parbreak', 'LineComment', 'BlockComment'))
_ARG_PUNCTUATION = frozenset(('LeftParen', 'RightParen', 'Comma'))
_KNOWN_TYPES = {
    'Str': 'string', 'Label': 'label', 'ContentBlock': 'content', 'Equation': 'content',
    'Int': 'number', 'Float': 'number', 'Numeric': 'number',
    'Bool': 'boolean', 'None': 'none', 'Auto': 'auto',
    'Array': 'array', 'Dict': 'dictionary', 'Closure': 'function',
}


@dataclass(frozen=True)
class ReferenceResult:
    diagnostics: tuple[Diagnostic, ...] = ()
    edits: tuple[Edit, ...] = ()
    skips: tuple[str, ...] = ()
    parse_errors: tuple[tuple[int, int], ...] = ()
    suppressed: tuple[Diagnostic, ...] = ()


def _raw(tree, index):
    node = tree.nodes[index]
    return tree.source[node.start:node.end]


def _sig(tree, index):
    return [i for i in tree.nodes[index].children if tree.nodes[i].kind not in _TRIVIA]


def _string(tree, index):
    # A real Str proves its type even when its escaped value is unsupported.
    # Decode only unescaped literal UTF-8, never Python/JSON string semantics.
    if tree.nodes[index].kind != 'Str':
        return None
    raw = _raw(tree, index)
    if not (raw.startswith(b'"') and raw.endswith(b'"')) or b'\\' in raw:
        return None
    value = raw[1:-1].decode('utf-8')
    return value if all(ord(c) >= 32 for c in value) else None


def _label(tree, index):
    raw = _raw(tree, index)
    if tree.nodes[index].kind != 'Label' or not (raw.startswith(b'<') and raw.endswith(b'>')):
        return None
    return raw[1:-1].decode('utf-8')


def _plain_ref(tree, index):
    """Exactly [@name], with no whitespace, comments, supplement or options."""
    nodes = tree.nodes
    children = nodes[index].children
    if nodes[index].kind != 'ContentBlock' or [nodes[c].kind for c in children] != ['LeftBracket', 'Markup', 'RightBracket']:
        return None
    markup = nodes[children[1]].children
    if len(markup) != 1 or nodes[markup[0]].kind != 'Ref':
        return None
    ref = nodes[markup[0]].children
    if len(ref) != 1 or nodes[ref[0]].kind != 'RefMarker':
        return None
    marker = _raw(tree, ref[0])
    return marker[1:].decode('utf-8') if marker.startswith(b'@') and len(marker) > 1 else None


def analyze(tree, path, resolution, reference_catalog=None):
    source, nodes = tree.source, tree.nodes
    require(reference_catalog is None or type(reference_catalog) is ReferenceCatalog,
            'Reference rules require an immutable validated catalog snapshot')
    if nodes[0].errors:
        errors = tuple((n.start, n.end) for n in nodes if n.kind == 'Error')
        result = ReferenceResult(skips=('Malformed Typst syntax; no repair proposed',),
                                 parse_errors=errors or ((0, len(source)),))
        return {rid: result for rid in RULE_IDS}
    if resolution.source_sha256 != digest(source) or len(resolution.facts) != len(nodes):
        result = ReferenceResult(skips=('Stale or mismatched static resolution; no repair proposed',))
        return {rid: result for rid in RULE_IDS}
    rules = {r['id']: r for r in load_rules()[0]['rules']}
    scopes = resolve_suppressions(tree, set(rules))
    findings, edits, suppressed = ({rid: [] for rid in RULE_IDS} for _ in range(3))
    skips = {rid: list(resolution.skips) for rid in RULE_IDS}
    # Absence of findings in this subset must not claim complete graph/type
    # coverage. In particular, deferred aliases and generated labels are absent.
    for rid in RULE_IDS:
        skips[rid].append('Only verified direct-package root calls and literal arguments are supported')
    if reference_catalog is None:
        skips[CATALOG_TARGET].append('No catalog configured; catalog membership skipped')

    def skip(rid, start, reason):
        skips[rid].append(f'{reason} at byte {start}')

    def add(rid, start, explanation, edit=None):
        rule = rules[rid]
        line = source[:start].count(b'\n') + 1
        column = len(source[source.rfind(b'\n', 0, start) + 1:start].decode('utf-8')) + 1
        finding = Diagnostic(rid, rule['grade'], path, line, column, explanation, rule['source'])
        if any(s.covers(rid, start) for s in scopes):
            suppressed[rid].append(finding)
        else:
            findings[rid].append(finding)
            if edit is not None:
                edits[rid].append(edit)

    root_calls = frozenset(nodes[0].children) if tree.mode == 'markup' else frozenset()
    comment_starts = sorted(n.start for n in nodes if n.kind in ('LineComment', 'BlockComment'))
    for index, call in enumerate(nodes):
        if call.kind != 'FuncCall':
            continue
        binding = resolution.binding_for(index)
        known = bool(binding and binding.kind == 'api' and binding.name == 'project-ref'
                     and binding.package == PROJECT_REFERENCES
                     and binding.source_sha256 == PROJECT_REFERENCES_SOURCE_SHA256)
        parts = _sig(tree, index)
        if not known:
            if parts and _raw(tree, parts[0]) == b'project-ref':
                for rid in RULE_IDS:
                    skip(rid, call.start, 'Unresolved or unrelated project-ref callee')
            continue
        if index not in root_calls or resolution.facts[index].deferred:
            for rid in RULE_IDS:
                skip(rid, call.start, 'Nested or captured project-ref call is unsupported')
            continue
        if len(parts) != 2 or nodes[parts[1]].kind != 'Args':
            skip(ARGUMENTS, call.start, 'Unsupported call argument syntax')
            continue
        args = [i for i in _sig(tree, parts[1]) if nodes[i].kind not in _ARG_PUNCTUATION]
        named = [i for i in args if nodes[i].kind == 'Named']
        spread = any(nodes[i].kind == 'Spread' for i in args)
        positional = [i for i in args if nodes[i].kind not in ('Named', 'Spread')]
        if named or len(positional) > 2 or (not spread and len(positional) != 2):
            add(ARGUMENTS, call.start, 'project-ref requires exactly two positional arguments and no named arguments.')
            continue
        if spread:
            for rid in RULE_IDS:
                skip(rid, call.start, 'Spread arguments have unresolved arity and values')
            continue
        project_id, target_id = positional
        project_type = _KNOWN_TYPES.get(nodes[project_id].kind)
        target_type = _KNOWN_TYPES.get(nodes[target_id].kind)
        project = _string(tree, project_id)
        label = _label(tree, target_id)
        if project_type is None:
            skip(ARGUMENTS, nodes[project_id].start, 'Project argument type is unresolved')
        elif project_type != 'string':
            add(ARGUMENTS, nodes[project_id].start, 'project-ref project must be a string alias.')
        if target_type is None:
            skip(ARGUMENTS, nodes[target_id].start, 'Target type/global identity is unresolved')
        elif target_type != 'label':
            edit = None
            ref = _plain_ref(tree, target_id)
            # Reject comments anywhere in the call as a conservative repair
            # boundary; the diagnostic still applies to known content values.
            comment_index = bisect_left(comment_starts, call.start)
            comments = comment_index < len(comment_starts) and comment_starts[comment_index] < call.end
            if (ref is not None and not ref.startswith(PROJECT_REFERENCE_LOCAL_PREFIXES)
                    and project is not None and reference_catalog is not None and not comments
                    and reference_catalog.membership(project, ref) == 'unique'):
                target = nodes[target_id]
                edit = Edit(target.start, target.end, ('<' + ref + '>').encode('utf-8'))
            add(ARGUMENTS, nodes[target_id].start,
                f'project-ref target must be a global Typst label, not {target_type}.', edit)
            if edit is None and target_type == 'content':
                skip(ARGUMENTS, nodes[target_id].start,
                     'fix_unavailable: requires exactly [@label], a literal project and a unique configured catalog target')
        elif label is not None and label.startswith(PROJECT_REFERENCE_LOCAL_PREFIXES):
            add(ARGUMENTS, nodes[target_id].start, 'project-ref target uses a local-scope label prefix rejected by the reviewed API.')
            continue

        if reference_catalog is None:
            continue
        if project is None or label is None:
            skip(CATALOG_TARGET, call.start, 'Membership requires a literal project string and global label')
            continue
        membership = reference_catalog.membership(project, label)
        if membership == 'unknown-project':
            add(CATALOG_TARGET, nodes[project_id].start, f'Unknown catalog project {project!r}.')
        elif membership == 'ambiguous-label':
            add(CATALOG_TARGET, nodes[target_id].start, f'Ambiguous catalog label <{label}> in project {project!r}.')
        elif membership == 'missing-label':
            add(CATALOG_TARGET, nodes[target_id].start, f'Missing catalog label <{label}> in project {project!r}.')
    return {rid: ReferenceResult(tuple(findings[rid]), tuple(edits[rid]),
                                tuple(dict.fromkeys(skips[rid])), suppressed=tuple(suppressed[rid]))
            for rid in RULE_IDS}
