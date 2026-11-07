"""Real typed-equation tests; all repairs remain in memory or test temp files."""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
from display_rules import CONTINUATION_GAP as GAP, STANDALONE, RULE_IDS, analyze
from edit_plan import plan
from package_loader import load_packages
from script_rule import analyze as scripts
from static_resolver import imported_paths, resolve
from suppression import resolve as suppressions
from syntax_client import AdapterError, EXECUTABLE, parse
from typst_lint import load_rules


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter explicitly first')
class DisplayRuleTests(unittest.TestCase):
    def run_source(self, source, *, packages=False):
        tree = parse(source)
        loaded = load_packages(REPO, imported_paths(tree)).sources if packages else ()
        return analyze(tree, 'fixture.typ', resolve(tree, packages=loaded))

    def candidate(self, source, **kwargs):
        results = self.run_source(source, **kwargs)
        planned = plan(source, [edit for result in results.values() for edit in result.edits])
        self.assertFalse(planned.deferred)
        return results, planned.candidate()

    def check(self, source, expected, *, active=(), packages=False):
        raw, wanted = source.encode(), expected.encode()
        results, candidate = self.candidate(raw, packages=packages)
        self.assertEqual(candidate, wanted)
        self.assertEqual({rid for rid, result in results.items() if result.edits}, set(active))
        self.assertFalse(parse(candidate).nodes[0].errors)
        final = self.run_source(candidate, packages=packages)
        self.assertFalse(any(result.edits or result.diagnostics for result in final.values()), final)
        self.assertEqual(final, self.run_source(candidate, packages=packages))
        return results

    def test_exact_standalone_both_sides_and_zero_width_boundaries(self):
        for source, expected in [
                ('Thus $ A=B $ and the claim follows.', 'Thus\n$ A=B $\nand the claim follows.'),
                ('Thus$ A=B $and the claim follows.', 'Thus\n$ A=B $\nand the claim follows.'),
                ('Thus\t $ A=B $\t and.', 'Thus\n$ A=B $\nand.'),
                ('$ A=B $ and.', '$ A=B $\nand.'),
                ('Thus $ A=B $', 'Thus\n$ A=B $'),
                ('Thus $\nA=B\n$ and.', 'Thus\n$\nA=B\n$\nand.'),
                ('Thus $ A=B $ and $ C=D $ more.', 'Thus\n$ A=B $\nand\n$ C=D $\nmore.'),
                ('Thus $ A=B $\nNext.', 'Thus\n$ A=B $\nNext.')]:
            with self.subTest(source=source):
                self.check(source, expected, active=(STANDALONE,))

    def test_inline_math_and_asymmetric_spacing_are_not_display(self):
        for source in ('Thus $A=B$ and more.', 'Thus $ A=B$ and more.', 'Thus $A=B $ and more.',
                       '$A=B$\n\nHence.', '$ A=B$\n\nHence.', '$A=B $\n\nHence.'):
            with self.subTest(source=source):
                results = self.check(source, source)
                self.assertFalse(any(result.skips for result in results.values()))

    def test_existing_single_newlines_are_unchanged(self):
        for source in ('Thus\n$ A=B $\nand.', '$ A=B $\nHence.', '$ A=B $\n  Hence.',
                       '$ A=B $', '  $ A=B $  \n', '$\n A=B\n $\nHence.'):
            with self.subTest(source=source): self.check(source, source)

    def test_single_attached_semantic_label_stays_with_display(self):
        for source, expected, active in [
                ('Thus $ A=B $ <energy> and.', 'Thus\n$ A=B $ <energy>\nand.', STANDALONE),
                ('Thus $ A=B $<energy>and.', 'Thus\n$ A=B $<energy>\nand.', STANDALONE),
                ('$\nA=B\n$\t<energy> and.', '$\nA=B\n$\t<energy>\nand.', STANDALONE),
                ('$ A=B $ <energy>\n\nHence.', '$ A=B $ <energy>\nHence.', GAP)]:
            with self.subTest(source=source):
                self.check(source, expected, active=(active,))

    def test_continuation_gap_exact_whitespace_only_replacements(self):
        for source, expected in [
                ('$ A=B $\n\nHence the identity holds.', '$ A=B $\nHence the identity holds.'),
                ('$ A=B $ \n \t\n  Hence.', '$ A=B $\nHence.'),
                ('$ A=B $\n\n\n\nHence.', '$ A=B $\nHence.'),
                ('\t$ A=B $\n\nHence.', '\t$ A=B $\nHence.'),
                ('$\nA=B\n$ <energy>\n\nHence.', '$\nA=B\n$ <energy>\nHence.')]:
            with self.subTest(source=source): self.check(source, expected, active=(GAP,))

    def test_bom_unicode_crlf_and_diagnostic_columns(self):
        results = self.check('\ufeff따라서 $ α=β $ 계속.\r\n', '\ufeff따라서\r\n$ α=β $\r\n계속.\r\n',
                             active=(STANDALONE,))
        self.assertEqual((results[STANDALONE].diagnostics[0].line,
                          results[STANDALONE].diagnostics[0].column), (1, 6))
        self.check('\ufeff$ α=β $ <에너지>\r\n \t\r\n  따라서.\r\n',
                   '\ufeff$ α=β $ <에너지>\r\n따라서.\r\n', active=(GAP,))
        self.check('// 앞\r\n따라서 $ α=β $ 계속.', '// 앞\r\n따라서\r\n$ α=β $\r\n계속.',
                   active=(STANDALONE,))
        self.check('\ufeff$ A=B $', '\ufeff$ A=B $')

    def test_preserves_existing_mixed_newlines_and_math_bytes(self):
        source = 'Prior.\r\nThus $\n A=B\n$ and.\r\n'
        expected = 'Prior.\r\nThus\n$\n A=B\n$\r\nand.\r\n'
        self.check(source, expected, active=(STANDALONE,))
        self.check('$ A=B $\r\n\nHence.\n', '$ A=B $\r\nHence.\n', active=(GAP,))

    def test_no_evaluation_and_equation_internal_overlay_and_comments_untouched(self):
        source = 'Thus $ A=B #overlay([inside]) /* preserve */ $ <energy> and.'
        expected = 'Thus\n$ A=B #overlay([inside]) /* preserve */ $ <energy>\nand.'
        self.check(source, expected, active=(STANDALONE,))

    def test_comments_strings_raw_and_escaped_dollars_are_data(self):
        source = '#let s = "Thus $ A=B $ and."\n// Thus $ A=B $ and.\n/* $ A=B $ */\n`Thus $ A=B $ and.`\n\\$ A=B \\$'
        self.check(source, source)

    def test_captured_code_closure_and_unknown_nested_bodies_are_partial(self):
        for source in ('#let saved = [Thus $ A=B $ and.]', '#let f() = [Thus $ A=B $ and.]',
                       '#text[Thus $ A=B $ and.]', '#block[$ A=B $\n\nHence.]',
                       '#unknown[Thus $ A=B $ and.]', '*Thus $ A=B $ and.*',
                       '= Thus $ A=B $ and.', '- Thus $ A=B $ and.', '#context [Thus $ A=B $ and.]'):
            with self.subTest(source=source):
                result = self.check(source, source)
                self.assertTrue(all(result[rid].skips for rid in RULE_IDS))

    def test_unknown_show_and_prior_output_effect_refuse_body_proofs(self):
        for prefix in ('#show: body => body\n', '#unknown()\n', '#include "missing.typ"\n'):
            source = prefix + 'Thus $ A=B $ and.'
            result = self.check(source, source)
            self.assertTrue(all(result[rid].skips for rid in RULE_IDS))

    def test_comments_at_boundary_are_not_crossed(self):
        for source in ('$ A=B $ /* note */ and.', 'Thus /* note */ $ A=B $',
                       '$ A=B $\n// note\n\nHence.', '$ A=B $\n\n/* note */ Hence.',
                       '$ A=B $ /* note */ <energy>\n\nHence.'):
            with self.subTest(source=source):
                result = self.check(source, source)
                self.assertTrue(any(r.skips for r in result.values()))

    def test_linebreaks_and_structural_siblings_are_not_prose_continuations(self):
        for suffix in ('= Heading\n', '- Item\n', '+ Item\n', '/ Term: definition\n',
                       '$ C=D $\n', '`raw`\n', '\\\nText.'):
            source = '$ A=B $\n\n' + suffix
            with self.subTest(source=source): self.check(source, source)

    def test_marked_new_paragraph_is_not_gap_repair(self):
        source = ('#import "@local/text-utils:0.1.2": paragraph_tab\n'
                  '$ A=B $\n\n#paragraph_tab\nNew idea.')
        results = self.check(source, source, packages=True)
        self.assertFalse(results[GAP].skips)
        # No name-only provenance guess, and never search through unknown code.
        unknown = self.check('$ A=B $\n\n#paragraph_tab\nNew idea.',
                             '$ A=B $\n\n#paragraph_tab\nNew idea.')
        self.assertTrue(unknown[GAP].skips)

    def test_unknown_inline_prose_starts_are_explicit_partial_coverage(self):
        for suffix in ('*Hence.*', '_Hence._', '@energy hence.', '$x$ hence.', '#text[Hence.]',
                       '"Hence."', '\\#Hence.'):
            source = '$ A=B $\n\n' + suffix
            with self.subTest(suffix=suffix):
                results = self.check(source, source)
                self.assertTrue(results[GAP].skips)

    def test_cross_line_and_multiple_labels_refuse_without_moving_any_bytes(self):
        for source in ('Thus $ A=B $\n<energy> and.', '$ A=B $\n<energy>\n\nHence.',
                       '$ A=B $ <a> <b> and.', '$ A=B $ <a>\n<b>\n\nHence.'):
            with self.subTest(source=source):
                result = self.check(source, source)
                self.assertTrue(all(r.skips for r in result.values()))

    def test_suppression_equation_anchor_covers_both_boundary_edits(self):
        directive = '// typst-lint: ignore-next pde.standalone-display -- intentional\n'
        source = directive + '$ A=B $ <energy> and.'
        results = self.check(source, source)
        self.assertEqual(len(results[STANDALONE].suppressed), 1)
        directive = '// typst-lint: ignore-next pde.display-continuation-gap -- intentional\n'
        source = directive + '$ A=B $ <energy>\n\nHence.'
        results = self.check(source, source)
        self.assertEqual(len(results[GAP].suppressed), 1)

    def test_directive_on_preceding_text_does_not_own_display(self):
        directive = '// typst-lint: ignore-next pde.standalone-display -- text only\n'
        results = self.check(directive + 'Thus $ A=B $ and.', directive + 'Thus\n$ A=B $\nand.',
                             active=(STANDALONE,))
        self.assertFalse(results[STANDALONE].suppressed)

    def test_following_directive_never_redirected_or_removed(self):
        source = ('$ A=B $\n\n// typst-lint: ignore-next pde.display-continuation-gap -- prose only\n'
                  'Hence the claim.')
        results = self.check(source, source)
        self.assertFalse(results[GAP].suppressed)
        self.assertTrue(results[GAP].skips)

    def test_other_rule_directive_and_semantic_target_remain_unchanged(self):
        source = (b'// typst-lint: ignore-next core.parenthesized-scripts -- intentional\n'
                  b'$ A=x^i $ <energy> and.')
        results, candidate = self.candidate(source)
        self.assertTrue(results[STANDALONE].edits)
        known = {r['id'] for r in load_rules()[0]['rules']}
        for value in (source, candidate):
            tree = parse(value)
            scope, = suppressions(tree, known)
            self.assertEqual(value[scope.start:scope.end], b'$ A=x^i $')
            script = scripts(tree, 'fixture.typ')
            self.assertEqual(len(script.suppressed), 1)
            self.assertFalse(script.edits)

    def test_structural_reinterpretation_is_refused_after_actual_candidate_parse(self):
        for suffix in ('= Heading', '- item', '+ item', '1. item', '/ term: definition'):
            source = ('$ A=B $ ' + suffix).encode()
            with self.subTest(suffix=suffix):
                results, candidate = self.candidate(source)
                self.assertEqual(candidate, source)
                self.assertTrue(results[STANDALONE].diagnostics)
                self.assertTrue(any('structural syntax' in reason for reason in results[STANDALONE].skips))

    def test_second_pass_gap_is_left_for_composition_refusal_not_guessed(self):
        source = b'Thus $ A=B $\n\nHence.'
        results, candidate = self.candidate(source)
        self.assertEqual(candidate, b'Thus\n$ A=B $\n\nHence.')
        self.assertTrue(results[STANDALONE].edits)
        self.assertFalse(results[GAP].edits)
        self.assertTrue(any('already be standalone' in reason for reason in results[GAP].skips))
        self.assertTrue(self.run_source(candidate)[GAP].edits)

    def test_bad_syntax_and_stale_resolution_fail_closed(self):
        for source in (b'Thus $ A=B ', b'$ x^ $\n\nHence.', b'#let = 1\nThus $ A=B $ and.'):
            results = self.run_source(source)
            self.assertTrue(all(r.parse_errors and r.skips and not r.edits for r in results.values()))
        tree = parse(b'Thus $ A=B $ and.')
        resolution = resolve(tree)
        for invalid in (replace(resolution, source_sha256='wrong'), replace(resolution, facts=())):
            results = analyze(tree, 'fixture.typ', invalid)
            self.assertTrue(all(r.skips and not r.edits for r in results.values()))

    def test_non_markup_parser_mode_never_certified_as_prose(self):
        tree = parse(b'let body = $ A=B $', 'code')
        results = analyze(tree, 'fixture.typ', resolve(tree))
        self.assertTrue(all(r.skips and not r.edits for r in results.values()))

    def test_candidate_parser_failure_reports_unavailable_fix(self):
        tree = parse(b'Thus $ A=B $ and.')
        with patch('display_rules.parse', side_effect=AdapterError('fixture parser failure')):
            results = analyze(tree, 'fixture.typ', resolve(tree))
        self.assertTrue(results[STANDALONE].diagnostics)
        self.assertFalse(results[STANDALONE].edits)
        self.assertTrue(any('fix_unavailable' in reason for reason in results[STANDALONE].skips))

    def test_non_ascii_whitespace_and_bare_cr_are_not_guessed(self):
        for source in ('$ A=B $\u00a0and.', '$ A=B $\r\rHence.', '$ A=B $\n\u00a0\nHence.'):
            results, candidate = self.candidate(source.encode())
            self.assertEqual(candidate, source.encode())
            self.assertTrue(any(result.skips for result in results.values()))


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                     and shutil.which('typst') and shutil.which('pdftotext') and EXECUTABLE.is_file(),
                     'Opt in with TYPST_LINT_RENDER_TESTS=1; requires typst and pdftotext')
class DisplayRenderTests(unittest.TestCase):
    def test_fixture_compiles_with_text_order_and_equation_label_preserved(self):
        source = (b'Thus $ A=B $ <energy> and the claim follows.\n'
                  b'$ C=D $\n\nHence the identity holds.\n')
        tree = parse(source)
        results = analyze(tree, 'fixture.typ', resolve(tree))
        candidate = plan(source, [e for r in results.values() for e in r.edits]).candidate()
        self.assertNotEqual(candidate, source)
        texts = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in (('before', source), ('after', candidate)):
                typ, pdf = root / f'{name}.typ', root / f'{name}.pdf'
                typ.write_bytes(value)
                subprocess.run([shutil.which('typst'), 'compile', str(typ), str(pdf)],
                               check=True, capture_output=True, timeout=60)
                text = subprocess.run([shutil.which('pdftotext'), str(pdf), '-'],
                                      check=True, capture_output=True, timeout=30).stdout
                texts.append(text.split())
                query = subprocess.run([shutil.which('typst'), 'query', str(typ), '<energy>'],
                                       check=True, capture_output=True, timeout=60)
                elements = json.loads(query.stdout)
                self.assertEqual(len(elements), 1)
                self.assertEqual(elements[0]['func'], 'equation')
                self.assertTrue(elements[0]['block'])
                self.assertEqual(typ.read_bytes(), value)
        self.assertEqual(texts[0], texts[1])


if __name__ == '__main__':
    unittest.main()
