"""Combined import item destinations must not masquerade as builtin footnote."""
from pathlib import Path
import json
import os
import subprocess
import sys
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_adjacent_footnote_punctuation as adjacent
import document_adjacent_punctuation_preview as punctuation
import document_conditional_aligned_spacer as aligned
import document_conditional_prose_spacer as prose
import document_nested_prose_footnote_gap as gaps
import document_sequence_preview as sequence
import footnote_rules
from static_resolver import resolve
from syntax_client import EXECUTABLE, parse
from typst_lint import load_rules
import test_document_preview_cli as fixtures


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class CombinedFootnoteShadowTests(unittest.TestCase):
    def test_import_destinations_and_explicit_rebindings(self):
        yes = (b'#import "other.typ" as whatever: item as footnote\n',
               b'#import "other.typ" as whatever: footnote\n',
               b'#import "other.typ" as whatever: a.b.footnote\n',
               b'#import "other.typ" as footnote: item\n',
               b'#import "other.typ": item as footnote\n',
               b'#import x.footnote\n',
               b'#let footnote(body) = none\n')
        no = (b'#import "other.typ" as whatever: footnote as renamed\n',
              b'#import "other.typ" as whatever: item as other\n',
              b'#import "other.typ" as whatever: item\n',
              b'#import x.footnote as other\n',
              b'#import x.footnote: item\n')
        for expected, cases in ((True, yes), (False, no)):
            for prefix in cases:
                with self.subTest(prefix=prefix):
                    tree = parse(prefix+b'#block[This holds#footnote[Reason.].]')
                    self.assertEqual(adjacent._explicit_footnote_shadow(tree), expected)
                    self.assertEqual(gaps._has_explicit_shadow(tree), expected)
                    result = adjacent.analyze(tree, 'main.typ')
                    self.assertEqual(bool(result['diagnostics']), not expected)
                    self.assertEqual(bool(result['unresolved']), expected)
                    candidate = punctuation.preview(tree, 'main.typ')
                    if expected:
                        self.assertEqual(candidate['verification'], 'withheld')
                        self.assertIn('Explicit footnote binding', candidate['reason'])
                        self.assertFalse(candidate['edits'])
                    else:
                        self.assertEqual(len(candidate['edits']), 2)

    def test_composed_compile_plan_never_moves_shadowed_footnote(self):
        catalog = load_rules()[0]['rules']
        for dest in (b'item as footnote', b'footnote'):
            source = (b'#import "other.typ" as whatever: '+dest+b'\n'
                      b'#block[This holds#footnote[Reason.]. $x^i$]')
            tree = parse(source)
            record = sequence.preview(tree, 'main.typ', catalog, conditional_adjacent_punctuation=True)
            self.assertEqual(record['verification'], 'withheld', record)
            self.assertIn('Explicit footnote binding', record['reason'])
            self.assertFalse(record['steps'])

    def test_conditional_spacers_distinguish_explicit_and_unknown_bindings(self):
        explicit = (b'#import "other.typ" as whatever: item as footnote\n',
                    b'#import "other.typ" as whatever: footnote\n',
                    b'#import "other.typ" as footnote: item\n',
                    b'#import x.footnote\n',
                    b'#let footnote(body) = none\n')
        conditional = (b'#import "other.typ" as whatever: *\n',
                       b'#import "other.typ" as whatever: item as other\n',
                       b'#import "other.typ" as whatever: footnote as renamed\n')
        cases = ((prose, b'#block[Text.#dots_space #footnote[X]]', footnote_rules.PROSE),
                 (aligned, b'#block[$ a &= b #footnote[X] $]', footnote_rules.ALIGNED))
        for module, body, rule in cases:
            for prefix in explicit:
                with self.subTest(module=module.__name__, prefix=prefix):
                    tree = parse(prefix + body)
                    hints = footnote_rules.analyze_conditional_nested(tree, 'main.typ', resolve(tree))[rule]
                    self.assertEqual(len(hints.diagnostics), 1)
                    self.assertFalse(hints.edits)
                    self.assertTrue(any('Explicit footnote binding' in reason for reason in hints.skips))
                    preview = module.preview(tree, 'main.typ')
                    self.assertEqual(preview['verification'], 'withheld')
                    self.assertFalse(preview['edits'])
            for prefix in conditional:
                with self.subTest(module=module.__name__, prefix=prefix):
                    tree = parse(prefix + body)
                    hints = footnote_rules.analyze_conditional_nested(tree, 'main.typ', resolve(tree))[rule]
                    self.assertEqual(len(hints.diagnostics), 1)
                    preview = module.preview(tree, 'main.typ')
                    self.assertNotEqual(preview['verification'], 'withheld', preview)
                    self.assertEqual(len(preview['edits']), 1)

    def test_existing_suppression_for_unshadowed_spacer_is_preserved(self):
        source = (b'// typst-lint: ignore-next pde.prose-footnote-no-spacer -- intentional\n'
                  b'#block[Text.#dots_space #footnote[X]]')
        tree = parse(source)
        hints = footnote_rules.analyze_conditional_nested(tree, 'main.typ', resolve(tree))[footnote_rules.PROSE]
        self.assertFalse(hints.diagnostics)
        self.assertEqual(len(hints.suppressed), 1)
        self.assertIsNone(prose.preview(tree, 'main.typ'))

    def test_three_composed_footnote_previews_withhold_explicit_shadows(self):
        cases = ((footnote_rules.preview_conditional_math_gaps,
                  b'#block[$ x #footnote[note]\n\ny $]', 'conditional-gaps'),
                 (footnote_rules.preview_conditional_prose_punctuation,
                  b'#block[Text, #footnote[note]]', 'conditional-gaps-and-punctuation'),
                 (footnote_rules.preview_conditional_unaligned_spacers,
                  b'#block[$ x #dots_space #footnote[note] $]', 'conditional-gaps-and-spacers'))
        explicit = (b'#let footnote(body) = none\n',
                    b'#import "other.typ" as whatever: item as footnote\n',
                    b'#import x.footnote\n',
                    b'#import "other.typ" as footnote: item\n')
        catalog = load_rules()[0]['rules']
        for preview, body, profile in cases:
            for prefix in explicit:
                with self.subTest(preview=preview.__name__, prefix=prefix):
                    tree = parse(prefix + body)
                    result = preview(tree, 'main.typ', resolve(tree), catalog=catalog)
                    self.assertEqual(result['verification'], 'withheld', result)
                    self.assertIn('footnote binding is not locally proven', result['reason'])
                    self.assertFalse(result['edits'])
                    composed = sequence.preview(tree, 'main.typ', catalog, profile=profile)
                    self.assertEqual(composed['verification'], 'withheld', composed)
                    self.assertFalse(composed['steps'])
            tree = parse(b'#import "other.typ" as whatever: *\n' + body)
            result = preview(tree, 'main.typ', resolve(tree), catalog=catalog)
            self.assertNotEqual(result['verification'], 'withheld', result)
            self.assertTrue(result['edits'])


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class CombinedFootnoteShadowCompilerTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.DocumentPreviewCliTests('runTest'); self.f.setUp(); self.addCleanup(self.f.doCleanups)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and sys.platform == 'darwin',
                         'Opt in on macOS with reviewed Typst')
    def test_actual_compile_reports_no_footnote_move(self):
        source = (b'#import "other.typ" as whatever: item as footnote\n'
                  b'#block[This holds#footnote[Reason.]. $x^i$]')
        target=self.f.project/'main.typ'; target.write_bytes(source)
        (self.f.project/'other.typ').write_bytes(b'#let item(body) = none\n')
        self.f.value['files'].append({'root':'project','path':'other.typ','role':'resource'})
        self.f.manifest.write_text(json.dumps(self.f.value),encoding='utf-8')
        result=subprocess.run(self.f.command('--conditional-adjacent-punctuation','--review-edits','--format','json'),
                              capture_output=True,timeout=60)
        report=json.loads(result.stdout)
        self.assertEqual(result.returncode,2,report)
        self.assertFalse(report['compiler_invoked'] or report['pdf_generated'])
        self.assertIn('Explicit footnote binding', report['error'])
        self.assertFalse(report['source_files_modified'] or report['publication_authority'])
        self.assertEqual(target.read_bytes(),source)
