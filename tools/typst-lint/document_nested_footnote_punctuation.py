"""Conditional, source-only preview of punctuation after nested prose footnotes.

The source-local built-in binding is required, but include-site binding,
execution, rendering and source-write authority are not established here.
"""
from collections import Counter

from document_gap_preview import _MappedEdits, _scopes
from edit_plan import Edit, plan
import footnote_rules
import proof_rules
from static_resolver import resolve
from syntax_client import parse
from typst_lint import InputError, SafetyError, digest, load_rules


class _Offsets:
    def __init__(self, source):
        self.source = source
        self.lines = [0] + [index + 1 for index, byte in enumerate(source) if byte == 10]
        self.columns = {}

    def offset(self, finding):
        if type(finding.line) is not int or not 1 <= finding.line <= len(self.lines):
            raise SafetyError('Invalid footnote diagnostic line')
        start = self.lines[finding.line - 1]
        end = self.lines[finding.line] - 1 if finding.line < len(self.lines) else len(self.source)
        columns = self.columns.get(finding.line)
        if columns is None:
            columns = [0]
            for char in self.source[start:end].decode('utf-8'):
                columns.append(columns[-1] + len(char.encode('utf-8')))
            self.columns[finding.line] = columns
        if type(finding.column) is not int or not 1 <= finding.column <= len(columns):
            raise SafetyError('Invalid footnote diagnostic column')
        return start + columns[finding.column - 1]


def _keys(source, findings, edits=(), excluded=frozenset()):
    result = Counter()
    offsets = _Offsets(source)
    mapped_position = _MappedEdits(edits).mapped
    for finding in findings:
        position = offsets.offset(finding)
        if position in excluded:
            continue
        mapped = mapped_position(position)
        result[(finding.rule_id, finding.grade, finding.path, mapped,
                finding.explanation, finding.source)] += 1
    return result


def _simple_body(nodes, root):
    allowed = {'ContentBlock', 'LeftBracket', 'Markup', 'Text', 'Space', 'RightBracket'}
    pending = [root]
    while pending:
        node = nodes[pending.pop()]
        if node.kind not in allowed:
            return False
        pending.extend(node.children)
    return True


def _select(tree, resolution, notes):
    source, nodes = tree.source, tree.nodes
    offsets = _Offsets(source)
    active = Counter(offsets.offset(item) for item in notes[footnote_rules.PUNCT].diagnostics)
    edits, selected = [], set()
    for container in nodes:
        if container.kind != 'Markup':
            continue
        siblings = container.children
        for pos in range(2, len(siblings) - 1):
            call_id = siblings[pos]
            call, mark = nodes[call_id], nodes[siblings[pos - 1]]
            if (call.kind != 'FuncCall' or mark.kind != 'Hash' or mark.end != call.start
                    or active[mark.start] != 1 or nodes[siblings[pos - 2]].kind != 'Space'):
                continue
            space = nodes[siblings[pos - 2]]
            if (pos < 3 or nodes[siblings[pos - 3]].kind != 'Text'
                    or space.end != mark.start or not source[space.start:space.end]
                    or source[space.start:space.end].strip(b' \t')):
                continue
            previous = nodes[siblings[pos - 3]]
            if previous.end != space.start:
                continue
            text = source[previous.start:previous.end].decode('utf-8')
            if not text or not text[-1].isalnum():
                continue
            next_node = nodes[siblings[pos + 1]]
            punctuation = source[call.end:call.end + 1]
            if (next_node.kind != 'Text' or next_node.start != call.end
                    or punctuation not in (b'.', b',')
                    or (call.end + 1 < container.end
                        and source[call.end + 1:call.end + 2] not in (b' ', b'\t', b'\r', b'\n'))):
                continue
            binding = resolution.facts[call_id].binding
            if not (binding and binding.kind == 'builtin' and binding.name == 'footnote'):
                continue
            if (not source[mark.start:call.end].startswith(b'#footnote[')
                    or source[call.end - 1:call.end] != b']' or len(call.children) != 2):
                continue
            args = nodes[call.children[1]]
            if (args.kind != 'Args' or len(args.children) != 1
                    or nodes[args.children[0]].kind != 'ContentBlock'
                    or not _simple_body(nodes, args.children[0])):
                continue
            edits.append(Edit(space.start, call.end + 1,
                              punctuation + source[mark.start:call.end]))
            selected.add(mark.start)
    return tuple(edits), frozenset(selected)


def preview(tree, path, catalog=None):
    """Return a validated in-memory proposal or None; never write any source.

    Supports only nested prose with a single trailing comma/period, literal
    simple footnote content, horizontal pre-call whitespace, and a locally
    resolved built-in footnote. Other forms are deliberately left unchanged.
    """
    records = load_rules()[0]['rules'] if catalog is None else catalog
    source = tree.source
    try:
        if tree.mode != 'markup' or tree.nodes[0].errors:
            raise SafetyError('Requires well-formed markup source')
        resolution = resolve(tree, packages=())
        before = footnote_rules.analyze_conditional_nested(tree, path, resolution, catalog=records)
        edits, selected = _select(tree, resolution, before)
        if not edits:
            return None
        candidate_plan = plan(source, edits)
        if (candidate_plan.deferred or len(candidate_plan.edits) != len(edits)
                or len(selected) != len(edits)):
            raise SafetyError('Overlapping or duplicate punctuation proposals')
        candidate = candidate_plan.candidate()
        parsed = parse(candidate, tree.mode)
        if parsed.nodes[0].errors or _scopes(tree, records, edits) != _scopes(parsed, records):
            raise SafetyError('Punctuation proposal changes syntax validity or suppression targets')
        after_resolution = resolve(parsed, packages=())
        after = footnote_rules.analyze_conditional_nested(parsed, path, after_resolution, catalog=records)
        for rule_id in footnote_rules.RULE_IDS:
            excluded = selected if rule_id == footnote_rules.PUNCT else frozenset()
            if (_keys(source, before[rule_id].diagnostics, edits, excluded)
                    != _keys(candidate, after[rule_id].diagnostics)
                    or _keys(source, before[rule_id].suppressed, edits)
                    != _keys(candidate, after[rule_id].suppressed)
                    or after[rule_id].parse_errors):
                raise SafetyError('Punctuation proposal changed another footnote finding or suppression')
        proof_before = proof_rules.analyze_conditional(tree, path, resolution, catalog=records)
        proof_after = proof_rules.analyze_conditional(parsed, path, after_resolution, catalog=records)
        if (_keys(source, proof_before.diagnostics, edits) != _keys(candidate, proof_after.diagnostics)
                or _keys(source, proof_before.suppressed, edits) != _keys(candidate, proof_after.suppressed)):
            raise SafetyError('Punctuation proposal changed proof findings or suppressions')
        if _select(parsed, after_resolution, after)[0]:
            raise SafetyError('Punctuation proposal did not converge')
        return dict(path=path, baseline_sha256=digest(source), candidate_sha256=digest(candidate),
                    verification='conditional-nested-punctuation-syntax-directives-and-footnote-reanalysis',
                    runtime_binding_proved=False, render_equivalence_proved=False, edit_permission=False,
                    edits=[dict(start=edit.start, end=edit.end, replacement=edit.replacement.decode('utf-8'),
                                rule_id=footnote_rules.PUNCT) for edit in candidate_plan.edits])
    except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
        return dict(path=path, baseline_sha256=digest(source), candidate_sha256=None,
                    verification='withheld', runtime_binding_proved=False, render_equivalence_proved=False,
                    edit_permission=False, edits=[], reason=str(error))
