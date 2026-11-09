"""Explicit conditional composition of display and figure footnote boundaries."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import document_composed_prose_block_gap as block
import document_composed_prose_footnote_gap as budget
import document_prose_footnote_display_gap as display
import document_sequence_preview as sequence
import test_document_preview_cli as cli_fixtures
import test_document_footnote_evidence as evidence_fixtures
from syntax_client import EXECUTABLE, parse
from typst_lint import SafetyError, digest, load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ComposedProseBlockTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_rules()[0]['rules']

    def preview(self, source, **kwargs):
        return sequence.preview(parse(source), 'main.typ', self.catalog, **kwargs)

    def test_selected_lf_crlf_replay_and_tamper(self):
        for newline in (b'\n', b'\r\n'):
            for kind, tail in (('display', b'$ x = y $'), ('figure', b'#figure([Illustration])')):
                source = b'#block[Text.#footnote[X]' + newline * 2 + tail + b']'
                with self.subTest(newline=newline, kind=kind):
                    self.assertIsNone(self.preview(source))
                    record = self.preview(source, profile='conditional-gaps')
                    edit = record['steps'][-1]['edits'][0]
                    self.assertEqual(edit['replacement'].encode(), b'#parbreak()' + newline)
                    if kind == 'figure':
                        self.assertEqual(edit['following_kind'], 'figure')
                        self.assertTrue(edit['following_conditions'])
                    self.assertTrue(all(edit[x] is False for x in ('runtime_binding_proved','execution_proved',
                        'render_equivalence_proved','edit_permission','compiler_permission')))
                    candidate = sequence.replay(parse(source), 'main.typ', self.catalog, record, profile='conditional-gaps')
                    self.assertIn(b'#parbreak()', candidate)
                    bad = deepcopy(record)
                    bad['steps'][-1]['edits'][0]['execution_proved'] = True
                    with self.assertRaises(SafetyError):
                        sequence.replay(parse(source), 'main.typ', self.catalog, bad, profile='conditional-gaps')
                    with self.assertRaises(SafetyError):
                        sequence.replay(parse(source), 'main.typ', self.catalog, record)

    def test_unselected_mixed_shadow_suppression_and_budget_preserve_other_plans(self):
        tails = (b'$x$', b'// comment\n$ x = y $', b'#paragraph-tab\nNext.', b'#unknown([x])')
        for tail in tails:
            source = b'#block[Text.#footnote[X]\n\n' + tail + b']'
            with self.subTest(tail=tail):
                self.assertIsNone(self.preview(source, profile='conditional-gaps'))
        for prefix in (b'#let figure = x => x\n', b'#let parbreak = none\n'):
            source = prefix + b'#block[Text.#footnote[X]\n\n#figure([X])]'
            self.assertIsNone(self.preview(source, profile='conditional-gaps'))
        suppressed = (b'// typst-lint: ignore-next pde.footnote-trailing-gap -- deliberate\n'
                      b'#block[Text.#footnote[X]\n\n$ x = y $]')
        self.assertIsNone(self.preview(suppressed, profile='conditional-gaps'))
        mixed = (b'#block[Text.#footnote[X]\n\n#figure([X])]\n'
                 b'#block[Text.#footnote[X]\n\n#paragraph-tab\nNext.]')
        self.assertIsNone(self.preview(mixed, profile='conditional-gaps'))
        source = b'Thus $x^i$.\n\n#block[Text.#footnote[X]\n\n#figure([X])]'
        prior = self.preview(source)
        with patch.object(budget, 'MAX_PREFLIGHT_WORK', 1):
            result = self.preview(source, profile='conditional-gaps')
        self.assertEqual(prior['steps'], result['steps'])

    def test_independent_source_and_helper_tamper_fail_closed(self):
        source = b'#block[Text.#footnote[X]\n\n$ x = y $]'
        offset = source.index(b'Text.')
        forged = dict(rule_id='forged.rule', baseline_sha256=digest(source),
                      candidate_sha256=digest(source[:offset]+b'Evil.'+source[offset+5:]),
                      edits=[dict(start=offset,end=offset+5,replacement='Evil.',rule_id='forged.rule')])
        with patch.object(block, 'preview', side_effect=lambda t,p,c,k: forged if k == 'display' else None):
            record = self.preview(source, profile='conditional-gaps')
        self.assertEqual(record['verification'], 'withheld')
        self.assertFalse(record['steps'])
        original = display.preview
        def altered(*args, **kwargs):
            result = original(*args, **kwargs)
            del result['compiler_permission']
            return result
        with patch.object(display, 'preview', side_effect=altered):
            record = self.preview(source, profile='conditional-gaps')
        self.assertEqual(record['verification'], 'withheld')
        self.assertFalse(record['steps'])

    def test_directives_and_many_same_line_markers_rejected_before_resolution(self):
        cases = (b'// typst-lint: ignore-next ' + b' ' * 1500 + b'X\n',
                 b'#block[' + b'#paragraph_tab' * 1000 + b']\n')
        for prefix in cases:
            tree = parse(prefix + b'#block[Text.#footnote[X]\n\n$ x = y $]')
            with patch.object(block, 'resolve', side_effect=AssertionError('must not resolve')):
                self.assertIsNone(block.preview(tree, 'main.typ', self.catalog, 'display'))


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ComposedProseBlockEvidenceTests(unittest.TestCase):
    def test_facade_evidence_survives_display_and_figure_replay(self):
        f = evidence_fixtures.PreviewFootnoteEvidenceTests('runTest'); f.setUp(); self.addCleanup(f.doCleanups)
        source = (b'#import "styles.typ": *\n#block[$ x #dots_space #footnote[Note] $]\n'
                  b'#block[Text.#footnote[X]\n\n$ x = y $]\n'
                  b'#block[Text.#footnote[X]\n\n#figure([X])]')
        f.source = source; (f.project / 'main.typ').write_bytes(source)
        proof, session, snapshot = f.capture(); context = session._context
        tree = context.source_for('main.typ').tree; catalog = load_rules()[0]['rules']
        profile = 'conditional-gaps-and-spacers'
        record = sequence.preview(tree, 'main.typ', catalog, profile=profile, footnote_evidence=proof)
        self.assertNotEqual(record['verification'], 'withheld', record)
        result = sequence.replay_document(context, [record], catalog, profile=profile, footnote_evidence=proof)
        self.assertEqual(result['main.typ'].count(b'#parbreak()'), 2)
        self.assertNotIn(b'#dots_space', result['main.typ'])
        self.assertEqual((f.project / 'main.typ').read_bytes(), source)


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ComposedProseBlockCliTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and sys.platform == 'darwin',
                         'Opt in on macOS with reviewed Typst')
    def test_cli_compiles_both_stages_without_source_authority(self):
        f = cli_fixtures.DocumentPreviewCliTests('runTest'); f.setUp(); self.addCleanup(f.doCleanups)
        source = (b'#block[Text.#footnote[X]\n\n$ x = y $]\n'
                  b'#block[Text.#footnote[X]\n\n#figure([X])]')
        (f.project / 'main.typ').write_bytes(source)
        run = subprocess.run(f.command('--conditional-gaps','--review-edits','--format','json'),
                             capture_output=True,timeout=120)
        result = json.loads(run.stdout)
        self.assertEqual(run.returncode, 2, result)
        self.assertTrue(result['compiler_invoked'] and result['pdf_generated'])
        self.assertEqual(result['planned_edits'], 2)
        self.assertFalse(result['source_apply_gate']['eligible'])
        self.assertEqual((f.project / 'main.typ').read_bytes(), source)
