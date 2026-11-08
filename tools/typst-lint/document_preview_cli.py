"""Explicit read-only document PDF preview from a caller-supplied resource manifest."""
import argparse
import json
from pathlib import Path
import stat
import sys

import closed_core_compile as runtime
import document_dependencies
import document_preview_compile
import document_preview_discover
import document_resources
import document_sequence_preview
import document_display
import include_context
import script_rule
from package_loader import PackageInputError, _absolute_root, read_snapshot_file
from typst_lint import InputError, SafetyError, VERSION, checked_path, load_rules, relative, require

MAX_MANIFEST_BYTES = 1024 * 1024
MAX_REVIEW_BYTES = 16 * 1024 * 1024


class NoPartialPreview(SafetyError):
    """Recognized unsupported sources exist, but no candidate can be compared."""
    def __init__(self, withheld, uninspected_rule_ids):
        super().__init__('Partial preview has no verified candidate edits')
        self.withheld = tuple(withheld)
        self.uninspected_rule_ids = tuple(uninspected_rule_ids)


class _PreviewParser(argparse.ArgumentParser):
    def __init__(self, *args, pdf_stream=False, **kwargs):
        self._pdf_stream = pdf_stream
        super().__init__(*args, **kwargs)

    def print_help(self, file=None):
        super().print_help(file or (sys.stderr if self._pdf_stream else None))


def review_sources(context, records, withheld_records=None):
    """Describe the same source sequences the compiler will independently replay.

    This is output for review, never proof of current live files or write authority.
    """
    indexed = {record['path']: record for record in records}
    withheld = {record['path']: record for record in (withheld_records or ())}
    sources = []
    for source in context.sources:
        record = indexed.get(source.path)
        row = dict(path=source.path, baseline_sha256=source.identity.source_sha256,
                   candidate_sha256=(record['candidate_sha256'] if record else source.identity.source_sha256),
                   steps=(record['steps'] if record else []),
                   unresolved_display_contexts=(record['unresolved_display_contexts'] if record else None))
        if withheld_records is not None:
            row['status'] = ('withheld' if source.path in withheld else 'candidate' if record else 'unchanged')
        sources.append(row)
    review = dict(schema=1, stage_offsets='each step applies to its own baseline_sha256',
                  replacements_may_contain_source_text=True, sources=sources)
    require(len(json.dumps(review, ensure_ascii=False).encode('utf-8')) <= MAX_REVIEW_BYTES,
            'Source review exceeds the serialized review size limit')
    return review


def describe_withheld(records, catalog):
    result = []
    for row in records:
        item = dict(path=row['path'], baseline_sha256=row['baseline_sha256'], rule_id=row['rule_id'],
                    reason_code=row['reason_code'], reason=row['reason'])
        if row['reason_code'] == 'caller-excluded-source':
            item['uninspected_rule_ids'] = [rule['id'] for rule in catalog]
        result.append(item)
    return result


def spacer_preview_summary(records):
    """Describe actual staged rule proposals without asserting their effect."""
    return dict(
        planned_edits=sum(len(step['edits']) for record in records for step in record['steps']
                          if step['rule_id'] == 'pde.unaligned-footnote-no-spacer'),
        policy=('The PDE rule forbids a spacer immediately before an equation-level footnote '
                'in a display without alignment or math line-break tokens.'),
        eligibility=('The preview selects supported literal tokens with conditional footnote '
                     'binding and reanalysis; spacer binding and runtime context remain unproved.'),
        render_effect=('Removing the helper may change visible content or layout; its effect '
                       'is not established by the source plan.'),
        pdf_attribution=('The PDF comparison covers all planned edits; it does not attribute '
                         'a difference to this rule or establish general visual equivalence.'))


def continuation_preview_summary(records):
    return dict(planned_edits=sum(len(step['edits']) for record in records for step in record['steps']
                                  if step['rule_id'] == 'pde.continuation-line'),
                eligibility='Only bounded literal prose sentence boundaries are selected; execution and prose ownership are unproved.',
                render_effect='Replacing source newlines may change rendering; visual equivalence is not established.',
                pdf_attribution='The PDF comparison covers all planned edits and does not attribute differences to this rule.')


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SafetyError('Duplicate resource manifest key')
        result[key] = value
    return result


def load_manifest(path, root, *, allow_empty=False):
    """Read one explicit manifest; serialized data grants no capture authority."""
    require(type(path) is str and path.startswith('/') and str(Path(path)) == path,
            'Manifest must be a canonical absolute path')
    raw, _ = runtime._capture(Path(path), MAX_MANIFEST_BYTES)
    return parse_manifest_bytes(raw, root, allow_empty=allow_empty)


def parse_manifest_bytes(raw, root, *, allow_empty=False):
    """Parse retained manifest bytes without rereading or granting authority."""
    require(type(raw) is bytes and len(raw) <= MAX_MANIFEST_BYTES, 'Invalid resource manifest bytes')
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
    require(type(files) is list and (allow_empty or bool(files)) and len(files) <= document_resources.MAX_FILES,
            'Manifest must contain a bounded explicit file list')
    requests = []
    for row in files:
        require(type(row) is dict and set(row) == {'root', 'path', 'role'},
                'Each manifest file requires root, path and role')
        requests.append(document_resources.ResourceRequest(row['root'], row['path'], row['role']))
    allow = data.get('allow_apple_asset_fonts', False)
    require(type(allow) is bool, 'Manifest Apple asset selection must be Boolean')
    return roots, requests, allow


def main(arguments=None, *, gate=False):
    words = list(sys.argv[1:] if arguments is None else arguments)
    pdf_requested = any(word.split('=', 1)[0] == '--pdf-stdout' for word in words)
    streams = None
    if pdf_requested:
        # Lazy import avoids the original compiler's manifest-parser import cycle.
        import document_original_compile_cli as streams
        if (streams._same_streams()
                or streams._regular_stream_id(getattr(sys.stderr, 'buffer', sys.stderr)) is not None):
            return 2
    parser = _PreviewParser(prog='typst-style compile --preview' if gate else 'typst-lint preview-compile',
        description=__doc__, allow_abbrev=False,
        epilog='The resource manifest is explicit. PDF bytes are emitted only with --pdf-stdout. '
               'The selected rules remain partial, so a successful preview exits 2.', pdf_stream=pdf_requested)
    parser.add_argument('--root', required=True, help='Canonical absolute consumer root')
    parser.add_argument('--entrypoint', required=True, help='Canonical project-relative .typ entrypoint')
    parser.add_argument('--manifest', required=True, help='Canonical absolute resource manifest JSON')
    parser.add_argument('--discover-dependencies', action='store_true',
                        help='Probe the baseline read-only and add observed dependency paths')
    parser.add_argument('--format', choices=('json', 'text'), default='text')
    parser.add_argument('--pdf-stdout', action='store_true', help='Emit only the candidate PDF to stdout')
    parser.add_argument('--review-edits', action='store_true',
                        help='Include bounded per-source, per-stage edits and hashes in the JSON report')
    parser.add_argument('--conditional-gaps', action='store_true',
                        help='Also preview conditional proof and nested math-footnote gap edits')
    parser.add_argument('--conditional-continuations', action='store_true',
                        help='Preview conditional prose continuation line joins; rendering equivalence is unproved')
    parser.add_argument('--conditional-punctuation', action='store_true',
                        help='Also preview conditional prose-footnote spacing (requires --conditional-gaps)')
    parser.add_argument('--conditional-spacers', action='store_true',
                        help='Also preview conditional unaligned footnote spacer removal; visible content or layout may change (requires --conditional-gaps)')
    parser.add_argument('--conditional-facade-footnotes', action='store_true',
                        help='Use captured facade namespaces for spacer footnotes (requires --conditional-spacers)')
    parser.add_argument('--partial-unsupported-preview', action='store_true',
                        help='Keep recognized unsupported deferred-footnote files unchanged (requires --conditional-facade-footnotes)')
    parser.add_argument('--partial-unproven-binding-preview', action='store_true',
                        help='Also keep exact source-footnote binding refusals unchanged (requires --partial-unsupported-preview)')
    parser.add_argument('--withhold-source', action='append', default=[], metavar='PROJECT_RELATIVE_PATH',
                        help='Keep this source unchanged through every rule stage; it can still execute during PDF compilation (may be repeated)')
    args = parser.parse_args(words)
    output_identity = error_identity = None
    if args.pdf_stdout:
        output_identity = streams._stream_identity(getattr(sys.stdout, 'buffer', sys.stdout))
        error_identity = streams._stream_identity(getattr(sys.stderr, 'buffer', sys.stderr))
        if output_identity is not None and output_identity[2] not in (stat.S_IFIFO, stat.S_IFSOCK):
            print(json.dumps(dict(schema=1, mode='preview-compile', status=2,
                error='PDF stdout must be a pipe or socket; regular-file aliases are refused',
                pdf_generated=False, pdf_streamed=False, publication_authority=False,
                output_stream_note='Shell redirection may already have truncated its target before this program starts.')),
                file=sys.stderr)
            return 2
    if args.review_edits and args.format != 'json' and not args.pdf_stdout:
        parser.error('--review-edits requires --format json or --pdf-stdout')
    if args.conditional_punctuation and not args.conditional_gaps:
        parser.error('--conditional-punctuation requires --conditional-gaps')
    if args.conditional_spacers and not args.conditional_gaps:
        parser.error('--conditional-spacers requires --conditional-gaps')
    if args.conditional_facade_footnotes and not args.conditional_spacers:
        parser.error('--conditional-facade-footnotes requires --conditional-spacers')
    if args.partial_unsupported_preview and not args.conditional_spacers:
        parser.error('--partial-unsupported-preview requires --conditional-spacers')
    if args.partial_unsupported_preview and not args.conditional_facade_footnotes:
        parser.error('--partial-unsupported-preview requires --conditional-facade-footnotes')
    if args.partial_unproven_binding_preview and not args.partial_unsupported_preview:
        parser.error('--partial-unproven-binding-preview requires --partial-unsupported-preview')
    partial_requested = args.partial_unsupported_preview or bool(args.withhold_source)
    # The executable installs cooperative SIGINT/SIGTERM handling; imported
    # callers do not change process-wide signal handlers.
    import offline_compile_cli
    signals = offline_compile_cli._active_signals
    cancel = signals.cancel_event if signals is not None else None
    phase = 'input'
    result = None
    discovery_completed = False
    starts = document_preview_compile.PreviewCompilerStarts()
    profile = ('conditional-gaps-punctuation-and-spacers' if args.conditional_punctuation and args.conditional_spacers else
               'conditional-gaps-and-spacers' if args.conditional_spacers else
               'conditional-gaps-and-punctuation' if args.conditional_punctuation else
               'conditional-gaps' if args.conditional_gaps else 'standard')
    try:
        raw_root = args.root
        root = _absolute_root(raw_root)
        require(raw_root == str(root), 'Preview root must be canonical and absolute')
        relative(args.entrypoint)
        require(args.entrypoint.endswith('.typ'), 'Preview entrypoint must be a Typst path')
        roots, requests, allow = load_manifest(args.manifest, root, allow_empty=args.discover_dependencies)
        _, source = read_snapshot_file(checked_path(root, args.entrypoint), include_context.MAX_SOURCE_BYTES)
        phase = 'planning'
        context = include_context.load_include_context(root, ((args.entrypoint, source),), budget='book-v1')
        catalog, catalog_hash = load_rules()
        explicit_paths = tuple(args.withhold_source)
        explicit = document_sequence_preview.validate_explicit_withheld(context, explicit_paths)
        def plan_records(evidence):
            records, withheld = [], []
            for item in context.sources:
                if cancel is not None and cancel.is_set():
                    raise runtime.CompilerFailure('cancelled', kind=runtime.CompilerFailureKind.CANCELLED)
                if item.path in explicit:
                    withheld.append(document_sequence_preview.explicit_withheld_record(item))
                    continue
                record = document_sequence_preview.preview(item.tree, item.path, catalog['rules'], profile=profile,
                                                           footnote_evidence=evidence,
                                                           classify_deferred=args.partial_unsupported_preview,
                                                           classify_unproven_binding=args.partial_unproven_binding_preview,
                                                           conditional_continuations=args.conditional_continuations)
                if record is not None:
                    if record['verification'] == 'withheld':
                        if (args.partial_unsupported_preview and
                                (record.get('reason_code') == 'unsupported-deferred-footnote-binding'
                                 or (args.partial_unproven_binding_preview and record.get('reason_code')
                                     == 'unproven-source-footnote-binding'))):
                            withheld.append(record)
                        else:
                            raise SafetyError(f"{item.path}: {record['reason']}")
                    else:
                        records.append(record)
            if withheld and not records:
                raise NoPartialPreview(withheld, [rule['id'] for rule in catalog['rules'] if rule['id'] not in
                    (document_display.STANDALONE, script_rule.RULE_ID, document_display.RULE_ID)])
            return records, withheld

        # Preserve the existing fail-before-discovery behavior unless this
        # profile needs a resource-bound facade proof for planning.
        planned = None if args.conditional_facade_footnotes else plan_records(None)
        footnote_session = None
        if args.conditional_facade_footnotes:
            import conditional_footnote_binding
            import namespace_multiroot
            require('packages' in roots and 'cache' in roots, 'Facade footnotes require package and cache roots')
            namespaces = namespace_multiroot.load_multiroot_capture(roots['packages'], roots['cache'])
            footnote_session = conditional_footnote_binding.ConditionalFootnoteBinding(context, namespaces)
        observed = None
        discovery_baseline_sha256 = None
        font_hashes = {}
        if args.discover_dependencies:
            phase = 'discovery'
            raw_dependencies, observed, font_hashes, discovery_baseline_sha256 = document_preview_discover.discover(
                context, roots, requests, entrypoint=args.entrypoint,
                allow_apple_asset_fonts=allow, cancel_event=cancel, starts=starts)
            discovery_completed = True
            selected = {(item.root, item.path): item for item in requests}
            for root_id, path in observed:
                role = ('source' if root_id == 'project' and path.endswith('.typ') else
                        'resource' if root_id == 'project' else 'package')
                selected.setdefault((root_id, path), document_resources.ResourceRequest(root_id, path, role))
            for item in context.sources:
                selected.setdefault(('project', item.path),
                                    document_resources.ResourceRequest('project', item.path, 'source'))
            requests = list(selected.values())
        if footnote_session is not None:
            selected = {(item.root, item.path): item for item in requests}
            for item in footnote_session._graph.sources:
                selected.setdefault(('project', item.path),
                                    document_resources.ResourceRequest('project', item.path, 'source'))
            choices = {item.import_path: item.root_index for item in footnote_session._namespaces.selections}
            for item in footnote_session._namespaces.packages:
                index = choices[item.evidence.import_path]
                require(index in (0, 1), 'Invalid namespace package selection')
                root_id = ('packages', 'cache')[index]
                root_path = Path(footnote_session._namespaces.roots[index])
                for identity in (item.evidence.manifest_identity, item.evidence.source_identity):
                    require(type(identity) is str, 'Namespace evidence lacks a captured package path')
                    try:
                        relative_path = Path(identity).relative_to(root_path).as_posix()
                    except ValueError as error:
                        raise SafetyError('Namespace evidence is outside its selected root') from error
                    selected.setdefault((root_id, relative_path),
                                        document_resources.ResourceRequest(root_id, relative_path, 'package'))
            requests = list(selected.values())
        phase = 'capture'
        snapshot = document_resources.capture(roots, requests, allow_apple_asset_fonts=allow)
        if observed is not None:
            captured_fonts = {(item.request.root, item.request.path): item.sha256 for item in snapshot.files
                              if item.request.role == 'font'}
            require(captured_fonts == font_hashes, 'Selected fonts changed after dependency discovery')
            comparison = document_dependencies.compare(snapshot, raw_dependencies, cwd=str(root), roots=roots)
            require(not comparison.missing, 'A discovered dependency is absent from the resource capture')
        footnote_evidence = None
        if footnote_session is not None:
            import document_footnote_evidence
            footnote_evidence = document_footnote_evidence.PreviewFootnoteEvidence(footnote_session, snapshot)
        if planned is None:
            phase = 'planning'
            planned = plan_records(footnote_evidence)
        records, withheld = planned
        review = review_sources(context, records, withheld if partial_requested else None) if args.review_edits else None
        phase = 'compile'
        preview = document_preview_compile.compile_preview(context, records, catalog['rules'], snapshot,
                                                           entrypoint=args.entrypoint, cancel_event=cancel,
                                                           expected_dependencies=observed,
                                                           expected_baseline_sha256=discovery_baseline_sha256,
                                                           profile=profile, starts=starts,
                                                           footnote_evidence=footnote_evidence,
                                                           partial_unsupported=args.partial_unsupported_preview,
                                                           withheld_records=withheld if partial_requested else None,
                                                           partial_unproven_binding=args.partial_unproven_binding_preview,
                                                           explicit_withheld_sources=explicit_paths,
                                                           conditional_continuations=args.conditional_continuations)
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
                      discovery_performed=observed is not None,
                      discovery_observed_dependencies=len(observed) if observed is not None else None,
                      dependency_closure_proved=False, compiler_sha256=preview.compiler_sha256,
                      sequence_profile=profile,
                      conditional_facade_footnotes=args.conditional_facade_footnotes,
                      conditionally_previewed_rule_ids=(
                          (['pde.proof-trailing-gap', 'pde.footnote-trailing-gap'] if args.conditional_gaps else [])
                          + (['pde.prose-footnote-punctuation'] if args.conditional_punctuation else [])
                          + (['pde.unaligned-footnote-no-spacer'] if args.conditional_spacers else [])
                          + (['pde.continuation-line'] if args.conditional_continuations else [])),
                      partially_inspected_rule_ids=[document_display.STANDALONE, script_rule.RULE_ID, document_display.RULE_ID],
                      uninspected_rule_ids=[rule['id'] for rule in catalog['rules'] if rule['id'] not in
                                           (document_display.STANDALONE, script_rule.RULE_ID, document_display.RULE_ID)],
                      source_apply_gate=dict(eligible=False,
                          blockers=['global_rule_coverage_incomplete', 'dependency_closure_unproved',
                                    'live_source_freshness_unproved', 'protected_source_transaction_unavailable']
                                   + ([] if preview.pdfs_identical else ['preview_pdfs_differ'])
                                   + (['partial_preview_withheld_files'] if withheld else [])))
        if partial_requested:
            if args.partial_unsupported_preview:
                result['partial_unsupported_preview'] = True
            if args.withhold_source:
                result['explicit_source_withholding'] = True
            if args.partial_unproven_binding_preview:
                result['partial_unproven_binding_preview'] = True
            result['withheld_sources'] = describe_withheld(withheld, catalog['rules'])
        if args.conditional_spacers:
            result['conditional_spacer_preview'] = spacer_preview_summary(records)
        if args.conditional_continuations:
            result['conditional_continuation_preview'] = continuation_preview_summary(records)
        if review is not None:
            result['source_review'] = review
        if cancel is not None and cancel.is_set():
            raise runtime.CompilerFailure('cancelled', kind=runtime.CompilerFailureKind.CANCELLED)
        if args.pdf_stdout:
            phase = 'stream'
            if (streams._same_streams()
                    or streams._regular_stream_id(getattr(sys.stderr, 'buffer', sys.stderr)) is not None
                    or streams._stream_identity(getattr(sys.stderr, 'buffer', sys.stderr)) != error_identity):
                return 2
            require(streams._stream_identity(getattr(sys.stdout, 'buffer', sys.stdout)) == output_identity,
                    'PDF stdout stream changed before transfer')
            streams._write_pdf(preview.candidate_pdf, cancel)
            if cancel is not None and cancel.is_set():
                raise runtime.CompilerFailure('cancelled', kind=runtime.CompilerFailureKind.CANCELLED)
            result['pdf_streamed'] = True
    except (InputError, SafetyError, PackageInputError, OSError, ValueError, runtime.CompilerFailure) as error:
        result = dict(schema=1, tool_version=VERSION, mode='preview-compile', status=3 if phase == 'stream' else 2,
                      phase=phase, compiler_stage=None, pdf_generated=(phase == 'stream'),
                      source_files_modified=False, publication_authority=False, error=str(error))
        if args.pdf_stdout and phase == 'stream':
            result['stdout_may_be_partial'] = True
        if isinstance(error, NoPartialPreview):
            if args.partial_unsupported_preview:
                result['partial_unsupported_preview'] = True
            if args.withhold_source:
                result['explicit_source_withholding'] = True
            if args.partial_unproven_binding_preview:
                result['partial_unproven_binding_preview'] = True
            result['global_coverage_complete'] = False
            result['uninspected_rule_ids'] = list(error.uninspected_rule_ids)
            result['withheld_sources'] = describe_withheld(error.withheld, catalog['rules'])
            result['source_apply_gate'] = dict(eligible=False, blockers=['global_rule_coverage_incomplete',
                'partial_preview_withheld_files', 'no_verified_candidate_edits'])
            if args.conditional_spacers:
                result['conditional_spacer_preview'] = spacer_preview_summary([])
            if args.conditional_continuations:
                result['conditional_continuation_preview'] = continuation_preview_summary([])
        if isinstance(error, runtime.CompilerFailure) and error.invocation is not None:
            starts.observe(error.invocation)
            result['compiler_stage'] = document_preview_compile._PURPOSE_STAGES.get(error.invocation.purpose)
            if error.invocation.stderr:
                result['compiler_diagnostics'] = error.invocation.stderr[:8192].decode('utf-8', errors='replace')
        elif isinstance(error, document_preview_compile.PreviewCompilerDiagnostics):
            starts.observe(error.invocation)
            stage = document_preview_compile._PURPOSE_STAGES.get(error.invocation.purpose)
            result['compiler_stage'] = stage if stage == error.stage else None
            if error.invocation.stderr:
                result['compiler_diagnostics'] = error.invocation.stderr[:8192].decode('utf-8', errors='replace')
    stage_starts = starts.stages()
    if discovery_completed:
        stage_starts['discovery'] = True
    if result.get('pdf_generated'):
        stage_starts['baseline'] = stage_starts['candidate'] = True
    result['compiler_stages_started'] = stage_starts
    result['compiler_invoked'] = document_preview_compile.PreviewCompilerStarts._combine(stage_starts.values())
    result['compiler_version_probe_invoked'] = starts.version_probe_invoked()
    if args.pdf_stdout:
        result.setdefault('pdf_streamed', False)
        result['source_modification_scope'] = ('Tool-controlled actions only; caller redirection and '
                                               'downstream pipe consumers are outside this report')
        result['output_stream_note'] = ('Stdout and stderr are caller-managed; shell redirection can truncate files '
            'before this program starts. Stdout requires a pipe or socket. A broken stdout stream can contain '
            'a partial PDF. Stderr regular files are refused without a report.')
        if (streams._same_streams()
                or streams._regular_stream_id(getattr(sys.stderr, 'buffer', sys.stderr)) is not None
                or streams._stream_identity(getattr(sys.stderr, 'buffer', sys.stderr)) != error_identity):
            return 2
    stream = sys.stderr if args.pdf_stdout else sys.stdout
    if args.format == 'json' or args.pdf_stdout:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2), file=stream)
    elif result.get('error'):
        stage = f" during {result['compiler_stage']}" if result.get('compiler_stage') else ''
        print(f"typst-lint preview-compile: blocked{stage}: {result['error']}", file=stream)
        if result.get('compiler_stage'):
            print(f"Compiler invoked: {result['compiler_invoked']}", file=stream)
        if result.get('compiler_diagnostics'):
            print(result['compiler_diagnostics'], file=stream)
        if result.get('partial_unsupported_preview') or result.get('explicit_source_withholding'):
            print('Global rule coverage remains incomplete; source application is unavailable.', file=stream)
            for row in result['withheld_sources']:
                print(f"{row['path']}: {row['rule_id']} {row['reason_code']} "
                      f"baseline sha256={row['baseline_sha256']}: {row['reason']}", file=stream)
            print('Uninspected rules: ' + ', '.join(result['uninspected_rule_ids']), file=stream)
        if 'conditional_spacer_preview' in result:
            info = result['conditional_spacer_preview']
            print(f"Conditional spacer removals planned: {info['planned_edits']}. {info['policy']} "
                  f"{info['eligibility']} {info['render_effect']} {info['pdf_attribution']}", file=stream)
        if 'conditional_continuation_preview' in result:
            info = result['conditional_continuation_preview']
            print(f"Conditional continuation joins planned: {info['planned_edits']}. "
                  f"{info['eligibility']} {info['render_effect']} {info['pdf_attribution']}", file=stream)
    else:
        print(f"typst-lint preview-compile: PDF generated; {result['observed_dependencies']} observed dependencies; "
              f"baseline/candidate identical: {result['pdfs_identical']}", file=stream)
        print('Sources were not modified; global rule coverage remains incomplete (exit 2).', file=stream)
        if 'conditional_spacer_preview' in result:
            info = result['conditional_spacer_preview']
            print(f"Conditional spacer removals planned: {info['planned_edits']}. {info['policy']} "
                  f"{info['eligibility']} {info['render_effect']} {info['pdf_attribution']}", file=stream)
        if 'conditional_continuation_preview' in result:
            info = result['conditional_continuation_preview']
            print(f"Conditional continuation joins planned: {info['planned_edits']}. "
                  f"{info['eligibility']} {info['render_effect']} {info['pdf_attribution']}", file=stream)
        if partial_requested:
            print(f"Partial preview: {len(result['withheld_sources'])} source(s) kept at their captured original bytes; "
                  f"{result['planned_sources']} source(s) have candidate edits.", file=stream)
            for row in result['withheld_sources']:
                print(f"{row['path']}: {row['rule_id']} {row['reason_code']} "
                      f"baseline sha256={row['baseline_sha256']}: {row['reason']}", file=stream)
            print('Uninspected rules: ' + ', '.join(result['uninspected_rule_ids']), file=stream)
    if signals is not None and signals.first_signal is not None:
        return -signals.first_signal
    return result['status']
