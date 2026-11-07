"""All binding/language assertions use the official real Typst AST.

The explicit package fixture is read only; no compiler, package lookup, import
execution, download or installation is involved in these tests.
"""
from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from syntax_client import EXECUTABLE, parse
from static_resolver import PackageSource, TEXT_UTILS, imported_paths, resolve
from typst_lint import InputError

PACKAGE_FILE = TOOL.parents[1] / 'local/text-utils/0.1.2/lib.typ'
IMPORT = '#import "@local/text-utils:0.1.2": paragraph_tab, apply-paragraph-tabs, highlighted\n'


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter explicitly first')
class ResolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package = PackageSource(TEXT_UTILS, str(PACKAGE_FILE), PACKAGE_FILE.read_bytes())

    def check(self, source, *, fallback='en', packages=None):
        tree = parse(source.encode())
        self.assertFalse(tree.nodes[0].errors, source)
        return tree, resolve(tree, fallback_language=fallback,
                             packages=(self.package,) if packages is None else packages)

    def facts(self, result, kind, text):
        tree, resolved = result
        return [resolved.facts[i] for i, n in enumerate(tree.nodes)
                if n.kind == kind and tree.source[n.start:n.end].decode() == text]

    def last(self, result, kind='Ident', text='paragraph_tab'):
        return self.facts(result, kind, text)[-1]

    def test_explicit_verified_package_is_required(self):
        source = IMPORT + '#paragraph_tab\ntherefore this follows.'
        result = self.check(source)
        fact = self.last(result)
        self.assertEqual((fact.binding.kind, fact.binding.name, fact.binding.package),
                         ('api', 'paragraph_tab', TEXT_UTILS))
        self.assertEqual(fact.binding.source_identity, str(PACKAGE_FILE))
        self.assertTrue(self.last(result, 'Text', 'therefore this follows.').prose_safe)
        for packages in ((), (PackageSource(TEXT_UTILS, 'other-copy', self.package.source+b'\n'),)):
            with self.subTest(packages=len(packages)):
                result = self.check(source, packages=packages)
                self.assertIsNone(self.last(result).binding)
                self.assertFalse(self.last(result, 'Text', 'therefore this follows.').prose_safe)
                self.assertTrue(result[1].skips)

    def test_aliases_module_fields_and_parentheses(self):
        sources = [
            '#import "@local/text-utils:0.1.2": paragraph-tab as paragraph_tab\n#paragraph_tab',
            '#import "@local/text-utils:0.1.2" as u\n#let paragraph_tab = u.paragraph_tab\n#paragraph_tab',
            '#import "@local/text-utils:0.1.2"\n#let u = text-utils\n#let paragraph_tab = (u.paragraph-tab)\n#paragraph_tab',
            '#import "@local/text-utils:0.1.2": *\n#paragraph_tab',
            IMPORT + '#let p = paragraph_tab\n#let paragraph_tab = p\n#paragraph_tab',
        ]
        for source in sources:
            with self.subTest(source=source):
                binding = self.last(self.check(source)).binding
                self.assertIsNotNone(binding)
                self.assertEqual(binding.name, 'paragraph_tab')

    def test_unknown_imports_versions_and_facades_are_not_contracts(self):
        for path in ('../Styles/styles.typ', '@local/text-utils:0.1.3', '@preview/text-utils:0.1.2'):
            source = f'#import "{path}": paragraph_tab\n#paragraph_tab'
            with self.subTest(path=path):
                self.assertIsNone(self.last(self.check(source)).binding)
        for source in ('#let paragraph_tab = []\n#paragraph_tab',
                       '#import "Styles/styles.typ" as u\n#u.paragraph_tab'):
            result = self.check(source)
            self.assertIsNone(self.last(result, 'FieldAccess' if '#u.' in source else 'Ident',
                                        'u.paragraph_tab' if '#u.' in source else 'paragraph_tab').binding)

    def test_simple_shadowing_before_and_after_import(self):
        result = self.check(IMPORT + '#paragraph_tab\n#let paragraph_tab = []\n#paragraph_tab')
        uses = [f for f in self.facts(result, 'Ident', 'paragraph_tab') if f.binding]
        self.assertEqual(len(uses), 1)
        self.assertIsNone(self.last(result).binding)
        result = self.check('#let paragraph_tab = []\n' + IMPORT + '#paragraph_tab')
        self.assertEqual(self.last(result).binding.name, 'paragraph_tab')
        result = self.check(IMPORT + '#let paragraph_tab = paragraph_tab\n#paragraph_tab')
        self.assertEqual(self.last(result).binding.name, 'paragraph_tab')

    def test_scope_shadowing_does_not_leak_but_assignments_do(self):
        result = self.check(IMPORT + '#{ let paragraph_tab = []; paragraph_tab }\n#paragraph_tab')
        self.assertEqual(self.last(result).binding.name, 'paragraph_tab')
        self.assertIsNone(self.facts(result, 'Ident', 'paragraph_tab')[-2].binding)
        for assignment in ('paragraph_tab = []', 'paragraph_tab += []', '(paragraph_tab, x) = ([], 1)'):
            with self.subTest(assignment=assignment):
                result = self.check(IMPORT + '#let x = 0\n#{ ' + assignment + ' }\n#paragraph_tab')
                self.assertIsNone(self.last(result).binding)
        result = self.check(IMPORT + '#let saved = paragraph_tab\n#{ paragraph_tab = [] }\n#saved')
        self.assertEqual(self.last(result, 'Ident', 'saved').binding.name, 'paragraph_tab')

    def test_destructuring_and_named_patterns_shadow(self):
        for declaration in ('let (paragraph_tab, x) = ([], 1)',
                            'let (value: paragraph_tab, ..rest) = (value: [],)',
                            'let (..paragraph_tab) = ([],)'):
            result = self.check(IMPORT + '#' + declaration + '\n#paragraph_tab')
            self.assertIsNone(self.last(result).binding)
        # Dictionary keys are not declarations.
        result = self.check(IMPORT + '#let (paragraph_tab: x) = (paragraph_tab: [],)\n#paragraph_tab')
        self.assertEqual(self.last(result).binding.name, 'paragraph_tab')

    def test_unknown_wildcard_invalidates_existing_and_builtin_proofs(self):
        source = IMPORT + '#import "unknown.typ": *\n#set text(lang: "en")\n#paragraph_tab\ntherefore result.'
        result = self.check(source)
        self.assertIsNone(self.last(result).binding)
        self.assertIsNone(self.last(result, 'SetRule', 'set text(lang: "en")').binding)
        self.assertIsNone(self.last(result, 'Text', 'therefore result.').effective_language)
        # Later verified import repairs only its explicit names, never builtin text.
        result = self.check('#import "unknown.typ": *\n' + IMPORT + '#paragraph_tab')
        self.assertEqual(self.last(result).binding.name, 'paragraph_tab')
        # An unknown selective import only shadows the declared item.
        result = self.check(IMPORT + '#import "unknown.typ": other\n#paragraph_tab')
        self.assertEqual(self.last(result).binding.name, 'paragraph_tab')

    def test_wildcard_unknown_in_inner_scope_does_not_leak(self):
        result = self.check(IMPORT + '#{ import "unknown.typ": *; paragraph_tab }\n#paragraph_tab')
        self.assertIsNone(self.facts(result, 'Ident', 'paragraph_tab')[-2].binding)
        self.assertEqual(self.last(result).binding.name, 'paragraph_tab')

    def test_conditional_mutation_invalidates_provenance(self):
        for fragment in ('#if maybe { paragraph_tab = [] }',
                         '#for x in (1, 2) { paragraph_tab = [] }',
                         '#while maybe { paragraph_tab = [] }'):
            with self.subTest(fragment=fragment):
                result = self.check(IMPORT + fragment + '\n#paragraph_tab')
                self.assertIsNone(self.last(result).binding)
        result = self.check(IMPORT + '#for paragraph_tab in (1, 2) [#paragraph_tab]\n#paragraph_tab')
        self.assertIsNone(self.facts(result, 'Ident', 'paragraph_tab')[-2].binding)
        self.assertEqual(self.last(result).binding.name, 'paragraph_tab')

    def test_closures_not_executed_or_inferred_and_call_result_not_aliased(self):
        result = self.check(IMPORT + '#let f() = { paragraph_tab = []; paragraph_tab }\n#paragraph_tab')
        self.assertEqual(self.last(result).binding.name, 'paragraph_tab')
        nested = self.facts(result, 'Ident', 'paragraph_tab')[-2]
        self.assertTrue(nested.deferred)
        self.assertIsNone(nested.binding)
        result = self.check(IMPORT + '#let paragraph_tab = highlighted[hello]\n#paragraph_tab')
        self.assertIsNone(self.last(result).binding)
        self.assertEqual(self.last(result, 'FuncCall', 'highlighted[hello]').binding.name, 'highlighted')

    def test_source_language_overrides_fallback_in_order(self):
        result = self.check(IMPORT + '#paragraph_tab\ntherefore first.\n#set text(lang: "fr")\n#paragraph_tab\ntherefore second.\n#set text(lang: "en-GB")\n#paragraph_tab\ntherefore third.')
        self.assertEqual([self.last(result, 'Text', f'therefore {word}.').effective_language
                          for word in ('first', 'second', 'third')], ['en', 'fr', 'en-GB'])
        result = self.check(IMPORT + '#paragraph_tab\ntherefore x.', fallback=None)
        self.assertIsNone(self.last(result, 'Text', 'therefore x.').effective_language)
        result = self.check(IMPORT + '#set text(lang: "en")\n#paragraph_tab\ntherefore x.', fallback=None)
        self.assertEqual(self.last(result, 'Text', 'therefore x.').effective_language, 'en')

    def test_dynamic_conditional_spread_and_escaped_languages_remain_unknown(self):
        for setting in ('text(lang: language)', 'text(lang: "en") if maybe',
                        'text(..options)', 'text(lang: "en", ..options)',
                        'text(lang: "e\\u{6e}")'):
            with self.subTest(setting=setting):
                result = self.check(IMPORT + '#set ' + setting + '\n#paragraph_tab\ntherefore x.')
                self.assertIsNone(self.last(result, 'Text', 'therefore x.').effective_language)
        # A later unconditional set overrides an earlier dynamic set, not vice versa.
        result = self.check(IMPORT + '#set text(lang: language)\n#set text(lang: "en")\n#paragraph_tab\ntherefore x.')
        self.assertEqual(self.last(result, 'Text', 'therefore x.').effective_language, 'en')

    def test_builtin_text_alias_and_shadowing(self):
        result = self.check(IMPORT + '#let t = text\n#set t(lang: "de")\n#paragraph_tab\ntherefore x.')
        self.assertEqual(self.last(result, 'Text', 'therefore x.').effective_language, 'de')
        for declaration in ('#let text = value', '#import "unknown.typ": text'):
            result = self.check(IMPORT + declaration + '\n#set text(lang: "en")\n#paragraph_tab\ntherefore x.')
            self.assertIsNone(self.last(result, 'Text', 'therefore x.').effective_language)
        # Shadowing the name does not retroactively change an already-set language.
        result = self.check(IMPORT + '#set text(lang: "de")\n#let text = value\n#paragraph_tab\ntherefore x.')
        self.assertEqual(self.last(result, 'Text', 'therefore x.').effective_language, 'de')

    def test_unknown_show_cannot_be_rescued_by_config_or_later_set(self):
        for rule in ('#show: unknown', '#show text: it => text(lang: "fr", it)',
                     '#show: apply-paragraph-tabs.with()',
                     '#let apply-paragraph-tabs = x\n#show: apply-paragraph-tabs'):
            with self.subTest(rule=rule):
                result = self.check(IMPORT + rule + '\n#set text(lang: "en")\n#paragraph_tab\ntherefore x.')
                fact = self.last(result, 'Text', 'therefore x.')
                self.assertIsNone(fact.effective_language)
                self.assertFalse(fact.prose_safe)

    def test_verified_marker_show_preserves_language_and_aliases(self):
        for rule in ('#show: apply-paragraph-tabs', '#show /* comment */ : apply-paragraph-tabs',
                     '#let tabs = apply-paragraph-tabs\n#show: tabs'):
            result = self.check(IMPORT + rule + '\n#set text(lang: "en")\n#paragraph_tab\ntherefore x.')
            fact = self.last(result, 'Text', 'therefore x.')
            self.assertEqual(fact.effective_language, 'en')
            self.assertTrue(fact.prose_safe)
        result = self.check('#import "@local/text-utils:0.1.2" as u\n#show: u.apply-paragraph-tabs\n#let paragraph_tab = u.paragraph_tab\n#paragraph_tab\ntherefore x.')
        self.assertTrue(self.last(result, 'Text', 'therefore x.').prose_safe)

    def test_prose_only_root_not_heading_list_caption_content_or_context(self):
        for fragment in ('= #paragraph_tab therefore heading', '- #paragraph_tab therefore list',
                         '#figure([], caption: [#paragraph_tab\ntherefore caption])',
                         '#block[#paragraph_tab\ntherefore nested]',
                         '#let body = [#paragraph_tab\ntherefore deferred]',
                         '#context [#paragraph_tab\ntherefore contextual]'):
            result = self.check(IMPORT + fragment)
            text_facts = [result[1].facts[i] for i, n in enumerate(result[0].nodes)
                          if n.kind == 'Text' and b'therefore' in result[0].source[n.start:n.end]]
            self.assertTrue(text_facts)
            self.assertTrue(all(not fact.prose_safe for fact in text_facts))
        result = self.check(IMPORT + '$ x^i $\n\n#paragraph_tab\ntherefore result.')
        self.assertTrue(self.last(result, 'Text', 'therefore result.').prose_safe)

    def test_context_flag_is_lexical_and_does_not_leak(self):
        result = self.check(IMPORT + '#context { highlighted[inside] }\n#highlighted[outside]')
        self.assertTrue(self.last(result, 'FuncCall', 'highlighted[inside]').in_context)
        self.assertFalse(self.last(result, 'FuncCall', 'highlighted[outside]').in_context)

    def test_include_and_unknown_output_effects_are_not_language_proof(self):
        for fragment in ('#include "missing.typ"', '#unknown[]', '#value', '#{ [body] }'):
            with self.subTest(fragment=fragment):
                result = self.check(IMPORT + fragment + '\n#set text(lang: "en")\n#paragraph_tab\ntherefore result.')
                fact = self.last(result, 'Text', 'therefore result.')
                self.assertFalse(fact.prose_safe)
                self.assertIsNone(fact.effective_language)

    def test_plain_builtin_footnote_preserves_only_bounded_literal_case(self):
        suffix = '\n#paragraph_tab\ntherefore result.'
        for call in ('#footnote[Reason.]', '#let fn = footnote\n#fn[Reason. More text.]'):
            result = self.check(IMPORT + call + suffix)
            fact = self.last(result, 'Text', 'therefore result.')
            self.assertTrue(fact.prose_safe)
            self.assertEqual(fact.effective_language, 'en')
        for call in ('#let footnote = unknown\n#footnote[Reason.]',
                     '#footnote[#unknown[]]', '#footnote[$x$]',
                     '#footnote[#set text(lang:"en")\nReason.]',
                     '#footnote(numbering: "1")[Reason.]', '#footnote[@label]'):
            with self.subTest(call=call):
                result = self.check(IMPORT + call + suffix)
                fact = self.last(result, 'Text', 'therefore result.')
                self.assertFalse(fact.prose_safe)
                self.assertIsNone(fact.effective_language)

    def test_dynamic_import_and_set_condition_cannot_hide_mutation(self):
        for code in ('#set text(lang: "en") if { paragraph_tab = []; true }',
                     '#import { paragraph_tab = []; "unknown.typ" }: other'):
            with self.subTest(code=code):
                result = self.check(IMPORT + code + '\n#paragraph_tab')
                self.assertIsNone(self.last(result).binding)
        result = self.check('#import "@local/text-utils:0.1.2" as u\n#{ (u.paragraph_tab, x) = ([], 1) }\n#u.paragraph_tab')
        self.assertIsNone(self.last(result, 'FieldAccess', 'u.paragraph_tab').binding)

    def test_nested_and_deferred_language_is_not_effective_root_language(self):
        for fragment in ('#unknown[#set text(lang:"en")\ntherefore nested.]',
                         '#let content = [#set text(lang:"en")\ntherefore nested.]',
                         '#context [#set text(lang:"en")\ntherefore nested.]'):
            result = self.check(IMPORT + fragment)
            self.assertIsNone(self.last(result, 'Text', 'therefore nested.').effective_language)
            self.assertFalse(self.last(result, 'Text', 'therefore nested.').prose_safe)

    def test_strings_comments_math_and_raw_are_not_import_statements(self):
        source = '#let x = "#import fake"\n// #import "other.typ": *\n`#import "raw.typ": *`\n' + IMPORT + '$ paragraph_tab $\n#paragraph_tab'
        result = self.check(source)
        self.assertEqual(imported_paths(result[0]), (TEXT_UTILS,))
        self.assertEqual(self.last(result).binding.name, 'paragraph_tab')
        self.assertTrue(all(f.binding is None for f in self.facts(result, 'MathIdent', 'paragraph_tab')))

    def test_dynamic_paths_nested_item_paths_and_escaped_strings_unsupported(self):
        for statement in ('#import path: paragraph_tab',
                          '#import "@local/text-utils:0.1.2": nested.paragraph_tab',
                          '#import "@local/text-utils:0.1.\\u{32}": paragraph_tab'):
            result = self.check(statement + '\n#paragraph_tab')
            self.assertIsNone(self.last(result).binding)
        tree = parse(b'#import name: *\n#import "a.typ": x\n#import "a.typ": y')
        self.assertEqual(imported_paths(tree), ('a.typ',))

    def test_nonmarkup_mode_never_has_effective_prose_language(self):
        for mode, source in (('code', b'set text(lang: "en"); text'),
                             ('math', b'x + y')):
            tree = parse(source, mode)
            result = resolve(tree, fallback_language='en', packages=(self.package,))
            self.assertTrue(all(not fact.prose_safe and fact.effective_language is None
                                for fact in result.facts))

    def test_no_filesystem_or_evaluation_and_immutable_results(self):
        tree = parse((IMPORT + '#paragraph_tab').encode())
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('no filesystem')):
            result = resolve(tree, packages=(self.package,), fallback_language='en')
        with self.assertRaises(FrozenInstanceError):
            result.facts = ()
        self.assertIsInstance(result.facts, tuple)
        self.assertFalse(result.complete)
        for packages in ([self.package], (self.package, self.package), (PackageSource(TEXT_UTILS, '', b'x'),)):
            with self.assertRaises(InputError):
                resolve(tree, packages=packages)
        with self.assertRaises(InputError):
            resolve(tree, fallback_language='')
        broken = parse(b'#let =')
        result = resolve(broken, packages=(self.package,))
        self.assertTrue(result.skips)
        self.assertTrue(all(f.binding is None and not f.prose_safe for f in result.facts))


if __name__ == '__main__':
    unittest.main()
