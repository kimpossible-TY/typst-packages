"""Bounded explicit binary resource capture for document preview work.

No resource discovery, compiler execution, network, filesystem writes, or
compiler/write authority is provided. Callers supply exact roots and relative
paths; observed compiler dependencies and font choices remain separate facts.
"""
from dataclasses import dataclass, field
import os
from pathlib import Path
import pwd
import re
import stat
import sys
from types import MappingProxyType
import unicodedata
import weakref

import closed_core_compile as runtime
from typst_lint import SafetyError, digest

MAX_FILES = 512
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 192 * 1024 * 1024
MAX_PATH_BYTES = 4096
MAX_TOTAL_PATH_BYTES = 128 * 1024
MAX_DEPTH = 64
ROLES = frozenset(('source', 'package', 'resource', 'font'))
APPLE_ASSET_ROOT = '/System/Library/AssetsV2/com_apple_MobileAsset_Font8'
_APPLE_TRUST_PREFIX = '/System'
_APPLE_ASSET_PATH = re.compile(r'[0-9a-f]{40}\.asset/AssetData/[A-Za-z0-9._ -]+\.(?:ttf|otf|ttc)', re.I)


@dataclass(frozen=True, slots=True)
class ResourceRequest:
    root: str
    path: str
    role: str


@dataclass(frozen=True, slots=True)
class ResourceBlob:
    request: ResourceRequest
    sha256: str
    size: int
    evidence: runtime.FileEvidence
    data: bytes = field(repr=False)


@dataclass(frozen=True, slots=True, weakref_slot=True)
class ResourceCapture:
    roots: tuple[tuple[str, str], ...]
    files: tuple[ResourceBlob, ...]
    total_bytes: int
    apple_asset_fonts: bool = False
    complete_dependency_closure: bool = False
    compiler_authority: bool = False


_ISSUED = {}


def _signature(snapshot):
    if (type(snapshot) is not ResourceCapture or type(snapshot.roots) is not tuple
            or type(snapshot.files) is not tuple or type(snapshot.total_bytes) is not int
            or type(snapshot.apple_asset_fonts) is not bool
            or snapshot.complete_dependency_closure is not False or snapshot.compiler_authority is not False):
        raise SafetyError('Invalid resource capture representation')
    values = []
    for item in snapshot.files:
        if (type(item) is not ResourceBlob or type(item.request) is not ResourceRequest
                or any(type(value) is not str for value in
                       (item.request.root, item.request.path, item.request.role))
                or type(item.data) is not bytes or type(item.size) is not int
                or type(item.sha256) is not str or type(item.evidence) is not runtime.FileEvidence
                or item.size != len(item.data) or item.sha256 != digest(item.data)):
            raise SafetyError('Invalid captured resource bytes')
        evidence = item.evidence
        values.append((item.request.root, item.request.path, item.request.role, item.sha256,
                       item.size, evidence.path, evidence.identity, evidence.sha256,
                       evidence.size, evidence.parents, item.data))
    return snapshot.roots, tuple(values), snapshot.total_bytes, snapshot.apple_asset_fonts


def _same_data(expected, supplied):
    if type(expected) is not type(supplied):
        return False
    if type(expected) is tuple:
        return len(expected) == len(supplied) and all(_same_data(a, b) for a, b in zip(expected, supplied))
    return expected == supplied


def assert_capture(snapshot):
    """Check the identical issued in-memory record, without filesystem reads."""
    retained = _ISSUED.get(id(snapshot))
    if retained is None or retained[0]() is not snapshot or not _same_data(retained[1], _signature(snapshot)):
        raise SafetyError('Resource capture is fabricated, expired or changed')
    return snapshot


def _issue(snapshot):
    key = id(snapshot)
    signature = _signature(snapshot)
    def expired(reference):
        current = _ISSUED.get(key)
        if current is not None and current[0] is reference:
            _ISSUED.pop(key, None)
    _ISSUED[key] = weakref.ref(snapshot, expired), signature
    return snapshot


def _relative(path):
    if (type(path) is not str or not path or len(path) > MAX_PATH_BYTES or path.startswith('/') or '\\' in path
            or any(part in ('', '.', '..') for part in path.split('/'))
            or any(ord(char) < 32 or ord(char) == 127 for char in path)):
        raise SafetyError('Resource path must be canonical and relative')
    try:
        length = len(path.encode('utf-8'))
    except UnicodeError as error:
        raise SafetyError('Resource path must be UTF-8') from error
    if length > MAX_PATH_BYTES or len(path.split('/')) > MAX_DEPTH:
        raise SafetyError('Resource path limit exceeded')
    return length


def _apple_asset_font(path, relative, bound):
    """Capture one explicit MobileAsset font under a narrow owner/path policy."""
    if not _APPLE_ASSET_PATH.fullmatch(relative):
        raise SafetyError('Apple asset font path is outside the reviewed layout')
    try:
        asset_uid = pwd.getpwnam('_nsurlsessiond').pw_uid
        with runtime._anchored_parent(path) as (parent, parents, check_parents):
            for name, identity in parents:
                if name == _APPLE_TRUST_PREFIX or name.startswith(_APPLE_TRUST_PREFIX + '/'):
                    if identity[3] not in (0, asset_uid) or identity[2] & 0o022:
                        raise SafetyError('Unsafe Apple font asset parent owner or mode')
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                                 | getattr(os, 'O_CLOEXEC', 0), dir_fd=parent)
            try:
                before = os.fstat(descriptor)
                if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                        or before.st_uid != asset_uid or before.st_mode & (stat.S_ISUID | stat.S_ISGID | 0o022)
                        or not 0 <= before.st_size <= bound):
                    raise SafetyError('Unsafe Apple font asset file owner, mode or size')
                if runtime._identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) != runtime._identity(before):
                    raise SafetyError('Apple font asset changed while opening')
                data = bytearray()
                while True:
                    chunk = os.read(descriptor, min(65536, bound - len(data) + 1))
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > bound:
                        raise SafetyError('Apple font asset byte limit exceeded')
                if (runtime._identity(before) != runtime._identity(os.fstat(descriptor))
                        or runtime._identity(before) != runtime._identity(os.stat(
                            path.name, dir_fd=parent, follow_symlinks=False)) or len(data) != before.st_size):
                    raise SafetyError('Apple font asset changed while reading')
                check_parents()
                raw = bytes(data)
                return raw, runtime.FileEvidence(str(path), runtime._identity(before), digest(raw), len(raw), parents)
            finally:
                os.close(descriptor)
    except (KeyError, OSError, ValueError) as error:
        raise SafetyError(f'Apple font asset is unavailable or unsafe: {error}') from error


def capture(roots, requests, *, allow_apple_asset_fonts=False):
    """Capture explicit regular files with retained identity and second reads.

    Every root and request must be explicitly supplied. Source bytes, including
    fonts, are held in memory and never serialized by this module. A failure or
    changed file rejects the entire capture; omitted or later-selected resources
    are not inferred and system/font discovery is not disabled or captured.
    """
    if type(allow_apple_asset_fonts) is not bool:
        raise SafetyError('Apple asset font selection must be an explicit Boolean')
    if allow_apple_asset_fonts and sys.platform != 'darwin':
        raise SafetyError('Apple asset font capture is available only on macOS')
    if type(roots) is not dict or not roots or len(roots) > 8:
        raise SafetyError('Explicit resource roots are required')
    normalized = {}
    root_names = set()
    for name, raw in roots.items():
        if (type(name) is not str or len(name) > 64 or not name.isascii() or not name.isidentifier()
                or type(raw) is not str or len(raw) > MAX_PATH_BYTES or not raw.startswith('/')
                or '\\' in raw or any(ord(char) < 32 or ord(char) == 127 for char in raw)
                or str(Path(raw)) != raw or any(p in ('.', '..') for p in raw.split('/'))):
            raise SafetyError('Resource roots must have canonical absolute paths and identifiers')
        try:
            if len(raw.encode('utf-8')) > MAX_PATH_BYTES:
                raise SafetyError('Resource root path byte limit exceeded')
        except UnicodeError as error:
            raise SafetyError('Resource roots must be UTF-8') from error
        if name.casefold() in root_names:
            raise SafetyError('Resource root IDs collide under case folding')
        root_names.add(name.casefold())
        normalized[name] = Path(raw)
    if type(requests) not in (list, tuple) or not requests or len(requests) > MAX_FILES:
        raise SafetyError('Explicit resource file count is outside the fixed bounds')
    seen, identities, names, spellings, files = set(), set(), set(), {}, []
    total = path_total = 0
    for request in requests:
        if (type(request) is not ResourceRequest or type(request.root) is not str
                or request.root not in normalized or type(request.role) is not str
                or request.role not in ROLES):
            raise SafetyError('Invalid resource request')
        path_total += _relative(request.path)
        if path_total > MAX_TOTAL_PATH_BYTES:
            raise SafetyError('Resource aggregate path limit exceeded')
        key = (request.root, request.path)
        collision = (request.root, unicodedata.normalize('NFC', request.path).casefold())
        if key in seen or collision in names:
            raise SafetyError('Duplicate or colliding resource path')
        parts = request.path.split('/')
        for index in range(1, len(parts) + 1):
            spelling = '/'.join(parts[:index])
            folded = (request.root, unicodedata.normalize('NFC', spelling).casefold())
            kind = 'file' if index == len(parts) else 'directory'
            previous = spellings.get(folded)
            if previous is not None and previous != (spelling, kind):
                raise SafetyError('Resource path components collide under case or Unicode normalization')
            spellings[folded] = spelling, kind
        seen.add(key)
        names.add(collision)
        target = normalized[request.root] / request.path
        apple_asset = allow_apple_asset_fonts and str(normalized[request.root]) == APPLE_ASSET_ROOT
        if apple_asset and request.role != 'font':
            raise SafetyError('Apple font asset policy admits only explicitly selected fonts')
        raw, evidence = (_apple_asset_font(target, request.path, MAX_FILE_BYTES)
                         if apple_asset else runtime._capture(target, MAX_FILE_BYTES))
        identity = evidence.identity[:2]
        if identity in identities:
            raise SafetyError('Resource identity appears under multiple paths')
        identities.add(identity)
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise SafetyError('Resource aggregate byte limit exceeded')
        files.append(ResourceBlob(request, digest(raw), len(raw), evidence, raw))
    # Recheck the entire captured set after its final member is read. This is a
    # bounded sampled consistency check, not a live lock or atomic snapshot.
    for item in files:
        apple_asset = allow_apple_asset_fonts and str(normalized[item.request.root]) == APPLE_ASSET_ROOT
        if apple_asset:
            current, evidence = _apple_asset_font(Path(item.evidence.path), item.request.path, MAX_FILE_BYTES)
            if evidence != item.evidence:
                raise SafetyError('Apple font asset identity changed during capture')
        else:
            current = runtime._same(Path(item.evidence.path), item.evidence, MAX_FILE_BYTES)
        if current != item.data:
            raise SafetyError('Captured resource bytes changed during capture')
    return _issue(ResourceCapture(tuple(sorted((name, str(path)) for name, path in normalized.items())),
                                  tuple(files), total, apple_asset_fonts=allow_apple_asset_fonts))


def stage_document(context, records, catalog, snapshot, *, project_root='project', profile='standard'):
    """Bind verified source candidates to explicit captured resource bytes.

    The mapping is for private staging experiments only. It neither establishes
    complete dependencies nor rechecks live files, runs a compiler, writes files,
    or grants publication authority.
    """
    import include_context
    from document_sequence_preview import replay_document
    assert_capture(snapshot)
    include_context.assert_include_context(context)
    roots = dict(snapshot.roots)
    if type(project_root) is not str or roots.get(project_root) != context.project_root:
        raise SafetyError('Captured project root does not match the include context')
    resources = {(item.request.root, item.request.path): item for item in snapshot.files}
    for source in context.sources:
        resource = resources.get((project_root, source.path))
        if resource is None or resource.data != source.source:
            raise SafetyError('A captured document source is absent or differs from the resource snapshot')
    candidates = replay_document(context, records, catalog, profile=profile)
    staged = {(item.request.root, item.request.path):
              candidates[item.request.path] if item.request.root == project_root and item.request.path in candidates
              else item.data for item in snapshot.files}
    assert_capture(snapshot)
    return MappingProxyType(staged)
