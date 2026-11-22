"""Owned fresh-interpreter barriers for internal graph PDF publication tests.

Every input is an isolated temporary fixture. Recovery/inventory deliberately do
not import a graph fixture, parse source, or load native/font resource authority.
"""
from dataclasses import fields, is_dataclass
import json
from pathlib import Path
import signal
import sys
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pdf_publication_store as store


def json_value(value):
    if is_dataclass(value):
        return {item.name: json_value(getattr(value, item.name)) for item in fields(value)}
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
            raise OSError('owned graph publication process fault')

    def source_fault(point, path):
        if point == request.get('source_boundary'):
            print(json.dumps({'paused': point, 'path': str(path)}), flush=True)
            while True:
                signal.pause()

    try:
        if request['action'] == 'recover':
            report = store.recover_publication(root, destination=request['destination'],
                journal_id=request['journal_id'], lease=lease, fault=fault)
            output = {'report': json_value(report)}
        elif request['action'] == 'inventory':
            output = {'entries': json_value(store.inventory_publications(root))}
        else:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from graph_compile_test_support import GraphFixture, VERSION
            import closed_core_compile as runtime
            from transactions import OfflineExclusive
            # Reopen an existing fixture without rewriting any source or evidence.
            f = GraphFixture.__new__(GraphFixture)
            f.base, f.root = Path(request['base']), root
            f.target, f.catalog = request['target'], Path(request['catalog'])
            f.store = Path(request['source_store'])
            f.lease = OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers()
            f.compiler = Path(request['compiler'])
            if request['action'] not in ('lock-probe', 'source-apply'):
                raw = f.compiler.read_bytes()
                f.reviewed = runtime._ReviewedNativeArtifact(f.compiler, runtime._hash(raw), VERSION)
            if request['action'] == 'legacy-publish':
                import closed_core_publish as legacy
                import protected_fix
                with mock.patch.object(protected_fix, 'CATALOG', f.catalog), \
                        protected_fix.ProtectedCoreFix(root, f.target,
                            journal_directory=f.store, lease=f.lease) as fix, f.resources():
                    with legacy.PreparedPdfPublication(fix, destination=request['destination'],
                                                       lease=lease, fault=fault) as pub:
                        fix.apply(fix.validate())
                        report = pub.compile_and_publish(fix.compile_snapshot(),
                            profile=runtime.RESOURCE_PROFILE, deadline_seconds=60.)
                        output = {'report': json_value(report)}
            else:
                with f.context(committed=False, fault=source_fault) as fix:
                    if request['action'] == 'lock-probe':
                        output = {'lock_reacquired': True}
                    elif request['action'] == 'source-apply':
                        fix.apply(fix.validate())
                        output = {'source_applied': True}
                    else:
                        import captured_relative_publish as publication
                        with f.resources(), publication.PreparedCapturedRelativePdfPublication(
                                fix, destination=request['destination'], lease=lease, fault=fault) as pub:
                            fix.apply(fix.validate())
                            report = pub.compile_and_publish(fix.committed_graph_snapshot(),
                                profile=runtime.RESOURCE_PROFILE, deadline_seconds=60.)
                            output = {'report': json_value(report)}
    except Exception as error:
        output = {'exception': type(error).__name__, 'message': str(error)}
        if getattr(error, 'report', None) is not None:
            output['report'] = json_value(error.report)
    print(json.dumps(output), flush=True)


if __name__ == '__main__':
    main()
