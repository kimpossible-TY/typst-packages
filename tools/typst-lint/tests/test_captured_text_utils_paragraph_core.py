"""Real pinned-parser coverage of the internal paragraph proof only."""
from dataclasses import FrozenInstanceError, replace, fields
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import captured_text_utils_paragraph_core as core
from capitalization import WORDS, evaluate
from syntax_client import EXECUTABLE, Tree, Node, parse
from static_resolver import PackageSource

ROOT = Path(__file__).resolve().parents[3]
PREFIX = '#import "@local/text-utils:0.1.2": paragraph_tab, apply-paragraph-tabs\n#show: apply-paragraph-tabs\n'
CONFIG = b'rule-set="pde"\ninclude=["main.typ"]\n[capitalization]\nlanguage="en"\n'

def changed(tree, index, **values):
    nodes = list(tree.nodes)
    nodes[index] = replace(nodes[index], **values)
    return replace(tree, nodes=tuple(nodes))

def fake_proof(proof, name, value):
    result = object.__new__(core.CapturedTextUtilsParagraphProof)
    for f in fields(proof): object.__setattr__(result,f.name,getattr(proof,f.name))
    object.__setattr__(result,name,value)
    return result

@unittest.skipUnless(EXECUTABLE.is_file(), 'Build the explicitly pinned parser first')
class ParagraphCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lib=(Path(__file__).resolve().parents[1] / 'tests/fixtures/reviewed-packages')/'local/text-utils/0.1.2/lib.typ'
        cls.authority=dict(config_bytes=CONFIG,
            catalog_bytes=(ROOT/'tools/typst-lint/rules/catalog.json').read_bytes(),
            package_source=PackageSource(core.IMPORT_PATH,str(lib),lib.read_bytes()),
            manifest_bytes=lib.with_name('typst.toml').read_bytes(),package_tree=parse(lib.read_bytes()))
    def source(self,body='\n#paragraph_tab\ntherefore a sentence.\n'):
        return (PREFIX+body).encode()
    def admit(self,body='\n#paragraph_tab\ntherefore a sentence.\n',raw=None,**authority):
        tree=parse(self.source(body) if raw is None else raw)
        selected=self.authority|authority
        proof=core.certify(tree,'main.typ',**selected)
        core.verify_proof(proof,tree,'main.typ',**selected)
        return tree,proof
    def refuse(self,body=None,raw=None,error=core.CapturedTextUtilsParagraphError,**authority):
        tree=parse(self.source(body) if raw is None else raw)
        with self.assertRaises(error): core.certify(tree,'main.typ',**(self.authority|authority))
    def test_all_26_lowercase_starters_and_only_exact_initial_byte(self):
        self.assertEqual(len(WORDS),26)
        for word in sorted(WORDS):
            with self.subTest(word=word):
                tree,proof=self.admit('\n#paragraph_tab\n'+word.decode()+' a sentence.\n')
                self.assertEqual(len(proof.contexts),1)
                p=proof.paragraphs[0]
                result=evaluate(tree.source,proof.contexts[0])
                self.assertEqual((result.edit.start,result.edit.end,result.edit.replacement),(p.edit_start,p.edit_start+1,p.replacement))
                candidate=tree.source[:p.edit_start]+p.replacement+tree.source[p.edit_start+1:]
                final=parse(candidate); checked=core.certify(final,'main.typ',**self.authority)
                core.compare_candidate(proof,checked,before_tree=tree,after_tree=final,path='main.typ',**self.authority)
                self.assertEqual(sum(a!=b for a,b in zip(tree.source,candidate)),1)
    def test_titlecase_and_all_uppercase_are_verified_clean_noops(self):
        for word in sorted(WORDS):
            for starter in (word.capitalize(),word.upper()):
                with self.subTest(starter=starter):
                    tree,proof=self.admit('\n#paragraph_tab\n'+starter.decode()+' a sentence.\n')
                    self.assertIsNone(proof.paragraphs[0].edit_start)
                    self.assertEqual(evaluate(tree.source,proof.contexts[0]).skip_reason,'already-uppercase')
                    core.compare_candidate(proof,proof,before_tree=tree,after_tree=tree,path='main.typ',**self.authority)
    def test_bom_lf_crlf_spacing_multiple_paragraphs_and_optional_unmarked_start(self):
        raw=('\ufeff \t\n'+PREFIX+'\nAn initial paragraph.\n\n \t#paragraph_tab \t\n \ttherefore an inline $x^(2)+y_(i)$ expression.\n\n#paragraph_tab\nTHE next sentence.\n').encode()
        for line_ending in (b'\n',b'\r\n'):
            tree,proof=self.admit(raw=raw.replace(b'\n',line_ending))
            self.assertEqual(len(proof.paragraphs),3)
            self.assertIsNone(proof.paragraphs[0].marker_nodes)
            self.assertEqual(len(proof.boundaries),2)
            actual=core.analyze_boundaries(tree,'main.typ',**self.authority)
            self.assertEqual(actual.boundaries,proof.boundaries)
            self.assertFalse(actual.diagnostics+actual.edits+actual.skips+actual.parse_errors+actual.suppressed)
    def test_exact_33_rule_source_bound_matrix(self):
        tree,proof=self.admit()
        self.assertEqual(len(proof.obligations),33)
        self.assertEqual(len(proof.evaluated_rule_ids),5)
        self.assertEqual(len(proof.structural_reasons),28)
        for record in proof.obligations:
            self.assertEqual(record.source_sha256,proof.source_sha256)
            self.assertEqual(record.tree_sha256,proof.tree_sha256)
            self.assertEqual(len(record.catalog_record_sha256),64)
            self.assertEqual(record.disposition,'requires-actual-evaluation' if record.rule_id in core.EVALUATED_RULE_IDS else 'structurally-inapplicable')
        self.assertIn('one physical',dict(proof.structural_reasons)['pde.continuation-line'])
        self.assertFalse(hasattr(proof,'complete'))
        from source_profiles import grammar_for_source_profile
        with self.assertRaises(ValueError): grammar_for_source_profile(core.SOURCE_PROFILE)
    def test_package_and_invoked_closure_are_separate_pinned_facts(self):
        _,proof=self.admit()
        identity=proof.package
        self.assertEqual((identity.source_bytes,identity.manifest_bytes),(5583,173))
        self.assertEqual(len(identity.exports),6)
        raw=self.authority['package_source'].source
        self.assertEqual(raw[slice(*identity.invoked_body_span)],b''.join(raw.splitlines(keepends=True)[10:22]))
        self.assertNotEqual(identity.invoked_body_sha256,identity.source_sha256)
    def test_every_excluded_source_construct_is_refused(self):
        bad=(
            '= Heading','- List','+ Enumeration','*strong*','_emphasis_',
            '@label','<label>','`raw`','\\#escape','"quoted"','[nested]',
            '#set text(lang:"en")','#show: x=>x','#let x=1','#context [body]',
            '#read("hidden")','#parbreak()','#paragraph_tab()',
            '// comment','/* comment */','// typst-lint-disable pde.paragraph-capitalization',
            '#import "other.typ": value','#include "other.typ"',
            'therefore \uf000 hidden.','therefore \ue001 hidden.',
            'therefore $ x=2 $ display.','therefore $x_i$ dirty.',
            'therefore $cancel(x)$ call.','therefore $x+\n y$ wrapped.',
            'therefore #footnote[body] call.','therefore #unknown call.',
        )
        for body in bad:
            with self.subTest(body=body): self.refuse('\n#paragraph_tab\n'+body+'\n')
    def test_unknown_nonallowlisted_mixed_case_incomplete_starters_are_refused(self):
        for starter in ('unknown','a','x','thE','tHerefore','éthe','therefore,','the.','theorem','the_','the\\'):
            with self.subTest(starter=starter): self.refuse('\n#paragraph_tab\n'+starter+' text.\n')
        self.refuse('\n#paragraph_tab\nthe')
    def test_markers_require_complete_physical_boundary_and_immediate_first_word(self):
        bodies=(
            '#paragraph_tab\ntherefore text.\n',
            '\n#paragraph_tab\n\ntherefore text.\n',
            '\n#paragraph_tab therefore text.\n',
            '\n# paragraph_tab\ntherefore text.\n',
            '\n#paragraph_tab // comment\ntherefore text.\n',
            '\n#paragraph_tab\n// comment\ntherefore text.\n',
            '\n#paragraph_tab\rtherefore text.\n',
            '\n#paragraph_tab\n',
            '\n#paragraph_tab\n#paragraph_tab\ntherefore text.\n',
            '\n#paragraph_tab\ntherefore one.\nUnmarked continuation.\n',
            '\n#paragraph_tab\ntherefore one.\n\nUnmarked paragraph.\n',
            '\nInitial prose.\n#paragraph_tab\ntherefore two.\n',
        )
        for body in bodies:
            with self.subTest(body=body): self.refuse(body)
    def test_import_and_show_contract_is_exact(self):
        alternatives=(
            PREFIX.replace('paragraph_tab, apply-paragraph-tabs','apply-paragraph-tabs, paragraph_tab'),
            PREFIX.replace('paragraph_tab, apply-paragraph-tabs','paragraph_tab'),
            PREFIX.replace('paragraph_tab, apply-paragraph-tabs','paragraph_tab, apply-paragraph-tabs, highlighted'),
            PREFIX.replace('paragraph_tab, apply-paragraph-tabs','*'),
            PREFIX.replace('paragraph_tab, apply-paragraph-tabs','paragraph_tab as marker, apply-paragraph-tabs'),
            PREFIX.replace(': paragraph_tab, apply-paragraph-tabs',''),
            PREFIX.replace('@local/text-utils:0.1.2','@local/text-utils:0.1.3'),
            PREFIX.replace('@local/text-utils:0.1.2','facade.typ'),
            PREFIX.replace('#show: apply-paragraph-tabs\n',''),
            PREFIX+'#show: apply-paragraph-tabs\n',
            PREFIX.replace('#show: apply-paragraph-tabs','#show: apply-paragraph-tabs.with()'),
            PREFIX.replace('#show: apply-paragraph-tabs','#show: (apply-paragraph-tabs)'),
            PREFIX.replace('\n#show','; #show'),
        )
        for prefix in alternatives:
            with self.subTest(prefix=prefix): self.refuse(raw=(prefix+'\n#paragraph_tab\ntherefore text.\n').encode())
        self.refuse(raw=('Existing prose.\n'+PREFIX+'\n#paragraph_tab\ntherefore text.\n').encode())
    def test_empty_or_unmarked_only_scope_is_refused(self):
        self.refuse('')
        self.refuse('\nOnly unmarked prose.\n')
        self.refuse('\n')
    def test_strict_pde_config_requires_only_explicit_english_and_target(self):
        valid=core.validate_config(CONFIG,'main.typ')
        self.assertEqual(valid.language,'en')
        with self.assertRaises(FrozenInstanceError): valid.language='fr'
        core.validate_config(b'entrypoint="main.typ"\n'+CONFIG,'main.typ')
        invalid=(None,True,bytearray(CONFIG),b'',CONFIG.replace(b'pde',b'core'),CONFIG.replace(b'main.typ',b'*.typ'),
            CONFIG.replace(b'"en"',b'"EN"'),CONFIG.replace(b'"en"',b'"en-US"'),CONFIG.replace(b'"en"',b'"fr"'),
            CONFIG.replace(b'language="en"',b'language=true'),CONFIG.replace(b'language="en"',b''),
            b'entrypoint="other.typ"\n'+CONFIG,b'exclude=[]\n'+CONFIG,CONFIG+b'unknown=1\n',
            CONFIG.replace(b'["main.typ"]',b'["main.typ","other.typ"]'),CONFIG+b'language="en"\n')
        for raw in invalid:
            with self.subTest(raw=raw),self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.validate_config(raw,'main.typ')
        for path in ('../main.typ','/main.typ','*.typ','a?.typ','a[1].typ','a{b}.typ','main.md','main.typ\n',True):
            with self.subTest(path=path),self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.validate_config(CONFIG,path)
    def test_package_manifest_config_and_catalog_mismatches_refuse(self):
        package=self.authority['package_source']
        alternatives=(
            dict(package_source=replace(package,source=package.source+b'\n')),
            dict(package_source=replace(package,source_identity='text-utils/0.1.2/lib.typ')),
            dict(package_source=replace(package,source_identity=str(ROOT)+'/local/text-utils/0.1.3/lib.typ')),
            dict(package_source=replace(package,import_path='@local/text-utils:0.1.3')),
            dict(package_source=replace(package,artifacts=(object(),))),
            dict(package_source=object()),dict(manifest_bytes=self.authority['manifest_bytes']+b'\n'),
            dict(catalog_bytes=self.authority['catalog_bytes']+b'\n'),dict(config_bytes=CONFIG.replace(b'en',b'fr')),
            dict(package_tree=parse(b'#let fake=1')),dict(source_profile='captured-relative-core-v1'),dict(source_profile=True),
        )
        for authority in alternatives:
            with self.subTest(authority=authority): self.refuse(raw=self.source(),**authority)
    def test_package_identity_is_explicit_not_selected_by_proof(self):
        tree,proof=self.admit()
        selected=self.authority|dict(package_source=replace(self.authority['package_source'],source_identity='/other/local/text-utils/0.1.2/lib.typ'))
        other=core.certify(tree,'main.typ',**selected)
        self.assertNotEqual(proof.package.source_identity,other.package.source_identity)
        with self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.verify_proof(proof,tree,'main.typ',**selected)
    def test_proof_fields_are_private_frozen_and_all_rederived(self):
        tree,proof=self.admit()
        with self.assertRaises(TypeError): core.CapturedTextUtilsParagraphProof()
        with self.assertRaises(FrozenInstanceError): proof.target_path='other.typ'
        for fake in (True,{},object.__new__(core.CapturedTextUtilsParagraphProof)):
            with self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.verify_proof(fake,tree,'main.typ',**self.authority)
        for f in fields(proof):
            if f.name=='_issuer': continue
            value=getattr(proof,f.name)
            bad=(() if value else (True,)) if type(value) is tuple else (True if type(value) is int else 'altered')
            with self.subTest(field=f.name),self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.verify_proof(fake_proof(proof,f.name,bad),tree,'main.typ',**self.authority)
    def test_nested_bool_int_missing_and_oversized_proof_tampering_is_refused(self):
        tree,proof=self.admit()
        p=proof.paragraphs[0]
        for bad in (replace(p,ordinal=False),replace(p,first_span=(True,p.first_span[1])),replace(p,content_nodes=(1,)*100001)):
            with self.subTest(bad=bad),self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.verify_proof(fake_proof(proof,'paragraphs',(bad,)),tree,'main.typ',**self.authority)
        context=replace(proof.contexts[0],body_start=False)
        with self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.verify_proof(fake_proof(proof,'contexts',(context,)),tree,'main.typ',**self.authority)
        fake=fake_proof(proof,'paragraphs',proof.paragraphs)
        object.__delattr__(fake,'tree_sha256')
        with self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.verify_proof(fake,tree,'main.typ',**self.authority)
    def test_source_tree_and_all_parser_metadata_are_bound(self):
        tree,proof=self.admit('\n#paragraph_tab\ntherefore $x^(2)$ text.\n')
        variants=(replace(tree,source=bytearray(tree.source)),replace(tree,nodes=list(tree.nodes)),replace(tree,mode='math'),
            changed(tree,0,start=False),changed(tree,0,children=(1,)*100001),changed(tree,1,errors=0),
            changed(tree,1,operands=(('top',1),)*100001),changed(tree,1,display=False),changed(tree,1,start=True))
        for altered in variants:
            with self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.certify(altered,'main.typ',**self.authority)
        unknown=next(i for i,n in enumerate(tree.nodes) if n.kind=='Text')
        altered=changed(tree,unknown,kind='UnknownToken')
        with self.assertRaises(core.CapturedTextUtilsParagraphSyntaxError): core.certify(altered,'main.typ',**self.authority)
        for raw in (tree.source.replace(b'text.',b'evil.'),tree.source.replace(b'x^(2)',b'y^(2)')):
            with self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.verify_proof(proof,parse(raw),'main.typ',**self.authority)
    def test_missing_tree_or_node_fields_are_typed_refusals(self):
        for target,field in (('tree','source'),('tree','mode'),('tree','nodes'),
            ('node','kind'),('node','start'),('node','end'),('node','children'),('node','errors'),('node','operands'),('node','display')):
            tree=parse(self.source())
            object.__delattr__(tree if target=='tree' else tree.nodes[1],field)
            with self.subTest(field=field),self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.certify(tree,'main.typ',**self.authority)
    def test_candidate_cannot_change_prose_math_marker_or_noop_source(self):
        tree,proof=self.admit('\n#paragraph_tab\ntherefore $x^(2)$ text.\n')
        candidates=(tree.source,tree.source.replace(b'therefore',b'THEREFORE'),
            tree.source.replace(b'therefore',b'Therefore').replace(b'text.',b'evil.'),
            tree.source.replace(b'therefore',b'Therefore').replace(b'x^(2)',b'y^(2)'),
            tree.source.replace(b'therefore',b'Therefore')+b' ')
        for raw in candidates:
            with self.subTest(raw=raw):
                after=parse(raw); checked=core.certify(after,'main.typ',**self.authority)
                with self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.compare_candidate(proof,checked,before_tree=tree,after_tree=after,path='main.typ',**self.authority)
    def test_boundary_proof_tampering_and_unmarked_boundaries_refuse(self):
        tree,proof=self.admit('\nInitial prose.\n\n#paragraph_tab\ntherefore one.\n\n#paragraph_tab\nThe other.\n')
        for field,value in (('boundaries',()),('boundaries',(replace(proof.boundaries[0],previous_paragraph=True),)+proof.boundaries[1:])):
            with self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.verify_proof(fake_proof(proof,field,value),tree,'main.typ',**self.authority)
        with self.assertRaises(core.CapturedTextUtilsParagraphSyntaxError): core.analyze_boundaries(parse(tree.source.replace(b'#paragraph_tab\nThe other.',b'The other.')),'main.typ',**self.authority)
    def test_fixed_paragraph_and_byte_budgets(self):
        body=''.join('\n#paragraph_tab\nThe sentence.\n' for _ in range(core.MAX_PARAGRAPHS))
        _,proof=self.admit(body)
        self.assertEqual(len(proof.paragraphs),core.MAX_PARAGRAPHS)
        self.refuse(body+'\n#paragraph_tab\nThe last.\n',error=core.CapturedTextUtilsParagraphAuthorityError)
        with self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.validate_config(CONFIG+b' '*(core.MAX_CONFIG_BYTES+1),'main.typ')
        tree=parse(self.source())
        with self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.certify(replace(tree,source=b'x'*(1048576+1)),'main.typ',**self.authority)
    def test_pure_core_never_parses_or_loads_live_catalog(self):
        tree=parse(self.source())
        with (patch('syntax_client.parse',side_effect=AssertionError('parser execution')),
              patch('typst_lint.load_rules',side_effect=AssertionError('live catalog read'))):
            core.certify(tree,'main.typ',**self.authority)

if __name__=='__main__': unittest.main()
