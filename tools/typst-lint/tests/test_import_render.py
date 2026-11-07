"""Opt-in actual Typst evidence for pure graph binding and output preservation.

All inputs, outputs, package roots and empty caches belong to the fixture. No
network package fetch, installed source changes or production compile gate.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import plan
from import_graph import load_import_graph, rebase_graph
from import_rules import ORDER, REDUNDANT, analyze
from syntax_client import EXECUTABLE, parse


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                     and shutil.which('typst') and EXECUTABLE.is_file(),
                     'Opt in with TYPST_LINT_RENDER_TESTS=1; requires Typst and syntax adapter')
class ImportRenderTests(unittest.TestCase):
    def test_reordering_and_removal_preserve_actual_exports_and_svg(self):
        version = subprocess.run(['typst', '--version'], check=True, capture_output=True, text=True).stdout
        if not version.startswith('typst 0.15.1 '):
            self.skipTest('Evidence requires Typst 0.15.1')
        with tempfile.TemporaryDirectory(prefix='typst-import-render-') as directory:
            root = Path(directory).resolve()
            (root / 'Styles').mkdir()
            (root / 'empty-cache').mkdir()
            (root / 'empty-packages').mkdir()
            files = {
                'leaf.typ': b'#let token = "Retained"\n#let count = 7\n',
                'Styles/styles.typ': b'#import "../leaf.typ": token, count\n',
                'Styles/local_tags.typ': b'#import "../leaf.typ": token, count\n',
                'figures.typ': b'#let diagram = "Diagram"\n',
            }
            for path, source in files.items():
                (root / path).write_bytes(source)
            source = (b'#import "figures.typ": diagram\n'
                      b'#import "Styles/styles.typ": token as retained, count\n'
                      b'#import "Styles/local_tags.typ": token as retained, count\n'
                      b'#metadata((retained, count, diagram)) <identity>\n'
                      b'#retained #count #diagram\n')
            main = root / 'main.typ'
            main.write_bytes(source)
            graph = load_import_graph(root, (('main.typ', source),))
            result = analyze(parse(source), 'main.typ', graph=graph)
            self.assertTrue(result[ORDER].edits and result[REDUNDANT].edits)
            candidate = plan(source, [e for r in result.values() for e in r.edits]).candidate()
            expected = (b'#import "Styles/styles.typ": token as retained, count\n'
                        b'#import "figures.typ": diagram\n'
                        b'#metadata((retained, count, diagram)) <identity>\n'
                        b'#retained #count #diagram\n')
            self.assertEqual(candidate, expected)
            queries, renders = [], []
            flags = ['--root', str(root), '--package-path', str(root / 'empty-packages'),
                     '--package-cache-path', str(root / 'empty-cache')]
            for stage, content in (('before', source), ('after', candidate)):
                main.write_bytes(content)
                query = subprocess.run(['typst', 'query', *flags, str(main), '<identity>'],
                                       capture_output=True, text=True, check=True, timeout=30)
                queries.append(json.loads(query.stdout))
                svg = root / f'{stage}.svg'
                subprocess.run(['typst', 'compile', *flags, str(main), str(svg)],
                               capture_output=True, check=True, timeout=30)
                renders.append(svg.read_bytes())
            self.assertEqual(queries[0], queries[1])
            self.assertEqual(queries[0][0]['value'], ['Retained', 7, 'Diagram'])
            self.assertEqual(renders[0], renders[1])
            candidate_graph = rebase_graph(graph, 'main.typ', source, candidate)
            final = analyze(parse(candidate), 'main.typ', graph=candidate_graph)
            self.assertFalse(any(r.edits or r.diagnostics for r in final.values()))
            self.assertEqual({path: (root / path).read_bytes() for path in files}, files)
            self.assertEqual(list((root / 'empty-cache').iterdir()), [])
            self.assertEqual(list((root / 'empty-packages').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
