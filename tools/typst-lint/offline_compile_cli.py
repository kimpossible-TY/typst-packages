"""Explicit offline repair, private compilation and journaled PDF publication.

The old single-source selections and separate captured-relative graph selection
use their own source and publication engines. No serialized report is authority.
There are no compiler/store/limit overrides.
The only process that may signal itself is the executable entrypoint below.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import re
import signal
import sys
import threading

import closed_core_compile as compiler
from captured_relative_fix import ProtectedCapturedRelativeFix
from captured_relative_publish import PreparedCapturedRelativePdfPublication
from captured_text_utils_paragraph_fix import ProtectedCapturedTextUtilsParagraphFix
from captured_text_utils_paragraph_publish import PreparedCapturedTextUtilsParagraphPdfPublication
from closed_core_publish import PreparedPdfPublication
from fixed_journal import fixed_journal_directory, journal_inventory, recovery_report_json
import offline_cli as offline
from pdf_publication_store import (OfflineOutputExclusive, PublicationError,
    PublicationPending, PublicationReport, inventory_publications, literal_destination, recover_publication)
from protected_fix import ProtectedCoreFix, _literal_target
from resource_profiles import (APPLEGOTHIC_RESOURCE_PROFILE, ASCII_RESOURCE_PROFILE,
                               validate_resource_profile)
from source_profiles import (CAPTURED_RELATIVE_SOURCE_PROFILE,
    CAPTURED_TEXT_UTILS_PARAGRAPH_SOURCE_PROFILE, is_offline_source_profile)
from transactions import OfflineExclusive, RecoveryRequired, TransactionFailure
from typst_lint import InputError, SafetyError


# Scoped only by executable_main. Imported main() never changes signal handlers.
_active_signals = None
_KOREAN_PROFILE_LIMITATION = (
    'Korean strong/emph remains regular upright; headings gain size but not a Korean bold face. '
    'This profile does not establish styled Korean fidelity.')


def validate_arguments(args, parser):
    """Validate every combination and literal before root access or lease minting."""
    if args.command == 'output-status':
        if (args.profile is not None or args.resource_profile is not None
                or args.offline_exclusive or args.offline_output_exclusive
                or args.target is not None or args.output is not None or args.journal_id is not None):
            parser.error('output-status accepts only --root and --format')
        return
    if args.output is None:
        parser.error('--output is required')
    try:
        literal_destination(args.output)
    except (PublicationError, ValueError, OSError) as error:
        parser.error(str(error))
    if not args.offline_output_exclusive:
        parser.error('--offline-output-exclusive is required; all output, ancestor and storage writers must remain stopped')
    if args.command == 'recover-output':
        if (args.profile is not None or args.resource_profile is not None
                or args.offline_exclusive or args.target is not None):
            parser.error('recover-output accepts no source assertion, target or profile options')
        if args.journal_id is None or re.fullmatch('[0-9a-f]{32}', args.journal_id) is None:
            parser.error('recover-output requires --journal-id with exactly 32 lowercase hexadecimal characters')
        return
    if not is_offline_source_profile(args.profile):
        parser.error('compile requires an explicit known --profile')
    try:
        validate_resource_profile(args.resource_profile)
    except ValueError:
        parser.error('compile requires an explicit known --resource-profile')
    if (args.profile == CAPTURED_TEXT_UTILS_PARAGRAPH_SOURCE_PROFILE
            and args.resource_profile != ASCII_RESOURCE_PROFILE):
        parser.error('The paragraph profile requires --resource-profile embedded-ascii-v1')
    if not args.offline_exclusive:
        parser.error('--offline-exclusive is required; all source writers must remain stopped')
    if args.target is None:
        parser.error('--target is required')
    try:
        _literal_target(args.target)
        if args.profile == CAPTURED_RELATIVE_SOURCE_PROFILE:
            offline.canonical_path(args.target)
        elif args.profile == CAPTURED_TEXT_UTILS_PARAGRAPH_SOURCE_PROFILE:
            offline._paragraph_target(args.target)
    except (InputError, SafetyError, ValueError) as error:
        parser.error(str(error))
    if args.journal_id is not None:
        parser.error('--journal-id is available only for explicit recovery')


def invocation_json(value):
    """Primitive, output-only conversion. Bytes are explicit, never decoded."""
    return dict(purpose=value.purpose, argv=list(value.argv), cwd=value.cwd,
        environment=[list(pair) for pair in value.environment], returncode=value.returncode,
        stdout_hex=value.stdout.hex(), stderr_hex=value.stderr.hex(),
        launch_attempted=value.launch_attempted, started=value.started,
        lifecycle=value.lifecycle, termination_reason=value.termination_reason)


def _file_evidence_json(value):
    return dict(path=value.path, identity=list(value.identity), sha256=value.sha256,
        size=value.size, parents=[[path, list(identity)] for path, identity in value.parents])


def resource_report_json(report):
    """Original primitive font evidence only, never registry data or font bytes."""
    if report is None:
        return None
    return dict(profile=report.profile, repertoire_version=report.repertoire_version,
        fonts=[dict(face_index=font.face_index,
            installed=_file_evidence_json(font.installed), private=_file_evidence_json(font.private),
            identity=dict(format=font.identity.format, family=font.identity.family,
                postscript_name=font.identity.postscript_name, full_name=font.identity.full_name,
                version=font.identity.version, unique_name=font.identity.unique_name))
            for font in report.fonts])


def _resource_details(report, *, historical=False):
    available = not historical and report is not None
    return dict(resource_report=resource_report_json(report) if available else None,
        resource_details_available=available,
        resource_details_note=('Original font identities were not persisted; no live resources were consulted'
            if historical else 'Original captured resource evidence' if available
            else 'Resource evidence was not captured or is unavailable'))


def graph_observation_json(observation):
    """Original compiler observations only; expected membership is separate."""
    if observation is None:
        return None
    budget = observation.scalar_budget
    return dict(dependency_paths=list(observation.dependency_paths),
        dependencies=_file_evidence_json(observation.dependencies),
        sources=[dict(path=source.path, sha256=source.sha256, size=source.size)
                 for source in observation.sources],
        aggregate_source_bytes=observation.aggregate_source_bytes,
        scalar_budget=dict(policy=budget.policy, limit=budget.limit,
            total_charge=budget.total_charge, use_count=budget.use_count,
            origin_count=budget.origin_count))


def package_observation_json(observation):
    """Detached original staged bytes and exact observed package dependencies."""
    if observation is None:
        return None
    def source(value):
        return dict(path=value.path, sha256=value.sha256, size=value.size,
                    private=_file_evidence_json(value.private))
    return dict(source_profile=observation.source_profile,
        root_grammar_version=observation.grammar_version,
        parser_contract=list(observation.parser_contract),
        config_sha256=observation.config_sha256, catalog_sha256=observation.catalog_sha256,
        evidence_hashes=[dict(path=path, sha256=sha256)
                         for path, sha256 in observation.evidence_hashes],
        dependency_paths=list(observation.dependency_paths),
        dependencies=_file_evidence_json(observation.dependencies),
        root_source=source(observation.root_source),
        package_identity=dict(namespace='local', name='text-utils', version='0.1.2',
            role='read-only-semantic-evidence'),
        package_sources=[source(value) for value in observation.package_sources],
        package_directories=[dict(path=value.path, identity=list(value.identity),
            parents=[[path, list(identity)] for path, identity in value.parents])
            for value in observation.package_directories])


def compile_report_json(report):
    failure = report.compiler_failure
    return dict(safety_gate=report.safety_gate, compiler_invoked=report.compiler_invoked,
        compiler_status=report.compiler_status,
        attempted_invocations=[invocation_json(item) for item in report.attempted_invocations],
        compiler_failure=None if failure is None else dict(reason=failure.reason,
            kind=failure.kind.value,
            invocation=None if failure.invocation is None else invocation_json(failure.invocation)),
        **_resource_details(report.resource_report))


def publication_report_json(report, *, historical=False):
    return dict(phase=report.phase, gate_status=report.gate_status,
        compiler_invoked=report.compiler_invoked, compiler_status=report.compiler_status,
        attempted_invocations=[invocation_json(item) for item in report.attempted_invocations],
        invocation_origin='historical_recovery' if historical else 'original_invocation',
        invocation_details_available=not historical,
        invocation_details_note=('Original argv and streams were not persisted; an empty list does not mean no past execution'
                                 if historical else 'Bounded original invocation details'),
        source_commit=report.source_commit, source_journal_id=report.source_journal_id,
        output_disposition=report.output_disposition, recovery_required=report.recovery_required,
        journal_id=report.journal_id, observed_sha256=report.observed_sha256, reason=report.reason,
        **_resource_details(report.resource_report, historical=historical))


def inventory_json(entries):
    return dict(historical_only=True, pending=[e.journal_id for e in entries if e.pending],
        entries=[dict(journal_id=e.journal_id, state=e.state, pending=e.pending,
            destination=e.destination, error=e.error, compiler_invoked=e.compiler_invoked,
            compiler_status=e.compiler_status, **_resource_details(None, historical=True)) for e in entries],
        **({'empty': 'no PDF journals observed'} if not entries else {}))


def _immediate_compiler_error(error):
    # Do not search chains: a later safety/cleanup failure must supersede status.
    if isinstance(error, PublicationError):
        return error.__cause__
    return error


def _fatal_signal(number):
    """Whether resetting this platform's signal to default actually terminates.

    valid_signals() alone also admits stop/continue and default-ignored signals.
    Only reviewed fatal defaults are eligible; this never sends a probe signal.
    """
    if type(number) is not int or number <= 0 or number not in signal.valid_signals():
        return False
    names = {'SIGABRT', 'SIGALRM', 'SIGBUS', 'SIGFPE', 'SIGHUP', 'SIGILL',
             'SIGINT', 'SIGKILL', 'SIGPIPE', 'SIGPROF', 'SIGQUIT', 'SIGSEGV',
             'SIGSYS', 'SIGTERM', 'SIGTRAP', 'SIGUSR1', 'SIGUSR2', 'SIGVTALRM',
             'SIGXCPU', 'SIGXFSZ'}
    if sys.platform == 'darwin':
        names.add('SIGEMT')
    elif sys.platform.startswith('linux'):
        # SIGIO/SIGPOLL terminates on Linux but is ignored by default on Darwin.
        names.update(('SIGIO', 'SIGPWR', 'SIGSTKFLT'))
    if number in {int(getattr(signal, name)) for name in names if hasattr(signal, name)}:
        return True
    return (sys.platform.startswith('linux') and hasattr(signal, 'SIGRTMIN')
            and hasattr(signal, 'SIGRTMAX')
            and int(signal.SIGRTMIN) <= number <= int(signal.SIGRTMAX))


def classify_status(error, publication_report, *, source_cleanup_safe=True,
                    context_cleanup_safe=True):
    """Pure conservative process/gate classifier; negative means logical signal.

    No output, self-signal or reason-string matching occurs here. The caller
    classifies original argument/input errors separately after safe output abort.
    """
    if source_cleanup_safe is not True or context_cleanup_safe is not True:
        return 3
    if type(publication_report) is not PublicationReport:
        return 3
    if (type(publication_report.gate_status) is not int
            or publication_report.recovery_required is not False):
        return 3
    if error is None:
        return 0 if (publication_report.compiler_invoked is True
            and type(publication_report.compiler_status) is int
            and publication_report.compiler_status == 0
            and type(publication_report.attempted_invocations) is tuple
            and all(type(item) is compiler.Invocation for item in publication_report.attempted_invocations)
            and any(item.purpose == 'compile' and item.started is True
                    and item.launch_attempted is True and type(item.returncode) is int
                    and item.returncode == 0 and item.lifecycle == 'exited'
                    for item in publication_report.attempted_invocations)
            and publication_report.phase == 'published'
            and publication_report.gate_status == 0
            and publication_report.output_disposition == 'published'
            and not publication_report.recovery_required) else 3
    failure = _immediate_compiler_error(error)
    if not isinstance(failure, compiler.CompilerFailure) or failure.kind is not compiler.CompilerFailureKind.PROCESS_EXIT:
        return 3
    report, invocation = failure.report, failure.invocation
    if (type(report) is not compiler.CompileReport
            or type(invocation) is not compiler.Invocation
            or type(report.attempted_invocations) is not tuple
            or type(publication_report.attempted_invocations) is not tuple
            or any(type(item) is not compiler.Invocation for item in
                   (*report.attempted_invocations, *publication_report.attempted_invocations))):
        return 3
    if not (publication_report.gate_status == 3
            and publication_report.phase == 'aborted_unpublished'
            and publication_report.output_disposition == 'untouched'
            and not publication_report.recovery_required
            and report.safety_gate == 'passed'
            and report.compiler_invoked is True
            and invocation is not None and invocation.purpose == 'compile'
            and invocation.started is True and invocation.launch_attempted is True
            and type(invocation.returncode) is int
            and (1 <= invocation.returncode <= 255 or
                 invocation.returncode < 0 and _fatal_signal(-invocation.returncode))
            and invocation.lifecycle == ('signaled' if invocation.returncode < 0 else 'exited')
            and invocation in report.attempted_invocations
            and type(report.compiler_status) is int
            and report.compiler_status == invocation.returncode
            and publication_report.compiler_invoked is True
            and type(publication_report.compiler_status) is int
            and publication_report.compiler_status == invocation.returncode
            and invocation in publication_report.attempted_invocations):
        return 3
    return invocation.returncode


@contextmanager
def _tracked(context, cleanup, key):
    """Record exit failure independently of the error that initiated cleanup."""
    value = context.__enter__()
    cleanup[key] = False
    try:
        yield value
    except BaseException:
        details = sys.exc_info()
        suppress = context.__exit__(*details)
        cleanup[key] = True
        if not suppress:
            raise
    else:
        context.__exit__(None, None, None)
        cleanup[key] = True


def _cancelled():
    if _active_signals is not None and _active_signals.cancel_event.is_set():
        raise KeyboardInterrupt('Executable cancellation requested')


def _output_safe(report):
    return (report is not None and not report.recovery_required
            and ((report.phase == 'aborted_unpublished' and report.output_disposition == 'untouched')
                 or (report.phase == 'restored' and report.output_disposition == 'restored')
                 or (report.phase == 'published' and report.gate_status == 0
                     and report.output_disposition == 'published')))


def _source_after_apply_failure(root):
    try:
        inventory = journal_inventory(root)
        return dict(state='required' if inventory.pending else 'not_required', checked=True,
                    pending=list(inventory.pending)), inventory.complete
    except (InputError, SafetyError, OSError, ValueError) as error:
        return dict(state='unknown', checked=True, pending=None, error=str(error)), False


def _print(result, json_format):
    if json_format:
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2), flush=True)
        return
    invoked = result['compiler_invoked']
    compiler_text = ('compiler invoked' if invoked is True else
                     'compiler not invoked' if invoked is False else 'compiler invocation unknown')
    print(f"typst-lint: {'completed' if result['status'] == 0 else 'blocked'} (status {result['status']}); {compiler_text}")
    if result.get('termination_signal') is not None:
        print(f"Termination signal: {result['termination_signal']}")
    if 'error' in result:
        print(f"{result['error_code']}: {result['error']}")
    if 'publication_report' in result:
        report = result['publication_report']
        print(f"PDF output: {report['output_disposition']}; journal {report['journal_id']}; recovery {'required' if report['recovery_required'] else 'not required'}")
        if report['invocation_origin'] == 'historical_recovery':
            print(report['invocation_details_note'])
            print(report['resource_details_note'])
    if 'output_inventory' in result:
        inventory = result['output_inventory']
        print(inventory.get('empty', f"{len(inventory['entries'])} historical PDF journals; {len(inventory['pending'])} pending"))
    if result.get('source_summary') is not None:
        summary = result['source_summary']
        print(f"Source profile: {offline._source_selection_text(summary)}; {summary['target']}")
        offline._print_graph_sources(summary)
        print('Input profile complete; global 33-rule coverage remains PARTIAL')
    if 'graph_observation_available' in result:
        observation = result['graph_observation']
        if observation is None:
            print(result['graph_observation_note'])
        else:
            print(f"Compiler-observed dependencies: {', '.join(observation['dependency_paths'])}")
            budget = observation['scalar_budget']
            print(f"Scalar render budget ({budget['policy']}): {budget['total_charge']} of {budget['limit']} bytes")
    if 'package_observation_available' in result:
        observation = result['package_observation']
        if observation is None:
            print(result['package_observation_note'])
        else:
            print(f"Compiler-observed dependencies: {', '.join(observation['dependency_paths'])}")
            print('Captured package: @local/text-utils:0.1.2; read-only semantic evidence')
    if 'known_resource_profile_limitation' in result:
        limitation = result['known_resource_profile_limitation']
        print(f"Known resource-profile limitation ({limitation['profile']}): {limitation['note']}")
    sys.stdout.flush()


def main(arguments=None, *, gate=False):
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    json_format, command = offline._json_requested(arguments), offline._command(arguments)
    phase, error, root = 'arguments', None, None
    summary = publication = publication_report = compile_report = None
    cleanup = {}
    apply_started = False
    source_recovery = dict(state='not_checked', checked=False, pending=None)
    source_cleanup_safe = True
    selected_resource_profile = None
    selected_source_profile = None
    result = None
    try:
        args = offline._arguments(arguments, gate)
        command, json_format = args.command, args.format == 'json'
        if command not in ('compile', 'output-status', 'recover-output'):
            raise offline.OfflineArgumentsError('This entrypoint accepts only explicit compile/output routes')
        if command == 'compile':
            selected_resource_profile = args.resource_profile
            selected_source_profile = args.profile
        phase = 'root'
        root = offline._root(args.root)
        if command == 'output-status':
            phase = 'output-status'
            entries = inventory_publications(root)
            result = offline._base(command, 3 if any(e.pending for e in entries) else 0, phase)
            result['output_inventory'] = inventory_json(entries)
            if result['status']:
                result.update(error_code='output_recovery_required', error='Pending or corrupt PDF journals require explicit output recovery')
        elif command == 'recover-output':
            phase = 'recover-output'
            output_lease = OfflineOutputExclusive.assert_exporters_watchers_closed_and_no_other_writers()
            publication_report = recover_publication(root, destination=args.output,
                journal_id=args.journal_id, lease=output_lease)
            result = offline._base(command, 3 if publication_report.recovery_required else 0, phase)
            result.update(output=args.output, journal_id=args.journal_id)
        else:
            phase = 'capture'
            source_lease = OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers()
            output_lease = OfflineOutputExclusive.assert_exporters_watchers_closed_and_no_other_writers()
            if args.profile == CAPTURED_TEXT_UTILS_PARAGRAPH_SOURCE_PROFILE:
                context = ProtectedCapturedTextUtilsParagraphFix(root, args.target,
                    package_path=args.package_path,
                    journal_directory=fixed_journal_directory(root), lease=source_lease,
                    create_journal_directory=True, source_profile=args.profile)
            elif args.profile == CAPTURED_RELATIVE_SOURCE_PROFILE:
                context = ProtectedCapturedRelativeFix(root, args.target,
                    journal_directory=fixed_journal_directory(root), lease=source_lease,
                    create_journal_directory=True, source_profile=args.profile)
            else:
                context = ProtectedCoreFix(root, args.target,
                    journal_directory=fixed_journal_directory(root), lease=source_lease,
                    create_journal_directory=True, source_profile=args.profile)
            with _tracked(context, cleanup, 'source') as fix:
                _cancelled()
                phase = 'output-prepare'
                if args.profile == CAPTURED_TEXT_UTILS_PARAGRAPH_SOURCE_PROFILE:
                    publication = PreparedCapturedTextUtilsParagraphPdfPublication(fix,
                        destination=args.output, lease=output_lease)
                elif args.profile == CAPTURED_RELATIVE_SOURCE_PROFILE:
                    publication = PreparedCapturedRelativePdfPublication(fix,
                        destination=args.output, lease=output_lease)
                else:
                    publication = PreparedPdfPublication(fix, destination=args.output, lease=output_lease)
                with _tracked(publication, cleanup, 'output'):
                    _cancelled()
                    phase = 'validate'
                    candidate = fix.validate()
                    _cancelled()
                    phase = 'apply'
                    apply_started = True
                    try:
                        fix.apply(candidate)
                    finally:
                        summary = offline._applied_summary(fix)
                    _cancelled()
                    phase = 'compile'
                    snapshot = (fix.committed_snapshot()
                        if args.profile == CAPTURED_TEXT_UTILS_PARAGRAPH_SOURCE_PROFILE else
                        fix.committed_graph_snapshot()
                        if args.profile == CAPTURED_RELATIVE_SOURCE_PROFILE else fix.compile_snapshot())
                    publication_report = publication.compile_and_publish(snapshot,
                        profile=args.resource_profile, deadline_seconds=10.0,
                        cancel_event=None if _active_signals is None else _active_signals.cancel_event)
            result = offline._base(command, 0, 'completed')
            result.update(**offline._source_selection_json(summary),
                          resource_profile=args.resource_profile,
                          target=args.target, output=args.output)
    except (InputError, SafetyError, OSError, ValueError, compiler.CompilerFailure, KeyboardInterrupt) as caught:
        error = caught
        result = offline._error(caught, command, phase)
        if isinstance(caught, KeyboardInterrupt):
            result.update(status=3, error_code='interrupted', error='Operation interrupted')
        elif isinstance(caught, compiler.UnsupportedCompileProfile):
            result.update(status=2, error_code='unsupported_resource_profile')
        elif isinstance(caught, PublicationError):
            result.update(status=3, error_code='publication_failure')
            if isinstance(caught, PublicationPending):
                result['output_inventory'] = inventory_json(caught.entries)
        elif isinstance(caught, compiler.CompilerFailure):
            result.update(status=3, error_code='compiler_failure')
    # At this point both contexts have ended. Report only immutable observations.
    if publication is not None and publication.report is not None:
        publication_report = publication.report
    if command == 'compile':
        context_cleanup_safe = all(cleanup.values())
        if apply_started and summary is None:
            source_recovery, source_cleanup_safe = _source_after_apply_failure(root)
        elif summary is not None:
            source_recovery = dict(state='not_required', checked=False, pending=[])
        elif isinstance(error, RecoveryRequired):
            source_cleanup_safe = False
            source_recovery = dict(state='required', checked=True, pending=list(error.journals))
        if isinstance(error, TransactionFailure):
            source_recovery['recovery_report'] = recovery_report_json(error.report)
        if summary is not None:
            result['source_summary'] = offline._summary_json(summary)
            result['source_commit'] = 'committed'
        else:
            result['source_summary'] = None
            result['source_commit'] = 'unknown' if apply_started else 'not_applied'
        immediate = _immediate_compiler_error(error)
        # Preserve the actual immutable report through context teardown. A later
        # safety/cleanup error may have an empty default report of its own.
        compile_report = (None if publication is None else publication.compile_report)
        if compile_report is None:
            compile_report = getattr(immediate, 'report', None)
        if not isinstance(compile_report, compiler.CompileReport):
            compile_report = None
        if publication_report is not None:
            # Original input refusal is still 2 after a safely reconciled abort.
            original_input = error is not None and result['status'] == 2
            if isinstance(immediate, compiler.UnsupportedCompileProfile):
                original_input = True
                result.update(error_code='unsupported_resource_profile', error=str(immediate))
            classified = classify_status(error, publication_report,
                source_cleanup_safe=source_cleanup_safe, context_cleanup_safe=context_cleanup_safe)
            result['status'] = (2 if original_input and source_cleanup_safe
                and context_cleanup_safe and _output_safe(publication_report) else classified)
        elif not context_cleanup_safe or not source_cleanup_safe:
            result['status'] = 3
        # Executable requests preserve the original selected signal only after
        # source and output cleanup are known safe. Repeats never interrupt exits.
        selected = None if _active_signals is None else _active_signals.first_signal
        if selected is not None:
            result['received_signals'] = list(_active_signals.received)
            attempts = () if publication_report is None else publication_report.attempted_invocations
            compiler_cleanup_safe = (publication_report is not None
                and publication_report.compiler_invoked is not None
                and not isinstance(immediate, compiler.CompileSafetyError)
                and (compile_report is None or compile_report.safety_gate != 'refused')
                and all(item.started is not None and item.lifecycle not in ('running', 'uncertain')
                        for item in attempts))
            if source_cleanup_safe and context_cleanup_safe and _output_safe(publication_report) and compiler_cleanup_safe:
                result['status'] = -selected
            else:
                result['status'] = 3
        result['source_recovery'] = source_recovery
        if compile_report is not None:
            result['compile_report'] = compile_report_json(compile_report)
        if selected_source_profile == CAPTURED_RELATIVE_SOURCE_PROFILE:
            observation = None if publication is None else publication.graph_observation
            result.update(graph_observation=graph_observation_json(observation),
                graph_observation_available=observation is not None,
                graph_observation_note=('Original compiler-observed dependency evidence and scalar render budget'
                    if observation is not None else
                    'Compiler-observed dependency evidence is unavailable; no validated graph artifact was obtained'))
    if command == 'compile' and selected_source_profile == CAPTURED_TEXT_UTILS_PARAGRAPH_SOURCE_PROFILE:
        observation = None if publication is None else publication.package_observation
        result.update(package_observation=package_observation_json(observation),
            package_observation_available=observation is not None,
            package_observation_note=('Original compiler-observed root and text-utils package dependencies'
                if observation is not None else
                'Compiler-observed package dependencies are unavailable; no validated paragraph artifact was obtained'))
    if publication_report is not None:
        historical = command == 'recover-output'
        result['publication_report'] = publication_report_json(publication_report, historical=historical)
        result['compiler_observation_origin'] = 'historical_recovery' if historical else 'original_invocation'
        result['compiler_invoked'] = publication_report.compiler_invoked
        result['compiler_status'] = publication_report.compiler_status
        result['output_recovery'] = dict(state='required' if publication_report.recovery_required else 'not_required',
            journal_id=publication_report.journal_id)
        if historical:
            result['source_recovery'] = source_recovery
    else:
        # No output session was admitted; do not claim its store was checked.
        observed = result.get('output_inventory')
        state = ('required' if observed['pending'] else 'not_required') if observed is not None else ('unknown' if publication is not None else 'not_checked')
        result['output_recovery'] = dict(state=state, journal_id=None)
    if result['status'] < 0:
        result['termination_signal'] = -result['status']
    # A fixed property of the explicitly selected profile, not captured font
    # evidence or a reconstruction of historical invocation/resource details.
    if command == 'compile' and selected_resource_profile == APPLEGOTHIC_RESOURCE_PROFILE:
        result['known_resource_profile_limitation'] = dict(
            profile=selected_resource_profile, note=_KOREAN_PROFILE_LIMITATION)
    _print(result, json_format)
    return result['status']


class _ExecutableSignals:
    """Executable-only cooperative cancellation; source apply is not interrupted."""
    def __init__(self):
        self.cancel_event = threading.Event()
        self.first_signal = None
        self.received = []
        self.previous = {}

    def receive(self, number, frame):
        if self.first_signal is None:
            self.first_signal = number
        # Bound memory even if an external sender repeatedly requests shutdown.
        if len(self.received) < 32:
            self.received.append(number)
        self.cancel_event.set()

    def __enter__(self):
        for number in (signal.SIGINT, signal.SIGTERM):
            self.previous[number] = signal.getsignal(number)
            signal.signal(number, self.receive)
        return self

    def __exit__(self, *details):
        for number, handler in self.previous.items():
            signal.signal(number, handler)


def executable_main(arguments=None):
    """Run, clean, flush, then reproduce a natural/requested signal on self only."""
    global _active_signals
    from typst_lint import main as lint_main
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    previous = _active_signals
    if offline._command(arguments) == 'compile' and offline.requested(arguments):
        try:
            with _ExecutableSignals() as coordinator:
                _active_signals = coordinator
                status = lint_main(arguments)
                sys.stdout.flush()
                sys.stderr.flush()
        finally:
            _active_signals = previous
    else:
        status = lint_main(arguments)
        sys.stdout.flush()
        sys.stderr.flush()
    if type(status) is not int:
        raise SystemExit(3)
    if status < 0:
        number = -status
        # Defense in depth: malformed/nonterminating evidence can never stop
        # this process or reach handler/mask changes, even if a caller bypasses
        # the pure classifier. Genuine SIGKILL needs no handler reset.
        if not _fatal_signal(number):
            raise SystemExit(3)
        if number != signal.SIGKILL:
            signal.signal(number, signal.SIG_DFL)
        if hasattr(signal, 'pthread_sigmask'):
            signal.pthread_sigmask(signal.SIG_UNBLOCK, {number})
        os.kill(os.getpid(), number)
        # An ignored/failed signal cannot be translated to SystemExit(-number).
        raise RuntimeError('Selected termination signal did not terminate executable')
    raise SystemExit(status)


if __name__ == '__main__':
    # Dispatch must reuse this coordinator, not import a second module instance.
    sys.modules['offline_compile_cli'] = sys.modules[__name__]
    executable_main()
