"""Fresh-interpreter publication/crash driver, for isolated test fixtures only.

No user-facing CLI route, environment installation, consumer rollout or actual
project edits are enabled. Recovery never loads compiler authority or source.
"""
from dataclasses import asdict, is_dataclass
import json
import os
from pathlib import Path
import signal
import sys
from types import SimpleNamespace
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_pdf_publication_integration import (
    compiler, fixture_fix, make_compiler, publication, store,
)
from transactions import OfflineExclusive


def json_value(value):
    if is_dataclass(value):
        return {key: json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return {'hex': value.hex()}
    return value


def main():
    request = json.loads(sys.argv[1])
    root = Path(request['root'])
    lease = store.OfflineOutputExclusive.assert_exporters_watchers_closed_and_no_other_writers()

    def fault(point, path):
        if point == request.get('boundary'):
            print(json.dumps({'paused': point, 'path': str(path)}), flush=True)
            while True:
                signal.pause()
        if point == request.get('fail_point'):
            raise OSError('owned process fixture publication failure')

    try:
        if request['action'] == 'recover':
            report = store.recover_publication(root, destination=request['destination'],
                journal_id=request['journal_id'], lease=lease, fault=fault)
            output = {'report': json_value(report)}
        elif request['action'] == 'inventory':
            output = {'entries': json_value(store.inventory_publications(root))}
        else:
            f = SimpleNamespace(base=Path(request['base']), root=root,
                target=request['target'], catalog=Path(request['catalog']),
                source_store=Path(request['source_store']), destination=request['destination'],
                source_lease=OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers())
            if request['action'] == 'lock-probe':
                with fixture_fix(f):
                    output = {'lock_reacquired': True}
            else:
                record = make_compiler(f, mode=request.get('compiler_mode', 'success'),
                                       marker=request.get('compiler_marker'))
                with fixture_fix(f) as fix, mock.patch.object(compiler, '_reviewed_native_artifact', return_value=record):
                    with publication.PreparedPdfPublication(fix, destination=f.destination,
                                                           lease=lease, fault=fault) as pub:
                        candidate = fix.validate()
                        fix.apply(candidate)
                        # Crash tests target storage boundaries, not a 10s performance limit.
                        report = pub.compile_and_publish(fix.compile_snapshot(),
                            profile=compiler.RESOURCE_PROFILE, deadline_seconds=60.)
                        output = {'report': json_value(report)}
    except Exception as error:
        output = {'exception': type(error).__name__, 'message': str(error)}
        if getattr(error, 'report', None) is not None:
            output['report'] = json_value(error.report)
    print(json.dumps(output), flush=True)


if __name__ == '__main__':
    main()
