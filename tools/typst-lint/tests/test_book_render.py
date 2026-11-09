"""Opt-in representative reset equivalence, not general permission to repair.

Only exact repository dependencies are explicitly copied into temporary package
roots. The real compiler uses an empty cache; the production gate stays closed.
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
from book_rules import RESET
from static_resolver import MATH_BOOK_SOURCE_SHA256, MATH_BLOCKS_SOURCE_SHA256
from syntax_client import EXECUTABLE
from typst_lint import digest


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                     and shutil.which('typst') and shutil.which('pdftotext') and EXECUTABLE.is_file(),
                     'Opt in with TYPST_LINT_RENDER_TESTS=1; requires typst, pdftotext and syntax adapter')
class BookRenderTests(unittest.TestCase):
    def test_adjacent_default_pair_has_equal_representative_render_and_reference_queries(self):
        version = subprocess.run(['typst', '--version'], check=True, capture_output=True, text=True).stdout
        if not version.startswith('typst 0.15.1 '):
            self.skipTest('Representative equivalence evidence requires Typst 0.15.1')
        source = (b'#import "@local/math-book:0.2.0": apply-math-book\n'
                  b'#import "@local/math-blocks:0.2.0": apply-math-block-reset, theorem\n'
                  b'#show: apply-math-book.with(title: "Book reset evidence")\n'
                  b'#show: apply-math-block-reset\n'
                  b'#set page(width: 360pt, height: 500pt, margin: 30pt, header: none, footer: none)\n'
                  b'#set text(font: "New Computer Modern")\n'
                  b'#set heading(numbering: "1.1")\n'
                  b'= One\n== Alpha\n#theorem[First claim.] <first>\n'
                  b'== Beta\n#theorem[Second claim.] <second>\n'
                  b'= Two\n== Gamma\n#theorem[Third claim.] <third>\n'
                  b'See @first, @second, and @third.\n')
        # This is a manually specified test comparison, never a lint proposal.
        candidate = source.replace(b'#show: apply-math-block-reset\n', b'', 1)
        with tempfile.TemporaryDirectory(prefix='typst-book-render-') as directory:
            root = Path(directory).resolve()
            packages, cache, consumer = (root / p for p in ('packages', 'empty-cache', 'consumer'))
            cache.mkdir()
            consumer.mkdir()
            originals = {}
            for name, version in (('math-book', '0.2.0'), ('math-blocks', '0.2.0'),
                                  ('text-utils', '0.1.2'), ('project-references', '0.1.0')):
                for filename in (('lib.typ', 'typst.toml', 'cover.typ') if name == 'math-book'
                                 else ('lib.typ', 'typst.toml')):
                    relative = Path('local') / name / version / filename
                    raw = (((TOOL / 'tests/fixtures/reviewed-packages') if relative.parts[1] == "text-utils" else REPO) / relative).read_bytes()
                    destination = packages / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(raw)
                    originals[relative] = raw
            self.assertEqual(digest(originals[Path('local/math-book/0.2.0/lib.typ')]), MATH_BOOK_SOURCE_SHA256)
            self.assertEqual(digest(originals[Path('local/math-blocks/0.2.0/lib.typ')]), MATH_BLOCKS_SOURCE_SHA256)
            fixture = consumer / 'main.typ'
            fixture.write_bytes(source)
            (consumer / '.typst-lint.toml').write_text('rule-set="core"\nentrypoint="main.typ"\n')
            lint = [sys.executable, str(TOOL / 'typst-lint'), 'fix', '--dry-run', '--root', str(consumer),
                    '--package-path', str(packages), '--format', 'json']
            process = subprocess.run(lint, capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, 2, process.stderr)
            value = json.loads(process.stdout)
            self.assertEqual({d['rule_id'] for d in value['diagnostics']}, {RESET})
            self.assertFalse(value['proposed_edits'] or value['compiler_invoked'] or value['applied_fixes'])
            self.assertFalse(value['coverage']['complete'])
            self.assertEqual(fixture.read_bytes(), source)
            common = ['--root', str(consumer), '--package-path', str(packages),
                      '--package-cache-path', str(cache)]
            renders, queries = [], []
            for stage, contents in (('before', source), ('after', candidate)):
                fixture.write_bytes(contents)
                pdf, xml = consumer / f'{stage}.pdf', consumer / f'{stage}.xml'
                subprocess.run(['typst', 'compile', *common, str(fixture), str(pdf)],
                               check=True, capture_output=True, timeout=60)
                subprocess.run(['pdftotext', '-bbox', str(pdf), str(xml)],
                               check=True, capture_output=True, timeout=30)
                words = [node for node in ET.parse(xml).iter() if node.tag.endswith('}word')]
                renders.append(tuple((node.text, tuple(sorted(node.attrib.items()))) for node in words))
                text = ' '.join(node.text or '' for node in words)
                for number in ('1.1.1', '1.2.1', '2.1.1'):
                    self.assertIn('Theorem ' + number, text)
                stage_queries = {}
                for selector in ('<first>', '<second>', '<third>', 'heading'):
                    query = subprocess.run(['typst', 'query', *common, str(fixture), selector],
                                           check=True, capture_output=True, text=True, timeout=60)
                    stage_queries[selector] = json.loads(query.stdout)
                queries.append(stage_queries)
            self.assertEqual(renders[0], renders[1])
            self.assertEqual(queries[0], queries[1])
            self.assertEqual(len(queries[0]['heading']), 5)
            self.assertTrue(all(len(queries[0][selector]) == 1 for selector in ('<first>', '<second>', '<third>')))
            process = subprocess.run(lint, capture_output=True, text=True, timeout=30)
            self.assertEqual(process.returncode, 2, process.stderr)
            self.assertFalse(json.loads(process.stdout)['diagnostics'])
            self.assertEqual(fixture.read_bytes(), candidate)
            self.assertFalse(list(cache.rglob('*')))
            for relative, raw in originals.items():
                self.assertEqual((packages / relative).read_bytes(), raw)
                self.assertEqual((((TOOL / 'tests/fixtures/reviewed-packages') if relative.parts[1] == "text-utils" else REPO) / relative).read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()
