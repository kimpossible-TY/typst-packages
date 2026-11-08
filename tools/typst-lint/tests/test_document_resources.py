"""Explicit binary capture and refusal boundaries for document resources."""
from pathlib import Path
from dataclasses import replace
import os
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_resources as resources
import document_sequence_preview
import include_context
from typst_lint import SafetyError, digest, load_rules


class DocumentResourceCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'fonts').mkdir()
        (self.root / 'fonts' / 'sample.otf').write_bytes(b'\x00\xfffont\x80')
        (self.root / 'image.png').write_bytes(b'\x89PNG\x00\xff')
        self.roots = {'project': str(self.root)}

    def req(self, path, role='resource'):
        return resources.ResourceRequest('project', path, role)

    def test_binary_and_font_bytes_are_retained_without_completeness_claim(self):
        snapshot = resources.capture(self.roots, [self.req('image.png'), self.req('fonts/sample.otf', 'font')])
        self.assertEqual([item.data for item in snapshot.files], [b'\x89PNG\x00\xff', b'\x00\xfffont\x80'])
        self.assertEqual(snapshot.files[1].sha256, digest(snapshot.files[1].data))
        self.assertEqual(snapshot.total_bytes, 13)
        self.assertFalse(snapshot.complete_dependency_closure or snapshot.compiler_authority)
        self.assertNotIn('font\\x80', repr(snapshot))

    def test_missing_symlink_and_hardlink_are_refused(self):
        (self.root / 'symlink.png').symlink_to(self.root / 'image.png')
        (self.root / 'hardlink.png').hardlink_to(self.root / 'image.png')
        for path in ('missing.png', 'symlink.png', 'hardlink.png'):
            with self.subTest(path=path), self.assertRaises(SafetyError):
                resources.capture(self.roots, [self.req(path)])

    def test_duplicate_collision_and_unsafe_paths_are_refused(self):
        for paths in (('image.png', 'image.png'), ('image.png', 'IMAGE.png'),
                      ('../image.png',), ('/image.png',), ('fonts//sample.otf',)):
            with self.subTest(paths=paths), self.assertRaises(SafetyError):
                resources.capture(self.roots, [self.req(path) for path in paths])

    def test_same_file_cannot_be_captured_via_overlapping_roots(self):
        roots = {'project': str(self.root), 'fonts': str(self.root / 'fonts')}
        requests = [self.req('fonts/sample.otf', 'font'),
                    resources.ResourceRequest('fonts', 'sample.otf', 'font')]
        with self.assertRaises(SafetyError):
            resources.capture(roots, requests)

    def test_root_ids_and_directory_components_cannot_alias_in_private_staging(self):
        with self.assertRaises(SafetyError):
            resources.capture({'project': str(self.root), 'PROJECT': str(self.root)}, [self.req('image.png')])
        with self.assertRaises(SafetyError):
            resources.capture(self.roots, [self.req('fonts/sample.otf', 'font'),
                                           self.req('FONTS/other.otf', 'font')])
        with self.assertRaises(SafetyError):
            resources.capture({'a' * 65: str(self.root)},
                              [resources.ResourceRequest('a' * 65, 'image.png', 'resource')])

    def test_untyped_or_unselected_roots_and_roles_are_refused(self):
        for request in (resources.ResourceRequest([], 'image.png', 'resource'),
                        resources.ResourceRequest('other', 'image.png', 'resource'),
                        resources.ResourceRequest('project', 'image.png', []),
                        resources.ResourceRequest('project', 'image.png', 'unknown'),
                        ('project', 'image.png', 'resource')):
            with self.subTest(request=request), self.assertRaises(SafetyError):
                resources.capture(self.roots, [request])
        for raw in ('/tmp/../tmp', '/tmp/\nname', '/' + 'a' * (resources.MAX_PATH_BYTES + 1), '/tmp/\ud800'):
            with self.subTest(root=repr(raw)), self.assertRaises(SafetyError):
                resources.capture({'project': raw}, [self.req('image.png')])

    def test_byte_and_count_limits_refuse_without_partial_result(self):
        with patch.object(resources, 'MAX_FILES', 1), self.assertRaises(SafetyError):
            resources.capture(self.roots, [self.req('image.png'), self.req('fonts/sample.otf', 'font')])
        with patch.object(resources, 'MAX_TOTAL_BYTES', 5), self.assertRaises(SafetyError):
            resources.capture(self.roots, [self.req('image.png')])
        with patch.object(resources, 'MAX_FILE_BYTES', 5), self.assertRaises(SafetyError):
            resources.capture(self.roots, [self.req('image.png')])

    def test_mutation_before_final_verification_refuses_capture(self):
        original_same = resources.runtime._same
        def mutate(path, evidence, bound):
            path.write_bytes(b'changed')
            return original_same(path, evidence, bound)
        with patch.object(resources.runtime, '_same', side_effect=mutate), self.assertRaises(SafetyError):
            resources.capture(self.roots, [self.req('image.png')])

    def test_capture_refusal_names_request_and_preserves_original_exception(self):
        cause = OSError('underlying fixture refusal')
        error = resources.runtime.CompileSafetyError('Unsafe/unavailable captured file')
        error.__cause__ = cause
        original_report = error.report
        with patch.object(resources.runtime, '_capture', side_effect=error):
            with self.assertRaises(resources.runtime.CompileSafetyError) as caught:
                resources.capture(self.roots, [self.req('fonts/sample.otf', 'font')])
        self.assertIs(caught.exception, error)
        self.assertIs(caught.exception.__cause__, cause)
        self.assertIs(caught.exception.report, original_report)
        self.assertIn('Unsafe/unavailable captured file', str(error))
        self.assertIn('resource root=project, path=fonts/sample.otf, phase=capture', str(error))
        self.assertNotIn('font\\x80', str(error))

    def test_recheck_refusal_names_request_and_preserves_original_exception(self):
        cause = OSError('underlying fixture refusal')
        error = resources.runtime.CompileSafetyError('Captured file identity or bytes changed')
        error.__cause__ = cause
        with patch.object(resources.runtime, '_same', side_effect=error):
            with self.assertRaises(resources.runtime.CompileSafetyError) as caught:
                resources.capture(self.roots, [self.req('image.png')])
        self.assertIs(caught.exception, error)
        self.assertIs(caught.exception.__cause__, cause)
        self.assertIn('resource root=project, path=image.png, phase=recheck', str(error))

    def test_apple_asset_refusals_name_request_in_both_phases(self):
        relative = 'a' * 40 + '.asset/AssetData/fixture.ttf'
        request = resources.ResourceRequest('apple', relative, 'font')
        roots = {'apple': resources.APPLE_ASSET_ROOT}
        initial = SafetyError('fixture apple capture refusal')
        with patch.object(resources.sys, 'platform', 'darwin'), \
             patch.object(resources, '_apple_asset_font', side_effect=initial):
            with self.assertRaises(SafetyError) as caught:
                resources.capture(roots, [request], allow_apple_asset_fonts=True)
        self.assertIs(caught.exception, initial)
        self.assertIn(f'resource root=apple, path={relative}, phase=capture', str(initial))

        raw = b'fixture-font'
        evidence = resources.runtime.FileEvidence(str(Path(resources.APPLE_ASSET_ROOT) / relative),
                                                   (1, 2), digest(raw), len(raw), ())
        recheck = SafetyError('fixture apple recheck refusal')
        with patch.object(resources.sys, 'platform', 'darwin'), \
             patch.object(resources, '_apple_asset_font', side_effect=[(raw, evidence), recheck]):
            with self.assertRaises(SafetyError) as caught:
                resources.capture(roots, [request], allow_apple_asset_fonts=True)
        self.assertIs(caught.exception, recheck)
        self.assertIn(f'resource root=apple, path={relative}, phase=recheck', str(recheck))

    def test_document_staging_binds_source_candidates_to_binary_resources(self):
        main = b'#include "child.typ"\n'
        child = b'Thus $ x^i $\n\nHence.\n'
        (self.root / 'main.typ').write_bytes(main)
        (self.root / 'child.typ').write_bytes(child)
        context = include_context.load_include_context(self.root, (('main.typ', main),))
        catalog = load_rules()[0]['rules']
        child_source = context.source_for('child.typ')
        record = document_sequence_preview.preview(child_source.tree, 'child.typ', catalog)
        requests = [self.req('main.typ', 'source'), self.req('child.typ', 'source'),
                    self.req('image.png'), self.req('fonts/sample.otf', 'font')]
        snapshot = resources.capture(self.roots, requests)
        staged = resources.stage_document(context, [record], catalog, snapshot)
        self.assertEqual(staged['project', 'main.typ'], main)
        self.assertEqual(staged['project', 'child.typ'], b'Thus\n$ x^(i) $\nHence.\n')
        self.assertEqual(staged['project', 'image.png'], b'\x89PNG\x00\xff')
        self.assertEqual(staged['project', 'fonts/sample.otf'], b'\x00\xfffont\x80')
        self.assertEqual((self.root / 'child.typ').read_bytes(), child)
        with self.assertRaises(TypeError):
            staged['project', 'main.typ'] = b'changed'
        with self.assertRaises(SafetyError):
            resources.stage_document(context, [], catalog, snapshot)
        with self.assertRaises(SafetyError):
            resources.stage_document(context, [record], catalog, replace(snapshot))
        missing = resources.capture(self.roots, requests[:1])
        with self.assertRaises(SafetyError):
            resources.stage_document(context, [record], catalog, missing)
        (self.root / 'child.typ').write_bytes(child + b'changed')
        newer = resources.capture(self.roots, requests)
        with self.assertRaises(SafetyError):
            resources.stage_document(context, [record], catalog, newer)

    def test_conditional_profile_is_bound_to_replayed_records(self):
        source = b'#block[\n#proof[Done.]\nNext.\n$\n A #footnote[Reason.]\n\n B\n$\n]'
        (self.root / 'main.typ').write_bytes(source)
        context = include_context.load_include_context(self.root, (('main.typ', source),))
        catalog = load_rules()[0]['rules']
        snapshot = resources.capture(self.roots, [self.req('main.typ', 'source')])
        tree = context.source_for('main.typ').tree
        record = document_sequence_preview.preview(tree, 'main.typ', catalog, profile='conditional-gaps')
        self.assertTrue(record['steps'])
        staged = resources.stage_document(context, [record], catalog, snapshot, profile='conditional-gaps')
        self.assertNotEqual(staged['project', 'main.typ'], source)
        self.assertEqual((self.root / 'main.typ').read_bytes(), source)
        with self.assertRaises(SafetyError):
            resources.stage_document(context, [record], catalog, snapshot)
        with self.assertRaises(SafetyError):
            resources.stage_document(context, [], catalog, snapshot, profile='conditional-gaps')

    def test_reflectively_modified_issued_capture_is_rejected(self):
        snapshot = resources.capture(self.roots, [self.req('image.png')])
        object.__setattr__(snapshot.files[0], 'data', b'changed')
        with self.assertRaises(SafetyError):
            resources.assert_capture(snapshot)

    def test_boolean_lookalike_in_file_identity_is_rejected(self):
        snapshot = resources.capture(self.roots, [self.req('image.png')])
        evidence = snapshot.files[0].evidence
        identity = list(evidence.identity)
        self.assertEqual(identity[3], 1)
        identity[3] = True
        object.__setattr__(evidence, 'identity', tuple(identity))
        with self.assertRaises(SafetyError):
            resources.assert_capture(snapshot)

    def test_explicit_apple_asset_policy_checks_layout_role_owner_and_mode(self):
        asset = self.root / (('a' * 40) + '.asset') / 'AssetData'
        asset.mkdir(parents=True)
        font = asset / 'Sample.otf'
        font.write_bytes(b'\x00\xffasset-font')
        path = font.relative_to(self.root).as_posix()
        request = self.req(path, 'font')
        with patch.object(resources, 'APPLE_ASSET_ROOT', str(self.root)), \
             patch.object(resources, '_APPLE_TRUST_PREFIX', str(self.root)), \
             patch.object(resources.sys, 'platform', 'darwin'), \
             patch.object(resources.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=os.getuid())):
            snapshot = resources.capture(self.roots, [request], allow_apple_asset_fonts=True)
            self.assertTrue(snapshot.apple_asset_fonts)
            self.assertEqual(snapshot.files[0].data, b'\x00\xffasset-font')
            self.assertIs(resources.assert_capture(snapshot), snapshot)
            for invalid in (self.req(path, 'resource'), self.req('fonts/sample.otf', 'font')):
                with self.subTest(invalid=invalid), self.assertRaises(SafetyError):
                    resources.capture(self.roots, [invalid], allow_apple_asset_fonts=True)
            font.chmod(0o666)
            with self.assertRaises(SafetyError):
                resources.capture(self.roots, [request], allow_apple_asset_fonts=True)
            font.chmod(0o644)
            with patch.object(resources.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=os.getuid() + 10000)):
                with self.assertRaises(SafetyError):
                    resources.capture(self.roots, [request], allow_apple_asset_fonts=True)

    def test_apple_asset_font_change_between_reads_is_rejected(self):
        asset = self.root / (('b' * 40) + '.asset') / 'AssetData'
        asset.mkdir(parents=True)
        font = asset / 'Sample.ttf'
        font.write_bytes(b'original')
        request = self.req(font.relative_to(self.root).as_posix(), 'font')
        original = resources._apple_asset_font
        calls = []
        def mutate(path, relative, bound):
            calls.append(1)
            if len(calls) == 2:
                path.write_bytes(b'modified')
            return original(path, relative, bound)
        with patch.object(resources, 'APPLE_ASSET_ROOT', str(self.root)), \
             patch.object(resources, '_APPLE_TRUST_PREFIX', str(self.root)), \
             patch.object(resources.sys, 'platform', 'darwin'), \
             patch.object(resources.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=os.getuid())), \
             patch.object(resources, '_apple_asset_font', side_effect=mutate):
            with self.assertRaises(SafetyError):
                resources.capture(self.roots, [request], allow_apple_asset_fonts=True)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1'
                         and sys.platform == 'darwin' and shutil.which('fc-list'),
                         'Opt in on macOS with fontconfig available')
    def test_available_macos_mobileasset_font_is_captured_without_distribution(self):
        result = subprocess.run(['fc-list', '-f', '%{postscriptname}\t%{file}\n'],
                                check=True, capture_output=True, text=True, timeout=30)
        matches = [line.split('\t', 1)[1] for line in result.stdout.splitlines()
                   if line.startswith('Klee-Medium\t')]
        root = Path(resources.APPLE_ASSET_ROOT)
        matches = [Path(path) for path in matches if Path(path).is_relative_to(root)]
        if len(matches) != 1:
            self.skipTest('A single Klee MobileAsset font is not available')
        request = resources.ResourceRequest('assets', matches[0].relative_to(root).as_posix(), 'font')
        snapshot = resources.capture({'assets': str(root)}, [request], allow_apple_asset_fonts=True)
        self.assertTrue(snapshot.apple_asset_fonts)
        self.assertGreater(snapshot.files[0].size, 0)
        self.assertFalse(snapshot.compiler_authority)

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with TYPST_LINT_RENDER_TESTS=1 and Typst')
    def test_captured_package_and_candidate_compile_in_private_fixture(self):
        project = self.root / 'project'
        package_root = self.root / 'packages'
        package = package_root / 'local' / 'sample' / '0.1.0'
        project.mkdir()
        package.mkdir(parents=True)
        main = b'#import "@local/sample:0.1.0": label\n#label\n#include "child.typ"\n'
        child = b'Thus $ x^i $\n\nHence.\n'
        (project / 'main.typ').write_bytes(main)
        (project / 'child.typ').write_bytes(child)
        (package / 'typst.toml').write_bytes(b'[package]\nname = "sample"\nversion = "0.1.0"\nentrypoint = "lib.typ"\n')
        (package / 'lib.typ').write_bytes(b'#let label = [A label.]\n')
        context = include_context.load_include_context(project, (('main.typ', main),))
        catalog = load_rules()[0]['rules']
        records = [document_sequence_preview.preview(s.tree, s.path, catalog) for s in context.sources]
        records = [record for record in records if record is not None]
        requests = [resources.ResourceRequest('project', path, 'source') for path in ('main.typ', 'child.typ')]
        requests += [resources.ResourceRequest('packages', f'local/sample/0.1.0/{name}', 'package')
                     for name in ('typst.toml', 'lib.typ')]
        captured = resources.capture({'project': str(project), 'packages': str(package_root)}, requests)
        candidates = resources.stage_document(context, records, catalog, captured)
        staging = self.root / 'staging'
        roots = {'project': staging / 'project', 'packages': staging / 'packages'}
        for base in roots.values():
            base.mkdir(parents=True)
        cache = staging / 'cache'
        cache.mkdir()
        for item in captured.files:
            target = roots[item.request.root] / item.request.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(item.data)
        def compile():
            pdf = staging / 'output.pdf'
            subprocess.run(['typst', 'compile', '--root', str(roots['project']),
                            '--package-path', str(roots['packages']),
                            '--package-cache-path', str(cache), '--ignore-system-fonts',
                            '--creation-timestamp', '1700000000',
                            str(roots['project'] / 'main.typ'), str(pdf)],
                           check=True, capture_output=True, timeout=30)
            return pdf.read_bytes()
        before = compile()
        for (name, path), data in candidates.items():
            (roots[name] / path).write_bytes(data)
        after = compile()
        self.assertEqual(before, after)
        self.assertEqual((project / 'child.typ').read_bytes(), child)
