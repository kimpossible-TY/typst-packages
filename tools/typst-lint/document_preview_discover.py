"""Read-only sandboxed dependency probe for an explicit document preview.

The compiler reads the caller's project/package roots but can write only to a
private temporary workspace and cannot use the network. Observations are only a
seed for a later captured baseline/candidate compile, not complete OS-read proof.
"""
from contextlib import ExitStack
import os
from pathlib import Path
import stat
import tempfile
import time
import unicodedata

import closed_core_compile as runtime
import document_dependencies
import document_preview_compile as preview
import document_resources
import include_context
from typst_lint import SafetyError, digest


def discover(context, roots, requests, *, entrypoint, allow_apple_asset_fonts=False,
             cancel_event=None, deadline_seconds=120.0):
    """Probe baseline dependencies and retain explicit font identities."""
    include_context.assert_include_context(context)
    if (type(roots) is not dict or not roots or len(roots) > 8
            or not {'project', 'packages', 'cache'}.issubset(roots)
            or roots.get('project') != context.project_root):
        raise SafetyError('Discovery requires matching explicit project, packages and cache roots')
    root_names = set()
    for name, value in roots.items():
        if (type(name) is not str or len(name) > 64 or not name.isascii()
                or not name.isidentifier() or name.casefold() in root_names):
            raise SafetyError('Discovery root IDs must be distinct ASCII identifiers')
        root_names.add(name.casefold())
        document_dependencies._absolute(value)
    if type(entrypoint) is not str or len(context.entrypoints) != 1 or context.entrypoints[0].path != entrypoint:
        raise SafetyError('Discovery requires the captured single entrypoint')
    if (type(requests) not in (list, tuple) or len(requests) > document_resources.MAX_FILES
            or any(type(item) is not document_resources.ResourceRequest for item in requests)):
        raise SafetyError('Discovery requires explicit typed resource requests')
    if type(allow_apple_asset_fonts) is not bool:
        raise SafetyError('Apple asset font selection must be an explicit Boolean')
    seen = set()
    for item in requests:
        if (type(item.root) is not str or item.root not in roots or type(item.role) is not str
                or item.role not in document_resources.ROLES):
            raise SafetyError('Invalid discovery resource request')
        document_resources._relative(item.path)
        if (item.root, item.path) in seen:
            raise SafetyError('Duplicate discovery resource path')
        seen.add((item.root, item.path))
    if (type(deadline_seconds) not in (int, float) or isinstance(deadline_seconds, bool)
            or not 0 < deadline_seconds <= 120):
        raise SafetyError('Discovery deadline must be in (0, 120] seconds')
    import threading
    if cancel_event is not None and type(cancel_event) is not threading.Event:
        raise SafetyError('Discovery cancellation requires a threading.Event')
    def cancelled():
        if cancel_event is not None and cancel_event.is_set():
            raise runtime.CompilerFailure('cancelled', kind=runtime.CompilerFailureKind.CANCELLED)
    cancelled()
    fonts = [item for item in requests if item.role == 'font']
    font_capture = (document_resources.capture(roots, fonts, allow_apple_asset_fonts=allow_apple_asset_fonts)
                    if fonts else None)
    font_bytes = font_capture.total_bytes if font_capture else 0
    parent_upper_bound = sum(max(0, len(item.path.split('/')) - 1) for item in fonts)
    if (font_bytes + runtime._MAX_EXECUTABLE + preview.MAX_PDF_BYTES + preview.MAX_DEPS_BYTES > preview.MAX_PREVIEW_BYTES
            or 12 + len(fonts) + parent_upper_bound > preview.MAX_PREVIEW_ENTRIES):
        raise SafetyError('Discovery fonts exceed the private workspace budget')
    compiler_record, compiler_bytes, compiler_evidence = runtime._capture_compiler()
    _, sandbox_evidence = runtime._capture(preview.SANDBOX, 2 * 1024 * 1024, executable=True)
    with tempfile.TemporaryDirectory(prefix='typst-document-discovery-') as directory, ExitStack() as stack:
        base = Path(directory).resolve()
        profile = preview._sandbox_profile(base)
        checks = []
        for name in ('project', 'packages', 'cache'):
            raw = roots[name]
            if type(raw) is not str or not raw.startswith('/') or str(Path(raw)) != raw:
                raise SafetyError('Discovery roots must be canonical absolute paths')
            _, _, check = stack.enter_context(runtime._anchored_parent(Path(raw) / '.preview-root'))
            checks.append(check)
        executable = base / 'typst'
        runtime._write_private(executable, compiler_bytes, 0o500)
        _, executable_evidence = runtime._capture(executable, runtime._MAX_EXECUTABLE, executable=True)
        if executable_evidence.sha256 != compiler_evidence.sha256:
            raise SafetyError('Private discovery compiler differs from the pinned capture')
        external, project_fonts = base / 'external-fonts', base / 'project-fonts'
        preview._mkdir(external)
        preview._mkdir(project_fonts)
        staged_fonts = {}
        basenames = set()
        if font_capture is not None:
            for item in font_capture.files:
                cancelled()
                suffix = Path(item.request.path).suffix.lower()
                if suffix not in ('.ttf', '.otf', '.ttc'):
                    raise SafetyError('Discovered font must have a supported extension')
                if item.request.root == 'project':
                    if not item.request.path.startswith('fonts/'):
                        raise SafetyError('Project fonts must be under the fonts directory')
                    destination = project_fonts / item.request.path[len('fonts/'):]
                else:
                    name = Path(item.request.path).name
                    folded = unicodedata.normalize('NFC', name).casefold()
                    if folded in basenames:
                        raise SafetyError('External font basenames collide during discovery')
                    basenames.add(folded)
                    destination = external / name
                preview._mkdir(destination.parent)
                runtime._write_private(destination, item.data, 0o600)
                _, evidence = runtime._capture(destination, document_resources.MAX_FILE_BYTES)
                if evidence.sha256 != item.sha256 or evidence.size != item.size:
                    raise SafetyError('Private discovery font differs from the capture')
                staged_fonts[destination] = evidence
        envbase = base / 'environment'
        preview._mkdir(envbase)
        for name in ('home', 'cache', 'config', 'data', 'tmp'):
            preview._mkdir(envbase / name)
        environment = runtime._environment(envbase)
        pdf, pdf_fd, pdf_id = runtime._bind_output(base / 'probe.pdf', stack)
        deps, deps_fd, deps_id = runtime._bind_output(base / 'dependencies', stack)
        allowed_files = {path for path in base.rglob('*') if path.is_file()}
        allowed_dirs = {path for path in base.rglob('*') if path.is_dir()}
        limits = {pdf: preview.MAX_PDF_BYTES, deps: preview.MAX_DEPS_BYTES}
        last_check = [0.0]
        def check(force=False):
            if force or time.monotonic() - last_check[0] >= 1.0:
                preview._workspace_guard(base, allowed_files, allowed_dirs, limits)
                for verify in checks:
                    verify()
                last_check[0] = time.monotonic()
        probe = runtime._run([str(preview.SANDBOX), '-p', profile, str(executable), '--version'],
                             purpose='document-discovery-version', cwd=base, environment=environment,
                             deadline=time.monotonic() + min(float(deadline_seconds), 10.0),
                             cancel_event=cancel_event, check_workspace=check)
        if probe.stdout != compiler_record.version or probe.stderr:
            raise SafetyError('Private discovery compiler version mismatch')
        command = [str(preview.SANDBOX), '-p', profile, str(executable), 'compile',
                   '--root', roots['project'], '--package-path', roots['packages'],
                   '--package-cache-path', roots['cache'], '--ignore-system-fonts',
                   '--font-path', str(external) + os.pathsep + str(project_fonts),
                   '--creation-timestamp', '1700000000', '--deps', str(deps), '--deps-format', 'zero',
                   '--', entrypoint, str(pdf)]
        invocation = runtime._run(command, purpose='document-discovery-compile', cwd=Path(roots['project']),
                                  environment=environment, deadline=time.monotonic() + deadline_seconds,
                                  cancel_event=cancel_event, check_workspace=check)
        if invocation.stderr:
            raise SafetyError('Dependency discovery compiler emitted warnings or diagnostics')
        check(force=True)
        for path, descriptor, identity in ((pdf, pdf_fd, pdf_id), (deps, deps_fd, deps_id)):
            if (runtime._output_identity(os.fstat(descriptor)) != identity
                    or runtime._output_identity(path.lstat()) != identity):
                raise SafetyError('Dependency discovery output identity changed')
        pdf_bytes, _ = runtime._capture(pdf, preview.MAX_PDF_BYTES)
        raw, _ = runtime._capture(deps, preview.MAX_DEPS_BYTES)
        runtime._pdf(pdf_bytes)
        observed = document_dependencies.parse(raw, cwd=roots['project'], roots=roots)
        for path, evidence in staged_fonts.items():
            runtime._same(path, evidence, document_resources.MAX_FILE_BYTES)
        runtime._same(executable, executable_evidence, runtime._MAX_EXECUTABLE, executable=True)
        runtime._same(compiler_record.path, compiler_evidence, runtime._MAX_EXECUTABLE, executable=True)
        runtime._same(preview.SANDBOX, sandbox_evidence, 2 * 1024 * 1024, executable=True)
        if font_capture is not None:
            document_resources.assert_capture(font_capture)
        include_context.assert_include_context(context)
        check(force=True)
        cancelled()
        font_hashes = {(item.request.root, item.request.path): item.sha256
                       for item in font_capture.files} if font_capture else {}
        return raw, observed, font_hashes, digest(pdf_bytes)
