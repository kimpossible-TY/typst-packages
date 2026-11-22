"""Real source-to-CLI evidence over test-owned copies of the reviewed module."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import Edit, plan
from flowbox_rules import RULE_ID
from source_analysis import rule_ids
from syntax_client import EXECUTABLE
from typst_lint import digest

PACKAGE = REPO / 'local/math-blocks/0.2.0/lib.typ'


def copy_dependencies(destination):
    originals = {}
    for name, version in (('text-utils', '0.1.2'), ('project-references', '0.1.0')):
        for filename in ('lib.typ', 'typst.toml'):
            relative = Path('local') / name / version / filename
            source = ((TOOL / 'tests/fixtures/reviewed-packages') / relative).read_bytes()
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source)
            originals[relative] = source
    return originals


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter explicitly first')
class FlowboxIntegrationTests(unittest.TestCase):
    def test_check_dry_run_fix_and_compile_have_a_real_positive_and_stay_read_only(self):
        original = PACKAGE.read_bytes()
        source = original.replace(b'stroke: 1pt + theme.rule,', b'stroke: 0.750pt /* keep width */,')
        source = b'\xef\xbb\xbf' + source.replace(b'\n', b'\r\n')
        expected = source.replace(b'0.750pt /* keep width */', b'0.750pt + theme.rule /* keep width */')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            packages = root / 'packages'
            dependency_bytes = copy_dependencies(packages)
            target = root / 'candidate-lib.typ'
            target.write_bytes(source)
            (root / '.typst-lint.toml').write_text('rule-set="pde"\ninclude=["candidate-lib.typ"]\n')
            (root / 'output.pdf').write_bytes(b'previous output')
            spies = root / 'bin'
            spies.mkdir()
            marker = root / 'compiler-invoked'
            spy = spies / 'typst'
            spy.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 99\n')
            spy.chmod(0o755)
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            for mode, extra in (('check', []), ('fix', ['--dry-run']), ('fix', []),
                                ('compile', ['--', 'candidate-lib.typ', 'output.pdf'])):
                launcher = 'typst-style' if mode == 'compile' else 'typst-lint'
                command = [sys.executable, str(TOOL / launcher), mode, '--root', str(root),
                           '--package-path', str(packages), '--format', 'json', *extra]
                run = subprocess.run(command, capture_output=True, text=True, timeout=30,
                                     env={**os.environ, 'PATH': str(spies) + os.pathsep + os.environ.get('PATH', '')})
                self.assertEqual(run.returncode, 2, run.stderr)
                value = json.loads(run.stdout)
                self.assertEqual({d['rule_id'] for d in value['diagnostics']}, {RULE_ID}, value)
                self.assertEqual(value['coverage']['available_backends'], list(rule_ids('pde')))
                self.assertIn(RULE_ID, value['coverage']['available_backends'])
                self.assertEqual({e['rule_id'] for e in value['proposed_edits']}, {RULE_ID})
                self.assertEqual(len(value['proposed_edits']), 1)
                proposal = value['proposed_edits'][0]
                self.assertEqual(proposal['baseline_sha256'], digest(source))
                self.assertEqual(proposal['start'], proposal['end'])
                self.assertEqual(proposal['replacement'], ' + theme.rule')
                self.assertEqual(plan(source, [Edit(proposal['start'], proposal['end'],
                                                   proposal['replacement'].encode())]).candidate(), expected)
                self.assertFalse(value['compiler_invoked'] or value['applied_fixes'] or value['coverage']['complete'])
                row = next(r for r in value['coverage']['files'] if r['rule_id'] == RULE_ID)
                self.assertEqual(row['status'], 'partial')
                self.assertFalse(value['configuration_failures'] or value['parse_failures'] or value['package_unavailable'])
                self.assertEqual({e['source_sha256'] for e in value['package_evidence']},
                                 {digest(raw) for relative, raw in dependency_bytes.items() if relative.name == 'lib.typ'})
                self.assertEqual({str(p.relative_to(root)): p.read_bytes()
                                  for p in root.rglob('*') if p.is_file()}, before)
                self.assertFalse(marker.exists())
            target.write_bytes(expected)  # Only this test harness applies its own proposal.
            again = subprocess.run([sys.executable, str(TOOL / 'typst-lint'), 'check', '--root', str(root),
                                    '--package-path', str(packages), '--format', 'json'],
                                   capture_output=True, text=True, timeout=30)
            self.assertEqual(again.returncode, 2, again.stderr)
            self.assertFalse(json.loads(again.stdout)['diagnostics'] or json.loads(again.stdout)['proposed_edits'])
            self.assertEqual(target.read_bytes(), expected)
            self.assertEqual(PACKAGE.read_bytes(), original)
            for relative, raw in dependency_bytes.items():
                self.assertEqual((packages / relative).read_bytes(), raw)
                self.assertEqual(((TOOL / 'tests/fixtures/reviewed-packages') / relative).read_bytes(), raw)

    def test_core_exclusion_configuration_ignore_inline_suppression_and_fragments(self):
        source = PACKAGE.read_bytes().replace(b'stroke: 1pt + theme.rule,', b'stroke: 1pt,')
        suppressed = source.replace(b'#let flowbox(body)',
                                    b'// typst-lint: ignore-next pde.flowbox-colored-border -- example\n#let flowbox(body)')
        fragment = b'#let flowbox(body) = context { let theme = (rule: red); block(stroke: 1pt, body) }\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target, config = root / 'candidate-lib.typ', root / '.typst-lint.toml'
            for contents, settings, count, available in (
                (source, b'rule-set="core"\n', 0, False),
                (source, b'rule-set="pde"\n[[ignore]]\npaths=["candidate-lib.typ"]\n'
                 b'rules=["pde.flowbox-colored-border"]\nreason="reviewed example"\n', 1, True),
                (suppressed, b'rule-set="pde"\n', 1, True),
                (fragment, b'rule-set="pde"\n', 0, True),
            ):
                target.write_bytes(contents)
                config.write_bytes(settings)
                process = subprocess.run([sys.executable, str(TOOL / 'typst-lint'), 'check', '--root', str(root),
                                          '--format', 'json'], capture_output=True, text=True, timeout=30)
                self.assertEqual(process.returncode, 2, process.stderr)
                value = json.loads(process.stdout)
                self.assertFalse(value['diagnostics'] or value['proposed_edits'] or value['compiler_invoked'])
                self.assertEqual(value['suppressed_by_rule'].get(RULE_ID, 0), count)
                self.assertEqual(RULE_ID in value['coverage']['available_backends'], available)
                self.assertEqual(target.read_bytes(), contents)
                self.assertEqual(config.read_bytes(), settings)


if __name__ == '__main__':
    unittest.main()
