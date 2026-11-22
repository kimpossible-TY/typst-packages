"""Root-only repair of one transaction-captured inert relative graph.

The explicit graph CLI uses the separate graph compiler and PDF publisher.
Existing single-source compiler and publisher APIs do not accept these receipts.
The runtime is trusted Python, not a sandbox against arbitrary program changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
import weakref

import captured_relative_core as core
from captured_relative_inputs import CapturedRelativeInputSession
from candidate_convergence import converge
from edit_plan import plan
from protected_fix import (AppliedInsertion, _attachment_roles, _catalog, _config,
                           _findings, _matches_authority, _retain_authority,
                           _script_insertions, _script_ownership)
from script_rule import RULE_ID as SCRIPT, ScriptResult, analyze as analyze_script
from syntax_client import AdapterError, Tree, parse
from transactions import (DEFAULT_READ_LIMITS, CommittedSnapshot, GraphAnalysis,
                          GraphSnapshot, SourceEdit, TransactionError)
from typst_lint import digest

CORE_RULE_IDS = frozenset(core.CORE_RULE_IDS)
_SEAL = object()


class CapturedRelativeFixError(TransactionError):
    """Changed candidate, graph, coverage, provenance or live filesystem state."""


def _require(condition, message):
    if not condition:
        raise CapturedRelativeFixError(message)


def _issued(kind, **values):
    instance = object.__new__(kind)
    for name, value in values.items():
        object.__setattr__(instance, name, value)
    return instance


def _binding(binding, root):
    # Imports are an initial prefix. Script-only insertions cannot change their
    # node IDs/spans, so only the root-source digest is excluded here. All
    # module hashes/spans and every complete declaration/alias/import route stay.
    return replace(binding, provenance=tuple(
        replace(step, source_sha256='') if step.path == root else step
        for step in binding.provenance))


def _semantics(proof, root):
    return (proof.source_profile, proof.target_path, proof.root_grammar_version,
            proof.module_grammar_version, proof.parser_contract,
            tuple(item for item in proof.sources if item.path != root),
            proof.edges, proof.modules,
            tuple(_binding(item, root) for item in proof.root_bindings),
            tuple((use.path, use.ordinal, use.syntax_kind, use.name,
                   _binding(use.binding, root), use.owners, use.operands)
                  for use in proof.imported_uses),
            proof.evaluated_rule_ids, proof.not_applicable_rule_ids,
            proof.structural_reasons)


@dataclass(frozen=True, slots=True)
class _Selection:
    source_profile: str
    root_grammar_version: str
    module_grammar_version: str
    parser_contract: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Pass:
    tree: Tree
    result: ScriptResult
    analysis: GraphAnalysis
    proof: core.CapturedRelativeCoreProof
    module_results: tuple[tuple[str, ScriptResult], ...]


@dataclass(frozen=True, slots=True, init=False, eq=False)
class CapturedRelativeFixCandidate:
    """Opaque bridge token. Copies, reports and a bare tx token confer nothing."""
    sources: Mapping[str, bytes]
    passes: int
    findings: frozenset[str]
    _token: object = field(repr=False)
    _owner: object = field(repr=False)
    _seal: object = field(repr=False)

    def __init__(self, *args, **kwargs):
        raise TypeError('Candidates are issued only by an active relative fix bridge')


@dataclass(frozen=True, slots=True)
class CapturedRelativeSourceSummary:
    """Final validated source membership, not compiler-observed dependencies."""
    path: str
    sha256: str
    size: int
    role: str


@dataclass(frozen=True, slots=True)
class CapturedRelativeImportSummary:
    importer: str
    request: str
    target: str
    node_id: int
    span: tuple[int, int]
    items: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class CapturedRelativeFixSummary:
    """Reporting only, never input to apply or committed authority."""
    profile: str
    target: str
    original_sha256: str
    final_sha256: str
    required_rule_ids: tuple[str, ...]
    evaluated_rule_ids: tuple[str, ...]
    structural_reasons: tuple[tuple[str, str], ...]
    applied_insertions: tuple[AppliedInsertion, ...]
    root_grammar_version: str
    module_grammar_version: str
    parser_contract: tuple[str, ...]
    modified_paths: tuple[str, ...] = ()
    journal_id: str | None = None
    sources: tuple[CapturedRelativeSourceSummary, ...] = ()
    import_edges: tuple[CapturedRelativeImportSummary, ...] = ()


def _restore_summary(retained):
    """Copy only the original finite summary shape, never a changed report."""
    kind, expected = retained
    if kind in (str, bytes, int, bool, type(None)):
        return expected
    if kind is tuple:
        return tuple(_restore_summary(item) for item in expected)
    _require(kind in (CapturedRelativeFixSummary, CapturedRelativeSourceSummary,
                      CapturedRelativeImportSummary, AppliedInsertion),
             'Invalid retained relative summary record')
    return kind(**{name: _restore_summary(fact) for name, fact in expected})


@dataclass(frozen=True, slots=True)
class _Validation:
    candidate: CapturedRelativeFixCandidate
    replay: object
    captures: Mapping[str, Tree]
    proof: core.CapturedRelativeCoreProof
    history: tuple[_Pass, ...]
    root: Path
    target: str
    evidence: Mapping[str, bytes | None]
    config_sha256: str
    catalog_sha256: str
    selection: _Selection
    summary: CapturedRelativeFixSummary


@dataclass(frozen=True, slots=True, init=False, eq=False)
class CapturedRelativeCommittedGraph:
    """Opaque live committed graph. It is NOT an existing compiler input."""
    root: Path
    target: str
    source_profile: str
    root_grammar_version: str
    module_grammar_version: str
    parser_contract: tuple[str, ...]
    captures: Mapping[str, Tree] = field(repr=False)
    sources: Mapping[str, bytes] = field(repr=False)
    evidence: Mapping[str, bytes | None] = field(repr=False)
    proof: core.CapturedRelativeCoreProof
    required_rule_ids: tuple[str, ...]
    evaluated_rule_ids: tuple[str, ...]
    structural_reasons: tuple[tuple[str, str], ...]
    config_sha256: str
    catalog_sha256: str
    _receipt: CommittedSnapshot = field(repr=False)
    _validation: _Validation = field(repr=False)
    _owner: object = field(repr=False)
    _seal: object = field(repr=False)

    def __init__(self, *args, **kwargs):
        raise TypeError('Committed graphs require this active bridge\'s successful apply')


class ProtectedCapturedRelativeFix:
    """Own exactly one capture session and borrow its one locked transaction.

    ``lease`` is the caller's genuine offline assertion. ``fault`` is only a
    transaction test hook. No existing capture/proof/transaction can be supplied.
    """
    def __init__(self, root: Path, target: str, *, journal_directory: Path,
                 lease, limits=DEFAULT_READ_LIMITS, fault=None,
                 create_journal_directory=False, source_profile=core.SOURCE_PROFILE):
        self._session = CapturedRelativeInputSession(root, target,
            journal_directory=journal_directory, lease=lease, limits=limits,
            create_journal_directory=create_journal_directory,
            source_profile=source_profile, fault=fault)
        self.target = target
        self.source_profile = source_profile
        self.root_grammar_version = core.ROOT_GRAMMAR_VERSION
        self.module_grammar_version = core.MODULE_GRAMMAR_VERSION
        self.parser_contract = core.PARSER_CONTRACT
        self._selection = _Selection(source_profile, self.root_grammar_version,
                                     self.module_grammar_version, self.parser_contract)
        self._selection_facts = _retain_authority(self._selection)
        self._owner = object()
        self._bridge_identity = weakref.ref(self)
        self._active = False
        self._tx = self._capture = self._baseline_graph = None
        self._capture_facts = None
        self._candidate = self._validation = self._validation_facts = None
        self._validation_pins = None
        self._applied_validation = self._committed = self._committed_facts = None
        self._receipt = self._summary = self._validated_summary = None
        self._summary_facts = None
        self._history = []
        # Monotonic admission facts: even a refusal before history/candidate
        # issuance means output preparation can no longer precede validation.
        self._validation_attempt_started = self._apply_attempt_started = False

    def _assert_publication_pristine(self):
        """Private pre-validation gate; never a post-apply baseline refresh."""
        identity = getattr(self, '_bridge_identity', None)
        _require(type(self) is ProtectedCapturedRelativeFix
                 and type(identity) is weakref.ReferenceType and identity() is self,
                 'An exact original active relative bridge is required for publication')
        self._assert_capture_binding()
        tx = self._tx
        _require(self._validation_attempt_started is False
                 and self._apply_attempt_started is False
                 and not self._history
                 and all(getattr(self, name) is None for name in (
                     '_candidate', '_validation', '_validation_facts', '_validation_pins',
                     '_applied_validation', '_committed', '_committed_facts', '_receipt',
                     '_summary', '_summary_facts', '_validated_summary'))
                 and tx._issued is None and tx._issued_facts is None
                 and tx._committed_receipt is None and tx._committed_graph is None
                 and tx._committed_files is None and tx._committed_evidence is None,
                 'Publication preparation must precede every validation or apply attempt')
        self._session.assert_current(self._capture)
        tx._check_active()

    def _borrow_publication_transaction(self):
        """Borrow only this session's original pristine locked transaction."""
        self._assert_publication_pristine()
        return self._tx

    def _assert_selection(self):
        _require(_matches_authority(self._selection, self._selection_facts)
                 and type(getattr(self, 'target', None)) is str
                 and self.target == self._session._target
                 and all(type(getattr(self, name, None)) is type(getattr(self._selection, name))
                         and getattr(self, name) == getattr(self._selection, name)
                         for name in ('source_profile', 'root_grammar_version',
                                      'module_grammar_version', 'parser_contract')),
                 'Relative bridge source selection or parser contract changed')

    def __enter__(self):
        self._assert_selection()
        self._session.__enter__()
        try:
            self._capture = self._session.capture
            self._tx = self._session._borrow_transaction(self._capture)
            self._baseline_graph = self._tx.snapshot
            self._capture_facts = _retain_authority((self._capture, self._baseline_graph,
                                                    self._selection))
            self._active = True
            self._context(self._baseline_graph)
            return self
        except BaseException:
            self._active = False
            self._session.__exit__(None, None, None)
            raise

    def __exit__(self, exc_type, exc, tb):
        self._active = False
        self._candidate = self._validation_facts = self._committed_facts = None
        self._validation_pins = None
        return self._session.__exit__(exc_type, exc, tb)

    def _assert_capture_binding(self):
        _require(self._active and self._capture_facts is not None
                 and _matches_authority((self._capture, self._baseline_graph, self._selection),
                                        self._capture_facts)
                 and self._tx is self._session._tx,
                 'Relative bridge capture binding changed or expired')
        self._assert_selection()
        self._session._assert_binding(self._capture)

    def _context(self, graph):
        self._assert_capture_binding()
        baseline = self._baseline_graph
        _require(type(graph) is GraphSnapshot and graph.root is baseline.root
                 and type(graph.sources) is MappingProxyType
                 and len(graph.sources) == 1 and tuple(graph.sources) == (self.target,)
                 and type(graph.sources[self.target]) is bytes
                 and type(graph.evidence) is MappingProxyType
                 and len(graph.evidence) == len(baseline.evidence)
                 and tuple(graph.evidence) == tuple(baseline.evidence)
                 and all(graph.evidence[p] is baseline.evidence[p] for p in baseline.evidence),
                 'Callback requires exactly the retained writable root and evidence identities')
        _config(graph.evidence[self._session._config_path], self.target)
        catalog, _ = _catalog(graph.evidence[self._session._catalog_path])
        return catalog

    def _certify(self, captures):
        try:
            proof = core.certify_captured_relative_core(self.target, captures,
                                                       source_profile=self._selection.source_profile)
            core.verify_captured_relative_core(proof, target_path=self.target,
                captures=captures, source_profile=self._selection.source_profile)
        except core.CapturedRelativeCoreError as error:
            raise CapturedRelativeFixError(f'Relative candidate grammar/proof changed: {error}') from error
        _require((proof.root_grammar_version, proof.module_grammar_version, proof.parser_contract)
                 == (self._selection.root_grammar_version, self._selection.module_grammar_version,
                     self._selection.parser_contract)
                 and tuple(proof.evaluated_rule_ids) == (SCRIPT,)
                 and frozenset(proof.not_applicable_rule_ids) == CORE_RULE_IDS - {SCRIPT}
                 and frozenset(r for r, reason in proof.structural_reasons if reason)
                     == CORE_RULE_IDS - {SCRIPT}, 'Incomplete captured graph core obligations')
        return proof

    def _graph_for_tree(self, tree):
        captures = MappingProxyType({p: tree if p == self.target else original
                                    for p, original in self._capture.captures.items()})
        proof = self._certify(captures)
        _require(core._same_exact(_semantics(proof, self.target),
                                 _semantics(self._capture.proof, self.target)),
                 'Root repair changed import edges/items, module proof, origins, routes or use ownership')
        return captures, proof

    @staticmethod
    def _actual_script(tree, path, catalog):
        try:
            result = analyze_script(tree, path, _verify=False, catalog=catalog)
        except AdapterError as error:
            raise CapturedRelativeFixError(f'Captured script analysis changed: {error}') from error
        _require(type(result) is ScriptResult
                 and all(type(getattr(result, name, None)) is tuple for name in
                         ('diagnostics', 'edits', 'skips', 'parse_errors', 'suppressed')),
                 'Immutable actual script analysis required for every graph source')
        _require(not (result.skips or result.parse_errors or result.suppressed),
                 'Skipped, suppressed or parse-error script analysis is unsupported')
        findings = _findings(tree, path, result)
        _require(bool(findings) == bool(result.edits)
                 and core._same_exact(result.edits, _script_insertions(tree)),
                 'Script proposals must exactly cover original operand boundaries')
        return result, findings

    def _analyze_tree(self, tree, catalog):
        self._assert_capture_binding()
        captures, proof = self._graph_for_tree(tree)
        modules = []
        for path, module in captures.items():
            if path != self.target:
                result, findings = self._actual_script(module, path, catalog)
                _require(result == ScriptResult() and not findings,
                         'Every inert module must have an empty actual script result')
                modules.append((path, result))
        result, findings = self._actual_script(tree, self.target, catalog)
        _require(not result.edits or tree.source == self._baseline_graph.sources[self.target],
                 'Relative bridge refuses a second repair pass')
        return result, findings, captures, proof, tuple(modules)

    def _analyze_graph(self, graph):
        catalog = self._context(graph)
        try:
            tree = parse(graph.sources[self.target])
        except AdapterError as error:
            raise CapturedRelativeFixError(f'Relative candidate parser failed: {error}') from error
        result, findings, _, proof, modules = self._analyze_tree(tree, catalog)
        analysis = GraphAnalysis(CORE_RULE_IDS, CORE_RULE_IDS,
            frozenset((self.target,)), frozenset(self._baseline_graph.evidence),
            findings=findings, edits=tuple(SourceEdit(self.target, edit) for edit in result.edits))
        self._history.append(_Pass(tree, result, analysis, proof, modules))
        return analysis

    def _independent_replay(self, snapshot):
        catalog = self._context(snapshot)
        # Reparse the exact captured original bytes, outside transaction callback.
        tree = parse(snapshot.sources[self.target])
        initial = self._analyze_tree(tree, catalog)[0]

        def analyze(checked):
            return {SCRIPT: self._analyze_tree(checked, catalog)[0]}

        def step(before, old, after, new, history):
            _require(not history.deferred and not history.moves,
                     'Relative script replay cannot defer edits or move source')
            _require(_attachment_roles(before) == _attachment_roles(after)
                     and _script_ownership(before) == _script_ownership(after),
                     'Relative repair changed wrapper/equation/nested operand owners')
            expected = _script_insertions(before)
            _require(history.edits == expected == plan(before.source, old[SCRIPT].edits).edits
                     and history.after == plan(before.source, expected).candidate()
                     and all(e.start == e.end and e.replacement
                             and not e.replacement.strip(b'()') for e in history.edits),
                     'Independent replay must preserve every original byte with exact parentheses')
            _require(not (new[SCRIPT].diagnostics or new[SCRIPT].edits),
                     'Relative repair requires a clean result after one pass')

        def final(candidate):
            _require(candidate.passes <= 1 and not candidate.final_results[SCRIPT].diagnostics
                     and not candidate.final_results[SCRIPT].edits,
                     'Relative candidate is not a clean one-pass result')
            _require(all(item.primary_rule_id == SCRIPT and item.rule_ids == (SCRIPT,)
                         and item.contributions and all(c.rule_id == SCRIPT
                             and c.pass_number == 1 for c in item.contributions)
                         for item in candidate.edits), 'Unowned relative repair attribution')
            _require(tuple(item.edit for item in candidate.edits) == _script_insertions(tree)
                     and plan(candidate.baseline, (item.edit for item in candidate.edits)).candidate()
                         == candidate.content, 'Original-source attributed replay differs')

        replay = converge(tree, {SCRIPT: initial}, analyze, step, final, max_passes=1)
        final(replay)
        return replay

    def validate(self):
        self._validation_attempt_started = True
        self._assert_capture_binding()
        _require(self._applied_validation is None, 'A relative bridge cannot be reused after apply')
        self._session.assert_current(self._capture)
        self._candidate = self._validation = self._validation_facts = None
        self._validation_pins = None
        self._history = []
        token = self._tx.validate(self._analyze_graph, required_rules=CORE_RULE_IDS)
        _require(not token.findings and type(token.passes) is int and token.passes <= 1,
                 'Relative transaction requires a clean one-pass candidate')
        try:
            replay = self._independent_replay(self._baseline_graph)
        except AdapterError as error:
            raise CapturedRelativeFixError(f'Independent candidate parser failed: {error}') from error
        baseline = self._baseline_graph.sources[self.target]
        _require(type(replay.passes) is int and replay.passes in (0, 1)
                 and type(replay.steps) is tuple and len(replay.steps) == replay.passes
                 and replay.baseline is baseline and type(replay.content) is bytes
                 and replay.final_tree.source is replay.content
                 and tuple(replay.initial_results) == (SCRIPT,)
                 and tuple(replay.final_results) == (SCRIPT,)
                 and token.sources == {self.target: replay.content} and token.passes == replay.passes,
                 'Transaction and independent relative replay disagree')
        expected = ((baseline, replay.content, replay.content) if token.passes
                    else (baseline, baseline))
        _require(tuple(p.tree.source for p in self._history) == expected
                 and core._same_exact(self._history[0].result, replay.initial_results[SCRIPT])
                 and all(core._same_exact(p.result, replay.final_results[SCRIPT])
                         and not p.analysis.findings for p in self._history[1:]),
                 'Transaction source history/results differ from independent replay')
        if replay.steps:
            _require(tuple(e.edit for e in self._history[0].analysis.edits) == replay.steps[0].edits,
                     'Transaction attribution differs from independent root-only proposals')
        captures, proof = self._graph_for_tree(replay.final_tree)
        self._session.assert_current(self._capture)
        candidate = _issued(CapturedRelativeFixCandidate, sources=token.sources,
            passes=token.passes, findings=token.findings, _token=token,
            _owner=self._owner, _seal=_SEAL)
        self._validated_summary = CapturedRelativeFixSummary(self.source_profile, self.target,
            digest(baseline), digest(replay.content), tuple(sorted(CORE_RULE_IDS)),
            tuple(proof.evaluated_rule_ids), tuple(proof.structural_reasons),
            tuple(AppliedInsertion(self.target, item.edit.start, item.edit.end, item.edit.replacement)
                  for item in replay.edits), self.root_grammar_version,
            self.module_grammar_version, self.parser_contract,
            sources=tuple(CapturedRelativeSourceSummary(source.path, source.source_sha256,
                source.source_bytes, 'writable_root' if source.path == self.target
                else 'unchanged_read_only_dependency') for source in proof.sources),
            import_edges=tuple(CapturedRelativeImportSummary(edge.importer, edge.request,
                edge.target, edge.node_id, tuple(edge.span),
                tuple(tuple(item) for item in edge.items)) for edge in proof.edges))
        validation = _Validation(candidate, replay, captures, proof, tuple(self._history),
            self._baseline_graph.root, self.target, self._baseline_graph.evidence,
            digest(self._baseline_graph.evidence[self._session._config_path]),
            digest(self._baseline_graph.evidence[self._session._catalog_path]), self._selection,
            self._validated_summary)
        self._candidate, self._validation = candidate, validation
        self._validation_facts = (validation, _retain_authority(validation))
        self._validation_pins = (candidate, token, replay, replay.final_tree, proof,
                                 tuple(captures.items()))
        self._assert_validation(validation)
        return candidate

    def _assert_validation(self, validation):
        facts = self._validation_facts
        _require(facts is not None and validation is facts[0]
                 and _matches_authority(validation, facts[1]),
                 'Relative candidate, token, replay, tree, proof or retained facts changed')
        self._assert_capture_binding()
        pins = self._validation_pins
        _require(pins is not None and validation.candidate is pins[0]
                 and validation.candidate._token is pins[1] and validation.replay is pins[2]
                 and validation.replay.final_tree is pins[3] and validation.proof is pins[4]
                 and all(validation.captures[path] is tree for path, tree in pins[5]),
                 'Relative retained candidate, replay, proof or parser Tree identity changed')
        _require(validation.selection is self._selection and validation.target == self.target
                 and validation.candidate._owner is self._owner
                 and validation.candidate._seal is _SEAL
                 and validation.candidate.sources is validation.candidate._token.sources,
                 'Relative candidate source membership or ownership changed')
        try:
            core.verify_captured_relative_core(validation.proof, target_path=self.target,
                captures=validation.captures, source_profile=self.source_profile)
        except core.CapturedRelativeCoreError as error:
            raise CapturedRelativeFixError(f'Retained candidate proof changed: {error}') from error

    def apply(self, candidate):
        self._apply_attempt_started = True
        _require(self._active and type(candidate) is CapturedRelativeFixCandidate
                 and candidate is self._candidate and self._applied_validation is None,
                 'Only this active bridge\'s original unused candidate may be applied')
        validation = self._validation
        self._assert_validation(validation)
        self._session.assert_current(self._capture)
        # Snapshot reporting before any transaction fault callback can mutate
        # exposed frozen objects reflectively. Follow independently retained
        # primitive facts, including source/edge/insertion records; this local
        # deep copy is never exposed mid-apply and supplies no write authority.
        report = _restore_summary(_retain_authority(validation.summary))
        self._candidate = None
        result = self._tx.apply(candidate._token)
        # Establish provenance and honest reporting immediately after actual
        # success, before later guards which may refuse a stale handoff.
        self._applied_validation = validation
        self._summary = replace(report, modified_paths=result.modified,
                                journal_id=result.journal)
        self._summary_facts = _retain_authority(self._summary)
        self._assert_validation(validation)
        return result

    @property
    def summary(self):
        _require(self._summary_facts is not None,
                 'A successful relative bridge apply is required for reporting')
        # Preserve ordinary report identity for existing callers, but a shared
        # or reflectively changed report cannot rebase or erase actual success.
        if not _matches_authority(self._summary, self._summary_facts):
            self._summary = _restore_summary(self._summary_facts)
        return self._summary

    def _assert_committed_binding(self, snapshot):
        validation = self._applied_validation
        _require(self._active and validation is not None and snapshot is not None
                 and snapshot is self._committed and type(snapshot) is CapturedRelativeCommittedGraph
                 and self._committed_facts is not None
                 and _matches_authority(snapshot, self._committed_facts),
                 'Only this active bridge\'s unchanged committed graph is authoritative')
        self._assert_validation(validation)
        _require(snapshot._owner is self._owner and snapshot._seal is _SEAL
                 and snapshot._validation is validation and snapshot._receipt is self._receipt
                 and snapshot.proof is validation.proof,
                 'Committed graph provenance changed')

    def committed_graph_snapshot(self):
        validation = self._applied_validation
        _require(self._active and validation is not None,
                 'This bridge\'s successful independently validated apply is required')
        self._assert_validation(validation)
        if self._committed is not None:
            self._assert_committed_binding(self._committed)
        try:
            receipt = self._tx.committed_snapshot()
        except (AttributeError, TypeError, ValueError) as error:
            raise CapturedRelativeFixError('Committed transaction receipt changed') from error
        _require(type(receipt) is CommittedSnapshot and receipt.root is validation.root
                 and type(receipt.sources) is MappingProxyType
                 and len(receipt.sources) == 1
                 and tuple(receipt.sources) == (self.target,)
                 and type(receipt.sources[self.target]) is bytes
                 and receipt.sources[self.target] == validation.candidate._token.sources[self.target]
                 and type(receipt.evidence) is MappingProxyType
                 and len(receipt.evidence) == len(validation.evidence)
                 and tuple(receipt.evidence) == tuple(validation.evidence)
                 and all(receipt.evidence[p] is validation.evidence[p] for p in validation.evidence),
                 'Committed transaction postimage/evidence differs from the proven graph')
        if self._committed is None:
            self._receipt = receipt
            sources = MappingProxyType({p: receipt.sources[self.target] if p == self.target
                                      else receipt.evidence[self._capture.dependency_evidence[p]]
                                      for p in sorted(validation.captures)})
            _require(all((validation.captures[p].source == sources[p] if p == self.target
                          else validation.captures[p].source is sources[p]) for p in sources),
                     'Committed graph Trees differ from retained root/dependency bytes')
            snapshot = _issued(CapturedRelativeCommittedGraph, root=validation.root,
                target=self.target, source_profile=self.source_profile,
                root_grammar_version=self.root_grammar_version,
                module_grammar_version=self.module_grammar_version, parser_contract=self.parser_contract,
                captures=validation.captures, sources=sources, evidence=receipt.evidence,
                proof=validation.proof, required_rule_ids=tuple(sorted(CORE_RULE_IDS)),
                evaluated_rule_ids=tuple(validation.proof.evaluated_rule_ids),
                structural_reasons=tuple(validation.proof.structural_reasons),
                config_sha256=validation.config_sha256, catalog_sha256=validation.catalog_sha256,
                _receipt=receipt, _validation=validation, _owner=self._owner, _seal=_SEAL)
            self._committed, self._committed_facts = snapshot, _retain_authority(snapshot)
        _require(receipt is self._receipt, 'Committed transaction generation changed')
        self.assert_committed_graph_current(self._committed)
        return self._committed

    def assert_committed_graph_current(self, snapshot):
        self._assert_committed_binding(snapshot)
        self._tx.assert_current(snapshot._receipt)
