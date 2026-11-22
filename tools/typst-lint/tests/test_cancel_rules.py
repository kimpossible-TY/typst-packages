"""Cancellation identity/refusals use only the real pinned official syntax tree."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from cancel_rules import RULE_ID, analyze
from source_analysis import analyze as analyze_source
from static_resolver import (PackageSource, TEXT_UTILS, MATH_BLOCKS,
                             is_math_cancel_binding, resolve)
from syntax_client import EXECUTABLE, parse


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter explicitly first')
class CancelRuleTests(unittest.TestCase):
    def check(self, source, *, mode='markup', packages=()):
        tree = parse(source.encode(), mode)
        self.assertFalse(tree.nodes[0].errors, source)
        resolution = resolve(tree, packages=packages)
        result = analyze(tree, 'fixture.typ', resolution)
        self.assertFalse(result.edits)
        return tree, resolution, result

    def assert_count(self, source, count, **kwargs):
        tree, resolution, result = self.check(source, **kwargs)
        self.assertEqual(len(result.diagnostics), count, (source, result.skips))
        return tree, resolution, result

    def test_actual_math_call_and_nested_calls(self):
        for source, count in (
            ('$cancel(x)$', 1),
            ('$ cancel(x) + cancel(y, cross: #true, stroke: #blue) $', 2),
            ('$frac(cancel(x), cancel(y))$', 2),
            ('#[$cancel(x)$]', 1),
            ('#{ let x = 1; $cancel(x)$ }', 1),
        ):
            with self.subTest(source=source):
                _, _, result = self.assert_count(source, count)
                self.assertTrue(any('fix_unavailable' in reason for reason in result.skips))

    def test_value_and_module_aliases_and_named_builtin_imports(self):
        for source in (
            '#let crossout = math.cancel\n$crossout(x)$',
            '#let crossout = (math.cancel)\n$crossout(x)$',
            '#let maths = (math)\n$maths.cancel(x)$',
            '#let maths = math\n#import maths\n$maths.cancel(x)$',
            '#import math\n$math.cancel(x)$',
            '#import math as maths\n$maths.cancel(x)$',
            '#import math: cancel as crossout\n$crossout(x)$',
            '#let maths = math\n#import maths: cancel as crossout\n$crossout(x)$',
            '#import math: cancel\n$cancel(x)$',
            '#let math = math\n$math.cancel(x)$',
            '#let crossout = math.cancel\n$#crossout($x$)$',
            '#let c = math.cancel\n$#c($x$)$',
            '$#math.cancel($x$)$',
        ):
            with self.subTest(source=source):
                self.assert_count(source, 1)

    def test_unaliased_builtin_import_preserves_source_identifier(self):
        for prefix in (
            '#let maths = math\n#import maths\n',
            '#let maths = math\n#import "shadow.typ" as math\n#import maths\n',
        ):
            self.assert_count(prefix + '$math.cancel(x)$', 0)
            self.assert_count(prefix + '$maths.cancel(x)$', 1)
        # No invented default destination for a computed/parenthesized source.
        self.assert_count('#import (math)\n$math.cancel(x)$', 0)
        self.assert_count('#import (math) as maths\n$maths.cancel(x)$', 1)

    def test_math_and_code_fallbacks_are_distinct(self):
        for source in (
            '#cancel[$x$]', '$#cancel($x$)$', '$math.cancel(x)$',
            '#let crossout = cancel\n$crossout(x)$',
            '#let c = math.cancel\n$c(x)$',  # Single-letter math text, not a call.
            '#let crossout = math.cancel\n#crossout[$x$]',
            '#let maths = math\n#maths.cancel[$x$]',
        ):
            with self.subTest(source=source):
                self.assert_count(source, 0)
        self.assert_count('cancel(x)', 1, mode='math')
        _, resolution, _ = self.assert_count('cancel(x)', 0, mode='code')
        self.assertFalse(any(is_math_cancel_binding(f.binding) for f in resolution.facts))
        _, resolution, _ = self.assert_count('#cancel[$x$]', 0)
        self.assertFalse(any(is_math_cancel_binding(f.binding) for f in resolution.facts))

    def test_locals_shadow_math_fallback_but_not_global_math_library(self):
        for prefix in (
            '#let cancel(body) = body\n', '#let cancel = none\n',
            '#let (cancel, other) = (none, 1)\n',
            '#let (key: cancel) = (key: none)\n',
            '#import "unknown.typ": cancel\n',
        ):
            with self.subTest(prefix=prefix):
                self.assert_count(prefix + '$cancel(x)$', 0)
        self.assert_count('#let math = none\n$cancel(x)$', 1)
        self.assert_count('#let math = none\n#let crossout = math.cancel\n$crossout(x)$', 0)
        self.assert_count('#{ let cancel = none; $cancel(x)$ }\n$cancel(y)$', 1)

    def test_unknown_wildcards_invalidate_names_and_builtin_fallbacks(self):
        for source in (
            '#import "unknown.typ": *\n$cancel(x)$',
            '#let crossout = math.cancel\n#import "unknown.typ": *\n$crossout(x)$',
            '#import "unknown.typ": *\n#let crossout = math.cancel\n$crossout(x)$',
            '#let maths = math\n#import "unknown.typ": *\n$maths.cancel(x)$',
        ):
            _, _, result = self.assert_count(source, 0)
            self.assertTrue(any('shadowed mathematical callee' in s for s in result.skips))
        self.assert_count('#{ import "unknown.typ": *; $cancel(x)$ }\n$cancel(y)$', 1)
        self.assert_count('#import "unknown.typ": unrelated\n$cancel(x)$', 1)
        self.assert_count('#import math: *\n$cancel(x)$', 1)
        self.assert_count('#let saved = math.cancel\n#import math: *\n$saved(x)$', 0)
        # The complete reviewed namespace excludes cancel; partial package
        # namespaces cannot establish absence of a same-named export.
        fixture = TOOL / 'tests/fixtures/reviewed-packages'
        packages = tuple(PackageSource(path, str(fixture / relative),
                                       (fixture / relative).read_bytes())
                         for path, relative in (
                             (TEXT_UTILS, 'local/text-utils/0.1.2/lib.typ'),
                             (MATH_BLOCKS, 'local/math-blocks/0.2.0/lib.typ')))
        self.assert_count(f'#import "{TEXT_UTILS}": *\n$cancel(x)$', 1, packages=packages)
        self.assert_count(f'#import "{MATH_BLOCKS}": *\n$cancel(x)$', 0, packages=packages)

    def test_mutations_invalidate_aliases_but_saved_values_remain(self):
        for effect in (
            '#{ crossout = none }', '#{ crossout += none }',
            '#if maybe { crossout = none }',
            '#for x in (1, 2) { crossout = none }',
            '#while maybe { crossout = none }',
            '#let other = none\n#{ (crossout, other) = (none, none) }',
        ):
            self.assert_count('#let crossout = math.cancel\n' + effect + '\n$crossout(x)$', 0)
        self.assert_count('#let crossout = math.cancel\n#let saved = crossout\n'
                          '#{ crossout = none }\n$saved(x)$', 1)
        self.assert_count('#let maths = math\n#{ maths.cancel = none }\n$maths.cancel(x)$', 0)
        self.assert_count('#let crossout = math.cancel\n$crossout(x)$\n'
                          '#let crossout = none\n$crossout(y)$', 1)
        # The callee is captured before arguments can mutate its source name.
        self.assert_count('#let crossout = math.cancel\n'
                          '$#crossout({ crossout = none; $x$ })$\n$crossout(y)$', 1)

    def test_dynamic_deferred_and_returned_values_are_not_inferred(self):
        for source in (
            '#let helper() = $cancel(x)$',
            '#let helper(cancel) = $cancel(x)$',
            '#let helper(cancel: none) = $cancel(x)$',
            '#context [$cancel(x)$]',
            '#if true [$cancel(x)$]',
            '#for n in (1, 2) [$cancel(x)$]',
            '#while false [$cancel(x)$]',
            '#let crossout = unknown(math.cancel)\n$crossout(x)$',
            '#let crossout = if true { math.cancel } else { math.cancel }\n$crossout(x)$',
            '#let crossout = math.cancel.with(cross: true)\n$crossout(x)$',
        ):
            self.assert_count(source, 0)
        _, _, result = self.assert_count('#let helper() = $cancel(x)$', 0)
        self.assertTrue(any('Deferred or dynamic mathematical call' in s for s in result.skips))
        self.assert_count('#let helper() = { let cancel = none }\n$cancel(x)$', 1)

    def test_same_spelling_helpers_and_intentional_options_are_untouched(self):
        for source in (
            '#let cancel(body) = body\n$cancel(x, stroke: #blue)$',
            '#let cancelmark(body, ..options) = body\n'
            '$cancelmark(x) + cancelmark(y, cross: #true, stroke: #blue)$',
            '#import "facade.typ": cancelmark\n$cancelmark(x, stroke: #blue)$',
            '#let obj = (cancel: body => body)\n$(obj.cancel)(x)$',
        ):
            self.assert_count(source, 0)
        # Name alone cannot turn a known builtin into the absent facade helper.
        self.assert_count('#let cancelmark = math.cancel\n$cancelmark(x)$', 1)

    def test_suppression_exact_ownership_unicode_and_crlf(self):
        source = ('\ufeff// typst-lint: ignore-next pde.theme-cancelmark -- example\r\n'
                  '$cancel(x) + cancel(y)$\r\n한글 $cancel(z)$\r\n')
        _, _, result = self.assert_count(source, 1)
        self.assertEqual(len(result.suppressed), 2)
        self.assertEqual((result.diagnostics[0].line, result.diagnostics[0].column), (3, 5))
        self.assertEqual(sum('fix_unavailable' in s for s in result.skips), 1)

    def test_malformed_and_stale_resolution_refuse(self):
        tree = parse(b'$cancel($')
        result = analyze(tree, 'bad.typ', resolve(tree))
        self.assertTrue(result.parse_errors)
        self.assertFalse(result.diagnostics or result.edits)
        tree = parse(b'$cancel(x)$')
        resolution = resolve(tree)
        for stale in (replace(resolution, source_sha256='stale'),
                      replace(resolution, facts=resolution.facts[:-1])):
            result = analyze(tree, 'stale.typ', stale)
            self.assertFalse(result.diagnostics or result.edits)
            self.assertIn('Stale or mismatched', result.skips[0])

    def test_composed_repairs_preserve_unresolved_cancellation_and_suppression(self):
        for suppressed in (False, True):
            directive = '// typst-lint: ignore-next pde.theme-cancelmark -- example\n' if suppressed else ''
            source = b'$x^i$\n' + (directive + '$cancel(x)$\n').encode()
            result = analyze_source(parse(source), 'main.typ', pack='pde')
            self.assertTrue(result['core.parenthesized-scripts'].edits)
            self.assertEqual(len(result[RULE_ID].suppressed), int(suppressed))
            self.assertEqual(len(result[RULE_ID].diagnostics), int(not suppressed))
            self.assertFalse(result[RULE_ID].edits)


if __name__ == '__main__':
    unittest.main()
