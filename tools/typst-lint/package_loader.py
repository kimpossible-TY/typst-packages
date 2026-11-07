"""Read only explicitly requested, reviewed package artifacts.

The root has Typst's ``--package-path`` layout, not an individual package
checkout. No cache, home directory, installed copy, network, evaluator or
recursive discovery is consulted. Import paths must come from the validated AST.
An immutable read snapshot is evidence for static analysis, not an editor lease,
a complete dependency graph or proof that a later Typst invocation uses it.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import tomllib
from typing import Iterable

from static_resolver import PackageSource, TEXT_UTILS, TEXT_UTILS_SOURCE_SHA256

MAX_MANIFEST_BYTES = 64 * 1024
MAX_SOURCE_BYTES = 1024 * 1024
MAX_IMPORT_PATHS = 4096
MAX_IMPORT_PATH_BYTES = 4096


class PackageInputError(ValueError):
    """The caller-supplied package path or one of its required files is invalid."""


@dataclass(frozen=True)
class PackageEvidence:
    import_path: str
    manifest_identity: str
    manifest_sha256: str
    source_identity: str | None
    source_sha256: str | None
    namespace: str
    name: str
    version: str
    entrypoint: str


@dataclass(frozen=True)
class PackageUnavailable:
    import_path: str
    code: str
    reason: str


@dataclass(frozen=True)
class PackageLoadResult:
    package_path: str
    sources: tuple[PackageSource, ...]
    evidence: tuple[PackageEvidence, ...]
    unavailable: tuple[PackageUnavailable, ...]


def _require(condition, message):
    if not condition:
        raise PackageInputError(message)


def _digest(source):
    return hashlib.sha256(source).hexdigest()


def _absolute_root(value):
    try:
        raw = os.fspath(value)
        _require(isinstance(raw, str) and bool(raw) and '\x00' not in raw,
                 'Package path must be a nonempty filesystem path')
        _require('..' not in Path(raw).parts,
                 'Package path must not contain parent traversal')
        root = Path(os.path.abspath(raw))
        _require(root.anchor == '/', 'Package path must use a canonical absolute filesystem root')
        return root
    except (TypeError, OSError, ValueError) as error:
        if isinstance(error, PackageInputError):
            raise
        raise PackageInputError(f'Invalid package path: {error}') from error


def _file_signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_uid, info.st_gid, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _directory_signature(info):
    # Unrelated changes to ancestor directory contents must not fail a read.
    return info.st_dev, info.st_ino, info.st_mode


@dataclass(frozen=True)
class _Directory:
    path: Path
    fd: int
    before: os.stat_result
    parent_fd: int | None
    name: str | None


@dataclass(frozen=True)
class _File:
    path: Path
    fd: int
    parent_fd: int
    name: str
    before: os.stat_result
    source: bytes
    limit: int


class _Snapshot:
    """Walk with directory descriptors and retain handles through final checks."""

    def __init__(self, root):
        self.root = root
        self.stack = ExitStack()
        self.directories = []
        self.files = []

    def __enter__(self):
        _require(hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY'),
                 'Safe package reads require no-follow directory descriptors')
        try:
            before = os.lstat(self.root.anchor)
            fd = self._open(self.root.anchor, self._directory_flags())
            _require(_directory_signature(before) == _directory_signature(os.fstat(fd)),
                     'Package path changed while opening its filesystem root')
            directory = _Directory(Path(self.root.anchor), fd, before, None, None)
            self.directories.append(directory)
            for part in self.root.parts[1:]:
                directory = self.directory(directory, part)
            self.directory_root = directory
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *args):
        self.stack.close()

    @staticmethod
    def _directory_flags():
        return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)

    def _open(self, name, flags, *, dir_fd=None):
        fd = os.open(name, flags, dir_fd=dir_fd)
        self.stack.callback(os.close, fd)
        return fd

    def directory(self, parent, name):
        path = parent.path / name
        before = os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
        _require(stat.S_ISDIR(before.st_mode),
                 f'Package path component is not a directory or is a symbolic link: {path}')
        fd = self._open(name, self._directory_flags(), dir_fd=parent.fd)
        _require(_directory_signature(before) == _directory_signature(os.fstat(fd)),
                 f'Package directory changed while opening: {path}')
        directory = _Directory(path, fd, before, parent.fd, name)
        self.directories.append(directory)
        return directory

    @staticmethod
    def _read_bytes(fd, limit, path):
        chunks = []
        size = 0
        while True:
            chunk = os.read(fd, min(65536, limit + 1 - size))
            if not chunk:
                return b''.join(chunks)
            size += len(chunk)
            _require(size <= limit, f'Package file exceeds byte limit ({limit}): {path}')
            chunks.append(chunk)

    def read(self, parent, name, limit):
        path = parent.path / name
        before = os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
        _require(stat.S_ISREG(before.st_mode),
                 f'Package input is not a regular file or is a symbolic link: {path}')
        _require(before.st_nlink == 1, f'Hard-linked package inputs are unsupported: {path}')
        _require(0 <= before.st_size <= limit,
                 f'Package file exceeds byte limit ({limit}): {path}')
        # NONBLOCK prevents a raced replacement by a FIFO from hanging open().
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0)
        fd = self._open(name, flags, dir_fd=parent.fd)
        _require(_file_signature(before) == _file_signature(os.fstat(fd)),
                 f'Package input changed while opening: {path}')
        source = self._read_bytes(fd, limit, path)
        _require(len(source) == before.st_size,
                 f'Package input changed while reading: {path}')
        record = _File(path, fd, parent.fd, name, before, source, limit)
        self._verify_file(record)
        try:
            source.decode('utf-8')
        except UnicodeError as error:
            raise PackageInputError(f'Package input is not UTF-8: {path}') from error
        self.files.append(record)
        return source

    @staticmethod
    def _verify_file(record):
        expected = _file_signature(record.before)
        _require(expected == _file_signature(os.fstat(record.fd))
                 and expected == _file_signature(os.stat(
                     record.name, dir_fd=record.parent_fd, follow_symlinks=False)),
                 f'Package input changed while reading: {record.path}')

    def verify(self):
        # Re-read both original open files, catching same-size edits as well as
        # inode replacement. This never opens another path or follows a link.
        for record in self.files:
            self._verify_file(record)
            os.lseek(record.fd, 0, os.SEEK_SET)
            current = self._read_bytes(record.fd, record.limit, record.path)
            _require(current == record.source,
                     f'Package input content changed while reading: {record.path}')
            self._verify_file(record)
        for directory in self.directories:
            current = (os.lstat(directory.path) if directory.parent_fd is None else
                       os.stat(directory.name, dir_fd=directory.parent_fd, follow_symlinks=False))
            expected = _directory_signature(directory.before)
            _require(expected == _directory_signature(current)
                     and expected == _directory_signature(os.fstat(directory.fd)),
                     f'Package directory changed while reading: {directory.path}')


def _requested_paths(import_paths):
    _require(not isinstance(import_paths, (str, bytes)),
             'Package imports must be an iterable of AST-derived path strings')
    result = set()
    try:
        for count, path in enumerate(import_paths, 1):
            _require(count <= MAX_IMPORT_PATHS, 'Too many package import paths')
            _require(type(path) is str and bool(path) and '\x00' not in path
                     and len(path.encode('utf-8')) <= MAX_IMPORT_PATH_BYTES,
                     'Invalid or oversized package import path')
            result.add(path)
    except (TypeError, UnicodeError) as error:
        raise PackageInputError('Invalid package import paths') from error
    return tuple(sorted(result))


def _manifest(source, path):
    try:
        data = tomllib.loads(source.decode('utf-8'))
    except (ValueError, RecursionError) as error:
        raise PackageInputError(f'Malformed package manifest: {path}') from error
    package = data.get('package')
    _require(type(package) is dict, f'Missing package table in manifest: {path}')
    for key in ('name', 'version', 'entrypoint'):
        _require(type(package.get(key)) is str and bool(package[key].strip()),
                 f'Missing or malformed package.{key} in manifest: {path}')
    if 'namespace' in package:
        _require(type(package['namespace']) is str and bool(package['namespace'].strip()),
                 f'Malformed package.namespace in manifest: {path}')
    entrypoint = package['entrypoint']
    _require(not entrypoint.startswith('/') and '\\' not in entrypoint and '\x00' not in entrypoint
             and all(part not in ('', '.', '..') for part in entrypoint.split('/')),
             f'Unsafe package entrypoint in manifest: {path}')
    return package


def load_packages(package_path: str | os.PathLike[str],
                  import_paths: Iterable[str]) -> PackageLoadResult:
    """Load only reviewed imports explicitly requested from a supplied root.

    Callers obtain ``import_paths`` using ``static_resolver.imported_paths``.
    Unknown imports and valid but unreviewed metadata/content produce explicit
    ``unavailable`` records, never invented exports or version diagnostics.
    A supplied root, required manifest or source that is unavailable, malformed,
    unsafe or changed raises ``PackageInputError``. Unknown imports are not read.
    Results and original source bytes are immutable. Paths are canonical absolute
    identities; no symbolic-link component, hard link or parent escape is used.
    """
    requested = _requested_paths(import_paths)
    root = _absolute_root(package_path)
    sources, evidence, unavailable = [], [], []
    try:
        with _Snapshot(root) as snapshot:
            for import_path in requested:
                if import_path != TEXT_UTILS:
                    unavailable.append(PackageUnavailable(
                        import_path, 'unreviewed-package',
                        'No reviewed static contract exists for this import; its files were not read'))
                    continue
                directory = snapshot.directory_root
                for component in ('local', 'text-utils', '0.1.2'):
                    directory = snapshot.directory(directory, component)
                manifest_path = str(directory.path / 'typst.toml')
                manifest = snapshot.read(directory, 'typst.toml', MAX_MANIFEST_BYTES)
                package = _manifest(manifest, manifest_path)
                metadata = dict(
                    import_path=import_path, manifest_identity=manifest_path,
                    manifest_sha256=_digest(manifest), namespace='local',
                    name=package['name'], version=package['version'], entrypoint=package['entrypoint'])
                if (package['name'] != 'text-utils' or package['version'] != '0.1.2'
                        or package['entrypoint'] != 'lib.typ'
                        or package.get('namespace', 'local') != 'local'):
                    evidence.append(PackageEvidence(source_identity=None, source_sha256=None, **metadata))
                    unavailable.append(PackageUnavailable(
                        import_path, 'unreviewed-package-metadata',
                        'Package metadata does not match the reviewed local/text-utils/0.1.2 lib.typ artifact'))
                    continue
                source_path = str(directory.path / 'lib.typ')
                source = snapshot.read(directory, 'lib.typ', MAX_SOURCE_BYTES)
                source_hash = _digest(source)
                evidence.append(PackageEvidence(source_identity=source_path, source_sha256=source_hash, **metadata))
                if source_hash != TEXT_UTILS_SOURCE_SHA256:
                    unavailable.append(PackageUnavailable(
                        import_path, 'unreviewed-package-source',
                        'Package source bytes do not match the reviewed text-utils 0.1.2 artifact'))
                    continue
                sources.append(PackageSource(import_path, source_path, source))
            snapshot.verify()
    except OSError as error:
        raise PackageInputError(f'Cannot safely read package path {root}: {error}') from error
    return PackageLoadResult(str(root), tuple(sources), tuple(evidence), tuple(unavailable))
