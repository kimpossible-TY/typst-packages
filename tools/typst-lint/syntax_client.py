"""Bounded client for the locally built, pinned, syntax-only official parser."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import selectors
import stat
import subprocess
import time

from typst_lint import InputError, digest

MAX_INPUT = 1_048_576
MAX_OUTPUT = 16_777_216
MAX_NODES = 100_000
MAX_DEPTH = 256
EXECUTABLE = Path(__file__).resolve().parent / 'syntax-adapter/target/debug/typst-lint-syntax'


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


@dataclass(frozen=True)
class Tree:
    source: bytes
    mode: str
    nodes: tuple[Node, ...]


def demand(condition, message):
    if not condition:
        raise AdapterError(message)


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
    demand(type(payload.get('schema')) is int and payload['schema'] == 1
           and payload.get('adapter_version') == '0.1.0'
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
        demand(type(record) is dict and set(record) == {'kind', 'start', 'end', 'children', 'errors', 'operands'}, 'Invalid syntax node fields')
        kind, start, end = record['kind'], record['start'], record['end']
        demand(type(kind) is str and kind.isascii() and kind.isidentifier() and len(kind) < 80, 'Invalid SyntaxKind')
        demand(type(start) is int and type(end) is int and start in boundaries and end in boundaries and start <= end, 'Invalid UTF-8 syntax span')
        children = record['children']
        demand(type(children) is list and all(type(i) is int and 0 <= i < len(records) for i in children), 'Invalid syntax child')
        demand(type(record['errors']) is bool and type(record['operands']) is list, 'Invalid syntax flags/operands')
        operands = []
        for operand in record['operands']:
            demand(type(operand) is dict and set(operand) == {'role', 'node'}
                   and operand['role'] in ('top', 'bottom') and type(operand['node']) is int
                   and operand['node'] in children and kind == 'MathAttach', 'Invalid attachment operand')
            operands.append((operand['role'], operand['node']))
        demand(len({r for r, _ in operands}) == len(operands), 'Duplicate attachment operand role')
        nodes.append(Node(kind, start, end, tuple(children), record['errors'], tuple(operands)))
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
        info = path.stat()
        # Cargo may hardlink this read-only executable to its deps artifact.
        # Source-write hardlink restrictions do not apply to build artifacts.
        demand(stat.S_ISREG(info.st_mode) and os.access(path, os.X_OK), 'Syntax adapter is not a regular executable')
        raw = _run(source, mode, path, timeout)
        demand(len(raw) <= MAX_OUTPUT, 'Syntax adapter output limit exceeded')
        return validate(json.loads(raw), source, mode)
    except AdapterError:
        raise
    except (OSError, ValueError, subprocess.TimeoutExpired, RecursionError) as error:
        raise AdapterError(f'Syntax adapter unavailable or invalid: {error}') from error
