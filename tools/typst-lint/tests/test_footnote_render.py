"""Opt-in CLI proposals -> isolated before/after spacer rendering evidence.

All required local packages are copied into a temporary explicit package path.
No shared install, consumer, dependency discovery or downloaded fixture is used.
Only this test applies proposals to its own files; production gates stay shut.
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
from footnote_rules import ALIGNED, UNALIGNED, PROSE
from static_resolver import MATH_BLOCKS, MATH_BLOCKS_SOURCE_SHA256
from syntax_client import EXECUTABLE
from typst_lint import digest


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                     and shutil.which('typst') and shutil.which('pdftotext') and EXECUTABLE.is_file(),
                     'Opt in with TYPST_LINT_RENDER_TESTS=1; requires typst, pdftotext and syntax adapter')
class FootnoteRenderTests(unittest.TestCase):
    def test_reviewed_spacer_insertion_and_removals_render_intended_effects(self):
        version = subprocess.run(['typst', '--version'], check=True, capture_output=True, text=True).stdout
        if not version.startswith('typst 0.15.1 '):
            self.skipTest('Placement comparison requires Typst 0.15.1')
        prefix = (b'#import "@local/math-blocks:0.2.0": dots_space\n'
                  b'#set page(width: 360pt, height: 260pt, margin: 30pt)\n'
                  b'#set text(size: 12pt)\n')
        fixtures = (
            (ALIGNED, b'$ A &= B #footnote[Aligned note.] $',
             b'$ A &= B #dots_space #footnote[Aligned note.] $', False, True, 'Aligned'),
            (UNALIGNED, b'$ A = B #dots_space #footnote[Unaligned note.] $',
             b'$ A = B #footnote[Unaligned note.] $', True, False, 'Unaligned'),
            (PROSE, b'This.#dots_space #footnote[Prose note.]',
             b'This.#footnote[Prose note.]', True, False, 'Prose'),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            packages, cache = root / 'packages', root / 'empty-cache'
            cache.mkdir()
            originals = {}
            # The immutable math-blocks entrypoint imports only these two local
            # dependencies. Their manifests/entrypoints are copied explicitly;
            # project-ref's unused runtime catalog is not read or manufactured.
            for name, version in (('math-blocks', '0.2.0'), ('text-utils', '0.1.2'),
                                  ('project-references', '0.1.0')):
                destination = packages / 'local' / name / version
                destination.mkdir(parents=True)
                for filename in ('lib.typ', 'typst.toml'):
                    relative = Path('local') / name / version / filename
                    contents = ((TOOL / 'tests/fixtures/reviewed-packages') / relative).read_bytes()
                    (packages / relative).write_bytes(contents)
                    originals[relative] = contents
            self.assertEqual(digest(originals[Path('local/math-blocks/0.2.0/lib.typ')]),
                             MATH_BLOCKS_SOURCE_SHA256)

            for index, (rule, before, after, dots_before, dots_after, body_word) in enumerate(fixtures):
                with self.subTest(rule=rule):
                    consumer = root / f'consumer-{index}'
                    consumer.mkdir()
                    source = prefix + before
                    fixture = consumer / 'fixture.typ'
                    fixture.write_bytes(source)
                    (consumer / '.typst-lint.toml').write_text('rule-set="pde"\n')
                    command = [sys.executable, str(TOOL / 'typst-lint'), 'fix', '--dry-run',
                               '--root', str(consumer), '--package-path', str(packages), '--format', 'json']
                    process = subprocess.run(command, capture_output=True, text=True, timeout=30)
                    self.assertEqual(process.returncode, 2, process.stderr)
                    report = json.loads(process.stdout)
                    self.assertFalse(report['coverage']['complete'])
                    self.assertFalse(report['compiler_invoked'] or report['applied_fixes'])
                    self.assertEqual(fixture.read_bytes(), source)
                    self.assertEqual({edit['rule_id'] for edit in report['proposed_edits']}, {rule})
                    evidence = next(item for item in report['package_evidence'] if item['import_path'] == MATH_BLOCKS)
                    self.assertEqual(evidence['source_sha256'], MATH_BLOCKS_SOURCE_SHA256)
                    proposals = [Edit(edit['start'], edit['end'], edit['replacement'].encode())
                                 for edit in report['proposed_edits']]
                    candidate = plan(source, proposals).candidate()
                    self.assertEqual(candidate, prefix + after)

                    renders = []
                    for stage, contents, expect_dots in (('before', source, dots_before),
                                                        ('after', candidate, dots_after)):
                        fixture.write_bytes(contents)  # Test-owned fixture only.
                        pdf, xml = consumer / f'{stage}.pdf', consumer / f'{stage}.xml'
                        subprocess.run(['typst', 'compile', '--root', str(consumer),
                                        '--package-path', str(packages), '--package-cache-path', str(cache),
                                        str(fixture), str(pdf)], check=True, capture_output=True, timeout=60)
                        subprocess.run(['pdftotext', '-bbox', str(pdf), str(xml)],
                                       check=True, capture_output=True, timeout=30)
                        words = [node for node in ET.parse(xml).iter() if node.tag.endswith('}word')]
                        top = ''.join(node.text or '' for node in words if float(node.attrib['yMin']) < 80)
                        note = [(node.text, node.attrib) for node in words if float(node.attrib['yMin']) > 180]
                        self.assertEqual('⋯' in top, expect_dots)
                        self.assertEqual(top.replace('⋯', ''), 'This.1' if rule == PROSE else '𝐴=𝐵1')
                        self.assertEqual(''.join(text for text, _ in note), '1' + body_word + 'note.')
                        renders.append(note)
                    # The body, number and its page-bottom placement remain
                    # identical; only the requested equation/prose spacer changes.
                    self.assertEqual(renders[0], renders[1])
                    second = subprocess.run(command, capture_output=True, text=True, timeout=30)
                    self.assertEqual(second.returncode, 2, second.stderr)
                    self.assertFalse(json.loads(second.stdout)['proposed_edits'])
                    self.assertEqual(fixture.read_bytes(), candidate)

            self.assertFalse(list(cache.rglob('*')))
            for relative, contents in originals.items():
                self.assertEqual((packages / relative).read_bytes(), contents)
                self.assertEqual(((TOOL / 'tests/fixtures/reviewed-packages') / relative).read_bytes(), contents)


if __name__ == '__main__':
    unittest.main()
