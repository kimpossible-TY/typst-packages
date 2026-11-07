"""Partial syntax-backed lint analysis; no active writer or compiler dispatcher."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tomllib

VERSION = '0.2.0-parser'
CATALOG = Path(__file__).with_name('rules') / 'catalog.json'


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


def load_rules(path=CATALOG):
    raw = path.read_bytes()
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
    """Validate static membership data, never fetch URLs or check freshness."""
    raw = path.read_bytes()
    data = json.loads(raw)
    require(isinstance(data, dict) and type(data.get('schema')) is int and data['schema'] == 1
            and isinstance(data.get('projects'), dict), 'Invalid reference catalog schema')
    for alias, project in data['projects'].items():
        require(bool(alias.strip()) and isinstance(project, dict), 'Invalid catalog project')
        require(isinstance(project.get('references'), dict), f'Missing references: {alias}')
        strings(project.get('ambiguous_labels', []), 'ambiguous_labels', nonempty=False)
        for label, ref in project['references'].items():
            require(bool(label.strip()) and isinstance(ref, dict), f'Invalid reference: {alias}/{label}')
            require(type(ref.get('page')) is int and ref['page'] > 0, f'Invalid reference page: {label}')
            require(isinstance(ref.get('text'), str) and bool(ref['text'].strip()), f'Invalid reference text: {label}')
            for axis in ('x', 'y'):
                if axis in ref:
                    try:
                        valid = type(ref[axis]) in (int, float) and math.isfinite(ref[axis])
                    except OverflowError:
                        valid = False
                    require(valid, f'Invalid coordinate: {label}')
    return digest(raw)


def collect(root, config):
    selected = []
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
            raw = checked_path(root, rel).read_bytes()
            raw.decode('utf-8')
            selected.append({'path': rel, 'sha256': digest(raw), 'bytes': len(raw)})
    return sorted(selected, key=lambda f: f['path'])


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


def report(root, mode):
    catalog, rule_hash = load_rules()
    config = read_config(root, catalog['rules'])
    enabled = [r for r in catalog['rules'] if r['id'].split('.')[0] in catalog['packs'][config.pack]]
    inputs = collect(root, config)
    entry_hash = None
    if config.entrypoint:
        require(config.entrypoint.endswith('.typ'), 'Entrypoint must be a Typst file')
        raw = checked_path(root, config.entrypoint).read_bytes()
        raw.decode('utf-8')
        entry_hash = digest(raw)
    reference_hash = read_reference_catalog(checked_path(root, config.catalog)) if config.catalog else None
    # Imported here to keep pure configuration/edit primitives independent of the adapter.
    from syntax_client import AdapterError, parse
    from script_rule import RULE_ID, analyze
    active_findings, proposals, file_coverage, parse_failures = [], [], [], []
    suppressed_count = 0
    evaluated = []
    for item in inputs:
        name = item['path']
        if not name.endswith('.typ'):
            file_coverage.append(dict(path=name, rule_id=RULE_ID, status='unsupported',
                                      reasons=['Markdown extraction is not implemented']))
            continue
        try:
            raw = checked_path(root, name).read_bytes()
            require(digest(raw) == item['sha256'], f'Input changed during analysis: {name}')
            result = analyze(parse(raw), name)
        except (AdapterError, OSError, InputError) as error:
            file_coverage.append(dict(path=name, rule_id=RULE_ID, status='unsupported', reasons=[str(error)]))
            continue
        active, hidden = diagnostics(result.diagnostics, config, catalog['rules'])
        active_findings.extend(active)
        suppressed_count += len(hidden)
        if result.parse_errors:
            parse_failures.append(dict(path=name, spans=[dict(start=a, end=b) for a, b in result.parse_errors]))
        if result.skips:
            file_coverage.append(dict(path=name, rule_id=RULE_ID, status='partial', reasons=list(result.skips)))
        else:
            evaluated.append(dict(path=name, rule_id=RULE_ID))
            file_coverage.append(dict(path=name, rule_id=RULE_ID, status='evaluated', reasons=[]))
        # Config ignores cover the entire file/rule, never individual edit spans.
        if active and not hidden:
            proposals.extend(dict(path=name, start=e.start, end=e.end,
                                  replacement=e.replacement.decode('utf-8'), rule_id=RULE_ID,
                                  baseline_sha256=item['sha256']) for e in result.edits)
    active_findings.sort(key=lambda d: (d.path, d.line, d.column, d.rule_id))
    script_complete = bool(inputs) and len(evaluated) == len(inputs)
    unsupported = [dict(rule_id=r['id'], reason=('See per-file script coverage' if r['id'] == RULE_ID
                                                else 'Binding/rule backend not implemented'))
                   for r in enabled if r['id'] != RULE_ID or not script_complete]
    return dict(schema=1, tool_version=VERSION, pack_version=catalog['version'], mode=mode,
                status=2, phase='analysis', compiler_invoked=False, compiler_status=None,
                rule_set=config.pack, inputs=inputs,
                hashes=dict(config=config.sha256, rules=rule_hash, entrypoint=entry_hash, reference_catalog=reference_hash),
                diagnostics=[asdict(d) for d in active_findings], suppressed_count=suppressed_count,
                applied_fixes=[], proposed_edits=proposals, proposal_status='partial-not-applicable-transaction',
                parse_failures=parse_failures, recovery=None,
                coverage=dict(complete=False, enabled=[r['id'] for r in enabled],
                              implemented=[RULE_ID] if evaluated else [], evaluated=evaluated,
                              files=file_coverage, unsupported=unsupported,
                              optional_skips=[] if config.catalog else [dict(check='catalog-membership', reason='No catalog configured')]),
                blockers=['Required rule coverage remains incomplete; Markdown, bindings and inline suppressions are unavailable',
                          'Protected source transactions and immutable compile snapshots are unavailable'],
                routes=dict(cli='unmanaged', ci='unmanaged', watch='unmanaged', editor='unmanaged'))


def main(argv=None, *, gate=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=VERSION)
    parser.add_argument('command', choices=['compile'] if gate else ['check', 'fix'])
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--format', choices=['json', 'text'], default='text')
    if not gate:
        parser.add_argument('--dry-run', action='store_true')
    arguments = list(sys.argv[1:] if argv is None else argv)
    if gate and '--' in arguments:
        arguments = arguments[:arguments.index('--')]  # Never execute the remaining compiler argv.
    args = parser.parse_args(arguments)
    if not gate and args.dry_run and args.command != 'fix':
        parser.error('--dry-run requires fix')
    try:
        root = args.root.resolve(strict=True)
        require(root.is_dir(), 'Consumer root must be a directory')
        result = report(root, 'compile' if gate else ('dry-run' if args.dry_run else args.command))
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
