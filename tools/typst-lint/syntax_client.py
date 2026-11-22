"""Bounded client for the locally built, pinned, syntax-only official parser."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import selectors
import stat
import subprocess
import time
import threading

from typst_lint import InputError, digest

MAX_INPUT = 1_048_576
MAX_OUTPUT = 16_777_216
MAX_NODES = 100_000
MAX_DEPTH = 256
_TOOL_ROOT = Path(__file__).resolve().parent
_ADAPTER_BASE = _TOOL_ROOT / 'syntax-adapter/target'
RELEASE_EXECUTABLE = _ADAPTER_BASE / 'release/typst-lint-syntax'
DEBUG_EXECUTABLE = _ADAPTER_BASE / 'debug/typst-lint-syntax'


def _release_present():
    """Treat invalid candidate ancestors and access errors as present, not absent."""
    for directory in (_ADAPTER_BASE.parent, _ADAPTER_BASE, RELEASE_EXECUTABLE.parent):
        try:
            info = directory.stat(follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError:
            return True
        if not stat.S_ISDIR(info.st_mode):
            return True
    try:
        RELEASE_EXECUTABLE.stat(follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return True


# Choose once per process. A present but invalid release path is never skipped.
EXECUTABLE = RELEASE_EXECUTABLE if _release_present() else DEBUG_EXECUTABLE
MAX_ADAPTER_BYTES = 64 * 1024 * 1024
_adapter_lock = threading.Lock()
_adapter_pin = None


class AdapterError(InputError):
    pass


@dataclass(frozen=True)
class Node:
    kind: str
    start: int
    end: int
    children: tuple[int, ...]
    errors: bool
    operands: tuple[tuple[str, int], ...]
    display: bool | None


@dataclass(frozen=True)
class Tree:
    source: bytes
    mode: str
    nodes: tuple[Node, ...]


def demand(condition, message):
    if not condition:
        raise AdapterError(message)


def _file_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _directory_identities(path):
    """Capture the fixed in-checkout directory chain without following links."""
    values = []
    flags = os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_DIRECTORY', 0)
    for directory in (_TOOL_ROOT, _ADAPTER_BASE.parent, _ADAPTER_BASE, path.parent):
        before = directory.stat(follow_symlinks=False)
        demand(stat.S_ISDIR(before.st_mode) and directory.resolve() == directory,
               'Syntax adapter directory is unsafe')
        descriptor = os.open(directory, flags)
        try:
            demand(_file_identity(os.fstat(descriptor)) == _file_identity(before),
                   'Syntax adapter directory changed during capture')
            demand(_file_identity(directory.stat(follow_symlinks=False)) == _file_identity(before)
                   and directory.resolve() == directory, 'Syntax adapter directory path changed during capture')
        finally:
            os.close(descriptor)
        values.append((str(directory.relative_to(_TOOL_ROOT)), _file_identity(before)))
    return tuple(values)


def _capture_adapter(path, *, require_executable=True):
    """Read one fixed candidate without following symlinks."""
    path = Path(path)
    demand(path.is_absolute() and path in (DEBUG_EXECUTABLE, RELEASE_EXECUTABLE) and path.resolve() == path,
           'Only fixed local syntax adapter paths are allowed')
    directories = _directory_identities(path)
    before = path.stat(follow_symlinks=False)
    demand(stat.S_ISREG(before.st_mode) and (not require_executable or os.access(path, os.X_OK)),
           'Syntax adapter is not a regular executable')
    demand(0 < before.st_size <= MAX_ADAPTER_BYTES, 'Syntax adapter size exceeds limit')
    flags = os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        demand(_file_identity(opened) == _file_identity(before), 'Syntax adapter changed during capture')
        hasher, total = hashlib.sha256(), 0
        while True:
            chunk = os.read(descriptor, 128 * 1024)
            if not chunk:
                break
            total += len(chunk)
            demand(total <= MAX_ADAPTER_BYTES, 'Syntax adapter size exceeds limit')
            hasher.update(chunk)
        demand(total == before.st_size and _file_identity(os.fstat(descriptor)) == _file_identity(before),
               'Syntax adapter changed during capture')
        demand(_file_identity(path.stat(follow_symlinks=False)) == _file_identity(before)
               and path.resolve() == path, 'Syntax adapter path changed during capture')
        demand(_directory_identities(path) == directories, 'Syntax adapter directory changed during capture')
        return (str(path), _file_identity(before), hasher.hexdigest(), directories)
    finally:
        os.close(descriptor)


def adapter_identity(path=EXECUTABLE):
    """Read the selected adapter without following symlinks."""
    demand(Path(path) == EXECUTABLE, 'Only the selected local syntax adapter is allowed')
    return _capture_adapter(path)


def _selection():
    return RELEASE_EXECUTABLE if _release_present() else DEBUG_EXECUTABLE


def _entry_present(path):
    try:
        path.stat(follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _profile_directory_identities():
    """Retain both profile directory states, including an empty release directory."""
    values = []
    for directory in (DEBUG_EXECUTABLE.parent, RELEASE_EXECUTABLE.parent):
        try:
            info = directory.stat(follow_symlinks=False)
        except FileNotFoundError:
            values.append((directory.name, None))
        else:
            values.append((directory.name, _file_identity(info)))
    return tuple(values)


def _pin_adapter():
    """Reject a changed binary within this process; never trust protocol labels as a hash."""
    global _adapter_pin
    with _adapter_lock:
        demand(_selection() == EXECUTABLE, 'Syntax adapter selection changed during this process')
        profiles = _profile_directory_identities()
        current = (*adapter_identity(), profiles)
        demand(_profile_directory_identities() == profiles,
               'Syntax adapter profile directories changed during capture')
        if _adapter_pin is None:
            _adapter_pin = current
        demand(current == _adapter_pin, 'Syntax adapter changed during this process')
        return current


def audit_adapter_inventory():
    """Capture selected bytes and both fixed candidate states for audit drift checks."""
    demand(_selection() == EXECUTABLE, 'Syntax adapter selection changed during audit')
    selected = _pin_adapter() if (_release_present() if EXECUTABLE == RELEASE_EXECUTABLE
                                  else _entry_present(EXECUTABLE)) else None
    candidates = {}
    for name, path in (('debug', DEBUG_EXECUTABLE), ('release', RELEASE_EXECUTABLE)):
        if not _entry_present(path):
            candidates[name] = None
            continue
        info = path.stat(follow_symlinks=False)
        if stat.S_ISREG(info.st_mode) and path.resolve() == path:
            if path != EXECUTABLE and not (0 < info.st_size <= MAX_ADAPTER_BYTES):
                identity, sha256 = _file_identity(info), None
            else:
                try:
                    _, identity, sha256, _ = _capture_adapter(path, require_executable=(path == EXECUTABLE))
                except PermissionError:
                    if path == EXECUTABLE:
                        raise
                    identity, sha256 = _file_identity(info), None
            candidates[name] = {'identity': identity, 'sha256': sha256, 'kind': 'regular'}
        else:
            candidates[name] = {'identity': _file_identity(info), 'sha256': None, 'kind': 'other'}
    demand(_selection() == EXECUTABLE, 'Syntax adapter selection changed during audit')
    if selected is not None:
        demand(_pin_adapter() == selected, 'Syntax adapter changed during audit')
    return {'path': EXECUTABLE.relative_to(_ADAPTER_BASE.parent.parent).as_posix(),
            'sha256': selected[2] if selected is not None else None,
            'candidates': candidates, 'directories': selected[3] if selected is not None else None,
            'profile_directories': selected[4] if selected is not None else None}


def _run(source, mode, executable, timeout):
    """Drain pipes with bounded memory and deadline; never use a shell or PATH."""
    deadline = time.monotonic() + timeout
    with subprocess.Popen([str(executable), mode], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        with selectors.DefaultSelector() as selector:
            for stream in (process.stdin, process.stdout, process.stderr):
                os.set_blocking(stream.fileno(), False)
            if source:
                selector.register(process.stdin, selectors.EVENT_WRITE, 'in')
            else:
                process.stdin.close()
            selector.register(process.stdout, selectors.EVENT_READ, 'out')
            selector.register(process.stderr, selectors.EVENT_READ, 'err')
            sent = 0
            output, error = bytearray(), bytearray()
            try:
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    demand(remaining > 0, 'Syntax adapter timeout')
                    for key, _ in selector.select(min(remaining, 0.1)):
                        stream = key.fileobj
                        if key.data == 'in':
                            try:
                                sent += os.write(stream.fileno(), source[sent:sent+8192])
                            except BrokenPipeError:
                                sent = len(source)
                            if sent == len(source):
                                selector.unregister(stream)
                                stream.close()
                        else:
                            chunk = os.read(stream.fileno(), 65536)
                            if not chunk:
                                selector.unregister(stream)
                                continue
                            target = output if key.data == 'out' else error
                            target.extend(chunk)
                            demand(len(target) <= (MAX_OUTPUT if key.data == 'out' else 65536),
                                   'Syntax adapter output limit exceeded')
                remaining = deadline - time.monotonic()
                demand(remaining > 0, 'Syntax adapter timeout')
                status = process.wait(timeout=remaining)
                demand(status == 0, f'Syntax adapter failed ({status}): {error.decode("utf-8", errors="replace")[:1000]}')
                demand(not error, 'Unexpected syntax adapter stderr')
                return bytes(output)
            except (BaseException,):
                process.kill()
                process.wait()
                raise


def validate(payload, source, mode):
    """Validate all tree links/spans before any rule consumes adapter data."""
    demand(type(payload) is dict, 'Invalid syntax protocol object')
    demand(type(payload.get('schema')) is int and payload['schema'] == 2
           and payload.get('adapter_version') == '0.2.0'
           and payload.get('syntax_version') == '0.15.1', 'Syntax adapter version/schema mismatch')
    demand(payload.get('mode') == mode and type(payload.get('source_bytes')) is int
           and payload['source_bytes'] == len(source)
           and payload.get('source_sha256') == digest(source), 'Syntax source hash/length/mode mismatch')
    records = payload.get('nodes')
    demand(type(records) is list and 0 < len(records) <= MAX_NODES, 'Invalid syntax node count')
    boundaries = {0}
    offset = 0
    for char in source.decode('utf-8'):
        offset += len(char.encode('utf-8'))
        boundaries.add(offset)
    nodes = []
    for record in records:
        demand(type(record) is dict and set(record) == {'kind', 'start', 'end', 'children', 'errors', 'operands', 'display'}, 'Invalid syntax node fields')
        kind, start, end = record['kind'], record['start'], record['end']
        demand(type(kind) is str and kind.isascii() and kind.isidentifier() and len(kind) < 80, 'Invalid SyntaxKind')
        demand(type(start) is int and type(end) is int and start in boundaries and end in boundaries and start <= end, 'Invalid UTF-8 syntax span')
        children = record['children']
        demand(type(children) is list and all(type(i) is int and 0 <= i < len(records) for i in children), 'Invalid syntax child')
        demand(type(record['errors']) is bool and type(record['operands']) is list, 'Invalid syntax flags/operands')
        demand(type(record['display']) is bool if kind == 'Equation' else record['display'] is None,
               'Invalid typed equation display flag')
        operands = []
        for operand in record['operands']:
            demand(type(operand) is dict and set(operand) == {'role', 'node'}
                   and operand['role'] in ('top', 'bottom') and type(operand['node']) is int
                   and operand['node'] in children and kind == 'MathAttach', 'Invalid attachment operand')
            operands.append((operand['role'], operand['node']))
        demand(len({r for r, _ in operands}) == len(operands), 'Duplicate attachment operand role')
        nodes.append(Node(kind, start, end, tuple(children), record['errors'], tuple(operands), record['display']))
    demand(nodes[0].kind == {'markup':'Markup', 'code':'Code', 'math':'Math'}[mode]
           and nodes[0].start == 0 and nodes[0].end == len(source), 'Invalid syntax root')
    seen = set()
    stack = [(0, 0)]
    while stack:
        index, depth = stack.pop()
        demand(index not in seen and depth <= MAX_DEPTH, 'Cyclic/shared/deep syntax tree')
        seen.add(index)
        node = nodes[index]
        cursor = node.start
        for child_id in node.children:
            demand(child_id > index, 'Syntax tree is not preorder')
            child = nodes[child_id]
            demand(child.start == cursor and child.end <= node.end, 'Syntax children do not partition parent')
            demand(not child.errors or node.errors, 'Inconsistent syntax error flags')
            cursor = child.end
            stack.append((child_id, depth+1))
        if node.children:
            demand(cursor == node.end, 'Syntax children leave uncovered bytes')
        if node.kind == 'Error':
            demand(node.errors, 'Error node without error flag')
    demand(len(seen) == len(nodes), 'Unreachable syntax nodes')
    return Tree(source, mode, tuple(nodes))


def parse(source: bytes, mode='markup', *, executable=EXECUTABLE, timeout=5.0):
    demand(type(source) is bytes and len(source) <= MAX_INPUT, 'Syntax input must be immutable bytes of at most 1 MiB')
    demand(mode in ('markup', 'code', 'math'), 'Unsupported syntax mode')
    demand(type(timeout) in (int, float) and 0 < timeout <= 30, 'Invalid syntax timeout')
    try:
        source.decode('utf-8')
        path = Path(executable)
        demand(path.is_absolute() and path == EXECUTABLE and path.resolve() == EXECUTABLE,
               'Only the known absolute local syntax adapter is allowed')
        _pin_adapter()
        try:
            raw = _run(source, mode, path, timeout)
        finally:
            _pin_adapter()
        demand(len(raw) <= MAX_OUTPUT, 'Syntax adapter output limit exceeded')
        return validate(json.loads(raw), source, mode)
    except AdapterError:
        raise
    except (OSError, ValueError, subprocess.TimeoutExpired, RecursionError) as error:
        raise AdapterError(f'Syntax adapter unavailable or invalid: {error}') from error
