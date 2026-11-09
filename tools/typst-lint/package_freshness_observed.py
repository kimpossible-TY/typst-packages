"""Compare explicitly observed local-package files across two supplied roots.

This is sampled file evidence only. It does not discover dependencies, infer
which root the compiler selected, identify an installation's originating commit,
check upstream, or establish complete package equality. No files are written.
"""
from contextlib import ExitStack
from dataclasses import dataclass
import errno
import os
from pathlib import Path
import unicodedata

import closed_core_compile as runtime
from document_resources import _relative
from typst_lint import InputError, SafetyError, require

MAX_OBSERVED_FILES = 256
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_ROOT_BYTES = 4096
MAX_TOTAL_PATH_BYTES = 128 * 1024


@dataclass(frozen=True)
class ObservedFileComparison:
    path: str
    status: str
    checkout_sha256: str | None = None
    installed_sha256: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ObservedComparison:
    checkout_root: str
    installed_root: str
    files: tuple[ObservedFileComparison, ...]
    scope: str = 'caller-supplied-files-only'
    observation_origin_proved: bool = False
    complete_package_equality_proved: bool = False
    runtime_selection_proved: bool = False
    commit_provenance_proved: bool = False
    upstream_freshness_proved: bool = False
    filesystem_lease: bool = False


def _root(raw):
    require(type(raw) is str and raw.startswith('/') and str(Path(raw)) == raw
            and not any(component in ('.', '..') for component in raw.split('/'))
            and not any(ord(char) < 32 or ord(char) == 127 for char in raw),
            'Observed comparison requires canonical absolute roots')
    try:
        require(len(raw.encode('utf-8')) <= MAX_ROOT_BYTES, 'Observed root path is too long')
    except UnicodeError as error:
        raise InputError('Observed root path must be valid UTF-8') from error
    return Path(raw)


def _paths(paths):
    require(type(paths) in (list, tuple) and len(paths) <= MAX_OBSERVED_FILES,
            'Observed file list must be a bounded list or tuple')
    seen = set()
    spellings = {}
    total = 0
    for path in paths:
        total += _relative(path)
        parts = path.split('/')
        require(len(parts) >= 4 and parts[0] == 'local',
                'Observed paths must identify files under local/<name>/<version>/')
        key = unicodedata.normalize('NFC', path).casefold()
        require(key not in seen, 'Duplicate or case/Unicode-colliding observed paths')
        seen.add(key)
        for index in range(1, len(parts) + 1):
            spelling = '/'.join(parts[:index])
            folded = unicodedata.normalize('NFC', spelling).casefold()
            kind = 'file' if index == len(parts) else 'directory'
            prior = spellings.get(folded)
            require(prior is None or prior == (spelling, kind),
                    'Observed path components collide under case or Unicode normalization')
            spellings[folded] = spelling, kind
    require(total <= MAX_TOTAL_PATH_BYTES, 'Observed aggregate path length exceeded')
    return tuple(paths)


def _capture(path):
    try:
        _, evidence = runtime._capture(path, MAX_FILE_BYTES)
        return 'present', evidence
    except runtime.CompileSafetyError as error:
        # Only an actual ENOENT is a sampled absence. Unsafe files, denied
        # permissions and races are not silently treated as missing.
        cause = error.__cause__
        if isinstance(cause, OSError) and cause.errno == errno.ENOENT:
            return 'missing', None
        return 'unavailable', None


def _recheck(path, sampled):
    state, evidence = sampled
    if state == 'unavailable':
        return False
    if state == 'missing':
        return _capture(path)[0] == 'missing'
    try:
        runtime._same(path, evidence, MAX_FILE_BYTES)
        return True
    except runtime.CompileSafetyError:
        return False


def compare_observed(checkout_root, installed_root, paths):
    """Return per-file evidence for explicit project-observed paths only.

    The supplied list itself does not prove a compiler observed these paths.
    Each file and every named ancestor is checked without following symlinks.
    The complete batch is sampled again before return. Missing/unavailable or
    changed inputs cannot become equality evidence. No absolute runtime lookup,
    network, directory inventory, Git operation, write, or update is performed.
    """
    checkout, installed = _root(checkout_root), _root(installed_root)
    paths = _paths(paths)
    samples = []
    total = 0
    try:
        with ExitStack() as stack:
            # Keep both roots and their ancestor handles open through all reads.
            checks = []
            for root in (checkout, installed):
                _, _, check = stack.enter_context(runtime._anchored_parent(root / '.observed-root'))
                checks.append(check)
            for path in paths:
                pair = tuple(_capture(root / path) for root in (checkout, installed))
                total += sum(evidence.size for state, evidence in pair if state == 'present')
                require(total <= MAX_TOTAL_BYTES, 'Observed files exceed aggregate byte limit')
                samples.append((path, pair))
            results = []
            for path, pair in samples:
                if not all(_recheck(root / path, sample)
                           for root, sample in zip((checkout, installed), pair)):
                    results.append(ObservedFileComparison(path, 'unavailable', reason='unsafe-or-changed-during-capture'))
                    continue
                left, right = pair
                if left[0] == right[0] == 'present':
                    status = 'match' if left[1].sha256 == right[1].sha256 else 'different'
                elif left[0] == right[0] == 'missing':
                    status = 'both-missing'
                else:
                    status = 'checkout-missing' if left[0] == 'missing' else 'installed-missing'
                results.append(ObservedFileComparison(path, status,
                    left[1].sha256 if left[1] else None, right[1].sha256 if right[1] else None))
            for check in checks:
                check()
            return ObservedComparison(str(checkout), str(installed), tuple(results))
    except (OSError, runtime.CompileSafetyError) as error:
        raise SafetyError(f'Observed roots could not be safely captured: {error}') from error
