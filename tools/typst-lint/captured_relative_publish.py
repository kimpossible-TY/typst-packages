"""Internal prepare-before-repair publication for one captured relative graph.

The live bridge owns source authority and its existing transaction/root lock.
A separate output lease and durably prepared baseline precede every validation
or apply attempt. Neither precompiled artifacts nor caller bytes are accepted.
"""
from __future__ import annotations

import math
import threading
import time
from typing import NamedTuple
import weakref

import captured_relative_compile as compiler
import closed_core_compile as runtime
from captured_relative_fix import ProtectedCapturedRelativeFix
from transactions import TransactionError
from resource_profiles import validate_resource_profile
from pdf_publication_store import (DEFAULT_PUBLICATION_LIMITS, OfflineOutputExclusive,
    PublicationError, PublicationPending, PublicationReport, _Store, _demand,
    _lease, literal_destination)


def _invocation_records(attempts):
    # Durable inert outcome summary; no argv is ever replayed. Full bounded
    # immutable invocation reports remain available to the original caller.
    return [dict(purpose=a.purpose, started=getattr(a, 'started', True),
                 returncode=a.returncode, lifecycle=getattr(a, 'lifecycle',
                    'uncertain' if a.returncode is None else
                    'signaled' if a.returncode < 0 else 'exited')) for a in attempts]


class _Binding(NamedTuple):
    publication: object
    fix: object
    owner: object
    session: object
    capture: object
    baseline: object
    transaction: object
    store: object
    destination: str
    lease: object


# Original identity records are separate from caller-visible instance fields.
# Explicit publication references are weak. Closing or failed preparation also
# drops registration, including indirect references through trusted fault hooks.
_CONSTRUCTIONS = weakref.WeakKeyDictionary()
_BINDINGS = weakref.WeakKeyDictionary()


class PreparedCapturedRelativePdfPublication:
    """One output baseline captured durably before this session validates sources.

    The session owns exactly one compiler call. There is deliberately no public
    publish-artifact method: an earlier/foreign artifact cannot establish ordering.
    """
    def __init__(self, fix, *, destination, lease, limits=DEFAULT_PUBLICATION_LIMITS, fault=None):
        _demand(type(fix) is ProtectedCapturedRelativeFix,
                'A real ProtectedCapturedRelativeFix is required')
        self.fix = fix
        self.destination = literal_destination(destination)
        self.lease, self.limits, self.fault = lease, limits, fault
        self._construction = (weakref.ref(self), fix, self.destination, lease)
        _CONSTRUCTIONS[self] = self._construction
        self._active = self._used = self._attempted = False
        self._store = self._binding = None
        self._artifact = None
        self._attempts = ()
        self._compiler_invoked = False
        self._compiler_status = None
        self._compile_report_facts = None
        self.report = None

    @property
    def compile_report(self):
        # Fresh historical values from independent compiler-boundary facts.
        # A shared artifact/report mutation can neither rebase nor erase them.
        return (None if self._compile_report_facts is None
                else compiler._restore(self._compile_report_facts))

    def _source(self):
        binding = _BINDINGS.get(self)
        original = _CONSTRUCTIONS.get(self)
        fix = original[1] if binding is None else binding.fix
        applied = getattr(fix, '_applied_validation', None) is not None
        tx = None if binding is None else binding.transaction
        return ('committed' if applied else 'not_applied'), (
            getattr(tx, '_journal', None) if applied else None)

    def _report(self, phase, gate, disposition, recovery=False, reason=None, observed=None):
        source, journal = self._source()
        binding = _BINDINGS.get(self)
        store = None if binding is None else binding.store
        report = self.compile_report
        attempts = () if report is None else tuple(report.attempted_invocations)
        return PublicationReport(phase, gate, self._compiler_invoked, self._compiler_status,
            attempts, source, disposition, recovery,
            None if store is None else store.journal_id,
            None if observed is None else observed.sha256, journal, reason, report)

    @staticmethod
    def _borrow(fix):
        try:
            return fix._borrow_publication_transaction()
        except (TransactionError, AttributeError, TypeError, ValueError) as error:
            raise PublicationError(f'Relative publication requires a pristine live bridge: {error}') from error

    def _assert_identity(self):
        original = _CONSTRUCTIONS.get(self)
        _demand(type(self) is PreparedCapturedRelativePdfPublication
                and original is not None and self._construction is original
                and original[0]() is self and self.fix is original[1]
                and self.destination == original[2] and self.lease is original[3],
                'Only the original publication and constructor binding may be used')

    def __enter__(self):
        self._assert_identity()
        _demand(not self._active and not self._used, 'Publication contexts cannot be reused')
        _lease(self.lease)
        fix = self.fix
        _demand(type(fix) is ProtectedCapturedRelativeFix,
                'A real ProtectedCapturedRelativeFix is required')
        tx = self._borrow(fix)
        store = _Store(tx.root, transaction=tx, limits=self.limits, create=True, fault=self.fault)
        self._store = store
        binding = _Binding(weakref.ref(self), fix, fix._owner, fix._session, fix._capture,
                           fix._baseline_graph, tx, store, self.destination, self.lease)
        self._binding = binding
        _BINDINGS[self] = binding
        try:
            store.__enter__()
            # Pending/corrupt history refuses before even destination capture,
            # validation, source application or any compiler invocation.
            entries = store.inventory()
            if any(e.pending for e in entries):
                raise PublicationPending(entries)
            store.strengthen_terminals(entries)
            store.bind_destination(self.destination)
            # Include every captured evidence input, even unused or transitive
            # modules and external config/catalog evidence, not only the root.
            paths = [tx.root / p for p in tx.targets]
            paths.extend(e.path for e in tx.evidence_inputs)
            paths.append(tx.store)
            paths.extend(runtime._LAUNCHERS)
            paths.extend(record.path for record in runtime._NATIVE_ARTIFACTS.values())
            try:
                paths.append(runtime._reviewed_native_artifact().path)
            except compiler.CompileSafetyError:
                pass  # The graph compiler refuses the unsupported platform.
            store.prepare(protected_paths=tuple(paths))
            # prepare has hooks. A validation/apply attempt inside any hook must
            # not turn already-started source work into a prepared chronology.
            _demand(self._borrow(fix) is tx, 'Publication transaction changed during preparation')
            self._active = True
            self._check()
            return self
        except BaseException as error:
            self._used = True
            self._active = False
            if store.journal_fd is not None:
                try:
                    self.report = self._failure(error, compiler_error=False)
                except BaseException:
                    pass
            try:
                store.close()
            finally:
                _BINDINGS.pop(self, None)
                _CONSTRUCTIONS.pop(self, None)
            raise

    def _check(self):
        self._assert_identity()
        binding = _BINDINGS.get(self)
        _demand(self._active and type(binding) is _Binding
                and self._binding is binding and binding.publication() is self and self.fix is binding.fix
                and binding.fix is self._construction[1]
                and type(binding.fix) is ProtectedCapturedRelativeFix
                and binding.fix._active and binding.fix._owner is binding.owner
                and binding.fix._session is binding.session
                and binding.fix._capture is binding.capture
                and binding.fix._baseline_graph is binding.baseline
                and binding.fix._tx is binding.transaction
                and binding.session._tx is binding.transaction
                and self._store is binding.store
                and binding.store.transaction is binding.transaction
                and binding.store.root == binding.transaction.root
                and self.destination == binding.destination
                and binding.store.destination == binding.destination
                and self.lease is binding.lease,
                'Active original publication, bridge and transaction binding required')
        _lease(binding.lease)
        # Binding-only guard is intentional after apply. assert_current on the
        # original input capture would incorrectly reject a genuine root repair.
        binding.fix._assert_capture_binding()
        binding.transaction._check_active()
        binding.store.check()

    def _outcome(self, value):
        # Called only at this publication's one compiler call boundary. Later
        # safety errors with empty/default reports cannot overwrite actual facts.
        report = getattr(value, 'report', None)
        if type(report) is compiler.CompileReport:
            self._compile_report_facts = compiler._retain(report)
            historical = self.compile_report
            self._attempts = tuple(historical.attempted_invocations)
            self._compiler_invoked = historical.compiler_invoked
            self._compiler_status = historical.compiler_status
        elif isinstance(value, compiler.UnsupportedCompileProfile):
            self._compiler_invoked, self._compiler_status = False, None

    def _failure(self, error, *, compiler_error):
        # Reconcile only the originally prepared output store, even if an
        # exposed session attribute was replaced before an authority refusal.
        store = _BINDINGS[self].store
        reason = str(error)[:2048]
        gate = 2 if isinstance(error, compiler.UnsupportedCompileProfile) else 3
        if compiler_error and isinstance(error, compiler.CompilerFailure):
            # Compiler failure status remains raw; gate is a separate outcome.
            gate = 3
        if getattr(store, '_committed', False):
            return self._report('published', 0, 'published', observed=store.postimage)
        if getattr(store, '_completion_started', False):
            # The completion event may already be durable. Do not append a
            # contradictory failure marker and do not blindly start rollback.
            observed = None
            try:
                _, observed = store.output_now()
            except (OSError, TransactionError):
                pass
            return self._report('publication_commit_uncertain', 3, 'uncertain', True,
                                reason, observed)
        replaced = getattr(store, '_replace_returned', False)
        if replaced:
            try:
                post = getattr(store, 'postimage', None)
                _demand(post is not None, 'Rename occurred but postimage was not safely observed')
                restored = store.restore(post)
                return self._report('restored', 3, 'restored', reason=reason, observed=restored)
            except BaseException as recovery_error:
                if getattr(store, '_restored', False):
                    return self._report('restored', 3, 'restored', reason=reason,
                                        observed=store._last_restoration)
                reason = f'{reason}; restoration unresolved: {recovery_error}'[:2048]
                try:
                    store.event('recovery_required', reason=reason)
                except BaseException:
                    pass
                observed = None
                try:
                    _, observed = store.output_now()
                except (OSError, TransactionError):
                    pass
                return self._report('recovery_required', 3, 'preservedconflict', True,
                                    reason, observed)
        # os.replace itself can have uncertain effect if an injected/system
        # wrapper throws after executing it. Observe before classifying untouched.
        if getattr(store, '_replace_started', False):
            try:
                store.assert_output(store.old)
            except (OSError, TransactionError):
                try:
                    store.event('recovery_required', reason=reason)
                except BaseException:
                    pass
                return self._report('recovery_required', 3, 'uncertain', True, reason)
        try:
            now = store.assert_output(store.old)
            store.event('aborted_unpublished', reason=reason,
                compiler_invoked=self._compiler_invoked, compiler_status=self._compiler_status,
                invocations=_invocation_records(self._attempts))
            return self._report('aborted_unpublished', gate, 'untouched', reason=reason, observed=now)
        except BaseException as conflict:
            if getattr(store, '_aborted', False):
                return self._report('aborted_unpublished', gate, 'untouched', reason=reason,
                                    observed=store.old)
            reason = f'{reason}; unchanged baseline not confirmed: {conflict}'[:2048]
            try:
                store.event('recovery_required', reason=reason)
            except BaseException:
                pass
            return self._report('recovery_required', 3, 'preservedconflict', True, reason)

    def compile_and_publish(self, snapshot, *, profile, deadline_seconds=10.0, cancel_event=None):
        self._check()
        _demand(not self._attempted, 'A prepared publication owns exactly one compile attempt')
        self._attempted = True
        store = self._store
        artifact = None
        started = time.monotonic()
        try:
            # Exact live original bridge/session graph, never a reporting summary.
            self._check()
            binding = _BINDINGS[self]
            compiler._guard(binding.fix, snapshot)
            store.assert_output(store.old)
            try:
                validate_resource_profile(profile)
            except ValueError as error:
                raise compiler.UnsupportedCompileProfile(str(error)) from error
            if (type(deadline_seconds) not in (int, float) or not math.isfinite(deadline_seconds)
                    or not 0 < deadline_seconds <= 120
                    or (cancel_event is not None and type(cancel_event) is not threading.Event)):
                raise compiler.UnsupportedCompileProfile('Invalid bounded deadline or cancellation event')
            # Preparation is deliberately resource-neutral. Only the explicitly
            # selected profile can add a font collision check, before capture or
            # any durable launch intent. ASCII does not stat a system font.
            resource_paths = runtime.resource_profile_protected_paths(profile)
            if resource_paths:
                store.check_collisions(resource_paths)
            store.event('compile_launch_intent', source_sha256=runtime._hash(snapshot.sources[snapshot.target]),
                        source_journal_id=binding.transaction._journal, profile=profile,
                        source_target=snapshot.target, config_sha256=snapshot.config_sha256,
                        catalog_sha256=snapshot.catalog_sha256)
            store.hit('before_compile', self.destination)
            store.assert_output(store.old)
            self._check()
            compiler._guard(binding.fix, snapshot)
            self._compiler_invoked = None  # Durable launch intent, outcome not yet known.
            try:
                artifact = compiler.compile_committed_graph(binding.fix, snapshot, profile=profile,
                    deadline_seconds=deadline_seconds, cancel_event=cancel_event)
            except BaseException as compile_error:
                # Only the compiler call owns this attempt ledger. Later artifact
                # guards can carry an empty report and must not erase execution.
                self._outcome(compile_error)
                raise
            self._artifact = artifact
            self._outcome(artifact)
            compiler.assert_artifact_current(binding.fix, artifact)
            store.assert_output(store.old)
            store.event('compiled', compiler_sha256=artifact.compiler.sha256,
                pdf_sha256=artifact.pdf_sha256, invocations=_invocation_records(self._attempts),
                compiler_invoked=self._compiler_invoked, compiler_status=self._compiler_status)
            store.hit('after_compiled', self.destination)
            def authority():
                self._check()
                if cancel_event is not None and cancel_event.is_set():
                    raise PublicationError('Publication cancelled')
                if time.monotonic() - started >= deadline_seconds:
                    raise PublicationError('Publication deadline exceeded')
                _demand(self._binding is binding and self._artifact is artifact,
                        'Foreign publication binding or artifact')
                compiler._guard(binding.fix, snapshot)
                compiler.assert_artifact_current(binding.fix, artifact)
            authority()
            raw = artifact.pdf_bytes
            stage = store.stage(raw)
            post = store.replace(stage, authority)
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                # Reconcile permitted output-only rollback, then preserve signal
                # semantics while exposing the report on this session.
                self.report = self._failure(error, compiler_error=True)
                raise
            self.report = self._failure(error, compiler_error=True)
            raise PublicationError(str(error), self.report) from error
        # A genuinely durable published event has ended the mutation path.
        # Reporting/allocation/faults here can never enter the rollback handler.
        self.report = self._report('published', 0, 'published', observed=post)
        store.hit('after_published', self.destination)
        return self.report

    def __exit__(self, exc_type, exc, tb):
        binding = _BINDINGS.get(self)
        if binding is None or binding.publication() is not self:
            # A copied, never-entered or already-closed object owns no store.
            self._active = False
            self._used = True
            _CONSTRUCTIONS.pop(self, None)
            return False
        try:
            if self._active and not self._attempted:
                self._attempted = True
                self.report = self._failure(exc or PublicationError('Publication context exited before compile'),
                                            compiler_error=False)
        finally:
            self._active = False
            self._used = True
            binding = _BINDINGS.get(self)
            try:
                if binding is not None and binding.publication() is self:
                    binding.store.close()
            finally:
                _BINDINGS.pop(self, None)
                _CONSTRUCTIONS.pop(self, None)
        return False
