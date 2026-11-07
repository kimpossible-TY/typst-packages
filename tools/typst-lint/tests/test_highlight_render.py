"""Opt-in CLI highlight proposals compiled only in isolated test consumers.

Query evidence proves that literal prose remains highlighted and equations retain
meaning outside the highlight. This is not full geometry equivalence, editor
safety, production application or a compiler gate.
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
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
from edit_plan import Edit, plan
from typst_lint import digest


def descendants(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from descendants(child)
    elif isinstance(value, list):
        for child in value:
            yield from descendants(child)


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                     and shutil.which('typst'),
                     'Opt in with TYPST_LINT_RENDER_TESTS=1; requires typst')
class HighlightRenderTests(unittest.TestCase):
    def test_real_candidates_preserve_prose_and_extract_equations(self):
        for helper in ('highlight', 'highlighted'):
            with self.subTest(helper=helper), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                consumer, packages, cache = root / 'consumer', root / 'packages', root / 'cache'
                consumer.mkdir()
                packages.mkdir()
                cache.mkdir()
                imported = b''
                original_package = {}
                if helper == 'highlighted':
                    package = packages / 'local/text-utils/0.1.2'
                    package.mkdir(parents=True)
                    for filename in ('lib.typ', 'typst.toml'):
                        raw = (REPO / 'local/text-utils/0.1.2' / filename).read_bytes()
                        (package / filename).write_bytes(raw)
                        original_package[filename] = raw
                    imported = b'#import "@local/text-utils:0.1.2": highlighted\n'
                source = imported + b'#' + helper.encode() + b'[The term $x^i$ matters.]\n'
                input_file, output = consumer / 'main.typ', consumer / 'main.pdf'
                input_file.write_bytes(source)
                output.write_bytes(b'previous fixture output')
                config = consumer / '.typst-lint.toml'
                config.write_bytes(b'rule-set="pde"\n')
                command = [sys.executable, str(TOOL / 'typst-lint'), 'fix', '--dry-run',
                           '--root', str(consumer), '--package-path', str(packages), '--format', 'json']
                checked = subprocess.run(command, capture_output=True, text=True, timeout=30)
                self.assertEqual(checked.returncode, 2, checked.stderr)
                report = json.loads(checked.stdout)
                self.assertEqual({d['rule_id'] for d in report['diagnostics']},
                                 {'pde.text-only-highlight', 'core.parenthesized-scripts'})
                self.assertFalse(report['compiler_invoked'] or report['applied_fixes'] or report['coverage']['complete'])
                edits = []
                for item in report['proposed_edits']:
                    self.assertEqual(item['baseline_sha256'], digest(source))
                    edits.append(Edit(item['start'], item['end'], item['replacement'].encode()))
                candidate = plan(source, edits).candidate()
                expected = imported + (f'#{helper}[The term ]$x^(i)$#{helper}[ matters.]\n').encode()
                self.assertEqual(candidate, expected)
                self.assertEqual(input_file.read_bytes(), source)
                self.assertEqual(output.read_bytes(), b'previous fixture output')
                input_file.write_bytes(candidate)  # Only this test's isolated consumer.
                repeated = subprocess.run(command, capture_output=True, text=True, timeout=30)
                self.assertEqual(repeated.returncode, 2, repeated.stderr)
                final = json.loads(repeated.stdout)
                self.assertFalse(final['proposed_edits'] or final['diagnostics'])
                self.assertFalse(final['compiler_invoked'] or final['applied_fixes'])
                self.assertEqual(output.read_bytes(), b'previous fixture output')
                options = ['--root', str(consumer), '--package-path', str(packages),
                           '--package-cache-path', str(cache)]
                subprocess.run([shutil.which('typst'), 'compile', *options, str(input_file), str(output)],
                               check=True, capture_output=True, timeout=60)
                self.assertTrue(output.read_bytes().startswith(b'%PDF-'))
                def query(selector):
                    result = subprocess.run([shutil.which('typst'), 'query', *options, str(input_file), selector],
                                            check=True, capture_output=True, timeout=60)
                    return json.loads(result.stdout)
                highlights = query('highlight')
                self.assertEqual(len(highlights), 2)
                highlighted_nodes = list(descendants(highlights))
                self.assertFalse(any(n.get('func') == 'equation' for n in highlighted_nodes))
                self.assertEqual([n['text'] for n in highlighted_nodes if n.get('func') == 'text'],
                                 ['The term', 'matters.'])
                equations = query('math.equation')
                self.assertEqual(len(equations), 1)
                self.assertFalse(equations[0]['block'])
                self.assertEqual(equations[0]['body'], {
                    'func': 'attach', 'base': {'func': 'symbol', 'text': 'x'},
                    't': {'func': 'symbol', 'text': 'i'}})
                self.assertEqual(input_file.read_bytes(), candidate)
                self.assertEqual(config.read_bytes(), b'rule-set="pde"\n')
                for filename, raw in original_package.items():
                    self.assertEqual((package / filename).read_bytes(), raw)
                self.assertEqual(list(cache.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
