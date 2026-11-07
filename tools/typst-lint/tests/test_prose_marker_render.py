"""Opt-in real CLI plan -> isolated explicit-package render indentation proof."""
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
from prose_marker_rules import RULE_ID
from static_resolver import TEXT_UTILS_SOURCE_SHA256
from syntax_client import EXECUTABLE
from typst_lint import digest


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                     and shutil.which('typst') and shutil.which('pdftotext') and EXECUTABLE.is_file(),
                     'Opt in with TYPST_LINT_RENDER_TESTS=1; requires typst, pdftotext and syntax adapter')
class ProseMarkerRenderTests(unittest.TestCase):
    def test_actual_marker_proposal_indents_without_changing_any_prose_byte(self):
        version = subprocess.run(['typst', '--version'], check=True, capture_output=True, text=True).stdout
        if not version.startswith('typst 0.15.1 '):
            self.skipTest('Placement comparison requires Typst 0.15.1')
        source = (b'#import "@local/text-utils:0.1.2": paragraph_tab, apply-paragraph-tabs\n'
                  b'#show: apply-paragraph-tabs\n'
                  b'#set page(width: 360pt, height: 260pt, margin: 30pt)\n'
                  b'#set text(size: 12pt, lang: "en")\n'
                  b'Baseline.\n\nThis consequence follows.\n')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            consumer, packages, cache = root / 'consumer', root / 'packages', root / 'empty-cache'
            consumer.mkdir()
            cache.mkdir()
            destination = packages / 'local/text-utils/0.1.2'
            destination.mkdir(parents=True)
            originals = {}
            for filename in ('lib.typ', 'typst.toml'):
                original = (REPO / 'local/text-utils/0.1.2' / filename).read_bytes()
                (destination / filename).write_bytes(original)
                originals[filename] = original
            self.assertEqual(digest(originals['lib.typ']), TEXT_UTILS_SOURCE_SHA256)
            fixture = consumer / 'fixture.typ'
            fixture.write_bytes(source)
            (consumer / '.typst-lint.toml').write_text('rule-set="pde"\n')
            command = [sys.executable, str(TOOL / 'typst-lint'), 'fix', '--dry-run',
                       '--root', str(consumer), '--package-path', str(packages), '--format', 'json']
            process = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, 2, process.stderr)
            value = json.loads(process.stdout)
            self.assertFalse(value['compiler_invoked'] or value['applied_fixes'] or value['coverage']['complete'])
            self.assertEqual(fixture.read_bytes(), source)
            self.assertEqual({edit['rule_id'] for edit in value['proposed_edits']}, {RULE_ID})
            proposals = [Edit(edit['start'], edit['end'], edit['replacement'].encode())
                         for edit in value['proposed_edits']]
            candidate = plan(source, proposals).candidate()
            self.assertEqual(candidate, source.replace(b'This consequence', b'#paragraph_tab\nThis consequence'))
            common = ['--root', str(consumer), '--package-path', str(packages), '--package-cache-path', str(cache)]
            renders = []
            for stage, content in (('before', source), ('after', candidate)):
                fixture.write_bytes(content)
                pdf, xml = consumer / f'{stage}.pdf', consumer / f'{stage}.xml'
                subprocess.run(['typst', 'compile', *common, str(fixture), str(pdf)],
                               check=True, capture_output=True, timeout=60)
                subprocess.run(['pdftotext', '-bbox', str(pdf), str(xml)],
                               check=True, capture_output=True, timeout=30)
                words = [node for node in ET.parse(xml).iter() if node.tag.endswith('}word')]
                self.assertEqual([node.text for node in words], ['Baseline.', 'This', 'consequence', 'follows.'])
                renders.append({node.text: node.attrib for node in words})
            before, after = renders
            self.assertEqual(before['Baseline.'], after['Baseline.'])
            self.assertAlmostEqual(float(after['This']['xMin']) - float(before['This']['xMin']), 18.0, delta=0.05)
            final = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(final.returncode, 2, final.stderr)
            self.assertFalse(json.loads(final.stdout)['proposed_edits'])
            self.assertEqual(fixture.read_bytes(), candidate)
            self.assertFalse(list(cache.rglob('*')))
            for filename, original in originals.items():
                self.assertEqual((destination / filename).read_bytes(), original)
                self.assertEqual((REPO / 'local/text-utils/0.1.2' / filename).read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
