"""Bounded, separate output-only PDF publication journals (internal engine API).

No compiler, parser, source writer, CLI route or artifact authority lives here.
The output lease is an operator assertion, not detection of external writers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid

from transactions import TransactionError, _plain_metadata, _xattrs, _copy_xattrs
from resource_profiles import validate_resource_profile

KIND = 'typst-pdf-publication'
SCHEMA = 1
STORE_NAME = '.typst-lint-pdf-journals'
_TERMINAL = frozenset(('published', 'restored', 'aborted_unpublished'))
_STATES = frozenset(('prepared', 'compile_launch_intent', 'compiled', 'staged',
    'replace_intent', 'replaced', 'published', 'aborted_unpublished',
    'rollback_intent', 'restored', 'recovery_required'))
_TRANSITIONS = {None: {'prepared'}, 'prepared': {'compile_launch_intent','aborted_unpublished','recovery_required','restored'},
    'compile_launch_intent': {'compiled','aborted_unpublished','recovery_required','restored'},
    'compiled': {'staged','aborted_unpublished','recovery_required','restored'},
    'staged': {'replace_intent','aborted_unpublished','recovery_required','restored'},
    'replace_intent': {'replaced','rollback_intent','aborted_unpublished','recovery_required','restored'},
    'replaced': {'published','rollback_intent','recovery_required','restored'},
    'rollback_intent': {'restored','recovery_required','rollback_intent'},
    'recovery_required': {'rollback_intent','restored','recovery_required','aborted_unpublished'},
    'restored': set(), 'published': set(), 'aborted_unpublished': set()}

_ID = re.compile(r'[0-9a-f]{32}\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_LEASE = object()


class PublicationError(TransactionError):
    def __init__(self, reason, report=None):
        self.report = report
        super().__init__(reason)


class PublicationPending(PublicationError):
    def __init__(self, entries):
        self.entries = tuple(entries)
        self.journals = tuple(e.journal_id for e in entries if e.pending)
        super().__init__('Explicit PDF publication recovery required: ' + ', '.join(self.journals))


@dataclass(frozen=True, slots=True, init=False)
class OfflineOutputExclusive:
    """Caller asserts exclusive output, ancestors, exporters and journal storage."""
    _seal: object = field(repr=False)

    @classmethod
    def assert_exporters_watchers_closed_and_no_other_writers(cls):
        value = object.__new__(cls)
        object.__setattr__(value, '_seal', _LEASE)
        return value


@dataclass(frozen=True, slots=True)
class PublicationLimits:
    max_old_pdf_bytes: int = 16 * 1024 * 1024
    max_new_pdf_bytes: int = 16 * 1024 * 1024
    max_metadata_bytes: int = 256 * 1024
    max_journal_file_bytes: int = 256 * 1024
    max_journal_json_bytes: int = 1024 * 1024
    max_journal_binary_bytes: int = 48 * 1024 * 1024
    max_events: int = 64
    max_directory_entries: int = 128
    max_attempts: int = 128
    max_store_bytes: int = 256 * 1024 * 1024

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise PublicationError(f'Publication limit {name} must be a nonnegative integer')


DEFAULT_PUBLICATION_LIMITS = PublicationLimits()


@dataclass(frozen=True, slots=True)
class PublicationReport:
    phase: str
    gate_status: int
    compiler_invoked: bool | None
    compiler_status: int | None
    attempted_invocations: tuple = ()
    source_commit: str = 'unknown'
    output_disposition: str = 'untouched'
    recovery_required: bool = False
    journal_id: str | None = None
    observed_sha256: str | None = None
    source_journal_id: str | None = None
    reason: str | None = None
    # Original immutable compiler facts only; never persisted or reconstructed.
    compile_report: object | None = None

    @property
    def resource_report(self):
        return None if self.compile_report is None else self.compile_report.resource_report

    @property
    def journal(self):
        return self.journal_id


@dataclass(frozen=True, slots=True)
class PublicationEntry:
    journal_id: str
    state: str
    pending: bool
    destination: str | None
    error: str | None = None
    compiler_invoked: bool | None = False
    compiler_status: int | None = None

    @property
    def journal(self):
        return self.journal_id


@dataclass(frozen=True, slots=True)
class _Observation:
    fingerprint: tuple
    sha256: str
    size: int
    mode: int
    uid: int
    gid: int
    xattrs: tuple = ()

    def json(self):
        return dict(fingerprint=list(self.fingerprint), sha256=self.sha256,
                    size=self.size, mode=self.mode, uid=self.uid, gid=self.gid,
                    xattrs=[[name, value.hex()] for name, value in self.xattrs])


def _demand(condition, message):
    if not condition:
        raise PublicationError(message)


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_uid, info.st_gid,
            getattr(info, 'st_flags', 0))


def _directory_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            getattr(info, 'st_flags', 0))


def _root(value):
    path = Path(value)
    _demand(path.is_absolute() and str(path) == os.path.abspath(path)
            and path != Path('/'), 'Explicit canonical absolute root required')
    # resolve is a comparison, never a substitute for the no-follow walk below.
    _demand(path.resolve() == path, 'Root contains a symlink or noncanonical component')
    return path


def literal_destination(value):
    _demand(type(value) is str and 0 < len(os.fsencode(value)) <= 4096
            and '\x00' not in value and not value.startswith('/')
            and all(p and p not in ('.', '..') for p in value.split('/'))
            and not any(c in value for c in '*?[]{}')
            and Path(value).suffix == '.pdf' and str(Path(value)) == value,
            'One literal canonical root-relative .pdf destination required')
    return value


def _lease(value):
    _demand(type(value) is OfflineOutputExclusive and getattr(value, '_seal', None) is _LEASE,
            'Separate explicit offline-output exclusive assertion required')


class _Anchor:
    """Retained no-follow descriptors and every named absolute directory edge."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.rows = []
        try:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            fd = os.open('/', flags)
            self.rows.append((Path('/'), fd, _directory_identity(os.fstat(fd))))
            prefix = Path('/')
            for name in self.directory.parts[1:]:
                child = os.open(name, flags, dir_fd=fd)
                prefix = prefix / name
                self.rows.append((prefix, child, _directory_identity(os.fstat(child))))
                fd = child
            self.check()
        except BaseException:
            self.close()
            raise

    @property
    def fd(self):
        return self.rows[-1][1]

    def evidence(self):
        return [[str(p), list(identity)] for p, _, identity in self.rows]

    def check(self):
        for i, (path, fd, identity) in enumerate(self.rows):
            named = (os.stat('/', follow_symlinks=False) if i == 0 else
                     os.stat(path.name, dir_fd=self.rows[i - 1][1], follow_symlinks=False))
            _demand(_directory_identity(os.fstat(fd)) == identity
                    == _directory_identity(named), f'Directory identity changed: {path}')

    def close(self):
        for _, fd, _ in reversed(self.rows):
            os.close(fd)
        self.rows.clear()


def _no_metadata(fd, path, limits):
    _plain_metadata(fd, Path(path), max_bytes=limits.max_metadata_bytes)
    values = _xattrs(fd, max_bytes=limits.max_metadata_bytes)
    _demand(not values or (sys.platform == 'darwin' and len(values) == 1
            and values[0][0] == 'com.apple.provenance'),
            f'Output/journal xattrs unsupported: {path}')
    return values


def _private_directory(fd, path, limits):
    info = os.fstat(fd)
    _demand(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o700,
            f'Existing publication directory must be owned mode 0700: {path}')
    return _no_metadata(fd, path, limits)


def _read(fd, name, maximum, limits, *, missing=False, private=False):
    """Bounded stable no-follow read. Existing output may contain arbitrary bytes."""
    try:
        handle = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
                         dir_fd=fd)
    except FileNotFoundError:
        if missing:
            return None, None
        raise
    try:
        before = os.fstat(handle)
        _demand(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and before.st_uid == os.geteuid()
                and stat.S_IMODE(before.st_mode) in ((0o600,) if private else (0o600, 0o644))
                and 0 <= before.st_size <= maximum,
                f'Expected bounded owned ordinary single-link file: {name}')
        metadata = _no_metadata(handle, name, limits)
        _demand(_identity(before) == _identity(os.stat(name, dir_fd=fd, follow_symlinks=False)),
                f'File identity changed: {name}')
        raw = bytearray()
        while True:
            chunk = os.read(handle, min(65536, maximum - len(raw) + 1))
            if not chunk:
                break
            _demand(len(raw) + len(chunk) <= maximum, f'File byte bound exceeded: {name}')
            raw.extend(chunk)
        _demand(_no_metadata(handle, name, limits) == metadata, 'File metadata changed while reading')
        _demand(_identity(before) == _identity(os.fstat(handle))
                == _identity(os.stat(name, dir_fd=fd, follow_symlinks=False))
                and len(raw) == before.st_size, f'File changed during bounded read: {name}')
        data = bytes(raw)
        return data, _Observation(_identity(before), _hash(data), len(data),
                                 stat.S_IMODE(before.st_mode), before.st_uid, before.st_gid, metadata)
    finally:
        os.close(handle)


def _observation(value, maximum=DEFAULT_PUBLICATION_LIMITS.max_metadata_bytes):
    if value is None:
        return None
    _demand(type(value) is dict and set(value) == {'fingerprint','sha256','size','mode','uid','gid','xattrs'},
            'Invalid observation schema')
    fp = value['fingerprint']
    _demand(type(fp) is list and len(fp) == 10 and all(type(i) is int and i >= 0 for i in fp)
            and type(value['sha256']) is str and _HASH.fullmatch(value['sha256'])
            and all(type(value[k]) is int and value[k] >= 0 for k in ('size','mode','uid','gid'))
            and value['mode'] in (0o600, 0o644)
            and value['uid'] == os.geteuid()
            and fp[2] == stat.S_IFREG | value['mode'] and fp[3] == 1
            and fp[4] == value['size'] and fp[7] == value['uid'] and fp[8] == value['gid']
            and fp[9] == 0, 'Invalid observation fingerprint')
    metadata = _metadata_load(value['xattrs'], maximum)
    return _Observation(tuple(fp), value['sha256'], value['size'], value['mode'], value['uid'], value['gid'], metadata)


def _metadata_load(value, maximum=DEFAULT_PUBLICATION_LIMITS.max_metadata_bytes):
    _demand(type(value) is list and len(value) <= 1, 'Invalid supported metadata list')
    if not value:
        return ()
    row = value[0]
    _demand(sys.platform == 'darwin' and type(row) is list and len(row) == 2
            and row[0] == 'com.apple.provenance' and type(row[1]) is str
            and len(row[1]) <= maximum * 2
            and len(row[1]) % 2 == 0 and re.fullmatch('[0-9a-f]*', row[1]),
            'Invalid bounded provenance metadata')
    return ((row[0], bytes.fromhex(row[1])),)


def _same_renamed(before, after):
    # Only our immediately observed rename may explain ctime, never recovery.
    return (before is not None and after is not None and before.sha256 == after.sha256
            and before.fingerprint[:6] == after.fingerprint[:6]
            and before.fingerprint[7:] == after.fingerprint[7:] and before.xattrs == after.xattrs)


def _names(fd, limit):
    names = []
    with os.scandir(fd) as iterator:
        for entry in iterator:
            _demand(len(names) < limit, 'Publication directory entry limit exceeded')
            names.append(entry.name)
    return sorted(names)


def _json(raw):
    def unique(pairs):
        result = {}
        for k, v in pairs:
            _demand(k not in result, 'Duplicate journal JSON key')
            result[k] = v
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
    except (ValueError, UnicodeDecodeError, RecursionError) as error:
        raise PublicationError(f'Invalid publication JSON: {error}') from error


class _Store:
    """Borrow a real transaction lock, or own one for read-only inventory/recovery."""
    def __init__(self, root, *, limits=DEFAULT_PUBLICATION_LIMITS, transaction=None,
                 create=False, fault=None):
        _demand(type(limits) is PublicationLimits, 'Immutable PublicationLimits required')
        limits.__post_init__()
        self.root = _root(root)
        self.limits, self.transaction, self.create, self.fault = limits, transaction, create, fault
        self.store = self.root.parent / STORE_NAME
        _demand(STORE_NAME.casefold() not in (p.casefold() for p in self.root.parts),
                'Selected root collides with reserved publication storage')
        self.namespace_name = 'root-' + _hash(os.fsencode(self.root))
        self.namespace = self.store / self.namespace_name
        self.anchors = []
        self._active = False
        self.ns_fd = None
        self.journal_fd = None
        self.journal_id = None
        self.events = []
        self.json_bytes = 0
        self.store_bytes = 0
        self._committed = False
        self.private_metadata = {}

    def observation(self, value):
        return _observation(value, self.limits.max_metadata_bytes)

    def hit(self, point, path=None):
        if self.fault is not None:
            self.fault(point, path)

    def sync(self, fd, point, path=None):
        self.hit(point, path)
        os.fsync(fd)

    def __enter__(self):
        try:
            self.root_anchor = _Anchor(self.root)
            self.anchors.append(self.root_anchor)
            self.root_identity = _directory_identity(os.fstat(self.root_anchor.fd))
            if self.transaction is not None:
                self.transaction._check_active()
                _demand(self.transaction.root == self.root
                        and os.fstat(self.transaction._root_fd).st_ino == self.root_identity[1]
                        and os.fstat(self.transaction._root_fd).st_dev == self.root_identity[0],
                        'Only the active original transaction root lock may be borrowed')
            else:
                try:
                    fcntl.flock(self.root_anchor.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    raise PublicationError(f'Root lock unavailable: {error}') from error
            self.parent_anchor = _Anchor(self.root.parent)
            self.anchors.append(self.parent_anchor)
            if self.create:
                _demand(self.root.name.casefold() != STORE_NAME.casefold(),
                        'Root collides with publication storage')
                self._mkdir(self.parent_anchor.fd, STORE_NAME)
            try:
                self.store_anchor = _Anchor(self.store)
            except FileNotFoundError:
                _demand(not self.create, 'Publication store missing')
                self._active = True
                return self
            self.anchors.append(self.store_anchor)
            self._retain_private(self.store_anchor)
            _demand(os.fstat(self.store_anchor.fd).st_dev == self.root_identity[0],
                    'Publication storage must be on the root filesystem')
            if self.create:
                self._mkdir(self.store_anchor.fd, self.namespace_name)
            try:
                self.ns_anchor = _Anchor(self.namespace)
            except FileNotFoundError:
                _demand(not self.create, 'Publication namespace missing')
                self._active = True
                return self
            self.anchors.append(self.ns_anchor)
            self.ns_fd = self.ns_anchor.fd
            self._retain_private(self.ns_anchor)
            _demand(os.fstat(self.ns_fd).st_dev == self.root_identity[0], 'Cross-device namespace')
            self._active = True
            self.check()
            return self
        except BaseException:
            self.close()
            raise

    def _mkdir(self, fd, name):
        try:
            os.mkdir(name, 0o700, dir_fd=fd)
            created = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            try:
                _private_directory(created, name, self.limits)
                os.fsync(created)
            finally:
                os.close(created)
            os.fsync(fd)
        except FileExistsError:
            pass  # Existing permission/metadata checks follow, never chmod rescue.

    def _retain_private(self, anchor):
        metadata = _private_directory(anchor.fd, anchor.directory, self.limits)
        prior = self.private_metadata.get(anchor.directory)
        _demand(prior is None or prior == metadata, 'Private directory metadata changed')
        self.private_metadata[anchor.directory] = metadata

    def check(self):
        _demand(self._active, 'Active publication root lock required')
        if self.transaction is not None:
            self.transaction._check_active()
        for anchor in self.anchors:
            anchor.check()
            if anchor.directory in (self.store, self.namespace) or anchor.directory.parent == self.namespace:
                _demand(_private_directory(anchor.fd, anchor.directory, self.limits)
                        == self.private_metadata[anchor.directory], 'Private directory metadata changed')
        _demand(_directory_identity(os.fstat(self.root_anchor.fd)) == self.root_identity,
                'Publication root changed')

    def close(self):
        self._active = False
        for anchor in reversed(self.anchors):
            anchor.close()
        self.anchors.clear()
        self.ns_fd = self.journal_fd = None

    def __exit__(self, *args):
        self.close()

    def bind_destination(self, destination, expected_parents=None):
        self.destination = literal_destination(destination)
        self.output = self.root / self.destination
        anchor = _Anchor(self.output.parent)
        self.anchors.append(anchor)
        self.output_anchor = anchor
        _demand(all(identity[0] == self.root_identity[0] for path, _, identity in anchor.rows
                    if path == self.root or self.root in path.parents), 'Cross-device output ancestor')
        self.parents = anchor.evidence()
        if expected_parents is not None:
            _demand(self.parents == expected_parents, 'Recorded output ancestors changed')
        self.check()

    def output_now(self, *, maximum=None):
        self.check()
        result = _read(self.output_anchor.fd, self.output.name,
            (max(self.limits.max_old_pdf_bytes, self.limits.max_new_pdf_bytes)
             if maximum is None else maximum), self.limits, missing=True)
        self.check()
        _demand(result[1] is None or result[1].fingerprint[0] == self.root_identity[0],
                'Cross-device output file')
        return result

    def assert_output(self, expected):
        maximum = 0 if expected is None else expected.size
        _, now = self.output_now(maximum=maximum)
        _demand(now == expected, 'Output baseline changed (bytes, inode or metadata)')
        return now

    def _write(self, name, raw, *, mode=0o600, gid=None, binary=False, xattrs=None):
        _demand(type(raw) is bytes, 'Immutable journal bytes required')
        if binary:
            _demand(len(raw) <= max(self.limits.max_old_pdf_bytes, self.limits.max_new_pdf_bytes),
                    'Binary publication limit exceeded')
            names = _names(self.journal_fd, self.limits.max_directory_entries)
            used = sum(os.stat(n, dir_fd=self.journal_fd, follow_symlinks=False).st_size
                       for n in names if n in ('backup.bin','publish.bin','restore.bin'))
            _demand(used + len(raw) <= self.limits.max_journal_binary_bytes,
                    'Journal binary aggregate limit exceeded')
        else:
            _demand(len(raw) <= self.limits.max_journal_file_bytes
                    and self.json_bytes + len(raw) <= self.limits.max_journal_json_bytes,
                    'Journal JSON byte limit exceeded')
        _demand(self.store_bytes + len(raw) <= self.limits.max_store_bytes,
                'Publication store aggregate byte limit exceeded')
        self.check()
        self.hit('before_write', name)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=self.journal_fd)
        try:
            view = memoryview(raw)
            while view:
                self.hit('write', name)
                count = os.write(fd, view)
                _demand(count > 0, 'Short publication write')
                view = view[count:]
            if gid is not None and os.fstat(fd).st_gid != gid:
                os.fchown(fd, -1, gid)
            os.fchmod(fd, mode)
            self.hit('metadata_copy', name)
            if xattrs is not None:
                _copy_xattrs(fd, xattrs, max_bytes=self.limits.max_metadata_bytes)
            _no_metadata(fd, name, self.limits)
            self.sync(fd, 'file_fsync', name)
        finally:
            os.close(fd)
        if not binary:
            self.json_bytes += len(raw)
        self.store_bytes += len(raw)
        self.hit('before_readback', name)
        readback, observation = _read(self.journal_fd, name, len(raw), self.limits,
                                      private=not binary)
        _demand(readback == raw, 'Publication stage/readback changed')
        self.check()
        return observation

    def event(self, state, **values):
        _demand(state in _STATES and len(self.events) < self.limits.max_events,
                'Publication state/event bound exceeded')
        _demand(state in _TRANSITIONS[self.events[-1]['state'] if self.events else None],
                'Invalid live publication state transition')
        data = dict(schema=SCHEMA, kind=KIND, sequence=len(self.events), state=state, **values)
        self._validate_event(data)
        self._validate_chain(self.manifest, self.events + [data])
        raw = json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        self.hit('before_event', state)
        self._write(f'event-{len(self.events):04d}.json', raw)
        self.sync(self.journal_fd, 'event_directory_fsync', state)
        self.events.append(data)
        if state == 'published':
            self._committed = True
        elif state == 'restored':
            self._restored = True
            self._last_restoration = self.observation(data['restoration'])
        elif state == 'aborted_unpublished':
            self._aborted = True
        self.hit('after_event', state)

    def prepare(self, *, protected_paths=()):
        self.check()
        entries = self.inventory()
        if any(e.pending for e in entries):
            raise PublicationPending(entries)
        self.strengthen_terminals(entries)
        _demand(len(entries) < self.limits.max_attempts, 'Retained publication attempt limit exceeded')
        self.check_collisions(protected_paths)
        data, old = self.output_now(maximum=self.limits.max_old_pdf_bytes)
        _demand(old is None or old.size <= self.limits.max_old_pdf_bytes, 'Previous output exceeds bound')
        self.old = old
        self.journal_id = uuid.uuid4().hex
        _demand(self.ns_fd is not None, 'Existing publication namespace required')
        os.mkdir(self.journal_id, 0o700, dir_fd=self.ns_fd)
        self.sync(self.ns_fd, 'namespace_fsync', self.journal_id)
        anchor = _Anchor(self.namespace / self.journal_id)
        self.anchors.append(anchor)
        self.journal_fd = anchor.fd
        self._retain_private(anchor)
        backup = self._write('backup.bin', data, binary=True, xattrs=old.xattrs) if old is not None else None
        self.manifest = dict(schema=SCHEMA, kind=KIND, root=str(self.root),
            root_ancestors=self.root_anchor.evidence(), destination=self.destination,
            parents=self.parents, old=None if old is None else old.json(),
            backup=None if backup is None else backup.json(),
            private_metadata=[[str(p), [[n, v.hex()] for n, v in metadata]]
                              for p, metadata in sorted(self.private_metadata.items())])
        raw = json.dumps(self.manifest, sort_keys=True, separators=(',', ':')).encode()
        self._write('manifest.json', raw)
        self.event('prepared')
        self.assert_output(old)
        self.hit('after_prepared', self.destination)

    def check_collisions(self, protected_paths):
        output_fold = str(self.output).casefold()
        forbidden = tuple(Path(p) for p in protected_paths) + (self.store,)
        _, current = self.output_now(maximum=self.limits.max_old_pdf_bytes)
        for path in forbidden:
            folded = str(path).casefold()
            _demand(output_fold != folded and not output_fold.startswith(folded + '/')
                    and not folded.startswith(output_fold + '/'), 'Destination collides with protected path')
            try:
                info = path.stat()
            except FileNotFoundError:
                continue
            _demand(current is None or current.fingerprint[:2] != (info.st_dev, info.st_ino),
                    'Destination aliases a protected inode')
        _demand(self.root != self.store and self.store not in self.root.parents,
                'Root collides with publication store')

    def _read_attempt(self, journal_id):
        _demand(type(journal_id) is str and _ID.fullmatch(journal_id), 'Invalid publication journal ID')
        anchor = _Anchor(self.namespace / journal_id)
        try:
            fd = anchor.fd
            _private_directory(fd, anchor.directory, self.limits)
            names = _names(fd, self.limits.max_directory_entries)
            _demand(all(n in ('manifest.json','backup.bin','publish.bin','restore.bin')
                        or re.fullmatch(r'event-[0-9]{4}\.json', n) for n in names),
                    'Unexpected publication journal entry')
            total = 0
            binary = 0
            for name in names:
                s = os.stat(name, dir_fd=fd, follow_symlinks=False)
                _demand(stat.S_ISREG(s.st_mode) and s.st_nlink == 1, 'Unsafe journal entry')
                total += s.st_size
                if name.endswith('.bin'):
                    binary += s.st_size
            _demand(binary <= self.limits.max_journal_binary_bytes
                    and total - binary <= self.limits.max_journal_json_bytes,
                    'Publication journal aggregate limit exceeded')
            raw, _ = _read(fd, 'manifest.json', self.limits.max_journal_file_bytes,
                           self.limits, private=True)
            manifest = _json(raw)
            _demand(type(manifest) is dict and set(manifest) == {'schema','kind','root',
                'root_ancestors','destination','parents','old','backup','private_metadata'}
                and manifest['schema'] == SCHEMA and type(manifest['schema']) is int
                and manifest['kind'] == KIND and manifest['root'] == str(self.root),
                'Wrong publication manifest kind/schema/root')
            literal_destination(manifest['destination'])
            _demand(manifest['root_ancestors'] == self.root_anchor.evidence(),
                    'Recorded root ancestors changed')
            self._validate_parents(manifest['parents'], self.root / manifest['destination'])
            self._validate_private_metadata(manifest['private_metadata'], anchor)
            old, backup = self.observation(manifest['old']), self.observation(manifest['backup'])
            for observation in (old, backup):
                self._metadata_bound(observation)
            _demand((old is None) == (backup is None)
                    and (old is None or (old.size <= self.limits.max_old_pdf_bytes
                        and old.fingerprint[0] == self.root_identity[0]
                        and old.xattrs == backup.xattrs)),
                    'Invalid previous output/backup')
            if backup is not None:
                backup_raw, observed = _read(fd, 'backup.bin', self.limits.max_old_pdf_bytes,
                                             self.limits, private=True)
                _demand(observed == backup and _hash(backup_raw) == old.sha256
                        and len(backup_raw) == old.size, 'Backup changed or corrupt')
            else:
                _demand('backup.bin' not in names, 'Unexpected backup for absent output')
            event_names = sorted(n for n in names if n.startswith('event-'))
            _demand(len(event_names) <= self.limits.max_events, 'Event count exceeded')
            events = []
            last = None
            json_size = len(raw)
            for i, name in enumerate(event_names):
                _demand(name == f'event-{i:04d}.json', 'Publication event sequence gap')
                event_raw, _ = _read(fd, name, self.limits.max_journal_file_bytes,
                                     self.limits, private=True)
                json_size += len(event_raw)
                _demand(json_size <= self.limits.max_journal_json_bytes, 'Event JSON bound exceeded')
                event = _json(event_raw)
                _demand(type(event) is dict and {'schema','kind','sequence','state'} <= set(event)
                        and event['schema'] == SCHEMA and type(event['schema']) is int
                        and event['kind'] == KIND and event['sequence'] == i
                        and type(event['sequence']) is int and event['state'] in _TRANSITIONS[last],
                        'Invalid publication state transition')
                self._validate_event(event)
                for key in ('stage','postimage','restoration'):
                    if key in event:
                        self._metadata_bound(self.observation(event[key]))
                last = event['state']
                events.append(event)
            _demand(events, 'Missing durable prepared event')
            self._validate_chain(manifest, events)
            for binary_name, bound in (('publish.bin',self.limits.max_new_pdf_bytes),
                                       ('restore.bin',self.limits.max_old_pdf_bytes)):
                if binary_name in names:
                    raw_binary, obs = _read(fd, binary_name, bound, self.limits)
                    references = [e.get('stage') for e in events if e.get('stage_name') == binary_name]
                    _demand(not references or any(self.observation(ref) == obs for ref in references),
                            f'Recorded stage changed: {binary_name}')
            anchor.check()
            self.check()
            return manifest, events, total, json_size
        finally:
            anchor.close()

    def _metadata_bound(self, observation):
        _demand(observation is None or sum(len(os.fsencode(n)) + 1 + len(v)
                for n,v in observation.xattrs) <= self.limits.max_metadata_bytes,
                'Recorded file metadata bound exceeded')

    def _validate_private_metadata(self, value, attempt_anchor):
        expected = (self.store, self.namespace, attempt_anchor.directory)
        _demand(type(value) is list and len(value) == 3
                and [row[0] for row in value if type(row) is list and len(row) == 2]
                    == sorted(str(p) for p in expected), 'Invalid private directory metadata schema')
        for path, raw in value:
            metadata = _metadata_load(raw, self.limits.max_metadata_bytes)
            _demand(sum(len(os.fsencode(n)) + 1 + len(v) for n,v in metadata)
                    <= self.limits.max_metadata_bytes, 'Directory metadata bound exceeded')
            current = (_private_directory(attempt_anchor.fd, attempt_anchor.directory, self.limits)
                       if path == str(attempt_anchor.directory) else self.private_metadata[Path(path)])
            _demand(current == metadata, 'Retained private directory metadata changed')

    def _validate_parents(self, value, output):
        _demand(type(value) is list and len(value) == len(output.parent.parts), 'Invalid parent chain')
        for i, row in enumerate(value):
            path = Path('/').joinpath(*output.parent.parts[1:i + 1])
            _demand(type(row) is list and len(row) == 2 and row[0] == str(path)
                    and type(row[1]) is list and len(row[1]) == 6
                    and all(type(n) is int and n >= 0 for n in row[1])
                    and stat.S_ISDIR(row[1][2]), 'Invalid recorded parent identity')

    def _validate_event(self, e):
        # Closed per-event schemas. Reports are inert bounded data, never commands.
        state = e['state']
        extras = {'prepared': set(), 'compile_launch_intent': {'source_sha256','source_journal_id','profile','source_target',
            'config_sha256','catalog_sha256'},
            'compiled': {'compiler_sha256','pdf_sha256','invocations','compiler_invoked','compiler_status'},
            'staged': {'stage','stage_name'}, 'replace_intent': {'stage','stage_name'},
            'replaced': {'postimage'}, 'published': {'postimage'},
            'aborted_unpublished': {'reason','compiler_invoked','compiler_status','invocations'},
            'rollback_intent': {'postimage','stage','stage_name','action'},
            'restored': {'restoration'}, 'recovery_required': {'reason'}}[state]
        _demand(set(e) == {'schema','kind','sequence','state'} | extras, 'Unexpected event fields')
        for key in ('stage','postimage','restoration'):
            if key in e:
                self.observation(e[key])
        if 'stage_name' in e:
            _demand(e['stage_name'] in ('publish.bin','restore.bin',None), 'Invalid stage name')
        for key in ('source_sha256','compiler_sha256','pdf_sha256','config_sha256','catalog_sha256'):
            if key in e:
                _demand(type(e[key]) is str and _HASH.fullmatch(e[key]), 'Invalid event hash')
        if 'source_journal_id' in e:
            _demand(e['source_journal_id'] is None or (type(e['source_journal_id']) is str
                    and _ID.fullmatch(e['source_journal_id'])), 'Invalid source journal ID')
        if 'source_target' in e:
            target = e['source_target']
            _demand(type(target) is str and 0 < len(os.fsencode(target)) <= 4096
                    and '\x00' not in target and not target.startswith('/')
                    and all(p and p not in ('.','..') for p in target.split('/'))
                    and target.endswith('.typ'), 'Invalid historical source target')
        if 'profile' in e:
            try:
                validate_resource_profile(e['profile'])
            except ValueError as error:
                raise PublicationError('Invalid publication profile') from error
        if 'compiler_invoked' in e:
            _demand(e['compiler_invoked'] is None or type(e['compiler_invoked']) is bool,
                    'Invalid compiler invocation observation')
            _demand(e['compiler_status'] is None or type(e['compiler_status']) is int,
                    'Invalid raw compiler status')
        if 'invocations' in e:
            _demand(type(e['invocations']) is list and len(e['invocations']) <= 2,
                    'Invalid bounded invocation report')
            for invocation in e['invocations']:
                _demand(type(invocation) is dict and set(invocation) == {'purpose','started','returncode','lifecycle'}
                        and invocation['purpose'] in ('compile','version-probe')
                        and (invocation['started'] is None or type(invocation['started']) is bool)
                        and (invocation['returncode'] is None or type(invocation['returncode']) is int)
                        and invocation['lifecycle'] in ('launch_failed','running','exited','signaled','uncertain'),
                        'Invalid invocation evidence')
        if 'reason' in e:
            _demand(type(e['reason']) is str and len(e['reason']) <= 2048, 'Invalid bounded reason')
        if 'action' in e:
            _demand(e['action'] in ('replace','unlink'), 'Invalid restoration action')

    def _validate_chain(self, manifest, events):
        """Reject structurally valid but contradictory historical observations."""
        old = self.observation(manifest['old'])
        compiled = stage = post = restoration_stage = None
        launched = False
        for event in events:
            state = event['state']
            if state == 'compile_launch_intent':
                _demand(not launched, 'Duplicate compiler launch intent')
                launched = True
            elif state == 'compiled':
                attempts = event['invocations']
                actual = [a for a in attempts if a['purpose'] == 'compile']
                _demand(launched and compiled is None and event['compiler_invoked'] is True
                        and event['compiler_status'] == 0 and len(actual) == 1
                        and actual[0]['started'] is True and actual[0]['returncode'] == 0
                        and actual[0]['lifecycle'] == 'exited',
                        'Compiled state contradicts compiler outcome')
                compiled = event
            elif state in ('staged','replace_intent'):
                observation = self.observation(event['stage'])
                _demand(compiled is not None and observation is not None
                        and event['stage_name'] == 'publish.bin'
                        and observation.size <= self.limits.max_new_pdf_bytes
                        and observation.sha256 == compiled['pdf_sha256']
                        and observation.fingerprint[0] == self.root_identity[0]
                        and observation.mode == (0o600 if old is None else old.mode)
                        and (old is None or (observation.gid == old.gid and observation.xattrs == old.xattrs)),
                        'Publication stage contradicts committed compile/metadata')
                if state == 'staged':
                    _demand(stage is None, 'Duplicate publication stage')
                    stage = observation
                else:
                    _demand(stage == observation, 'Replace intent differs from staged inode')
            elif state in ('replaced','published'):
                observation = self.observation(event['postimage'])
                _demand(stage is not None and _same_renamed(stage, observation),
                        'Publication postimage differs from staged inode')
                if state == 'replaced':
                    _demand(post is None, 'Duplicate replacement observation')
                    post = observation
                else:
                    _demand(post is not None and observation == post,
                            'Published completion differs from observed postimage')
            elif state == 'rollback_intent':
                observation = self.observation(event['postimage'])
                _demand(stage is not None and _same_renamed(stage, observation)
                        and (post is None or post == observation),
                        'Rollback targets an unrecognized publication inode')
                post = observation
                restoration_stage = self.observation(event['stage'])
                if old is None:
                    _demand(event['action'] == 'unlink' and restoration_stage is None
                            and event['stage_name'] is None, 'Invalid absent-output restoration intent')
                else:
                    _demand(event['action'] == 'replace' and event['stage_name'] == 'restore.bin'
                            and restoration_stage is not None and restoration_stage.sha256 == old.sha256
                            and restoration_stage.size == old.size and restoration_stage.mode == old.mode
                            and restoration_stage.uid == old.uid and restoration_stage.gid == old.gid
                            and restoration_stage.xattrs == old.xattrs
                            and restoration_stage.fingerprint[0] == self.root_identity[0],
                            'Restoration stage differs from original content/metadata')
            elif state == 'restored':
                observation = self.observation(event['restoration'])
                _demand(observation == old or (old is not None and restoration_stage is not None
                        and _same_renamed(restoration_stage, observation)),
                        'Restored completion is not a recognized original/restoration')
            elif state == 'aborted_unpublished':
                _demand(post is None, 'Cannot report untouched after observed replacement')

    def inventory(self):
        self.check()
        if self.ns_fd is None:
            return ()
        names = _names(self.ns_fd, self.limits.max_attempts)
        entries = []
        total = 0
        for name in names:
            try:
                manifest, events, size, _ = self._read_attempt(name)
                total += size
                _demand(total <= self.limits.max_store_bytes, 'Aggregate publication store bound exceeded')
                state = events[-1]['state']
                invoked, status = _compiler_outcome(events)
                entries.append(PublicationEntry(name, state, state not in _TERMINAL,
                    manifest['destination'], compiler_invoked=invoked, compiler_status=status))
            except (OSError, TransactionError, ValueError, TypeError, KeyError, RecursionError) as error:
                # Unknown/corrupt retained content cannot be excluded from the
                # aggregate write budget. Refuse writes rather than assume zero.
                total = max(total, self.limits.max_store_bytes + 1)
                entries.append(PublicationEntry(name, 'corrupt', True, None, str(error)[:2048], None, None))
        self.store_bytes = total
        self.check()
        return tuple(entries)

    def strengthen_terminals(self, entries):
        """Before a new writer, strengthen historical completion acknowledgments.

        Borrow this lock and temporary attempt descriptors; do not bind/read any
        historical destination, create events, or change its publication state.
        """
        keys = ('manifest','events','json_bytes','journal_id','journal_fd','old',
                'destination','output','output_anchor','parents')
        for entry in entries:
            if entry.pending:
                continue
            saved = {key: self.__dict__[key] for key in keys if key in self.__dict__}
            anchor_count = len(self.anchors)
            private_paths = set(self.private_metadata)
            try:
                self.open_attempt(entry.journal_id, bind_destination=False)
                _demand(self.events[-1]['state'] == entry.state
                        and entry.state in _TERMINAL, 'Historical terminal state changed')
                self.sync_terminal(point_prefix='writer_terminal')
            finally:
                for anchor in reversed(self.anchors[anchor_count:]):
                    anchor.close()
                del self.anchors[anchor_count:]
                for path in set(self.private_metadata) - private_paths:
                    del self.private_metadata[path]
                for key in keys:
                    if key in saved:
                        self.__dict__[key] = saved[key]
                    else:
                        self.__dict__.pop(key, None)

    def sync_terminal(self, *, point_prefix='recovery_terminal'):
        """Recovery acknowledges a visible terminal record only after own syncs.

        Inventory intentionally never calls this. Current output is not read or
        changed; completion is historical even if a later writer replaced it.
        """
        _demand(self.events and self.events[-1]['state'] in _TERMINAL,
                'Only terminal completion evidence can be durability-confirmed')
        event = self.events[-1]
        name = f"event-{event['sequence']:04d}.json"
        raw, observation = _read(self.journal_fd, name, self.limits.max_journal_file_bytes,
                                 self.limits, private=True)
        _demand(_json(raw) == event, 'Terminal event changed before recovery sync')
        fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
                     dir_fd=self.journal_fd)
        try:
            _demand(_identity(os.fstat(fd)) == observation.fingerprint,
                    'Terminal event descriptor changed')
            self.sync(fd, point_prefix + '_file_fsync', event['state'])
            _demand(_identity(os.fstat(fd)) == observation.fingerprint
                    == _identity(os.stat(name, dir_fd=self.journal_fd, follow_symlinks=False))
                    and _no_metadata(fd, name, self.limits) == observation.xattrs,
                    'Terminal event changed during recovery sync')
        finally:
            os.close(fd)
        self.sync(self.journal_fd, point_prefix + '_directory_fsync', event['state'])
        check_raw, check_observation = _read(self.journal_fd, name,
            self.limits.max_journal_file_bytes, self.limits, private=True)
        _demand(check_raw == raw and check_observation == observation,
                'Terminal event changed after recovery sync')
        self.check()

    def open_attempt(self, journal_id, *, bind_destination=True):
        _demand(self.ns_fd is not None, 'Recovery requires an existing publication namespace')
        self.manifest, self.events, _, self.json_bytes = self._read_attempt(journal_id)
        self.journal_id = journal_id
        anchor = _Anchor(self.namespace / journal_id)
        self.anchors.append(anchor)
        self.journal_fd = anchor.fd
        self._retain_private(anchor)
        self.old = self.observation(self.manifest['old'])
        self.destination = self.manifest['destination']
        self.output = self.root / self.destination
        if bind_destination:
            self.bind_destination(self.destination, self.manifest['parents'])
        self.check()

    def stage(self, raw):
        _demand(len(raw) <= self.limits.max_new_pdf_bytes, 'New PDF byte limit exceeded')
        self.assert_output(self.old)
        mode = 0o600 if self.old is None else self.old.mode
        gid = os.getegid() if self.old is None else self.old.gid
        stage = self._write('publish.bin', raw, mode=mode, gid=gid, binary=True,
                            xattrs=None if self.old is None else self.old.xattrs)
        self.event('staged', stage=stage.json(), stage_name='publish.bin')
        self.hit('after_staged', self.destination)
        return stage

    def replace(self, stage, authority):
        authority()
        self.assert_output(self.old)
        _, current = _read(self.journal_fd, 'publish.bin', self.limits.max_new_pdf_bytes, self.limits)
        _demand(current == stage, 'Publication stage changed')
        self.event('replace_intent', stage=stage.json(), stage_name='publish.bin')
        authority()
        self.assert_output(self.old)
        self.hit('before_replace', self.destination)
        self.assert_output(self.old)
        _, current = _read(self.journal_fd, 'publish.bin', self.limits.max_new_pdf_bytes, self.limits)
        _demand(current == stage, 'Publication stage changed before rename')
        authority()
        self._replace_started = True
        os.replace('publish.bin', self.output.name, src_dir_fd=self.journal_fd,
                   dst_dir_fd=self.output_anchor.fd)
        self._replace_returned = True
        _, post = self.output_now()
        _demand(_same_renamed(stage, post), 'Published name differs from our renamed stage')
        self.postimage = post
        self.hit('after_replace_before_event', self.destination)
        self.event('replaced', postimage=post.json())
        self.hit('after_replaced', self.destination)
        self.sync(self.output_anchor.fd, 'destination_directory_fsync', self.destination)
        self.sync(self.journal_fd, 'moved_stage_directory_fsync', self.destination)
        self.hit('after_directory_sync_before_commit', self.destination)
        authority()
        self.assert_output(post)
        self.hit('before_published_event', self.destination)
        authority()
        self.assert_output(post)
        # From this point a failed event write/fsync is an uncertain commit ack.
        self._completion_started = True
        self.event('published', postimage=post.json())
        self._committed = True
        return post

    def restore(self, postimage, *, recovery=False):
        self.assert_output(postimage)
        prefix = 'recovery_' if recovery else ''
        if self.old is None:
            self.event('rollback_intent', postimage=postimage.json(), stage=None,
                       stage_name=None, action='unlink')
            self.hit(prefix + 'before_restore', self.destination)
            self.assert_output(postimage)
            os.unlink(self.output.name, dir_fd=self.output_anchor.fd)
            self.hit(prefix + 'after_restore_before_event', self.destination)
            self.sync(self.output_anchor.fd, 'restoration_directory_fsync', self.destination)
            self.sync(self.journal_fd, 'restoration_journal_directory_fsync', self.destination)
            self.assert_output(None)
            restored = None
        else:
            raw, backup = _read(self.journal_fd, 'backup.bin', self.limits.max_old_pdf_bytes,
                                 self.limits, private=True)
            _demand(backup == self.observation(self.manifest['backup']) and _hash(raw) == self.old.sha256,
                    'Retained backup changed')
            # A prior interrupted stage is only reused if its exact durable intent matches.
            try:
                stage = self._write('restore.bin', raw, mode=self.old.mode, gid=self.old.gid, binary=True,
                                    xattrs=self.old.xattrs)
            except FileExistsError:
                _, stage = _read(self.journal_fd, 'restore.bin', self.limits.max_old_pdf_bytes, self.limits)
                intents = [e for e in self.events if e['state'] == 'rollback_intent'
                           and e.get('stage_name') == 'restore.bin']
                _demand(intents and stage == self.observation(intents[-1]['stage']),
                        'Unrecognized existing restoration stage')
            self.event('rollback_intent', postimage=postimage.json(), stage=stage.json(),
                       stage_name='restore.bin', action='replace')
            self.hit(prefix + 'before_restore', self.destination)
            self.assert_output(postimage)
            _, current = _read(self.journal_fd, 'restore.bin', self.limits.max_old_pdf_bytes, self.limits)
            _demand(current == stage, 'Restoration stage changed')
            os.replace('restore.bin', self.output.name, src_dir_fd=self.journal_fd,
                       dst_dir_fd=self.output_anchor.fd)
            _, restored = self.output_now()
            _demand(_same_renamed(stage, restored), 'Restored name differs from owned stage')
            self._restoration_observed = restored
            self.hit(prefix + 'after_restore_before_event', self.destination)
            self.sync(self.output_anchor.fd, 'restoration_directory_fsync', self.destination)
            self.sync(self.journal_fd, 'restoration_journal_directory_fsync', self.destination)
            self.assert_output(restored)
        self.event('restored', restoration=None if restored is None else restored.json())
        self.hit(prefix + 'after_restored', self.destination)
        return restored


def _compiler_outcome(events):
    invoked, status = False, None
    for event in events:
        if event['state'] == 'compile_launch_intent':
            invoked, status = None, None
        if 'compiler_invoked' in event:
            invoked, status = event['compiler_invoked'], event['compiler_status']
    return invoked, status


def inventory_publications(root, *, limits=DEFAULT_PUBLICATION_LIMITS):
    """Read-only inventory under an independently acquired root lock; no mkdir."""
    with _Store(root, limits=limits) as store:
        return store.inventory()


def _inventory_already_locked(transaction, *, limits=DEFAULT_PUBLICATION_LIMITS):
    with _Store(transaction.root, transaction=transaction, limits=limits) as store:
        return store.inventory()


def recover_publication(root, *, destination, journal_id, lease,
                        limits=DEFAULT_PUBLICATION_LIMITS, fault=None):
    """Explicit output-only recovery; never executes compiler or recorded argv."""
    _lease(lease)
    destination = literal_destination(destination)
    with _Store(root, limits=limits, fault=fault) as store:
        entries = store.inventory()
        _demand(store.ns_fd is not None, 'Recovery requires existing journal storage')
        store.open_attempt(journal_id, bind_destination=False)
        _demand(store.destination == destination, 'Exact recovery destination allowlist mismatch')
        invoked, status = _compiler_outcome(store.events)
        source = next((e.get('source_journal_id') for e in store.events
                       if e['state'] == 'compile_launch_intent'), None)
        state = store.events[-1]['state']
        store.hit('recovery_started', destination)
        def report(phase, disposition, needed=False, reason=None, observed=None):
            return PublicationReport(phase, 3 if needed else 0, invoked, status, (),
                'historical', disposition, needed, journal_id,
                None if observed is None else observed.sha256, source, reason)
        if state in _TERMINAL:
            # A terminal commit is never a recovery rollback target. Confirm
            # visible completion durability, including an earlier uncertain ack.
            try:
                store.sync_terminal()
            except (OSError, TransactionError, ValueError) as error:
                return report('recovery_required', 'uncertain', True,
                              'Terminal completion durability unconfirmed: ' + str(error)[:1800])
            disposition = ('published' if state == 'published' else
                           'restored' if state == 'restored' else 'untouched')
            return report(state, disposition,
                          reason='Historical completion; current output was not examined')
        try:
            store.bind_destination(destination, store.manifest['parents'])
            _, now = store.output_now()
            if now == store.old:
                store.event('restored', restoration=None if now is None else now.json())
                return report('restored', 'untouched', observed=now)
            # Strict full postrename observations only. Stage ctime is NOT silently
            # ignored across restart; an unobserved rename remains unresolved.
            posts = [_observation(e['postimage']) for e in store.events
                     if e['state'] in ('replaced','rollback_intent')]
            restorations = [_observation(e['restoration']) for e in store.events if 'restoration' in e]
            if now in restorations:
                store.event('restored', restoration=None if now is None else now.json())
                return report('restored', 'restored', observed=now)
            intents = [e for e in store.events if e['state'] == 'rollback_intent']
            if now is None and store.old is None and intents and intents[-1]['action'] == 'unlink':
                store.event('restored', restoration=None)
                return report('restored', 'restored')
            _demand(now is not None and now in posts, 'Output is not an exactly recognized publication postimage')
            restored = store.restore(now, recovery=True)
            return report('restored', 'restored', observed=restored)
        except (OSError, TransactionError, ValueError, TypeError) as error:
            if getattr(store, '_restored', False):
                return report('restored', 'restored', observed=store._last_restoration)
            try:
                store.event('recovery_required', reason=str(error)[:2048])
            except (OSError, TransactionError, ValueError):
                pass
            return report('recovery_required', 'preservedconflict', True, str(error)[:2048])
