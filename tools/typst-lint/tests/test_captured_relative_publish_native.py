"""Opt-in actual Mac acceptance for INTERNAL relative-graph PDF publication.

All consumers, journals, PDFs and rasters are private local temporary files.
Only the adapter captures the pinned executable and installed AppleGothic face;
no Apple font bytes are fixtures, retained outputs or redistribution. These two
renders exercise durable publication, not another exhaustive compiler matrix.
The earlier compiler suite's identical-postimage determinism comparison is not
evidence of visual equivalence with an original unparenthesized source. This
suite likewise makes no original-source visual-equivalence claim.
"""
from contextlib import contextmanager, ExitStack
import base64
import hashlib
import importlib
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
import captured_relative_fix as bridge
import captured_relative_inputs as inputs
import closed_core_compile as runtime
import pdf_publication_store as store
from resource_profiles import (ASCII_RESOURCE_PROFILE as ASCII,
    APPLEGOTHIC_RESOURCE_PROFILE as KOREAN, REPERTOIRE_VERSIONS)
from syntax_client import EXECUTABLE
from transactions import OfflineExclusive, _xattrs
from typst_lint import CATALOG

PINNED = Path('/opt/homebrew/Cellar/typst/0.15.1/bin/typst')
PINNED_SHA256 = '08d01e5a434e821c4a1ce30dd1593e1bbc57c9b1203c4d760d8912ef4ce6a621'
FONT = Path('/System/Library/Fonts/Supplemental/AppleGothic.ttf')
FONT_SHA256 = '69071005e523e3a2bf276b35b0066f68a7ec0d942c860941ad188ef15ef69c51'
PDF_HELPERS = ('pdfinfo', 'pdftotext', 'pdffonts', 'pdftoppm', 'pdftohtml')
OLD_PDF = b'owned arbitrary previous output\x00\xff\r\n'
ASCII_ROOT = (b'\xef\xbb\xbf#import "../mods/values.typ": count, label\r\n'
    b'= Published #label\r\nGraph $x_count + y^count$\r\n')
ASCII_EXPECTED = ASCII_ROOT.replace(b'x_count', b'x_(count)').replace(b'y^count', b'y^(count)')
ASCII_MODULES = {
    'mods/values.typ': b'#import "leaf.typ": seed\n#let count=seed\n#let label="captured"\n',
    'mods/leaf.typ': b'#let seed=7\n',
}
KOREAN_ROOT = b'#import "values.typ": text, count\nDependency #text $x_count$\n'
KOREAN_EXPECTED = KOREAN_ROOT.replace(b'x_count', b'x_(count)')
KOREAN_TEXT = '한글가힣ㄱㅏ ·–—‘’“”…₩、。〈〉《》「」『』【】'
KOREAN_MODULES = {'values.typ': ('#let text="' + KOREAN_TEXT + '"\n#let count=7\n').encode()}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def file_state(path):
    """Retain hashes/metadata, never retain installed compiler or font bytes."""
    info = path.lstat()
    with path.open('rb') as handle:
        sha256 = hashlib.file_digest(handle, 'sha256').hexdigest()
        attrs = _xattrs(handle.fileno())
    return (sha256, info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_uid, info.st_gid, info.st_mtime_ns,
            info.st_ctime_ns, attrs)


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1',
    'Opt in to private native graph PDF publication with TYPST_LINT_RENDER_TESTS=1')
class NativeCapturedRelativePublishTests(unittest.TestCase):
    def setUp(self):
        if sys.platform != 'darwin' or platform.machine() != 'arm64':
            self.skipTest('Reviewed native graph publication requires Darwin arm64 Typst 0.15.1')
        if not EXECUTABLE.is_file():
            self.skipTest('Missing built pinned syntax adapter: ' + str(EXECUTABLE))
        if not PINNED.is_file():
            self.skipTest('Missing reviewed Typst 0.15.1 executable: ' + str(PINNED))
        missing = [name for name in PDF_HELPERS if not shutil.which(name)]
        if missing:
            self.skipTest('Missing installed local PDF helpers: ' + ', '.join(missing))
        # Availability is a skip; a changed reviewed resource is a failure.
        self.remember_installed(PINNED, PINNED_SHA256, 44694736)
        self.publication = importlib.import_module('captured_relative_publish')
        temporary = tempfile.TemporaryDirectory(prefix='typst-relative-publish-native-')
        self.base = Path(temporary.name).resolve()
        self.base.chmod(0o700)

        def cleanup():
            temporary.cleanup()
            self.assertFalse(self.base.exists(), 'Private test outputs were not cleaned')

        self.addCleanup(cleanup)

    def remember_installed(self, path, expected_hash, expected_size):
        before = file_state(path)
        self.assertEqual((before[0], before[5]), (expected_hash, expected_size),
                         'Installed resource differs from reviewed pinned bytes: ' + str(path))
        self.addCleanup(lambda: self.assertEqual(file_state(path), before,
                         'Installed resource changed: ' + str(path)))

    def fixture(self, source, modules, *, target='main.typ', present):
        root, journals = self.base / 'consumer', self.base / 'source journals'
        root.mkdir(mode=0o700)
        journals.mkdir(mode=0o700)
        sources = {target: source, **modules}
        for name, raw in sources.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            path.chmod(0o640 if name == target else 0o444)
        config = root / '.typst-lint.toml'
        config.write_bytes(('rule-set="core"\ninclude=[' + json.dumps(target) + ']\n').encode())
        catalog = self.base / 'owned catalog.json'
        catalog.write_bytes(CATALOG.read_bytes())
        unrelated = root / 'unselected.typ'
        unrelated.write_bytes(b'\xff dirty user work #read("never")\n')
        destination = 'build with spaces/final graph.pdf'
        output = root / destination
        output.parent.mkdir(mode=0o700)
        if present:
            output.write_bytes(OLD_PDF)
            output.chmod(0o644)
        return SimpleNamespace(root=root, journals=journals, target=target,
            source=root / target, sources=sources, config=config, catalog=catalog,
            unrelated=unrelated, destination=destination, output=output, present=present,
            source_lease=OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers(),
            output_lease=store.OfflineOutputExclusive.assert_exporters_watchers_closed_and_no_other_writers())

    @contextmanager
    def context(self, f):
        with mock.patch.object(inputs, 'CATALOG', f.catalog):
            with bridge.ProtectedCapturedRelativeFix(f.root, f.target,
                    journal_directory=f.journals, lease=f.source_lease) as fix:
                yield fix

    def helper(self, name, *arguments):
        result = subprocess.run([shutil.which(name), *map(str, arguments)],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def check_render(self, f):
        raw = f.output.read_bytes()
        self.assertTrue(raw.startswith(b'%PDF-'))
        self.assertTrue(raw.rstrip().endswith(b'%%EOF'))
        self.assertGreater(len(raw), 1000)
        info = self.helper('pdfinfo', f.output).decode()
        self.assertEqual(next(line.split()[1] for line in info.splitlines()
                              if line.startswith('Pages:')), '1')
        text = self.helper('pdftotext', '-layout', f.output, '-').decode('utf-8')
        self.assertNotIn('\ufffd', text)
        xml = ET.fromstring(self.helper('pdftotext', '-bbox', f.output, '-'))
        boxes = [(node.text, tuple(float(node.attrib[key]) for key in
                  ('xMin', 'yMin', 'xMax', 'yMax')))
                 for node in xml.iter() if node.tag.endswith('}word')]
        self.assertTrue(boxes)
        self.assertTrue(all(x2 > x1 and y2 > y1 for _, (x1, y1, x2, y2) in boxes))
        raster = self.base / 'private-render'
        self.helper('pdftoppm', '-singlefile', '-r', '80', f.output, raster)
        magic, dimensions, maximum, pixels = raster.with_suffix('.ppm').read_bytes().split(b'\n', 3)
        self.assertEqual((magic, maximum), (b'P6', b'255'))
        width, height = map(int, dimensions.split())
        self.assertEqual(len(pixels), width * height * 3)
        self.assertLess(min(pixels), 200, 'Raster contains no dark glyph pixels')
        self.assertEqual(max(pixels), 255)
        fonts = self.helper('pdffonts', f.output)
        rows = [row for row in fonts.splitlines()[2:] if row.strip()]
        self.assertTrue(rows, fonts)
        self.assertTrue(all(row.split()[-5:-2] == [b'yes', b'yes', b'yes'] for row in rows), fonts)
        return text, boxes, fonts

    def publish(self, f, expected, *, profile):
        immutable_paths = [f.root / name for name in f.sources if name != f.target]
        immutable_paths.extend((f.config, f.catalog, f.unrelated))
        preserved = {path: file_state(path) for path in immutable_paths}
        source_before = file_state(f.source)
        old = file_state(f.output) if f.present else None
        observed, prepared = [], []
        real_compile = compiler.compile_committed_graph

        def prepare_observer(point, _path):
            if point == 'after_prepared':
                self.assertEqual(file_state(f.source), source_before)
                self.assertEqual(file_state(f.output) if f.present else None, old)
                self.assertFalse(observed)
                prepared.append(point)

        with self.context(f) as fix:
            # Output baseline must be durable before the first validate/apply.
            with self.publication.PreparedCapturedRelativePdfPublication(fix,
                    destination=f.destination, lease=f.output_lease,
                    fault=prepare_observer) as pub:
                self.assertEqual(prepared, ['after_prepared'])
                candidate = fix.validate()
                self.assertEqual(dict(candidate.sources), {f.target: expected})
                fix.apply(candidate)
                snapshot = fix.committed_graph_snapshot()
                self.assertEqual(dict(snapshot.sources), {f.target: expected,
                    **{name: raw for name, raw in f.sources.items() if name != f.target}})
                self.assertEqual(f.source.read_bytes(), expected)
                self.assertEqual(fix.summary.modified_paths, (f.target,))

                def retain_genuine(*args, **kwargs):
                    artifact = real_compile(*args, **kwargs)
                    compiler.assert_artifact_current(fix, artifact)
                    observed.append((artifact, artifact.pdf_bytes))
                    return artifact

                # This observes, but never substitutes for, the actual compiler.
                with mock.patch.object(compiler, 'compile_committed_graph', side_effect=retain_genuine):
                    report = pub.compile_and_publish(snapshot, profile=profile, deadline_seconds=120)
                self.assertEqual(len(observed), 1)
                artifact, raw = observed[0]
                compiler.assert_artifact_current(fix, artifact)
                self.assertEqual(f.output.read_bytes(), artifact.pdf_bytes)
                self.assertIs(pub.report, report)
                self.assertEqual(report.compile_report, artifact.report)
                self.assertIsNot(report.compile_report, artifact.report)
                self.assertEqual((report.phase, report.gate_status, report.source_commit,
                    report.output_disposition, report.recovery_required),
                    ('published', 0, 'committed', 'published', False))
                self.assertEqual((report.compiler_invoked, report.compiler_status), (True, 0))
                self.assertEqual(report.source_journal_id, fix.summary.journal_id)
                self.assertEqual(report.observed_sha256, digest(raw))
                self.assertEqual(report.attempted_invocations, artifact.report.attempts)
                self.assertEqual([(a.purpose, a.started, a.returncode, a.lifecycle)
                    for a in report.attempted_invocations],
                    [('version-probe', True, 0, 'exited'), ('compile', True, 0, 'exited')])
                self.assertEqual((report.resource_report.profile, report.resource_report.repertoire_version),
                                 (profile, REPERTOIRE_VERSIONS[profile]))
                self.assertEqual(artifact.compiler.sha256, PINNED_SHA256)
                self.assertEqual(report.compile_report.safety_gate, 'passed')
                # Private compiler/font/source workspaces are already removed.
                for evidence in (artifact.private_compiler, artifact.output, artifact.dependencies,
                        *(source.private for source in artifact.sources),
                        *(font.private for font in report.resource_report.fonts)):
                    self.assertFalse(Path(evidence.path).exists())
                for invocation in report.attempted_invocations:
                    self.assertFalse(Path(invocation.cwd).exists())
                self.assertEqual({path: file_state(path) for path in preserved}, preserved)

        with self.assertRaises(runtime.CompileSafetyError):
            _ = artifact.pdf_bytes
        self.assertEqual(f.output.read_bytes(), raw)
        self.assertEqual(stat.S_IMODE(f.output.stat().st_mode), 0o644 if f.present else 0o600)
        if f.present:
            self.assertNotEqual(f.output.stat().st_ino, old[2])
        entries = store.inventory_publications(f.root)
        self.assertEqual(len(entries), 1)
        entry, = entries
        self.assertEqual((entry.journal_id, entry.destination, entry.state, entry.pending,
                          entry.compiler_invoked, entry.compiler_status),
                         (report.journal_id, f.destination, 'published', False, True, 0))
        journals = list((f.root.parent / store.STORE_NAME).glob('root-*/*/manifest.json'))
        self.assertEqual(len(journals), 1)
        journal = journals[0].parent
        manifest = json.loads(journals[0].read_bytes())
        self.assertEqual((manifest['schema'], manifest['kind'], manifest['destination']),
                         (1, store.KIND, f.destination))
        self.assertEqual(manifest['old']['sha256'] if f.present else manifest['old'],
                         digest(OLD_PDF) if f.present else None)
        self.assertEqual((journal / 'backup.bin').exists(), f.present)
        if f.present:
            self.assertEqual((journal / 'backup.bin').read_bytes(), OLD_PDF)
        self.assertFalse((journal / 'publish.bin').exists())
        self.assertFalse((journal / 'restore.bin').exists())
        events = [json.loads(path.read_bytes()) for path in sorted(journal.glob('event-*.json'))]
        self.assertEqual([event['state'] for event in events],
                         ['prepared', 'compile_launch_intent', 'compiled', 'staged',
                          'replace_intent', 'replaced', 'published'])
        launch, compiled, final = events[1], events[2], events[-1]
        self.assertEqual((launch['source_sha256'], launch['source_journal_id'], launch['profile'],
                          launch['source_target'], launch['config_sha256'], launch['catalog_sha256']),
                         (digest(expected), report.source_journal_id, profile, f.target,
                          snapshot.config_sha256, snapshot.catalog_sha256))
        self.assertEqual((compiled['compiler_sha256'], compiled['pdf_sha256'],
                          final['postimage']['sha256'], final['postimage']['size']),
                         (PINNED_SHA256, digest(raw), digest(raw), len(raw)))
        source_manifests = list(f.journals.glob('root-*/*/manifest.json'))
        self.assertEqual(len(source_manifests), 1)
        self.assertEqual(source_manifests[0].parent.name, report.source_journal_id)
        source_manifest = json.loads(source_manifests[0].read_bytes())
        source_file, = source_manifest['files']
        self.assertEqual((source_file['path'], base64.b64decode(source_file['before']),
                          base64.b64decode(source_file['after'])),
                         (f.target, f.sources[f.target], expected))
        source_events = [json.loads(path.read_bytes())['kind'] for path in
                         sorted(source_manifests[0].parent.glob('event-*.json'))]
        self.assertEqual(source_events[-1], 'committed')
        self.assertNotIn('recovery_required', source_events)
        self.assertEqual({path: file_state(path) for path in preserved}, preserved)
        self.assertFalse(list(self.base.rglob('AppleGothic.ttf')))
        return report

    def test_ascii_repaired_relative_graph_replaces_existing_output_durably(self):
        f = self.fixture(ASCII_ROOT, ASCII_MODULES,
                         target='chapters 한글/main.typ', present=True)
        report = self.publish(f, ASCII_EXPECTED, profile=ASCII)
        self.assertEqual(report.resource_report.fonts, ())
        text, _, fonts = self.check_render(f)
        for token in ('Published', 'captured', 'Graph'):
            self.assertIn(token, text)
        self.assertIn('𝑥7', ''.join(text.split()))
        self.assertIn(b'NewCMMath', fonts)
        self.assertNotIn(b'AppleGothic', fonts)

    def test_dependency_only_korean_publishes_absent_output_then_recovery_is_historical(self):
        if not FONT.is_file():
            self.skipTest('Missing reviewed private-local AppleGothic Regular font: ' + str(FONT))
        self.remember_installed(FONT, FONT_SHA256, 15253080)
        f = self.fixture(KOREAN_ROOT, KOREAN_MODULES, present=False)
        self.assertTrue(KOREAN_ROOT.isascii())
        self.assertTrue(all(name.isascii() for name in f.sources))
        report = self.publish(f, KOREAN_EXPECTED, profile=KOREAN)
        font, = report.resource_report.fonts
        self.assertEqual((font.installed.path, font.installed.sha256, font.installed.size,
                          font.private.sha256, font.private.size, font.face_index),
                         (str(FONT), FONT_SHA256, 15253080, FONT_SHA256, 15253080, 0))
        self.assertEqual((font.identity.family, font.identity.full_name),
                         ('AppleGothic', 'AppleGothic Regular'))
        text, boxes, fonts = self.check_render(f)
        self.assertIn('Dependency', text)
        for character in KOREAN_TEXT.replace(' ', ''):
            self.assertIn(character, text)
        self.assertTrue(any('한글가힣ㄱㅏ' in word for word, _ in boxes))
        self.assertIn(b'AppleGothic', fonts)
        xml = ET.fromstring(self.helper('pdftohtml', '-xml', '-hidden', '-stdout', f.output))
        specs = {node.attrib['id']: node.attrib for node in xml.iter('fontspec')}
        spans = [node for node in xml.iter('text') if '한글가힣ㄱㅏ' in ''.join(node.itertext())]
        self.assertTrue(spans)
        self.assertTrue(all('AppleGothic' in specs[node.attrib['font']]['family'] for node in spans))
        # Plain Regular only: this does not certify Korean bold or italic.

        # Remove only owned temporary source/evidence history. Make runtime
        # resources unavailable via mocks, never alter installed system files.
        for name in f.sources:
            (f.root / name).unlink()
        f.config.unlink()
        f.catalog.unlink()
        shutil.rmtree(f.journals)
        f.output.write_bytes(b'later private user edit, after durable publication\n')
        later = file_state(f.output)
        with ExitStack() as stack:
            forbidden = [(compiler, '_guard'), (compiler, 'assert_artifact_current'),
                (compiler, 'compile_committed_graph'), (runtime, '_reviewed_native_artifact'),
                (runtime, '_reviewed_resource_artifact'), (runtime, '_capture_compiler'),
                (runtime, '_capture_fonts'), (runtime, '_run'), (runtime, '_same')]
            for owner, name in forbidden:
                stack.enter_context(mock.patch.object(owner, name,
                    side_effect=AssertionError('Historical recovery must not access live ' + name)))
            historical = store.recover_publication(f.root, destination=f.destination,
                journal_id=report.journal_id, lease=f.output_lease)
        self.assertEqual((historical.phase, historical.source_commit,
                          historical.output_disposition, historical.recovery_required),
                         ('published', 'historical', 'published', False))
        self.assertEqual((historical.compiler_invoked, historical.compiler_status), (True, 0))
        self.assertEqual(historical.source_journal_id, report.source_journal_id)
        self.assertIsNone(historical.observed_sha256)
        self.assertIsNone(historical.compile_report)
        self.assertIsNone(historical.resource_report)
        self.assertEqual(historical.attempted_invocations, ())
        self.assertIn('Historical', historical.reason)
        self.assertEqual(file_state(f.output), later)
        entry, = store.inventory_publications(f.root)
        self.assertEqual((entry.state, entry.pending), ('published', False))


if __name__ == '__main__':
    unittest.main()
