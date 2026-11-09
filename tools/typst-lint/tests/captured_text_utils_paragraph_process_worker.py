"""Fresh interpreter for test-owned internal paragraph repair crash barriers.

This is not a public CLI. All paths are supplied by an isolated test fixture.
Recovery intentionally uses only the existing transaction store and schema.
"""
from dataclasses import asdict
import json
from pathlib import Path
import signal
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import captured_text_utils_paragraph_fix as bridge
import captured_text_utils_paragraph_inputs as inputs
from transactions import Fingerprint, OfflineExclusive, ProtectedTransaction, RecoveryRequired


def main():
    request = json.loads(sys.argv[1])
    root, store = Path(request['root']), Path(request['store'])
    target = request['target']
    inputs.CATALOG = Path(request['catalog'])

    def pause(point, path):
        if [point, path] == request.get('boundary'):
            print(json.dumps({'paused': [point, path]}), flush=True)
            while True:
                signal.pause()

    lease = OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers()
    try:
        if request['action'] == 'apply':
            with bridge.ProtectedCapturedTextUtilsParagraphFix(root, target,
                    package_path=Path(request['package_path']), journal_directory=store,
                    lease=lease, fault=pause) as fix:
                candidate = fix.validate()
                result = fix.apply(candidate)
                output = dict(modified=result.modified, journal=result.journal,
                    findings=sorted(result.unresolved_findings), passes=candidate.passes,
                    final_sha256=fix.summary.final_sha256,
                    profile=fix.summary.profile)
        else:
            with ProtectedTransaction(root, (target,), journal_directory=store,
                    lease=lease, recovery_only=request['action'] == 'recover', fault=pause) as tx:
                if request['action'] == 'recover':
                    report = tx.recover(request['journal'], preserve_conflicts={
                        path: Fingerprint(**value)
                        for path, value in request.get('preserve', {}).items()})
                    output = {name: getattr(report, name) for name in (
                        'journal', 'restored', 'unchanged', 'changed', 'unresolved',
                        'preserved', 'errors', 'complete')}
                    output['conflicts'] = {path: asdict(value)
                                           for path, value in report.conflicts.items()}
                else:
                    output = {'open': True}
    except RecoveryRequired as error:
        output = {'pending': error.journals}
    print(json.dumps(output), flush=True)


if __name__ == '__main__':
    main()
