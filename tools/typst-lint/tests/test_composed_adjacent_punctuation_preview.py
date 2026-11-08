"""Explicit punctuation moves remain atomic and replay-checked in compile preview."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_adjacent_punctuation_preview as adjacent
import document_sequence_preview as seq
from syntax_client import EXECUTABLE, parse
from typst_lint import SafetyError, load_rules
import test_document_preview_cli as fixtures


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ComposedAdjacentPunctuationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_rules()[0]['rules']

    def test_profiles_replay_atomic_pairs_and_selection(self):
        source = b'#block[This holds#footnote[Reason.]. It follows#footnote[Why.], yes.]'
        expected = b'#block[This holds.#footnote[Reason.] It follows,#footnote[Why.] yes.]'
        for profile in seq._VERIFICATIONS:
            for continuations in (False, True):
                with self.subTest(profile=profile, continuations=continuations):
                    tree = parse(source)
                    record = seq.preview(tree, 'main.typ', self.catalog, profile=profile,
                        conditional_continuations=continuations, conditional_adjacent_punctuation=True)
                    self.assertNotEqual(record['verification'], 'withheld', record)
                    step = next(x for x in record['steps'] if x.get('atomic_edits'))
                    self.assertEqual(len(step['edits']), 4)
                    self.assertEqual(seq.replay(tree, 'main.typ', self.catalog, record, profile=profile,
                        conditional_continuations=continuations, conditional_adjacent_punctuation=True), expected)
                    with self.assertRaises(SafetyError):
                        seq.replay(tree, 'main.typ', self.catalog, record, profile=profile,
                                   conditional_continuations=continuations)
                    for action in ('delete', 'conditions', 'atomic', 'authority'):
                        forged = deepcopy(record)
                        row = next(x for x in forged['steps'] if x.get('atomic_edits'))
                        if action == 'delete': row['edits'].pop()
                        elif action == 'conditions': row['edits'][0]['conditions'] = ['forged']
                        elif action == 'atomic': row['atomic_edits'] = False
                        else: row['edits'][0]['compiler_permission'] = True
                        with self.assertRaises(SafetyError):
                            seq.replay(tree, 'main.typ', self.catalog, forged, profile=profile,
                                conditional_continuations=continuations, conditional_adjacent_punctuation=True)

    def test_suppression_ambiguous_and_existing_steps(self):
        cases = (b'#block[This holds.#footnote[Reason.]]', b'#block[This holds#footnote[Reason.]!]',
                 b'// typst-lint: ignore-next pde.prose-footnote-punctuation -- intentional\n'
                 b'#block[This holds#footnote[Reason.].]')
        for source in cases:
            with self.subTest(source=source):
                record = seq.preview(parse(source), 'main.typ', self.catalog, conditional_adjacent_punctuation=True)
                self.assertTrue(record is None or all(not x.get('atomic_edits') for x in record['steps']), record)
        source = b'#block[This holds#footnote[Reason.]. $x^i$]'
        record = seq.preview(parse(source), 'main.typ', self.catalog, conditional_adjacent_punctuation=True)
        self.assertNotEqual(record['verification'], 'withheld', record)
        self.assertGreaterEqual(len(record['steps']), 2)
        self.assertEqual(record['steps'][-1]['rule_id'], adjacent.RULE)

    def test_unresolved_paragraph_boundary_withholds_file(self):
        source = b'#block[\n#paragraph_tab\nNext.]\n#block[This holds#footnote[Reason.].]'
        record = seq.preview(parse(source), 'main.typ', self.catalog, conditional_adjacent_punctuation=True)
        self.assertEqual(record['verification'], 'withheld')
        self.assertFalse(record['steps'])

    def test_forged_proof_and_omitted_candidates_withhold(self):
        source = b'#block[This holds#footnote[Reason.].]'
        original = adjacent.preview
        for mode in ('omit', 'pair', 'conditions'):
            def forged(tree, path, **kwargs):
                value = original(tree, path, **kwargs)
                if mode == 'omit': return None
                if value is not None:
                    if mode == 'pair': value['edits'].pop()
                    else: value['conditions'] = ('forged',)
                return value
            with self.subTest(mode=mode), patch.object(adjacent, 'preview', side_effect=forged):
                record = seq.preview(parse(source), 'main.typ', self.catalog, conditional_adjacent_punctuation=True)
                self.assertEqual(record['verification'], 'withheld', record)
                self.assertFalse(record['steps'])


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ComposedAdjacentPunctuationCliTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DocumentPreviewCliTests('runTest'); self.f.setUp(); self.addCleanup(self.f.doCleanups)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and sys.platform == 'darwin',
                         'Opt in on macOS with reviewed Typst')
    def test_actual_compile_and_original_unchanged(self):
        source = b'#block[This holds#footnote[Reason.].]'; path = self.f.project / 'main.typ'; path.write_bytes(source)
        result = subprocess.run(self.f.command('--conditional-adjacent-punctuation', '--conditional-continuations',
                                '--review-edits', '--format', 'json'), capture_output=True, timeout=60)
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2, report)
        self.assertTrue(report['compiler_invoked'] and report['pdf_generated'])
        summary = report['conditional_adjacent_punctuation_preview']
        self.assertEqual((summary['planned_moves'], summary['planned_edits']), (1, 2))
        self.assertTrue(summary['atomic_pairs'])
        self.assertFalse(report['source_apply_gate']['eligible'])
        self.assertEqual(path.read_bytes(), source)
