"""Opt-in native acceptance through the actual paragraph public launcher.

The production default ten-second compile budget and pinned runtime are used
without test injection. All consumers, packages, journals, PDFs and rasters are
private temporary copies. Only embedded fonts are admitted; no font is copied,
committed, uploaded or required. The older independent BEFORE/AFTER baseline is
not rerun here. This file observes the genuine CLI adapter's retained evidence.
"""
import base64
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

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paragraph_fix_test_support import fixture, evidence
from syntax_client import EXECUTABLE
import test_captured_text_utils_paragraph_native as pdf_inspection
from test_captured_text_utils_paragraph_native import (
    PINNED, PINNED_SHA256,
    MANIFEST_SHA256, PACKAGE_SHA256, CATALOG_SHA256, PDF_HELPERS, ROWS,
    admitted_source, digest, file_state)

PROFILE = 'captured-text-utils-paragraph-v1'
LAUNCHER = TOOL / 'typst-lint'
OLD_PDF = b'private original PDF sentinel\x00\xff\r\n'


def expected_source(source):
    for _, _, _, word, label in ROWS:
        source = source.replace(word + b' ' + label, word[:1].upper() + word[1:] + b' ' + label)
    return source


@unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1',
    'Opt in to private native paragraph CLI with TYPST_LINT_RENDER_TESTS=1')
class NativeCapturedTextUtilsParagraphCliTests(unittest.TestCase):
    def setUp(self):
        if sys.platform != 'darwin' or platform.machine() != 'arm64':
            self.skipTest('Reviewed native paragraph CLI requires Darwin arm64')
        if not EXECUTABLE.is_file() or not PINNED.is_file():
            self.skipTest('Missing built pinned syntax adapter or reviewed Typst 0.15.1')
        self.helpers = {name: shutil.which(name) for name in PDF_HELPERS}
        if not all(self.helpers.values()):
            self.skipTest('Missing installed PDF inspection helpers')
        self.resources = {}
        for path, expected, size in (
                (PINNED, PINNED_SHA256, 44694736),
                ((TOOL / 'tests/fixtures/reviewed-packages') / 'local/text-utils/0.1.2/typst.toml', MANIFEST_SHA256, 173),
                ((TOOL / 'tests/fixtures/reviewed-packages') / 'local/text-utils/0.1.2/lib.typ', PACKAGE_SHA256, 5583),
                (TOOL / 'rules/catalog.json', CATALOG_SHA256, None),
                (EXECUTABLE, None, None), (LAUNCHER, None, None)):
            before = file_state(path)
            if expected is not None:
                self.assertEqual(before[0], expected, str(path))
            if size is not None:
                self.assertEqual(before[5], size, str(path))
            self.resources[path] = before
        temporary = tempfile.TemporaryDirectory(prefix='typst-paragraph-cli-native-')
        self.base = Path(temporary.name).resolve()
        self.base.chmod(0o700)
        def cleanup():
            temporary.cleanup()
            self.assertFalse(self.base.exists(), 'Private native artifacts were not cleaned')
        self.addCleanup(cleanup)
        self.addCleanup(self.assert_resources_current)
        self.sequence = 0

    assert_resources_current = pdf_inspection.NativeCapturedTextUtilsParagraphTests.assert_resources_current
    helper = pdf_inspection.NativeCapturedTextUtilsParagraphTests.helper
    inspect_pdf = pdf_inspection.NativeCapturedTextUtilsParagraphTests.inspect_pdf
    paragraph_boxes = pdf_inspection.NativeCapturedTextUtilsParagraphTests.paragraph_boxes

    def fixture(self, source, *, present):
        self.sequence += 1
        f = fixture(self.base, self.sequence, source=source,
                    target='nested source with spaces/paragraph file.typ')
        f.destination = 'build with spaces/nested/paragraph file.pdf'
        f.output = f.root / f.destination
        f.output.parent.mkdir(parents=True, mode=0o700)
        f.present = present
        if present:
            f.output.write_bytes(OLD_PDF)
            f.output.chmod(0o644)
        namespace = 'root-' + digest(os.fsencode(f.root))
        f.source_namespace = f.base / '.typst-lint-journals' / namespace
        f.output_namespace = f.base / '.typst-lint-pdf-journals' / namespace
        f.preserved = {path: file_state(path) for path in evidence(f)}
        return f

    def invoke(self, f, command='compile', *, status=0):
        args = [command, '--root', str(f.root), '--format', 'json']
        if command in ('fix', 'compile'):
            args += ['--profile', PROFILE, '--offline-exclusive', '--target', f.target,
                     '--package-path', str(f.package_path)]
        if command == 'compile':
            args += ['--resource-profile', 'embedded-ascii-v1', '--offline-output-exclusive',
                     '--output', f.destination]
        self.assert_resources_current()
        result = subprocess.run([str(LAUNCHER), *args], cwd=f.root,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), capture_output=True, timeout=60)
        self.assertEqual((result.returncode, result.stderr), (status, b''), result.stdout)
        self.assertTrue(result.stdout.isascii())
        report = json.loads(result.stdout)
        self.assertEqual((report['schema'], report['status']), (1, status))
        self.assertEqual({path: file_state(path) for path in f.preserved}, f.preserved)
        self.assert_resources_current()
        return report

    def manifests(self, f, *, source=False):
        return sorted((f.source_namespace if source else f.output_namespace).glob('*/manifest.json'))

    def events(self, manifest):
        return [json.loads(path.read_bytes()) for path in sorted(manifest.parent.glob('event-*.json'))]

    def assert_summary(self, f, report, original, expected, *, changed):
        summary = report['source_summary'] if report['command'] == 'compile' else report
        self.assertEqual((summary['profile'], summary['root_grammar_version']),
                         (PROFILE, 'captured-text-utils-paragraph-grammar-v1'))
        self.assertEqual(summary['parser_contract'], ['typst-syntax:0.15.1', 'adapter:0.2.0', 'schema:2'])
        self.assertEqual((summary['original_sha256'], summary['final_sha256']),
                         (digest(original), digest(expected)))
        self.assertEqual(summary['modified_paths'], [f.target] if changed else [])
        self.assertEqual(summary['package_sha256'], PACKAGE_SHA256)
        self.assertEqual({item['path']: (item['sha256'], item['size'])
                          for item in summary['package_identity']['files']},
                         {str(f.manifest): (MANIFEST_SHA256, 173),
                          str(f.package): (PACKAGE_SHA256, 5583)})
        self.assertEqual(len(summary['required_rule_ids']), 33)
        self.assertEqual(len(summary['evaluated_rule_ids']), 5)
        self.assertEqual(len(summary['not_applicable']), 28)
        self.assertFalse(summary['coverage']['global_complete'])
        self.assertEqual(summary['coverage']['global_partial_rule_count'], 33)
        replay = original
        edits = summary['applied_edits']
        self.assertEqual(len(edits), len(ROWS) if changed else 0)
        for edit in sorted(edits, key=lambda item: item['start'], reverse=True):
            self.assertEqual((edit['path'], edit['end'], edit['rule_id']),
                             (f.target, edit['start'] + 1, 'pde.paragraph-capitalization'))
            self.assertEqual(len(edit['replacement'].encode()), 1)
            self.assertEqual(edit['replacement'].encode(), replay[edit['start']:edit['end']].upper())
            replay = replay[:edit['start']] + edit['replacement'].encode() + replay[edit['end']:]
        self.assertEqual(replay, expected)
        self.assertEqual(f.source.read_bytes(), expected)
        self.assertEqual(stat.S_IMODE(f.source.stat().st_mode), 0o640)
        if changed:
            manifest, = self.manifests(f, source=True)
            data = json.loads(manifest.read_bytes())
            self.assertEqual((data['schema'], data['root']), (1, str(f.root)))
            record, = data['files']
            self.assertEqual((record['path'], base64.b64decode(record['before']),
                base64.b64decode(record['after'])), (f.target, original, expected))
            self.assertEqual(self.events(manifest)[-1]['kind'], 'committed')
            self.assertEqual(manifest.parent.name, summary['journal_id'])
        else:
            self.assertIsNone(summary['journal_id'])
        return summary

    def assert_publication(self, f, report, expected):
        self.assertEqual((report['phase'], report['source_commit'], report['compiler_invoked'],
                          report['compiler_status']), ('completed', 'committed', True, 0))
        self.assertEqual(report['source_recovery']['state'], 'not_required')
        self.assertEqual(report['output_recovery']['state'], 'not_required')
        publication = report['publication_report']
        self.assertEqual((publication['phase'], publication['output_disposition'],
                          publication['recovery_required']), ('published', 'published', False))
        self.assertEqual(publication['source_journal_id'], report['source_summary']['journal_id'])
        raw = f.output.read_bytes()
        self.assertEqual(publication['observed_sha256'], digest(raw))
        attempts = publication['attempted_invocations']
        self.assertEqual(attempts, report['compile_report']['attempted_invocations'])
        self.assertEqual([(item['purpose'], item['returncode'], item['lifecycle'], item['started'])
                          for item in attempts], [('version-probe', 0, 'exited', True), ('compile', 0, 'exited', True)])
        self.assertEqual(bytes.fromhex(attempts[0]['stdout_hex']), b'typst 0.15.1 (unknown commit)\n')
        for item in attempts:
            self.assertTrue(item['launch_attempted'])
            self.assertEqual(item['stderr_hex'], '')
        compile_attempt = attempts[-1]
        argv, cwd = compile_attempt['argv'], Path(compile_attempt['cwd'])
        self.assertIn('--ignore-system-fonts', argv)
        self.assertNotIn('--font-path', argv)
        self.assertEqual(argv[argv.index('--deps-format') + 1], 'zero')
        self.assertEqual(Path(argv[argv.index('--root') + 1]), cwd)
        self.assertEqual(Path(argv[-2]), cwd / f.target)
        package_root = Path(argv[argv.index('--package-path') + 1])
        package_leaf = package_root / 'local/text-utils/0.1.2'
        observation = report['package_observation']
        self.assertTrue(report['package_observation_available'])
        self.assertEqual((observation['source_profile'], observation['root_grammar_version'],
                          observation['parser_contract']), (PROFILE,
                          'captured-text-utils-paragraph-grammar-v1',
                          ['typst-syntax:0.15.1', 'adapter:0.2.0', 'schema:2']))
        self.assertEqual({item['path']: item['sha256'] for item in observation['evidence_hashes']},
                         {str(f.config): digest(f.config.read_bytes()),
                          str(TOOL / 'rules/catalog.json'): CATALOG_SHA256,
                          str(f.manifest): MANIFEST_SHA256, str(f.package): PACKAGE_SHA256})
        root_source = observation['root_source']
        self.assertEqual((root_source['path'], root_source['sha256'], root_source['size'],
                          root_source['private']['path']),
                         ('project/' + f.target, digest(expected), len(expected), str(cwd / f.target)))
        sources = observation['package_sources']
        self.assertEqual({item['path']: (item['sha256'], item['size']) for item in sources},
                         {'packages/local/text-utils/0.1.2/typst.toml': (MANIFEST_SHA256, 173),
                          'packages/local/text-utils/0.1.2/lib.typ': (PACKAGE_SHA256, 5583)})
        for source in [root_source, *sources]:
            private = source['private']
            self.assertEqual((private['sha256'], private['size']), (source['sha256'], source['size']))
            self.assertTrue(private['identity'] and private['parents'])
            self.assertFalse(Path(private['path']).exists())
        self.assertEqual({item['path'] for item in observation['package_directories']},
                         {str(package_root), str(package_root / 'local'),
                          str(package_root / 'local/text-utils'), str(package_leaf)})
        observed = observation['dependency_paths']
        # These values are ORIGINAL compiler observations. Expected membership is
        # checked separately, and the observed order is used to verify raw hash.
        self.assertEqual(len(observed), 3)
        self.assertEqual(set(observed), {f.target, str(package_leaf / 'lib.typ'),
                                        str(package_leaf / 'typst.toml')})
        depraw = b'\0'.join(os.fsencode(path) for path in observed) + b'\0'
        deps = observation['dependencies']
        self.assertEqual((deps['sha256'], deps['size']), (digest(depraw), len(depraw)))
        self.assertEqual(deps['path'], argv[argv.index('--deps') + 1])
        self.assertTrue(deps['identity'] and deps['parents'])
        self.assertEqual(publication['resource_report']['fonts'], [])
        self.assertEqual(publication['resource_report']['profile'], 'embedded-ascii-v1')
        self.assertFalse(cwd.parent.exists(), 'Private compiler workspace remains')
        for item in attempts:
            self.assertFalse(Path(item['argv'][0]).exists())
        manifest, = self.manifests(f)
        data = json.loads(manifest.read_bytes())
        self.assertEqual((data['schema'], data['kind'], data['destination']),
                         (1, 'typst-pdf-publication', f.destination))
        self.assertEqual(data['old']['sha256'] if f.present else data['old'], digest(OLD_PDF) if f.present else None)
        events = self.events(manifest)
        self.assertEqual([event['state'] for event in events], ['prepared', 'compile_launch_intent',
            'compiled', 'staged', 'replace_intent', 'replaced', 'published'])
        self.assertEqual((events[2]['compiler_sha256'], events[2]['pdf_sha256'],
            events[-1]['postimage']['sha256']), (PINNED_SHA256, digest(raw), digest(raw)))
        self.assertEqual(stat.S_IMODE(f.output.stat().st_mode), 0o644 if f.present else 0o600)
        environment = dict(os.environ, LANG='C', LC_ALL='C', TZ='UTC')
        pdf = self.inspect_pdf(f.output, environment)
        paragraphs = self.paragraph_boxes(pdf)
        self.assertEqual([item[0] for item in paragraphs[0]], ['Baseline', 'anchor', 'remains.'])
        for paragraph, (_, _, _, word, label) in zip(paragraphs[1:], ROWS):
            self.assertEqual([item[0] for item in paragraph], [word.capitalize().decode(),
                label.decode(), 'preserves', 'iPhone', 'eBay', 'NASA', 'lowerCase.'])
            self.assertAlmostEqual(paragraph[0][1][0] - paragraphs[0][0][1][0], 16.5, delta=.05)
        self.assertEqual({spec['size'] for spec in pdf['fontspecs']}, {'11'})
        names = {line.split()[0].split('+', 1)[-1] for line in pdf['fonts'].splitlines()[2:] if line.strip()}
        self.assertEqual(names, {'LibertinusSerif-Regular-Identity-H'})
        self.assertNotIn('\uf000', pdf['text'])
        print('PARAGRAPH_CLI_NATIVE_OBSERVATION ' + json.dumps(dict(
            target=f.target, source_sha256=digest(expected), source_journal_id=publication['source_journal_id'],
            output_journal_id=publication['journal_id'], output_sha256=digest(raw),
            dependency_paths=observed, dependency_sha256=deps['sha256'],
            compiler_sha256=events[2]['compiler_sha256'], pdf=pdf), sort_keys=True), flush=True)
        return pdf

    def test_public_compile_repairs_lf_and_replaces_existing_pdf(self):
        original = admitted_source(b'\n')
        expected = expected_source(original)
        f = self.fixture(original, present=True)
        before_source, before_pdf = file_state(f.source), file_state(f.output)
        report = self.invoke(f)
        self.assert_summary(f, report, original, expected, changed=True)
        self.assert_publication(f, report, expected)
        self.assertNotEqual(file_state(f.source)[2], before_source[2])
        self.assertNotEqual(file_state(f.output)[2], before_pdf[2])
        before = file_state(f.source)
        again = self.invoke(f, 'fix')
        self.assert_summary(f, again, expected, expected, changed=False)
        self.assertFalse(again['compiler_invoked'])
        self.assertEqual(file_state(f.source), before)

    def test_source_only_crlf_fix_noop_then_compile_to_absent_pdf_keeps_source_identity(self):
        original = admitted_source(b'\r\n')
        expected = expected_source(original)
        f = self.fixture(original, present=False)
        fixed = self.invoke(f, 'fix')
        self.assert_summary(f, fixed, original, expected, changed=True)
        self.assertFalse(fixed['compiler_invoked'])
        self.assertFalse(f.output.exists())
        before = file_state(f.source)
        no_op = self.invoke(f, 'fix')
        self.assert_summary(f, no_op, expected, expected, changed=False)
        self.assertEqual(file_state(f.source), before)
        report = self.invoke(f)
        self.assert_summary(f, report, expected, expected, changed=False)
        self.assert_publication(f, report, expected)
        self.assertEqual(file_state(f.source), before)
        self.assertEqual(len(self.manifests(f, source=True)), 1)

    def test_real_ascii_resource_refusal_keeps_old_pdf_and_committed_source(self):
        original = admitted_source(b'\n').replace(b'lowerCase.', 'lowerCase 한글.'.encode())
        expected = expected_source(original)
        f = self.fixture(original, present=True)
        before = file_state(f.output)
        report = self.invoke(f, status=2)
        self.assert_summary(f, report, original, expected, changed=True)
        self.assertEqual((report['error_code'], report['source_commit'], report['compiler_invoked'],
                          report['compiler_status']), ('unsupported_resource_profile', 'committed', False, None))
        self.assertEqual(report['publication_report']['attempted_invocations'], [])
        self.assertEqual(report['publication_report']['output_disposition'], 'untouched')
        self.assertFalse(report['publication_report']['recovery_required'])
        self.assertEqual(file_state(f.output), before)


if __name__ == '__main__':
    unittest.main()
