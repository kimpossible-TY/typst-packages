"""Diagnostic-only local marker tags and bounded annotation-target checks.

All helper identities come from the scoped resolver and immutable reviewed
package snapshots. This module reads no package, cache or dependency itself.
Unknown display output defeats the negative assertion that bounds are missing.
"""
from dataclasses import dataclass

from scoped_provenance import resolve, PACKAGE, SOURCE_SHA256
from static_resolver import (_literal_string, MANNOT_NAMESPACE, is_mannot_binding,
                             has_mannot_namespace, is_cetz_line_binding)
from suppression import resolve as suppressions
from typst_lint import Diagnostic, load_rules

TAGS = 'core.scoped-marker-tags'
BOUNDS = 'core.annotation-marker-bounds'
RULE_IDS = (TAGS, BOUNDS)
CALLS = frozenset(('FuncCall', 'MathCall'))
TRIVIA = frozenset(('Space', 'Parbreak', 'LineComment', 'BlockComment'))
PUNCTUATION = frozenset(('LeftParen', 'RightParen', 'Comma', 'Hash'))


@dataclass(frozen=True)
class MarkerResult:
    diagnostics: tuple = ()
    edits: tuple = ()
    skips: tuple = ()
    parse_errors: tuple = ()
    suppressed: tuple = ()


def call_arguments(r, i):
    """Actual argument-expression children, including in hash-escaped math."""
    return [c for c in r.sig(i) if r.nodes[c].kind not in PUNCTUATION]


def _call(r, i):
    parts = r.sig(i)
    if (r.nodes[i].kind not in CALLS or len(parts) != 2
            or r.nodes[parts[1]].kind not in ('Args', 'MathArgs')):
        return None
    return parts[0], call_arguments(r, parts[1])


def _scope_field(binding, names):
    return bool(binding and binding.kind == 'scope-field' and binding.name in names
                and binding.package == PACKAGE and binding.source_sha256 == SOURCE_SHA256
                and binding.source_identity)


def _scope_invocation(r, callee):
    # A dictionary field's dot-call syntax is a separate invalid method call,
    # not execution of the stored function. Immutable function aliases and a
    # parenthesized dictionary field are the supported actual invocation forms.
    return r.nodes[callee].kind in ('Parenthesized', 'Ident')


def _raw_api(binding, name):
    return is_mannot_binding(binding, name)


def _literal_names(r, i):
    value = _literal_string(r.tree, i)
    if value is not None:
        return (value,)
    if r.nodes[i].kind != 'Array':
        return None
    values = [_literal_string(r.tree, c) for c in call_arguments(r, i)]
    return tuple(values) if all(value is not None for value in values) else None


def scoped_targets(r, i):
    """Return exact resolver-owned (scope, literal) targets, or None.

    This proves a target's identity, not that its label is emitted or its marker
    applied. ASCII unescaped literals are the intentionally bounded subset.
    """
    shape = _call(r, i)
    binding = r.facts[i].binding
    if shape is None or not _scope_field(binding, ('tag', 'tags')):
        return None
    callee, args = shape
    if not _scope_invocation(r, callee):
        return None
    if len(args) != 1:
        return None
    if binding.name == 'tag':
        name = _literal_string(r.tree, args[0])
        return ((binding.source_identity, name),) if name is not None else None
    if r.nodes[args[0]].kind != 'Array':
        return None
    names = _literal_names(r, args[0])
    return tuple((binding.source_identity, name) for name in names) if names is not None else None


def _named(r, i):
    if r.nodes[i].kind != 'Named':
        return None
    parts = [c for c in r.sig(i) if r.nodes[c].kind not in ('Colon', 'Hash')]
    if len(parts) != 2 or r.nodes[parts[0]].kind != 'Ident':
        return None
    return r.text(parts[0]), parts[1]


def _raw_mark(r, i):
    shape = _call(r, i)
    if shape is None or not _raw_api(r.facts[i].binding, 'mark'):
        return None
    _, args = shape
    positional = [a for a in args if r.nodes[a].kind != 'Named']
    named = [_named(r, a) for a in args if r.nodes[a].kind == 'Named']
    # Extra positional labels/colors, spreads and marker customizations are
    # intentionally not interpreted as the canonical body + named-tag form.
    if (len(positional) != 1 or len(named) != 1 or named[0] is None
            or named[0][0] != 'tag' or r.nodes[positional[0]].kind == 'Spread'):
        return None
    return positional[0], named[0][1]


def applied_marker(r, i):
    """Classify one actual marker application, not an unapplied factory.

    Output/display membership must be established by the caller. Immutable
    aliases retain the resolver-issued scope-marker value and never gain a
    target from their identifier spelling.
    """
    shape = _call(r, i)
    if shape is None:
        return None
    _, args = shape
    binding = r.facts[i].binding
    if (binding and binding.kind == 'scope-marker' and binding.package == PACKAGE
            and binding.source_sha256 == SOURCE_SHA256 and binding.source_identity
            and len(args) == 1 and r.nodes[args[0]].kind not in ('Named', 'Spread')):
        return binding.source_identity, binding.name
    mark = _raw_mark(r, i)
    if mark is not None:
        targets = scoped_targets(r, mark[1])
        if targets is not None and len(targets) == 1:
            return targets[0]
    return None


def overlay_targets(r, i):
    """Exact local overlay forms and generated raw-overlay target labels."""
    shape = _call(r, i)
    if shape is None:
        return None
    _, args = shape
    binding = r.facts[i].binding
    if (not args or any(r.nodes[a].kind in ('Named', 'Spread') for a in args)):
        return None
    if (_scope_field(binding, ('annot',)) and len(args) == 3
            and _scope_invocation(r, shape[0])):
        names = _literal_names(r, args[0])
        return tuple((binding.source_identity, name) for name in names) if names is not None else None
    local = bool(binding and binding.kind == 'api' and binding.package == PACKAGE
                 and binding.source_sha256 == SOURCE_SHA256 and binding.name == 'annot-cetz-local')
    if ((local or _raw_api(binding, 'annot-cetz')) and len(args) == 3
            or _raw_api(binding, 'annot') and len(args) == 2):
        return scoped_targets(r, args[0])
    return None


@dataclass(frozen=True)
class DisplayProof:
    # Events are ordered actual output applications. Duplicate markers are
    # retained so an anchor consumer cannot mistake a set for uniqueness.
    events: tuple = ()
    markers: tuple = ()
    complete: bool = False
    reasons: tuple = ()


_VALUE_LEAVES = frozenset(('Ident', 'MathIdent', 'Str', 'Int', 'Float', 'Numeric',
                           'Bool', 'None', 'Auto'))
_OUTPUT_ATOMS = frozenset(('MathText', 'Text', 'Str', 'Int', 'Float', 'Numeric',
                          'Escape', 'Shorthand', 'SmartQuote', 'MathShorthand'))
_OUTPUT_NEUTRAL = TRIVIA | frozenset(('Hash', 'Dollar', 'LeftParen', 'RightParen',
    'LeftBracket', 'RightBracket', 'Hat', 'Underscore', 'Slash', 'MathAlignPoint',
    'Linebreak', 'Comma', 'Prime'))
_OUTPUT_CONTAINERS = frozenset(('Equation', 'Math', 'MathAttach', 'MathFrac',
    'MathDelimited', 'MathPrimes', 'MathRoot', 'Markup', 'ContentBlock', 'Parenthesized'))


def side_effect_free(r, i):
    """Argument evaluation only, not a promise about later callback behavior.

    Reading an identifier does not execute a passed CeTZ implementation or
    drawable. The reviewed overlay checks target bounds before invoking them.
    Code containing unknown calls is never accepted merely because it is an
    argument rather than direct display output.
    """
    kind = r.nodes[i].kind
    if kind in _VALUE_LEAVES:
        return True
    if kind in ('FieldAccess', 'MathFieldAccess'):
        parts = r.sig(i)
        return (len(parts) == 3 and r.nodes[parts[1]].kind == 'Dot'
                and r.nodes[parts[2]].kind in ('Ident', 'MathIdent')
                and side_effect_free(r, parts[0]))
    if kind in ('Array', 'Parenthesized'):
        return all(side_effect_free(r, child) for child in call_arguments(r, i))
    if kind in CALLS:
        shape = _call(r, i)
        if shape is None:
            return False
        binding = r.facts[i].binding
        if _scope_field(binding, ('node', 'anchor')):
            return (_scope_invocation(r, shape[0]) and len(shape[1]) == 2
                    and all(_literal_string(r.tree, arg) is not None for arg in shape[1]))
        # The captured CeTZ shapes.line returns an inert drawing instruction.
        # Its actual definition identity, not a name like `line`, is required.
        return bool(is_cetz_line_binding(binding) and shape[1]
                    and all(side_effect_free(r, arg) for arg in shape[1]))
    if kind == 'Named':
        named = _named(r, i)
        return named is not None and side_effect_free(r, named[1])
    if kind == 'Dictionary':
        return all(r.nodes[c].kind in ('LeftParen', 'RightParen', 'Comma', 'Colon')
                   or side_effect_free(r, c) for c in r.sig(i))
    if kind == 'CodeBlock':
        parts = r.sig(i)
        return all(r.nodes[c].kind in ('LeftBrace', 'RightBrace')
                   or r.nodes[c].kind == 'Code' and all(
                       r.nodes[d].kind == 'Semicolon' or side_effect_free(r, d)
                       for d in r.sig(c)) for c in parts)
    if kind in _OUTPUT_ATOMS or kind in _OUTPUT_NEUTRAL:
        return True
    if kind in _OUTPUT_CONTAINERS:
        return all(side_effect_free(r, child) for child in r.nodes[i].children)
    return False


def _literal_output(r, i):
    """A literal value that cannot hide executable content during measure()."""
    kind = r.nodes[i].kind
    if kind in _OUTPUT_ATOMS or kind in _OUTPUT_NEUTRAL:
        return True
    return (kind in _OUTPUT_CONTAINERS
            and all(_literal_output(r, child) for child in r.nodes[i].children))


def display_proof(r, display):
    """Strict source-order proof for one display, independent of its owner.

    Only reviewed target and marker calls plus literal, marker-free output are
    classified. A caller must separately establish direct callback/display
    membership and the semantic namespace/core-annot prerequisites.
    """
    events, markers, reasons = [], [], []

    def unknown(i, reason):
        reasons.append(f'{reason} at byte {r.nodes[i].start}')
        return 'unknown'

    def scan(i, preceding_atom=False):
        node = r.nodes[i]
        if i != display and node.kind == 'Equation' and node.display:
            return unknown(i, 'Nested display output has a separate annotation boundary')
        if node.kind in _OUTPUT_ATOMS:
            return 'atom'
        if node.kind in _OUTPUT_NEUTRAL:
            return 'neutral'
        if node.kind in CALLS:
            target = applied_marker(r, i)
            if target is not None:
                raw = _raw_mark(r, i)
                body = raw[0] if raw is not None else _call(r, i)[1][0]
                scan(body)
                events.append(('marker', target, i))
                markers.append(target)
                return 'producer'
            targets = scoped_targets(r, i)
            if targets is not None:
                binding = r.facts[i].binding
                if binding.name != 'tag' or len(targets) != 1 or not preceding_atom:
                    return unknown(i, 'Plain target attachment is not proven on a marker-free math atom')
                events.append(('plain', targets[0], i))
                return 'producer'
            targets = overlay_targets(r, i)
            if targets is not None:
                args = _call(r, i)[1]
                # Raw annot measures its annotation content before looking up
                # target bounds. An inert identifier may hide executable
                # content there, unlike the later-invoked CeTZ drawer inputs.
                if (_raw_api(r.facts[i].binding, 'annot')
                        and not _literal_output(r, args[1])):
                    return unknown(i, 'Raw annotation content may execute before target bounds are read')
                if not all(side_effect_free(r, arg) for arg in args[1:]):
                    return unknown(i, 'Overlay argument evaluation may produce unknown marker output')
                for target in targets:
                    events.append(('overlay', target, i))
                return 'producer'
            return unknown(i, 'Display call output or target production is unknown')
        if node.kind in _OUTPUT_CONTAINERS:
            previous = 'atom' if preceding_atom else 'neutral'
            pure = True
            for child in node.children:
                result = scan(child, previous == 'atom')
                if result != 'neutral':
                    previous = result
                    pure = pure and result == 'atom'
            if pure and previous == 'atom':
                return 'atom'
            return 'producer' if not pure and previous != 'unknown' else previous
        return unknown(i, 'Display output may contain an unclassified marker')

    if r.nodes[display].kind != 'Equation' or not r.nodes[display].display or r.nodes[display].errors:
        return DisplayProof(reasons=('A valid display Equation is required',))
    scan(display)
    return DisplayProof(tuple(events), tuple(markers), not reasons, tuple(dict.fromkeys(reasons)))


def analyze(tree, path, *, packages=(), import_graph=None):
    packages = tuple(packages)
    r = resolve(tree, packages, import_graph=import_graph, source_path=path)
    catalog = {rule['id']: rule for rule in load_rules()[0]['rules']}
    scopes = suppressions(tree, set(catalog))
    diagnostics = {rid: [] for rid in RULE_IDS}
    suppressed = {rid: [] for rid in RULE_IDS}
    skips = {rid: list(r.skips) for rid in RULE_IDS}
    parents = {child: i for i, node in enumerate(tree.nodes) for child in node.children}
    bodies = {body: call for call, body in r.callback_bodies.items()}

    def add(rid, i, message):
        start = tree.nodes[i].start
        rule = catalog[rid]
        finding = Diagnostic(rid, rule['grade'], path, tree.source[:start].count(b'\n') + 1,
                             len(tree.source[tree.source.rfind(b'\n', 0, start)+1:start].decode()) + 1,
                             message, rule['source'])
        (suppressed if any(s.covers(rid, start) for s in scopes) else diagnostics)[rid].append(finding)

    def note(rid, i, message):
        skips[rid].append(message if i is None else f'{message} at byte {tree.nodes[i].start}')

    def annotation_scope(i):
        while i in parents:
            i = parents[i]
            if i in bodies:
                call = bodies[i]
                binding = r.facts[call].binding
                return r.scope_calls[call] if binding.name != 'local-tag-scope' else None
        return None

    for i, node in enumerate(tree.nodes):
        if node.kind not in CALLS:
            continue
        mark = _raw_mark(r, i)
        if mark is not None and annotation_scope(i) is not None and tree.nodes[mark[1]].kind == 'Label':
            add(TAGS, i, 'Use a scope-generated tag for this raw mannot marker label')

    # The sound missing-bounds collector is intentionally separate from target
    # identity classification and requires actual captured helper definitions.
    _analyze_bounds(r, packages, annotation_scope, add, note)
    errors = tuple((node.start, node.end) for node in tree.nodes if node.kind == 'Error')
    for rid in RULE_IDS:
        skips[rid].append('Partial: direct reviewed annotation callbacks and literal targets only; no facade, runtime evaluation or graph-wide bounds inference')
    return {rid: MarkerResult(tuple(diagnostics[rid]), (), tuple(dict.fromkeys(skips[rid])),
                               errors, tuple(suppressed[rid])) for rid in RULE_IDS}


def scope_ownership(r, *, include_tag_scopes=False):
    """Return direct-output display owners and known colliding scope IDs.

    This is a source-graph boundary, not runtime scope evaluation. Stored
    content, arbitrary wrappers and styles never gain ownership from ancestry.
    """
    parents = {child: i for i, node in enumerate(r.nodes) for child in node.children}
    bodies = {body: call for call, body in r.callback_bodies.items()}
    scope_calls = {identity: call for call, identity in r.scope_calls.items()}

    def annotation_scope(i):
        while i in parents:
            i = parents[i]
            if i in bodies:
                call = bodies[i]
                binding = r.facts[call].binding
                return r.scope_calls[call] if include_tag_scopes or binding.name != 'local-tag-scope' else None
        return None

    def returned_body(content, scope_body):
        parent = parents.get(content)
        if parent is None or r.nodes[parent].kind != 'Code':
            return False
        block = parents.get(parent)
        parts = r.sig(parent)
        # One literal returned content block, optionally following immutable
        # lets. Arbitrary wrappers, stored content and code return paths remain
        # opaque even when their child equations happen to look identical.
        return (block == scope_body and r.nodes[block].kind == 'CodeBlock'
                and bool(parts) and parts[-1] == content
                and all(r.nodes[c].kind in ('LetBinding', 'Semicolon') for c in parts[:-1]))

    def direct_content(i, body):
        parent = parents.get(i)
        if parent is None or r.nodes[parent].kind != 'Markup':
            return False
        content = parents.get(parent)
        return (content is not None and r.nodes[content].kind == 'ContentBlock'
                and (content == body or returned_body(content, body)))

    def owned_scope(call):
        parent = parents.get(call)
        if parent == 0:
            return r.tree.mode == 'markup' and r.nodes[0].kind == 'Markup'
        cursor = call
        while cursor in parents:
            cursor = parents[cursor]
            if cursor in bodies:
                outer = bodies[cursor]
                return direct_content(call, cursor) and owned_scope(outer)
        return False

    collisions, prefixes = set(), {}
    for call, identity in r.scope_calls.items():
        shape = _call(r, call)
        named = dict(item for arg in shape[1] if (item := _named(r, arg)) is not None)
        key = None
        if 'prefix' in named:
            prefix = _literal_string(r.tree, named['prefix'])
            if prefix is not None:
                key = ('prefix', prefix)
        if key is None and 'parent' in named and 'name' in named:
            parent = r.facts[named['parent']].binding
            name = _literal_string(r.tree, named['name'])
            if (parent and parent.kind in ('tag-scope', 'annotation-scope')
                    and name is not None and ('prefix' not in named or r.nodes[named['prefix']].kind == 'Auto')):
                key = ('child', parent.source_identity, name)
        if key is not None:
            if key in prefixes:
                collisions.update((identity, prefixes[key]))
            else:
                prefixes[key] = identity

    owners = {}
    for display, node in enumerate(r.nodes):
        if node.kind != 'Equation' or not node.display:
            continue
        owner = annotation_scope(display)
        if owner is None:
            continue
        call = scope_calls.get(owner)
        body = r.callback_bodies.get(call)
        if call is not None and direct_content(display, body) and owned_scope(call):
            owners[display] = owner
    if any(node.kind in ('SetRule', 'ShowRule') for node in r.nodes):
        owners.clear()
    return owners, frozenset(collisions)


def _analyze_bounds(r, packages, annotation_scope, add, note):
    if not (has_mannot_namespace(packages)
            and _raw_api(r.package_binding(MANNOT_NAMESPACE, 'core-annot'), 'core-annot')):
        note(BOUNDS, None, 'Bounds require authentic mannot namespace and core-annot definition artifacts')
        return
    # Unknown show/set rules can change the emitted content of a literal atom.
    # Do not infer their effect, lifetime or applicability from source spelling.
    if any(node.kind in ('SetRule', 'ShowRule') for node in r.nodes):
        note(BOUNDS, None, 'Bounds refused: output styles may replace literal math atoms')
        return
    owners, collisions = scope_ownership(r)
    for display, node in enumerate(r.nodes):
        if node.kind != 'Equation' or not node.display:
            continue
        owner = annotation_scope(display)
        if owner is None:
            continue
        if owners.get(display) != owner:
            note(BOUNDS, display, 'Bounds refused: direct callback display output ownership is unproven')
            continue
        proof = display_proof(r, display)
        if not proof.complete:
            for reason in proof.reasons:
                note(BOUNDS, display, f'Bounds refused: {reason}')
            continue
        if any(target[0] != owner for _, target, _ in proof.events):
            note(BOUNDS, display, 'Bounds refused: target belongs to a different scope')
            continue
        if owner in collisions or any(target[0] in collisions for _, target, _ in proof.events):
            note(BOUNDS, display, 'Bounds refused: explicit scope namespace collision')
            continue
        nearest, reported = {}, set()
        for kind, target, i in proof.events:
            if kind == 'overlay':
                if nearest.get(target) == 'plain' and (i, target) not in reported:
                    add(BOUNDS, i, f'Nearest preceding plain scoped tag for "{target[1]}" supplies no annotation bounds')
                    reported.add((i, target))
            else:
                nearest[target] = kind
