"""Read only explicitly requested, reviewed package artifacts.

The root has Typst's ``--package-path`` layout, not an individual package
checkout. No cache, home directory, installed copy, network, evaluator or
recursive discovery is consulted. Import paths must come from the validated AST;
an exact verified package may request its fixed optional namespace attestation.
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

from package_artifacts import PackageArtifact, SUPPLEMENTAL_ARTIFACTS
from static_resolver import (PackageSource, REVIEWED_PACKAGES, REVIEWED_NAMESPACE_PACKAGES,
                             REVIEWED_NAMESPACE_DEPENDENCIES)

MAX_MANIFEST_BYTES = 64 * 1024
MAX_SOURCE_BYTES = 1024 * 1024
MAX_IMPORT_PATHS = 4096
MAX_IMPORT_PATH_BYTES = 4096


class PackageInputError(ValueError):
    """The caller-supplied package path or one of its required files is invalid."""


@dataclass(frozen=True)
class ArtifactEvidence:
    relative_path: str
    source_identity: str
    source_sha256: str


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
    artifacts: tuple[ArtifactEvidence, ...] = ()


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
        _require(name not in ('', '.', '..') and '/' not in name,
                 'Package directory traversal requires one safe component at a time')
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
        _require(name not in ('', '.', '..') and '/' not in name,
                 'Package reads require a basename under a verified directory descriptor')
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


def read_snapshot_file(path: str | os.PathLike[str], limit: int) -> tuple[str, bytes]:
    """Read one explicit file with the same retained-descriptor safeguards.

    Used by the optional reference catalog and configured entrypoint loaders,
    not package dependency discovery. Verification rereads the retained
    descriptor, never reopens an unchecked path. No evaluator, cache or
    sibling-file lookup is involved.
    """
    _require(type(limit) is int and limit > 0, 'Invalid snapshot file byte limit')
    target = _absolute_root(path)
    with _Snapshot(target.parent) as snapshot:
        source = snapshot.read(snapshot.directory_root, target.name, limit)
        snapshot.verify()
    return str(target), source


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
    Only a successfully verified package may enqueue its fixed optional namespace
    attestation. A missing optional file/component reports unavailability without
    blocking other rules; malformed/unsafe inputs and raced disappearance still
    fail. Direct user-requested imports remain required, including namespace-only
    artifacts. No arbitrary dependency traversal or package evaluation occurs.
    Results and original source bytes are immutable. Paths are canonical absolute
    identities; no symbolic-link component, hard link or parent escape is used.
    """
    requested = _requested_paths(import_paths)
    root = _absolute_root(package_path)
    sources, evidence, unavailable = [], [], []
    pending = [(path, False) for path in requested]
    scheduled = set(requested)
    try:
        with _Snapshot(root) as snapshot:
            def exists(parent, name):
                # Only initial absence is optional. Do not catch failures during
                # the subsequent descriptor walk/open/read/verification: an
                # existing file that disappears during capture is a race.
                try:
                    os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
                    return True
                except FileNotFoundError:
                    return False

            def missing(import_path):
                unavailable.append(PackageUnavailable(
                    import_path, 'missing-optional-namespace',
                    'Optional reviewed namespace evidence is absent from the explicit package path; dependent repairs are unavailable'))

            for import_path, optional in pending:
                contract = next((p for p in (*REVIEWED_PACKAGES, *REVIEWED_NAMESPACE_PACKAGES)
                                 if p.import_path == import_path), None)
                if contract is None:
                    unavailable.append(PackageUnavailable(
                        import_path, 'unreviewed-package',
                        'No reviewed static contract exists for this import; its files were not read'))
                    continue
                directory = snapshot.directory_root
                absent = False
                for component in (contract.namespace, contract.name, contract.version):
                    if optional and not exists(directory, component):
                        absent = True
                        break
                    directory = snapshot.directory(directory, component)
                if absent or (optional and not exists(directory, 'typst.toml')):
                    missing(import_path)
                    continue
                manifest_path = str(directory.path / 'typst.toml')
                manifest = snapshot.read(directory, 'typst.toml', MAX_MANIFEST_BYTES)
                package = _manifest(manifest, manifest_path)
                metadata = dict(
                    import_path=import_path, manifest_identity=manifest_path,
                    manifest_sha256=_digest(manifest), namespace=contract.namespace,
                    name=package['name'], version=package['version'], entrypoint=package['entrypoint'])
                if (package['name'] != contract.name or package['version'] != contract.version
                        or package['entrypoint'] != contract.entrypoint
                        or package.get('namespace', contract.namespace) != contract.namespace):
                    evidence.append(PackageEvidence(source_identity=None, source_sha256=None, **metadata))
                    unavailable.append(PackageUnavailable(
                        import_path, 'unreviewed-package-metadata',
                        f'Package metadata does not match the reviewed {contract.namespace}/{contract.name}/{contract.version} {contract.entrypoint} artifact'))
                    continue
                if contract.manifest_sha256 is not None and _digest(manifest) != contract.manifest_sha256:
                    evidence.append(PackageEvidence(source_identity=None, source_sha256=None, **metadata))
                    unavailable.append(PackageUnavailable(
                        import_path, 'unreviewed-package-manifest',
                        f'Package manifest bytes do not match the reviewed {contract.name} {contract.version} namespace artifact'))
                    continue
                package_directory = directory
                components = contract.entrypoint.split('/')
                for component in components[:-1]:
                    if optional and not exists(directory, component):
                        absent = True
                        break
                    directory = snapshot.directory(directory, component)
                if absent or (optional and not exists(directory, components[-1])):
                    evidence.append(PackageEvidence(source_identity=None, source_sha256=None, **metadata))
                    missing(import_path)
                    continue
                source_path = str(directory.path / components[-1])
                source = snapshot.read(directory, components[-1], MAX_SOURCE_BYTES)
                source_hash = _digest(source)
                evidence.append(PackageEvidence(source_identity=source_path, source_sha256=source_hash, **metadata))
                if source_hash != contract.source_sha256:
                    unavailable.append(PackageUnavailable(
                        import_path, 'unreviewed-package-source',
                        f'Package source bytes do not match the reviewed {contract.name} {contract.version} artifact'))
                    continue
                artifacts = []
                for relative, expected_hash in SUPPLEMENTAL_ARTIFACTS.get(import_path, ()):
                    artifact_directory = package_directory
                    parts = relative.split('/')
                    absent_artifact = False
                    for part in parts[:-1]:
                        if not exists(artifact_directory, part):
                            absent_artifact = True
                            break
                        artifact_directory = snapshot.directory(artifact_directory, part)
                    if absent_artifact or not exists(artifact_directory, parts[-1]):
                        continue
                    data = snapshot.read(artifact_directory, parts[-1], MAX_SOURCE_BYTES)
                    identity = str(artifact_directory.path / parts[-1])
                    artifacts.append(PackageArtifact(relative, identity, data))
                evidence[-1] = PackageEvidence(source_identity=source_path, source_sha256=source_hash,
                    artifacts=tuple(ArtifactEvidence(a.relative_path, a.source_identity, _digest(a.source))
                                    for a in artifacts), **metadata)
                sources.append(PackageSource(import_path, source_path, source, tuple(artifacts)))
                for dependency in REVIEWED_NAMESPACE_DEPENDENCIES.get(import_path, ()):
                    if dependency not in scheduled:
                        scheduled.add(dependency)
                        pending.append((dependency, True))
            snapshot.verify()
    except OSError as error:
        raise PackageInputError(f'Cannot safely read package path {root}: {error}') from error
    return PackageLoadResult(str(root), tuple(sources), tuple(evidence), tuple(unavailable))
