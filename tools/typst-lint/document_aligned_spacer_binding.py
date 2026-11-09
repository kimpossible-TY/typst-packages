"""Conditional captured binding of canonical dots_space before footnotes.

Source provenance only: this does not prove evaluation, rendering or edits.
"""
from dataclasses import replace

import conditional_footnote_binding as binding
import conditional_highlight as conditional
import document_footnote_evidence as footnotes
import document_theme_selector_evidence as theme
import facade_provenance
import import_graph as graph
from static_resolver import Binding, MATH_BLOCKS, MATH_BLOCKS_SOURCE_SHA256
from syntax_client import parse
from typst_lint import SafetyError, digest

_NAME = 'dots_space'
MAX_WORK = 32_000_000
MAX_OFFSETS = 16


class _Namespace(binding._NamespaceAnalyzer):
    def incoming(self, tree, index, path, active):
        result = super().incoming(tree, index, path, active)
        shape = graph.import_shape(tree, index)
        combined = conditional._combined_import(tree, index) if shape is None else None
        if combined is not None:
            shape = combined[0]
        if result.reason or shape is None or shape.request != MATH_BLOCKS:
            return result
        package = self.packages.get(MATH_BLOCKS)
        if package is None or digest(package.source) != MATH_BLOCKS_SOURCE_SHA256:
            return result
        names = ((_NAME, _NAME),) if shape.items is None else shape.items
        exports = dict(result.exports)
        for original, local in names:
            if original == _NAME and local in exports:
                exports[local] = conditional._Value((), _NAME)
        return replace(result, exports=tuple(sorted(exports.items())))



class SpacerBindingProof:
    @staticmethod
    def issue(evidence, path, source, offsets, *, max_work=MAX_WORK, require_inserted=False):
        """Reissue lexical provenance; optionally verify exact inserted math tokens.

        Work is conservatively reserved before resolver replay. The mandatory sealed
        capture integrity check itself has its existing fixed resource limits and is
        separate from this local budget. Elapsed time and execution are unproved.
        """
        resources = footnotes._valid(evidence)
        if (type(path) is not str or path not in evidence._session._graph.selected
                or type(source) is not bytes or type(offsets) is not tuple or not offsets
                or type(max_work) is not int or not 0 < max_work <= MAX_WORK
                or type(require_inserted) is not bool):
            raise SafetyError('Invalid spacer binding proof request')
        if len(offsets) > MAX_OFFSETS or len(offsets) > max_work:
            raise SafetyError('Spacer binding offset budget exceeded')
        if (any(type(x) is not int or not 0 <= x < len(source) for x in offsets)
                or len(offsets) != len(set(offsets))):
            raise SafetyError('Invalid spacer binding proof request')
        if len(source) > max_work:
            raise SafetyError('Spacer binding work budget exceeded')
        tree = parse(source)
        if tree.nodes[0].errors:
            raise SafetyError('Spacer candidate has invalid syntax')
        # Repeated closure ownership checks may inspect a complete tree. Namespace
        # engines have independent AST limits; reserve both before replay, plus
        # graph bytes and a conservative package/resource verification allowance.
        closures = sum(node.kind == 'Closure' for node in tree.nodes)
        calls = sum(node.kind == 'FuncCall' and source[node.start:node.start + 8] == b'footnote'
                    for node in tree.nodes)
        imports = sum(node.kind == 'ModuleImport' for node in tree.nodes)
        package_count = len(evidence._session._namespaces.packages)
        resource_count = len(evidence._snapshot.files)
        resource_path_bytes = sum(len(item.request.root.encode('utf-8')) + len(item.request.path.encode('utf-8'))
                                  for item in evidence._snapshot.files)
        graph_bytes = sum(len(item.source) for item in evidence._session._graph.sources)
        reserved = (2 * conditional.MAX_AST_WORK + 8 * len(tree.nodes) * (closures + 1)
                    + 2 * len(source) + 8 * graph_bytes
                    + 8 * (resource_count + resource_path_bytes) * max(1, package_count)
                      * (calls + imports + 2))
        if reserved > max_work:
            raise SafetyError('Spacer binding work budget exceeded')
        if require_inserted:
            anchored = set()
            for body in tree.nodes:
                if body.kind != 'Math':
                    continue
                siblings = body.children
                for i in range(len(siblings) - 4):
                    group = [tree.nodes[index] for index in siblings[i:i + 5]]
                    if (tuple(item.kind for item in group) == ('Hash', 'Ident', 'Space', 'Hash', 'FuncCall')
                            and source[group[0].start:group[3].start] == b'#dots_space '
                            and group[3].end == group[4].start
                            and source[group[3].start:group[4].end].startswith(b'#footnote[')):
                        anchored.add(group[3].start)
            if not set(offsets).issubset(anchored):
                raise SafetyError('Inserted spacer is not the exact math token immediately before the footnote')
        issued = footnotes.PreviewFootnoteEvidence.candidate_calls(evidence, path, source)
        if issued.source_sha256 != digest(source) or issued.unresolved:
            raise SafetyError('Footnote binding evidence is unresolved')
        if not set(offsets).issubset({call.start for call in issued.calls}):
            raise SafetyError('Footnote binding is unproven')
        math = theme._verify_selector(evidence, resources)
        session = evidence._session
        engine = _Namespace(session._graph, session._namespaces, (), False)
        engine.reason = facade_provenance._graph_reason(session._graph)
        engine.packages = {item.evidence.import_path: item for item in session._namespaces.packages}
        engine.evidence = session._namespaces.evidence
        wanted, found = set(offsets), set()

        class Observer(binding._FootnoteResolver):
            def import_node(self, index, state):
                before = state.lookup(_NAME)
                shape = graph.import_shape(self.tree, index)
                combined = conditional._combined_import(self.tree, index) if shape is None else None
                if combined is not None:
                    shape = combined[0]
                super().import_node(index, state)
                if shape is None:
                    return
                module = self.engine.incoming(self.tree, index, path, (path,))
                if module.reason or self.engine.exhausted:
                    state.bind(_NAME, None)
                    return
                exports = dict(module.exports)
                if _NAME in exports:
                    value = exports[_NAME]
                    # Wildcard relative facades are included in the footnote
                    # binding's staged-facade revalidation. Named relative routes
                    # are deliberately withheld until they have the same check.
                    if (not shape.request.startswith('@') and shape.items is not None):
                        state.bind(_NAME, None)
                    elif value is not None and value.canonical_name == _NAME:
                        for package in module.packages:
                            footnotes._verify_package(session, resources, package)
                        state.bind(_NAME, Binding('api', _NAME, MATH_BLOCKS,
                                                   math.source_identity, math.source_sha256))
                    else:
                        state.bind(_NAME, None)
                elif shape.items is None:
                    for package in module.packages:
                        footnotes._verify_package(session, resources, package)
                    state.bind(_NAME, before)

            def visit(self, index, state, *, output=False):
                node = self.nodes[index]
                if node.kind == 'Closure':
                    closure = self._literal_closure(index, state)
                    if closure is not None:
                        body, names, _, _ = closure
                        value = state.lookup(_NAME)
                        if (_NAME not in names and not state.deferred and value and value.kind == 'api'
                                and value.name == _NAME and value.package == MATH_BLOCKS
                                and value.source_sha256 == MATH_BLOCKS_SOURCE_SHA256):
                            stack, local, shadow = [body], set(), False
                            while stack:
                                i = stack.pop()
                                n = self.nodes[i]
                                if n.kind == 'FuncCall' and n.start > 0 and n.start - 1 in wanted:
                                    local.add(n.start - 1)
                                if n.kind == 'LetBinding':
                                    parts = self.sig(i)
                                    pending = [parts[1]] if len(parts) > 1 else []
                                    while pending:
                                        j = pending.pop()
                                        if (self.nodes[j].kind in ('Ident', 'MathIdent')
                                                and graph.raw(self.tree, j) == b'dots_space'):
                                            shadow = True
                                        pending.extend(self.nodes[j].children)
                                stack.extend(n.children)
                            if not shadow:
                                found.update(local)
                return super().visit(index, state, output=output)

        observer = Observer(tree, path, engine)
        observer.visit(0, observer.state)
        if found != wanted or engine.exhausted or observer.unresolved:
            raise SafetyError('Canonical spacer binding is not proved at every footnote')
        footnotes._valid(evidence)
        return dict(path=path, source_sha256=digest(source), offsets=sorted(found), reserved_work=reserved,
                    runtime_binding_proved=False, execution_proved=False, edit_permission=False,
                    compiler_permission=False, render_equivalence_proved=False)


def prove(evidence, path, source, offsets, *, max_work=MAX_WORK, require_inserted=False):
    return SpacerBindingProof.issue(evidence, path, source, offsets,
                                    max_work=max_work, require_inserted=require_inserted)
