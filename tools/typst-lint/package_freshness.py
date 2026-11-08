"""Read-only comparison of an explicitly selected package with a GitHub ref.

The caller identifies both the checkout package and the installed copy. No
project invocation, import resolution, ambient cache discovery, fetch, or
package update is performed. Results describe sampled bytes, not a live lease.
"""
from __future__ import annotations

from contextlib import ExitStack
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
import tomllib
from urllib.parse import urlsplit

MAX_FILES = 2048
MAX_DIRS = 128
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_PATH_BYTES = 4096
MAX_GIT_OUTPUT = 4 * 1024 * 1024
MAX_PACKAGE_PAIRS = 64
MAX_BATCH_BYTES = 512 * 1024 * 1024
GIT_TIMEOUT = 15.0
_OID = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
_REF = re.compile(r'refs/(?:heads|tags)/[A-Za-z0-9][A-Za-z0-9._/-]{0,200}\Z')
_REPO = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z')
_OWNER = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})\Z')


class FreshnessError(ValueError):
    """Invalid input, unsafe snapshot, or unavailable bounded Git operation."""


class FreshnessCancelled(Exception):
    """A caller requested cancellation; no later work should be started."""


_CANCEL_EVENT = ContextVar('typst_package_freshness_cancel', default=None)


def _check_cancel():
    event = _CANCEL_EVENT.get()
    if event is not None and event.is_set():
        raise FreshnessCancelled('Package freshness check cancelled')


def _checked_call(function, *args, **kwargs):
    _check_cancel()
    result = function(*args, **kwargs)
    _check_cancel()
    return result


def _github_url(value):
    if type(value) is not str or len(value) > 512:
        raise FreshnessError('Upstream must be an explicit GitHub HTTPS URL')
    try:
        url = urlsplit(value)
    except ValueError as error:
        raise FreshnessError('Upstream must be an explicit GitHub HTTPS URL') from error
    parts = url.path.split('/')
    if (url.scheme != 'https' or url.netloc != 'github.com' or url.query or url.fragment
            or len(parts) != 3 or parts[0] or not _OWNER.fullmatch(parts[1])
            or not _REPO.fullmatch(parts[2]) or parts[2] in ('.', '..')
            or parts[2].endswith('.lock')):
        raise FreshnessError('Upstream must be an exact github.com owner/repository HTTPS URL')
    return value


def _ref(value):
    if (type(value) is not str or not _REF.fullmatch(value) or '..' in value
            or '//' in value or value.endswith(('/', '.'))
            or any(part in ('', '.', '..') or part.startswith('.') or part.endswith('.lock')
                   for part in value.split('/'))):
        raise FreshnessError('Upstream ref must be an exact branch or tag ref')
    return value


def _selected_path(value):
    if not isinstance(value, (str, os.PathLike)):
        raise FreshnessError('An explicit absolute directory is required')
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise FreshnessError('Directory must be absolute and contain no parent traversal')
    return path


def _directory(value):
    path = _selected_path(value)
    try:
        path = path.resolve(strict=True)
        if not path.is_dir():
            raise FreshnessError('Selected path is not a directory')
    except FileNotFoundError as error:
        raise FreshnessError('Selected directory is missing') from error
    except (OSError, RuntimeError) as error:
        raise FreshnessError('Selected directory cannot be resolved') from error
    return path


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


@dataclass(frozen=True)
class _Snapshot:
    files: tuple
    directories: tuple
    inventory_sha256: str
    manifest: bytes | None


def _capture(root, object_format):
    """Sample every regular file and directory twice, without following links."""
    _check_cancel()
    if object_format not in ('sha1', 'sha256'):
        raise FreshnessError('Unsupported Git object format')
    flags = os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
    directory_flags = flags | getattr(os, 'O_DIRECTORY', 0)
    if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        raise FreshnessError('Safe no-follow directory capture is unavailable')
    files, directories, entries, total, manifest = [], [], [], 0, None
    with ExitStack() as stack:
        try:
            root_fd = os.open(root, directory_flags)
            stack.callback(os.close, root_fd)
            root_stat = os.fstat(root_fd)
            if not stat.S_ISDIR(root_stat.st_mode):
                raise FreshnessError('Selected package is not a directory')
            directories.append(('', root_fd, None, None, _signature(root_stat)))
            pending = [('', root_fd)]

            def read_file(fd, size, *, keep_manifest):
                content_hash = hashlib.sha256()
                blob_hash = hashlib.new(object_format)
                blob_hash.update(f'blob {size}\0'.encode('ascii'))
                retained = bytearray() if keep_manifest and size <= 65536 else None
                read = 0
                while True:
                    _check_cancel()
                    chunk = os.read(fd, min(65536, size + 1 - read))
                    if not chunk:
                        break
                    read += len(chunk)
                    if read > size:
                        raise FreshnessError('Package file changed during capture')
                    content_hash.update(chunk)
                    blob_hash.update(chunk)
                    if retained is not None:
                        retained.extend(chunk)
                if read != size:
                    raise FreshnessError('Package file changed during capture')
                return content_hash.hexdigest(), blob_hash.hexdigest(), bytes(retained) if retained is not None else None

            while pending:
                _check_cancel()
                relative, directory_fd = pending.pop()
                with os.scandir(directory_fd) as scanned:
                    names = sorted(entry.name for entry in scanned)
                for name in names:
                    _check_cancel()
                    path = f'{relative}/{name}' if relative else name
                    if (not name or name in ('.', '..') or '/' in name or '\x00' in name
                            or len(path.encode('utf-8')) > MAX_PATH_BYTES):
                        raise FreshnessError('Unsafe or oversized package path')
                    info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        if len(directories) >= MAX_DIRS:
                            raise FreshnessError('Package directory limit exceeded')
                        child = os.open(name, directory_flags, dir_fd=directory_fd)
                        stack.callback(os.close, child)
                        if _signature(os.fstat(child)) != _signature(info):
                            raise FreshnessError('Package directory changed during capture')
                        directories.append((path, child, directory_fd, name, _signature(info)))
                        pending.append((path, child))
                    elif stat.S_ISREG(info.st_mode):
                        if len(files) >= MAX_FILES or info.st_size > MAX_FILE_BYTES:
                            raise FreshnessError('Package file limit exceeded')
                        total += info.st_size
                        if total > MAX_TOTAL_BYTES:
                            raise FreshnessError('Package byte limit exceeded')
                        fd = os.open(name, flags | getattr(os, 'O_NONBLOCK', 0), dir_fd=directory_fd)
                        try:
                            if _signature(os.fstat(fd)) != _signature(info):
                                raise FreshnessError('Package file changed during capture')
                            sha, blob, retained = read_file(fd, info.st_size, keep_manifest=path == 'typst.toml')
                            if _signature(os.fstat(fd)) != _signature(info):
                                raise FreshnessError('Package file changed during capture')
                        finally:
                            os.close(fd)
                        if path == 'typst.toml':
                            manifest = retained
                        execute_bits = info.st_mode & 0o111
                        files.append((path, info.st_size, execute_bits, sha, blob))
                        entries.append((directory_fd, name, _signature(info), sha, blob))
                    else:
                        raise FreshnessError('Package contains a symlink or non-regular entry')
            for directory_fd, name, signature, sha, blob in entries:
                _check_cancel()
                info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if _signature(info) != signature:
                    raise FreshnessError('Package file changed after capture')
                fd = os.open(name, flags | getattr(os, 'O_NONBLOCK', 0), dir_fd=directory_fd)
                try:
                    if _signature(os.fstat(fd)) != signature:
                        raise FreshnessError('Package file changed after capture')
                    current_sha, current_blob, _ = read_file(fd, info.st_size, keep_manifest=False)
                    if (_signature(os.fstat(fd)) != signature or current_sha != sha or current_blob != blob):
                        raise FreshnessError('Package file changed after capture')
                finally:
                    os.close(fd)
            for path, fd, parent, name, signature in directories:
                _check_cancel()
                current = os.stat(root, follow_symlinks=False) if parent is None else os.stat(name, dir_fd=parent, follow_symlinks=False)
                if _signature(current) != signature or _signature(os.fstat(fd)) != signature:
                    raise FreshnessError('Package directory changed during capture')
        except (OSError, UnicodeError) as error:
            raise FreshnessError('Package capture refused an unsafe or changing path') from error
    ordered_files = tuple(sorted(files))
    ordered_directories = tuple(sorted(path for path, *_ in directories))
    serialized = json.dumps([ordered_directories, ordered_files], ensure_ascii=True, separators=(',', ':')).encode('ascii')
    return _Snapshot(ordered_files, ordered_directories, hashlib.sha256(serialized).hexdigest(), manifest)


def _git_environment(home):
    return {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'XDG_CONFIG_HOME': str(home),
            'LANG': 'C', 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_SYSTEM': '/dev/null',
            'GIT_TERMINAL_PROMPT': '0', 'GIT_ASKPASS': '/usr/bin/false',
            'GIT_OPTIONAL_LOCKS': '0', 'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_NO_LAZY_FETCH': '1',
            'GIT_LITERAL_PATHSPECS': '1'}


def _git(cwd, arguments, *, network=False):
    """Bound output/time, avoid shell/config credentials and kill owned children."""
    _check_cancel()
    binary = shutil.which('git', path='/usr/bin:/bin')
    if binary is None:
        raise FreshnessError('System Git is unavailable')
    with tempfile.TemporaryDirectory(prefix='typst-freshness-env-') as home:
        environment = _git_environment(home)
        if network:
            # A fresh cwd has no repository-local URL rewrites or credentials.
            cwd = home
            environment['GIT_CEILING_DIRECTORIES'] = str(Path(home).parent)
        command = [binary, '--no-optional-locks', '--no-lazy-fetch', '-c', 'core.fsmonitor=false',
                   '-c', 'core.untrackedCache=false', '-c', 'core.hooksPath=/dev/null',
                   '-c', 'credential.helper=', '-c', 'http.extraheader=',
                   '-c', 'http.followRedirects=false', '-c', 'protocol.allow=never',
                   '-c', 'protocol.https.allow=always', *arguments]
        try:
            process = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        except OSError as error:
            raise FreshnessError('Git could not start') from error
        outputs = {process.stdout: bytearray(), process.stderr: bytearray()}
        try:
            with selectors.DefaultSelector() as selector:
                for stream in outputs:
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ)
                deadline = time.monotonic() + GIT_TIMEOUT
                while selector.get_map():
                    _check_cancel()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise FreshnessError('Git operation timed out')
                    for key, _ in selector.select(min(remaining, 0.25)):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            outputs[key.fileobj].extend(chunk)
                            if len(outputs[key.fileobj]) > MAX_GIT_OUTPUT:
                                raise FreshnessError('Git output limit exceeded')
                remaining = deadline - time.monotonic()
                _check_cancel()
                if remaining <= 0:
                    raise FreshnessError('Git operation timed out')
                try:
                    code = process.wait(timeout=remaining)
                except subprocess.TimeoutExpired as error:
                    raise FreshnessError('Git operation timed out') from error
            return code, bytes(outputs[process.stdout]), bytes(outputs[process.stderr])
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
        finally:
            process.stdout.close()
            process.stderr.close()


def _output(cwd, *arguments):
    code, output, _ = _checked_call(_git, cwd, list(arguments))
    if code:
        raise FreshnessError('Required local Git metadata is unavailable')
    return output.decode('utf-8').strip()


def _require_local_complete_store(root):
    """Refuse partial/promisor/alternate object stores before reading objects."""
    # Read effective repository and worktree config; system/global config are
    # already disabled in the process environment. A worktree-specific
    # promisor must not escape the guard.
    code, _, _ = _checked_call(_git, root, ['config', '--includes', '--get-regexp',
                                           r'^(extensions\.partialclone|remote\..*\.promisor|remote\..*\.partialclonefilter)$'])
    if code == 0:
        raise FreshnessError('Partial or promisor Git object store is not inspected')
    if code != 1:
        raise FreshnessError('Git object-store configuration is unavailable')
    def git_path(name):
        path = Path(_checked_call(_output, root, 'rev-parse', '--git-path', name))
        return path if path.is_absolute() else root / path
    if os.path.lexists(git_path('objects/info/alternates')):
        raise FreshnessError('Git alternate object store is not inspected')
    pack = git_path('objects/pack')
    if pack.is_symlink():
        raise FreshnessError('Symlinked Git pack directory is not inspected')
    try:
        with os.scandir(pack) as entries:
            for count, entry in enumerate(entries, 1):
                _check_cancel()
                if count > 10000:
                    raise FreshnessError('Git pack inventory exceeds its limit')
                if entry.name.endswith('.promisor'):
                    raise FreshnessError('Partial or promisor Git object store is not inspected')
    except FileNotFoundError:
        pass
    except OSError as error:
        raise FreshnessError('Git pack inventory is unavailable') from error


def _remote(url, ref):
    targets = [ref, ref + '^{}'] if ref.startswith('refs/tags/') else [ref]
    code, output, _ = _checked_call(_git, None, ['ls-remote', '--exit-code', '--', url, *targets], network=True)
    if code:
        raise FreshnessError('Upstream ref could not be read without credentials')
    refs = {}
    try:
        for line in output.decode('ascii').splitlines():
            _check_cancel()
            oid, name = line.split('\t')
            if not _OID.fullmatch(oid) or name not in targets or name in refs:
                raise FreshnessError('Upstream returned ambiguous ref data')
            refs[name] = oid
    except (UnicodeError, ValueError) as error:
        raise FreshnessError('Upstream returned malformed ref data') from error
    if ref not in refs:
        raise FreshnessError('Requested upstream ref was not found')
    return refs.get(ref + '^{}', refs[ref]), refs[ref]


def _tree(root, commit, relative):
    code, data, _ = _checked_call(_git, root, ['ls-tree', '-r', '-z', '--full-tree', commit, '--', relative])
    if code:
        raise FreshnessError('Commit tree is unavailable locally')
    expected = {}
    try:
        for entry in data.split(b'\0'):
            _check_cancel()
            if not entry:
                continue
            metadata, raw_path = entry.split(b'\t', 1)
            mode, kind, oid = metadata.decode('ascii').split(' ')
            path = raw_path.decode('utf-8')
            prefix = relative + '/'
            if (not path.startswith(prefix) or mode not in ('100644', '100755')
                    or kind != 'blob' or not _OID.fullmatch(oid)):
                raise FreshnessError('Commit package tree has unsupported entries')
            name = path[len(prefix):]
            if not name or name in expected:
                raise FreshnessError('Commit package tree is ambiguous')
            expected[name] = (0o111 if mode == '100755' else 0, oid)
    except (ValueError, UnicodeError) as error:
        raise FreshnessError('Commit package tree is malformed') from error
    if not expected:
        raise FreshnessError('Package is absent from the selected commit')
    return expected


def _index_tree(root, relative):
    """Read index entries only; do not refresh or consult worktree filters."""
    code, data, _ = _checked_call(_git, root, ['ls-files', '--stage', '-z', '--', relative])
    if code:
        raise FreshnessError('Package index is unavailable')
    expected = {}
    try:
        for entry in data.split(b'\0'):
            _check_cancel()
            if not entry:
                continue
            metadata, raw_path = entry.split(b'\t', 1)
            mode, oid, stage = metadata.decode('ascii').split(' ')
            path = raw_path.decode('utf-8')
            prefix = relative + '/'
            if not path.startswith(prefix) or not _OID.fullmatch(oid):
                raise FreshnessError('Package index is malformed')
            if stage != '0':
                return None
            if mode not in ('100644', '100755'):
                raise FreshnessError('Package index has unsupported entries')
            name = path[len(prefix):]
            if not name or name in expected:
                raise FreshnessError('Package index is ambiguous')
            expected[name] = (0o111 if mode == '100755' else 0, oid)
    except (ValueError, UnicodeError) as error:
        raise FreshnessError('Package index is malformed') from error
    return expected


def _manifest(snapshot):
    if snapshot.manifest is None:
        return None
    try:
        parsed = tomllib.loads(snapshot.manifest.decode('utf-8'))
        package = parsed['package']
        name, version = package['name'], package['version']
        if type(name) is not str or type(version) is not str:
            return None
        return {'name': name, 'version': version}
    except (UnicodeError, ValueError, KeyError, TypeError):
        return None


def _inspect_freshness_many(checkout_root, package_pairs, upstream_url, upstream_ref, *, online=False):
    """Compare explicit package pairs against one explicitly selected ref.

    Each pair is (checkout package directory, installed package directory).
    One remote ref query is shared by the entire bounded batch. Paths and the
    upstream are caller assertions, not discovered runtime selection. A remote
    commit missing locally is never fetched or guessed. Directory captures are
    sampled evidence rather than a filesystem lease.
    """
    if type(online) is not bool:
        raise FreshnessError('Online selection must be Boolean')
    if (type(package_pairs) not in (list, tuple) or not 1 <= len(package_pairs) <= MAX_PACKAGE_PAIRS
            or any(type(pair) not in (list, tuple) or len(pair) != 2 for pair in package_pairs)):
        raise FreshnessError('Supply a bounded list of checkout and installed package path pairs')
    root = _checked_call(_directory, checkout_root)
    pairs = []
    for pair in package_pairs:
        _check_cancel()
        pairs.append(tuple(map(_selected_path, pair)))
    url, ref = _github_url(upstream_url), _ref(upstream_ref)
    result = {'schema': 1, 'checked_at_utc': datetime.now(timezone.utc).isoformat(),
              'upstream': {'url': url, 'ref': ref, 'online_requested': online, 'network_attempted': False,
                           'observed_at_utc': None, 'observed_oid': None,
                           'commit': None, 'ref_object': None, 'reason': None},
              'checkout': {'root': str(root), 'is_git': None, 'metadata_complete': False,
                           'no_lazy_fetch_supported': None, 'object_format': None,
                           'head': None, 'branch': None, 'dirty': None,
                           'dirty_reason': 'global_worktree_not_inspected_to_avoid_git_filters',
                           'relation': 'unknown', 'reason': None},
              'packages': [],
              'limitations': ['Live files can change after the capture; no filesystem lease is held.',
                              'Package directories are sampled sequentially, not captured atomically.',
                              'The selected installation and runtime dependency closure are caller assertions.',
                              'Git clean filters and checkout-wide worktree status are not evaluated.',
                              'A remote commit missing locally cannot be classified without fetching.',
                              'The selected GitHub ref does not establish latest versions in the Typst preview registry.',
                              'Git tree comparison covers regular files and executable modes; Git does not track empty directories.',
                              'Selected root symlinks are resolved; links inside captured packages are refused.']}
    object_format = 'sha1'
    try:
        capability_code, _, capability_error = _checked_call(_git, root, ['--version'])
        if capability_code:
            if b'unknown option: --no-lazy-fetch' in capability_error:
                result['checkout']['no_lazy_fetch_supported'] = False
            raise FreshnessError('Git no-lazy-fetch support is unavailable or could not be checked')
        result['checkout']['no_lazy_fetch_supported'] = True
        code, top_bytes, git_error = _checked_call(_git, root, ['rev-parse', '--show-toplevel'])
        if code:
            if b'not a git repository' in git_error:
                result['checkout']['is_git'] = False
            raise FreshnessError('Git checkout is absent or inaccessible')
        result['checkout']['is_git'] = True
        top = Path(top_bytes.decode('utf-8').strip()).resolve(strict=True)
        if top != root:
            raise FreshnessError('Checkout root is not the Git top-level directory')
        _checked_call(_require_local_complete_store, root)
        object_format = _checked_call(_output, root, 'rev-parse', '--show-object-format')
        if object_format not in ('sha1', 'sha256'):
            raise FreshnessError('Unsupported Git object format')
        result['checkout']['object_format'] = object_format
        head = _checked_call(_output, root, 'rev-parse', '--verify', 'HEAD^{commit}')
        if not _OID.fullmatch(head):
            raise FreshnessError('Checkout HEAD is invalid')
        code, branch, _ = _checked_call(_git, root, ['symbolic-ref', '--quiet', '--short', 'HEAD'])
        if code not in (0, 1):
            raise FreshnessError('Checkout branch is unavailable')
        result['checkout'].update(metadata_complete=True, head=head,
            branch=branch.decode('utf-8').strip() if code == 0 else None)
    except (FreshnessError, OSError, UnicodeError, ValueError) as error:
        result['checkout']['reason'] = str(error)
    target = None
    if online and result['checkout']['metadata_complete']:
        _check_cancel()
        result['upstream']['network_attempted'] = None
        try:
            target, object_id = _checked_call(_remote, url, ref)
            result['upstream'].update(network_attempted=True, observed_oid=target, ref_object=object_id,
                                      observed_at_utc=datetime.now(timezone.utc).isoformat())
        except (FreshnessError, OSError, UnicodeError, ValueError) as error:
            result['upstream']['reason'] = str(error)
            target = None
        try:
            if target is None:
                raise FreshnessError('Upstream ref was not resolved')
            if len(target) != len(head):
                raise FreshnessError('Upstream and checkout object formats differ')
            code, commit_bytes, _ = _checked_call(_git, root, ['rev-parse', '--verify', target + '^{commit}'])
            if code:
                result['checkout']['reason'] = 'upstream_oid_unverified_locally'
                target = None
            else:
                target = commit_bytes.decode('ascii').strip()
                if not _OID.fullmatch(target) or len(target) != len(head):
                    raise FreshnessError('Locally resolved upstream commit is invalid')
                result['upstream']['commit'] = target
                if target == head:
                    result['checkout']['relation'] = 'equal'
                else:
                    grafts = Path(_checked_call(_output, root, 'rev-parse', '--git-path', 'info/grafts'))
                    if not grafts.is_absolute():
                        grafts = root / grafts
                    shallow = _checked_call(_output, root, 'rev-parse', '--is-shallow-repository')
                    if grafts.exists():
                        result['checkout']['reason'] = 'grafted_history_unverified'
                    else:
                        behind, _, _ = _checked_call(_git, root, ['merge-base', '--is-ancestor', head, target])
                        ahead, _, _ = _checked_call(_git, root, ['merge-base', '--is-ancestor', target, head])
                        if behind == 0 and ahead == 1:
                            result['checkout']['relation'] = 'behind'
                        elif ahead == 0 and behind == 1:
                            result['checkout']['relation'] = 'ahead'
                        elif behind == ahead == 1 and shallow == 'false':
                            result['checkout']['relation'] = 'diverged'
                        else:
                            result['checkout']['reason'] = 'history_incomplete_or_ambiguous'
        except (FreshnessError, OSError, UnicodeError, ValueError) as error:
            if result['upstream']['network_attempted']:
                result['checkout']['reason'] = str(error)
            target = None
    elif not online:
        result['upstream']['reason'] = 'offline_not_checked'
    else:
        result['upstream']['reason'] = 'checkout_git_metadata_unavailable'
    snapshots = {}
    index_checks = []
    aggregate_bytes = 0
    budget_exceeded = False
    for package_input, installed_input in pairs:
        _check_cancel()
        record = {'checkout_package_path': str(package_input), 'installed_path': str(installed_input),
                  'resolved_checkout_package_path': None, 'resolved_installed_path': None,
                  'checkout_path_state': 'unknown', 'installed_path_state': 'unknown',
                  'selection': 'caller-supplied', 'runtime_selection_proved': False,
                  'snapshot_object_format': object_format,
                  'package_dirty': None, 'package_dirty_reason': 'git_worktree_filters_not_evaluated',
                  'checkout_raw_matches_head_tree': None, 'index_matches_head_tree': None,
                  'matches_checkout_snapshot': None, 'matches_upstream_tree': None,
                  'checkout_snapshot_sha256': None, 'installed_snapshot_sha256': None,
                  'checkout_manifest': None, 'installed_manifest': None, 'reason': None}
        result['packages'].append(record)
        if budget_exceeded:
            record['reason'] = 'package_batch_byte_limit_exceeded'
            continue
        try:
            resolved = []
            for label, path in (('checkout', package_input), ('installed', installed_input)):
                _check_cancel()
                try:
                    value = _checked_call(_directory, path)
                except FreshnessError as error:
                    record[label + '_path_state'] = 'missing' if str(error) == 'Selected directory is missing' else 'unavailable'
                    record['reason'] = f'{label}_package_directory_{record[label + "_path_state"]}'
                    resolved.append(None)
                else:
                    record[label + '_path_state'] = 'available'
                    record['resolved_checkout_package_path' if label == 'checkout' else 'resolved_installed_path'] = str(value)
                    resolved.append(value)
            if any(value is None for value in resolved):
                continue
            package, installed = resolved
            if not result['checkout']['metadata_complete']:
                relative = None
                record['reason'] = 'checkout_git_metadata_unavailable'
            else:
                try:
                    relative = package.relative_to(root).as_posix()
                    if relative == '.':
                        raise ValueError('Package must be a directory inside the checkout')
                except ValueError:
                    relative = None
                    record['reason'] = 'checkout_package_not_inside_checkout'
            for path in (package, installed):
                _check_cancel()
                if path not in snapshots:
                    snapshot = _checked_call(_capture, path, object_format)
                    size = sum(item[1] for item in snapshot.files)
                    if aggregate_bytes + size > MAX_BATCH_BYTES:
                        budget_exceeded = True
                        raise FreshnessError('Package batch byte limit exceeded')
                    aggregate_bytes += size
                    snapshots[path] = snapshot
            first, second = snapshots[package], snapshots[installed]
            record.update(matches_checkout_snapshot=(first.files == second.files and first.directories == second.directories),
                checkout_snapshot_sha256=first.inventory_sha256, installed_snapshot_sha256=second.inventory_sha256,
                checkout_manifest=_manifest(first), installed_manifest=_manifest(second))
            if relative is not None:
                try:
                    head_tree = _checked_call(_tree, root, head, relative)
                    index_tree = _checked_call(_index_tree, root, relative)
                    checkout_raw = {path: (execute_bits, blob) for path, _, execute_bits, _, blob in first.files}
                    record['checkout_raw_matches_head_tree'] = checkout_raw == head_tree
                    record['index_matches_head_tree'] = index_tree == head_tree
                    index_checks.append((relative, index_tree, record))
                except FreshnessError as error:
                    record['package_dirty_reason'] = str(error)
            if target is not None and relative is not None:
                expected = _checked_call(_tree, root, target, relative)
                observed = {path: (execute_bits, blob) for path, _, execute_bits, _, blob in second.files}
                record['matches_upstream_tree'] = observed == expected
        except (FreshnessError, OSError, UnicodeError, ValueError) as error:
            record['reason'] = str(error)
    for relative, before, record in index_checks:
        _check_cancel()
        try:
            if _checked_call(_index_tree, root, relative) != before:
                raise FreshnessError('Package index changed during the check')
        except (FreshnessError, OSError, UnicodeError, ValueError):
            record['index_matches_head_tree'] = None
            record['package_dirty_reason'] = 'index_changed_or_recheck_failed'
    if result['checkout']['metadata_complete']:
        try:
            later_head = _checked_call(_output, root, 'rev-parse', '--verify', 'HEAD^{commit}')
            branch_code, branch_bytes, _ = _checked_call(_git, root, ['symbolic-ref', '--quiet', '--short', 'HEAD'])
            later_branch = branch_bytes.decode('utf-8').strip() if branch_code == 0 else None
            if (branch_code not in (0, 1) or later_head != head
                    or later_branch != result['checkout']['branch']):
                raise FreshnessError('Checkout changed during the check')
        except (FreshnessError, OSError, UnicodeError, ValueError):
            result['checkout'].update(metadata_complete=False, relation='unknown', dirty=None,
                                      reason='checkout_changed_or_recheck_failed')
    return result


def inspect_freshness_many(checkout_root, package_pairs, upstream_url, upstream_ref, *, online=False,
                           cancel_event=None):
    """Inspect explicit packages, cooperatively cancelling owned work when requested."""
    if cancel_event is not None and type(cancel_event) is not threading.Event:
        raise FreshnessError('Cancellation requires a threading.Event')
    token = _CANCEL_EVENT.set(cancel_event)
    try:
        _check_cancel()
        result = _inspect_freshness_many(checkout_root, package_pairs, upstream_url, upstream_ref,
                                         online=online)
        _check_cancel()
        return result
    finally:
        _CANCEL_EVENT.reset(token)


def inspect_freshness(checkout_root, checkout_package_path, installed_package_path,
                      upstream_url, upstream_ref, *, online=False, cancel_event=None):
    """Single-package convenience wrapper; output keeps the batch schema."""
    return inspect_freshness_many(checkout_root, [(checkout_package_path, installed_package_path)],
                                  upstream_url, upstream_ref, online=online, cancel_event=cancel_event)
