"""Actual document CLI over isolated include graphs and source-only candidates."""
import json
import os
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))

import document_cli
from edit_plan import Edit, plan
from syntax_client import EXECUTABLE
from typst_lint import InputError, digest


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class DocumentCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='typst-document-cli-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def write(self, path, source):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source)
        return target

    def run_cli(self, *options, entrypoint='main.typ', root=None, format='json', env=None):
        command = [sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
                   '--root', str(self.root if root is None else root),
                   '--entrypoint', entrypoint, '--format', format, *options]
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, env=env)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(result.stderr, result.stderr)
        return json.loads(result.stdout) if format == 'json' else result.stdout

    def test_nested_repeated_includes_and_opaque_wrappers_preserve_inventory(self):
        self.write('main.typ', b'#book-part[\n#include "chapter/chapter.typ"\n'
                               b'#include "chapter/chapter.typ"\n]\n')
        self.write('chapter/chapter.typ', b'#include "section.typ"\n')
        source = b'#import "missing-styles.typ": *\nWords $x^i$ and $y_(j)$.\n'
        self.write('chapter/section.typ', source)
        result = self.run_cli()
        context = result['include_context']
        self.assertEqual(context['budget'], 'book-v1')
        self.assertEqual(len(context['sources']), 3)
        self.assertEqual(len(context['edges']), 3)
        self.assertEqual(len(context['occurrences']), 5)
        self.assertTrue(context['unresolved'], 'Opaque containment must remain unresolved')
        occurrences = [row for row in context['occurrences']
                       if row['source']['path'] == 'chapter/section.typ']
        self.assertEqual(len(occurrences), 2)
        self.assertNotEqual(occurrences[0]['edge_chain'], occurrences[1]['edge_chain'])
        self.assertEqual(len(result['diagnostics']), 1, 'Check each source once, not each occurrence')
        finding, = result['diagnostics']
        self.assertEqual(finding['path'], 'chapter/section.typ')
        self.assertEqual(finding['line'], 2)
        self.assertEqual(len(result['proposed_edits']), 2)
        self.assertTrue(all(row['path'] == 'chapter/section.typ' for row in result['proposed_edits']))
        self.assertEqual(self.run_cli(), result, 'Inventory output must be deterministic')
        self.assertEqual((self.root / 'chapter/section.typ').read_bytes(), source)
        self.assertFalse(result['package_resolution_performed'])
        self.assertFalse(result['coverage']['global_complete'])

    def test_source_bound_candidate_can_be_replayed_and_is_idempotent(self):
        original = (b'\xef\xbb\xbf#import "unused-missing.typ": *\r\n'
                    + '한글 $x^i_j$ /* preserve */\r\n'.encode())
        target = self.write('main.typ', original)
        before = self.run_cli()
        edits = []
        for row in before['proposed_edits']:
            self.assertEqual(row['baseline_sha256'], digest(original))
            self.assertEqual(row['rule_id'], 'core.parenthesized-scripts')
            edits.append(Edit(row['start'], row['end'], row['replacement'].encode()))
        candidate = plan(original, edits).candidate()
        self.assertIn(b'$x^(i)_(j)$', candidate)
        self.assertIn(b'/* preserve */\r\n', candidate)
        file, = before['coverage']['files']
        self.assertEqual(file['candidate_sha256'], digest(candidate))
        self.assertEqual(file['candidate_verification'], 'parse-and-rule-idempotence-checked')
        self.assertEqual(target.read_bytes(), original)
        target.write_bytes(candidate)  # This test owns its disposable fixture.
        after = self.run_cli()
        self.assertEqual(after['diagnostics'], [])
        self.assertEqual(after['proposed_edits'], [])
        self.assertFalse(after['coverage']['global_complete'])
        self.assertFalse(after['compiler_invoked'])

    def test_inline_suppression_is_honored_but_consumer_config_is_not_loaded(self):
        self.write('.typst-lint.toml', b'not even valid TOML or UTF-8: \xff')
        self.write('main.typ', b'// typst-lint: ignore-next core.parenthesized-scripts -- intentional\n'
                               b'$x^i$\n\n$y^j$\n')
        result = self.run_cli()
        self.assertFalse(result['consumer_config_loaded'])
        self.assertEqual(result['suppressed_count'], 1)
        self.assertEqual(len(result['diagnostics']), 1)
        self.assertEqual(result['diagnostics'][0]['line'], 4)
        self.assertEqual(len(result['proposed_edits']), 2)

    def test_missing_and_dynamic_include_requests_remain_explicit(self):
        self.write('main.typ', b'#include "absent.typ"\n#include selected\n$x^i$\n')
        result = self.run_cli()
        context = result['include_context']
        codes = {code for row in context['unresolved'] for code in row['codes']}
        self.assertIn('missing-source', codes)
        self.assertIn('dynamic-request', codes)
        self.assertEqual(len(context['sources']), 1)
        self.assertEqual(len(result['diagnostics']), 1)

    def test_symlink_entrypoint_and_dependency_are_refused(self):
        self.write('actual.typ', b'$x^i$\n')
        (self.root / 'main.typ').symlink_to('actual.typ')
        result = self.run_cli()
        self.assertEqual(result['phase'], 'input')
        self.assertIn('link', result['error'].lower())
        (self.root / 'main.typ').unlink()
        self.write('main.typ', b'#include "linked.typ"\n')
        (self.root / 'linked.typ').symlink_to('actual.typ')
        result = self.run_cli()
        self.assertEqual(result['phase'], 'input')
        self.assertIn('error', result)
        self.assertNotIn('include_context', result)

    def test_malformed_source_and_invalid_directive_refuse_without_partial_report(self):
        for source in (b'$x^(', b'// typst-lint: ignore-next core.parenthesized-scripts\n$x^i$\n'):
            with self.subTest(source=source):
                self.write('main.typ', source)
                result = self.run_cli()
                self.assertEqual(result['phase'], 'input')
                self.assertIn('error', result)
                self.assertNotIn('proposed_edits', result)
                self.assertFalse(result['compiler_invoked'])

    def test_invalid_directive_in_include_identifies_the_source(self):
        self.write('main.typ', b'#include "chapter/section.typ"\n')
        self.write('chapter/section.typ', b'// typst-lint: ignore-next core.parenthesized-scripts\n$x^i$\n')
        result = self.run_cli()
        self.assertEqual(result['phase'], 'input')
        self.assertIn('chapter/section.typ:', result['error'])
        self.assertIn('directive', result['error'])
        self.assertNotIn('proposed_edits', result)

    def test_partial_script_source_keeps_findings_and_withholds_every_edit(self):
        self.write('main.typ', b'$x^/* comment */i$ and $y^j$.\n')
        result = self.run_cli()
        self.assertEqual(len(result['diagnostics']), 1)
        self.assertEqual(result['proposed_edits'], [])
        file, = result['coverage']['files']
        self.assertEqual(file['status'], 'partial')
        self.assertEqual(file['proposed_edits'], 0)
        self.assertIsNone(file['candidate_sha256'])
        self.assertEqual(file['candidate_verification'], 'no-candidate')
        self.assertTrue(any('withheld' in reason for reason in file['reasons']))

    def test_candidate_losing_script_coverage_is_withheld_after_fresh_parse(self):
        source = b'$x^i$\n'
        target = self.write('main.typ', source)
        original_analyze = document_cli.analyze

        def lose_coverage(tree, path, **kwargs):
            result = original_analyze(tree, path, **kwargs)
            if tree.source != source:
                return replace(result, skips=('new unsupported candidate operand',))
            return result

        with patch.object(document_cli, 'analyze', side_effect=lose_coverage) as analyze:
            result = document_cli.report_document(str(self.root), 'main.typ')
        self.assertEqual(analyze.call_count, 2)
        self.assertEqual(len(result['diagnostics']), 1)
        self.assertEqual(result['proposed_edits'], [])
        file, = result['coverage']['files']
        self.assertEqual(file['status'], 'partial')
        self.assertIsNone(file['candidate_sha256'])
        self.assertEqual(file['candidate_verification'], 'no-candidate')
        self.assertIn('Candidate coverage: new unsupported candidate operand', file['reasons'])
        self.assertEqual(target.read_bytes(), source)

    def test_paths_must_be_canonical_and_entrypoint_typst(self):
        self.write('main.typ', b'$x^i$\n')
        for path in ('../main.typ', '/main.typ', './main.typ', 'main.md', 'bad\n.typ'):
            with self.subTest(path=path):
                result = self.run_cli(entrypoint=path)
                self.assertEqual(result['phase'], 'input')
                self.assertIn('error', result)
        result = self.run_cli(root='.')
        self.assertEqual(result['phase'], 'input')

    def test_no_compiler_or_consumer_mutations_and_clean_source_stays_incomplete(self):
        source = b'Literal prose and $x^(i)$.\n'
        self.write('main.typ', source)
        self.write('existing.pdf', b'previous output')
        binary = self.write('bin/typst', b'#!/bin/sh\nprintf invoked > "$DOCUMENT_COMPILER_TRIPWIRE"\nexit 99\n')
        binary.chmod(0o755)
        tripwire = self.root / 'compiler-attempt'
        env = dict(os.environ, PATH=str(binary.parent) + os.pathsep + os.environ.get('PATH', ''),
                   DOCUMENT_COMPILER_TRIPWIRE=str(tripwire))
        baseline = {str(path.relative_to(self.root)): path.read_bytes()
                    for path in self.root.rglob('*') if path.is_file()}
        result = self.run_cli(env=env)
        self.assertEqual(result['diagnostics'], [])
        self.assertEqual(result['proposed_edits'], [])
        self.assertEqual(result['applied_fixes'], [])
        self.assertFalse(result['compiler_invoked'])
        self.assertFalse(result['coverage']['global_complete'])
        self.assertFalse(tripwire.exists())
        self.assertEqual({str(path.relative_to(self.root)): path.read_bytes()
                          for path in self.root.rglob('*') if path.is_file()}, baseline)
        text = self.run_cli(format='text', env=env)
        self.assertIn('Global coverage incomplete (exit 2)', text)
        self.assertIn('Only core.parenthesized-scripts', text)

    def test_report_budget_refuses_instead_of_truncating(self):
        self.write('main.typ', b'$x^i$\n')
        with patch.object(document_cli, 'MAX_REPORT_ITEMS', 1):
            with self.assertRaisesRegex(InputError, 'report item limit'):
                document_cli.report_document(str(self.root), 'main.typ')


if __name__ == '__main__':
    unittest.main()
