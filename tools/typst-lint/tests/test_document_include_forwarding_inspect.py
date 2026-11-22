"""Optional include ownership keeps unresolved and all prior reports intact."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import document_cli
import include_forwarding_evidence as forwarding
import test_include_forwarding_evidence as fixtures
from typst_lint import InputError, SafetyError


@unittest.skipUnless(fixtures.EXECUTABLE.is_file(), 'Requires pinned syntax adapter')
class IncludeForwardingInspectTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.IncludeForwardingEvidenceTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def issue(self, extra=b''):
        source = (b'#import "@local/math-book:0.2.0": book-part\n'
                  b'#book-part[#include "child.typ"]\n'
                  b'#context { [#include "child.typ"] }\n' + extra)
        context = self.fixture.context(source)
        options = dict(root=str(self.fixture.project), entrypoint='main.typ', rule_set='pde',
                       package_path=str(self.fixture.packages), footnote_cache_path=str(self.fixture.cache),
                       preview_sequence=True, inspect_capitalization_review=True)
        return source, context, options

    def test_opt_in_keeps_full_baseline_and_unresolved_inventory(self):
        source, _, options = self.issue(b'#paragraph_tab\ncompare the values.\n')
        baseline = document_cli.report_document(**options)
        result = document_cli.report_document(**options, inspect_include_forwarding=True)
        evidence = result.pop('conditional_include_forwarding')
        self.assertEqual(result, baseline)
        self.assertEqual(evidence['status'], 'CONDITIONAL')
        self.assertEqual(evidence['recognized_site_count'], 2)
        self.assertEqual(evidence['unresolved_sites_before'], len(baseline['include_context']['unresolved']))
        self.assertTrue(evidence['original_unresolved_retained'])
        self.assertFalse(evidence['runtime_execution_proved'] or evidence['compiler_permission']
                         or evidence['edit_permission'] or evidence['compiler_observation'])
        self.assertEqual([r['starter'] for r in baseline['capitalization_starter_review']['findings']], ['compare'])
        self.assertEqual((self.fixture.project / 'main.typ').read_bytes(), source)

    def test_unexecuted_branch_does_not_become_forwarding_evidence(self):
        source = (b'#import "@local/math-book:0.2.0": book-part\n'
                  b'#if false [#book-part[#include "child.typ"]]\n'
                  b'#context { if false { [#include "child.typ"] } }')
        self.fixture.context(source)
        options = dict(root=str(self.fixture.project), entrypoint='main.typ', rule_set='pde',
                       package_path=str(self.fixture.packages), footnote_cache_path=str(self.fixture.cache))
        result = document_cli.report_document(**options, inspect_include_forwarding=True)
        self.assertEqual(result['conditional_include_forwarding']['recognized_site_count'], 0)
        self.assertTrue(result['include_context']['unresolved'])

    def test_missing_package_and_work_limit_withhold_only_supplement(self):
        _, _, options = self.issue()
        baseline = document_cli.report_document(**options)
        with patch.object(forwarding, '_charge', side_effect=InputError('Include forwarding work budget exceeded')):
            result = document_cli.report_document(**options, inspect_include_forwarding=True)
        section = result.pop('conditional_include_forwarding')
        self.assertEqual(result, baseline)
        self.assertEqual(section['status'], 'UNAVAILABLE')
        self.assertIn('work budget', section['reason'])
        (self.fixture.packages / 'local/math-book/0.2.0/lib.typ').unlink()
        # Rebuild the baseline after the independent package disappearance.
        missing_baseline = document_cli.report_document(**options)
        missing = document_cli.report_document(**options, inspect_include_forwarding=True)
        self.assertEqual(missing.pop('conditional_include_forwarding')['status'], 'UNAVAILABLE')
        self.assertEqual(missing, missing_baseline)

    def test_tampered_report_is_rejected_and_byte_headroom_preserves_baseline(self):
        _, _, options = self.issue()
        baseline = document_cli.report_document(**options)
        original = forwarding.analyze
        def tamper(*args, **kwargs):
            value = original(*args, **kwargs)
            value['sites'][0]['target_sha256'] = '0' * 64
            return value
        with patch.object(forwarding, 'analyze', side_effect=tamper):
            with self.assertRaisesRegex(ValueError, 'does not match captured source'):
                document_cli.report_document(**options, inspect_include_forwarding=True)
        size = len(json.dumps(baseline, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8'))
        with patch.object(document_cli, 'MAX_MODERN_REPORT_BYTES', size):
            limited = document_cli.report_document(**options, inspect_include_forwarding=True)
        self.assertEqual(limited, baseline)
