"""Opt-in verified footnote/marker composition rendered only in temp fixtures.

The default duplicate-break comparison is deliberately not an assertion that
parbreak calls may generally be collapsed: a show rule observes both calls.
"""
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
from edit_plan import plan
from footnote_rules import GAP
from package_loader import load_packages
from paragraph_rules import CAPITALIZATION
from prose_marker_rules import RULE_ID as MARKER
from source_analysis import analyze
from static_resolver import TEXT_UTILS, TEXT_UTILS_SOURCE_SHA256
from syntax_client import EXECUTABLE, parse
from typst_lint import digest

PREFIX = (b'#import "@local/text-utils:0.1.2": paragraph_tab, apply-paragraph-tabs\n'
          b'#show: apply-paragraph-tabs\n'
          b'#set page(width: 420pt, height: 300pt, margin: 30pt)\n'
          b'#set text(size: 12pt, lang: "en")\n')


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                     and shutil.which('typst') and shutil.which('pdftotext') and EXECUTABLE.is_file(),
                     'Opt in with TYPST_LINT_RENDER_TESTS=1; requires typst, pdftotext and syntax adapter')
class FootnoteCompositionRenderTests(unittest.TestCase):
    def setUp(self):
        version = subprocess.run(['typst', '--version'], check=True, capture_output=True, text=True).stdout
        if not version.startswith('typst 0.15.1 '):
            self.skipTest('Placement comparison requires Typst 0.15.1')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.consumer = self.root / 'consumer'
        self.packages = self.root / 'packages'
        self.cache = self.root / 'empty-cache'
        self.consumer.mkdir()
        self.cache.mkdir()
        self.destination = self.packages / 'local/text-utils/0.1.2'
        self.destination.mkdir(parents=True)
        self.originals = {}
        for filename in ('lib.typ', 'typst.toml'):
            original = (REPO / 'local/text-utils/0.1.2' / filename).read_bytes()
            self.originals[filename] = original
            (self.destination / filename).write_bytes(original)
        self.assertEqual(digest(self.originals['lib.typ']), TEXT_UTILS_SOURCE_SHA256)
        loaded = load_packages(self.packages, (TEXT_UTILS,))
        self.assertFalse(loaded.unavailable)
        self.assertEqual(len(loaded.sources), 1)
        self.sources = loaded.sources
        self.addCleanup(self.assert_dependencies_unchanged)

    def assert_dependencies_unchanged(self):
        self.assertFalse(list(self.cache.rglob('*')))
        for filename, original in self.originals.items():
            self.assertEqual((self.destination / filename).read_bytes(), original)
            self.assertEqual((REPO / 'local/text-utils/0.1.2' / filename).read_bytes(), original)

    def verified(self, source):
        # The default _verify=True performs actual parse/resolution and bounded
        # convergence; no synthetic rule results or provenance proofs are used.
        results = analyze(parse(source), 'fixture.typ', pack='pde', packages=self.sources)
        edits = [edit for result in results.values() for edit in result.edits]
        candidate = plan(source, edits)
        self.assertFalse(candidate.deferred)
        return results, candidate.candidate()

    def render(self, name, source):
        fixture = self.consumer / 'fixture.typ'
        fixture.write_bytes(source)
        pdf, xml = self.consumer / f'{name}.pdf', self.consumer / f'{name}.xml'
        subprocess.run(['typst', 'compile', '--root', str(self.consumer),
                        '--package-path', str(self.packages), '--package-cache-path', str(self.cache),
                        str(fixture), str(pdf)], check=True, capture_output=True, timeout=60)
        subprocess.run(['pdftotext', '-bbox', str(pdf), str(xml)],
                       check=True, capture_output=True, timeout=30)
        self.assertEqual(fixture.read_bytes(), source)
        self.assert_dependencies_unchanged()
        return [(node.text, node.attrib) for node in ET.parse(xml).iter() if node.tag.endswith('}word')]

    def test_marked_gap_verified_break_preserves_every_word_and_bbox(self):
        source = PREFIX + b'First idea.#footnote[Reason.]\n\n#paragraph_tab\nThis consequence follows.\n'
        expected = source.replace(b'#footnote[Reason.]\n', b'#footnote[Reason.]\n#parbreak()\n')
        results, candidate = self.verified(source)
        self.assertEqual(candidate, expected)
        self.assertTrue(results[GAP].edits)
        self.assertEqual(results[GAP].repair_passes, 1)
        step, = results[GAP].repair_steps
        self.assertEqual((step.before, step.after), (source, candidate))
        self.assertEqual({item.primary_rule_id for item in step.applied}, {GAP})
        before = self.render('marked-before', source)
        after = self.render('marked-final', candidate)
        self.assertTrue(any(word.endswith('Reason.') for word, _ in before))
        self.assertEqual(before, after)
        final, unchanged = self.verified(candidate)
        self.assertEqual(unchanged, candidate)
        self.assertFalse(any(result.edits for result in final.values()))

    def test_unmarked_gap_real_two_pass_casing_and_exact_indent(self):
        source = PREFIX + b'First idea.#footnote[Reason.]\n\nthis consequence follows.\n'
        marker_only = source.replace(b'#footnote[Reason.]\n', b'#footnote[Reason.]\n#parbreak()\n')
        marker_only = marker_only.replace(b'this consequence', b'#paragraph_tab\nthis consequence')
        expected = marker_only.replace(b'this consequence', b'This consequence')
        results, candidate = self.verified(source)
        self.assertEqual(candidate, expected)
        owner = next(result for result in results.values() if result.edits)
        self.assertEqual(owner.repair_passes, 2)
        first, second = owner.repair_steps
        self.assertEqual((first.before, first.after), (source, marker_only))
        self.assertEqual((second.before, second.after), (marker_only, candidate))
        self.assertEqual({rid for item in first.applied for rid in item.rule_ids}, {MARKER})
        self.assertEqual({rid for item in second.applied for rid in item.rule_ids}, {CAPITALIZATION})
        casing, = second.edits
        self.assertEqual((casing.end - casing.start, casing.replacement), (1, b'T'))
        for step in (first, second):
            self.assertFalse(step.moves)
            self.assertEqual(plan(step.before, step.edits).candidate(), step.after)
        before = self.render('unmarked-before', source)
        middle = self.render('unmarked-marker-only', first.after)
        after = self.render('unmarked-final', candidate)
        words = [word for word, _ in before]
        index = words.index('this')
        self.assertEqual([word for word, _ in middle], words)
        self.assertEqual([word for word, _ in after], words[:index] + ['This'] + words[index + 1:])
        # The initial prose, superscript marker, and footer remain byte-for-byte
        # equal in the extracted text and bounding boxes across all renders.
        self.assertTrue(any(word.endswith('Reason.') for word in words))
        for i, word in enumerate(words):
            if i not in range(index, index + 3):
                self.assertEqual(before[i], middle[i])
                self.assertEqual(before[i], after[i])
        self.assertAlmostEqual(float(before[index][1]['xMin']), 30.0, delta=0.01)
        for render in (middle, after):
            self.assertAlmostEqual(float(render[index][1]['xMin']), 48.0, delta=0.01)
            for i in range(index, index + 3):
                for axis in ('yMin', 'yMax'):
                    self.assertEqual(before[i][1][axis], render[i][1][axis])
        for i in range(index, index + 3):
            for axis in ('xMin', 'xMax'):
                self.assertAlmostEqual(float(middle[i][1][axis]) - float(before[i][1][axis]),
                                       1.5 * 12, delta=0.01)
        final, unchanged = self.verified(candidate)
        self.assertEqual(unchanged, candidate)
        self.assertFalse(any(result.edits for result in final.values()))

    def test_duplicate_builtin_breaks_default_equal_but_show_observes_both(self):
        single = PREFIX + b'First idea.#footnote[Reason.]\n#parbreak()\nThis consequence follows.\n'
        double = single.replace(b'#parbreak()', b'#parbreak()#parbreak()')
        self.assertEqual(self.render('single-default', single), self.render('double-default', double))
        show = b'#show parbreak: [BREAK]\n'
        visible_single = self.render('single-show', PREFIX + show + single[len(PREFIX):])
        visible_double = self.render('double-show', PREFIX + show + double[len(PREFIX):])
        single_text = ''.join(word for word, _ in visible_single)
        double_text = ''.join(word for word, _ in visible_double)
        self.assertEqual(single_text.count('BREAK'), 1)
        self.assertEqual(double_text.count('BREAK'), 2)
        self.assertIn('BREAKBREAK', double_text)
        self.assertNotEqual(visible_single, visible_double)


if __name__ == '__main__':
    unittest.main()
