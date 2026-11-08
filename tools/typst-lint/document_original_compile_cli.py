"""Explicit original-only, captured Typst compilation to stdout.

This route performs no lint and never writes project sources or an output path.
The compiled PDF is emitted only after capture and compiler checks succeed.
"""
import argparse
import io
import json
import os
from pathlib import Path
import select
import stat
import sys

import closed_core_compile as runtime
import document_dependencies
import document_preview_compile
import document_preview_discover
import document_resources
import include_context
from document_preview_cli import MAX_MANIFEST_BYTES, parse_manifest_bytes
from package_loader import PackageInputError, _absolute_root, read_snapshot_file
from typst_lint import InputError, SafetyError, VERSION, checked_path, relative, require


class _Parser(argparse.ArgumentParser):
    def print_help(self, file=None):
        super().print_help(file or sys.stderr)


def _stream_identity(stream):
    try:
        info = os.fstat(stream.fileno())
    except (AttributeError, io.UnsupportedOperation):
        return None
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))


def _same_streams():
    output = getattr(sys.stdout, 'buffer', sys.stdout)
    error = getattr(sys.stderr, 'buffer', sys.stderr)
    if output is error:
        return True
    left, right = _stream_identity(output), _stream_identity(error)
    return left is not None and left == right


def _regular_stream_id(stream):
    identity = _stream_identity(stream)
    return identity[:2] if identity is not None and identity[2] == stat.S_IFREG else None


def _path_id(path):
    try:
        info = os.stat(path)
    except (OSError, ValueError):
        return None
    return (info.st_dev, info.st_ino) if stat.S_ISREG(info.st_mode) else None


def _stream_collisions(protected):
    protected = {item for item in protected if item is not None}
    return (_regular_stream_id(getattr(sys.stdout, 'buffer', sys.stdout)) in protected,
            _regular_stream_id(getattr(sys.stderr, 'buffer', sys.stderr)) in protected)


def _write_pdf(data, cancel):
    """Stream validated bytes with cooperative cancellation for pipe consumers."""
    output = sys.stdout.buffer
    try:
        fd = output.fileno()
    except (AttributeError, io.UnsupportedOperation):
        if cancel is not None and cancel.is_set():
            raise runtime.CompilerFailure('cancelled', kind=runtime.CompilerFailureKind.CANCELLED)
        if output.write(data) != len(data):
            raise OSError('stdout accepted only part of the PDF')
        output.flush()
        return
    original_blocking = os.get_blocking(fd)
    try:
        os.set_blocking(fd, False)
        view = memoryview(data)
        offset = 0
        while offset < len(view):
            if cancel is not None and cancel.is_set():
                raise runtime.CompilerFailure('cancelled', kind=runtime.CompilerFailureKind.CANCELLED)
            _, ready, _ = select.select([], [fd], [], 0.1)
            if not ready:
                continue
            try:
                count = os.write(fd, view[offset:offset + 65536])
            except BlockingIOError:
                continue
            if count <= 0:
                raise OSError('stdout accepted no PDF bytes')
            offset += count
        output.flush()
    finally:
        os.set_blocking(fd, original_blocking)


def main(arguments=None):
    # Reporting on the same file descriptor would corrupt the PDF stream.
    # A refusal must be silent because stderr itself aliases stdout.
    if _same_streams():
        return 2
    # Before discovery, the complete input set is not known. Never append an
    # error report to a regular file which might be an uncaptured project input.
    if _regular_stream_id(getattr(sys.stderr, 'buffer', sys.stderr)) is not None:
        return 2
    parser = _Parser(prog='typst-style compile --compile-original', description=__doc__, allow_abbrev=False)
    parser.add_argument('--root', required=True)
    parser.add_argument('--entrypoint', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--discover-dependencies', action='store_true')
    parser.add_argument('--pdf-stdout', action='store_true', required=True,
                        help='Emit the verified original PDF bytes on stdout; report JSON on stderr')
    words = list(sys.argv[1:] if arguments is None else arguments)
    seen = set()
    for word in words:
        option = word.split('=', 1)[0]
        if option in parser._option_string_actions and option not in ('-h', '--help'):
            if option in seen:
                parser.error(f'Duplicate option: {option}')
            seen.add(option)
    args = parser.parse_args(words)
    output_identity = _stream_identity(getattr(sys.stdout, 'buffer', sys.stdout))
    if output_identity is not None and output_identity[2] not in (stat.S_IFIFO, stat.S_IFSOCK):
        print(json.dumps(dict(schema=1, mode='original-compile', status=2,
                              error='PDF stdout must be a pipe or socket; regular-file aliases are refused',
                              pdf_streamed=False, lint_performed=False, global_coverage_complete=False,
                              output_stream_note=('Shell redirection may already have truncated its target '
                                                  'before this program starts.'))), file=sys.stderr)
        return 2
    import offline_compile_cli
    signals = offline_compile_cli._active_signals
    cancel = signals.cancel_event if signals is not None else None
    starts = document_preview_compile.PreviewCompilerStarts()
    phase = 'input'
    result = dict(schema=1, tool_version=VERSION, mode='original-compile', status=2,
                  lint_performed=False, global_coverage_complete=False,
                  source_files_opened_for_write_by_tool=False, source_application_authority=False,
                  publication_authority=False, dependency_closure_proved=False,
                  live_source_freshness_proved=False, pdf_streamed=False,
                  output_stream_note=('Stdout and stderr are caller-managed; shell redirection can truncate files '
                                      'before this program starts. Stdout requires a pipe or socket. '
                                      'A broken stdout stream can contain a partial PDF. '
                                      'Stderr regular files are refused without a report.'))
    try:
        root = _absolute_root(args.root)
        require(args.root == str(root), 'Compile root must be canonical and absolute')
        relative(args.entrypoint)
        require(args.entrypoint.endswith('.typ'), 'Compile entrypoint must be a Typst path')
        # Reject known aliases before reading these paths. The shell may already
        # have opened or truncated either stream's target before our process ran.
        out_collision, err_collision = _stream_collisions((_path_id(args.manifest), _path_id(root / args.entrypoint)))
        if err_collision:
            return 2  # Do not append even an error report to a protected input.
        require(not out_collision, 'PDF stdout aliases the manifest or entrypoint')
        require(type(args.manifest) is str and args.manifest.startswith('/')
                and str(Path(args.manifest)) == args.manifest, 'Manifest must be a canonical absolute path')
        raw_manifest, manifest_evidence = runtime._capture(Path(args.manifest), MAX_MANIFEST_BYTES)
        roots, requests, allow = parse_manifest_bytes(raw_manifest, root, allow_empty=args.discover_dependencies)
        known = [manifest_evidence.identity[:2]]
        for request in requests:
            if (type(request.root) is str and request.root in roots
                    and type(roots[request.root]) is str and type(request.path) is str):
                document_resources._relative(request.path)
                known.append(_path_id(Path(roots[request.root]) / request.path))
        out_collision, err_collision = _stream_collisions(known)
        if err_collision:
            return 2
        require(not out_collision, 'PDF stdout aliases a requested input')
        _, source = read_snapshot_file(checked_path(root, args.entrypoint), include_context.MAX_SOURCE_BYTES)
        context = include_context.load_include_context(root, ((args.entrypoint, source),), budget='book-v1')
        observed = expected_pdf = None
        fonts = {}
        if args.discover_dependencies:
            phase = 'discovery'
            raw_deps, observed, fonts, expected_pdf = document_preview_discover.discover(
                context, roots, requests, entrypoint=args.entrypoint,
                allow_apple_asset_fonts=allow, cancel_event=cancel, starts=starts)
            selected = {(item.root, item.path): item for item in requests}
            for root_id, path in observed:
                role = ('source' if root_id == 'project' and path.endswith('.typ') else
                        'resource' if root_id == 'project' else 'package')
                selected.setdefault((root_id, path), document_resources.ResourceRequest(root_id, path, role))
            for item in context.sources:
                selected.setdefault(('project', item.path),
                                    document_resources.ResourceRequest('project', item.path, 'source'))
            requests = list(selected.values())
        phase = 'capture'
        snapshot = document_resources.capture(roots, requests, allow_apple_asset_fonts=allow)
        protected = [manifest_evidence.identity[:2]] + [item.evidence.identity[:2] for item in snapshot.files]
        out_collision, err_collision = _stream_collisions(protected)
        if err_collision:
            return 2
        require(not out_collision, 'PDF stdout aliases a captured input')
        if observed is not None:
            captured_fonts = {(item.request.root, item.request.path): item.sha256 for item in snapshot.files
                              if item.request.role == 'font'}
            require(captured_fonts == fonts, 'Selected fonts changed after dependency discovery')
            comparison = document_dependencies.compare(snapshot, raw_deps, cwd=str(root), roots=roots)
            require(not comparison.missing, 'A discovered dependency is absent from the resource capture')
        phase = 'compile'
        compiled = document_preview_compile.compile_original(context, snapshot, entrypoint=args.entrypoint,
            cancel_event=cancel, expected_dependencies=observed, expected_baseline_sha256=expected_pdf, starts=starts)
        if cancel is not None and cancel.is_set():
            raise runtime.CompilerFailure('cancelled', kind=runtime.CompilerFailureKind.CANCELLED)
        result.update(status=0, phase='complete', pdf_sha256=compiled.pdf_sha256,
            observed_dependencies=compiled.dependency_count, unobserved_captured_resources=compiled.unobserved_capture_count,
            selected_system_font_files=compiled.system_font_files, compiler_sha256=compiled.compiler_sha256,
            resource_count=len(snapshot.files), source_count=len(context.sources),
            discovery_performed=observed is not None,
            note='Compilation succeeded; lint was not performed and rule coverage is incomplete')
        phase = 'stream'
        out_collision, err_collision = _stream_collisions(protected)
        if (_same_streams() or err_collision
                or _regular_stream_id(getattr(sys.stderr, 'buffer', sys.stderr)) is not None):
            return 2
        require(not out_collision, 'PDF stdout aliases a captured input')
        require(_stream_identity(getattr(sys.stdout, 'buffer', sys.stdout)) == output_identity,
                'PDF stdout stream changed before transfer')
        _write_pdf(compiled.pdf, cancel)
        if cancel is not None and cancel.is_set():
            raise runtime.CompilerFailure('cancelled', kind=runtime.CompilerFailureKind.CANCELLED)
        result['pdf_streamed'] = True
    except (InputError, SafetyError, PackageInputError, OSError, ValueError, runtime.CompilerFailure) as error:
        result.update(status=3 if phase == 'stream' else 2, phase=phase, error=str(error), pdf_streamed=False)
        if phase == 'stream':
            result['stdout_may_be_partial'] = True
        if isinstance(error, runtime.CompilerFailure) and error.invocation is not None:
            starts.observe(error.invocation)
            if error.invocation.stderr:
                result['compiler_diagnostics'] = error.invocation.stderr[:8192].decode('utf-8', errors='replace')
        elif isinstance(error, document_preview_compile.PreviewCompilerDiagnostics):
            starts.observe(error.invocation)
            if error.invocation.stderr:
                result['compiler_diagnostics'] = error.invocation.stderr[:8192].decode('utf-8', errors='replace')
    stages = starts.stages()
    result['compiler_stages_started'] = stages
    result['compiler_invoked'] = starts.compiler_invoked()
    result['compiler_version_probe_invoked'] = starts.version_probe_invoked()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2), file=sys.stderr)
    if signals is not None and signals.first_signal is not None:
        return -signals.first_signal
    return result['status']


if __name__ == '__main__':
    sys.exit(main())
