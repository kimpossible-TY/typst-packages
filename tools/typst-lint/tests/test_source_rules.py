"""Real .typ input through AST, verified package resolution, report and CLI."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
from capitalization import WORDS
from edit_plan import Edit, plan
from package_loader import load_packages
from paragraph_rules import CAPITALIZATION as CAP, MARKER_GAP as GAP, analyze as paragraphs
from script_rule import RULE_ID as SCRIPT
from source_analysis import analyze
from static_resolver import imported_paths, resolve
from syntax_client import EXECUTABLE, parse
from typst_lint import digest, report

IMPORT = b'#import "@local/text-utils:0.1.2": paragraph_tab\n'
LANG = b'#set text(lang: "en")\n'
MARKER = b'#paragraph_tab\n'


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter explicitly first')
class SourceRuleTests(unittest.TestCase):
    def run_source(self, source, *, fallback=None, packages=True, ignored=frozenset()):
        tree = parse(source)
        sources = load_packages((TOOL / 'tests/fixtures/reviewed-packages'), imported_paths(tree)).sources if packages else ()
        return analyze(tree, 'main.typ', pack='pde', packages=sources,
                       fallback_language=fallback, ignored=ignored)

    def candidate(self, source, **kwargs):
        result = self.run_source(source, **kwargs)
        edits = [edit for value in result.values() for edit in value.edits]
        return result, plan(source, edits).candidate()

    def test_fixture_composes_all_three_rules_and_is_idempotent(self):
        directory = TOOL / 'tests/fixtures/paragraph-source'
        source = (directory / 'combined.typ').read_bytes()
        expected = (directory / 'combined.expected.typ').read_bytes()
        results, candidate = self.candidate(source)
        self.assertEqual(candidate, expected)
        self.assertEqual({rid for rid, r in results.items() if r.edits}, {CAP, GAP, SCRIPT})
        final = self.run_source(candidate)
        self.assertFalse(any(r.edits or r.diagnostics for r in final.values()))
        self.assertTrue(any('allowlist' in reason for reason in final[CAP].skips))
        self.assertFalse(parse(candidate).nodes[0].errors)

    def test_all_26_starters_have_one_byte_source_edit(self):
        for word in WORDS:
            with self.subTest(word=word):
                source = IMPORT + LANG + b'\n' + MARKER + word + b' holds.\n'
                result, candidate = self.candidate(source)
                self.assertEqual(len(result[CAP].edits), 1)
                self.assertEqual(result[CAP].edits[0].end - result[CAP].edits[0].start, 1)
                self.assertEqual(candidate, source[:-len(word + b' holds.\n')] + word.capitalize() + b' holds.\n')
                self.assertFalse(self.run_source(candidate)[CAP].edits)

    def test_never_searches_past_nonliteral_initial_or_changes_protected_word(self):
        for first in (b'$x$ therefore holds.', b'#text[therefore holds.]', b'@therefore then holds.',
                      b'"therefore holds."', b'*therefore holds.*', b'_therefore holds._',
                      b'`therefore holds.`', b'\\#therefore holds.', b'// comment\ntherefore holds.',
                      b'therefore, holds.', b'therefore-like holds.', b'therefore\xc2\xa0holds.',
                      b'iPhone holds.', b'eBay holds.', b'tHis holds.', b'x holds.',
                      b'theorem holds.', 'élan holds.'.encode(), 'α holds.'.encode(),
                      b'Therefore holds.', b'THEREFORE holds.'):
            with self.subTest(first=first):
                source = IMPORT + LANG + b'\n' + MARKER + first + b'\n'
                result, candidate = self.candidate(source)
                self.assertFalse(result[CAP].edits)
                self.assertFalse(result[CAP].diagnostics)
                self.assertEqual(candidate, source)

    def test_exact_adjacent_line_gap(self):
        for gap, allowed in ((b'\n', True), (b'\r\n', True), (b' \t\n\t ', True),
                             (b'\n\n', False), (b'\r\n\r\n', False), (b'\r', False),
                             (b' ', False), (b'\n// comment\n', False)):
            with self.subTest(gap=gap):
                source = IMPORT + LANG + b'\n#paragraph_tab' + gap + b'therefore holds.\n'
                result = self.run_source(source)
                self.assertEqual(bool(result[CAP].edits), allowed)

    def test_language_source_overrides_and_dynamic_cannot_use_fallback(self):
        for setting, fallback, allowed in ((b'', None, False), (b'', 'en', True),
                (b'#set text(lang: "en-US")\n', None, True),
                (b'#set text(lang: "ko")\n', 'en', False),
                (b'#set text(lang: language)\n', 'en', False),
                (b'#set text(lang: if true { "en" } else { "ko" })\n', 'en', False),
                (b'#set text(lang: "en") if condition\n', 'en', False),
                (b'#set text(lang: "en")\n#set text(lang: "fr")\n', 'en', False)):
            with self.subTest(setting=setting, fallback=fallback):
                source = IMPORT + setting + b'\n' + MARKER + b'therefore holds.\n'
                self.assertEqual(bool(self.run_source(source, fallback=fallback)[CAP].edits), allowed)

    def test_imports_alias_shadowing_and_explicit_evidence(self):
        source = IMPORT + LANG + b'\n' + MARKER + b'therefore holds.\n'
        result = self.run_source(source, packages=False)
        self.assertFalse(result[CAP].edits)
        self.assertTrue(result[CAP].skips)
        for prefix in (b'#import "unreviewed.typ": paragraph_tab\n',
                       b'#import "@local/text-utils:9.0.0": paragraph_tab\n',
                       IMPORT + b'#let paragraph_tab = "unrelated"\n',
                       IMPORT + b'#import "unreviewed.typ": *\n',
                       IMPORT + b'#let paragraph_tab(body) = body\n',
                       IMPORT + b'#let alias = paragraph_tab\n#let paragraph_tab = "shadow"\n'):
            with self.subTest(prefix=prefix):
                result = self.run_source(prefix + LANG + b'\n' + MARKER + b'therefore holds.\n')
                self.assertFalse(result[CAP].diagnostics or result[CAP].edits)
                self.assertFalse(result[GAP].diagnostics or result[GAP].edits)
        for source in (b'#import "@local/text-utils:0.1.2": paragraph_tab as pt\n' + LANG + b'\n#pt\ntherefore holds.\n',
                       b'#import "@local/text-utils:0.1.2" as utils\n' + LANG + b'\n#utils.paragraph_tab\ntherefore holds.\n'):
            result = self.run_source(source)
            self.assertFalse(result[CAP].edits)
            self.assertTrue(any('alias spelling' in reason for reason in result[CAP].skips))
        # Import renaming into the canonical local spelling has real provenance.
        source = b'#import "@local/text-utils:0.1.2": paragraph-tab as paragraph_tab\n' + LANG + b'\n' + MARKER + b'therefore holds.\n'
        self.assertTrue(self.run_source(source)[CAP].edits)

    def test_nested_and_unknown_output_contexts_are_explicitly_unsupported(self):
        for body in (b'#block[\n' + MARKER + b'therefore holds.\n]',
                     b'#let body = [\n' + MARKER + b'therefore holds.\n]',
                     b'#let f() = [\n' + MARKER + b'therefore holds.\n]',
                     b'#box[\n' + MARKER + b'therefore holds.\n]',
                     b'#unknown()\n' + MARKER + b'therefore holds.\n',
                     b'#show: unknown\n' + MARKER + b'therefore holds.\n',
                     b'#include "other.typ"\n' + MARKER + b'therefore holds.\n'):
            with self.subTest(body=body):
                result = self.run_source(IMPORT + LANG + b'\n' + body)
                self.assertFalse(result[CAP].edits)
                self.assertTrue(result[CAP].skips)

    def test_marker_gap_immediate_previous_line_and_no_footnote_guess(self):
        for previous, changes in ((b'First.\n', True), (b'First.\n\n', False),
                                  (b'First.\n \t\n', False), (b'First.\n// comment\n', False),
                                  (b'This holds.#footnote[Reason.]\n', False)):
            with self.subTest(previous=previous):
                source = IMPORT + LANG + previous + MARKER + b'Therefore holds.\n'
                result, candidate = self.candidate(source)
                self.assertEqual(bool(result[GAP].edits), changes)
                if changes:
                    self.assertEqual(candidate, source.replace(MARKER, b'\n' + MARKER))
                elif previous.endswith((b'comment\n', b'Reason.]\n')):
                    self.assertTrue(result[GAP].diagnostics)
                    self.assertTrue(any('fix_unavailable' in reason for reason in result[GAP].skips))
                else:
                    self.assertFalse(result[GAP].diagnostics)

    def test_bom_crlf_unicode_columns_and_horizontal_indentation(self):
        source = b'\xef\xbb\xbf' + IMPORT.replace(b'\n', b'\r\n') + LANG.replace(b'\n', b'\r\n')
        source += '기존 문장.\r\n'.encode() + b'  #paragraph_tab\r\n\t therefore $x^i$ holds.\r\n'
        result, candidate = self.candidate(source)
        self.assertEqual(candidate, source.replace(b'  #paragraph_tab', b'\r\n  #paragraph_tab').replace(b'therefore', b'Therefore').replace(b'x^i', b'x^(i)'))
        self.assertEqual((result[CAP].diagnostics[0].line, result[CAP].diagnostics[0].column), (5, 3))
        self.assertTrue(candidate.startswith(b'\xef\xbb\xbf'))
        self.assertNotIn(b'\n', candidate.replace(b'\r\n', b''))

    def test_marker_anchored_suppression_expires_and_removes_edit(self):
        directive = b'// typst-lint: ignore-next pde.paragraph-capitalization, pde.paragraph-marker-gap -- intentional\n'
        source = IMPORT + LANG + directive + MARKER + b'therefore holds.\n\n' + MARKER + b'therefore holds.\n'
        result, candidate = self.candidate(source)
        self.assertEqual(len(result[CAP].suppressed), 1)
        self.assertEqual(len(result[GAP].suppressed), 1)
        self.assertEqual(len(result[CAP].diagnostics), 1)
        self.assertIn(directive + MARKER + b'therefore', candidate)
        self.assertTrue(candidate.endswith(MARKER + b'Therefore holds.\n'))
        source = IMPORT + LANG + directive + b'Ordinary word.\n\n' + MARKER + b'therefore holds.\n'
        result = self.run_source(source)
        self.assertFalse(result[CAP].suppressed)
        self.assertTrue(result[CAP].edits)

    def test_stale_resolution_fails_closed(self):
        source = IMPORT + LANG + b'\n' + MARKER + b'therefore holds.\n'
        tree = parse(source)
        resolution = resolve(tree, packages=load_packages((TOOL / 'tests/fixtures/reviewed-packages'), imported_paths(tree)).sources)
        for altered in (replace(resolution, source_sha256='stale'), replace(resolution, facts=())):
            result = paragraphs(tree, 'main.typ', altered)
            self.assertFalse(any(r.edits or r.diagnostics for r in result.values()))
            self.assertTrue(all(r.skips for r in result.values()))

    def test_unresolved_diagnostic_survives_shifted_candidate_positions(self):
        source = IMPORT + LANG + b'$x^i$\n' + MARKER + b'therefore holds.\n'
        result, candidate = self.candidate(source)
        self.assertTrue(result[GAP].diagnostics)
        self.assertFalse(result[GAP].edits)
        self.assertTrue(result[CAP].edits and result[SCRIPT].edits)
        self.assertEqual(candidate, source.replace(b'x^i', b'x^(i)').replace(b'therefore', b'Therefore'))
        final = self.run_source(candidate)
        self.assertEqual(len(final[GAP].diagnostics), 1)
        self.assertFalse(any(r.edits for r in final.values()))

    def test_suppression_does_not_hide_unrelated_script_or_cross_bad_marker(self):
        directive = b'// typst-lint: ignore-next pde.paragraph-capitalization -- intentional\n'
        source = IMPORT + LANG + directive + MARKER + b'therefore $x^i$ holds.\n\n' + MARKER + b'therefore holds.\n'
        result = self.run_source(source)
        self.assertEqual(len(result[CAP].suppressed), 1)
        self.assertEqual(len(result[CAP].diagnostics), 1)
        self.assertEqual(len(result[SCRIPT].diagnostics), 1)
        self.assertTrue(result[SCRIPT].edits)
        for bad_marker in (b'#paragraph_tab inline prose.\n', b'#paragraph_tab\n\n'):
            source = IMPORT + LANG + directive + bad_marker + b'therefore holds.\n\n' + MARKER + b'therefore holds.\n'
            result = self.run_source(source)
            self.assertFalse(result[CAP].suppressed)
            self.assertEqual(len(result[CAP].diagnostics), 1)

    def test_malformed_source_and_unreviewed_package_have_no_paragraph_proposals(self):
        for source in (IMPORT + LANG + b'\n' + MARKER + b'therefore $x^$ holds.',
                       IMPORT + LANG + b'\n' + MARKER + b'therefore holds.\n#let ='):
            result = self.run_source(source)
            self.assertTrue(result[CAP].parse_errors)
            self.assertFalse(any(r.edits for r in result.values()))
        source = IMPORT + LANG + b'\n' + MARKER + b'therefore holds.'
        tree = parse(source)
        packages = load_packages((TOOL / 'tests/fixtures/reviewed-packages'), imported_paths(tree)).sources
        altered = tuple(replace(package, source=package.source + b'\n// changed') for package in packages)
        result = analyze(tree, 'main.typ', pack='pde', packages=altered)
        self.assertFalse(result[CAP].edits or result[GAP].edits)
        self.assertTrue(result[CAP].skips)

    def test_candidate_introducing_new_finding_refuses_every_edit(self):
        import source_analysis
        source = IMPORT + LANG + b'First.\n' + MARKER + b'therefore $x^i$ holds.\n'
        original = source_analysis._analyze
        count = 0
        # Do not recursively call the patched analyzer from the injected case.
        finding = replace(self.run_source(IMPORT + LANG + b'\n' + MARKER + b'therefore holds.\n')[CAP].diagnostics[0], line=1, column=1, explanation='Unexpected new finding')
        def changed(*args):
            nonlocal count
            count += 1
            result = original(*args)
            if count > 1:
                result[CAP] = replace(result[CAP], diagnostics=(finding,))
            return result
        with patch.object(source_analysis, '_analyze', side_effect=changed):
            result = self.run_source(source)
        self.assertFalse(any(r.edits for r in result.values()))
        self.assertTrue(any('introduces a new finding' in reason for reason in result[CAP].skips))

    def test_report_cli_explicit_package_evidence_and_no_writes_all_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = (TOOL / 'tests/fixtures/paragraph-source/combined.typ').read_bytes()
            target = root / 'main.typ'
            target.write_bytes(source)
            (root / '.typst-lint.toml').write_text('rule-set="pde"\n')
            missing = report(root, 'check')
            self.assertFalse(any(e['rule_id'] == CAP for e in missing['proposed_edits']))
            for command, extra in (('check', []), ('fix', ['--dry-run']), ('fix', []), ('compile', [])):
                launcher = 'typst-style' if command == 'compile' else 'typst-lint'
                process = subprocess.run([sys.executable, str(TOOL / launcher), command, *extra,
                    '--root', str(root), '--package-path', str((TOOL / 'tests/fixtures/reviewed-packages')), '--format', 'json'], capture_output=True, text=True)
                self.assertEqual(process.returncode, 2, process.stderr)
                value = json.loads(process.stdout)
                self.assertEqual(value['package_evidence'][0]['source_sha256'], digest(((TOOL / 'tests/fixtures/reviewed-packages') / 'local/text-utils/0.1.2/lib.typ').read_bytes()))
                self.assertEqual({e['rule_id'] for e in value['proposed_edits']}, {CAP, GAP, SCRIPT})
                self.assertFalse(value['compiler_invoked'] or value['applied_fixes'] or value['coverage']['complete'])
                self.assertEqual(target.read_bytes(), source)
            (root / '.typst-lint.toml').write_text('rule-set="pde"\n[[ignore]]\npaths=["main.typ"]\nrules=["pde.paragraph-capitalization","pde.paragraph-marker-gap"]\nreason="intentional"\n')
            value = report(root, 'dry-run', package_path=(TOOL / 'tests/fixtures/reviewed-packages'))
            self.assertEqual({e['rule_id'] for e in value['proposed_edits']}, {SCRIPT})
            self.assertEqual(value['suppressed_by_rule'], {CAP: 1, GAP: 1})
            target.write_bytes(b'// typst-lint: ignore-next invalid -- reason\n' + source)
            value = report(root, 'dry-run', package_path=(TOOL / 'tests/fixtures/reviewed-packages'))
            self.assertTrue(value['configuration_failures'])
            self.assertFalse(value['proposed_edits'])


if __name__ == '__main__':
    unittest.main()
