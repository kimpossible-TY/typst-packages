"""Reviewed proof identities through real CLI and whole-candidate composition."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import Edit, plan
from proof_rules import RULE_ID
from source_analysis import rule_ids
from static_resolver import MATH_BLOCKS, MATH_BLOCKS_SOURCE_SHA256
from syntax_client import EXECUTABLE
from typst_lint import report

IMPORT = b'#import "@local/math-blocks:0.2.0": proof\n'


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class ProofIntegrationTests(unittest.TestCase):
    def test_real_cli_proposes_stable_proof_and_script_edits_but_never_writes_or_compiles(self):
        source = b'\xef\xbb\xbf' + IMPORT + b'#proof[The claim $x^i$ follows.] <claim>\nNext paragraph.\n'
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            root, packages, cache = base / 'consumer', base / 'packages', base / 'empty-cache'
            root.mkdir()
            cache.mkdir()
            package = packages / 'local/math-blocks/0.2.0'
            package.mkdir(parents=True)
            originals = {}
            for name in ('lib.typ', 'typst.toml'):
                originals[name] = (REPO / 'local/math-blocks/0.2.0' / name).read_bytes()
                (package / name).write_bytes(originals[name])
            # Dependency files are deliberately absent: linting checks the
            # explicit immutable artifact without executing any imports.
            target = root / 'main.typ'
            target.write_bytes(source)
            output = root / 'output.pdf'
            output.write_bytes(b'keep existing output')
            (root / '.typst-lint.toml').write_text('rule-set="pde"\n')
            for mode in ('check', 'dry-run', 'fix', 'compile'):
                launcher = 'typst-style' if mode == 'compile' else 'typst-lint'
                command = [sys.executable, str(TOOL / launcher), 'fix' if mode == 'dry-run' else mode,
                           '--root', str(root), '--package-path', str(packages), '--format', 'json']
                if mode == 'dry-run':
                    command.append('--dry-run')
                if mode == 'compile':
                    command.extend(['--', 'main.typ', 'output.pdf'])
                process = subprocess.run(command, capture_output=True, text=True, timeout=30)
                self.assertEqual(process.returncode, 2, process.stderr)
                value = json.loads(process.stdout)
                self.assertEqual({d['rule_id'] for d in value['diagnostics']},
                                 {RULE_ID, 'core.parenthesized-scripts'})
                self.assertEqual(value['coverage']['available_backends'], list(rule_ids('pde')))
                self.assertFalse(value['coverage']['complete'] or value['compiler_invoked'] or value['applied_fixes'])
                self.assertEqual(value['proposal_status'], 'partial-not-applicable-transaction')
                evidence, = value['package_evidence']
                self.assertEqual((evidence['import_path'], evidence['source_sha256']),
                                 (MATH_BLOCKS, MATH_BLOCKS_SOURCE_SHA256))
                self.assertEqual(target.read_bytes(), source)
                self.assertEqual(output.read_bytes(), b'keep existing output')
                proposals = [Edit(e['start'], e['end'], e['replacement'].encode())
                             for e in value['proposed_edits']]
                candidate = plan(source, proposals).candidate()
                self.assertEqual(candidate, source.replace(b'x^i', b'x^(i)').replace(b'<claim>\n', b'<claim>\n\n'))
            target.write_bytes(candidate)  # Test-owned temp file, never production.
            final = report(root, 'check', package_path=packages)
            self.assertFalse(final['proposed_edits'] or final['diagnostics'])
            self.assertFalse(final['coverage']['complete'])
            self.assertFalse(list(cache.rglob('*')))
            for name, contents in originals.items():
                self.assertEqual((package / name).read_bytes(), contents)
                self.assertEqual((REPO / 'local/math-blocks/0.2.0' / name).read_bytes(), contents)

    def test_suppression_config_ignore_and_core_pack_leave_source_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path, config = root / 'main.typ', root / '.typst-lint.toml'
            cases = (
                (b'// typst-lint: ignore-next pde.proof-trailing-gap -- chosen\n', 'rule-set="pde"\n'),
                (b'', 'rule-set="pde"\n[[ignore]]\npaths=["main.typ"]\nrules=["pde.proof-trailing-gap"]\nreason="chosen"\n'),
                (b'', 'rule-set="core"\n'),
            )
            for directive, settings in cases:
                with self.subTest(settings=settings, directive=directive):
                    source = IMPORT + directive + b'#proof[X]\nNext.'
                    path.write_bytes(source)
                    config.write_text(settings)
                    value = report(root, 'check', package_path=REPO)
                    self.assertFalse(value['diagnostics'] or value['proposed_edits'])
                    self.assertEqual(path.read_bytes(), source)
                    if directive:
                        self.assertEqual(value['suppressed_by_rule'][RULE_ID], 1)
                    if settings == 'rule-set="core"\n':
                        self.assertNotIn(RULE_ID, value['coverage']['available_backends'])

    def test_composition_preserves_suppression_ownership_and_ineligible_cases(self):
        source = (IMPORT + b'// typst-lint: ignore-next core.parenthesized-scripts -- proof body\n'
                  b'#proof[$ x^i $] <claim>\nNext.\n')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / 'main.typ'
            (root / '.typst-lint.toml').write_text('rule-set="pde"\n')
            path.write_bytes(source)
            value = report(root, 'check', package_path=REPO)
            self.assertEqual(value['suppressed_by_rule']['core.parenthesized-scripts'], 1)
            self.assertEqual({e['rule_id'] for e in value['proposed_edits']}, {RULE_ID})
            candidate = plan(source, [Edit(e['start'], e['end'], e['replacement'].encode())
                                      for e in value['proposed_edits']]).candidate()
            self.assertEqual(candidate, source.replace(b'<claim>\n', b'<claim>\n\n'))
            path.write_bytes(candidate)
            final = report(root, 'check', package_path=REPO)
            self.assertFalse(final['diagnostics'] or final['proposed_edits'])
            self.assertEqual(final['suppressed_by_rule']['core.parenthesized-scripts'], 1)
            for body in (b'#proof[X]', b'#proof[X]\n<claim>\nNext.',
                         b'#proof[X]\n// ownership\nNext.', b'#block[#proof[X]\nNext.]'):
                path.write_bytes(IMPORT + body)
                value = report(root, 'check', package_path=REPO)
                self.assertNotIn(RULE_ID, {d['rule_id'] for d in value['diagnostics']})
                self.assertFalse(value['proposed_edits'])


if __name__ == '__main__':
    unittest.main()
