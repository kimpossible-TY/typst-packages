"""Run a deterministic module shard of the complete standard unittest discovery.

Sorted defining-module names are assigned round-robin, using one-based shard
indices. Every discovered TestCase belongs to exactly one shard. Its original
object and discovery order are retained, so unittest's class/module fixtures,
skips, subtests and failure handling still run normally. No test is executed
during discovery or partitioning. All shards reject discovery errors, including
errors in modules that would otherwise belong to a different shard.

Explicit measured-v1 selection balances observed costs. Only the reviewed,
source-pinned paragraph-boundary class is divided into method groups; other
modules retain their class/module fixtures together in one worker process.
"""

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
import unittest

BALANCE_PROFILE = Path(__file__).with_name('ci-balance-v1.json')
SPLIT_MODULE = 'test_captured_text_utils_paragraph_boundary_fix'
SPLIT_CLASS = 'CapturedTextUtilsParagraphBoundaryFixTests'
SPLIT_SOURCES = (SPLIT_MODULE + '.py', 'paragraph_fix_test_support.py')
MAX_PROFILE_BYTES = 128 * 1024
MAX_REVIEWED_SOURCE_BYTES = 256 * 1024


class ShardError(ValueError):
    """Discovery or partitioning cannot safely provide a nonempty test shard."""


@dataclass(frozen=True)
class Shard:
    tests: tuple[unittest.TestCase, ...]
    discovered_count: int
    modules: tuple[str, ...]
    selected_modules: tuple[str, ...]
    balance_profile: str | None = None
    estimated_ms: int | None = None


def iter_cases(suite):
    # Custom suites can override execution semantics. Fail rather than silently
    # discard those semantics while selecting individual TestCase objects.
    if type(suite) is not unittest.TestSuite:
        raise ShardError('Only standard unittest.TestSuite discovery is supported')
    for item in suite:
        if isinstance(item, unittest.TestCase):
            yield item
        elif isinstance(item, unittest.TestSuite):
            yield from iter_cases(item)
        else:
            raise ShardError('Discovery returned an unsupported non-TestCase item')


def discover_suite(start_directory):
    directory = Path(start_directory).resolve()
    if not directory.is_dir():
        raise ShardError(f'Test directory does not exist: {directory}')
    loader = unittest.TestLoader()
    suite = loader.discover(str(directory))
    if loader.errors:
        raise ShardError('Unittest discovery failed:\n' + '\n'.join(loader.errors))
    return suite


def load_balance_profile(name):
    if name != 'measured-v1':
        raise ShardError('Unknown balance profile')
    try:
        with BALANCE_PROFILE.open('rb') as source:
            raw = source.read(MAX_PROFILE_BYTES + 1)
        if len(raw) > MAX_PROFILE_BYTES:
            raise ShardError('Balance profile exceeds its byte limit')
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ShardError('Duplicate balance profile key')
                result[key] = value
            return result
        profile = json.loads(raw, object_pairs_hook=unique_object)
    except (OSError, ValueError, RecursionError) as error:
        raise ShardError(f'Cannot read balance profile: {error}') from error
    fields = {'schema', 'profile', 'default_weight_ms', 'source_pins',
              'module_weights_ms', 'boundary_method_weights_ms', 'metadata'}
    if (type(profile) is not dict or set(profile) != fields
            or type(profile['schema']) is not int or profile['schema'] != 1
            or profile['profile'] != name or type(profile['metadata']) is not dict):
        raise ShardError('Invalid balance profile schema')
    def valid_weight(value):
        return type(value) is int and 0 < value <= 3_600_000
    if not valid_weight(profile['default_weight_ms']):
        raise ShardError('Invalid default balance weight')
    for field in ('module_weights_ms', 'boundary_method_weights_ms'):
        values = profile[field]
        if (type(values) is not dict or len(values) > 4096
                or any(type(key) is not str or not key or len(key) > 512
                       or not valid_weight(value) for key, value in values.items())):
            raise ShardError('Invalid balance weights')
    pins = profile['source_pins']
    if (type(pins) is not dict or set(pins) != set(SPLIT_SOURCES)
            or any(type(value) is not str or len(value) != 64
                   or any(c not in '0123456789abcdef' for c in value) for value in pins.values())):
        raise ShardError('Invalid reviewed source pins')
    # Check even when discovery contributes no cases from this module: a new
    # load_tests hook must not evade review by returning an empty suite.
    _verify_reviewed_sources(pins)
    return profile


def _verify_reviewed_sources(pins):
    for filename, expected_hash in pins.items():
        try:
            with Path(__file__).with_name('tests').joinpath(filename).open('rb') as source:
                raw = source.read(MAX_REVIEWED_SOURCE_BYTES + 1)
        except OSError as error:
            raise ShardError(f'Cannot read reviewed split source: {error}') from error
        if len(raw) > MAX_REVIEWED_SOURCE_BYTES or hashlib.sha256(raw).hexdigest() != expected_hash:
            raise ShardError(f'Method-split source changed; review and update its pin: {filename}')


def _verify_split_module(cases):
    module = sys.modules.get(SPLIT_MODULE)
    cls = getattr(module, SPLIT_CLASS, None)
    expected = Path(__file__).with_name('tests') / SPLIT_SOURCES[0]
    if (module is None or getattr(module, '__file__', None) is None
            or Path(module.__file__).resolve() != expected.resolve()
            or type(cls) is not type or cls.__bases__ != (unittest.TestCase,)
            or any(type(case) is not cls for case in cases)):
        raise ShardError('Unreviewed class in the method-split module')
    if any(name in vars(module) for name in ('setUpModule', 'tearDownModule', 'load_tests')):
        raise ShardError('Method splitting refuses module fixtures or load_tests')
    hooks = ('setUpClass', 'tearDownClass', 'run', 'debug', '__call__', '__init__',
             'id', 'doClassCleanups')
    if any(name in vars(cls) for name in hooks):
        raise ShardError('Method splitting refuses class fixtures or execution hooks')


def _balanced_selection(cases, modules, shard_index, shard_count, profile):
    grouped = {module: [] for module in modules}
    for case in cases:
        grouped[type(case).__module__].append(case)
    if SPLIT_MODULE in grouped or SPLIT_MODULE in sys.modules:
        _verify_split_module(grouped.get(SPLIT_MODULE, ()))
    units = []
    for module, members in grouped.items():
        if module == SPLIT_MODULE:
            methods = {}
            for case in members:
                methods.setdefault(case.id(), []).append(case)
            for identity, method_cases in methods.items():
                weight = profile['boundary_method_weights_ms'].get(identity, profile['default_weight_ms'])
                units.append((('case', identity), weight * len(method_cases), method_cases))
        else:
            weight = profile['module_weights_ms'].get(module,
                profile['default_weight_ms'] * len(members))
            units.append((('module', module), weight, members))
    loads = [0] * shard_count
    owners = {}
    for _, weight, members in sorted(units, key=lambda unit: (-unit[1], unit[0])):
        worker = min(range(shard_count), key=lambda index: (loads[index], index))
        loads[worker] += weight
        for case in members:
            owners[id(case)] = worker
    selected = tuple(case for case in cases if owners[id(case)] == shard_index - 1)
    selected_modules = tuple(sorted({type(case).__module__ for case in selected}))
    return selected, selected_modules, loads[shard_index - 1]


def select_shard(suite, shard_index, shard_count, *, balance_profile=None):
    if shard_count < 1 or not 1 <= shard_index <= shard_count:
        raise ShardError('Require shard-count >= 1 and 1 <= shard-index <= shard-count')
    cases = tuple(iter_cases(suite))
    if not cases:
        raise ShardError('Discovery found no tests')
    modules = tuple(sorted({type(case).__module__ for case in cases}))
    estimated_ms = None
    if balance_profile is None:
        selected_modules = modules[shard_index - 1::shard_count]
        selected_names = set(selected_modules)
        selected = tuple(case for case in cases if type(case).__module__ in selected_names)
    else:
        profile = load_balance_profile(balance_profile)
        selected, selected_modules, estimated_ms = _balanced_selection(
            cases, modules, shard_index, shard_count, profile)
    if not selected:
        raise ShardError(f'Shard {shard_index}/{shard_count} contains no tests')
    return Shard(selected, len(cases), modules, selected_modules, balance_profile, estimated_ms)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shard-index', type=int, required=True)
    parser.add_argument('--shard-count', type=int, required=True)
    parser.add_argument('--start-directory', type=Path,
                        default=Path(__file__).resolve().with_name('tests'))
    parser.add_argument('--balance-profile', choices=('measured-v1',),
                        help='Explicit measured scheduling with one reviewed method-split class')
    args = parser.parse_args(argv)
    if args.shard_count < 1 or not 1 <= args.shard_index <= args.shard_count:
        parser.error('Require shard-count >= 1 and 1 <= shard-index <= shard-count')
    try:
        shard = select_shard(discover_suite(args.start_directory),
                             args.shard_index, args.shard_count, balance_profile=args.balance_profile)
    except (Exception, SystemExit) as error:
        print(f'Test shard discovery/selection failed: {error}', file=sys.stderr, flush=True)
        return 2
    print(f'Shard {args.shard_index}/{args.shard_count}: discovered '
          f'{shard.discovered_count} tests in {len(shard.modules)} modules; selected '
          f'{len(shard.tests)} tests in {len(shard.selected_modules)} modules',
          file=sys.stderr, flush=True)
    if shard.balance_profile is not None:
        print(f'Balance profile {shard.balance_profile}: estimated relative load '
              f'{shard.estimated_ms} ms; tests remain in discovery order', file=sys.stderr, flush=True)
    try:
        result = unittest.TextTestRunner(
            verbosity=2, warnings=None if sys.warnoptions else 'default',
        ).run(unittest.TestSuite(shard.tests))
    except SystemExit as error:
        # unittest records exits in test methods, but class/module fixtures can
        # propagate them. Even SystemExit(0) is an incomplete, failing shard.
        print(f'Test shard execution aborted by SystemExit: {error}', file=sys.stderr, flush=True)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
