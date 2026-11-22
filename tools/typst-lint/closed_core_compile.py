"""Internal explicit resource-profile compiler, never an ordinary build gate.

The trusted Python runtime and OS are not adversarial sandboxes. Only reviewed
compiler/font resources are copied; trusted OS libraries/frameworks are outside
that claim. Dependencies are checked evidence, not an exhaustive OS-read trace.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, fields
from enum import Enum
import hashlib
import math
import os
from pathlib import Path
import platform
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from types import MappingProxyType
import weakref

from protected_fix import ClosedCoreCompileSnapshot, ProtectedCoreFix
from resource_profiles import (ASCII_RESOURCE_PROFILE, APPLEGOTHIC_RESOURCE_PROFILE,
    REPERTOIRE_VERSIONS, resource_profile_protected_paths,
    validate_resource_profile, validate_source_repertoire)
from transactions import TransactionError
from typst_lint import InputError, SafetyError

RESOURCE_PROFILE = ASCII_RESOURCE_PROFILE
_VERSION = b'typst 0.15.1 (unknown commit)\n'


class UnsupportedCompileProfile(InputError):
    """Explicit resource profile/source is unsupported; no compile was run."""


class CompileSafetyError(SafetyError):
    """Stale authority, unsafe capture, unreviewed compiler, or invalid artifact."""
    def __init__(self, *args, report=None):
        self.report = report if report is not None else CompileReport((), 'refused', None)
        super().__init__(*args)


class CompilerFailureKind(str, Enum):
    """Observed failure cause, distinct from a cleanup-observed process status."""
    PROCESS_EXIT = 'process_exit'
    CANCELLED = 'cancelled'
    TIMEOUT = 'timeout'
    STREAM_LIMIT = 'stream_limit'
    UNKNOWN = 'unknown'


class CompilerFailure(Exception):
    """A probe/compile failed, was cancelled, exceeded limits, or timed out."""
    def __init__(self, reason, invocation=None, *,
                 kind: CompilerFailureKind = CompilerFailureKind.UNKNOWN, report=None):
        self.reason = reason
        self.invocation = invocation
        self._kind = kind
        self.report = report if report is not None else CompileReport((), 'pending', None)
        super().__init__(reason)

    @property
    def kind(self) -> CompilerFailureKind:
        return self._kind


@dataclass(frozen=True, slots=True)
class FileEvidence:
    path: str
    identity: tuple[int, ...]
    sha256: str
    size: int
    parents: tuple[tuple[str, tuple[int, ...]], ...] = ()


@dataclass(frozen=True, slots=True)
class FontIdentity:
    """Reviewed metadata, not inferred permission to redistribute font bytes."""
    format: str
    family: str
    postscript_name: str
    full_name: str
    version: str
    unique_name: str


@dataclass(frozen=True, slots=True)
class FontEvidence:
    face_index: int
    installed: FileEvidence
    private: FileEvidence
    identity: FontIdentity


@dataclass(frozen=True, slots=True)
class ResourceReport:
    """Original captured facts; later staleness does not refresh or erase them."""
    profile: str
    repertoire_version: str
    fonts: tuple[FontEvidence, ...]


@dataclass(frozen=True, slots=True)
class Invocation:
    purpose: str
    argv: tuple[str, ...]
    cwd: str
    environment: tuple[tuple[str, str], ...]
    returncode: int | None
    stdout: bytes
    stderr: bytes
    launch_attempted: bool = False
    started: bool | None = False
    lifecycle: str = 'uncertain'
    termination_reason: str | None = None


@dataclass(frozen=True, slots=True)
class CompilerFailureInfo:
    reason: str
    invocation: Invocation | None
    kind: CompilerFailureKind = CompilerFailureKind.UNKNOWN


@dataclass(frozen=True, slots=True)
class CompileReport:
    """Output-only historical facts, never compiler or artifact authority."""
    attempts: tuple[Invocation, ...]
    safety_gate: str
    compiler_failure: CompilerFailureInfo | None
    resource_report: ResourceReport | None = None

    @property
    def attempted_invocations(self):
        return self.attempts

    @property
    def compiler_invoked(self):
        attempts = tuple(item for item in self.attempts if item.purpose == 'compile')
        if any(item.started is True for item in attempts):
            return True
        return None if any(item.started is None for item in attempts) else False

    @property
    def compiler_status(self):
        return next((item.returncode for item in reversed(self.attempts)
                     if item.purpose == 'compile'), None)


class _Attempt:
    """Issuer-owned mutable state; only immutable snapshots leave the runner."""
    def __init__(self, purpose, argv, cwd, environment, output, error):
        self.metadata = purpose, tuple(argv), str(cwd), tuple(sorted(environment.items()))
        self.output, self.error = output, error
        self.started = None
        self.lifecycle = 'uncertain'
        self.returncode = None
        self.termination_reason = None

    def observe(self, process):
        # Only an observed wait/poll status can establish exit/signal facts.
        if process.returncode is not None:
            self.returncode = process.returncode
            self.lifecycle = 'signaled' if self.returncode < 0 else 'exited'

    def snapshot(self):
        return Invocation(*self.metadata, self.returncode, bytes(self.output), bytes(self.error),
                          True, self.started, self.lifecycle, self.termination_reason)


class _AttemptLedger:
    def __init__(self):
        self.attempts = []
        self.compiler_failure = None
        self.resource_report = None

    def begin(self, *args):
        # There is at most one probe and one compile in this fixed protocol.
        _demand(len(self.attempts) < 2, 'Compiler invocation count exceeded')
        attempt = _Attempt(*args)
        self.attempts.append(attempt)
        return attempt

    def note_failure(self, failure):
        if isinstance(failure, CompilerFailure) and self.compiler_failure is None:
            self.compiler_failure = CompilerFailureInfo(failure.reason, failure.invocation, failure.kind)

    def snapshot(self, safety_gate):
        return CompileReport(tuple(item.snapshot() for item in self.attempts),
                             safety_gate, self.compiler_failure, self.resource_report)


@dataclass(frozen=True, slots=True)
class _ReviewedNativeArtifact:
    path: Path
    sha256: str
    version: bytes


# Reviewed path + bytes + platform, not an approval supplied by a caller. An
# unknown artifact is refused BEFORE executing even a version probe.
_NATIVE_ARTIFACTS = MappingProxyType({('darwin', 'arm64'): _ReviewedNativeArtifact(
    Path('/opt/homebrew/Cellar/typst/0.15.1/bin/typst'),
    '08d01e5a434e821c4a1ce30dd1593e1bbc57c9b1203c4d760d8912ef4ce6a621',
    _VERSION),
})
@dataclass(frozen=True, slots=True)
class _ReviewedFontArtifact:
    path: Path
    sha256: str
    size: int
    face_index: int
    identity: FontIdentity


@dataclass(frozen=True, slots=True)
class _ReviewedResourceArtifact:
    compiler_sha256: str
    fonts: tuple[_ReviewedFontArtifact, ...]


# Private trust declarations, not selectable paths, hashes, metadata or argv.
# Absence of OS/2 embedding bits is NOT redistribution permission. This font
# may be copied only to an ephemeral private local workspace on this Mac.
_RESOURCE_ARTIFACTS = MappingProxyType({
    (APPLEGOTHIC_RESOURCE_PROFILE, 'darwin', 'arm64'): _ReviewedResourceArtifact(
        '08d01e5a434e821c4a1ce30dd1593e1bbc57c9b1203c4d760d8912ef4ce6a621',
        (_ReviewedFontArtifact(resource_profile_protected_paths(APPLEGOTHIC_RESOURCE_PROFILE)[0],
            '69071005e523e3a2bf276b35b0066f68a7ec0d942c860941ad188ef15ef69c51',
            15253080, 0, FontIdentity('TrueType', 'AppleGothic', 'AppleGothic',
                'AppleGothic Regular', '22.0d2e1',
                'AppleGothic Regular;13.0d1e3;2017-07-12')),)),
})
_MAX_FONT = 16 * 1024 * 1024
_MAX_FONT_TOTAL = 16 * 1024 * 1024
_MAX_FONTS = 1
_MAX_EXECUTABLE = 64 * 1024 * 1024
_MAX_PDF = 16 * 1024 * 1024
_MAX_WORKSPACE = 96 * 1024 * 1024
_MAX_STREAM = 64 * 1024
_MAX_DEPENDENCIES = 64 * 1024
_MAX_ENTRIES = 128
_LAUNCHERS = tuple(Path(__file__).resolve().parent / name
                   for name in ('typst-lint', 'typst-style', 'typst_lint.py'))


def _demand(condition, message):
    if not condition:
        raise CompileSafetyError(message)


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_uid, info.st_gid)


def _directory_identity(info):
    # Parent directory timestamps/size change for unrelated sibling writes.
    # Device/inode, kind/mode and ownership must stay fixed.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)


@contextmanager
def _anchored_parent(path):
    """Retain and recheck every no-follow named directory edge from root."""
    path = Path(path)
    _demand(path.is_absolute() and path.anchor == '/' and path.name
            and all(part not in ('.', '..') for part in path.parts[1:]),
            'Capture requires an exact canonical path')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    directories = []
    try:
        root = os.open('/', flags)
        directories.append((Path('/'), root, _directory_identity(os.fstat(root))))
        parent = root
        prefix = Path('/')
        for name in path.parts[1:-1]:
            child = os.open(name, flags, dir_fd=parent)
            prefix = prefix / name
            fingerprint = _directory_identity(os.fstat(child))
            directories.append((prefix, child, fingerprint))
            _demand(fingerprint == _directory_identity(os.stat(name, dir_fd=parent,
                    follow_symlinks=False)), 'Capture parent identity changed')
            parent = child

        def check_parents():
            for index, (directory, fd, fingerprint) in enumerate(directories):
                _demand(_directory_identity(os.fstat(fd)) == fingerprint,
                        'Capture parent descriptor changed')
                named = (os.stat('/', follow_symlinks=False) if index == 0 else
                    os.stat(directory.name, dir_fd=directories[index - 1][1], follow_symlinks=False))
                _demand(_directory_identity(named) == fingerprint, 'Capture parent was replaced')
        check_parents()
        evidence = tuple((str(directory), identity) for directory, _, identity in directories)
        yield parent, evidence, check_parents
        check_parents()
    finally:
        for _, descriptor, _ in reversed(directories):
            os.close(descriptor)


def _capture_unchecked(path, bound, *, executable=False):
    """Bounded no-follow leaf read, retained parent and named-leaf evidence."""
    path = Path(path)
    with _anchored_parent(path) as (parent, parents, check_parents):
        descriptor = os.open(path.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        try:
            before = os.fstat(descriptor)
            _demand(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                    and 0 <= before.st_size <= bound,
                    'Capture requires a bounded single-link regular file')
            _demand(not before.st_mode & (stat.S_ISUID | stat.S_ISGID | 0o022)
                    and before.st_uid in (0, os.geteuid()), 'Unsafe captured file mode/owner')
            if executable:
                _demand(bool(before.st_mode & 0o111), 'Compiler is not executable')
            _demand(_identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False))
                    == _identity(before), 'Capture identity changed')
            raw = bytearray()
            while True:
                chunk = os.read(descriptor, min(65536, bound - len(raw) + 1))
                if not chunk:
                    break
                raw.extend(chunk)
                _demand(len(raw) <= bound, 'Capture byte limit exceeded')
            _demand(_identity(before) == _identity(os.fstat(descriptor))
                    == _identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False))
                    and len(raw) == before.st_size, 'Capture changed while reading')
            check_parents()
            data = bytes(raw)
            return data, FileEvidence(str(path), _identity(before), _hash(data), len(data), parents)
        finally:
            os.close(descriptor)


def _capture(path, bound, *, executable=False):
    try:
        return _capture_unchecked(path, bound, executable=executable)
    except (OSError, ValueError, RuntimeError) as error:
        raise CompileSafetyError(f'Unsafe/unavailable captured file: {error}') from error


def _same(path, expected, bound, *, executable=False):
    raw, current = _capture(path, bound, executable=executable)
    _demand(current == expected, 'Captured file identity or bytes changed')
    return raw


def _reviewed_native_artifact():
    record = _NATIVE_ARTIFACTS.get((sys.platform, platform.machine()))
    _demand(record is not None, 'No reviewed native compiler artifact for this platform')
    return record


def _capture_compiler():
    record = _reviewed_native_artifact()
    raw, evidence = _capture(record.path, _MAX_EXECUTABLE, executable=True)
    for launcher in _LAUNCHERS:
        # Reject canonical aliases and inode aliases before executing anything.
        _demand(record.path != launcher.resolve(), 'Recursive compiler launcher')
        try:
            info = launcher.stat()
        except FileNotFoundError:
            continue
        _demand(evidence.identity[:2] != (info.st_dev, info.st_ino),
                'Recursive compiler launcher inode')
    _demand(evidence.sha256 == record.sha256, 'Unreviewed compiler SHA-256')
    return record, raw, evidence


def _reviewed_resource_artifact(profile):
    record = _RESOURCE_ARTIFACTS.get((profile, sys.platform, platform.machine()))
    _demand(record is not None, 'No reviewed font resource profile for this platform')
    return record


def _capture_fonts(profile, compiler):
    # ASCII never resolves/captures external fonts, including on unknown hosts.
    if profile == ASCII_RESOURCE_PROFILE:
        return ()
    record = _reviewed_resource_artifact(profile)
    _demand(record.compiler_sha256 == compiler.sha256,
            'Font profile does not bind this reviewed compiler')
    _demand(len(record.fonts) == _MAX_FONTS == 1
            and sum(font.size for font in record.fonts) <= _MAX_FONT_TOTAL,
            'Font resource count/total byte limit exceeded')
    captures = []
    for font in record.fonts:
        _demand(0 < font.size <= _MAX_FONT and font.face_index == 0,
                'Invalid reviewed font size/face')
        raw, installed = _capture(font.path, _MAX_FONT)
        _demand(installed.size == font.size and installed.sha256 == font.sha256,
                'Unreviewed font size or SHA-256')
        captures.append((font, raw, installed))
    return tuple(captures)


def _guard(fix, snapshot):
    _demand(type(fix) is ProtectedCoreFix and type(snapshot) is ClosedCoreCompileSnapshot,
            'A live bridge-owned committed compile snapshot is required')
    try:
        fix.assert_compile_current(snapshot)
    except TransactionError as error:
        raise CompileSafetyError(f'Committed compile authority is stale: {error}') from error


def _environment(base):
    # Construct from nothing. Never inherit PATH, TYPST_*, font/package settings,
    # SOURCE_DATE_EPOCH, DYLD_*/LD_*, proxies, credentials, or user cache paths.
    return {'HOME': str(base / 'home'), 'XDG_CACHE_HOME': str(base / 'cache'),
            'XDG_CONFIG_HOME': str(base / 'config'), 'XDG_DATA_HOME': str(base / 'data'),
            'TMPDIR': str(base / 'tmp'), 'LANG': 'C', 'LC_ALL': 'C', 'TZ': 'UTC'}


def _workspace_check(base, allowed_files, allowed_directories, pdf):
    root = base.lstat()
    _demand(stat.S_ISDIR(root.st_mode) and root.st_uid == os.geteuid()
            and not root.st_mode & 0o077, 'Unsafe private workspace root')
    total = 0
    count = 0
    for directory, subdirs, files_ in os.walk(base, followlinks=False):
        for name in subdirs + files_:
            path = Path(directory) / name
            info = path.lstat()
            count += 1
            _demand(count <= _MAX_ENTRIES, 'Workspace entry limit exceeded')
            if stat.S_ISDIR(info.st_mode):
                _demand(path in allowed_directories and info.st_uid == os.geteuid()
                        and not info.st_mode & 0o077, 'Unexpected/unsafe workspace directory')
            else:
                _demand(path in allowed_files and stat.S_ISREG(info.st_mode)
                        and info.st_nlink == 1 and info.st_uid == os.geteuid()
                        and not info.st_mode & 0o077, 'Unexpected/unsafe workspace file')
                total += info.st_size
                _demand(path != pdf or info.st_size <= _MAX_PDF, 'PDF byte limit exceeded')
                _demand(total <= _MAX_WORKSPACE, 'Workspace byte limit exceeded')


def _kill(process):
    # Also stops descendants retaining pipes. The reviewed production binary
    # does not spawn a shell; process groups bound failure/cancellation cleanup.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _run(argv, *, purpose, cwd, environment, deadline, cancel_event, check_workspace,
         _ledger=None):
    ledger = _AttemptLedger() if _ledger is None else _ledger
    output, error = bytearray(), bytearray()
    process = None
    attempt = None

    def report():
        if attempt is not None:
            return attempt.snapshot()
        # Legacy failure.invocation is retained for a prelaunch cancellation or
        # deadline. It is deliberately NOT an attempted invocation in the ledger.
        return Invocation(purpose, tuple(argv), str(cwd), tuple(sorted(environment.items())),
                          None, bytes(output), bytes(error))

    try:
        if cancel_event is not None and cancel_event.is_set():
            raise CompilerFailure('cancelled', report(), kind=CompilerFailureKind.CANCELLED)
        if time.monotonic() >= deadline:
            raise CompilerFailure('timeout', report(), kind=CompilerFailureKind.TIMEOUT)
        check_workspace()
        # Bound retained text independently of stream bounds. These values are
        # generated internally, not a user-supplied command/environment facility.
        _demand(len(argv) <= 64 and len(environment) <= 16
                and all(type(item) is str and len(os.fsencode(item)) <= 8192
                        for item in (*argv, str(cwd), *environment, *environment.values())),
                'Compiler invocation metadata limit exceeded')
        attempt = ledger.begin(purpose, argv, cwd, environment, output, error)
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, close_fds=True,
                start_new_session=True, umask=0o077)
        except (OSError, ValueError, subprocess.SubprocessError):
            attempt.started = False
            attempt.lifecycle = 'launch_failed'
            attempt.termination_reason = 'launch failed'
            raise
        # An interruption before Popen returns cannot honestly establish whether
        # a child started. Keep started=None and lifecycle=uncertain in that case.
        attempt.started = True
        attempt.lifecycle = 'running'
        with selectors.DefaultSelector() as selector:
            for stream, kind in ((process.stdout, 'stdout'), (process.stderr, 'stderr')):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, kind)
            while selector.get_map() or process.poll() is None:
                attempt.observe(process)
                if cancel_event is not None and cancel_event.is_set():
                    raise CompilerFailure('cancelled', kind=CompilerFailureKind.CANCELLED)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CompilerFailure('timeout', kind=CompilerFailureKind.TIMEOUT)
                check_workspace()
                for key, _ in selector.select(min(remaining, 0.025)):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        target = output if key.data == 'stdout' else error
                        if len(target) + len(chunk) > _MAX_STREAM:
                            raise CompilerFailure(f'{key.data} byte limit exceeded',
                                                  kind=CompilerFailureKind.STREAM_LIMIT)
                        target.extend(chunk)
        process.wait()
        attempt.observe(process)
        check_workspace()
        if process.returncode != 0:
            raise CompilerFailure('signal' if process.returncode < 0 else 'nonzero exit', report(),
                                  kind=CompilerFailureKind.PROCESS_EXIT)
        return report()
    except BaseException as failure:
        if attempt is not None and attempt.termination_reason is None:
            attempt.termination_reason = (failure.reason if isinstance(failure, CompilerFailure)
                else 'interrupted' if isinstance(failure, KeyboardInterrupt)
                else 'safety refusal' if isinstance(failure, CompileSafetyError)
                else 'runtime failure')
        cleanup_failure = None
        if process is not None:
            try:
                _kill(process)
            except BaseException as error_:
                cleanup_failure = error_
            finally:
                attempt.observe(process)
                if attempt.returncode is None:
                    attempt.lifecycle = 'uncertain'
        if isinstance(failure, CompilerFailure):
            failure.invocation = report()
        ledger.note_failure(failure)
        if isinstance(failure, (CompilerFailure, CompileSafetyError, KeyboardInterrupt)):
            failure.report = ledger.snapshot('refused' if isinstance(failure, CompileSafetyError)
                                             else 'pending')
        if cleanup_failure is not None and not isinstance(failure, KeyboardInterrupt):
            raise CompileSafetyError('Compiler cleanup did not complete',
                                     report=ledger.snapshot('refused')) from cleanup_failure
        raise
    finally:
        if process is not None:
            for stream in (process.stdout, process.stderr):
                stream.close()


def _write_private(path, raw, mode):
    with _anchored_parent(path) as (parent, _, check_parents):
        descriptor = os.open(path.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600, dir_fd=parent)
        try:
            view = memoryview(raw)
            while view:
                count = os.write(descriptor, view)
                _demand(count > 0, 'Private capture write made no progress')
                view = view[count:]
            os.fchmod(descriptor, mode)
            check_parents()
        finally:
            os.close(descriptor)


def _output_identity(info):
    # Content length/timestamps may change while the compiler writes. The
    # precreated inode/owner/mode/link identity must never be replaced.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_uid, info.st_gid)


def _bind_output(path, stack):
    with _anchored_parent(path) as (parent, _, check_parents):
        descriptor = os.open(path.name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600, dir_fd=parent)
        stack.callback(os.close, descriptor)
        expected = _output_identity(os.fstat(descriptor))
        _demand(expected == _output_identity(os.stat(path.name, dir_fd=parent,
                follow_symlinks=False)), 'New output identity changed')
        check_parents()
        return path, descriptor, expected


def _pdf(raw):
    _demand(raw.startswith((b'%PDF-1.', b'%PDF-2.')) and len(raw) >= 32,
            'Output is not a basic PDF')
    trailer = re.search(rb'startxref\s+(\d+)\s+%%EOF\s*\Z', raw[-1024:])
    _demand(trailer is not None, 'PDF is missing a complete final trailer')
    offset = int(trailer.group(1))
    _demand(0 < offset < len(raw) and
            (raw[offset:].startswith(b'xref') or
             re.match(rb'\d+ \d+ obj\b', raw[offset:]) is not None),
            'PDF startxref is invalid')


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class ClosedCoreCompileArtifact:
    """Opaque in-memory artifact. Access to PDF bytes rechecks live authority.

    Already-read bytes cannot be revoked. This is an internal cooperative API,
    not protection from hostile Python reflection or a malicious OS/user.
    """
    profile: str
    target: str
    source_sha256: str
    source_bytes: int
    config_sha256: str
    catalog_sha256: str
    evidence_hashes: tuple[tuple[str, str | None], ...]
    compiler: FileEvidence
    private_compiler: FileEvidence
    input: FileEvidence
    output: FileEvidence
    dependencies: FileEvidence
    dependency_paths: tuple[str, ...]
    probes: tuple[Invocation, ...]
    compilation: Invocation
    report: CompileReport
    resource_report: ResourceReport
    pdf_sha256: str
    _pdf_bytes: bytes = field(repr=False)

    def __init__(self, *args, **kwargs):
        raise TypeError('Artifacts are issued only by compile_committed_snapshot')

    def __getattribute__(self, name):
        if name == '_pdf_bytes':
            # Also covers ordinary dataclass asdict/astuple and generated frozen
            # slot state access. Explicit hostile reflection is out of scope.
            record = _ARTIFACTS.get(self)
            _demand(record is not None and record[0]() is not None, 'Unissued or expired artifact')
            assert_artifact_current(record[0](), self)
        return object.__getattribute__(self, name)

    @property
    def pdf_bytes(self):
        return self._pdf_bytes


# Identity registration rejects copied/reconstructed objects, including dataclass
# replacements. Weak owner references do not extend an offline context's lifetime.
_ARTIFACTS = weakref.WeakKeyDictionary()


def _artifact_values(artifact):
    def freeze(value):
        if type(value) in (FileEvidence, FontIdentity, FontEvidence, ResourceReport,
                           Invocation, CompileReport, CompilerFailureInfo):
            return (type(value), tuple(freeze(getattr(value, field.name)) for field in fields(value)))
        if type(value) is tuple:
            return tuple(freeze(item) for item in value)
        return value
    return tuple(freeze(object.__getattribute__(artifact, field.name)
                        if field.name == '_pdf_bytes' else getattr(artifact, field.name))
                 for field in fields(ClosedCoreCompileArtifact))


def assert_artifact_current(fix, artifact):
    _demand(type(artifact) is ClosedCoreCompileArtifact, 'Unissued artifact type')
    record = _ARTIFACTS.get(artifact)
    _demand(record is not None and record[0]() is fix and _artifact_values(artifact) == record[2],
            'Unissued, altered, or foreign-session artifact')
    _guard(fix, record[1])
    # Original compiler drift blocks later use too. The private workspace has
    # already been destroyed, so its immutable evidence is historical only.
    _same(Path(artifact.compiler.path), artifact.compiler, _MAX_EXECUTABLE, executable=True)
    for font in artifact.resource_report.fonts:
        _same(Path(font.installed.path), font.installed, _MAX_FONT)
    _demand(_hash(object.__getattribute__(artifact, '_pdf_bytes')) == artifact.pdf_sha256,
            'Artifact PDF bytes changed')
    _guard(fix, record[1])


def compile_committed_snapshot(fix, snapshot, *, profile, deadline_seconds=10.0, cancel_event=None):
    """Compile with an issuer-owned attempt ledger, never reporting authority."""
    ledger = _AttemptLedger()
    try:
        return _compile_committed_snapshot(fix, snapshot, profile=profile,
            deadline_seconds=deadline_seconds, cancel_event=cancel_event, _ledger=ledger)
    except BaseException as failure:
        ledger.note_failure(failure)
        if isinstance(failure, (CompilerFailure, CompileSafetyError, KeyboardInterrupt)):
            failure.report = ledger.snapshot('refused' if isinstance(failure, CompileSafetyError)
                                             else 'passed' if isinstance(failure, CompilerFailure)
                                             else 'pending')
        raise


def _compile_committed_snapshot(fix, snapshot, *, profile, deadline_seconds, cancel_event, _ledger):
    """Compile retained bytes, with no public path/hash/args/environment bypass.

    profile has no default. Repertoire is an additional restriction on source,
    never permission to bypass the independent closed-core AST grammar.
    """
    try:
        validate_resource_profile(profile)
    except ValueError as error:
        raise UnsupportedCompileProfile(str(error)) from error
    if (type(deadline_seconds) not in (int, float) or not math.isfinite(deadline_seconds)
            or not 0 < deadline_seconds <= 120
            or (cancel_event is not None and type(cancel_event) is not threading.Event)):
        raise UnsupportedCompileProfile('Invalid bounded deadline or cancellation event')
    deadline = time.monotonic() + deadline_seconds
    _guard(fix, snapshot)
    raw = snapshot.source
    try:
        validate_source_repertoire(profile, raw)
    except ValueError as error:
        raise UnsupportedCompileProfile(str(error)) from error
    _demand(_hash(raw) == snapshot.source_sha256 and len(raw) <= 1024 * 1024,
            'Invalid retained postimage hash/length')
    try:
        record, compiler_bytes, compiler = _capture_compiler()
        font_captures = _capture_fonts(profile, compiler)
        with tempfile.TemporaryDirectory(prefix='typst-embedded-ascii-') as temporary, ExitStack() as outputs:
            base = Path(temporary).resolve()
            os.chmod(base, 0o700)
            directories = {base / name for name in ('bin', 'project', 'out', 'home', 'cache',
                'config', 'data', 'tmp', 'packages', 'package-cache')}
            if font_captures:
                directories.add(base / 'fonts')
            input_path = base / 'project' / snapshot.target
            directories.update(input_path.parents)
            directories.discard(base)
            # Do not mkdir ancestors outside this owned workspace.
            directories = {p for p in directories if base in p.parents}
            for directory in sorted(directories, key=lambda p: len(p.parts)):
                directory.mkdir(mode=0o700, exist_ok=True)
            executable = base / 'bin' / 'typst'
            pdf, deps = base / 'out' / 'artifact.pdf', base / 'out' / 'dependencies.zero'
            bound_outputs = (_bind_output(pdf, outputs), _bind_output(deps, outputs))
            _write_private(executable, compiler_bytes, 0o500)
            _write_private(input_path, raw, 0o400)
            _, private_compiler = _capture(executable, _MAX_EXECUTABLE, executable=True)
            _, captured_input = _capture(input_path, 1024 * 1024)
            _demand(private_compiler.sha256 == compiler.sha256
                    and captured_input.sha256 == snapshot.source_sha256, 'Private copy differs')
            font_evidence = []
            for font, font_bytes, installed in font_captures:
                private_path = base / 'fonts' / 'AppleGothic.ttf'
                _write_private(private_path, font_bytes, 0o400)
                _, private = _capture(private_path, _MAX_FONT)
                _demand(private.size == installed.size and private.sha256 == installed.sha256,
                        'Private font copy differs')
                font_evidence.append(FontEvidence(font.face_index, installed, private, font.identity))
            resource_report = ResourceReport(profile, REPERTOIRE_VERSIONS[profile], tuple(font_evidence))
            _ledger.resource_report = resource_report
            environment = _environment(base)
            allowed = {executable, input_path, pdf, deps}
            allowed.update(Path(font.private.path) for font in font_evidence)
            directory_identities = {p: (p.stat().st_dev, p.stat().st_ino,
                p.stat().st_mode, p.stat().st_uid, p.stat().st_gid)
                for p in directories | {base}}

            def check_workspace():
                for path, expected in directory_identities.items():
                    info = path.lstat()
                    _demand((info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
                            == expected, 'Private workspace directory identity changed')
                for path, descriptor, expected in bound_outputs:
                    _demand(_output_identity(os.fstat(descriptor)) == expected
                            == _output_identity(path.lstat()), 'Bound compiler output was replaced')
                _workspace_check(base, allowed, directories, pdf)
                for font in font_evidence:
                    _demand(Path(font.private.path).lstat().st_size <= _MAX_FONT,
                            'Private font byte limit exceeded')

            last_invocation = None
            output = dependencies = None

            def check_request():
                if cancel_event is not None and cancel_event.is_set():
                    raise CompilerFailure('cancelled', last_invocation, kind=CompilerFailureKind.CANCELLED)
                if time.monotonic() >= deadline:
                    raise CompilerFailure('timeout', last_invocation, kind=CompilerFailureKind.TIMEOUT)

            def revalidate():
                _guard(fix, snapshot)
                _same(record.path, compiler, _MAX_EXECUTABLE, executable=True)
                _same(executable, private_compiler, _MAX_EXECUTABLE, executable=True)
                _same(input_path, captured_input, 1024 * 1024)
                for font in font_evidence:
                    _same(Path(font.installed.path), font.installed, _MAX_FONT)
                    _same(Path(font.private.path), font.private, _MAX_FONT)
                check_workspace()
                if output is not None:
                    _same(pdf, output, _MAX_PDF)
                if dependencies is not None:
                    _same(deps, dependencies, _MAX_DEPENDENCIES)
                _guard(fix, snapshot)

            try:
                revalidate()
                probe = _run((str(executable), '--version'), purpose='version-probe',
                    cwd=base / 'project', environment=environment, deadline=deadline,
                    cancel_event=cancel_event, check_workspace=check_workspace, _ledger=_ledger)
                last_invocation = probe
                _demand(probe.stdout == record.version and not probe.stderr, 'Compiler version mismatch')
                revalidate()
                argv = (str(executable), 'compile', '--format', 'pdf', '--root', str(base / 'project'),
                    '--ignore-system-fonts', '--package-path', str(base / 'packages'),
                    '--package-cache-path', str(base / 'package-cache'), '--creation-timestamp', '0',
                    '--jobs', '1', '--diagnostic-format', 'short', '--deps', str(deps),
                    '--deps-format', 'zero')
                if font_evidence:
                    argv += ('--font-path', str(base / 'fonts'))
                argv += (str(input_path), str(pdf))
                compilation = _run(argv, purpose='compile', cwd=base / 'project',
                    environment=environment, deadline=deadline, cancel_event=cancel_event,
                    check_workspace=check_workspace, _ledger=_ledger)
                last_invocation = compilation
                revalidate()
                pdf_bytes, output = _capture(pdf, _MAX_PDF)
                _pdf(pdf_bytes)
                dep_bytes, dependencies = _capture(deps, _MAX_DEPENDENCIES)
                # Reviewed Typst emits exactly the cwd-relative retained target.
                _demand(dep_bytes == os.fsencode(snapshot.target) + b'\0',
                        'Unexpected compiler dependency evidence')
                _same(pdf, output, _MAX_PDF)
                _same(deps, dependencies, _MAX_DEPENDENCIES)
                revalidate()
                values = (profile, snapshot.target, snapshot.source_sha256, len(raw),
                    snapshot.config_sha256, snapshot.catalog_sha256,
                    tuple(sorted((path, None if data is None else _hash(data))
                                 for path, data in snapshot.evidence.items())),
                    compiler, private_compiler, captured_input, output, dependencies,
                    (snapshot.target,), (probe,), compilation, _ledger.snapshot('passed'), resource_report,
                    _hash(pdf_bytes), pdf_bytes)
                artifact = object.__new__(ClosedCoreCompileArtifact)
                for field, value in zip(fields(ClosedCoreCompileArtifact), values, strict=True):
                    object.__setattr__(artifact, field.name, value)
            except BaseException as failure:
                # Capture the original failure BEFORE a final safety guard can
                # supersede it. Reports do not turn compiler status into gate status.
                _ledger.note_failure(failure)
                raise
            finally:
                # A compiler failure never rolls back a valid committed source fix.
                # Staleness/safety takes precedence over a compiler's exit status.
                revalidate()
        _guard(fix, snapshot)
        _same(record.path, compiler, _MAX_EXECUTABLE, executable=True)
        for font in font_evidence:
            _same(Path(font.installed.path), font.installed, _MAX_FONT)
        _guard(fix, snapshot)
        check_request()
        _ARTIFACTS[artifact] = (weakref.ref(fix), snapshot, _artifact_values(artifact))
        return artifact
    except (CompileSafetyError, UnsupportedCompileProfile, CompilerFailure):
        raise
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        raise CompileSafetyError(f'Isolated compiler unavailable or unsafe: {error}') from error
