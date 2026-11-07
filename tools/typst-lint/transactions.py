"""Offline-only protected transactions, used by the explicit closed-core CLI profile.

Ordinary CLI routes remain read-only and cannot authorize this writer.

This is a trusted-engine API, not a way for a caller's JSON/boolean to certify
analysis. ``validate`` invokes an in-process analyzer over immutable graph views
and mints a session-bound token. Production callers must supply the *entire*
required rule set and a real complete analyzer; fixture analyzers prove only this
storage protocol. No parser, compiler, editor detection, or editor API lives here.

The POSIX directory flock coordinates this library's runs, even with different
journal stores. It is NOT a compare-and-swap operation or an arbitrary editor
lock. OfflineExclusive is the operator's explicit assertion that all editors,
watchers, and other writers are stopped for the whole context. Violating that
contract can still race a rename. Every caller for a root MUST use the same
configured private journal directory so that crash recovery can be discovered.
Keep that directory (including completed journals) until recovery is no longer
needed. Missing/deleted/forged journals cannot be detected by this library.

Only existing allowlisted UTF-8 source files are replaced. Dependencies/config
are read-only evidence, including explicitly absent optional inputs. Original
bytes and postimages are journaled outside the source tree. Replacement is
atomic PER FILE, never across files. Recovery replaces only recognized postimage
inodes/bytes/modes; later changes are preserved and require explicit resolution.
Successful source fixes are never automatically undone for compiler failures.
"""
from __future__ import annotations

import base64
import ctypes
import errno
from functools import lru_cache
from dataclasses import dataclass, field, replace
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from types import MappingProxyType
from typing import Callable, Mapping, NamedTuple
import uuid

try:
    import fcntl
except ImportError:  # Explicitly unsupported rather than a no-op lock.
    fcntl = None

from edit_plan import Edit, plan
from typst_lint import SafetyError


class TransactionError(SafetyError):
    """A protected-source precondition failed; no compilation is authorized."""


class SourceEncodingError(TransactionError):
    """An initial UTF-8 source capture cannot be admitted as profile input."""


@dataclass(frozen=True)
class ReadLimits:
    """Byte ceilings, including on revalidation and explicit crash recovery.

    Capture totals include source/evidence bytes and extended-attribute names
    (including their NUL terminators) and values. They apply to each graph view,
    not cumulatively to repeated reads of the same view. Journal totals apply
    to the manifest plus events of each individual journal. Directory enumeration
    separately caps each namespace/journal count without first allocating an
    unbounded list. Zero permits only
    empty content. These bounds are not a total process-memory/time guarantee.
    """
    max_source_bytes: int = 1024 * 1024
    max_evidence_bytes: int = 4 * 1024 * 1024
    max_capture_bytes: int = 16 * 1024 * 1024
    max_metadata_bytes: int = 256 * 1024
    max_journal_bytes: int = 64 * 1024 * 1024
    max_journal_total_bytes: int = 128 * 1024 * 1024
    max_directory_entries: int = 100_000

    def __post_init__(self):
        for name, value in vars(self).items():
            if type(value) is not int or value < 0:
                raise TransactionError(f'Read limit {name} must be a nonnegative integer')


DEFAULT_READ_LIMITS = ReadLimits()
_READ_CHUNK_BYTES = 64 * 1024


@dataclass
class _ReadBudget:
    remaining: int

    def consume(self, amount: int, label: object) -> None:
        _fail(0 <= amount <= self.remaining, f'Aggregate read budget exceeded: {label}')
        self.remaining -= amount


class RecoveryRequired(TransactionError):
    def __init__(self, journals: tuple[str, ...]):
        self.journals = journals
        super().__init__('Explicit recovery required for journals: ' + ', '.join(journals))


class TransactionFailure(TransactionError):
    def __init__(self, reason: str, report: 'RecoveryReport'):
        self.report = report
        super().__init__(f'{reason}; explicit recovery required: {report.journal}')


_LEASE_SEAL = object()
_TOKEN_SEAL = object()
_COMMITTED_SEAL = object()


@dataclass(frozen=True, init=False)
class OfflineExclusive:
    """An assertion, NOT evidence that this process inspected editor buffers."""
    _seal: object = field(repr=False)

    @classmethod
    def assert_editors_watchers_closed_and_no_other_writers(cls) -> 'OfflineExclusive':
        instance = object.__new__(cls)
        object.__setattr__(instance, '_seal', _LEASE_SEAL)
        return instance


@dataclass(frozen=True)
class EvidenceInput:
    path: Path
    allow_missing: bool = False
    # Opt in to the full source-like reader policy, not decoding alone:
    # source-byte bound, UTF-8, owner/group, ACL and filesystem-flag checks.
    utf8: bool = False


@dataclass(frozen=True)
class Fingerprint:
    device: int
    inode: int
    mode: int
    uid: int
    gid: int
    sha256: str
    metadata_sha256: str


@dataclass(frozen=True)
class _File:
    path: Path
    data: bytes | None
    fingerprint: Fingerprint | None
    parent: tuple[int, int]
    times: tuple[int, int] | None
    xattrs: tuple[tuple[str, bytes], ...] = ()
    # Optional live descriptor-chain identity. Never serialized into schema 1.
    parents: tuple[tuple[str, tuple[int, int]], ...] = ()


@dataclass(frozen=True)
class CaptureFingerprints:
    """Reporting view of actual retained captures, never fresh stat authority."""
    sources: Mapping[str, Fingerprint | None]
    evidence: Mapping[str, Fingerprint | None]
    directories: Mapping[str, tuple[int, int]]


@dataclass(frozen=True)
class GraphSnapshot:
    root: Path
    sources: Mapping[str, bytes]
    evidence: Mapping[str, bytes | None]


@dataclass(frozen=True)
class SourceEdit:
    path: str
    edit: Edit


@dataclass(frozen=True)
class GraphAnalysis:
    """Trusted analyzer output with explicit coverage, never user attestations.

    ``findings`` are stable semantic IDs, not byte-offset-based identifiers.
    ``parsed_sources`` must include every selected source, including unchanged
    files. The trusted engine is responsible for dependency graph completeness,
    resource interpretation, correct parser results, and accurate rule coverage.
    An engine that lies cannot be made trustworthy by a filesystem transaction.
    """
    implemented_rules: frozenset[str]
    evaluated_rules: frozenset[str]
    parsed_sources: frozenset[str]
    examined_evidence: frozenset[str]
    parse_errors: tuple[str, ...] = ()
    unsupported: tuple[str, ...] = ()
    findings: frozenset[str] = frozenset()
    edits: tuple[SourceEdit, ...] = ()


@dataclass(frozen=True, init=False)
class ValidatedCandidate:
    sources: Mapping[str, bytes]
    findings: frozenset[str]
    passes: int
    _owner: object = field(repr=False)
    _seal: object = field(repr=False)


class _CandidateFacts(NamedTuple):
    """Issuance-owned facts, independent of the caller-visible token's fields."""
    source_view: Mapping[str, bytes]
    sources: tuple[tuple[str, bytes], ...]
    findings: frozenset[str]
    passes: int
    owner: object
    seal: object


@dataclass(frozen=True, slots=True, init=False, eq=False)
class CommittedSnapshot:
    """Opaque live-session receipt for already verified committed source bytes.

    These views share immutable captured bytes. They are not a serialization or
    an assertion about compiler resources. Only the issuing active transaction
    can guard this receipt; constructing an equivalent value grants nothing.
    """
    root: Path
    sources: Mapping[str, bytes] = field(repr=False)
    evidence: Mapping[str, bytes | None] = field(repr=False)
    _owner: object = field(repr=False)
    _seal: object = field(repr=False)

    def __init__(self, *args, **kwargs):
        raise TypeError('CommittedSnapshot is issued only after successful apply')


@dataclass(frozen=True)
class ApplyResult:
    journal: str | None
    modified: tuple[str, ...]
    unresolved_findings: frozenset[str]


@dataclass(frozen=True)
class RecoveryReport:
    journal: str
    restored: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    preserved: tuple[str, ...] = ()
    conflicts: Mapping[str, Fingerprint] = field(default_factory=lambda: MappingProxyType({}))
    errors: tuple[str, ...] = ()
    complete: bool = False


def _fail(condition: bool, message: str) -> None:
    if not condition:
        raise TransactionError(message)


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _relative(value: str) -> str:
    _fail(isinstance(value, str) and bool(value) and not value.startswith('/')
          and '\\' not in value and '\x00' not in value
          and all(p not in ('', '.', '..') for p in value.split('/')),
          f'Expected canonical root-relative path: {value!r}')
    return value


def _absolute(value: os.PathLike | str) -> Path:
    raw = os.fspath(value)
    _fail(isinstance(raw, str) and raw.startswith('/') and '\x00' not in raw
          and os.path.normpath(raw) == raw and not raw.startswith('//'),
          f'Expected canonical absolute path: {raw!r}')
    return Path(raw)


def _directory(path: Path, *, identities: list | None = None) -> int:
    """Open every component without following symlinks, including ancestors."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open('/', flags)
    try:
        current = Path('/')
        if identities is not None:
            identities.append((str(current), _identity(os.fstat(fd))))
        for name in path.parts[1:]:
            next_fd = os.open(name, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            current = current / name
            if identities is not None:
                identities.append((str(current), _identity(os.fstat(fd))))
        return fd
    except BaseException:
        os.close(fd)
        raise


def _identity(s: os.stat_result) -> tuple[int, int]:
    return s.st_dev, s.st_ino


def _stable(s: os.stat_result) -> tuple:
    return (s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_size,
            s.st_mtime_ns, s.st_ctime_ns, s.st_uid, s.st_gid, getattr(s, 'st_flags', 0))


@lru_cache(maxsize=1)
def _darwin_metadata_api():
    # System libc only, via the Python standard library. No installed adapter.
    libc = ctypes.CDLL(None, use_errno=True)
    libc.flistxattr.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
    libc.flistxattr.restype = ctypes.c_ssize_t
    libc.fgetxattr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    libc.fgetxattr.restype = ctypes.c_ssize_t
    libc.fsetxattr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int]
    libc.fsetxattr.restype = ctypes.c_int
    libc.fremovexattr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    libc.fremovexattr.restype = ctypes.c_int
    libc.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
    libc.acl_get_fd_np.restype = ctypes.c_void_p
    libc.acl_get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    libc.acl_get_entry.restype = ctypes.c_int
    libc.acl_free.argtypes = [ctypes.c_void_p]
    libc.acl_free.restype = ctypes.c_int
    return libc


@lru_cache(maxsize=1)
def _linux_metadata_api():
    libc = ctypes.CDLL(None, use_errno=True)
    libc.flistxattr.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]
    libc.flistxattr.restype = ctypes.c_ssize_t
    libc.fgetxattr.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t]
    libc.fgetxattr.restype = ctypes.c_ssize_t
    return libc


def _xattrs(fd: int, *, max_bytes: int = DEFAULT_READ_LIMITS.max_metadata_bytes) -> tuple[tuple[str, bytes], ...]:
    _fail(type(max_bytes) is int and max_bytes >= 0, 'Invalid metadata read limit')
    if sys.platform == 'darwin':
        libc = _darwin_metadata_api()
        list_args, get_args = (0,), (0, 0)
    elif sys.platform == 'linux':
        libc = _linux_metadata_api()
        list_args, get_args = (), ()
    else:
        raise TransactionError('Metadata-safe replacements are supported only on macOS and Linux')
    count = libc.flistxattr(fd, None, 0, *list_args)
    _fail(count >= 0, 'Cannot inspect extended attributes')
    _fail(count <= max_bytes, 'Metadata read limit exceeded (attribute names)')
    # An empty buffer is passed with its queried size of zero; a concurrent
    # addition is detected by the return value, without allocating its size.
    names = ctypes.create_string_buffer(count)
    _fail(libc.flistxattr(fd, names, count, *list_args) == count,
          'Extended attributes changed while reading')
    remaining = max_bytes - count
    result = []
    for name in names.raw.rstrip(b'\x00').split(b'\x00') if count else ():
        size = libc.fgetxattr(fd, name, None, 0, *get_args)
        _fail(size >= 0, 'Cannot read extended attribute')
        _fail(size <= remaining, 'Metadata read limit exceeded (attribute values)')
        value = ctypes.create_string_buffer(size)
        _fail(libc.fgetxattr(fd, name, value, size, *get_args) == size,
              'Extended attribute changed while reading')
        result.append((os.fsdecode(name), value.raw))
        remaining -= size
    return tuple(sorted(result))


def _metadata_size(values: tuple[tuple[str, bytes], ...]) -> int:
    return sum(len(os.fsencode(name)) + 1 + len(value) for name, value in values)


def _xattr_hash(values: tuple[tuple[str, bytes], ...]) -> str:
    return _hash(_json_bytes([[name, base64.b64encode(value).decode()] for name, value in values]))


def _copy_xattrs(fd: int, wanted: tuple[tuple[str, bytes], ...], *,
                 max_bytes: int = DEFAULT_READ_LIMITS.max_metadata_bytes) -> None:
    """Copy metadata only onto our new staging inode, never change the source."""
    _fail(_metadata_size(wanted) <= max_bytes, 'Metadata read limit exceeded')
    old, desired = dict(_xattrs(fd, max_bytes=max_bytes)), dict(wanted)
    for name in old.keys() - desired.keys():
        if sys.platform == 'darwin':
            _fail(_darwin_metadata_api().fremovexattr(fd, os.fsencode(name), 0) == 0,
                  f'Cannot preserve absence of extended attribute: {name}')
        else:
            os.removexattr(fd, name)
    for name, value in desired.items():
        if old.get(name) == value:
            continue
        if sys.platform == 'darwin':
            _fail(_darwin_metadata_api().fsetxattr(fd, os.fsencode(name), value, len(value), 0, 0) == 0,
                  f'Cannot preserve extended attribute: {name}')
        else:
            os.setxattr(fd, name, value)
    _fail(_xattrs(fd, max_bytes=max_bytes) == wanted, 'Staged extended attributes differ from the baseline')


def _plain_metadata(fd: int, path: Path, *,
                    max_bytes: int = DEFAULT_READ_LIMITS.max_metadata_bytes) -> None:
    """Refuse ACLs/flags we cannot safely copy; xattrs are copied and checked.

    POSIX mode, UID, GID and extended attributes are preserved. Modification /
    change timestamps are deliberately new; birth/access times are not preserved.
    """
    value = os.fstat(fd)
    _fail(getattr(value, 'st_flags', 0) == 0, f'Filesystem flags unsupported: {path}')
    if sys.platform == 'darwin':
        libc = _darwin_metadata_api()
        ctypes.set_errno(0)
        acl = libc.acl_get_fd_np(fd, 0x00000100)  # ACL_TYPE_EXTENDED, sys/acl.h
        if not acl and ctypes.get_errno() == errno.ENOENT:
            return  # Existing open descriptor with no extended ACL on macOS.
        _fail(bool(acl), f'Cannot inspect ACL: {path}')
        try:
            entry = ctypes.c_void_p()
            ctypes.set_errno(0)
            result = libc.acl_get_entry(acl, 0, ctypes.byref(entry))  # ACL_FIRST_ENTRY
            _fail(result == -1 and ctypes.get_errno() == errno.EINVAL,
                  f'Extended ACL unsupported or unreadable: {path}')
        finally:
            libc.acl_free(acl)
    elif sys.platform == 'linux' and hasattr(os, 'listxattr'):
        _fail(not any(name.startswith('system.posix_acl_') for name, _ in _xattrs(fd, max_bytes=max_bytes)),
              f'Extended ACL unsupported: {path}')
        size = ctypes.sizeof(ctypes.c_ulong)
        _fail(size in (4, 8), 'Unsupported Linux inode flag ABI')
        buffer = bytearray(size)
        try:
            fcntl.ioctl(fd, 0x80006601 | (size << 16), buffer, True)
        except OSError as error:
            raise TransactionError(f'Cannot inspect Linux inode flags: {path}') from error
        flags = int.from_bytes(buffer, sys.byteorder)
        _fail(flags & ~(0x00080000 | 0x10000000) == 0, f'Filesystem flags unsupported: {path}')
    else:
        raise TransactionError('Metadata-safe replacements are supported only on macOS and Linux')


def _bounded_bytes(fd: int, maximum: int, label: object, *, before=None) -> bytes:
    """Never request/retain more than maximum + one growth-detection byte."""
    _fail(type(maximum) is int and maximum >= 0, 'Invalid byte read limit')
    before = os.fstat(fd) if before is None else before
    _fail(before.st_size <= maximum, f'Byte read limit exceeded: {label}')
    data = bytearray()
    while True:
        block = os.read(fd, min(_READ_CHUNK_BYTES, maximum - len(data) + 1))
        if not block:
            break
        _fail(len(block) <= maximum - len(data), f'Byte read limit exceeded while reading: {label}')
        data.extend(block)
    return bytes(data)


def _read(path: Path, *, missing: bool = False, utf8: bool = False,
          limits: ReadLimits = DEFAULT_READ_LIMITS, budget: _ReadBudget | None = None,
          retain_ancestry: bool = False) -> _File:
    budget = _ReadBudget(limits.max_capture_bytes) if budget is None else budget
    maximum = min(limits.max_source_bytes if utf8 else limits.max_evidence_bytes, budget.remaining)
    parents = []
    parent_fd = (_directory(path.parent, identities=parents) if retain_ancestry
                 else _directory(path.parent))
    try:
        parent = _identity(os.fstat(parent_fd))
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=parent_fd)
        except FileNotFoundError:
            if missing:
                return _File(path, None, None, parent, None, parents=tuple(parents))
            raise
        try:
            before = os.fstat(fd)
            _fail(stat.S_ISREG(before.st_mode), f'Not a regular file: {path}')
            _fail(before.st_nlink == 1, f'Hard-linked file refused: {path}')
            _fail(before.st_size <= maximum, f'Byte read limit exceeded: {path}')
            metadata_maximum = min(limits.max_metadata_bytes, budget.remaining - before.st_size)
            if utf8:
                _fail(before.st_uid == os.geteuid(), f'Source is not owned by the effective user: {path}')
                _fail(before.st_gid in {*os.getgroups(), os.getegid()}, f'Source group cannot be preserved: {path}')
                _plain_metadata(fd, path, max_bytes=metadata_maximum)
            attributes = _xattrs(fd, max_bytes=metadata_maximum)
            metadata_size = _metadata_size(attributes)
            data = _bounded_bytes(fd, min(maximum, budget.remaining - metadata_size), path, before=before)
            after = os.fstat(fd)
            current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            _fail(_stable(before) == _stable(after) == _stable(current),
                  f'File changed while reading: {path}')
            budget.consume(len(data) + metadata_size, path)
            if utf8:
                try:
                    data.decode('utf-8')
                except UnicodeError as error:
                    raise SourceEncodingError(f'Non-UTF-8 source: {path}') from error
            fp = Fingerprint(before.st_dev, before.st_ino,
                             stat.S_IMODE(before.st_mode), before.st_uid, before.st_gid, _hash(data), _xattr_hash(attributes))
            return _File(path, data, fp, parent, (before.st_mtime_ns, before.st_ctime_ns), attributes, tuple(parents))
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')) + '\n').encode()


def _fp_json(fp: Fingerprint) -> dict:
    return dict(device=fp.device, inode=fp.inode, mode=fp.mode, uid=fp.uid, gid=fp.gid, sha256=fp.sha256, metadata_sha256=fp.metadata_sha256)


def _fp_load(value: object) -> Fingerprint:
    _fail(isinstance(value, dict) and set(value) == {'device', 'inode', 'mode', 'uid', 'gid', 'sha256', 'metadata_sha256'},
          'Malformed journal fingerprint')
    _fail(all(type(value[k]) is int and value[k] >= 0 for k in ('device', 'inode', 'mode', 'uid', 'gid'))
          and value['inode'] > 0 and 0 <= value['mode'] <= 0o7777
          and all(isinstance(value[k], str) and re.fullmatch('[0-9a-f]{64}', value[k]) is not None
                  for k in ('sha256', 'metadata_sha256')),
          'Invalid journal fingerprint')
    return Fingerprint(**value)


def _decode(value: object, *, maximum: int = DEFAULT_READ_LIMITS.max_source_bytes) -> bytes:
    _fail(isinstance(value, str), 'Malformed journal bytes')
    _fail(len(value) <= 4 * ((maximum + 2) // 3), 'Journal decoded byte limit exceeded')
    try:
        decoded = base64.b64decode(value, validate=True)
    except ValueError as error:
        raise TransactionError('Invalid journal base64') from error
    _fail(len(decoded) <= maximum, 'Journal decoded byte limit exceeded')
    return decoded


def _private_directory(fd: int, path: Path, *,
                       max_bytes: int = DEFAULT_READ_LIMITS.max_metadata_bytes) -> None:
    s = os.fstat(fd)
    _fail(stat.S_ISDIR(s.st_mode) and s.st_uid == os.geteuid()
          and stat.S_IMODE(s.st_mode) & 0o077 == 0,
          f'Journal directory must be owner-only and owned by this user: {path}')
    _plain_metadata(fd, path, max_bytes=max_bytes)


@dataclass(frozen=True)
class JournalEntry:
    journal: str
    status: str
    error: str | None = None
    targets: tuple[str, ...] = ()


class _JournalReader:
    """One schema-1 bounded reader shared by transactions and read-only status."""

    @property
    def limits(self) -> ReadLimits:
        return self._limits

    def _journal_names(self, fd: int) -> list[str]:
        names = []
        with os.scandir(fd) as entries:
            for entry in entries:
                _fail(len(names) < self.limits.max_directory_entries,
                      'Journal directory entry limit exceeded')
                names.append(entry.name)
        return names

    def _load_json(self, directory_fd: int, name: str, *, budget: _ReadBudget | None = None) -> object:
        budget = _ReadBudget(self.limits.max_journal_total_bytes) if budget is None else budget
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        try:
            s = os.fstat(fd)
            _fail(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and s.st_uid == os.geteuid()
                  and stat.S_IMODE(s.st_mode) & 0o077 == 0, f'Unsafe journal file: {name}')
            data = _bounded_bytes(fd, min(self.limits.max_journal_bytes, budget.remaining), name, before=s)
            named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            _fail(_stable(s) == _stable(os.fstat(fd)) == _stable(named), f'Journal changed while reading: {name}')
            budget.consume(len(data), name)
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    _fail(key not in result, f'Duplicate JSON key in journal: {name}')
                    result[key] = value
                return result
            try:
                return json.loads(data, object_pairs_hook=unique)
            except TransactionError:
                raise
            except (ValueError, RecursionError) as error:
                raise TransactionError(f'Malformed journal JSON: {name}: {error}') from error
        finally:
            os.close(fd)

    def _open_journal(self, journal: str, *, with_identity: bool = False):
        _fail(isinstance(journal, str) and re.fullmatch('[0-9a-f]{32}', journal) is not None,
              'Invalid transaction journal ID')
        fd = os.open(journal, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._ns_fd)
        try:
            before = os.fstat(fd)
            _private_directory(fd, self._namespace / journal, max_bytes=self.limits.max_metadata_bytes)
            _fail(before.st_dev == self._store_identity[0],
                  'Journal must be on the source filesystem')
            for name in self._journal_names(fd):
                _fail(name == 'manifest.json' or re.fullmatch(r'event-[0-9]{8}\.json', name)
                      or re.fullmatch(r'(stage-[0-9]+|restore-[0-9a-f]{32})\.bin', name),
                      f'Unknown journal artifact: {journal}/{name}')
                s = os.stat(name, dir_fd=fd, follow_symlinks=False)
                _fail(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and s.st_uid == os.geteuid(),
                      f'Unsafe journal artifact: {journal}/{name}')
            self._journal_unchanged(fd, journal, before)
            return (fd, before) if with_identity else fd
        except BaseException:
            os.close(fd)
            raise

    def _journal_unchanged(self, fd: int, journal: str, before: os.stat_result) -> None:
        named = os.stat(journal, dir_fd=self._ns_fd, follow_symlinks=False)
        _fail(_stable(before) == _stable(os.fstat(fd)) == _stable(named),
              f'Journal directory changed while reading: {journal}')

    def _load_manifest(self, fd: int, *, budget: _ReadBudget | None = None) -> dict:
        value = self._load_json(fd, 'manifest.json', budget=budget)
        _fail(isinstance(value, dict) and set(value) == {'schema', 'root', 'root_identity', 'files', 'evidence'}
              and value['schema'] == 1 and value['root'] == str(self.root)
              and value['root_identity'] == list(self._root_identity),
              'Journal belongs to a different root or has an invalid schema')
        _fail(type(value['files']) is list and bool(value['files'])
              and type(value['evidence']) is list, 'Invalid journal manifest entries')
        paths = set()
        before_budget = _ReadBudget(self.limits.max_capture_bytes)
        after_budget = _ReadBudget(self.limits.max_capture_bytes)
        for index, entry in enumerate(value['files']):
            _fail(type(entry) is dict and set(entry) == {'path', 'before', 'after', 'fingerprint', 'parent', 'stage', 'xattrs'},
                  'Invalid journal source entry')
            path = _relative(entry['path'])
            _fail(path not in paths and Path(path).suffix in ('.typ', '.md'), 'Invalid or duplicate journal source')
            paths.add(path)
            _fail(entry['stage'] == f'stage-{index}.bin', 'Invalid journal stage path')
            fp = _fp_load(entry['fingerprint'])
            _fail(fp.uid == os.geteuid() and fp.mode <= 0o777
                  and fp.device == self._store_identity[0], 'Unsafe journal source ownership/mode/device')
            _fail(type(entry['parent']) is list and len(entry['parent']) == 2
                  and all(type(n) is int and n >= 0 for n in entry['parent']),
                  'Invalid journal parent identity')
            _fail(type(entry['xattrs']) is list, 'Invalid journal metadata')
            attrs = []
            metadata_remaining = min(self.limits.max_metadata_bytes,
                                     before_budget.remaining, after_budget.remaining)
            for pair in entry['xattrs']:
                _fail(type(pair) is list and len(pair) == 2 and isinstance(pair[0], str)
                      and bool(pair[0]) and '\x00' not in pair[0], 'Invalid journal metadata entry')
                metadata_remaining -= len(os.fsencode(pair[0])) + 1
                _fail(metadata_remaining >= 0, 'Journal metadata byte limit exceeded')
                attribute = _decode(pair[1], maximum=metadata_remaining)
                metadata_remaining -= len(attribute)
                attrs.append((pair[0], attribute))
            _fail(attrs == sorted(set(attrs)) and len({name for name, _ in attrs}) == len(attrs)
                  and _xattr_hash(tuple(attrs)) == fp.metadata_sha256, 'Invalid journal metadata digest')
            metadata_size = _metadata_size(tuple(attrs))
            before_budget.consume(metadata_size, path)
            after_budget.consume(metadata_size, path)
            before = _decode(entry['before'], maximum=min(self.limits.max_source_bytes, before_budget.remaining))
            after = _decode(entry['after'], maximum=min(self.limits.max_source_bytes, after_budget.remaining))
            before_budget.consume(len(before), path)
            after_budget.consume(len(after), path)
            _fail(_hash(before) == fp.sha256 and before != after, 'Invalid original journal bytes')
            try:
                before.decode('utf-8')
                after.decode('utf-8')
            except UnicodeError as error:
                raise TransactionError('Invalid UTF-8 journal source') from error
            _fail(before.startswith(b'\xef\xbb\xbf') == after.startswith(b'\xef\xbb\xbf'),
                  'Journal changes source BOM')
        evidence_paths = set()
        for entry in value['evidence']:
            _fail(type(entry) is dict and set(entry) == {'path', 'bytes', 'fingerprint'},
                  'Invalid journal evidence entry')
            path = _absolute(entry['path'])
            _fail(str(path) not in evidence_paths, 'Duplicate journal evidence')
            evidence_paths.add(str(path))
            if entry['bytes'] is None:
                _fail(entry['fingerprint'] is None, 'Malformed absent evidence')
            else:
                content = _decode(entry['bytes'], maximum=min(self.limits.max_evidence_bytes,
                                                            before_budget.remaining, after_budget.remaining))
                before_budget.consume(len(content), path)
                after_budget.consume(len(content), path)
                _fail(_hash(content) == _fp_load(entry['fingerprint']).sha256,
                      'Invalid journal evidence bytes')
        return value

    def _events(self, fd: int, manifest: dict, *, budget: _ReadBudget | None = None) -> list[dict]:
        budget = _ReadBudget(self.limits.max_journal_total_bytes) if budget is None else budget
        names = sorted(n for n in self._journal_names(fd) if n.startswith('event-'))
        _fail(names == [f'event-{i:08d}.json' for i in range(len(names))], 'Noncontiguous journal events')
        events = []
        entries = {e['path']: e for e in manifest['files']}
        paths = set(entries)
        terminal = False
        applied = set()
        for name in names:
            event = self._load_json(fd, name, budget=budget)
            _fail(isinstance(event, dict) and isinstance(event.get('kind'), str),
                  'Invalid journal event sequence')
            kind = event['kind']
            _fail(not terminal or kind == 'recovery_required', 'Events follow completed journal')
            if kind in ('staged', 'replace_intent', 'applied', 'restore_intent', 'restored'):
                _fail(set(event) == {'kind', 'path', 'fingerprint'} and event['path'] in paths,
                      'Invalid journal progress path')
                fp = _fp_load(event['fingerprint'])
                original = _fp_load(entries[event['path']]['fingerprint'])
                content = entries[event['path']]['before' if kind in ('restore_intent', 'restored') else 'after']
                _fail(fp.sha256 == _hash(_decode(content, maximum=self.limits.max_source_bytes))
                      and (fp.device, fp.mode, fp.uid, fp.gid, fp.metadata_sha256) ==
                      (original.device, original.mode, original.uid, original.gid, original.metadata_sha256),
                      'Progress fingerprint disagrees with immutable manifest')
                if kind == 'applied':
                    applied.add(event['path'])
            elif kind == 'recovery_required':
                _fail(set(event) == {'kind'}, 'Invalid recovery-required event')
                terminal = False
            elif kind in ('committed', 'recovered'):
                _fail(set(event) == {'kind'}, 'Invalid journal completion')
                _fail(kind != 'committed' or applied == paths, 'Commit lacks complete per-file progress')
                terminal = True
            else:
                _fail(False, 'Unknown journal event kind')
            events.append(event)
        return events

    def _read_journal(self, journal: str):
        """Open, fully validate and return a live journal fd plus bounded data.

        All malformed structure is a typed storage failure, including JSON values
        that are the wrong kind for a nested schema field. The successful caller
        owns the descriptor; failures always close it here.
        """
        fd, before = self._open_journal(journal, with_identity=True)
        try:
            budget = _ReadBudget(self.limits.max_journal_total_bytes)
            manifest = self._load_manifest(fd, budget=budget)
            events = self._events(fd, manifest, budget=budget)
            self._journal_unchanged(fd, journal, before)
            return fd, manifest, events, budget
        except BaseException as error:
            os.close(fd)
            if isinstance(error, TransactionError):
                raise
            if isinstance(error, (ValueError, TypeError, KeyError, RecursionError)):
                raise TransactionError(f'Malformed journal structure: {journal}: {error}') from error
            raise

    def _journal_entries(self) -> tuple[JournalEntry, ...]:
        entries = []
        namespace_before = os.fstat(self._ns_fd)
        for journal in sorted(self._journal_names(self._ns_fd)):
            try:
                fd, manifest, events, _ = self._read_journal(journal)
                try:
                    status = (events[-1]['kind'] if events and
                              events[-1]['kind'] in ('committed', 'recovered') else 'incomplete')
                    entries.append(JournalEntry(journal, status,
                                                targets=tuple(e['path'] for e in manifest['files'])))
                finally:
                    os.close(fd)
            except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
                # Corrupt or interrupted journals fail closed, never disappear.
                entries.append(JournalEntry(journal, 'unreadable-or-corrupt',
                                            f'{type(error).__name__}: {error}'))
        _fail(_stable(namespace_before) == _stable(os.fstat(self._ns_fd)),
              'Journal namespace changed while reading')
        return tuple(entries)

    def _pending(self) -> tuple[str, ...]:
        return tuple(entry.journal for entry in self._journal_entries()
                     if entry.status not in ('committed', 'recovered'))


class ProtectedTransaction(_JournalReader):
    """Hold root lock and offline assertion from baseline through handoff.

    The explicit allowlist is already filtered by the trusted selection engine;
    this module does not infer excludes, generated files, or packages. Evidence
    must enumerate every config/dependency/resource on which analysis depends.
    Journal storage must be a canonical owner-only directory outside
    the root, on the same filesystem as every replacement. It must exist unless
    ``create_journal_directory=True`` explicitly permits creating only that directory
    under the root lock and genuine offline assertion. Unsupported platforms
    and cross-device layouts fail rather than weakening the protocol. Sources
    must be owned by the effective user, with a group this user may preserve.
    Normal transaction targets must have the owner-write mode bit set; an
    intentionally read-only source is refused even if rename would be allowed.
    Recovery remains separate so existing journals can still be reconciled.
    Extended attributes are preserved. ACLs, special permission bits and
    nonstructural filesystem flags are refused; timestamp preservation is not promised. Metadata probing currently
    supports macOS and Linux only, and fails closed if probing is unavailable.

    ``fault`` is an internal testing hook, not a CLI option. A BaseException (as
    opposed to Exception) simulates abrupt termination and leaves recovery work.
    Recovery contexts cannot mint candidates or apply new changes. Normal
    contexts block on any incomplete journal in this root's configured store.
    """
    def __init__(self, root: Path, targets, *, journal_directory: Path,
                 evidence=(), lease: OfflineExclusive | None = None,
                 recovery_only: bool = False,
                 create_journal_directory: bool = False,
                 require_existing_namespace: bool = False,
                 limits: ReadLimits = DEFAULT_READ_LIMITS,
                 retain_ancestry: bool = False,
                 fault: Callable[[str, str | None], None] | None = None):
        _fail(type(retain_ancestry) is bool, 'Live ancestry opt-in must be a boolean')
        self._retain_ancestry = retain_ancestry
        _fail(type(create_journal_directory) is bool, 'Journal creation opt-in must be a boolean')
        _fail(type(require_existing_namespace) is bool, 'Existing namespace requirement must be a boolean')
        _fail(not (create_journal_directory and require_existing_namespace),
              'Existing namespace requirement cannot create journal storage')
        _fail(type(limits) is ReadLimits, 'Explicit immutable ReadLimits required')
        # Recheck fields even if a caller bypassed the frozen dataclass API.
        limits.__post_init__()
        self._limits = limits
        self.root = _absolute(root)
        self.store = _absolute(journal_directory)
        _fail(self.store != self.root and self.root not in self.store.parents,
              'Journal storage must be outside the selected root')
        values = tuple(targets)
        _fail(len(values) == len(set(values)), 'Duplicate target allowlist entries')
        self.targets = tuple(sorted(_relative(p) for p in values))
        _fail(all(Path(p).suffix in ('.typ', '.md') for p in self.targets),
              'Only selected .typ/.md source files are writable')
        self.evidence_inputs = tuple(evidence)
        _fail(all(isinstance(e, EvidenceInput) and type(e.allow_missing) is bool
                  and type(e.utf8) is bool for e in self.evidence_inputs), 'Invalid evidence input')
        paths = tuple(_absolute(e.path) for e in self.evidence_inputs)
        self._evidence_utf8 = MappingProxyType({p: e.utf8 for p, e in zip(map(str, paths), self.evidence_inputs)})
        _fail(len(paths) == len(set(paths)), 'Duplicate evidence paths')
        _fail(not set(paths) & {self.root / p for p in self.targets},
              'Targets cannot also be read-only evidence')
        _fail(not any(p == self.store or self.store in p.parents for p in paths),
              'Transaction storage cannot be an analysis input')
        self.lease = lease
        self.recovery_only = recovery_only
        self.create_journal_directory = create_journal_directory
        self.require_existing_namespace = require_existing_namespace
        self.fault = fault
        self._fds: list[int] = []
        self._owner = object()
        self._issued: ValidatedCandidate | None = None
        self._issued_facts: _CandidateFacts | None = None
        self._used = False
        self._active = False
        self._files: dict[str, _File] = {}
        self._evidence: dict[str, _File] = {}
        self._committed_files: Mapping[str, _File] | None = None
        self._committed_evidence: Mapping[str, _File] | None = None
        self._committed_graph: GraphSnapshot | None = None
        self._committed_receipt: CommittedSnapshot | None = None
        self._namespace_name = 'root-' + _hash(os.fsencode(self.root))
        self._namespace = self.store / self._namespace_name
        self._event_number = 0
        self._journal: str | None = None
        self._tx_fd: int | None = None
        self._journal_bytes = 0

    def _hit(self, point: str, path: str | None = None) -> None:
        if self.fault is not None:
            self.fault(point, path)

    def _sync(self, fd: int, point: str, path: str | None = None) -> None:
        self._hit(point, path)
        os.fsync(fd)

    def __enter__(self) -> 'ProtectedTransaction':
        _fail(not self._active and not self._used, 'Transaction contexts cannot be reused')
        _fail(isinstance(self.lease, OfflineExclusive)
              and getattr(self.lease, '_seal', None) is _LEASE_SEAL,
              'Explicit offline-exclusive assertion required; editor state is not known')
        _fail(os.name == 'posix' and fcntl is not None
              and hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY') and hasattr(os, 'fchown')
              and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd
              and os.rename in os.supports_dir_fd and os.scandir in os.supports_fd,
              'Protected transactions require supported POSIX dirfd, nofollow and flock primitives')
        try:
            self._root_fd = _directory(self.root)
            self._fds.append(self._root_fd)
            try:
                fcntl.flock(self._root_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise TransactionError(f'Root lock unavailable: {self.root}: {error}') from error
            self._root_identity = _identity(os.fstat(self._root_fd))
            self._store_fd = self._open_store()
            self._fds.append(self._store_fd)
            _private_directory(self._store_fd, self.store, max_bytes=self.limits.max_metadata_bytes)
            self._store_identity = _identity(os.fstat(self._store_fd))
            _fail(self._root_identity[0] == self._store_identity[0],
                  'Journal storage must be on the source filesystem')
            if not self.require_existing_namespace:
                try:
                    os.mkdir(self._namespace_name, 0o700, dir_fd=self._store_fd)
                    os.fsync(self._store_fd)
                except FileExistsError:
                    pass
            self._ns_fd = os.open(self._namespace_name,
                                  os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                  dir_fd=self._store_fd)
            self._fds.append(self._ns_fd)
            _private_directory(self._ns_fd, self._namespace, max_bytes=self.limits.max_metadata_bytes)
            self._namespace_identity = _identity(os.fstat(self._ns_fd))
            _fail(self._namespace_identity[0] == self._store_identity[0],
                  'Journal namespace must be on the source filesystem')
            self._active = True
            self._check_active()
            self.pending = self._pending()
            if self.pending and not self.recovery_only:
                raise RecoveryRequired(self.pending)
            if not self.recovery_only:
                budget = _ReadBudget(self.limits.max_capture_bytes)
                self._files = {p: _read(self.root / p, utf8=True, limits=self.limits, budget=budget,
                                        retain_ancestry=self._retain_ancestry)
                               for p in self.targets}
                self._evidence = {str(_absolute(e.path)): _read(_absolute(e.path), missing=e.allow_missing, utf8=e.utf8,
                                                              limits=self.limits, budget=budget,
                                                              retain_ancestry=self._retain_ancestry)
                                  for e in self.evidence_inputs}
                for p, value in self._files.items():
                    _fail(value.fingerprint.device == self._store_identity[0],
                          f'Cross-device source replacement unsupported: {p}')
                    _fail(value.fingerprint.mode <= 0o777,
                          f'Special permission bits unsupported: {p}')
                    _fail(bool(value.fingerprint.mode & stat.S_IWUSR),
                          f'Source target is not owner-writable: {p}')
                self._revalidate()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _open_store(self) -> int:
        if not self.create_journal_directory:
            return _directory(self.store)
        # The caller is already holding the cooperating root lock and has a
        # genuine operator lease. Never create ancestors, chmod an existing
        # store, follow a symlink, or select a fallback location.
        parent_fd = _directory(self.store.parent)
        try:
            parent_identity = _identity(os.fstat(parent_fd))
            _fail(parent_identity[0] == self._root_identity[0],
                  'Journal storage parent must be on the source filesystem')
            self._check_directory_identity(self.root, self._root_identity)
            self._check_directory_identity(self.store.parent, parent_identity)
            try:
                os.mkdir(self.store.name, 0o700, dir_fd=parent_fd)
                created = True
            except FileExistsError:
                created = False
            named = os.stat(self.store.name, dir_fd=parent_fd, follow_symlinks=False)
            _fail(not created or stat.S_IMODE(named.st_mode) == 0o700,
                  f'Created journal storage must have mode 0700: {self.store}')
            fd = os.open(self.store.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         dir_fd=parent_fd)
            try:
                _fail(_stable(named) == _stable(os.fstat(fd)),
                      f'Journal storage identity changed while opening: {self.store}')
                _private_directory(fd, self.store, max_bytes=self.limits.max_metadata_bytes)
                _fail(_identity(os.fstat(fd))[0] == self._root_identity[0],
                      'Journal storage must be on the source filesystem')
                if created:
                    self._sync(parent_fd, 'store_parent_fsync', str(self.store))
                current = os.stat(self.store.name, dir_fd=parent_fd, follow_symlinks=False)
                _fail(_stable(named) == _stable(os.fstat(fd)) == _stable(current),
                      f'Journal storage identity changed while opening: {self.store}')
                self._check_directory_identity(self.root, self._root_identity)
                self._check_directory_identity(self.store.parent, parent_identity)
                return fd
            except BaseException:
                os.close(fd)
                raise
        finally:
            os.close(parent_fd)

    @staticmethod
    def _check_directory_identity(path: Path, expected: tuple[int, int]) -> None:
        fd = _directory(path)
        try:
            _fail(_identity(os.fstat(fd)) == expected, f'Directory identity changed: {path}')
        finally:
            os.close(fd)

    def __exit__(self, exc_type, exc, tb):
        self._active = False
        self._issued = None
        self._issued_facts = None
        for fd in reversed(self._fds):
            os.close(fd)
        self._fds.clear()
        self._used = True

    def _check_active(self) -> None:
        _fail(self._active, 'Active root lock and offline-exclusive context required')
        for path, expected in ((self.root, self._root_identity),
                               (self.store, self._store_identity),
                               (self._namespace, self._namespace_identity)):
            fd = _directory(path)
            try:
                _fail(_identity(os.fstat(fd)) == expected, f'Directory identity changed: {path}')
                if path != self.root:
                    _private_directory(fd, path, max_bytes=self.limits.max_metadata_bytes)
            finally:
                os.close(fd)

    def _revalidate(self, expected: Mapping[str, _File] | None = None, *,
                    evidence: Mapping[str, _File] | None = None) -> None:
        self._check_active()
        budget = _ReadBudget(self.limits.max_capture_bytes)
        for sources, values in ((True, (self._files if expected is None else expected).values()),
                                (False, (self._evidence if evidence is None else evidence).values())):
            for value in values:
                try:
                    now = _read(value.path, missing=value.data is None,
                                utf8=sources or self._evidence_utf8[str(value.path)],
                                limits=self.limits, budget=budget,
                                retain_ancestry=bool(value.parents))
                except SourceEncodingError as error:
                    # Encoding failure after initial capture is a stale/raced
                    # protected baseline, not merely unsupported initial input.
                    raise TransactionError(f'Stale baseline: {error}') from error
                _fail(now == value, f'Stale baseline (bytes, identity, mode or metadata): {value.path}')

    @property
    def limits(self) -> ReadLimits:
        return self._limits

    def _candidate_sizes(self, sizes: Mapping[str, int]) -> None:
        budget = _ReadBudget(self.limits.max_capture_bytes)
        for p, size in sizes.items():
            _fail(size <= self.limits.max_source_bytes, f'Candidate source byte limit exceeded: {p}')
            budget.consume(size + _metadata_size(self._files[p].xattrs), p)
        for value in self._evidence.values():
            budget.consume((0 if value.data is None else len(value.data)) + _metadata_size(value.xattrs), value.path)

    @property
    def captured_fingerprints(self) -> CaptureFingerprints:
        """Immutable identities from retained baseline records, without stat refresh.

        This is inspection data, not a validated-candidate or compile receipt.
        Optional ancestry belongs only to this live transaction and is not a
        promise that historical/restarted journal recovery retains those guards.
        """
        self._check_active()
        _fail(not self.recovery_only, 'Recovery context has no capture identities')
        directories = {}
        for value in (*self._files.values(), *self._evidence.values()):
            for path, identity in value.parents:
                _fail(path not in directories or directories[path] == identity,
                      f'Inconsistent retained directory identity: {path}')
                directories[path] = identity
        return CaptureFingerprints(
            MappingProxyType({p: f.fingerprint for p, f in self._files.items()}),
            MappingProxyType({p: f.fingerprint for p, f in self._evidence.items()}),
            MappingProxyType(directories))

    @property
    def snapshot(self) -> GraphSnapshot:
        """The original analysis baseline, never a post-fix compilation snapshot."""
        self._check_active()
        _fail(not self.recovery_only, 'Recovery context has no analysis snapshot')
        return self._graph({p: value.data for p, value in self._files.items()})

    def _graph(self, sources: Mapping[str, bytes]) -> GraphSnapshot:
        return GraphSnapshot(self.root, MappingProxyType(dict(sources)),
                             MappingProxyType({p: f.data for p, f in self._evidence.items()}))

    def _retain_committed(self, expected: Mapping[str, _File]) -> None:
        # No live recapture: changed files are the exact verified postimages
        # already retained by apply; unchanged/no-op files remain the baseline.
        # This runs OUTSIDE the rollback handler, after the durable commit.
        self._committed_files = MappingProxyType(dict(expected))
        self._committed_evidence = MappingProxyType(dict(self._evidence))
        graph = GraphSnapshot(self.root,
            MappingProxyType({p: value.data for p, value in expected.items()}),
            MappingProxyType({p: value.data for p, value in self._committed_evidence.items()}))
        receipt = object.__new__(CommittedSnapshot)
        for name, value in dict(root=graph.root, sources=graph.sources, evidence=graph.evidence,
                                _owner=self._owner, _seal=_COMMITTED_SEAL).items():
            object.__setattr__(receipt, name, value)
        self._committed_graph = graph
        self._committed_receipt = receipt

    def committed_snapshot(self) -> CommittedSnapshot:
        """Return this apply's receipt only if its retained postimages are current.

        A later stale generation refuses handoff without undoing committed fixes
        or blessing fresh live bytes. The baseline ``snapshot`` stays unchanged.
        """
        receipt = self._committed_receipt
        self.assert_current(receipt)
        return receipt

    def assert_current(self, receipt: CommittedSnapshot) -> None:
        """Revalidate a genuine issued receipt under the existing root lock."""
        self._check_active()
        _fail(not self.recovery_only and self._used
              and receipt is not None and receipt is self._committed_receipt
              and type(receipt) is CommittedSnapshot
              and receipt._owner is self._owner and receipt._seal is _COMMITTED_SEAL
              and self._committed_files is not None and self._committed_evidence is not None
              and self._committed_graph is not None,
              'Only this active session\'s successful apply has a committed snapshot')
        graph = self._committed_graph
        _fail(receipt.root == graph.root == self.root
              and receipt.sources is graph.sources and receipt.evidence is graph.evidence,
              'Committed snapshot receipt changed')
        self._revalidate(self._committed_files, evidence=self._committed_evidence)

    def validate(self, analyze: Callable[[GraphSnapshot], GraphAnalysis], *,
                 required_rules: frozenset[str]) -> ValidatedCandidate:
        """Run complete trusted analysis/convergence twice at the final state.

        This is the sole token issuer. No method accepts a caller-supplied final
        byte map, parse-valid flag, content hash, or serialized approval token.
        Immutable views are supplied anew on every pass; no filesystem writes
        are made by this method. The callback itself must be trusted code.
        """
        self._check_active()
        _fail(not self.recovery_only and not self._used, 'Cannot validate in this context')
        _fail(isinstance(required_rules, frozenset) and bool(required_rules)
              and all(isinstance(r, str) and r for r in required_rules),
              'A nonempty complete required-rule set is mandatory')
        _fail(callable(analyze), 'Trusted analyzer callback required')
        self._revalidate()
        sources = {p: f.data for p, f in self._files.items()}

        def checked(candidate):
            state = analyze(self._graph(candidate))
            _fail(isinstance(state, GraphAnalysis), 'Trusted graph analysis required')
            for name in ('implemented_rules', 'evaluated_rules', 'parsed_sources',
                         'examined_evidence', 'findings'):
                value = getattr(state, name)
                _fail(isinstance(value, frozenset) and all(isinstance(x, str) for x in value),
                      f'Analysis {name} must be immutable string identities')
            _fail(required_rules <= state.implemented_rules
                  and required_rules == state.evaluated_rules,
                  'Incomplete required-rule coverage')
            _fail(state.parsed_sources == frozenset(sources)
                  and state.examined_evidence == frozenset(self._evidence),
                  'Incomplete source/dependency/config analysis')
            _fail(type(state.parse_errors) is tuple and not state.parse_errors
                  and type(state.unsupported) is tuple and not state.unsupported,
                  'Parse failure or unsupported required analysis')
            _fail(type(state.edits) is tuple, 'Analysis edits must be immutable')
            grouped = {p: [] for p in sources}
            for proposal in state.edits:
                _fail(isinstance(proposal, SourceEdit) and proposal.path in grouped,
                      'Repair target is outside the explicit source allowlist')
                _fail(isinstance(proposal.edit, Edit) and isinstance(proposal.edit.replacement, bytes)
                      and len(proposal.edit.replacement) <= self.limits.max_source_bytes,
                      'Repair replacement exceeds source byte limit or is invalid')
                grouped[proposal.path].append(proposal.edit)
            plans = {p: plan(candidate[p], edits) for p, edits in grouped.items()}
            self._candidate_sizes({p: len(candidate[p]) + sum(len(e.replacement) - (e.end - e.start)
                                                             for e in proposed.edits)
                                   for p, proposed in plans.items()})
            return state, plans

        state, plans = checked(sources)
        initial_findings = state.findings
        seen = {tuple(sorted(sources.items()))}
        for iteration in range(9):
            edits = any(p.edits for p in plans.values())
            deferred = any(p.deferred for p in plans.values())
            if not edits and not deferred:
                _fail(state.findings <= initial_findings, 'Candidate introduces new findings')
                again, second = checked(sources)
                _fail(again.findings == state.findings
                      and not any(p.edits or p.deferred for p in second.values()),
                      'Candidate is not idempotent')
                self._revalidate()
                # Retain immutable primitive payloads separately from the issued
                # object's replaceable attributes and the map's backing dict.
                items = tuple((str(p), bytes(data)) for p, data in sources.items())
                view = MappingProxyType(dict(items))
                findings = frozenset(str(item) for item in state.findings)
                token = object.__new__(ValidatedCandidate)
                for name, value in dict(sources=view, findings=findings, passes=iteration,
                                        _owner=self._owner, _seal=_TOKEN_SEAL).items():
                    object.__setattr__(token, name, value)
                self._issued_facts = _CandidateFacts(view, items, findings, iteration,
                                                     self._owner, _TOKEN_SEAL)
                self._issued = token
                return token
            _fail(iteration < 8, 'Eight-pass convergence limit exceeded')
            _fail(edits, 'Unresolved conflicting edits')
            sources = {p: edit_plan.candidate() for p, edit_plan in plans.items()}
            for p, content in sources.items():
                _fail(content.startswith(b'\xef\xbb\xbf') == self._files[p].data.startswith(b'\xef\xbb\xbf'),
                      f'Candidate changes UTF-8 BOM: {p}')
            key = tuple(sorted(sources.items()))
            _fail(key not in seen, 'Repeated candidate state')
            seen.add(key)
            state, plans = checked(sources)
        raise AssertionError('Unreachable')

    def _write_new(self, directory_fd: int, name: str, data: bytes, *,
                   mode: int = 0o600, group: int | None = None, xattrs=None, stage: bool = False,
                   path: str | None = None) -> Fingerprint:
        maximum = self.limits.max_source_bytes if stage else self.limits.max_journal_bytes
        _fail(len(data) <= maximum, f'Write byte limit exceeded: {path or name}')
        if not stage:
            _fail(len(data) <= self.limits.max_journal_total_bytes - self._journal_bytes,
                  f'Aggregate journal write limit exceeded: {name}')
            self._journal_bytes += len(data)
        self._hit('stage_create' if stage else 'journal_create', path or name)
        fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     mode, dir_fd=directory_fd)
        try:
            created = os.fstat(fd)
            expected_group = created.st_gid if group is None else group
            self._hit('stage_write' if stage else 'journal_write', path or name)
            view = memoryview(data)
            while view:
                count = os.write(fd, view)
                _fail(count > 0, 'Short write made no progress')
                view = view[count:]
            # Content writes may themselves affect inode metadata. Apply the
            # requested ownership/mode/xattrs afterward, on our own inode only.
            if created.st_gid != expected_group:
                os.fchown(fd, -1, expected_group)
            os.fchmod(fd, mode)
            if xattrs is not None:
                _copy_xattrs(fd, xattrs, max_bytes=self.limits.max_metadata_bytes)
            expected_attrs = _xattrs(fd, max_bytes=self.limits.max_metadata_bytes) if xattrs is None else xattrs
            _plain_metadata(fd, Path(path or name), max_bytes=self.limits.max_metadata_bytes)
            before_sync = os.fstat(fd)
            self._sync(fd, 'stage_fsync' if stage else 'journal_fsync', path or name)
            # Do not turn an observed, possibly corrupted stage into the trusted
            # postimage. Compare its actual bytes and final metadata to intent.
            os.lseek(fd, 0, os.SEEK_SET)
            actual = _bounded_bytes(fd, len(data), path or name)
            attrs = _xattrs(fd, max_bytes=self.limits.max_metadata_bytes)
            _plain_metadata(fd, Path(path or name), max_bytes=self.limits.max_metadata_bytes)
            current = os.fstat(fd)
            named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            _fail(_stable(before_sync) == _stable(current) == _stable(named)
                  and stat.S_ISREG(current.st_mode) and current.st_nlink == 1
                  and current.st_uid == os.geteuid() and current.st_gid == expected_group
                  and stat.S_IMODE(current.st_mode) == mode and attrs == expected_attrs
                  and bytes(actual) == data, f'Staged bytes or metadata changed: {path or name}')
            return Fingerprint(current.st_dev, current.st_ino, mode, current.st_uid, expected_group,
                               _hash(data), _xattr_hash(expected_attrs))
        finally:
            os.close(fd)

    def _event(self, kind: str, **values) -> None:
        name = f'event-{self._event_number:08d}.json'
        self._event_number += 1  # Never overwrite even a partially written event.
        self._write_new(self._tx_fd, name, _json_bytes(dict(kind=kind, **values)))
        self._sync(self._tx_fd, 'journal_directory_fsync', name)

    def _apply_inputs(self, candidate: ValidatedCandidate) -> tuple[Mapping[str, bytes], frozenset[str]]:
        facts = self._issued_facts
        _fail(candidate is self._issued and type(candidate) is ValidatedCandidate
              and facts is not None and facts.owner is self._owner and facts.seal is _TOKEN_SEAL,
              'Only this active session\'s validated candidate may be applied')
        fields = vars(candidate)
        _fail(type(fields) is dict and len(fields) == 5 and all(type(name) is str for name in fields)
              and set(fields) == {'sources', 'findings', 'passes', '_owner', '_seal'}
              and fields['_owner'] is facts.owner and fields['_seal'] is facts.seal
              and type(fields['passes']) is int and fields['passes'] == facts.passes
              and type(fields['findings']) is frozenset
              and len(fields['findings']) == len(facts.findings)
              and all(type(item) is str for item in fields['findings'])
              and fields['findings'] == facts.findings
              and fields['sources'] is facts.source_view,
              'Validated candidate fields changed since issuance')
        view = fields['sources']
        try:
            matches = len(view) == len(facts.sources) and all(
                type(path) is str and type(data) is bytes and path == old_path and data == old_data
                for (path, data), (old_path, old_data) in zip(view.items(), facts.sources))
        except RuntimeError as error:
            raise TransactionError('Validated candidate sources changed since issuance') from error
        _fail(matches, 'Validated candidate sources changed since issuance')
        # No later read of the token or its map is allowed to select bytes or
        # result facts. Freeze from issuance facts BEFORE filesystem operations,
        # including active/pending checks, and BEFORE any fault callback.
        return MappingProxyType(dict(facts.sources)), facts.findings

    def apply(self, candidate: ValidatedCandidate) -> ApplyResult:
        _fail(self._active, 'Active root lock and offline-exclusive context required')
        _fail(not self.recovery_only and not self._used, 'Cannot apply in this context')
        sources, findings = self._apply_inputs(candidate)
        self._check_active()
        _fail(not self._pending(), 'Incomplete journal blocks source application')
        self._revalidate()
        self._candidate_sizes({p: len(content) for p, content in sources.items()})
        changed = tuple(p for p in self.targets if sources[p] != self._files[p].data)
        self._used = True
        self._issued = None
        self._issued_facts = None
        if not changed:
            self._retain_committed(self._files)
            return ApplyResult(None, (), findings)
        journal = uuid.uuid4().hex
        self._journal = journal
        self._journal_bytes = 0
        os.mkdir(journal, 0o700, dir_fd=self._ns_fd)
        self._tx_fd = os.open(journal, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._ns_fd)
        self._fds.append(self._tx_fd)
        manifest = dict(schema=1, root=str(self.root), root_identity=list(self._root_identity), files=[], evidence=[])
        for index, p in enumerate(changed):
            before = self._files[p]
            manifest['files'].append(dict(path=p, before=base64.b64encode(before.data).decode(),
                                          after=base64.b64encode(sources[p]).decode(),
                                          fingerprint=_fp_json(before.fingerprint),
                                          parent=list(before.parent), stage=f'stage-{index}.bin',
                                          xattrs=[[name, base64.b64encode(data).decode()] for name, data in before.xattrs]))
        for p, value in self._evidence.items():
            manifest['evidence'].append(dict(path=p, bytes=None if value.data is None else base64.b64encode(value.data).decode(),
                                            fingerprint=None if value.fingerprint is None else _fp_json(value.fingerprint)))
        expected = dict(self._files)
        try:
            self._sync(self._ns_fd, 'journal_parent_fsync', journal)
            self._write_new(self._tx_fd, 'manifest.json', _json_bytes(manifest))
            self._sync(self._tx_fd, 'journal_directory_fsync', 'manifest.json')
            stages = {}
            for entry in manifest['files']:
                p = entry['path']
                stages[p] = self._write_new(self._tx_fd, entry['stage'], sources[p],
                                            mode=self._files[p].fingerprint.mode, group=self._files[p].fingerprint.gid,
                                            xattrs=self._files[p].xattrs, stage=True, path=p)
                self._event('staged', path=p, fingerprint=_fp_json(stages[p]))
            self._hit('before_revalidate', None)
            self._revalidate(expected)
            for entry in manifest['files']:
                p = entry['path']
                self._event('replace_intent', path=p, fingerprint=_fp_json(stages[p]))
                self._hit('before_replace', p)
                self._revalidate(expected)
                # Stage ownership/content is checked, not trusted merely by its name.
                staged = _read(self._namespace / journal / entry['stage'], utf8=True, limits=self.limits)
                _fail(staged.fingerprint == stages[p], f'Stage changed: {p}')
                parents = []
                parent_fd = (_directory((self.root / p).parent, identities=parents)
                             if self._files[p].parents else _directory((self.root / p).parent))
                try:
                    _fail(not self._files[p].parents or tuple(parents) == self._files[p].parents,
                          f'Source ancestor changed: {p}')
                    _fail(_identity(os.fstat(parent_fd)) == self._files[p].parent,
                          f'Source parent changed: {p}')
                    os.replace(entry['stage'], Path(p).name, src_dir_fd=self._tx_fd, dst_dir_fd=parent_fd)
                    self._hit('after_replace', p)
                    self._sync(parent_fd, 'source_directory_fsync', p)
                    self._sync(self._tx_fd, 'journal_directory_fsync', p)
                finally:
                    os.close(parent_fd)
                expected[p] = _read(self.root / p, utf8=True, limits=self.limits,
                                    retain_ancestry=bool(self._files[p].parents))
                _fail(expected[p].parents == self._files[p].parents, f'Source ancestor changed: {p}')
                _fail(expected[p].fingerprint == stages[p], f'Unexpected replacement identity: {p}')
                self._event('applied', path=p, fingerprint=_fp_json(stages[p]))
            self._hit('before_final_validation', None)
            self._revalidate(expected)
            self._event('committed')
        except Exception as error:
            # Best-effort rollback is not completion: an explicit recovery pass
            # must reconcile the durable record before another transaction.
            record_error = None
            try:
                self._event('recovery_required')
            except Exception as marker_error:
                record_error = f'Recovery marker failed: {type(marker_error).__name__}: {marker_error}'
            try:
                report = self._recover(journal, finish=False, preserve_conflicts={})
                if record_error is not None:
                    report = replace(report, errors=report.errors + (record_error,))
            except Exception as recovery_error:
                report = RecoveryReport(journal, unresolved=changed,
                                        errors=(f'{type(recovery_error).__name__}: {recovery_error}',))
            raise TransactionFailure(f'{type(error).__name__}: {error}', report) from error
        # A successful durable committed event is the source-commit boundary.
        # Snapshot allocation, handoff and later currentness checks must NEVER
        # enter the source rollback/recovery path above.
        self._retain_committed(expected)
        return ApplyResult(journal, changed, findings)

    def recover(self, journal: str, *,
                preserve_conflicts: Mapping[str, Fingerprint] | None = None) -> RecoveryReport:
        """Explicitly reconcile an incomplete journal without compiling.

        A first pass restores recognized postimages only. For a conflict, inspect
        the preserved file and pass its exact reported Fingerprint in a later
        ``preserve_conflicts`` call to acknowledge that version without writing
        it. A missing/unreadable file is never recreated. Corrupt journals need
        manual investigation; this API does not delete or silently dismiss them.
        """
        self._check_active()
        _fail(self.recovery_only, 'Explicit recovery-only context required')
        return self._recover(journal, finish=True, preserve_conflicts=preserve_conflicts or {})

    def _recover(self, journal: str, *, finish: bool,
                 preserve_conflicts: Mapping[str, Fingerprint]) -> RecoveryReport:
        self._check_active()
        fd, manifest, events, budget = self._read_journal(journal)
        try:
            entries = manifest['files']
            _fail({e['path'] for e in entries} <= set(self.targets),
                  'Recovery journal exceeds explicit target allowlist')
            _fail(set(preserve_conflicts) <= {e['path'] for e in entries}
                  and all(isinstance(v, Fingerprint) for v in preserve_conflicts.values()),
                  'Invalid explicit conflict resolution')
            if events and events[-1]['kind'] in ('committed', 'recovered'):
                return RecoveryReport(journal, complete=True)
            previous_fd, previous_number, previous_bytes = self._tx_fd, self._event_number, self._journal_bytes
            self._tx_fd, self._event_number = fd, len(events)
            self._journal_bytes = self.limits.max_journal_total_bytes - budget.remaining
            restored, unchanged, changed, unresolved, preserved, errors = [], [], [], [], [], []
            conflicts = {}
            reconciled = {}
            evidence_bytes = sum(len(_decode(entry['bytes'], maximum=self.limits.max_evidence_bytes))
                                 for entry in manifest['evidence'] if entry['bytes'] is not None)
            def recovery_budget():
                remaining = _ReadBudget(self.limits.max_capture_bytes)
                remaining.consume(evidence_bytes, 'recovery evidence')
                return remaining
            current_budget = recovery_budget()
            try:
                for entry in entries:
                    p = entry['path']
                    try:
                        self._check_active()
                        current = _read(self.root / p, utf8=True, limits=self.limits, budget=current_budget)
                        _fail(current.parent == tuple(entry['parent']), f'Source parent changed: {p}')
                        before = _decode(entry['before'], maximum=self.limits.max_source_bytes)
                        after = _decode(entry['after'], maximum=self.limits.max_source_bytes)
                        original_fp = _fp_load(entry['fingerprint'])
                        restored_fps = [_fp_load(e['fingerprint']) for e in events
                                        if e.get('path') == p and e['kind'] in ('restore_intent', 'restored')]
                        post_fps = [_fp_load(e['fingerprint']) for e in events
                                    if e.get('path') == p and e['kind'] in ('replace_intent', 'applied')]
                        if current.data == before and current.fingerprint in [original_fp, *restored_fps]:
                            unchanged.append(p)
                            reconciled[p] = current
                            continue
                        if (current.data == after and current.fingerprint in post_fps):
                            name = 'restore-' + uuid.uuid4().hex + '.bin'
                            fp = self._write_new(fd, name, before, mode=original_fp.mode, group=original_fp.gid,
                                                 xattrs=tuple((k, _decode(v, maximum=self.limits.max_metadata_bytes))
                                                              for k, v in entry['xattrs']),
                                                 stage=True, path=p)
                            self._event('restore_intent', path=p, fingerprint=_fp_json(fp))
                            self._hit('before_restore', p)
                            now = _read(self.root / p, utf8=True, limits=self.limits)
                            _fail(now == current, f'Postimage changed during recovery: {p}')
                            parent_fd = _directory((self.root / p).parent)
                            try:
                                _fail(_identity(os.fstat(parent_fd)) == current.parent,
                                      f'Source parent changed during recovery: {p}')
                                stage = _read(self._namespace / journal / name, utf8=True, limits=self.limits)
                                _fail(stage.fingerprint == fp, f'Recovery stage changed: {p}')
                                os.replace(name, Path(p).name, src_dir_fd=fd, dst_dir_fd=parent_fd)
                                self._hit('after_restore', p)
                                self._sync(parent_fd, 'source_directory_fsync', p)
                                self._sync(fd, 'journal_directory_fsync', p)
                            finally:
                                os.close(parent_fd)
                            reconciled[p] = _read(self.root / p, utf8=True, limits=self.limits)
                            _fail(reconciled[p].fingerprint == fp, f'Restored file changed: {p}')
                            restored.append(p)
                            self._event('restored', path=p, fingerprint=_fp_json(fp))
                        elif preserve_conflicts.get(p) == current.fingerprint:
                            preserved.append(p)
                            reconciled[p] = current
                        else:
                            changed.append(p)
                            unresolved.append(p)
                            conflicts[p] = current.fingerprint
                    except Exception as error:
                        unresolved.append(p)
                        errors.append(f'{p}: {type(error).__name__}: {error}')
                # A crash may have occurred between a restore rename and its
                # directory fsync. Flush all reconciled files/parents again,
                # then validate exact identities before recording completion.
                if not unresolved and finish:
                    try:
                        completion_budget = recovery_budget()
                        for entry in entries:
                            p = entry['path']
                            now = _read(self.root / p, utf8=True, limits=self.limits, budget=completion_budget)
                            _fail(now == reconciled[p], f'Reconciled file changed: {p}')
                            parent_fd = _directory((self.root / p).parent)
                            try:
                                source_fd = os.open(Path(p).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                                    dir_fd=parent_fd)
                                try:
                                    self._sync(source_fd, 'recovery_source_fsync', p)
                                finally:
                                    os.close(source_fd)
                                self._sync(parent_fd, 'recovery_directory_fsync', p)
                            finally:
                                os.close(parent_fd)
                        final_budget = recovery_budget()
                        for p, expected in reconciled.items():
                            _fail(_read(self.root / p, utf8=True, limits=self.limits, budget=final_budget) == expected,
                                  f'Reconciled file changed: {p}')
                        self._event('recovered')
                    except Exception as completion_error:
                        unresolved.extend(entry['path'] for entry in entries)
                        errors.append(f'Recovery completion failed: {type(completion_error).__name__}: {completion_error}')
                        try:
                            self._event('recovery_required')
                        except Exception as marker_error:
                            errors.append(f'Recovery marker failed: {type(marker_error).__name__}: {marker_error}')
                return RecoveryReport(journal, tuple(restored), tuple(unchanged), tuple(changed),
                                      tuple(sorted(set(unresolved))), tuple(preserved), MappingProxyType(conflicts),
                                      tuple(errors), not unresolved and finish)
            finally:
                self._tx_fd, self._event_number, self._journal_bytes = previous_fd, previous_number, previous_bytes
        finally:
            os.close(fd)
