"""Bounded dependency-evidence parsing; no filesystem access is inferred."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_dependencies as deps
import document_resources as resources
from typst_lint import SafetyError


class DocumentDependencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.project = self.base / 'project'
        self.packages = self.base / 'packages'
        self.project.mkdir()
        self.packages.mkdir()
        self.roots = {'project': str(self.project), 'packages': str(self.packages)}

    def parse(self, raw, *, roots=None):
        return deps.parse(raw, cwd=str(self.project), roots=self.roots if roots is None else roots)

    def test_relative_and_absolute_dependencies_are_classified(self):
        raw = b'main.typ\0../packages/module.typ\0' + str(self.project / 'fig.png').encode() + b'\0'
        self.assertEqual(self.parse(raw), (('project', 'main.typ'), ('packages', 'module.typ'),
                                          ('project', 'fig.png')))

    def test_malformed_duplicate_ambiguous_and_escaping_paths_are_refused(self):
        for raw in (b'', b'main.typ', b'\0', b'main.typ\0\0', b'\xff\0',
                    b'main.typ\0main.typ\0', b'main.typ\0MAIN.TYP\0',
                    b'../../outside.typ\0', b'unsafe/../main.typ\0', b'./main.typ\0',
                    b'/tmp/../outside\0', b'foo//bar\0'):
            with self.subTest(raw=raw), self.assertRaises(SafetyError):
                self.parse(raw)
        nested = {'project': str(self.project), 'nested': str(self.project / 'nested')}
        with self.assertRaises(SafetyError):
            self.parse(b'nested/file.typ\0', roots=nested)

    def test_dependency_limits_and_types_are_enforced(self):
        with self.assertRaises(SafetyError):
            self.parse(bytearray(b'main.typ\0'))
        with patch.object(deps, 'MAX_FILES', 1), self.assertRaises(SafetyError):
            self.parse(b'main.typ\0child.typ\0')
        with patch.object(deps, 'MAX_DEPENDENCY_BYTES', 3), self.assertRaises(SafetyError):
            self.parse(b'main.typ\0')
        with patch.object(deps, 'MAX_PATH_BYTES', 3), self.assertRaises(SafetyError):
            self.parse(b'main.typ\0')

    def test_compare_detects_uncaptured_and_unobserved_files(self):
        (self.project / 'main.typ').write_bytes(b'Hello.')
        (self.packages / 'module.typ').write_bytes(b'World.')
        snapshot = resources.capture(self.roots, [resources.ResourceRequest('project', 'main.typ', 'source'),
                                                  resources.ResourceRequest('packages', 'module.typ', 'package')])
        result = deps.compare(snapshot, b'main.typ\0missing.png\0', cwd=str(self.project), roots=self.roots)
        self.assertEqual(result.missing, (('project', 'missing.png'),))
        self.assertEqual(result.unobserved, (('packages', 'module.typ'),))
        self.assertFalse(result.compiler_origin_proved or result.exhaustive_os_reads_proved)
        with self.assertRaises(SafetyError):
            deps.compare(snapshot, b'main.typ\0', cwd=str(self.project), roots={'project': str(self.project)})

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1 and Typst')
    def test_actual_compiler_dependency_output_matches_captured_package(self):
        main = b'#import "@local/sample:0.1.0": text-value\n#text-value\n'
        (self.project / 'main.typ').write_bytes(main)
        package = self.packages / 'local' / 'sample' / '0.1.0'
        package.mkdir(parents=True)
        (package / 'typst.toml').write_bytes(b'[package]\nname = "sample"\nversion = "0.1.0"\nentrypoint = "lib.typ"\n')
        (package / 'lib.typ').write_bytes(b'#let text-value = [Hello.]\n')
        cache = self.base / 'cache'
        cache.mkdir()
        roots = {**self.roots, 'cache': str(cache)}
        requests = [resources.ResourceRequest('project', 'main.typ', 'source')]
        requests += [resources.ResourceRequest('packages', f'local/sample/0.1.0/{name}', 'package')
                     for name in ('typst.toml', 'lib.typ')]
        snapshot = resources.capture(roots, requests)
        output, evidence = self.base / 'output.pdf', self.base / 'deps'
        subprocess.run(['typst', 'compile', '--root', str(self.project),
                        '--package-path', str(self.packages), '--package-cache-path', str(cache),
                        '--ignore-system-fonts', '--deps', str(evidence), '--deps-format', 'zero',
                        'main.typ', str(output)], cwd=self.project, check=True, capture_output=True, timeout=30)
        result = deps.compare(snapshot, evidence.read_bytes(), cwd=str(self.project), roots=roots)
        self.assertFalse(result.missing or result.unobserved)
        self.assertEqual(len(result.observed), 3)
        self.assertFalse(result.compiler_origin_proved or result.exhaustive_os_reads_proved)
