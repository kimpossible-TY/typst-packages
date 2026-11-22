"""Simple direct references stay inside their conditional source-only highlight."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_modern_highlight as modern
import document_cli
import document_modern_highlight_preview as preview
from edit_plan import Edit, plan
import test_conditional_modern_highlight as fixtures


@unittest.skipUnless(fixtures.EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ModernReferenceHighlightPreviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ConditionalModernHighlightTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def analyze(self, body):
        source = fixtures.IMPORT + body
        session, _ = self.fixture.session(source)
        return source, modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)

    def test_simple_references_are_opt_in_and_remain_inside_highlight(self):
        source, analyzer = self.analyze(b'#highlighted[See @first before $x$ and @second after.]')
        self.assertFalse(preview.preview_all(analyzer)['by_file'])
        result = preview.preview_all(analyzer, allow_direct_references=True)
        row, = result['by_file']
        self.assertEqual(row['verification'], 'conditional-modern-highlight-syntax-directives-and-reanalysis')
        edit, = row['edits']
        candidate = plan(source, [Edit(edit['start'], edit['end'], edit['replacement'].encode())]).candidate()
        self.assertEqual(candidate, fixtures.IMPORT +
            b'#highlighted[See @first before ]$x$#highlighted[ and @second after.]')
        self.assertIn(preview.REFERENCE_CONDITION[0], result['conditions'])
        self.assertFalse(result['edit_permission'] or result['compiler_permission']
                         or result['render_equivalence_proved'])
        self.assertEqual((self.fixture.project / 'main.typ').read_bytes(), source)

    def test_supplement_or_executable_reference_is_withheld(self):
        for body in (b'#highlighted[See @target[supplement] $x$]',
                     b'#highlighted[See @target[#panic("executed")] $x$]'):
            with self.subTest(body=body):
                _, analyzer = self.analyze(body)
                self.assertFalse(preview.preview_all(analyzer, allow_direct_references=True)['by_file'])

    def test_suppressed_and_shadowed_helpers_do_not_propose_changes(self):
        for body in (b'// typst-lint: ignore-next pde.text-only-highlight -- deliberate\n'
                     b'#highlighted[See @target $x$]',
                     b'#let highlighted = none\n#highlighted[See @target $x$]'):
            with self.subTest(body=body):
                _, analyzer = self.analyze(body)
                result = preview.preview_all(analyzer, allow_direct_references=True)
                self.assertFalse(any(row['edits'] for row in result['by_file']))

    def test_inspect_option_exposes_reference_candidate_without_compile(self):
        source = fixtures.IMPORT + b'#highlighted[See @target before $x$ after.]'
        (self.fixture.project / 'main.typ').write_bytes(source)
        result = document_cli.report_document(str(self.fixture.project), 'main.typ', rule_set='pde',
            package_path=str(self.fixture.packages), footnote_cache_path=str(self.fixture.cache),
            modern_highlight_evidence=True, preview_conditional_modern_highlights=True)
        section = result['conditional_modern_highlight_previews']
        self.assertEqual(section['previewed_diagnostics'], 1)
        self.assertFalse(result['compiler_invoked'] or result['applied_fixes'])
        self.assertIn(preview.REFERENCE_CONDITION[0], section['conditions'])
        self.assertEqual((self.fixture.project / 'main.typ').read_bytes(), source)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and shutil.which('typst') and shutil.which('pdftotext'),
                         'Opt in with Typst and pdftotext')
    def test_real_typst_custom_reference_show_executes_once_in_both(self):
        body = (b'#show ref: it => [CUSTOMREF]\n= Target <target>\n'
                b'#highlighted[Before @target and $x$ after.]')
        source, analyzer = self.analyze(body)
        result = preview.preview_all(analyzer, allow_direct_references=True)
        row, = result['by_file']
        edit, = row['edits']
        candidate = plan(source, [Edit(edit['start'], edit['end'], edit['replacement'].encode())]).candidate()
        with tempfile.TemporaryDirectory(prefix='highlight-ref-render-') as folder:
            root = Path(folder)
            for name, data in (('baseline', source), ('candidate', candidate)):
                typ, pdf = root / (name + '.typ'), root / (name + '.pdf')
                typ.write_bytes(data)
                run = subprocess.run(['typst', 'compile', '--root', str(root), '--package-path',
                    str(self.fixture.packages), '--creation-timestamp', '0', str(typ), str(pdf)],
                    capture_output=True, timeout=30)
                self.assertEqual(run.returncode, 0, run.stderr)
                text = subprocess.run(['pdftotext', str(pdf), '-'], capture_output=True, check=True, timeout=30).stdout
                self.assertEqual(text.count(b'CUSTOMREF'), 1, text)
        self.assertEqual((self.fixture.project / 'main.typ').read_bytes(), source)


if __name__ == '__main__':
    unittest.main()
