"""Opt-in isolated CLI proposal, rendered paragraph and label identity evidence.

All compiler inputs/packages are explicit test-owned temp copies. No downloads,
shared installs, user project writes or production compile gates are involved.
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
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import Edit, plan
from proof_rules import RULE_ID
from static_resolver import MATH_BLOCKS_SOURCE_SHA256
from syntax_client import EXECUTABLE
from typst_lint import digest


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                     and shutil.which('typst') and shutil.which('pdftotext') and EXECUTABLE.is_file(),
                     'Opt in with TYPST_LINT_RENDER_TESTS=1; requires typst, pdftotext and syntax adapter')
class ProofRenderTests(unittest.TestCase):
    def test_gap_separates_following_prose_and_preserves_labelled_proof(self):
        version = subprocess.run(['typst', '--version'], check=True, capture_output=True, text=True).stdout
        if not version.startswith('typst 0.15.1 '):
            self.skipTest('Placement comparison requires Typst 0.15.1')
        prefix = (b'#import "@local/math-blocks:0.2.0": proof\n'
                  b'#set page(width: 360pt, height: 260pt, margin: 30pt)\n'
                  b'#set text(size: 12pt)\n')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            packages, cache, consumer = root / 'packages', root / 'empty-cache', root / 'consumer'
            cache.mkdir()
            consumer.mkdir()
            originals = {}
            for name, version in (('math-blocks', '0.2.0'), ('text-utils', '0.1.2'),
                                  ('project-references', '0.1.0')):
                destination = packages / 'local' / name / version
                destination.mkdir(parents=True)
                for filename in ('lib.typ', 'typst.toml'):
                    relative = Path('local') / name / version / filename
                    contents = ((TOOL / 'tests/fixtures/reviewed-packages') / relative).read_bytes()
                    (packages / relative).write_bytes(contents)
                    originals[relative] = contents
            self.assertEqual(digest(originals[Path('local/math-blocks/0.2.0/lib.typ')]), MATH_BLOCKS_SOURCE_SHA256)
            source = prefix + b'#proof[The claim follows.] <claim>\nNext paragraph.'
            fixture = consumer / 'fixture.typ'
            fixture.write_bytes(source)
            (consumer / '.typst-lint.toml').write_text('rule-set="pde"\n')
            command = [sys.executable, str(TOOL / 'typst-lint'), 'fix', '--dry-run',
                       '--root', str(consumer), '--package-path', str(packages), '--format', 'json']
            process = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, 2, process.stderr)
            report = json.loads(process.stdout)
            self.assertFalse(report['coverage']['complete'] or report['compiler_invoked'] or report['applied_fixes'])
            self.assertEqual(fixture.read_bytes(), source)
            self.assertEqual({e['rule_id'] for e in report['proposed_edits']}, {RULE_ID})
            proposals = [Edit(e['start'], e['end'], e['replacement'].encode()) for e in report['proposed_edits']]
            candidate = plan(source, proposals).candidate()
            self.assertEqual(candidate, source.replace(b'<claim>\n', b'<claim>\n\n'))
            common = ['--root', str(consumer), '--package-path', str(packages),
                      '--package-cache-path', str(cache)]
            renders, labels = [], []
            for stage, contents in (('before', source), ('after', candidate)):
                fixture.write_bytes(contents)
                pdf, xml = consumer / f'{stage}.pdf', consumer / f'{stage}.xml'
                subprocess.run(['typst', 'compile', *common, str(fixture), str(pdf)],
                               check=True, capture_output=True, timeout=60)
                subprocess.run(['pdftotext', '-bbox', str(pdf), str(xml)],
                               check=True, capture_output=True, timeout=30)
                words = [node for node in ET.parse(xml).iter() if node.tag.endswith('}word')]
                self.assertEqual([node.text for node in words],
                                 ['Proof.', 'The', 'claim', 'follows.', '∎', 'Next', 'paragraph.'])
                renders.append({node.text: node.attrib for node in words})
                query = subprocess.run(['typst', 'query', *common, str(fixture), '<claim>'],
                                       check=True, capture_output=True, text=True, timeout=60)
                labels.append(json.loads(query.stdout))
            # Proof text remains in the same place. The QED moves to the right
            # margin and following prose begins below it, at the left margin.
            before, after = renders
            for word in ('Proof.', 'The', 'claim', 'follows.'):
                self.assertEqual(before[word], after[word])
            self.assertLess(abs(float(before['Next']['yMin']) - float(before['∎']['yMin'])), 3)
            self.assertGreater(float(after['Next']['yMin']), float(after['∎']['yMax']))
            self.assertAlmostEqual(float(after['Next']['xMin']), 30.0)
            self.assertAlmostEqual(float(after['∎']['xMax']), 330.0)
            self.assertEqual(labels[0], labels[1])
            self.assertEqual(len(labels[0]), 1)
            self.assertEqual(labels[0][0]['label'], '<claim>')
            self.assertEqual(labels[0][0]['func'], 'sequence')
            self.assertEqual(labels[0][0]['children'][-1], {'func': 'symbol', 'text': '∎'})
            final = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(final.returncode, 2, final.stderr)
            self.assertFalse(json.loads(final.stdout)['proposed_edits'])
            self.assertEqual(fixture.read_bytes(), candidate)
            self.assertFalse(list(cache.rglob('*')))
            for relative, contents in originals.items():
                self.assertEqual((packages / relative).read_bytes(), contents)
                self.assertEqual(((TOOL / 'tests/fixtures/reviewed-packages') / relative).read_bytes(), contents)


if __name__ == '__main__':
    unittest.main()
