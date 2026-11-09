"""Owned fresh-process driver through the actual public paragraph launcher.

Only the private reviewed-artifact seam and existing storage fault callbacks
are patched. No CLI/environment hook, native compiler, font or consumer is used.
"""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import runpy
import sys
import time
from unittest import mock

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
import captured_text_utils_paragraph_fix as bridge
import captured_text_utils_paragraph_inputs as inputs
import closed_core_compile as runtime
import pdf_publication_store as output_store
import transactions


def make_compiler(request):
    path = Path(request['base']) / 'owned paragraph compiler'
    program = '''import os, pathlib, sys, time
if sys.argv[1:] == ['--version']:
    os.write(1, VERSION)
    raise SystemExit(0)
assert sys.argv[1] == 'compile'
source, output = map(pathlib.Path, sys.argv[-2:])
pathlib.Path(MARKER).write_text(str(os.getpid()))
if MODE == 'nonzero':
    os.write(1, b'owned paragraph stdout\\x00\\xff')
    os.write(2, b'owned paragraph stderr\\xfe\\n')
    raise SystemExit(19)
if MODE == 'hang':
    while True:
        time.sleep(.01)
packages = pathlib.Path(sys.argv[sys.argv.index('--package-path') + 1])
leaf = packages / 'local/text-utils/0.1.2'
prefix = b'%PDF-1.7\\n%retained-source=' + source.read_bytes().hex().encode() + b'\\n'
output.write_bytes(prefix + b'xref\\n0 1\\n0000000000 65535 f \\ntrailer\\n<< /Size 1 >>\\nstartxref\\n' + str(len(prefix)).encode() + b'\\n%%EOF\\n')
observed = (leaf / 'typst.toml', source.relative_to(pathlib.Path.cwd()), leaf / 'lib.typ')
pathlib.Path(sys.argv[sys.argv.index('--deps') + 1]).write_bytes(b''.join(os.fsencode(p) + b'\\0' for p in observed))
'''
    raw = (f'#!{sys.executable}\nVERSION = {runtime._VERSION!r}\n'
           f'MODE = {request.get("compiler_mode", "success")!r}\n'
           f'MARKER = {request["compiler_marker"]!r}\n' + program).encode()
    if path.exists():
        path.chmod(0o700)
    path.write_bytes(raw)
    path.chmod(0o500)
    return runtime._ReviewedNativeArtifact(path, hashlib.sha256(raw).hexdigest(), runtime._VERSION)


def main():
    request = json.loads(sys.argv[1])
    with ExitStack() as stack:
        if request.get('fake_compiler', True):
            artifact = make_compiler(request)
            stack.enter_context(mock.patch.object(runtime, '_reviewed_native_artifact', return_value=artifact))
        else:
            stack.enter_context(mock.patch.object(runtime, '_capture_compiler',
                side_effect=AssertionError('Historical recovery must not capture a compiler')))
        stack.enter_context(mock.patch.object(inputs, 'CATALOG', Path(request['catalog'])))
        events, seen = [], set()

        def hit(domain, point, path):
            key = [domain, point, path]
            events.append(key)
            for index, boundary in enumerate(request.get('boundaries', [])):
                if key == boundary and index not in seen:
                    seen.add(index)
                    marker = Path(request['ready'] + str(index))
                    marker.write_text(json.dumps(key))
                    release = Path(request['release'] + str(index))
                    deadline = time.monotonic() + 60
                    while not release.exists():
                        if time.monotonic() > deadline:
                            raise RuntimeError('Owned paragraph barrier release timed out')
                        time.sleep(.01)

        old_source_hit = transactions.ProtectedTransaction._hit
        def source_hit(self, point, path=None):
            old_source_hit(self, point, path)
            hit('source', point, path)
        stack.enter_context(mock.patch.object(transactions.ProtectedTransaction, '_hit', source_hit))
        old_output_hit = output_store._Store.hit
        def output_hit(self, point, path=None):
            old_output_hit(self, point, path)
            hit('output', point, path)
        stack.enter_context(mock.patch.object(output_store._Store, 'hit', output_hit))
        if request.get('assert_order'):
            old_validate = bridge.ProtectedCapturedTextUtilsParagraphFix.validate
            def validate(self, *args, **kwargs):
                assert ['output', 'after_prepared', request['output']] in events, events
                assert not Path(request['compiler_marker']).exists()
                assert (Path(request['root']) / request['target']).read_bytes() == bytes.fromhex(request['original_hex'])
                return old_validate(self, *args, **kwargs)
            stack.enter_context(mock.patch.object(bridge.ProtectedCapturedTextUtilsParagraphFix, 'validate', validate))
        if request.get('forbid_validate'):
            stack.enter_context(mock.patch.object(bridge.ProtectedCapturedTextUtilsParagraphFix, 'validate',
                side_effect=AssertionError('Pending output must block paragraph validation')))
        if request.get('forbid_source'):
            stack.enter_context(mock.patch.object(bridge.ProtectedCapturedTextUtilsParagraphFix, '__enter__',
                side_effect=AssertionError('Output recovery must not open paragraph inputs')))
        # Execute the real launcher, including production self-signal semantics.
        sys.argv = [str(TOOL / 'typst-lint'), *request['argv']]
        runpy.run_path(str(TOOL / 'typst-lint'), run_name='__main__')


if __name__ == '__main__':
    main()
