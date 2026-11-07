"""Shared explicit-offline parsing and source-only fix/recovery routes.

Reports are outputs, never candidates or coverage inputs. The separate
offline_compile_cli module owns compiler/PDF composition. There is no serialized
authority, user-selectable catalog or store.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import errno
import json
from pathlib import Path
import re
import stat
import sys

from closed_core import ClosedCoreError
from captured_relative_core import canonical_path
from captured_relative_fix import (CapturedRelativeFixError, CapturedRelativeFixSummary,
                                   ProtectedCapturedRelativeFix)
from captured_text_utils_paragraph_fix import (CapturedTextUtilsParagraphFixError,
    CapturedTextUtilsParagraphFixSummary, ProtectedCapturedTextUtilsParagraphFix)
from captured_text_utils_paragraph_inputs import _literal_target as _paragraph_target
from captured_text_utils_paragraph_core import _canonical_package_identity
from fixed_journal import (JournalInventoryError, fixed_journal_directory,
                           inventory_json, journal_inventory,
                           recovery_report_json)
from protected_fix import (ProtectedCoreError, ProtectedCoreFix,
                           ProtectedCoreInputError, _literal_target)
from syntax_client import AdapterError
from source_profiles import (CAPTURED_RELATIVE_SOURCE_PROFILE,
    CAPTURED_TEXT_UTILS_PARAGRAPH_SOURCE_PROFILE,
    CLOSED_CORE_SOURCE_PROFILE, KNOWN_OFFLINE_SOURCE_PROFILES, is_offline_source_profile)
from transactions import (OfflineExclusive, ProtectedTransaction,
                          RecoveryRequired, SourceEncodingError, TransactionError,
                          TransactionFailure, _absolute)
from typst_lint import InputError, SafetyError, VERSION

PROFILE = CLOSED_CORE_SOURCE_PROFILE
_NEW_OPTIONS = frozenset(('--profile', '--offline-exclusive', '--target', '--journal-id',
                          '--resource-profile', '--offline-output-exclusive'))
_NEW_COMMANDS = frozenset(('journal-status', 'recover', 'output-status', 'recover-output'))


class OfflineArgumentsError(InputError):
    pass


class OfflineRootError(InputError):
    """The initial explicit root argument is absent or not a directory."""


class OfflineRootResolutionError(TransactionError):
    """Root resolution itself failed, including older pathlib loop errors."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise OfflineArgumentsError(message)


def _command(arguments):
    index = 0
    value_options = {'--root', '--format', '--package-path', '--profile', '--target', '--journal-id',
                     '--resource-profile', '--output'}
    while index < len(arguments):
        word = arguments[index]
        if word == '--':
            return arguments[index + 1] if index + 1 < len(arguments) else None
        if word in value_options:
            index += 2
        elif word.startswith('-'):
            index += 1
        else:
            return word
    return None


def requested(arguments):
    """Inspect the full argv, including inert compile tails, before dispatch."""
    # Ordinary compiler argv after -- stays inert. New authority options are
    # rejected even there, instead of being silently discarded.
    head = arguments[:arguments.index('--')] if '--' in arguments else arguments
    command = _command(arguments)
    return (command in _NEW_COMMANDS
            or (command == 'fix' and any(word in ('-h', '--help') for word in head))
            or any(word.split('=', 1)[0] in _NEW_OPTIONS for word in arguments))


def _json_requested(arguments):
    return any(word == '--format=json' or
               (word == '--format' and index + 1 < len(arguments)
                and arguments[index + 1] == 'json')
               for index, word in enumerate(arguments))


def _arguments(arguments, gate):
    parser = _Parser(description=__doc__, allow_abbrev=False)
    parser.add_argument('command', choices=('check', 'fix', 'compile', 'journal-status', 'recover', 'output-status', 'recover-output'))
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--format', choices=('json', 'text'), default='text')
    parser.add_argument('--profile', choices=tuple(sorted(KNOWN_OFFLINE_SOURCE_PROFILES)),
                        help='Explicit source grammar selection')
    parser.add_argument('--offline-exclusive', action='store_true',
                        help='Assert editors, watchers and every other source writer are stopped for the whole operation')
    parser.add_argument('--target')
    parser.add_argument('--journal-id')
    parser.add_argument('--resource-profile',
                        help='Explicit compile only: embedded-applegothic-ko-v1 or embedded-ascii-v1')
    parser.add_argument('--offline-output-exclusive', action='store_true',
                        help='Assert exporters, watchers and all output/ancestor/storage writers remain stopped')
    parser.add_argument('--output')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--package-path')
    # No last-value-wins opt-ins or silently ignored command-specific options.
    seen = set()
    for word in arguments:
        option = word.split('=', 1)[0]
        if option in parser._option_string_actions and option not in ('-h', '--help'):
            if option in seen:
                parser.error(f'Duplicate option: {option}')
            seen.add(option)
    args = parser.parse_args(arguments)
    if gate or args.command == 'check':
        parser.error('Explicit offline routes are unavailable for check/typst-style compile')
    if args.dry_run:
        parser.error('Offline routes do not accept --dry-run')
    if (args.command in ('fix', 'compile')
            and args.profile == CAPTURED_TEXT_UTILS_PARAGRAPH_SOURCE_PROFILE):
        if args.package_path is None:
            parser.error('The paragraph profile requires explicit --package-path')
        try:
            # Validate the original spelling before Path could normalize it.
            # No resolve, stat, discovery or ambient cache occurs here.
            package_path = _absolute(args.package_path)
            if not _canonical_package_identity(str(package_path / 'local/text-utils/0.1.2/lib.typ')):
                raise ValueError('Paragraph --package-path must have bounded canonical UTF-8 spelling')
            args.package_path = package_path
        except (TypeError, ValueError, UnicodeError, TransactionError) as error:
            parser.error(str(error))
    elif args.package_path is not None:
        parser.error('--package-path is available only for explicit paragraph fix/compile')
    if args.command in ('compile', 'output-status', 'recover-output'):
        from offline_compile_cli import validate_arguments
        validate_arguments(args, parser)
        return args
    if args.resource_profile is not None or args.offline_output_exclusive or args.output is not None:
        parser.error('Source-only routes do not accept output/resource options')
    if args.command == 'journal-status':
        if args.profile is not None or args.offline_exclusive or args.target is not None or args.journal_id is not None:
            parser.error('journal-status accepts only --root and --format')
    else:
        if not args.offline_exclusive:
            parser.error('--offline-exclusive is required; it asserts all editors, watchers and other writers remain stopped')
        if args.target is None:
            parser.error('--target is required')
        _literal_target(args.target)
        if args.command == 'fix':
            if not is_offline_source_profile(args.profile):
                parser.error('fix requires an explicit known --profile')
            if args.profile in (CAPTURED_RELATIVE_SOURCE_PROFILE,
                                CAPTURED_TEXT_UTILS_PARAGRAPH_SOURCE_PROFILE):
                try:
                    (_paragraph_target if args.profile == CAPTURED_TEXT_UTILS_PARAGRAPH_SOURCE_PROFILE
                     else canonical_path)(args.target)
                except (InputError, SafetyError, ValueError) as error:
                    parser.error(str(error))
            if args.journal_id is not None:
                parser.error('--journal-id is available only for recover')
        else:
            if args.profile is not None:
                parser.error('recover does not accept --profile or require current profile admission')
            if args.journal_id is None or re.fullmatch('[0-9a-f]{32}', args.journal_id) is None:
                parser.error('recover requires --journal-id with exactly 32 lowercase hexadecimal characters')
    return args


def _base(command, status, phase):
    return dict(schema=1, tool_version=VERSION, command=command, status=status,
                phase=phase, compiler_invoked=False, compiler_status=None)


def _source_selection_json(summary):
    """Separate graph grammar facts from the old single-source selection."""
    if summary.profile == CAPTURED_RELATIVE_SOURCE_PROFILE:
        return dict(profile=summary.profile,
                    root_grammar_version=summary.root_grammar_version,
                    module_grammar_version=summary.module_grammar_version,
                    parser_contract=list(summary.parser_contract))
    if summary.profile == CAPTURED_TEXT_UTILS_PARAGRAPH_SOURCE_PROFILE:
        return dict(profile=summary.profile, root_grammar_version=summary.grammar_version,
                    parser_contract=list(summary.parser_contract))
    return dict(profile=summary.profile, grammar_version=summary.grammar_version)


def _source_selection_text(summary):
    if summary['profile'] == CAPTURED_RELATIVE_SOURCE_PROFILE:
        return (f"{summary['profile']} (root {summary['root_grammar_version']}; "
                f"module {summary['module_grammar_version']}; "
                f"parser {', '.join(summary['parser_contract'])})")
    if summary['profile'] == CAPTURED_TEXT_UTILS_PARAGRAPH_SOURCE_PROFILE:
        return (f"{summary['profile']} (root {summary['root_grammar_version']}; "
                f"parser {', '.join(summary['parser_contract'])})")
    return f"{summary['profile']} ({summary['grammar_version']})"


def _print_graph_sources(summary):
    if summary['profile'] == CAPTURED_RELATIVE_SOURCE_PROFILE:
        print('Committed graph sources: ' + ', '.join(
            f"{source['path']} ({source['role']})" for source in summary['sources']))
        print(f"Captured import edges: {len(summary['import_edges'])}")


def _summary_json(summary):
    """Explicit primitive output conversion; never consumed by the writer."""
    if summary.profile == CAPTURED_TEXT_UTILS_PARAGRAPH_SOURCE_PROFILE:
        return dict(**_source_selection_json(summary), target=summary.target,
            original_sha256=summary.original_sha256, final_sha256=summary.final_sha256,
            config_sha256=summary.config_sha256, catalog_sha256=summary.catalog_sha256,
            package_sha256=summary.package_sha256,
            package_identity=dict(namespace='local', name='text-utils', version='0.1.2',
                role='read-only-semantic-evidence',
                files=[dict(path=path, sha256=sha256, size=size)
                       for path, sha256, size in summary.package_files]),
            required_rule_ids=list(summary.required_rule_ids),
            evaluated_rule_ids=list(summary.evaluated_rule_ids),
            not_applicable=[dict(rule_id=rid, reason=reason)
                            for rid, reason in summary.structural_reasons],
            applied_edits=[dict(path=e.path, start=e.start, end=e.end,
                replacement=e.replacement.decode('utf-8'), rule_id=e.rule_id)
                for e in summary.applied_edits],
            modified_paths=list(summary.modified_paths), journal_id=summary.journal_id,
            final_findings=[], coverage=dict(input_profile_complete=True,
                input_profile_rule_count=len(summary.required_rule_ids),
                evaluated_rule_count=len(summary.evaluated_rule_ids),
                structurally_discharged_rule_count=len(summary.structural_reasons), global_complete=False,
                global_rule_status='PARTIAL', global_complete_rule_count=0,
                global_partial_rule_count=33))
    result = dict(**_source_selection_json(summary), target=summary.target,
                original_sha256=summary.original_sha256, final_sha256=summary.final_sha256,
                required_rule_ids=list(summary.required_rule_ids),
                evaluated_rule_ids=list(summary.evaluated_rule_ids),
                not_applicable=[dict(rule_id=rid, reason=reason)
                                for rid, reason in summary.structural_reasons],
                applied_insertions=[dict(path=e.path, start=e.start, end=e.end,
                                         replacement=e.replacement.decode('utf-8'), rule_id=e.rule_id)
                                    for e in summary.applied_insertions],
                modified_paths=list(summary.modified_paths), journal_id=summary.journal_id,
                final_findings=[], coverage=dict(input_profile_complete=True,
                    input_profile_rule_count=15, global_complete=False,
                    global_rule_status='PARTIAL', global_complete_rule_count=0,
                    global_partial_rule_count=33))
    if summary.profile == CAPTURED_RELATIVE_SOURCE_PROFILE:
        result.update(sources=[dict(path=source.path, sha256=source.sha256,
                                   size=source.size, role=source.role)
                               for source in summary.sources],
            import_edges=[dict(importer=edge.importer, request=edge.request,
                target=edge.target, node_id=edge.node_id, span=list(edge.span),
                items=[list(item) for item in edge.items]) for edge in summary.import_edges])
    return result


def _applied_summary(fix):
    """Retain known apply facts even if a later bridge safety check refused."""
    try:
        summary = fix.summary
        # These exact finite graph/paragraph reports contain primitive frozen records.
        # Detach it now: later compiler/cleanup callbacks can still hold the
        # bridge's returned summary and reflectively mutate nested records.
        return deepcopy(summary) if type(summary) in (CapturedRelativeFixSummary,
            CapturedTextUtilsParagraphFixSummary) else summary
    except (ProtectedCoreError, CapturedRelativeFixError, CapturedTextUtilsParagraphFixError):
        return None


def _error(error, command, phase):
    # Specific subclasses precede their old broad InputError/SafetyError bases.
    if isinstance(error, OfflineArgumentsError):
        status, code, phase = 2, 'invalid_arguments', 'arguments'
    elif isinstance(error, OfflineRootError):
        status, code, phase = 2, 'invalid_root', 'root'
    elif isinstance(error, OfflineRootResolutionError):
        status, code, phase = 3, 'unsafe_root_resolution', 'root'
    elif isinstance(error, ProtectedCoreInputError):
        status, code, phase = 2, error.error_code, error.phase
    elif isinstance(error, AdapterError):
        status, code, phase = 2, 'parser_failure', 'parser'
    elif isinstance(error, ClosedCoreError):
        status, code, phase = 2, 'unsupported_grammar', 'admission'
    elif isinstance(error, SourceEncodingError) and phase == 'capture':
        status, code, phase = 2, 'invalid_source_encoding', 'input'
    elif isinstance(error, RecoveryRequired):
        status, code, phase = 3, 'recovery_required', 'journal'
    elif isinstance(error, TransactionFailure):
        status, code, phase = 3, 'transaction_failure', 'apply'
    elif isinstance(error, JournalInventoryError):
        status, code, phase = 3, 'unsafe_inventory', 'journal-status'
    elif isinstance(error, ProtectedCoreError):
        status, code = 3, 'protected_invariant'
    elif isinstance(error, TransactionError):
        status, code = 3, 'unsafe_transaction'
    elif isinstance(error, SafetyError):
        status, code = 3, 'unsafe_candidate'
    elif isinstance(error, InputError):
        status, code = 2, 'invalid_input'
    elif isinstance(error, OSError):
        status, code = 3, 'filesystem_failure'
    else:
        status, code = 2, 'invalid_input'
    result = _base(command, status, phase)
    result.update(error_code=code, error=str(error))
    if isinstance(error, RecoveryRequired):
        result['recovery_required'] = dict(journals=list(error.journals))
    if isinstance(error, TransactionFailure):
        result['recovery_report'] = recovery_report_json(error.report)
    return result


def _print(result, json_format):
    if json_format:
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
        return
    state = 'completed' if result['status'] == 0 else 'blocked'
    print(f"typst-lint: {state} (exit {result['status']}); compiler not invoked")
    if 'error' in result:
        print(f"{result['error_code']}: {result['error']}")
    if result['command'] == 'fix' and 'profile' in result:
        print(f"{_source_selection_text(result)}: {result['target']}; {len(result['modified_paths'])} modified paths")
        _print_graph_sources(result)
        print('Input profile complete; global 33-rule coverage remains PARTIAL')
    elif 'error' not in result and result['command'] == 'journal-status':
        print(f"{len(result['inventory']['entries'])} retained journals; {len(result['inventory']['pending'])} pending")
    elif 'error' not in result and result['command'] == 'recover':
        print(f"Journal {result['recovery_report']['journal']}: recovery complete")


def _root(path):
    try:
        root = path.resolve(strict=True)
    except OSError as error:
        if error.errno in (errno.ENOENT, errno.ENOTDIR):
            raise OfflineRootError(f'Invalid consumer root: {path}: {error}') from error
        if error.errno == errno.ELOOP:
            raise OfflineRootResolutionError(f'Unsafe root resolution: {path}: {error}') from error
        raise
    except RuntimeError as error:
        # Python 3.11/3.12 pathlib reports symlink loops as RuntimeError.
        # Normalize only this resolver operation, never arbitrary runtime
        # failures from analysis/apply. Do not re-probe a possibly changed
        # path to decide whether a failed resolution can be trusted.
        raise OfflineRootResolutionError(f'Unsafe root resolution: {path}: {error}') from error
    # Resolution admitted this path. Subsequent stat/open disappearance or
    # permission errors are operational races, not invalid-argument success.
    root_info = root.stat()
    if not stat.S_ISDIR(root_info.st_mode):
        raise OfflineRootError('Consumer root must be a directory')
    return root


def main(arguments=None, *, gate=False):
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    json_format = _json_requested(arguments)
    command = _command(arguments)
    if command in ('compile', 'output-status', 'recover-output'):
        from offline_compile_cli import main as compile_main
        return compile_main(arguments, gate=gate)
    phase = 'arguments'
    summary = None
    try:
        args = _arguments(arguments, gate)
        command, json_format = args.command, args.format == 'json'
        phase = 'root'
        root = _root(args.root)
        phase = 'journal-storage'
        store = fixed_journal_directory(root)
        if command == 'journal-status':
            phase = 'journal-status'
            inventory = journal_inventory(root)
            status = 0 if inventory.complete else 3
            result = _base(command, status, 'completed' if status == 0 else 'journal-status')
            result['inventory'] = inventory_json(inventory)
            if status:
                result.update(error_code='incomplete_inventory', error='Incomplete or unreadable journals require explicit recovery')
        else:
            # Source-only assertion construction happens only after the
            # actual flag and command/profile/target combinations were checked.
            lease = OfflineExclusive.assert_editors_watchers_closed_and_no_other_writers()
            if command == 'fix':
                phase = 'capture'
                if args.profile == CAPTURED_TEXT_UTILS_PARAGRAPH_SOURCE_PROFILE:
                    context = ProtectedCapturedTextUtilsParagraphFix(root, args.target,
                        package_path=args.package_path, journal_directory=store,
                        lease=lease, create_journal_directory=True, source_profile=args.profile)
                elif args.profile == CAPTURED_RELATIVE_SOURCE_PROFILE:
                    context = ProtectedCapturedRelativeFix(root, args.target, journal_directory=store,
                        lease=lease, create_journal_directory=True, source_profile=args.profile)
                else:
                    context = ProtectedCoreFix(root, args.target, journal_directory=store,
                        lease=lease, create_journal_directory=True,
                        source_profile=args.profile)
                with context as fix:
                    phase = 'validate'
                    candidate = fix.validate()
                    phase = 'apply'
                    try:
                        fix.apply(candidate)
                    finally:
                        summary = _applied_summary(fix)
                    result = _base(command, 0, 'completed')
                    result.update(_summary_json(summary))
            else:
                phase = 'recover'
                with ProtectedTransaction(root, (args.target,), journal_directory=store,
                        lease=lease, recovery_only=True, require_existing_namespace=True) as transaction:
                    recovered = transaction.recover(args.journal_id)
                status = 0 if recovered.complete else 3
                result = _base(command, status, 'completed' if status == 0 else 'recover')
                result.update(target=args.target, journal_id=args.journal_id,
                              recovery_report=recovery_report_json(recovered))
                if status:
                    result.update(error_code='unresolved_recovery', error='Recovery remains unresolved; later source changes were preserved')
    except (InputError, SafetyError, OSError, ValueError) as error:
        result = _error(error, command, phase)
        if summary is not None:
            result.update(_summary_json(summary), source_commit='committed')
    _print(result, json_format)
    return result['status']
