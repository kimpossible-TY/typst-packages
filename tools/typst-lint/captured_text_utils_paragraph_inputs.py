"""Internal transaction-owned text-utils paragraph inputs, without write authority.

Only this session's one ProtectedTransaction supplies authoritative bytes and
identities. The fixed package leaf is semantic evidence, never a selected PDE
source. Capture does not evaluate helper calls, repair, compile or load packages.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import tomllib
from types import MappingProxyType
from typing import Mapping
import weakref

from captured_text_utils_paragraph_core import MAX_CONFIG_BYTES, ParagraphConfig, validate_config
from facade_provenance import InitializerCertificate, certify_initializer
from protected_fix import _catalog, _matches_authority, _retain_authority
from static_resolver import PackageSource, TEXT_UTILS, TEXT_UTILS_SOURCE_SHA256
from syntax_client import AdapterError, Tree, parse
from transactions import (DEFAULT_READ_LIMITS, EvidenceInput, GraphSnapshot,
                          OfflineExclusive, ProtectedTransaction, ReadLimits,
                          SourceEncodingError, TransactionError, _LEASE_SEAL,
                          _absolute, _directory)
from typst_lint import CATALOG, InputError, digest

SOURCE_PROFILE = 'captured-text-utils-paragraph-v1'
MAX_SOURCE_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_PACKAGE_BYTES = 1024 * 1024
MAX_PATH_BYTES = 512
MANIFEST_SHA256 = 'e1d27052412a39d4f839dede28406159c1c9a5394592a73c2ed5666dcbc60df2'
MANIFEST_SIZE = 173
PACKAGE_SIZE = 5583
_CONFIG = '.typst-lint.toml'
_PACKAGE_LEAF = Path('local/text-utils/0.1.2')
_SEAL = object()


class CapturedTextUtilsParagraphInputError(InputError):
    """Initially absent, malformed or unsupported input to this internal slice."""


class CapturedTextUtilsParagraphCaptureError(TransactionError):
    """Unsafe reads or changed, foreign, expired or altered capture authority."""


def _require(condition, message):
    if not condition:
        raise CapturedTextUtilsParagraphCaptureError(message)


def _input(message, error=None):
    exception = CapturedTextUtilsParagraphInputError(message)
    if error is not None:
        raise exception from error
    raise exception


def _literal_target(target):
    if (type(target) is not str or not target or len(target) > MAX_PATH_BYTES
            or target.startswith(('/', '@')) or '\\' in target
            or any(part in ('', '.', '..') for part in target.split('/'))
            or not target.endswith('.typ') or any(c in target for c in '*?[]{}')
            or any(ord(c) < 32 or ord(c) == 127 for c in target)):
        _input('One literal canonical root-relative .typ target is required')
    try:
        if len(target.encode('utf-8')) > MAX_PATH_BYTES:
            _input('Paragraph target path byte budget exceeded')
    except UnicodeError as error:
        _input('Paragraph target path must be UTF-8', error)
    return target


def _manifest(raw):
    _require(type(raw) is bytes and len(raw) <= MAX_MANIFEST_BYTES,
             'Paragraph manifest accepted-size budget exceeded')
    try:
        data = tomllib.loads(raw.decode('utf-8'))
    except (UnicodeError, tomllib.TOMLDecodeError) as error:
        _input(f'Invalid retained text-utils manifest: {error}', error)
    package = data.get('package')
    # Independently check metadata, even though the exact reviewed bytes are
    # also pinned. Never derive another pathname from untrusted manifest text.
    if (type(package) is not dict
            or any(type(package.get(name)) is not str for name in ('name', 'version', 'entrypoint'))
            or (package['name'], package['version'], package['entrypoint'])
            != ('text-utils', '0.1.2', 'lib.typ')
            or package.get('namespace', 'local') != 'local'):
        _input('Retained text-utils manifest metadata is unsupported')
    if len(raw) != MANIFEST_SIZE or digest(raw) != MANIFEST_SHA256:
        _input('The exact reviewed text-utils 0.1.2 manifest is required')


def _parse(raw):
    try:
        tree = parse(raw)
    except AdapterError as error:
        _input(f'Unsupported initial paragraph syntax: {error}', error)
    if any(node.errors for node in tree.nodes):
        _input('Malformed initial paragraph syntax')
    return tree


def _initial_presence(path):
    """Absence classification only; this no-follow probe grants no baseline."""
    fd = None
    try:
        fd = _directory(path.parent)
        os.stat(path.name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError as error:
        _input(f'Missing initial paragraph input: {path}', error)
    except OSError as error:
        raise CapturedTextUtilsParagraphCaptureError(f'Unsafe initial paragraph input: {path}: {error}') from error
    finally:
        if fd is not None:
            os.close(fd)


def _tree_signature(tree):
    return json.dumps((tree.mode, tuple((node.kind, node.start, node.end,
        node.children, node.errors, node.operands, node.display) for node in tree.nodes)),
        separators=(',', ':'), ensure_ascii=True).encode('ascii')


@dataclass(frozen=True, slots=True)
class CapturedTextUtilsParagraphInputReport:
    """Bounded reporting data. Copies and serialization grant no authority."""
    source_profile: str
    target: str
    source_sha256: str
    source_size: int
    config_sha256: str
    catalog_sha256: str
    package_files: tuple[tuple[str, str, int], ...]
    package_role: str = 'read-only-semantic-evidence'


@dataclass(frozen=True, slots=True, init=False, eq=False)
class CapturedTextUtilsParagraphInputs:
    """Opaque original-baseline receipt, not a grammar, repair or compile token."""
    root: Path
    target: str
    source_profile: str
    tree: Tree = field(repr=False)
    package_tree: Tree = field(repr=False)
    config: ParagraphConfig
    manifest: bytes = field(repr=False)
    package: PackageSource = field(repr=False)
    initializer: InitializerCertificate
    catalog: tuple[Mapping, ...] = field(repr=False)
    dependency_evidence: Mapping[str, str]
    report: CapturedTextUtilsParagraphInputReport
    _owner: object = field(repr=False)
    _seal: object = field(repr=False)

    def __init__(self, *args, **kwargs):
        raise TypeError('Paragraph input receipts are issued only by an active capture session')


@dataclass(frozen=True, slots=True)
class _Retained:
    transaction: ProtectedTransaction
    graph: GraphSnapshot
    tree: Tree
    nodes: tuple
    package_tree: Tree
    package_nodes: tuple
    config: ParagraphConfig
    manifest: bytes
    package: PackageSource
    initializer: InitializerCertificate
    catalog: tuple
    dependencies: Mapping[str, str]
    report: CapturedTextUtilsParagraphInputReport
    tree_signature: bytes
    package_tree_signature: bytes
    receipt: CapturedTextUtilsParagraphInputs


class CapturedTextUtilsParagraphInputSession:
    """Own one transaction, one root target and exactly four fixed evidence files.

    There is no discovery, package loader, ambient search or transaction injection.
    The caller supplies its genuine offline-exclusive assertion. Package helper
    call semantics and root grammar are separate obligations of the repair bridge.
    """
    def __init__(self, root: Path, target: str, *, package_path: Path,
                 journal_directory: Path, lease, source_profile=SOURCE_PROFILE,
                 limits=DEFAULT_READ_LIMITS, create_journal_directory=False, fault=None):
        _require(type(self) is CapturedTextUtilsParagraphInputSession,
                 'Only the original concrete paragraph capture session may own a transaction')
        self._issuer = weakref.ref(self)
        if type(source_profile) is not str or source_profile != SOURCE_PROFILE:
            _input('Unknown internal paragraph source profile')
        self._target = _literal_target(target)
        _require(type(limits) is ReadLimits, 'Explicit immutable ReadLimits required')
        limits.__post_init__()
        _require(all(getattr(limits, name) <= getattr(DEFAULT_READ_LIMITS, name)
                     for name in ReadLimits.__dataclass_fields__),
                 'Paragraph capture cannot expand default transaction read limits')
        _require(type(lease) is OfflineExclusive and getattr(lease, '_seal', None) is _LEASE_SEAL,
                 'Explicit caller offline-exclusive lease required')
        package_root = _absolute(package_path)
        self._profile = source_profile
        self._owner = object()
        self._active = self._used = False
        self._retained = self._binding_authority = None
        # Pure constructor checks canonical paths, exact selection, storage
        # overlap, booleans and limits before any presence probe or lock.
        evidence = (EvidenceInput(_absolute(root) / _CONFIG, utf8=True),
                    EvidenceInput(_absolute(CATALOG), utf8=True),
                    EvidenceInput(package_root / _PACKAGE_LEAF / 'typst.toml', utf8=True),
                    EvidenceInput(package_root / _PACKAGE_LEAF / 'lib.typ', utf8=True))
        self._tx = ProtectedTransaction(root, (self._target,),
            journal_directory=journal_directory, lease=lease, evidence=evidence,
            limits=limits, create_journal_directory=create_journal_directory,
            retain_ancestry=True, fault=fault)
        self._transaction_issuer = weakref.ref(self._tx)
        self._root = self._tx.root
        self._dependencies = MappingProxyType(dict(zip(
            ('config', 'catalog', 'manifest', 'package'), (str(item.path) for item in evidence))))
        self._expected_evidence = frozenset(self._dependencies.values())
        self._selection_authority = _retain_authority(self._selection())

    def _assert_owner(self):
        issuer = getattr(self, '_issuer', None)
        transaction_issuer = getattr(self, '_transaction_issuer', None)
        transaction = getattr(self, '_tx', None)
        _require(type(self) is CapturedTextUtilsParagraphInputSession
                 and type(issuer) is weakref.ReferenceType and issuer() is self
                 and type(transaction) is ProtectedTransaction
                 and type(transaction_issuer) is weakref.ReferenceType
                 and transaction_issuer() is transaction,
                 'Only the original paragraph capture session may own this transaction')

    def _selection(self):
        try:
            tx = self._tx
            return (self._root, self._target, self._profile, self._expected_evidence,
                    tx.root, tx.store, tx.targets, tx.evidence_inputs, tx.limits, tx.lease,
                    tx._retain_ancestry, tx.create_journal_directory, tx.recovery_only,
                    tx.require_existing_namespace, self._dependencies)
        except (AttributeError, TypeError, ValueError) as error:
            raise CapturedTextUtilsParagraphCaptureError('Paragraph input selection changed') from error

    def __enter__(self):
        self._assert_owner()
        _require(not self._active and not self._used, 'Paragraph capture sessions cannot be reused')
        self._used = True
        _require(_matches_authority(self._selection(), self._selection_authority),
                 'Paragraph capture selection changed before entry')
        for path in (self._root / self._target, *(item.path for item in self._tx.evidence_inputs)):
            _initial_presence(path)
        try:
            self._tx.__enter__()
        except SourceEncodingError as error:
            _input(f'Non-UTF-8 initial paragraph capture: {error}', error)
        except OSError as error:
            raise CapturedTextUtilsParagraphCaptureError(f'Unsafe paragraph capture: {error}') from error
        try:
            graph = self._tx.snapshot
            _require(type(graph) is GraphSnapshot and graph.root == self._root
                     and frozenset(graph.sources) == frozenset((self._target,))
                     and frozenset(graph.evidence) == self._expected_evidence,
                     'Unexpected or missing transaction-retained paragraph inputs')
            _require(all(type(raw) is bytes for raw in (*graph.sources.values(), *graph.evidence.values())),
                     'All five paragraph capture files must be present immutable bytes')
            self._check_aliases()
            # Pure grammar owns this strict parser; it performs no filesystem reads.
            _require(len(graph.evidence[self._dependencies['config']]) <= MAX_CONFIG_BYTES,
                     'Paragraph configuration accepted-size budget exceeded')
            try:
                config = validate_config(graph.evidence[self._dependencies['config']], self._target)
            except InputError as error:
                _input(f'Unsupported initial paragraph configuration: {error}', error)
            catalog, catalog_sha = _catalog(graph.evidence[self._dependencies['catalog']])
            manifest = graph.evidence[self._dependencies['manifest']]
            _manifest(manifest)
            raw = graph.evidence[self._dependencies['package']]
            _require(len(raw) <= MAX_PACKAGE_BYTES, 'Paragraph package accepted-size budget exceeded')
            if len(raw) != PACKAGE_SIZE or digest(raw) != TEXT_UTILS_SOURCE_SHA256:
                _input('The exact reviewed text-utils 0.1.2 entrypoint is required')
            package = PackageSource(TEXT_UTILS, self._dependencies['package'], raw)
            initializer = certify_initializer(package)
            if not initializer.accepted:
                _input(f'Retained text-utils initializer is unsupported: {initializer.reason}')
            source = graph.sources[self._target]
            _require(len(source) <= MAX_SOURCE_BYTES, 'Paragraph source accepted-size budget exceeded')
            tree = _parse(source)
            package_tree = _parse(raw)
            report = CapturedTextUtilsParagraphInputReport(self._profile, self._target,
                digest(source), len(source), digest(graph.evidence[self._dependencies['config']]), catalog_sha,
                ((self._dependencies['manifest'], digest(manifest), len(manifest)),
                 (package.source_identity, digest(raw), len(raw))))
            receipt = object.__new__(CapturedTextUtilsParagraphInputs)
            for name, value in dict(root=self._root, target=self._target, source_profile=self._profile,
                    tree=tree, package_tree=package_tree, config=config, manifest=manifest, package=package,
                    initializer=initializer, catalog=catalog, dependency_evidence=self._dependencies,
                    report=report, _owner=self._owner, _seal=_SEAL).items():
                object.__setattr__(receipt, name, value)
            self._retained = _Retained(self._tx, graph, tree, tree.nodes, package_tree,
                package_tree.nodes, config, manifest, package, initializer, catalog,
                self._dependencies, report, _tree_signature(tree), _tree_signature(package_tree), receipt)
            self._binding_authority = _retain_authority((receipt, graph, self._tx.targets,
                                                        self._tx.evidence_inputs, self._tx.limits, self._selection()))
            self._active = True
            self.assert_current(receipt)
            return self
        except BaseException:
            self._active = False
            self._binding_authority = None
            self._tx.__exit__(None, None, None)
            raise

    def _check_aliases(self):
        tx = self._tx
        fingerprints = tx.captured_fingerprints
        _require(frozenset(fingerprints.sources) == frozenset((self._target,))
                 and frozenset(fingerprints.evidence) == self._expected_evidence,
                 'Missing or unexpected transaction-retained paragraph identities')
        identities = set()
        for label, fingerprint in (*fingerprints.sources.items(), *fingerprints.evidence.items()):
            _require(fingerprint is not None and fingerprint.mode <= 0o777,
                     f'Present regular paragraph input with ordinary permissions required: {label}')
            identity = fingerprint.device, fingerprint.inode
            _require(identity not in identities, f'Paragraph input file alias refused: {label}')
            identities.add(identity)
        directories = {}
        for path, identity in fingerprints.directories.items():
            _require(identity not in directories or directories[identity] == path,
                     f'Paragraph input directory alias refused: {path}')
            _require(identity not in (tx._store_identity, tx._namespace_identity),
                     f'Paragraph input aliases transaction storage: {path}')
            directories[identity] = path

    @property
    def capture(self):
        self._assert_owner()
        _require(self._retained is not None, 'An active paragraph capture is required')
        self.assert_current(self._retained.receipt)
        return self._retained.receipt

    def _assert_binding(self, capture):
        """Bounded historical binding only; never refresh a filesystem baseline."""
        self._assert_owner()
        retained = self._retained
        _require(self._active and retained is not None and self._tx is retained.transaction
                 and type(capture) is CapturedTextUtilsParagraphInputs and capture is retained.receipt
                 and getattr(capture, '_owner', None) is self._owner
                 and getattr(capture, '_seal', None) is _SEAL,
                 'Only the active session\'s original paragraph capture is authoritative')
        # Compare independent original facts before traversing shared objects.
        # This bounded expected-shape walk refuses reflective growth/type changes.
        _require(self._binding_authority is not None and _matches_authority(
            (capture, retained.graph, retained.transaction.targets,
             retained.transaction.evidence_inputs, retained.transaction.limits, self._selection()), self._binding_authority),
            'Retained paragraph input binding changed')
        _require(capture.root is self._root and capture.root is retained.graph.root
                 and capture.target == self._target and capture.source_profile == self._profile
                 and retained.transaction.targets == (self._target,)
                 and capture.tree is retained.tree and capture.tree.nodes is retained.nodes
                 and capture.package_tree is retained.package_tree
                 and capture.package_tree.nodes is retained.package_nodes
                 and capture.config is retained.config and capture.manifest is retained.manifest
                 and capture.package is retained.package and capture.initializer is retained.initializer
                 and capture.catalog is retained.catalog and capture.report is retained.report
                 and capture.dependency_evidence is retained.dependencies is self._dependencies,
                 'Original paragraph input objects changed')
        _require(capture.tree.source is retained.graph.sources[self._target]
                 and capture.manifest is retained.graph.evidence[retained.dependencies['manifest']]
                 and capture.package.source is retained.graph.evidence[retained.dependencies['package']]
                 and capture.package.source_identity == retained.dependencies['package']
                 and capture.package_tree.source is capture.package.source
                 and _tree_signature(capture.package_tree) == retained.package_tree_signature
                 and _tree_signature(capture.tree) == retained.tree_signature,
                 'Paragraph parser/package facts differ from retained transaction bytes')

    def assert_current(self, capture):
        """Always revalidate the original baseline, even after private bridge apply."""
        self._assert_binding(capture)
        self._retained.transaction._revalidate()

    def _borrow_transaction(self, capture):
        """Privately borrow this exact locked transaction without ownership transfer."""
        self.assert_current(capture)
        return self._retained.transaction

    def __exit__(self, exc_type, exc, tb):
        self._assert_owner()
        self._active = False
        self._binding_authority = None
        return self._tx.__exit__(exc_type, exc, tb)
