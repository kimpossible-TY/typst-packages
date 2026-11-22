"""Internal captured-relative proofs: pure authority and exact bounded grammar."""
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, replace
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import captured_relative_core as core
from syntax_client import EXECUTABLE, Node, Tree, parse


def text_tree(raw=b'Plain prose.'):
    children = (1,) if raw else ()
    nodes = (Node('Markup', 0, len(raw), children, False, (), None),)
    if raw:
        nodes += (Node('Text', 0, len(raw), (), False, (), None),)
    return Tree(raw, 'markup', nodes)


def changed(tree, index, **fields):
    nodes = list(tree.nodes)
    nodes[index] = replace(nodes[index], **fields)
    return replace(tree, nodes=tuple(nodes))


class PureAuthorityTests(unittest.TestCase):
    def test_no_io_parser_evaluator_catalog_or_import_graph_authority(self):
        tree = text_tree()
        with (patch('builtins.open', side_effect=AssertionError('read')),
              patch.object(Path, 'read_bytes', side_effect=AssertionError('read')),
              patch.object(Path, 'stat', side_effect=AssertionError('read')),
              patch('syntax_client.parse', side_effect=AssertionError('parse')),
              patch('subprocess.Popen', side_effect=AssertionError('execute')),
              patch('builtins.eval', side_effect=AssertionError('eval'))):
            proof = core.certify('main.typ', {'main.typ': tree})
            self.assertIsNone(core.verify_proof(proof, target_path='main.typ', captures={'main.typ': tree}))
        from import_graph import ImportGraph, ImportSource
        with self.assertRaises(core.CapturedRelativeAuthorityError):
            core.certify('main.typ', ImportGraph('/irrelevant', (ImportSource('main.typ', tree.source),), ('main.typ',)))

    def test_exact_scope_rules_and_graph_sensitive_reasons(self):
        proof = core.certify('main.typ', {'main.typ': text_tree()})
        self.assertEqual(proof.source_profile, 'captured-relative-core-v1')
        self.assertNotEqual(proof.root_grammar_version, proof.module_grammar_version)
        self.assertEqual(proof.evaluated_rule_ids, ('core.parenthesized-scripts',))
        self.assertEqual(len(proof.not_applicable_rule_ids), 14)
        self.assertEqual(tuple(r for r, _ in proof.structural_reasons), proof.not_applicable_rule_ids)
        for rule in ('core.unique-scope-namespace', 'core.single-book-show'):
            self.assertIn('exhaustively inert', dict(proof.structural_reasons)[rule])
        from source_profiles import grammar_for_source_profile
        with self.assertRaises(ValueError): grammar_for_source_profile(core.SOURCE_PROFILE)

    def test_proof_cannot_be_constructed_and_every_field_is_rederived(self):
        captures = {'main.typ': text_tree()}
        proof = core.certify('main.typ', captures)
        with self.assertRaises(TypeError): core.CapturedRelativeCoreProof()
        with self.assertRaises(FrozenInstanceError): proof.target_path = 'other.typ'
        for fake in (True, {}, object.__new__(core.CapturedRelativeCoreProof)):
            with self.assertRaises(core.CapturedRelativeAuthorityError):
                core.verify_proof(fake, target_path='main.typ', captures=captures)
        mutations = dict(source_profile='closed-core-v1', target_path='other.typ',
            root_grammar_version='changed', module_grammar_version='changed', parser_contract=(),
            sources=(), edges=('fabricated',), modules=('fabricated',), root_bindings=('fabricated',),
            imported_uses=('fabricated',), evaluated_rule_ids=(), not_applicable_rule_ids=(), structural_reasons=())
        for name, value in mutations.items():
            fake = object.__new__(core.CapturedRelativeCoreProof)
            for field in proof.__dataclass_fields__:
                object.__setattr__(fake, field, getattr(proof, field))
            object.__setattr__(fake, name, value)
            with self.subTest(field=name), self.assertRaises(core.CapturedRelativeAuthorityError):
                core.verify_proof(fake, target_path='main.typ', captures=captures)
        for selected in (None, True, 'closed-core-v1', 'structured-core-v1', 'other'):
            with self.subTest(selected=selected), self.assertRaises(core.CapturedRelativeAuthorityError):
                core.verify_proof(proof, target_path='main.typ', captures=captures, source_profile=selected)

    def test_source_tree_and_path_tampering(self):
        tree = text_tree()
        proof = core.certify('main.typ', {'main.typ': tree})
        for captures, path in (({'main.typ': text_tree(b'Other prose.')}, 'main.typ'),
                               ({'other.typ': tree}, 'other.typ')):
            with self.assertRaises(core.CapturedRelativeAuthorityError):
                core.verify_proof(proof, target_path=path, captures=captures)
        for path in ('', '/main.typ', '../main.typ', './main.typ', 'a//main.typ', 'a/../main.typ',
                     'main.md', '@pkg/main.typ', 'a\\main.typ', 'main.typ\x00', 'a\n/main.typ', None):
            with self.subTest(path=path), self.assertRaises(core.CapturedRelativeAuthorityError):
                core.certify(path, {path: tree})
        for malformed in (replace(tree, source=bytearray(tree.source)), replace(tree, mode='math'),
                          replace(tree, source=b'\xff'), replace(tree, nodes=list(tree.nodes)),
                          changed(tree, 1, start=False), changed(tree, 1, children=[]),
                          changed(tree, 1, errors=0), changed(tree, 1, display=False),
                          changed(tree, 0, children=(1, 1)), changed(tree, 0, end=2),
                          changed(tree, 1, errors=True),
                          # Invalid representation must not be hidden by error flags.
                          changed(changed(tree, 0, errors=True), 1, start=False, errors=True)):
            with self.subTest(tree=malformed), self.assertRaises(core.CapturedRelativeAuthorityError):
                core.certify('main.typ', {'main.typ': malformed})

    def test_missing_original_tree_fields_are_typed_authority_refusals(self):
        for target, field in (('tree','source'),('tree','mode'),('tree','nodes'),
                              ('node','kind'),('node','start'),('node','end'),
                              ('node','children'),('node','errors'),('node','operands'),('node','display')):
            for operation in ('certify','verify','discovery'):
                tree = text_tree()
                captures = {'main.typ':tree}
                proof = core.certify('main.typ',captures)
                object.__delattr__(tree if target=='tree' else tree.nodes[1],field)
                with self.subTest(field=field,operation=operation), self.assertRaises(core.CapturedRelativeAuthorityError):
                    if operation=='certify': core.certify('main.typ',captures)
                    elif operation=='verify': core.verify_proof(proof,target_path='main.typ',captures=captures)
                    else: core.discover_relative_requests(tree,'main.typ')

    def test_opaque_or_meta_sources_do_not_masquerade_as_literal_proof(self):
        for raw in (b'#import "m.typ": x', b'#read("secret")', b'/* hi */', b'$x_i$', b'@label',
                    b'\xef\xbb\xbf#let x=1'):
            with self.subTest(raw=raw), self.assertRaises(core.CapturedRelativeSyntaxError):
                core.certify('main.typ', {'main.typ': text_tree(raw)})
        raw = b'Hidden'
        opaque = Tree(raw, 'markup', (Node('Markup', 0, len(raw), (), False, (), None),))
        with self.assertRaises(core.CapturedRelativeSyntaxError): core.certify('main.typ', {'main.typ': opaque})

    def test_budgets_preflight_before_copying_untrusted_mappings_or_links(self):
        class LyingMapping(Mapping):
            def __len__(self): return 1
            def __getitem__(self,key): return text_tree()
            def __iter__(self): raise AssertionError('Use bounded items')
            def items(self):
                for i in range(core.MAX_FILES + 1):
                    yield (f'name{i}.typ',text_tree())
                raise AssertionError('Unbounded mapping consumed')
        with self.assertRaises(core.CapturedRelativeAuthorityError):
            core.certify('name0.typ',LyingMapping())
        tree = text_tree()
        for malformed in (changed(tree,0,children=(1,)*100_000),
                          changed(tree,1,operands=(('top',1),)*100_000)):
            with self.assertRaises(core.CapturedRelativeAuthorityError):
                core.certify('main.typ',{'main.typ':malformed})

    def test_exact_path_and_parent_spelling_boundaries(self):
        path = 'x' * (core.MAX_PATH_BYTES - 4) + '.typ'
        self.assertEqual(len(path.encode()), 512)
        self.assertEqual(core.canonical_path(path), path)
        with self.assertRaises(core.CapturedRelativeAuthorityError): core.canonical_path('x' + path)
        self.assertEqual(core.resolve_relative('a/b/main.typ', '../../m.typ'), 'm.typ')
        self.assertEqual(core.resolve_relative('a/b/main.typ', '../m.typ'), 'a/m.typ')
        self.assertEqual(core.resolve_relative('a/main.typ', 'b/m.typ'), 'a/b/m.typ')
        for request in ('./m.typ', 'b/../m.typ', 'b//m.typ', 'b/./m.typ', '/m.typ', '@x:y',
                        'm\\file.typ', 'm.typ/', '', 'm.typ\n'):
            with self.subTest(request=request), self.assertRaises(core.CapturedRelativeCoreError):
                core.resolve_relative('a/main.typ', request)
        with self.assertRaises(core.CapturedRelativeAuthorityError): core.resolve_relative('a/main.typ', '../../m.typ')
        for importer, request in (('a/main.typ','../a/m.typ'),('a/b/main.typ','../../a/m.typ'),
                                  ('a/b/main.typ','../b/m.typ')):
            with self.subTest(importer=importer,request=request), self.assertRaises(core.CapturedRelativeSyntaxError):
                core.resolve_relative(importer,request)
        self.assertEqual(core.resolve_relative('a/main.typ','../b/m.typ'),'b/m.typ')
        with self.assertRaises(core.CapturedRelativeSyntaxError):
            core.resolve_relative('main.typ','module.txt')

    def test_individual_source_and_aggregate_nodes_are_bounded(self):
        raw = b'x' * core.MAX_FILE_BYTES
        tree = text_tree(raw)
        core.certify('main.typ', {'main.typ': tree})
        with self.assertRaises(core.CapturedRelativeAuthorityError):
            core.certify('main.typ', {'main.typ': text_tree(raw + b'x')})
        # The 200,000 aggregate-node cap is independent of each parser's 100k.
        nodes = tuple(Node('Text', i, i + 1, (), False, (), None) for i in range(70_000))
        nodes = (Node('Markup', 0, 70_000, tuple(range(1, 70_001)), False, (), None), *nodes)
        item = Tree(b'x' * 70_000, 'markup', nodes)
        with self.assertRaisesRegex(core.CapturedRelativeAuthorityError, 'node budget'):
            core.certify('a.typ', {'a.typ': item, 'b.typ': item, 'c.typ': item})


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned parser explicitly first')
class RealParserTests(unittest.TestCase):
    def captures(self, root, modules=None, target='main.typ'):
        return {path: parse(source.encode('utf-8')) for path, source in {target: root, **(modules or {})}.items()}

    def admit(self, root, modules=None, target='main.typ'):
        captures = self.captures(root, modules, target)
        proof = core.certify(target, captures)
        core.verify_proof(proof, target_path=target, captures=captures)
        self.assertEqual(captures[target].source, root.encode())
        return captures, proof

    def refuse(self, root, modules=None, error=core.CapturedRelativeSyntaxError):
        captures = self.captures(root, modules)
        with self.assertRaises(error): core.certify('main.typ', captures)

    def test_scalar_values_origins_renames_and_transitive_diamond(self):
        modules = {
            'm/shared.typ': '#let count = 12\n#let same = 12\n#let txt = "a+b"\n#let bracket = "("\n',
            'm/left.typ': '#import "shared.typ": count as original, txt\n#let next = original\n',
            'm/right.typ': '#import "shared.typ": count, same, bracket\n#let another = count\n',
        }
        _, proof = self.admit('#import "m/left.typ": next as count, txt\n'
            '#import "m/right.typ": another, same, bracket\n'
            '= Title $ x^count + y_another + z^txt + z_bracket $\n#txt\n', modules)
        env = {b.name: b for b in proof.root_bindings}
        self.assertEqual(env['count'].kind, 'integer')
        self.assertEqual(env['count'].value, 12)
        self.assertEqual(env['count'].origin, env['another'].origin)
        self.assertNotEqual(env['count'].origin, env['same'].origin)
        self.assertEqual(env['count'].origin.path, 'm/shared.typ')
        self.assertEqual([s.action for s in env['count'].provenance], ['declaration', 'import', 'alias', 'import'])
        self.assertEqual([(s.name, s.referenced_name) for s in env['count'].provenance[1:]],
                         [('original', 'count'), ('next', 'original'), ('count', 'next')])
        self.assertEqual([b.value for b in (env['txt'], env['bracket'])], ['a+b', '('])
        self.assertEqual(len(proof.modules), 3)
        self.assertEqual(len(proof.edges), 4)
        self.assertEqual(len(proof.imported_uses), 5)
        for identity in proof.sources:
            self.assertEqual(len(identity.tree_sha256), 64)

    def test_unused_inert_values_and_exact_integer_range(self):
        modules = {'m.typ': '#let count=9223372036854775807\n#let empty=""\n'
            '#let yes=true\n#let no=false\n#let absent=none\n#let automatic=auto\n#let alias=yes\n'}
        self.admit('#import "m.typ": count, yes\n$count$ #count\n', modules)
        self.refuse('#import "m.typ": yes\n$yes$', modules)
        self.refuse('#import "m.typ": yes\n#yes', modules)
        for literal in ('9223372036854775808', '-1', '+1', '0xff', '1.0', '1e2', '1pt', '123456789012345678901'):
            self.refuse('#import "m.typ": count\n$x^count$', {'m.typ': f'#let count={literal}'})
        self.admit('#import "m.typ": count\n#count', {'m.typ': '#let count=0'})

    def test_every_unused_initializer_and_module_statement_is_checked(self):
        bad = (
            '#let unused=read("secret")', '#let unused=eval("1")', '#let unused=image("x")',
            '#let unused=(1,2)', '#let unused=(a:1)', '#let unused=1+2', '#let unused=count+1',
            '#let unused={ 1 }', '#let unused=[text]', '#let unused=x=>x', '#let unused(x)=x',
            '#let unused=if true {1} else {2}', '#let unused=count.at(0)', '#let unused=math.pi',
            '#let unused="a\\n"', '#let unused="a\nb"', '#let unused=-1',
            '#let unused=later\n#let later=1', '#count', 'Output', '$x_i$',
            '#set text(size:12pt)', '#show: x=>x', '#context 1', '#count=2', '#count+=1',
            '#include "hidden.typ"', '#import "hidden.typ": *', '#import "hidden.typ"',
            '#import "@preview/pkg:1.0.0": thing', '#import ("hidden.typ"): thing',
            '// comment', '/* comment */', '// typst-lint-disable core.parenthesized-scripts',
        )
        for statement in bad:
            with self.subTest(statement=statement):
                self.refuse('#import "m.typ": count\n$x^count$', {'m.typ': '#let count=1\n'+statement+'\n'})

    def test_root_exhaustive_grammar_and_import_prefix(self):
        modules = {'m.typ': '#let count=12\n#let txt="한글 a+b ("\n#let sqrt=7\n'}
        self.admit('\ufeff\r\n#import "m.typ": count, txt, sqrt\r\n'
            '= Heading $x^count$\r\n\r\n- *Strong #txt $y_sqrt$*\r\n+ _Value $x^count$_\r\n', modules)
        bad = (
            'Text\n#import "m.typ": count\n$x^count$',
            '*#import "m.typ": count*', '= #import "m.typ": count',
            '#import "m.typ": count;\n$x^count$', '#import "m.typ": count $x^count$',
            '#import "m.typ": count\n#let other=1', '#import "m.typ": count\n#count.at(0)',
            '#import "m.typ": count\n#count()', '#import "m.typ": count\n#{count}',
            '#import "m.typ": count\n$ x^#count $', '#import "m.typ": count\n$sqrt(count)$',
            '#import "m.typ": count\n//comment', '#import "m.typ": count\n/*comment*/',
            '#import "m.typ": count\n#unknown', '#include "m.typ"',
            '#import "m.typ": *', '#import "m.typ"', '#import "m.typ" as other',
            '#import "m.typ": count.at', '#import "m.typ": count,,txt',
        )
        for source in bad:
            with self.subTest(source=source): self.refuse(source, modules)

    def test_cycles_missing_extra_duplicate_and_missing_export(self):
        self.refuse('#import "a.typ": count\n$x^count$',
            {'a.typ': '#import "b.typ": count', 'b.typ': '#import "a.typ": count'})
        self.refuse('#import "a.typ": count\n$x^count$', {'a.typ': '#import "main.typ": count'})
        self.refuse('#import "m.typ": count\n$x^count$', {}, core.CapturedRelativeAuthorityError)
        self.refuse('Prose.', {'m.typ': '#let count=1'}, core.CapturedRelativeAuthorityError)
        self.refuse('#import "m.typ": count\n#count', {'m.typ': '#let other=1'})
        for modules, root in (
            ({'m.typ':'#let count=1\n#let count=2'}, '#import "m.typ": count\n#count'),
            ({'m.typ':'#let count=1'}, '#import "m.typ": count,count\n#count'),
            ({'m.typ':'#let count=1'}, '#import "m.typ": count\n#import "m.typ": count\n#count'),
            ({'m.typ':'#let count=1','n.typ':'#let count=1'}, '#import "m.typ": count\n#import "n.typ": count\n#count'),
            ({'m.typ':'#let count=1','n.typ':'#import "m.typ": count\n#let count=2'}, '#import "n.typ": count\n#count')):
            with self.subTest(root=root, modules=modules): self.refuse(root, modules)

    def test_math_ident_shadowing_and_no_reinterpretation(self):
        _, proof = self.admit('#import "m.typ": sqrt, a, foo-bar\n$ x_sqrt + x^a + foo-bar $\n#foo-bar',
            {'m.typ': '#let sqrt=7\n#let a=1\n#let foo-bar="("'})
        self.assertEqual([(u.syntax_kind,u.name) for u in proof.imported_uses], [('MathIdent','sqrt'),('Ident','foo-bar')])
        self.assertEqual(proof.imported_uses[0].binding.value, 7)
        self.refuse('#import "m.typ": sqrt\n$sqrt(2)$', {'m.typ':'#let sqrt=7'})

    def test_original_trees_and_imported_ownership_survive_actual_script_proposals(self):
        from edit_plan import plan
        from script_rule import analyze
        roots = (
            '= Title $x^count$\n\n- *$x_y_text + x^count/y_text$*\n',
            '$ x^y^count + x_(y_text) + x^(count/text) $\n#text',
            '_$ x_sqrt + x_bracket $_',
        )
        for body in roots:
            source = '#import "m.typ": count, text, sqrt, bracket\n' + body
            captures, before = self.admit(source, {'m.typ': '#let count=12\n#let text="a+b"\n#let sqrt=7\n#let bracket="("'})
            tree = captures['main.typ']
            result = analyze(tree, 'main.typ', _verify=False)
            self.assertFalse(result.skips or result.parse_errors or result.suppressed)
            candidate = plan(tree.source, result.edits).candidate()
            original_tree = tree
            final_captures = {**captures, 'main.typ': parse(candidate)}
            after = core.certify('main.typ', final_captures)
            self.assertIs(captures['main.typ'], original_tree)
            self.assertEqual(original_tree.source, source.encode())
            self.assertTrue(all(e.start == e.end and not e.replacement.strip(b'()') for e in result.edits))
            self.assertEqual(tuple(u.semantic_identity for u in before.imported_uses),
                             tuple(u.semantic_identity for u in after.imported_uses))
            self.assertEqual(before.modules, after.modules)
            self.assertEqual(captures['m.typ'], final_captures['m.typ'])
            self.assertEqual([s for s in before.sources if s.path == 'm.typ'],
                             [s for s in after.sources if s.path == 'm.typ'])
            self.assertTrue(any(u.span != v.span or u.node_id != v.node_id
                                for u,v in zip(before.imported_uses,after.imported_uses)))

    def test_discovery_is_complete_but_never_a_proof(self):
        source = '#import "a.typ": count\n#import "m/b.typ": other\n$x_i$'
        tree = parse(source.encode())
        self.assertEqual(core.discover_relative_requests(tree, 'main.typ'),
                         (('a.typ','a.typ'),('m/b.typ','m/b.typ')))
        # Discovery scans nested nodes too; final certification rejects placement.
        wrapped = parse(b'= #import "a.typ": count\n')
        self.assertEqual(core.discover_relative_requests(wrapped,'main.typ'), (('a.typ','a.typ'),))
        for source in ('#include "a.typ"', '#import "a.typ": *', '#import "@preview/x:1.0.0": count',
                       '#import ("a.typ"): count', '#import "a.typ": count.at'):
            with self.subTest(source=source), self.assertRaises(core.CapturedRelativeCoreError):
                core.discover_relative_requests(parse(source.encode()), 'main.typ')

    def test_fixed_file_edge_depth_directory_and_path_boundaries(self):
        # 32 captured files, exactly, and source-identity path bytes exactly16KiB.
        names = [('x'*506 + f'{i:02}.typ') for i in range(32)]
        self.assertTrue(all(len(n.encode()) == 512 for n in names))
        root = ''.join(f'#import "{n}": value as value{i}\n' for i,n in enumerate(names[1:]))
        captures = self.captures(root, {n:'#let value=1' for n in names[1:]}, names[0])
        self.assertEqual(len(core.certify(names[0], captures).sources),32)
        captures['extra.typ'] = parse(b'#let value=1')
        with self.assertRaises(core.CapturedRelativeAuthorityError): core.certify(names[0],captures)
        # 128 distinct import statements, each destination unambiguous.
        root = ''.join(f'#import "m.typ": value as value{i}\n' for i in range(128))
        self.admit(root, {'m.typ':'#let value=1'})
        self.refuse(root+'#import "m.typ": value as last\n', {'m.typ':'#let value=1'}, core.CapturedRelativeAuthorityError)
        for depth in (16,17):
            modules = {f'm{i}.typ': (f'#import "m{i+1}.typ": value' if i<depth else '#let value=1') for i in range(1,depth+1)}
            source = '#import "m1.typ": value\n#value'
            if depth==16: self.admit(source, modules)
            else: self.refuse(source, modules, core.CapturedRelativeAuthorityError)
        # Root plus63 nested parents admitted; one more directory refused.
        for count in (63,64):
            target = '/'.join(['a']*count+['main.typ'])
            mapping = {target: parse(b'Prose.')}
            if count==63: core.certify(target,mapping)
            else:
                with self.assertRaises(core.CapturedRelativeAuthorityError): core.certify(target,mapping)

    def test_exact_aggregate_source_byte_boundary(self):
        def sized_module(size):
            prefix, suffix = '#let value="', '"'
            return prefix + 'x' * (size - len(prefix) - len(suffix)) + suffix
        modules = {'m0.typ':sized_module(core.MAX_FILE_BYTES - 13),
                   'm1.typ':sized_module(core.MAX_FILE_BYTES),
                   'm2.typ':sized_module(core.MAX_FILE_BYTES),
                   'm3.typ':'#let value=12'}
        self.assertEqual(len(modules['m3.typ']),13)
        prefix = ''.join(f'#import "m{i}.typ": value as value{i}\n' for i in range(4))
        root = prefix + 'x' * (core.MAX_FILE_BYTES-len(prefix))
        captures = self.captures(root,modules)
        self.assertEqual(sum(len(t.source) for t in captures.values()),core.MAX_SOURCE_BYTES)
        proof = core.certify('main.typ',captures)
        self.assertEqual(sum(s.source_bytes for s in proof.sources),core.MAX_SOURCE_BYTES)
        captures['m0.typ']=parse(sized_module(core.MAX_FILE_BYTES - 12).encode())
        with self.assertRaisesRegex(core.CapturedRelativeAuthorityError,'Aggregate source'):
            core.certify('main.typ',captures)

    def test_cached_diamond_does_not_bypass_longer_route_depth(self):
        # Shared node is first certified shallowly; the second path reaches it
        # at depth16, but its own dependency would then be at forbidden depth17.
        modules = {'shared.typ':'#import "leaf.typ": value', 'leaf.typ':'#let value=1'}
        modules.update({f'm{i}.typ': f'#import "m{i+1}.typ": value' for i in range(1,15)})
        modules['m15.typ']='#import "shared.typ": value'
        self.refuse('#import "shared.typ": value as shallow\n#import "m1.typ": value as deep',
                    modules,core.CapturedRelativeAuthorityError)

    def test_alias_binding_and_use_budgets(self):
        for length in (63,64):
            module = '#let name0=1\n'+''.join(f'#let name{i}=name{i-1}\n' for i in range(1,length))
            root = f'#import "m.typ": name{length-1} as value\n#value'
            if length==63: self.admit(root,{'m.typ':module})
            else:
                #64 module steps plus final root named import exceeds64.
                self.refuse(root,{'m.typ':module},core.CapturedRelativeAuthorityError)
        module = ''.join(f'#let name{i}=1\n' for i in range(4095))
        self.admit('#import "m.typ": name0\n#name0',{'m.typ':module})
        self.refuse('#import "m.typ": name0, name1\n#name0',{'m.typ':module},core.CapturedRelativeAuthorityError)
        prefix = '#import "m.typ": count\n'
        self.admit(prefix+'#count '*8192,{'m.typ':'#let count=1'})
        self.refuse(prefix+'#count '*8193,{'m.typ':'#let count=1'},core.CapturedRelativeAuthorityError)

    def test_type_altered_nested_proofs_do_not_pass_python_numeric_equality(self):
        captures = self.captures('#import "m.typ": count\n#count', {'m.typ':'#let count=1'})
        for select, field, value in (
            (lambda p:p.root_bindings[0], 'value', True),
            (lambda p:p.root_bindings[0], 'value', 1.0),
            (lambda p:p.root_bindings[0].origin, 'node_id', 2.0),
            (lambda p:p.root_bindings[0].provenance[0], 'node_id', 2.0),
            (lambda p:p.imported_uses[0], 'ordinal', True),
            (lambda p:p.sources[0], 'source_bytes', float(len(captures['m.typ'].source))),
        ):
            proof = core.certify('main.typ',captures)
            object.__setattr__(select(proof),field,value)
            with self.subTest(field=field,value=value), self.assertRaises(core.CapturedRelativeAuthorityError):
                core.verify_proof(proof,target_path='main.typ',captures=captures)
        proof = core.certify('main.typ',captures)
        object.__delattr__(proof.root_bindings[0].provenance[0], 'referenced_name')
        with self.assertRaises(core.CapturedRelativeAuthorityError):
            core.verify_proof(proof,target_path='main.typ',captures=captures)

    def test_actual_syntax_error_and_forged_authority_remain_distinct(self):
        bad = parse(b'#let count=')
        self.assertTrue(bad.nodes[0].errors)
        with self.assertRaises(core.CapturedRelativeSyntaxError):
            core.certify('main.typ',{'main.typ':bad})
        forged = changed(bad,0,end=0)
        with self.assertRaises(core.CapturedRelativeAuthorityError):
            core.certify('main.typ',{'main.typ':forged})


if __name__ == '__main__': unittest.main()
