"""Text output exposes incomplete cancellation analysis without inventing findings."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from syntax_client import EXECUTABLE


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class CancelCoverageTextTests(unittest.TestCase):
    def test_counts_match_json_and_exclude_proven_finding(self):
        source = b'$foo(x)$\n#context $cancel(x)$\n$cancel(x)$\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            entry = root / 'main.typ'
            entry.write_bytes(source)
            def invoke(fmt, *options):
                run = subprocess.run([sys.executable, str(TOOL / 'typst-lint'),
                    'inspect-document', '--root', str(root), '--entrypoint', 'main.typ',
                    '--rule-set', 'pde', '--format', fmt, *options],
                    capture_output=True, text=True, timeout=15)
                self.assertEqual(run.returncode, 2, run.stderr)
                self.assertFalse(run.stderr)
                return json.loads(run.stdout) if fmt == 'json' else run.stdout
            ordinary_text = invoke('text')
            self.assertNotIn('Cancellation coverage is incomplete', ordinary_text)
            report = invoke('json', '--local-rule-hints')
            hints = report['local_rule_hints']
            row, = (r for r in hints['coverage'] if r['rule_id'] == 'pde.theme-cancelmark')
            from cancel_rules import DEFERRED_REASON, UNRESOLVED_REASON
            deferred = sum(r.startswith(DEFERRED_REASON) for r in row['reasons'])
            unresolved = sum(r.startswith(UNRESOLVED_REASON) for r in row['reasons'])
            self.assertEqual((deferred, unresolved), (1, 1))
            self.assertEqual(sum(d['rule_id'] == 'pde.theme-cancelmark'
                                 for d in hints['diagnostics']), 1)
            text = invoke('text', '--local-rule-hints')
            self.assertIn('across captured unique source files', text)
            self.assertIn('1 deferred/dynamic and 1 unresolved/shadowed mathematical call sites', text)
            self.assertIn('these are not violation counts and execution is unproved.', text)
            self.assertIn('See JSON for source positions.', text)
            self.assertFalse(report['applied_fixes'] or report['compiler_invoked'])
            self.assertEqual(entry.read_bytes(), source)

    def test_repeated_includes_are_counted_once_per_captured_source(self):
        main = b'$foo(x)$\n#include "a.typ"\n#include "a.typ"\n#include "b.typ"\n'
        a = b'$foo(x)$\n#context $cancel(x)$\n'
        b = b'$foo(x)$\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for name, data in (('main.typ', main), ('a.typ', a), ('b.typ', b)):
                (root / name).write_bytes(data)
            def invoke(fmt):
                run = subprocess.run([sys.executable, str(TOOL / 'typst-lint'),
                    'inspect-document', '--root', str(root), '--entrypoint', 'main.typ',
                    '--rule-set', 'pde', '--local-rule-hints', '--format', fmt],
                    capture_output=True, text=True, timeout=15)
                self.assertEqual(run.returncode, 2, run.stderr)
                self.assertFalse(run.stderr)
                return json.loads(run.stdout) if fmt == 'json' else run.stdout
            report = invoke('json')
            rows = [r for r in report['local_rule_hints']['coverage']
                    if r['rule_id'] == 'pde.theme-cancelmark']
            self.assertEqual({r['path'] for r in rows}, {'main.typ', 'a.typ', 'b.typ'})
            self.assertEqual(len(rows), 3)
            from cancel_rules import DEFERRED_REASON, UNRESOLVED_REASON
            reasons = [reason for r in rows for reason in r['reasons']]
            self.assertEqual(sum(r.startswith(DEFERRED_REASON) for r in reasons), 1)
            self.assertEqual(sum(r.startswith(UNRESOLVED_REASON) for r in reasons), 3)
            text = invoke('text')
            self.assertIn('across captured unique source files', text)
            self.assertIn('1 deferred/dynamic and 3 unresolved/shadowed mathematical call sites', text)
            for name, data in (('main.typ', main), ('a.typ', a), ('b.typ', b)):
                self.assertEqual((root / name).read_bytes(), data)

    def test_no_summary_when_no_unresolved_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'main.typ').write_bytes(b'$cancel(x)$\n')
            run = subprocess.run([sys.executable, str(TOOL / 'typst-lint'),
                'inspect-document', '--root', str(root), '--entrypoint', 'main.typ',
                '--rule-set', 'pde', '--local-rule-hints', '--format', 'text'],
                capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 2, run.stderr)
            self.assertNotIn('Cancellation coverage is incomplete', run.stdout)


if __name__ == '__main__':
    unittest.main()
