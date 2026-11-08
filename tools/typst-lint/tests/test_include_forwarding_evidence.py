"""Conditional include forwarding is not runtime or write authority."""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))
import include_context
import document_resources
import include_forwarding_evidence as forwarding
from syntax_client import EXECUTABLE
from typst_lint import InputError, SafetyError


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires the pinned syntax adapter')
class IncludeForwardingEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='include-forwarding-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.project, self.packages, self.cache = (root / name for name in ('project', 'packages', 'cache'))
        for folder in (self.project, self.packages, self.cache):
            folder.mkdir()
        self.install('math-book', '0.2.0')

    def install(self, name, version):
        target = self.packages / 'local' / name / version
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(REPO / 'local' / name / version, target)

    def context(self, source, extra=None):
        (self.project / 'main.typ').write_bytes(source)
        (self.project / 'child.typ').write_bytes(b'#metadata("probe")')
        for name, content in (extra or {}).items():
            (self.project / name).write_bytes(content)
        return include_context.load_include_context(self.project, (('main.typ', source),))

    def snapshot(self, context, *, selected='packages', extra=()):
        roots = dict(project=str(self.project), packages=str(self.packages), cache=str(self.cache))
        requests = [document_resources.ResourceRequest('project', item.path, 'source') for item in context.sources]
        requests += [document_resources.ResourceRequest(selected, 'local/math-book/0.2.0/' + name, 'package')
                     for name in ('typst.toml', 'lib.typ')]
        requests.extend(extra)
        return document_resources.capture(roots, requests)

    def inspect(self, source):
        context = self.context(source)
        result = forwarding.analyze(context, self.packages, snapshot=self.snapshot(context))
        self.assertTrue(result['original_unresolved_retained'])
        self.assertTrue(result['resource_capture_bound'])
        for name in ('runtime_execution_proved', 'runtime_package_selection_proved',
                     'coverage_complete', 'edit_permission', 'compiler_permission', 'publication_authority'):
            self.assertFalse(result[name], (name, result))
        return context, result

    def test_direct_book_and_context_bodies_are_conditional_and_keep_codes(self):
        source = (b'#import "@local/math-book:0.2.0": book-part\n'
                  b'#book-part(prefix: "A", single-chapter: true)[#include "child.typ"]\n'
                  b'#context { [#include "child.typ"] }')
        context, result = self.inspect(source)
        self.assertEqual(result['unresolved_sites_before'], 2)
        self.assertEqual(result['unresolved_occurrences_before'], 2)
        self.assertEqual(result['recognized_site_count'], 2)
        self.assertEqual(result['conditionally_explained_occurrences'], 2)
        self.assertEqual({row['evidence_kind'] for row in result['sites']},
                         {'reviewed-book-part-literal-body', 'direct-context-literal-content'})
        self.assertTrue(all(row['conditions'] and row['retained_unresolved_codes'] for row in result['sites']))
        self.assertEqual(len(context.unresolved), 2)
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)

    def test_incomplete_binding_nested_calls_and_dynamic_options_remain_unresolved(self):
        direct = b'#import "@local/math-book:0.2.0": book-part\n'
        cases = (b'#book-part[#include "child.typ"]',
                 direct + b'#let book-part = none\n#book-part[#include "child.typ"]',
                 direct + b'#let ignored = { book-part = none; none }\n#book-part[#include "child.typ"]',
                 direct + b'#let ignored = { import "other.typ": *; none }\n#book-part[#include "child.typ"]',
                 direct + b'#import "other.typ": *\n#book-part[#include "child.typ"]',
                 direct + b'#book-part(prefix: dynamic)[#include "child.typ"]',
                 direct + b'#book-part[#box[#include "child.typ"]]',
                 b'#let saved = [#context { [#include "child.typ"] }]')
        for source in cases:
            with self.subTest(source=source):
                self.context(source, {'other.typ': b'#let other = none'})
                context = include_context.load_include_context(self.project, (('main.typ', source),))
                result = forwarding.analyze(context, self.packages, snapshot=self.snapshot(context))
                self.assertFalse(result['sites'])
                self.assertTrue(result['unresolved_sites_before'])

    def test_repeated_includes_have_distinct_sites_and_occurrences(self):
        source = (b'#import "@local/math-book:0.2.0": book-part\n'
                  b'#book-part[#include "child.typ"\n#include "child.typ"]')
        _, result = self.inspect(source)
        self.assertEqual(result['recognized_site_count'], 2)
        self.assertEqual(result['conditionally_explained_occurrences'], 2)
        self.assertNotEqual(result['sites'][0]['start'], result['sites'][1]['start'])

    def test_unissued_or_modified_context_and_package_are_rejected(self):
        source = b'#import "@local/math-book:0.2.0": book-part\n#book-part[#include "child.typ"]'
        context = self.context(source)
        snapshot = self.snapshot(context)
        with self.assertRaises(include_context.IncludeCaptureError):
            forwarding.analyze(replace(context), self.packages, snapshot=snapshot)
        with self.assertRaises(SafetyError):
            forwarding.analyze(context, self.packages, snapshot=replace(snapshot))
        package = self.packages / 'local/math-book/0.2.0/lib.typ'
        package.write_bytes(package.read_bytes() + b'\n')
        # The captured bytes remain usable as conditional evidence; this makes
        # no assertion about the live package's later freshness.
        self.assertEqual(forwarding.analyze(context, self.packages, snapshot=snapshot)['status'], 'CONDITIONAL')
        result = forwarding.analyze(context, self.packages, snapshot=self.snapshot(context))
        self.assertEqual(result['status'], 'UNAVAILABLE')
        self.assertFalse(result['sites'] or result['resource_capture_bound'])

    def test_changed_capture_during_analysis_discards_evidence(self):
        source = b'#import "@local/math-book:0.2.0": book-part\n#book-part[#include "child.typ"]'
        context = self.context(source)
        snapshot = self.snapshot(context)
        original = forwarding._book_calls
        def changed(tree):
            result = original(tree)
            object.__setattr__(snapshot.files[0], 'data', b'changed')
            return result
        with patch.object(forwarding, '_book_calls', side_effect=changed):
            with self.assertRaises(SafetyError):
                forwarding.analyze(context, self.packages, snapshot=snapshot)

    def test_missing_or_mismatched_resource_capture_is_unavailable(self):
        source = b'#import "@local/math-book:0.2.0": book-part\n#book-part[#include "child.typ"]'
        context = self.context(source)
        missing = forwarding.analyze(context, self.packages)
        self.assertEqual(missing['status'], 'UNAVAILABLE')
        self.assertFalse(missing['sites'] or missing['resource_capture_bound'])
        roots = dict(project=str(self.project), packages=str(self.packages), cache=str(self.cache))
        requests = [document_resources.ResourceRequest('project', item.path, 'source') for item in context.sources]
        partial = document_resources.capture(roots, requests)
        result = forwarding.analyze(context, self.packages, snapshot=partial)
        self.assertEqual(result['status'], 'UNAVAILABLE')
        self.assertTrue(result['original_unresolved_retained'])
        (self.project / 'main.typ').write_bytes(source + b'\n')
        mismatch = forwarding.analyze(context, self.packages, snapshot=self.snapshot(context))
        self.assertEqual(mismatch['status'], 'UNAVAILABLE')
        self.assertIn('include source', mismatch['reason'])

    def test_cache_selection_rejects_a_higher_priority_staged_package(self):
        source = b'#import "@local/math-book:0.2.0": book-part\n#book-part[#include "child.typ"]'
        context = self.context(source)
        primary = self.packages / 'local/math-book/0.2.0'
        cached = self.cache / 'local/math-book/0.2.0'
        cached.parent.mkdir(parents=True)
        shutil.copytree(primary, cached)
        shutil.rmtree(primary)
        result = forwarding.analyze(context, self.cache, snapshot=self.snapshot(context, selected='cache'))
        self.assertEqual(result['status'], 'CONDITIONAL')
        self.assertEqual(result['package_evidence']['root_id'], 'cache')
        primary.mkdir(parents=True)
        shutil.copyfile(cached / 'typst.toml', primary / 'typst.toml')
        shadow = document_resources.ResourceRequest('packages', 'local/math-book/0.2.0/typst.toml', 'package')
        result = forwarding.analyze(context, self.cache,
                                    snapshot=self.snapshot(context, selected='cache', extra=(shadow,)))
        self.assertEqual(result['status'], 'UNAVAILABLE')
        self.assertIn('higher-priority', result['reason'])

    @unittest.skipUnless(os.environ.get('TYPST_LINT_RENDER_TESTS') == '1' and shutil.which('typst'),
                         'Opt in with the reviewed Typst compiler')
    def test_owned_fixture_observes_both_includes_execute(self):
        if not subprocess.check_output(['typst', '--version'], text=True).startswith('typst 0.15.1 '):
            self.skipTest('Fixture comparison pins Typst 0.15.1')
        for name, version in (('math-blocks', '0.2.0'), ('text-utils', '0.1.2'),
                              ('project-references', '0.1.0')):
            self.install(name, version)
        source = (b'#import "@local/math-book:0.2.0": book-part\n'
                  b'#book-part[#include "child.typ"]\n#context { [#include "child.typ"] }')
        context = self.context(source)
        self.assertEqual(forwarding.analyze(context, self.packages,
                                           snapshot=self.snapshot(context))['recognized_site_count'], 2)
        run = subprocess.run(['typst', 'query', '--root', str(self.project), '--package-path', str(self.packages),
                              '--package-cache-path', str(self.cache), '--field', 'value', '--format', 'json',
                              str(self.project / 'main.typ'), 'metadata'], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout).count('probe'), 2)
        self.assertEqual((self.project / 'main.typ').read_bytes(), source)


if __name__ == '__main__':
    unittest.main()
