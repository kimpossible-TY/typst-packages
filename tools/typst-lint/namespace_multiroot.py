"""Conditional namespace captures from two explicit ordered package roots.

The first existing package-version path wins, even if its manifest or source is
missing or unreviewed. Both roots are opened simultaneously using no-follow
descriptors; higher-priority absences are checked again before the capture is
issued. This is sampled, in-memory evidence, not a live filesystem lease or a
proof of the compiler's eventual package selection.
"""
from contextlib import ExitStack
from dataclasses import astuple, dataclass
import os
import weakref

import namespace_packages as namespace
from package_loader import (MAX_MANIFEST_BYTES, MAX_SOURCE_BYTES, PackageEvidence,
                            PackageInputError, PackageUnavailable, _Snapshot)


@dataclass(frozen=True, slots=True)
class RootSelection:
    import_path: str
    root_index: int | None


@dataclass(frozen=True, slots=True, weakref_slot=True)
class MultiRootCapture:
    roots: tuple[str, str]
    packages: tuple
    unavailable: tuple[PackageUnavailable, ...]
    selections: tuple[RootSelection, ...]

    @property
    def evidence(self):
        return tuple(item.evidence for item in self.packages)


_ISSUED = {}


def _signature(capture):
    """Retain independent immutable field values to detect later alteration."""
    return (capture.roots,
            tuple((type(item), astuple(item)) for item in capture.packages),
            tuple(astuple(item) for item in capture.unavailable),
            tuple(astuple(item) for item in capture.selections))


def _issue(capture):
    identity = id(capture)
    def release(reference):
        found = _ISSUED.get(identity)
        if found is not None and found[0] is reference:
            _ISSUED.pop(identity, None)
    _ISSUED[identity] = (weakref.ref(capture, release), _signature(capture))
    return capture


def verify_multiroot_capture(capture):
    """Purely verify a locally issued, unchanged multi-root snapshot.

    Object issuance binds first-root absence evidence: serialized or fabricated
    records cannot promote a lower-priority package into verified evidence.
    """
    try:
        if type(capture) is not MultiRootCapture:
            return False
        issued = _ISSUED.get(id(capture))
        if issued is None or issued[0]() is not capture or issued[1] != _signature(capture):
            return False
        if (type(capture.roots) is not tuple or len(capture.roots) != 2
                or any(type(root) is not str or str(namespace._canonical_root(root)) != root
                       for root in capture.roots)
                or capture.roots[0] == capture.roots[1]
                or type(capture.packages) is not tuple or type(capture.unavailable) is not tuple
                or type(capture.selections) is not tuple):
            return False
        contracts = {item.import_path: item for item in namespace._CONTRACTS}
        if (len(capture.selections) != len(contracts)
                or any(type(item) is not RootSelection or type(item.import_path) is not str
                       or item.import_path not in contracts
                       or (item.root_index is not None and (type(item.root_index) is not int
                                                            or item.root_index not in (0, 1)))
                       for item in capture.selections)
                or len({item.import_path for item in capture.selections}) != len(contracts)):
            return False
        selected = {item.import_path: item.root_index for item in capture.selections}
        seen, available, total = set(), set(), 0
        for item in capture.packages:
            if type(item) not in (namespace.NamespacePackage, namespace.CollisionOnlyNamespace):
                return False
            if type(item.evidence) is not PackageEvidence:
                return False
            path = item.evidence.import_path
            contract = contracts.get(path)
            index = selected.get(path)
            if contract is None or path in seen or index is None:
                return False
            if type(item) is namespace.CollisionOnlyNamespace:
                variant = namespace.TextUtilsCollisionCapture(capture.roots[index], item, None)
                if (path != namespace.TEXT_UTILS or hasattr(item, 'origins')
                        or not namespace.verify_text_utils_collision_capture(variant)):
                    return False
            elif (type(item.manifest) is not bytes or type(item.source) is not bytes
                    or len(item.manifest) > MAX_MANIFEST_BYTES or len(item.source) > MAX_SOURCE_BYTES
                    or namespace._digest(item.manifest) != contract.manifest_sha256
                    or namespace._digest(item.source) != contract.source_sha256
                    or not namespace._metadata_matches(item.manifest, contract)
                    or item.evidence != namespace._evidence(namespace._canonical_root(capture.roots[index]),
                                                            contract, item.manifest, item.source)
                    or item.exports != contract.exports or item.origins != namespace._origins(contract)):
                return False
            total += len(item.manifest) + len(item.source)
            seen.add(path)
            available.add(path)
        for item in capture.unavailable:
            if (type(item) is not PackageUnavailable or type(item.import_path) is not str
                    or item.import_path not in contracts
                    or item.import_path in seen or type(item.code) is not str or not item.code
                    or type(item.reason) is not str or not item.reason):
                return False
            seen.add(item.import_path)
        return (total <= namespace.MAX_NAMESPACE_BYTES and seen == set(contracts)
                and all(set(contracts[path].dependencies) <= available for path in available))
    except (AttributeError, KeyError, TypeError, ValueError, OSError, RecursionError):
        return False


def _directory_change_signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_mtime_ns, info.st_ctime_ns)


def load_multiroot_capture(package_path, cache_path):
    """Capture fixed reviewed packages from explicit package then cache roots."""
    roots = tuple(namespace._canonical_root(path) for path in (package_path, cache_path))
    if roots[0] == roots[1]:
        raise PackageInputError('Package and cache roots must be distinct')
    packages, unavailable, selected = {}, {}, {}
    total = files = 0
    missing = []
    try:
        with ExitStack() as stack:
            snapshots = tuple(stack.enter_context(_Snapshot(root)) for root in roots)
            identities = [(os.fstat(item.directory_root.fd).st_dev, os.fstat(item.directory_root.fd).st_ino)
                          for item in snapshots]
            if identities[0] == identities[1]:
                raise PackageInputError('Package and cache roots must not alias the same directory')

            def exists(directory, name):
                before = _directory_change_signature(os.fstat(directory.fd))
                try:
                    os.stat(name, dir_fd=directory.fd, follow_symlinks=False)
                except FileNotFoundError:
                    if _directory_change_signature(os.fstat(directory.fd)) != before:
                        raise PackageInputError('Package absence changed while capturing roots')
                    missing.append((directory, name, before))
                    return False
                return True

            def read(snapshot, directory, name, limit):
                nonlocal total, files
                value = snapshot.read(directory, name, limit)
                files += 1
                total += len(value)
                if files > namespace.MAX_NAMESPACE_FILES or total > namespace.MAX_NAMESPACE_BYTES:
                    raise PackageInputError('Multi-root namespace snapshot exceeds its fixed budget')
                return value

            def absent(contract):
                unavailable[contract.import_path] = PackageUnavailable(
                    contract.import_path, 'missing-namespace-artifact',
                    'The selected package version or required manifest/entrypoint is absent')

            for contract in namespace._CONTRACTS:
                current = None
                for index, snapshot in enumerate(snapshots):
                    directory = snapshot.directory_root
                    for component in (contract.namespace, contract.name, contract.version):
                        if not exists(directory, component):
                            break
                        directory = snapshot.directory(directory, component)
                    else:
                        current = (index, snapshot, directory)
                        break
                if current is None:
                    selected[contract.import_path] = None
                    absent(contract)
                    continue
                index, snapshot, directory = current
                selected[contract.import_path] = index
                if not exists(directory, 'typst.toml'):
                    absent(contract)
                    continue
                manifest = read(snapshot, directory, 'typst.toml', MAX_MANIFEST_BYTES)
                if namespace._digest(manifest) != contract.manifest_sha256:
                    unavailable[contract.import_path] = PackageUnavailable(
                        contract.import_path, 'unreviewed-namespace-manifest',
                        'Selected manifest bytes differ from the fixed reviewed namespace artifact')
                    continue
                if not namespace._metadata_matches(manifest, contract):
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
                source = read(snapshot, directory, parts[-1], MAX_SOURCE_BYTES)
                if namespace._digest(source) != contract.source_sha256:
                    if (contract.import_path == namespace.TEXT_UTILS
                            and namespace._digest(source) == namespace._TEXT_COLLISION_CONTRACT.source_sha256):
                        variant = namespace.text_utils_collision_from_snapshot(roots[index], manifest, source)
                        if not namespace.verify_text_utils_collision_capture(variant):
                            raise PackageInputError('Namespace-only variant failed verification')
                        packages[contract.import_path] = variant.package
                        continue
                    unavailable[contract.import_path] = PackageUnavailable(
                        contract.import_path, 'unreviewed-namespace-source',
                        'Selected entrypoint bytes differ from the fixed reviewed namespace artifact')
                    continue
                packages[contract.import_path] = namespace.NamespacePackage(
                    namespace._evidence(roots[index], contract, manifest, source), manifest, source,
                    contract.exports, namespace._origins(contract))
            for snapshot in snapshots:
                snapshot.verify()
            for directory, name, expected in missing:
                if _directory_change_signature(os.fstat(directory.fd)) != expected:
                    raise PackageInputError('Package absence changed while capturing roots')
                try:
                    os.stat(name, dir_fd=directory.fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise PackageInputError('Package absence changed while capturing roots')
                if _directory_change_signature(os.fstat(directory.fd)) != expected:
                    raise PackageInputError('Package absence changed while capturing roots')
            for snapshot in snapshots:
                snapshot.verify()
    except OSError as error:
        raise PackageInputError(f'Cannot safely read explicit namespace roots: {error}') from error
    for contract in namespace._CONTRACTS:
        if contract.import_path in packages and any(dep not in packages for dep in contract.dependencies):
            del packages[contract.import_path]
            unavailable[contract.import_path] = PackageUnavailable(
                contract.import_path, 'unavailable-namespace-dependency',
                'A fixed wildcard dependency namespace is unavailable in this capture')
    capture = MultiRootCapture(tuple(map(str, roots)),
        tuple(packages[c.import_path] for c in namespace._CONTRACTS if c.import_path in packages),
        tuple(unavailable[c.import_path] for c in namespace._CONTRACTS if c.import_path in unavailable),
        tuple(RootSelection(c.import_path, selected[c.import_path]) for c in namespace._CONTRACTS))
    _issue(capture)
    if not verify_multiroot_capture(capture):
        raise PackageInputError('Internal multi-root namespace capture failed verification')
    return capture
