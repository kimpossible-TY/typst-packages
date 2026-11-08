"""Real AST and reviewed on-disk package bytes; no Typst compiler/evaluation."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from syntax_client import EXECUTABLE, parse
from static_resolver import MATH_BLOCKS, PackageSource, resolve
from package_loader import load_packages
from theme_rules import analyze, analyze_conditional, NO_SET, CONTEXT

PACKAGE = TOOL.parents[1] / 'local/math-blocks/0.2.0/lib.typ'
IMPORT = '#import "@local/math-blocks:0.2.0": theme-from-text-fill, light-theme, dark-theme\n'


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter explicitly first')
class ThemeTests(unittest.TestCase):
    def check(self, source, packages=None):
        tree = parse(source.encode())
        self.assertFalse(tree.nodes[0].errors, source)
        packages = (PackageSource(MATH_BLOCKS, str(PACKAGE), PACKAGE.read_bytes()),) if packages is None else packages
        return analyze(tree, 'fixture.typ', resolve(tree, packages=packages))

    def test_real_package_loader(self):
        result = load_packages(TOOL.parents[1], (MATH_BLOCKS,))
        self.assertEqual(len(result.sources), 1)
        self.assertFalse(result.unavailable)

    def test_palette_targets_and_aliases(self):
        for target in ('light-theme', 'dark-theme', 'theme-from-text-fill'):
            result = self.check(IMPORT + f'#set {target}(fill: red)')
            self.assertEqual(len(result[NO_SET].diagnostics), 1)
            self.assertFalse(result[NO_SET].edits)
        for rhs in ('light-theme', '(dark-theme)', 'theme-from-text-fill()'):
            result = self.check(IMPORT + f'#let theme = {rhs}\n#set theme(fill: red)')
            self.assertEqual(len(result[NO_SET].diagnostics), 1)

    def test_module_rename_and_wildcard(self):
        for source in (
            '#import "@local/math-blocks:0.2.0" as m\n#let f(body) = { m.theme-from-text-fill(); body }',
            '#import "@local/math-blocks:0.2.0": theme-from-text-fill as palette\n#let f(body) = { palette(); body }',
            '#import "@local/math-blocks:0.2.0": *\n#let f(body) = { theme-from-text-fill(); body }',
        ):
            self.assertEqual(len(self.check(source)[CONTEXT].diagnostics), 1)

    def test_reusable_context_and_root(self):
        body = '{ let theme = theme-from-text-fill(); block(fill: theme.page, body) }'
        result = self.check(IMPORT + '#let mybox(body) = ' + body)
        self.assertEqual(len(result[CONTEXT].diagnostics), 1)
        self.assertFalse(result[CONTEXT].edits)
        self.assertTrue(any('fix_unavailable' in s for s in result[CONTEXT].skips))
        for code in ('#let mybox(body) = context ' + body,
                     '#let mybox(body) = { context { theme-from-text-fill() }; body }',
                     '#let theme = theme-from-text-fill()'):
            self.assertFalse(self.check(IMPORT + code)[CONTEXT].diagnostics)

    def test_no_spelling_based_proof(self):
        code = '#let mybox(body) = { theme-from-text-fill(); body }\n#set light-theme(fill: red)'
        for prefix, packages in ((IMPORT, ()), ('', None),
                                  ('#import "facade.typ": *\n', None),
                                  (IMPORT.replace('0.2.0', '0.1.0'), None)):
            result = self.check(prefix + code, packages)
            self.assertFalse(result[NO_SET].diagnostics)
            self.assertFalse(result[CONTEXT].diagnostics)
        stale = (PackageSource(MATH_BLOCKS, str(PACKAGE), PACKAGE.read_bytes() + b'\n'),)
        self.assertFalse(self.check(IMPORT + code, stale)[CONTEXT].diagnostics)

    def test_shadowing_and_mutation(self):
        for code in (
            '#let f(theme-from-text-fill) = { theme-from-text-fill(); [ok] }',
            '#let f(body) = { let theme-from-text-fill = () => red; theme-from-text-fill(); body }',
            '#let f(body) = { theme-from-text-fill = () => red; theme-from-text-fill(); body }',
            '#let theme-from-text-fill = () => red\n#let f(body) = { theme-from-text-fill(); body }',
            '#import "unknown.typ": *\n#let f(body) = { theme-from-text-fill(); body }',
            '#let f(theme-from-text-fill: none) = { theme-from-text-fill(); [ok] }',
        ):
            self.assertFalse(self.check(IMPORT + code)[CONTEXT].diagnostics, code)
        result = self.check(IMPORT + '#let theme = light-theme\n#{theme.page = red}\n#set theme(fill: red)')
        self.assertFalse(result[NO_SET].diagnostics)

    def test_settable_elements_and_unknown_values(self):
        result = self.check(IMPORT + '#set page(fill: light-theme.page)\n#set text(fill: dark-theme.text)')
        self.assertFalse(result[NO_SET].diagnostics)
        for rhs in ('(page: white)', 'theme-from-text-fill(unknown: true)', 'unknown()'):
            self.assertFalse(self.check(IMPORT + f'#let theme = {rhs}\n#set theme(fill: red)')[NO_SET].diagnostics)

    def test_local_import_in_helper_and_palette_capture(self):
        result = self.check('#let f(body) = { import "@local/math-blocks:0.2.0": theme-from-text-fill; theme-from-text-fill(); body }')
        self.assertEqual(len(result[CONTEXT].diagnostics), 1)
        self.assertEqual(len(self.check(IMPORT + '#let theme = light-theme\n#let f() = { set theme(fill: red) }')[NO_SET].diagnostics), 1)

    def test_non_content_helpers_never_fixed(self):
        for body in ('{ theme-from-text-fill() }', '{ return theme-from-text-fill() }',
                     '{ let x = theme-from-text-fill(); x = none; x }'):
            result = self.check(IMPORT + '#let f() = ' + body)[CONTEXT]
            self.assertEqual(len(result.diagnostics), 1)
            self.assertFalse(result.edits)

    def test_suppression_and_unicode_locations(self):
        source = IMPORT + '// typst-lint: ignore-next core.no-set-theme -- example\n#set light-theme(fill: red)\n한글 #set dark-theme(fill: red)'
        result = self.check(source)[NO_SET]
        self.assertEqual(len(result.suppressed), 1)
        self.assertEqual(len(result.diagnostics), 1)
        self.assertEqual((result.diagnostics[0].line, result.diagnostics[0].column), (4, 5))
        result = self.check(IMPORT + '// typst-lint: ignore-next core.theme-in-context -- caller supplies context\n#let f() = { theme-from-text-fill() }')[CONTEXT]
        self.assertFalse(result.diagnostics)
        self.assertEqual(len(result.suppressed), 1)

    def test_deferred_mutations_do_not_change_outer_palette(self):
        result = self.check(IMPORT + '#let theme = light-theme\n#let f() = { theme = none }\n#set theme(fill: red)')
        self.assertEqual(len(result[NO_SET].diagnostics), 1)
        result = self.check(IMPORT + '#let f() = { theme-from-text-fill() }\n#let theme-from-text-fill = () => red')
        self.assertEqual(len(result[CONTEXT].diagnostics), 1)

    def test_default_arguments_are_not_helper_body(self):
        result = self.check(IMPORT + '#let f(theme: theme-from-text-fill()) = [ok]')
        self.assertFalse(result[CONTEXT].diagnostics)

    def test_malformed_and_stale_resolution(self):
        tree = parse(b'#let f( = {')
        result = analyze(tree, 'bad.typ', resolve(tree))
        self.assertTrue(result[CONTEXT].parse_errors)
        tree = parse((IMPORT + '#set light-theme(fill: red)').encode())
        resolution = replace(resolve(tree), source_sha256='stale')
        self.assertFalse(analyze(tree, 'stale.typ', resolution)[NO_SET].diagnostics)

    def test_conditional_unresolved_facade_call_and_context_boundary(self):
        source = ('#import "styles.typ": theme-from-text-fill\n'
                  '#let first() = { theme-from-text-fill() }\n'
                  '#let second() = context { theme-from-text-fill() }\n'
                  '#let third(v: theme-from-text-fill()) = [ok]\n'
                  '#let fourth() = { theme-from-text-fill(1) }\n'
                  '#let fifth() = { theme-from-text-fill.with() }\n'
                  '#let value = theme-from-text-fill()\n')
        tree = parse(source.encode())
        self.assertFalse(tree.nodes[0].errors)
        result = analyze_conditional(tree, 'fixture.typ', resolve(tree))
        self.assertEqual(len(result.diagnostics), 1)
        self.assertEqual(result.diagnostics[0].line, 2)
        self.assertIn('if this unresolved name binds', result.diagnostics[0].explanation)
        self.assertFalse(result.edits)

    def test_conditional_excludes_proven_selector_to_avoid_duplicate(self):
        tree = parse((IMPORT + '#let f() = { theme-from-text-fill() }').encode())
        packages = (PackageSource(MATH_BLOCKS, str(PACKAGE), PACKAGE.read_bytes()),)
        result = analyze_conditional(tree, 'fixture.typ', resolve(tree, packages=packages))
        self.assertFalse(result.diagnostics)

    def test_conditional_shadow_is_explicitly_unproved_and_suppression_applies(self):
        source = ('#let theme-from-text-fill() = none\n'
                  '// typst-lint: ignore-next core.theme-in-context -- unrelated helper\n'
                  '#let f() = { theme-from-text-fill() }\n')
        tree = parse(source.encode())
        result = analyze_conditional(tree, 'fixture.typ', resolve(tree))
        self.assertFalse(result.diagnostics)
        self.assertEqual(len(result.suppressed), 1)
        self.assertIn('binding', result.skips[0])

    def test_conditional_parse_and_resolution_refusals(self):
        tree = parse(b'#let f( = {')
        self.assertTrue(analyze_conditional(tree, 'bad.typ', resolve(tree)).parse_errors)
        tree = parse(b'#let f() = { theme-from-text-fill() }')
        resolution = replace(resolve(tree), source_sha256='stale')
        self.assertFalse(analyze_conditional(tree, 'fixture.typ', resolution).diagnostics)


if __name__ == '__main__':
    unittest.main()
