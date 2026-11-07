"""Internal prepare-before-fix, compile-and-publish composition, never CLI dispatch.

This API borrows ProtectedCoreFix's already-held root lock. The separately minted
output lease is required even when source exclusivity was already asserted.
"""
from __future__ import annotations

from pathlib import Path
import time

import closed_core_compile as compiler
from protected_fix import ProtectedCoreFix
from transactions import TransactionError
from typst_lint import InputError
from pdf_publication_store import (DEFAULT_PUBLICATION_LIMITS, OfflineOutputExclusive,
    PublicationError, PublicationPending, PublicationReport, _Store, _demand,
    _lease, _observation, literal_destination)


def _invocation_records(attempts):
    # Durable inert outcome summary; no argv is ever replayed. Full bounded
    # immutable invocation reports remain available to the original caller.
    return [dict(purpose=a.purpose, started=getattr(a, 'started', True),
                 returncode=a.returncode, lifecycle=getattr(a, 'lifecycle',
                    'uncertain' if a.returncode is None else
                    'signaled' if a.returncode < 0 else 'exited')) for a in attempts]


class PreparedPdfPublication:
    """One output baseline captured durably before this session validates sources.

    The session owns exactly one compiler call. There is deliberately no public
    publish-artifact method: an earlier/foreign artifact cannot establish ordering.
    """
    def __init__(self, fix, *, destination, lease, limits=DEFAULT_PUBLICATION_LIMITS, fault=None):
        _demand(type(fix) is ProtectedCoreFix, 'A real ProtectedCoreFix is required')
        self.fix = fix
        self.destination = literal_destination(destination)
        self.lease, self.limits, self.fault = lease, limits, fault
        self._active = self._used = self._attempted = False
        self._store = None
        self._artifact = None
        self._attempts = ()
        self._compiler_invoked = False
        self._compiler_status = None
        self.report = None

    def _source(self):
        applied = getattr(self.fix, '_applied_validation', None) is not None
        journal = None
        if applied:
            journal = self.fix.summary.journal_id
        return ('committed' if applied else 'not_applied'), journal

    def _report(self, phase, gate, disposition, recovery=False, reason=None, observed=None):
        source, journal = self._source()
        return PublicationReport(phase, gate, self._compiler_invoked, self._compiler_status,
            self._attempts, source, disposition, recovery,
            None if self._store is None else self._store.journal_id,
            None if observed is None else observed.sha256, journal, reason)

    def __enter__(self):
        _demand(not self._active and not self._used, 'Publication contexts cannot be reused')
        _lease(self.lease)
        fix, tx = self.fix, self.fix.transaction
        _demand(fix._active and not fix._history and fix._candidate is None
                and fix._compile_validation is None and fix._applied_validation is None
                and tx._issued is None and tx._committed_receipt is None,
                'Publication preparation must precede source validation and application')
        tx._check_active()
        store = _Store(tx.root, transaction=tx, limits=self.limits, create=True, fault=self.fault)
        self._store = store
        try:
            store.__enter__()
            # Pending/corrupt inventory precedes even destination capture, source
            # validation/application and every compiler invocation.
            entries = store.inventory()
            if any(e.pending for e in entries):
                raise PublicationPending(entries)
            store.strengthen_terminals(entries)
            store.bind_destination(self.destination)
            paths = [tx.root / p for p in tx.targets]
            paths.extend(e.path for e in tx.evidence_inputs)
            paths.append(tx.store)
            paths.extend(compiler._LAUNCHERS)
            paths.extend(record.path for record in compiler._NATIVE_ARTIFACTS.values())
            try:
                paths.append(compiler._reviewed_native_artifact().path)
            except compiler.CompileSafetyError:
                pass  # The compiler will refuse the unsupported platform itself.
            store.prepare(protected_paths=tuple(paths))
            self._active = True
            return self
        except BaseException as error:
            # Preparation may already have durable evidence. Never remove it.
            self._used = True
            if store.journal_fd is not None:
                try:
                    self.report = self._failure(error, compiler_error=False)
                except BaseException:
                    pass
            store.close()
            raise

    def _check(self):
        _demand(self._active and self.fix._active, 'Active prepared publication and source session required')
        self._store.check()

    def _outcome(self, value):
        report = getattr(value, 'report', None)
        if report is not None and hasattr(report, 'compiler_invoked'):
            self._attempts = tuple(report.attempted_invocations)
            self._compiler_invoked = report.compiler_invoked
            self._compiler_status = report.compiler_status
        elif isinstance(value, compiler.ClosedCoreCompileArtifact):
            # Compatibility only while the independent reporting prerequisite
            # lands. These actual opaque artifact observations are not authority.
            self._attempts = tuple(value.probes) + (value.compilation,)
            self._compiler_invoked = True
            self._compiler_status = value.compilation.returncode
        elif isinstance(value, compiler.UnsupportedCompileProfile):
            self._compiler_invoked, self._compiler_status = False, None

    def _failure(self, error, *, compiler_error):
        store = self._store
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
            # Exact same live bridge/session receipt, not its reporting summary.
            compiler._guard(self.fix, snapshot)
            store.assert_output(store.old)
            if profile != compiler.RESOURCE_PROFILE or type(profile) is not str:
                raise compiler.UnsupportedCompileProfile('Explicit embedded-ascii-v1 profile required')
            store.event('compile_launch_intent', source_sha256=snapshot.source_sha256,
                        source_journal_id=self.fix.summary.journal_id, profile=profile,
                        source_target=snapshot.target, config_sha256=snapshot.config_sha256,
                        catalog_sha256=snapshot.catalog_sha256)
            store.hit('before_compile', self.destination)
            store.assert_output(store.old)
            self._compiler_invoked = None  # Durable launch intent, outcome not yet known.
            try:
                artifact = compiler.compile_committed_snapshot(self.fix, snapshot, profile=profile,
                    deadline_seconds=deadline_seconds, cancel_event=cancel_event)
            except BaseException as compile_error:
                # Only the compiler call owns this attempt ledger. Later artifact
                # guards can carry an empty report and must not erase execution.
                self._outcome(compile_error)
                raise
            self._artifact = artifact
            self._outcome(artifact)
            compiler.assert_artifact_current(self.fix, artifact)
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
                _demand(self._artifact is artifact, 'Foreign publication artifact')
                compiler.assert_artifact_current(self.fix, artifact)
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
        try:
            if self._active and not self._attempted:
                self._attempted = True
                self.report = self._failure(exc or PublicationError('Publication context exited before compile'),
                                            compiler_error=False)
        finally:
            self._active = False
            self._used = True
            if self._store is not None:
                self._store.close()
        return False
