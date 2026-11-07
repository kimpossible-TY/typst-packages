"""Internal transaction-owned relative scalar inputs; no apply/compile bridge.

Discovery proposes a finite evidence allowlist, never a baseline. Only the
existing ProtectedTransaction's retained bytes and identities may issue the live
receipt below. Neither report data nor a pure grammar proof grants authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import captured_relative_core as core
from protected_fix import _catalog, _config
from syntax_client import AdapterError, Tree, parse
from transactions import (DEFAULT_READ_LIMITS, EvidenceInput, GraphSnapshot,
                          OfflineExclusive, ProtectedTransaction, ReadLimits,
                          SourceEncodingError, TransactionError, _LEASE_SEAL, _directory,
                          _read)
from typst_lint import CATALOG, InputError, digest

_CONFIG = '.typst-lint.toml'
_SEAL = object()


class CapturedRelativeInputError(InputError):
    """An initially absent, invalidly encoded or unsupported project input."""


class CapturedRelativeCaptureError(TransactionError):
    """Unsafe reads or stale/foreign/altered transaction capture authority."""


def _require(condition, message):
    if not condition:
        raise CapturedRelativeCaptureError(message)


def _input(message, error=None):
    exception = CapturedRelativeInputError(message)
    if error is not None:
        raise exception from error
    raise exception


def _parse(source):
    try:
        return parse(source)
    except AdapterError as error:
        _input(f'Unsupported captured relative syntax: {error}', error)


def _language(operation):
    try:
        return operation()
    except core.CapturedRelativeSyntaxError as error:
        _input(f'Unsupported captured relative language: {error}', error)
    except core.CapturedRelativeAuthorityError as error:
        raise CapturedRelativeCaptureError(str(error)) from error


def _tree_signature(tree):
    # Retain primitives independently of caller-visible frozen dataclass objects.
    # object.__setattr__ can otherwise mutate a shared Tree/Node in place.
    try:
        return json.dumps((tree.mode, tuple((n.kind, n.start, n.end, n.children,
            n.errors, n.operands, n.display) for n in tree.nodes)),
            separators=(',', ':'), ensure_ascii=True).encode('ascii')
    except (AttributeError, TypeError, ValueError, UnicodeError) as error:
        raise CapturedRelativeCaptureError('Captured parser tree changed') from error


def _report_signature(report):
    try:
        _require(type(report) is CapturedRelativeInputReport
                 and type(report.source_profile) is str and len(report.source_profile) <= 128
                 and type(report.target) is str and len(report.target) <= core.MAX_PATH_BYTES
                 and type(report.sources) is tuple and len(report.sources) <= core.MAX_FILES
                 and type(report.edges) is tuple and len(report.edges) <= core.MAX_IMPORT_EDGES
                 and all(type(value) is str and len(value) == 64
                         for value in (report.config_sha256, report.catalog_sha256)),
                 'Captured report representation changed')
        for row in report.sources:
            _require(type(row) is tuple and len(row) == 3
                     and type(row[0]) is str and len(row[0]) <= core.MAX_PATH_BYTES
                     and type(row[1]) is str and len(row[1]) == 64
                     and type(row[2]) is int and 0 <= row[2] <= core.MAX_FILE_BYTES,
                     'Captured report source record changed')
        for row in report.edges:
            _require(type(row) is tuple and len(row) == 3
                     and all(type(value) is str and len(value) <= core.MAX_PATH_BYTES for value in row),
                     'Captured report edge record changed')
        return json.dumps((report.source_profile, report.target, report.sources,
            report.edges, report.config_sha256, report.catalog_sha256),
            separators=(',', ':'), ensure_ascii=True).encode('ascii')
    except (AttributeError, TypeError, ValueError, UnicodeError) as error:
        raise CapturedRelativeCaptureError('Captured report changed') from error


def _path_budget(paths):
    _require(len(paths) <= core.MAX_FILES, 'Relative source file budget exceeded')
    _require(sum(len(p.encode('utf-8')) for p in paths) <= core.MAX_TOTAL_PATH_BYTES,
             'Relative total path byte budget exceeded')
    directories = {''}
    for path in paths:
        parts = path.split('/')[:-1]
        directories.update('/'.join(parts[:i]) for i in range(1, len(parts) + 1))
    _require(len(directories) <= core.MAX_PROJECT_DIRECTORIES,
             'Relative project parent-directory budget exceeded')


def _initial_presence(root, logical):
    """Classify absence before a read; disappearance once reading is unsafe.

    Even this non-authoritative probe follows no symlink, including ancestors.
    It cannot grant a byte or identity baseline to the later transaction.
    """
    fd = None
    try:
        fd = _directory(root)
        for part in logical.split('/')[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=fd)
            os.close(fd)
            fd = next_fd
        os.stat(logical.split('/')[-1], dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError as error:
        _input(f'Missing initial relative source: {logical}', error)
    except OSError as error:
        raise CapturedRelativeCaptureError(f'Unsafe initial relative source: {logical}: {error}') from error
    finally:
        if fd is not None:
            os.close(fd)


@dataclass(frozen=True, slots=True)
class _Discovery:
    paths: tuple[str, ...]
    edges: tuple[tuple[str, str, str], ...]


def _discover(root, target, limits):
    """Read bounded candidates. All returned bytes/trees are deliberately dropped."""
    pending, seen, edges = [(target, 0)], set(), []
    source_bytes = nodes = 0
    while pending:
        path, depth = pending.pop()
        _require(depth <= core.MAX_IMPORT_DEPTH, 'Relative import depth budget exceeded')
        if path in seen:
            continue
        seen.add(path)
        _path_budget(seen)
        _initial_presence(root, path)
        try:
            value = _read(root / path, utf8=True, limits=limits)
        except SourceEncodingError as error:
            _input(f'Non-UTF-8 initial relative source: {path}', error)
        except (OSError, TransactionError) as error:
            raise CapturedRelativeCaptureError(f'Unsafe relative discovery read: {path}: {error}') from error
        source_bytes += len(value.data)
        _require(source_bytes <= core.MAX_SOURCE_BYTES, 'Relative aggregate source byte budget exceeded')
        tree = _parse(value.data)
        nodes += len(tree.nodes)
        _require(nodes <= core.MAX_TOTAL_NODES, 'Relative aggregate syntax node budget exceeded')
        requests = _language(lambda: core.discover_relative_requests(tree, path))
        for request, dependency in requests:
            edges.append((path, request, dependency))
            _require(len(edges) <= core.MAX_IMPORT_EDGES, 'Relative import edge budget exceeded')
            pending.append((dependency, depth + 1))
    return _Discovery(tuple(sorted(seen)), tuple(sorted(edges)))


@dataclass(frozen=True, slots=True)
class CapturedRelativeInputReport:
    """Reporting only. Copies/serialization confer no live-session authority."""
    source_profile: str
    target: str
    sources: tuple[tuple[str, str, int], ...]
    edges: tuple[tuple[str, str, str], ...]
    config_sha256: str
    catalog_sha256: str


@dataclass(frozen=True, slots=True, init=False, eq=False)
class CapturedRelativeInputs:
    """Opaque live receipt for a baseline, not permission to repair or compile."""
    root: Path
    target: str
    source_profile: str
    captures: Mapping[str, Tree] = field(repr=False)
    dependency_evidence: Mapping[str, str]
    proof: core.CapturedRelativeCoreProof
    report: CapturedRelativeInputReport
    _owner: object = field(repr=False)
    _seal: object = field(repr=False)

    def __init__(self, *args, **kwargs):
        raise TypeError('CapturedRelativeInputs is issued only by an active capture session')


@dataclass(frozen=True, slots=True)
class _Retained:
    transaction: ProtectedTransaction
    graph: GraphSnapshot
    captures: Mapping[str, Tree]
    dependency_evidence: Mapping[str, str]
    proof: core.CapturedRelativeCoreProof
    report: CapturedRelativeInputReport
    tree_signatures: Mapping[str, bytes]
    report_signature: bytes
    receipt: CapturedRelativeInputs


class CapturedRelativeInputSession:
    """One writable root target, read-only modules, existing root lock/journals.

    This internal checkpoint only captures and certifies the baseline grammar.
    It does not run all core rules, issue a candidate, write sources or compile.
    ``lease`` must be the caller's genuine existing OfflineExclusive assertion.
    """
    def __init__(self, root: Path, target: str, *, journal_directory: Path,
                 lease, source_profile=core.SOURCE_PROFILE,
                 limits=DEFAULT_READ_LIMITS, create_journal_directory=False):
        # All argument/selection/storage checks precede discovery filesystem work.
        if type(source_profile) is not str or source_profile != core.SOURCE_PROFILE:
            _input('Unknown internal captured relative source profile')
        try:
            core.canonical_path(target)
        except core.CapturedRelativeAuthorityError as error:
            _input(f'Invalid captured relative target: {error}', error)
        if any(c in target for c in '*?[]{}'):
            _input('Captured relative target must be one literal .typ path')
        _require(type(limits) is ReadLimits, 'Explicit immutable ReadLimits required')
        limits.__post_init__()
        _require(all(getattr(limits, field) <= getattr(DEFAULT_READ_LIMITS, field)
                     for field in ReadLimits.__dataclass_fields__),
                 'Internal capture cannot expand default transaction read limits')
        _require(type(lease) is OfflineExclusive and getattr(lease, '_seal', None) is _LEASE_SEAL,
                 'Explicit caller offline-exclusive lease required')
        self._target, self._profile = target, source_profile
        self._owner = object()
        self._active = self._used = False
        self._retained = None
        self._limits = limits
        self._create_store = create_journal_directory
        # Constructor is pure: this validates canonical roots, storage overlap,
        # target/evidence collision, booleans and immutable limits, without a lock.
        self._preflight = ProtectedTransaction(root, (target,),
            journal_directory=journal_directory, lease=lease, limits=limits,
            evidence=(EvidenceInput(Path(root) / _CONFIG, allow_missing=True, utf8=True),
                      EvidenceInput(CATALOG, utf8=True)),
            create_journal_directory=create_journal_directory, retain_ancestry=True)
        self._root, self._store = self._preflight.root, self._preflight.store
        self._lease = lease
        self._config_path = str(self._root / _CONFIG)
        self._catalog_path = str(Path(CATALOG).absolute())
        self._tx = None

    def __enter__(self):
        _require(not self._active and not self._used, 'Capture sessions cannot be reused')
        self._used = True
        discovery = _discover(self._root, self._target, self._limits)
        dependencies = MappingProxyType({p: str(self._root / p)
            for p in discovery.paths if p != self._target})
        evidence = (EvidenceInput(self._root / _CONFIG, allow_missing=True, utf8=True),
                    EvidenceInput(Path(self._catalog_path), utf8=True),
                    *(EvidenceInput(Path(p), utf8=True) for p in dependencies.values()))
        tx = ProtectedTransaction(self._root, (self._target,),
            journal_directory=self._store, lease=self._lease, limits=self._limits,
            evidence=evidence, create_journal_directory=self._create_store, retain_ancestry=True)
        self._tx = tx
        try:
            tx.__enter__()
        except SourceEncodingError as error:
            _input(f'Non-UTF-8 authoritative relative capture: {error}', error)
        except OSError as error:
            raise CapturedRelativeCaptureError(f'Unsafe authoritative relative capture: {error}') from error
        try:
            graph = tx.snapshot
            expected = frozenset((self._config_path, self._catalog_path, *dependencies.values()))
            _require(type(graph) is GraphSnapshot and graph.root == self._root
                     and frozenset(graph.sources) == frozenset((self._target,))
                     and frozenset(graph.evidence) == expected,
                     'Unexpected or missing authoritative captured inputs')
            _config(graph.evidence[self._config_path], self._target)
            _, catalog_sha = _catalog(graph.evidence[self._catalog_path])
            self._check_aliases(tx, dependencies)
            sources = {self._target: graph.sources[self._target],
                       **{p: graph.evidence[e] for p, e in dependencies.items()}}
            _require(all(type(data) is bytes for data in sources.values()),
                     'Present immutable relative source bytes required')
            _require(all(len(data) <= core.MAX_FILE_BYTES for data in sources.values())
                     and sum(map(len, sources.values())) <= core.MAX_SOURCE_BYTES,
                     'Authoritative relative source byte budget exceeded')
            trees, total_nodes = {}, 0
            for path, data in sources.items():
                trees[path] = _parse(data)
                total_nodes += len(trees[path].nodes)
                _require(total_nodes <= core.MAX_TOTAL_NODES,
                         'Authoritative relative syntax node budget exceeded')
            captures = MappingProxyType(trees)
            proof = _language(lambda: core.certify_captured_relative_core(self._target, captures,
                                                        source_profile=self._profile))
            authoritative_edges = tuple(sorted((e.importer, e.request, e.target) for e in proof.edges))
            _require(frozenset(captures) == frozenset(discovery.paths)
                     and authoritative_edges == discovery.edges,
                     'Relative closure or edges changed between discovery and capture')
            core.verify_captured_relative_core(proof, target_path=self._target,
                                              captures=captures, source_profile=self._profile)
            report = CapturedRelativeInputReport(self._profile, self._target,
                tuple((p, digest(captures[p].source), len(captures[p].source)) for p in sorted(captures)),
                tuple(sorted(authoritative_edges)), digest(graph.evidence[self._config_path]), catalog_sha)
            receipt = object.__new__(CapturedRelativeInputs)
            for name, value in dict(root=self._root, target=self._target, source_profile=self._profile,
                    captures=captures, dependency_evidence=dependencies, proof=proof,
                    report=report, _owner=self._owner, _seal=_SEAL).items():
                object.__setattr__(receipt, name, value)
            self._retained = _Retained(tx, graph, captures, dependencies, proof, report,
                MappingProxyType({p: _tree_signature(t) for p, t in captures.items()}),
                _report_signature(report), receipt)
            self._active = True
            self.assert_current(receipt)
            return self
        except BaseException:
            self._active = False
            tx.__exit__(None, None, None)
            raise

    @staticmethod
    def _check_aliases(tx, dependencies):
        fingerprints = tx.captured_fingerprints
        expected_sources = frozenset(tx.targets)
        expected_evidence = frozenset(str(e.path) for e in tx.evidence_inputs)
        _require(frozenset(fingerprints.sources) == expected_sources
                 and frozenset(fingerprints.evidence) == expected_evidence,
                 'Missing or unexpected retained capture identities')
        identities = set()
        for label, fingerprint in ((*fingerprints.sources.items(), *fingerprints.evidence.items())):
            if fingerprint is None:
                continue
            _require(fingerprint.mode <= 0o777, f'Unsupported special file permissions: {label}')
            identity = fingerprint.device, fingerprint.inode
            _require(identity not in identities, f'Captured file alias refused: {label}')
            identities.add(identity)
        directory_names = {}
        for path, identity in fingerprints.directories.items():
            if Path(path) == tx.root or tx.root in Path(path).parents:
                _require(identity not in directory_names or directory_names[identity] == path,
                         f'Captured project directory alias refused: {path}')
                directory_names[identity] = path
        _require(not set(dependencies.values()) & {str(tx.root / p) for p in tx.targets},
                 'Dependency/source evidence overlap')

    @property
    def capture(self):
        _require(self._retained is not None, 'Active captured baseline required')
        self.assert_current(self._retained.receipt)
        return self._retained.receipt

    def assert_current(self, capture):
        retained = self._retained
        _require(self._active and retained is not None and self._tx is retained.transaction
                 and type(capture) is CapturedRelativeInputs and capture is retained.receipt
                 and getattr(capture, '_owner', None) is self._owner
                 and getattr(capture, '_seal', None) is _SEAL,
                 'Only this active session\'s original capture receipt is authoritative')
        _require(all(hasattr(capture, name) for name in ('root', 'target', 'source_profile',
                 'captures', 'dependency_evidence', 'proof', 'report')),
                 'Captured relative receipt fields changed')
        # The report is shared with retained state, so validate its required
        # fields before any direct shared-report access below.
        _require(_report_signature(capture.report) == retained.report_signature,
                 'Captured relative report changed')
        _require(capture.root == self._root == retained.graph.root
                 and capture.target == self._target == retained.report.target
                 and capture.source_profile == self._profile == retained.report.source_profile
                 and retained.transaction.targets == (self._target,)
                 and capture.captures is retained.captures
                 and capture.dependency_evidence is retained.dependency_evidence
                 and capture.proof is retained.proof and capture.report is retained.report,
                 'Captured relative receipt changed')
        _require(frozenset(capture.captures) == frozenset(retained.tree_signatures),
                 'Captured parser tree membership changed')
        for path, tree in capture.captures.items():
            source = (retained.graph.sources[path] if path == self._target
                      else retained.graph.evidence[retained.dependency_evidence[path]])
            _require(type(tree) is Tree and type(getattr(tree, 'source', None)) is bytes
                     and tree.source is source,
                     f'Captured parser tree differs from retained transaction bytes: {path}')
        try:
            core.verify_captured_relative_core(capture.proof, target_path=self._target,
                captures=capture.captures, source_profile=self._profile)
        except core.CapturedRelativeCoreError as error:
            raise CapturedRelativeCaptureError(f'Captured relative proof changed: {error}') from error
        # Pure verification checks node/link/operand bounds before serialization.
        for path, tree in capture.captures.items():
            _require(_tree_signature(tree) == retained.tree_signatures[path],
                     f'Captured parser tree changed: {path}')
        # Exact retained transaction records, never rediscovery/fresh byte authority.
        retained.transaction._revalidate()

    def __exit__(self, exc_type, exc, tb):
        self._active = False
        if self._tx is not None:
            return self._tx.__exit__(exc_type, exc, tb)
