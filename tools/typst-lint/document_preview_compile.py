"""Private, explicitly selected document preview compilation on macOS.

This is not the ordinary build gate, a source transaction, PDF publication, or
a proof of exhaustive OS reads. Inputs are issued in-memory captures only.
"""
from contextlib import ExitStack
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
import unicodedata

import closed_core_compile as runtime
import document_dependencies
import document_resources
import include_context
from typst_lint import SafetyError, digest

MAX_PREVIEW_BYTES = 640 * 1024 * 1024
MAX_PREVIEW_ENTRIES = 4096
MAX_PDF_BYTES = 16 * 1024 * 1024
MAX_DEPS_BYTES = document_dependencies.MAX_DEPENDENCY_BYTES
SANDBOX = Path('/usr/bin/sandbox-exec')


class PreviewCompilerDiagnostics(SafetyError):
    """Successful private compile emitted diagnostics and was refused."""

    def __init__(self, stage, invocation):
        super().__init__('Preview compiler emitted warnings or diagnostics')
        self.stage = stage
        self.invocation = invocation


class NoComposedPreview(SafetyError):
    """Both verified stages are empty; retain the partial-preview summary."""
    def __init__(self, summary):
        super().__init__('No verified composed candidate edits')
        self.summary = summary


_PURPOSE_STAGES = {'document-discovery-compile': 'discovery',
                   'document-preview-baseline': 'baseline',
                   'document-preview-candidate': 'candidate'}
_VERSION_PURPOSES = frozenset(('document-discovery-version', 'document-preview-version'))


class PreviewCompilerStarts:
    """Historical process starts, separate from a version probe or authority."""
    def __init__(self):
        self._started = {purpose: [] for purpose in (*_PURPOSE_STAGES, *_VERSION_PURPOSES)}
        self._unknown = []

    def observe(self, invocation):
        if type(invocation) is runtime.Invocation and invocation.purpose in self._started:
            self._started[invocation.purpose].append(invocation.started)
        elif type(invocation) is runtime.Invocation and invocation.started is not False:
            self._unknown.append(None)

    @staticmethod
    def _combine(values):
        if any(value is True for value in values):
            return True
        return None if any(value is None for value in values) else False

    def stages(self):
        return {stage: self._combine(self._started[purpose] + self._unknown)
                for purpose, stage in _PURPOSE_STAGES.items()}

    def compiler_invoked(self):
        return self._combine([value for purpose in _PURPOSE_STAGES for value in self._started[purpose]] + self._unknown)

    def version_probe_invoked(self):
        return self._combine([value for purpose in _VERSION_PURPOSES for value in self._started[purpose]] + self._unknown)


def _run_tracked(argv, *, starts, **kwargs):
    # The shared runtime ledger is deliberately per invocation: its own
    # two-attempt bound is for another compile protocol. Retain only started
    # state here, including interrupted launches and failures before return.
    if starts is None:
        return runtime._run(argv, **kwargs)
    if type(starts) is not PreviewCompilerStarts:
        raise SafetyError('Invalid preview compiler start tracker')
    ledger = runtime._AttemptLedger()
    try:
        return runtime._run(argv, _ledger=ledger, **kwargs)
    finally:
        for attempt in ledger.attempts:
            starts.observe(attempt.snapshot())


@dataclass(frozen=True, slots=True)
class PreviewCompileResult:
    baseline_sha256: str
    candidate_sha256: str
    pdfs_identical: bool
    dependency_count: int
    unobserved_capture_count: int
    compiler_sha256: str
    system_font_files: int
    candidate_pdf: bytes = field(repr=False)
    source_files_modified: bool = False
    dependency_closure_proved: bool = False
    publication_authority: bool = False
    modern_highlight_evidence: dict | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class OriginalCompileResult:
    pdf_sha256: str
    dependency_count: int
    unobserved_capture_count: int
    compiler_sha256: str
    system_font_files: int
    pdf: bytes = field(repr=False)
    source_files_modified: bool = False
    dependency_closure_proved: bool = False
    publication_authority: bool = False


def _sandbox_profile(base):
    value = str(base)
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', value):
        raise SafetyError('Temporary preview path cannot be represented safely in sandbox policy')
    return f'(version 1)(allow default)(deny network*)(deny file-write*)(allow file-write* (subpath "{value}"))'


def _mkdir(path):
    """Create only private directories under a newly owned temporary root."""
    pending = []
    current = path
    while not current.exists():
        pending.append(current)
        current = current.parent
    for directory in reversed(pending):
        directory.mkdir(mode=0o700)
    for directory in (*pending, path):
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise SafetyError('Unsafe private preview directory')


def _workspace_guard(base, allowed_files, allowed_dirs, output_limits):
    """Bound private entries without following symlinks or accepting new paths."""
    total = count = 0
    info = base.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise SafetyError('Unsafe private preview workspace')
    for directory, subdirs, names in os.walk(base, followlinks=False):
        for name in subdirs + names:
            path = Path(directory) / name
            entry = path.lstat()
            count += 1
            if count > MAX_PREVIEW_ENTRIES or entry.st_uid != os.geteuid() or entry.st_mode & 0o077:
                raise SafetyError('Private preview workspace entry limit or ownership violation')
            if stat.S_ISDIR(entry.st_mode):
                if path not in allowed_dirs:
                    raise SafetyError('Unexpected private preview directory')
            elif stat.S_ISREG(entry.st_mode) and entry.st_nlink == 1:
                if path not in allowed_files or entry.st_size > output_limits.get(path, MAX_PREVIEW_BYTES):
                    raise SafetyError('Unexpected or oversized private preview file')
                total += entry.st_size
                if total > MAX_PREVIEW_BYTES:
                    raise SafetyError('Private preview workspace byte limit exceeded')
            else:
                raise SafetyError('Unsafe private preview workspace entry')


def _compile_captured(context, records, catalog, snapshot, *, entrypoint, deadline_seconds=120.0,
                    cancel_event=None, expected_dependencies=None, expected_baseline_sha256=None,
                    profile='standard', starts=None, footnote_evidence=None,
                    partial_unsupported=False, withheld_records=None, partial_unproven_binding=False,
                    explicit_withheld_sources=(), conditional_continuations=False, conditional_adjacent_punctuation=False,
                    original_only=False, modern_highlight_evidence=None,
                    composed_modern_evidence=None, sequence_facade_evidence=False):
    """Compile baseline and candidate from explicit captured bytes in private.

    macOS sandbox-exec denies networking and writes outside the temporary
    workspace. The pinned Typst binary runs with system fonts disabled, the
    captured external font files first, then the captured project fonts. Each
    observed dependency must be captured and the two observed sets must match.
    PDF bytes are returned in memory; no destination or original source is
    written. This is a bounded preview, not proof of complete future reads or
    permission to publish/apply changes.
    """
    if (type(deadline_seconds) not in (int, float) or isinstance(deadline_seconds, bool)
            or not 0 < deadline_seconds <= 120):
        raise SafetyError('Preview compiler deadline must be in (0, 120] seconds')
    if cancel_event is not None and type(cancel_event) is not threading.Event:
        raise SafetyError('Preview cancellation requires a threading.Event')
    if expected_dependencies is not None:
        if (type(expected_dependencies) is not tuple or len(expected_dependencies) > document_resources.MAX_FILES
                or any(type(item) is not tuple or len(item) != 2 or any(type(x) is not str for x in item)
                       for item in expected_dependencies)
                or len(set(expected_dependencies)) != len(expected_dependencies)):
            raise SafetyError('Invalid expected preview dependency membership')
    if expected_baseline_sha256 is not None and (type(expected_baseline_sha256) is not str
            or re.fullmatch(r'[0-9a-f]{64}', expected_baseline_sha256) is None):
        raise SafetyError('Invalid expected preview baseline PDF hash')
    def cancelled():
        if cancel_event is not None and cancel_event.is_set():
            raise runtime.CompilerFailure('cancelled', kind=runtime.CompilerFailureKind.CANCELLED)
    cancelled()
    include_context.assert_include_context(context)
    document_resources.assert_capture(snapshot)
    if (type(entrypoint) is not str or len(context.entrypoints) != 1
            or context.entrypoints[0].path != entrypoint):
        raise SafetyError('Preview requires the captured single entrypoint')
    roots = dict(snapshot.roots)
    if not {'project', 'packages', 'cache'}.issubset(roots):
        raise SafetyError('Preview requires explicit project, packages and cache roots')
    if any(len(name) > 64 for name in roots):
        raise SafetyError('Preview resource root ID is too long')
    modern_summary = None
    composed_view = None
    if composed_modern_evidence is not None:
        import document_composed_modern_highlight as composed_modern
        import document_footnote_evidence as footnote_capture
        if (original_only is not False or modern_highlight_evidence is not None
                or type(composed_modern_evidence) is not footnote_capture.PreviewFootnoteEvidence
                or composed_modern_evidence._snapshot is not snapshot
                or composed_modern_evidence._session._context is not context
                or footnote_evidence is not None or type(sequence_facade_evidence) is not bool):
            raise SafetyError('Composed modern preview requires the matching issued resource capture')
        candidates, modern_summary, composed_view = composed_modern.stage(composed_modern_evidence, records,
            profile=profile, partial_unsupported=partial_unsupported, withheld_records=withheld_records,
            partial_unproven_binding=partial_unproven_binding, explicit_withheld_sources=explicit_withheld_sources,
            conditional_continuations=conditional_continuations,
            conditional_adjacent_punctuation=conditional_adjacent_punctuation,
            sequence_facade_evidence=sequence_facade_evidence)
        if not modern_summary['source_count']:
            raise NoComposedPreview(modern_summary)
    elif sequence_facade_evidence is not False:
        raise SafetyError('Sequence facade selection requires a composed modern preview')
    elif modern_highlight_evidence is not None:
        import document_modern_highlight_evidence as modern_evidence
        if (original_only is not False or type(modern_highlight_evidence) is not modern_evidence.CapturedModernHighlightEvidence
                or modern_highlight_evidence._snapshot is not snapshot
                or modern_highlight_evidence._analyzer._session._context is not context
                or records is not None or type(profile) is not str or profile != 'standard' or footnote_evidence is not None
                or partial_unsupported is not False or partial_unproven_binding is not False
                or withheld_records is not None or type(explicit_withheld_sources) is not tuple
                or explicit_withheld_sources or conditional_continuations is not False
                or conditional_adjacent_punctuation is not False):
            raise SafetyError('Modern highlight preview requires its own issued capture and exclusive profile')
        candidates, modern_summary = modern_evidence.stage_candidates(modern_highlight_evidence)
        if not modern_summary['candidate_count']:
            raise SafetyError('No verified modern highlight candidate edits')
    elif original_only:
        candidates = document_resources.stage_original(context, snapshot)
    else:
        candidates = document_resources.stage_document(context, records, catalog, snapshot, profile=profile,
                                                       footnote_evidence=footnote_evidence,
                                                       partial_unsupported=partial_unsupported,
                                                       withheld_records=withheld_records,
                                                       partial_unproven_binding=partial_unproven_binding,
                                                       explicit_withheld_sources=explicit_withheld_sources,
                                                       conditional_continuations=conditional_continuations,
                                                       conditional_adjacent_punctuation=conditional_adjacent_punctuation)
    cancelled()
    external_fonts = [item for item in snapshot.files
                      if item.request.role == 'font' and item.request.root != 'project']
    names = [Path(item.request.path).name for item in external_fonts]
    if len({unicodedata.normalize('NFC', name).casefold() for name in names}) != len(names):
        raise SafetyError('External font basenames collide in the private preview')
    for item in snapshot.files:
        extension = Path(item.request.path).suffix.lower()
        if item.request.role == 'font' and extension not in ('.ttf', '.otf', '.ttc'):
            raise SafetyError('Captured font must have a supported font-file extension')
        if item.request.root == 'project':
            if item.request.role == 'font' and not item.request.path.startswith('fonts/'):
                raise SafetyError('Project fonts must be captured under the fonts directory')
            if item.request.path.startswith('fonts/') and extension in ('.ttf', '.otf', '.ttc') and item.request.role != 'font':
                raise SafetyError('Every captured project font must be explicitly marked as a font')
    # Reserve space and a conservative entry count before creating any staged
    # files. External fonts are duplicated into each ordered font directory.
    candidate_total = sum(len(candidates[item.request.root, item.request.path]) for item in snapshot.files)
    copies = 1 if original_only else 2
    staged_bytes = (snapshot.total_bytes + (0 if original_only else candidate_total)
                    + copies * sum(item.size for item in external_fonts)
                    + runtime._MAX_EXECUTABLE + copies * (MAX_PDF_BYTES + MAX_DEPS_BYTES))
    parent_upper_bound = sum(max(0, len(item.request.path.split('/')) - 1) for item in snapshot.files)
    staged_entries = 8 + copies * (6 + len(roots) + len(snapshot.files) + len(external_fonts) + parent_upper_bound)
    if staged_bytes > MAX_PREVIEW_BYTES or staged_entries > MAX_PREVIEW_ENTRIES:
        raise SafetyError('Captured resources exceed the private preview workspace budget')
    compiler_record, compiler_bytes, compiler_evidence = runtime._capture_compiler()
    sandbox_bytes, sandbox_evidence = runtime._capture(SANDBOX, 2 * 1024 * 1024, executable=True)
    del sandbox_bytes  # The fixed system sandbox executable remains at its reviewed path.

    with tempfile.TemporaryDirectory(prefix='typst-document-preview-') as directory, ExitStack() as stack:
        base = Path(directory).resolve()
        sandbox_profile = _sandbox_profile(base)
        compiler = base / 'typst'
        runtime._write_private(compiler, compiler_bytes, 0o500)
        _, private_compiler = runtime._capture(compiler, runtime._MAX_EXECUTABLE, executable=True)
        if private_compiler.sha256 != compiler_evidence.sha256 or private_compiler.size != len(compiler_bytes):
            raise SafetyError('Private preview compiler differs from the pinned capture')
        workspaces = {}
        input_evidence = {compiler: private_compiler}
        labels = ('baseline',) if original_only else ('baseline', 'candidate')
        for label in labels:
            workspace = base / label
            _mkdir(workspace)
            staged_roots = {name: workspace / name for name in roots}
            for folder in staged_roots.values():
                _mkdir(folder)
            system_fonts = workspace / 'selected-system-fonts'
            _mkdir(system_fonts)
            project_fonts = staged_roots['project'] / 'fonts'
            _mkdir(project_fonts)
            for item in snapshot.files:
                cancelled()
                key = item.request.root, item.request.path
                raw = item.data if label == 'baseline' else candidates[key]
                target = staged_roots[item.request.root] / item.request.path
                _mkdir(target.parent)
                runtime._write_private(target, raw, 0o600)
                _, evidence = runtime._capture(target, document_resources.MAX_FILE_BYTES)
                if evidence.sha256 != digest(raw) or evidence.size != len(raw):
                    raise SafetyError('Private preview input differs from the captured bytes')
                input_evidence[target] = evidence
                if item in external_fonts:
                    target_font = system_fonts / Path(item.request.path).name
                    runtime._write_private(target_font, raw, 0o600)
                    _, evidence = runtime._capture(target_font, document_resources.MAX_FILE_BYTES)
                    if evidence.sha256 != digest(raw) or evidence.size != len(raw):
                        raise SafetyError('Private preview font differs from the captured bytes')
                    input_evidence[target_font] = evidence
            pdf, pdf_fd, pdf_identity = runtime._bind_output(workspace / 'output.pdf', stack)
            deps, deps_fd, deps_identity = runtime._bind_output(workspace / 'dependencies', stack)
            workspaces[label] = (workspace, staged_roots, system_fonts, project_fonts,
                                 pdf, pdf_fd, pdf_identity, deps, deps_fd, deps_identity)
        if modern_highlight_evidence is not None:
            modern_evidence._valid(modern_highlight_evidence)
        if composed_view is not None:
            import document_intermediate_context
            document_intermediate_context.assert_intermediate(composed_view)
            footnote_capture._valid(composed_modern_evidence)
        envbase = base / 'environment'
        _mkdir(envbase)
        for name in ('home', 'cache', 'config', 'data', 'tmp'):
            _mkdir(envbase / name)
        environment = runtime._environment(envbase)
        allowed_files = {path for path in base.rglob('*') if path.is_file()}
        allowed_dirs = {path for path in base.rglob('*') if path.is_dir()}
        output_limits = {}
        for data in workspaces.values():
            output_limits[data[4]] = MAX_PDF_BYTES
            output_limits[data[7]] = MAX_DEPS_BYTES
        last_check = [0.0]
        def check_workspace(force=False):
            if force or time.monotonic() - last_check[0] >= 1.0:
                _workspace_guard(base, allowed_files, allowed_dirs, output_limits)
                last_check[0] = time.monotonic()

        probe = _run_tracked([str(SANDBOX), '-p', sandbox_profile, str(compiler), '--version'], starts=starts,
                             purpose='document-preview-version', cwd=base, environment=environment,
                             deadline=time.monotonic() + min(float(deadline_seconds), 10.0),
                             cancel_event=cancel_event, check_workspace=check_workspace)
        if probe.stdout != compiler_record.version or probe.stderr:
            raise SafetyError('Private preview compiler version does not match the pinned binary')
        outputs = {}
        for label in labels:
            workspace, staged, system_fonts, project_fonts, pdf, pdf_fd, pdf_id, deps, deps_fd, deps_id = workspaces[label]
            def verify_outputs():
                check_workspace(force=True)
                for path, descriptor, identity in ((pdf, pdf_fd, pdf_id), (deps, deps_fd, deps_id)):
                    if (runtime._output_identity(os.fstat(descriptor)) != identity
                            or runtime._output_identity(path.lstat()) != identity):
                        raise SafetyError('Private preview output identity changed')
            command = [str(SANDBOX), '-p', sandbox_profile, str(compiler), 'compile',
                       '--root', str(staged['project']), '--package-path', str(staged['packages']),
                       '--package-cache-path', str(staged['cache']), '--ignore-system-fonts',
                       '--font-path', str(system_fonts) + os.pathsep + str(project_fonts),
                       '--creation-timestamp', '1700000000', '--deps', str(deps),
                       '--deps-format', 'zero', '--', entrypoint, str(pdf)]
            try:
                invocation = _run_tracked(command, starts=starts, purpose=f'document-preview-{label}', cwd=staged['project'],
                                          environment=environment, deadline=time.monotonic() + deadline_seconds,
                                          cancel_event=cancel_event, check_workspace=check_workspace)
            except runtime.CompilerFailure as error:
                # _run cleans up before returning this failure. If launch was
                # possible, refuse unsafe output before exposing its stderr.
                # A proven prelaunch cancellation has not touched outputs.
                if error.invocation is None or error.invocation.started is not False:
                    verify_outputs()
                raise
            verify_outputs()
            if invocation.stderr:
                raise PreviewCompilerDiagnostics(label, invocation)
            pdf_bytes, _ = runtime._capture(pdf, MAX_PDF_BYTES)
            dep_bytes, _ = runtime._capture(deps, MAX_DEPS_BYTES)
            runtime._pdf(pdf_bytes)
            if (label == 'baseline' and expected_baseline_sha256 is not None
                    and digest(pdf_bytes) != expected_baseline_sha256):
                raise SafetyError('Captured baseline PDF differs from dependency discovery')
            comparison = document_dependencies.compare(snapshot, dep_bytes, cwd=str(staged['project']),
                                                       roots={name: str(path) for name, path in staged.items()})
            if comparison.missing:
                raise SafetyError('Compiler observed resources outside the explicit capture')
            if (label == 'baseline' and expected_dependencies is not None
                    and set(comparison.observed) != set(expected_dependencies)):
                raise SafetyError('Captured baseline dependencies differ from discovery')
            outputs[label] = pdf_bytes, comparison
        if not original_only and set(outputs['baseline'][1].observed) != set(outputs['candidate'][1].observed):
            raise SafetyError('Candidate compiler dependency membership changed')
        for path, evidence in input_evidence.items():
            runtime._same(path, evidence, runtime._MAX_EXECUTABLE if path == compiler else document_resources.MAX_FILE_BYTES,
                          executable=path == compiler)
        runtime._same(compiler_record.path, compiler_evidence, runtime._MAX_EXECUTABLE, executable=True)
        runtime._same(SANDBOX, sandbox_evidence, 2 * 1024 * 1024, executable=True)
        include_context.assert_include_context(context)
        document_resources.assert_capture(snapshot)
        if modern_highlight_evidence is not None:
            modern_evidence._valid(modern_highlight_evidence)
        if composed_view is not None:
            document_intermediate_context.assert_intermediate(composed_view)
            footnote_capture._valid(composed_modern_evidence)
        check_workspace(force=True)
        cancelled()
        if original_only:
            pdf, comparison = outputs['baseline']
            return OriginalCompileResult(digest(pdf), len(comparison.observed), len(comparison.unobserved),
                                         compiler_evidence.sha256, len(external_fonts), pdf)
        before, after = outputs['baseline'][0], outputs['candidate'][0]
        return PreviewCompileResult(digest(before), digest(after), before == after,
                                    len(outputs['candidate'][1].observed),
                                    len(outputs['candidate'][1].unobserved), compiler_evidence.sha256,
                                    len(external_fonts), after, modern_highlight_evidence=modern_summary)


def compile_preview(context, records, catalog, snapshot, *, entrypoint, deadline_seconds=120.0,
                    cancel_event=None, expected_dependencies=None, expected_baseline_sha256=None,
                    profile='standard', starts=None, footnote_evidence=None,
                    partial_unsupported=False, withheld_records=None, partial_unproven_binding=False,
                    explicit_withheld_sources=(), conditional_continuations=False, conditional_adjacent_punctuation=False,
                    modern_highlight_evidence=None, composed_modern_evidence=None,
                    sequence_facade_evidence=False):
    return _compile_captured(context, records, catalog, snapshot, entrypoint=entrypoint,
        deadline_seconds=deadline_seconds, cancel_event=cancel_event, expected_dependencies=expected_dependencies,
        expected_baseline_sha256=expected_baseline_sha256, profile=profile, starts=starts,
        footnote_evidence=footnote_evidence, partial_unsupported=partial_unsupported,
        withheld_records=withheld_records, partial_unproven_binding=partial_unproven_binding,
        explicit_withheld_sources=explicit_withheld_sources, conditional_continuations=conditional_continuations,
        conditional_adjacent_punctuation=conditional_adjacent_punctuation,
        modern_highlight_evidence=modern_highlight_evidence,
        composed_modern_evidence=composed_modern_evidence,
        sequence_facade_evidence=sequence_facade_evidence)


def compile_original(context, snapshot, *, entrypoint, deadline_seconds=120.0, cancel_event=None,
                     expected_dependencies=None, expected_baseline_sha256=None, starts=None):
    """Compile only the captured original once; no lint or source edits are performed."""
    return _compile_captured(context, None, None, snapshot, entrypoint=entrypoint,
        deadline_seconds=deadline_seconds, cancel_event=cancel_event,
        expected_dependencies=expected_dependencies, expected_baseline_sha256=expected_baseline_sha256,
        starts=starts, original_only=True)
