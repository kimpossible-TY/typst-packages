"""Include evidence is conditional, capture-bound and never hides unresolved sites."""
from dataclasses import replace
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_original_compile_cli as cli
import document_resources
import include_forwarding_evidence as forwarding
from syntax_client import EXECUTABLE
from typst_lint import InputError, SafetyError
import test_include_forwarding_evidence as fixtures


@unittest.skipUnless(EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class OriginalCompileIncludeForwardingTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.IncludeForwardingEvidenceTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_same_capture_matches_evidence_and_retains_original_unresolved(self):
        source = (b'#import "@local/math-book:0.2.0": book-part\n'
                  b'#book-part[#include "child.typ"]\n#context { [#include "child.typ"] }')
        context = self.f.context(source)
        snapshot = self.f.snapshot(context)
        expected = forwarding.analyze(context, self.f.packages, snapshot=snapshot)
        result = cli.lint_captured_document(context, snapshot, rule_set='pde', local_hints=True)
        evidence = result['conditional_include_forwarding']
        self.assertEqual(evidence['status'], 'CONDITIONAL')
        self.assertEqual(evidence['sites'], expected['sites'])
        self.assertEqual(evidence['recognized_site_count'], 2)
        self.assertEqual(evidence['conditionally_explained_occurrences'], 2)
        self.assertEqual(evidence['unresolved_sites_before'], len(context.unresolved))
        self.assertEqual(len(result['coverage']['unresolved_includes']), len(context.unresolved))
        self.assertTrue(evidence['original_unresolved_retained'])
        self.assertFalse(evidence['runtime_execution_proved'] or evidence['runtime_package_selection_proved']
                         or evidence['coverage_complete'] or evidence['edit_permission']
                         or evidence['compiler_permission'] or evidence['publication_authority'])
        self.assertEqual((self.f.project / 'main.typ').read_bytes(), source)

    def test_missing_package_is_visible_not_a_clean_include_result(self):
        source = b'#context { [#include "child.typ"] }'
        context = self.f.context(source)
        roots = dict(project=str(self.f.project), packages=str(self.f.packages), cache=str(self.f.cache))
        requests = [document_resources.ResourceRequest('project', item.path, 'source') for item in context.sources]
        snapshot = document_resources.capture(roots, requests)
        result = cli.lint_captured_document(context, snapshot, rule_set='pde', local_hints=True)
        evidence = result['conditional_include_forwarding']
        self.assertEqual(evidence['status'], 'UNAVAILABLE')
        self.assertTrue(evidence['reason'])
        self.assertFalse(evidence['sites'] or evidence['resource_capture_bound'])
        self.assertEqual(len(result['coverage']['unresolved_includes']), len(context.unresolved))

    def test_selected_cache_and_higher_priority_capture_conflict(self):
        source = b'#import "@local/math-book:0.2.0": book-part\n#book-part[#include "child.typ"]'
        context = self.f.context(source)
        primary = self.f.packages / 'local/math-book/0.2.0'
        cached = self.f.cache / 'local/math-book/0.2.0'
        cached.parent.mkdir(parents=True)
        shutil.move(primary, cached)
        result, _ = cli._include_forwarding(context, self.f.snapshot(context, selected='cache'), 50)
        self.assertEqual(result['status'], 'CONDITIONAL')
        self.assertEqual(result['package_evidence']['root_id'], 'cache')
        primary.mkdir(parents=True)
        (primary / 'typst.toml').write_text('incomplete')
        extra = (document_resources.ResourceRequest('packages', 'local/math-book/0.2.0/typst.toml', 'package'),)
        conflict = self.f.snapshot(context, selected='cache', extra=extra)
        result, _ = cli._include_forwarding(context, conflict, 50)
        self.assertEqual(result['status'], 'UNAVAILABLE')
        self.assertFalse(result['sites'])

    def test_changed_capture_and_forged_or_omitted_evidence_refuse(self):
        source = b'#import "@local/math-book:0.2.0": book-part\n#book-part[#include "child.typ"]'
        context = self.f.context(source)
        snapshot = self.f.snapshot(context)
        with self.assertRaises(SafetyError):
            cli._include_forwarding(context, replace(snapshot), 50)
        original = forwarding.analyze
        def altered(*args, **kwargs):
            report = original(*args, **kwargs)
            report['sites'][0]['line'] += 1
            return report
        with patch.object(forwarding, 'analyze', side_effect=altered), self.assertRaises((InputError, SafetyError)):
            cli._include_forwarding(context, snapshot, 50)
        def omitted(*args, **kwargs):
            report = original(*args, **kwargs)
            report['sites'] = []
            report['recognized_site_count'] = 0
            report['conditionally_explained_occurrences'] = 0
            return report
        with patch.object(forwarding, 'analyze', side_effect=omitted), self.assertRaises((InputError, SafetyError)):
            cli._include_forwarding(context, snapshot, 50)
        def boolean_count(*args, **kwargs):
            report = original(*args, **kwargs)
            report['recognized_site_count'] = True
            return report
        with patch.object(forwarding, 'analyze', side_effect=boolean_count), self.assertRaises((InputError, SafetyError)):
            cli._include_forwarding(context, snapshot, 50)

    def test_site_samples_keep_exact_counts_and_omissions(self):
        source = b'#context { [' + b'\n'.join([b'#include "child.typ"'] * 70) + b'] }'
        context = self.f.context(source)
        evidence, _ = cli._include_forwarding(context, self.f.snapshot(context), 50)
        self.assertEqual(evidence['recognized_site_count'], 70)
        self.assertEqual(len(evidence['sites']), 50)
        self.assertEqual(evidence['sites_omitted'], 20)
        self.assertEqual(evidence['unresolved_sites_before'], 70)
        self.assertTrue(evidence['original_unresolved_retained'])
