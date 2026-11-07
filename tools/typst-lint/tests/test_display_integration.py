"""Real CLI and cross-rule composition; writes stay inside test fixtures."""
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
from continuation_rules import RULE_ID as CONTINUATION
from footnote_rules import GAP as FOOTNOTE_GAP, PUNCT as FOOTNOTE_PUNCT, ALIGNED as FOOTNOTE_ALIGNED
from source_analysis import rule_ids
from typst_lint import digest, report


class DisplayIntegrationTests(unittest.TestCase):
    def test_all_seven_backends_compose_without_applying_to_consumer(self):
        source = (
            b'#import "@local/text-utils:0.1.2": paragraph_tab, apply-paragraph-tabs\n'
            b'#import "@local/project-references:0.1.0": project-ref\n'
            b'#set text(lang: "en")\n#show: apply-paragraph-tabs\n'
            b'Baseline.\n#paragraph_tab\nthis follows $ A^i=B $ and more.\n\n'
            b'$ C=D $ <equation>\n\nHence the conclusion.\n'
            b'#project-ref("pde", [@energy])\n#project-ref("pde", <missing>)\n')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            path, old_output = root/'main.typ', root/'main.pdf'
            path.write_bytes(source)
            old_output.write_bytes(b'previous published output')
            (root/'.typst-lint.toml').write_text('rule-set="pde"\ncatalog="catalog.json"\n')
            (root/'catalog.json').write_text(json.dumps({'schema': 1, 'projects': {
                'pde': {'references': {'energy': {'page': 1, 'text': 'Energy'}}}}}))
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            results = []
            for launcher, arguments in (
                    ('typst-lint', ['check']), ('typst-lint', ['fix', '--dry-run']),
                    ('typst-lint', ['fix']), ('typst-style', ['compile'])):
                command = [sys.executable, str(TOOL/launcher), *arguments, '--root', str(root),
                           '--package-path', str(REPO), '--format', 'json']
                if launcher == 'typst-style':
                    command += ['--', 'main.typ', 'main.pdf']
                process = subprocess.run(command, capture_output=True, text=True, timeout=30)
                self.assertEqual(process.returncode, 2, process.stderr)
                result = json.loads(process.stdout)
                self.assertFalse(result['compiler_invoked'])
                self.assertFalse(result['applied_fixes'])
                self.assertFalse(result['coverage']['complete'])
                self.assertEqual(result['coverage']['available_backends'], list(rule_ids('pde')))
                self.assertEqual({item['rule_id'] for item in result['diagnostics']}, {'core.parenthesized-scripts', 'core.project-ref-arguments', 'core.project-ref-catalog-target', 'pde.paragraph-capitalization', 'pde.paragraph-marker-gap', 'pde.standalone-display', 'pde.display-continuation-gap'})
                self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})
                results.append(result)
            proposals = results[0]['proposed_edits']
            self.assertTrue(proposals)
            self.assertTrue(all(r['proposed_edits'] == proposals for r in results))
            edits = []
            for item in proposals:
                self.assertEqual(item['path'], 'main.typ')
                self.assertEqual(item['baseline_sha256'], digest(source))
                edits.append(Edit(item['start'], item['end'], item['replacement'].encode()))
            candidate = plan(source, edits).candidate()
            self.assertIn(b'Baseline.\n\n#paragraph_tab\nThis follows\n$ A^(i)=B $\nand more.', candidate)
            self.assertIn(b'$ C=D $ <equation>\nHence the conclusion.', candidate)
            self.assertIn(b'#project-ref("pde", <energy>)', candidate)
            self.assertIn(b'#project-ref("pde", <missing>)', candidate)
            path.write_bytes(candidate)  # Test-owned temporary fixture only.
            second = report(root, 'dry-run', package_path=REPO)
            self.assertFalse(second['proposed_edits'])
            self.assertEqual([item['rule_id'] for item in second['diagnostics']],
                             ['core.project-ref-catalog-target'])
            self.assertEqual(old_output.read_bytes(), b'previous published output')

    def test_second_pass_requirement_is_explicitly_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = b'Thus $ A=B $\n\nHence the claim.\n'
            (root/'main.typ').write_bytes(source)
            (root/'.typst-lint.toml').write_text('rule-set="pde"\n')
            result = report(root, 'dry-run')
            self.assertFalse(result['proposed_edits'])
            self.assertTrue(any('second-pass' in reason for item in result['coverage']['files']
                                for reason in item['reasons']))
            self.assertEqual((root/'main.typ').read_bytes(), source)

    def test_newline_cannot_reinterpret_prose_as_a_heading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = b'$ A=B $ = Heading\n'
            (root/'main.typ').write_bytes(source)
            (root/'.typst-lint.toml').write_text('rule-set="pde"\n')
            result = report(root, 'dry-run')
            self.assertFalse(result['proposed_edits'])
            self.assertTrue(any('structural syntax' in reason for item in result['coverage']['files']
                                for reason in item['reasons']))
            self.assertEqual((root/'main.typ').read_bytes(), source)


if __name__ == '__main__':
    unittest.main()
