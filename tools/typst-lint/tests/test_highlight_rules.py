"""Real parser fixtures for bounded highlight extraction and composition."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import plan
from highlight_rules import RULE_ID, analyze
from static_resolver import PackageSource, TEXT_UTILS, resolve
from syntax_client import EXECUTABLE, parse
import source_analysis


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class HighlightRules(unittest.TestCase):
    def run_source(self, source, packages=()):
        tree = parse(source)
        return analyze(tree, 'fixture.typ', resolve(tree, packages=packages))

    def test_exact_split_and_idempotence(self):
        for source, expected in (
            (b'#highlight[The term $x^(2)$ matters.]', b'#highlight[The term ]$x^(2)$#highlight[ matters.]'),
            (b'#highlight[$x$]', b'$x$'),
            (b'#highlight([$x$])', b'$x$'),
            (b'#highlight([A $x$ B],)', b'#highlight[A ]$x$#highlight[ B]'),
            (b'#highlight[$x$$y$]', b'$x$$y$'),
            (b'#highlight[A $x$ B $y$ C]', b'#highlight[A ]$x$#highlight[ B ]$y$#highlight[ C]'),
            ('#highlight[한글 $α$ 끝]'.encode(), '#highlight[한글 ]$α$#highlight[ 끝]'.encode()),
            (b'#highlight[A\r\n$ X=Y $\r\nB]', b'#highlight[A\r\n]$ X=Y $#highlight[\r\nB]'),
        ):
            with self.subTest(source=source):
                result = self.run_source(source)
                self.assertEqual(len(result.diagnostics), 1)
                candidate = plan(source, result.edits).candidate()
                self.assertEqual(candidate, expected, result.skips)
                final = self.run_source(candidate)
                self.assertFalse(final.diagnostics)
                self.assertFalse(final.edits)

    def test_refusals_still_diagnose(self):
        for source in (
            b'#highlight(fill: red)[A $x$]', b'#highlight([A $x$], [B])',
            b'#highlight[A *bold* $x$]', b'#highlight[*bold $x$*]', b'#highlight[A _emph_ $x$]',
            b'#highlight[A // attached\n$x$]', b'#highlight[A /* comment */ $x$]',
            b'#highlight[$x$ <eq>]', b'#highlight[$x$] <label>',
            b'#highlight[A #text[B] $x$]', b'#highlight[$#foo()$]',
            b'#highlight[A\n\n$x$]',
            b'#block[#highlight[$x$]]',
        ):
            with self.subTest(source=source):
                result = self.run_source(source)
                self.assertTrue(result.diagnostics)
                self.assertFalse(result.edits)
                self.assertTrue(any('fix_unavailable' in s for s in result.skips))

    def test_unknown_and_shadowed_are_not_guessed(self):
        for source in (b'#highlighted[$x$]', b'#let highlight(x) = x\n#highlight[$x$]',
                       b'#import "unknown.typ": *\n#highlight[$x$]', b'#highlight[#let x = [$x$]]', b'#highlight(..args)[A $x$]', b'#foo[$x$]', b'#highlight[plain]', b'#highlight(fill: $x$)[plain]', b'#let f() = [#highlight[$x$]]'):
            result = self.run_source(source)
            self.assertFalse(result.diagnostics)
            self.assertFalse(result.edits)

    def test_nested_highlight_does_not_duplicate_outer_diagnosis(self):
        result = self.run_source(b'#highlight[A #highlight[$x$]]')
        self.assertEqual(len(result.diagnostics), 1)
        self.assertFalse(result.edits)

    def test_reviewed_package_alias(self):
        package = PackageSource(TEXT_UTILS, 'reviewed-test', ((TOOL / 'tests/fixtures/reviewed-packages')/'local/text-utils/0.1.2/lib.typ').read_bytes())
        source = b'#import "@local/text-utils:0.1.2": highlighted as hi\n#hi[A $x$ B]'
        result = self.run_source(source, (package,))
        self.assertTrue(result.edits, result.skips)
        self.assertTrue(plan(source, result.edits).candidate().endswith(b'#hi[A ]$x$#hi[ B]'))
        self.assertFalse(self.run_source(source).diagnostics)

    def test_suppression_and_unrelated_directive_ownership(self):
        for rid in (RULE_ID, 'core.parenthesized-scripts', 'pde.paragraph-marker-gap'):
            source = f'// typst-lint: ignore-next {rid} -- intentional\n#highlight[$x$]'.encode()
            result = self.run_source(source)
            self.assertFalse(result.edits)
            self.assertEqual(bool(result.suppressed), rid == RULE_ID)
            self.assertEqual(bool(result.diagnostics), rid != RULE_ID)

    def test_malformed_and_stale_proofs(self):
        self.assertTrue(self.run_source(b'#highlight[$x$').parse_errors)
        tree = parse(b'#highlight[$x$]')
        result = analyze(tree, 'x.typ', resolve(parse(b'other')))
        self.assertFalse(result.edits)
        self.assertIn('Stale', result.skips[0])

    def composed(self, source):
        # Exercise the real verifier with the proposed registry integration,
        # without modifying the parent-owned source_analysis module.
        original = source_analysis._analyze
        def registered(tree, path, pack, fallback_language, packages, reference_catalog, entrypoint, import_graph=None):
            results = original(tree, path, pack, fallback_language, packages, reference_catalog, entrypoint, import_graph)
            if pack == 'pde':
                results[RULE_ID] = analyze(tree, path, resolve(tree, packages=packages, fallback_language=fallback_language))
            return results
        with patch.object(source_analysis, '_analyze', registered):
            return source_analysis.analyze(parse(source), 'fixture.typ', pack='pde')

    def test_script_boundary_edits_compose(self):
        source = b'#highlight[The $x^2$ term.]'
        results = self.composed(source)
        proposals = [e for r in results.values() for e in r.edits]
        self.assertTrue(proposals)
        candidate = plan(source, proposals).candidate()
        self.assertEqual(candidate, b'#highlight[The ]$x^(2)$#highlight[ term.]')
        self.assertFalse(any(r.edits for r in self.composed(candidate).values()))

    def test_display_second_pass_converges_with_exact_attribution(self):
        source = b'Thus #highlight[$ X=Y $] and more.'
        results = self.composed(source)
        candidate = plan(source, [e for r in results.values() for e in r.edits]).candidate()
        self.assertEqual(candidate, b'Thus\n$ X=Y $\nand more.')
        self.assertEqual(results[RULE_ID].repair_passes, 2)
        self.assertFalse(any(r.edits for r in self.composed(candidate).values()))


if __name__ == '__main__':
    unittest.main()
