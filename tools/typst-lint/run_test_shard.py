"""Run a deterministic module shard of the complete standard unittest discovery.

Sorted defining-module names are assigned round-robin, using one-based shard
indices. Every discovered TestCase belongs to exactly one shard. Its original
object and discovery order are retained, so unittest's class/module fixtures,
skips, subtests and failure handling still run normally. No test is executed
during discovery or partitioning. All shards reject discovery errors, including
errors in modules that would otherwise belong to a different shard.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
import unittest


class ShardError(ValueError):
    """Discovery or partitioning cannot safely provide a nonempty test shard."""


@dataclass(frozen=True)
class Shard:
    tests: tuple[unittest.TestCase, ...]
    discovered_count: int
    modules: tuple[str, ...]
    selected_modules: tuple[str, ...]


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


def select_shard(suite, shard_index, shard_count):
    if shard_count < 1 or not 1 <= shard_index <= shard_count:
        raise ShardError('Require shard-count >= 1 and 1 <= shard-index <= shard-count')
    cases = tuple(iter_cases(suite))
    if not cases:
        raise ShardError('Discovery found no tests')
    modules = tuple(sorted({type(case).__module__ for case in cases}))
    selected_modules = modules[shard_index - 1::shard_count]
    selected_names = set(selected_modules)
    selected = tuple(case for case in cases if type(case).__module__ in selected_names)
    if not selected:
        raise ShardError(f'Shard {shard_index}/{shard_count} contains no tests')
    return Shard(selected, len(cases), modules, selected_modules)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shard-index', type=int, required=True)
    parser.add_argument('--shard-count', type=int, required=True)
    parser.add_argument('--start-directory', type=Path,
                        default=Path(__file__).resolve().with_name('tests'))
    args = parser.parse_args(argv)
    if args.shard_count < 1 or not 1 <= args.shard_index <= args.shard_count:
        parser.error('Require shard-count >= 1 and 1 <= shard-index <= shard-count')
    try:
        shard = select_shard(discover_suite(args.start_directory),
                             args.shard_index, args.shard_count)
    except (Exception, SystemExit) as error:
        print(f'Test shard discovery/selection failed: {error}', file=sys.stderr, flush=True)
        return 2
    print(f'Shard {args.shard_index}/{args.shard_count}: discovered '
          f'{shard.discovered_count} tests in {len(shard.modules)} modules; selected '
          f'{len(shard.tests)} tests in {len(shard.selected_modules)} modules',
          file=sys.stderr, flush=True)
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
