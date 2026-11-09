"""Conditional prose-marker composition cannot write consumer sources."""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import document_composed_prose_marker as subject
import document_sequence_preview as sequence
from syntax_client import EXECUTABLE,parse
from typst_lint import SafetyError,digest,load_rules


@unittest.skipUnless(EXECUTABLE.is_file(),'Requires pinned syntax adapter')
class ProseMarkerCompositionTests(unittest.TestCase):
    catalog=load_rules()[0]['rules']
    def sequence(self,source,**kwargs):
        return sequence.preview(parse(source),'main.typ',self.catalog,**kwargs)
    def test_opt_in_lf_crlf_and_replay(self):
        for nl in (b'\n',b'\r\n'):
            source=b'First idea.'+nl+nl+b'Second idea.'
            self.assertIsNone(self.sequence(source))
            result=self.sequence(source,conditional_prose_markers=True)
            self.assertEqual(result['steps'][-1]['edits'][0]['replacement'].encode(),b'#paragraph_tab'+nl)
            self.assertEqual(sequence.replay(parse(source),'main.typ',self.catalog,result,conditional_prose_markers=True),
                             source.replace(b'Second',b'#paragraph_tab'+nl+b'Second'))
            changed=deepcopy(result);changed['steps'][-1]['edits'][0]['replacement']='#evil()\n'
            with self.assertRaises(SafetyError):sequence.replay(parse(source),'main.typ',self.catalog,changed,conditional_prose_markers=True)
            with self.assertRaises(SafetyError):sequence.replay(parse(source),'main.typ',self.catalog,result)
    def test_literal_math_and_nested_ownership(self):
        for source in (b'#block[First $x$ idea.\n\nSecond idea.]', b'First $x$ idea.\n\nSecond idea.'):
            result=self.sequence(source,conditional_prose_markers=True)
            self.assertIsNotNone(result);self.assertEqual(result['steps'][-1]['rule_id'],'pde.prose-paragraph-marker')
    def test_nonprose_and_first_sentence_never_selected(self):
        for source in (b'First idea.', b'First idea.\n\n$ x $', b'First idea.\n\n= Heading',
                       b'First idea.\n\n- List item', b'First idea.\n\n`Raw`', b'First idea.\n\nsecond idea.'):
            with self.subTest(source=source): self.assertIsNone(self.sequence(source,conditional_prose_markers=True))
    def test_suppression_and_shadow_do_not_create_plan(self):
        for prefix in (b'#let paragraph_tab = 1\n',b'#import "styles.typ" as paragraph_tab: *\n',
                       b'#import "styles.typ" as foo: other as paragraph_tab\n'):
            self.assertIsNone(self.sequence(prefix+b'First idea.\n\nSecond idea.',conditional_prose_markers=True))
        src=(b'// typst-lint: ignore-next pde.prose-paragraph-marker -- intentional\n'
             b'#block[First idea.\n\nSecond idea.]')
        hint=subject.prose_marker_rules.analyze_conditional(parse(src),'main.typ',catalog=self.catalog)
        self.assertEqual(len(hint.suppressed),1)
        self.assertIsNone(self.sequence(src,conditional_prose_markers=True))
    def test_budget_skip_preserves_previous_stages(self):
        source=b'Thus $x^i$.\n\nFirst idea.\n\nSecond idea.'
        baseline=self.sequence(source)
        with patch.object(subject,'_within_budget',return_value=False):
            result=self.sequence(source,conditional_prose_markers=True)
        self.assertEqual(result['steps'],baseline['steps'])
    def test_forged_helper_and_common_hint_rejected(self):
        source=b'First idea.\n\nSecond idea.'; start=source.index(b'First')
        forged=dict(rule_id='pde.prose-paragraph-marker',baseline_sha256=digest(source),
                    candidate_sha256=digest(source[:start]+b'#paragraph_tab\n'+source[start:]),
                    edits=[dict(start=start,end=start,replacement='#paragraph_tab\n',rule_id='pde.prose-paragraph-marker')])
        with patch.object(subject,'preview',return_value=forged):
            self.assertEqual(self.sequence(source,conditional_prose_markers=True)['verification'],'withheld')
        orig=subject.prose_marker_rules.analyze_conditional
        def wrong(tree,*args,**kwargs):
            result=orig(tree,*args,**kwargs)
            if result.diagnostics:
                return replace(result,diagnostics=(replace(result.diagnostics[0],line=1,column=1),))
            return result
        with patch.object(subject.prose_marker_rules,'analyze_conditional',side_effect=wrong):
            self.assertEqual(self.sequence(source,conditional_prose_markers=True)['verification'],'withheld')
    def test_capitalization_after_insertion_is_composed(self):
        source=b'First idea.\n\nSecond idea.\n\n#block[Previous.\n\n#paragraph_tab\ntherefore x.]'
        result=self.sequence(source,conditional_prose_markers=True,conditional_capitalization=True)
        self.assertEqual([r['rule_id'] for r in result['steps']],['pde.prose-paragraph-marker','pde.paragraph-capitalization'])
        got=sequence.replay(parse(source),'main.typ',self.catalog,result,conditional_prose_markers=True,conditional_capitalization=True)
        self.assertIn(b'#paragraph_tab\nSecond',got);self.assertIn(b'Therefore',got)


@unittest.skipUnless(EXECUTABLE.is_file() and os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                     and sys.platform=='darwin','Opt in with reviewed compiler')
class ProseMarkerCompileTests(unittest.TestCase):
    def test_undefined_runtime_marker_fails_candidate_without_writing_source(self):
        import test_document_preview_cli as fixtures
        f=fixtures.DocumentPreviewCliTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        source=b'First idea.\n\nSecond idea.'
        (f.project/'main.typ').write_bytes(source)
        run=subprocess.run(f.command('--conditional-prose-markers','--format','json'),capture_output=True,timeout=120)
        result=json.loads(run.stdout)
        self.assertEqual(run.returncode,2,result)
        self.assertFalse(result['pdf_generated'])
        self.assertEqual(result['compiler_stage'],'candidate')
        self.assertIn('unknown variable: paragraph_tab',result['compiler_diagnostics'])
        self.assertFalse(result.get('source_apply_gate',{}).get('eligible',False))
        self.assertEqual((f.project/'main.typ').read_bytes(),source)

    def test_cli_gate_and_original_preservation(self):
        import test_document_preview_cli as fixtures
        f=fixtures.DocumentPreviewCliTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        source=b'#import "styles.typ": *\n\nFirst idea.\n\nSecond idea.'
        (f.project/'main.typ').write_bytes(source);(f.project/'styles.typ').write_bytes(b'#let paragraph_tab = h(1em)\n')
        f.value['files'].append(dict(root='project',path='styles.typ',role='source'))
        f.manifest.write_text(json.dumps(f.value),encoding='utf-8')
        run=subprocess.run(f.command('--conditional-prose-markers','--review-edits','--format','json'),capture_output=True,timeout=120)
        result=json.loads(run.stdout);self.assertEqual(run.returncode,2,result);self.assertTrue(result['pdf_generated'])
        self.assertEqual(result['conditional_prose_marker_preview']['planned_edits'],1)
        self.assertFalse(result['source_apply_gate']['eligible']);self.assertEqual((f.project/'main.typ').read_bytes(),source)
