"""Opt-in actual CLI two-pass marker/casing plan -> isolated Typst rendering.

Only this test applies proposals to its own temporary fixture. Original
dependencies, installed packages and consumer documents are never changed.
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
from paragraph_rules import CAPITALIZATION
from prose_marker_rules import RULE_ID
from static_resolver import TEXT_UTILS_SOURCE_SHA256
from syntax_client import EXECUTABLE
from typst_lint import digest


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                     and shutil.which('typst') and shutil.which('pdftotext') and EXECUTABLE.is_file(),
                     'Opt in with TYPST_LINT_RENDER_TESTS=1; requires typst, pdftotext and syntax adapter')
class MarkerFollowupRenderTests(unittest.TestCase):
    def test_actual_two_pass_cli_plan_capitalizes_and_indents_only_eligible_prose(self):
        version = subprocess.run(['typst', '--version'], check=True, capture_output=True, text=True).stdout
        if not version.startswith('typst 0.15.1 '):
            self.skipTest('Placement comparison requires Typst 0.15.1')
        source = (b'#import "@local/text-utils:0.1.2": paragraph_tab, apply-paragraph-tabs\n'
                  b'#show: apply-paragraph-tabs\n'
                  b'#set page(width: 420pt, height: 300pt, margin: 30pt)\n'
                  b'#set text(size: 12pt, lang: "en")\n'
                  b'Baseline.\n\nthis consequence follows with iPhone and eBay.\n\n'
                  b'iPhone remains unchanged.\n\nMath $x^(i)$ stays.\n')
        expected = source.replace(b'this consequence', b'#paragraph_tab\nThis consequence')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            consumer, packages, cache = root / 'fixture', root / 'packages', root / 'empty-cache'
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
            config = consumer / '.typst-lint.toml'
            config.write_bytes(b'rule-set="pde"\ninclude=["fixture.typ"]\n')
            old_output = consumer / 'previous.pdf'
            old_output.write_bytes(b'previous output must not be compiled over')
            command = [sys.executable, str(TOOL / 'typst-lint'), 'fix', '--dry-run',
                       '--root', str(consumer), '--package-path', str(packages), '--format', 'json']
            process = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, 2, process.stderr)
            value = json.loads(process.stdout)
            self.assertFalse(value['compiler_invoked'] or value['applied_fixes'] or value['coverage']['complete'])
            self.assertEqual(value['proposal_status'], 'partial-not-applicable-transaction')
            self.assertEqual(fixture.read_bytes(), source)
            self.assertEqual(config.read_bytes(), b'rule-set="pde"\ninclude=["fixture.typ"]\n')
            self.assertEqual(old_output.read_bytes(), b'previous output must not be compiled over')
            self.assertEqual({item['rule_id'] for item in value['diagnostics']}, {RULE_ID})
            self.assertEqual(len(value['proposed_edits']), 1)
            proposal, = value['proposed_edits']
            self.assertEqual((proposal['path'], proposal['baseline_sha256'], proposal['repair_passes']),
                             ('fixture.typ', digest(source), 2))
            self.assertEqual(set(proposal['rule_ids']), {RULE_ID, CAPITALIZATION})
            candidate = plan(source, (Edit(proposal['start'], proposal['end'], proposal['replacement'].encode()),)).candidate()
            self.assertEqual(candidate, expected)

            # Serialized history records actual distinct rule passes: insertion
            # leaves lowercase intact, then one ASCII byte changes on reparse.
            record, = value['convergence_evidence']
            self.assertEqual((record['path'], record['passes'], record['baseline_sha256'], record['candidate_sha256']),
                             ('fixture.typ', 2, digest(source), digest(candidate)))
            first, second = record['steps']
            stage = source
            for step, owner in ((first, RULE_ID), (second, CAPITALIZATION)):
                self.assertEqual(step['before_sha256'], digest(stage))
                applied, = step['applied']
                self.assertEqual(applied['rule_id'], owner)
                self.assertEqual(applied['rule_ids'], [owner])
                self.assertFalse(step['moves'])
                stage = plan(stage, (Edit(applied['start'], applied['end'], applied['replacement'].encode()),)).candidate()
                self.assertEqual(step['after_sha256'], digest(stage))
                if owner == RULE_ID:
                    self.assertEqual(stage, source.replace(b'this consequence', b'#paragraph_tab\nthis consequence'))
                    self.assertEqual((applied['start'], applied['end']),
                                     (source.index(b'this consequence'),) * 2)
                    self.assertEqual(applied['replacement'], '#paragraph_tab\n')
                else:
                    self.assertEqual(applied['end'], applied['start'] + 1)
                    self.assertEqual(applied['replacement'], 'T')
            self.assertEqual(stage, candidate)
            self.assertEqual(candidate.count(b'iPhone'), source.count(b'iPhone'))
            self.assertIn(b'eBay.', candidate)
            self.assertIn(b'iPhone remains unchanged.\n\nMath $x^(i)$ stays.\n', candidate)
            self.assertEqual(sorted(p.name for p in consumer.iterdir()),
                             ['.typst-lint.toml', 'fixture.typ', 'previous.pdf'])
            self.assertFalse(list(cache.rglob('*')))

            common = ['--root', str(consumer), '--package-path', str(packages), '--package-cache-path', str(cache)]
            renders = []
            for stage_name, content in (('before', source), ('after', candidate)):
                fixture.write_bytes(content)  # This temporary fixture only.
                pdf, xml = consumer / f'{stage_name}.pdf', consumer / f'{stage_name}.xml'
                subprocess.run(['typst', 'compile', *common, str(fixture), str(pdf)],
                               check=True, capture_output=True, timeout=60)
                subprocess.run(['pdftotext', '-bbox', str(pdf), str(xml)],
                               check=True, capture_output=True, timeout=30)
                words = [node for node in ET.parse(xml).iter() if node.tag.endswith('}word')]
                renders.append([(word.text, word.attrib) for word in words])
                self.assertFalse(list(cache.rglob('*')))
                for filename, original in originals.items():
                    self.assertEqual((destination / filename).read_bytes(), original)
                    self.assertEqual((REPO / 'local/text-utils/0.1.2' / filename).read_bytes(), original)
            before, after = renders
            before_words, after_words = [word for word, _ in before], [word for word, _ in after]
            self.assertEqual(before_words[:8], ['Baseline.', 'this', 'consequence', 'follows',
                                                'with', 'iPhone', 'and', 'eBay.'])
            self.assertEqual(after_words, [word if i != 1 else 'This' for i, word in enumerate(before_words)])
            self.assertEqual(before[0], after[0])
            self.assertAlmostEqual(float(after[1][1]['xMin']) - float(before[1][1]['xMin']),
                                   18.0, delta=0.05)
            # Independent protected-name paragraph, math glyphs and remaining
            # prose retain their exact text order and PDF bounding boxes.
            self.assertEqual(before_words[8:12], ['iPhone', 'remains', 'unchanged.', 'Math'])
            self.assertEqual(before[8:], after[8:])
            self.assertNotIn('IPhone', after_words)
            self.assertNotIn('EBay.', after_words)
            final = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(final.returncode, 2, final.stderr)
            final_value = json.loads(final.stdout)
            self.assertFalse(final_value['proposed_edits'])
            self.assertFalse(final_value['compiler_invoked'] or final_value['applied_fixes'])
            self.assertEqual(len(final_value['diagnostics']), 1)
            self.assertEqual(final_value['diagnostics'][0]['rule_id'], RULE_ID)
            self.assertEqual(fixture.read_bytes(), candidate)
            self.assertEqual(old_output.read_bytes(), b'previous output must not be compiled over')
            self.assertFalse(list(cache.rglob('*')))
            for filename, original in originals.items():
                self.assertEqual((destination / filename).read_bytes(), original)
                self.assertEqual((REPO / 'local/text-utils/0.1.2' / filename).read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
