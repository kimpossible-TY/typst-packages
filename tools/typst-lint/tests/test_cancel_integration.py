"""Real read-only CLI calls, not synthetic parser/resolver attestations."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from cancel_rules import RULE_ID
from source_analysis import rule_ids
from syntax_client import EXECUTABLE


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter explicitly first')
class CancelIntegrationTests(unittest.TestCase):
    def test_check_dry_run_fix_and_compile_are_read_only_and_partial(self):
        source = ('\ufeff#let crossout = math.cancel\r\n'
                  '$crossout(x, stroke: #blue, cross: #true)$\r\n'
                  '#let cancelmark(body, ..options) = body\r\n'
                  '$cancelmark(y, stroke: #blue)$\r\n').encode()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            packages = root / 'packages'
            packages.mkdir()
            (root / 'main.typ').write_bytes(source)
            (root / '.typst-lint.toml').write_bytes(b'rule-set="pde"\n')
            (root / 'output.pdf').write_bytes(b'previous output')
            spies = root / 'bin'
            spies.mkdir()
            marker = root / 'compiler-invoked'
            spy = spies / 'typst'
            spy.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 99\n')
            spy.chmod(0o755)
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            for mode, extra in (('check', []), ('fix', ['--dry-run']), ('fix', []),
                                ('compile', ['--', 'main.typ', 'output.pdf'])):
                launcher = 'typst-style' if mode == 'compile' else 'typst-lint'
                command = [sys.executable, str(TOOL / launcher), mode, '--root', str(root),
                           '--package-path', str(packages), '--format', 'json', *extra]
                run = subprocess.run(command, capture_output=True, text=True, timeout=30,
                                     env={**os.environ, 'PATH': str(spies) + os.pathsep + os.environ.get('PATH', '')})
                self.assertEqual(run.returncode, 2, run.stderr)
                value = json.loads(run.stdout)
                self.assertEqual({d['rule_id'] for d in value['diagnostics']}, {RULE_ID})
                self.assertEqual(value['coverage']['available_backends'], list(rule_ids('pde')))
                self.assertIn(RULE_ID, value['coverage']['available_backends'])
                self.assertFalse(value['compiler_invoked'] or value['applied_fixes'] or value['proposed_edits'])
                self.assertFalse(value['coverage']['complete'])
                row = next(r for r in value['coverage']['files'] if r['rule_id'] == RULE_ID)
                self.assertEqual(row['status'], 'partial')
                self.assertTrue(any('fix_unavailable' in s for s in row['reasons']))
                self.assertEqual({str(p.relative_to(root)): p.read_bytes()
                                  for p in root.rglob('*') if p.is_file()}, before)
                self.assertFalse(marker.exists())
                self.assertFalse(list(packages.iterdir()))

    def test_core_exclusion_and_configuration_and_inline_suppressions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target, config = root / 'main.typ', root / '.typst-lint.toml'
            cases = (
                (b'$cancel(x)$\n', b'rule-set="core"\n', 0, False),
                (b'$cancel(x)$\n', b'rule-set="pde"\n[[ignore]]\npaths=["main.typ"]\n'
                 b'rules=["pde.theme-cancelmark"]\nreason="reviewed example"\n', 1, True),
                (b'// typst-lint: ignore-next pde.theme-cancelmark -- example\n$cancel(x)$\n',
                 b'rule-set="pde"\n', 1, True),
            )
            for source, settings, count, available in cases:
                target.write_bytes(source)
                config.write_bytes(settings)
                run = subprocess.run([sys.executable, str(TOOL / 'typst-lint'), 'check',
                                      '--root', str(root), '--format', 'json'],
                                     capture_output=True, text=True, timeout=30)
                self.assertEqual(run.returncode, 2, run.stderr)
                value = json.loads(run.stdout)
                self.assertFalse(value['diagnostics'] or value['proposed_edits'] or value['compiler_invoked'])
                self.assertEqual(value['suppressed_by_rule'].get(RULE_ID, 0), count)
                self.assertEqual(RULE_ID in value['coverage']['available_backends'], available)
                self.assertEqual(target.read_bytes(), source)
                self.assertEqual(config.read_bytes(), settings)


if __name__ == '__main__':
    unittest.main()
