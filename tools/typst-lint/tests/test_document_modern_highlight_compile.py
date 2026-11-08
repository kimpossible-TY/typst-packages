"""Exclusive modern highlight compile uses the captured candidate map."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_modern_highlight_evidence as evidence
import document_preview_compile as compiler
import test_document_modern_highlight_evidence as fixtures
from typst_lint import SafetyError


@unittest.skipUnless(fixtures.fixtures.EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class ModernHighlightCompileTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CapturedModernHighlightTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project, self.packages, self.cache = self.fixture.project, self.fixture.packages, self.fixture.cache

    def test_stage_map_is_derived_and_unchanged_files_are_preserved(self):
        proof, _, _, original = self.fixture.issue()
        staged, report = evidence.stage_candidates(proof)
        self.assertNotEqual(staged['project', 'main.typ'], original['project', 'main.typ'])
        self.assertEqual(staged['packages', 'local/text-utils/0.1.2/lib.typ'],
                         original['packages', 'local/text-utils/0.1.2/lib.typ'])
        self.assertEqual(report['candidate_count'], 1)
        with self.assertRaises(TypeError):
            staged['project', 'main.typ'] = b'changed'

    def test_forged_or_mixed_evidence_is_rejected_before_compiler(self):
        proof, analyzer, snapshot, _ = self.fixture.issue()
        context = analyzer._session._context
        class ForgedProfile(str):
            pass
        with patch.object(compiler.runtime, '_capture_compiler') as capture:
            for options in (dict(modern_highlight_evidence=object()),
                            dict(modern_highlight_evidence=proof, profile='conditional-gaps'),
                            dict(modern_highlight_evidence=proof, conditional_continuations=True),
                            dict(modern_highlight_evidence=proof, conditional_continuations=0),
                            dict(modern_highlight_evidence=proof, profile=ForgedProfile('standard')),
                            dict(modern_highlight_evidence=proof, partial_unsupported=None),
                            dict(modern_highlight_evidence=proof, explicit_withheld_sources=[]),
                            dict(modern_highlight_evidence=proof, withheld_records={})):
                with self.subTest(options=options), self.assertRaises(SafetyError):
                    compiler.compile_preview(context, None, [], snapshot, entrypoint='main.typ', **options)
            capture.assert_not_called()

    def test_cli_rejects_incompatible_options_and_never_runs_compiler(self):
        proof, analyzer, _, _ = self.fixture.issue()
        del proof, analyzer
        command = [sys.executable, str(TOOL / 'typst-lint'), 'preview-compile', '--root', str(self.project),
                   '--entrypoint', 'main.typ', '--manifest', str(self.project.parent / 'absent.json'),
                   '--conditional-modern-highlights', '--format', 'json']
        for option in ('--conditional-gaps', '--conditional-continuations', '--review-edits'):
            run = subprocess.run(command + [option], capture_output=True, timeout=10)
            self.assertEqual(run.returncode, 2)
            self.assertIn(b'exclusive preview', run.stderr)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with the pinned Typst compiler')
    def test_cli_compiles_captured_original_and_candidate(self):
        proof, analyzer, snapshot, _ = self.fixture.issue()
        source = (self.project / 'main.typ').read_bytes()
        manifest = self.project.parent / 'modern-manifest.json'
        manifest.write_text(json.dumps(dict(roots=dict(snapshot.roots), files=[
            dict(root=item.request.root, path=item.request.path, role=item.request.role) for item in snapshot.files])))
        command = [sys.executable, str(TOOL / 'typst-lint'), 'preview-compile', '--root', str(self.project),
                   '--entrypoint', 'main.typ', '--manifest', str(manifest), '--format', 'json',
                   '--conditional-modern-highlights']
        run = subprocess.run(command, capture_output=True, text=True, timeout=90)
        self.assertEqual(run.returncode, 2, run.stderr)
        report = json.loads(run.stdout)
        self.assertTrue(report.get('pdf_generated'), report)
        self.assertEqual(report['planned_edits'], 1)
        self.assertEqual(report['sequence_profile'], 'modern-highlight-only')
        self.assertFalse(report['source_apply_gate']['eligible'] or report['publication_authority'])
        self.assertFalse(report['conditional_modern_highlight_preview']['runtime_binding_proved'])
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)
        del proof, analyzer

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and compiler.SANDBOX.is_file(),
                         'Opt in on macOS with the pinned Typst compiler')
    def test_partial_candidates_keep_unpreviewed_gate_blocker(self):
        source = fixtures.fixtures.IMPORT + (b'#highlighted[Bad $x_1$ end.]\n'
                                             b'#highlighted[Good $x$ end.]')
        _, _, snapshot, _ = self.fixture.issue(source)
        manifest = self.project.parent / 'modern-manifest.json'
        manifest.write_text(json.dumps(dict(roots=dict(snapshot.roots), files=[
            dict(root=item.request.root, path=item.request.path, role=item.request.role) for item in snapshot.files])))
        run = subprocess.run([sys.executable, str(TOOL / 'typst-lint'), 'preview-compile',
                              '--root', str(self.project), '--entrypoint', 'main.typ', '--manifest', str(manifest),
                              '--format', 'json', '--conditional-modern-highlights'], capture_output=True,
                             text=True, timeout=90)
        report = json.loads(run.stdout)
        self.assertTrue(report.get('pdf_generated'), report)
        info = report['conditional_modern_highlight_preview']
        self.assertEqual((info['status'], info['candidate_count'], info['unpreviewed_diagnostics']),
                         ('INCOMPLETE', 1, 1))
        self.assertIn('modern_highlight_incomplete', report['source_apply_gate']['blockers'])
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)
