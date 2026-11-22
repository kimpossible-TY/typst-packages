"""Owned subprocess harness for the real explicit compile/output CLI.

No corresponding CLI flag or environment variable exists. Only private trusted
compiler review and existing transaction/publication fault seams are patched.
All programs, marker files and mutations belong to temporary test consumers.
"""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import captured_relative_fix
import captured_relative_inputs
import closed_core_compile as compiler
import closed_core_publish as publication
import pdf_publication_store as output_store
import protected_fix
import transactions
import typst_lint


def make_compiler(base, request):
    path = base / 'owned compile fixture'
    program = '''import os, pathlib, signal, sys, time
if sys.argv[1:] == ['--version']:
    if MODE == 'probe-exit':
        raise SystemExit(23)
    if MODE == 'probe-signal':
        os.kill(os.getpid(), signal.SIGTERM)
    os.write(1, VERSION)
    raise SystemExit(0)
assert sys.argv[1] == 'compile'
source = pathlib.Path(sys.argv[-2])
output = pathlib.Path(sys.argv[-1])
deps = pathlib.Path(sys.argv[sys.argv.index('--deps') + 1])
pathlib.Path(MARKER).write_text(str(os.getpid()))
if MODE == 'nonzero' or MODE == 'nonzero-drift':
    os.write(1, b'owned stdout\\x00\\xff')
    os.write(2, b'owned stderr\\xfe\\n')
    raise SystemExit(19)
if MODE.startswith('signal-'):
    number = int(MODE.split('-')[1])
    if number != signal.SIGKILL:
        signal.signal(number, signal.SIG_DFL)
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {number})
    os.kill(os.getpid(), number)
if MODE == 'stream':
    while True:
        os.write(1, b'x' * 8192)
if MODE in ('hang', 'timeout'):
    while True:
        time.sleep(.02)
prefix = b'%PDF-1.7\\n%retained-source=' + source.read_bytes().hex().encode() + b'\\n'
pdf = prefix + b'xref\\n0 1\\n0000000000 65535 f \\ntrailer\\n<< /Size 1 >>\\nstartxref\\n' + str(len(prefix)).encode() + b'\\n%%EOF\\n'
output.write_bytes(pdf)
deps.write_bytes(b''.join(os.fsencode(path) + b'\\0' for path in DEPENDENCIES) if DEPENDENCIES is not None else os.fsencode(source.relative_to(pathlib.Path.cwd())) + b'\\0')
'''
    raw = (f'#!{sys.executable}\nMODE = {request.get("compiler_mode", "success")!r}\n'
           f'VERSION = {compiler._VERSION!r}\nMARKER = {request["compiler_marker"]!r}\n'
           f'DEPENDENCIES = {request.get("dependency_paths")!r}\n'
           + program).encode()
    if path.exists():
        path.chmod(0o700)
    path.write_bytes(raw)
    path.chmod(0o500)
    return compiler._ReviewedNativeArtifact(path, hashlib.sha256(raw).hexdigest(), compiler._VERSION)


def main():
    request = json.loads(sys.argv[1])
    base = Path(request['base'])
    import offline_compile_cli
    stack = ExitStack()
    record = None
    if request.get('fake_compiler', True):
        record = make_compiler(base, request)
        stack.enter_context(mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record))
    if request.get('catalog'):
        stack.enter_context(mock.patch.object(protected_fix, 'CATALOG', Path(request['catalog'])))
        stack.enter_context(mock.patch.object(captured_relative_inputs, 'CATALOG', Path(request['catalog'])))
    fix_type = (captured_relative_fix.ProtectedCapturedRelativeFix
        if request.get('source_profile') == 'captured-relative-core-v1'
        else protected_fix.ProtectedCoreFix)
    events = []
    seen = set()

    def hit(domain, point, path=None):
        events.append([domain, point, path])
        key = [domain, point, path]
        for index, boundary in enumerate(request.get('boundaries', [])):
            if key == boundary and index not in seen:
                seen.add(index)
                Path(request['ready'] + str(index)).write_text(json.dumps(key))
                release = Path(request['release'] + str(index))
                until = time.monotonic() + 90
                while not release.exists():
                    if time.monotonic() > until:
                        raise RuntimeError('Owned test boundary release timed out')
                    time.sleep(.01)
        if key == request.get('interrupt_boundary'):
            raise KeyboardInterrupt('Owned source interruption')
        if key == request.get('fail_boundary'):
            raise OSError('Owned CLI publication/transaction failure')
        if key == request.get('mutate_boundary'):
            destination = {'source': Path(request['root']) / request['target'],
                           'config': Path(request['root']) / '.typst-lint.toml',
                           'catalog': Path(request.get('catalog', str(protected_fix.CATALOG))),
                           'compiler': None if record is None else record.path,
                           'output': Path(request['root']) / request['output']}[request['mutate_kind']]
            destination.chmod(0o600)
            destination.write_bytes(destination.read_bytes() + b'\nowned later edit\n')

    actual_tx_hit = transactions.ProtectedTransaction._hit
    def source_hit(self, point, path=None):
        actual_tx_hit(self, point, path)
        hit('source', point, path)
    stack.enter_context(mock.patch.object(transactions.ProtectedTransaction, '_hit', source_hit))
    actual_pdf_hit = output_store._Store.hit
    def pdf_hit(self, point, path=None):
        actual_pdf_hit(self, point, path)
        hit('output', point, path)
    stack.enter_context(mock.patch.object(output_store._Store, 'hit', pdf_hit))

    if request.get('assert_order'):
        actual_validate = fix_type.validate
        def validate(self, *args, **kwargs):
            assert ['output', 'after_prepared', request['output']] in events, events
            assert not Path(request['compiler_marker']).exists()
            assert (Path(request['root']) / request['target']).read_bytes() == bytes.fromhex(request['original_hex'])
            return actual_validate(self, *args, **kwargs)
        stack.enter_context(mock.patch.object(fix_type, 'validate', validate))
    if request.get('forbid_validate'):
        stack.enter_context(mock.patch.object(fix_type, 'validate',
            side_effect=AssertionError('Blocked entry must precede source validate/apply')))
    if request.get('forbid_output_scan'):
        stack.enter_context(mock.patch.object(output_store._Store, 'inventory',
            side_effect=AssertionError('Source entry failure must not imply output scan')))
    if request.get('forbid_source'):
        stack.enter_context(mock.patch.object(fix_type, '__enter__',
            side_effect=AssertionError('Output-only route must not open source/config')))
    if request.get('readonly') or request.get('forbid_leases'):
        for owner, name in ((transactions.OfflineExclusive,
                'assert_editors_watchers_closed_and_no_other_writers'),
                (output_store.OfflineOutputExclusive,
                'assert_exporters_watchers_closed_and_no_other_writers')):
            stack.enter_context(mock.patch.object(owner, name,
                side_effect=AssertionError('Read-only/invalid route must not construct lease')))
    if request.get('readonly'):
        stack.enter_context(mock.patch.object(os, 'fsync', side_effect=AssertionError('Read-only status must not fsync')))
        stack.enter_context(mock.patch.object(os, 'mkdir', side_effect=AssertionError('Read-only status must not mkdir')))
        stack.enter_context(mock.patch.object(compiler, '_capture_compiler',
            side_effect=AssertionError('Read-only status must not capture compiler')))
    actual_run = compiler._run
    def run(argv, **kwargs):
        if kwargs['purpose'] == 'compile':
            if request.get('compiler_mode') == 'timeout':
                # Exercise the real runner and cleanup, without a fragile 10s sleep.
                kwargs['deadline'] = time.monotonic() + .3
            if request.get('compiler_mode') in ('launch-failure', 'launch-uncertain'):
                error = OSError('Owned known launch failure') if request['compiler_mode'] == 'launch-failure' else KeyboardInterrupt('Owned uncertain launch')
                with mock.patch.object(compiler.subprocess, 'Popen', side_effect=error):
                    return actual_run(argv, **kwargs)
        try:
            return actual_run(argv, **kwargs)
        except compiler.CompilerFailure:
            if request.get('compiler_mode') == 'nonzero-drift' and kwargs['purpose'] == 'compile':
                path = Path(request['root']) / request['target']
                path.write_bytes(path.read_bytes() + b'\nowned later edit\n')
            raise
    stack.enter_context(mock.patch.object(compiler, '_run', run))
    if request.get('cleanup_failure'):
        actual_kill = compiler._kill
        def kill(process):
            actual_kill(process)
            raise OSError('Owned later cleanup failure')
        stack.enter_context(mock.patch.object(compiler, '_kill', kill))
    if request.get('source_exit_failure'):
        actual_exit = fix_type.__exit__
        def exit_fix(self, *args):
            actual_exit(self, *args)
            raise OSError('Owned source context close failure')
        stack.enter_context(mock.patch.object(fix_type, '__exit__', exit_fix))

    with stack:
        if request.get('ignore_block_signal'):
            number = request['ignore_block_signal']
            signal.signal(number, signal.SIG_IGN)
            signal.pthread_sigmask(signal.SIG_BLOCK, {number})
        if request.get('callable'):
            handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
            with mock.patch.object(os, 'kill', side_effect=AssertionError('main() must not self-signal')):
                status = typst_lint.main(request['argv'], gate=request.get('gate', False))
            Path(request['callable_result']).write_text(json.dumps(dict(status=status,
                handlers_unchanged=all(signal.getsignal(s) is h for s, h in handlers.items()))))
            return 0
        if request.get('gate'):
            return typst_lint.main(request['argv'], gate=True)
        return offline_compile_cli.executable_main(request['argv'])


if __name__ == '__main__':
    raise SystemExit(main())
