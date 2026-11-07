"""Opt-in isolated Typst 0.15.1 probes of actual math binding behavior.

No cancelmark replacement or light/dark palette effect is claimed: no reviewed
helper exists. These probes compile unchanged fixtures and inspect equation
content, rather than treating same-spelled syntax as evidence of cancellation.
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
from cancel_rules import analyze
from static_resolver import resolve
from syntax_client import EXECUTABLE, parse


def descendants(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from descendants(child)
    elif isinstance(value, list):
        for child in value:
            yield from descendants(child)


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                     and shutil.which('typst') and EXECUTABLE.is_file(),
                     'Opt in with TYPST_LINT_RENDER_TESTS=1; requires Typst and pinned adapter')
class CancelRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        version = subprocess.run([shutil.which('typst'), '--version'], check=True,
                                 capture_output=True, text=True, timeout=10).stdout
        if not version.startswith('typst 0.15.1 '):
            raise unittest.SkipTest('These semantic probes require Typst 0.15.1')

    def probe(self, source, actual, diagnostic):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            consumer, packages, cache = root / 'consumer', root / 'packages', root / 'cache'
            for path in (consumer, packages, cache):
                path.mkdir()
            target = consumer / 'main.typ'
            raw = source.encode()
            target.write_bytes(raw)
            shadow = consumer / 'shadow.typ'
            shadow.write_bytes(b'#let cancel(body, ..options) = [shadow]\n')
            tree = parse(raw)
            self.assertFalse(tree.nodes[0].errors)
            result = analyze(tree, 'main.typ', resolve(tree))
            self.assertEqual(len(result.diagnostics), diagnostic)
            self.assertFalse(result.edits)
            options = ['--root', str(consumer), '--package-path', str(packages),
                       '--package-cache-path', str(cache)]
            subprocess.run([shutil.which('typst'), 'compile', *options, str(target), str(consumer / 'main.pdf')],
                           check=True, capture_output=True, timeout=60)
            query = subprocess.run([shutil.which('typst'), 'query', *options, str(target), 'math.equation'],
                                   check=True, capture_output=True, text=True, timeout=60)
            equations = json.loads(query.stdout)
            cancellations = [n for n in descendants(equations) if n.get('func') == 'cancel']
            self.assertEqual(len(cancellations), actual)
            self.assertEqual(target.read_bytes(), raw)
            self.assertEqual(shadow.read_bytes(), b'#let cancel(body, ..options) = [shadow]\n')
            self.assertFalse(list(packages.iterdir()) or list(cache.iterdir()))
            return cancellations

    def test_builtin_alias_shadow_and_math_scope_behavior(self):
        for source, actual, diagnostic in (
            ('$cancel(x)$', 1, 1),
            ('$frac(cancel(x), cancel(y))$', 2, 2),
            ('#let crossout = math.cancel\n$crossout(x)$', 1, 1),
            ('#let c = math.cancel\n$c(x)$', 0, 0),
            ('#let c = math.cancel\n$#c($x$)$', 1, 1),
            ('#let maths = math\n$maths.cancel(x)$', 1, 1),
            ('#import math as maths\n$maths.cancel(x)$', 1, 1),
            ('#let maths = math\n#import maths\n$maths.cancel(x)$', 1, 1),
            ('#import math\n$math.cancel(x)$', 1, 1),
            ('#let maths = math\n#import "shadow.typ" as math\n#import maths\n$math.cancel(x)$', 0, 0),
            ('#let maths = math\n#import "shadow.typ" as math\n#import maths\n$maths.cancel(x)$', 1, 1),
            ('#import math: cancel as crossout\n$crossout(x)$', 1, 1),
            ('#import math: *\n$cancel(x)$', 1, 1),
            ('#let math = none\n$cancel(x)$', 1, 1),
            ('#let math = math\n$math.cancel(x)$', 1, 1),
            ('#let cancel(body) = [shadow]\n$cancel(x)$', 0, 0),
            ('#import "shadow.typ": *\n$cancel(x)$', 0, 0),
            ('#let crossout = math.cancel\n#{ crossout = body => [shadow] }\n$crossout(x)$', 0, 0),
            ('#let crossout = math.cancel\n#let saved = crossout\n'
             '#{ crossout = body => [shadow] }\n$saved(x)$', 1, 1),
            ('#[$cancel(x)$]', 1, 1),
            # Real cancellation can exist where this partial backend refuses.
            ('#let helper() = $cancel(x)$\n#helper()', 1, 0),
            ('#if true [$cancel(x)$]', 1, 0),
        ):
            with self.subTest(source=source):
                self.probe(source, actual, diagnostic)

    def test_explicit_builtin_options_remain_exact(self):
        cancellations = self.probe('$cancel(x, stroke: #blue, cross: #true)$', 1, 1)
        self.assertEqual(cancellations[0]['body'], {'func': 'symbol', 'text': 'x'})
        self.assertIs(cancellations[0]['cross'], True)
        self.assertEqual(cancellations[0]['stroke'], 'rgb("#0074d9")')

    def test_global_builtin_names_are_not_math_fallbacks(self):
        for source in ('#cancel[$x$]', '$#cancel($x$)$', '$math.cancel(x)$',
                       '#let maths = math\n#import maths\n$math.cancel(x)$'):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                target = root / 'main.typ'
                target.write_text(source)
                for name in ('packages', 'cache'):
                    (root / name).mkdir()
                result = subprocess.run([shutil.which('typst'), 'compile', '--root', str(root),
                                         '--package-path', str(root / 'packages'), '--package-cache-path', str(root / 'cache'),
                                         str(target), str(root / 'main.pdf')],
                                        capture_output=True, text=True, timeout=60)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('unknown variable:', result.stderr)
                tree = parse(source.encode())
                self.assertFalse(analyze(tree, 'main.typ', resolve(tree)).diagnostics)
                self.assertEqual(target.read_text(), source)


if __name__ == '__main__':
    unittest.main()
