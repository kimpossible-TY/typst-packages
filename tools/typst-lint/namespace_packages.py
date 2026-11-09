"""Conditional namespace evidence from six explicitly supplied package roots.

These complete name sets describe the pinned root modules *if they load
successfully*. They do not certify initializer purity, dependencies, runtime
package selection, helper calls, or rendering. Nothing here grants edit or
compiler authority or extends static_resolver's reviewed bindings. No evaluator,
network, ambient package cache, directory discovery or transitive read is used.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path

from package_loader import (MAX_MANIFEST_BYTES, MAX_SOURCE_BYTES, PackageEvidence,
                            PackageInputError, PackageUnavailable, _Snapshot,
                            _absolute_root, _manifest)

MAX_NAMESPACE_PACKAGES = 6
MAX_NAMESPACE_FILES = 12
MAX_NAMESPACE_BYTES = 8 * 1024 * 1024
MAX_ROOT_BYTES = 4096
MAX_ROOT_COMPONENTS = 128
TEXT_UTILS = '@local/text-utils:0.1.2'
MANNOT = '@preview/mannot:0.4.0'
CETZ = '@preview/cetz:0.4.2'


@dataclass(frozen=True)
class _Contract:
    namespace: str
    name: str
    version: str
    entrypoint: str
    manifest_sha256: str
    source_sha256: str
    exports: tuple[str, ...]
    dependencies: tuple[str, ...] = ()

    @property
    def import_path(self):
        return f'@{self.namespace}/{self.name}:{self.version}'


# Root AST review: retain root let names, explicit imported names and module
# aliases. A wildcard imports the complete pinned dependency set. Root output
# and function bodies do not create exports. Underscore names remain public;
# repeated names denote the last binding. Native Typst 0.15.1 dictionary(module)
# probes independently agreed with these sets (including all 19 CeTZ names).
_TEXT_EXPORTS = (
    'apply-paragraph-tabs', 'capitalize-title', 'highlighted',
    'is-numeric-title', 'paragraph-tab', 'paragraph_tab',
)
_BLOCK_EXPORTS = (
    'apply-math-block-reset', 'callout', 'capitalize-title', 'dark-theme',
    'definition', 'dots_space', 'emphasis', 'flowbox',
    'heading-numbering-style', 'is-numeric-title', 'lemma', 'light-theme',
    'math-block-kinds', 'math-block-title', 'math_font', 'note',
    'numbered-math-block', 'project-ref', 'proof', 'proposition', 'qed',
    'reset-math-block-counters', 'scoped-figure-numbering', 'scoped-numbering',
    'theme-from-text-fill', 'theorem', 'title_size',
)
_MANNOT_EXPORTS = (
    'annot', 'annot-cetz', 'core-annot', 'core-mark', 'mark', 'markhl',
    'markrect', 'markub', 'markul', 'markuw', 'tiptoe',
)
_CETZ_EXPORTS = (
    'angle', 'canvas', 'coordinate', 'decorations', 'draw', 'drawable',
    'intersection', 'mark', 'mark-shapes', 'matrix', 'palette', 'path-util',
    'process', 'sorting', 'styles', 'tree', 'util', 'vector', 'version',
)
_SCOPE_EXPORTS = tuple(sorted((*_MANNOT_EXPORTS,
    '_local-scope-annotation-counter', '_local-scope-counter', 'annot-cetz-local',
    'local-scope-annotations', 'local-tag-scope', 'mannot-scope')))
_DIAGRAM_EXPORTS = tuple(sorted((*_CETZ_EXPORTS,
    'description_box', 'legend_box', 'sample-curve', 'sample-function')))

_CONTRACTS = (
    _Contract('local', 'text-utils', '0.1.2', 'lib.typ',
        'e1d27052412a39d4f839dede28406159c1c9a5394592a73c2ed5666dcbc60df2',
        '7e151150dac47b01ebf7706457906133d74a52abee1b5c43ce824ff89279bd71', _TEXT_EXPORTS),
    _Contract('local', 'math-blocks', '0.2.0', 'lib.typ',
        'e5eeaa72f830e8893e5f7c4dd10129b9819f31a8f1b80b7fbe22551f1295c5c4',
        '542c7ce1ebfc6fbd51a7fefb2b55405a968a951b61d49170e49ef331948f36ff', _BLOCK_EXPORTS),
    _Contract('local', 'scoped-annotations', '0.3.0', 'lib.typ',
        '2deffaddbdcde7f320f9ff4ed8d16430b41ce0d933140ca7cfb8cdbb708f3dfd',
        'eb8e7ff7b31238c8fa4b996deb9502fec0ae4610240427a8b40873708e509203',
        _SCOPE_EXPORTS, (MANNOT,)),
    _Contract('local', 'cetz-helpers', '0.2.0', 'lib.typ',
        '7733132a6cb15391cbe64afc8f5f86281a44b1a87c9df98a7a72e3931ec4e402',
        '19c807971311c94a58413fbc5cac14ae2ea5065c78206d95ceda97890558e2c3',
        _DIAGRAM_EXPORTS, (CETZ,)),
    _Contract('preview', 'mannot', '0.4.0', 'src/lib.typ',
        'aef4310b36955dc98d8b03ec4abf17d45777d0a08b65d6e67214752e57647545',
        '288027cea059b327bf3253c44ae975339e7afc0f149375d9d87ca1c1fe739f57', _MANNOT_EXPORTS),
    _Contract('preview', 'cetz', '0.4.2', 'src/lib.typ',
        '2ccea70cc14ad8d79897179d3c8946758e610903e59f154acdb0f9b9ff81ac02',
        '53380cd9de16c1f845f325e9d8be8e0f03493c2073fba1c54d546e1906a8f8ed', _CETZ_EXPORTS),
)

# Separately reviewed namespace-only variant.  Its highlighted implementation
# differs from the helper contract above; matching this hash says only which
# names the root module exports if initialization succeeds.  It is intentionally
# not a member of _CONTRACTS and cannot create a NamespacePackage or origins.
_TEXT_COLLISION_CONTRACT = _Contract('local', 'text-utils', '0.1.2', 'lib.typ',
    'e1d27052412a39d4f839dede28406159c1c9a5394592a73c2ed5666dcbc60df2',
    '670599f5fca254fdb93487021099678f13ecb6357877e93ebdcf6cc3e17992ed', _TEXT_EXPORTS)

_COLLISION_UNAVAILABLE = {
    'missing-namespace-artifact': 'The explicit root lacks the reviewed text-utils manifest or entrypoint',
    'unreviewed-namespace-manifest': 'The text-utils manifest differs from the reviewed namespace-only variant',
    'unreviewed-namespace-source': 'The text-utils source differs from the reviewed namespace-only variant',
}


@dataclass(frozen=True)
class NamespacePackage:
    evidence: PackageEvidence
    manifest: bytes = field(repr=False)
    source: bytes = field(repr=False)
    exports: tuple[str, ...]
    # Only selected helper identities are tracked. All remaining values are
    # opaque, even when a familiar name occurs in a reviewed namespace.
    origins: tuple[tuple[str, str], ...] = ()

    @property
    def import_path(self):
        return self.evidence.import_path


@dataclass(frozen=True)
class NamespacePackageCapture:
    package_path: str
    packages: tuple[NamespacePackage, ...]
    unavailable: tuple[PackageUnavailable, ...]

    @property
    def evidence(self):
        return tuple(package.evidence for package in self.packages)


@dataclass(frozen=True)
class CollisionOnlyNamespace:
    """Names from one exact snapshot; no helper, marker, or edit authority.

    The retained bytes are private verification input, not report material.
    This deliberately is not a NamespacePackage and has no ``origins``.
    """
    evidence: PackageEvidence
    manifest: bytes = field(repr=False)
    source: bytes = field(repr=False)
    exports: tuple[str, ...]


@dataclass(frozen=True)
class TextUtilsCollisionCapture:
    package_path: str
    package: CollisionOnlyNamespace | None
    unavailable: PackageUnavailable | None


def _digest(source):
    return hashlib.sha256(source).hexdigest()


def _canonical_root(value):
    try:
        raw = os.fspath(value)
    except (TypeError, ValueError, OSError) as error:
        raise PackageInputError('Namespace package path must be a nonempty string path') from error
    if type(raw) is not str or not raw:
        raise PackageInputError('Namespace package path must be a nonempty string path')
    if len(raw) > MAX_ROOT_BYTES:
        raise PackageInputError('Namespace package root path limit exceeded')
    try:
        size = len(raw.encode('utf-8'))
    except UnicodeError as error:
        raise PackageInputError('Namespace package path must be UTF-8') from error
    if size > MAX_ROOT_BYTES:
        raise PackageInputError('Namespace package root path limit exceeded')
    root = _absolute_root(raw)
    if raw != str(root):
        raise PackageInputError('Namespace package path must be canonical and absolute')
    if len(root.parts) > MAX_ROOT_COMPONENTS:
        raise PackageInputError('Namespace package root path limit exceeded')
    return root


def _origins(contract):
    # Namespace variants sharing the same import path must not inherit helper
    # identity just because the package name matches.
    return (('highlighted', TEXT_UTILS),) if contract == _CONTRACTS[0] else ()


def _evidence(root, contract, manifest, source):
    directory = root / contract.namespace / contract.name / contract.version
    return PackageEvidence(
        contract.import_path, str(directory / 'typst.toml'), _digest(manifest),
        str(directory / contract.entrypoint), _digest(source), contract.namespace,
        contract.name, contract.version, contract.entrypoint)


def _metadata_matches(manifest, contract):
    package = _manifest(manifest, contract.import_path)
    return (package['name'] == contract.name and package['version'] == contract.version
            and package['entrypoint'] == contract.entrypoint
            and package.get('namespace', contract.namespace) == contract.namespace)


def _collision_unavailable(root, code):
    return TextUtilsCollisionCapture(str(root), None,
        PackageUnavailable(TEXT_UTILS, code, _COLLISION_UNAVAILABLE[code]))


def verify_text_utils_collision_capture(capture):
    """Recheck exact variant bytes and names without filesystem access.

    Successful evidence certifies only the reviewed namespace name set under
    successful initialization and exact runtime selection.  In particular it
    confers no identity or behavior proof for highlighted or paragraph markers.
    """
    try:
        if type(capture) is not TextUtilsCollisionCapture or type(capture.package_path) is not str:
            return False
        root = _canonical_root(capture.package_path)
        if capture.package is None:
            item = capture.unavailable
            return (type(item) is PackageUnavailable and item.import_path == TEXT_UTILS
                    and type(item.code) is str and item.code in _COLLISION_UNAVAILABLE
                    and item.reason == _COLLISION_UNAVAILABLE[item.code])
        package = capture.package
        contract = _TEXT_COLLISION_CONTRACT
        return (capture.unavailable is None and type(package) is CollisionOnlyNamespace
                and type(package.evidence) is PackageEvidence
                and type(package.manifest) is bytes and len(package.manifest) <= MAX_MANIFEST_BYTES
                and type(package.source) is bytes and len(package.source) <= MAX_SOURCE_BYTES
                and _digest(package.manifest) == contract.manifest_sha256
                and _digest(package.source) == contract.source_sha256
                and _metadata_matches(package.manifest, contract)
                and package.evidence == _evidence(root, contract, package.manifest, package.source)
                and type(package.exports) is tuple and package.exports == contract.exports)
    except (AttributeError, KeyError, TypeError, ValueError, OSError, RecursionError):
        return False


def text_utils_collision_from_snapshot(package_path, manifest, source):
    """Validate exact bytes already captured by a trusted filesystem reader.

    This pure constructor does not open ``package_path`` or establish that the
    caller actually read those bytes there.  Its path is a conditional claim;
    the caller owns all root/descriptor/race checks and runtime-selection facts.
    It can issue only the separate collision-only type, never helper identity.
    """
    root = _canonical_root(package_path)
    contract = _TEXT_COLLISION_CONTRACT
    if (type(manifest) is not bytes or len(manifest) > MAX_MANIFEST_BYTES
            or type(source) is not bytes or len(source) > MAX_SOURCE_BYTES
            or _digest(manifest) != contract.manifest_sha256
            or _digest(source) != contract.source_sha256):
        raise PackageInputError('Namespace-only snapshot does not match the reviewed manifest and source')
    if not _metadata_matches(manifest, contract):
        raise PackageInputError('Pinned namespace-only manifest has inconsistent metadata')
    package = CollisionOnlyNamespace(_evidence(root, contract, manifest, source),
                                     manifest, source, contract.exports)
    return TextUtilsCollisionCapture(str(root), package, None)


def load_text_utils_collision_only(package_path: str | os.PathLike[str]) -> TextUtilsCollisionCapture:
    """Capture one explicit namespace-only variant without helper capability.

    The package path is never inferred from a home directory or a cache.  Source
    bytes are retained privately to revalidate provenance and must not be put in
    reports.  Initial absence or nonmatching bytes yield typed unavailability;
    unsafe files and races fail the complete capture.
    """
    root = _canonical_root(package_path)
    contract = _TEXT_COLLISION_CONTRACT
    try:
        with _Snapshot(root) as snapshot:
            def exists(directory, name):
                try:
                    os.stat(name, dir_fd=directory.fd, follow_symlinks=False)
                    return True
                except FileNotFoundError:
                    return False

            def unavailable(code):
                snapshot.verify()
                return _collision_unavailable(root, code)

            directory = snapshot.directory_root
            for component in (contract.namespace, contract.name, contract.version):
                if not exists(directory, component):
                    return unavailable('missing-namespace-artifact')
                directory = snapshot.directory(directory, component)
            if not exists(directory, 'typst.toml'):
                return unavailable('missing-namespace-artifact')
            manifest = snapshot.read(directory, 'typst.toml', MAX_MANIFEST_BYTES)
            if _digest(manifest) != contract.manifest_sha256:
                return unavailable('unreviewed-namespace-manifest')
            if not _metadata_matches(manifest, contract):
                raise PackageInputError('Pinned namespace-only manifest has inconsistent metadata')
            if not exists(directory, contract.entrypoint):
                return unavailable('missing-namespace-artifact')
            source = snapshot.read(directory, contract.entrypoint, MAX_SOURCE_BYTES)
            if _digest(source) != contract.source_sha256:
                return unavailable('unreviewed-namespace-source')
            captured = text_utils_collision_from_snapshot(root, manifest, source)
            snapshot.verify()
            return captured
    except OSError as error:
        raise PackageInputError(f'Cannot safely read namespace-only package path {root}: {error}') from error


def verify_namespace_capture(capture):
    """Recheck supplied immutable bytes/facts without filesystem or evaluator IO.

    A valid capture is conditional static evidence, never a filesystem lease,
    signed attestation, proof of successful initialization or repair capability.
    No caller-supplied hash, export set, origin or artifact path is trusted.
    """
    try:
        if (type(capture) is not NamespacePackageCapture
                or type(capture.package_path) is not str
                or type(capture.packages) is not tuple
                or type(capture.unavailable) is not tuple
                or len(capture.packages) + len(capture.unavailable) != MAX_NAMESPACE_PACKAGES):
            return False
        root = _canonical_root(capture.package_path)
        contracts = {contract.import_path: contract for contract in _CONTRACTS}
        seen, available, total = set(), set(), 0
        for package in capture.packages:
            if type(package) is not NamespacePackage or type(package.evidence) is not PackageEvidence:
                return False
            evidence = package.evidence
            if (any(type(getattr(evidence, field)) is not str for field in (
                    'import_path', 'manifest_identity', 'manifest_sha256',
                    'source_identity', 'source_sha256', 'namespace', 'name',
                    'version', 'entrypoint')) or type(evidence.artifacts) is not tuple
                    or evidence.artifacts or type(package.exports) is not tuple
                    or any(type(name) is not str for name in package.exports)
                    or type(package.origins) is not tuple
                    or any(type(origin) is not tuple or len(origin) != 2
                           or any(type(value) is not str for value in origin)
                           for origin in package.origins)):
                return False
            contract = contracts.get(package.import_path)
            if (contract is None or package.import_path in seen
                    or type(package.manifest) is not bytes or type(package.source) is not bytes
                    or len(package.manifest) > MAX_MANIFEST_BYTES or len(package.source) > MAX_SOURCE_BYTES
                    or _digest(package.manifest) != contract.manifest_sha256
                    or _digest(package.source) != contract.source_sha256
                    or not _metadata_matches(package.manifest, contract)
                    or package.evidence != _evidence(root, contract, package.manifest, package.source)
                    or package.exports != contract.exports
                    or package.origins != _origins(contract)):
                return False
            total += len(package.manifest) + len(package.source)
            seen.add(package.import_path)
            available.add(package.import_path)
        for item in capture.unavailable:
            if (type(item) is not PackageUnavailable or type(item.import_path) is not str
                    or item.import_path not in contracts or item.import_path in seen
                    or type(item.code) is not str or not item.code
                    or type(item.reason) is not str or not item.reason):
                return False
            seen.add(item.import_path)
        return (total <= MAX_NAMESPACE_BYTES and seen == set(contracts)
                and all(set(contracts[path].dependencies) <= available for path in available))
    except (AttributeError, KeyError, TypeError, ValueError, OSError, RecursionError):
        return False


def load_namespace_packages(package_path: str | os.PathLike[str]) -> NamespacePackageCapture:
    """Capture at most six fixed manifests/entrypoints from an explicit root.

    Initial absence or unpinned bytes yield per-package unavailability. Unsafe
    files, invalid encoding and races fail the complete capture. Wildcard
    reexports additionally need the exact dependency root in this same capture;
    named-import dependencies need not be read to know their local bound names.
    All handles remain open through a final descriptor and content recheck.
    """
    root = _canonical_root(package_path)
    packages, unavailable = {}, {}
    total, files = 0, 0
    try:
        with _Snapshot(root) as snapshot:
            def exists(directory, name):
                try:
                    os.stat(name, dir_fd=directory.fd, follow_symlinks=False)
                    return True
                except FileNotFoundError:
                    return False

            def read(directory, name, limit):
                nonlocal total, files
                value = snapshot.read(directory, name, limit)
                files += 1
                total += len(value)
                if files > MAX_NAMESPACE_FILES or total > MAX_NAMESPACE_BYTES:
                    raise PackageInputError('Namespace package snapshot exceeds its fixed budget')
                return value

            def absent(contract):
                unavailable[contract.import_path] = PackageUnavailable(
                    contract.import_path, 'missing-namespace-artifact',
                    'The fixed reviewed manifest or entrypoint is absent from the explicit package path')

            for contract in _CONTRACTS:
                directory = snapshot.directory_root
                for component in (contract.namespace, contract.name, contract.version):
                    if not exists(directory, component):
                        absent(contract)
                        break
                    directory = snapshot.directory(directory, component)
                if contract.import_path in unavailable:
                    continue
                if not exists(directory, 'typst.toml'):
                    absent(contract)
                    continue
                manifest = read(directory, 'typst.toml', MAX_MANIFEST_BYTES)
                if _digest(manifest) != contract.manifest_sha256:
                    unavailable[contract.import_path] = PackageUnavailable(
                        contract.import_path, 'unreviewed-namespace-manifest',
                        'Manifest bytes differ from the fixed reviewed namespace artifact')
                    continue
                if not _metadata_matches(manifest, contract):
                    raise PackageInputError('Pinned namespace manifest has inconsistent metadata')
                parts = contract.entrypoint.split('/')
                for component in parts[:-1]:
                    if not exists(directory, component):
                        absent(contract)
                        break
                    directory = snapshot.directory(directory, component)
                if contract.import_path in unavailable:
                    continue
                if not exists(directory, parts[-1]):
                    absent(contract)
                    continue
                source = read(directory, parts[-1], MAX_SOURCE_BYTES)
                if _digest(source) != contract.source_sha256:
                    unavailable[contract.import_path] = PackageUnavailable(
                        contract.import_path, 'unreviewed-namespace-source',
                        'Entrypoint bytes differ from the fixed reviewed namespace artifact')
                    continue
                packages[contract.import_path] = NamespacePackage(
                    _evidence(root, contract, manifest, source), manifest, source,
                    contract.exports, _origins(contract))
            snapshot.verify()
    except OSError as error:
        raise PackageInputError(f'Cannot safely read namespace package path {root}: {error}') from error
    for contract in _CONTRACTS:
        if contract.import_path in packages and any(dep not in packages for dep in contract.dependencies):
            del packages[contract.import_path]
            unavailable[contract.import_path] = PackageUnavailable(
                contract.import_path, 'unavailable-namespace-dependency',
                'A fixed wildcard dependency namespace is unavailable in this capture')
    return NamespacePackageCapture(str(root),
        tuple(packages[c.import_path] for c in _CONTRACTS if c.import_path in packages),
        tuple(unavailable[c.import_path] for c in _CONTRACTS if c.import_path in unavailable))
