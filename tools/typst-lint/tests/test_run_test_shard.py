import contextlib
import io
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import run_test_shard as runner


def make_case(module, name, method='test_case', body=None):
    case_class = type(name, (unittest.TestCase,), {
        '__module__': module,
        method: body or (lambda self: None),
    })
    return case_class(method)


class ShardPartitionTests(unittest.TestCase):
    def suite(self):
        # Deliberately differ from sorted module order, with nested classes.
        cases = [make_case(module, name) for module, name in [
            ('test_z', 'First'), ('test_z', 'Second'), ('test_a', 'Third'),
            ('test_a', 'Fourth'), ('test_m', 'Fifth'), ('test_b', 'Sixth'),
        ]]
        return unittest.TestSuite([
            unittest.TestSuite(cases[:2]),
            unittest.TestSuite([unittest.TestSuite(cases[2:4]), *cases[4:]]),
        ]), cases

    def test_two_shards_are_an_exhaustive_disjoint_union_of_original_objects(self):
        suite, cases = self.suite()
        shards = [runner.select_shard(suite, index, 2) for index in (1, 2)]
        identities = [{id(case) for case in shard.tests} for shard in shards]
        self.assertFalse(identities[0] & identities[1])
        self.assertEqual(identities[0] | identities[1], {id(case) for case in cases})
        self.assertEqual(sum(len(shard.tests) for shard in shards), len(cases))
        for shard in shards:
            self.assertEqual(shard.discovered_count, len(cases))
            self.assertEqual(shard.modules, ('test_a', 'test_b', 'test_m', 'test_z'))

    def test_sorted_modules_are_round_robin_and_discovery_order_is_retained(self):
        suite, cases = self.suite()
        first = runner.select_shard(suite, 1, 2)
        second = runner.select_shard(suite, 2, 2)
        self.assertEqual(first.selected_modules, ('test_a', 'test_m'))
        self.assertEqual(second.selected_modules, ('test_b', 'test_z'))
        self.assertEqual(first.tests, (cases[2], cases[3], cases[4]))
        self.assertEqual(second.tests, (cases[0], cases[1], cases[5]))

    def test_assignment_is_stable_across_reordering_and_repeated_selection(self):
        suite, cases = self.suite()
        for index in (1, 2):
            initial = runner.select_shard(suite, index, 2)
            self.assertEqual(initial, runner.select_shard(suite, index, 2))
            reversed_shard = runner.select_shard(unittest.TestSuite(reversed(cases)), index, 2)
            self.assertEqual(initial.selected_modules, reversed_shard.selected_modules)
            self.assertEqual(reversed_shard.tests, tuple(reversed(initial.tests)))

    def test_adding_cases_within_a_module_does_not_change_assignment(self):
        suite, cases = self.suite()
        extra = make_case('test_a', 'Extra')
        for index in (1, 2):
            before = runner.select_shard(suite, index, 2)
            after = runner.select_shard(unittest.TestSuite([*cases, extra]), index, 2)
            self.assertEqual(before.selected_modules, after.selected_modules)
            self.assertEqual(tuple(case for case in after.tests if case is not extra), before.tests)

    def test_one_shard_retains_all_cases_in_order(self):
        suite, cases = self.suite()
        self.assertEqual(runner.select_shard(suite, 1, 1).tests, tuple(cases))

    def test_three_shards_are_also_an_exhaustive_disjoint_union(self):
        suite, cases = self.suite()
        selected = [case for index in (1, 2, 3)
                    for case in runner.select_shard(suite, index, 3).tests]
        self.assertEqual(sorted(map(id, selected)), sorted(map(id, cases)))

    def test_invalid_partition_numbers_are_rejected(self):
        suite, _ = self.suite()
        for index, count in [(0, 2), (-1, 2), (3, 2), (1, 0), (1, -1)]:
            with self.subTest(index=index, count=count), self.assertRaises(runner.ShardError):
                runner.select_shard(suite, index, count)

    def test_empty_discovery_and_empty_selected_shard_are_rejected(self):
        with self.assertRaisesRegex(runner.ShardError, 'no tests'):
            runner.select_shard(unittest.TestSuite(), 1, 2)
        with self.assertRaisesRegex(runner.ShardError, 'contains no tests'):
            runner.select_shard(unittest.TestSuite([make_case('only', 'Only')]), 2, 2)

    def test_unsupported_leaf_and_custom_suite_are_rejected_without_execution(self):
        class CustomSuite(unittest.TestSuite):
            def run(self, result, debug=False):
                raise AssertionError('A custom suite must not be silently flattened')

        for suite in [unittest.TestSuite([lambda result: None]),
                      CustomSuite([make_case('only', 'Only')]),
                      unittest.TestSuite([CustomSuite([make_case('only', 'Nested')])])]:
            with self.subTest(suite=type(suite).__name__), self.assertRaises(runner.ShardError):
                runner.select_shard(suite, 1, 1)

    def test_original_module_and_class_fixtures_run_in_order_within_each_shard(self):
        events = []
        cases = []
        for module_name in ('shard_fixture_b', 'shard_fixture_a'):
            module = types.ModuleType(module_name)
            module.setUpModule = lambda name=module_name: events.append(name + ':module-up')
            module.tearDownModule = lambda name=module_name: events.append(name + ':module-down')
            patch = mock.patch.dict(sys.modules, {module_name: module})
            patch.start()
            self.addCleanup(patch.stop)
            for class_name in ('First', 'Second'):
                label = module_name + ':' + class_name
                case_class = type(class_name, (unittest.TestCase,), {
                    '__module__': module_name,
                    'setUpClass': classmethod(lambda cls, name=label: events.append(name + ':class-up')),
                    'tearDownClass': classmethod(lambda cls, name=label: events.append(name + ':class-down')),
                    'test_a': lambda self, name=label: events.append(name + ':a'),
                    'test_b': lambda self, name=label: events.append(name + ':b'),
                })
                cases.extend([case_class('test_a'), case_class('test_b')])
        suite = unittest.TestSuite(cases)
        for index, module_name in [(1, 'shard_fixture_a'), (2, 'shard_fixture_b')]:
            events.clear()
            shard = runner.select_shard(suite, index, 2)
            result = unittest.TextTestRunner(stream=io.StringIO()).run(unittest.TestSuite(shard.tests))
            self.assertTrue(result.wasSuccessful())
            self.assertEqual(result.testsRun, 4)
            expected = [module_name + ':module-up']
            for class_name in ('First', 'Second'):
                label = module_name + ':' + class_name
                expected.extend([label + ':class-up', label + ':a', label + ':b', label + ':class-down'])
            expected.append(module_name + ':module-down')
            self.assertEqual(events, expected)


class ShardDiscoveryAndCommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='unittest_shard_')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.original_path = list(sys.path)
        self.addCleanup(setattr, sys, 'path', self.original_path)

    def write_module(self, suffix, source):
        name = 'test_' + self.root.name + '_' + suffix
        (self.root / (name + '.py')).write_text(source, encoding='utf-8')
        self.addCleanup(sys.modules.pop, name, None)
        return name

    def invoke(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            status = runner.main(['--start-directory', str(self.root), *args])
        return status, output.getvalue()

    def test_standard_discovery_and_command_run_selected_cases_and_report_counts(self):
        for suffix in ('a', 'b', 'c'):
            self.write_module(suffix, 'import unittest\nclass Cases(unittest.TestCase):\n'
                              '    def test_ok(self): pass\n')
        for index, selected in [(1, 2), (2, 1)]:
            status, output = self.invoke('--shard-index', str(index), '--shard-count', '2')
            self.assertEqual(status, 0, output)
            self.assertIn(f'discovered 3 tests in 3 modules; selected {selected} tests', output)
            self.assertIn(f'Ran {selected} test', output)

    def test_skipped_cases_are_selected_and_retain_unittest_skip_semantics(self):
        self.write_module('skip', 'import unittest\nclass Cases(unittest.TestCase):\n'
                          '    @unittest.skip("original skip")\n'
                          '    def test_skip(self): raise AssertionError("must remain skipped")\n')
        status, output = self.invoke('--shard-index', '1', '--shard-count', '1')
        self.assertEqual(status, 0, output)
        self.assertIn('selected 1 tests in 1 modules', output)
        self.assertIn('original skip', output)
        self.assertIn('OK (skipped=1)', output)

    def test_module_level_skip_is_retained_in_discovery_and_execution(self):
        self.write_module('skip', 'import unittest\nraise unittest.SkipTest("module skip")\n')
        status, output = self.invoke('--shard-index', '1', '--shard-count', '1')
        self.assertEqual(status, 0, output)
        self.assertIn('discovered 1 tests', output)
        self.assertIn('module skip', output)
        self.assertIn('OK (skipped=1)', output)

    def test_failed_test_error_subtest_and_unexpected_success_return_nonzero(self):
        bodies = [
            '    def test_bad(self): self.fail("test failure")\n',
            '    def test_bad(self): raise RuntimeError("test error")\n',
            '    def test_bad(self):\n        with self.subTest(value=1): self.fail("subtest")\n',
            '    @unittest.expectedFailure\n    def test_bad(self): pass\n',
        ]
        for index, body in enumerate(bodies):
            with self.subTest(body=body):
                # Each case is isolated so a prior failure cannot mask this one.
                directory = self.root / str(index)
                directory.mkdir()
                name = 'test_failure_' + self.root.name + '_' + str(index)
                (directory / (name + '.py')).write_text(
                    'import unittest\nclass Cases(unittest.TestCase):\n' + body, encoding='utf-8')
                self.addCleanup(sys.modules.pop, name, None)
                output = io.StringIO()
                with contextlib.redirect_stderr(output):
                    status = runner.main(['--start-directory', str(directory),
                                          '--shard-index', '1', '--shard-count', '1'])
                self.assertEqual(status, 1, output.getvalue())
                self.assertIn('FAILED', output.getvalue())

    def test_import_failure_is_fatal_before_filtering_on_every_shard(self):
        self.write_module('a', 'import unittest\nclass Cases(unittest.TestCase):\n'
                          '    def test_never_run(self): raise AssertionError("should not execute")\n')
        self.write_module('b', 'raise ImportError("broken import sentinel")\n')
        for index in ('1', '2'):
            status, output = self.invoke('--shard-index', index, '--shard-count', '2')
            self.assertEqual(status, 2, output)
            self.assertIn('Unittest discovery failed', output)
            self.assertIn('broken import sentinel', output)
            self.assertNotIn('should not execute', output)
            self.assertNotIn('Ran ', output)

    def test_load_tests_error_is_fatal(self):
        self.write_module('load', 'def load_tests(loader, tests, pattern):\n'
                          '    raise RuntimeError("load_tests sentinel")\n')
        status, output = self.invoke('--shard-index', '1', '--shard-count', '1')
        self.assertEqual(status, 2, output)
        self.assertIn('load_tests sentinel', output)
        self.assertNotIn('Ran ', output)

    def test_discovery_exception_is_fatal_and_does_not_run_a_suite(self):
        with mock.patch.object(runner.unittest.TestLoader, 'discover',
                               side_effect=ImportError('discovery sentinel')):
            status, output = self.invoke('--shard-index', '1', '--shard-count', '1')
        self.assertEqual(status, 2, output)
        self.assertIn('discovery sentinel', output)
        self.assertNotIn('Ran ', output)

    def test_zero_system_exit_during_import_or_load_tests_is_fatal(self):
        for index, source in enumerate([
            'raise SystemExit(0)\n',
            'def load_tests(loader, tests, pattern):\n    raise SystemExit(0)\n',
        ]):
            with self.subTest(source=source):
                directory = self.root / str(index)
                directory.mkdir()
                name = 'test_discovery_exit_' + self.root.name + '_' + str(index)
                (directory / (name + '.py')).write_text(source, encoding='utf-8')
                self.addCleanup(sys.modules.pop, name, None)
                output = io.StringIO()
                with contextlib.redirect_stderr(output):
                    status = runner.main(['--start-directory', str(directory),
                                          '--shard-index', '1', '--shard-count', '1'])
                self.assertEqual(status, 2, output.getvalue())
                self.assertIn('discovery/selection failed', output.getvalue())
                self.assertNotIn('Ran ', output.getvalue())

    def test_zero_system_exit_in_module_or_class_fixture_is_a_failing_shard(self):
        for index, source in enumerate([
            'import unittest\ndef setUpModule(): raise SystemExit(0)\n'
            'class Cases(unittest.TestCase):\n    def test_ok(self): pass\n',
            'import unittest\nclass Cases(unittest.TestCase):\n'
            '    @classmethod\n    def setUpClass(cls): raise SystemExit(0)\n'
            '    def test_ok(self): pass\n',
        ]):
            with self.subTest(source=source):
                directory = self.root / str(index)
                directory.mkdir()
                name = 'test_fixture_exit_' + self.root.name + '_' + str(index)
                (directory / (name + '.py')).write_text(source, encoding='utf-8')
                self.addCleanup(sys.modules.pop, name, None)
                output = io.StringIO()
                with contextlib.redirect_stderr(output):
                    status = runner.main(['--start-directory', str(directory),
                                          '--shard-index', '1', '--shard-count', '1'])
                self.assertEqual(status, 1, output.getvalue())
                self.assertIn('execution aborted by SystemExit', output.getvalue())

    def test_missing_directory_empty_discovery_and_empty_selection_fail(self):
        status, output = self.invoke('--shard-index', '1', '--shard-count', '2')
        self.assertEqual(status, 2, output)
        self.assertIn('Discovery found no tests', output)
        with self.assertRaisesRegex(runner.ShardError, 'does not exist'):
            runner.discover_suite(self.root / 'missing')
        self.write_module('only', 'import unittest\nclass Cases(unittest.TestCase):\n'
                          '    def test_ok(self): pass\n')
        status, output = self.invoke('--shard-index', '2', '--shard-count', '2')
        self.assertEqual(status, 2, output)
        self.assertIn('contains no tests', output)

    def test_malformed_arguments_fail_before_discovery(self):
        arguments = [[], ['--shard-index', '1'], ['--shard-count', '2'],
                     ['--shard-index', 'x', '--shard-count', '2'],
                     ['--shard-index', '1', '--shard-count', 'x'],
                     ['--shard-index', '0', '--shard-count', '2'],
                     ['--shard-index', '-1', '--shard-count', '2'],
                     ['--shard-index', '3', '--shard-count', '2'],
                     ['--shard-index', '1', '--shard-count', '0'],
                     ['--shard-index', '1', '--shard-count', '-1'],
                     ['--shard-index', '1', '--shard-count', '2', '--unknown']]
        for args in arguments:
            with self.subTest(args=args), mock.patch.object(runner, 'discover_suite') as discover:
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                    runner.main(args)
                self.assertEqual(raised.exception.code, 2)
                discover.assert_not_called()


if __name__ == '__main__':
    unittest.main()
