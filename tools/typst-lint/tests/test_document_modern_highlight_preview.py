"""Conditional literal modern highlight splits never authorize writes."""
from pathlib import Path
from dataclasses import replace
import os
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import conditional_modern_highlight as modern
import document_modern_highlight_preview as preview
from edit_plan import Edit, plan
from syntax_client import parse
import test_conditional_modern_highlight as fixtures
from typst_lint import InputError, SafetyError, digest


@unittest.skipUnless(fixtures.EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class ModernHighlightPreviewTests(unittest.TestCase):
    # Reuse the isolated exact package/session setup, not any user source.
    def setUp(self):
        self.fixture = fixtures.ConditionalModernHighlightTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project = self.fixture.project
        self.packages = self.fixture.packages
        self.cache = self.fixture.cache

    def proposal(self, body):
        source = fixtures.IMPORT + body
        session, context = self.fixture.session(source)
        evidence = modern.ModernHighlightAnalyzer(session, scoped_callbacks=True)
        result = preview.preview_all(evidence)
        return source, context, evidence, result

    def candidate(self, body):
        source, _, evidence, result = self.proposal(body)
        row, = result['by_file']
        self.assertEqual(row['verification'], 'conditional-modern-highlight-syntax-directives-and-reanalysis', row)
        self.assertFalse(row['runtime_binding_proved'] or row['render_equivalence_proved']
                         or row['edit_permission'] or row['compiler_permission'])
        edits = [Edit(item['start'], item['end'], item['replacement'].encode()) for item in row['edits']]
        candidate = plan(source, edits).candidate()
        self.assertEqual(row['baseline_sha256'], digest(source))
        self.assertEqual(row['candidate_sha256'], digest(candidate))
        return source, candidate, result

    def test_literal_prose_and_math_preserve_tokens_and_unicode_spaces(self):
        cases = ((b'#highlighted[Text $x$ follows.]', b'#highlighted[Text ]$x$#highlighted[ follows.]'),
                 (b'#highlighted[$x$]', b'$x$'),
                 ('#highlighted[한글 $x$ 다음 $y$ 끝]'.encode(),
                  '#highlighted[한글 ]$x$#highlighted[ 다음 ]$y$#highlighted[ 끝]'.encode()))
        for body, expected in cases:
            with self.subTest(body=body):
                source, candidate, _ = self.candidate(body)
                self.assertEqual(candidate, fixtures.IMPORT + expected)
                self.assertEqual((self.project / 'main.typ').read_bytes(), source)

    def test_unrelated_duplicate_math_nodes_and_adjacent_text_keep_ownership(self):
        body = b'Before $x(y)$, #highlighted[Text $x$ follows.] after.'
        source, candidate, _ = self.candidate(body)
        self.assertEqual(candidate, fixtures.IMPORT +
                         b'Before $x(y)$, #highlighted[Text ]$x$#highlighted[ follows.] after.')
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)

    def test_unchanged_duplicate_math_nodes_cannot_share_one_match(self):
        source, context, evidence, _ = self.proposal(b'Before $x(y)$, #highlighted[Text $x$ follows.] after.')
        tree = context.source_for('main.typ').tree
        diagnostics = evidence.analyze(tree, 'main.typ')['diagnostics']
        edits, plans = preview._select(tree, diagnostics)
        candidate = parse(plan(source, edits).candidate())
        grouped = {}
        for i, node in enumerate(candidate.nodes):
            if node.kind == 'Math':
                grouped.setdefault((node.start, node.end), []).append(i)
        duplicate = next(group for group in grouped.values() if len(group) == 2)
        nodes = list(candidate.nodes)
        nodes[duplicate[1]] = replace(nodes[duplicate[1]], kind='Text')
        with self.assertRaisesRegex(SafetyError, 'syntax outside'):
            preview._verify_insertions(tree, replace(candidate, nodes=tuple(nodes)), edits, plans)

    def test_opaque_modes_comments_calls_and_code_math_are_not_proposed(self):
        bodies = (b'#highlighted(mode: "flow")[Text $x$ end.]',
                  b'#highlighted[#unknown[$x$]]', b'#highlighted[Text // comment\n$x$]',
                  b'#highlighted[Text $ f(x) $]', b'#highlighted[Text $ #footnote[x] $]',
                  b'#let highlighted = none\n#highlighted[Text $x$]')
        for body in bodies:
            with self.subTest(body=body):
                _, _, _, result = self.proposal(body)
                self.assertFalse(any(row['edits'] for row in result['by_file']), result)

    def test_unpreviewed_and_withheld_diagnostics_are_visible_in_summary(self):
        body = b'#highlighted[Text $x$ follows.]\n#highlighted(mode: "phrase")[Other $y$ text.]'
        _, _, _, result = self.proposal(body)
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertEqual(result['diagnostics_count'], 2)
        self.assertEqual(result['previewed_diagnostics'], 1)
        self.assertEqual(result['unpreviewed_diagnostics'], 1)
        self.assertEqual(result['withheld_source_count'], 0)
        body = (b'// typst-lint: ignore-next pde.footnote-trailing-gap -- intentional\n'
                b'#highlighted[Text $x$ follows.]')
        _, _, _, result = self.proposal(body)
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertEqual(result['diagnostics_count'], 1)
        self.assertEqual(result['previewed_diagnostics'], 0)
        self.assertEqual(result['unpreviewed_diagnostics'], 1)
        self.assertEqual(result['withheld_source_count'], 1)

    def test_suppression_and_other_rule_directive_are_preserved(self):
        suppressed = (b'// typst-lint: ignore-next pde.text-only-highlight -- intentional\n'
                      b'#highlighted[Text $x$ follows.]')
        _, _, _, result = self.proposal(suppressed)
        self.assertFalse(result['by_file'])
        other_rule = (b'// typst-lint: ignore-next pde.footnote-trailing-gap -- intentional\n'
                      b'#highlighted[Text $x$ follows.]')
        _, _, _, result = self.proposal(other_rule)
        row, = result['by_file']
        self.assertEqual(row['verification'], 'withheld')
        self.assertFalse(row['edits'])

    def test_reparse_failure_and_unsafe_owner_are_withheld(self):
        source, context, evidence, _ = self.proposal(b'#highlighted[Text $x$ follows.]')
        with patch.object(preview, 'parse', side_effect=ValueError('fixture parse failure')):
            row, = preview.preview_all(evidence)['by_file']
        self.assertEqual(row['verification'], 'withheld')
        self.assertFalse(row['edits'])
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)
        with self.assertRaises(InputError):
            preview.preview_all(object())
        with self.assertRaisesRegex(InputError, 'budget'):
            preview.preview_all(evidence, max_items=1)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with the pinned Typst compiler')
    def test_owned_korean_math_fixture_renders_and_differs(self):
        if not subprocess.check_output(['typst', '--version'], text=True).startswith('typst 0.15.1 '):
            self.skipTest('Fixture comparison pins Typst 0.15.1')
        source, candidate, _ = self.candidate('#highlighted[한글 $x$ 다음]'.encode())
        pdfs = []
        for name, data in (('before', source), ('after', candidate)):
            path, output = self.project / (name + '.typ'), self.project / (name + '.pdf')
            path.write_bytes(data)
            run = subprocess.run(['typst', 'compile', '--root', str(self.project),
                                  '--package-path', str(self.packages), '--package-cache-path', str(self.cache),
                                  '--creation-timestamp', '0', str(path), str(output)], capture_output=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            pdfs.append(output.read_bytes())
        self.assertNotEqual(pdfs[0], pdfs[1])
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)
