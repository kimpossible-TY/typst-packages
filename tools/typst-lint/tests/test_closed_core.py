"""Closed applicability is proved from real pinned syntax, never skip relabeling."""
from dataclasses import FrozenInstanceError, replace
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import closed_core as core
from edit_plan import plan
from script_rule import analyze
from syntax_client import EXECUTABLE, MAX_DEPTH, MAX_INPUT, MAX_NODES, Node, Tree, parse
from typst_lint import load_rules


def tree_with(tree, index, **changes):
    nodes = list(tree.nodes)
    nodes[index] = replace(nodes[index], **changes)
    return replace(tree, nodes=tuple(nodes))


class ProofBoundaryTests(unittest.TestCase):
    def minimal(self, raw=b'Plain prose.'):
        return Tree(raw, 'markup', (Node('Markup', 0, len(raw), (1,), False, (), None),
                                    Node('Text', 0, len(raw), (), False, (), None)))

    def test_exact_fifteen_rules_and_each_applicability_reason(self):
        catalog = load_rules()[0]['rules']
        self.assertEqual(set(core.CORE_RULE_IDS), {r['id'] for r in catalog if r['id'].startswith('core.')})
        self.assertEqual(len(core.CORE_RULE_IDS), 15)
        proof = core.certify(self.minimal(), 'main.typ')
        self.assertEqual(proof.evaluated_rule_ids, ('core.parenthesized-scripts',))
        self.assertEqual(len(proof.not_applicable_rule_ids), 14)
        self.assertEqual(tuple(r for r, _ in proof.structural_reasons), proof.not_applicable_rule_ids)
        self.assertTrue(all(reason for _, reason in proof.structural_reasons))
        self.assertIn('using-helper precondition is false', dict(proof.structural_reasons)['core.single-book-show'])

    def test_no_filesystem_reads_or_parser_calls_in_admission_or_verification(self):
        tree = self.minimal()
        with (patch('builtins.open', side_effect=AssertionError('Filesystem read')),
             patch.object(Path, 'read_bytes', side_effect=AssertionError('Filesystem read')),
             patch.object(Path, 'stat', side_effect=AssertionError('Filesystem read')),
             patch('syntax_client.parse', side_effect=AssertionError('Parser call'))):
            proof = core.certify(tree, 'main.typ')
            self.assertIsNone(core.verify_proof(proof, tree, 'main.typ'))

    def test_proof_is_immutable_not_caller_constructible_or_boolean_coverage(self):
        tree = self.minimal()
        proof = core.certify(tree, 'main.typ')
        with self.assertRaises(FrozenInstanceError): proof.path = 'other.typ'
        with self.assertRaises(TypeError): core.CoverageProof()
        with self.assertRaises(TypeError): core.CoverageProof(complete=True)
        with self.assertRaises(TypeError): core.certify(tree, 'main.typ', complete=True)
        for fake in (True, False, 'COMPLETE', {'complete': True}, object.__new__(core.CoverageProof)):
            with self.subTest(fake=type(fake)), self.assertRaises(core.ClosedCoreError):
                core.verify_proof(fake, tree, 'main.typ')

    def test_hash_and_path_binding_is_rederived_not_trusted(self):
        tree = self.minimal()
        proof = core.certify(tree, 'chapters/main.typ')
        self.assertEqual(proof.source_sha256, hashlib.sha256(tree.source).hexdigest())
        self.assertEqual(proof.source_bytes, len(tree.source))
        self.assertEqual(proof.parser_contract, core.PARSER_CONTRACT)
        self.assertEqual(len(proof.tree_sha256), 64)
        self.assertEqual(proof, core.certify(tree, 'chapters/main.typ'))
        for other, path in ((self.minimal(b'Other prose.'), 'chapters/main.typ'),
                            (tree, 'other.typ')):
            with self.assertRaises(core.ClosedCoreError): core.verify_proof(proof, other, path)
        fake = object.__new__(core.CoverageProof)
        for field in core.CoverageProof.__dataclass_fields__:
            object.__setattr__(fake, field, getattr(proof, field))
        object.__setattr__(fake, 'grammar_version', 'caller-complete-v99')
        with self.assertRaises(core.ClosedCoreError): core.verify_proof(fake, tree, 'chapters/main.typ')

    def test_paths_are_exact_canonical_root_relative_typst_paths(self):
        for path in (None, Path('main.typ'), '', '/main.typ', '../main.typ', './main.typ',
                     'a//main.typ', 'a/../main.typ', 'a\\main.typ', 'main.typ\x00', 'main.md'):
            with self.subTest(path=path), self.assertRaises(core.ClosedCoreError):
                core.certify(self.minimal(), path)

    def test_source_and_tree_types_and_input_limit_fail_closed(self):
        tree = self.minimal()
        for value in (True, object(), replace(tree, source=bytearray(tree.source)),
                      replace(tree, source=b'\xff'), replace(tree, source=b'x' * (MAX_INPUT + 1)),
                      replace(tree, mode='code'), replace(tree, mode='math'),
                      replace(tree, nodes=list(tree.nodes)), replace(tree, nodes=()),
                      replace(tree, nodes=tree.nodes * (MAX_NODES + 1))):
            with self.subTest(value=type(value)), self.assertRaises(core.ClosedCoreError):
                core.certify(value, 'main.typ')

    def test_all_node_fields_are_revalidated_including_bool_spans(self):
        tree = self.minimal()
        for changes in ({'kind': 'FutureText'}, {'kind': ['Text']}, {'start': False}, {'end': 0.0},
                        {'children': []}, {'children': (True,)}, {'errors': 0}, {'display': False},
                        {'operands': []}, {'operands': (('top',),)}, {'operands': (['top', 1],)},
                        {'operands': (('top', 1),)}, {'start': 1}, {'end': len(tree.source) + 1},
                        {'children': (0,)}, {'errors': True}):
            with self.subTest(changes=changes), self.assertRaises(core.ClosedCoreError):
                core.certify(tree_with(tree, 1, **changes), 'main.typ')

    def test_structure_cannot_hide_code_as_fabricated_literal_or_orphan(self):
        for raw in (b'#include "secret.typ"', b'/* comment */', b'$x^i$', b'@private', b'`code`',
                    b'Normal\n#include "hidden.typ"', b'\xef\xbb\xbf#read("secret")'):
            with self.subTest(raw=raw), self.assertRaises(core.ClosedCoreError):
                core.certify(self.minimal(raw), 'main.typ')
        for raw in (b'#read(\"secret\")', b'Plain prose.'):
            with self.subTest(opaque=raw), self.assertRaises(core.ClosedCoreError):
                core.certify(Tree(raw, 'markup', (Node('Markup', 0, len(raw), (), False, (), None),)), 'main.typ')
        tree = self.minimal()
        with self.assertRaises(core.ClosedCoreError):
            core.certify(replace(tree, nodes=tree.nodes + (Node('Hash', 0, 1, (), False, (), None),)), 'main.typ')
        with self.assertRaises(core.ClosedCoreError):
            core.certify(tree_with(tree, 0, children=(1, 1)), 'main.typ')
        # The resource bound is checked again even if a caller fabricates Tree.
        nodes = tuple(Node('Math', 0, 1, (i+1,), False, (), None) for i in range(MAX_DEPTH + 1))
        nodes = (replace(nodes[0], kind='Markup'), *nodes[1:], Node('MathText', 0, 1, (), False, (), None))
        with self.assertRaises(core.ClosedCoreError): core.certify(Tree(b'x', 'markup', nodes), 'main.typ')


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter explicitly first')
class ActualParserGrammarTests(unittest.TestCase):
    def admit(self, source):
        tree = parse(source.encode('utf-8'))
        proof = core.certify(tree, 'fixture.typ')
        core.verify_proof(proof, tree, 'fixture.typ')
        return tree, proof

    def test_plain_root_prose_unicode_bom_crlf_without_book_helper_owner(self):
        for source in ('', 'Plain prose.', '한글 문장. Ελληνικά.', '\ufeff',
                       '\ufeffPlain prose\r\n\r\nAnother paragraph.\r\n', 'A\tB\r\nC',
                       'Word-word: sentence! (Parentheses).', 'No shared helpers are used here.'):
            with self.subTest(source=source):
                tree, proof = self.admit(source)
                self.assertIn('core.single-book-show', proof.not_applicable_rule_ids)
                self.assertEqual(len(proof.not_applicable_rule_ids), 14)
                self.assertEqual(tree.source, source.encode())

    def test_recursive_equations_and_script_repairs_stay_in_the_language(self):
        fixtures = (
            ('$X^i_j$', '$X^(i)_(j)$'),
            ('\ufeffProse\r\n$α^β + X_j$.\r\n', '\ufeffProse\r\n$α^(β) + X_(j)$.\r\n'),
            ('$partial^alpha + theta_beta$', '$partial^(alpha) + theta_(beta)$'),
            ('$x^y^z$', '$x^(y^(z))$'), ('$x_i_j$', '$x_(i_(j))$'),
            ("$x_i' + x''^j$", "$x_(i') + x''^(j)$"),
            ('$x^a/b^c$', '$x^(a)/b^(c)$'), ('$x^(a/b)$', '$x^(a/b)$'),
            ('$x^(a^b)$', '$x^(a^(b))$'),
            ('$x_(a+b)^((i_j))$', '$x_(a+b)^((i_(j)))$'),
            ('$x_{a} + x^[b]$', '$x_({a}) + x^([b])$'),
            ('$x^1.2 + x^23 + 3.0$', '$x^(1.2) + x^(23) + 3.0$'),
            ('$ ( x_i ) / ( y^j ) $', '$ ( x_(i) ) / ( y^(j) ) $'),
            ('$ x^ i \r\n_j $', '$ x^ (i) \r\n_(j) $'),
            ('$x^( i )$', '$x^( i )$'),
        )
        for source, expected in fixtures:
            with self.subTest(source=source):
                tree, _ = self.admit(source)
                result = analyze(tree, 'fixture.typ', _verify=False)
                self.assertFalse(result.skips or result.parse_errors or result.suppressed)
                candidate = plan(tree.source, result.edits).candidate()
                self.assertEqual(candidate, expected.encode())
                checked = parse(candidate)
                core.certify(checked, 'fixture.typ')
                final = analyze(checked, 'fixture.typ', _verify=False)
                self.assertFalse(final.edits or final.diagnostics or final.skips or final.parse_errors)

    def test_inline_display_typing_and_plain_identifier_names_are_not_purity_evidence(self):
        tree, _ = self.admit('Prose $x_i$ and $ x^j $ and $\r\ny_k\r\n$.')
        self.assertEqual([n.display for n in tree.nodes if n.kind == 'Equation'], [False, True, True])
        # Bare ordinary identifiers do not imply a call, regardless of name.
        self.admit('$read + eval + image + sin + alpha^beta$')
        for source in ('$sin(x)$', '$sqrt(x)$', '$read("secret")$', '$eval("1")$', '$image("secret")$'):
            with self.subTest(source=source), self.assertRaises(core.ClosedCoreError):
                core.certify(parse(source.encode()), 'fixture.typ')

    def test_calls_fields_code_resources_and_other_syntax_refused_at_any_depth(self):
        sources = (
            '#import "missing.typ": *\n$x^i$', '#include "missing.typ"',
            '#let x = 1\n$x^i$', '#show: body => body', '#set text(fill: red)',
            '#context [Text]', '#read("secret")', '#image("secret")', '#eval("1")',
            '#(s.tag)("x")', '#local-tag-scope(s => [$x^i$])',
            '@target', '<target>', '`$x^i$`', '*emphasis*', '_emphasis_', '= Heading',
            '- List', '+ Enum', '#[Text $x^i$]', 'https://example.com',
            '// typst-lint: ignore-next core.parenthesized-scripts\n$x^i$',
            '/* comment */$x^i$', '$/*comment*/ x^i$', '$x^i /*comment*/$',
            '$x^/*comment*/i$', '$x^#i$', '$x^"text"$', '$x^("text")$',
            '$x^(#read("secret"))$', '$x^((#include "missing.typ"))$',
            '$x^([#eval("1")])$', '$a/(#read("secret"))$',
            '$a_(b^((#eval("1"))))$', '$a_(sqrt(x))$', '$a^(foo.bar)$',
            '$a^(#(() => 1))$', '$x^i & y$', '$x^i \\ y$',
            '$x^(a//comment\n)$', '$x^(a/*comment*/)$', '$x^(#[$y^i$])$',
            '$x^(#show: body => body)$', '$x^(#set text(fill: red))$',
            '$x^(#let f() = 1)$', '$x^(#<label>)$', '$x^(#ref(<label>))$',
        )
        for source in sources:
            with self.subTest(source=source), self.assertRaises(core.ClosedCoreError):
                core.certify(parse(source.encode()), 'fixture.typ')

    def test_parse_errors_and_empty_math_refused(self):
        for source in ('$x^$', '$x^i', '#let =', '$x^(a$', '$$', '$ $', '$()$', '$x^()$'):
            with self.subTest(source=source), self.assertRaises(core.ClosedCoreError):
                core.certify(parse(source.encode()), 'fixture.typ')

    def test_every_token_and_typed_operand_is_checked_not_just_kind_inventory(self):
        tree, proof = self.admit('$x_i^j$')
        attach = next(i for i, n in enumerate(tree.nodes) if n.kind == 'MathAttach')
        atom = next(i for i, n in enumerate(tree.nodes) if n.kind == 'MathText')
        op = next(i for i, n in enumerate(tree.nodes) if n.kind == 'Underscore')
        equation = next(i for i, n in enumerate(tree.nodes) if n.kind == 'Equation')
        for i, changes in ((attach, {'operands': ()}),
                           (attach, {'operands': tuple(reversed(tree.nodes[attach].operands))}),
                           (attach, {'operands': (('top', tree.nodes[attach].operands[0][1]),)}),
                           (atom, {'kind': 'FutureMath'}), (atom, {'kind': 'FuncCall'}),
                           (atom, {'children': (atom,)}), (op, {'kind': 'Hat'}),
                           (equation, {'display': True}), (equation, {'display': 0})):
            with self.subTest(i=i, changes=changes), self.assertRaises(core.ClosedCoreError):
                core.verify_proof(proof, tree_with(tree, i, **changes), 'fixture.typ')
        # Changed bytes with preserved spans cannot masquerade as known tokens.
        for raw in (b'$x#i^j$', b'$x_i#j$', b'$x_i^#$'):
            with self.subTest(raw=raw), self.assertRaises(core.ClosedCoreError):
                core.certify(replace(tree, source=raw), 'fixture.typ')
        unicode_tree, _ = self.admit('$α^β$')
        i = next(i for i, n in enumerate(unicode_tree.nodes) if n.kind == 'MathText')
        with self.assertRaises(core.ClosedCoreError):
            core.certify(tree_with(unicode_tree, i, end=unicode_tree.nodes[i].end - 1), 'fixture.typ')


if __name__ == '__main__':
    unittest.main()
