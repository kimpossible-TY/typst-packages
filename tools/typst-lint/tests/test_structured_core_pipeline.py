"""Selected-source-profile bridge authority, using only temporary consumers."""
from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError, replace
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import closed_core
from edit_plan import Edit
import offline_cli
import offline_compile_cli
import protected_fix as bridge
from source_profiles import (CLOSED_CORE_SOURCE_PROFILE as V1,
                             STRUCTURED_CORE_SOURCE_PROFILE as V2,
                             grammar_for_source_profile)
from syntax_client import EXECUTABLE, parse
from transactions import OfflineExclusive
import typst_lint as lint

STRUCTURED = (b'\xef\xbb\xbfPreserved prose.\r\n= Title *strong _emph $x^(y_z)$_*\r\n'
              b'- Item $a_(b^c)$\r\n+ Other $w^q$\r\n')
REPAIRED = STRUCTURED.replace(b'y_z', b'y_(z)').replace(b'b^c', b'b^(c)').replace(b'w^q', b'w^(q)')


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class StructuredCorePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='structured-core-pipeline-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'consumer'
        self.root.mkdir()
        self.source = self.root / 'main.typ'
        self.source.write_bytes(STRUCTURED)
        (self.root / '.typst-lint.toml').write_bytes(b'rule-set="core"\ninclude=["main.typ"]\n')
        self.store = self.base / 'journals'
        self.store.mkdir(mode=0o700)
        self.catalog = self.base / 'catalog.json'
        self.catalog.write_bytes(lint.CATALOG.read_bytes())
        patcher = mock.patch.object(bridge, 'CATALOG', self.catalog)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lease = OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers()

    def context(self, **options):
        return bridge.ProtectedCoreFix(self.root, 'main.typ', journal_directory=self.store,
                                       lease=self.lease, **options)

    def journal_bytes(self):
        return {str(p.relative_to(self.store)): p.read_bytes()
                for p in self.store.rglob('*') if p.is_file()}

    def assert_unwritten(self, expected=STRUCTURED):
        self.assertEqual(self.source.read_bytes(), expected)
        self.assertFalse(tuple(self.store.rglob('manifest.json')))

    def invoke(self, arguments, main=offline_cli.main):
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(arguments)
        report = json.loads(output.getvalue())
        self.assertEqual(status, report['status'])
        return report

    def cli_args(self, profile=V2, command='fix'):
        args = [command, '--root', str(self.root), '--profile', profile,
                '--offline-exclusive', '--target', 'main.typ', '--format', 'json']
        if command == 'compile':
            args += ['--resource-profile', 'embedded-ascii-v1',
                     '--offline-output-exclusive', '--output', 'out.pdf']
        return args

    def test_same_structured_source_requires_explicit_v2_and_repairs_once(self):
        with self.context() as fix:
            self.assertEqual((fix.source_profile, fix.grammar_version), (V1, 'closed-core-markup-v1'))
            with self.assertRaises(closed_core.ClosedCoreError):
                fix.validate()
        self.assert_unwritten()
        with self.context(source_profile=V2) as fix:
            candidate = fix.validate()
            self.assertEqual(candidate.passes, 1)
            fix.apply(candidate)
            self.assertEqual(self.source.read_bytes(), REPAIRED)
            summary = fix.summary
            self.assertEqual((summary.profile, summary.grammar_version), (V2, 'closed-core-markup-v2'))
            self.assertEqual(len(summary.required_rule_ids), 15)
            self.assertEqual(len(summary.structural_reasons), 14)
            snapshot = fix.compile_snapshot()
            self.assertEqual((snapshot.source_profile, snapshot.grammar_version), (V2, 'closed-core-markup-v2'))
            self.assertEqual(snapshot.source, REPAIRED)
            fix.assert_compile_current(snapshot)
            with self.assertRaises(FrozenInstanceError):
                summary.grammar_version = 'changed'
            with self.assertRaises(FrozenInstanceError):
                snapshot.source_profile = V1
        journals = self.journal_bytes()
        with self.context(source_profile=V2) as fix:
            candidate = fix.validate()
            self.assertEqual(candidate.passes, 0)
            result = fix.apply(candidate)
            self.assertEqual((result.modified, result.journal), ((), None))
            self.assertEqual(fix.summary.applied_insertions, ())
            self.assertEqual(fix.compile_snapshot().source, REPAIRED)
        self.assertEqual(self.journal_bytes(), journals)

    def test_every_actual_grammar_check_uses_selected_profile_including_retention(self):
        with mock.patch.object(bridge, 'certify', wraps=closed_core.certify) as certify, \
             mock.patch.object(bridge, 'verify_proof', wraps=closed_core.verify_proof) as verify:
            with self.context(source_profile=V2) as fix:
                fix.apply(fix.validate())
                snapshot = fix.compile_snapshot()
                fix.assert_compile_current(snapshot)
        self.assertGreaterEqual(certify.call_count, 7)
        self.assertGreater(verify.call_count, certify.call_count)
        self.assertTrue(all(call.kwargs == {'source_profile': V2} for call in certify.call_args_list))
        self.assertTrue(all(call.kwargs == {'source_profile': V2} for call in verify.call_args_list))
        self.assertEqual({call.args[0].source for call in certify.call_args_list}, {STRUCTURED, REPAIRED})

    def test_nested_script_wrapper_and_equation_ownership_survives_only_grouping(self):
        before = parse(STRUCTURED)
        with self.context(source_profile=V2) as fix:
            candidate = fix.validate()
            replay = fix._compile_validation.replay
            self.assertEqual(bridge._script_ownership(before), bridge._script_ownership(replay.final_tree))
            owners, attachments = bridge._script_ownership(before)
            self.assertEqual(sum(owner[0][0] == 'Equation' for owner in owners), 3)
            self.assertTrue(any(record[1] for record in attachments))
            original = candidate.sources['main.typ']
            for item in reversed(replay.edits):
                edit = item.edit
                self.assertEqual(edit.start, edit.end)
                self.assertFalse(edit.replacement.strip(b'()'))
            fix.apply(candidate)
            self.assertEqual(original, REPAIRED)
        # Equal role lists alone cannot certify a change of equation ownership.
        a = parse(b'*$x^i$*\n$y^j$\n')
        b = parse(b'$x^i$\n*$y^j$*\n')
        self.assertEqual(bridge._attachment_roles(a), bridge._attachment_roles(b))
        self.assertNotEqual(bridge._script_ownership(a), bridge._script_ownership(b))

    def test_unknown_profile_is_refused_before_any_transaction_or_root_access(self):
        for invalid in ('structured-core', 'structured-core-v2', 'latest', 'STRUCTURED-CORE-V1', '', None, []):
            with self.subTest(profile=invalid), \
                 mock.patch.object(bridge, 'ProtectedTransaction', side_effect=AssertionError('No transaction construction')):
                with self.assertRaises(bridge.ProtectedCoreInputError) as raised:
                    self.context(source_profile=invalid)
                self.assertEqual((raised.exception.error_code, raised.exception.phase), ('invalid_profile', 'arguments'))
        for command in ('fix', 'compile'):
            with self.subTest(command=command), \
                 mock.patch.object(offline_cli, '_root', side_effect=AssertionError('No filesystem access')):
                result = self.invoke(self.cli_args('latest', command))
                self.assertEqual((result['status'], result['error_code']), (2, 'invalid_arguments'))
        self.assert_unwritten()

    def test_unsupported_nested_constructs_are_input_refusals_and_preserve_all_bytes(self):
        unsupported = (
            b'= Heading *#read("never")*\n', b'= Heading *$sin(x)_i$*\n',
            b'- _#include "never.typ"_\n', b'+ *before // comment\nafter*\n',
            b'= Heading <label>\n', b'1. Explicit enumerator $x^i$\n',
            b'/ term: definition $x^i$\n',
            b'= Heading\n// typst-lint: ignore-next core.parenthesized-scripts -- denied\n$x^i$\n',
        )
        for source in unsupported:
            with self.subTest(source=source):
                self.source.write_bytes(source)
                with self.context(source_profile=V2) as fix:
                    with self.assertRaises(closed_core.ClosedCoreError):
                        fix.validate()
                self.assert_unwritten(source)

    def test_cross_profile_proof_is_never_a_verifier_scope_selector(self):
        source = b'Plain $x_1$\n'
        self.source.write_bytes(source)
        for selected, supplied in ((V1, V2), (V2, V1)):
            proof = closed_core.certify(parse(source), 'main.typ', source_profile=supplied)
            with self.subTest(selected=selected), self.context(source_profile=selected) as fix, \
                 mock.patch.object(bridge, 'certify', return_value=proof):
                with self.assertRaises(bridge.ProtectedCoreError) as raised:
                    fix.validate()
                self.assertEqual(offline_cli._error(raised.exception, 'fix', 'validate')['status'], 3)
            self.assert_unwritten(source)

    def test_session_profile_and_grammar_mutation_before_validate_or_apply_are_safety3(self):
        for field, value in (('source_profile', V1), ('source_profile', 'unknown'),
                             ('grammar_version', 'closed-core-markup-v1')):
            for when in ('before_validate', 'before_apply'):
                with self.subTest(field=field, when=when), self.context(source_profile=V2) as fix:
                    token = fix.validate() if when == 'before_apply' else None
                    setattr(fix, field, value)
                    with self.assertRaises(bridge.ProtectedCoreError) as raised:
                        fix.apply(token) if token is not None else fix.validate()
                    self.assertEqual(offline_cli._error(raised.exception, 'fix', 'apply')['status'], 3)
                self.assert_unwritten()

    def test_retained_proof_profile_and_grammar_mutation_refused_before_apply(self):
        for field, value in (('source_profile', V1), ('grammar_version', 'closed-core-markup-v1')):
            with self.subTest(field=field), self.context(source_profile=V2) as fix:
                token = fix.validate()
                object.__setattr__(fix._compile_validation.proof, field, value)
                with self.assertRaises(bridge.ProtectedCoreError):
                    fix.apply(token)
            self.assert_unwritten()

    def test_candidate_grammar_failure_after_admitted_baseline_is_safety3(self):
        def changed(tree, path, **options):
            if tree.source != STRUCTURED:
                raise closed_core.ClosedCoreError('Injected post-baseline admission failure')
            return closed_core.certify(tree, path, **options)
        with self.context(source_profile=V2) as fix, \
             mock.patch.object(bridge, 'certify', side_effect=changed):
            with self.assertRaises(bridge.ProtectedCoreError) as raised:
                fix.validate()
            self.assertEqual(offline_cli._error(raised.exception, 'fix', 'validate')['status'], 3)
        self.assert_unwritten()

    def test_profile_drift_during_actual_apply_refuses_after_commit_without_rollback(self):
        holder = {}
        def fault(point, path):
            if point == 'before_replace':
                holder['fix'].source_profile = V1
        with self.context(source_profile=V2, fault=fault) as fix:
            holder['fix'] = fix
            token = fix.validate()
            with self.assertRaises(bridge.ProtectedCoreError):
                fix.apply(token)
            self.assertEqual((fix.summary.profile, fix.summary.grammar_version), (V2, 'closed-core-markup-v2'))
            self.assertIsNotNone(fix.summary.journal_id)
            self.assertEqual(self.source.read_bytes(), REPAIRED)
            events = tuple(self.store.rglob('event-*.json'))
            self.assertTrue(any(json.loads(p.read_bytes()).get('kind') == 'committed' for p in events))
            with self.assertRaises(bridge.ProtectedCoreError):
                fix.compile_snapshot()

    def test_cli_postcommit_drift_reports_safety3_and_the_known_original_commit(self):
        actual = bridge.ProtectedCoreFix
        for command, module in (('fix', offline_cli), ('compile', offline_compile_cli)):
            self.source.write_bytes(STRUCTURED)
            holder = {}
            def fault(point, path):
                if point == 'before_replace':
                    holder['fix'].source_profile = V1
            def create(*args, **options):
                value = actual(*args, fault=fault, **options)
                holder['fix'] = value
                return value
            with self.subTest(command=command), \
                 mock.patch.object(module, 'ProtectedCoreFix', side_effect=create):
                result = self.invoke(self.cli_args(command=command))
            self.assertEqual((result['status'], result['source_commit']), (3, 'committed'))
            summary = result if command == 'fix' else result['source_summary']
            self.assertEqual((summary['profile'], summary['grammar_version']), (V2, 'closed-core-markup-v2'))
            self.assertEqual(self.source.read_bytes(), REPAIRED)
            self.assertFalse(result['compiler_invoked'])

    def test_compile_snapshot_and_session_mutation_preserve_committed_fixes(self):
        for target in ('snapshot', 'session', 'proof'):
            for field, value in (('source_profile', V1), ('grammar_version', 'closed-core-markup-v1')):
                self.source.write_bytes(STRUCTURED)
                with self.subTest(target=target, field=field), self.context(source_profile=V2) as fix:
                    fix.apply(fix.validate())
                    snapshot = fix.compile_snapshot()
                    journals = self.journal_bytes()
                    chosen = snapshot if target == 'snapshot' else fix if target == 'session' else fix._applied_validation.proof
                    object.__setattr__(chosen, field, value)
                    for guard in (fix.compile_snapshot, lambda: fix.assert_compile_current(snapshot)):
                        with self.assertRaises(bridge.ProtectedCoreError):
                            guard()
                    self.assertEqual(self.source.read_bytes(), REPAIRED)
                    self.assertEqual(self.journal_bytes(), journals)

    def test_foreign_profile_candidate_and_snapshot_never_authorize_another_session(self):
        self.source.write_bytes(b'Plain $x_(1)$\n')
        with self.context() as old:
            candidate = old.validate()
            old.apply(candidate)
            snapshot = old.compile_snapshot()
        with self.context(source_profile=V2) as fix:
            with self.assertRaises(bridge.ProtectedCoreError):
                fix.apply(candidate)
            fix.apply(fix.validate())
            with self.assertRaises(bridge.ProtectedCoreError):
                fix.assert_compile_current(snapshot)
            current = fix.compile_snapshot()
            self.assertEqual((current.source_profile, current.grammar_version), (V2, 'closed-core-markup-v2'))

    def test_backend_cannot_replace_operand_grouping_with_other_parentheses(self):
        actual = bridge.analyze_script
        def altered(tree, path, **options):
            result = actual(tree, path, **options)
            return replace(result, edits=(Edit(0, 0, b'('), Edit(len(tree.source), len(tree.source), b')')))
        with self.context(source_profile=V2) as fix, \
             mock.patch.object(bridge, 'analyze_script', side_effect=altered):
            with self.assertRaises(bridge.ProtectedCoreError):
                fix.validate()
        self.assert_unwritten()

    def test_cli_exact_selection_and_profile_grammar_report_truth(self):
        original = self.invoke(self.cli_args(V1))
        self.assertEqual((original['status'], original['error_code']), (2, 'unsupported_grammar'))
        result = self.invoke(self.cli_args())
        self.assertEqual((result['status'], result['profile'], result['grammar_version']),
                         (0, V2, grammar_for_source_profile(V2)))
        self.assertFalse(result['compiler_invoked'])
        self.assertFalse(result['coverage']['global_complete'])
        self.assertEqual(self.source.read_bytes(), REPAIRED)
        again = self.invoke(self.cli_args())
        self.assertEqual((again['modified_paths'], again['journal_id'], again['applied_insertions']), ([], None, []))

    def test_v2_keeps_duplicate_dispatch_and_recovery_options_strict(self):
        for extra in (['--profile', V1], ['--profile=' + V2], ['--dry-run'], ['--', '--profile', V2]):
            with self.subTest(extra=extra):
                result = self.invoke(self.cli_args() + extra)
                self.assertEqual((result['status'], result['error_code']), (2, 'invalid_arguments'))
        for command in ('journal-status', 'recover', 'output-status', 'recover-output'):
            with self.subTest(command=command), \
                 mock.patch.object(offline_cli, '_root', side_effect=AssertionError('No filesystem access')):
                result = self.invoke([command, '--root', str(self.root), '--profile', V2, '--format', 'json'])
                self.assertEqual(result['status'], 2)
        self.assert_unwritten()

    def test_text_reports_identify_selected_profile_and_grammar_without_error_tracebacks(self):
        report = self.invoke(self.cli_args())
        output = io.StringIO()
        with redirect_stdout(output):
            offline_cli._print(report, False)
        self.assertIn(V2, output.getvalue())
        self.assertIn('closed-core-markup-v2', output.getvalue())
        failed_compile = offline_cli._base('compile', 3, 'compile')
        failed_compile.update(source_summary=report, error='injected failure', error_code='protected_invariant')
        output = io.StringIO()
        with redirect_stdout(output):
            offline_compile_cli._print(failed_compile, False)
        self.assertIn(V2, output.getvalue())
        self.assertIn('closed-core-markup-v2', output.getvalue())
        for command in ('fix', 'journal-status', 'recover'):
            result = offline_cli._error(offline_cli.OfflineArgumentsError('injected invalid input'), command, 'arguments')
            output = io.StringIO()
            with redirect_stdout(output):
                offline_cli._print(result, False)
            self.assertIn('invalid_arguments', output.getvalue())

    def test_korean_compile_note_is_selected_profile_limit_not_captured_evidence(self):
        for resource_profile in ('embedded-ascii-v1', 'embedded-applegothic-ko-v1'):
            args = self.cli_args(V1, command='compile')
            args[args.index('embedded-ascii-v1')] = resource_profile
            with self.subTest(profile=resource_profile):
                # The unchanged v1 source admission refuses before any compiler
                # or font capture. The note must describe a profile contract.
                report = self.invoke(args)
                self.assertEqual((report['status'], report['error_code']), (2, 'unsupported_grammar'))
                self.assertFalse(report['compiler_invoked'])
                self.assertNotIn('compile_report', report)
                self.assertIsNone(report['publication_report']['resource_report'])
                output = io.StringIO()
                with redirect_stdout(output):
                    offline_compile_cli._print(report, False)
                if resource_profile == 'embedded-ascii-v1':
                    self.assertNotIn('known_resource_profile_limitation', report)
                    self.assertNotIn('Known resource-profile limitation', output.getvalue())
                else:
                    limitation = report['known_resource_profile_limitation']
                    self.assertEqual(limitation['profile'], resource_profile)
                    self.assertIn('Korean strong/emph remains regular upright', limitation['note'])
                    self.assertIn('headings gain size but not a Korean bold face', limitation['note'])
                    self.assertIn('does not establish styled Korean fidelity', limitation['note'])
                    self.assertIn('Known resource-profile limitation', output.getvalue())
                    self.assertIn(limitation['note'], output.getvalue())
        self.assertEqual(self.source.read_bytes(), STRUCTURED)

    def test_korean_profile_note_is_absent_from_historical_recovery_and_inventory(self):
        historical = offline_compile_cli.PublicationReport('restored', 0, None, None,
            output_disposition='restored', journal_id='1' * 32)
        with mock.patch.object(offline_compile_cli, 'validate_resource_profile',
                               side_effect=AssertionError('History must not resolve a current profile')):
            inventory = self.invoke(['output-status', '--root', str(self.root), '--format', 'json'])
            with mock.patch.object(offline_compile_cli, 'recover_publication', return_value=historical):
                recovered = self.invoke(['recover-output', '--root', str(self.root), '--format', 'json',
                    '--offline-output-exclusive', '--output', 'out.pdf', '--journal-id', '1' * 32])
        for report in (inventory, recovered):
            self.assertEqual(report['status'], 0)
            self.assertNotIn('known_resource_profile_limitation', report)
            output = io.StringIO()
            with redirect_stdout(output):
                offline_compile_cli._print(report, False)
            self.assertNotIn('Known resource-profile limitation', output.getvalue())
        self.assertEqual(recovered['compiler_observation_origin'], 'historical_recovery')
        self.assertFalse(recovered['publication_report']['resource_details_available'])
        self.assertIsNone(recovered['publication_report']['resource_report'])

    def test_invalid_compile_arguments_do_not_report_a_selected_korean_profile(self):
        args = self.cli_args(command='compile')
        args[args.index('embedded-ascii-v1')] = 'embedded-applegothic-ko-v1'
        with mock.patch.object(offline_cli, '_root', side_effect=AssertionError('No filesystem access')):
            report = self.invoke(args + ['--profile', V1])
        self.assertEqual((report['status'], report['error_code']), (2, 'invalid_arguments'))
        self.assertNotIn('known_resource_profile_limitation', report)

    def test_explicit_help_names_both_exact_profiles_and_normal_cli_stays_read_only(self):
        for args in (['fix', '--help'], ['compile', '--profile', V2, '--help']):
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
                offline_cli._arguments(args, False)
            self.assertEqual(raised.exception.code, 0)
            self.assertIn(V1, output.getvalue())
            self.assertIn(V2, output.getvalue())
        for args in (['check', '--root', str(self.root), '--format', 'json'],
                     ['fix', '--root', str(self.root), '--format', 'json'],
                     ['compile', '--root', str(self.root), '--format', 'json']):
            with self.subTest(args=args):
                self.assertEqual(self.invoke(args, lint.main)['status'], 2)
        self.assert_unwritten()


if __name__ == '__main__':
    unittest.main()
