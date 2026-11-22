"""Bounded direct callback proofs for reviewed scoped-annotations 0.3.0.

No filesystem/evaluation occurs here. The caller supplies loader-verified bytes.
Raw mannot reexports and scoped-package facade imports are not certified. An
optional captured text-utils facade context preserves surrounding prose facts,
not scope-helper initializer authority. Scope callback execution is recognized
only for one literal, single-parameter closure.
Nested arbitrary closures, context expressions, mutation and includes stay opaque.
"""
from static_resolver import (_Resolver, Binding, _literal_string, is_mannot_binding, MANNOT_NAMESPACE,
                             SCOPED_ANNOTATIONS as PACKAGE,
                             SCOPED_ANNOTATIONS_SOURCE_SHA256 as SOURCE_SHA256)

FIELDS = frozenset(('tag', 'tags', 'name', 'names', 'anchor', 'ref', 'node', 'annot', 'mark'))
SCOPES = frozenset(('local-tag-scope', 'local-scope-annotations', 'mannot-scope'))


def arguments(resolver, i):
    return [c for c in resolver.sig(i) if resolver.nodes[c].kind not in
            ('LeftParen', 'RightParen', 'Comma')]


class ScopedResolver(_Resolver):
    def __init__(self, tree, packages, *, import_graph=None, source_path=None):
        super().__init__(tree, None, packages, import_graph, source_path)
        self.scope_calls = {}
        self.callback_bodies = {}
        self.state.bind('ref', Binding('builtin', 'ref'))

    def import_node(self, i, state):
        parts = self.sig(i)
        if (len(parts) >= 2 and _literal_string(self.tree, parts[1]) == PACKAGE
                and any(self.nodes[c].kind == 'Star' for c in parts)):
            state.unknown_wildcard()
            self.note(i, 'Scoped package wildcard includes unreviewed mannot reexports')
            return
        super().import_node(i, state)

    def visit(self, i, state, *, output=False):
        node, parts = self.nodes[i], self.sig(i)
        if node.kind == 'FieldAccess' and len(parts) == 3:
            receiver = self.visit(parts[0], state)
            field = self.text(parts[2])
            if receiver and receiver.kind in ('tag-scope', 'annotation-scope'):
                available = FIELDS if receiver.kind == 'annotation-scope' else FIELDS - {'node', 'annot', 'mark'}
                if field in available:
                    binding = Binding('scope-field', field, PACKAGE, receiver.source_identity, SOURCE_SHA256)
                    self.record(i, state, binding)
                    return binding
        if node.kind == 'FuncCall' and len(parts) == 2:
            callee = self.visit(parts[0], state)
            if (callee and callee.kind == 'scope-field' and callee.name == 'mark'
                    and not state.deferred and is_mannot_binding(self.package_binding(MANNOT_NAMESPACE, 'mark'), 'mark')):
                args = arguments(self, parts[1])
                target = _literal_string(self.tree, args[0]) if len(args) == 1 else None
                # Direct dictionary methods are not callable with Typst dot-call
                # syntax; a parenthesized field or immutable alias is required.
                if target is not None and self.nodes[parts[0]].kind in ('Parenthesized', 'Ident'):
                    self.record(i, state, callee)
                    self.visit(parts[1], state)
                    return Binding('scope-marker', target, PACKAGE, callee.source_identity, SOURCE_SHA256)
            if callee and callee.kind == 'api' and callee.package == PACKAGE and callee.name in SCOPES and not state.deferred:
                args = arguments(self, parts[1])
                positional = [c for c in args if self.nodes[c].kind != 'Named']
                named = [self.text(self.sig(c)[0]) for c in args if self.nodes[c].kind == 'Named']
                allowed = {'prefix', 'namespace'} if callee.name == 'local-tag-scope' else {'prefix', 'parent', 'name', 'namespace'}
                if callee.name == 'mannot-scope':
                    allowed.discard('namespace')
                if (len(positional) == 1 and self.nodes[positional[0]].kind == 'Closure'
                        and len(named) == len(set(named)) and set(named) <= allowed):
                    closure = self.sig(positional[0])
                    params = self.sig(closure[0]) if closure else []
                    if (len(closure) == 3 and self.nodes[closure[0]].kind == 'Params'
                            and len(params) == 1 and self.nodes[params[0]].kind == 'Ident'
                            and self.nodes[closure[1]].kind == 'Arrow'):
                        self.record(i, state, callee)
                        for arg in args:
                            if arg != positional[0]:
                                self.visit(arg, state)
                        identity = f'scope:{node.start}'
                        self.scope_calls[i] = identity
                        self.callback_bodies[i] = closure[2]
                        nested = state.nested()
                        nested.bind(self.text(params[0]), Binding(
                            'tag-scope' if callee.name == 'local-tag-scope' else 'annotation-scope',
                            callee.name, PACKAGE, identity, SOURCE_SHA256))
                        self.visit(closure[2], nested)
                        return None
                self.note(i, 'Unsupported scope callback or arguments')
        return super().visit(i, state, output=output)


def resolve(tree, packages=(), *, import_graph=None, source_path=None):
    resolver = ScopedResolver(tree, tuple(packages), import_graph=import_graph, source_path=source_path)
    # Mutation may change captures before deferred contextual scope execution.
    # Do not infer call order or chase include effects in this bounded backend.
    unsafe = {'ModuleInclude', 'DestructAssignment', 'Conditional', 'ForLoop', 'WhileLoop'}
    if tree.nodes[0].errors or any(n.kind in unsafe or (n.kind == 'Binary' and any(
            tree.nodes[c].kind in ('Eq', 'PlusEq', 'HyphEq', 'StarEq', 'SlashEq') for c in n.children))
            for n in tree.nodes):
        resolver.skips.append('Malformed syntax, mutation or dynamic execution: scope proofs withheld')
    else:
        resolver.visit(0, resolver.state)
    return resolver
