"""Compose three source-only previews over one retained document source.

Every stage uses the previous candidate bytes; this is not a write transaction
or a compiler snapshot. A failure withholds the complete per-source sequence.
"""
from document_display import RULE_ID as GAP, STANDALONE, analyze_all
from document_gap_preview import _scopes, preview as preview_gap
from document_standalone_preview import preview as preview_standalone
from edit_plan import Edit, plan
from array import array
from bisect import bisect_right
from collections import Counter
import footnote_rules
import include_context
import proof_rules
from script_rule import RULE_ID as SCRIPT, analyze as analyze_script
from static_resolver import resolve
from syntax_client import parse
from types import MappingProxyType
from typst_lint import InputError, SafetyError, digest


_STANDARD = 'standard'
_CONDITIONAL = 'conditional-gaps'
_PUNCTUATION = 'conditional-gaps-and-punctuation'
_SPACERS = 'conditional-gaps-and-spacers'
_PUNCTUATION_SPACERS = 'conditional-gaps-punctuation-and-spacers'
_VERIFICATIONS = {_STANDARD: 'hash-chained-parse-and-three-rule-reanalysis',
                  _CONDITIONAL: 'hash-chained-three-rule-and-conditional-gap-reanalysis',
                  _PUNCTUATION: 'hash-chained-three-rule-and-conditional-gap-punctuation-reanalysis',
                  _SPACERS: 'hash-chained-three-rule-and-conditional-gap-spacer-reanalysis',
                  _PUNCTUATION_SPACERS: 'hash-chained-three-rule-and-conditional-gap-punctuation-spacer-reanalysis'}


def _profile(value):
    if type(value) is not str or value not in _VERIFICATIONS:
        raise InputError('Unknown document preview profile')
    return value


def _verification(profile, conditional_continuations, conditional_adjacent_punctuation=False):
    base = _VERIFICATIONS[profile]
    if conditional_continuations:
        base += '-conditional-continuation-reanalysis'
    if conditional_adjacent_punctuation:
        base += '-conditional-adjacent-punctuation-reanalysis'
    return base


class _FindingMapper:
    def __init__(self, source, edits=()):
        self.source = source
        self.lines = [0] + [i + 1 for i, byte in enumerate(source) if byte == 10]
        self.columns = {}
        self.edits = tuple(edits)
        self.starts = [edit.start for edit in self.edits]
        self.ends = [edit.end for edit in self.edits]
        if self.starts != sorted(self.starts) or self.ends != sorted(self.ends):
            raise SafetyError('Unordered conditional edits')
        self.shifts = [0]
        for edit in self.edits:
            self.shifts.append(self.shifts[-1] + len(edit.replacement) - (edit.end - edit.start))

    def offset(self, finding):
        if (type(finding.line) is not int or not 1 <= finding.line <= len(self.lines)
                or type(finding.column) is not int):
            raise SafetyError('Invalid diagnostic location')
        start = self.lines[finding.line - 1]
        end = self.lines[finding.line] - 1 if finding.line < len(self.lines) else len(self.source)
        columns = self.columns.get(finding.line)
        if columns is None:
            text = self.source[start:end].decode('utf-8')
            columns = array('I', [0])
            offset = 0
            for char in text:
                offset += len(char.encode('utf-8'))
                columns.append(offset)
            self.columns[finding.line] = columns
        if not 1 <= finding.column <= len(columns):
            raise SafetyError('Invalid diagnostic column')
        return start + columns[finding.column - 1]

    def mapped(self, finding):
        position = self.offset(finding)
        index = bisect_right(self.starts, position) - 1
        if index >= 0 and self.edits[index].start < position < self.edits[index].end:
            raise SafetyError('An existing conditional finding lies inside an edit')
        return position + self.shifts[bisect_right(self.ends, position)]


def _keys(source, findings, edits=()):
    keys = Counter()
    mapper = _FindingMapper(source, edits)
    for finding in findings:
        keys[(finding.rule_id, finding.grade, finding.path, mapper.mapped(finding),
              finding.explanation, finding.source)] += 1
    return keys


def _same_findings(before_tree, before, after_tree, after, edits):
    return (_keys(before_tree.source, before.diagnostics, edits) == _keys(after_tree.source, after.diagnostics)
            and _keys(before_tree.source, before.suppressed, edits) == _keys(after_tree.source, after.suppressed))


def preview(tree, path, catalog, *, profile=_STANDARD, footnote_evidence=None, classify_deferred=False,
            classify_unproven_binding=False, conditional_continuations=False,
            conditional_adjacent_punctuation=False):
    """Return hash-chained edits or a withheld result for this source."""
    _profile(profile)
    if type(conditional_continuations) is not bool or type(conditional_adjacent_punctuation) is not bool:
        raise InputError('Conditional source selections must be Boolean')
    if type(classify_deferred) is not bool:
        raise InputError('Deferred classification must be an explicit Boolean')
    if type(classify_unproven_binding) is not bool or (classify_unproven_binding and not classify_deferred):
        raise InputError('Unproven binding classification requires explicit partial preview')
    baseline = tree.source
    current = tree
    steps = []
    deferred_unproved = False
    source_binding_unproved = False
    checked_import_tree = None

    def eligible_source_binding(tree, call_id, evidence):
        """Only exact calls in files whose literal imports have captured origins."""
        nonlocal checked_import_tree
        if call_id is None:
            return False
        import import_graph
        node = tree.nodes[call_id]
        parts = import_graph.significant(tree, call_id)
        if (node.kind != 'FuncCall' or node.start == 0 or tree.source[node.start - 1:node.start] != b'#'
                or len(parts) != 2 or tree.nodes[parts[0]].kind != 'Ident'
                or import_graph.raw(tree, parts[0]) != b'footnote' or tree.nodes[parts[1]].kind != 'Args'):
            return False
        arguments = tuple(tree.nodes[i].kind for i in import_graph.significant(tree, parts[1]))
        if arguments not in (('ContentBlock',), ('LeftParen', 'ContentBlock', 'RightParen')):
            return False
        if checked_import_tree is tree:
            return True
        session = evidence._session
        available = {item.evidence.import_path for item in session._namespaces.packages}
        computed = []
        for index, item in enumerate(tree.nodes):
            if item.kind != 'ModuleImport':
                continue
            request = import_graph.import_request(tree, index)
            if request is None:
                parts = import_graph.significant(tree, index)
                if (tuple(tree.nodes[i].kind for i in parts) != ('Import', 'FieldAccess', 'Colon', 'Star')):
                    raise SafetyError('Unproven source contains an unsupported computed import')
                fields = import_graph.significant(tree, parts[1])
                if (tuple(tree.nodes[i].kind for i in fields) != ('Ident', 'Dot', 'Ident')
                        or tuple(import_graph.raw(tree, i) for i in fields) != (b'cetz', b'.', b'draw')):
                    raise SafetyError('Unproven source contains an unsupported computed import')
                computed.append(index)
                continue
            if request.startswith('@'):
                if request not in available:
                    raise SafetyError('Unproven source imports an unavailable package')
            elif session._graph.target_for(path, request) is None:
                raise SafetyError('Unproven source imports an unavailable relative source')
        reachable = {path}
        while True:
            extra = {edge.target for edge in session._graph.edges if edge.importer in reachable} - reachable
            if not extra:
                break
            reachable.update(extra)
        for item in session._graph.unavailable:
            if item.path not in reachable:
                continue
            if (item.request.startswith('@') and item.request in available
                    and item.reason == 'Package evaluation and package-relative dependency graphs are unsupported'):
                continue
            # Every computed import in this candidate is checked below. Do
            # not extend this exception to uninspected transitive sources.
            if (item.path == path and not item.request and computed
                    and item.reason == 'Dynamic or escaped import is unsupported'):
                continue
            # Give callers an actionable location without turning an unknown
            # dependency into evidence that it is safe to stage. repr keeps
            # control characters and line breaks from forging diagnostic rows.
            raise SafetyError('Unproven source has an unresolved transitive import '
                              f'(source={item.path[:512]!r}, request={item.request[:512]!r}, '
                              f'reason={item.reason[:512]!r})')
        if computed:
            import document_cetz_draw_evidence
            parents = {child: index for index, node in enumerate(tree.nodes) for child in node.children}
            groups = {}
            for index in computed:
                current, closures = index, []
                while current in parents:
                    current = parents[current]
                    if tree.nodes[current].kind == 'Closure':
                        closures.append(current)
                if not closures:
                    raise SafetyError('Unproven source has a computed import outside a reviewed closure')
                groups.setdefault(closures[-1], []).append((tree.nodes[index].start, tree.nodes[index].end))
            for closure_id, spans in groups.items():
                closure = tree.nodes[closure_id]
                if not document_cetz_draw_evidence.supports_imports(
                        evidence, path, tree.source, closure.start, closure.end, tuple(spans)):
                    raise SafetyError('Unproven source computed import is not proven')
        checked_import_tree = tree
        return True

    def deferred_closure(tree, resolution, call_id, evidence):
        if call_id is None or not resolution.facts[call_id].deferred:
            return False
        parents = {child: index for index, node in enumerate(tree.nodes) for child in node.children}
        ancestor = call_id
        while ancestor in parents and tree.nodes[ancestor].kind != 'Closure':
            ancestor = parents[ancestor]
        if tree.nodes[ancestor].kind != 'Closure':
            return False
        closure = tree.nodes[ancestor]
        def safe_params(item):
            params = [tree.nodes[i] for i in item.children if tree.nodes[i].kind == 'Params']
            if len(params) != 1:
                return False
            for child in params[0].children:
                node = tree.nodes[child]
                if node.kind in ('Space', 'LineComment', 'BlockComment', 'LeftParen', 'RightParen', 'Comma'):
                    continue
                if node.kind != 'Ident' or tree.source[node.start:node.end] in (b'footnote', b'cetz'):
                    return False
            return True
        if not safe_params(closure):
            return False
        # Only exact reviewed declaration/import forms may later be certified;
        # all other local mutations stay safety refusals.
        pending = list(closure.children)
        assignments = {'Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq'}
        cetz_imports = []
        selector_lets = []
        while pending:
            index = pending.pop()
            node = tree.nodes[index]
            if node.kind == 'Closure' and not safe_params(node):
                return False
            if node.kind in ('ModuleInclude', 'DestructAssignment'):
                return False
            if node.kind == 'ModuleImport':
                cetz_imports.append((node.start, node.end))
            if node.kind == 'LetBinding':
                selector_lets.append((node.start, node.end))
            if node.kind == 'Binary' and any(tree.nodes[i].kind in assignments for i in node.children):
                return False
            pending.extend(node.children)

        if cetz_imports:
            import document_cetz_draw_evidence
            if not document_cetz_draw_evidence.supports_imports(
                    evidence, path, tree.source, closure.start, closure.end, tuple(cetz_imports)):
                return False
        if selector_lets:
            import document_theme_selector_evidence
            if not document_theme_selector_evidence.supports_theme_lets(
                    evidence, path, tree.source, closure.start, closure.end, tuple(selector_lets)):
                return False

        # Re-evaluate the lexical namespace at closure declaration using the
        # same issued graph as conditional footnote evidence. Deferred facts at
        # the eventual call alone cannot distinguish a captured builtin from a
        # preceding shadow or an unknown wildcard.
        import conditional_footnote_binding as bindings
        import facade_provenance
        session = evidence._session
        engine = bindings._NamespaceAnalyzer(session._graph, session._namespaces, (), False)
        engine.reason = facade_provenance._graph_reason(session._graph)
        engine.packages = {item.evidence.import_path: item for item in session._namespaces.packages}
        engine.evidence = session._namespaces.evidence

        class DeclarationObserver(bindings._FootnoteResolver):
            found = False
            def visit(self, index, state, *, output=False):
                if index == ancestor:
                    value = state.lookup('footnote')
                    preceding = any(self.evidence.get(frame) for frame in state.frames)
                    self.found = bool(not state.deferred and preceding and value
                                      and value.kind == 'builtin' and value.name == 'footnote')
                return super().visit(index, state, output=output)

        observer = DeclarationObserver(tree, path, engine)
        observer.visit(0, observer.state)
        return observer.found and not observer.unresolved and not engine.exhausted

    def apply_display(function, rule_id):
        nonlocal current
        result = function(current, path, catalog)
        if result is None:
            return
        if result['verification'] == 'withheld':
            raise SafetyError(f"{rule_id}: {result['reason']}")
        edits = tuple(Edit(row['start'], row['end'], row['replacement'].encode('utf-8'))
                      for row in result['edits'])
        candidate = plan(current.source, edits).candidate()
        if digest(current.source) != result['baseline_sha256'] or digest(candidate) != result['candidate_sha256']:
            raise SafetyError(f'{rule_id}: preview hash mismatch')
        steps.append(dict(rule_id=rule_id, baseline_sha256=digest(current.source),
                          candidate_sha256=digest(candidate), edits=result['edits']))
        current = parse(candidate, tree.mode)

    try:
        if classify_deferred:
            import document_footnote_evidence
            if type(footnote_evidence) is not document_footnote_evidence.PreviewFootnoteEvidence:
                raise SafetyError('Partial preview requires issued conditional footnote evidence')
            captured = document_footnote_evidence._valid(footnote_evidence)
            checked_packages = set()
            for item in footnote_evidence._session._namespaces.packages:
                document_footnote_evidence._verify_package(
                    footnote_evidence._session, captured, item.evidence, checked_packages)
        apply_display(preview_standalone, STANDALONE)
        script = analyze_script(current, path, catalog=catalog, _verify=False, _allow_code_call=True)
        if script.skips or script.parse_errors:
            raise SafetyError('Script analysis is incomplete: ' + '; '.join(script.skips))
        if script.edits:
            proposal = plan(current.source, script.edits)
            if proposal.deferred or len(proposal.edits) != len(script.edits):
                raise SafetyError('Conflicting or ineffective script edits')
            candidate = proposal.candidate()
            candidate_tree = parse(candidate, tree.mode)
            final = analyze_script(candidate_tree, path, catalog=catalog, _verify=False, _allow_code_call=True)
            if (candidate_tree.nodes[0].errors or final.parse_errors or final.skips or final.edits
                    or final.diagnostics or len(final.suppressed) != len(script.suppressed)):
                raise SafetyError('Script candidate failed fresh parse and reanalysis')
            if _scopes(current, catalog, proposal.edits) != _scopes(candidate_tree, catalog):
                raise SafetyError('Script candidate changes an inline directive target')
            steps.append(dict(rule_id=SCRIPT, baseline_sha256=digest(current.source),
                              candidate_sha256=digest(candidate),
                              edits=[dict(start=e.start, end=e.end,
                                          replacement=e.replacement.decode('utf-8'), rule_id=SCRIPT)
                                     for e in proposal.edits]))
            current = candidate_tree
        apply_display(preview_gap, GAP)
        if profile != _STANDARD and (b'#proof' in current.source or b'#footnote' in current.source):
            before_tree = current
            before_resolution = resolve(before_tree, packages=())
            notes_before = footnote_rules.analyze_conditional_nested(before_tree, path, before_resolution, catalog=catalog)
            proof_before = proof_rules.analyze_conditional(before_tree, path, before_resolution, catalog=catalog)
            note_preview = footnote_rules.preview_conditional_math_gaps(before_tree, path, before_resolution, catalog=catalog)
            if note_preview is not None:
                if note_preview['verification'] == 'withheld':
                    raise SafetyError('Conditional footnote preview withheld: ' + note_preview['reason'])
                if note_preview['verification'] != 'conditional-parse-syntax-directives-and-footnote-reanalysis':
                    raise SafetyError('Conditional footnote preview has unknown verification')
                edits = tuple(Edit(row['start'], row['end'], row['replacement'].encode('utf-8'))
                              for row in note_preview['edits'])
                candidate_plan = plan(before_tree.source, edits)
                candidate = candidate_plan.candidate()
                if (candidate_plan.deferred or len(candidate_plan.edits) != len(edits)
                        or digest(before_tree.source) != note_preview['baseline_sha256']
                        or digest(candidate) != note_preview['candidate_sha256']):
                    raise SafetyError('Conditional footnote preview hash or edit mismatch')
                current = parse(candidate, tree.mode)
                current_resolution = resolve(current, packages=())
                notes_after = footnote_rules.analyze_conditional_nested(current, path, current_resolution, catalog=catalog)
                proof_after = proof_rules.analyze_conditional(current, path, current_resolution, catalog=catalog)
                if not _same_findings(before_tree, proof_before, current, proof_after, edits):
                    raise SafetyError('Conditional footnote stage changed proof findings or suppressions')
                for rid in footnote_rules.RULE_IDS:
                    first, last = notes_before[rid], notes_after[rid]
                    if rid != footnote_rules.GAP:
                        if not _same_findings(before_tree, first, current, last, edits):
                            raise SafetyError('Conditional footnote stage changed another footnote rule')
                        continue
                    expected = _keys(before_tree.source, first.diagnostics, edits)
                    calls = {(node.start - 1, node.end) for node in before_tree.nodes if node.kind == 'FuncCall'}
                    mapper = _FindingMapper(before_tree.source)
                    target_mapper = _FindingMapper(before_tree.source, edits)
                    diagnosed = {}
                    for finding in first.diagnostics:
                        diagnosed.setdefault(mapper.offset(finding), []).append(finding)
                    matched = {}
                    for start, end in calls:
                        if start in diagnosed:
                            matched.setdefault(end, []).extend(diagnosed[start])
                    for edit in edits:
                        matching = matched.get(edit.start, ())
                        if len(matching) != 1:
                            raise SafetyError('Conditional footnote edit does not match one diagnosed call')
                        finding = matching[0]
                        key = (finding.rule_id, finding.grade, finding.path, target_mapper.mapped(finding),
                               finding.explanation, finding.source)
                        expected[key] -= 1
                    if (any(count < 0 for count in expected.values())
                            or +expected != _keys(current.source, last.diagnostics)
                            or _keys(before_tree.source, first.suppressed, edits) != _keys(current.source, last.suppressed)):
                        raise SafetyError('Conditional footnote findings changed beyond selected math gaps')
                steps.append(dict(rule_id=footnote_rules.GAP, baseline_sha256=digest(before_tree.source),
                                  candidate_sha256=digest(candidate), edits=note_preview['edits']))
            before_tree = current
            before_resolution = resolve(before_tree, packages=())
            notes_before = footnote_rules.analyze_conditional_nested(before_tree, path, before_resolution, catalog=catalog)
            proof_preview = proof_rules.preview_conditional(before_tree, path, before_resolution, catalog=catalog)
            if len(proof_preview.diagnostics) != len(proof_preview.edits):
                raise SafetyError('Conditional proof preview was withheld for at least one finding')
            if proof_preview.edits:
                edits = proof_preview.edits
                candidate_plan = plan(before_tree.source, edits)
                if candidate_plan.deferred or len(candidate_plan.edits) != len(edits):
                    raise SafetyError('Conditional proof preview contains conflicting edits')
                candidate = candidate_plan.candidate()
                current = parse(candidate, tree.mode)
                notes_after = footnote_rules.analyze_conditional_nested(current, path, resolve(current, packages=()), catalog=catalog)
                if any(not _same_findings(before_tree, notes_before[rid], current, notes_after[rid], edits)
                       for rid in footnote_rules.RULE_IDS):
                    raise SafetyError('Conditional proof stage changed footnote findings or suppressions')
                proof_after = proof_rules.analyze_conditional(current, path, resolve(current, packages=()), catalog=catalog)
                if (proof_after.diagnostics or _keys(before_tree.source, proof_preview.suppressed, edits)
                        != _keys(current.source, proof_after.suppressed)):
                    raise SafetyError('Conditional proof stage failed final reanalysis')
                if footnote_rules.preview_conditional_math_gaps(current, path, resolve(current, packages=()), catalog=catalog):
                    raise SafetyError('Conditional proof stage introduced footnote gap edits')
                steps.append(dict(rule_id=proof_rules.RULE_ID, baseline_sha256=digest(before_tree.source),
                                  candidate_sha256=digest(candidate),
                                  edits=[dict(start=e.start, end=e.end, replacement=e.replacement.decode('utf-8'),
                                              rule_id=proof_rules.RULE_ID) for e in candidate_plan.edits]))
            if profile in (_PUNCTUATION, _PUNCTUATION_SPACERS):
                before_tree = current
                before_resolution = resolve(before_tree, packages=())
                notes_before = footnote_rules.analyze_conditional_nested(
                    before_tree, path, before_resolution, catalog=catalog)
                proof_before = proof_rules.analyze_conditional(before_tree, path, before_resolution, catalog=catalog)
                punctuation = footnote_rules.preview_conditional_prose_punctuation(
                    before_tree, path, before_resolution, catalog=catalog)
                if punctuation is not None:
                    if punctuation['verification'] == 'withheld':
                        raise SafetyError('Conditional punctuation preview withheld: ' + punctuation['reason'])
                    if punctuation['verification'] != 'conditional-parse-syntax-directives-and-footnote-reanalysis':
                        raise SafetyError('Conditional punctuation preview has unknown verification')
                    edits = tuple(Edit(row['start'], row['end'], row['replacement'].encode('utf-8'))
                                  for row in punctuation['edits'])
                    candidate_plan = plan(before_tree.source, edits)
                    candidate = candidate_plan.candidate()
                    if (candidate_plan.deferred or len(candidate_plan.edits) != len(edits)
                            or digest(before_tree.source) != punctuation['baseline_sha256']
                            or digest(candidate) != punctuation['candidate_sha256']):
                        raise SafetyError('Conditional punctuation preview hash or edit mismatch')
                    current = parse(candidate, tree.mode)
                    current_resolution = resolve(current, packages=())
                    notes_after = footnote_rules.analyze_conditional_nested(
                        current, path, current_resolution, catalog=catalog)
                    proof_after = proof_rules.analyze_conditional(current, path, current_resolution, catalog=catalog)
                    if not _same_findings(before_tree, proof_before, current, proof_after, edits):
                        raise SafetyError('Conditional punctuation stage changed proof findings or suppressions')
                    for rid in footnote_rules.RULE_IDS:
                        first, last = notes_before[rid], notes_after[rid]
                        if rid != footnote_rules.PUNCT:
                            if not _same_findings(before_tree, first, current, last, edits):
                                raise SafetyError('Conditional punctuation stage changed another footnote rule')
                            continue
                        expected = _keys(before_tree.source, first.diagnostics, edits)
                        mapper = _FindingMapper(before_tree.source)
                        target_mapper = _FindingMapper(before_tree.source, edits)
                        diagnosed = {}
                        for finding in first.diagnostics:
                            diagnosed.setdefault(mapper.offset(finding), []).append(finding)
                        for edit in edits:
                            matching = diagnosed.get(edit.end, ())
                            if len(matching) != 1:
                                raise SafetyError('Conditional punctuation edit does not match one diagnosed call')
                            finding = matching[0]
                            key = (finding.rule_id, finding.grade, finding.path, target_mapper.mapped(finding),
                                   finding.explanation, finding.source)
                            expected[key] -= 1
                        if (any(count < 0 for count in expected.values())
                                or +expected != _keys(current.source, last.diagnostics)
                                or _keys(before_tree.source, first.suppressed, edits)
                                   != _keys(current.source, last.suppressed)):
                            raise SafetyError('Conditional punctuation findings changed beyond selected spacing')
                    if (footnote_rules.preview_conditional_prose_punctuation(
                            current, path, current_resolution, catalog=catalog) is not None
                            or footnote_rules.preview_conditional_math_gaps(
                                current, path, current_resolution, catalog=catalog) is not None):
                        raise SafetyError('Conditional punctuation stage did not converge or introduced gaps')
                    steps.append(dict(rule_id=footnote_rules.PUNCT, baseline_sha256=digest(before_tree.source),
                                      candidate_sha256=digest(candidate), edits=punctuation['edits']))
            if profile in (_SPACERS, _PUNCTUATION_SPACERS):
                before_tree = current
                before_resolution = resolve(before_tree, packages=())
                notes_before = footnote_rules.analyze_conditional_nested(
                    before_tree, path, before_resolution, catalog=catalog)
                proof_before = proof_rules.analyze_conditional(before_tree, path, before_resolution, catalog=catalog)
                spacer = footnote_rules.preview_conditional_unaligned_spacers(
                    before_tree, path, before_resolution, catalog=catalog)
                if spacer is not None:
                    if spacer['verification'] == 'withheld':
                        raise SafetyError('Conditional spacer preview withheld: ' + spacer['reason'])
                    if (spacer['verification'] != 'conditional-spacer-token-syntax-directives-and-footnote-reanalysis'
                            or spacer['path'] != path
                            or spacer['runtime_binding_proved'] is not False
                            or spacer['render_equivalence_proved'] is not False
                            or spacer['edit_permission'] is not False):
                        raise SafetyError('Conditional spacer preview has invalid verification or authority')
                    edits = tuple(Edit(row['start'], row['end'], row['replacement'].encode('utf-8'))
                                  for row in spacer['edits'])
                    candidate_plan = plan(before_tree.source, edits)
                    candidate = candidate_plan.candidate()
                    if (not edits or candidate_plan.deferred or len(candidate_plan.edits) != len(edits)
                            or digest(before_tree.source) != spacer['baseline_sha256']
                            or digest(candidate) != spacer['candidate_sha256']
                            or any(row['rule_id'] != footnote_rules.UNALIGNED for row in spacer['edits'])
                            or any(edit.replacement for edit in edits)):
                        raise SafetyError('Conditional spacer preview hash or edit mismatch')
                    current = parse(candidate, tree.mode)
                    if (current.nodes[0].errors
                            or footnote_rules._signature_without_spacers(before_tree, edits)
                            != footnote_rules._signature_without_spacers(current, ())
                            or _scopes(before_tree, catalog, edits) != _scopes(current, catalog)):
                        raise SafetyError('Conditional spacer changed syntax or suppression targets')
                    current_resolution = resolve(current, packages=())
                    notes_after = footnote_rules.analyze_conditional_nested(
                        current, path, current_resolution, catalog=catalog)
                    proof_after = proof_rules.analyze_conditional(current, path, current_resolution, catalog=catalog)
                    if not _same_findings(before_tree, proof_before, current, proof_after, edits):
                        raise SafetyError('Conditional spacer stage changed proof findings or suppressions')
                    mapper = _FindingMapper(before_tree.source)
                    target_mapper = _FindingMapper(before_tree.source, edits)
                    conditional_calls = []
                    conditional_proof = None
                    for rid in footnote_rules.RULE_IDS:
                        first, last = notes_before[rid], notes_after[rid]
                        expected = _keys(before_tree.source, first.diagnostics, edits)
                        if rid == footnote_rules.UNALIGNED:
                            diagnosed = {}
                            for finding in first.diagnostics:
                                diagnosed.setdefault(mapper.offset(finding), []).append(finding)
                            calls = {node.start - 1: index for index, node in enumerate(before_tree.nodes)
                                     if node.kind == 'FuncCall' and node.start > 0
                                     and before_tree.source[node.start - 1:node.start] == b'#'}
                            for edit in edits:
                                matching = diagnosed.get(edit.start, ())
                                if len(matching) != 1:
                                    raise SafetyError('Conditional spacer edit does not match one diagnosed call')
                                call_start = edit.end
                                while before_tree.source[call_start:call_start + 1] in (b' ', b'\t', b'\r', b'\n'):
                                    call_start += 1
                                call_id = calls.get(call_start)
                                binding = before_resolution.facts[call_id].binding if call_id is not None else None
                                if not (binding and binding.kind == 'builtin' and binding.name == 'footnote'):
                                    if footnote_evidence is None:
                                        proved = False
                                    else:
                                        import document_footnote_evidence
                                        if type(footnote_evidence) is not document_footnote_evidence.PreviewFootnoteEvidence:
                                            raise SafetyError('Invalid conditional footnote evidence')
                                        if conditional_proof is None:
                                            conditional_proof = document_footnote_evidence.PreviewFootnoteEvidence.candidate_calls(
                                                footnote_evidence, path, before_tree.source)
                                        if conditional_proof.source_sha256 != digest(before_tree.source):
                                            raise SafetyError('Conditional spacer footnote evidence has a stale source hash'
                                                if classify_deferred else
                                                'Conditional spacer footnote binding is not proven by captured facade')
                                        proved = any(call.start == call_start for call in conditional_proof.calls)
                                    if not proved:
                                        if classify_unproven_binding:
                                            if (conditional_proof is None or conditional_proof.unresolved
                                                    or not eligible_source_binding(before_tree, call_id, footnote_evidence)):
                                                raise SafetyError('Source footnote binding or import cannot be classified')
                                            source_binding_unproved = True
                                        elif (classify_deferred and conditional_proof is not None
                                                and not conditional_proof.unresolved
                                                and deferred_closure(before_tree, before_resolution, call_id,
                                                                     footnote_evidence)):
                                            deferred_unproved = True
                                        else:
                                            raise SafetyError('Conditional spacer footnote binding is not locally proven'
                                                if footnote_evidence is None else
                                                'Conditional spacer footnote binding is not proven by captured facade')
                                    else:
                                        conditional_calls.append(call_start + sum(
                                            len(item.replacement) - (item.end - item.start)
                                            for item in edits if item.end <= call_start))
                                finding = matching[0]
                                expected[(finding.rule_id, finding.grade, finding.path,
                                          target_mapper.mapped(finding), finding.explanation, finding.source)] -= 1
                        if (any(count < 0 for count in expected.values())
                                or +expected != _keys(current.source, last.diagnostics)
                                or _keys(before_tree.source, first.suppressed, edits)
                                   != _keys(current.source, last.suppressed)):
                            raise SafetyError('Conditional spacer changed another finding or suppression')
                    if (footnote_rules.preview_conditional_unaligned_spacers(
                            current, path, current_resolution, catalog=catalog) is not None
                            or footnote_rules.preview_conditional_math_gaps(
                                current, path, current_resolution, catalog=catalog) is not None
                            or (profile == _PUNCTUATION_SPACERS
                                and footnote_rules.preview_conditional_prose_punctuation(
                                    current, path, current_resolution, catalog=catalog) is not None)):
                        raise SafetyError('Conditional spacer stage did not converge or introduced edits')
                    step = dict(rule_id=footnote_rules.UNALIGNED, baseline_sha256=digest(before_tree.source),
                                candidate_sha256=digest(candidate), edits=spacer['edits'])
                    if conditional_calls:
                        step['conditional_footnote_calls'] = sorted(conditional_calls)
                    steps.append(step)
        if conditional_continuations:
            import document_conditional_continuation as continuation
            import paragraph_rules
            selected = continuation.preview(current, path, catalog=catalog)
            notes = continuation.continuation_rules.analyze_conditional(current, path, catalog=catalog)
            exact_edits, selected_notes = continuation._select(current, notes)
            if selected is None and exact_edits:
                raise SafetyError('Conditional continuation candidates were omitted')
            if selected is not None:
                if type(selected) is not dict:
                    raise SafetyError('Conditional continuation proof is invalid')
                if selected.get('verification') == 'withheld':
                    raise SafetyError('Conditional continuation preview withheld: ' + str(selected.get('reason', 'unknown')))
                if (selected.get('verification') != 'conditional-continuation-syntax-directives-and-reanalysis'
                        or any(selected.get(key) is not False for key in ('execution_proved',
                            'render_equivalence_proved', 'edit_permission', 'compiler_permission'))
                        or type(selected.get('conditions')) is not tuple
                        or selected['conditions'] != continuation.CONDITIONS):
                    raise SafetyError('Conditional continuation proof is invalid')
                before = current
                rows = selected.get('edits')
                if (type(rows) is not list or not rows or any(type(row) is not dict
                        or set(row) != {'start', 'end', 'replacement', 'rule_id'}
                        or row.get('rule_id') != continuation.RULE
                        or type(row.get('start')) is not int or type(row.get('end')) is not int
                        or not 0 <= row['start'] < row['end'] <= len(before.source)
                        or row.get('replacement') != ' '
                        or not continuation.continuation_rules._GAP.fullmatch(before.source[row['start']:row['end']])
                        for row in rows)):
                    raise SafetyError('Conditional continuation edit selection is invalid')
                edits = tuple(Edit(row['start'], row['end'], b' ') for row in rows)
                proposal = plan(before.source, edits)
                candidate = proposal.candidate()
                if (proposal.deferred or len(proposal.edits) != len(edits)
                        or tuple(proposal.edits) != exact_edits or len(selected_notes) != len(edits)
                        or digest(before.source) != selected.get('baseline_sha256')
                        or digest(candidate) != selected.get('candidate_sha256')):
                    raise SafetyError('Conditional continuation hash or edit mismatch')
                current = parse(candidate, tree.mode)
                final_notes = continuation.continuation_rules.analyze_conditional(current, path, catalog=catalog)
                if (current.nodes[0].errors or continuation._select(current, final_notes)[0]
                        or continuation.preview(current, path, catalog=catalog) is not None):
                    raise SafetyError('Conditional continuation stage did not converge')
                for checked in (before, current):
                    gap_hints = paragraph_rules.analyze_conditional_nested_gap(checked, path, catalog=catalog)
                    if any(reason.startswith(paragraph_rules.NESTED_GAP_UNRESOLVED) for reason in gap_hints.skips):
                        raise SafetyError('Conditional continuation cannot establish an unrelated paragraph marker boundary')
                conditional_rows = [dict(row, conditions=list(selected['conditions']), execution_proved=False,
                    render_equivalence_proved=False, edit_permission=False, compiler_permission=False) for row in rows]
                steps.append(dict(rule_id=continuation.RULE, baseline_sha256=digest(before.source),
                                  candidate_sha256=digest(candidate), edits=conditional_rows))
        if conditional_adjacent_punctuation:
            import document_adjacent_punctuation_preview as adjacent
            import paragraph_rules
            before = current
            selected = adjacent.preview(before, path, catalog=catalog)
            exact_edits, targets = adjacent._select(before, path, catalog)
            if selected is None and exact_edits:
                raise SafetyError('Conditional adjacent punctuation candidates were omitted')
            if selected is not None:
                if type(selected) is not dict:
                    raise SafetyError('Conditional adjacent punctuation proof is invalid')
                if selected.get('verification') == 'withheld':
                    raise SafetyError('Conditional adjacent punctuation withheld: ' + str(selected.get('reason', 'unknown')))
                if (selected.get('verification') != 'conditional-adjacent-punctuation-syntax-directives-and-reanalysis'
                        or selected.get('atomic_edits') is not True
                        or any(selected.get(key) is not False for key in ('runtime_binding_proved', 'execution_proved',
                            'render_equivalence_proved', 'edit_permission', 'compiler_permission'))
                        or type(selected.get('conditions')) is not tuple
                        or selected['conditions'] != adjacent.CONDITIONS):
                    raise SafetyError('Conditional adjacent punctuation proof is invalid')
                rows = selected.get('edits')
                if (type(rows) is not list or not rows or len(rows) != 2 * len(targets)
                        or any(type(row) is not dict or set(row) != {'start', 'end', 'replacement', 'rule_id'}
                            or row.get('rule_id') != adjacent.RULE or type(row.get('start')) is not int
                            or type(row.get('end')) is not int or type(row.get('replacement')) is not str
                            or not 0 <= row['start'] <= row['end'] <= len(before.source) for row in rows)):
                    raise SafetyError('Conditional adjacent punctuation edit selection is invalid')
                edits = tuple(Edit(row['start'], row['end'], row['replacement'].encode('utf-8')) for row in rows)
                proposal = plan(before.source, edits)
                candidate = proposal.candidate()
                if (proposal.deferred or len(proposal.edits) != len(edits)
                        or tuple(proposal.edits) != exact_edits
                        or digest(before.source) != selected.get('baseline_sha256')
                        or digest(candidate) != selected.get('candidate_sha256')):
                    raise SafetyError('Conditional adjacent punctuation edit pairing or hash mismatch')
                current = parse(candidate, tree.mode)
                if (current.nodes[0].errors or adjacent._select(current, path, catalog)[0]
                        or adjacent.preview(current, path, catalog=catalog) is not None):
                    raise SafetyError('Conditional adjacent punctuation stage did not converge')
                for checked in (before, current):
                    hints = paragraph_rules.analyze_conditional_nested_gap(checked, path, catalog=catalog)
                    if any(reason.startswith(paragraph_rules.NESTED_GAP_UNRESOLVED) for reason in hints.skips):
                        raise SafetyError('Conditional punctuation cannot establish an unrelated paragraph marker boundary')
                conditional_rows = [dict(row, conditions=list(selected['conditions']), atomic_edits=True,
                    runtime_binding_proved=False, execution_proved=False, render_equivalence_proved=False,
                    edit_permission=False, compiler_permission=False) for row in rows]
                steps.append(dict(rule_id=adjacent.RULE, baseline_sha256=digest(before.source),
                                  candidate_sha256=digest(candidate), atomic_edits=True, edits=conditional_rows))
        final_displays = analyze_all(current, path, catalog=catalog)
        final_scripts = analyze_script(current, path, catalog=catalog, _verify=False, _allow_code_call=True)
        if (current.nodes[0].errors or final_scripts.parse_errors or final_scripts.skips
                or final_scripts.diagnostics or final_scripts.edits
                or any(value.diagnostics for value in final_displays.values())):
            raise SafetyError('Final candidate failed the three selected source checks')
        if classify_deferred:
            document_footnote_evidence._valid(footnote_evidence)
        if source_binding_unproved:
            return dict(path=path, baseline_sha256=digest(baseline), candidate_sha256=None,
                        verification='withheld', steps=[],
                        reason='A source-level footnote binding is not proven; the entire file is unchanged',
                        reason_code='unproven-source-footnote-binding',
                        rule_id=footnote_rules.UNALIGNED)
        if deferred_unproved:
            return dict(path=path, baseline_sha256=digest(baseline), candidate_sha256=None,
                        verification='withheld', steps=[],
                        reason='A footnote in a deferred closure has no proven binding',
                        reason_code='unsupported-deferred-footnote-binding',
                        rule_id=footnote_rules.UNALIGNED)
        if not steps:
            return None
        result = dict(path=path, baseline_sha256=digest(baseline), candidate_sha256=digest(current.source),
                      verification=_verification(profile, conditional_continuations, conditional_adjacent_punctuation), steps=steps,
                      unresolved_display_contexts={rid: len(value.skips) for rid, value in final_displays.items()})
        if profile != _STANDARD or conditional_continuations or conditional_adjacent_punctuation:
            result.update(profile=profile, runtime_binding_proved=False, render_equivalence_proved=False,
                          edit_permission=False)
        if conditional_continuations:
            result['conditional_continuations'] = True
        if conditional_adjacent_punctuation:
            result['conditional_adjacent_punctuation'] = True
        return result
    except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
        return dict(path=path, baseline_sha256=digest(baseline), candidate_sha256=None,
                    verification='withheld', steps=[], reason=str(error))


def _same_data(expected, supplied):
    """Compare nested report data without treating booleans as integers."""
    if type(expected) is not type(supplied):
        return False
    if type(expected) is dict:
        return (expected.keys() == supplied.keys()
                and all(_same_data(value, supplied[key]) for key, value in expected.items()))
    if type(expected) is list:
        return len(expected) == len(supplied) and all(_same_data(a, b) for a, b in zip(expected, supplied))
    return expected == supplied


def replay(tree, path, catalog, record, *, profile=_STANDARD, footnote_evidence=None, conditional_continuations=False,
           conditional_adjacent_punctuation=False):
    """Recompute and replay a sequence in memory for a downstream consumer.

    Serialized plans are data, not authority: reject stale or altered records and
    check the hash chain again as edits are applied. The returned bytes are not a
    protected write receipt or a compiler resource snapshot.
    """
    expected = preview(tree, path, catalog, profile=profile, footnote_evidence=footnote_evidence,
                       conditional_continuations=conditional_continuations,
                       conditional_adjacent_punctuation=conditional_adjacent_punctuation)
    return _replay_expected(tree, record, expected, profile=profile, conditional_continuations=conditional_continuations,
                            conditional_adjacent_punctuation=conditional_adjacent_punctuation)


def _replay_expected(tree, record, expected, *, profile=_STANDARD, conditional_continuations=False,
                     conditional_adjacent_punctuation=False):
    _profile(profile)
    if (type(record) is not dict or expected is None or not _same_data(expected, record)
            or expected['verification'] != _verification(profile, conditional_continuations, conditional_adjacent_punctuation)):
        raise SafetyError('Sequence is stale, altered, incomplete or withheld')
    current = tree.source
    for step in record['steps']:
        if digest(current) != step['baseline_sha256']:
            raise SafetyError('Sequence baseline hash mismatch')
        edits = tuple(Edit(row['start'], row['end'], row['replacement'].encode('utf-8'))
                      for row in step['edits'])
        proposal = plan(current, edits)
        if proposal.deferred or len(proposal.edits) != len(edits):
            raise SafetyError('Sequence contains conflicting or ineffective edits')
        current = proposal.candidate()
        if digest(current) != step['candidate_sha256']:
            raise SafetyError('Sequence candidate hash mismatch')
    if digest(current) != record['candidate_sha256']:
        raise SafetyError('Sequence final hash mismatch')
    return current


def explicit_withheld_record(source):
    """Describe a caller-selected whole source; this is not a rule finding."""
    return dict(path=source.path, baseline_sha256=digest(source.source), candidate_sha256=None,
                verification='withheld', steps=[], rule_id=None,
                reason_code='caller-excluded-source',
                reason='Caller excluded this entire source from all preview rule stages')


def validate_explicit_withheld(context, paths):
    """Validate the separately supplied, exact caller selection."""
    if type(paths) is not tuple:
        raise SafetyError('Explicit source withholding requires a tuple of paths')
    if (len(paths) > len(context.sources) or any(type(path) is not str for path in paths)
            or len(set(paths)) != len(paths)):
        raise SafetyError('Invalid or duplicate explicitly withheld sources')
    from typst_lint import relative
    for path in paths:
        relative(path)
    if not set(paths).issubset({source.path for source in context.sources}):
        raise SafetyError('Explicitly withheld source is not a captured document source')
    return frozenset(paths)


def replay_document(context, records, catalog, *, profile=_STANDARD, footnote_evidence=None,
                    partial_unsupported=False, withheld_records=None, partial_unproven_binding=False,
                    explicit_withheld_sources=(), conditional_continuations=False, conditional_adjacent_punctuation=False):
    """Derive an immutable in-memory source map from one issued include capture.

    Every captured source must have exactly its current sequence record or no
    record when no changes are needed. Any withheld, omitted, duplicate or
    foreign record refuses the whole map. Includes establish no import/resource
    closure and the returned mapping is not compiler or write authority.
    """
    _profile(profile)
    if type(conditional_continuations) is not bool or type(conditional_adjacent_punctuation) is not bool:
        raise SafetyError('Conditional source selections must be Boolean')
    if type(partial_unsupported) is not bool:
        raise SafetyError('Partial preview selection must be an explicit Boolean')
    if (type(partial_unproven_binding) is not bool
            or (partial_unproven_binding and not partial_unsupported)):
        raise SafetyError('Unproven binding selection requires explicit partial preview')
    if withheld_records is None:
        withheld_records = []
    include_context.assert_include_context(context)
    explicit = validate_explicit_withheld(context, explicit_withheld_sources)
    if (type(withheld_records) is not list or len(withheld_records) > len(context.sources)
            or (withheld_records and not partial_unsupported and not explicit)):
        raise SafetyError('Invalid partial preview withheld records')
    if footnote_evidence is not None:
        import document_footnote_evidence
        if (type(footnote_evidence) is not document_footnote_evidence.PreviewFootnoteEvidence
                or footnote_evidence._session._context is not context):
            raise SafetyError('Conditional footnote evidence does not match the include capture')
        document_footnote_evidence._valid(footnote_evidence)
    if type(records) is not list or len(records) > len(context.sources):
        raise SafetyError('Invalid document sequence record set')
    indexed = {}
    for record in records:
        if type(record) is not dict or type(record.get('path')) is not str or record['path'] in indexed:
            raise SafetyError('Invalid or duplicate document sequence path')
        indexed[record['path']] = record
    withheld = {}
    for record in withheld_records:
        if (type(record) is not dict or type(record.get('path')) is not str
                or record['path'] in withheld or record['path'] in indexed):
            raise SafetyError('Invalid or duplicate partial preview source path')
        withheld[record['path']] = record
    if not (set(indexed) | set(withheld)).issubset({source.path for source in context.sources}):
        raise SafetyError('Document sequence contains a foreign path')
    candidates = {}
    for source in context.sources:
        record = indexed.get(source.path)
        excluded = withheld.get(source.path)
        if source.path in explicit:
            if record is not None or not _same_data(explicit_withheld_record(source), excluded):
                raise SafetyError('Explicitly withheld source record is stale, altered or missing')
            candidates[source.path] = source.source
            continue
        expected = preview(source.tree, source.path, catalog, profile=profile, footnote_evidence=footnote_evidence,
                           classify_deferred=partial_unsupported,
                           classify_unproven_binding=partial_unproven_binding,
                           conditional_continuations=conditional_continuations,
                           conditional_adjacent_punctuation=conditional_adjacent_punctuation)
        if excluded is not None:
            if (not partial_unsupported or expected is None or expected.get('verification') != 'withheld'
                    or expected.get('reason_code') not in (('unsupported-deferred-footnote-binding',
                        'unproven-source-footnote-binding') if partial_unproven_binding else
                        ('unsupported-deferred-footnote-binding',))
                    or not _same_data(expected, excluded)):
                raise SafetyError('Partial preview withheld record is stale, altered or unsupported')
            candidates[source.path] = source.source
        elif expected is None and record is None:
            candidates[source.path] = source.source
        else:
            candidates[source.path] = _replay_expected(source.tree, record, expected, profile=profile,
                                                       conditional_continuations=conditional_continuations,
                                                       conditional_adjacent_punctuation=conditional_adjacent_punctuation)
    conditional_calls = {record['path']: tuple(offset for step in record['steps']
                         for offset in step.get('conditional_footnote_calls', ())) for record in records}
    conditional_calls = {path: offsets for path, offsets in conditional_calls.items() if offsets}
    if conditional_calls:
        if footnote_evidence is None:
            raise SafetyError('Conditional footnote evidence is missing at replay')
        import document_footnote_evidence
        document_footnote_evidence.PreviewFootnoteEvidence.validate_staged_facades(
            footnote_evidence, candidates, conditional_calls)
    include_context.assert_include_context(context)
    return MappingProxyType(candidates)
