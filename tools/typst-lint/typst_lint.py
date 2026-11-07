"""Partial read-only analysis and explicit offline closed-core source/PDF routes."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tomllib

VERSION = '0.4.0-rules'
CATALOG = Path(__file__).with_name('rules') / 'catalog.json'
# Retained selected-source bytes, excluding bounded per-file read/verify buffers.
MAX_CAPTURE_BYTES = 64 * 1024 * 1024


class InputError(ValueError):
    """Invalid configuration, input, or unsupported analysis (exit 2)."""


class SafetyError(ValueError):
    """Unsafe or nonconvergent candidate (exit 3)."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise InputError(message)


def strings(value, name, *, nonempty=True):
    require(isinstance(value, list) and (bool(value) or not nonempty)
            and all(isinstance(x, str) and x.strip() for x in value),
            f'{name} must be a list of nonempty strings')
    return tuple(value)


def relative(value: str):
    require(isinstance(value, str) and bool(value) and not value.startswith('/')
            and '\\' not in value and '\x00' not in value
            and all(p not in ('', '.', '..') for p in value.split('/')),
            f'Expected canonical root-relative path: {value!r}')
    return value


def pattern(value: str):
    relative(value)
    require('[' not in value and ']' not in value and '?' not in value
            and all('**' not in p or p == '**' for p in value.split('/')),
            f'Unsupported glob (use * or whole-segment **): {value}')
    return value


def matches(path: str, glob: str) -> bool:
    """Segment-aware glob; ** can match zero directories, including at root."""
    parts, pats = path.split('/'), glob.split('/')

    @lru_cache(None)
    def match(i, j):
        if j == len(pats):
            return i == len(parts)
        if pats[j] == '**':
            return match(i, j + 1) or (i < len(parts) and match(i + 1, j))
        expression = re.escape(pats[j]).replace(r'\*', '[^/]*')
        return i < len(parts) and bool(re.fullmatch(expression, parts[i])) and match(i + 1, j + 1)
    return match(0, 0)


def parse_rules(raw: bytes):
    """Parse retained immutable catalog bytes without filesystem access."""
    require(type(raw) is bytes, 'Rule catalog must be immutable bytes')
    data = json.loads(raw)
    require(isinstance(data, dict) and type(data.get('schema')) is int and data['schema'] == 1, 'Invalid rule catalog schema')
    require(data.get('version') == VERSION, 'Rule catalog version mismatch')
    require(data.get('packs') == {'core': ['core'], 'pde': ['core', 'pde']}, 'Invalid pack composition')
    require(isinstance(data.get('rules'), list), 'Missing rule catalog')
    ids = set()
    for rule in data['rules']:
        require(isinstance(rule, dict), 'Invalid rule record')
        rid = rule.get('id')
        require(isinstance(rid, str) and re.fullmatch(r'(core|pde)\.[a-z]+(?:-[a-z]+)*', rid)
                and rid not in ids, f'Invalid or duplicate rule ID: {rid}')
        ids.add(rid)
        require(rule.get('grade') in ('error', 'warning'), f'Invalid grade: {rid}')
        require(rule.get('repair') in ('automatic', 'conditional', 'diagnostic-only'), f'Invalid repair policy: {rid}')
        for key in ('source', 'description', 'rewrite', 'preconditions'):
            require(isinstance(rule.get(key), str) and bool(rule[key].strip()), f'Missing {key}: {rid}')
    require(bool(ids), 'Empty rule catalog')
    return data, digest(raw)


def load_rules(path=CATALOG):
    return parse_rules(path.read_bytes())


@dataclass(frozen=True)
class Ignore:
    paths: tuple[str, ...]
    rules: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class Config:
    pack: str = 'core'
    include: tuple[str, ...] = ('**/*.typ',)
    exclude: tuple[str, ...] = ()
    ignores: tuple[Ignore, ...] = ()
    entrypoint: str | None = None
    catalog: str | None = None
    sha256: str | None = None
    capitalization_language: str | None = None

    def selects(self, path):
        return any(matches(path, g) for g in self.include) and not any(matches(path, g) for g in self.exclude)


def checked_path(root: Path, name: str) -> Path:
    relative(name)
    path = root
    for part in name.split('/'):
        path = path / part
        if path.is_symlink():
            raise InputError(f'Symbolic links are unsupported: {name}')
    require(path.resolve().is_relative_to(root), f'Path escapes root: {name}')
    mode = path.stat()
    require(stat.S_ISREG(mode.st_mode), f'Not a regular file: {name}')
    require(mode.st_nlink == 1, f'Hard links are unsupported: {name}')
    return path


def read_config(root, rules):
    path = root / '.typst-lint.toml'
    if not path.exists() and not path.is_symlink():
        return Config()
    raw = checked_path(root, '.typst-lint.toml').read_bytes()
    data = tomllib.loads(raw.decode('utf-8'))
    require(not (data.keys() - {'rule-set', 'include', 'exclude', 'ignore', 'entrypoint', 'catalog', 'capitalization'}), 'Unknown configuration key')
    pack = data.get('rule-set', 'core')
    require(isinstance(pack, str) and pack in ('core', 'pde'), f'Unknown rule-set: {pack!r}')
    include = strings(data.get('include', ['**/*.typ']), 'include')
    exclude = strings(data.get('exclude', []), 'exclude', nonempty=False)
    for glob in include + exclude:
        pattern(glob)
    ignores = data.get('ignore', [])
    require(isinstance(ignores, list), 'ignore must be an array of tables')
    parsed = []
    ids = {r['id'] for r in rules}
    for item in ignores:
        require(isinstance(item, dict) and set(item) == {'paths', 'rules', 'reason'}, 'Invalid ignore fields')
        paths = strings(item['paths'], 'ignore.paths')
        for glob in paths:
            pattern(glob)
        selected = strings(item['rules'], 'ignore.rules')
        require(set(selected) <= ids, f'Unknown ignore rule IDs: {sorted(set(selected) - ids)}')
        require(isinstance(item['reason'], str) and bool(item['reason'].strip()), 'Ignore requires a reason')
        parsed.append(Ignore(paths, selected, item['reason']))
    capitalization = data.get('capitalization', {})
    require(isinstance(capitalization, dict) and not (capitalization.keys() - {'language'}),
            'Invalid capitalization table or unknown fields')
    language = capitalization.get('language')
    if 'language' in capitalization:
        require(isinstance(language, str) and bool(language.strip()),
                'capitalization.language must be a nonempty string')
    for key in ('entrypoint', 'catalog'):
        if key in data:
            relative(data[key])
    return Config(pack, include, exclude, tuple(parsed), data.get('entrypoint'), data.get('catalog'), digest(raw), language)


def read_reference_catalog(path):
    """Digest-compatible wrapper; analysis uses the immutable loader directly."""
    from reference_catalog import load_reference_catalog
    return load_reference_catalog(path).sha256


@dataclass(frozen=True)
class _SelectedSource:
    path: str
    source: bytes = field(repr=False)
    sha256: str = field(init=False)

    def __post_init__(self):
        require(type(self.source) is bytes, 'Selected source must be immutable bytes')
        object.__setattr__(self, 'sha256', digest(self.source))

    def record(self):
        return dict(path=self.path, sha256=self.sha256, bytes=len(self.source))


def _read_selected_source(root, name, *, reread=False):
    # These modules depend on typst_lint primitives; keep initialization lazy.
    from package_loader import PackageInputError, read_snapshot_file
    from syntax_client import MAX_INPUT
    try:
        _, raw = read_snapshot_file(checked_path(root, name), MAX_INPUT)
    except PackageInputError as error:
        action = 'reread' if reread else 'read'
        raise InputError(f'Cannot safely {action} selected input {name}: {error}') from error
    return raw


def capture_selected_sources(root, config):
    """Capture selected UTF-8 bytes, at most 1 MiB each and 64 MiB in total.

    Per-file descriptor-verified reads are not an atomic directory snapshot or
    an editor lease. Selection/exclusion semantics are unchanged; no imports or
    dependency paths are traversed. Retained descriptors close after each read.
    """
    selected = []
    captured_bytes = 0
    def onerror(error):
        raise error
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=onerror):
        dirs.sort()
        for name in dirs[:]:
            child = Path(directory) / name
            rel = child.relative_to(root).as_posix()
            # A directory-only exclusion must also exclude all its descendants.
            excluded = any(g.endswith('/**') and matches(rel, g[:-3]) for g in config.exclude)
            if excluded:
                dirs.remove(name)
            elif child.is_symlink():
                raise InputError(f'Symbolic-link directory is unsupported: {rel}')
        for name in sorted(files):
            rel = (Path(directory) / name).relative_to(root).as_posix()
            if not config.selects(rel):
                continue
            require(Path(rel).suffix in ('.typ', '.md'), f'Unsupported selected input: {rel}')
            raw = _read_selected_source(root, rel)
            captured_bytes += len(raw)
            require(captured_bytes <= MAX_CAPTURE_BYTES,
                    f'Selected inputs exceed aggregate capture byte limit ({MAX_CAPTURE_BYTES}): {rel}')
            selected.append(_SelectedSource(rel, raw))
    return tuple(sorted(selected, key=lambda item: item.path))


def collect(root, config):
    """Return the public metadata-only projection of safely captured sources."""
    return [item.record() for item in capture_selected_sources(root, config)]


@dataclass(frozen=True)
class Diagnostic:
    rule_id: str
    grade: str
    path: str
    line: int
    column: int
    explanation: str
    source: str


def diagnostics(findings, config, rules):
    """Validate/order style findings and apply path ignores, never input errors."""
    catalog = {r['id']: r for r in rules}
    require(config.pack in ('core', 'pde'), f'Unknown diagnostic rule-set: {config.pack!r}')
    active, suppressed = [], []
    for finding in findings:
        rule = catalog.get(finding.rule_id)
        require(rule is not None and finding.grade == rule['grade'] and finding.source == rule['source'], 'Diagnostic metadata mismatch')
        require(config.pack == 'pde' or finding.rule_id.startswith('core.'),
                f'Diagnostic backend mismatch: disabled rule {finding.rule_id} for {config.pack}')
        relative(finding.path)
        require(type(finding.line) is int and finding.line > 0 and type(finding.column) is int and finding.column > 0, 'Invalid diagnostic position')
        require(bool(finding.explanation.strip()), 'Empty diagnostic explanation')
        if not config.selects(finding.path):
            continue
        ignored = any(finding.rule_id in i.rules and any(matches(finding.path, g) for g in i.paths) for i in config.ignores)
        (suppressed if ignored else active).append(finding)
    key = lambda d: (d.path, d.line, d.column, d.rule_id)
    return tuple(sorted(active, key=key)), tuple(sorted(suppressed, key=key))


def report(root, mode, *, package_path=None, _verify=True, _analysis_observer=None):
    catalog, rule_hash = load_rules()
    config = read_config(root, catalog['rules'])
    enabled = [r for r in catalog['rules'] if r['id'].split('.')[0] in catalog['packs'][config.pack]]
    selected_sources = capture_selected_sources(root, config)
    inputs = [item.record() for item in selected_sources]
    entry_hash = None
    entrypoint = None
    if config.entrypoint:
        require(config.entrypoint.endswith('.typ'), 'Entrypoint must be a Typst file')
        from book_provenance import EntrypointSnapshot
        from package_loader import PackageInputError, read_snapshot_file
        from syntax_client import MAX_INPUT
        try:
            _, raw = read_snapshot_file(checked_path(root, config.entrypoint), MAX_INPUT)
        except PackageInputError as error:
            raise InputError(f'Cannot safely read configured entrypoint: {error}') from error
        entry_hash = digest(raw)
        selected_entry = next((item for item in selected_sources if item.path == config.entrypoint), None)
        require(selected_entry is None or (selected_entry.sha256 == entry_hash and selected_entry.source == raw),
                f'Configured entrypoint changed after input selection: {config.entrypoint}')
        entrypoint = EntrypointSnapshot(config.entrypoint, raw)
    from reference_catalog import load_reference_catalog
    reference_catalog = load_reference_catalog(checked_path(root, config.catalog)) if config.catalog else None
    reference_hash = reference_catalog.sha256 if reference_catalog is not None else None
    # Imported here to keep pure configuration/edit primitives independent of the adapter.
    from syntax_client import AdapterError, parse
    from source_analysis import AnalysisContext, analyze, rule_ids
    from static_resolver import imported_paths
    from package_loader import load_packages
    from suppression import SuppressionError
    from markdown_scripts import MarkdownContext, RULE_ID as SCRIPT
    backends = rule_ids(config.pack)

    def applicable(name):
        return backends if name.endswith('.typ') else (SCRIPT,)
    active_findings, proposals, file_coverage, parse_failures = [], [], [], []
    suppressed_count = 0
    suppressed_by_rule = {}
    configuration_failures = []
    convergence_evidence = []
    evaluated, parsed, markdown = [], {}, {}
    for item in selected_sources:
        name = item.path
        if name.endswith('.md'):
            file_coverage.extend(dict(path=name, rule_id=rid, status='not-applicable',
                reasons=['Only math-script checking applies to Markdown examples'])
                for rid in backends if rid != SCRIPT)
        try:
            if entrypoint is not None and name == entrypoint.path:
                try:
                    _, raw = read_snapshot_file(checked_path(root, name), MAX_INPUT)
                except PackageInputError as error:
                    raise InputError(f'Cannot safely reread configured entrypoint: {error}') from error
            else:
                raw = _read_selected_source(root, name, reread=True)
            require(digest(raw) == item.sha256 and raw == item.source, f'Input changed during analysis: {name}')
            require(entrypoint is None or name != entrypoint.path or raw == entrypoint.source,
                    f'Configured entrypoint changed before parsing: {name}')
            if name.endswith('.md'):
                markdown[name] = item.source
            else:
                parsed[name] = parse(item.source)
        except (AdapterError, OSError, InputError) as error:
            file_coverage.extend(dict(path=name, rule_id=rid, status='unsupported', reasons=[str(error)])
                                 for rid in applicable(name))
    # Dependency bytes are captured before package lookup. Facade analysis and
    # candidate passes use only these retained inputs, never ambient caches.
    from import_graph import import_request, load_import_graph
    has_relative_import = any(
        request is not None and not request.startswith('@')
        for tree in parsed.values() for index, node in enumerate(tree.nodes)
        if node.kind == 'ModuleImport' for request in (import_request(tree, index),))
    import_graph = None
    if parsed and (config.pack == 'pde' or has_relative_import):
        import_graph = load_import_graph(root, ((item.path, item.source) for item in selected_sources
                                               if item.path in parsed))
    package_sources, package_evidence, package_unavailable = (), [], []
    resolved_package_path = None
    if package_path is not None and any(item.path.endswith('.typ') for item in selected_sources):
        requested = {name for tree in parsed.values() for name in imported_paths(tree)}
        if import_graph is not None:
            unavailable_sources = {item.path for item in import_graph.unavailable if not item.request}
            # Parse one dependency at a time rather than retaining another
            # graph-sized collection of syntax trees. Invalid dependencies
            # cannot grant facade identity or trigger speculative package reads.
            for dependency in import_graph.sources:
                if dependency.path in parsed:
                    require(parsed[dependency.path].source == dependency.source,
                            'Captured dependency graph differs from selected source bytes')
                    continue
                # Capture already recorded a syntax/resource or unresolved
                # import-context refusal. Preserve that negative evidence;
                # supplying a package root must not retry the refused parse.
                if dependency.path in unavailable_sources:
                    continue
                dependency_tree = parse(dependency.source)
                if not dependency_tree.nodes[0].errors:
                    requested.update(imported_paths(dependency_tree))
        loaded = load_packages(package_path, sorted(requested))
        package_sources = loaded.sources
        package_evidence = [asdict(record) for record in loaded.evidence]
        package_unavailable = [asdict(record) for record in loaded.unavailable]
        resolved_package_path = loaded.package_path
    for item in inputs:
        name = item['path']
        if name not in parsed and name not in markdown:
            continue
        ignored = frozenset(rid for ignore in config.ignores if any(matches(name, glob) for glob in ignore.paths)
                            for rid in ignore.rules)
        context = (MarkdownContext(markdown[name], name, ignored) if name in markdown else
            AnalysisContext(parsed[name], name, config.pack, config.capitalization_language,
                            tuple(package_sources), reference_catalog, entrypoint, import_graph, ignored))
        if _analysis_observer is not None:
            _analysis_observer(name, context)
        try:
            if name in markdown:
                from markdown_scripts import analyze as analyze_markdown
                results = {SCRIPT: analyze_markdown(context.source, name, ignored=ignored, _verify=_verify)}
            else:
                results = analyze(parsed[name], name, pack=config.pack,
                                  fallback_language=config.capitalization_language,
                                  packages=package_sources, reference_catalog=reference_catalog,
                                  entrypoint=entrypoint, import_graph=import_graph, ignored=ignored, _verify=_verify)
        except SuppressionError as error:
            configuration_failures.append(dict(path=name, error=str(error)))
            file_coverage.extend(dict(path=name, rule_id=rid, status='configuration-failure', reasons=[str(error)])
                                 for rid in applicable(name))
            continue
        except (AdapterError, OSError, InputError) as error:
            file_coverage.extend(dict(path=name, rule_id=rid, status='unsupported', reasons=[str(error)])
                                 for rid in applicable(name))
            continue
        steps = next((getattr(result, 'repair_steps', ()) for result in results.values()
                      if getattr(result, 'repair_passes', 0) > 1), ())
        if steps:
            convergence_evidence.append(dict(path=name, baseline_sha256=item['sha256'],
                candidate_sha256=digest(steps[-1].after), passes=len(steps),
                steps=[dict(before_sha256=digest(step.before), after_sha256=digest(step.after),
                    applied=[dict(start=a.edit.start, end=a.edit.end,
                                  replacement=a.edit.replacement.decode('utf-8'),
                                  rule_id=a.primary_rule_id, rule_ids=list(a.rule_ids))
                             for a in step.applied],
                    moves=[list(move) for move in step.moves]) for step in steps]))
        errors = sorted({span for result in results.values() for span in result.parse_errors})
        if errors:
            parse_failures.append(dict(path=name, spans=[dict(start=a, end=b) for a, b in errors]))
        for rid, result in results.items():
            active, hidden = diagnostics(result.diagnostics, config, catalog['rules'])
            active_findings.extend(active)
            suppressed_count += len(hidden) + len(result.suppressed)
            for finding in (*hidden, *result.suppressed):
                suppressed_by_rule[finding.rule_id] = suppressed_by_rule.get(finding.rule_id, 0) + 1
            if result.skips:
                file_coverage.append(dict(path=name, rule_id=rid, status='partial', reasons=list(result.skips)))
            else:
                evaluated.append(dict(path=name, rule_id=rid))
                file_coverage.append(dict(path=name, rule_id=rid, status='evaluated', reasons=[]))
            if result.edits and rid not in ignored:
                attributed = {a.edit: a for a in getattr(result, 'attributed_edits', ())}
                for e in result.edits:
                    record = dict(path=name, start=e.start, end=e.end,
                                  replacement=e.replacement.decode('utf-8'), rule_id=rid,
                                  baseline_sha256=item['sha256'])
                    proof = attributed.get(e)
                    if proof is not None and len(proof.rule_ids) > 1:
                        record['rule_ids'] = list(proof.rule_ids)
                    if getattr(result, 'repair_passes', 0) > 1:
                        record['repair_passes'] = result.repair_passes
                    proposals.append(record)
    active_findings.sort(key=lambda d: (d.path, d.line, d.column, d.rule_id))
    proposals.sort(key=lambda e: (e['path'], e['start'], e['end'], e['rule_id']))
    applicable_counts = {rid: sum(rid in applicable(item['path']) for item in inputs) for rid in backends}
    complete_backends = {rid for rid in backends if applicable_counts[rid] and
                         sum(row['rule_id'] == rid for row in evaluated) == applicable_counts[rid]}
    unsupported = [dict(rule_id=r['id'], reason=(
        'No applicable selected input for this rule' if not applicable_counts.get(r['id'], 0) else
        'See per-file supported-source coverage' if r['id'] in backends else
        'Binding/rule backend not implemented')) for r in enabled if r['id'] not in complete_backends]

    # available_backends reports wiring only: each backend can still be partial
    # or unavailable for a particular input. It never implies evaluated/complete.
    return dict(schema=1, tool_version=VERSION, pack_version=catalog['version'], mode=mode,
                status=2, phase='analysis', compiler_invoked=False, compiler_status=None,
                rule_set=config.pack, inputs=inputs,
                hashes=dict(config=config.sha256, rules=rule_hash, entrypoint=entry_hash, reference_catalog=reference_hash),
                package_path=resolved_package_path, package_evidence=package_evidence, package_unavailable=package_unavailable,
                diagnostics=[asdict(d) for d in active_findings], suppressed_count=suppressed_count,
                applied_fixes=[], proposed_edits=proposals, proposal_status='partial-not-applicable-transaction',
                parse_failures=parse_failures, configuration_failures=configuration_failures,
                suppressed_by_rule=dict(sorted(suppressed_by_rule.items())), recovery=None,
                coverage=dict(complete=False, enabled=[r['id'] for r in enabled],
                              available_backends=list(backends),
                              implemented=[rid for rid in backends if any(row['rule_id'] == rid for row in evaluated)], evaluated=evaluated,
                              files=file_coverage, unsupported=unsupported,
                              optional_skips=[] if config.catalog else [dict(check='catalog-membership', reason='No catalog configured')]),
                blockers=['Required rule coverage remains incomplete; general Markdown/dependency resolution and full rule acceptance are unavailable',
                          'Protected source transactions and immutable compile snapshots are unavailable'],
                routes=dict(cli='unmanaged', ci='unmanaged', watch='unmanaged', editor='unmanaged'),
                **({'convergence_evidence': convergence_evidence} if convergence_evidence else {}))


def main(argv=None, *, gate=False):
    arguments = list(sys.argv[1:] if argv is None else argv)
    # Import only at dispatch: the trusted bridge imports shared definitions
    # from this module. Existing read-only reports retain their old behavior.
    from offline_cli import requested, main as offline_main, _command as offline_command
    if requested(arguments):
        return offline_main(arguments, gate=gate)
    parser = argparse.ArgumentParser(description=__doc__, epilog=None if gate else
        'Explicit offline routes: fix --profile closed-core-v1 --offline-exclusive --target main.typ; '
        'compile --profile closed-core-v1 --resource-profile embedded-ascii-v1 '
        '--offline-exclusive --offline-output-exclusive --target main.typ --output build/main.pdf; '
        'Paragraph fix/compile selects --profile captured-text-utils-paragraph-v1 with '
        'explicit canonical --package-path /absolute/packages; compile requires embedded-ascii-v1. '
        'journal-status; recover --offline-exclusive --target main.typ --journal-id ID; '
        'output-status; recover-output --offline-output-exclusive --output build/main.pdf --journal-id ID. '
        'Use COMMAND --help for the offline route options.')
    parser.add_argument('--version', action='version', version=VERSION)
    parser.add_argument('command', choices=['compile'] if gate else ['check', 'fix', 'compile', 'journal-status', 'recover'])
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--format', choices=['json', 'text'], default='text')
    parser.add_argument('--package-path', type=Path, help='Explicit Typst package directory for bounded read-only provenance')
    if not gate:
        parser.add_argument('--dry-run', action='store_true')
    if (gate or offline_command(arguments) == 'compile') and '--' in arguments:
        arguments = arguments[:arguments.index('--')]  # Never execute the remaining compiler argv.
    args = parser.parse_args(arguments)
    if not gate and args.dry_run and args.command != 'fix':
        parser.error('--dry-run requires fix')
    try:
        root = args.root.resolve(strict=True)
        require(root.is_dir(), 'Consumer root must be a directory')
        result = report(root, 'compile' if gate else ('dry-run' if args.dry_run else args.command), package_path=args.package_path)
    except (InputError, OSError, ValueError) as error:
        result = dict(schema=1, tool_version=VERSION, status=2, phase='input',
                      compiler_invoked=False, compiler_status=None, error=str(error))
    if args.format == 'json':
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(f"typst-lint: blocked (exit {result['status']}); compiler not invoked")
        if 'error' in result:
            print(result['error'])
        else:
            coverage = result['coverage']
            print(f"{len(result['inputs'])} selected inputs; {len(coverage['implemented'])}/{len(coverage['enabled'])} enabled rule backends evaluated on supported inputs")
            for blocker in result['blockers']:
                print(blocker)
    return result['status']


if __name__ == '__main__':
    from offline_compile_cli import executable_main
    executable_main()
