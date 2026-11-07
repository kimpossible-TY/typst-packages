"""Opt-in isolated real rendering: the border color alone changes in each theme.

The harness alone applies the real CLI proposal to temporary source bytes. All
Typst package dependencies are exact repository copies and the cache is empty.
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
from flowbox_rules import RULE_ID, REVIEWED_SOURCE_SHA256
from syntax_client import EXECUTABLE
from typst_lint import digest
from test_flowbox_integration import copy_dependencies


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                     and shutil.which('typst') and EXECUTABLE.is_file(),
                     'Opt in with TYPST_LINT_RENDER_TESTS=1; requires typst and syntax adapter')
class FlowboxRenderTests(unittest.TestCase):
    def test_only_border_color_changes_preserving_width_body_geometry_and_labels(self):
        version = subprocess.run(['typst', '--version'], check=True, capture_output=True, text=True).stdout
        if not version.startswith('typst 0.15.1 '):
            self.skipTest('Exact SVG comparison requires Typst 0.15.1')
        original = (REPO / 'local/math-blocks/0.2.0/lib.typ').read_bytes()
        self.assertEqual(digest(original), REVIEWED_SOURCE_SHA256)
        source = original.replace(b'stroke: 1pt + theme.rule,', b'stroke: 1.750pt /* retain literal */,')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            packages, cache = root / 'packages', root / 'empty-cache'
            cache.mkdir()
            originals = copy_dependencies(packages)
            library = root / 'candidate-lib.typ'
            library.write_bytes(source)
            (root / '.typst-lint.toml').write_text('rule-set="pde"\ninclude=["candidate-lib.typ"]\n')
            command = [sys.executable, str(TOOL / 'typst-lint'), 'fix', '--dry-run', '--root', str(root),
                       '--package-path', str(packages), '--format', 'json']
            process = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, 2, process.stderr)
            report = json.loads(process.stdout)
            self.assertEqual({e['rule_id'] for e in report['proposed_edits']}, {RULE_ID})
            self.assertFalse(report['coverage']['complete'] or report['compiler_invoked'] or report['applied_fixes'])
            self.assertEqual(library.read_bytes(), source)
            candidate = plan(source, [Edit(e['start'], e['end'], e['replacement'].encode())
                                      for e in report['proposed_edits']]).candidate()
            self.assertEqual(candidate, source.replace(b'1.750pt /* retain literal */',
                                                      b'1.750pt + theme.rule /* retain literal */'))
            common = ['--root', str(root), '--package-path', str(packages), '--package-cache-path', str(cache)]
            for theme, expected_color in (('light-theme', '#2c3e50'), ('dark-theme', '#94a9c4')):
                consumer = root / 'consumer.typ'
                consumer.write_text('#import "candidate-lib.typ": flowbox, light-theme, dark-theme\n'
                                    '#set page(width: 250pt, height: 150pt, margin: 20pt, fill: ' + theme + '.page)\n'
                                    '#set text(fill: ' + theme + '.text)\n'
                                    '#flowbox[Stable body] <claim>\n')
                normalized, labels = [], []
                for stage, contents in (('before', source), ('after', candidate)):
                    library.write_bytes(contents)
                    svg = root / f'{theme}-{stage}.svg'
                    subprocess.run(['typst', 'compile', *common, str(consumer), str(svg)],
                                   check=True, capture_output=True, timeout=60)
                    document = ET.parse(svg)
                    borders = [node for node in document.iter() if 'stroke' in node.attrib]
                    self.assertEqual(len(borders), 1)
                    border = borders[0]
                    self.assertEqual(border.attrib['stroke'], '#000000' if stage == 'before' else expected_color)
                    self.assertEqual(border.attrib['stroke-width'], '1.75')
                    self.assertEqual(border.attrib['d'], 'M 0 0v 29.238h 210v -29.238Z ')
                    border.attrib['stroke'] = 'REVIEWED-BORDER-COLOR'
                    normalized.append(ET.tostring(document.getroot()))
                    query = subprocess.run(['typst', 'query', *common, str(consumer), '<claim>'],
                                           check=True, capture_output=True, text=True, timeout=60)
                    labels.append(json.loads(query.stdout))
                # Every element, glyph, body position, clipping rule and path
                # is identical after masking that single expected color.
                self.assertEqual(normalized[0], normalized[1])
                self.assertEqual(labels[0], labels[1])
                self.assertEqual(labels[0], [{'func': 'context', 'label': '<claim>'}])
            final = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(final.returncode, 2, final.stderr)
            self.assertFalse(json.loads(final.stdout)['diagnostics'] or json.loads(final.stdout)['proposed_edits'])
            self.assertEqual(library.read_bytes(), candidate)
            self.assertFalse(list(cache.rglob('*')))
            self.assertEqual((REPO / 'local/math-blocks/0.2.0/lib.typ').read_bytes(), original)
            for relative, raw in originals.items():
                self.assertEqual((packages / relative).read_bytes(), raw)
                self.assertEqual((REPO / relative).read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()
