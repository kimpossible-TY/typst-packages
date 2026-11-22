"""Distinct committed paragraph/package compiler, with no ambient package loader.

Only retained root and exact text-utils 0.1.2 bytes are staged. The trusted
Python runtime, pinned native compiler and OS are not adversarial sandboxes.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, field, fields, replace
import math
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from types import MappingProxyType
import unicodedata
import weakref

import captured_text_utils_paragraph_core as core
from captured_text_utils_paragraph_fix import (CapturedTextUtilsParagraphCommittedSnapshot,
                                               ProtectedCapturedTextUtilsParagraphFix)
import closed_core_compile as runtime
from closed_core_compile import (CompileSafetyError, CompilerFailure, CompilerFailureKind,
    CompileReport, CompilerFailureInfo, FileEvidence, FontIdentity, FontEvidence,
    ResourceReport, Invocation, UnsupportedCompileProfile)
from resource_profiles import (ASCII_RESOURCE_PROFILE, REPERTOIRE_VERSIONS, validate_resource_profile,
                               validate_source_repertoire)
from transactions import TransactionError

MARKER_RENDER_POLICY = 'text-utils-0.1.2-private-marker-removal-v1'
_PACKAGE_LEAF = 'packages/local/text-utils/0.1.2'


@dataclass(frozen=True, slots=True)
class MarkerRenderPolicy:
    """Separate reviewed generated-marker behavior, not raw ASCII inference."""
    policy: str
    private_codepoint: int
    marker_removed: bool
    horizontal_spacing: str
    package_sha256: str
    invoked_body_sha256: str


@dataclass(frozen=True, slots=True)
class ParagraphStagedSource:
    path: str
    sha256: str
    size: int
    private: FileEvidence


@dataclass(frozen=True, slots=True)
class DirectoryEvidence:
    """Actual private directory identity, not a later filesystem lookup."""
    path: str
    identity: tuple[int, ...]
    parents: tuple[tuple[str, tuple[int, ...]], ...]


@dataclass(frozen=True, slots=True)
class _Source:
    path: str
    raw: bytes = field(repr=False)
    sha256: str


@dataclass(frozen=True, slots=True)
class _Manifest:
    sources: tuple[_Source, ...]
    directories: tuple[str, ...]
    aggregate_source_bytes: int


_demand = runtime._demand
_hash = runtime._hash


def _guard(fix, snapshot):
    _demand(type(fix) is ProtectedCapturedTextUtilsParagraphFix
            and type(snapshot) is CapturedTextUtilsParagraphCommittedSnapshot,
            'A live paragraph bridge-owned committed snapshot is required')
    try:
        fix.assert_committed_current(snapshot)
    except (TransactionError, AttributeError, TypeError, ValueError) as error:
        raise CompileSafetyError(f'Committed paragraph authority is stale: {error}') from error


def _canonical(path):
    try:
        return core.canonical_path(path)
    except core.CapturedTextUtilsParagraphError as error:
        raise CompileSafetyError(f'Invalid committed source path: {error}') from error


def _manifest(snapshot):
    """Plan one unchanged root plus two fixed retained package files."""
    _canonical(snapshot.target)
    _demand(type(snapshot.sources) is MappingProxyType
            and tuple(snapshot.sources) == (snapshot.target,)
            and type(snapshot.sources[snapshot.target]) is bytes
            and snapshot.tree.source == snapshot.sources[snapshot.target],
            'Invalid committed paragraph source membership')
    _demand(snapshot.source_profile == core.SOURCE_PROFILE
            and snapshot.grammar_version == core.GRAMMAR_VERSION
            and snapshot.parser_contract == core.PARSER_CONTRACT,
            'Committed paragraph grammar/parser selection changed')
    sources = (_Source('project/' + snapshot.target, snapshot.sources[snapshot.target],
                       _hash(snapshot.sources[snapshot.target])),
               _Source(_PACKAGE_LEAF + '/typst.toml', snapshot.manifest, _hash(snapshot.manifest)),
               _Source(_PACKAGE_LEAF + '/lib.typ', snapshot.package.source, _hash(snapshot.package.source)))
    directories = set()
    total = 0
    for source in sources:
        _demand(type(source.raw) is bytes and len(source.raw) <= 1024 * 1024,
                'Committed paragraph/package source byte limit exceeded')
        total += len(source.raw)
        parts = source.path.split('/')[:-1]
        directories.update('/'.join(parts[:i]) for i in range(1, len(parts) + 1))
    _demand(total <= 4 * 1024 * 1024 and len(directories) <= 64,
            'Committed paragraph source/directory budget exceeded')
    names = {source.path for source in sources}
    _demand(len(names) == 3 and not names & directories,
            'Committed paragraph file/directory collision')
    folded = {}
    for name in sorted(names | directories):
        key = unicodedata.normalize('NFD', name).casefold()
        _demand(key not in folded or folded[key] == name,
                'Case/Unicode collision in committed paragraph layout')
        folded[key] = name
    return _Manifest(sources, tuple(sorted(directories, key=lambda path: (path.count('/'), path))), total)


def _marker_policy(snapshot):
    """Bind F000 elimination separately from complete raw-source repertoire.

    The exact reviewed helper closure replaces its private marker with h(1.5em).
    Its uppercase operation sees only admitted literal ASCII first letters. This
    is not general generated-Unicode fidelity or arbitrary helper-call authority.
    """
    package = snapshot.proof.package
    _demand(type(package) is core.PackageIdentity
            and package.import_path == core.IMPORT_PATH
            and package.source_sha256 == core.SOURCE_SHA256
            and package.manifest_sha256 == core.MANIFEST_SHA256,
            'Reviewed paragraph marker helper identity changed')
    start, end = package.invoked_body_span
    body = snapshot.package.source[start:end]
    _demand(_hash(body) == package.invoked_body_sha256
            and b'show regex("\\u{F000}"): h(1.5em)' in body
            and b'h(1.5em) + upper(char)' in body,
            'Reviewed private marker removal behavior changed')
    return MarkerRenderPolicy(MARKER_RENDER_POLICY, 0xF000, True, '1.5em',
                              package.source_sha256, package.invoked_body_sha256)


def _dependency_paths(raw, expected):
    """Compare every decoded record before traversing any observed path.

    The pinned compiler emits the literal relative root and absolute staged
    package lib + manifest. Alternate or normalized spellings never gain access.
    """
    _demand(type(expected) is tuple and len(expected) == 3
            and all(type(path) is str and path for path in expected)
            and len(set(expected)) == 3, 'Invalid exact paragraph dependency plan')
    _demand(type(raw) is bytes and 0 < len(raw) <= runtime._MAX_DEPENDENCIES
            and raw.endswith(b'\0') and raw.count(b'\0') == 3,
            'Malformed compiler package dependency evidence')
    try:
        paths = tuple(raw[:-1].decode('utf-8', errors='strict').split('\0'))
    except UnicodeError as error:
        raise CompileSafetyError('Compiler package dependency evidence is not UTF-8') from error
    _demand(len(paths) == 3 and all(path in expected for path in paths)
            and len(set(paths)) == 3 and set(paths) == set(expected),
            'Compiler dependencies differ from the exact root/package/manifest')
    return paths


def _directory_capture(path):
    with runtime._anchored_parent(path / '.directory-identity') as (descriptor, parents, check):
        info = os.fstat(descriptor)
        check()
        return DirectoryEvidence(str(path), runtime._directory_identity(info), parents)


def _mkdir_private(path):
    # Never use exist_ok=True: distinct logical spellings must not merge on a
    # case-insensitive or normalization-insensitive destination filesystem.
    with runtime._anchored_parent(path) as (parent, _, check):
        os.mkdir(path.name, mode=0o700, dir_fd=parent)
        descriptor = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                             | os.O_CLOEXEC, dir_fd=parent)
        try:
            os.fchmod(descriptor, 0o700)
            _demand(runtime._directory_identity(os.fstat(descriptor))
                    == runtime._directory_identity(os.stat(path.name, dir_fd=parent,
                                                           follow_symlinks=False)),
                    'New private directory identity changed')
            check()
        finally:
            os.close(descriptor)
    return _directory_capture(path)


@dataclass(frozen=True, slots=True, init=False, eq=False, weakref_slot=True)
class CapturedTextUtilsParagraphCompileArtifact:
    """Sealed paragraph-only output; every ordinary PDF read needs live authority."""
    profile: str
    source_profile: str
    grammar_version: str
    parser_contract: tuple[str, ...]
    target: str
    source_sha256: str
    aggregate_source_bytes: int
    root_source: ParagraphStagedSource
    package_sources: tuple[ParagraphStagedSource, ...]
    package_directories: tuple[DirectoryEvidence, ...]
    workspace_directories: tuple[DirectoryEvidence, ...]
    config_sha256: str
    catalog_sha256: str
    evidence_hashes: tuple[tuple[str, str | None], ...]
    compiler: FileEvidence
    private_compiler: FileEvidence
    project_directories: tuple[DirectoryEvidence, ...]
    output: FileEvidence
    dependencies: FileEvidence
    dependency_bytes: bytes
    dependency_paths: tuple[str, ...]
    probes: tuple[Invocation, ...]
    compilation: Invocation
    report: CompileReport
    resource_report: ResourceReport
    marker_policy: MarkerRenderPolicy
    pdf_sha256: str
    _pdf_bytes: bytes = field(repr=False)

    def __init__(self, *args, **kwargs):
        raise TypeError('Artifacts are issued only by compile_committed_snapshot')

    def __getattribute__(self, name):
        if name == '_pdf_bytes':
            record = _ARTIFACTS.get(self)
            _demand(record is not None and record[0]() is not None, 'Unissued or expired paragraph artifact')
            assert_artifact_current(record[0](), self)
        return object.__getattribute__(self, name)

    @property
    def pdf_bytes(self):
        return self._pdf_bytes


_ARTIFACTS = weakref.WeakKeyDictionary()
_RECORD_TYPES = (_Source, _Manifest, CapturedTextUtilsParagraphCompileArtifact, ParagraphStagedSource, DirectoryEvidence,
                 MarkerRenderPolicy, FileEvidence, FontIdentity, FontEvidence,
                 ResourceReport, Invocation, CompileReport, CompilerFailureInfo)


def _retain(value):
    """Only issuer-generated bounded data is copied, once, before exposure."""
    kind = type(value)
    if kind in (str, bytes, int, bool, type(None)):
        return kind, value
    if kind is CompilerFailureKind:
        return kind, value
    if kind is tuple:
        return kind, tuple(_retain(item) for item in value)
    _demand(kind in _RECORD_TYPES, 'Invalid issued paragraph artifact record type')
    return kind, tuple((item.name, _retain(object.__getattribute__(value, item.name)))
                       for item in fields(kind))


def _matches(value, retained):
    """Follow the original finite shape, never a reflectively altered artifact."""
    kind, expected = retained
    if type(value) is not kind:
        return False
    if kind in (str, bytes):
        return len(value) == len(expected) and value == expected
    if kind in (int, bool, type(None)):
        return value == expected
    if kind is CompilerFailureKind:
        return value is expected
    if kind is tuple:
        return len(value) == len(expected) and all(_matches(item, fact)
                                                    for item, fact in zip(value, expected))
    try:
        return all(_matches(object.__getattribute__(value, name), fact) for name, fact in expected)
    except AttributeError:
        return False


def _restore(retained):
    """Rebuild historical report data only from independent original facts."""
    kind, expected = retained
    if kind in (str, bytes, int, bool, type(None), CompilerFailureKind):
        return expected
    if kind is tuple:
        return tuple(_restore(item) for item in expected)
    instance = object.__new__(kind)
    for name, fact in expected:
        object.__setattr__(instance, name, _restore(fact))
    return instance


class _ParagraphAttemptLedger(runtime._AttemptLedger):
    """Keep actual resource reporting honest if a shared record is altered."""
    def __init__(self):
        self._resource_report = self._resource_report_original = None
        self._resource_report_facts = None
        super().__init__()

    @property
    def resource_report(self):
        return self._resource_report

    @resource_report.setter
    def resource_report(self, value):
        self._resource_report = value
        # Base initialization sets None. Only the first complete actual capture
        # establishes historical facts; later assignment cannot refresh them.
        if self._resource_report_facts is None and value is not None:
            self._resource_report_original = value
            self._resource_report_facts = _retain(value)

    def assert_resources_unchanged(self):
        _demand(self._resource_report is self._resource_report_original
                and (self._resource_report_facts is None
                     or _matches(self._resource_report, self._resource_report_facts)),
                'Actual compiler resource report changed')

    def snapshot(self, safety_gate):
        report = super().snapshot(safety_gate)
        resource = (None if self._resource_report_facts is None
                    else _restore(self._resource_report_facts))
        return replace(report, resource_report=resource)


def assert_artifact_current(fix, artifact):
    _demand(type(artifact) is CapturedTextUtilsParagraphCompileArtifact, 'Unissued paragraph artifact type')
    record = _ARTIFACTS.get(artifact)
    _demand(record is not None and record[0]() is fix and _matches(artifact, record[2]),
            'Unissued, altered, or foreign-session paragraph artifact')
    _guard(fix, record[1])
    runtime._same(Path(artifact.compiler.path), artifact.compiler,
                  runtime._MAX_EXECUTABLE, executable=True)
    for font in artifact.resource_report.fonts:
        runtime._same(Path(font.installed.path), font.installed, runtime._MAX_FONT)
    _demand(_hash(object.__getattribute__(artifact, '_pdf_bytes')) == artifact.pdf_sha256,
            'Graph artifact PDF bytes changed')
    _guard(fix, record[1])


def compile_committed_snapshot(fix, snapshot, *, profile, deadline_seconds=10.0, cancel_event=None):
    """Compile exactly one original live committed paragraph snapshot, without publication."""
    ledger = _ParagraphAttemptLedger()
    try:
        return _compile_committed_snapshot(fix, snapshot, profile=profile,
            deadline_seconds=deadline_seconds, cancel_event=cancel_event, _ledger=ledger)
    except BaseException as failure:
        ledger.note_failure(failure)
        if isinstance(failure, (CompilerFailure, CompileSafetyError, KeyboardInterrupt)):
            failure.report = ledger.snapshot('refused' if isinstance(failure, CompileSafetyError)
                                             else 'passed' if isinstance(failure, CompilerFailure)
                                             else 'pending')
        raise


def _compile_committed_snapshot(fix, snapshot, *, profile, deadline_seconds, cancel_event, _ledger):
    try:
        validate_resource_profile(profile)
        if profile != ASCII_RESOURCE_PROFILE:
            raise ValueError('Paragraph compile supports only embedded-ascii-v1')
    except ValueError as error:
        raise UnsupportedCompileProfile(str(error)) from error
    if (type(deadline_seconds) not in (int, float) or not math.isfinite(deadline_seconds)
            or not 0 < deadline_seconds <= 120
            or (cancel_event is not None and type(cancel_event) is not threading.Event)):
        raise UnsupportedCompileProfile('Invalid bounded deadline or cancellation event')
    deadline = time.monotonic() + deadline_seconds
    _guard(fix, snapshot)
    manifest = _manifest(snapshot)
    try:
        for source in manifest.sources:
            validate_source_repertoire(profile, source.raw)
    except ValueError as error:
        raise UnsupportedCompileProfile(str(error)) from error
    marker_policy = _marker_policy(snapshot)
    last_invocation = None
    retained = []

    def pin(value):
        retained.append((value, _retain(value)))
        return value

    def check_retained():
        _ledger.assert_resources_unchanged()
        _demand(all(_matches(value, facts) for value, facts in retained),
                'Retained paragraph compiler facts changed')

    pin(manifest)
    pin(marker_policy)

    def check_request():
        if cancel_event is not None and cancel_event.is_set():
            raise CompilerFailure('cancelled', last_invocation, kind=CompilerFailureKind.CANCELLED)
        if time.monotonic() >= deadline:
            raise CompilerFailure('timeout', last_invocation, kind=CompilerFailureKind.TIMEOUT)

    compiler, font_captures = None, ()
    issued = False
    try:
        check_request()
        _guard(fix, snapshot)
        record, compiler_bytes, compiler = runtime._capture_compiler()
        pin(compiler)
        # Copy immutable trust facts now, before any later callback. Installed
        # checks use the retained actual capture path, never a refreshed record.
        expected_version = record.version
        _demand(type(expected_version) is bytes, 'Invalid reviewed compiler version')
        font_captures = tuple((font.face_index, raw, installed, font.identity)
                              for font, raw, installed in runtime._capture_fonts(profile, compiler))
        _demand(not font_captures, 'ASCII paragraph resources must not capture fonts')
        for face_index, _, installed, identity in font_captures:
            _demand(type(face_index) is int and face_index == 0, 'Invalid reviewed font face')
            pin(installed)
            pin(identity)
        check_retained()
        check_request()
        with tempfile.TemporaryDirectory(prefix='typst-captured-paragraph-') as temporary, ExitStack() as outputs:
            base = Path(temporary).resolve()
            os.chmod(base, 0o700)
            project = base / 'project'
            logical_directories = {'bin', 'project', 'out', 'home', 'cache', 'config',
                                   'data', 'tmp', 'packages', 'package-cache'}
            logical_directories.update(manifest.directories)
            if font_captures:
                logical_directories.add('fonts')
            directories = {base / path for path in logical_directories}
            directory_records = [pin(_directory_capture(base))]
            executable = base / 'bin' / 'typst'
            pdf, deps = base / 'out' / 'artifact.pdf', base / 'out' / 'dependencies.zero'
            input_path = project / snapshot.target
            bound_outputs = []
            allowed = {executable, pdf, deps, *(base / source.path for source in manifest.sources)}
            if font_captures:
                allowed.add(base / 'fonts' / 'AppleGothic.ttf')
            staged_sources, font_evidence = [], []
            private_compiler = None
            output = dependencies = None

            def check_original():
                check_retained()
                _guard(fix, snapshot)
                runtime._same(Path(compiler.path), compiler, runtime._MAX_EXECUTABLE, executable=True)
                for _, _, installed, _ in font_captures:
                    runtime._same(Path(installed.path), installed, runtime._MAX_FONT)
                _guard(fix, snapshot)

            def check_workspace():
                # This is also the runner's in-flight callback: drift in live
                # source/evidence/resources must stop a still-running child.
                check_original()
                identities = set()
                for evidence in directory_records:
                    _demand(_directory_capture(Path(evidence.path)) == evidence,
                            'Private workspace directory identity changed')
                    _demand(evidence.identity[:2] not in identities, 'Private directory inode alias')
                    identities.add(evidence.identity[:2])
                for path, descriptor, expected in bound_outputs:
                    _demand(runtime._output_identity(os.fstat(descriptor)) == expected
                            == runtime._output_identity(path.lstat()), 'Bound compiler output was replaced')
                runtime._workspace_check(base, allowed, directories, pdf)
                if private_compiler is not None:
                    runtime._same(executable, private_compiler, runtime._MAX_EXECUTABLE, executable=True)
                leaf_identities = set()
                for path in allowed:
                    try:
                        info = path.lstat()
                    except FileNotFoundError:
                        continue
                    identity = info.st_dev, info.st_ino
                    _demand(identity not in leaf_identities and identity not in identities,
                            'Distinct private names share an inode')
                    leaf_identities.add(identity)
                for source in staged_sources:
                    runtime._same(Path(source.private.path), source.private, 1024 * 1024)
                for font in font_evidence:
                    runtime._same(Path(font.private.path), font.private, runtime._MAX_FONT)
                if any(path == deps for path, _, _ in bound_outputs):
                    _demand(deps.lstat().st_size <= runtime._MAX_DEPENDENCIES,
                            'Dependency output byte limit exceeded')

            def revalidate():
                check_workspace()
                if output is not None:
                    runtime._same(pdf, output, runtime._MAX_PDF)
                if dependencies is not None:
                    runtime._same(deps, dependencies, runtime._MAX_DEPENDENCIES)
                _guard(fix, snapshot)

            try:
                seen_directories = {directory_records[0].identity[:2]}
                for path in sorted(directories, key=lambda path: (len(path.parts), str(path))):
                    evidence = pin(_mkdir_private(path))
                    _demand(evidence.identity[:2] not in seen_directories,
                            'Distinct private directory names share an inode')
                    seen_directories.add(evidence.identity[:2])
                    directory_records.append(evidence)
                    check_workspace()
                    check_request()
                _demand(len(directory_records) == len(directories) + 1,
                        'Private directory membership changed')
                _guard(fix, snapshot)
                for path in (pdf, deps):
                    bound_outputs.append(runtime._bind_output(path, outputs))
                    check_workspace()
                runtime._write_private(executable, compiler_bytes, 0o500)
                _, private_compiler = runtime._capture(executable, runtime._MAX_EXECUTABLE, executable=True)
                pin(private_compiler)
                _demand(private_compiler.sha256 == compiler.sha256, 'Private compiler copy differs')
                for source in manifest.sources:
                    runtime._write_private(base / source.path, source.raw, 0o400)
                    raw, private = runtime._capture(base / source.path, 1024 * 1024)
                    _demand(raw == source.raw and private.sha256 == source.sha256,
                            'Private committed source copy differs')
                    staged_sources.append(pin(ParagraphStagedSource(source.path, source.sha256,
                                                                    len(source.raw), private)))
                    revalidate()
                    check_request()
                for face_index, font_bytes, installed, identity in font_captures:
                    private_path = base / 'fonts' / 'AppleGothic.ttf'
                    runtime._write_private(private_path, font_bytes, 0o400)
                    _, private = runtime._capture(private_path, runtime._MAX_FONT)
                    _demand(private.size == installed.size and private.sha256 == installed.sha256,
                            'Private font copy differs')
                    font_evidence.append(pin(FontEvidence(face_index, installed, private, identity)))
                    revalidate()
                resource_report = ResourceReport(profile, REPERTOIRE_VERSIONS[profile], tuple(font_evidence))
                pin(resource_report)
                _ledger.resource_report = resource_report
                environment = runtime._environment(base)
                revalidate()
                check_request()
                probe = runtime._run((str(executable), '--version'), purpose='version-probe',
                    cwd=project, environment=environment, deadline=deadline,
                    cancel_event=cancel_event, check_workspace=check_workspace, _ledger=_ledger)
                observed = _ledger.snapshot('pending').attempts
                _demand(len(observed) == 1 and _matches(probe, _retain(observed[0])),
                        'Returned probe metadata differs from the actual attempt ledger')
                pin(probe)
                last_invocation = probe
                _demand(probe.stdout == expected_version and not probe.stderr, 'Compiler version mismatch')
                revalidate()
                argv = (str(executable), 'compile', '--format', 'pdf', '--root', str(project),
                    '--ignore-system-fonts', '--package-path', str(base / 'packages'),
                    '--package-cache-path', str(base / 'package-cache'), '--creation-timestamp', '0',
                    '--jobs', '1', '--diagnostic-format', 'short', '--deps', str(deps),
                    '--deps-format', 'zero')
                if font_evidence:
                    argv += ('--font-path', str(base / 'fonts'))
                argv += (str(input_path), str(pdf))
                compilation = runtime._run(argv, purpose='compile', cwd=project,
                    environment=environment, deadline=deadline, cancel_event=cancel_event,
                    check_workspace=check_workspace, _ledger=_ledger)
                observed = _ledger.snapshot('pending').attempts
                _demand(len(observed) == 2 and _matches(compilation, _retain(observed[1])),
                        'Returned compile metadata differs from the actual attempt ledger')
                pin(compilation)
                last_invocation = compilation
                revalidate()
                pdf_bytes, output = runtime._capture(pdf, runtime._MAX_PDF)
                pin(output)
                runtime._pdf(pdf_bytes)
                dep_bytes, dependencies = runtime._capture(deps, runtime._MAX_DEPENDENCIES)
                pin(dependencies)
                dependency_paths = _dependency_paths(dep_bytes, (snapshot.target,
                    str(base / _PACKAGE_LEAF / 'lib.typ'), str(base / _PACKAGE_LEAF / 'typst.toml')))
                revalidate()
                check_request()
                report = pin(_ledger.snapshot('passed'))
                _demand(_matches((probe, compilation), _retain(report.attempts)),
                        'Invocation ledger/returned metadata disagree')
                values = dict(profile=profile, source_profile=snapshot.source_profile,
                    grammar_version=snapshot.grammar_version,
                    parser_contract=snapshot.parser_contract, target=snapshot.target,
                    source_sha256=manifest.sources[0].sha256,
                    aggregate_source_bytes=manifest.aggregate_source_bytes, root_source=staged_sources[0],
                    package_sources=tuple(staged_sources[1:]),
                    package_directories=tuple(e for e in directory_records
                                              if Path(e.path) == base / 'packages'
                                              or base / 'packages' in Path(e.path).parents),
                    workspace_directories=tuple(directory_records),
                    config_sha256=snapshot.config_sha256, catalog_sha256=snapshot.catalog_sha256,
                    evidence_hashes=tuple(sorted((path, None if data is None else _hash(data))
                                               for path, data in snapshot.evidence.items())),
                    compiler=compiler, private_compiler=private_compiler,
                    project_directories=tuple(evidence for evidence in directory_records
                                              if Path(evidence.path) == project
                                              or project in Path(evidence.path).parents),
                    output=output, dependencies=dependencies, dependency_bytes=dep_bytes,
                    dependency_paths=dependency_paths, probes=(probe,), compilation=compilation,
                    report=report, resource_report=resource_report,
                    marker_policy=marker_policy, pdf_sha256=_hash(pdf_bytes), _pdf_bytes=pdf_bytes)
                artifact = object.__new__(CapturedTextUtilsParagraphCompileArtifact)
                for item in fields(CapturedTextUtilsParagraphCompileArtifact):
                    object.__setattr__(artifact, item.name, values[item.name])
                artifact_facts = _retain(artifact)
            except BaseException as failure:
                _ledger.note_failure(failure)
                raise
            finally:
                # Original resource/source drift supersedes process failure,
                # retaining the original attempts and typed failure in reports.
                revalidate()
        _guard(fix, snapshot)
        check_retained()
        runtime._same(Path(compiler.path), compiler, runtime._MAX_EXECUTABLE, executable=True)
        for font in font_evidence:
            runtime._same(Path(font.installed.path), font.installed, runtime._MAX_FONT)
        _guard(fix, snapshot)
        check_request()
        _demand(_matches(artifact, artifact_facts), 'Paragraph artifact changed before issuance')
        _ARTIFACTS[artifact] = (weakref.ref(fix), snapshot, artifact_facts)
        issued = True
        return artifact
    except (CompileSafetyError, UnsupportedCompileProfile, CompilerFailure) as failure:
        _ledger.note_failure(failure)
        raise
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        raise CompileSafetyError(f'Isolated paragraph compiler unavailable or unsafe: {error}') from error
    finally:
        # This also covers resource/staging failures before the complete private
        # workspace protocol exists. No later read becomes a new baseline.
        if compiler is not None:
            check_retained()
            _guard(fix, snapshot)
            runtime._same(Path(compiler.path), compiler, runtime._MAX_EXECUTABLE, executable=True)
            for _, _, installed, _ in font_captures:
                runtime._same(Path(installed.path), installed, runtime._MAX_FONT)
            _guard(fix, snapshot)
            if issued:
                check_request()
