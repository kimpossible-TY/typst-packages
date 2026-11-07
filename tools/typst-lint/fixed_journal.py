"""Fixed public offline-profile journal location and truly read-only inventory.

This location is independent of config and environment. Keep both this store
and the original canonical root location: this route cannot discover removed or
forged stores, relocated roots, or journals made elsewhere by internal callers.
An absent store is an observation, not proof of historical transaction safety.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

import transactions as _tx
from transactions import (DEFAULT_READ_LIMITS, JournalEntry, ReadLimits,
                          RecoveryReport, TransactionError)


class JournalInventoryError(TransactionError):
    """The existing journal inventory could not be safely inspected."""
    code = 'journal-inventory-unavailable'


def fixed_journal_directory(root: Path) -> Path:
    """Derive the one public store from an already canonical absolute root.

    Do not resolve symlinks here. Callers choose the canonical root once, and
    all subsequent filesystem access verifies it using no-follow descriptors.
    """
    root = _tx._absolute(root)
    _tx._fail(root != root.parent, 'The filesystem root cannot be a protected project root')
    store = root.parent / '.typst-lint-journals'
    _tx._fail(store != root, 'Journal storage must be outside the selected root')
    return store


@dataclass(frozen=True)
class JournalInventory:
    root: Path
    store: Path
    namespace: Path
    store_present: bool
    namespace_present: bool
    entries: tuple[JournalEntry, ...] = ()

    @property
    def pending(self) -> tuple[str, ...]:
        return tuple(entry.journal for entry in self.entries
                     if entry.status not in ('committed', 'recovered'))

    @property
    def complete(self) -> bool:
        """Every observed entry is complete; this is not a historical claim."""
        return not self.pending


class _InventoryReader(_tx._JournalReader):
    def __init__(self, root, store, namespace, limits):
        self.root = root
        self.store = store
        self._namespace = namespace
        self._limits = limits


def _unchanged_directory(path: Path, expected: tuple[int, int]) -> None:
    fd = _tx._directory(path)
    try:
        _tx._fail(_tx._identity(os.fstat(fd)) == expected,
                  f'Directory identity changed while inspecting journals: {path}')
    finally:
        os.close(fd)


def _absent(parent_fd: int, name: str) -> None:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise JournalInventoryError(f'Journal directory appeared while inspecting: {name}')


def journal_inventory(root: Path, *, limits: ReadLimits = DEFAULT_READ_LIMITS) -> JournalInventory:
    """Lock and inspect existing journals, without an offline lease or writes.

    No source or current config is opened. Directory creation, fsync, chmod and
    recovery are deliberately absent. Corrupt/unreadable names remain visible
    and pending. Unsafe or raced storage raises JournalInventoryError instead of
    being represented as an empty, apparently clean inventory.
    """
    root = _tx._absolute(root)
    store = fixed_journal_directory(root)
    _tx._fail(type(limits) is ReadLimits, 'Explicit immutable ReadLimits required')
    limits.__post_init__()
    namespace = store / ('root-' + _tx._hash(os.fsencode(root)))
    _tx._fail(os.name == 'posix' and _tx.fcntl is not None
              and hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY')
              and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd
              and os.scandir in os.supports_fd,
              'Journal inventory requires supported POSIX dirfd, nofollow and flock primitives')
    reader = _InventoryReader(root, store, namespace, limits)
    fds = []
    identities = []
    try:
        root_fd = _tx._directory(root)
        fds.append(root_fd)
        try:
            _tx.fcntl.flock(root_fd, _tx.fcntl.LOCK_EX | _tx.fcntl.LOCK_NB)
        except OSError as error:
            raise JournalInventoryError(f'Root lock unavailable: {root}: {error}') from error
        reader._root_identity = _tx._identity(os.fstat(root_fd))
        identities.append((root, reader._root_identity))
        parent_fd = _tx._directory(store.parent)
        fds.append(parent_fd)
        parent_identity = _tx._identity(os.fstat(parent_fd))
        identities.append((store.parent, parent_identity))
        _tx._fail(parent_identity[0] == reader._root_identity[0],
                  'Journal storage parent must be on the source filesystem')
        _tx._fail(_tx._identity(os.stat(root.name, dir_fd=parent_fd, follow_symlinks=False))
                  == reader._root_identity, 'Root identity changed while inspecting journals')
        try:
            store_fd = os.open(store.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                               dir_fd=parent_fd)
        except FileNotFoundError:
            for path, identity in identities:
                _unchanged_directory(path, identity)
            _absent(parent_fd, store.name)
            return JournalInventory(root, store, namespace, False, False)
        fds.append(store_fd)
        _tx._private_directory(store_fd, store, max_bytes=limits.max_metadata_bytes)
        reader._store_identity = _tx._identity(os.fstat(store_fd))
        _tx._fail(reader._store_identity[0] == reader._root_identity[0],
                  'Journal storage must be on the source filesystem')
        identities.append((store, reader._store_identity))
        try:
            namespace_fd = os.open(namespace.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                   dir_fd=store_fd)
        except FileNotFoundError:
            for path, identity in identities:
                _unchanged_directory(path, identity)
            _tx._private_directory(store_fd, store, max_bytes=limits.max_metadata_bytes)
            _absent(store_fd, namespace.name)
            return JournalInventory(root, store, namespace, True, False)
        fds.append(namespace_fd)
        reader._ns_fd = namespace_fd
        _tx._private_directory(namespace_fd, namespace, max_bytes=limits.max_metadata_bytes)
        namespace_identity = _tx._identity(os.fstat(namespace_fd))
        _tx._fail(namespace_identity[0] == reader._store_identity[0],
                  'Journal namespace must be on the source filesystem')
        identities.append((namespace, namespace_identity))
        entries = reader._journal_entries()
        for path, identity in identities:
            _unchanged_directory(path, identity)
        _tx._private_directory(store_fd, store, max_bytes=limits.max_metadata_bytes)
        _tx._private_directory(namespace_fd, namespace, max_bytes=limits.max_metadata_bytes)
        return JournalInventory(root, store, namespace, True, True, entries)
    except JournalInventoryError:
        raise
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        raise JournalInventoryError(f'{type(error).__name__}: {error}') from error
    finally:
        for fd in reversed(fds):
            os.close(fd)


def inventory_json(report: JournalInventory) -> dict:
    return dict(root=str(report.root), store=str(report.store), namespace=str(report.namespace),
                store_present=report.store_present, namespace_present=report.namespace_present,
                complete=report.complete, pending=list(report.pending),
                entries=[dict(journal=entry.journal, status=entry.status, error=entry.error,
                              targets=list(entry.targets))
                         for entry in report.entries])


def recovery_report_json(report: RecoveryReport) -> dict:
    """Serialize immutable conflict maps without deepcopy/asdict failures."""
    return dict(journal=report.journal, restored=list(report.restored),
                unchanged=list(report.unchanged), changed=list(report.changed),
                unresolved=list(report.unresolved), preserved=list(report.preserved),
                conflicts={path: _tx._fp_json(fp) for path, fp in report.conflicts.items()},
                errors=list(report.errors), complete=report.complete)
