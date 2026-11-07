"""Opt-in real parser -> partial plan -> isolated Typst rendering evidence.

Only this test applies returned proposals to its temporary fixture. Production
CLI writes/compilation remain disabled and full-rule coverage remains incomplete.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import Edit, plan
from typst_lint import digest


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                     and shutil.which('typst') and shutil.which('pdftotext'),
                     'Opt in with TYPST_LINT_RENDER_TESTS=1; requires typst and pdftotext')
class SourceRulesRenderTests(unittest.TestCase):
    def test_actual_source_plans_render_and_preserve_protected_name(self):
        source = (b'#import "@local/text-utils:0.1.2": paragraph_tab, apply-paragraph-tabs\n'
                  b'#set text(size: 12pt, lang: "en")\n'
                  b'#show: apply-paragraph-tabs\n'
                  b'Baseline.\n#paragraph_tab\nthis consequence $x^i$ follows.\n\n'
                  b'#paragraph_tab\niPhone remains unchanged.\n')
        packages = TOOL.parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            input_file, pdf, xml = root/'fixture.typ', root/'fixture.pdf', root/'fixture.xml'
            (root/'.typst-lint.toml').write_text('rule-set = "pde"\n', encoding='utf-8')
            input_file.write_bytes(source)
            command = [sys.executable, str(TOOL/'typst-lint'), 'fix', '--dry-run',
                       '--root', str(root), '--package-path', str(packages), '--format', 'json']
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 2, result.stderr)
            report = json.loads(result.stdout)
            self.assertFalse(report['compiler_invoked'])
            self.assertFalse(report['applied_fixes'])
            self.assertEqual(report['proposal_status'], 'partial-not-applicable-transaction')
            self.assertEqual(input_file.read_bytes(), source)
            ids = {item['rule_id'] for item in report['diagnostics']}
            self.assertTrue({'core.parenthesized-scripts', 'pde.paragraph-marker-gap',
                             'pde.paragraph-capitalization'} <= ids)
            edits = []
            for proposal in report['proposed_edits']:
                self.assertEqual(proposal['path'], 'fixture.typ')
                self.assertEqual(proposal['baseline_sha256'], digest(source))
                edits.append(Edit(proposal['start'], proposal['end'],
                                  proposal['replacement'].encode('utf-8')))
            candidate = plan(source, edits).candidate()
            self.assertIn(b'Baseline.\n\n#paragraph_tab\nThis consequence $x^(i)$ follows.', candidate)
            self.assertIn(b'#paragraph_tab\niPhone remains unchanged.', candidate)
            input_file.write_bytes(candidate)  # Test-owned isolated fixture only.
            second = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(second.returncode, 2, second.stderr)
            self.assertFalse(json.loads(second.stdout)['proposed_edits'])
            subprocess.run([shutil.which('typst'), 'compile', '--package-path', str(packages),
                            str(input_file), str(pdf)], check=True, capture_output=True, timeout=60)
            subprocess.run([shutil.which('pdftotext'), '-bbox', str(pdf), str(xml)],
                           check=True, capture_output=True, timeout=30)
            words = [node for node in ET.parse(xml).iter() if node.tag.endswith('}word')]
            baseline = next(word for word in words if word.text == 'Baseline.')
            capitalized = next(word for word in words if word.text == 'This')
            self.assertAlmostEqual(float(capitalized.attrib['xMin']) - float(baseline.attrib['xMin']),
                                   18.0, delta=0.05)
            self.assertIn('iPhone', [word.text for word in words])
            self.assertNotIn('IPhone', [word.text for word in words])
            self.assertNotIn('this', [word.text for word in words])
            self.assertEqual(input_file.read_bytes(), candidate)


if __name__ == '__main__':
    unittest.main()
