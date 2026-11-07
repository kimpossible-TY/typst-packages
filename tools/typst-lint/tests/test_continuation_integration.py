"""Cross-rule and read-only CLI checks for bounded continuation/footnotes."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from continuation_rules import RULE_ID as CONTINUATION
from edit_plan import Edit, plan
from footnote_rules import GAP, PUNCT, ALIGNED, UNALIGNED, PROSE
from typst_lint import report


class ContinuationIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('typst'), 'Typst compiler unavailable')
    def test_joined_plain_prose_renders_identically_in_isolated_files(self):
        version = subprocess.run(['typst', '--version'], capture_output=True, text=True, check=True).stdout
        if not version.startswith('typst 0.15.1 '):
            self.skipTest('Render comparison requires Typst 0.15.1')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = b'It is smooth and compactly supported.\nThe calculation follows.\n'
            after = b'It is smooth and compactly supported. The calculation follows.\n'
            for name, contents in (('before', before), ('after', after)):
                source, output = root / (name + '.typ'), root / (name + '.svg')
                source.write_bytes(contents)
                subprocess.run(['typst', 'compile', '--format', 'svg', str(source), str(output)],
                               check=True, capture_output=True)
            self.assertEqual((root / 'before.svg').read_bytes(), (root / 'after.svg').read_bytes())

    def test_cli_composes_and_reanalyzes_without_writing(self):
        source = (b'This statement holds.\nAnother calculation follows. $ A^i=B $ and more.\n\n'
                  b'This. #footnote[Reason.]')
        expected = (b'This statement holds. Another calculation follows.\n$ A^(i)=B $\n'
                    b'and more.\n\nThis.#footnote[Reason.]')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path = root / 'main.typ'
            path.write_bytes(source)
            (root / '.typst-lint.toml').write_text('rule-set="pde"\n')
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            previous = None
            for launcher, args in (('typst-lint', ['check']), ('typst-lint', ['fix', '--dry-run']),
                                   ('typst-lint', ['fix']), ('typst-style', ['compile'])):
                command = [sys.executable, str(TOOL / launcher), *args, '--root', str(root), '--format', 'json']
                if launcher == 'typst-style':
                    command += ['--', 'main.typ', 'main.pdf']
                process = subprocess.run(command, capture_output=True, text=True, timeout=30)
                self.assertEqual(process.returncode, 2, process.stderr)
                result = json.loads(process.stdout)
                self.assertFalse(result['compiler_invoked'] or result['applied_fixes'])
                self.assertFalse(result['coverage']['complete'])
                self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})
                proposals = result['proposed_edits']
                self.assertEqual({e['rule_id'] for e in proposals},
                                 {'core.parenthesized-scripts', 'pde.standalone-display', CONTINUATION, PUNCT})
                if previous is not None:
                    self.assertEqual(proposals, previous)
                previous = proposals
            edits = [Edit(e['start'], e['end'], e['replacement'].encode()) for e in previous]
            self.assertEqual(plan(source, edits).candidate(), expected)
            path.write_bytes(expected)  # Test-owned consumer only.
            self.assertFalse(report(root, 'dry-run')['proposed_edits'])

    def test_missing_spacer_proof_never_creates_repairs_or_claims_full_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / '.typst-lint.toml').write_text('rule-set="pde"\n')
            for source in (b'$ A &= B #footnote[X] $', b'$ A = B #dots_space #footnote[X] $',
                           b'Text.#dots_space #footnote[X]'):
                (root / 'main.typ').write_bytes(source)
                result = report(root, 'dry-run')
                self.assertFalse([e for e in result['proposed_edits'] if e['rule_id'] in (ALIGNED, UNALIGNED, PROSE)])
                self.assertIn(UNALIGNED, result['coverage']['available_backends'])
                self.assertIn(PROSE, result['coverage']['available_backends'])
                rows = {r['rule_id']: r for r in result['coverage']['files']}
                for rid in (ALIGNED, UNALIGNED, PROSE):
                    self.assertEqual(rows[rid]['status'], 'partial')
                self.assertEqual(rows[CONTINUATION]['status'], 'partial')

    def test_gap_suppression_remains_owned_by_footnote(self):
        source = (b'// typst-lint: ignore-next pde.footnote-trailing-gap -- intentional\n'
                  b'#footnote[X]\n\nNext.')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / '.typst-lint.toml').write_text('rule-set="pde"\n')
            (root / 'main.typ').write_bytes(source)
            result = report(root, 'dry-run')
            self.assertEqual(result['suppressed_by_rule'].get(GAP), 1)
            self.assertFalse([d for d in result['diagnostics'] if d['rule_id'] == GAP])


if __name__ == '__main__':
    unittest.main()
