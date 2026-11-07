"""Applied reports keep original facts despite issued-report inspection mutation.

This is a reporting-integrity boundary, not a sandbox against arbitrary trusted
Python changes. All source, config, catalog and journal writes are temporary.
"""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import protected_fix as bridge
from edit_plan import Edit, plan
from source_profiles import CLOSED_CORE_SOURCE_PROFILE, STRUCTURED_CORE_SOURCE_PROFILE
from syntax_client import EXECUTABLE
from transactions import OfflineExclusive, TransactionError


PROFILES = (
    (CLOSED_CORE_SOURCE_PROFILE, b'Preserved $x_1$\n', b'Preserved $x_(1)$\n'),
    (STRUCTURED_CORE_SOURCE_PROFILE, b'= Heading *$x_1$*\n', b'= Heading *$x_(1)$*\n'),
)
OUTER_CHANGES = {
    'profile': 'forged-profile', 'target': 'other.typ',
    'original_sha256': '0' * 64, 'final_sha256': '0' * 64,
    'required_rule_ids': (), 'evaluated_rule_ids': (),
    'structural_reasons': (), 'applied_insertions': (),
    'modified_paths': ('other.typ',), 'journal_id': 'forged-journal',
    'grammar_version': 'forged-grammar',
}
INSERTION_CHANGES = {
    'path': 'other.typ', 'start': 999, 'end': 999,
    'replacement': b'forged', 'rule_id': 'core.forged',
}


def copy_report(summary):
    return replace(summary, applied_insertions=tuple(
        replace(item) for item in summary.applied_insertions))


@contextmanager
def changed(value, name, replacement):
    original = getattr(value, name)
    object.__setattr__(value, name, replacement)
    try:
        yield
    finally:
        object.__setattr__(value, name, original)


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class ProtectedSummaryBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / 'consumer'
        self.root.mkdir()
        self.store = self.base / 'journals'
        self.store.mkdir(mode=0o700)
        self.source = self.root / 'main.typ'
        (self.root / '.typst-lint.toml').write_bytes(b'rule-set="core"\ninclude=["main.typ"]\n')
        self.catalog = self.base / 'catalog.json'
        self.catalog.write_bytes(bridge.CATALOG.read_bytes())
        patch = mock.patch.object(bridge, 'CATALOG', self.catalog)
        patch.start()
        self.addCleanup(patch.stop)
        # Only test-owned temporary inputs are covered by this assertion.
        self.lease = OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers()

    def context(self, profile, source, **options):
        self.source.write_bytes(source)
        return bridge.ProtectedCoreFix(self.root, 'main.typ', journal_directory=self.store,
                                       lease=self.lease, source_profile=profile, **options)

    def journals(self):
        return {str(path.relative_to(self.store)): path.read_bytes()
                for path in self.store.rglob('*') if path.is_file()}

    def unavailable(self, fix):
        with self.assertRaises(bridge.ProtectedCoreError):
            _ = fix.summary
        with self.assertRaises(bridge.ProtectedCoreError):
            fix.compile_snapshot()

    def refuse_report_drift(self, fix, candidate):
        source, journals = self.source.read_bytes(), self.journals()
        with mock.patch.object(fix.transaction, 'apply', side_effect=AssertionError('entered apply')):
            with self.assertRaisesRegex(bridge.ProtectedCoreError, 'reporting facts changed'):
                fix.apply(candidate)
        self.assertEqual(self.source.read_bytes(), source)
        self.assertEqual(self.journals(), journals)
        self.assertFalse(fix.transaction._used)
        self.unavailable(fix)

    def assert_report(self, fix, expected, original, final, result):
        summary = fix.summary
        self.assertEqual(summary, replace(expected, modified_paths=result.modified,
                                          journal_id=result.journal))
        self.assertEqual(summary.original_sha256, hashlib.sha256(original).hexdigest())
        self.assertEqual(summary.final_sha256, hashlib.sha256(final).hexdigest())
        self.assertEqual(self.source.read_bytes(), final)
        self.assertEqual(plan(original, (Edit(item.start, item.end, item.replacement)
                                        for item in summary.applied_insertions)).candidate(), final)
        self.assertTrue(all(item.path == 'main.typ' and item.start == item.end
                            and item.rule_id == bridge.SCRIPT
                            and not item.replacement.strip(b'()')
                            for item in summary.applied_insertions))
        self.assertEqual(result.modified, ('main.typ',) if original != final else ())
        self.assertEqual(result.journal is None, original == final)
        if result.journal is not None:
            # The transaction uses its fixed namespace below the configured store.
            events = self.store.rglob(f'{result.journal}/event-*.json')
            self.assertTrue(any(json.loads(path.read_bytes())['kind'] == 'committed'
                                for path in events))

    def assert_snapshot(self, fix, expected, final):
        snapshot = fix.compile_snapshot()
        self.assertEqual(snapshot.source, final)
        self.assertEqual(snapshot.source_sha256, expected.final_sha256)
        self.assertEqual((snapshot.source_profile, snapshot.grammar_version),
                         (expected.profile, expected.grammar_version))
        self.assertEqual(snapshot.target, expected.target)
        self.assertEqual(snapshot.required_rule_ids, expected.required_rule_ids)
        self.assertEqual(snapshot.evaluated_rule_ids, expected.evaluated_rule_ids)
        self.assertEqual(snapshot.structural_reasons, expected.structural_reasons)
        self.assertIs(fix.compile_snapshot(), snapshot)
        fix.assert_compile_current(snapshot)

    def test_outer_facts_are_checked_before_dirty_and_noop_apply_without_consuming_candidate(self):
        for profile, dirty, clean in PROFILES:
            for source in (dirty, clean):
                with self.subTest(profile=profile, noop=source == clean), self.context(profile, source) as fix:
                    candidate = fix.validate()
                    expected = copy_report(fix._validated_summary)
                    for name, value in OUTER_CHANGES.items():
                        if getattr(expected, name) == value:
                            value = ('forged',) if type(value) is tuple else 'forged'
                        with self.subTest(field=name), changed(fix._validated_summary, name, value):
                            self.refuse_report_drift(fix, candidate)
                    result = fix.apply(candidate)
                    self.assert_report(fix, expected, source, clean, result)
                    self.assert_snapshot(fix, expected, clean)

    def test_nested_insertions_and_equal_but_wrong_field_types_are_checked_before_apply(self):
        for profile, dirty, clean in PROFILES:
            with self.subTest(profile=profile), self.context(profile, dirty) as fix:
                candidate = fix.validate()
                summary = fix._validated_summary
                expected = copy_report(summary)
                item = summary.applied_insertions[0]
                for name, value in INSERTION_CHANGES.items():
                    with self.subTest(field=name), changed(item, name, value):
                        self.refuse_report_drift(fix, candidate)
                with changed(item, 'start', float(item.start)):
                    self.refuse_report_drift(fix, candidate)
                with changed(summary, 'required_rule_ids', list(summary.required_rule_ids)):
                    self.refuse_report_drift(fix, candidate)
                with changed(fix, '_validated_summary', copy_report(summary)):
                    self.refuse_report_drift(fix, candidate)
                result = fix.apply(candidate)
                self.assert_report(fix, expected, dirty, clean, result)

    def test_callback_outer_nested_or_replaced_report_cannot_forge_success_or_compile_snapshot(self):
        for profile, dirty, clean in PROFILES:
            for point in ('before_revalidate', 'before_final_validation'):
                for mutation in ('outer', 'nested', 'replace'):
                    holder, hits = {}, []
                    def fault(actual, path):
                        if actual != point:
                            return
                        hits.append(actual)
                        fix = holder['fix']
                        if mutation == 'replace':
                            fix._validated_summary = None
                            return
                        target = (fix._validated_summary if mutation == 'outer'
                                  else fix._validated_summary.applied_insertions[0])
                        changes = OUTER_CHANGES if mutation == 'outer' else INSERTION_CHANGES
                        for name, value in changes.items():
                            object.__setattr__(target, name, value)
                    with self.subTest(profile=profile, point=point, mutation=mutation), \
                         self.context(profile, dirty, fault=fault) as fix:
                        holder['fix'] = fix
                        candidate = fix.validate()
                        exposed = fix._validated_summary
                        exposed_items = exposed.applied_insertions
                        expected = copy_report(exposed)
                        result = fix.apply(candidate)
                        self.assertEqual(hits, [point])
                        self.assert_report(fix, expected, dirty, clean, result)
                        self.assertIsNot(fix.summary, exposed)
                        for copied, original in zip(fix.summary.applied_insertions, exposed_items):
                            self.assertIsNot(copied, original)
                        self.assert_snapshot(fix, expected, clean)

    def test_report_alias_mutation_after_success_never_changes_applied_report(self):
        for profile, dirty, clean in PROFILES:
            with self.subTest(profile=profile), self.context(profile, dirty) as fix:
                candidate = fix.validate()
                exposed = fix._validated_summary
                expected = copy_report(exposed)
                result = fix.apply(candidate)
                object.__setattr__(exposed, 'final_sha256', '0' * 64)
                object.__setattr__(exposed.applied_insertions[0], 'rule_id', 'core.forged')
                self.assert_report(fix, expected, dirty, clean, result)
                self.assert_snapshot(fix, expected, clean)

    def test_repeated_validation_replaces_pinned_report_and_rejects_old_candidate(self):
        for profile, dirty, clean in PROFILES:
            with self.subTest(profile=profile), self.context(profile, dirty) as fix:
                old = fix.validate()
                old_summary = fix._validated_summary
                object.__setattr__(old_summary, 'final_sha256', '0' * 64)
                candidate = fix.validate()
                expected = copy_report(fix._validated_summary)
                object.__setattr__(old_summary.applied_insertions[0], 'start', 999)
                with self.assertRaises(bridge.ProtectedCoreError):
                    fix.apply(old)
                self.unavailable(fix)
                result = fix.apply(candidate)
                self.assert_report(fix, expected, dirty, clean, result)
                self.assert_snapshot(fix, expected, clean)

    def test_postcommit_selection_or_proof_drift_keeps_truthful_known_commit_report(self):
        for profile, dirty, clean in PROFILES:
            for mutation in ('selection', 'proof'):
                holder = {}
                def fault(point, path):
                    if point != 'before_final_validation':
                        return
                    fix = holder['fix']
                    object.__setattr__(fix._validated_summary, 'final_sha256', '0' * 64)
                    object.__setattr__(fix._validated_summary.applied_insertions[0], 'path', 'other.typ')
                    if mutation == 'selection':
                        fix.source_profile = 'forged-profile'
                    else:
                        object.__setattr__(fix._compile_validation.proof, 'source_sha256', '0' * 64)
                with self.subTest(profile=profile, mutation=mutation), \
                     self.context(profile, dirty, fault=fault) as fix:
                    holder['fix'] = fix
                    candidate = fix.validate()
                    expected = copy_report(fix._validated_summary)
                    actual_apply = fix.transaction.apply
                    def record_apply(token):
                        result = actual_apply(token)
                        holder['result'] = result
                        return result
                    with mock.patch.object(fix.transaction, 'apply', side_effect=record_apply) as apply:
                        with self.assertRaises(bridge.ProtectedCoreError):
                            fix.apply(candidate)
                    self.assertEqual(apply.call_count, 1)
                    self.assert_report(fix, expected, dirty, clean, holder['result'])
                    journals = self.journals()
                    with self.assertRaises(bridge.ProtectedCoreError):
                        fix.compile_snapshot()
                    self.assertEqual(self.journals(), journals)

    def test_failed_apply_exposes_no_successful_summary_or_compile_snapshot(self):
        for profile, dirty, _ in PROFILES:
            # A rolled-back failure still requires explicit recovery. Keep its
            # journal isolated rather than weakening that existing contract.
            self.store = self.base / f'failed-journals-{profile}'
            self.store.mkdir(mode=0o700)
            def fault(point, path):
                if point == 'before_final_validation':
                    raise RuntimeError('injected apply failure')
            with self.subTest(profile=profile), self.context(profile, dirty, fault=fault) as fix:
                candidate = fix.validate()
                with self.assertRaises(TransactionError):
                    fix.apply(candidate)
                self.assertEqual(self.source.read_bytes(), dirty)
                self.unavailable(fix)

    def test_unknown_postcommit_transaction_handoff_exposes_no_success_report(self):
        for profile, dirty, clean in PROFILES:
            with self.subTest(profile=profile), self.context(profile, dirty) as fix:
                candidate = fix.validate()
                with mock.patch.object(fix.transaction, '_retain_committed',
                                       side_effect=RuntimeError('unknown transaction handoff')):
                    with self.assertRaisesRegex(RuntimeError, 'unknown transaction handoff'):
                        fix.apply(candidate)
                self.assertEqual(self.source.read_bytes(), clean)
                self.unavailable(fix)


if __name__ == '__main__':
    unittest.main()
