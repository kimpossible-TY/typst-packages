"""Experimental one-site source-only math preview in a reviewed callback.

Not a CLI or editing capability.  Successful results remain conditional on
runtime package selection, callback execution, styles, and rendering.
"""
import conditional_modern_highlight as modern
import conditional_highlight as highlight
import scoped_reference_preview as scoped
import document_math_builtin_evidence as math_evidence
import document_modern_highlight_preview as preview
import import_graph
from document_gap_preview import _MappedEdits
from edit_plan import plan
from syntax_client import parse
from typst_lint import digest
from static_resolver import Binding
from typst_lint import InputError, SafetyError, load_rules

MAX_BODY_NODES = 60_000
_FORBIDDEN_BODY = frozenset(('Closure', 'Contextual', 'ShowRule', 'Conditional', 'ForLoop', 'WhileLoop',
                             'ModuleImport', 'ModuleInclude', 'DestructAssignment', 'LetBinding'))
_ASSIGNMENTS = frozenset(('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq'))
CONDITIONS = preview.MATH_CONDITIONS + (
    'The captured reviewed wrapper executes the same callback in the same runtime context.',
    'The captured builtin math bindings remain the intended runtime values.',
    'This prototype does not establish execution, side effects, styles, or rendering.',
)


def _callback_body(tree, callback):
    """Admit exactly one root-wrapper callback with a structurally plain body."""
    if (type(callback) is not dict or type(callback.get('parameters')) not in (tuple, list)
            or tuple(callback['parameters']) != ('s',)
            or any(type(callback.get(k)) is not int for k in ('body_start', 'body_end', 'call_start', 'call_end'))):
        raise SafetyError('Callback capture is unsupported')
    nodes = tree.nodes
    parents = {c: i for i, n in enumerate(nodes) for c in n.children}
    bodies = [i for i, n in enumerate(nodes) if n.kind == 'ContentBlock'
              and (n.start, n.end) == (callback['body_start'], callback['body_end'])]
    if len(bodies) != 1:
        raise SafetyError('Callback body is ambiguous')
    body = bodies[0]
    closure = parents.get(body)
    if closure is None or nodes[closure].kind != 'Closure':
        raise SafetyError('Callback is not a direct literal closure')
    parts = import_graph.significant(tree, closure)
    if (len(parts) != 3 or tuple(nodes[i].kind for i in parts) != ('Params', 'Arrow', 'ContentBlock')
            or parts[2] != body):
        raise SafetyError('Callback shape is unsupported')
    params = import_graph.significant(tree, parts[0])
    if (len(params) != 1 or nodes[params[0]].kind != 'Ident'
            or tree.source[nodes[params[0]].start:nodes[params[0]].end] != b's'):
        raise SafetyError('Callback parameter is unsupported')
    args = parents.get(closure)
    call = parents.get(args) if args is not None else None
    if (args is None or nodes[args].kind != 'Args' or call is None or nodes[call].kind != 'FuncCall'
            or (nodes[call].start, nodes[call].end) != (callback['call_start'], callback['call_end'])
            or parents.get(call) != 0 or nodes[0].kind != 'Markup'):
        raise SafetyError('Callback wrapper ownership is unsupported')
    pending = [body]
    count = 0
    while pending:
        i = pending.pop()
        count += 1
        if count > MAX_BODY_NODES:
            raise SafetyError('Scoped math callback work budget exceeded')
        n = nodes[i]
        if n.kind in _FORBIDDEN_BODY or n.errors:
            raise SafetyError('Callback body contains dynamic or binding syntax')
        if n.kind == 'Binary' and any(nodes[c].kind in _ASSIGNMENTS for c in n.children):
            raise SafetyError('Callback body contains an assignment')
        pending.extend(n.children)
    return closure, body


class _ScopedMathResolver(math_evidence._MathResolver):
    def __init__(self, tree, path, engine, callback):
        super().__init__(tree, path, engine)
        self.allowed, self.body = _callback_body(tree, callback)
        self.visited = False

    def visit(self, index, state, *, output=False):
        if index != self.allowed:
            return super().visit(index, state, output=output)
        self._watch(state)
        if state.deferred or self.visited:
            raise SafetyError('Callback capture was deferred or visited more than once')
        self.visited = True
        # Captured outer bindings are immutable inside Typst functions. Preserve
        # only exact reviewed builtins; unknown/shadowed names remain unknown.
        known = {name for name in math_evidence._BUILTINS
                 if self._lookup(state, name) == Binding('builtin-math', name)}
        nested = state.nested(deferred=True)
        nested.deferred = False
        for name in known:
            nested.bind(name, Binding('builtin-math', name))
        nested.bind('s', None)
        self.record(index, state)
        self.visit(self.body, nested)
        return None


def _calls(tree, diagnostic, resolver):
    nodes = tree.nodes
    matches = [i for i, n in enumerate(nodes) if n.kind == 'FuncCall'
               and (n.start, n.end) == (diagnostic['start'], diagnostic['end'])]
    if len(matches) != 1:
        raise SafetyError('Highlight source call is ambiguous')
    pending, calls = [matches[0]], []
    while pending:
        i = pending.pop()
        node = nodes[i]
        if node.kind == 'Equation':
            inner = [i]
            while inner:
                j = inner.pop()
                n = nodes[j]
                if n.kind in math_evidence._FORBIDDEN_MATH:
                    raise SafetyError('Math arguments contain unsupported code')
                if n.kind == 'MathCall':
                    calls.append(j)
                    if len(calls) > math_evidence.MAX_ITEMS or j not in resolver.resolved:
                        raise SafetyError('Math binding is unresolved or exceeds its work limit')
                inner.extend(n.children)
        else:
            pending.extend(node.children)
    if not calls or not resolver.visited or resolver.engine.exhausted:
        raise SafetyError('No reviewed callback math call was established')
    return calls


def preview_one(evidence, path, start):
    """Reissue a site from sealed evidence, then verify one independent plan."""
    if type(evidence) is not modern.ModernHighlightAnalyzer or type(path) is not str or type(start) is not int:
        raise InputError('Issued modern evidence and a source location are required')
    modern.ModernHighlightAnalyzer._check(evidence)
    source = evidence._session._context.source_for(path)
    if source is None:
        raise InputError('Source is outside the issued capture')
    package = next((item for item in evidence._session._namespaces.packages
                    if item.evidence.import_path == highlight.SCOPED_ANNOTATIONS), None)
    if package is None or package.evidence.source_sha256 != scoped._SCOPE_HASH:
        raise SafetyError('Exact reviewed callback wrapper is unavailable')
    replay = modern._ModernAnalyzer(evidence._session, evidence._variant,
                                    scoped_callbacks=evidence._scoped_callbacks)
    report = modern.ModernHighlightAnalyzer._analyze_one(evidence, source.tree, path, replay)
    modern.ModernHighlightAnalyzer._check(evidence)
    matches = [d for d in report['diagnostics'] if d['start'] == start]
    if len(matches) != 1:
        raise InputError('No unique active highlight diagnostic at this location')
    diagnostic = matches[0]
    contexts = diagnostic.get('callback_context') or []
    if len(contexts) != 1:
        raise SafetyError('Exactly one issued callback context is required')
    callback = contexts[0]
    ordinal = callback.get('content_context_index')
    routes = diagnostic.get('content_context', ())
    if type(ordinal) is not int or not 0 <= ordinal < len(routes) or not routes[ordinal]:
        raise SafetyError('Issued callback wrapper route is unavailable')
    origin = routes[ordinal][0]
    if (origin.get('path') != highlight.SCOPED_ANNOTATIONS
            or origin.get('source_sha256') != scoped._SCOPE_HASH
            or origin.get('operation') != 'package-export'
            or origin.get('imported_name') not in highlight._SCOPED_CALLBACKS):
        raise SafetyError('Callback wrapper route is not the reviewed artifact')
    resolver = _ScopedMathResolver(source.tree, path, math_evidence._engine(evidence._session), callback)
    resolver.visit(0, resolver.state)
    _calls(source.tree, diagnostic, resolver)
    empty = dict(path=path, baseline_sha256=digest(source.tree.source), candidate_sha256=None,
                 verification='withheld', conditions=CONDITIONS, edits=[], runtime_binding_proved=False,
                 execution_proved=False, coverage_complete=False, render_equivalence_proved=False,
                 edit_permission=False, compiler_permission=False)
    try:
        edits, plans = preview._select(source.tree, [diagnostic],
                        proven_math_spans=frozenset({(diagnostic['start'], diagnostic['end'])}))
        if len(edits) != 1:
            raise SafetyError('Highlight syntax is outside this prototype')
        proposal = plan(source.tree.source, edits)
        if proposal.deferred or len(proposal.edits) != 1:
            raise SafetyError('Conflicting highlight split')
        candidate = proposal.candidate()
        parsed = parse(candidate, source.tree.mode)
        records = load_rules()[0]['rules']
        if parsed.nodes[0].errors or preview._scopes(source.tree, records, proposal.edits) != preview._scopes(parsed, records):
            raise SafetyError('Highlight split changed syntax or suppression targets')
        preview._verify_insertions(source.tree, parsed, proposal.edits, plans)
        mapper = _MappedEdits(proposal.edits)
        mapped = dict(callback)
        for key in ('body_start', 'body_end', 'call_start', 'call_end'):
            mapped[key] = mapper.mapped(callback[key])
        after = _ScopedMathResolver(parsed, path, math_evidence._engine(evidence._session), mapped)
        after.visit(0, after.state)
        if not after.visited or after.engine.exhausted:
            raise SafetyError('Candidate callback math capture was not established')
        for edit, _, _, spans in plans:
            base = mapper.mapped(edit.start)
            for kind, begin, end, _ in spans:
                if kind != 'reviewed-math-equation':
                    continue
                equations = [i for i, n in enumerate(parsed.nodes) if n.kind == 'Equation'
                             and (n.start, n.end) == (base + begin, base + end)]
                if len(equations) != 1:
                    raise SafetyError('Candidate equation ownership changed')
                pending = [equations[0]]
                while pending:
                    i = pending.pop()
                    n = parsed.nodes[i]
                    if n.kind == 'MathCall' and i not in after.resolved:
                        raise SafetyError('Candidate math binding is no longer supported')
                    pending.extend(n.children)
        before_resolution = preview.resolve(source.tree, packages=())
        after_resolution = preview.resolve(parsed, packages=())
        before_display = preview.analyze_displays(source.tree, path, catalog=records)
        after_display = preview.analyze_displays(parsed, path, catalog=records)
        if any(not preview._same_findings(source.tree, before_display[rid], parsed, after_display[rid], edits)
               for rid in before_display):
            raise SafetyError('Highlight split changes display findings')
        before_scripts = preview.analyze_scripts(source.tree, path, catalog=records, _verify=False, _allow_code_call=True)
        after_scripts = preview.analyze_scripts(parsed, path, catalog=records, _verify=False, _allow_code_call=True)
        if not preview._same_script_findings_with_moved_equations(source.tree, before_scripts, parsed,
                                                                  after_scripts, edits, plans):
            raise SafetyError('Highlight split changes script findings')
        before_notes = preview.footnote_rules.analyze_conditional_nested(source.tree, path, before_resolution, catalog=records)
        after_notes = preview.footnote_rules.analyze_conditional_nested(parsed, path, after_resolution, catalog=records)
        if any(not preview._same_findings(source.tree, before_notes[rid], parsed, after_notes[rid], edits)
               for rid in preview.footnote_rules.RULE_IDS):
            raise SafetyError('Highlight split changes footnote findings')
        for analyzer in (preview.paragraph_rules.analyze_conditional_nested_gap,
                         preview.paragraph_rules.analyze_conditional_nested_target,
                         preview.paragraph_rules.analyze_conditional_nested_capitalization,
                         preview.prose_marker_rules.analyze_conditional, preview.continuation_rules.analyze_conditional):
            if not preview._same_findings(source.tree, analyzer(source.tree, path, catalog=records),
                                          parsed, analyzer(parsed, path, catalog=records), edits):
                raise SafetyError('Highlight split changes paragraph findings')
        if not preview._same_findings(source.tree, preview.proof_rules.analyze_conditional(
                source.tree, path, before_resolution, catalog=records), parsed,
                preview.proof_rules.analyze_conditional(parsed, path, after_resolution, catalog=records), edits):
            raise SafetyError('Highlight split changes proof findings')
        modern.ModernHighlightAnalyzer._check(evidence)
        result = dict(empty, verification='conditional-modern-highlight-syntax-directives-and-reanalysis',
                      candidate_sha256=digest(candidate), edits=[dict(start=e.start, end=e.end,
                      replacement=e.replacement.decode('utf-8'), rule_id=preview.RULE) for e in edits])
    except (InputError, SafetyError, OSError, ValueError, RecursionError) as error:
        result = dict(empty, reason=str(error))
    modern.ModernHighlightAnalyzer._check(evidence)
    return result
