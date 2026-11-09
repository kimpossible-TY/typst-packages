"""Opt-in native acceptance for the INTERNAL committed relative graph compiler.

Consumers, journals, PDFs and rasters are private local temporary files. The
adapter alone captures the pinned compiler and installed AppleGothic Regular
face. No font bytes are fixtures, retained test outputs, or redistribution.
The repaired/already-parenthesized comparison checks deterministic identical
postimages, not visual equivalence with the original ungrouped source.
"""
from contextlib import contextmanager
import copy
from dataclasses import FrozenInstanceError
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
from types import SimpleNamespace
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import captured_relative_compile as compiler
import captured_relative_core as core
import captured_relative_fix as bridge
import captured_relative_inputs as inputs
import closed_core_compile as runtime
from resource_profiles import ASCII_RESOURCE_PROFILE as ASCII, APPLEGOTHIC_RESOURCE_PROFILE as KOREAN
from syntax_client import EXECUTABLE
from transactions import OfflineExclusive
from typst_lint import CATALOG

PINNED = Path('/opt/homebrew/Cellar/typst/0.15.1/bin/typst')
PINNED_SHA256 = '08d01e5a434e821c4a1ce30dd1593e1bbc57c9b1203c4d760d8912ef4ce6a621'
FONT = Path('/System/Library/Fonts/Supplemental/AppleGothic.ttf')
FONT_SHA256 = '69071005e523e3a2bf276b35b0066f68a7ec0d942c860941ad188ef15ef69c51'
PDF_HELPERS = ('pdfinfo', 'pdftotext', 'pdffonts', 'pdftoppm', 'pdftohtml')

ASCII_ROOT = (
    b'\xef\xbb\xbf#import "../mods/left.typ": renamed as count, txt\r\n'
    b'#import "../mods/right.typ": another, same, sin\r\n'
    b'#import "../mods/unused.typ": unused\r\n'
    b'= Heading #count $a_(b_count)^sin$\r\n'
    b'- *Strong #txt $x_another/y^same + w^txt$*\r\n'
    b'+ _Emphasis #count $z_(count^2) + t_sin$_\r\n')
ASCII_EXPECTED = ASCII_ROOT.replace(b'b_count', b'b_(count)').replace(
    b')^sin', b')^(sin)').replace(b'x_another', b'x_(another)').replace(
    b'y^same', b'y^(same)').replace(b'w^txt', b'w^(txt)').replace(
    b'count^2', b'count^(2)').replace(b't_sin', b't_(sin)')
ASCII_MODULES = {
    'mods/seed.typ': b'#let seed=7\n#let equal=7\n#let txt="(),_+"\n#let sin=3\n',
    'mods/left.typ': b'#import "seed.typ": seed as original, txt\n#let renamed=original\n',
    'mods/right.typ': b'#import "seed.typ": seed, equal as same, sin\n#let another=seed\n',
    'mods/unused.typ': b'#import "deep/leaf.typ": leaf\n#let unused=leaf\n',
    'mods/deep/leaf.typ': b'#let leaf="unrendered"\n',
}
KOREAN_ROOT = b'#import "values.typ": text, count\nDependency #text $x_count$\n'
KOREAN_EXPECTED = KOREAN_ROOT.replace(b'x_count', b'x_(count)')
KOREAN_TEXT = '한글가힣ㄱㅏ ·–—‘’“”…₩、。〈〉《》「」『』【】'
KOREAN_MODULES = {'values.typ': ('#let text="' + KOREAN_TEXT + '"\n#let count=7\n').encode()}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1',
                     'Opt in to private native graph compilation with TYPST_LINT_RENDER_TESTS=1')
class NativeCapturedRelativeCompileTests(unittest.TestCase):
    def setUp(self):
        if sys.platform != 'darwin' or platform.machine() != 'arm64':
            self.skipTest('Reviewed native graph compiler requires Darwin arm64 Typst 0.15.1')
        if not EXECUTABLE.is_file():
            self.skipTest('Missing built pinned syntax adapter: ' + str(EXECUTABLE))
        if not PINNED.is_file():
            self.skipTest('Missing reviewed Typst 0.15.1 executable: ' + str(PINNED))
        missing = [name for name in PDF_HELPERS if not shutil.which(name)]
        if missing:
            self.skipTest('Missing installed local PDF helpers: ' + ', '.join(missing))
        self.assertEqual(PINNED.stat().st_size, 44694736,
                         'Installed compiler size differs from reviewed Typst 0.15.1')
        self.temporary = tempfile.TemporaryDirectory(prefix='typst-relative-native-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.base.chmod(0o700)
        self.serial = 0

    def fixture(self, source, modules, *, target='main.typ'):
        self.serial += 1
        base = self.base / str(self.serial)
        root, store = base / 'consumer', base / 'journals'
        root.mkdir(parents=True, mode=0o700)
        store.mkdir(mode=0o700)
        sources = {target: source, **modules}
        for name, raw in sources.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            path.chmod(0o640 if name == target else 0o444)
        config = root / '.typst-lint.toml'
        config.write_bytes(('rule-set="core"\ninclude=[' + json.dumps(target) + ']\n').encode())
        catalog = base / 'owned-catalog.json'
        catalog.write_bytes(CATALOG.read_bytes())
        unrelated = root / 'unselected.typ'
        unrelated.write_bytes(b'\xff #read("must never be captured")\n')
        output = (root / target).with_suffix('.pdf')
        output.write_bytes(b'owned previous PDF sentinel\x00\xff')
        return SimpleNamespace(base=base, root=root, store=store, target=target,
            sources=sources, source=root / target, config=config, catalog=catalog,
            unrelated=unrelated, output=output,
            lease=OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers())

    @contextmanager
    def context(self, f):
        with mock.patch.object(inputs, 'CATALOG', f.catalog):
            with bridge.ProtectedCapturedRelativeFix(f.root, f.target,
                    journal_directory=f.store, lease=f.lease) as fix:
                yield fix

    def state(self, path):
        value = path.lstat()
        return (path.read_bytes(), value.st_dev, value.st_ino, value.st_mode,
                value.st_uid, value.st_gid, value.st_mtime_ns)

    def preserved(self, f):
        paths = [f.root / name for name in f.sources if name != f.target]
        return {path: self.state(path) for path in
                (*paths, f.config, f.catalog, f.unrelated, f.output)}

    def helper(self, name, *arguments):
        result = subprocess.run([shutil.which(name), *map(str, arguments)],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def boxes(self, pdf):
        root = ET.fromstring(self.helper('pdftotext', '-bbox', pdf, '-'))
        return tuple((node.text, tuple(float(node.attrib[key]) for key in
                     ('xMin', 'yMin', 'xMax', 'yMax')))
                     for node in root.iter() if node.tag.endswith('}word'))

    def check_pdf(self, f, raw, *, stem):
        # Retain only temporary private local PDF/raster outputs, never fonts.
        pdf = f.base / (stem + '.pdf')
        pdf.write_bytes(raw)
        pdf.chmod(0o600)
        self.assertTrue(raw.startswith(b'%PDF-'))
        self.assertTrue(raw.rstrip().endswith(b'%%EOF'))
        self.assertGreater(len(raw), 1000)
        self.assertLessEqual(len(raw), 16 * 1024 * 1024)
        info = self.helper('pdfinfo', pdf).decode()
        pages = next(line.split()[1] for line in info.splitlines() if line.startswith('Pages:'))
        self.assertEqual(pages, '1')
        text = self.helper('pdftotext', '-layout', pdf, '-').decode('utf-8')
        self.assertNotIn('\ufffd', text)
        boxes = self.boxes(pdf)
        self.assertTrue(boxes)
        for _, (x1, y1, x2, y2) in boxes:
            self.assertGreater(x2, x1)
            self.assertGreater(y2, y1)
        raster = f.base / stem
        self.helper('pdftoppm', '-singlefile', '-r', '80', pdf, raster)
        pixels = raster.with_suffix('.ppm').read_bytes()
        magic, dimensions, maximum, payload = pixels.split(b'\n', 3)
        self.assertEqual((magic, maximum), (b'P6', b'255'))
        width, height = map(int, dimensions.split())
        self.assertEqual(len(payload), width * height * 3)
        self.assertLess(min(payload), 200, 'Raster contains no dark glyph pixels')
        self.assertEqual(max(payload), 255)
        return pdf, text, boxes, pixels

    def compile(self, f, fix, snapshot, *, profile):
        observed, run = [], runtime._run

        def inspect(argv, **kwargs):
            if kwargs['purpose'] == 'compile':
                project = Path(kwargs['cwd'])
                copied = {path.relative_to(project).as_posix(): path.read_bytes()
                          for path in project.rglob('*') if path.is_file()}
                self.assertEqual(copied, dict(snapshot.sources))
                self.assertEqual(Path(argv[argv.index('--root') + 1]), project)
                self.assertEqual(Path(argv[-2]), project / f.target)
                self.assertIn('--ignore-system-fonts', argv)
                self.assertEqual(argv[argv.index('--deps-format') + 1], 'zero')
                self.assertEqual(argv.count('--font-path'), 1 if profile == KOREAN else 0)
                if profile == KOREAN:
                    directory = Path(argv[argv.index('--font-path') + 1])
                    self.assertEqual([path.name for path in directory.iterdir()], ['AppleGothic.ttf'])
                self.assertTrue(all(stat.S_IMODE((project / name).stat().st_mode) == 0o400
                                    for name in snapshot.sources))
            result = run(argv, **kwargs)
            if kwargs['purpose'] == 'compile':
                raw = Path(argv[argv.index('--deps') + 1]).read_bytes()
                self.assertTrue(raw.endswith(b'\0'))
                paths = tuple(raw[:-1].decode('utf-8', errors='strict').split('\0'))
                self.assertEqual(set(paths), set(snapshot.sources))
                self.assertEqual(len(paths), len(set(paths)))
                observed.append((copied, raw, paths, str(project)))
            return result

        with mock.patch.object(runtime, '_run', side_effect=inspect):
            artifact = compiler.compile_committed_graph(fix, snapshot,
                profile=profile, deadline_seconds=120)
        self.assertEqual(len(observed), 1)
        copied, dependencies, paths, cwd = observed[0]
        self.assertEqual(artifact.dependency_bytes, dependencies)
        self.assertEqual(artifact.dependency_paths, paths)  # Preserve observed order, never assume it.
        self.assertEqual(artifact.dependencies.sha256, digest(dependencies))
        self.assertEqual(artifact.dependencies.size, len(dependencies))
        self.assertEqual(artifact.source_profile, core.SOURCE_PROFILE)
        self.assertEqual((artifact.root_grammar_version, artifact.module_grammar_version,
                          artifact.parser_contract),
                         (snapshot.root_grammar_version, snapshot.module_grammar_version,
                          snapshot.parser_contract))
        self.assertEqual(artifact.source_sha256, digest(copied[f.target]))
        self.assertEqual(artifact.config_sha256, snapshot.config_sha256)
        self.assertEqual(artifact.catalog_sha256, snapshot.catalog_sha256)
        self.assertEqual(dict(artifact.evidence_hashes),
            {path: None if raw is None else digest(raw) for path, raw in snapshot.evidence.items()})
        self.assertEqual(artifact.aggregate_source_bytes, sum(map(len, copied.values())))
        self.assertLessEqual(artifact.aggregate_source_bytes, 4 * 1024 * 1024)
        self.assertEqual({item.path for item in artifact.sources}, set(copied))
        self.assertLessEqual(len(artifact.sources), 32)
        self.assertLessEqual(len(artifact.project_directories), 64)
        self.assertEqual(len({item.private.identity[:2] for item in artifact.sources}), len(copied))
        for item in artifact.sources:
            self.assertEqual((item.sha256, item.size), (digest(copied[item.path]), len(copied[item.path])))
            self.assertEqual((item.private.sha256, item.private.size), (item.sha256, item.size))
            self.assertLessEqual(item.size, 1024 * 1024)
            self.assertFalse(Path(item.private.path).exists())
        self.assertEqual(artifact.compiler.path, str(PINNED))
        self.assertEqual((artifact.compiler.sha256, artifact.private_compiler.sha256),
                         (PINNED_SHA256, PINNED_SHA256))
        self.assertEqual(artifact.compiler.size, 44694736)
        self.assertEqual(artifact.probes[0].stdout, runtime._VERSION)
        self.assertEqual((artifact.compilation.returncode, artifact.compilation.stderr), (0, b''))
        self.assertEqual((artifact.report.safety_gate, artifact.report.compiler_status), ('passed', 0))
        self.assertEqual(artifact.report.attempts, (*artifact.probes, artifact.compilation))
        self.assertEqual(artifact.report.resource_report, artifact.resource_report)
        self.assertEqual(artifact.scalar_budget.policy, 'captured-relative-scalar-render-budget-v1')
        self.assertEqual(artifact.scalar_budget.limit, 1048576)
        self.assertLessEqual(artifact.scalar_budget.total_charge, artifact.scalar_budget.limit)
        self.assertEqual(artifact.scalar_budget.use_count, len(snapshot.proof.imported_uses))
        for item in (*artifact.project_directories, artifact.private_compiler,
                     artifact.output, artifact.dependencies):
            self.assertFalse(Path(item.path).exists())
        self.assertFalse(Path(cwd).exists())
        with self.assertRaises(FrozenInstanceError):
            artifact.aggregate_source_bytes = 0
        # All authority/resource rechecks happen after private workspace cleanup,
        # while the protected bridge and its genuine committed receipt are live.
        compiler.assert_artifact_current(fix, artifact)
        raw = artifact.pdf_bytes
        self.assertEqual(artifact.pdf_sha256, digest(raw))
        self.assertEqual((artifact.output.sha256, artifact.output.size), (digest(raw), len(raw)))
        return artifact, raw

    def test_ascii_nested_diamond_unused_alias_shadow_and_noop_postimage_match(self):
        # Unicode logical paths are independent of the ASCII source repertoire.
        target = 'chapters 한글 with spaces/main.typ'
        repaired = self.fixture(ASCII_ROOT, ASCII_MODULES, target=target)
        noop = self.fixture(ASCII_EXPECTED, ASCII_MODULES, target=target)
        outputs = []
        retained = []
        for f, changed in ((repaired, True), (noop, False)):
            with self.subTest(repaired=changed):
                preserved = self.preserved(f)
                before = self.state(f.source)
                with self.context(f) as fix:
                    candidate = fix.validate()
                    self.assertEqual(candidate.sources[f.target], ASCII_EXPECTED)
                    fix.apply(candidate)
                    snapshot = fix.committed_graph_snapshot()
                    self.assertEqual(dict(snapshot.sources), {f.target: ASCII_EXPECTED, **ASCII_MODULES})
                    self.assertEqual(f.source.read_bytes(), ASCII_EXPECTED)
                    self.assertEqual(fix.summary.modified_paths, (f.target,) if changed else ())
                    bindings = {binding.name: binding for binding in snapshot.proof.root_bindings}
                    self.assertEqual(bindings['count'].origin, bindings['another'].origin)
                    self.assertNotEqual(bindings['count'].origin, bindings['same'].origin)
                    self.assertEqual(bindings['sin'].value, 3)  # Imported integer shadows math.sin.
                    with mock.patch.object(runtime, '_capture_compiler', side_effect=AssertionError('no capture')), \
                            mock.patch.object(runtime, '_capture_fonts', side_effect=AssertionError('no font capture')):
                        with self.assertRaises(runtime.CompileSafetyError) as caught:
                            compiler.compile_committed_graph(fix, copy.copy(snapshot), profile=ASCII)
                    self.assertEqual(caught.exception.report.attempts, ())
                    artifact, raw = self.compile(f, fix, snapshot, profile=ASCII)
                    self.assertEqual(artifact.resource_report, runtime.ResourceReport(ASCII, 'ascii-v1', ()))
                    pdf, text, boxes, raster = self.check_pdf(f, raw, stem='native')
                    for token in ('Heading', 'Strong', 'Emphasis', '(),_+'):
                        self.assertIn(token, text)
                    self.assertNotIn('unrendered', text)
                    fonts = self.helper('pdffonts', pdf)
                    self.assertIn(b'NewCMMath', fonts)
                    self.assertNotIn(b'AppleGothic', fonts)
                    self.assertTrue(all(row.split()[-5:-2] == [b'yes', b'yes', b'yes']
                                        for row in fonts.splitlines()[2:] if row.strip()), fonts)
                    outputs.append((raw, text, boxes, raster))
                    retained.append(dict(snapshot.sources))
                with self.assertRaises(runtime.CompileSafetyError):
                    _ = artifact.pdf_bytes
                self.assertEqual(self.preserved(f), preserved)
                if not changed:
                    self.assertEqual(self.state(f.source), before)
                    self.assertEqual(list(f.store.glob('root-*/*/manifest.json')), [])
                else:
                    self.assertEqual(len(list(f.store.glob('root-*/*/manifest.json'))), 1)
        self.assertEqual(retained[0], retained[1])
        self.assertEqual(outputs[0], outputs[1])

    def test_dependency_only_korean_refuses_ascii_before_resources_then_keeps_actual_glyphs(self):
        if not FONT.is_file():
            self.skipTest('Missing reviewed private-local AppleGothic Regular font: ' + str(FONT))
        self.assertEqual(FONT.stat().st_size, 15253080,
                         'Installed AppleGothic size differs from reviewed Regular face')
        f = self.fixture(KOREAN_ROOT, KOREAN_MODULES)
        self.assertTrue(KOREAN_ROOT.isascii())
        self.assertTrue(all(name.isascii() for name in f.sources))
        preserved = self.preserved(f)
        with self.context(f) as fix:
            fix.apply(fix.validate())
            snapshot = fix.committed_graph_snapshot()
            summary = fix.summary
            self.assertEqual(dict(snapshot.sources), {f.target: KOREAN_EXPECTED, **KOREAN_MODULES})
            with mock.patch.object(runtime, '_capture_compiler', side_effect=AssertionError('no compiler capture')) as capture, \
                    mock.patch.object(runtime, '_capture_fonts', side_effect=AssertionError('no font capture')) as fonts, \
                    mock.patch.object(runtime, '_run', side_effect=AssertionError('no native attempt')) as run:
                with self.assertRaises(runtime.UnsupportedCompileProfile):
                    compiler.compile_committed_graph(fix, snapshot, profile=ASCII)
            capture.assert_not_called()
            fonts.assert_not_called()
            run.assert_not_called()
            self.assertIs(fix.summary, summary)
            self.assertEqual(f.source.read_bytes(), KOREAN_EXPECTED)
            self.assertEqual(self.preserved(f), preserved)
            artifact, raw = self.compile(f, fix, snapshot, profile=KOREAN)
            self.assertEqual(artifact.resource_report.profile, KOREAN)
            font, = artifact.resource_report.fonts
            self.assertEqual((font.installed.path, font.installed.sha256,
                              font.installed.size, font.face_index),
                             (str(FONT), FONT_SHA256, 15253080, 0))
            self.assertEqual((font.private.sha256, font.private.size), (FONT_SHA256, 15253080))
            self.assertEqual((font.identity.family, font.identity.full_name),
                             ('AppleGothic', 'AppleGothic Regular'))
            self.assertNotEqual(font.installed.identity, font.private.identity)
            self.assertFalse(Path(font.private.path).exists())
            pdf, text, boxes, _ = self.check_pdf(f, raw, stem='native-korean')
            self.assertIn('Dependency', text)
            self.assertIn('𝑥7', ''.join(text.split()))
            for character in KOREAN_TEXT.replace(' ', ''):
                self.assertIn(character, text)
            self.assertTrue(any('한글가힣ㄱㅏ' in word for word, _ in boxes))
            fonts = self.helper('pdffonts', pdf)
            rows = [row for row in fonts.splitlines() if b'AppleGothic' in row]
            self.assertTrue(rows, fonts)
            self.assertTrue(all(row.split()[-5:-2] == [b'yes', b'yes', b'yes'] for row in rows))
            xml = ET.fromstring(self.helper('pdftohtml', '-xml', '-hidden', '-stdout', pdf))
            specs = {node.attrib['id']: node.attrib for node in xml.iter('fontspec')}
            spans = [node for node in xml.iter('text') if '한글가힣ㄱㅏ' in ''.join(node.itertext())]
            self.assertTrue(spans)
            self.assertTrue(all('AppleGothic' in specs[node.attrib['font']]['family'] for node in spans))
            # This fixture uses plain Korean only. One captured Regular face
            # does not establish Korean bold/italic or arbitrary Unicode fidelity.
            self.assertEqual(fix.summary.modified_paths, (f.target,))
        with self.assertRaises(runtime.CompileSafetyError):
            compiler.assert_artifact_current(fix, artifact)
        self.assertEqual(f.source.read_bytes(), KOREAN_EXPECTED)
        self.assertEqual(self.preserved(f), preserved)


if __name__ == '__main__':
    unittest.main()
