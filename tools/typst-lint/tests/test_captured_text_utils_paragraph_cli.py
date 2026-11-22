"""Public paragraph selector/argument boundaries before any side effects.

Only the fix success case admits the real parser. Compile/runtime behavior is
covered by the separate deterministic compiler/publisher and recovery modules.
"""
from contextlib import redirect_stdout, redirect_stderr, ExitStack
import io
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import captured_text_utils_paragraph_core as core
import captured_text_utils_paragraph_inputs as inputs
import closed_core_compile as runtime
import offline_cli
import pdf_publication_store as output_store
import source_profiles
import transactions
import typst_lint as lint
from syntax_client import EXECUTABLE
from paragraph_fix_test_support import EXPECTED, evidence, state
from test_captured_text_utils_paragraph_compile import ParagraphCompileFixtures

PROFILE = 'captured-text-utils-paragraph-v1'


def arguments(command='compile'):
    args = [command, '--root', '/owned/consumer', '--format', 'json', '--profile', PROFILE,
            '--offline-exclusive', '--target', 'nested with spaces/main.typ',
            '--package-path', '/owned/packages']
    if command == 'compile':
        args += ['--resource-profile', 'embedded-ascii-v1', '--offline-output-exclusive',
                 '--output', 'build with spaces/owned.pdf']
    return args


def replace_argument(argv, option, value):
    argv = list(argv)
    argv[argv.index(option) + 1] = value
    return argv


class ParagraphCliArgumentTests(unittest.TestCase):
    def invoke(self, argv, *, gate=False):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            status = lint.main(argv, gate=gate)
        self.assertEqual(errors.getvalue(), '')
        result = json.loads(output.getvalue())
        self.assertEqual(result['status'], status)
        return result

    def test_offline_selection_does_not_widen_single_source_registry(self):
        self.assertEqual(source_profiles.CAPTURED_TEXT_UTILS_PARAGRAPH_SOURCE_PROFILE, PROFILE)
        self.assertEqual(source_profiles.KNOWN_SOURCE_PROFILES, {'closed-core-v1', 'structured-core-v1'})
        self.assertEqual(source_profiles.KNOWN_OFFLINE_SOURCE_PROFILES,
                         {'closed-core-v1', 'structured-core-v1', 'captured-relative-core-v1', PROFILE})
        self.assertTrue(source_profiles.is_offline_source_profile(PROFILE))
        for profile in (None, True, [], PROFILE.upper(), ' ' + PROFILE, PROFILE + ' ', 'paragraph'):
            self.assertFalse(source_profiles.is_offline_source_profile(profile))
        for selector in (source_profiles.validate_source_profile, source_profiles.grammar_for_source_profile):
            with self.assertRaises(ValueError):
                selector(PROFILE)
        for command in ('fix', 'compile'):
            parsed = offline_cli._arguments(arguments(command), False)
            self.assertEqual(parsed.profile, PROFILE)
            self.assertEqual(parsed.package_path, Path('/owned/packages'))

    def test_missing_duplicate_alternate_raw_arguments_refuse_before_filesystem_or_leases(self):
        variants = []
        good = arguments()
        for option in ('--profile', '--offline-exclusive', '--target', '--package-path',
                       '--resource-profile', '--offline-output-exclusive', '--output'):
            args = list(good)
            index = args.index(option)
            del args[index:index + (1 if option.startswith('--offline-') else 2)]
            variants.append((args, False))
        for option in ('--root', '--profile', '--target', '--package-path', '--resource-profile', '--output', '--format'):
            index = good.index(option)
            variants += [(good + good[index:index + 2], False),
                         (good + [option + '=' + good[index + 1]], False)]
        for option in ('--offline-exclusive', '--offline-output-exclusive'):
            variants.append((good + [option], False))
        for value in ('packages', './packages', '../packages', '~/packages', '/owned//packages',
                      '/owned/./packages', '/owned/a/../packages', '/owned/packages/',
                      '/owned\\packages', '/owned/pack\0ages'):
            variants.append((replace_argument(good, '--package-path', value), False))
        for value in ('./main.typ', '../main.typ', '/owned/main.typ', 'a//main.typ',
                      'a/../main.typ', 'main.TYP', 'main.typ/', 'a\\main.typ'):
            variants.append((replace_argument(good, '--target', value), False))
        for value in ('embedded-applegothic-ko-v1', '', 'embedded-ascii-v1 ', PROFILE):
            variants.append((replace_argument(good, '--resource-profile', value), False))
        for value in (PROFILE.upper(), ' ' + PROFILE, PROFILE + ' ', 'paragraph'):
            variants.append((replace_argument(good, '--profile', value), False))
        for tail in (['--dry-run'], ['--', '--open'], ['extra.typ'], ['--compiler-path', '/bin/true'],
                     ['--deadline-seconds', '120'], ['--font-path', '/owned/font'],
                     ['--journal-id', 'a' * 32], ['--package', '/owned/packages']):
            variants.append((good + tail, False))
        variants.append((good, True))
        with ExitStack() as patches:
            for module, name in ((Path, 'resolve'), (Path, 'stat'),
                    (transactions.OfflineExclusive, 'assert_editors_watchers_closed_and_no_other_writers'),
                    (output_store.OfflineOutputExclusive, 'assert_exporters_watchers_closed_and_no_other_writers'),
                    (runtime, '_capture_compiler')):
                patches.enter_context(mock.patch.object(module, name, side_effect=AssertionError('argument validation side effect')))
            for argv, gate in variants:
                with self.subTest(argv=argv, gate=gate):
                    result = self.invoke(argv, gate=gate)
                    self.assertEqual((result['status'], result['phase'], result['error_code']),
                                     (2, 'arguments', 'invalid_arguments'))
                    self.assertFalse(result['compiler_invoked'])
                    self.assertIsNone(result['compiler_status'])

    def test_package_path_remains_forbidden_for_old_profiles_history_check_gate_and_dryrun(self):
        variants = []
        for command in ('fix', 'compile'):
            for profile in ('closed-core-v1', 'structured-core-v1', 'captured-relative-core-v1'):
                variants.append((replace_argument(arguments(command), '--profile', profile), False))
        for command in ('journal-status', 'recover', 'output-status', 'recover-output'):
            variants.append(([command, '--root', '/owned/consumer', '--format', 'json',
                              '--package-path', '/owned/packages'], False))
            variants.append(([command, '--root', '/owned/consumer', '--format', 'json',
                              '--profile', PROFILE], False))
        variants += [(['check'] + arguments('fix')[1:], False),
                     (arguments('fix') + ['--dry-run'], False), (arguments('compile'), True)]
        # Supplying the old package option after an inert compiler tail must not
        # make a new offline authority option disappear from validation.
        variants.append((['compile', '--root', '/owned/consumer', '--format', 'json', '--',
                          '--profile', PROFILE, '--package-path', '/owned/packages'], False))
        with mock.patch.object(Path, 'resolve', side_effect=AssertionError('filesystem')):
            for argv, gate in variants:
                with self.subTest(argv=argv, gate=gate):
                    result = self.invoke(argv, gate=gate)
                    self.assertEqual((result['status'], result['phase'], result['error_code']),
                                     (2, 'arguments', 'invalid_arguments'))


@unittest.skipUnless(EXECUTABLE.is_file(), 'Build pinned syntax adapter first')
class ParagraphCliFixSummaryTests(ParagraphCompileFixtures, unittest.TestCase):
    def test_public_fix_exact_edit_and_idempotent_summary_without_compile_or_package_writes(self):
        f = self.fixture()
        before = tuple(state(p) for p in evidence(f))
        args = arguments('fix')
        args = replace_argument(args, '--root', str(f.root))
        args = replace_argument(args, '--target', f.target)
        args = replace_argument(args, '--package-path', str(f.package_path))
        def invoke():
            output = io.StringIO()
            with redirect_stdout(output):
                status = lint.main(args)
            result = json.loads(output.getvalue())
            self.assertEqual(result['status'], status)
            return result
        with mock.patch.object(inputs, 'CATALOG', f.catalog), \
                mock.patch.object(runtime, '_capture_compiler', side_effect=AssertionError('fix captured compiler')):
            result = invoke()
            self.assertEqual(result['status'], 0, result)
            self.assertFalse(result['compiler_invoked'])
            self.assertIsNone(result['compiler_status'])
            self.assertEqual(result['profile'], PROFILE)
            self.assertEqual(result['root_grammar_version'], core.GRAMMAR_VERSION)
            self.assertEqual(len(result['required_rule_ids']), 33)
            self.assertEqual(len(result['evaluated_rule_ids']), 5)
            self.assertEqual(len(result['not_applicable']), 28)
            self.assertEqual(result['modified_paths'], [f.target])
            self.assertEqual(len(result['applied_edits']), 1)
            self.assertEqual(result['applied_edits'][0]['replacement'], 'T')
            self.assertEqual(result['applied_edits'][0]['end'] - result['applied_edits'][0]['start'], 1)
            self.assertNotIn('applied_insertions', result)
            self.assertNotIn('sources', result)
            self.assertNotIn('import_edges', result)
            self.assertNotIn('module_grammar_version', result)
            self.assertEqual(result['package_identity']['namespace'], 'local')
            self.assertEqual(result['package_identity']['name'], 'text-utils')
            self.assertEqual(result['package_identity']['version'], '0.1.2')
            self.assertEqual(len(result['package_identity']['files']), 2)
            self.assertFalse(result['coverage']['global_complete'])
            self.assertTrue(result['coverage']['input_profile_complete'])
            source = state(f.source)
            again = invoke()
            self.assertEqual(again['status'], 0, again)
            self.assertIsNone(again['journal_id'])
            self.assertEqual(again['applied_edits'], [])
            self.assertEqual(again['modified_paths'], [])
            self.assertEqual(state(f.source), source)
        self.assertEqual(f.source.read_bytes(), EXPECTED)
        self.assertEqual(tuple(state(p) for p in evidence(f)), before)


if __name__ == '__main__':
    unittest.main()
