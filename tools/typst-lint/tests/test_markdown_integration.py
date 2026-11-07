"""Read-only Markdown dispatch and independently authenticated repair regressions.

Every source, output sentinel and compiler tripwire is owned by a temporary
fixture. These checks establish bounded math-script support, never a writer,
compiler gate, package execution or complete Markdown/rule coverage.
"""
import copy
from dataclasses import FrozenInstanceError, replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from audit_rules import candidate_verdict
from convergence_evidence import authenticate_candidate
from edit_plan import Edit, plan
import source_analysis
from syntax_client import AdapterError, EXECUTABLE
import typst_lint as lint

SCRIPT = 'core.parenthesized-scripts'
DIRECTIVE = b'// typst-lint: ignore-next core.parenthesized-scripts -- intentional example'
TEACHING = b'Do not write bare scripts such as `$X^i$` or `$X_j$`.'


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class MarkdownIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='typst-markdown-integration-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def put(self, name, source):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source if type(source) is bytes else source.encode('utf-8'))
        return path

    def select_markdown(self, *, mixed=False, pack='core', extra=''):
        include = '["**/*.typ", "**/*.md"]' if mixed else '["**/*.md"]'
        return self.put('.typst-lint.toml',
                        f'rule-set="{pack}"\ninclude={include}\n' + extra)

    def report(self, *, contexts=None, **kwargs):
        return lint.report(self.root, 'check',
                           _analysis_observer=contexts.__setitem__ if contexts is not None else None,
                           **kwargs)

    def rows(self, value, name='notes.md'):
        return {row['rule_id']: row for row in value['coverage']['files'] if row['path'] == name}

    def candidate(self, source, value, name='notes.md'):
        edits = [Edit(item['start'], item['end'], item['replacement'].encode('utf-8'))
                 for item in value['proposed_edits'] if item['path'] == name]
        planned = plan(source, edits)
        self.assertFalse(planned.deferred)
        return planned.candidate()

    def assert_read_only(self, value):
        self.assertEqual(value['status'], 2)
        self.assertEqual(value['phase'], 'analysis')
        self.assertFalse(value['compiler_invoked'] or value['applied_fixes'])
        self.assertFalse(value['coverage']['complete'])
        self.assertEqual(value['proposal_status'], 'partial-not-applicable-transaction')

    def cli(self, mode, *, compiler=None):
        launcher = 'typst-style' if mode == 'compile' else 'typst-lint'
        command = [sys.executable, str(TOOL / launcher),
                   'fix' if mode == 'dry-run' else mode,
                   '--root', str(self.root), '--format', 'json']
        if mode == 'dry-run':
            command.append('--dry-run')
        if mode == 'compile':
            command += ['--', str(compiler), 'notes.md', 'existing.pdf']
        process = subprocess.run(command, capture_output=True, text=True, timeout=30,
                                 env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertEqual(process.stderr, '')
        value = json.loads(process.stdout)
        self.assert_read_only(value)
        return value

    def test_default_typst_selection_excludes_markdown(self):
        typst = b'$T^i$\n'
        self.put('main.typ', typst)
        markdown = self.put('notes.md', b'```typst\n#let =\n```\n$M_j$\n')
        value = self.report()
        self.assertEqual([item['path'] for item in value['inputs']], ['main.typ'])
        self.assertEqual({item['path'] for item in value['diagnostics']}, {'main.typ'})
        self.assertFalse(any(row['path'] == 'notes.md' for row in value['coverage']['files']))
        self.assertFalse(value['parse_failures'] or value['configuration_failures'])
        self.assertEqual(self.candidate(typst, value, 'main.typ'), b'$T^(i)$\n')
        self.assertEqual(markdown.read_bytes(), b'```typst\n#let =\n```\n$M_j$\n')
        self.assert_read_only(value)

    def test_explicit_markdown_selection_has_only_script_rule_coverage(self):
        self.select_markdown(pack='pde')
        source = b'# Notes\nUse $X^i$ and `$Y_j$`.\n'
        self.put('notes.md', source)
        value = self.report()
        self.assertEqual(value['inputs'], [dict(path='notes.md', sha256=lint.digest(source), bytes=len(source))])
        self.assertEqual(len(value['diagnostics']), 2)
        self.assertEqual({finding['rule_id'] for finding in value['diagnostics']}, {SCRIPT})
        self.assertEqual(self.candidate(source, value), b'# Notes\nUse $X^(i)$ and `$Y_(j)$`.\n')
        rows = self.rows(value)
        self.assertEqual(set(rows), set(value['coverage']['enabled']))
        self.assertIn(rows[SCRIPT]['status'], ('evaluated', 'partial'))
        for rid, row in rows.items():
            if rid != SCRIPT:
                self.assertEqual(row['status'], 'not-applicable')
                self.assertEqual(row['reasons'], ['Only math-script checking applies to Markdown examples'])
        self.assertTrue(all(row['rule_id'] == SCRIPT for row in value['coverage']['evaluated']))
        self.assertEqual(value['coverage']['implemented'],
                         [SCRIPT] if rows[SCRIPT]['status'] == 'evaluated' else [])
        self.assert_read_only(value)

    def test_mixed_inputs_never_run_typst_rule_composition_on_markdown(self):
        self.select_markdown(mixed=True, pack='pde')
        self.put('main.typ', b'$T^i$\n')
        self.put('notes.md', b'```typst\nText #highlight[$ M_j $] remains.\n```\n')
        with patch.object(source_analysis, 'analyze', wraps=source_analysis.analyze) as analyze:
            value = self.report()
        self.assertEqual(analyze.call_count, 1)
        self.assertEqual(analyze.call_args.args[1], 'main.typ')
        self.assertEqual({(finding['path'], finding['rule_id']) for finding in value['diagnostics']},
                         {('main.typ', SCRIPT), ('notes.md', SCRIPT)})
        self.assertEqual({(edit['path'], edit['rule_id']) for edit in value['proposed_edits']},
                         {('main.typ', SCRIPT), ('notes.md', SCRIPT)})
        typst_rows, markdown_rows = self.rows(value, 'main.typ'), self.rows(value)
        self.assertNotEqual(typst_rows['pde.text-only-highlight']['status'], 'not-applicable')
        self.assertEqual(markdown_rows['pde.text-only-highlight']['status'], 'not-applicable')
        self.assertFalse(any(row['path'] == 'notes.md' and row['rule_id'] != SCRIPT
                             for row in value['coverage']['evaluated']))

    def test_markdown_import_examples_do_not_resolve_packages_or_import_graphs(self):
        self.select_markdown(pack='pde')
        source = (b'```typst\n#import "@preview/not-installed:9.9.9": *\n'
                  b'#import "never-read.typ": *\n#let alias = highlighted\n'
                  b'#alias[$X^i$]\n#read("never-read.txt")\n```\n')
        self.put('notes.md', source)
        self.put('never-read.txt', b'not an analysis input')
        with patch('package_loader.load_packages', side_effect=AssertionError('No Markdown package loading')), \
                patch('import_graph.load_import_graph', side_effect=AssertionError('No Markdown import graph')), \
                patch.object(source_analysis, 'analyze', side_effect=AssertionError('No Markdown Typst backend')):
            value = self.report(package_path=self.root / 'nonexistent-package-directory')
        self.assertEqual(len(value['diagnostics']), 1)
        self.assertEqual(value['diagnostics'][0]['rule_id'], SCRIPT)
        self.assertEqual(self.candidate(source, value), source.replace(b'$X^i$', b'$X^(i)$'))
        self.assertIsNone(value['package_path'])
        self.assertEqual(value['package_evidence'], [])
        self.assertEqual(value['package_unavailable'], [])

    def test_utf8_positions_and_byte_edits_preserve_bom_crlf_and_all_prose(self):
        self.select_markdown()
        source = (b'\xef\xbb\xbf# Notes\r\n' + '한글 $X^i$와 `$Y_j$`.\r\n'.encode('utf-8')
                  + b'```typst\r\n$Z^k$\r\n```\r\n~~~typst\r\n$Q_l$\r\n~~~\r\n')
        path = self.put('notes.md', source)
        contexts = {}
        value = self.report(contexts=contexts)
        extraction = contexts['notes.md'].analyze().extraction
        self.assertEqual([example.kind for example in extraction.examples],
                         ['inline-math', 'inline-code-math', 'typst-fence', 'typst-fence'])
        self.assertEqual([source[example.start:example.end] for example in extraction.examples],
                         [b'$X^i$', b'$Y_j$', b'$Z^k$\r\n', b'$Q_l$\r\n'])
        self.assertEqual(extraction.exemptions, ())
        with self.assertRaises(FrozenInstanceError):
            extraction.examples[0].start = 0
        expected_positions = []
        for token in (b'X^i', b'Y_j', b'Z^k', b'Q_l'):
            offset = source.index(token) + 2
            line = source[:offset].count(b'\n') + 1
            left = source.rfind(b'\n', 0, offset) + 1
            column = len(source[left:offset].decode('utf-8')) + 1
            expected_positions.append((line, column))
        self.assertEqual([(item['line'], item['column']) for item in value['diagnostics']], expected_positions)
        expected = source
        for before, after in ((b'X^i', b'X^(i)'), (b'Y_j', b'Y_(j)'),
                              (b'Z^k', b'Z^(k)'), (b'Q_l', b'Q_(l)')):
            expected = expected.replace(before, after)
        candidate = self.candidate(source, value)
        self.assertEqual(candidate, expected)
        self.assertTrue(candidate.startswith(b'\xef\xbb\xbf'))
        self.assertNotIn(b'\n', candidate.replace(b'\r\n', b''))
        for edit in value['proposed_edits']:
            self.assertEqual(edit['baseline_sha256'], lint.digest(source))
            self.assertEqual(edit['start'], edit['end'])
            self.assertIn(edit['replacement'], ('(', ')'))
            source[:edit['start']].decode('utf-8')  # Must end on a byte boundary.
            self.assertTrue(any(example.start <= edit['start'] <= example.end
                                for example in extraction.examples))
        self.assertEqual(path.read_bytes(), source)
        path.write_bytes(candidate)  # Only the test-owned fixture is changed.
        final = self.report()
        self.assertFalse(final['diagnostics'] or final['proposed_edits'])
        self.assertFalse(final['parse_failures'] or final['configuration_failures'])

    def test_all_real_cli_modes_are_read_only_and_never_invoke_compiler(self):
        config = self.select_markdown(pack='pde')
        source = b'\xef\xbb\xbf# Notes\r\nUse `$X^i$`.\r\n~~~typst\r\n$Y_j$\r\n~~~\r\n'
        path = self.put('notes.md', source)
        config_before = config.read_bytes()
        output = self.put('existing.pdf', b'KEEP EXISTING OUTPUT')
        compiler = self.put('compiler-tripwire', '#!/bin/sh\nprintf invoked > "$0.ran"\n')
        compiler.chmod(0o755)
        for mode in ('check', 'dry-run', 'fix', 'compile'):
            with self.subTest(mode=mode):
                value = self.cli(mode, compiler=compiler)
                self.assertEqual(value['mode'], mode)
                self.assertEqual(len(value['diagnostics']), 2)
                self.assertTrue(value['proposed_edits'])
                self.assertEqual({item['rule_id'] for item in value['proposed_edits']}, {SCRIPT})
                self.assertEqual(path.read_bytes(), source)
                self.assertEqual(config.read_bytes(), config_before)
                self.assertEqual(output.read_bytes(), b'KEEP EXISTING OUTPUT')
                self.assertFalse(Path(str(compiler) + '.ran').exists())

    def test_config_ignore_suppresses_findings_and_proposals_but_retains_coverage(self):
        self.select_markdown(extra='[[ignore]]\npaths=["**/*.md"]\n'
                             f'rules=["{SCRIPT}"]\nreason="intentional examples"\n')
        source = b'Inline $X^i$ and `$Y_j$`.\n```typst\n$Z^k$\n```\n'
        self.put('notes.md', source)
        value = self.report()
        self.assertFalse(value['diagnostics'] or value['proposed_edits'])
        self.assertEqual(value['suppressed_count'], 3)
        self.assertEqual(value['suppressed_by_rule'], {SCRIPT: 3})
        self.assertIn(self.rows(value)[SCRIPT]['status'], ('evaluated', 'partial'))

    def test_real_fence_directive_targets_one_whole_construct_and_expires(self):
        self.select_markdown()
        source = (b'```typst\n' + DIRECTIVE + b'\n#box[\n$A^i + B_j$\n]\n$C^k$\n```\n'
                  b'~~~typst\n$D_l$\n~~~\nInline $E^m$.\n')
        self.put('notes.md', source)
        value = self.report()
        self.assertEqual(value['suppressed_count'], 2)
        self.assertEqual(value['suppressed_by_rule'], {SCRIPT: 2})
        self.assertEqual(len(value['diagnostics']), 3)
        self.assertFalse(value['configuration_failures'])
        expected = source.replace(b'C^k', b'C^(k)').replace(b'D_l', b'D_(l)').replace(b'E^m', b'E^(m)')
        self.assertEqual(self.candidate(source, value), expected)

    def test_config_and_directive_ignores_count_each_operand_once(self):
        self.select_markdown(extra='[[ignore]]\npaths=["**/*.md"]\n'
                             f'rules=["{SCRIPT}"]\nreason="intentional examples"\n')
        self.put('notes.md', b'Inline $X^i$.\n```typst\n' + DIRECTIVE + b'\n$Y_j$\n$Z^k$\n```\n')
        contexts = {}
        value = self.report(contexts=contexts)
        self.assertFalse(value['diagnostics'] or value['proposed_edits'])
        self.assertEqual(value['suppressed_count'], 3)
        self.assertEqual(value['suppressed_by_rule'], {SCRIPT: 3})
        result = contexts['notes.md'].analyze()
        self.assertEqual(len(result.diagnostics), 2)
        self.assertEqual(len(result.suppressed), 1)
        self.assertFalse(result.edits)

    def test_markdown_prose_and_other_rule_directives_cannot_suppress_scripts(self):
        self.select_markdown()
        source = (b'// typst-lint: ignore-next missing.rule -- Markdown prose, not Typst\n'
                  b'Inline $X^i$.\n```typst\n'
                  b'// typst-lint: ignore-next pde.text-only-highlight -- different rule\n$Y_j$\n```\n')
        self.put('notes.md', source)
        value = self.report()
        self.assertFalse(value['configuration_failures'])
        self.assertEqual(value['suppressed_count'], 0)
        self.assertEqual(len(value['diagnostics']), 2)
        self.assertEqual(self.candidate(source, value), source.replace(b'X^i', b'X^(i)').replace(b'Y_j', b'Y_(j)'))

    def test_exact_teaching_sentence_only_is_exempt(self):
        self.select_markdown()
        near_match = b'Do not write bare script such as `$X^i$` or `$X_j$`.'
        source = TEACHING + b'\n' + near_match + b'\nUse `$A^i$` or `$B_j$`.\n'
        self.put('notes.md', source)
        value = self.report()
        self.assertEqual(len(value['diagnostics']), 4)
        self.assertEqual([item['line'] for item in value['diagnostics']], [2, 2, 3, 3])
        candidate = self.candidate(source, value)
        self.assertEqual(candidate.splitlines()[0], TEACHING)
        self.assertEqual(candidate, TEACHING + b'\nDo not write bare script such as '
                         b'`$X^(i)$` or `$X_(j)$`.\nUse `$A^(i)$` or `$B_(j)$`.\n')

    def test_unsupported_markdown_remains_explicitly_partial_without_repairs(self):
        self.select_markdown()
        for source in (b'$$\nX^i\n$$\n',
                       b'Inline $X^\ni$ spans lines.\n',
                       b'`not just $X^i$`\n',
                       b' ```typst\n$X^i$\n ```\n',
                       b'```typst title=example\n$X^i$\n```\n'):
            with self.subTest(source=source):
                self.put('notes.md', source)
                value = self.report()
                row = self.rows(value)[SCRIPT]
                self.assertEqual(row['status'], 'partial')
                self.assertTrue(row['reasons'])
                self.assertFalse(value['diagnostics'] or value['proposed_edits'])
                self.assertFalse(value['compiler_invoked'])

    def test_ignored_malformed_supported_math_is_still_an_unsuppressible_parse_failure(self):
        self.select_markdown(extra='[[ignore]]\npaths=["**/*.md"]\n'
                             f'rules=["{SCRIPT}"]\nreason="intentional examples"\n')
        for source in (b'Inline $X^$.\n', b'```typst\n#let =\n```\n'):
            with self.subTest(source=source):
                path = self.put('notes.md', source)
                value = self.report()
                self.assertTrue(value['parse_failures'])
                self.assertEqual({item['path'] for item in value['parse_failures']}, {'notes.md'})
                for failure in value['parse_failures']:
                    for span in failure['spans']:
                        self.assertTrue(0 <= span['start'] <= span['end'] <= len(source))
                self.assertFalse(value['proposed_edits'])
                self.assertEqual(path.read_bytes(), source)

    def test_invalid_or_dangling_fence_directives_are_not_hidden_by_config_ignore(self):
        self.select_markdown(extra='[[ignore]]\npaths=["**/*.md"]\n'
                             f'rules=["{SCRIPT}"]\nreason="intentional examples"\n')
        for body in (b'// typst-lint: ignore-next missing.rule -- invalid\n$X^i$\n',
                     b'// typst-lint: ignore-next core.parenthesized-scripts --\n$X^i$\n',
                     DIRECTIVE + b'\n'):
            with self.subTest(body=body):
                source = b'```typst\n' + body + b'```\n$Y_j$\n'
                self.put('notes.md', source)
                value = self.report()
                self.assertEqual(len(value['configuration_failures']), 1)
                self.assertEqual(value['configuration_failures'][0]['path'], 'notes.md')
                self.assertFalse(value['proposed_edits'])
                self.assertEqual(self.rows(value)[SCRIPT]['status'], 'configuration-failure')

    def test_unavailable_parser_is_unsupported_not_success_or_applicable_other_rules(self):
        self.select_markdown(pack='pde')
        self.put('notes.md', b'Inline $X^i$.\n')
        with patch('markdown_scripts.parse', side_effect=AdapterError('test syntax adapter unavailable')):
            value = self.report()
        self.assertFalse(value['diagnostics'] or value['proposed_edits'])
        self.assertEqual(self.rows(value)[SCRIPT]['status'], 'unsupported')
        self.assertTrue(any('test syntax adapter unavailable' in reason
                            for reason in self.rows(value)[SCRIPT]['reasons']))
        self.assertTrue(all(row['status'] == 'not-applicable' for rid, row in self.rows(value).items()
                            if rid != SCRIPT))
        self.assert_read_only(value)

    def test_markdown_changed_after_capture_is_refused_before_rule_analysis(self):
        self.select_markdown()
        path = self.put('notes.md', b'Inline $X^i$.\n')
        original = lint.capture_selected_sources
        def raced(root, config):
            captured = original(root, config)
            path.write_bytes(b'Inline $Y_j$.\n')
            return captured
        with patch.object(lint, 'capture_selected_sources', side_effect=raced), \
                patch('syntax_client.parse', side_effect=AssertionError('Changed source must not parse')):
            value = self.report()
        self.assertFalse(value['diagnostics'] or value['proposed_edits'])
        row = self.rows(value)[SCRIPT]
        self.assertEqual(row['status'], 'unsupported')
        self.assertIn('Input changed during analysis: notes.md', ' '.join(row['reasons']))

    def audit_pair(self):
        self.select_markdown(pack='pde')
        source = (b'\xef\xbb\xbf# Notes\r\nInline `$X^i$`.\r\n```typst\r\n'
                  + DIRECTIVE + b'\r\n$Y_j$\r\n$Z^k$\r\n```\r\n')
        path = self.put('notes.md', source)
        contexts = {}
        baseline = self.report(contexts=contexts)
        candidate = self.candidate(source, baseline)
        path.write_bytes(candidate)
        final = self.report()
        self.assertNotEqual(candidate, source)
        self.assertFalse(final['proposed_edits'] or final['diagnostics'])
        return baseline, final, {'notes.md': source.decode('utf-8')}, {'notes.md': candidate.decode('utf-8')}, contexts

    def test_audit_authenticates_markdown_against_frozen_captured_context_without_rereads(self):
        from markdown_scripts import MarkdownContext
        baseline, final, sources, candidates, contexts = self.audit_pair()
        context = contexts['notes.md']
        self.assertIs(type(context), MarkdownContext)
        self.assertEqual(context.source, sources['notes.md'].encode('utf-8'))
        self.assertEqual(context.path, 'notes.md')
        with self.assertRaises(FrozenInstanceError):
            context.source = b'changed'
        self.put('notes.md', b'changed after independent capture')
        with patch.object(lint, '_read_selected_source', side_effect=AssertionError('No context source rereads')), \
                patch('package_loader.load_packages', side_effect=AssertionError('No Markdown package resolution')), \
                patch.object(source_analysis, '_analyze', side_effect=AssertionError('No unrelated Markdown rules')):
            value = candidate_verdict(baseline, final, sources, candidates, analysis_contexts=contexts)
        self.assertEqual(value['status'], 'VERIFIED_SUBSET', value['reasons'])
        self.assertTrue(value['verified'])
        self.assertEqual(value['repaired_pairs'], [dict(path='notes.md', rule_id=SCRIPT)])
        self.assertEqual(value['checked_pairs'], [dict(path='notes.md', rule_id=SCRIPT)])
        self.assertFalse(final['coverage']['complete'])

    def test_audit_cannot_certify_markdown_without_original_context(self):
        baseline, final, sources, candidates, contexts = self.audit_pair()
        for supplied in (None, {}, {'notes.md': object()},
                         {'notes.md': replace(contexts['notes.md'], path='different.md')},
                         {'notes.md': replace(contexts['notes.md'], source=b'changed baseline')}):
            with self.subTest(context=supplied):
                value = candidate_verdict(baseline, final, sources, candidates, analysis_contexts=supplied)
                self.assertEqual(value['status'], 'BLOCKED_ANALYSIS')
                self.assertFalse(value['verified'])
                self.assertIn('repair ownership', ' '.join(value['reasons']))

    def test_audit_preserves_partial_unsupported_regions_while_authenticating_independent_math(self):
        self.select_markdown()
        source = b'$$\nX^i\n$$\nInline $Y_j$.\n'
        path = self.put('notes.md', source)
        contexts = {}
        baseline = self.report(contexts=contexts)
        candidate = self.candidate(source, baseline)
        self.assertEqual(candidate, b'$$\nX^i\n$$\nInline $Y_(j)$.\n')
        path.write_bytes(candidate)
        final = self.report()
        for value in (baseline, final):
            self.assertEqual(self.rows(value)[SCRIPT]['status'], 'partial')
            self.assertFalse(value['coverage']['complete'])
        verdict = candidate_verdict(baseline, final, {'notes.md': source.decode()},
                                    {'notes.md': candidate.decode()}, analysis_contexts=contexts)
        self.assertEqual(verdict['status'], 'VERIFIED_SUBSET', verdict['reasons'])
        self.assertTrue(verdict['verified'])

    def test_audit_refuses_forged_markdown_attribution_hash_and_unrelated_edits(self):
        baseline, final, sources, candidates, contexts = self.audit_pair()
        for change in ('attribution', 'hash', 'unrelated-edit', 'invented-history'):
            with self.subTest(change=change):
                forged = copy.deepcopy(baseline)
                changed_candidates = dict(candidates)
                if change == 'attribution':
                    for edit in forged['proposed_edits']:
                        edit['rule_id'] = 'pde.text-only-highlight'
                elif change == 'hash':
                    forged['proposed_edits'][0]['baseline_sha256'] = '0' * 64
                elif change == 'unrelated-edit':
                    forged['proposed_edits'].append(dict(path='notes.md', start=0, end=0,
                        replacement='invented prose ', rule_id=SCRIPT,
                        baseline_sha256=lint.digest(sources['notes.md'].encode('utf-8'))))
                    changed_candidates['notes.md'] = 'invented prose ' + candidates['notes.md']
                else:
                    forged['convergence_evidence'] = [dict(path='notes.md', passes=2, steps=[])]
                value = candidate_verdict(forged, final, sources, changed_candidates, analysis_contexts=contexts)
                self.assertEqual(value['status'], 'BLOCKED_ANALYSIS', value['reasons'])
                self.assertFalse(value['verified'])

    def test_markdown_context_rejects_wrong_baseline_and_retains_config_ignore(self):
        from markdown_scripts import MarkdownContext
        source = b'Inline $X^i$.\n'
        context = MarkdownContext(source, 'notes.md', frozenset({SCRIPT}))
        results = context.analyze()
        result = results[SCRIPT] if isinstance(results, dict) else results
        self.assertFalse(result.edits)
        with self.assertRaises(lint.SafetyError):
            authenticate_candidate(context, b'not the captured baseline', source)
        with self.assertRaises(lint.SafetyError):
            authenticate_candidate(context, source, b'Inline $X^(i)$.\n')


if __name__ == '__main__':
    unittest.main()
