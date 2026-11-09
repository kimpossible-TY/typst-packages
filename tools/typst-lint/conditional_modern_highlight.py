"""Diagnostic-only highlight evidence for one independently reviewed artifact.

This does not turn collision-only namespace evidence into a general helper
capability. It recognizes the exact modern text-utils 0.1.2 bytes and their
documented body/mode interface solely for this conditional source diagnostic.
No edit, compiler or runtime-rendering authority follows.
"""
from dataclasses import asdict, dataclass, replace
import json
import weakref

import conditional_footnote_binding as binding
import conditional_highlight as highlight
import facade_provenance
import import_graph
import namespace_packages
from typst_lint import InputError, digest, load_rules

MODERN_SOURCE_SHA256 = '670599f5fca254fdb93487021099678f13ecb6357877e93ebdcf6cc3e17992ed'
MODERN_MANIFEST_SHA256 = 'e1d27052412a39d4f839dede28406159c1c9a5394592a73c2ed5666dcbc60df2'
REVIEWED_VARIANT = 'text-utils-0.1.2-main-c161707'
RULE = highlight.RULE_ID
MAX_BATCH_ITEMS = 100_000
MAX_BATCH_BYTES = 16 * 1024 * 1024
_ISSUED = {}
VARIANT_CONDITION = (
    'The selected highlighted function is the separately reviewed text-utils 0.1.2 '
    'main-c161707 implementation and its captured mode is used at runtime.'
)


class ModernVariantUnavailable(InputError):
    """The selected package is not the exact modern artifact for this route."""


@dataclass(frozen=True)
class _ModernHighlightNamespace:
    """Internal capability for this diagnostic, not a NamespacePackage."""
    evidence: object
    exports: tuple
    origins: tuple


def _body(tree, call_id):
    """Recognize one literal body and the exact default/flow/phrase modes."""
    nodes = tree.nodes
    parts = import_graph.significant(tree, call_id)
    if len(parts) != 2 or nodes[parts[1]].kind != 'Args':
        return None, None, 'Unsupported modern highlighted call arguments'
    bodies, named, mode = [], set(), 'flow'
    for child in import_graph.significant(tree, parts[1]):
        node = nodes[child]
        if node.kind in ('LeftParen', 'RightParen', 'Comma'):
            continue
        if node.kind == 'ContentBlock':
            bodies.append(child)
            continue
        if node.kind != 'Named':
            return None, None, 'Modern highlighted requires one literal content body'
        fields = import_graph.significant(tree, child)
        if len(fields) != 3 or nodes[fields[0]].kind != 'Ident':
            return None, None, 'Unsupported modern highlighted named argument'
        name = import_graph.raw(tree, fields[0]).decode('utf-8')
        if name in named or name != 'mode':
            return None, None, 'Duplicate or unsupported modern highlighted option'
        named.add(name)
        value = import_graph.literal_string(tree, fields[2])
        if value not in ('flow', 'phrase'):
            return None, None, 'Modern highlighted mode is dynamic or unsupported'
        mode = value
    if len(bodies) != 1:
        return None, None, 'Modern highlighted requires exactly one literal content body'
    # Only descend through syntax with known literal content ownership. An
    # opaque call might render math, but is not a negative proof.
    pending, has_math, opaque = [bodies[0]], False, False
    while pending:
        index = pending.pop()
        node = nodes[index]
        if node.kind == 'Equation':
            has_math = True
            continue
        if node.kind in ('ContentBlock', 'Markup', 'Strong', 'Emph'):
            pending.extend(node.children)
        elif node.children and node.kind not in ('Hash',):
            opaque = True
    if not has_math and opaque:
        return None, None, 'Modern highlighted body contains unsupported nested content'
    return has_math, mode, None


class _ModernAnalyzer(highlight._Analyzer):
    """Reuse the source-graph scanner after independently checking the variant."""
    def __init__(self, session, variant, *, scoped_callbacks):
        self.graph = session._graph
        self.rule_ids = frozenset((RULE,))
        self.scoped_callbacks = scoped_callbacks
        self.reason = facade_provenance._graph_reason(self.graph)
        self.packages = {item.evidence.import_path: item for item in session._namespaces.packages
                         if type(item) is namespace_packages.NamespacePackage}
        # This internal adapter is issued only after the exact modern bytes,
        # manifest and selected root have been independently checked. It is
        # never returned as ordinary namespace or source-edit evidence.
        self.packages[highlight.TEXT_UTILS] = _ModernHighlightNamespace(
            variant.evidence, variant.exports, (('highlighted', highlight.TEXT_UTILS),))
        self.evidence = tuple(item.evidence for item in session._namespaces.packages)
        self.trees, self.modules, self.refusals = {}, {}, {}
        self.module_visits = self.ast_work = 0
        self.exhausted = None
        self.known_rule_ids = frozenset(rule['id'] for rule in load_rules()[0]['rules'])

    def body_math(self, tree, call_id):
        has_math, _, reason = _body(tree, call_id)
        return bool(has_math), reason


class ModernHighlightAnalyzer:
    """Exact modern-variant source hints over an issued multi-root session."""
    def __init__(self, session, *, scoped_callbacks=False):
        if type(scoped_callbacks) is not bool:
            raise InputError('Scoped callback selection must be a boolean')
        if not binding._valid(session):
            raise InputError('An unchanged issued multi-root session is required')
        variant = next((item for item in session._namespaces.packages
                        if item.evidence.import_path == highlight.TEXT_UTILS), None)
        if (type(variant) is not namespace_packages.CollisionOnlyNamespace
                or digest(variant.source) != MODERN_SOURCE_SHA256
                or digest(variant.manifest) != MODERN_MANIFEST_SHA256
                or variant.evidence.source_sha256 != MODERN_SOURCE_SHA256
                or variant.exports != namespace_packages._TEXT_EXPORTS):
            raise ModernVariantUnavailable('The exact reviewed modern text-utils variant is unavailable')
        self._session, self._variant, self._scoped_callbacks = session, variant, scoped_callbacks
        identity = id(self)
        def release(reference):
            item = _ISSUED.get(identity)
            if item is not None and item[0] is reference:
                _ISSUED.pop(identity, None)
        _ISSUED[identity] = (weakref.ref(self, release), session, variant, scoped_callbacks)

    def _check(self):
        issued = _ISSUED.get(id(self))
        if (type(self) is not ModernHighlightAnalyzer or issued is None or issued[0]() is not self
                or issued[1] is not self._session or issued[2] is not self._variant
                or issued[3] is not self._scoped_callbacks
                or not binding._valid(self._session)
                or not any(item is self._variant for item in self._session._namespaces.packages)):
            raise InputError('Modern highlight capture changed or is unissued')

    def _analyze_one(self, tree, path, analyzer):
        source = self._session._context.source_for(path)
        if source is None or tree is not source.tree:
            raise InputError('Modern highlight requires the exact issued source tree and path')
        result = highlight._analyze(tree, path, analyzer)
        calls = {}
        if result.diagnostics or result.suppressed:
            for index, node in enumerate(tree.nodes):
                if node.kind == 'FuncCall':
                    key = node.start, node.end
                    calls[key] = index if key not in calls else None
        def describe(finding):
            call = calls.get((finding.start, finding.end))
            if call is None:
                raise InputError('Modern highlight finding does not identify one captured call')
            has_math, mode, reason = _body(tree, call)
            if not has_math or reason:
                raise InputError('Modern highlight finding has inconsistent call evidence')
            item = asdict(replace(finding, conditions=finding.conditions + (VARIANT_CONDITION,)))
            item['mode'] = mode
            call_parts = import_graph.significant(tree, call)
            names = [import_graph.significant(tree, arg) for arg in import_graph.significant(tree, call_parts[1])
                     if tree.nodes[arg].kind == 'Named']
            item['mode_source'] = ('explicit' if any(parts and tree.nodes[parts[0]].kind == 'Ident'
                and import_graph.raw(tree, parts[0]) == b'mode' for parts in names) else 'default')
            return item
        diagnostics = [describe(item) for item in result.diagnostics]
        suppressed = [describe(item) for item in result.suppressed]
        return dict(path=path, source_sha256=digest(tree.source), status=result.status,
                    artifact=REVIEWED_VARIANT, package_evidence=asdict(self._variant.evidence),
                    diagnostics=diagnostics, suppressed=suppressed, unresolved=list(result.unresolved),
                    parse_errors=list(result.parse_errors), runtime_binding_proved=False,
                    execution_proved=False, coverage_complete=False, edit_permission=False,
                    compiler_permission=False, render_equivalence_proved=False)

    def analyze(self, tree, path):
        self._check()
        result = self._analyze_one(tree, path, _ModernAnalyzer(
            self._session, self._variant, scoped_callbacks=self._scoped_callbacks))
        self._check()
        return result

    def analyze_all(self, *, max_items=MAX_BATCH_ITEMS, max_bytes=MAX_BATCH_BYTES):
        """Analyze the captured sources as one all-or-nothing bounded batch."""
        if (type(max_items) is not int or not 0 < max_items <= MAX_BATCH_ITEMS
                or type(max_bytes) is not int or not 0 < max_bytes <= MAX_BATCH_BYTES):
            raise InputError('Invalid modern highlight batch report budget')
        self._check()
        analyzer = _ModernAnalyzer(self._session, self._variant, scoped_callbacks=self._scoped_callbacks)
        batch = dict(status='CONDITIONAL', artifact=REVIEWED_VARIANT, by_file=[], report_items=0,
                     runtime_binding_proved=False, execution_proved=False, coverage_complete=False,
                     edit_permission=False, compiler_permission=False, render_equivalence_proved=False)
        reports, items = batch['by_file'], 1
        encoded = json.dumps(batch, ensure_ascii=False, sort_keys=True, indent=2)
        # Bound the section as nested once in the pretty document JSON report.
        size = len(encoded.encode('utf-8')) + 2 * encoded.count('\n')
        for source in self._session._context.sources:
            result = self._analyze_one(source.tree, source.path, analyzer)
            items += (1 + len(result['diagnostics']) + len(result['suppressed'])
                      + len(result['unresolved']) + len(result['parse_errors']))
            for finding in (*result['diagnostics'], *result['suppressed']):
                items += (len(finding['conditions']) + len(finding['route']) + len(finding['source_hashes'])
                          + len(finding['package_evidence']) + len(finding['package_requests'])
                          + sum(len(route) for route in finding['content_context'])
                          + len(finding['callback_context']))
                items += sum(len(callback['parameters']) for callback in finding['callback_context'])
            encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
            size += len(encoded.encode('utf-8')) + 6 * encoded.count('\n') + (8 if reports else 12)
            if items > max_items or size > max_bytes:
                raise InputError('Modern highlight batch report budget exceeded')
            reports.append(result)
        size += len(str(items)) - 1
        if size > max_bytes:
            raise InputError('Modern highlight batch report budget exceeded')
        batch['report_items'] = items
        self._check()
        return batch
