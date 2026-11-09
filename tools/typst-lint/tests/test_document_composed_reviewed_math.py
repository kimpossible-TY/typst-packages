"""Reviewed math composition is explicitly selected and snapshot-bound."""
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_document_intermediate_context as fixtures
import document_composed_modern_highlight as composed
import document_footnote_evidence as footnote
import document_modern_highlight_preview as preview
import document_preview_compile as compiler
import document_sequence_preview as sequence
from typst_lint import load_rules
from typst_lint import SafetyError

IMPORT = fixtures.fixtures.fixtures.IMPORT


@unittest.skipUnless(fixtures.fixtures.fixtures.EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ComposedReviewedMathTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.IntermediateContextTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.inner = self.fixture.fixture

    def issue(self, source):
        _, analyzer, snapshot, _ = self.inner.issue(source)
        return footnote.PreviewFootnoteEvidence(analyzer._session, snapshot)

    def test_opt_in_combines_literal_and_reviewed_math_once(self):
        source = IMPORT + b'#highlighted[Plain $x$.]\n#highlighted[Math $cal(frac(x, y))$.]'
        proof = self.issue(source)
        baseline, baseline_report, _ = composed.stage(proof, [])
        final, report, _ = composed.stage(proof, [], reviewed_math=True)
        self.assertEqual(baseline_report['candidate_count'], 1)
        self.assertEqual(report['candidate_count'], 2, report)
        self.assertIn(b'$cal(frac(x, y))$', final['project', 'main.typ'])
        self.assertNotEqual(baseline['project', 'main.typ'], final['project', 'main.typ'])
        self.assertTrue(report['reviewed_math'])
        self.assertFalse(report['edit_permission'] or report['compiler_permission'] or report['runtime_binding_proved'])
        self.assertEqual((self.fixture.project / 'main.typ').read_bytes(), source)

    def test_shadow_and_wildcard_are_distinguished(self):
        for declaration, expected in ((b'#let other = none\n', 1), (b'#let cal(x) = x\n', 0)):
            with self.subTest(declaration=declaration):
                (self.fixture.project / 'names.typ').write_bytes(declaration)
                source = IMPORT + b'#import "names.typ": *\n#highlighted[Math $cal(x)$.]'
                proof = self.issue(source)
                _, report, _ = composed.stage(proof, [], reviewed_math=True)
                self.assertEqual(report['candidate_count'], expected, report)

    def test_wildcard_dependency_source_edits_are_withheld(self):
        dependency = IMPORT + b'#highlighted[Dependency $cal(x)$.]\n#let other = none\n'
        (self.fixture.project / 'names.typ').write_bytes(dependency)
        source = IMPORT + b'#include "names.typ"\n#import "names.typ": *\n#highlighted[Main $cal(x)$.]'
        proof = self.issue(source)
        final, report, _ = composed.stage(proof, [], reviewed_math=True)
        self.assertEqual(final['project', 'names.typ'], dependency)
        self.assertEqual(report['candidate_count'], 1, report)
        self.assertEqual(report['modern_withheld_sources'][0]['reason_code'], 'math-import-dependency')

    def test_changed_capture_and_replayed_evidence_refuse(self):
        proof = self.issue(IMPORT + b'#highlighted[Math $cal(x)$.]')
        original = preview.preview_all
        calls = 0
        def changed(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs)
            calls += 1
            if calls == 1:
                object.__setattr__(proof._snapshot.files[0], 'data', b'changed')
            return result
        with patch.object(composed.highlight, 'preview_all', changed):
            with self.assertRaises(SafetyError):
                composed.stage(proof, [], reviewed_math=True)

    def test_package_capture_change_is_rejected(self):
        proof = self.issue(IMPORT + b'#highlighted[Math $cal(x)$.]')
        blob = next(x for x in proof._snapshot.files if x.request.path.endswith('/lib.typ'))
        object.__setattr__(blob, 'data', blob.data + b'\n#let cal(x) = x\n')
        with self.assertRaises(SafetyError):
            composed.stage(proof, [], reviewed_math=True)

    def test_suppression_and_existing_sequence_edits_remain_intact(self):
        source = (IMPORT + b'// typst-lint: ignore-next pde.text-only-highlight -- intentional\n'
                  b'#highlighted[Skip $cal(a)$.]\n#highlighted[Use $cal(x_i)$.]')
        proof = self.issue(source)
        catalog = load_rules()[0]['rules']
        records = [result for item in proof._session._context.sources
                   if (result := sequence.preview(item.tree, item.path, catalog)) is not None]
        final, report, _ = composed.stage(proof, records, reviewed_math=True)
        self.assertEqual(report['candidate_count'], 1, report)
        self.assertIn(b'#highlighted[Skip $cal(a)$.]', final['project', 'main.typ'])
        self.assertIn(b'$cal(x_(i))$', final['project', 'main.typ'])

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with pinned Typst')
    def test_real_cli_compiles_owned_fixture_without_writing_source(self):
        source = IMPORT + b'#highlighted[Text $cal(frac(x, y))$ end.]'
        proof = self.issue(source)
        snapshot = proof._snapshot
        manifest = self.fixture.project.parent / 'reviewed-math-manifest.json'
        manifest.write_text(json.dumps(dict(roots=dict(snapshot.roots), files=[
            dict(root=x.request.root, path=x.request.path, role=x.request.role) for x in snapshot.files])))
        command = [sys.executable, str(fixtures.TOOL / 'typst-lint'), 'preview-compile',
                   '--root', str(self.fixture.project), '--entrypoint', 'main.typ',
                   '--manifest', str(manifest), '--format', 'json', '--compose-modern-highlights',
                   '--compose-reviewed-math-highlights']
        run = subprocess.run(command, capture_output=True, text=True, timeout=90)
        self.assertEqual(run.returncode, 2, run.stderr)
        report = json.loads(run.stdout)
        self.assertTrue(report.get('pdf_generated'), report)
        self.assertTrue(report['conditional_modern_highlight_preview']['reviewed_math'])
        self.assertEqual(report['conditional_modern_highlight_preview']['candidate_count'], 1)
        self.assertEqual((self.fixture.project / 'main.typ').read_bytes(), source)

    def test_cli_requires_explicit_composition(self):
        command = [sys.executable, str(fixtures.TOOL / 'typst-lint'), 'preview-compile',
                   '--root', str(self.fixture.project), '--entrypoint', 'main.typ',
                   '--manifest', str(self.fixture.project.parent / 'missing.json'),
                   '--compose-reviewed-math-highlights']
        result = subprocess.run(command, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'requires --compose-modern-highlights', result.stderr)


if __name__ == '__main__':
    unittest.main()
