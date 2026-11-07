"""Subprocess driver for temporary-consumer crash tests, never a user CLI."""
from dataclasses import asdict
import json
from pathlib import Path
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from transactions import (Fingerprint, OfflineExclusive, ProtectedTransaction,
                          RecoveryRequired)
from test_transactions import RULES, synthetic_engine


def main():
    request = json.loads(sys.argv[1])

    def pause_at_boundary(point, path):
        if [point, path] == request.get('boundary'):
            # Parent sends SIGKILL only after this handshake. No Python exception
            # or context-manager cleanup participates in the interrupted write.
            print(json.dumps({'paused': [point, path]}), flush=True)
            while True:
                signal.pause()

    options = dict(
        journal_directory=Path(request['store']),
        lease=OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers(),
        recovery_only=request['action'] == 'recover', fault=pause_at_boundary)
    try:
        with ProtectedTransaction(Path(request['root']), ('a.typ', 'b.typ'), **options) as tx:
            if request['action'] == 'apply':
                result = tx.apply(tx.validate(synthetic_engine, required_rules=RULES))
                output = {'modified': result.modified, 'journal': result.journal}
            elif request['action'] == 'recover':
                report = tx.recover(request['journal'], preserve_conflicts={
                    p: Fingerprint(**fp) for p, fp in request.get('preserve', {}).items()})
                output = {name: getattr(report, name) for name in (
                    'journal', 'restored', 'unchanged', 'changed', 'unresolved',
                    'preserved', 'errors', 'complete')}
                output['conflicts'] = {p: asdict(fp) for p, fp in report.conflicts.items()}
            else:
                output = {'open': True}
    except RecoveryRequired as error:
        output = {'pending': error.journals}
    print(json.dumps(output), flush=True)


if __name__ == '__main__':
    main()
