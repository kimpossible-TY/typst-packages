"""Static import obligations remain bounded, opt-in and non-authoritative."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_cli
import document_import_obligations as obligations
import include_context
from syntax_client import EXECUTABLE
from typst_lint import SafetyError


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class DocumentImportObligationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='import-obligations-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.sources = {
            'main.typ': b'#include "child.typ"\n#import "a.typ": item\n',
            'child.typ': b'#import "a.typ": item\n',
            'a.typ': b'#import "b.typ": item\n#let item = 1\n',
            'b.typ': (b'#import "a.typ": item\n#import "@preview/fletcher:0.5.8": *\n'
                      b'#import target: *\n#import "missing.typ": x\n'),
        }
        for path, source in self.sources.items():
            (self.root / path).write_bytes(source)

    def context(self):
        return include_context.load_include_context(self.root, (('main.typ', self.sources['main.typ']),))

    def test_reachable_obligations_include_shortest_path_and_terminate_on_cycle(self):
        report = obligations.analyze(self.context())
        self.assertFalse(report['runtime_reachability_proved'])
        self.assertFalse(report['package_evaluation_performed'])
        self.assertFalse(report['dynamic_imports_complete'])
        self.assertFalse(report['request_count_is_location_count'])
        self.assertFalse(report['edit_permission'] or report['compiler_permission'])
        self.assertEqual({item['path'] for item in report['relative_sources']},
                         {'main.typ', 'child.typ', 'a.typ', 'b.typ'})
        rows = {row['path']: row for row in report['by_source']}
        self.assertEqual(set(rows), {'main.typ', 'child.typ'})
        for row in rows.values():
            self.assertEqual({x['category'] for x in row['obligations']},
                             {'package-not-evaluated', 'dynamic-or-escaped-import', 'relative-source-missing'})
            self.assertEqual(row['reachable_source_count'], 3)
            self.assertEqual([edge['target'] for edge in row['obligations'][0]['relative_import_path']],
                             ['a.typ', 'b.typ'])
        self.assertEqual({path: (self.root / path).read_bytes() for path in self.sources}, self.sources)

    def test_unissued_context_and_changed_seed_fail_closed(self):
        context = self.context()
        with self.assertRaises(include_context.IncludeCaptureError):
            obligations.analyze(replace(context))
        (self.root / 'main.typ').write_bytes(self.sources['main.typ'] + b'changed')
        with self.assertRaises(ValueError):
            obligations.analyze(context)

    def test_limits_abort_without_partial_report(self):
        context = self.context()
        for name in ('MAX_WORK', 'MAX_OUTPUT_ITEMS', 'MAX_OUTPUT_BYTES'):
            with self.subTest(name=name), patch.object(obligations, name, 1), self.assertRaises(SafetyError):
                obligations.analyze(context)

    def test_outside_root_and_symlink_imports_never_read_target(self):
        source = b'#import "../outside.typ": item\n'
        (self.root / 'main.typ').write_bytes(source)
        context = include_context.load_include_context(self.root, (('main.typ', source),))
        report = obligations.analyze(context)
        item = report['by_source'][0]['obligations'][0]
        self.assertEqual(item['category'], 'other-unresolved-import')
        with tempfile.TemporaryDirectory(prefix='outside-import-') as outside:
            target = Path(outside) / 'secret.typ'
            target.write_text('untouched')
            (self.root / 'outside.typ').symlink_to(target)
            source = b'#import "outside.typ": item\n'
            (self.root / 'main.typ').write_bytes(source)
            context = include_context.load_include_context(self.root, (('main.typ', source),))
            with self.assertRaises(ValueError):
                obligations.analyze(context)
            self.assertEqual(target.read_text(), 'untouched')

    def test_cli_json_text_and_opt_out(self):
        baseline = document_cli.report_document(str(self.root), 'main.typ')
        self.assertNotIn('import_obligations', baseline)
        def run(fmt):
            return subprocess.run([sys.executable, str(TOOL / 'typst-lint'), 'inspect-document',
                '--root', str(self.root), '--entrypoint', 'main.typ', '--import-obligations', '--format', fmt],
                capture_output=True, text=True, timeout=30)
        result = run('json')
        self.assertEqual(result.returncode, 2, result.stderr)
        parsed = json.loads(result.stdout)
        self.assertFalse(parsed['compiler_invoked'] or parsed['applied_fixes'])
        self.assertEqual(parsed['import_obligations']['source_obligation_pair_count'], 6)
        self.assertEqual(parsed['diagnostics'], baseline['diagnostics'])
        text = run('text')
        self.assertEqual(text.returncode, 2, text.stderr)
        self.assertIn('Static import obligations', text.stdout)
        self.assertIn('package-not-evaluated', text.stdout)


if __name__ == '__main__':
    unittest.main()
