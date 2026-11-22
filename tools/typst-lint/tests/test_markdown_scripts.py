"""Real pinned-parser examples and byte-preserving Markdown proposals."""
from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import plan
import markdown_scripts as markdown
from script_rule import RULE_ID, analyze as scripts
from suppression import SuppressionError
from syntax_client import EXECUTABLE, parse
from typst_lint import InputError

DIRECTIVE = b'// typst-lint: ignore-next core.parenthesized-scripts -- intentional example'


class InputContractTests(unittest.TestCase):
    def test_immutable_source_path_and_context(self):
        context = markdown.MarkdownContext(b'ordinary prose\n', 'guide.md')
        self.assertNotIn('ordinary prose', repr(context))
        with self.assertRaises(FrozenInstanceError):
            context.source = b'changed'
        for raw in (bytearray(b'x'), 'x', b'\xff', b'x' * (markdown.MAX_INPUT + 1)):
            with self.subTest(raw_type=type(raw)), self.assertRaises(InputError):
                markdown.extract(raw)
        for path in ('guide.typ', '../guide.md', '/guide.md'):
            with self.subTest(path=path), self.assertRaises(InputError):
                markdown.MarkdownContext(b'x', path)
        with self.assertRaises(InputError):
            markdown.MarkdownContext(b'x', 'guide.md', {RULE_ID})

    def test_bare_cr_is_partial_without_normalization(self):
        source = b'$x^i$\r$x_j$'
        result = markdown.extract(source)
        self.assertEqual(result.examples, ())
        self.assertIn('Bare CR', result.skips[0])


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter explicitly first')
class MarkdownScriptsTests(unittest.TestCase):
    def check(self, source, expected, active, suppressed=0, *, partial=False):
        if isinstance(source, str):
            source = source.encode('utf-8')
        if isinstance(expected, str):
            expected = expected.encode('utf-8')
        result = markdown.MarkdownContext(source, 'docs/guide.md').analyze()
        self.assertEqual(len(result.diagnostics), active, result)
        self.assertEqual(len(result.suppressed), suppressed, result)
        self.assertEqual(result.parse_errors, (), result)
        self.assertEqual(bool(result.skips), partial, result.skips)
        self.assertEqual(plan(source, result.edits).candidate(), expected, result.skips)
        final = markdown.analyze(expected, 'docs/guide.md')
        self.assertFalse(final.edits, final)
        self.assertFalse(final.diagnostics, final)
        self.assertEqual(len(final.suppressed), suppressed)
        self.assertEqual(final.parse_errors, ())
        for edit in result.edits:
            self.assertEqual(edit.start, edit.end)
            self.assertFalse(edit.replacement.strip(b'()'))
            self.assertTrue(any(e.start <= edit.start <= e.end for e in result.extraction.examples))
        return result

    def test_fences_bare_and_code_span_examples(self):
        source = b'# Guide\n```typst\n$X^i_j$\n```\nInline $Y^k$ and `$Z_l$`.\n'
        expected = b'# Guide\n```typst\n$X^(i)_(j)$\n```\nInline $Y^(k)$ and `$Z_(l)$`.\n'
        result = self.check(source, expected, 4)
        self.assertEqual([e.kind for e in result.extraction.examples],
                         ['typst-fence', 'inline-math', 'inline-code-math'])
        spans = [source[e.start:e.end] for e in result.extraction.examples]
        self.assertEqual(spans, [b'$X^i_j$\n', b'$Y^k$', b'$Z_l$'])

    def test_tilde_longer_closer_and_exact_operand_shapes(self):
        source = '~~~typst\n$x^y^z + x_i\' + x^a/b + α^β$\n~~~~ \t\n'
        expected = '~~~typst\n$x^(y^(z)) + x_(i\') + x^(a)/b + α^(β)$\n~~~~ \t\n'
        self.check(source, expected, 5)
        self.check('````typst\n$x^(i)_(j)$\n`````\n', '````typst\n$x^(i)_(j)$\n`````\n', 0)

    def test_bom_crlf_unicode_original_positions(self):
        source = '\ufeffGuide 한글 `$α^β$`.\r\n```typst\r\nText $γ_δ$.\r\n```\r\n'
        expected = '\ufeffGuide 한글 `$α^(β)$`.\r\n```typst\r\nText $γ_(δ)$.\r\n```\r\n'
        result = self.check(source, expected, 2)
        self.assertEqual([(d.line, d.column) for d in result.diagnostics], [(1, 15), (3, 9)])
        self.assertEqual(result.extraction.examples[0].start, source.encode().index(b'$'))

    def test_teaching_sentence_exact_and_only_its_bytes_exempted(self):
        teaching = markdown.TEACHING_SENTENCE
        source = teaching + b' Nearby `$Z^k$`.\n$Q_j$\n'
        expected = teaching + b' Nearby `$Z^(k)$`.\n$Q_(j)$\n'
        result = self.check(source, expected, 2)
        self.assertEqual(result.extraction.exemptions, ((0, len(teaching)),))
        for changed in (teaching.replace(b'Do', b'do', 1), teaching[:-1] + b'!',
                        teaching.replace(b'such as', b'like')):
            with self.subTest(changed=changed):
                result = markdown.analyze(changed, 'guide.md')
                self.assertEqual(len(result.diagnostics), 2)
                self.assertEqual(result.extraction.exemptions, ())

    def test_no_keyword_based_bad_example_exemptions(self):
        self.check(b'Incorrect: `$X^i$`; bad: $Y_j$.',
                   b'Incorrect: `$X^(i)$`; bad: $Y_(j)$.', 2)

    def test_fence_strings_comments_raw_and_unresolved_import_are_data(self):
        prefix = (b'```typst\n#import "missing.typ": *\n'
                  b'#let sample = "$X^i$"\n// $Y_j$\n/* $Z^k$ */\n`$Q_l$`\n')
        self.check(prefix + b'$A^b$\n```\n', prefix + b'$A^(b)$\n```\n', 1)
        self.check(b'```python\n$x^i$\n'+DIRECTIVE+b'\n```\n$y^j$',
                   b'```python\n$x^i$\n'+DIRECTIVE+b'\n```\n$y^(j)$', 1)

    def test_math_string_dollars_and_escaped_dollars_use_actual_parser(self):
        self.check(b'Inline $"cost $5"^i + x\\$y^j$.',
                   b'Inline $"cost $5"^(i) + x\\$y^(j)$.', 2)

    def test_multibacktick_exact_equation_code_span(self):
        self.check(b'Use ``$x^i$`` here and `$y_j$`.',
                   b'Use ``$x^(i)$`` here and `$y_(j)$`.', 2)

    def test_fence_directive_targets_real_complete_construct_then_expires(self):
        source = b'```typst\n'+DIRECTIVE+b'\n$\n x^i + y_j\n$\n$z^k$\n```\n$u_v$'
        expected = b'```typst\n'+DIRECTIVE+b'\n$\n x^i + y_j\n$\n$z^(k)$\n```\n$u_(v)$'
        self.check(source, expected, 2, 2)

    def test_nested_fence_suppression_and_new_fence_do_not_leak(self):
        prefix = b'```typst\n#box[\n'+DIRECTIVE+b'\n$x^i$\n$y^j$\n]\n```\n'
        source = prefix+b'```typst\n$z^k$\n```'
        expected = prefix.replace(b'$y^j$',b'$y^(j)$')+b'```typst\n$z^(k)$\n```'
        self.check(source, expected, 2, 1)

    def test_fence_configuration_errors_map_original_line_and_ignore_cannot_hide(self):
        for directive in (DIRECTIVE.replace(b'-- intentional example', b'--'),
                          DIRECTIVE.replace(b'core.parenthesized-scripts', b'unknown.rule'), DIRECTIVE):
            ending = b'\n```' if directive == DIRECTIVE else b'\n$x^i$\n```'
            with self.subTest(directive=directive), self.assertRaises(SuppressionError) as failure:
                markdown.analyze(b'Guide\n\n```typst\n'+directive+ending,
                                 'guide.md', ignored=frozenset({RULE_ID}))
            self.assertIn('at line 4:', str(failure.exception))

    def test_config_ignore_withholds_edits_but_preserves_findings(self):
        result = markdown.MarkdownContext(b'$x^i$\n`$y_j$`', 'guide.md', frozenset({RULE_ID})).analyze()
        self.assertEqual(len(result.diagnostics), 2)
        self.assertEqual(result.edits, ())
        self.assertEqual(result.suppressed, ())

    def test_closed_parse_failures_are_unsuppressible_and_block_all_edits(self):
        for source in (b'$x^i$\n`$y^$`', b'$x^i$\n```typst\n$y^$\n```',
                       b'```typst\n#let =\n```\n$x^i$'):
            with self.subTest(source=source):
                result = markdown.analyze(source, 'guide.md')
                self.assertTrue(result.parse_errors)
                self.assertFalse(result.edits)
                ignored = markdown.analyze(source, 'guide.md', ignored=frozenset({RULE_ID}))
                self.assertEqual(ignored.parse_errors, result.parse_errors)
                self.assertFalse(ignored.edits)
        result = markdown.analyze(b'Heading\n`$x^$`', 'guide.md')
        self.assertEqual(result.parse_errors, ((12, 12),))

    def test_script_backend_conditional_refusals_stay_partial(self):
        for equation in (b'$x^#i$', b'$x^/* comment */i$'):
            with self.subTest(equation=equation):
                self.check(b'```typst\n'+equation+b'\n```',
                           b'```typst\n'+equation+b'\n```', 0, partial=True)

    def test_teaching_sentence_does_not_change_actual_typst_backend(self):
        result = scripts(parse(b'$X^i$\n$X_j$'), 'guide.typ')
        self.assertEqual(len(result.diagnostics), 2)
        self.assertEqual(plan(b'$X^i$\n$X_j$', result.edits).candidate(), b'$X^(i)$\n$X_(j)$')

    def test_prose_capitalization_and_markers_are_never_enabled(self):
        self.check(b'#paragraph_tab\nthis remains lowercase. $x^i$\n',
                   b'#paragraph_tab\nthis remains lowercase. $x^(i)$\n', 1)

    def test_unsupported_markdown_is_quarantined_not_guessed(self):
        cases = [b'$$x^i$$\n$y_j$', b'$$\n$x^i$\n$$\n$y_j$',
                 b'`ordinary $x^i$`\n$y_j$', b'`$x^i$\n$z^k$`\n$y_j$',
                 b'- item\n  $x^i$\n\n$y_j$', b'    $x^i$\n\n$y_j$',
                 b'<div>\n$x^i$\n</div>\n\n$y_j$',
                 b'[link]($x^i$)\n\n$y_j$', b'---\nvalue: $x^i$\n---\n$y_j$',
                 b'```typst options\n$x^i$\n```\n$y_j$']
        for source in cases:
            with self.subTest(source=source):
                self.check(source, source.replace(b'$y_j$', b'$y_(j)$'), 1, partial=True)

    def test_unclosed_fences_and_spans_do_not_expose_their_contents(self):
        for source in (b'```typst\n$x^i$', b'```python\n$x^i$',
                       b'`example\n$x^i$', b'$$\n$x^i$', b'$unclosed\n$x^i$'):
            with self.subTest(source=source):
                self.check(source, source, 0, partial=True)

    def test_unsupported_container_fences_never_guess_an_unrelated_prefix_close(self):
        for source in (b' ```typst\n- ```\n$X^i$\n ```\n',
                       b' ```python\n> ```\n$X^i$\n ```\n',
                       b' ~~~typst\n- ~~~\n$X^i$\n ~~~\n',
                       b'> ```typst\n> $x^i$\n> ```\n\n$y_j$'):
            with self.subTest(source=source):
                self.check(source, source, 0, partial=True)

    def test_html_comments_raw_tags_and_display_dollars_keep_multiline_quarantine(self):
        for hidden in (b'<!--\n\n$x^i$\n-->', b'<script>\n\n$x^i$\n</script>',
                       b'<pre>\n</prefix>\n$x^i$\n</pre>',
                       b'<script>\n</scripture>\n$x^i$\n</script>',
                       b'<![CDATA[\n\n$x^i$\n]]>', b'<?xml\n\n$x^i$\n?>',
                       b'$$\n\\$$ still hidden $x^i$\n$$'):
            source = hidden + b'\n\n$y_j$'
            with self.subTest(hidden=hidden):
                self.check(source, hidden + b'\n\n$y_(j)$', 1, partial=True)

    def test_link_labels_reference_forms_and_multiline_destinations_are_quarantined(self):
        for source in (b'[$X^i$](url)\n', b'![$X^i$](image.png)\n',
                       b'[$X^i$][ref]\n\n[ref]: /a\n',
                       b'[$X^i$]\n\n[$X^i$]: /a\n',
                       b'[$X^i$]\n($Y_j$)\n',
                       b'$Z^k$ before [$X^i$](url)\n'):
            with self.subTest(source=source):
                self.check(source, source, 0, partial=True)

    def test_unclosed_bare_math_never_guesses_a_quoted_dollar_recovery(self):
        for source in (b'$\n"$" $X^i$\n', b'$x^i\n+ z^k$\n$y_j$',
                       b'$\n\n$x^i$\n```typst\n$y_j$\n```'):
            with self.subTest(source=source):
                self.check(source, source, 0, partial=True)

    def test_probe_budget_never_splits_utf8(self):
        source = '$x^i$ 한글'.encode()
        with patch.object(markdown, 'MAX_EXAMPLE_BYTES', 8):
            self.check(source, '$x^(i)$ 한글'.encode(), 1)

    def test_example_limits_are_explicit_and_no_parser_input_is_truncated(self):
        with patch.object(markdown, 'MAX_EXAMPLES', 1):
            result = markdown.analyze(b'$x^i$ $y_j$', 'guide.md')
            self.assertEqual(len(result.extraction.examples), 1)
            self.assertTrue(any('count limit' in s for s in result.skips))
            self.assertEqual(plan(b'$x^i$ $y_j$', result.edits).candidate(), b'$x^(i)$ $y_j$')
        with patch.object(markdown, 'MAX_EXAMPLE_BYTES', 8):
            for source in (b'```typst\n$longname^alpha$\n```', b'`$longname^alpha$`'):
                with self.subTest(source=source):
                    result = markdown.analyze(source, 'guide.md')
                    self.assertFalse(result.diagnostics or result.edits)
                    self.assertTrue(any('64 KiB' in s for s in result.skips))

    def test_candidate_source_limit_withholds_insertions(self):
        source = b'$x^i$'
        with patch.object(markdown, 'MAX_INPUT', len(source)):
            result = markdown.analyze(source, 'guide.md')
            self.assertEqual(len(result.diagnostics), 1)
            self.assertFalse(result.edits)
            self.assertTrue(any('candidate exceeds' in s for s in result.skips))

    def test_extraction_attempt_budget_counts_failed_probes_not_only_examples(self):
        source = b'`$x$ $y$` ' * 20
        original = markdown.parse
        with patch.object(markdown, 'MAX_ATTEMPTS', 3), patch.object(markdown, 'parse', wraps=original) as probe:
            result = markdown.extract(source)
        self.assertEqual(probe.call_count, 3)
        self.assertFalse(result.examples)
        self.assertTrue(any('budget exhausted' in s for s in result.skips))
        source = b'$x^i$ ' * 20
        with patch.object(markdown, 'MAX_ATTEMPTS', 3), patch.object(markdown, 'parse', wraps=original) as probe:
            result = markdown.extract(source)
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(len(result.examples), 1)
        self.assertTrue(any('budget exhausted' in s for s in result.skips))

    def test_reason_count_and_line_recount_cost_are_bounded(self):
        source = b'> opaque\n' * 20_000
        original = markdown._line
        with patch.object(markdown, '_line', wraps=original) as line:
            result = markdown.extract(source)
        self.assertEqual(len(result.skips), markdown.MAX_REASONS + 1)
        self.assertEqual(result.skips[-1], markdown.REASON_LIMIT)
        self.assertLessEqual(line.call_count, markdown.MAX_REASONS)
        self.assertFalse(result.examples)

    def test_long_backslash_and_container_runs_do_not_rescan_each_prefix(self):
        source = b'\\' * markdown.MAX_INPUT
        original = markdown._escaped
        with patch.object(markdown, '_escaped', wraps=original) as escaped:
            result = markdown.extract(source)
        self.assertEqual(escaped.call_count, 0)
        self.assertFalse(result.examples or result.skips)
        source = b'>' * markdown.MAX_INPUT
        self.assertEqual(markdown._container(source), b'')
        result = markdown.extract(source)
        self.assertFalse(result.examples)
        self.assertEqual(len(result.skips), 1)

    def test_extraction_metadata_is_frozen_and_exact(self):
        result = markdown.extract(b'Before `$x^i$`.\n')
        with self.assertRaises(FrozenInstanceError):
            result.examples = ()
        with self.assertRaises(FrozenInstanceError):
            result.examples[0].start = 0
        self.assertEqual((result.examples[0].start, result.examples[0].end), (8, 13))


if __name__ == '__main__':
    unittest.main()
