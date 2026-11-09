"""Captured CeTZ draw imports do not turn conditional hints into edit authority."""
import unittest
from unittest.mock import patch

import test_document_cetz_draw_evidence as fixtures
import document_cetz_detached_annotations as subject
import document_footnote_evidence
from syntax_client import EXECUTABLE
from typst_lint import SafetyError

PREFIX = (b'#import "@local/text-utils:0.1.2": *\n'
          b'#import "@preview/cetz:0.4.2"\n')
BODY = (b'#local-scope-annotations(s => [\n'
        b'$ x $\n'
        b'#(s.annot)(("one",), cetz, { import cetz.draw: *; '
        b'line((s.node)("one", "south"), (0, 0)) })\n])\n')


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class CetzDetachedTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CetzDrawEvidenceTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def analyze(self, source):
        return subject.analyze(self.fixture.proof(source), 'main.typ')

    def test_literal_draw_import_yields_conditional_hint_only(self):
        result = self.analyze(PREFIX + BODY)
        self.assertEqual(len(result['diagnostics']), 1, result)
        self.assertEqual(result['diagnostics'][0]['certainty'], 'conditional')
        for field in ('runtime_binding_proved', 'execution_proved', 'coverage_complete',
                      'edit_permission', 'compiler_permission'):
            self.assertFalse(result[field])

    def test_shadow_receiver_escape_and_dynamic_context_withheld(self):
        for source in (
            PREFIX + b'#let cetz = none\n' + BODY,
            PREFIX + b'#import "unknown.typ": *\n' + BODY,
            PREFIX + BODY.replace(b's =>', b'cetz =>').replace(b's.annot', b'cetz.annot').replace(b's.node', b'cetz.node'),
            PREFIX + BODY.replace(b'line((s.node)("one", "south"), (0, 0))',
                                  b'line(s.node, (0, 0))'),
            PREFIX + BODY.replace(b'line((s.node)("one", "south"), (0, 0))',
                                  b'evil((s.node)("one", "south"))'),
            PREFIX + b'#for x in (1,) [' + BODY + b']',
        ):
            with self.subTest(source=source):
                result = self.analyze(source)
                self.assertEqual(result['diagnostics'], [], result)

    def test_suppression_applies_at_the_exact_call(self):
        source = PREFIX + BODY.replace(
            b'#(s.annot)',
            b'// typst-lint: ignore-next core.annotation-in-display -- intentional\n#(s.annot)',
        )
        result = self.analyze(source)
        self.assertEqual(result['diagnostics'], [], result)
        self.assertEqual(len(result['suppressed']), 1, result)

    def test_snapshot_and_source_identity_are_required(self):
        proof = self.fixture.proof(PREFIX + BODY)
        with self.assertRaises(SafetyError):
            subject.analyze(proof, 'missing.typ')
        with patch.object(document_footnote_evidence, '_valid', side_effect=SafetyError('changed')):
            with self.assertRaises(SafetyError):
                subject.analyze(proof, 'main.typ')

    def test_changed_draw_namespace_rejected(self):
        proof = self.fixture.proof(PREFIX + BODY, mutate_draw=True)
        with self.assertRaises(SafetyError):
            subject.analyze(proof, 'main.typ')

    def test_changed_used_draw_implementation_rejected(self):
        for name in ('shapes', 'styling'):
            path = self.fixture.packages / f'preview/cetz/0.4.2/src/draw/{name}.typ'
            original = path.read_bytes()
            try:
                path.write_bytes(original + b'\n#let injected = none\n')
                proof = self.fixture.proof(PREFIX + BODY)
                with self.subTest(name=name), self.assertRaises(SafetyError):
                    subject.analyze(proof, 'main.typ')
            finally:
                path.write_bytes(original)


if __name__ == '__main__':
    unittest.main()
