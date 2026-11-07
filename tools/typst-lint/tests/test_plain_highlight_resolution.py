"""Only proven text-only root highlight output preserves subsequent facts."""
from pathlib import Path
import sys
import unittest

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from package_loader import load_packages
from static_resolver import TEXT_UTILS, resolve
from syntax_client import EXECUTABLE, parse


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class PlainHighlightResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packages = load_packages(TOOL.parents[1], (TEXT_UTILS,)).sources

    def after(self, source, packages=None):
        tree = parse(source + b'\nFollowing prose.')
        self.assertFalse(tree.nodes[0].errors, source)
        resolution = resolve(tree, packages=self.packages if packages is None else packages)
        return resolution.facts[-1], resolution

    def test_exact_builtin_reviewed_alias_and_module_literal_text(self):
        for source in (
            b'#highlight[Plain text]', b'#highlight[]', b'#highlight[ ]',
            '#highlight[한글\r\ntext]'.encode(),
            b'#let hi = highlight\n#hi[Plain text]',
            b'#import "@local/text-utils:0.1.2": highlighted as hi\n#hi[Plain text]',
            b'#import "@local/text-utils:0.1.2" as u\n#u.highlighted[Plain text]',
        ):
            with self.subTest(source=source):
                fact, resolution = self.after(source)
                self.assertTrue(fact.prose_safe)
                self.assertFalse(any('Output effect/body ownership' in s for s in resolution.skips))

    def test_math_and_every_unsupported_body_shape_remain_unknown(self):
        for source in (
            b'#highlight[The $x$ term]', b'#highlight[The $x^(i)$ term]',
            b'#highlight[The $frac(x,y)$ term]', b'#highlight[Text\n\nmore]',
            b'#highlight[Text #text[more]]', b'#highlight[Text *strong*]',
            b'#highlight[Text _emph_]', b'#highlight[Text <label>]',
            b'#highlight[Text // comment\nmore]', b'#highlight[Text /* comment */ more]',
            b'#highlight(fill:red)[Text]', b'#highlight([Text])',
            b'#highlight[Text][more]', b'#highlight(..args)[Text]',
            b'#highlight[Text #let x = 1]', b'#highlight[Text #set text(lang:"de")]',
            b'#highlighted[Text]', b'#let highlight(x) = x\n#highlight[Text]',
            b'#import "unknown.typ": *\n#highlight[Text]',
            b'#block[#highlight[Text]]',
        ):
            with self.subTest(source=source):
                fact, resolution = self.after(source)
                self.assertFalse(fact.prose_safe)
                self.assertTrue(any('Output effect/body ownership' in s for s in resolution.skips))

    def test_no_reset_of_unknown_styles_and_no_nested_body_certification(self):
        for prefix in (b'#show: unknown\n', b'#unknown[]\n', b'#set unknown()\n'):
            self.assertFalse(self.after(prefix + b'#highlight[Text]')[0].prose_safe)
        source = b'#highlight[Text]'
        tree = parse(source)
        resolution = resolve(tree)
        body = next(i for i, node in enumerate(tree.nodes) if node.kind == 'Text')
        self.assertFalse(resolution.facts[body].prose_safe)


if __name__ == '__main__':
    unittest.main()
