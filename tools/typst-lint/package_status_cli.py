"""Read-only package report from a manifest or one sandboxed discovery run.

A manifest alone is caller-provided selection data, not runtime import proof.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import threading
from datetime import datetime, timezone

import closed_core_compile as runtime
import document_preview_cli
import document_preview_discover
import document_resources
import include_context
import package_freshness
import package_freshness_observed
import package_freshness_registry
from package_loader import PackageInputError, _absolute_root, read_snapshot_file
from typst_lint import InputError, SafetyError, VERSION, checked_path, relative, require


def inspect(root, manifest, *, checkout=None, upstream=None, ref=None, online=False,
            discover_dependencies=False, entrypoint=None, cancel_event=None):
    """Report sampled package evidence; never install, fetch Git objects or edit."""
    require(type(root) is str and root == str(_absolute_root(root)),
            'Project root must be canonical and absolute')
    require(type(manifest) is str and manifest.startswith('/') and str(Path(manifest)) == manifest,
            'Manifest must be a canonical absolute path')
    require(type(online) is bool, 'Online selection must be Boolean')
    require(type(discover_dependencies) is bool, 'Dependency discovery selection must be Boolean')
    require(cancel_event is None or type(cancel_event) is threading.Event,
            'Cancellation requires a threading.Event')
    require((discover_dependencies and type(entrypoint) is str) or
            (not discover_dependencies and entrypoint is None),
            '--entrypoint is required only with --discover-dependencies')
    def cancelled():
        if cancel_event is not None and cancel_event.is_set():
            raise runtime.CompilerFailure('cancelled', kind=runtime.CompilerFailureKind.CANCELLED)
    cancelled()
    raw, _ = runtime._capture(Path(manifest), document_preview_cli.MAX_MANIFEST_BYTES)
    roots, requests, allow = document_preview_cli.parse_manifest_bytes(raw, Path(root), allow_empty=True)
    discovery = None
    if discover_dependencies:
        relative(entrypoint)
        require(entrypoint.endswith('.typ') and len(entrypoint.encode('utf-8')) <= include_context.MAX_PATH_BYTES
                and not any(ord(char) < 32 or ord(char) == 127 for char in entrypoint),
                'Entrypoint must be a bounded project-relative Typst path')
        _, source = read_snapshot_file(checked_path(Path(root), entrypoint), include_context.MAX_SOURCE_BYTES)
        context = include_context.load_include_context(Path(root), ((entrypoint, source),), budget='book-v1')
        _, observed, font_hashes, pdf_sha256 = document_preview_discover.discover(
            context, roots, requests, entrypoint=entrypoint,
            allow_apple_asset_fonts=allow, cancel_event=cancel_event)
        discovery = dict(entrypoint=entrypoint, observed_at_utc=datetime.now(timezone.utc).isoformat(),
                         observed_dependencies=len(observed), selected_font_files=len(font_hashes),
                         selected_fonts=[dict(root=root_id, path=path, sha256=sha)
                                         for (root_id, path), sha in sorted(font_hashes.items())],
                         selected_fonts_used_proved=False, dependency_list_exhaustive_proved=False,
                         baseline_pdf_sha256=pdf_sha256, future_runtime_selection_proved=False,
                         compared_bytes_same_as_discovery_proved=False)
        # Select only dependency paths observed in this one compiler run. Seed
        # entries that were not observed do not become claimed dependencies.
        requests = [document_resources.ResourceRequest(root_id, path, 'package')
                    for root_id, path in observed if root_id in ('packages', 'cache')]
        cancelled()
    local, preview, unrecognized, seen = {}, set(), [], set()
    for request in requests:
        require(type(request.root) is str and request.root in roots and type(request.role) is str
                and request.role in document_resources.ROLES,
                'Manifest contains an invalid resource root or role')
        document_resources._relative(request.path)
        key = (request.root, request.path)
        require(key not in seen, 'Manifest contains a duplicate resource path')
        seen.add(key)
        parts = request.path.split('/')
        if request.root not in ('packages', 'cache'):
            continue
        if len(parts) < 4:
            unrecognized.append(dict(root=request.root, path=request.path, reason='incomplete_package_path'))
            continue
        if parts[0] not in ('local', 'preview'):
            unrecognized.append(dict(root=request.root, path=request.path, reason='unsupported_namespace'))
            continue
        raw_root = roots[request.root]
        require(type(raw_root) is str and raw_root == str(_absolute_root(raw_root)),
                'Manifest package roots must be canonical absolute paths')
        name, version = parts[1], parts[2]
        if parts[0] == 'local':
            local.setdefault((request.root, name, version), []).append(request.path)
        else:
            try:
                package_freshness_registry._requests([(name, version)])
            except package_freshness_registry.RegistryInputError:
                unrecognized.append(dict(root=request.root, path=request.path,
                                         reason='unsupported_preview_package_identity'))
            else:
                preview.add((name, version))
    require(len(local) <= package_freshness.MAX_PACKAGE_PAIRS, 'Selected local package count exceeds limit')
    require(len(preview) <= package_freshness_registry.MAX_PACKAGES, 'Selected preview package count exceeds limit')
    if local:
        require(all(type(value) is str and value for value in (checkout, upstream, ref)),
                'Local package checks require --checkout, --upstream and --ref')
        checkout_root = _absolute_root(checkout)
        require(checkout == str(checkout_root), 'Checkout root must be canonical and absolute')
        pairs = []
        for root_id, name, version in sorted(local):
            package_rel = Path('local') / name / version
            pairs.append((str(checkout_root / package_rel), str(Path(roots[root_id]) / package_rel)))
        cancelled()
        local_report = package_freshness.inspect_freshness_many(str(checkout_root), pairs, upstream, ref,
                                                                online=online, cancel_event=cancel_event)
        cancelled()
        file_reports = []
        for root_id in sorted({item[0] for item in local}):
            cancelled()
            paths = sorted({path for (rid, _, _), paths in local.items() if rid == root_id for path in paths})
            if len(paths) > package_freshness_observed.MAX_OBSERVED_FILES:
                file_reports.append(dict(root=root_id, status='unknown', reason='file_list_limit_exceeded'))
            else:
                try:
                    sample = package_freshness_observed.compare_observed(str(checkout_root), roots[root_id], paths)
                    file_reports.append(dict(root=root_id, status='sampled', comparison=asdict(sample)))
                except (InputError, SafetyError, OSError, ValueError) as error:
                    file_reports.append(dict(root=root_id, status='unknown', reason=str(error)))
    else:
        local_report, file_reports = None, []
    cancelled()
    preview_report = (package_freshness_registry.inspect_preview_registry(sorted(preview), online=online,
                                                                          cancel_event=cancel_event)
                      if preview else None)
    cancelled()
    return dict(schema=1, tool_version=VERSION, mode='packages-status', status=0,
        manifest_path=manifest, manifest_sha256=hashlib.sha256(raw).hexdigest(),
        selection='compiler-discovery-paths' if discovery is not None else 'caller-supplied-manifest',
        discovery=discovery, manifest_runtime_observation_proved=False,
        current_runtime_selection_proved=False, source_files_modified=False, update_permission=False,
        local=local_report, declared_file_comparisons=file_reports, preview_registry=preview_report,
        unrecognized_manifest_entries=unrecognized,
        limitations=['Discovery observes one sandboxed run; compared package bytes may change after that run.'
                     if discovery is not None else 'Manifest entries are caller declarations and may include unused resources.',
                     'Selected fonts and other OS reads are not exhaustively represented by compiler dependencies.',
                     'Only local and preview paths under the explicit packages/cache roots are classified.',
                     'File equality and version ordering do not prove package compatibility or runtime selection.',
                     'No packages, checkout, Git objects or output files are modified.'])


def main(arguments=None):
    parser = argparse.ArgumentParser(prog='typst-lint packages-status', description=__doc__, allow_abbrev=False,
        epilog='Exit 0 means a report was generated, not that all packages are current. '
               'Use --online for explicit GitHub ref and official Typst registry lookups.')
    parser.add_argument('--root', required=True, help='Canonical absolute project root matching the manifest')
    parser.add_argument('--manifest', required=True, help='Canonical absolute explicit resource manifest JSON')
    parser.add_argument('--checkout', help='Canonical absolute typst-packages Git checkout')
    parser.add_argument('--upstream', help='Explicit GitHub HTTPS repository URL')
    parser.add_argument('--ref', help='Exact upstream branch or tag ref, e.g. refs/heads/main')
    parser.add_argument('--online', action='store_true', help='Opt into bounded read-only online lookups')
    parser.add_argument('--discover-dependencies', action='store_true',
                        help='Run a sandboxed baseline and select its observed package paths')
    parser.add_argument('--entrypoint', help='Project-relative .typ entrypoint for dependency discovery')
    parser.add_argument('--format', choices=('json', 'text'), default='text')
    args = parser.parse_args(sys.argv[1:] if arguments is None else arguments)
    import offline_compile_cli
    signals = offline_compile_cli._active_signals
    try:
        result = inspect(args.root, args.manifest, checkout=args.checkout, upstream=args.upstream,
                         ref=args.ref, online=args.online, discover_dependencies=args.discover_dependencies,
                         entrypoint=args.entrypoint, cancel_event=signals.cancel_event if signals else None)
    except (InputError, SafetyError, PackageInputError, package_freshness.FreshnessError,
            package_freshness.FreshnessCancelled, package_freshness_registry.RegistryCancelled,
            package_freshness_registry.RegistryCleanupError,
            package_freshness_registry.RegistryInputError,
            runtime.CompilerFailure, OSError, ValueError) as error:
        result = dict(schema=1, tool_version=VERSION, mode='packages-status', status=2,
                      error=str(error), source_files_modified=False, update_permission=False)
        if isinstance(error, runtime.CompilerFailure) and error.invocation is not None and error.invocation.stderr:
            result['compiler_diagnostics'] = error.invocation.stderr[:8192].decode('utf-8', errors='replace')
    if args.format == 'json':
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    elif result.get('error'):
        print(f"typst-lint packages-status: blocked: {result['error']}")
        if result.get('compiler_diagnostics'):
            print(result['compiler_diagnostics'])
    else:
        def evidence(value):
            return 'unknown' if value is None else 'yes' if value is True else 'no' if value is False else str(value)
        print(f"Package report selection: {result['selection']}; future runtime selection is not proved.")
        if result['local']:
            report = result['local']
            print(f"Checkout vs selected ref: {report['checkout']['relation']} "
                  f"({report['upstream']['reason'] or report['checkout']['reason'] or 'observed'})")
            print(f"  Checkout dirty: {evidence(report['checkout']['dirty'])} ({report['checkout']['dirty_reason']})")
            for item in report['packages']:
                print(f"  {item['installed_path']}: upstream tree match={evidence(item['matches_upstream_tree'])}; "
                      f"checkout snapshot match={evidence(item['matches_checkout_snapshot'])}; "
                      f"checkout raw/HEAD match={evidence(item['checkout_raw_matches_head_tree'])}; "
                      f"index/HEAD match={evidence(item['index_matches_head_tree'])}; {item['reason'] or 'sampled'}")
        if result['preview_registry']:
            for item in result['preview_registry']['packages']:
                print(f"  @preview/{item['name']}:{item['requested_version']}: {item['status']} "
                      f"(latest={item['latest_version'] or 'unknown'})")
        if result['unrecognized_manifest_entries']:
            print(f"Unrecognized manifest entries: {len(result['unrecognized_manifest_entries'])}")
        print('No package was updated; compatibility and current runtime selection remain unproved.')
    if signals is not None and signals.first_signal is not None:
        return -signals.first_signal
    return result['status']
