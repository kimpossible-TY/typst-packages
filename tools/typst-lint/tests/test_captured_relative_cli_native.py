"""Opt-in acceptance of the real public captured-relative CLI executable.

The public production ten-second deadline is used without injection or overrides.
Consumers, journals, PDFs and rasters are private local temporary files. Only
production captures the installed compiler and AppleGothic Regular face; no font
bytes are fixtures, retained outputs, uploads or redistribution. These checks
make no original-source visual-equivalence or Korean bold/italic claim.
"""
import base64
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
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from transactions import _xattrs
from syntax_client import EXECUTABLE as PARSER, audit_adapter_inventory

LAUNCHER = TOOL / 'typst-lint'
CATALOG = TOOL / 'rules/catalog.json'
PROFILE = 'captured-relative-core-v1'
ASCII = 'embedded-ascii-v1'
KOREAN = 'embedded-applegothic-ko-v1'
ROOT_GRAMMAR = 'captured-relative-root-grammar-v1'
MODULE_GRAMMAR = 'captured-relative-module-grammar-v1'
PARSER_CONTRACT = ['typst-syntax:0.15.1', 'adapter:0.2.0', 'schema:2']
PINNED = Path('/opt/homebrew/Cellar/typst/0.15.1/bin/typst')
PINNED_SHA256 = '08d01e5a434e821c4a1ce30dd1593e1bbc57c9b1203c4d760d8912ef4ce6a621'
FONT = Path('/System/Library/Fonts/Supplemental/AppleGothic.ttf')
FONT_SHA256 = '69071005e523e3a2bf276b35b0066f68a7ec0d942c860941ad188ef15ef69c51'
PDF_HELPERS = ('pdfinfo', 'pdftotext', 'pdffonts', 'pdftoppm', 'pdftohtml')
SOURCE_STORE = '.typst-lint-journals'
OUTPUT_STORE = '.typst-lint-pdf-journals'
OLD_PDF = b'owned previous PDF sentinel\x00\xff\r\n'

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


def file_state(path):
    """Hashes and identities/metadata only, never retain installed font bytes."""
    info = path.lstat()
    with path.open('rb') as handle:
        sha256 = hashlib.file_digest(handle, 'sha256').hexdigest()
        attrs = _xattrs(handle.fileno())
    return (sha256, info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_uid, info.st_gid, info.st_mtime_ns,
            info.st_ctime_ns, attrs)


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1',
    'Opt in to private native public graph CLI with TYPST_LINT_RENDER_TESTS=1')
class NativeCapturedRelativeCliTests(unittest.TestCase):
    def setUp(self):
        if sys.platform != 'darwin' or platform.machine() != 'arm64':
            self.skipTest('Reviewed public graph CLI requires Darwin arm64 Typst 0.15.1')
        if not PARSER.is_file():
            self.skipTest('Missing built pinned syntax adapter: ' + str(PARSER))
        if not PINNED.is_file():
            self.skipTest('Missing reviewed Typst 0.15.1 executable: ' + str(PINNED))
        missing = [name for name in PDF_HELPERS if not shutil.which(name)]
        if missing:
            self.skipTest('Missing installed local PDF helpers: ' + ', '.join(missing))
        self.assertTrue(LAUNCHER.is_file() and os.access(LAUNCHER, os.X_OK))
        self.remember_resource(PINNED, PINNED_SHA256, 44694736)
        self.remember_resource(CATALOG)
        self.remember_resource(LAUNCHER)
        self.remember_resource(PARSER)
        adapter_inventory = audit_adapter_inventory()
        self.addCleanup(lambda: self.assertEqual(audit_adapter_inventory(), adapter_inventory,
                                                  'Syntax adapter capture changed during test'))
        temporary = tempfile.TemporaryDirectory(prefix='typst-relative-cli-native-')
        self.base = Path(temporary.name).resolve()
        self.base.chmod(0o700)

        def cleanup():
            temporary.cleanup()
            self.assertFalse(self.base.exists(), 'Private test outputs were not cleaned')

        self.addCleanup(cleanup)

    def remember_resource(self, path, expected_hash=None, expected_size=None):
        before = file_state(path)
        if expected_hash is not None:
            self.assertEqual((before[0], before[5]), (expected_hash, expected_size),
                             'Installed resource differs from reviewed bytes: ' + str(path))
        self.addCleanup(lambda: self.assertEqual(file_state(path), before,
                         'Installed/repository resource changed: ' + str(path)))

    def fixture(self, source, modules, *, target='main.typ', present):
        root = self.base / 'consumer'
        root.mkdir(mode=0o700)
        sources = {target: source, **modules}
        for name, raw in sources.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            path.chmod(0o640 if name == target else 0o444)
        config = root / '.typst-lint.toml'
        config.write_bytes(('rule-set="core"\ninclude=[' + json.dumps(target) + ']\n').encode())
        unrelated = root / 'unselected.typ'
        unrelated.write_bytes(b'\xff dirty private work #read("never capture")\n')
        destination = 'build with spaces/final graph.pdf'
        output = root / destination
        output.parent.mkdir(mode=0o700)
        if present:
            output.write_bytes(OLD_PDF)
            output.chmod(0o644)
        f = SimpleNamespace(root=root, target=target, source=root / target,
            sources=sources, config=config, unrelated=unrelated,
            destination=destination, output=output, present=present)
        immutable = [root / name for name in modules] + [config, unrelated]
        f.preserved = {path: file_state(path) for path in immutable}
        return f

    def invoke(self, f, arguments, *, status=0, json_format=True):
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([str(LAUNCHER), *arguments], cwd=f.root,
            env=environment, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, status, (result.stdout, result.stderr))
        self.assertEqual(result.stderr, b'')
        text = result.stdout.decode('utf-8', errors='strict')
        if not json_format:
            return text
        payload = json.loads(text)
        self.assertEqual((payload['schema'], payload['status']), (1, status))
        return payload

    def compile(self, f, profile, *, status=0):
        # No imported adapter, test runner, alternate compiler or public override.
        return self.invoke(f, ['compile', '--root', str(f.root), '--profile', PROFILE,
            '--resource-profile', profile, '--offline-exclusive',
            '--offline-output-exclusive', '--target', f.target,
            '--output', f.destination, '--format', 'json'], status=status)

    def manifests(self, store):
        return list((self.base / store).glob('root-*/*/manifest.json'))

    def events(self, manifest):
        return [json.loads(path.read_bytes()) for path in sorted(manifest.parent.glob('event-*.json'))]

    def check_preserved(self, f):
        self.assertEqual({path: file_state(path) for path in f.preserved}, f.preserved)
        for name, raw in f.sources.items():
            if name != f.target:
                self.assertEqual((f.root / name).read_bytes(), raw)
        expected = set(f.sources) | {'.typst-lint.toml', 'unselected.typ'}
        if f.output.exists():
            expected.add(f.destination)
        self.assertEqual({p.relative_to(f.root).as_posix() for p in f.root.rglob('*')
                          if p.is_file()}, expected)
        self.assertFalse(list(self.base.rglob('AppleGothic.ttf')))
        self.assertFalse(list(f.root.rglob('*.tmp')))

    def check_source_summary(self, f, summary, expected, *, before, changed):
        self.assertEqual((summary['profile'], summary['root_grammar_version'],
            summary['module_grammar_version'], summary['parser_contract']),
            (PROFILE, ROOT_GRAMMAR, MODULE_GRAMMAR, PARSER_CONTRACT))
        self.assertNotIn('grammar_version', summary)
        self.assertEqual((summary['target'], summary['original_sha256'], summary['final_sha256']),
                         (f.target, digest(before), digest(expected)))
        self.assertEqual(summary['modified_paths'], [f.target] if changed else [])
        self.assertEqual(summary['final_findings'], [])
        coverage = summary['coverage']
        self.assertTrue(coverage['input_profile_complete'])
        self.assertFalse(coverage['global_complete'])
        self.assertEqual((coverage['input_profile_rule_count'], coverage['global_rule_status'],
            coverage['global_complete_rule_count'], coverage['global_partial_rule_count']),
            (15, 'PARTIAL', 0, 33))
        self.assertEqual(len(summary['required_rule_ids']), 15)
        self.assertIn('core.parenthesized-scripts', summary['evaluated_rule_ids'])
        final_sources = {**f.sources, f.target: expected}
        self.assertEqual({item['path']: (item['sha256'], item['size'], item['role'])
                          for item in summary['sources']},
            {name: (digest(raw), len(raw), 'writable_root' if name == f.target
                else 'unchanged_read_only_dependency') for name, raw in final_sources.items()})
        self.assertEqual(len(summary['sources']), len(final_sources))
        expected_edges = []
        for importer, raw in final_sources.items():
            for line in raw.removeprefix(b'\xef\xbb\xbf').decode().splitlines():
                if not line.startswith('#import '):
                    continue
                request, names = line[len('#import '):].split(': ', 1)
                request = json.loads(request)
                target = os.path.normpath(str(Path(importer).parent / request))
                items = [tuple(part.split(' as ')) if ' as ' in part else (part, part)
                         for part in names.split(', ')]
                expected_edges.append((importer, request, target, tuple(items)))
        self.assertCountEqual([(edge['importer'], edge['request'], edge['target'],
            tuple(map(tuple, edge['items']))) for edge in summary['import_edges']], expected_edges)
        for edge in summary['import_edges']:
            self.assertIs(type(edge['node_id']), int)
            self.assertGreaterEqual(edge['node_id'], 0)
            start, end = edge['span']
            self.assertTrue(0 <= start < end <= len(final_sources[edge['importer']]))
            self.assertIn(edge['request'].encode(), final_sources[edge['importer']][start:end])
        insertions = summary['applied_insertions']
        self.assertEqual(bool(insertions), changed)
        replay = before
        for edit in sorted(insertions, key=lambda edit: edit['start'], reverse=True):
            self.assertEqual((edit['path'], edit['start']), (f.target, edit['end']))
            self.assertEqual(edit['rule_id'], 'core.parenthesized-scripts')
            self.assertTrue(edit['replacement'] and not edit['replacement'].strip('()'))
            replay = replay[:edit['start']] + edit['replacement'].encode() + replay[edit['end']:]
        self.assertEqual(replay, expected)
        if changed:
            self.assertRegex(summary['journal_id'], '^[0-9a-f]{32}$')
        else:
            self.assertIsNone(summary['journal_id'])

    def check_source_journal(self, f, expected, journal_id):
        manifests = self.manifests(SOURCE_STORE)
        self.assertEqual(len(manifests), 1)
        manifest, = manifests
        self.assertEqual(manifest.parent.name, journal_id)
        data = json.loads(manifest.read_bytes())
        self.assertEqual((data['schema'], data['root']), (1, str(f.root)))
        entry, = data['files']
        self.assertEqual((entry['path'], base64.b64decode(entry['before']),
            base64.b64decode(entry['after'])), (f.target, f.sources[f.target], expected))
        events = self.events(manifest)
        self.assertEqual(events[-1]['kind'], 'committed')
        self.assertNotIn('recovery_required', [event['kind'] for event in events])
        self.assertTrue(all(p.name == 'manifest.json' or p.name.startswith('event-')
                            for p in manifest.parent.iterdir()))

    def check_success(self, f, payload, expected, profile, *, before, changed):
        self.assertEqual((payload['command'], payload['phase'], payload['source_commit'],
            payload['compiler_invoked'], payload['compiler_status']),
            ('compile', 'completed', 'committed', True, 0))
        self.assertEqual((payload['profile'], payload['resource_profile'], payload['target'],
            payload['output']), (PROFILE, profile, f.target, f.destination))
        self.assertEqual((payload['root_grammar_version'], payload['module_grammar_version'],
            payload['parser_contract']), (ROOT_GRAMMAR, MODULE_GRAMMAR, PARSER_CONTRACT))
        self.assertNotIn('grammar_version', payload)
        self.check_source_summary(f, payload['source_summary'], expected, before=before, changed=changed)
        self.assertEqual(f.source.read_bytes(), expected)
        self.assertEqual(stat.S_IMODE(f.source.stat().st_mode), 0o640)
        self.assertEqual(payload['source_recovery']['state'], 'not_required')
        self.assertEqual(payload['source_recovery']['pending'], [])
        self.assertEqual(payload['output_recovery']['state'], 'not_required')
        report = payload['publication_report']
        self.assertEqual((report['phase'], report['gate_status'], report['source_commit'],
            report['output_disposition'], report['recovery_required']),
            ('published', 0, 'committed', 'published', False))
        self.assertEqual(report['source_journal_id'], payload['source_summary']['journal_id'])
        raw = f.output.read_bytes()
        self.assertEqual(report['observed_sha256'], digest(raw))
        self.assertEqual(report['invocation_origin'], 'original_invocation')
        self.assertTrue(report['invocation_details_available'])
        self.assertEqual(payload['compiler_observation_origin'], 'original_invocation')
        compile_report = payload['compile_report']
        self.assertEqual((compile_report['safety_gate'], compile_report['compiler_failure']), ('passed', None))
        self.assertEqual((compile_report['compiler_invoked'], compile_report['compiler_status']), (True, 0))
        attempts = report['attempted_invocations']
        self.assertEqual(compile_report['attempted_invocations'], attempts)
        self.assertEqual([(a['purpose'], a['launch_attempted'], a['started'], a['returncode'],
            a['lifecycle']) for a in attempts],
            [('version-probe', True, True, 0, 'exited'), ('compile', True, True, 0, 'exited')])
        self.assertEqual(bytes.fromhex(attempts[0]['stdout_hex']), b'typst 0.15.1 (unknown commit)\n')
        for attempt in attempts:
            self.assertEqual(attempt['stderr_hex'], '')
            self.assertFalse(Path(attempt['cwd']).exists())
            self.assertFalse(Path(attempt['argv'][0]).exists())
        invocation = attempts[-1]
        argv, cwd = invocation['argv'], Path(invocation['cwd'])
        self.assertEqual(argv[1], 'compile')
        self.assertIn('--ignore-system-fonts', argv)
        self.assertEqual(argv[argv.index('--deps-format') + 1], 'zero')
        self.assertEqual(Path(argv[argv.index('--root') + 1]), cwd)
        self.assertFalse(cwd.parent.exists(), 'Private compiler workspace was not cleaned')
        self.assertEqual(Path(argv[-2]), cwd / f.target)
        self.assertEqual(argv.count('--font-path'), 1 if profile == KOREAN else 0)
        self.assertFalse(Path(argv[-1]).exists())
        resource = report['resource_report']
        self.assertEqual(compile_report['resource_report'], resource)
        self.assertTrue(report['resource_details_available'])
        self.assertTrue(compile_report['resource_details_available'])
        self.assertEqual((resource['profile'], resource['repertoire_version']),
                         (profile, 'ascii-v1' if profile == ASCII else 'applegothic-ko-v1'))
        if profile == ASCII:
            self.assertEqual(resource['fonts'], [])
        else:
            font, = resource['fonts']
            installed, private = font['installed'], font['private']
            self.assertEqual((installed['path'], installed['sha256'], installed['size'],
                private['sha256'], private['size'], font['face_index']),
                (str(FONT), FONT_SHA256, 15253080, FONT_SHA256, 15253080, 0))
            self.assertEqual(installed['identity'][:2], list(file_state(FONT)[1:3]))
            self.assertEqual((font['identity']['family'], font['identity']['full_name']),
                             ('AppleGothic', 'AppleGothic Regular'))
            self.assertFalse(Path(private['path']).exists())
            self.assertFalse(Path(argv[argv.index('--font-path') + 1]).exists())
            self.assertIn('regular upright', payload['known_resource_profile_limitation']['note'])
        self.assertTrue(payload['graph_observation_available'])
        graph = payload['graph_observation']
        sources = {**f.sources, f.target: expected}
        self.assertEqual({item['path']: (item['sha256'], item['size']) for item in graph['sources']},
                         {name: (digest(raw), len(raw)) for name, raw in sources.items()})
        self.assertEqual(len(graph['sources']), len(sources))
        paths = graph['dependency_paths']
        self.assertEqual(set(paths), set(sources))
        self.assertEqual(len(paths), len(set(paths)))
        # Verify the reported bytes in their ORIGINAL observed order, not sorted.
        dependencies = b'\0'.join(path.encode() for path in paths) + b'\0'
        evidence = graph['dependencies']
        self.assertEqual((evidence['sha256'], evidence['size']), (digest(dependencies), len(dependencies)))
        self.assertEqual(evidence['path'], argv[argv.index('--deps') + 1])
        self.assertFalse(Path(evidence['path']).exists())
        self.assertTrue(evidence['identity'] and evidence['parents'])
        self.assertEqual(graph['aggregate_source_bytes'], sum(map(len, sources.values())))
        budget = graph['scalar_budget']
        self.assertEqual((budget['policy'], budget['limit']),
                         ('captured-relative-scalar-render-budget-v1', 1048576))
        self.assertTrue(0 < budget['total_charge'] <= budget['limit'])
        self.assertTrue(0 < budget['origin_count'] <= budget['use_count'])
        manifests = [m for m in self.manifests(OUTPUT_STORE) if m.parent.name == report['journal_id']]
        self.assertEqual(len(manifests), 1)
        manifest, = manifests
        data = json.loads(manifest.read_bytes())
        self.assertEqual((data['schema'], data['kind'], data['destination']),
                         (1, 'typst-pdf-publication', f.destination))
        self.assertEqual(data['old']['sha256'] if f.present else data['old'],
                         digest(OLD_PDF) if f.present else None)
        self.assertEqual((manifest.parent / 'backup.bin').exists(), f.present)
        if f.present:
            self.assertEqual((manifest.parent / 'backup.bin').read_bytes(), OLD_PDF)
        events = self.events(manifest)
        self.assertEqual([event['state'] for event in events], ['prepared', 'compile_launch_intent',
            'compiled', 'staged', 'replace_intent', 'replaced', 'published'])
        launch, compiled, final = events[1], events[2], events[-1]
        self.assertEqual((launch['source_sha256'], launch['source_journal_id'], launch['profile'],
            launch['source_target'], launch['config_sha256'], launch['catalog_sha256']),
            (digest(expected), report['source_journal_id'], profile, f.target,
             digest(f.config.read_bytes()), digest(CATALOG.read_bytes())))
        self.assertEqual((compiled['compiler_sha256'], compiled['pdf_sha256'],
            final['postimage']['sha256'], final['postimage']['size']),
            (PINNED_SHA256, digest(raw), digest(raw), len(raw)))
        self.assertEqual(stat.S_IMODE(f.output.stat().st_mode), 0o644 if f.present else 0o600)
        self.assertTrue(all(p.name in ('manifest.json', 'backup.bin') or p.name.startswith('event-')
                            for p in manifest.parent.iterdir()))
        self.check_preserved(f)
        return report

    def helper(self, name, *arguments):
        result = subprocess.run([shutil.which(name), *map(str, arguments)],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def check_render(self, f):
        raw = f.output.read_bytes()
        self.assertTrue(raw.startswith(b'%PDF-') and raw.rstrip().endswith(b'%%EOF'))
        self.assertTrue(1000 < len(raw) <= 16 * 1024 * 1024)
        info = self.helper('pdfinfo', f.output).decode()
        self.assertEqual(next(line.split()[1] for line in info.splitlines() if line.startswith('Pages:')), '1')
        text = self.helper('pdftotext', '-layout', f.output, '-').decode('utf-8')
        self.assertNotIn('\ufffd', text)
        xml = ET.fromstring(self.helper('pdftotext', '-bbox', f.output, '-'))
        boxes = [(node.text, tuple(float(node.attrib[key]) for key in
            ('xMin', 'yMin', 'xMax', 'yMax'))) for node in xml.iter() if node.tag.endswith('}word')]
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

    def check_noop_and_inventory(self, f, *, expected_output_journals):
        # Preserve publication evidence BEFORE later read-only/no-op commands.
        saved = {p: file_state(p) for name in (SOURCE_STORE, OUTPUT_STORE)
                 for p in (self.base / name).rglob('*') if p.is_file()}
        root_before, output_before = file_state(f.source), file_state(f.output)
        text = self.invoke(f, ['fix', '--root', str(f.root), '--profile', PROFILE,
            '--offline-exclusive', '--target', f.target, '--format', 'text'], json_format=False)
        for token in ('compiler not invoked', PROFILE, ROOT_GRAMMAR, MODULE_GRAMMAR,
                      '0 modified paths', 'global 33-rule coverage remains PARTIAL'):
            self.assertIn(token, text)
        inventory = self.invoke(f, ['output-status', '--root', str(f.root), '--format', 'json'])
        entries = inventory['output_inventory']['entries']
        self.assertEqual(len(entries), expected_output_journals)
        self.assertEqual(inventory['output_inventory']['pending'], [])
        self.assertTrue(inventory['output_inventory']['historical_only'])
        self.assertFalse(inventory['compiler_invoked'])
        self.assertNotIn('graph_observation', inventory)
        self.assertTrue(all(not entry['pending'] and not entry['resource_details_available']
                            and entry['resource_report'] is None for entry in entries))
        text = self.invoke(f, ['output-status', '--root', str(f.root), '--format', 'text'], json_format=False)
        self.assertIn(f'{expected_output_journals} historical PDF journals; 0 pending', text)
        self.assertIn('compiler not invoked', text)
        self.assertEqual((file_state(f.source), file_state(f.output)), (root_before, output_before))
        self.assertEqual({p: file_state(p) for name in (SOURCE_STORE, OUTPUT_STORE)
                 for p in (self.base / name).rglob('*') if p.is_file()}, saved)
        self.check_preserved(f)

    def test_ascii_public_cli_repairs_transitive_graph_and_replaces_existing_pdf(self):
        f = self.fixture(ASCII_ROOT, ASCII_MODULES,
            target='chapters 한글 with spaces/main.typ', present=True)
        source_before, output_before = file_state(f.source), file_state(f.output)
        payload = self.compile(f, ASCII)
        report = self.check_success(f, payload, ASCII_EXPECTED, ASCII, before=ASCII_ROOT, changed=True)
        self.assertNotEqual(file_state(f.source)[2], source_before[2])
        self.assertNotEqual(file_state(f.output)[2], output_before[2])
        self.check_source_journal(f, ASCII_EXPECTED, report['source_journal_id'])
        self.assertEqual(len(self.manifests(OUTPUT_STORE)), 1)
        text, _, fonts = self.check_render(f)
        for token in ('Heading', 'Strong', 'Emphasis', '(),_+'):
            self.assertIn(token, text)
        self.assertNotIn('unrendered', text)
        self.assertIn(b'NewCMMath', fonts)
        self.assertNotIn(b'AppleGothic', fonts)
        self.check_noop_and_inventory(f, expected_output_journals=1)

    def test_dependency_only_korean_ascii_zero_launch_then_publication_to_absent_pdf(self):
        if not FONT.is_file():
            self.skipTest('Missing reviewed private-local AppleGothic Regular font: ' + str(FONT))
        self.remember_resource(FONT, FONT_SHA256, 15253080)
        f = self.fixture(KOREAN_ROOT, KOREAN_MODULES, present=False)
        self.assertTrue(KOREAN_ROOT.isascii())
        self.assertTrue(all(name.isascii() for name in f.sources))
        refused = self.compile(f, ASCII, status=2)
        self.assertEqual((refused['error_code'], refused['source_commit'], refused['compiler_invoked'],
            refused['compiler_status']), ('unsupported_resource_profile', 'committed', False, None))
        self.check_source_summary(f, refused['source_summary'], KOREAN_EXPECTED,
                                  before=KOREAN_ROOT, changed=True)
        refusal_report = refused['publication_report']
        self.assertEqual((refusal_report['phase'], refusal_report['gate_status'],
            refusal_report['output_disposition'], refusal_report['recovery_required']),
            ('aborted_unpublished', 2, 'untouched', False))
        self.assertEqual(refusal_report['attempted_invocations'], [])
        self.assertIsNone(refusal_report['resource_report'])
        self.assertFalse(refusal_report['resource_details_available'])
        self.assertIsNone(refused['graph_observation'])
        self.assertFalse(refused['graph_observation_available'])
        self.assertFalse(f.output.exists())
        self.assertEqual(f.source.read_bytes(), KOREAN_EXPECTED)
        self.check_source_journal(f, KOREAN_EXPECTED, refused['source_summary']['journal_id'])
        aborted, = self.manifests(OUTPUT_STORE)
        self.assertEqual(aborted.parent.name, refusal_report['journal_id'])
        self.assertEqual([event['state'] for event in self.events(aborted)],
                         ['prepared', 'compile_launch_intent', 'aborted_unpublished'])
        self.check_preserved(f)
        source_before = file_state(f.source)
        old_history = {p: file_state(p) for name in (SOURCE_STORE, OUTPUT_STORE)
                       for p in (self.base / name).rglob('*') if p.is_file()}
        payload = self.compile(f, KOREAN)
        report = self.check_success(f, payload, KOREAN_EXPECTED, KOREAN,
                                    before=KOREAN_EXPECTED, changed=False)
        self.assertIsNone(report['source_journal_id'])  # No-op source, new independent PDF journal.
        self.assertEqual(file_state(f.source), source_before)
        self.assertEqual({p: file_state(p) for p in old_history}, old_history)
        self.assertEqual(len(self.manifests(SOURCE_STORE)), 1)
        self.assertEqual(len(self.manifests(OUTPUT_STORE)), 2)
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
        # Only plain Regular is characterized; headings/strong/emph are not Korean styling proof.
        self.check_noop_and_inventory(f, expected_output_journals=2)


if __name__ == '__main__':
    unittest.main()
