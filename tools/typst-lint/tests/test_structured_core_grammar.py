"""Explicit structured grammar admission from exact pinned parser trees."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import closed_core as core
import source_profiles as profiles
from edit_plan import plan
from script_rule import analyze
from syntax_client import EXECUTABLE, MAX_DEPTH, Node, Tree, parse

V1 = profiles.CLOSED_CORE_SOURCE_PROFILE
V2 = profiles.STRUCTURED_CORE_SOURCE_PROFILE


def tree_with(tree, index, **changes):
    nodes = list(tree.nodes)
    nodes[index] = replace(nodes[index], **changes)
    return replace(tree, nodes=tuple(nodes))


def shaped(spec):
    """Construct exact-span synthetic trees to challenge grammar, not provenance."""
    nodes, chunks = [], []
    position = 0

    def add(item):
        nonlocal position
        kind, content = item
        index, start = len(nodes), position
        nodes.append(None)
        if type(content) is str:
            raw = content.encode()
            chunks.append(raw)
            position += len(raw)
            children = ()
        else:
            children = tuple(add(child) for child in content)
        nodes[index] = Node(kind, start, position, children, False, (), None)
        return index

    add(spec)
    return Tree(b''.join(chunks), 'markup', tuple(nodes))


def clone_proof(proof, **changes):
    value = object.__new__(core.CoverageProof)
    for field in core.CoverageProof.__dataclass_fields__:
        object.__setattr__(value, field, changes.get(field, getattr(proof, field)))
    return value


class SourceProfileBoundaryTests(unittest.TestCase):
    def minimal(self):
        return shaped(('Markup', [('Text', 'Plain prose.')]))

    def test_exact_versioned_mapping_is_pure_and_immutable(self):
        self.assertEqual(dict(profiles.GRAMMAR_VERSIONS), {
            V1: 'closed-core-markup-v1', V2: 'closed-core-markup-v2'})
        self.assertEqual(profiles.KNOWN_SOURCE_PROFILES, frozenset((V1, V2)))
        with patch('builtins.open', side_effect=AssertionError('Filesystem read')):
            for profile, grammar in profiles.GRAMMAR_VERSIONS.items():
                self.assertEqual(profiles.validate_source_profile(profile), profile)
                self.assertEqual(profiles.grammar_for_source_profile(profile), grammar)
        with self.assertRaises(TypeError):
            profiles.GRAMMAR_VERSIONS[V1] = 'closed-core-markup-v2'

    def test_unknown_profile_names_and_types_refuse_without_aliases(self):
        class Name(str):
            pass
        for profile in (None, True, {}, [], b'structured-core-v1', Name(V2), '', 'latest',
                        'structured-core', 'closed-core-v2', 'closed-core-markup-v2',
                        'Structured-core-v1', ' structured-core-v1', 'structured-core-v1 '):
            with self.subTest(profile=profile):
                with self.assertRaises(ValueError): profiles.validate_source_profile(profile)
                with self.assertRaises(ValueError): profiles.grammar_for_source_profile(profile)
                with self.assertRaises(core.ClosedCoreError):
                    core.certify(self.minimal(), 'main.typ', source_profile=profile)

    def test_default_remains_v1_and_proofs_bind_caller_selected_profile(self):
        tree = self.minimal()
        old = core.certify(tree, 'main.typ')
        new = core.certify(tree, 'main.typ', source_profile=V2)
        self.assertEqual((old.source_profile, old.grammar_version), (V1, core.GRAMMAR_VERSION))
        self.assertEqual((new.source_profile, new.grammar_version), (V2, 'closed-core-markup-v2'))
        self.assertNotEqual(old, new)
        self.assertEqual((old.evaluated_rule_ids, old.not_applicable_rule_ids, old.structural_reasons),
                         (new.evaluated_rule_ids, new.not_applicable_rule_ids, new.structural_reasons))
        self.assertIsNone(core.verify_proof(old, tree, 'main.typ'))
        self.assertIsNone(core.verify_proof(new, tree, 'main.typ', source_profile=V2))
        for proof, selection in ((new, V1), (old, V2)):
            with self.subTest(selection=selection), self.assertRaises(core.ClosedCoreError):
                core.verify_proof(proof, tree, 'main.typ', source_profile=selection)
        with self.assertRaises(core.ClosedCoreError): core.verify_proof(new, tree, 'main.typ')

    def test_profile_grammar_tree_path_source_fields_are_rederived(self):
        tree = self.minimal()
        proof = core.certify(tree, 'chapters/main.typ', source_profile=V2)
        for changes in ({'source_profile': V1}, {'source_profile': 'latest'},
                        {'grammar_version': 'closed-core-markup-v1'},
                        {'grammar_version': 'closed-core-markup-v99'},
                        {'source_sha256': '0' * 64}, {'source_bytes': 0},
                        {'tree_sha256': '0' * 64}, {'path': 'other.typ'}):
            with self.subTest(changes=changes), self.assertRaises(core.ClosedCoreError):
                core.verify_proof(clone_proof(proof, **changes), tree,
                                  'chapters/main.typ', source_profile=V2)
        for other, path in ((replace(tree, source=b'Other prose.'), 'chapters/main.typ'),
                            (tree, 'other.typ'),
                            (shaped(('Markup', [('Text', 'Plain'), ('Space', ' '),
                                                ('Text', 'prose.')])), 'chapters/main.typ')):
            with self.subTest(path=path), self.assertRaises(core.ClosedCoreError):
                core.verify_proof(proof, other, path, source_profile=V2)

    def test_admission_and_verification_stay_pure_and_not_parser_authentication(self):
        # Structurally valid fabricated input is not official parser evidence.
        # Its rejection at the protected bridge remains a separate boundary.
        tree = shaped(('Markup', [('Strong', [('Star', '*'), ('Markup', []), ('Star', '*')])]))
        with (patch('builtins.open', side_effect=AssertionError('Filesystem read')),
              patch.object(Path, 'read_bytes', side_effect=AssertionError('Filesystem read')),
              patch.object(Path, 'stat', side_effect=AssertionError('Filesystem read')),
              patch('syntax_client.parse', side_effect=AssertionError('Parser call'))):
            proof = core.certify(tree, 'main.typ', source_profile=V2)
            core.verify_proof(proof, tree, 'main.typ', source_profile=V2)
        with self.assertRaises(core.ClosedCoreError): core.certify(tree, 'main.typ')

    def test_local_nonempty_opaque_and_extra_body_shapes_refuse(self):
        cases = (
            ('Strong', [('Star', '*'), ('Markup', 'hidden'), ('Star', '*')]),
            ('Emph', [('Underscore', '_'), ('Markup', 'hidden'), ('Underscore', '_')]),
            ('Heading', [('HeadingMarker', '='), ('Markup', [('Text', 'body')])]),
            ('ListItem', [('ListMarker', '-'), ('Markup', [('Text', 'body')])]),
            ('EnumItem', [('EnumMarker', '+'), ('Space', ' '), ('Markup', [])]),
            ('Strong', [('Star', '*'), ('Markup', []), ('Markup', []), ('Star', '*')]),
            ('Heading', [('HeadingMarker', '='), ('Space', ' '), ('Markup', []), ('Markup', [])]),
            ('Strong', [('Star', '*'), ('Text', 'body'), ('Star', '*')]),
            ('Emph', [('Underscore', '_'), ('Markup', [])]),
            ('Heading', [('HeadingMarker', '=')]),
        )
        for kind, children in cases:
            with self.subTest(kind=kind, children=children), self.assertRaises(core.ClosedCoreError):
                core.certify(shaped(('Markup', [(kind, children)])), 'main.typ', source_profile=V2)

    def test_marker_delimiter_order_bytes_and_separator_shapes_refuse(self):
        cases = (
            ('Heading', [('HeadingMarker', '-'), ('Markup', [])]),
            ('ListItem', [('ListMarker', '+'), ('Markup', [])]),
            ('EnumItem', [('EnumMarker', '1.'), ('Space', ' '), ('Markup', [('Text', 'one')])]),
            ('Heading', [('HeadingMarker', '='), ('Space', '\n '), ('Markup', [('Text', 'body')])]),
            ('Heading', [('HeadingMarker', '='), ('Space', '\u00a0'), ('Markup', [('Text', 'body')])]),
            ('ListItem', [('ListMarker', '-'), ('Space', '\n'), ('Markup', [('Text', 'body')])]),
            ('ListItem', [('ListMarker', '-'), ('Space', '\n\n  '), ('Markup', [('Text', 'body')])]),
            ('EnumItem', [('EnumMarker', '+'), ('Parbreak', ' '), ('Markup', [('Text', 'body')])]),
            ('Strong', [('Star', '_'), ('Markup', []), ('Star', '*')]),
            ('Strong', [('Star', '*'), ('Markup', []), ('Underscore', '*')]),
            ('Emph', [('Underscore', '_'), ('Markup', []), ('Underscore', '*')]),
            ('Strong', [('Markup', []), ('Star', '*'), ('Star', '*')]),
        )
        for kind, children in cases:
            with self.subTest(kind=kind, children=children), self.assertRaises(core.ClosedCoreError):
                core.certify(shaped(('Markup', [(kind, children)])), 'main.typ', source_profile=V2)

    def test_unknown_nested_descendants_and_bom_positions_refuse(self):
        for unknown in ('FutureText', 'ModuleImport', 'ModuleInclude', 'FuncCall', 'MathCall',
                        'Label', 'Raw', 'Hash', 'Comment', 'LineComment', 'BlockComment',
                        'Link', 'ContentBlock', 'TermItem'):
            tree = shaped(('Markup', [('Strong', [('Star', '*'), ('Markup', [
                ('Emph', [('Underscore', '_'), ('Markup', [(unknown, 'hidden')]),
                          ('Underscore', '_')])]), ('Star', '*')])]))
            with self.subTest(unknown=unknown), self.assertRaises(core.ClosedCoreError):
                core.certify(tree, 'main.typ', source_profile=V2)
        for text in ('\ufeff\ufefftext', 'text\ufeff', '*\ufeff*'):
            tree = shaped(('Markup', [('Text', text)]))
            with self.subTest(text=text), self.assertRaises(core.ClosedCoreError):
                core.certify(tree, 'main.typ', source_profile=V2)
        nodes = tuple(Node('Markup', 0, 1, (i + 1,), False, (), None)
                      for i in range(MAX_DEPTH + 1))
        tree = Tree(b'x', 'markup', nodes + (Node('Text', 0, 1, (), False, (), None),))
        with self.assertRaises(core.ClosedCoreError): core.certify(tree, 'main.typ', source_profile=V2)


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter explicitly first')
class ActualStructuredGrammarTests(unittest.TestCase):
    def admit(self, source):
        tree = parse(source.encode())
        proof = core.certify(tree, 'fixture.typ', source_profile=V2)
        core.verify_proof(proof, tree, 'fixture.typ', source_profile=V2)
        self.assertEqual(tree.source, source.encode())
        return tree, proof

    def test_each_exact_wrapper_and_empty_nested_body_from_pinned_parser(self):
        for source, kind in (('= Heading', 'Heading'), ('======= Heading', 'Heading'),
                             ('- Item', 'ListItem'), ('+ Item', 'EnumItem'),
                             ('*Strong*', 'Strong'), ('_Emph_', 'Emph'),
                             ('=', 'Heading'), ('-', 'ListItem'), ('+', 'EnumItem'),
                             ('**', 'Strong'), ('__', 'Emph'),
                             ('= **', 'Heading'), ('- __', 'ListItem'), ('+ **', 'EnumItem')):
            with self.subTest(source=source):
                tree, proof = self.admit(source)
                self.assertIn(kind, {n.kind for n in tree.nodes})
                self.assertEqual(proof.grammar_version, 'closed-core-markup-v2')
                with self.assertRaises(core.ClosedCoreError): core.certify(tree, 'fixture.typ')
                with self.assertRaises(core.ClosedCoreError):
                    core.certify(tree, 'fixture.typ', source_profile=V1)

    def test_recursive_wrappers_prose_equations_bom_crlf_korean_and_whitespace(self):
        fixtures = (
            '= *Heading _nested $x_i$_*\r\n- **\r\n+ __\r\n',
            '- *Outer _inner $x^i$_*\n  + *$ y_j $*\n    - == Child $z^k$\n',
            '*\n= Heading $x_i$\n*', '*\n- one\n\n  two $x_i$\n*',
            '=\tHeading\r\n-\tItem\r\n+\tItem\r\n',
            '-\r\n  body $x_i$\r\n', '+\r\n \r\n  body $x_i$\r\n',
            '* a * _\r\n_', '\ufeff\r\n= 한글 $x_i$\r\n- *문장*\r\n',
            '\ufeffPlain prose.\r\n\r\n== _한글 $ x^j $_\r\n', '', '\ufeff',
        )
        for source in fixtures:
            with self.subTest(source=source):
                _, proof = self.admit(source)
                self.assertEqual(len(proof.not_applicable_rule_ids), 14)

    def test_parentheses_repair_reparses_and_keeps_structured_admission(self):
        fixtures = (
            ('= Heading $x_i$\n', '= Heading $x_(i)$\n'),
            ('- *Outer _inner $x^i$_*\n  + $ y_j $\n',
             '- *Outer _inner $x^(i)$_*\n  + $ y_(j) $\n'),
            ('\ufeff\r\n== _한글 $x_i^j$_\r\n', '\ufeff\r\n== _한글 $x_(i)^(j)$_\r\n'),
            ('*\n- $x^y^z$\n  + $x_i_j$\n*', '*\n- $x^(y^(z))$\n  + $x_(i_(j))$\n*'),
        )
        for source, expected in fixtures:
            with self.subTest(source=source):
                tree, _ = self.admit(source)
                result = analyze(tree, 'fixture.typ', _verify=False)
                self.assertFalse(result.skips or result.parse_errors or result.suppressed)
                self.assertTrue(result.diagnostics and result.edits)
                candidate = plan(tree.source, result.edits).candidate()
                self.assertEqual(candidate, expected.encode())
                final, _ = self.admit(candidate.decode())
                result = analyze(final, 'fixture.typ', _verify=False)
                self.assertFalse(result.diagnostics or result.edits or result.skips or result.parse_errors)

    def test_numbered_enumerators_term_lists_and_unknown_bodies_still_refuse(self):
        forbidden = (
            '/ term: description',
            '#import "missing.typ": *', '#include "missing.typ"', '#read("secret")',
            '#image("secret")', '#let x = 1', '#set text(fill: red)', '#show: body => body',
            '<label>', '@label', '`raw`', 'https://example.com',
            '// typst-lint: ignore-next core.parenthesized-scripts\n$x_i$',
            '/* comment */', '$/*comment*/x_i$', '$x^#i$', '$sqrt(x)$', '$x^"text"$',
        )
        for content in ('1. numbered', '42. numbered'):
            # Heading/inline bodies can parse these bytes as ordinary prose,
            # not an explicit enumerator. Refuse the actual EnumItem shapes.
            for wrapper in ('{}', '- {}', '+ {}', '*\n{}\n*', '_\n{}\n_'):
                with self.subTest(enumerator=wrapper.format(content)), self.assertRaises(core.ClosedCoreError):
                    core.certify(parse(wrapper.format(content).encode()), 'fixture.typ', source_profile=V2)
        for content in forbidden:
            for wrapper in ('{}', '*{}*', '_{}_', '= {}', '- {}', '+ {}', '- *\n{}\n*'):
                source = wrapper.format(content)
                with self.subTest(source=source), self.assertRaises(core.ClosedCoreError):
                    core.certify(parse(source.encode()), 'fixture.typ', source_profile=V2)

    def test_malformed_delimiters_nonleading_bom_and_unexpanded_v1_math_refuse(self):
        for source in ('*unclosed', '_unclosed', '*mixed_', '_mixed*', '*\n\n*',
                       '\ufeff\ufeffText', 'Text\ufeff', '*\ufeffText*', '= \ufeffText',
                       '\ufeff= Heading', '$$', '$ $', '$x_{}$', '$x^func(y)$', '$alpha.field$',
                       '= $x^(#read("secret"))$', '*$x^/*comment*/i$*'):
            with self.subTest(source=source), self.assertRaises(core.ClosedCoreError):
                core.certify(parse(source.encode()), 'fixture.typ', source_profile=V2)

    def test_forged_wrapper_token_metadata_and_exact_source_mismatch_refuse(self):
        tree, proof = self.admit('= *$x_i$*')
        for kind, changes in (('HeadingMarker', {'kind': 'ListMarker'}),
                              ('Star', {'kind': 'Underscore'}),
                              ('Markup', {'operands': (('top', 1),)}),
                              ('Heading', {'display': False})):
            index = next(i for i, node in enumerate(tree.nodes) if node.kind == kind)
            with self.subTest(kind=kind), self.assertRaises(core.ClosedCoreError):
                core.verify_proof(proof, tree_with(tree, index, **changes),
                                  'fixture.typ', source_profile=V2)
        for source in (b'- *$x_i$*', b'= _$x_i$*', b'= *$x_i$_'):
            with self.subTest(source=source), self.assertRaises(core.ClosedCoreError):
                core.certify(replace(tree, source=source), 'fixture.typ', source_profile=V2)


if __name__ == '__main__':
    unittest.main()
