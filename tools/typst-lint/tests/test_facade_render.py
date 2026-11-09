"""Opt-in real CLI plans and rendering for bounded named facade re-exports.

These small fixtures compare the reviewed direct helper with one/two local
facade hops. They do not establish general document or render equivalence.
Only fresh temporary candidates are edited, and only this test invokes Typst.
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
from footnote_rules import GAP
from paragraph_rules import CAPITALIZATION
from prose_marker_rules import RULE_ID as MARKER
from static_resolver import TEXT_UTILS, TEXT_UTILS_SOURCE_SHA256
from syntax_client import EXECUTABLE
from typst_lint import digest

DIRECT = b'#import "@local/text-utils:0.1.2": paragraph_tab, apply-paragraph-tabs\n'
IMPORT = b'#import "styles.typ": saved-marker as paragraph_tab, tabs as apply-paragraph-tabs\n'
STYLES = (b'#import "@local/text-utils:0.1.2": paragraph-tab as marker, '
          b'apply-paragraph-tabs as tabs\n#let saved-marker = marker\n#let count = 1\n')
REEXPORT = (b'/* Pure named re-export and bounded literals. */\n'
            b'#import "leaf.typ": saved-marker as prior, tabs\n'
            b'#let saved-marker = prior\n#let count = 1\n#let saved-count = count\n'
            b'#let yes = true\n#let absent = none\n#let automatic = auto\n'
            b'#let title = "plain"\n')
SETUP = (b'#show: apply-paragraph-tabs\n'
         b'#set page(width: 480pt, height: 320pt, margin: 30pt)\n'
         b'#set text(size: 12pt, lang: "en")\n')
CONFIG = b'rule-set="pde"\ninclude=["main.typ"]\n'
SENTINEL = b'previous output must not be compiled over'
FOLLOWING = (b'this consequence follows with iPhone and eBay.\n\n'
             b'iPhone remains unchanged.\n\nMath $x^(i)$ stays.\n')


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                     and shutil.which('typst') and shutil.which('pdftotext') and EXECUTABLE.is_file(),
                     'Opt in with TYPST_LINT_RENDER_TESTS=1; requires typst, pdftotext and syntax adapter')
class FacadeRenderTests(unittest.TestCase):
    def setUp(self):
        self.typst = shutil.which('typst')
        version = subprocess.run([self.typst, '--version'], check=True,
                                 capture_output=True, text=True, timeout=30).stdout
        if not version.startswith('typst 0.15.1 '):
            self.skipTest('Placement comparison requires Typst 0.15.1; found ' + version.strip())
        temporary = tempfile.TemporaryDirectory(prefix='typst-facade-render-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.packages, self.cache = self.base / 'packages', self.base / 'empty-cache'
        self.cache.mkdir()
        self.package_bytes = {}
        for filename in ('lib.typ', 'typst.toml'):
            relative = Path('local/text-utils/0.1.2') / filename
            original = ((TOOL / 'tests/fixtures/reviewed-packages') / relative).read_bytes()
            target = self.packages / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original)
            self.package_bytes[relative] = original
        self.assertEqual(digest(self.package_bytes[Path('local/text-utils/0.1.2/lib.typ')]),
                         TEXT_UTILS_SOURCE_SHA256)
        # An independent trap catches any attempted production CLI compiler
        # dispatch. Actual render calls below use the installed absolute path.
        self.bin = self.base / 'cli-bin'
        self.bin.mkdir()
        self.compiler_attempt = self.base / 'unexpected-compiler-invocation'
        trap = self.bin / 'typst'
        trap.write_text('#!/bin/sh\nprintf invoked > "$TYPST_FACADE_COMPILER_ATTEMPT"\nexit 99\n')
        trap.chmod(0o700)
        self.addCleanup(self.assert_dependencies_unchanged)

    def assert_dependencies_unchanged(self):
        self.assertFalse(list(self.cache.rglob('*')))
        self.assertFalse(self.compiler_attempt.exists())
        self.assertEqual({p.relative_to(self.packages) for p in self.packages.rglob('*') if p.is_file()},
                         set(self.package_bytes))
        for relative, original in self.package_bytes.items():
            self.assertEqual((self.packages / relative).read_bytes(), original)
            self.assertEqual(((TOOL / 'tests/fixtures/reviewed-packages') / relative).read_bytes(), original)

    def consumer(self, name, source, dependencies):
        root = self.base / name
        root.mkdir()
        for filename, raw in {'main.typ': source, '.typst-lint.toml': CONFIG,
                              'previous.pdf': SENTINEL, **dependencies}.items():
            (root / filename).write_bytes(raw)
        return root

    @staticmethod
    def snapshot(root):
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}

    def cli(self, root):
        before = self.snapshot(root)
        environment = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ.get('PATH', ''),
                           TYPST_FACADE_COMPILER_ATTEMPT=str(self.compiler_attempt))
        process = subprocess.run([sys.executable, str(TOOL / 'typst-lint'), 'fix', '--dry-run',
                                  '--root', str(root), '--package-path', str(self.packages),
                                  '--format', 'json'], env=environment,
                                 capture_output=True, text=True, timeout=60)
        self.assertEqual(process.returncode, 2, process.stderr)
        value = json.loads(process.stdout)
        self.assertEqual((value['status'], value['phase']), (2, 'analysis'))
        self.assertFalse(value['compiler_invoked'] or value['applied_fixes'] or value['coverage']['complete'])
        self.assertEqual(value['proposal_status'], 'partial-not-applicable-transaction')
        self.assertEqual([item['path'] for item in value['inputs']], ['main.typ'])
        evidence, = value['package_evidence']
        self.assertEqual((evidence['import_path'], evidence['source_sha256']),
                         (TEXT_UTILS, TEXT_UTILS_SOURCE_SHA256))
        self.assertFalse(value['parse_failures'] or value['configuration_failures'])
        # The package loader still reports local import strings as unreviewed
        # packages. Bounded facade proof is separate, not full import coverage.
        self.assertEqual({item['import_path'] for item in value['package_unavailable']},
                         {name for name in before if name.endswith('.typ') and name != 'main.typ'})
        self.assertTrue(all(item['code'] == 'unreviewed-package' for item in value['package_unavailable']))
        self.assertEqual(self.snapshot(root), before)
        self.assertEqual((root / 'previous.pdf').read_bytes(), SENTINEL)
        self.assert_dependencies_unchanged()
        return value

    def candidate_history(self, source, value, footnote):
        self.assertTrue(value['proposed_edits'])
        for edit in value['proposed_edits']:
            self.assertEqual((edit['path'], edit['baseline_sha256'], edit['repair_passes']),
                             ('main.typ', digest(source), 2))
        owners = {rid for edit in value['proposed_edits'] for rid in edit.get('rule_ids', [edit['rule_id']])}
        self.assertEqual(owners, {MARKER, CAPITALIZATION})
        edits = [Edit(edit['start'], edit['end'], edit['replacement'].encode('utf-8'))
                 for edit in value['proposed_edits']]
        proposed = plan(source, edits)
        self.assertFalse(proposed.deferred)
        candidate = proposed.candidate()
        middle = source.replace(b'this consequence', b'#paragraph_tab\nthis consequence', 1)
        if footnote:
            middle = middle.replace(b']\n\n', b']\n#parbreak()\n\n', 1)
            self.assertIn(GAP, {item['rule_id'] for item in value['diagnostics']})
        self.assertEqual(candidate, middle.replace(b'this consequence', b'This consequence', 1))
        record, = value['convergence_evidence']
        self.assertEqual((record['path'], record['passes'], record['baseline_sha256'], record['candidate_sha256']),
                         ('main.typ', 2, digest(source), digest(candidate)))
        self.assertEqual(len(record['steps']), 2)
        stage = source
        for step, owner, expected in zip(record['steps'], (MARKER, CAPITALIZATION), (middle, candidate)):
            self.assertEqual(step['before_sha256'], digest(stage))
            self.assertFalse(step['moves'])
            self.assertEqual({item['rule_id'] for item in step['applied']}, {owner})
            self.assertTrue(all(item['rule_ids'] == [owner] for item in step['applied']))
            applied = [Edit(item['start'], item['end'], item['replacement'].encode('utf-8'))
                       for item in step['applied']]
            if owner == MARKER:
                self.assertEqual(len(applied), 2 if footnote else 1)
                self.assertTrue(all(edit.start == edit.end for edit in applied))
                self.assertEqual({edit.replacement for edit in applied},
                                 {b'#paragraph_tab\n', b'#parbreak()\n'} if footnote else {b'#paragraph_tab\n'})
            else:
                casing, = applied
                self.assertEqual((casing.end - casing.start, casing.replacement), (1, b'T'))
            stage = plan(stage, applied).candidate()
            self.assertEqual(stage, expected)
            self.assertEqual(step['after_sha256'], digest(stage))
        return middle, candidate

    def render(self, name, root):
        before = self.snapshot(root)
        pdf, xml = self.base / f'{name}.pdf', self.base / f'{name}.xml'
        subprocess.run([self.typst, 'compile', '--root', str(root),
                        '--package-path', str(self.packages), '--package-cache-path', str(self.cache),
                        str(root / 'main.typ'), str(pdf)], check=True, capture_output=True, timeout=60)
        subprocess.run(['pdftotext', '-bbox', str(pdf), str(xml)],
                       check=True, capture_output=True, timeout=30)
        words = [(node.text, node.attrib) for node in ET.parse(xml).iter() if node.tag.endswith('}word')]
        self.assertTrue(words)
        self.assertEqual(self.snapshot(root), before)
        self.assert_dependencies_unchanged()
        return words

    def assert_intended_geometry(self, renders, footnote):
        before, middle, after = renders
        words = [word for word, _ in before]
        index = words.index('this')
        length = 7  # this consequence follows with iPhone and eBay.
        self.assertEqual(words[index:index + length],
                         ['this', 'consequence', 'follows', 'with', 'iPhone', 'and', 'eBay.'])
        self.assertEqual([word for word, _ in middle], words)
        self.assertEqual([word for word, _ in after], words[:index] + ['This'] + words[index + 1:])
        self.assertIn('iPhone', words[index + length:])
        self.assertIn('Math', words[index + length:])
        if footnote:
            self.assertTrue(any(word.endswith('Reason.') for word in words))
        for i in range(len(words)):
            if not index <= i < index + length:
                # Includes the independent protected-name paragraph, all math,
                # initial prose, and the footnote marker/footer when present.
                self.assertEqual(before[i], middle[i])
                self.assertEqual(before[i], after[i])
        self.assertAlmostEqual(float(before[index][1]['xMin']), 30.0, delta=0.01)
        for rendered in (middle, after):
            self.assertAlmostEqual(float(rendered[index][1]['xMin']), 48.0, delta=0.01)
            for i in range(index, index + length):
                for axis in ('yMin', 'yMax'):
                    self.assertEqual(before[i][1][axis], rendered[i][1][axis])
        for i in range(index, index + length):
            for axis in ('xMin', 'xMax'):
                self.assertAlmostEqual(float(middle[i][1][axis]) - float(before[i][1][axis]),
                                       1.5 * 12, delta=0.01)
        # Casing can change the first glyph's width. All following words retain
        # their dimensions and move by exactly that one width difference.
        delta = float(after[index][1]['xMax']) - float(middle[index][1]['xMax'])
        for i in range(index + 1, index + length):
            for axis in ('xMin', 'xMax'):
                self.assertAlmostEqual(float(after[i][1][axis]) - float(middle[i][1][axis]), delta, delta=0.01)

    def check_chain(self, *, footnote):
        body = (b'Baseline.#footnote[Reason.]\n\n' if footnote else b'Baseline.\n\n') + FOLLOWING
        direct_renders = None
        variants = (('direct', DIRECT, {}), ('one-hop', IMPORT, {'styles.typ': STYLES}),
                    ('two-hop', IMPORT, {'styles.typ': REEXPORT, 'leaf.typ': STYLES}))
        for name, incoming, dependencies in variants:
            with self.subTest(import_kind=name, footnote=footnote):
                source = incoming + SETUP + body
                original = self.consumer(name + '-original', source, dependencies)
                value = self.cli(original)
                middle, candidate = self.candidate_history(source, value, footnote)
                # Never apply proposals to the production CLI's original input.
                marked = self.consumer(name + '-marker-candidate', middle, dependencies)
                final = self.consumer(name + '-final-candidate', candidate, dependencies)
                renders = [self.render(name + '-' + stage, root) for stage, root in
                           (('before', original), ('marker', marked), ('final', final))]
                self.assert_intended_geometry(renders, footnote)
                if direct_renders is None:
                    direct_renders = renders
                else:
                    # Exact extracted text and every word's PDF bbox agree
                    # with the direct-package helper at all three stages.
                    self.assertEqual(renders, direct_renders)
                final_value = self.cli(final)
                self.assertFalse(final_value['proposed_edits'])
                self.assertEqual((original / 'main.typ').read_bytes(), source)
                self.assertEqual((final / 'main.typ').read_bytes(), candidate)

    def test_one_and_two_hop_marker_then_casing_match_direct_helper_render(self):
        self.check_chain(footnote=False)

    def test_one_and_two_hop_footnote_break_marker_then_casing_match_direct_render(self):
        self.check_chain(footnote=True)


if __name__ == '__main__':
    unittest.main()
