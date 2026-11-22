"""Owned temporary-fixture instrumentation around the actual public CLI.

There is deliberately no corresponding production flag or environment hook.
SIGKILL tests pause this exact child at an existing internal transaction seam;
all routing, argument parsing, analysis, storage and recovery remain real.
"""
import json
from pathlib import Path
import signal
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import protected_fix
import syntax_client
import transactions
import typst_lint


def main():
    request = json.loads(sys.argv[1])
    if 'catalog' in request:
        protected_fix.CATALOG = Path(request['catalog'])

    if request.get('forbid_offline_assert'):
        def forbidden_assertion():
            raise AssertionError('Read-only inventory must not assert operator exclusivity')
        transactions.OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers = (
            staticmethod(forbidden_assertion))

    if request.get('adapter_fault'):
        actual_run = syntax_client._run

        def bad_adapter(*args, **kwargs):
            raw = actual_run(*args, **kwargs)
            if request['adapter_fault'] == 'invalid-json':
                return b'not JSON'
            payload = json.loads(raw)
            if request['adapter_fault'] == 'pin':
                payload['syntax_version'] = '0.15.2'
            elif request['adapter_fault'] == 'schema':
                payload['schema'] = 3
            elif request['adapter_fault'] == 'ast':
                payload['nodes'][0]['end'] += 1
            else:
                raise AssertionError('Unknown test-only adapter fault')
            return json.dumps(payload).encode()

        syntax_client._run = bad_adapter

    actual_hit = transactions.ProtectedTransaction._hit
    mutated = False

    def instrumented_hit(self, point, path=None):
        nonlocal mutated
        actual_hit(self, point, path)
        if [point, path] == request.get('mutate_boundary') and not mutated:
            mutated = True
            destination = Path(request['mutate_path'])
            raw = bytes.fromhex(request['mutate_hex'])
            if request.get('replace_inode'):
                replacement = destination.with_name(destination.name + '.owned-test-save')
                replacement.write_bytes(raw)
                replacement.chmod(destination.stat().st_mode & 0o777)
                replacement.replace(destination)
            else:
                destination.write_bytes(raw)
        if [point, path] == request.get('boundary'):
            print(json.dumps({'paused': [point, path]}), flush=True)
            while True:
                signal.pause()

    transactions.ProtectedTransaction._hit = instrumented_hit
    return typst_lint.main(request['argv'], gate=request.get('gate', False))


if __name__ == '__main__':
    raise SystemExit(main())
