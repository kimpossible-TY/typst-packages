"""Explicit read-only document PDF preview from a caller-supplied resource manifest."""
import argparse
import json
from pathlib import Path
import sys

import closed_core_compile as runtime
import document_preview_compile
import document_resources
import document_sequence_preview
import include_context
from package_loader import PackageInputError, _absolute_root, read_snapshot_file
from typst_lint import InputError, SafetyError, VERSION, checked_path, load_rules, relative, require

MAX_MANIFEST_BYTES = 1024 * 1024


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SafetyError('Duplicate resource manifest key')
        result[key] = value
    return result


def load_manifest(path, root):
    """Read one explicit manifest; serialized data grants no capture authority."""
    require(type(path) is str and path.startswith('/') and str(Path(path)) == path,
            'Manifest must be a canonical absolute path')
    raw, _ = runtime._capture(Path(path), MAX_MANIFEST_BYTES)
    try:
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Invalid JSON constant')))
    except (UnicodeError, ValueError, RecursionError) as error:
        raise SafetyError(f'Invalid resource manifest: {error}') from error
    require(type(data) is dict and set(data) <= {'roots', 'files', 'allow_apple_asset_fonts'}
            and {'roots', 'files'} <= set(data), 'Invalid resource manifest fields')
    roots, files = data['roots'], data['files']
    require(type(roots) is dict and roots.get('project') == str(root),
            'Manifest project root must equal the selected root')
    require(type(files) is list and 0 < len(files) <= document_resources.MAX_FILES,
            'Manifest must contain a bounded explicit file list')
    requests = []
    for row in files:
        require(type(row) is dict and set(row) == {'root', 'path', 'role'},
                'Each manifest file requires root, path and role')
        requests.append(document_resources.ResourceRequest(row['root'], row['path'], row['role']))
    allow = data.get('allow_apple_asset_fonts', False)
    require(type(allow) is bool, 'Manifest Apple asset selection must be Boolean')
    return roots, requests, allow


def main(arguments=None):
    parser = argparse.ArgumentParser(prog='typst-lint preview-compile', description=__doc__,
        epilog='The resource manifest is explicit. PDF bytes are emitted only with --pdf-stdout. '
               'The selected rules remain partial, so a successful preview exits 2.')
    parser.add_argument('--root', required=True, help='Canonical absolute consumer root')
    parser.add_argument('--entrypoint', required=True, help='Canonical project-relative .typ entrypoint')
    parser.add_argument('--manifest', required=True, help='Canonical absolute resource manifest JSON')
    parser.add_argument('--format', choices=('json', 'text'), default='text')
    parser.add_argument('--pdf-stdout', action='store_true', help='Emit only the candidate PDF to stdout')
    args = parser.parse_args(sys.argv[1:] if arguments is None else arguments)
    # The executable installs cooperative SIGINT/SIGTERM handling; imported
    # callers do not change process-wide signal handlers.
    import offline_compile_cli
    signals = offline_compile_cli._active_signals
    cancel = signals.cancel_event if signals is not None else None
    phase = 'input'
    result = None
    try:
        raw_root = args.root
        root = _absolute_root(raw_root)
        require(raw_root == str(root), 'Preview root must be canonical and absolute')
        relative(args.entrypoint)
        require(args.entrypoint.endswith('.typ'), 'Preview entrypoint must be a Typst path')
        roots, requests, allow = load_manifest(args.manifest, root)
        _, source = read_snapshot_file(checked_path(root, args.entrypoint), include_context.MAX_SOURCE_BYTES)
        phase = 'planning'
        context = include_context.load_include_context(root, ((args.entrypoint, source),), budget='book-v1')
        catalog, catalog_hash = load_rules()
        records = []
        for item in context.sources:
            if cancel is not None and cancel.is_set():
                raise runtime.CompilerFailure('cancelled', kind=runtime.CompilerFailureKind.CANCELLED)
            record = document_sequence_preview.preview(item.tree, item.path, catalog['rules'])
            if record is not None:
                if record['verification'] == 'withheld':
                    raise SafetyError(f"{item.path}: {record['reason']}")
                records.append(record)
        phase = 'capture'
        snapshot = document_resources.capture(roots, requests, allow_apple_asset_fonts=allow)
        phase = 'compile'
        preview = document_preview_compile.compile_preview(context, records, catalog['rules'], snapshot,
                                                           entrypoint=args.entrypoint, cancel_event=cancel)
        result = dict(schema=1, tool_version=VERSION, mode='preview-compile', status=2, phase='preview',
                      compiler_invoked=True, pdf_generated=True, source_files_modified=False,
                      publication_authority=False, global_coverage_complete=False,
                      source_count=len(context.sources), planned_sources=len(records),
                      planned_edits=sum(len(step['edits']) for record in records for step in record['steps']),
                      resource_count=len(snapshot.files), catalog_sha256=catalog_hash,
                      baseline_pdf_sha256=preview.baseline_sha256, candidate_pdf_sha256=preview.candidate_sha256,
                      pdfs_identical=preview.pdfs_identical, observed_dependencies=preview.dependency_count,
                      unobserved_captured_resources=preview.unobserved_capture_count,
                      system_font_files=preview.system_font_files,
                      dependency_closure_proved=False, compiler_sha256=preview.compiler_sha256)
        if cancel is not None and cancel.is_set():
            raise runtime.CompilerFailure('cancelled', kind=runtime.CompilerFailureKind.CANCELLED)
        if args.pdf_stdout:
            sys.stdout.buffer.write(preview.candidate_pdf)
            sys.stdout.buffer.flush()
    except (InputError, SafetyError, PackageInputError, OSError, ValueError, runtime.CompilerFailure) as error:
        result = dict(schema=1, tool_version=VERSION, mode='preview-compile', status=2, phase=phase,
                      compiler_invoked=None if phase == 'compile' else False, pdf_generated=False,
                      source_files_modified=False, publication_authority=False, error=str(error))
        if isinstance(error, runtime.CompilerFailure) and error.invocation is not None:
            if type(error.invocation.started) is bool:
                result['compiler_invoked'] = error.invocation.started
            if error.invocation.stderr:
                result['compiler_diagnostics'] = error.invocation.stderr[:8192].decode('utf-8', errors='replace')
    stream = sys.stderr if args.pdf_stdout else sys.stdout
    if args.format == 'json' or args.pdf_stdout:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2), file=stream)
    elif result.get('error'):
        print(f"typst-lint preview-compile: blocked: {result['error']}", file=stream)
        if result.get('compiler_diagnostics'):
            print(result['compiler_diagnostics'], file=stream)
    else:
        print(f"typst-lint preview-compile: PDF generated; {result['observed_dependencies']} observed dependencies; "
              f"baseline/candidate identical: {result['pdfs_identical']}", file=stream)
        print('Sources were not modified; global rule coverage remains incomplete (exit 2).', file=stream)
    if signals is not None and signals.first_signal is not None:
        return -signals.first_signal
    return result['status']
