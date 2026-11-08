"""Conditional project-ref facade provenance from explicit package snapshots.

Exports and literal call sites are traced separately. Neither creates a
static-resolver binding, evaluates a module, reads a catalog or grants edits.
The reviewed project-references package may be in a separately supplied root.
"""
from dataclasses import dataclass, field, replace
from bisect import bisect_right
import os

import conditional_highlight as conditional
import import_graph as graph
import namespace_multiroot
import namespace_packages
from package_loader import PackageInputError, _absolute_root, load_packages
from static_resolver import MATH_BLOCKS, MATH_BLOCKS_SOURCE_SHA256
from static_resolver import PROJECT_REFERENCES, PROJECT_REFERENCES_SOURCE_SHA256
from typst_lint import digest

NAME = 'project-ref'
MAX_CALL_IMPORT_HASH_BYTES = 128 * 1024 * 1024
CONDITIONS = (
    'The runtime selects the exact captured packages and source files.',
    'Live paths may have changed since capture; the snapshot is not a filesystem lease.',
    'All referenced imports load and initialize successfully.',
    'This export is used at the intended call site without later shadowing.',
)


@dataclass(frozen=True)
class ReferenceExport:
    path: str
    name: str
    status: str
    reason: str | None = None
    route: tuple = ()
    source_hashes: tuple = ()
    package_evidence: tuple = ()
    package_requests: tuple = ()
    conditions: tuple[str, ...] = CONDITIONS
    edit_permission: bool = field(default=False, init=False)


CALL_CONDITIONS = CONDITIONS[:3] + (
    'Before this call, no executed expression mutates its captured lexical binding.',
    'The reported call and any enclosing literal content or callback actually execute.',
    'This trace does not prove catalog membership, argument validity or rendered output.',
)


@dataclass(frozen=True)
class ReferenceCall:
    path: str
    start: int
    end: int
    line: int
    column: int
    source_sha256: str
    route: tuple
    source_hashes: tuple
    package_evidence: tuple
    package_requests: tuple
    conditions: tuple[str, ...] = CALL_CONDITIONS
    status: str = field(default='CONDITIONAL', init=False)
    edit_permission: bool = field(default=False, init=False)


@dataclass(frozen=True)
class ReferenceCalls:
    calls: tuple[ReferenceCall, ...] = ()
    unresolved: tuple[str, ...] = ()
    status: str = 'CONDITIONAL'
    edit_permission: bool = field(default=False, init=False)


def _reference_scoped_body(tree, index, canonical):
    """Extract a literal callback with inert, explicit wrapper options."""
    nodes = tree.nodes
    children = nodes[index].children
    if len(children) != 2 or nodes[children[1]].kind != 'Args':
        return None, 'Scoped wrapper requires one direct positional callback'
    positional, named = [], set()
    for child in graph.significant(tree, children[1]):
        kind = nodes[child].kind
        if kind in ('LeftParen', 'RightParen', 'Comma'):
            continue
        if kind == 'Named':
            parts = graph.significant(tree, child)
            if len(parts) != 3 or nodes[parts[0]].kind != 'Ident':
                return None, 'Unsupported scoped-wrapper option syntax'
            name = graph.raw(tree, parts[0]).decode('utf-8')
            if (name not in conditional._SCOPED_CALLBACKS[canonical] or name in named
                    or nodes[parts[2]].kind not in ('Auto', 'Str', 'Ident')):
                return None, 'Scoped-wrapper option is unsupported or potentially effectful'
            named.add(name)
        else:
            positional.append(child)
    if len(positional) != 1 or nodes[positional[0]].kind != 'Closure':
        return None, 'Scoped wrapper requires one direct positional callback'
    closure = graph.significant(tree, positional[0])
    if len(closure) != 3 or nodes[closure[0]].kind != 'Params' or nodes[closure[1]].kind != 'Arrow':
        return None, 'Scoped callback requires an anonymous arrow closure'
    params = graph.significant(tree, closure[0])
    if len(params) == 3 and nodes[params[0]].kind == 'LeftParen' and nodes[params[2]].kind == 'RightParen':
        params = (params[1],)
    if len(params) != 1 or nodes[params[0]].kind != 'Ident':
        return None, 'Scoped callback requires exactly one positional identifier'
    body = closure[2]
    while nodes[body].kind == 'Parenthesized':
        parts = graph.significant(tree, body)
        if len(parts) != 3 or nodes[parts[0]].kind != 'LeftParen' or nodes[parts[2]].kind != 'RightParen':
            return None, 'Scoped callback return is not one literal content block'
        body = parts[1]
    if nodes[body].kind != 'ContentBlock':
        return None, 'Scoped callback return is not one literal content block'
    return (body, (graph.raw(tree, params[0]).decode('utf-8'),)), None


class _ReferenceNamespace(conditional._Analyzer):
    def __init__(self, context, namespaces, reference_package):
        super().__init__(context, namespaces, (conditional.RULE_ID,), True)
        # Multi-root evidence is accepted only in this reference-specific
        # analyzer. It cannot grant highlight or paragraph-marker capabilities.
        self.multi_root = None
        if namespace_multiroot.verify_multiroot_capture(namespaces):
            self.multi_root = namespaces
            self.reason = conditional._graph_reason(context)
            self.packages = {item.evidence.import_path: item for item in namespaces.packages}
            self.evidence = namespaces.evidence
        self.reference_package = reference_package
        self.reference_evidence = reference_package.evidence[0]
        self.call_import_hash_bytes = 0

    def _selection(self, tree, index):
        shape = graph.import_shape(tree, index)
        combined = conditional._combined_import(tree, index) if shape is None else None
        if combined is not None:
            return combined[0], combined[1], True
        return shape, None, False

    def incoming(self, tree, index, path, active):
        if self.exhausted:
            return conditional._Module(reason=self.exhausted)
        shape, alias, combined = self._selection(tree, index)
        request = shape.request if shape is not None else None
        # A reviewed namespace-only variant contributes names but no helper or
        # marker identities. Preserve it as opaque for every imported name.
        module_alias = conditional._module_alias(tree, index) if shape is None else None
        collision_request = request if request is not None else (module_alias[0] if module_alias else None)
        package = self.packages.get(collision_request)
        if type(package) is namespace_packages.CollisionOnlyNamespace:
            if shape is None:
                return conditional._Module(((module_alias[1], None),), packages=(package.evidence,),
                                           requests=(collision_request,))
            items = tuple((name, name) for name in package.exports) if shape.items is None else shape.items
            if len(items) + int(combined) > conditional.MAX_BINDINGS:
                return self.exhaust('Conditional namespace binding limit exceeded')
            if any(original not in package.exports for original, _ in items):
                return conditional._Module(reason=f'Requested namespace export unavailable: {collision_request}',
                                           intrinsic_refusal=True)
            exports = [(alias, None)] if combined else []
            exports.extend((local, None) for _, local in items)
            if not self.bindings(exports):
                return conditional._Module(reason=self.exhausted)
            return conditional._Module(tuple(exports), packages=(package.evidence,), requests=(collision_request,))
        if request == PROJECT_REFERENCES:
            items = ((NAME, NAME),) if shape.items is None else shape.items
            if len(items) + int(combined) > conditional.MAX_BINDINGS:
                return self.exhaust('Conditional namespace binding limit exceeded')
            if any(original != NAME for original, _ in items):
                return conditional._Module(reason='Requested project-reference export unavailable', intrinsic_refusal=True)
            exports = [(alias, None)] if combined else []
            for original, local in items:
                value = conditional._Value((conditional.NameRoute(PROJECT_REFERENCES,
                    PROJECT_REFERENCES_SOURCE_SHA256, 'package-export', PROJECT_REFERENCES, NAME, NAME),), NAME)
                value = self.route(value, conditional.NameRoute(path, digest(tree.source),
                    ('combined-' if combined else '') + ('wildcard-import' if shape.items is None else 'named-import'),
                    request, original, local))
                if self.exhausted:
                    return conditional._Module(reason=self.exhausted)
                exports.append((local, value))
            if not self.bindings(exports):
                return conditional._Module(reason=self.exhausted)
            return conditional._Module(tuple(exports), packages=(self.reference_evidence,), requests=(request,))

        result = super().incoming(tree, index, path, active)
        if result.reason or request != MATH_BLOCKS or shape is None:
            return result
        package = self.packages.get(MATH_BLOCKS)
        if (package is None or package.evidence.source_sha256 != MATH_BLOCKS_SOURCE_SHA256
                or NAME not in package.exports):
            return result
        items = ((NAME, NAME),) if shape.items is None else shape.items
        matches = [(original, local) for original, local in items if original == NAME]
        if not matches:
            return result
        exports = dict(result.exports)
        for original, local in matches:
            # The pinned math-blocks bytes explicitly import this exact named
            # export from project-references and do not redefine it.
            value = conditional._Value((conditional.NameRoute(PROJECT_REFERENCES,
                PROJECT_REFERENCES_SOURCE_SHA256, 'package-export', PROJECT_REFERENCES, NAME, NAME),
                conditional.NameRoute(MATH_BLOCKS, MATH_BLOCKS_SOURCE_SHA256,
                    'named-import', PROJECT_REFERENCES, NAME, NAME)), NAME)
            value = self.route(value, conditional.NameRoute(path, digest(tree.source),
                ('combined-' if combined else '') + ('wildcard-import' if shape.items is None else 'named-import'),
                request, original, local))
            if self.exhausted:
                return conditional._Module(reason=self.exhausted)
            exports[local] = value
        packages = {evidence.import_path: evidence for evidence in result.packages}
        packages[PROJECT_REFERENCES] = self.reference_evidence
        return replace(result, exports=tuple(sorted(exports.items())),
                       packages=tuple(packages[key] for key in sorted(packages)),
                       requests=tuple(sorted(set(result.requests) | {PROJECT_REFERENCES})))


class ConditionalReferenceProvenance:
    """Trace final facade exports from one graph and two explicit package roots."""

    def __init__(self, import_graph, namespace_capture, reference_package_root):
        raw = os.fspath(reference_package_root)
        root = _absolute_root(raw)
        if type(raw) is not str or raw != str(root):
            raise PackageInputError('Reference package root must be canonical and absolute')
        captured = load_packages(root, (PROJECT_REFERENCES,))
        if (len(captured.sources) != 1 or len(captured.evidence) != 1 or captured.unavailable
                or captured.sources[0].import_path != PROJECT_REFERENCES
                or digest(captured.sources[0].source) != PROJECT_REFERENCES_SOURCE_SHA256
                or captured.evidence[0].source_sha256 != PROJECT_REFERENCES_SOURCE_SHA256):
            raise PackageInputError('Exact reviewed project-references package is unavailable')
        self._reference_capture = captured
        self._engine = _ReferenceNamespace(import_graph, namespace_capture, captured)
        # A separately captured reference package cannot override the stated
        # package/cache precedence. Require it in the first root, where its
        # successful capture proves this exact version exists. A reference
        # found only in a later root needs separate first-root absence evidence.
        self._selection_reason = None
        if (self._engine.multi_root is not None
                and str(root) != self._engine.multi_root.roots[0]):
            self._selection_reason = 'Reference package is not captured from the first namespace root'

    @property
    def package_path(self):
        """The explicit root used to capture project-references, even on a refusal."""
        return self._reference_capture.package_path

    @property
    def package_evidence(self):
        """Immutable manifest/source evidence, not a live package selection proof."""
        return self._reference_capture.evidence

    def export(self, path, name=NAME):
        engine = self._engine
        if type(path) is not str or type(name) is not str or not name:
            return ReferenceExport(str(path), str(name), 'UNAVAILABLE', 'Invalid source path or export name')
        if engine.reason or engine.exhausted:
            return ReferenceExport(path, name, 'UNAVAILABLE', engine.reason or engine.exhausted)
        if engine.multi_root is not None and not namespace_multiroot.verify_multiroot_capture(engine.multi_root):
            return ReferenceExport(path, name, 'UNAVAILABLE', 'Multi-root capture was changed or expired')
        if self._selection_reason:
            return ReferenceExport(path, name, 'UNAVAILABLE', self._selection_reason)
        module = engine.module(path, ())
        if module.reason:
            return ReferenceExport(path, name, 'UNAVAILABLE', module.reason)
        value = dict(module.exports).get(name)
        if value is None or value.canonical_name != NAME:
            return ReferenceExport(path, name, 'UNAVAILABLE', 'Export identity is absent, shadowed or opaque')
        return ReferenceExport(path, name, 'CONDITIONAL', route=value.route,
            source_hashes=module.sources, package_evidence=module.packages,
            package_requests=module.requests)

    def calls(self, path):
        """Trace direct identifier calls in one selected, captured source.

        This is lexical, conditional evidence only: arbitrary expression side
        effects, module initialization and whether content executes are not
        evaluated. Unknown scopes and declarations discard the affected path.
        """
        engine = self._engine
        if (type(path) is not str or engine.reason or engine.exhausted or self._selection_reason
                or (engine.multi_root is not None
                    and not namespace_multiroot.verify_multiroot_capture(engine.multi_root))):
            return ReferenceCalls(unresolved=(engine.reason or engine.exhausted or self._selection_reason
                                               or 'Invalid or expired capture',), status='UNAVAILABLE')
        item = engine.graph.source_for(path)
        if item is None or path not in engine.graph.selected:
            return ReferenceCalls(unresolved=('Source is not in the selected capture',), status='UNAVAILABLE')
        try:
            tree = conditional.parse(item.source)
        except conditional.AdapterError as error:
            return ReferenceCalls(unresolved=(f'Source syntax unavailable: {error}',), status='UNAVAILABLE')
        if tree.nodes[0].errors or tree.mode != 'markup':
            return ReferenceCalls(unresolved=('Malformed or non-markup source',), status='UNAVAILABLE')
        if not engine.charge_ast(tree, passes=3):
            return ReferenceCalls(unresolved=(engine.exhausted,), status='UNAVAILABLE')
        nodes, source = tree.nodes, tree.source
        found, unresolved = [], []
        work = [0]
        # Compute conservative subtree properties once. This bounds assignment,
        # return and expression-declaration checks even for deep syntax trees.
        mutations = [False] * len(nodes)
        returns = [False] * len(nodes)
        injections = [False] * len(nodes)
        calls_below = [False] * len(nodes)
        for index in range(len(nodes) - 1, -1, -1):
            node = nodes[index]
            children = node.children
            effect_children = (tuple(c for c in children if nodes[c].kind == 'Params')
                               if node.kind == 'Closure' else children)
            mutations[index] = (node.kind == 'DestructAssignment'
                or (node.kind == 'Binary' and any(nodes[c].kind in conditional._ASSIGNMENTS
                                                   for c in graph.significant(tree, index)))
                or any(mutations[c] for c in effect_children))
            returns[index] = node.kind == 'FuncReturn' or any(returns[c] for c in effect_children)
            calls_below[index] = node.kind == 'FuncCall' or any(calls_below[c] for c in children)
            if node.kind == 'Closure':
                injections[index] = any(injections[c] for c in effect_children)
            elif node.kind in ('ContentBlock', 'CodeBlock'):
                injections[index] = False
            else:
                injections[index] = (node.kind in ('LetBinding', 'ModuleImport', 'ModuleInclude')
                                     or any(injections[c] for c in children))
        line_starts = [0] + [i + 1 for i, byte in enumerate(source) if byte == 10]

        def visit(index, env, sources, packages, requests, depth=0):
            work[0] += 1
            if work[0] > conditional.MAX_AST_WORK:
                engine.exhaust('Reference call inspection work limit exceeded')
                return
            if depth > 256:
                unresolved.append('Reference call inspection budget exceeded')
                return
            node = nodes[index]
            if node.kind in ('Markup', 'ContentBlock'):
                if not engine.charge_scope(env, sources, packages, requests, ()):
                    return
                local = dict(env)
                local_sources, local_packages, local_requests = dict(sources), dict(packages), set(requests)
                for child in node.children:
                    part = nodes[child]
                    if mutations[child]:
                        local.clear()
                        unresolved.append(f'Possible namespace mutation at byte {part.start}')
                    elif part.kind == 'ModuleImport':
                        shape, _, _ = engine._selection(tree, child)
                        multiplicity = 1
                        if shape is not None and shape.items is not None:
                            multiplicity += len(shape.items)
                        elif shape is not None and shape.items is None:
                            package = engine.packages.get(shape.request)
                            if package is not None:
                                multiplicity += len(package.exports)
                            elif not shape.request.startswith('@'):
                                target = engine.graph.target_for(path, shape.request)
                                if target is not None:
                                    multiplicity += len(engine.module(target, (path,)).exports)
                        engine.call_import_hash_bytes += len(source) * multiplicity
                        if engine.call_import_hash_bytes > MAX_CALL_IMPORT_HASH_BYTES:
                            engine.exhaust('Reference call import hashing limit exceeded')
                            return
                        incoming = engine.incoming(tree, child, path, (path,))
                        if incoming.reason:
                            local.clear()
                            unresolved.append(f'{incoming.reason} at byte {part.start}')
                        else:
                            local.update(incoming.exports)
                            if not engine.bindings(local):
                                return
                            local_sources.update((s.path, s) for s in incoming.sources)
                            local_packages.update((p.import_path, p) for p in incoming.packages)
                            local_requests.update(incoming.requests)
                    elif part.kind == 'LetBinding':
                        if calls_below[child] and 'Declaration initializer or deferred body is not inspected' not in unresolved:
                            unresolved.append('Declaration initializer or deferred body is not inspected')
                        parts = graph.significant(tree, child)
                        if (len(parts) == 4 and nodes[parts[0]].kind == 'Let'
                                and nodes[parts[1]].kind == 'Ident' and nodes[parts[2]].kind == 'Eq'
                                and nodes[parts[3]].kind == 'Ident'):
                            bound = graph.raw(tree, parts[1]).decode('utf-8')
                            original = graph.raw(tree, parts[3]).decode('utf-8')
                            prior = local.get(original)
                            value = (engine.route(prior, conditional.NameRoute(path, item.sha256,
                                         'alias', '', original, bound)) if prior is not None else None)
                            binding = (bound, value)
                        else:
                            binding = conditional._let(tree, child, local, path, engine)
                        if binding is None or any(injections[c] for c in part.children):
                            local.clear()
                            unresolved.append(f'Unsupported declaration at byte {part.start}')
                        else:
                            local[binding[0]] = binding[1]
                            if not engine.bindings(local):
                                return
                    elif part.kind == 'Contextual':
                        # Context bodies have a separate lexical block. Do not
                        # inspect calls inside, but preserve the surrounding
                        # namespace when the effects scan saw no assignment.
                        parts = graph.significant(tree, child)
                        if len(parts) != 2 or nodes[parts[1]].kind not in ('CodeBlock', 'ContentBlock'):
                            local.clear()
                        unresolved.append(f'Contextual body not inspected at byte {part.start}')
                    elif part.kind in ('ModuleInclude', 'CodeBlock', 'Conditional', 'WhileLoop', 'ForLoop'):
                        local.clear()
                        unresolved.append(f'Unsupported namespace scope at byte {part.start}')
                    else:
                        visit(child, local, local_sources, local_packages, local_requests, depth + 1)
                return
            if node.kind == 'Equation':
                reason = 'Code expressions inside equations are not inspected'
                if reason not in unresolved:
                    unresolved.append(reason)
                return
            if node.kind == 'Closure':
                reason = 'Deferred closure body is not inspected'
                if calls_below[index] and reason not in unresolved:
                    unresolved.append(reason)
                return
            if node.kind in ('LetBinding', 'ModuleImport', 'ModuleInclude',
                             'CodeBlock', 'Conditional', 'WhileLoop', 'ForLoop', 'Contextual',
                             'Raw'):
                reason = 'Nested dynamic scope is not inspected'
                if node.kind != 'Raw' and calls_below[index] and reason not in unresolved:
                    unresolved.append(reason)
                return
            if node.kind == 'FuncCall' and node.children and nodes[node.children[0]].kind == 'Ident':
                name = graph.raw(tree, node.children[0]).decode('utf-8')
                value = env.get(name)
                if value is not None and value.canonical_name == NAME:
                    line = bisect_right(line_starts, node.start)
                    start = line_starts[line - 1]
                    engine.ast_work += node.start - start
                    if not engine.charge_scope(env, sources, packages, requests, value.route):
                        return
                    column = len(source[start:node.start].decode('utf-8')) + 1
                    found.append(ReferenceCall(path, node.start, node.end, line, column, item.sha256,
                        value.route, tuple(sources[k] for k in sorted(sources)),
                        tuple(packages[k] for k in sorted(packages)), tuple(sorted(requests))))
                if value is not None and value.canonical_name in conditional._SCOPED_CALLBACKS:
                    callback, reason = _reference_scoped_body(tree, index, value.canonical_name)
                    if callback is not None:
                        if returns[callback[0]]:
                            unresolved.append(f'Callback has an active return at byte {nodes[callback[0]].start}')
                            return
                        if not engine.charge_scope(env, sources, packages, requests, (), parameters=callback[1]):
                            return
                        child_env = dict(env)
                        child_env.update((name, None) for name in callback[1])
                        visit(callback[0], child_env, sources, packages, requests, depth + 1)
                    else:
                        unresolved.append(f'{reason} at byte {node.start}')
                    return
            elif node.kind == 'FuncCall':
                reason = 'Dynamic or field call identity is not inspected'
                if reason not in unresolved:
                    unresolved.append(reason)
            # Declarations embedded in expressions can affect name visibility in
            # ways this bounded walker does not model. Never promote those sites.
            if any(injections[c] for c in node.children):
                unresolved.append(f'Unsupported expression declaration at byte {node.start}')
                return
            for child in node.children:
                visit(child, env, sources, packages, requests, depth + 1)

        visit(0, {}, {path: conditional.SourceHash(path, item.sha256)},
              {p.import_path: p for p in engine.evidence}, set())
        if engine.exhausted:
            return ReferenceCalls(unresolved=(engine.exhausted,), status='UNAVAILABLE')
        return ReferenceCalls(tuple(found), tuple(dict.fromkeys(unresolved)),
                              'PARTIAL' if unresolved else 'CONDITIONAL')
