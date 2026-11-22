"""Compare edit planning against the original exhaustive conflict definition."""
from pathlib import Path
import random
import sys
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import Edit, Plan, conflict, plan
from typst_lint import SafetyError


def exhaustive(baseline, proposals):
    edits = sorted({edit for edit in proposals if baseline[edit.start:edit.end] != edit.replacement})
    blocked = {edit for edit in edits
               if any(edit != other and conflict(edit, other) for other in edits)}
    accepted = tuple(edit for edit in edits if edit not in blocked)
    deferred = tuple(sorted(blocked))
    candidate = baseline
    for edit in reversed(accepted):
        candidate = candidate[:edit.start] + edit.replacement + candidate[edit.end:]
    return accepted, deferred, candidate


class EditPlanScalingTests(unittest.TestCase):
    def test_nested_touching_and_insertions_are_classified_exhaustively(self):
        baseline = b'abcdefghijklmno'
        proposals = [Edit(0, 12, b'A'), Edit(2, 3, b'B'), Edit(8, 9, b'C'),
                     Edit(12, 12, b'X'), Edit(12, 15, b'Y'), Edit(15, 15, b'Z')]
        result = plan(baseline, proposals)
        self.assertEqual((result.edits, result.deferred, result.candidate()), exhaustive(baseline, proposals))
        self.assertEqual(len(result.deferred), len(proposals))

    def test_randomized_plans_match_exhaustive_reference(self):
        randomizer = random.Random(538241)
        for case in range(4000):
            size = randomizer.randrange(0, 28)
            baseline = bytes(randomizer.choice(b'abc') for _ in range(size))
            proposals = []
            for _ in range(randomizer.randrange(0, 24)):
                start = randomizer.randrange(size + 1)
                end = randomizer.randrange(start, size + 1)
                replacement = randomizer.choice((b'', b'x', b'y', b'zz', baseline[start:end]))
                proposals.append(Edit(start, end, replacement))
            with self.subTest(case=case):
                result = plan(baseline, proposals)
                self.assertEqual((result.edits, result.deferred, result.candidate()),
                                 exhaustive(baseline, proposals))

    def test_unicode_boundaries_and_manual_overlapping_plan(self):
        baseline = '가🙂e\u0301'.encode()
        for position in (1, 2, 4, 5, 6, 9):
            for edit in (Edit(position, position, b'x'), Edit(0, position, b'x')):
                with self.subTest(edit=edit), self.assertRaises(SafetyError) as raised:
                    plan(baseline, [edit])
                self.assertIsInstance(raised.exception.__cause__, UnicodeDecodeError)
                self.assertEqual(raised.exception.__cause__.object, baseline[:position])
        for position in (0, 3, 7, 8, 10):
            self.assertEqual(plan(baseline, [Edit(position, position, b'x')]).candidate(),
                             baseline[:position] + b'x' + baseline[position:])
        overlap = (Edit(0, 3, b'X'), Edit(1, 4, b'Y'))
        result = Plan(b'abcde', overlap, ()).candidate()
        self.assertEqual(result, b'X')
        with self.assertRaises(TypeError):
            Plan(b'abc', iter([Edit(1, 1, b'X')]), ()).candidate()

    def test_edit_subclass_with_custom_order_retains_exhaustive_behavior(self):
        class ReverseEdit(Edit):
            def __lt__(self, other):
                return (self.start, self.end, self.replacement) > (other.start, other.end, other.replacement)
        baseline = b'abcdef'
        proposals = [ReverseEdit(0, 3, b'X'), ReverseEdit(2, 4, b'Y'), ReverseEdit(5, 5, b'Z')]
        result = plan(baseline, proposals)
        self.assertEqual((result.edits, result.deferred, result.candidate()), exhaustive(baseline, proposals))

    def test_replacement_subclass_with_custom_hash_retains_exhaustive_behavior(self):
        class UnusualBytes(bytes):
            __hash__ = object.__hash__

            def __eq__(self, other):
                return bytes(self) == bytes(other)

        first, second = UnusualBytes(b'X'), UnusualBytes(b'X')
        self.assertEqual(first, second)
        self.assertNotEqual(hash(first), hash(second))
        proposals = [Edit(0, 0, first), Edit(0, 0, second)]
        result = plan(b'a', proposals)
        self.assertEqual((result.edits, result.deferred, result.candidate()), exhaustive(b'a', proposals))
        self.assertEqual(len(result.edits), 2)
        self.assertEqual(result.candidate(), b'XXa')

    def test_many_disjoint_edits_preserve_full_candidate(self):
        baseline = b'ab' * 10000
        edits = [Edit(index, index, b'!') for index in range(0, len(baseline), 2)]
        result = plan(baseline, edits)
        self.assertEqual(len(result.edits), 10000)
        self.assertFalse(result.deferred)
        self.assertEqual(result.candidate(), b'!ab' * 10000)
