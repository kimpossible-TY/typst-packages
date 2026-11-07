"""Bounded anchor repairs and diagnostic-only raw local-overlay use.

Consumes actual ASTs and loader-verified package bytes. The shared resolver owns
all callable/scope identity; neither a namespace's spelling nor a caller's set of
marker names is accepted as evidence. See ANCHOR_OVERLAY_CONTRACT.md.
"""
from dataclasses import dataclass

from edit_plan import Edit
from scoped_provenance import resolve, arguments, PACKAGE, SOURCE_SHA256
from static_resolver import _literal_string
from suppression import resolve as suppressions
from typst_lint import Diagnostic, load_rules

ANCHOR = 'core.scoped-anchor-access'
LOCAL = 'core.local-overlay-api'
RULE_IDS = (ANCHOR, LOCAL)

# Reviewed in exact CeTZ 0.4.2 src/anchor.typ, not guessed from string suffixes.
DIRECTIONS = frozenset(('east', 'north-east', 'north', 'north-west', 'west',
                        'south-west', 'south', 'south-east'))
PARTIAL = ('Partial: direct reviewed local scope callbacks and literal scoped targets only; '
           'anchor repairs require same-display applied markers and direct verified CeTZ line '
           'coordinates in a literal drawable block; no opaque drawing or rendering proof')


@dataclass(frozen=True)
class AnchorOverlayResult:
    diagnostics: tuple = ()
    edits: tuple = ()
    skips: tuple = ()
    parse_errors: tuple = ()
    suppressed: tuple = ()


def _scope_field(binding, name):
    return bool(binding and binding.kind == 'scope-field' and binding.name == name
                and binding.package == PACKAGE and binding.source_sha256 == SOURCE_SHA256)


def _parents(r):
    return {child: i for i, node in enumerate(r.nodes) for child in node.children}


def _display(r, parents, i):
    while i in parents:
        i = parents[i]
        if r.nodes[i].kind == 'Equation':
            return i if r.nodes[i].display else None
    return None


def _direct_display_output(r, parents, i, display):
    """Only a direct expression in this exact display's Math sequence.

    Content held by a let-binding, passed to arbitrary wrappers, nested in a
    second equation, or produced by an ordinary closure is not output evidence.
    """
    parent = parents.get(i)
    return (parent is not None and r.nodes[parent].kind == 'Math'
            and parents.get(parent) == display)


def _local_scope(r, parents, i):
    callbacks = {body: call for call, body in r.callback_bodies.items()}
    while i in parents:
        i = parents[i]
        if i in callbacks:
            return r.scope_calls[callbacks[i]]
        if r.nodes[i].kind in ('Closure', 'Contextual'):
            return None
    return None


def _call_args(r, i):
    parts = r.sig(i)
    if (r.nodes[i].kind != 'FuncCall' or len(parts) != 2
            or r.nodes[parts[1]].kind != 'Args'):
        return None
    args = arguments(r, parts[1])
    if any(r.nodes[arg].kind in ('Named', 'Spread') for arg in args):
        return None
    return args


def _literal_names(r, i):
    if r.nodes[i].kind == 'Str':
        name = _literal_string(r.tree, i)
        return (name,) if name is not None else None
    if r.nodes[i].kind == 'Array':
        items = [c for c in r.sig(i) if r.nodes[c].kind not in
                 ('LeftParen', 'RightParen', 'Comma')]
        values = tuple(_literal_string(r.tree, c) for c in items)
        return values if values and all(v is not None for v in values) else None
    return None


def _field_receiver(r, call, expected, scope):
    """Recover an actually available receiver, never a nearest variable name."""
    parts = r.sig(call)
    if len(parts) != 2:
        return None
    callee = parts[0]
    if r.nodes[callee].kind == 'Parenthesized':
        inner = [c for c in r.sig(callee) if r.nodes[c].kind not in
                 ('LeftParen', 'RightParen')]
        if len(inner) != 1:
            return None
        callee = inner[0]
    fields = r.sig(callee)
    if (r.nodes[callee].kind != 'FieldAccess' or len(fields) != 3
            or r.nodes[fields[0]].kind != 'Ident'
            or r.nodes[fields[1]].kind != 'Dot'
            or r.nodes[fields[2]].kind != 'Ident'
            or r.text(fields[2]) != expected):
        return None
    binding = r.facts[fields[0]].binding
    if not (binding and binding.kind in ('annotation-scope', 'tag-scope')
            and binding.package == PACKAGE and binding.source_sha256 == SOURCE_SHA256
            and binding.source_identity == scope
            and _scope_field(r.facts[callee].binding, expected)):
        return None
    return fields[0], binding.kind



def _prefix_safe(r, scope, seen=frozenset()):
    """Prove the generated node-name prefix has no CeTZ separator dots.

    The reviewed helper concatenates strings, while CeTZ splits a coordinate at
    its first dot. A scope identity with a dynamic/dotted prefix is therefore
    not sufficient to certify an anchor edit, even for a simple marker name.
    """
    if scope in seen:
        return False
    call = next((i for i, identity in r.scope_calls.items() if identity == scope), None)
    if call is None:
        return False
    binding = r.facts[call].binding
    if not (binding and binding.kind == 'api' and binding.package == PACKAGE
            and binding.source_sha256 == SOURCE_SHA256):
        return False
    parts = r.sig(call)
    named = {r.text(r.sig(arg)[0]): r.sig(arg)[-1]
             for arg in arguments(r, parts[1]) if r.nodes[arg].kind == 'Named'}
    prefix = named.get('prefix')
    if prefix is not None and r.nodes[prefix].kind != 'Auto':
        value = _literal_string(r.tree, prefix)
        return value is not None and '.' not in value
    parent = named.get('parent')
    name = named.get('name')
    has_parent = parent is not None and r.nodes[parent].kind != 'Auto'
    has_name = name is not None and r.nodes[name].kind != 'Auto'
    parent_safe = False
    if has_parent:
        owner = r.facts[parent].binding
        parent_safe = bool(owner and owner.kind in ('tag-scope', 'annotation-scope')
                           and owner.package == PACKAGE and owner.source_sha256 == SOURCE_SHA256
                           and _prefix_safe(r, owner.source_identity, seen | {scope}))
        if not parent_safe:
            return False
    if has_parent and has_name:
        value = _literal_string(r.tree, name)
        return value is not None and '.' not in value
    default = {'local-tag-scope': 'local-scope',
               'local-scope-annotations': 'local-scope-annotations',
               'mannot-scope': 'mannot-scope'}.get(binding.name)
    namespace = _literal_string(r.tree, named['namespace']) if 'namespace' in named else default
    return namespace is not None and '.' not in namespace


def _drawing(r, i, is_line):
    """Exact literal drawable: direct resolved line calls, no opaque geometry.

    Unknown calls can introduce ordinary nodes with the same name, change the
    coordinate resolver or alter drawing context. Do not infer their effect.
    """
    if r.nodes[i].kind != 'CodeBlock':
        return None, 'drawable is not a literal code block'
    contents = [c for c in r.sig(i) if r.nodes[c].kind not in ('LeftBrace', 'RightBrace')]
    if len(contents) != 1 or r.nodes[contents[0]].kind != 'Code':
        return None, 'drawable has no single direct code sequence'
    statements = [c for c in r.sig(contents[0]) if r.nodes[c].kind != 'Semicolon']
    calls = []
    for call in statements:
        if r.nodes[call].kind != 'FuncCall' or not is_line(r.facts[call].binding):
            return None, 'drawable contains an opaque or non-line drawing operation'
        parts = r.sig(call)
        if len(parts) != 2 or r.nodes[parts[1]].kind != 'Args':
            return None, 'line arguments have no reviewed coordinate shape'
        args = arguments(r, parts[1])
        if any(r.nodes[arg].kind == 'Spread' for arg in args):
            return None, 'spread line arguments have unknown coordinate roles and node names'
        positional = [arg for arg in args if r.nodes[arg].kind != 'Named']
        if len(positional) < 2:
            return None, 'line requires at least two positional coordinates'
        named = [arg for arg in args if r.nodes[arg].kind == 'Named']
        keys = [r.text(r.sig(arg)[0]) for arg in named]
        if len(keys) != len(set(keys)):
            return None, 'duplicate named line arguments are unsupported'
        if not set(keys) <= {'name', 'close', 'stroke', 'fill'}:
            return None, 'line styles with unreviewed drawing callbacks or geometry effects are unsupported'
        names = []
        for arg, key in zip(named, keys):
            if key == 'name':
                value = r.sig(arg)[-1]
                if r.nodes[value].kind == 'None':
                    continue
                name = _literal_string(r.tree, value)
                if name is None:
                    return None, 'dynamic ordinary drawing-node name may collide with a local marker'
                names.append(name)
        # Nested helper calls are accepted only for proven scope anchors/nodes.
        # An arbitrary call even in a style argument may mutate drawing state.
        pending = list(args)
        while pending:
            child = pending.pop()
            if r.nodes[child].kind in ('FuncCall', 'MathCall'):
                binding = r.facts[child].binding
                if not (_scope_field(binding, 'node') or _scope_field(binding, 'anchor')):
                    return None, 'nested opaque call makes drawing ownership unproven'
            if r.nodes[child].kind in ('Closure', 'Contextual', 'LetBinding', 'ModuleImport',
                                      'ModuleInclude', 'Conditional', 'ForLoop', 'WhileLoop',
                                      'CodeBlock', 'ContentBlock'):
                return None, 'nested dynamic or stored drawing expression is unsupported'
            pending.extend(r.nodes[child].children)
        calls.append((call, tuple(positional), tuple(names)))
    return tuple(calls), None


def analyze(tree, path, *, packages=()):
    # Shared predicates certify exact authentic raw artifacts, including the
    # namespace chain. Imports are deliberately late to avoid circular loading.
    from static_resolver import is_mannot_binding, is_cetz_line_binding, is_cetz_module_binding
    from marker_bounds_rules import scoped_targets, display_proof, scope_ownership

    r = resolve(tree, tuple(packages))
    parents = _parents(r)
    catalog = {rule['id']: rule for rule in load_rules()[0]['rules']}
    directives = suppressions(tree, set(catalog))
    diagnostics, edits, suppressed = ({rid: [] for rid in RULE_IDS} for _ in range(3))

    def add(rid, i, message, proposals=()):
        node = r.nodes[i]
        rule = catalog[rid]
        finding = Diagnostic(rid, rule['grade'], path, tree.source[:node.start].count(b'\n') + 1,
                             len(tree.source[tree.source.rfind(b'\n', 0, node.start)+1:node.start].decode()) + 1,
                             message, rule['source'])
        if any(d.covers(rid, node.start) for d in directives):
            suppressed[rid].append(finding)
            return False
        diagnostics[rid].append(finding)
        edits[rid].extend(proposals)
        return True

    displays = {}
    owned_displays, collisions = scope_ownership(r, include_tag_scopes=True)
    styles_unknown = any(node.kind in ('SetRule', 'ShowRule') for node in r.nodes)

    for i, node in enumerate(r.nodes):
        if node.kind != 'FuncCall':
            continue
        binding = r.facts[i].binding
        local = _local_scope(r, parents, i)
        if is_mannot_binding(binding, 'annot-cetz') and local is not None:
            add(LOCAL, i, 'Use annot-cetz-local or the scope annot helper instead of raw annot-cetz in a local scope')
        scoped = _scope_field(binding, 'annot')
        localized = bool(binding and binding.kind == 'api' and binding.package == PACKAGE
                         and binding.source_sha256 == SOURCE_SHA256 and binding.name == 'annot-cetz-local')
        if not (scoped or localized):
            continue
        args = _call_args(r, i)
        if args is None or len(args) != 3:
            r.note(i, f'{ANCHOR} skipped: overlay requires exactly three positional arguments')
            continue
        display = _display(r, parents, i)
        if display is None or not _direct_display_output(r, parents, i, display):
            r.note(i, f'{ANCHOR} skipped: overlay is not direct output in one display')
            continue
        if not is_cetz_module_binding(r.facts[args[1]].binding):
            r.note(i, f'{ANCHOR} skipped: overlay CeTZ module is not the reviewed coordinate backend')
            continue
        receiver = None
        if scoped:
            names = _literal_names(r, args[0])
            targets = tuple((binding.source_identity, name) for name in names) if names is not None else None
            receiver = _field_receiver(r, i, 'annot', binding.source_identity)
        else:
            targets = scoped_targets(r, args[0])
            target_call = args[0]
            if targets and r.nodes[target_call].kind == 'FuncCall':
                field = r.facts[target_call].binding
                if field and field.name in ('tag', 'tags'):
                    receiver = _field_receiver(r, target_call, field.name, targets[0][0])
        if not targets or len({scope for scope, name in targets}) != 1:
            r.note(i, f'{ANCHOR} skipped: overlay target names and unique resolved scope are unproven')
            continue
        if len(targets) != len(set(targets)):
            r.note(i, f'{ANCHOR} skipped: duplicate overlay targets do not establish unique drawing-node ownership')
            continue
        target_scope = targets[0][0]
        if target_scope in collisions:
            r.note(i, f'{ANCHOR} skipped: explicit local scope namespaces collide')
            continue
        if display not in owned_displays:
            r.note(i, f'{ANCHOR} skipped: display output is not owned by a direct local scope callback')
            continue
        if owned_displays[display] != target_scope:
            r.note(i, f'{ANCHOR} skipped: overlay target belongs to a different scope than the display')
            continue
        if styles_unknown:
            r.note(i, f'{ANCHOR} skipped: output styles may replace or duplicate marker content')
            continue
        if display not in displays:
            displays[display] = display_proof(r, display)
        proof = displays[display]
        if not proof.complete:
            r.note(i, f'{ANCHOR} skipped: display marker ownership is incomplete')
            continue
        if any(target[0] != target_scope for kind, target, event in proof.events):
            r.note(i, f'{ANCHOR} skipped: display contains generated targets from a different scope')
            continue
        plain = [target for kind, target, event in proof.events if kind == 'plain']
        owned = proof.markers
        preceding = [target for kind, target, event in proof.events
                     if kind == 'marker' and r.nodes[event].start < node.start]
        proven = {name for scope, name in targets
                  if owned.count((scope, name)) == 1 and (scope, name) in preceding
                  and (scope, name) not in plain}
        if not proven:
            r.note(i, f'{ANCHOR} skipped: no unique preceding same-display applied marker for the overlay targets')
            continue
        drawing, refusal = _drawing(r, args[2], is_cetz_line_binding)
        if drawing is None:
            r.note(i, f'{ANCHOR} skipped: {refusal}')
            continue
        ordinary_names = {name for call, points, names in drawing for name in names}
        for call, points, names in drawing:
            for point in points:
                raw = _literal_string(tree, point)
                if raw is None:
                    continue
                matches = [(name, direction) for name in proven for direction in DIRECTIONS
                           if raw == name + '.' + direction]
                if len(matches) != 1:
                    continue
                name, direction = matches[0]
                if name in ordinary_names or raw in ordinary_names:
                    r.note(point, f'{ANCHOR} skipped: ordinary drawing-node name collides with the marker anchor')
                    continue
                proposals = ()
                reason = None
                if receiver is None:
                    reason = 'the corresponding scope node/anchor receiver is unavailable through this alias'
                elif '.' in name:
                    reason = 'dotted marker names lack a reviewed CeTZ string-to-node disambiguation contract'
                elif not _prefix_safe(r, target_scope):
                    reason = 'generated scope prefix is dynamic, dotted or lacks a reviewed string-to-node proof'
                else:
                    receiver_id, kind = receiver
                    node_name = b'node' if kind == 'annotation-scope' else b'anchor'
                    receiver_node = r.nodes[receiver_id]
                    actual = tree.source[receiver_node.start:receiver_node.end]
                    # The original Str has already been proved plain ASCII with
                    # exact marker/direction bytes. Replace only that Str span.
                    original = tree.source[r.nodes[point].start:r.nodes[point].end]
                    marker_bytes = original[1:1+len(name)]
                    direction_bytes = original[2+len(name):-1]
                    replacement = (b'(' + actual + b'.' + node_name + b')("' + marker_bytes
                                   + b'", "' + direction_bytes + b'")')
                    proposals = (Edit(r.nodes[point].start, r.nodes[point].end, replacement),)
                active = add(ANCHOR, point, 'Use the corresponding scope node or anchor helper for a known local marker anchor', proposals)
                if active and reason:
                    r.note(point, f'{ANCHOR} repair refused: {reason}')
    skips = tuple(dict.fromkeys((*r.skips, PARTIAL)))
    errors = tuple((n.start, n.end) for n in tree.nodes if n.kind == 'Error')
    return {rid: AnchorOverlayResult(tuple(diagnostics[rid]), tuple(edits[rid]), skips, errors,
                                     tuple(suppressed[rid])) for rid in RULE_IDS}
