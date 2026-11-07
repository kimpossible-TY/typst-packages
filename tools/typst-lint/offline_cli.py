"""Shared explicit-offline parsing and source-only fix/recovery routes.

Reports are outputs, never candidates or coverage inputs. The separate
offline_compile_cli module owns compiler/PDF composition. There is no serialized
authority, user-selectable catalog or store.
"""
from __future__ import annotations

import argparse
import errno
import json
from pathlib import Path
import re
import stat
import sys

from closed_core import ClosedCoreError
from fixed_journal import (JournalInventoryError, fixed_journal_directory,
                           inventory_json, journal_inventory,
                           recovery_report_json)
from protected_fix import (ProtectedCoreError, ProtectedCoreFix,
                           ProtectedCoreInputError, _literal_target)
from syntax_client import AdapterError
from transactions import (OfflineExclusive, ProtectedTransaction,
                          RecoveryRequired, SourceEncodingError, TransactionError,
                          TransactionFailure)
from typst_lint import InputError, SafetyError, VERSION

PROFILE = 'closed-core-v1'
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
    parser.add_argument('--profile')
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
    if args.dry_run or args.package_path is not None:
        parser.error('Offline routes do not accept --dry-run or --package-path')
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
            if args.profile != PROFILE:
                parser.error('fix requires --profile closed-core-v1')
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


def _summary_json(summary):
    """Explicit primitive output conversion; never consumed by the writer."""
    return dict(profile=summary.profile, target=summary.target,
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
    elif result['command'] == 'fix':
        print(f"{result['profile']}: {result['target']}; {len(result['modified_paths'])} modified paths")
        print('Input profile complete; global 33-rule coverage remains PARTIAL')
    elif result['command'] == 'journal-status':
        print(f"{len(result['inventory']['entries'])} retained journals; {len(result['inventory']['pending'])} pending")
    elif result['command'] == 'recover':
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
                with ProtectedCoreFix(root, args.target, journal_directory=store,
                        lease=lease, create_journal_directory=True) as fix:
                    phase = 'validate'
                    candidate = fix.validate()
                    phase = 'apply'
                    fix.apply(candidate)
                    result = _base(command, 0, 'completed')
                    result.update(_summary_json(fix.summary))
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
    _print(result, json_format)
    return result['status']
