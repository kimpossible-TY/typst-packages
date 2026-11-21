"""Issued evidence must not trust an instance-overridden analysis method."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_footnote_evidence as evidence
import document_sequence_preview as sequence
import test_document_footnote_evidence as fixtures
from typst_lint import SafetyError, digest, load_rules


@unittest.skipUnless(fixtures.EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class FootnoteDispatchTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PreviewFootnoteEvidenceTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.proof, self.session, self.snapshot = self.fixture.capture()
        self.original = self.fixture.source
        self.call, = evidence.PreviewFootnoteEvidence.candidate_calls(self.proof, 'main.typ', self.original).calls

    def fake(self, candidate):
        offset = candidate.index(b'#footnote')
        call = replace(self.call, start=offset, end=offset + len(b'#footnote[Note]'),
                       source_sha256=digest(candidate), facade_sources=(), package_evidence=(), package_requests=())
        return evidence.CandidateCalls('main.typ', digest(candidate), (call,), ())

    def shadowed(self):
        return self.original.replace(b'#block', b'#let footnote(body) = body\n#block')

    def test_class_entry_recomputes_shadowed_binding_despite_instance_override(self):
        candidate = self.shadowed()
        override = self.proof._candidate_calls = Mock(return_value=self.fake(candidate))
        result = evidence.PreviewFootnoteEvidence.candidate_calls(self.proof, 'main.typ', candidate)
        self.assertFalse(result.calls)
        override.assert_not_called()

    def test_final_binding_validation_rejects_forged_instance_report(self):
        candidate = self.shadowed()
        override = self.proof._candidate_calls = Mock(return_value=self.fake(candidate))
        with self.assertRaises(SafetyError):
            evidence.PreviewFootnoteEvidence.validate_staged_facades(
                self.proof, {'main.typ': candidate}, {'main.typ': (candidate.index(b'#footnote'),)})
        override.assert_not_called()

    def test_changed_facade_cannot_be_hidden_by_empty_forged_dependencies(self):
        override = self.proof._candidate_calls = Mock(return_value=self.fake(self.original))
        with self.assertRaises(SafetyError):
            evidence.PreviewFootnoteEvidence.validate_staged_facades(
                self.proof, {'main.typ': self.original, 'styles.typ': self.fixture.facade + b'\n#let other = 1'},
                {'main.typ': (self.call.start,)})
        override.assert_not_called()

    def test_valid_binding_and_final_facade_ignore_instance_override(self):
        override = self.proof._candidate_calls = Mock(side_effect=AssertionError('untrusted instance dispatch'))
        result = evidence.PreviewFootnoteEvidence.candidate_calls(self.proof, 'main.typ', self.original)
        self.assertEqual(result.calls, (self.call,))
        evidence.PreviewFootnoteEvidence.validate_staged_facades(
            self.proof, {'main.typ': self.original}, {'main.typ': (self.call.start,)})
        override.assert_not_called()

    def test_actual_strict_spacer_plan_uses_independent_direct_analysis(self):
        direct = evidence.PreviewFootnoteEvidence.candidate_calls
        visited = []
        def track(proof, path, candidate):
            visited.append((path, digest(candidate)))
            return direct(proof, path, candidate)
        with patch.object(evidence.PreviewFootnoteEvidence, 'candidate_calls', new=track), \
             patch.object(evidence.PreviewFootnoteEvidence, 'candidate_calls_with_deferred',
                          side_effect=AssertionError('strict sequence used batch')):
            row = sequence.preview(self.session._context.source_for('main.typ').tree, 'main.typ',
                                   load_rules()[0]['rules'], profile='conditional-gaps-and-spacers',
                                   footnote_evidence=self.proof)
        self.assertIsNotNone(row)
        self.assertNotEqual(row['verification'], 'withheld')
        self.assertIn('pde.unaligned-footnote-no-spacer', [step['rule_id'] for step in row['steps']])
        self.assertTrue(visited)
        self.assertEqual((self.fixture.project / 'main.typ').read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main()
