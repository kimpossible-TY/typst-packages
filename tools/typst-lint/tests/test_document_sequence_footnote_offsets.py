"""Conditional footnote anchors are mapped through later sequence stages."""
from pathlib import Path
import sys
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_sequence_preview as sequence
import test_document_footnote_evidence as fixtures
from edit_plan import Edit, plan
from typst_lint import SafetyError, digest, load_rules


@unittest.skipUnless(fixtures.EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ConditionalFootnoteOffsetTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PreviewFootnoteEvidenceTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def record(self, *, two=False, continuation=True):
        source = (b'#import "styles.typ": *\n#block[This solution holds.\n    We continue.]\n'
                  b'#block[$ x #dots_space #footnote[Note] $]')
        if two:
            source += b'\n#block[$ y #dots_space #footnote[Second] $]'
        self.fixture.source = source
        (self.fixture.project / 'main.typ').write_bytes(source)
        proof, session, snapshot = self.fixture.capture()
        context = session._context
        tree = context.source_for('main.typ').tree
        catalog = load_rules()[0]['rules']
        profile = 'conditional-gaps-and-spacers'
        record = sequence.preview(tree, 'main.typ', catalog, profile=profile, footnote_evidence=proof,
                                  conditional_continuations=continuation)
        self.assertNotEqual(record['verification'], 'withheld', record)
        return proof, context, tree, catalog, profile, record, snapshot

    def test_continuation_maps_one_and_multiple_calls(self):
        for two in (False, True):
            with self.subTest(two=two):
                proof, context, tree, catalog, profile, record, _ = self.record(two=two)
                original = [n for step in record['steps'] for n in step.get('conditional_footnote_calls', ())]
                mapped = sequence._final_conditional_footnote_calls(tree, record)
                self.assertEqual(len(mapped), 2 if two else 1)
                self.assertEqual(tuple(x - 4 for x in original), mapped)
                candidates = sequence.replay_document(context, [record], catalog, profile=profile,
                    footnote_evidence=proof, conditional_continuations=True)
                verified = proof.candidate_calls('main.typ', candidates['main.typ'])
                self.assertTrue(set(mapped).issubset({call.start for call in verified.calls}))

    def test_insertions_and_deletions_before_calls_map_and_overlap_refuses(self):
        proof, _, tree, _, profile, record, _ = self.record(continuation=False)
        before = sequence._replay_expected(tree, record, record, profile=profile)
        anchors = sequence._final_conditional_footnote_calls(tree, record)
        first = anchors[0]
        def extend(edits):
            after = plan(before, edits).candidate()
            step = dict(rule_id='fixture', baseline_sha256=digest(before), candidate_sha256=digest(after),
                        edits=[dict(start=e.start, end=e.end, replacement=e.replacement.decode('utf-8')) for e in edits])
            return dict(record, steps=[*record['steps'], step], candidate_sha256=digest(after))
        inserted = extend((Edit(0, 0, b'// note\n'),))
        self.assertEqual(sequence._final_conditional_footnote_calls(tree, inserted), (first + 8,))
        deleted = extend((Edit(0, 1, b''),))
        self.assertEqual(sequence._final_conditional_footnote_calls(tree, deleted), (first - 1,))
        for edit in (Edit(first, first, b'x'), Edit(first, first + 1, b''),
                     Edit(first + 2, first + 3, b'')):
            with self.subTest(edit=edit), self.assertRaisesRegex(SafetyError, 'touches'):
                sequence._final_conditional_footnote_calls(tree, extend((edit,)))
