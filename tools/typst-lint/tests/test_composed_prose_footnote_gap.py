"""Prose footnote gaps compose only with explicit conditional-gap selection."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import document_composed_prose_footnote_gap as composed
import document_nested_prose_footnote_gap as gap
import document_sequence_preview as sequence
import test_document_footnote_evidence as fixtures
import test_document_preview_cli as cli_fixtures
from syntax_client import EXECUTABLE, parse
from typst_lint import SafetyError, digest, load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ComposedProseFootnoteTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_rules()[0]['rules']

    def preview(self, source, **kwargs):
        return sequence.preview(parse(source), 'main.typ', self.catalog, **kwargs)

    def test_explicit_profile_replays_source_and_refuses_tampering(self):
        for nl in (b'\n', b'\r\n'):
            source = b'#block[Text.#footnote[X]' + nl + nl + b'#paragraph_tab' + nl + b'Next.]'
            with self.subTest(nl=nl):
                self.assertIsNone(self.preview(source))
                record = self.preview(source, profile='conditional-gaps')
                row = record['steps'][-1]['edits'][0]
                self.assertEqual(row['replacement'].encode(), b'#parbreak()' + nl)
                self.assertTrue(all(row[x] is False for x in ('runtime_binding_proved','execution_proved',
                    'render_equivalence_proved','edit_permission','compiler_permission')))
                result = sequence.replay(parse(source), 'main.typ', self.catalog, record, profile='conditional-gaps')
                self.assertIn(b'#parbreak()', result)
                bad = deepcopy(record)
                bad['steps'][-1]['edits'][0]['replacement'] = '#evil()\n'
                with self.assertRaises(SafetyError):
                    sequence.replay(parse(source), 'main.typ', self.catalog, bad, profile='conditional-gaps')

    def test_other_forms_and_suppression_unchanged(self):
        for source in (b'#block[Text.#footnote[X]\n\n#paragraph-tab\nNext.]',
                       b'#let parbreak = none\n#block[Text.#footnote[X]\n\n#paragraph_tab\nNext.]',
                       b'// typst-lint: ignore-next pde.footnote-trailing-gap -- intention\n'
                       b'#block[Text.#footnote[X]\n\n#paragraph_tab\nNext.]'):
            with self.subTest(source=source):
                baseline = self.preview(source)
                candidate = self.preview(source, profile='conditional-gaps')
                self.assertEqual(None if baseline is None else baseline['steps'],
                                 None if candidate is None else candidate['steps'])

    def test_unresolved_and_budget_skip_keep_other_plan(self):
        source = (b'Thus $x^i$.\n\n#block[Text.#footnote[X]\n\n#paragraph_tab\nNext.\n'
                  b'#unknown()\n#paragraph_tab\nAgain.]')
        baseline = self.preview(source)
        record = self.preview(source, profile='conditional-gaps')
        self.assertEqual(baseline['steps'], record['steps'])
        safe = b'Thus $x^i$.\n\n#block[Text.#footnote[X]\n\n#paragraph_tab\nNext.]'
        baseline = self.preview(safe)
        with patch.object(composed, 'MAX_PREFLIGHT_WORK', 1):
            record = self.preview(safe, profile='conditional-gaps')
        self.assertEqual(baseline['steps'], record['steps'])

    def test_forged_proof_does_not_authorize_other_edit(self):
        source = b'#block[Text.#footnote[X]\n\n#paragraph_tab\nNext.]'
        original = gap.preview
        def forged(tree, path, **kwargs):
            result = original(tree, path, **kwargs)
            result['edits'][0]['replacement'] = '#evil()\n'
            return result
        with patch.object(gap, 'preview', side_effect=forged):
            record = self.preview(source, profile='conditional-gaps')
        self.assertEqual(record['verification'], 'withheld')
        self.assertFalse(record['steps'])

    def test_self_consistent_forged_composition_helper_is_rejected(self):
        source = b'#block[Text.#footnote[X]\n\n#paragraph_tab\nNext.]'
        start = source.index(b'Text.')
        forged = dict(rule_id='forged.unrelated-rule', baseline_sha256=digest(source),
            candidate_sha256=digest(source[:start] + b'Evil.' + source[start + 5:]),
            edits=[dict(start=start, end=start + 5, replacement='Evil.', rule_id='forged.unrelated-rule')])
        with patch.object(composed, 'preview', return_value=forged):
            record = self.preview(source, profile='conditional-gaps')
        self.assertEqual(record['verification'], 'withheld')
        self.assertFalse(record['steps'])

    def test_source_proof_missing_required_authority_flag_is_rejected(self):
        source = b'#block[Text.#footnote[X]\n\n#paragraph_tab\nNext.]'
        original = gap.preview
        def missing(tree, path, **kwargs):
            result = original(tree, path, **kwargs)
            del result['compiler_permission']
            return result
        with patch.object(gap, 'preview', side_effect=missing):
            record = self.preview(source, profile='conditional-gaps')
        self.assertEqual(record['verification'], 'withheld')
        self.assertFalse(record['steps'])

    def test_malformed_directive_is_rejected_before_source_analysis(self):
        source = (b'// typst-lint: ignore-next ' + b' ' * 1500 + b'X\n'
                  b'#block[Text.#footnote[X]\n\n#paragraph_tab\nNext.]')
        with patch.object(composed, 'resolve', side_effect=AssertionError('must not resolve')):
            self.assertIsNone(composed.preview(parse(source), 'main.typ', self.catalog))

    def test_many_markers_on_one_line_are_bounded_before_source_analysis(self):
        source = (b'#block[' + b'#paragraph_tab' * 1000
                  + b'\nText.#footnote[X]\n\n#paragraph_tab\nNext.]')
        with patch.object(composed, 'resolve', side_effect=AssertionError('must not resolve')):
            self.assertIsNone(composed.preview(parse(source), 'main.typ', self.catalog))


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ComposedProseFootnoteEvidenceTests(unittest.TestCase):
    def test_facade_footnote_evidence_survives_prose_stage_and_replay(self):
        f = fixtures.PreviewFootnoteEvidenceTests('runTest')
        f.setUp()
        self.addCleanup(f.doCleanups)
        source = (b'#import "styles.typ": *\n#block[$ x #dots_space #footnote[Note] $]\n'
                  b'#block[Text.#footnote[X]\n\n#paragraph_tab\nNext.]')
        f.source = source
        (f.project / 'main.typ').write_bytes(source)
        proof, session, snapshot = f.capture()
        context = session._context
        tree = context.source_for('main.typ').tree
        catalog = load_rules()[0]['rules']
        profile = 'conditional-gaps-and-spacers'
        record = sequence.preview(tree, 'main.typ', catalog, profile=profile, footnote_evidence=proof)
        self.assertNotEqual(record['verification'], 'withheld', record)
        self.assertTrue(any(e['replacement'].startswith('#parbreak()')
                            for step in record['steps'] for e in step['edits']))
        candidates = sequence.replay_document(context, [record], catalog, profile=profile, footnote_evidence=proof)
        self.assertIn(b'#parbreak()', candidates['main.typ'])
        self.assertNotIn(b'#dots_space', candidates['main.typ'])
        self.assertEqual((f.project / 'main.typ').read_bytes(), source)


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ComposedProseFootnoteCliTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and sys.platform == 'darwin',
                         'Opt in on macOS with reviewed Typst')
    def test_compile_reports_conditional_edit_without_source_authority(self):
        f = cli_fixtures.DocumentPreviewCliTests('runTest')
        f.setUp()
        self.addCleanup(f.doCleanups)
        source = (b'#import "styles.typ": paragraph_tab\n'
                  b'#block[Text.#footnote[X]\n\n#paragraph_tab\nNext.]\n')
        (f.project / 'main.typ').write_bytes(source)
        (f.project / 'styles.typ').write_bytes(b'#let paragraph_tab = []\n')
        f.value['files'].append(dict(root='project', path='styles.typ', role='source'))
        f.manifest.write_text(json.dumps(f.value), encoding='utf-8')
        run = subprocess.run(f.command('--conditional-gaps', '--review-edits', '--format', 'json'),
                             capture_output=True, timeout=120)
        result = json.loads(run.stdout)
        self.assertEqual(run.returncode, 2, result)
        self.assertTrue(result['compiler_invoked'] and result['pdf_generated'])
        self.assertEqual(result['planned_edits'], 1)
        self.assertFalse(result['source_apply_gate']['eligible'])
        rows = [row for item in result['source_review']['sources'] for stage in item['steps']
                for row in stage['edits'] if row['replacement'].startswith('#parbreak()')]
        self.assertEqual(len(rows), 1)
        self.assertTrue(all(rows[0][key] is False for key in ('runtime_binding_proved', 'execution_proved',
            'render_equivalence_proved', 'edit_permission', 'compiler_permission')))
        self.assertEqual((f.project / 'main.typ').read_bytes(), source)
