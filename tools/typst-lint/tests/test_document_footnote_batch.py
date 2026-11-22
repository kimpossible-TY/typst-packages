"""Same-source direct/deferred analysis shares only a single validated stage."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_deferred_footnote_evidence as deferred
import document_footnote_evidence as evidence
import document_sequence_preview as sequence
import test_document_footnote_evidence as fixtures
from typst_lint import InputError, SafetyError, load_rules


@unittest.skipUnless(fixtures.EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class FootnoteBatchTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PreviewFootnoteEvidenceTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.source = (b'#import "styles.typ": *\n#footnote[Root]\n'
                       b'#use(x => [#let helper(z) = [Other]\n#footnote[Note]])')
        self.fixture.source = self.source
        (self.fixture.project / 'main.typ').write_bytes(self.source)
        self.proof, self.session, self.snapshot = self.fixture.capture()
        self.offsets = (self.source.index(b'#footnote[Root]'), self.source.index(b'#footnote[Note]'))

    def test_matches_separate_analyses_and_reparses_once(self):
        expected_direct = self.proof.candidate_calls('main.typ', self.source)
        expected_deferred = deferred.analyze(self.proof, 'main.typ', self.offsets, candidate=self.source)
        with patch.object(evidence, 'parse', wraps=evidence.parse) as parsed, \
             patch.object(deferred, 'parse', wraps=deferred.parse) as deferred_parse:
            direct, derived = self.proof.candidate_calls_with_deferred('main.typ', self.source, self.offsets)
        self.assertEqual(direct, expected_direct)
        self.assertEqual(derived, expected_deferred)
        self.assertEqual(parsed.call_count, 1)
        deferred_parse.assert_not_called()

    def test_direct_only_does_not_run_deferred_analysis(self):
        with patch.object(deferred, '_analyze', side_effect=AssertionError('unneeded deferred analysis')):
            direct, derived = self.proof.candidate_calls_with_deferred('main.typ', self.source, (self.offsets[0],))
        self.assertTrue(direct.calls)
        self.assertIsNone(derived)

    def test_selection_is_strict_even_when_direct_is_proven(self):
        for offsets in ((), (True,), (-1,), (self.offsets[0], self.offsets[0]), [self.offsets[0]]):
            with self.subTest(offsets=offsets), self.assertRaises(InputError):
                self.proof.candidate_calls_with_deferred('main.typ', self.source, offsets)

    def test_snapshot_mutation_after_direct_is_not_accepted(self):
        original = evidence.PreviewFootnoteEvidence._candidate_calls
        def mutate(proof, *args, **kwargs):
            result = original(proof, *args, **kwargs)
            object.__setattr__(self.snapshot.files[0], 'data', b'changed')
            return result
        with patch.object(evidence.PreviewFootnoteEvidence, '_candidate_calls', new=mutate), \
             self.assertRaises(SafetyError):
            self.proof.candidate_calls_with_deferred('main.typ', self.source, self.offsets)

    def test_deferred_failure_is_not_converted_to_direct_success(self):
        with patch.object(deferred, '_analyze', side_effect=SafetyError('deferred analysis failed')), \
             self.assertRaisesRegex(SafetyError, 'deferred analysis failed'):
            self.proof.candidate_calls_with_deferred('main.typ', self.source, self.offsets)

    def test_snapshot_mutation_during_deferred_is_not_accepted(self):
        original = deferred._analyze
        def mutate(*args, **kwargs):
            result = original(*args, **kwargs)
            object.__setattr__(self.snapshot.files[0], 'data', b'changed')
            return result
        with patch.object(deferred, '_analyze', side_effect=mutate), self.assertRaises(SafetyError):
            self.proof.candidate_calls_with_deferred('main.typ', self.source, self.offsets)

    def test_namespace_selection_change_after_direct_is_not_used(self):
        original = evidence.PreviewFootnoteEvidence._candidate_calls
        def mutate(proof, *args, **kwargs):
            result = original(proof, *args, **kwargs)
            self.session._namespaces = object()
            return result
        with patch.object(evidence.PreviewFootnoteEvidence, '_candidate_calls', new=mutate), \
             patch.object(deferred, '_analyze') as analyze, self.assertRaises(SafetyError):
            self.proof.candidate_calls_with_deferred('main.typ', self.source, self.offsets)
        analyze.assert_not_called()

    def test_strict_sequence_and_final_facade_use_independent_checks(self):
        tree = self.session._context.source_for('main.typ').tree
        with patch.object(evidence.PreviewFootnoteEvidence, 'candidate_calls_with_deferred',
                          side_effect=AssertionError('strict path used batch')):
            sequence.preview(tree, 'main.typ', load_rules()[0]['rules'],
                             profile='conditional-gaps-and-spacers', footnote_evidence=self.proof)
            self.proof.validate_staged_facades({'main.typ': self.source}, {'main.typ': (self.offsets[0],)})


if __name__ == '__main__':
    unittest.main()
