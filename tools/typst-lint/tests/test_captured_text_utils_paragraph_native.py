"""Opt-in, test-owned BEFORE/AFTER paragraph rendering characterization.

This is independent native evidence, not production package-compilation authority.
The genuine internal bridge authorizes only the temporary root source repair.
No closed/graph compiler API, compiler receipt, font capture, public route or
consumer project is used. Only Typst's embedded fonts are admitted. All source,
package, PDF and raster copies remain in a private local temporary directory.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paragraph_fix_test_support import context, evidence, fixture
from syntax_client import EXECUTABLE
from transactions import _xattrs

PINNED = Path('/opt/homebrew/Cellar/typst/0.15.1/bin/typst')
PINNED_SHA256 = '08d01e5a434e821c4a1ce30dd1593e1bbc57c9b1203c4d760d8912ef4ce6a621'
MANIFEST_SHA256 = 'e1d27052412a39d4f839dede28406159c1c9a5394592a73c2ed5666dcbc60df2'
PACKAGE_SHA256 = '7e151150dac47b01ebf7706457906133d74a52abee1b5c43ce824ff89279bd71'
CATALOG_SHA256 = '955b0c721027369ec1fd100a7da00fc7304693bb40e2916904c6f3b06fae1826'
PDF_HELPERS = ('pdfinfo', 'pdftotext', 'pdffonts', 'pdftoppm', 'pdftohtml')
PREFIX = (b'#import "@local/text-utils:0.1.2": paragraph_tab, apply-paragraph-tabs\n'
          b'#show: apply-paragraph-tabs\n')
# Each row has unique words for associating PDF words with the source paragraph.
# Whitespace around the marker and before the first word is deliberately varied.
ROWS = (
    (b'', b'', b'', b'this', b'amber'),
    (b' \t', b' \t', b' \t', b'these', b'bronze'),
    (b'\t', b'', b'\t', b'we', b'cobalt'),
    (b'', b'\t ', b'    ', b'observe', b'denim'),
)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def file_state(path):
    """Read-only hash, identity and preserved metadata, excluding access time."""
    value = path.lstat()
    with path.open('rb') as handle:
        sha256 = hashlib.file_digest(handle, 'sha256').hexdigest()
        attrs = _xattrs(handle.fileno())
    return (sha256, value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
            value.st_size, value.st_uid, value.st_gid, value.st_mtime_ns,
            value.st_ctime_ns, getattr(value, 'st_flags', 0), attrs)


def identity_report(value):
    return dict(sha256=value[0], device=value[1], inode=value[2], mode=value[3],
                size=value[5], uid=value[6], gid=value[7], mtime_ns=value[8],
                ctime_ns=value[9], flags=value[10])


def admitted_source(newline):
    source = PREFIX + b'Baseline anchor remains.\n'
    for before, after, leading, word, label in ROWS:
        source += (b'\n' + before + b'#paragraph_tab' + after + b'\n' + leading
                   + word + b' ' + label + b' preserves iPhone eBay NASA lowerCase.\n')
    return source.replace(b'\n', newline)


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1',
    'Opt in to private paragraph characterization with TYPST_LINT_RENDER_TESTS=1')
class NativeCapturedTextUtilsParagraphTests(unittest.TestCase):
    def setUp(self):
        if sys.platform != 'darwin' or platform.machine() != 'arm64':
            self.skipTest('Reviewed native paragraph characterization requires Darwin arm64')
        if not EXECUTABLE.is_file():
            self.skipTest('Missing built pinned syntax adapter: ' + str(EXECUTABLE))
        if not PINNED.is_file():
            self.skipTest('Missing reviewed Typst 0.15.1 executable: ' + str(PINNED))
        self.helpers = {name: shutil.which(name) for name in PDF_HELPERS}
        missing = [name for name, path in self.helpers.items() if path is None]
        if missing:
            self.skipTest('Missing installed local PDF helpers: ' + ', '.join(missing))
        self.resources = {}
        for path, expected, size in (
                (PINNED, PINNED_SHA256, 44694736),
                (REPO / 'local/text-utils/0.1.2/typst.toml', MANIFEST_SHA256, 173),
                (REPO / 'local/text-utils/0.1.2/lib.typ', PACKAGE_SHA256, 5583),
                (TOOL / 'rules/catalog.json', CATALOG_SHA256, None),
                (EXECUTABLE, None, None)):
            actual = file_state(path)
            if expected is not None:
                self.assertEqual(actual[0], expected, 'Reviewed resource hash mismatch: ' + str(path))
            if size is not None:
                self.assertEqual(actual[5], size, 'Reviewed resource size mismatch: ' + str(path))
            self.resources[path] = actual
        temporary = tempfile.TemporaryDirectory(prefix='typst-paragraph-native-')
        self.base = Path(temporary.name).resolve()
        self.base.chmod(0o700)

        def cleanup():
            temporary.cleanup()
            self.assertFalse(self.base.exists(), 'Private paragraph artifacts were not cleaned')
        self.addCleanup(cleanup)
        self.addCleanup(self.assert_resources_current)

    def assert_resources_current(self):
        for path, original in self.resources.items():
            self.assertEqual(file_state(path), original,
                             'Installed/repository resource changed: ' + str(path))

    def helper(self, environment, name, *arguments):
        result = subprocess.run([self.helpers[name], *map(str, arguments)],
            cwd=self.base, env=environment, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, (name, result.stdout, result.stderr))
        return result.stdout

    def inspect_pdf(self, pdf, environment):
        raw = pdf.read_bytes()
        self.assertTrue(raw.startswith(b'%PDF-') and raw.rstrip().endswith(b'%%EOF'))
        self.assertTrue(1000 < len(raw) <= 16 * 1024 * 1024)
        info = self.helper(environment, 'pdfinfo', pdf).decode('utf-8')
        pages = next(line.split()[1] for line in info.splitlines() if line.startswith('Pages:'))
        self.assertEqual(pages, '1')
        text = self.helper(environment, 'pdftotext', '-layout', pdf, '-').decode('utf-8')
        self.assertNotIn('\ufffd', text)
        xml = ET.fromstring(self.helper(environment, 'pdftotext', '-bbox', pdf, '-'))
        boxes = tuple((node.text, tuple(float(node.attrib[key]) for key in
                      ('xMin', 'yMin', 'xMax', 'yMax')))
                      for node in xml.iter() if node.tag.endswith('}word'))
        self.assertTrue(boxes)
        self.assertTrue(all(x2 > x1 and y2 > y1 for _, (x1, y1, x2, y2) in boxes))
        fonts = self.helper(environment, 'pdffonts', pdf).decode('utf-8')
        font_rows = [line.split() for line in fonts.splitlines()[2:] if line.strip()]
        self.assertTrue(font_rows)
        self.assertTrue(all(row[-5] == 'yes' for row in font_rows), fonts)
        # Observe the default point size from the actual PDF, not a source set.
        html = ET.fromstring(self.helper(environment, 'pdftohtml', '-xml', '-hidden',
                            '-zoom', '1', '-stdout', pdf))
        specs = tuple(dict(node.attrib) for node in html.iter('fontspec'))
        self.assertTrue(specs)
        raster = pdf.with_suffix('')
        self.helper(environment, 'pdftoppm', '-singlefile', '-r', '80', pdf, raster)
        pixels = raster.with_suffix('.ppm').read_bytes()
        magic, dimensions, maximum, payload = pixels.split(b'\n', 3)
        self.assertEqual((magic, maximum), (b'P6', b'255'))
        width, height = map(int, dimensions.split())
        self.assertEqual(len(payload), width * height * 3)
        self.assertLess(min(payload), 200, 'Raster has no dark glyph pixels')
        self.assertEqual(max(payload), 255)
        return dict(text=text, boxes=boxes, fonts=fonts, fontspecs=specs,
                    pdf_sha256=digest(raw), raster_sha256=digest(pixels),
                    raster_dimensions=(width, height))

    def compile_private_copy(self, name, source, manifest, package):
        """Test harness only: no protected compiler receipt or capability exists."""
        self.assert_resources_current()
        base = self.base / name
        root, packages = base / 'project', base / 'packages'
        leaf = packages / 'local/text-utils/0.1.2'
        leaf.mkdir(parents=True, mode=0o700)
        root.mkdir(mode=0o700)
        staged = {root / 'main.typ': source, leaf / 'typst.toml': manifest,
                  leaf / 'lib.typ': package}
        for path, raw in staged.items():
            path.write_bytes(raw)
            path.chmod(0o400)
        for name in ('home', 'cache', 'data', 'tmp', 'package-cache'):
            (base / name).mkdir(mode=0o700)
        environment = dict(PATH='/usr/bin:/bin', HOME=str(base / 'home'),
            XDG_CACHE_HOME=str(base / 'cache'), XDG_DATA_HOME=str(base / 'data'),
            TMPDIR=str(base / 'tmp'), LANG='C', LC_ALL='C', TZ='UTC',
            SOURCE_DATE_EPOCH='0')
        pdf, deps = base / 'result.pdf', base / 'dependencies.zero'
        before = {path: file_state(path) for path in staged}
        command = [str(PINNED), 'compile', '--root', str(root),
            '--package-path', str(packages), '--package-cache-path', str(base / 'package-cache'),
            '--ignore-system-fonts', '--deps', str(deps), '--deps-format', 'zero',
            str(root / 'main.typ'), str(pdf)]
        result = subprocess.run(command, cwd=root, env=environment,
                                capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
        self.assertEqual(result.stderr, b'')
        self.assertEqual({path: file_state(path) for path in staged}, before)
        self.assertEqual({path for path in packages.rglob('*') if path.is_file()},
                         {leaf / 'typst.toml', leaf / 'lib.typ'})
        self.assertEqual(list((base / 'package-cache').rglob('*')), [])
        depraw = deps.read_bytes()
        self.assertTrue(depraw.endswith(b'\0'))
        observed = tuple(depraw[:-1].decode('utf-8').split('\0'))
        paths = tuple((root / path).resolve() for path in observed)
        self.assertEqual(len(paths), len(set(paths)))
        self.assertLessEqual(set(paths), set(staged), observed)
        self.assertLessEqual({root / 'main.typ', leaf / 'lib.typ'}, set(paths), observed)
        report = self.inspect_pdf(pdf, environment)
        report.update(dependency_paths=observed, dependency_sha256=digest(depraw),
            staged_sources={str(path.relative_to(base)): state[0] for path, state in before.items()},
            compiler_sha256=self.resources[PINNED][0])
        self.assertEqual({path: file_state(path) for path in staged}, before)
        self.assert_resources_current()
        return report

    def paragraph_boxes(self, report):
        boxes = report['boxes']
        self.assertEqual(len(boxes), 3 + 7 * len(ROWS), report['text'])
        return (boxes[:3], *(boxes[3 + i * 7:3 + (i + 1) * 7] for i in range(len(ROWS))))

    def characterize(self, newline):
        source = admitted_source(newline)
        expected = source
        positions = []
        for _, _, _, word, label in ROWS:
            start = source.index(word + b' ' + label)
            positions.append(start)
            expected = expected[:start] + word[:1].upper() + expected[start + 1:]
        f = fixture(self.base, 1, source=source)
        original = file_state(f.source)
        protected = {path: file_state(path) for path in evidence(f)}
        with context(f) as fix:
            candidate = fix.validate()
            self.assertEqual((candidate.passes, candidate.findings), (1, frozenset()))
            self.assertEqual(candidate.sources, {f.target: expected})
            self.assertEqual(file_state(f.source), original)
            # Genuine retained input objects, never fabricated proof/capture data.
            capture = fix._capture
            self.assertEqual(capture.tree.source, source)
            self.assertEqual((digest(capture.manifest), len(capture.manifest)),
                             (MANIFEST_SHA256, 173))
            self.assertEqual((digest(capture.package.source), len(capture.package.source)),
                             (PACKAGE_SHA256, 5583))
            retained_source, manifest, package = (capture.tree.source, capture.manifest,
                                                  capture.package.source)
            result = fix.apply(candidate)
            summary = fix.summary
            self.assertEqual(result.modified, (f.target,))
            self.assertEqual(summary.modified_paths, (f.target,))
            self.assertEqual((summary.original_sha256, summary.final_sha256),
                             (digest(source), digest(expected)))
            self.assertEqual([(edit.start, edit.end, edit.replacement, edit.rule_id)
                              for edit in summary.applied_edits],
                [(position, position + 1, source[position:position + 1].upper(),
                  'pde.paragraph-capitalization') for position in positions])
            self.assertEqual(f.source.read_bytes(), expected)
            self.assertEqual([i for i, (a, b) in enumerate(zip(source, expected)) if a != b],
                             positions)
            self.assertEqual(len(source), len(expected))
        # Applying the protected repair, not a manual canonical replacement,
        # produced the AFTER bytes. Compilation remains test-owned and private.
        repaired = f.source.read_bytes()
        after_apply = file_state(f.source)
        self.assertEqual(after_apply[3:8], original[3:8])
        self.assertEqual(after_apply[10:], original[10:])
        self.assertEqual({path: file_state(path) for path in protected}, protected)
        before = self.compile_private_copy('before', retained_source, manifest, package)
        after = self.compile_private_copy('after', repaired, manifest, package)
        self.assertEqual(file_state(f.source), after_apply)
        self.assertEqual({path: file_state(path) for path in protected}, protected)
        before_pars, after_pars = self.paragraph_boxes(before), self.paragraph_boxes(after)
        observation = dict(newline=repr(newline), source_before_sha256=digest(source),
            source_after_sha256=digest(repaired), changed_bytes=positions,
            source_identity_before=identity_report(original),
            source_identity_after=identity_report(after_apply),
            preserved_evidence={str(path.relative_to(f.base)): identity_report(value)
                                for path, value in protected.items()},
            unchanged_resources={str(path): identity_report(value)
                                 for path, value in self.resources.items()},
            before=before, after=after,
            indents_before=[p[0][1][0] - before_pars[0][0][1][0] for p in before_pars[1:]],
            indents_after=[p[0][1][0] - after_pars[0][0][1][0] for p in after_pars[1:]],
            first_word_changes=[(a[0][0], b[0][0]) for a, b in zip(before_pars[1:], after_pars[1:])])
        # Emit actual evidence before semantic assertions, including on failure.
        # Changed PDF/raster hashes are observations, never equivalence claims.
        print('PARAGRAPH_NATIVE_OBSERVATION ' + json.dumps(observation, sort_keys=True), flush=True)
        self.assertEqual(before_pars[0], after_pars[0], 'Unmarked baseline layout changed')
        self.assertEqual(before_pars[0][0][0], 'Baseline')
        for report in (before, after):
            font_names = {line.split()[0].split('+', 1)[-1]
                          for line in report['fonts'].splitlines()[2:] if line.strip()}
            self.assertEqual(font_names, {'LibertinusSerif-Regular-Identity-H'},
                             'Actual default embedded regular serif font changed')
            self.assertEqual({spec['size'] for spec in report['fontspecs']}, {'11'},
                             'Actual default PDF font size must establish 11pt/16.5pt intent')
        for index, (old, new, row) in enumerate(zip(before_pars[1:], after_pars[1:], ROWS)):
            _, _, _, word, label = row
            with self.subTest(paragraph=index, newline=newline):
                self.assertIn(old[0][0], (word.decode(), word.capitalize().decode()))
                self.assertEqual(new[0][0], word.capitalize().decode())
                wanted_tail = [label.decode(), 'preserves', 'iPhone', 'eBay', 'NASA', 'lowerCase.']
                self.assertEqual([item[0] for item in old[1:]], wanted_tail)
                self.assertEqual([item[0] for item in new[1:]], wanted_tail)
                self.assertAlmostEqual(observation['indents_after'][index], 1.5 * 11,
                    delta=0.05, msg='Repaired paragraph differs from intended 1.5em indentation')
                self.assertAlmostEqual(old[0][1][0], new[0][1][0], delta=0.01,
                    msg='Capitalization changed leading spacing/indentation')
                # A changed initial glyph can change the first word's advance.
                # Every following word must move by exactly that measured amount,
                # with no line movement, extra spacing or changed protected name.
                width_delta = new[0][1][2] - old[0][1][2]
                for old_word, new_word in zip(old, new):
                    self.assertEqual(old_word[1][1::2], new_word[1][1::2],
                                     'Unexpected vertical layout change')
                for old_word, new_word in zip(old[1:], new[1:]):
                    for coordinate in (0, 2):
                        self.assertAlmostEqual(new_word[1][coordinate] - old_word[1][coordinate],
                            width_delta, delta=0.01, msg='Unexpected middle-word spacing change')

    def test_lf_original_and_applied_repair(self):
        self.characterize(b'\n')

    def test_crlf_original_and_applied_repair(self):
        self.characterize(b'\r\n')


if __name__ == '__main__':
    unittest.main()
