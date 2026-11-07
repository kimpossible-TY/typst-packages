"""Pinned-parser, pure repair-admission/byte-ownership tests, no compiler/IO authority."""
from dataclasses import dataclass, fields, replace
from pathlib import Path
import gc
import weakref
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import captured_text_utils_paragraph_core as core
from candidate_convergence import AttributedEdit, Candidate, Contribution, Step, converge
from edit_plan import Edit, plan
from static_resolver import PackageSource
from syntax_client import EXECUTABLE, parse

ROOT=Path(__file__).resolve().parents[3]
PREFIX=b'#import "@local/text-utils:0.1.2": paragraph_tab, apply-paragraph-tabs\n#show: apply-paragraph-tabs\n'
CONFIG=b'rule-set="pde"\ninclude=["main.typ"]\n[capitalization]\nlanguage="en"\n'

@dataclass(frozen=True)
class Result:
    diagnostics: tuple=()
    edits: tuple=()
    skips: tuple=()
    parse_errors: tuple=()
    suppressed: tuple=()

@unittest.skipUnless(EXECUTABLE.is_file(),'Build the explicitly pinned parser first')
class ParagraphBoundaryCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lib=ROOT/'local/text-utils/0.1.2/lib.typ'
        cls.authority=dict(config_bytes=CONFIG,
            catalog_bytes=(ROOT/'tools/typst-lint/rules/catalog.json').read_bytes(),
            package_source=PackageSource(core.IMPORT_PATH,str(lib),lib.read_bytes()),
            manifest_bytes=lib.with_name('typst.toml').read_bytes(),package_tree=parse(lib.read_bytes()))
    def admit(self,body,*,raw=None):
        tree=parse(PREFIX+body if raw is None else raw)
        proof=core.certify_repair(tree,'main.typ',**self.authority)
        core.verify_repair_proof(proof,tree,'main.typ',**self.authority)
        return tree,proof
    def converge(self,body,*,raw=None):
        tree,initial=self.admit(body,raw=raw)
        pairs=[]
        def analyze(t):
            proof=core.certify_repair(t,'main.typ',**self.authority)
            by_rule={rid:[] for rid in core.EVALUATED_RULE_IDS}
            for r in proof.repairs: by_rule[r.rule_id].append(Edit(r.start,r.end,r.replacement))
            for p in proof.paragraphs:
                if p.edit_start is not None:
                    by_rule['pde.paragraph-capitalization'].append(Edit(p.edit_start,p.edit_start+1,p.replacement))
            return {rid:Result(edits=tuple(edits)) for rid,edits in by_rule.items()}
        def step(old,old_results,new,new_results,record):
            before=core.certify_repair(old,'main.typ',**self.authority)
            after=core.certify_repair(new,'main.typ',**self.authority)
            core.compare_repair_step(before,after,before_tree=old,after_tree=new,path='main.typ',step=record,**self.authority)
            pairs.append((old,new))
        def final(candidate):
            clean=core.certify(candidate.final_tree,'main.typ',**self.authority)
            core.compare_repair_candidate(initial,clean,before_tree=tree,after_tree=candidate.final_tree,
                path='main.typ',candidate=candidate,step_trees=tuple(pairs),**self.authority)
        candidate=converge(tree,analyze(tree),analyze,step,final,max_passes=2)
        clean=core.certify(candidate.final_tree,'main.typ',**self.authority)
        return tree,initial,candidate,clean,tuple(pairs)
    def test_missing_gap_repairs_existing_marker_then_existing_casing_in_one_pass(self):
        tree,initial,candidate,clean,pairs=self.converge(b'First idea.\n#paragraph_tab\ntherefore $x^(2)$ follows.\n')
        self.assertEqual(candidate.content,PREFIX+b'First idea.\n\n#paragraph_tab\nTherefore $x^(2)$ follows.\n')
        self.assertEqual(candidate.passes,1)
        self.assertEqual([(r.rule_id,r.replacement) for r in initial.repairs],[('pde.paragraph-marker-gap',b'\n')])
        self.assertEqual({i.primary_rule_id for i in candidate.steps[0].applied},
                         {'pde.paragraph-marker-gap','pde.paragraph-capitalization'})
        self.assertFalse(clean.paragraphs[0].marker_nodes)
    def test_zero_original_markers_get_real_marker_then_separate_case_pass(self):
        tree,initial,candidate,clean,pairs=self.converge(b'First idea.\n\nthis consequence follows.\n')
        inserted=PREFIX+b'First idea.\n\n#paragraph_tab\nthis consequence follows.\n'
        self.assertEqual(candidate.steps[0].after,inserted)
        self.assertEqual(candidate.content,inserted.replace(b'this consequence',b'This consequence'))
        self.assertEqual(candidate.passes,2)
        self.assertEqual(candidate.steps[0].applied[0].primary_rule_id,'pde.prose-paragraph-marker')
        self.assertEqual(candidate.steps[1].applied[0].primary_rule_id,'pde.paragraph-capitalization')
        self.assertFalse(initial.contexts)
        self.assertEqual(len(clean.contexts),1)
        self.assertEqual(candidate.edits[0].edit,Edit(tree.source.index(b'this'),tree.source.index(b'this')+1,b'#paragraph_tab\nT'))
    def test_mixed_crlf_bom_hspace_math_preserves_every_other_byte(self):
        raw=b'\xef\xbb\xbf'+PREFIX.replace(b'\n',b'\r\n')+(b'iPhone stays lowercase.\r\n \t#paragraph_tab \t\r\n \ttherefore $x^(i)+y_(2)$ follows.\r\n\t\r\n \tthis consequence uses eBay.\r\n\r\n#paragraph_tab\r\nThe final remains.\r\n')
        tree,initial,candidate,clean,pairs=self.converge(b'',raw=raw)
        expected=raw.replace(b'\r\n \t#paragraph_tab',b'\r\n\r\n \t#paragraph_tab',1).replace(b'\r\n \tthis',b'\r\n#paragraph_tab\r\n \tThis').replace(b'therefore',b'Therefore')
        self.assertEqual(candidate.content,expected)
        self.assertEqual([r.replacement for r in initial.repairs],[b'\r\n',b'#paragraph_tab\r\n'])
        self.assertTrue(candidate.content.startswith(b'\xef\xbb\xbf'))
        self.assertIn(b'iPhone stays lowercase.',candidate.content)
        self.assertEqual(candidate.passes,2)
        # An indented target retains two independent minimal baseline edits.
        marker=next(i for i in candidate.edits if i.primary_rule_id=='pde.prose-paragraph-marker')
        self.assertEqual(marker.edit.start,marker.edit.end)
        self.assertEqual(marker.edit.replacement,b'#paragraph_tab\r\n')
        mapping=dict(core.compare_repair_candidate(initial,clean,before_tree=tree,after_tree=candidate.final_tree,
            path='main.typ',candidate=candidate,step_trees=pairs,**self.authority))
        for i,n in enumerate(tree.nodes):
            if n.kind in ('Equation','MathAttach'):
                self.assertIn(i,mapping)
                self.assertEqual(n.kind,candidate.final_tree.nodes[mapping[i]].kind)
    def test_first_marked_paragraph_missing_gap_after_show_is_repairable(self):
        tree,initial,candidate,clean,pairs=self.converge(b'\t#paragraph_tab\ntherefore text.\n')
        self.assertEqual(initial.repairs[0].start,len(PREFIX))
        self.assertEqual(candidate.content,PREFIX+b'\n\t#paragraph_tab\nTherefore text.\n')
        self.assertEqual(candidate.passes,1)
    def test_mixed_newlines_use_each_owned_boundary_terminator(self):
        raw=PREFIX+b'First.\n \t#paragraph_tab\r\nThe second.\r\n\r\n\tThis third.\n'
        _,proof,candidate,_,_=self.converge(b'',raw=raw)
        self.assertEqual([r.replacement for r in proof.repairs],[b'\r\n',b'#paragraph_tab\r\n'])
        self.assertEqual(candidate.passes,1)
        self.assertIn(b'First.\n\r\n \t#paragraph_tab\r\n',candidate.content)
    def test_first_unmarked_paragraph_is_exempt_and_clean_proofs_unchanged(self):
        _,initial,candidate,clean,_=self.converge(b'unknown initial remains.\n\nThe second paragraph.\n')
        self.assertEqual(candidate.content,PREFIX+b'unknown initial remains.\n\n#paragraph_tab\nThe second paragraph.\n')
        self.assertEqual(initial.paragraphs[0].first_word,b'')
        self.assertIsNone(clean.paragraphs[0].edit_start)
        self.assertEqual(candidate.passes,1)
        self.assertEqual(clean.grammar_version,core.GRAMMAR_VERSION)
        self.assertEqual(type(clean),core.CapturedTextUtilsParagraphProof)
        self.assertEqual(initial.grammar_version,core.REPAIR_GRAMMAR_VERSION)
        self.assertNotEqual(initial.grammar_version,clean.grammar_version)
        with self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.verify_proof(initial,candidate.final_tree,'main.typ',**self.authority)
        tree=parse(PREFIX+b'\n#paragraph_tab\nThe second paragraph.\n')
        canonical=core.certify(tree,'main.typ',**self.authority)
        repaired=core.certify_repair(tree,'main.typ',**self.authority)
        self.assertFalse(repaired.repairs)
        self.assertEqual(canonical.contexts,repaired.contexts)
        clean_reasons={o.rule_id:o.reason for o in canonical.obligations}
        repair_reasons={o.rule_id:o.reason for o in repaired.obligations}
        self.assertIn('preceding blank line',clean_reasons['pde.paragraph-marker-gap'])
        self.assertIn('recorded missing-blank insertions',repair_reasons['pde.paragraph-marker-gap'])
        self.assertIn('recorded missing-marker insertions',repair_reasons['pde.prose-paragraph-marker'])
        self.assertEqual(len(repaired.obligations),33)
        self.assertEqual((len(repaired.evaluated_rule_ids),len(repaired.not_applicable_rule_ids)),(5,28))
    def test_dirty_admission_never_weakens_clean_certify(self):
        for body in (b'#paragraph_tab\ntherefore text.\n',b'First.\n\nthis follows.\n',b'First.\n#paragraph_tab\nThe second.\n'):
            tree,_=self.admit(body)
            with self.assertRaises(core.CapturedTextUtilsParagraphSyntaxError): core.certify(tree,'main.typ',**self.authority)
        with self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.certify(parse(PREFIX+b'First.\n\nthis follows.\n'),'main.typ',repair=True,**self.authority)
    def test_existing_exclusions_and_ambiguous_boundaries_remain_refusals(self):
        bodies=(b'Only first paragraph.\n',b'',b'First.\nthis follows.\n',
            b'First.\n\nunknown follows.\n',b'First.\n\niPhone follows.\n',b'First.\n\nThis, follows.\n',
            b'First.\n\n#block[this follows.]\n',b'First.\n\n#parbreak()\nthis follows.\n',
            b'First.\n\nthis #footnote[note] follows.\n',b'First.\n\nthis $ x=2 $ follows.\n',
            b'First.\n\nthis $x_i$ follows.\n',b'First.\n\nthis $cancel(x)$ follows.\n',
            b'First.\n\n// comment\nthis follows.\n',b'First.\n\n#paragraph_tab\n\nthis follows.\n',
            b'First.\n#paragraph_tab\n#paragraph_tab\nthis follows.\n',b'First.\n\n# paragraph_tab\nthis follows.\n',
            b'First.\n\n#paragraph_tab()\nthis follows.\n',b'First.\n\n$this$ follows.\n')
        for body in bodies:
            with self.subTest(body=body),self.assertRaises(core.CapturedTextUtilsParagraphError): self.admit(body)
    def test_all_repair_proof_data_rederived_and_equal_copies_unissued(self):
        tree,proof=self.admit(b'First.\n\nthis follows.\n')
        copied=object.__new__(core.CapturedTextUtilsParagraphRepairProof)
        for f in fields(proof): object.__setattr__(copied,f.name,getattr(proof,f.name))
        with self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.verify_repair_proof(copied,tree,'main.typ',**self.authority)
        for field,value in (('repairs',(replace(proof.repairs[0],start=True),)),
            ('repairs',proof.repairs*100001),('repairs',(proof,)),('paragraphs',(proof,)),
            ('grammar_version',core.GRAMMAR_VERSION),('nodes',()),('boundaries',()),('contexts',(True,)),
            ('obligations',proof.obligations[:-1]),('source_sha256','0'*64)):
            original=getattr(proof,field)
            try:
                object.__setattr__(proof,field,value)
                with self.subTest(field=field),self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.verify_repair_proof(proof,tree,'main.typ',**self.authority)
            finally: object.__setattr__(proof,field,original)
        core.verify_repair_proof(proof,tree,'main.typ',**self.authority)
    def test_issued_registry_uses_identity_without_hash_and_expires_even_self_cycles(self):
        tree=parse(PREFIX+b'First.\n\nthis follows.\n')
        # Tree parsing is already complete. Issuance, verification and lifetime
        # tracking below must not parse, hash a mutable proof or retain it.
        with (patch.object(core.CapturedTextUtilsParagraphRepairProof,'__hash__',
                           side_effect=AssertionError('dataclass proof hash')) as hashed,
              patch('syntax_client.parse',side_effect=AssertionError('parser'))):
            proof=core.certify_repair(tree,'main.typ',**self.authority)
            identity=id(proof); reference=weakref.ref(proof)
            self.assertIs(core._REPAIR_PROOFS.get(identity),proof)
            core.verify_repair_proof(proof,tree,'main.typ',**self.authority)
            del proof
            gc.collect()
            self.assertIsNone(reference())
            self.assertNotIn(identity,core._REPAIR_PROOFS)
            cyclic=core.certify_repair(tree,'main.typ',**self.authority)
            identity=id(cyclic); reference=weakref.ref(cyclic)
            object.__setattr__(cyclic,'repairs',(cyclic,))
            with self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError):
                core.verify_repair_proof(cyclic,tree,'main.typ',**self.authority)
            del cyclic
            gc.collect()
            self.assertIsNone(reference())
            self.assertNotIn(identity,core._REPAIR_PROOFS)
            hashed.assert_not_called()
    def test_step_rejects_same_pass_insert_plus_new_case_wrong_rules_moves_and_duplicates(self):
        tree,initial,candidate,clean,pairs=self.converge(b'First.\n\nthis follows.\n')
        step=candidate.steps[0]; after=pairs[0][1]; admitted=core.certify_repair(after,'main.typ',**self.authority)
        item=step.applied[0]
        variants=(replace(step,moves=((0,1,2),)),replace(step,deferred=(item,)),replace(step,applied=(item,item)),
            replace(step,applied=(replace(item,primary_rule_id='pde.paragraph-capitalization'),)),
            replace(step,applied=(replace(item,contributions=(replace(item.contributions[0],pass_number=True),)),)),
            replace(step,applied=(replace(item,contributions=item.contributions*2),)),
            replace(step,applied=(replace(item,edit=Edit(item.edit.start,item.edit.end,b'#paragraph_tab \n')),)))
        for bad in variants:
            with self.subTest(bad=bad),self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.compare_repair_step(initial,admitted,before_tree=tree,after_tree=after,path='main.typ',step=bad,**self.authority)
        final=candidate.final_tree
        final_repair=core.certify_repair(final,'main.typ',**self.authority)
        with self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.compare_repair_step(initial,final_repair,before_tree=tree,after_tree=final,path='main.typ',**self.authority)
    def test_candidate_rejects_forged_maps_history_copies_and_widened_equal_copy_envelopes(self):
        tree,initial,candidate,clean,pairs=self.converge(b'First.\n\nthis follows.\n')
        def check(value,history=pairs):
            core.compare_repair_candidate(initial,clean,before_tree=tree,after_tree=candidate.final_tree,
                path='main.typ',candidate=value,step_trees=history,**self.authority)
        item=candidate.edits[0]
        wider=replace(item,edit=Edit(0,item.edit.end,tree.source[:item.edit.start]+item.edit.replacement))
        variants=(replace(candidate,passes=True),replace(candidate,steps=candidate.steps*2),
            replace(candidate,steps=(candidate.steps[1],candidate.steps[0])),replace(candidate,edits=(wider,)),
            replace(candidate,edits=(replace(item,contributions=item.contributions[:1]),)),
            replace(candidate,edits=candidate.edits*100001),replace(candidate,baseline=tree.source+b' '),
            replace(candidate,final_tree=tree))
        for value in variants:
            with self.subTest(value=value.passes),self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): check(value)
        for history in (pairs*2,(pairs[1],pairs[0]),((tree,tree),pairs[1]),((True,False),)):
            with self.subTest(history=len(history)),self.assertRaises(core.CapturedTextUtilsParagraphError): check(candidate,history)
        with self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): check(None,())
        with patch.object(Candidate,'map_offset',side_effect=AssertionError('caller map')):
            with patch.object(Step,'map_offset',side_effect=AssertionError('caller map')): check(candidate)
    def test_changed_original_bytes_math_statements_and_extra_marker_cannot_pass(self):
        tree,initial,candidate,clean,pairs=self.converge(b'First.\n\nthis $x^(i)$ follows.\n')
        for raw in (candidate.content.replace(b'First',b'Worst'),candidate.content.replace(b'x^(i)',b'y^(i)'),
                    candidate.content.replace(b'This',b'THIS'),candidate.content+b' ',
                    candidate.content.replace(b'#paragraph_tab\nThis',b'#paragraph_tab\nThe extra.\n\n#paragraph_tab\nThis')):
            altered=parse(raw)
            final=core.certify(altered,'main.typ',**self.authority)
            with self.subTest(raw=raw),self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.compare_repair_candidate(initial,final,before_tree=tree,after_tree=altered,path='main.typ',candidate=candidate,step_trees=pairs,**self.authority)
    def test_fixed_paragraph_edit_history_and_growth_budgets(self):
        body=b'First.\n'+b'\nthis follows.\n'*(core.MAX_PARAGRAPHS-1)
        tree,proof=self.admit(body)
        self.assertEqual((len(proof.paragraphs),len(proof.repairs)),(256,255))
        with self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): self.admit(body+b'\nthis exceeds.\n')
        with self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core._replay_repairs(b'x'*1048576,(core.Repair('pde.paragraph-marker-gap',0,0,0,b'\n'),))
        with self.assertRaises(core.CapturedTextUtilsParagraphAuthorityError): core.verify_repair_proof(proof,tree,'main.typ',**(self.authority|{'catalog_bytes':self.authority['catalog_bytes']+b' '}))
    def test_pure_proof_and_comparison_never_parse_or_load_catalog(self):
        tree,initial,candidate,clean,pairs=self.converge(b'First.\n\nthis $x^(i)$ follows.\n')
        with (patch('syntax_client.parse',side_effect=AssertionError('parser')),
              patch('candidate_convergence.parse',side_effect=AssertionError('parser')),
              patch('typst_lint.load_rules',side_effect=AssertionError('catalog IO'))):
            core.certify_repair(tree,'main.typ',**self.authority)
            core.compare_repair_candidate(initial,clean,before_tree=tree,after_tree=candidate.final_tree,
                path='main.typ',candidate=candidate,step_trees=pairs,**self.authority)

if __name__=='__main__': unittest.main()
