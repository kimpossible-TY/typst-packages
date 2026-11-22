"""Independent capitalization-stage proof; no compile or write authority."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sys
import os
import json
import subprocess
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import document_composed_capitalization as subject
import document_sequence_preview as sequence
from syntax_client import EXECUTABLE, parse
from typst_lint import SafetyError, load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class CapitalizationCompositionTests(unittest.TestCase):
    catalog = load_rules()[0]['rules']

    def check(self, source, spelling='underscore'):
        return subject.preview(parse(source), 'main.typ', self.catalog, spelling=spelling)

    def test_each_spelling_is_a_hash_bound_conditional_stage(self):
        for spelling, token in [('underscore',b'paragraph_tab'),('hyphen',b'paragraph-tab')]:
            with self.subTest(spelling=spelling):
                row=self.check(b'#block[Previous sentence.\n\n#'+token+b'\ntherefore x.]',spelling)
                self.assertEqual(row['edits'][0]['replacement'],'T')
                self.assertFalse(row['edits'][0]['edit_permission'])
                self.assertFalse(row['edits'][0]['render_equivalence_proved'])

    def test_suppression_and_explicit_shadow_withhold(self):
        for source,spelling in [
            (b'#let paragraph_tab = 1\n#block[\n#paragraph_tab\ntherefore x.]','underscore'),
            (b'#for paragraph_tab in (1,) [\n#paragraph_tab\ntherefore x.]','underscore'),
            (b'#let paragraph-tab = 1\n#block[\n#paragraph-tab\ntherefore x.]','hyphen'),
            (b'#block[\n// typst-lint: ignore-next pde.paragraph-capitalization -- intended\n#paragraph_tab\ntherefore x.]','underscore')]:
            with self.subTest(source=source): self.assertIsNone(self.check(source,spelling))

    def test_opt_in_sequence_replays_and_rejects_tampering(self):
        source = b'#block[Previous sentence.\n\n#paragraph_tab\ntherefore x.]'
        tree = parse(source)
        self.assertIsNone(sequence.preview(tree, 'main.typ', self.catalog))
        record = sequence.preview(tree, 'main.typ', self.catalog, conditional_capitalization=True)
        self.assertEqual(sequence.replay(tree, 'main.typ', self.catalog, record,
                         conditional_capitalization=True), source.replace(b'therefore', b'Therefore'))
        tampered = deepcopy(record)
        tampered['steps'][0]['edits'][0]['replacement'] = 'X'
        with self.assertRaises(SafetyError):
            sequence.replay(tree, 'main.typ', self.catalog, tampered, conditional_capitalization=True)
        with self.assertRaises(SafetyError):
            sequence.replay(tree, 'main.typ', self.catalog, record)

    def test_first_marker_requires_supported_earlier_gap(self):
        source=b'#block[\n#paragraph_tab\ntherefore x.]'
        tree=parse(source)
        self.assertIsNone(sequence.preview(tree, 'main.typ', self.catalog, conditional_capitalization=True))
        row=sequence.preview(tree, 'main.typ', self.catalog, profile='conditional-gaps', conditional_capitalization=True)
        self.assertEqual([stage['rule_id'] for stage in row['steps']],
                         ['pde.paragraph-marker-gap','pde.paragraph-capitalization'])
        self.assertIn(b'Therefore',sequence.replay(tree,'main.typ',self.catalog,row,
                      profile='conditional-gaps',conditional_capitalization=True))

    def test_self_consistent_forged_stage_is_rejected(self):
        source=b'#block[Previous sentence.\n\n#paragraph_tab\ntherefore x.]'
        start=source.index(b'Previous')
        forged=dict(rule_id='pde.paragraph-capitalization', baseline_sha256=subject.digest(source),
            candidate_sha256=subject.digest(source[:start]+b'X'+source[start+1:]),
            edits=[dict(start=start,end=start+1,replacement='X',rule_id='pde.paragraph-capitalization')])
        with patch.object(subject,'preview',return_value=forged):
            record=sequence.preview(parse(source),'main.typ',self.catalog,conditional_capitalization=True)
        self.assertEqual(record['verification'],'withheld')
        self.assertFalse(record['steps'])

    def test_imported_marker_name_and_alias(self):
        suffix=b'#block[Previous sentence.\n\n#paragraph_tab\ntherefore x.]'
        self.assertIsNotNone(self.check(b'#import "styles.typ": paragraph_tab\n'+suffix))
        self.assertIsNotNone(self.check(b'#import "styles.typ": *\n'+suffix))
        self.assertIsNone(self.check(b'#import "styles.typ": other as paragraph_tab\n'+suffix))
        self.assertIsNone(self.check(b'#import "styles.typ" as paragraph_tab\n'+suffix))
        self.assertIsNone(self.check(b'#import "styles.typ" as paragraph_tab: *\n'+suffix))
        self.assertIsNone(self.check(b'#import "styles.typ" as foo: other as paragraph_tab\n'+suffix))
        self.assertIsNotNone(self.check(b'#import "styles.typ" as foo: paragraph_tab\n'+suffix))
        hyphen=b'#block[Previous sentence.\n\n#paragraph-tab\ntherefore x.]'
        self.assertIsNone(self.check(b'#import "styles.typ" as paragraph-tab: *\n'+hyphen,'hyphen'))
        self.assertIsNone(self.check(b'#import "styles.typ" as foo: other as paragraph-tab\n'+hyphen,'hyphen'))
        self.assertIsNotNone(self.check(b'#import "styles.typ" as foo: paragraph-tab\n'+hyphen,'hyphen'))

    def test_multiple_and_suppressed_candidates(self):
        source=(b'#block[Previous.\n\n// typst-lint: ignore-next pde.paragraph-capitalization -- intentional\n'
                b'#paragraph_tab\ntherefore x.\n\n#paragraph_tab\nhence y.]')
        row=self.check(source)
        self.assertIsNotNone(row)
        self.assertEqual(len(row['edits']),1)
        self.assertEqual(row['edits'][0]['replacement'],'H')

    def test_forged_common_analyzers_cannot_select_other_prose(self):
        for spelling, token in [('underscore',b'paragraph_tab'), ('hyphen',b'paragraph-tab')]:
            source=b'#block[previous prose.\n\n#'+token+b'\ntherefore x.]'
            previous=source.index(b'previous')
            if spelling == 'underscore':
                original=subject.paragraph_rules.analyze_conditional_nested_capitalization
                def forged(tree,*args,**kwargs):
                    result=original(tree,*args,**kwargs)
                    if result.diagnostics:
                        return replace(result,diagnostics=(replace(result.diagnostics[0],line=1,column=8),))
                    return result
                patcher=patch.object(subject.paragraph_rules,'analyze_conditional_nested_capitalization',side_effect=forged)
            else:
                original=subject.marker.analyze
                def forged(raw,*args,**kwargs):
                    result=original(raw,*args,**kwargs)
                    for row in result['diagnostics']:
                        if row['rule_id']=='pde.paragraph-capitalization':
                            row['start'],row['end']=previous,previous+1
                    return result
                patcher=patch.object(subject.marker,'analyze',side_effect=forged)
            with self.subTest(spelling=spelling), patcher:
                record=sequence.preview(parse(source),'main.typ',self.catalog,conditional_capitalization=True)
                self.assertEqual(record['verification'],'withheld')
                self.assertFalse(record['steps'])

    def test_budget_and_forged_source_proof(self):
        source=b'#block[Previous sentence.\n\n#paragraph_tab\ntherefore x.]'
        with patch.object(subject,'_within_budget',return_value=False):
            self.assertIsNone(self.check(source))
        real=subject.underscore.preview
        def forged(*args,**kwargs):
            row=real(*args,**kwargs)
            if row:
                row['edits'][0]['replacement']='X'
            return row
        with patch.object(subject.underscore,'preview',side_effect=forged):
            with self.assertRaises(SafetyError): self.check(source)


@unittest.skipUnless(EXECUTABLE.is_file() and os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                     and sys.platform == 'darwin', 'Opt in with reviewed compiler')
class CapitalizationCompileTests(unittest.TestCase):
    def test_cli_conditional_gate_and_source_preservation(self):
        import test_document_preview_cli as fixtures
        fixture=fixtures.DocumentPreviewCliTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        source=(b'#import "styles.typ": *\n'
                b'#block[Previous sentence.\n\n#paragraph_tab\ntherefore x.]')
        (fixture.project/'main.typ').write_bytes(source)
        (fixture.project/'styles.typ').write_bytes(b'#let paragraph_tab = []\n')
        fixture.value['files'].append(dict(root='project', path='styles.typ', role='source'))
        fixture.manifest.write_text(json.dumps(fixture.value), encoding='utf-8')
        run=subprocess.run(fixture.command('--conditional-capitalization','--review-edits','--format','json'),
                           capture_output=True, timeout=120)
        result=json.loads(run.stdout)
        self.assertEqual(run.returncode,2,result)
        self.assertTrue(result['pdf_generated'])
        self.assertEqual(result['conditional_capitalization_preview']['planned_edits'],1)
        self.assertFalse(result['source_apply_gate']['eligible'])
        self.assertEqual((fixture.project/'main.typ').read_bytes(),source)
