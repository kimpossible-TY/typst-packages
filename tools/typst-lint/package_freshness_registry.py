"""Compare explicit preview package versions with the official registry index.

Network access is opt-in, fixed to Typst's HTTPS index and bounded by an owned
child process. This module never examines a project or package directory and
never downloads packages. Index version order does not prove installed bytes,
runtime selection, compatibility, dependency ownership or update authority.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import threading
import time

REGISTRY_URL = 'https://packages.typst.org/preview/index.json'
MAX_INDEX_BYTES = 8 * 1024 * 1024
MAX_INDEX_ENTRIES = 100_000
MAX_PACKAGES = 256
FETCH_TIMEOUT_SECONDS = 15
_NAME = re.compile(r'[a-z][a-z0-9_-]{0,63}\Z')
_VERSION = re.compile(r'(0|[1-9][0-9]{0,9})\.(0|[1-9][0-9]{0,9})\.(0|[1-9][0-9]{0,9})\Z')

# The worker has a fixed URL and receives no user data. No inherited proxy,
# credentials or user Python configuration are needed. Its parent owns the
# process and applies a hard wall-clock timeout, including DNS and slow reads.
_WORKER = r'''
import json, re, sys, urllib.request

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

url = 'https://packages.typst.org/preview/index.json'
limit = 8 * 1024 * 1024
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
request = urllib.request.Request(url, headers={'User-Agent': 'typst-lint-registry/1',
    'Accept': 'application/json', 'Accept-Encoding': 'identity'})
with opener.open(request, timeout=10) as response:
    if response.status != 200 or response.geturl() != url:
        raise ValueError('Unexpected registry response')
    if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
        raise ValueError('Unsupported content encoding')
    length = response.headers.get('Content-Length')
    if length is not None and (not length.isascii() or not length.isdigit() or int(length) > limit):
        raise ValueError('Invalid registry length')
    body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError('Registry response exceeds limit')
    if length is not None and len(body) != int(length):
        raise ValueError('Registry response length differs from header')
    def header(name):
        value = response.headers.get(name)
        return value if value and len(value) <= 256 and re.fullmatch(r'[\x20-\x7e]+', value) else None
    meta = json.dumps({'etag': header('ETag'), 'last_modified': header('Last-Modified')},
                      ensure_ascii=True, separators=(',', ':')).encode('ascii')
    sys.stdout.buffer.write(meta + b'\n' + body)
'''


class RegistryInputError(ValueError):
    """Invalid explicit registry request; no network request is attempted."""


class _RegistryUnavailable(ValueError):
    pass


class RegistryCancelled(Exception):
    """The caller cancelled a registry lookup; never a normal unknown result."""


class RegistryCleanupError(Exception):
    """The isolated registry worker could not be confirmed reaped."""


def _cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise RegistryCancelled('Registry lookup cancelled')


def _version(value):
    if type(value) is not str or len(value) > 32:
        raise ValueError('Invalid numeric package version')
    match = _VERSION.fullmatch(value)
    if not match:
        raise ValueError('Invalid numeric package version')
    components = tuple(int(part) for part in match.groups())
    if any(part > 0xffffffff for part in components):
        raise ValueError('Package version component exceeds u32')
    return components


def _requests(packages):
    if type(packages) not in (tuple, list) or not 1 <= len(packages) <= MAX_PACKAGES:
        raise RegistryInputError('Supply a bounded nonempty list of preview package name/version pairs')
    result, seen = [], set()
    for item in packages:
        if type(item) not in (tuple, list) or len(item) != 2:
            raise RegistryInputError('Each preview package must contain a name and numeric version')
        name, version = item
        if type(name) is not str or not _NAME.fullmatch(name):
            raise RegistryInputError('Preview package name must be bounded lowercase ASCII')
        try:
            parsed = _version(version)
        except ValueError as error:
            raise RegistryInputError(str(error)) from error
        if (name, version) in seen:
            raise RegistryInputError('Duplicate preview package request')
        seen.add((name, version))
        result.append((name, version, parsed))
    return tuple(result)


def _run_worker(cancel_event=None):
    """Run the isolated fixed worker with bounded streaming and cancellation."""
    _cancelled(cancel_event)
    environment = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}
    try:
        with tempfile.TemporaryDirectory(prefix='typst-preview-index-') as cwd:
            _cancelled(cancel_event)
            process = subprocess.Popen([sys.executable, '-I', '-S', '-c', _WORKER],
                cwd=cwd, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                _cancelled(cancel_event)
                output = bytearray()
                deadline = time.monotonic() + FETCH_TIMEOUT_SECONDS
                with selectors.DefaultSelector() as selector:
                    os.set_blocking(process.stdout.fileno(), False)
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while selector.get_map():
                        _cancelled(cancel_event)
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise _RegistryUnavailable('registry_unreachable_or_timed_out')
                        for key, _ in selector.select(min(remaining, 0.1)):
                            chunk = os.read(key.fileobj.fileno(), 65536)
                            if not chunk:
                                selector.unregister(key.fileobj)
                            else:
                                output.extend(chunk)
                                if len(output) > MAX_INDEX_BYTES + 1024:
                                    raise _RegistryUnavailable('registry_response_unavailable')
                while process.poll() is None:
                    _cancelled(cancel_event)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise _RegistryUnavailable('registry_unreachable_or_timed_out')
                    try:
                        process.wait(timeout=min(remaining, 0.1))
                    except subprocess.TimeoutExpired:
                        pass
                _cancelled(cancel_event)
                if process.returncode:
                    raise _RegistryUnavailable('registry_response_unavailable')
                return bytes(output)
            finally:
                try:
                    if process.poll() is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        except OSError:
                            process.kill()
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired) as error:
                    raise RegistryCleanupError('registry_worker_cleanup_failed') from error
                finally:
                    process.stdout.close()
    except OSError as error:
        raise _RegistryUnavailable('registry_unreachable_or_timed_out') from error


def _fetch_index(cancel_event=None):
    """Read only the fixed official URL with bounded output and hard deadline."""
    output = _run_worker(cancel_event)
    _cancelled(cancel_event)
    header, separator, body = output.partition(b'\n')
    if not separator or len(header) > 1024 or len(body) > MAX_INDEX_BYTES:
        raise _RegistryUnavailable('registry_response_invalid')
    try:
        metadata = json.loads(header.decode('ascii'))
        if type(metadata) is not dict or set(metadata) != {'etag', 'last_modified'}:
            raise ValueError('Invalid metadata')
        if any(value is not None and (type(value) is not str or len(value) > 256
               or not re.fullmatch(r'[\x20-\x7e]+', value)) for value in metadata.values()):
            raise ValueError('Invalid metadata')
    except (UnicodeError, ValueError, RecursionError) as error:
        raise _RegistryUnavailable('registry_response_invalid') from error
    _cancelled(cancel_event)
    return body, metadata


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _index(body, cancel_event=None):
    _cancelled(cancel_event)
    if type(body) is not bytes or len(body) > MAX_INDEX_BYTES:
        raise ValueError('Invalid index size')
    data = json.loads(body.decode('utf-8'), object_pairs_hook=_unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid JSON constant')))
    _cancelled(cancel_event)
    if type(data) is not list or not 1 <= len(data) <= MAX_INDEX_ENTRIES:
        raise ValueError('Invalid registry index')
    versions = {}
    for index, row in enumerate(data):
        if index % 256 == 0:
            _cancelled(cancel_event)
        if type(row) is not dict:
            raise ValueError('Invalid registry entry')
        name, raw_version = row.get('name'), row.get('version')
        if type(name) is not str or not _NAME.fullmatch(name):
            raise ValueError('Invalid registry package name')
        version = _version(raw_version)
        available = versions.setdefault(name, {})
        if version in available:
            raise ValueError('Duplicate registry package version')
        available[version] = raw_version
    _cancelled(cancel_event)
    return versions


def inspect_preview_registry(packages, *, online=False, cancel_event=None):
    """Compare explicitly selected versions, returning unknown on failed lookup.

    Callers supply each ``(name, version)``; no project, installed directory or
    package cache is read. ``online`` must be explicitly true for any network IO.
    """
    if type(online) is not bool:
        raise RegistryInputError('Online selection must be Boolean')
    if cancel_event is not None and type(cancel_event) is not threading.Event:
        raise RegistryInputError('Cancellation requires a threading.Event')
    requested = _requests(packages)
    _cancelled(cancel_event)
    now = lambda: datetime.now(timezone.utc).isoformat()
    result = dict(schema=1, mode='preview-registry-freshness', checked_at_utc=now(),
                  index_url=REGISTRY_URL, online_lookup_requested=online,
                  network_attempted=None if online else False, retrieved_at_utc=None,
                  snapshot_sha256=None, etag=None, last_modified=None,
                  scope='explicit-name-and-version-only', runtime_selection_proved=False,
                  installed_bytes_verified=False, compatibility_proved=False,
                  direct_dependency_proved=False, update_permission=False,
                  packages=[dict(name=name, requested_version=version, status='unknown',
                                 latest_version=None, reason='offline_not_checked')
                            for name, version, _ in requested])
    if not online:
        _cancelled(cancel_event)
        return result
    try:
        body, metadata = _fetch_index(cancel_event)
        _cancelled(cancel_event)
        result.update(network_attempted=True, retrieved_at_utc=now(),
                      snapshot_sha256=hashlib.sha256(body).hexdigest(), **metadata)
        versions = _index(body, cancel_event)
        _cancelled(cancel_event)
    except (_RegistryUnavailable, UnicodeError, ValueError, RecursionError) as error:
        _cancelled(cancel_event)
        reason = str(error) if isinstance(error, _RegistryUnavailable) else 'registry_index_invalid'
        for row in result['packages']:
            row['reason'] = reason
        return result
    for row, (name, _, parsed) in zip(result['packages'], requested):
        _cancelled(cancel_event)
        available = versions.get(name)
        if not available:
            row['reason'] = 'package_not_in_index'
            continue
        latest = max(available)
        row['latest_version'] = available[latest]
        if parsed not in available:
            row['reason'] = 'version_not_in_index'
        else:
            row['status'] = 'matches_latest_version' if parsed == latest else 'newer_version_available'
            row['reason'] = None
    _cancelled(cancel_event)
    return result
