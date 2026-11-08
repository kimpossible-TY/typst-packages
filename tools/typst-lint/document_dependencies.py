"""Parse and compare compiler dependency evidence without granting authority.

Inputs are untrusted data. No compiler is invoked and no filesystem paths are
opened; a caller must independently establish the origin and resource capture.
"""
from dataclasses import dataclass
import os
from pathlib import Path
import unicodedata

from document_resources import MAX_FILES, MAX_PATH_BYTES, assert_capture
from typst_lint import SafetyError

MAX_DEPENDENCY_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class DependencyComparison:
    observed: tuple[tuple[str, str], ...]
    missing: tuple[tuple[str, str], ...]
    unobserved: tuple[tuple[str, str], ...]
    compiler_origin_proved: bool = False
    exhaustive_os_reads_proved: bool = False


def _absolute(value):
    if (type(value) is not str or not value.startswith('/') or value.startswith('//')
            or '\x00' in value or '\\' in value or str(Path(value)) != value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or any(part in ('.', '..') for part in value.split('/'))):
        raise SafetyError('Dependency roots and cwd must be canonical absolute paths')
    try:
        if len(value.encode('utf-8')) > MAX_PATH_BYTES:
            raise SafetyError('Dependency root path byte limit exceeded')
    except UnicodeError as error:
        raise SafetyError('Dependency roots and cwd must be UTF-8') from error
    return Path(value)


def parse(raw, *, cwd, roots):
    """Classify bounded Typst zero-format paths under explicit staging roots."""
    if (type(raw) is not bytes or not raw or len(raw) > MAX_DEPENDENCY_BYTES
            or not raw.endswith(b'\0')):
        raise SafetyError('Malformed or oversized dependency evidence')
    if type(roots) is not dict or not roots or len(roots) > 8:
        raise SafetyError('Explicit dependency roots are required')
    bases = {}
    for name, value in roots.items():
        if type(name) is not str or not name.isascii() or not name.isidentifier():
            raise SafetyError('Dependency root IDs must be ASCII identifiers')
        bases[name] = _absolute(value)
    workdir = _absolute(cwd)
    if not any(workdir == base or workdir.is_relative_to(base) for base in bases.values()):
        raise SafetyError('Dependency cwd is outside the selected roots')
    encoded = raw[:-1].split(b'\0')
    if not encoded or len(encoded) > MAX_FILES or any(not part for part in encoded):
        raise SafetyError('Invalid dependency record count or empty record')
    records, seen, folded = [], set(), set()
    for part in encoded:
        if len(part) > MAX_PATH_BYTES:
            raise SafetyError('Dependency path byte limit exceeded')
        try:
            name = part.decode('utf-8', errors='strict')
        except UnicodeError as error:
            raise SafetyError('Dependency path must be UTF-8') from error
        if ('\\' in name or any(ord(char) < 32 or ord(char) == 127 for char in name)):
            raise SafetyError('Dependency path contains unsupported characters')
        pieces = name.split('/')
        if name.startswith('/'):
            if name.startswith('//') or any(piece in ('', '.', '..') for piece in pieces[1:]):
                raise SafetyError('Absolute dependency path contains traversal')
        else:
            # Leading parent steps are needed for sibling package roots. Do not
            # collapse a named component followed by '..': its symlink semantics
            # cannot be determined by a pure lexical parser.
            named = False
            for piece in pieces:
                if not piece or piece == '.':
                    raise SafetyError('Dependency path is not canonical')
                if piece == '..':
                    if named:
                        raise SafetyError('Dependency path has ambiguous parent traversal')
                else:
                    named = True
        absolute = Path(os.path.normpath(name if name.startswith('/') else str(workdir / name)))
        matches = [(root, absolute.relative_to(base).as_posix()) for root, base in bases.items()
                   if absolute != base and absolute.is_relative_to(base)]
        if len(matches) != 1:
            raise SafetyError('Dependency path is outside or ambiguous among selected roots')
        record = matches[0]
        collision = record[0], unicodedata.normalize('NFC', record[1]).casefold()
        if record in seen or collision in folded:
            raise SafetyError('Duplicate or colliding dependency path')
        records.append(record)
        seen.add(record)
        folded.add(collision)
    return tuple(records)


def compare(snapshot, raw, *, cwd, roots):
    """Compare path membership with an issued capture, without proving origin."""
    assert_capture(snapshot)
    if type(roots) is not dict or set(roots) != {name for name, _ in snapshot.roots}:
        raise SafetyError('Staging root IDs differ from the resource capture')
    observed = parse(raw, cwd=cwd, roots=roots)
    captured = {(item.request.root, item.request.path) for item in snapshot.files}
    observed_set = set(observed)
    return DependencyComparison(observed, tuple(sorted(observed_set - captured)),
                                tuple(sorted(captured - observed_set)))
