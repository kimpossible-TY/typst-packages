"""Explicit binary capture and refusal boundaries for document resources."""
from pathlib import Path
from dataclasses import replace
import os
import shutil
import subprocess
import sys
import tempfile
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

    def test_untyped_or_unselected_roots_and_roles_are_refused(self):
        for request in (resources.ResourceRequest([], 'image.png', 'resource'),
                        resources.ResourceRequest('other', 'image.png', 'resource'),
                        resources.ResourceRequest('project', 'image.png', []),
                        resources.ResourceRequest('project', 'image.png', 'unknown'),
                        ('project', 'image.png', 'resource')):
            with self.subTest(request=request), self.assertRaises(SafetyError):
                resources.capture(self.roots, [request])

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
